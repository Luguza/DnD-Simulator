"""`Creature` — something that stands somewhere, has hit points, and answers when
the engine asks.

A creature holds its stat block, equipment and the AC derived from them, what
it holds in each hand and carries beside (`docs/decisions.md` § w2k), its
hit points and position, its `ActionEconomy`, its own `Dispatcher` (which
spends from that economy), its `EffectStore` and its `Resources`. Hit points
are plain attributes with no damage arithmetic here (`docs/decisions.md`
§ w0g2, § w1a).
"""

from collections.abc import Mapping
from typing import Final

from dndsim.core.geometry import Cell, in_reach
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.effects import Effect, EffectStore
from dndsim.creatures.resources import Resources
from dndsim.creatures.stats import UNARMOURED, Armour, StatBlock, Weapon
from dndsim.hooks.contexts import Context
from dndsim.hooks.dispatch import Dispatcher
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import (
    CRITICAL_HIT_THRESHOLD,
    DEATH_SAVE_FAILURES_TO_DIE,
    DEATH_SAVE_SUCCESSES_TO_STABILIZE,
    SHIELD_AC_BONUS,
)
from dndsim.rules.enums import Ability, Condition, Size, WeaponProperty

__all__ = ["HANDS", "UNSEATED", "Creature", "hands_for"]

HANDS: Final = 2
"""How many hands a creature holds things in."""

_GRASP_FT: Final = 5


UNSEATED: Final = InitiativeKey(
    initiative=0, dexterity=10, player_character=False, tiebreak=0.0
)
"""The place of a creature nobody has rolled initiative for yet, shared by every
such creature."""


def hands_for(weapon: Weapon | None) -> int:
    """How many hands holding `weapon` fills: two for a Two-Handed weapon and
    for a Versatile one whose damage is its two-handed die, else one; none for
    nothing."""
    if weapon is None:
        return 0
    if weapon.has(WeaponProperty.TWO_HANDED):
        return 2
    versatile = weapon.versatile_damage
    return 2 if versatile is not None and weapon.damage == versatile else 1


