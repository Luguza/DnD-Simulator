"""The APL, hand-compiled — the one stand-in the performance spike is allowed.

Each `if:`, `target:` and `move:` clause is a callable of one argument, the
`Namespace`, whose five roots are proxies built once per creature over weak
references to live state (DESIGN.md § Condition language). The engine binds
`trigger` and `target` before it asks. A gate id no entry claims is OFF.
`docs/decisions.md` § w0i.

DESIGN.md § APL and mid-attack decisions.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeAlias
from weakref import ReferenceType, ref

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft, in_reach
from dndsim.creatures.creature import Creature
from dndsim.hooks.contexts import Context
from dndsim.hooks.source import HookSource
from dndsim.tools.spike_features import raging

__all__ = [
    "AlliesProxy",
    "Apl",
    "EnemiesProxy",
    "Entry",
    "Namespace",
    "SelfProxy",
    "TargetProxy",
    "TriggerProxy",
    "first",
    "lowest_hp",
    "nearest",
    "toward_until_in_reach",
]


def _live(reference: "ReferenceType[Creature]") -> Creature:
    """Deref a proxy's weak reference; raises `EngineAssemblyError` if the
    creature is gone."""
    creature = reference()
    if creature is None:
        raise EngineAssemblyError(
            "an APL namespace outlived the creature it was built for; a "
            "namespace is compiled per build and lives exactly as long as the "
            "encounter that uses it"
        )
    return creature


class SelfProxy:
    """The `self.*` root: the acting creature, its effects and its pools."""

    __slots__ = ("_ref",)

    def __init__(self, creature: Creature) -> None:
        self._ref: ReferenceType[Creature] = ref(creature)

    @property
    def hp(self) -> int:
        return _live(self._ref).hp

    @property
    def hp_pct(self) -> float:
        creature = _live(self._ref)
        return creature.hp / creature.max_hp

    @property
    def position(self) -> Cell:
        return _live(self._ref).position

    @property
    def reach_ft(self) -> int:
        return _live(self._ref).reach_ft

    @property
    def raging(self) -> bool:
        return raging(_live(self._ref))

    @property
    def slots_highest(self) -> int:
        """`self.slots.highest`, resolved to one attribute rather than a
        sub-proxy."""
        return _live(self._ref).resources.spells.highest()


class TargetProxy:
    """The `target.*` root: whoever the current offer is against.

    Rebound by the engine per event, and held strongly; unbound reads as 0.
    """

    __slots__ = ("_creature",)

    def __init__(self) -> None:
        self._creature: Creature | None = None

    def bind(self, creature: Creature | None) -> None:
        self._creature = creature

    @property
    def ac(self) -> int:
        return 0 if self._creature is None else self._creature.armour_class

    @property
    def hp(self) -> int:
        return 0 if self._creature is None else self._creature.hp


class TriggerProxy:
    """The `trigger.*` root: what the engine is offering, right now.

    Bound by the engine before it dispatches a hook carrying gated handlers.
    """

    __slots__ = ("crit", "damage_estimate")

    def __init__(self) -> None:
        self.crit = False
        self.damage_estimate = 0

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None:
        self.crit = crit
        self.damage_estimate = damage_estimate


class EnemiesProxy:
    """The `enemies.*` root: everything hostile, as a set of live queries.

    Every query is computed from live positions, never cached (ROADMAP w0i).
    """

    __slots__ = ("_ref", "_side")

    def __init__(self, owner: Creature, side: Sequence[Creature]) -> None:
        self._ref: ReferenceType[Creature] = ref(owner)
        self._side = side

    def within(self, feet: int) -> int:
        origin = _live(self._ref).position
        return sum(
            1
            for c in self._side
            if not c.is_down and distance_ft(origin, c.position) <= feet
        )

    @property
    def alive(self) -> tuple[Creature, ...]:
        return tuple(c for c in self._side if not c.is_down)

    @property
    def in_reach(self) -> tuple[Creature, ...]:
        owner = _live(self._ref)
        origin = owner.position
        reach = owner.reach_ft
        return tuple(
            c
            for c in self._side
            if not c.is_down and in_reach(origin, c.position, reach)
        )


class AlliesProxy:
    """The `allies.*` root. `allies.down` is what Healing Word's entry reads."""

    __slots__ = ("_side",)

    def __init__(self, side: Sequence[Creature]) -> None:
        self._side = side

    @property
    def down(self) -> tuple[Creature, ...]:
        return tuple(c for c in self._side if c.is_alive and c.is_down)


