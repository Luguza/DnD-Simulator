"""The run-time half of the namespace: one proxy per root, built once per
creature over live state, so a compiled clause is attribute access rather than
object construction (DESIGN.md § Condition language).

Every group is filtered on live state on each read; only who is seated on
which side is kept between reads, until the world seats or unseats someone.
`docs/decisions.md` § w2e.
"""

from collections.abc import Callable
from weakref import ReferenceType, ref

from dndsim.apl.schema import NamespaceSchema
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import distance_ft, in_reach
from dndsim.creatures.creature import Creature
from dndsim.engine.world import World
from dndsim.hooks.contexts import Cell as CellLike

__all__ = [
    "AlliesProxy",
    "EnemiesProxy",
    "Namespace",
    "SelfProxy",
    "SlotsProxy",
    "TargetProxy",
    "TriggerProxy",
    "build_namespace",
    "hp_pct",
]


def hp_pct(creature: Creature) -> float:
    """Hit points as a fraction of the maximum."""
    return creature.hp / creature.max_hp


def _live(reference: "ReferenceType[Creature]") -> Creature:
    creature = reference()
    if creature is None:
        raise EngineAssemblyError(
            "an APL namespace outlived the creature it was built for; a "
            "namespace lives exactly as long as the encounter that uses it"
        )
    return creature


class SlotsProxy:
    """`self.slots.*`: the creature's spell slots."""

    __slots__ = ("_ref",)

    def __init__(self, creature: Creature) -> None:
        self._ref: ReferenceType[Creature] = ref(creature)

    @property
    def highest(self) -> int:
        return _live(self._ref).resources.spells.highest()


class SelfProxy:
    """The `self.*` root: the acting creature. A schema's extensions are
    properties of a subclass `build_namespace()` makes."""

    __slots__ = ("_ref", "slots")

    def __init__(self, creature: Creature) -> None:
        self._ref: ReferenceType[Creature] = ref(creature)
        self.slots = SlotsProxy(creature)

    @property
    def creature(self) -> Creature:
        return _live(self._ref)

    @property
    def hp(self) -> int:
        return _live(self._ref).hp

    @property
    def max_hp(self) -> int:
        return _live(self._ref).max_hp

    @property
    def hp_pct(self) -> float:
        return hp_pct(_live(self._ref))

    @property
    def ac(self) -> int:
        return _live(self._ref).armour_class

    @property
    def reach_ft(self) -> int:
        return _live(self._ref).reach_ft

    @property
    def speed_ft(self) -> int:
        return _live(self._ref).speed_ft

    @property
    def down(self) -> bool:
        return _live(self._ref).is_down


class TargetProxy:
    """The `target.*` root: whoever the current offer is against. Rebound by
    whoever asks; an unbound target reads 0 and `False`."""

    __slots__ = ("creature",)

    def __init__(self) -> None:
        self.creature: Creature | None = None

    def bind(self, creature: Creature | None) -> None:
        self.creature = creature

    @property
    def hp(self) -> int:
        return 0 if self.creature is None else self.creature.hp

    @property
    def max_hp(self) -> int:
        return 0 if self.creature is None else self.creature.max_hp

    @property
    def hp_pct(self) -> float:
        return 0.0 if self.creature is None else hp_pct(self.creature)

    @property
    def ac(self) -> int:
        return 0 if self.creature is None else self.creature.armour_class

    @property
    def reach_ft(self) -> int:
        return 0 if self.creature is None else self.creature.reach_ft

    @property
    def speed_ft(self) -> int:
        return 0 if self.creature is None else self.creature.speed_ft

    @property
    def down(self) -> bool:
        return self.creature is not None and self.creature.is_down


class TriggerProxy:
    """The `trigger.*` root: what is being offered, right now.

    `bind()` is the `Trigger` `engine/attack.py` tells about an attack;
    `offer_movement()` is told about a creature leaving reach.
    """

    __slots__ = ("crit", "damage_estimate", "destination", "mover", "origin")

    def __init__(self) -> None:
        self.crit = False
        self.damage_estimate = 0
        self.mover: Creature | None = None
        self.origin: CellLike | None = None
        self.destination: CellLike | None = None

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None:
        """An attack is on offer: replaces the whole offer."""
        self.crit = crit
        self.damage_estimate = damage_estimate
        self.mover = self.origin = self.destination = None

    def offer_movement(
        self, mover: Creature, origin: "CellLike", destination: "CellLike"
    ) -> None:
        """A creature is leaving reach: replaces the whole offer."""
        self.crit = False
        self.damage_estimate = 0
        self.mover = mover
        self.origin = origin
        self.destination = destination


