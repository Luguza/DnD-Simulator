"""`Creature` — something that stands somewhere, has hit points, and answers when
the engine asks.

`w0g2` cut the first version so that `w0h`'s walking skeleton and `w0i`'s
benchmark ran against a real object rather than a stub — real hit points, a real
position on the grid, a real `Dispatcher` carrying real handlers — and `w1a`
completed it with equipment, a derived Armor Class and the action economy,
rewriting none of it. `w1b` and `w1c` hang resources and effects off it next.

**A creature's AC is a fact about what it is wearing.** `armour` and `shield`
are equipment, beside the weapon, and `armour_class` is derived from them and
from the stat block's Dexterity (DESIGN.md § Reconnaissance: "AC is derived from
equipment, never written into a build"). It is derived *once* and cached, since
every attack made against this creature reads it and the inputs change only when
something is equipped. What changes AC for the length of one attack — Shield,
cover, Shield of Faith — is not equipment and does not belong here: it rides on
`AttackDeclaredContext.ac_bonus`, which is the defender's one lever in the
attack pipeline and the reason no hook in the closed registry recomputes AC.

**A creature owns its action economy and its dispatcher spends from it.** The
two are wired to each other here rather than by the build loader, because a
creature paying out of somebody else's economy is the same class of mistake as a
handler owned by somebody else, and this is the one place that can rule it out.
`Dispatcher.bind()` is left for the gate (w2f), which genuinely cannot be
wired at construction — the APL's namespace is built over the creature that owns
the dispatcher.

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

from collections.abc import Mapping
from typing import Final

from dndsim.core.geometry import Cell
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.stats import UNARMOURED, Armour, StatBlock, Weapon
from dndsim.hooks.contexts import Context
from dndsim.hooks.dispatch import Dispatcher
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import CRITICAL_HIT_THRESHOLD, SHIELD_AC_BONUS
from dndsim.rules.enums import Ability, Size

__all__ = ["UNSEATED", "Creature"]


UNSEATED: Final = InitiativeKey(
    initiative=0, dexterity=10, player_character=False, tiebreak=0.0
)
"""The place of a creature nobody has rolled initiative for yet.

`engine/turn.py` (w2c) owns the roster and draws a real key per combatant at the
top of an encounter; until then a creature needs *some* position for the clock
to compare against, and every unseated creature sharing one is the honest
statement that the order is not yet decided. Compare `position`, which has no
default at all — that one would be a silently wrong fight rather than an
undecided one."""


class Creature:
    """One combatant: its numbers, where it stands, and what is attached to it.

    Satisfies the `Combatant` protocol `hooks/contexts.py` declares — a name, a
    place in the order and the pool a hit comes out of — which is the contract
    `w0f` deferred to `w0g2` and `w1a`, and the contract test that says so is the
    reason the protocol may stay narrow.
    """

    __slots__ = (
        # A creature can be the referent of a weak reference, which a slotted
        # class is not by default. DESIGN.md § Condition language builds the
        # APL's namespace proxies over "weak references to live state", so
        # without this the compiled policy of every build would either be
        # impossible or would keep dead combatants alive for the length of a
        # run. One pointer per creature, nothing on the hot path (w0i).
        "__weakref__",
        "_armour",
        "_armour_class",
        "_modifiers",
        "_proficiency_bonus",
        "_shield",
        "_stats",
        "crit_threshold",
        "dispatcher",
        "economy",
        "hp",
        "initiative",
        "name",
        "position",
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
        armour: Armour = UNARMOURED,
        shield: bool = False,
        initiative: InitiativeKey = UNSEATED,
    ) -> None:
        # `position` has no default: two creatures defaulted onto the same cell
        # would be a fight in which everything is in everyone's reach and every
        # distance is zero, which is the kind of silence this design refuses.
        # `armour` does have one, because 10 + DEX is what the rules say about a
        # creature wearing nothing rather than a number standing in for one
        # somebody forgot.
        self.name = name
        self._stats = stats
        self.weapon = weapon
        self._armour = armour
        self._shield = shield
        self.position = position
        self.initiative = initiative
        self.hp = stats.max_hp
        self.temp_hp = 0
        # Read off the stat block once. Both are pure functions of a frozen
        # object and the attack pipeline reads them three times per resolution
        # (w0i measured recomputing them at ~1.5% of the budget). Both are
        # private, and read back through properties with no setter, because a
        # cache anybody can write to is a cache that says what the creature used
        # to be — which is the same reason `stats` is read-only.
        self._modifiers = stats.modifiers
        self._proficiency_bonus = stats.proficiency_bonus
        # Where this creature's attacks start critting. A derived stat rather
        # than a constant the attack reads, because Improved Critical lowers
        # the *character's* threshold to 19 and nothing about one attack roll
        # changes it — which is the reason `AttackRollContext` has no mutator
        # for it (`hooks/contexts.py`). A feature that lowers it sets it here.
        self.crit_threshold = CRITICAL_HIT_THRESHOLD
        self._armour_class = self._derive_armour_class()
        self.economy = ActionEconomy()
        self.dispatcher = Dispatcher(economy=self.economy)

    # -- what the stat block already knows ------------------------------------

    @property
    def stats(self) -> StatBlock:
        """The frozen numbers this creature was built from.

        Read-only, like the three numbers derived from it — `modifiers`,
        `proficiency_bonus` and the AC. A stat block swapped in afterwards would
        leave all three describing the creature this one used to be, and a
        creature whose build changed is a new creature rather than a mutated
        one.
        """
        return self._stats

    @property
    def modifiers(self) -> Mapping[Ability, int]:
        """Every ability modifier, settled at construction. A read-only view of
        the stat block's own mapping, so neither the creature nor a feature can
        write one."""
        return self._modifiers

    @property
    def proficiency_bonus(self) -> int:
        return self._proficiency_bonus

    @property
    def max_hp(self) -> int:
        return self._stats.max_hp

    @property
    def speed_ft(self) -> int:
        return self._stats.speed_ft

    @property
    def size(self) -> Size:
        return self._stats.size

    def modifier(self, ability: Ability) -> int:
        """The modifier for one ability score."""
        return self._modifiers[ability]

    # -- what it is wearing, and the AC that follows ---------------------------

    @property
    def armour_class(self) -> int:
        """This creature's Armor Class, derived from its equipment.

        No setter: an AC is what the armour, the shield and the Dexterity add up
        to, and a build that could assign one would be back to typing the number
        into the YAML. A bonus lasting one attack is `ac_bonus` on the attack's
        context; a bonus lasting longer is an `Effect` (w1c) that equips or
        changes something here.
        """
        return self._armour_class

    @property
    def armour(self) -> Armour:
        return self._armour

    @armour.setter
    def armour(self, armour: Armour) -> None:
        self._armour = armour
        self._armour_class = self._derive_armour_class()

    @property
    def shield(self) -> bool:
        """Whether a Shield is in hand: +2, the same for every shield in v1,
        which has no magic items (DESIGN.md § Settled decisions)."""
        return self._shield

    @shield.setter
    def shield(self, shield: bool) -> None:
        self._shield = shield
        self._armour_class = self._derive_armour_class()

    def _derive_armour_class(self) -> int:
        """The one place an AC is computed. Called when equipment changes, and
        never per attack — the inputs cannot change without coming through
        here."""
        armour_class = self._armour.armour_class(self._stats)
        return armour_class + SHIELD_AC_BONUS if self._shield else armour_class

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