class Creature:
    """One combatant: its numbers, where it stands, and what is attached to it.

    Satisfies the `Combatant` protocol.
    """

    __slots__ = (
        # A slotted class cannot be weakly referenced without this
        # (`docs/decisions.md` § w0i).
        "__weakref__",
        "_armour",
        "_armour_class",
        "_modifiers",
        "_proficiency_bonus",
        "_shield",
        "_stats",
        "crit_threshold",
        "_ammunition",
        "_dead",
        "_dropped",
        "_stowed",
        "_death_save_failures",
        "_death_save_successes",
        "_stable",
        "_unconscious",
        "dispatcher",
        "economy",
        "effects",
        "hp",
        "initiative",
        "name",
        "off_hand",
        "player_character",
        "position",
        "resources",
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
        off_hand: Weapon | None = None,
        carried: Mapping[Weapon, int] | None = None,
        ammunition: Mapping[str, int] | None = None,
        armour: Armour = UNARMOURED,
        shield: bool = False,
        initiative: InitiativeKey = UNSEATED,
        player_character: bool = False,
    ) -> None:
        self.name = name
        self._stats = stats
        # What is held in the main hand and the other; `weapon` is the one an
        # attack that names none is made with.
        self.weapon = weapon
        self.off_hand = off_hand
        # Weapons carried but not held, and pieces of ammunition, by count.
        self._stowed: dict[Weapon, int] = {
            w: n for w, n in (carried or {}).items() if n > 0
        }
        self._ammunition: dict[str, int] = {
            kind: n for kind, n in (ammunition or {}).items() if n > 0
        }
        self._dropped: list[tuple[Weapon, Cell]] = []
        self._armour = armour
        self._shield = shield
        if self.hands_held > HANDS:
            raise ValueError(f"{name} holds more than two hands can")
        self.position = position
        self.initiative = initiative
        self.hp = stats.max_hp
        self.temp_hp = 0
        # A player character makes death saving throws at 0 hit points;
        # anything else dies (`docs/decisions.md` § w1j). The four fields after
        # it move only through the methods under "dying", below.
        self.player_character = player_character
        self._dead = False
        self._stable = False
        self._death_save_successes = 0
        self._death_save_failures = 0
        # Cached, and read-only through properties (`docs/decisions.md` § w1a).
        self._modifiers = stats.modifiers
        self._proficiency_bonus = stats.proficiency_bonus
        # Where this creature's attacks start critting; a feature that lowers
        # it sets it here (`docs/decisions.md` § w1a).
        self.crit_threshold = CRITICAL_HIT_THRESHOLD
        # How many Unconscious effects are attached, which the Shield's bonus
        # lapses under (`docs/decisions.md` § w2j).
        self._unconscious = 0
        self._armour_class = self._derive_armour_class()
        self.economy = ActionEconomy(denied=self._is_incapacitated)
        self.dispatcher = Dispatcher(economy=self.economy)
        # Trap: `UNSEATED` is a seat in the order, not a position ahead of it,
        # so until a sweep or a shared clock moves this store, a duration
        # anchored to a creature that sorts before it already reads as begun
        # (`docs/decisions.md` § w2c).
        self.effects = EffectStore(self, now=TimeKey(1, UNSEATED, TurnPhase.START))
        # Empty until the build or a feature adds pools.
        self.resources = Resources(self, stats)

    # -- what the stat block already knows ------------------------------------

    @property
    def stats(self) -> StatBlock:
        """The frozen numbers this creature was built from. Read-only
        (`docs/decisions.md` § w1a)."""
        return self._stats

    @property
    def modifiers(self) -> Mapping[Ability, int]:
        """Every ability modifier, settled at construction, as a read-only
        view."""
        return self._modifiers

    @property
    def proficiency_bonus(self) -> int:
        return self._proficiency_bonus

    @property
    def max_hp(self) -> int:
        return self._stats.max_hp

    @property
    def speed_ft(self) -> int:
        """Speed now: 0 while an effect sets it so, otherwise the stat block's
        less what effects take off it, never below 0 (`docs/decisions.md`
        § w2c)."""
        speed = self._stats.speed_ft
        for effect in self.effects:
            if effect.SPEED_ZERO:
                return 0
            speed -= effect.speed_reduction_ft
        return max(speed, 0)

    @property
    def incapacitated(self) -> bool:
        """Whether an effect denies every Action, Bonus Action and Reaction."""
        return any(effect.INCAPACITATES for effect in self.effects)

    @property
    def size(self) -> Size:
        return self._stats.size

    def _is_incapacitated(self) -> bool:
        return self.incapacitated

    def modifier(self, ability: Ability) -> int:
        """The modifier for one ability score."""
        return self._modifiers[ability]

    # -- what it is wearing, and the AC that follows ---------------------------

    @property
    def armour_class(self) -> int:
        """This creature's Armor Class, derived from armour, shield and
        Dexterity. No setter (`docs/decisions.md` § w1a)."""
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
        """Whether a Shield is in hand, for `SHIELD_AC_BONUS`, which it gives
        only while this creature is not Unconscious."""
        return self._shield

    @shield.setter
    def shield(self, shield: bool) -> None:
        self._shield = shield
        self._armour_class = self._derive_armour_class()

    def _derive_armour_class(self) -> int:
        """The one place an AC is computed; called when equipment changes and
        when the Unconscious condition is attached or detached."""
        armour_class = self._armour.armour_class(self._stats)
        if self._shield and not self._unconscious:
            return armour_class + SHIELD_AC_BONUS
        return armour_class

    # -- what changes during a fight ------------------------------------------

    @property
    def reach_ft(self) -> int:
        """How far this creature threatens: the longest reach of a weapon it
        holds, 5 feet unarmed.

        Size is not added: reach is measured between footprints
        (`docs/decisions.md` § w1d).
        """
        reach = 5
        for weapon in (self.weapon, self.off_hand):
            if weapon is not None and weapon.reach_ft > reach:
                reach = weapon.reach_ft
        return reach

    @property
    def hands_held(self) -> int:
        """How many hands what this creature holds fills: a Two-Handed weapon,
        or a Versatile one held in two hands, two; any other weapon one; a
        Shield one. A grapple is held by an effect, not here
        (`docs/decisions.md` § w2j, § w2k)."""
        held = hands_for(self.weapon) + hands_for(self.off_hand)
        return held + 1 if self._shield else held

    @property
    def held(self) -> tuple[Weapon, ...]:
        """The weapons in this creature's hands, the main hand's first."""
        return tuple(w for w in (self.weapon, self.off_hand) if w is not None)

    @property
    def stowed(self) -> Mapping[Weapon, int]:
        """The weapons carried but not held, each with how many."""
        return self._stowed

    def ammunition(self, kind: str) -> int:
        """How many pieces of the ammunition named `kind` this creature has."""
        return self._ammunition.get(kind, 0)

    def spend_ammunition(self, kind: str) -> None:
        """Spend one piece of `kind`. Raises `ValueError` when there is none."""
        left = self._ammunition.get(kind, 0)
        if left < 1:
            raise ValueError(f"{self.name} has no {kind} left")
        self._ammunition[kind] = left - 1

    def draw(self, weapon: Weapon) -> int:
        """Take one `weapon` out of what is carried into a free hand, the main
        hand first, and return which hand: 0 the main, 1 the other.

        Raises `ValueError` when none is carried or the hands are full.
        """
        left = self._stowed.get(weapon, 0)
        if left < 1:
            raise ValueError(f"{self.name} carries no {weapon.name} to draw")
        hand = self._free_hand(weapon)
        if left == 1:
            del self._stowed[weapon]
        else:
            self._stowed[weapon] = left - 1
        self._hold(hand, weapon)
        return hand

    def stow(self, hand: int) -> None:
        """Put the weapon in `hand` (0 the main, 1 the other) away. Raises
        `ValueError` when that hand is empty."""
        weapon = self._take(hand)
        self._stowed[weapon] = self._stowed.get(weapon, 0) + 1

    def let_go(self, hand: int) -> Weapon:
        """Empty `hand` of the weapon in it, which is gone, and return it.
        Raises `ValueError` when that hand is empty."""
        return self._take(hand)

    def _take(self, hand: int) -> Weapon:
        weapon = self.weapon if hand == 0 else self.off_hand
        if weapon is None:
            raise ValueError(f"{self.name} holds nothing in that hand")
        self._hold(hand, None)
        return weapon

    def _hold(self, hand: int, weapon: Weapon | None) -> None:
        if hand == 0:
            self.weapon = weapon
        else:
            self.off_hand = weapon

    def can_hold(self, weapon: Weapon) -> bool:
        """Whether a hand is free for `weapon` beside what is already held."""
        return self.hands_held + hands_for(weapon) <= HANDS

    def _free_hand(self, weapon: Weapon) -> int:
        if not self.can_hold(weapon):
            raise ValueError(f"{self.name} has no hand free to hold {weapon.name} in")
        return 0 if self.weapon is None else 1

    @property
    def dropped(self) -> tuple[tuple[Weapon, Cell], ...]:
        """What `drop_held()` let go of and nothing has picked up, each with
        the square it fell in, in the order dropped."""
        return tuple(self._dropped)

    def drop_held(self) -> None:
        """Drop every weapon held where this creature stands. Holding nothing
        drops nothing; a Shield is worn, not held (`docs/decisions.md` § w2b).
        """
        for hand, weapon in ((0, self.weapon), (1, self.off_hand)):
            if weapon is not None:
                self._dropped.append((weapon, self.position))
                self._hold(hand, None)

    def within_grasp(self, weapon: Weapon | None = None) -> int | None:
        """Where in `dropped` the first `weapon`, or the first weapon, lies
        that this creature stands within 5 feet of and has a hand free for;
        `None` when there is none."""
        for index, (fallen, where) in enumerate(self._dropped):
            if (
                (weapon is None or fallen == weapon)
                and self.can_hold(fallen)
                and in_reach(self.position, where, _GRASP_FT, origin_size=self.size)
            ):
                return index
        return None

    def pick_up_weapon(self, weapon: Weapon | None = None) -> None:
        """Hold again a weapon this creature dropped: the one
        `within_grasp(weapon)` finds.

        Raises `ValueError` when there is none.
        """
        index = self.within_grasp(weapon)
        if index is None:
            raise ValueError(f"{self.name} has nothing of that dropped to pick up")
        fallen, _ = self._dropped.pop(index)
        self._hold(self._free_hand(fallen), fallen)

    @property
    def is_alive(self) -> bool:
        """Whether this creature is alive, which a creature at 0 hit points
        making death saving throws still is."""
        return not self._dead

    @property
    def is_down(self) -> bool:
        """Whether this creature is at 0 hit points: dying, stable or dead."""
        return self.hp <= 0

    # -- dying -------------------------------------------------------------------
    # Four read-only facts and the six methods that move them, which keep the
    # tallies' invariants (XPHB, "Dropping to 0 Hit Points" -> Death Saving
    # Throws; `docs/decisions.md` § w1j).

    @property
    def dead(self) -> bool:
        return self._dead

    @property
    def stable(self) -> bool:
        return self._stable

    @property
    def death_save_successes(self) -> int:
        return self._death_save_successes

    @property
    def death_save_failures(self) -> int:
        return self._death_save_failures

    def die(self) -> None:
        """Mark this creature dead. Its hit points are not touched."""
        self._dead = True
        self._stable = False

    def revive(self) -> None:
        """Bring this creature back from death, with clean tallies."""
        self._dead = False
        self.reset_death_saves()

    def reset_death_saves(self) -> None:
        """Reset what regaining hit points resets: both tallies, and Stable."""
        self._stable = False
        self._death_save_successes = 0
        self._death_save_failures = 0

    def stabilize(self) -> None:
        """Make this creature Stable, which resets both tallies."""
        self.reset_death_saves()
        self._stable = True

    def succeed_death_save(self) -> bool:
        """Count one success, and return whether it made the creature Stable."""
        self._death_save_successes += 1
        if self._death_save_successes < DEATH_SAVE_SUCCESSES_TO_STABILIZE:
            return False
        self.stabilize()
        return True

    def fail_death_save(self, failures: int = 1) -> None:
        """Count `failures` and end Stable; the third failure is death."""
        self._stable = False
        self._death_save_failures += failures
        if self._death_save_failures >= DEATH_SAVE_FAILURES_TO_DIE:
            self.die()

    # -- the hook pipeline -----------------------------------------------------

    def attach(self, source: HookSource) -> None:
        """Attach a feature, condition, effect or item to this creature.

        Attachment order is the last tiebreak in dispatch order (DESIGN.md §
        Ordering). Raises `ValueError` if `source` is owned by another creature.
        """
        if source.owner is not self:
            raise ValueError(
                f"{source!r} belongs to {source.owner.name} and cannot be "
                f"attached to {self.name}: `self.owner` inside every one of its "
                "handlers would be the wrong creature, which is the one thing "
                "hook Scope exists to make impossible to get wrong."
            )
        self.dispatcher.attach(source)
        if isinstance(source, Effect) and source.CONDITION is Condition.UNCONSCIOUS:
            self._unconscious += 1
            self._armour_class = self._derive_armour_class()

    def detach(self, source: HookSource) -> None:
        """Remove a source — an effect expiring, an item unequipped."""
        self.dispatcher.detach(source)
        if isinstance(source, Effect) and source.CONDITION is Condition.UNCONSCIOUS:
            self._unconscious -= 1
            self._armour_class = self._derive_armour_class()

    def dispatch(self, spec: HookSpec, ctx: Context) -> None:
        """Run this creature's handlers for `spec` over `ctx`; a hook nothing
        listens to is a no-op."""
        self.dispatcher.dispatch(spec, ctx)

    def __repr__(self) -> str:
        return f"<{self.name} {self.hp}/{self.max_hp} hp at {self.position}>"