class _Side:
    __slots__ = ("_ref", "_seated", "_seating", "_world")

    def __init__(self, owner: Creature, world: World) -> None:
        self._ref: ReferenceType[Creature] = ref(owner)
        self._world = world
        self._seating = -1
        self._seated: tuple[Creature, ...] = ()

    def _members(self) -> tuple[Creature, ...]:
        """The side's seated creatures that are not dead, in seating order."""
        world = self._world
        if self._seating != world.seating:
            self._seated = self._seat(_live(self._ref), world)
            self._seating = world.seating
        return tuple(c for c in self._seated if not c.dead)

    def _seat(self, owner: Creature, world: World) -> tuple[Creature, ...]:
        raise NotImplementedError

    @property
    def alive(self) -> tuple[Creature, ...]:
        """Standing: alive and above 0 hit points, in seating order."""
        return tuple(c for c in self._members() if not c.is_down)

    def within(self, feet: int) -> tuple[Creature, ...]:
        """Standing and within `feet` of the owner, between footprints."""
        owner = _live(self._ref)
        origin = owner.position
        size = owner.size
        return tuple(
            c
            for c in self._members()
            if not c.is_down
            and distance_ft(
                origin, c.position, origin_size=size, destination_size=c.size
            )
            <= feet
        )


class EnemiesProxy(_Side):
    """The `enemies.*` root: every seated living creature on another side."""

    __slots__ = ()

    def _seat(self, owner: Creature, world: World) -> tuple[Creature, ...]:
        side = world.side_of(owner)
        return tuple(c for c in world if world.side_of(c) != side)

    @property
    def in_reach(self) -> tuple[Creature, ...]:
        """Standing and within the owner's reach, between footprints."""
        owner = _live(self._ref)
        origin = owner.position
        reach = owner.reach_ft
        size = owner.size
        return tuple(
            c
            for c in self._members()
            if not c.is_down
            and in_reach(
                origin, c.position, reach, origin_size=size, destination_size=c.size
            )
        )


class AlliesProxy(_Side):
    """The `allies.*` root: every other seated living creature on the owner's
    side."""

    __slots__ = ()

    def _seat(self, owner: Creature, world: World) -> tuple[Creature, ...]:
        side = world.side_of(owner)
        return tuple(c for c in world if c is not owner and world.side_of(c) == side)

    @property
    def down(self) -> tuple[Creature, ...]:
        """Alive at 0 hit points."""
        return tuple(c for c in self._members() if c.is_down)


class Namespace:
    """The five roots of one creature, passed to every compiled clause."""

    __slots__ = ("allies", "enemies", "self", "target", "trigger", "world")

    def __init__(
        self,
        *,
        own: SelfProxy,
        enemies: EnemiesProxy,
        allies: AlliesProxy,
        world: World,
    ) -> None:
        # The root the APL author writes as `self`.
        self.self = own
        self.enemies = enemies
        self.allies = allies
        self.world = world
        self.target = TargetProxy()
        self.trigger = TriggerProxy()


def build_namespace(
    schema: NamespaceSchema, creature: Creature, world: World
) -> Namespace:
    """The namespace `creature` is asked about in `world`, answering `schema`'s
    extensions as properties of its `self`."""
    own_type: type[SelfProxy] = SelfProxy
    if schema.extensions:
        own_type = type(
            "SelfProxy",
            (SelfProxy,),
            {
                "__slots__": (),
                **{
                    name: property(_reader(extension.read))
                    for name, extension in schema.extensions.items()
                },
            },
        )
    return Namespace(
        own=own_type(creature),
        enemies=EnemiesProxy(creature, world),
        allies=AlliesProxy(creature, world),
        world=world,
    )


def _reader(
    read: Callable[[Creature], bool | int | float],
) -> Callable[[SelfProxy], bool | int | float]:
    def answer(proxy: SelfProxy) -> bool | int | float:
        return read(_live(proxy._ref))

    return answer
