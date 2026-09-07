"""`Creature` — something that stands somewhere, has hit points, and answers when
the engine asks.

A first cut (ROADMAP w0g2), built so that `w0h`'s walking skeleton and `w0i`'s
benchmark run against a real object rather than a stub: real hit points, a real
position on the grid, and a real `Dispatcher` carrying real handlers. `w1a`
completes it with derived stats, AC from equipment and the action economy; `w1b`
and `w1c` hang resources and effects off it. Nothing here is rewritten by those
tasks.

**A creature owns its dispatch table.** DESIGN.md § Levers asks for per-creature
precomputed tables and refuses a global bus, so the table is a field of the
creature and the reset ledger the `once_per` budgets are spent against is the
dispatcher's. That is also the whole of the dependency direction: `creatures/`
imports `hooks/`, never the other way about, which is why `hooks/contexts.py`
declares a narrow `Combatant` protocol instead of importing this class.

**Hit points are a plain attribute, and the arithmetic is not here.**
`engine/damage.py` owns the resolution order — immunity through temp HP to HP —
in exactly one file (DESIGN.md § Structural prevention), so a `take_damage()`
method on this class would be a second place that order could be written, and the
second place is the one that gets it wrong. What this class owes the engine is
the two numbers and the guarantee that they are the creature's own.

Sources: DESIGN.md § Hook system, § Levers; ROADMAP w0g2, w1a.
"""

from dndsim.core.geometry import Cell
from dndsim.creatures.stats import StatBlock, Weapon
from dndsim.hooks.contexts import Context
from dndsim.hooks.dispatch import Dispatcher
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, Size

__all__ = ["Creature"]


class Creature:
    """One combatant: its numbers, where it stands, and what is attached to it.

    Satisfies the `Combatant` protocol `hooks/contexts.py` declares — `name` and
    `initiative_index` — which is the contract `w0f` deferred to this task, and
    the contract test that says so is the reason the protocol may stay narrow.
    """

    __slots__ = (
        # A creature can be the referent of a weak reference, which a slotted
        # class is not by default. DESIGN.md § Condition language builds the
        # APL's namespace proxies over "weak references to live state", so
        # without this the compiled policy of every build would either be
        # impossible or would keep dead combatants alive for the length of a
        # run. One pointer per creature, nothing on the hot path (w0i).
        "__weakref__",
        "dispatcher",
        "hp",
        "initiative_index",
        "name",
        "position",
        "stats",
        "temp_hp",
        "weapon",
    )

    def __init__(
        self,
        *,
        name: str,
        stats: StatBlock,
        position: Cell,
        weapon: Weapon | None = None,
        initiative_index: int = 0,
    ) -> None:
        # `position` has no default: two creatures defaulted onto the same cell
        # would be a fight in which everything is in everyone's reach and every
        # distance is zero, which is the kind of silence this design refuses.
        self.name = name
        self.stats = stats
        self.weapon = weapon
        self.position = position
        self.initiative_index = initiative_index
        self.hp = stats.max_hp
        self.temp_hp = 0
        self.dispatcher = Dispatcher()

    # -- what the stat block already knows ------------------------------------

    @property
    def max_hp(self) -> int:
        return self.stats.max_hp

    @property
    def armour_class(self) -> int:
        """This creature's AC. A literal until `w1a` derives it from equipment."""
        return self.stats.armour_class

    @property
    def speed_ft(self) -> int:
        return self.stats.speed_ft

    @property
    def size(self) -> Size:
        return self.stats.size

    @property
    def proficiency_bonus(self) -> int:
        return self.stats.proficiency_bonus

    def modifier(self, ability: Ability) -> int:
        """The modifier for one ability score."""
        return self.stats.modifier(ability)

    # -- what changes during a fight ------------------------------------------

    @property
    def reach_ft(self) -> int:
        """How far this creature threatens, which is its weapon's reach.

        Unarmed is 5 feet, and so is every weapon without the Reach property.
        Size does not enter into it yet — a creature occupies one cell in the
        first cut of `core/geometry.py`, and `w1d` is where a Large creature's
        square and its reach stop being the same question.
        """
        return 5 if self.weapon is None else self.weapon.reach_ft

    @property
    def is_alive(self) -> bool:
        """Whether this creature is still standing.

        Hit points only: death saves, stabilising and the Dying condition are
        `w1j`'s, and until then dropping to 0 is the end of a combatant's
        participation rather than the beginning of three rounds of dice.
        """
        return self.hp > 0

    # -- the hook pipeline -----------------------------------------------------

    def attach(self, source: HookSource) -> None:
        """Attach a feature, condition, effect or item to this creature.

        Attachment order is the last tiebreak in dispatch order (DESIGN.md §
        Ordering), so the order features are attached in is the order the build
        YAML lists them and a run is deterministic.
        """
        if source.owner is not self:
            raise ValueError(
                f"{source!r} belongs to {source.owner.name} and cannot be "
                f"attached to {self.name}: `self.owner` inside every one of its "
                "handlers would be the wrong creature, which is the one thing "
                "hook Scope exists to make impossible to get wrong."
            )
        self.dispatcher.attach(source)

    def detach(self, source: HookSource) -> None:
        """Remove a source — an effect expiring, an item unequipped."""
        self.dispatcher.detach(source)

    def dispatch(self, spec: HookSpec, ctx: Context) -> None:
        """Run this creature's handlers for `spec` over `ctx`.

        A hook nothing on this creature listens to costs one failed dict lookup
        and a return, which is the Null Object ending of DESIGN.md § Hook system
        and the reason engine code dispatches unconditionally rather than asking
        first.
        """
        self.dispatcher.dispatch(spec, ctx)

    def __repr__(self) -> str:
        return f"<{self.name} {self.hp}/{self.max_hp} hp at {self.position}>"