class Namespace:
    """The five roots, built once per creature, passed to every compiled
    clause."""

    __slots__ = ("allies", "enemies", "self", "target", "trigger")

    def __init__(
        self,
        *,
        own: SelfProxy,
        enemies: EnemiesProxy,
        allies: AlliesProxy,
    ) -> None:
        # The root the APL author writes as `self`.
        self.self = own
        self.enemies = enemies
        self.allies = allies
        self.target = TargetProxy()
        self.trigger = TriggerProxy()


Condition: TypeAlias = Callable[[Namespace], bool]
Selector: TypeAlias = Callable[[Namespace], Creature | None]
Move: TypeAlias = Callable[[Namespace], Cell | None]


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the APL: an action id, up to three compiled clauses, and
    the gate id it answers."""

    action: str
    condition: Condition
    target: Selector | None = None
    move: Move | None = None
    gate: str | None = None


# --- the selector and movement grammar, as callables ------------------------


def lowest_hp(candidates: Sequence[Creature]) -> Creature | None:
    """`lowest_hp of <selector>` — focus fire."""
    return min(candidates, key=_hp_of, default=None)


def nearest(origin: Cell, candidates: Sequence[Creature]) -> Creature | None:
    """`nearest of <selector>` — the closest, ties broken by list order."""
    return min(candidates, key=lambda c: distance_ft(origin, c.position), default=None)


def first(candidates: Sequence[Creature]) -> Creature | None:
    """`first(<selector>)` — the head of an ordered list, or nothing."""
    return candidates[0] if candidates else None


def _hp_of(creature: Creature) -> int:
    return creature.hp


def toward_until_in_reach(origin: Cell, reach_ft: int, target: Creature) -> Cell | None:
    """`toward <selector> until in_reach` — the destination, not the route.

    Returns the nearest cell to `origin` within `reach_ft` of the target, or
    `None` when already in reach (DESIGN.md § APL). Computed from anchors, not
    footprints (debt #34).
    """
    goal = target.position
    if in_reach(origin, goal, reach_ft):
        return None
    steps = reach_ft // 5
    return Cell(
        goal.x + _clamp(origin.x - goal.x, steps),
        goal.y + _clamp(origin.y - goal.y, steps),
    )


def _clamp(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))


class Apl:
    """A compiled action priority list: one flat ordered list, asked twice.

    `choose(offered)` answers the turn loop and `allows(gate, source, ctx)`
    the dispatcher, as its `Gate` (DESIGN.md § APL).
    """

    __slots__ = ("_by_gate", "_entries", "_namespace")

    def __init__(self, entries: Sequence[Entry], namespace: Namespace) -> None:
        self._entries = tuple(entries)
        self._namespace = namespace
        self._by_gate = {e.gate: e for e in self._entries if e.gate is not None}

    @property
    def namespace(self) -> Namespace:
        """The roots the engine binds `target` and `trigger` on."""
        return self._namespace

    @property
    def entries(self) -> tuple[Entry, ...]:
        return self._entries

    def choose(self, offered: frozenset[str]) -> Entry | None:
        """The first entry whose action is offered and whose `if:` passes."""
        namespace = self._namespace
        for entry in self._entries:
            if entry.action in offered and entry.condition(namespace):
                return entry
        return None

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        """Whether a gated handler may act. Unmentioned is OFF."""
        entry = self._by_gate.get(gate_id)
        return entry is not None and entry.condition(self._namespace)
