"""`Creature` — something that stands somewhere, has hit points, and answers when
the engine asks.

A creature holds its stat block, equipment and the AC derived from them, what
it holds in each hand and carries beside, its
hit points and position, its `ActionEconomy`, its own `Dispatcher` (which
spends from that economy), its `EffectStore` and its `Resources`. Hit points
are plain attributes with no damage arithmetic here (`docs/decisions.md`
§ w0g2, § w1a).
"""

from collections.abc import Iterable, Mapping
from typing import Final

from dndsim.core.geometry import Cell
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.economy import LIGHT_WEAPON_GONE, ActionEconomy
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


def hands_for(weapon: Weapon) -> int:
    """How many hands wielding `weapon` fills: two for a Two-Handed weapon and
    for a Versatile one the builder handed its two-handed die, else one."""
    if weapon.has(WeaponProperty.TWO_HANDED):
        return 2
    versatile = weapon.versatile_damage
    return 2 if versatile is not None and weapon.damage == versatile else 1


UNSEATED: Final = InitiativeKey(
    initiative=0, dexterity=10, player_character=False, tiebreak=0.0
)
"""The place of a creature nobody has rolled initiative for yet, shared by every
such creature."""


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
        "_dead",
        "_dropped",
        "_stock",
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
        carried: Iterable[tuple[Weapon | str, int]] | None = None,
        armour: Armour = UNARMOURED,
        shield: bool = False,
        initiative: InitiativeKey = UNSEATED,
        player_character: bool = False,
    ) -> None:
        self.name = name
        self._stats = stats
        # The two hands: `weapon` is the one an attack naming none uses.
        self.weapon = weapon
        self.off_hand = off_hand
        # What is carried beside, by name: weapons stowed and pieces of
        # ammunition. `None` is a creature whose kit is not counted, which
        # never runs out (`docs/decisions.md` § w2k).
        self._stowed: dict[str, Weapon] | None = None
        self._stock: dict[str, int] | None = None
        if carried is not None:
            self._stowed, self._stock = {}, {}
            for item, count in carried:
                if count < 1:
                    raise ValueError(f"{self.name} carries {count} of {item}")
                if isinstance(item, Weapon):
                    self._stowed.setdefault(item.name, item)
                    item = item.name
                self._stock[item] = self._stock.get(item, 0) + count
        self._dropped: list[tuple[Weapon, Cell]] = []
        self._armour = armour
        self._shield = shield
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
        """How far this creature threatens: its weapon's reach, 5 feet unarmed.

        Size is not added: reach is measured between footprints
        (`docs/decisions.md` § w1d).
        """
        return 5 if self.weapon is None else self.weapon.reach_ft

    @property
    def hands_held(self) -> int:
        """How many hands what this creature holds fills: each weapon by
        `hands_for()`, a Shield one. A grapple is held by an effect, not here
        (`docs/decisions.md` § w2j)."""
        held = 1 if self._shield else 0
        for weapon in (self.weapon, self.off_hand):
            if weapon is not None:
                held += hands_for(weapon)
        return held

    def holding(self, name: str) -> int | None:
        """The hand (0 for `weapon`, 1 for `off_hand`) holding a weapon called
        `name`, or `None`."""
        weapon = self.weapon
        if weapon is not None and weapon.name == name:
            return 0
        off_hand = self.off_hand
        if off_hand is not None and off_hand.name == name:
            return 1
        return None

    def held(self, hand: int) -> Weapon | None:
        """What is in `hand`, 0 or 1."""
        return self.weapon if hand == 0 else self.off_hand

    # -- what it carries ------------------------------------------------------

    @property
    def counts_carried(self) -> bool:
        """Whether what this creature carries is counted, and can run out."""
        return self._stock is not None

    def carried(self, name: str) -> int:
        """How many of a weapon called `name`, or of a piece of ammunition,
        are carried beside what is held; 0 when nothing is counted."""
        stock = self._stock
        return 0 if stock is None else stock.get(name, 0)

    @property
    def stowed(self) -> tuple[Weapon, ...]:
        """One of each weapon carried beside what is held, in the order first
        carried."""
        stowed, stock = self._stowed, self._stock
        if stowed is None or stock is None:
            return ()
        return tuple(weapon for name, weapon in stowed.items() if stock[name])

    def has_ammunition(self, piece: str) -> bool:
        """Whether a piece of `piece` is carried to fire; always, when nothing
        is counted."""
        stock = self._stock
        return stock is None or stock.get(piece, 0) > 0

    def spend_ammunition(self, piece: str) -> None:
        """Spend one `piece` of ammunition, when what is carried is counted.

        Raises `ValueError` when none is carried.
        """
        stock = self._stock
        if stock is None:
            return
        if stock.get(piece, 0) < 1:
            raise ValueError(f"{self.name} carries no {piece} to fire")
        stock[piece] -= 1

    def draw(self, name: str) -> int:
        """Take a carried weapon called `name` into the first free hand, and
        return that hand.

        Raises `ValueError` when none is carried or no hand is free for it.
        """
        stowed = self._stowed
        if stowed is None or self.carried(name) < 1:
            raise ValueError(f"{self.name} carries no {name} to draw")
        weapon = stowed[name]
        if HANDS - self.hands_held < hands_for(weapon):
            raise ValueError(f"{self.name} has no hand free to draw {name}")
        stock = self._stock
        assert stock is not None
        stock[name] -= 1
        if self.weapon is None:
            self.weapon = weapon
            return 0
        self.off_hand = weapon
        return 1

    def stow(self, hand: int) -> None:
        """Put the weapon in `hand` away with what is carried.

        Raises `ValueError` for an empty hand, or a creature whose kit is not
        counted.
        """
        weapon = self.held(hand)
        stowed, stock = self._stowed, self._stock
        if weapon is None or stowed is None or stock is None:
            raise ValueError(f"{self.name} has nothing in hand {hand} to stow")
        stowed.setdefault(weapon.name, weapon)
        stock[weapon.name] = stock.get(weapon.name, 0) + 1
        self.let_go(hand)

    def let_go(self, hand: int) -> Weapon:
        """Empty `hand` — a weapon thrown, stowed or dropped — and return what
        it held. Raises `ValueError` for an empty hand."""
        weapon = self.held(hand)
        if weapon is None:
            raise ValueError(f"{self.name} holds nothing in hand {hand}")
        if hand == 0:
            self.weapon = None
        else:
            self.off_hand = None
        economy = self.economy
        if economy.light_hand == hand:
            economy.light_hand = LIGHT_WEAPON_GONE
        return weapon

    @property
    def dropped(self) -> tuple[tuple[Weapon, Cell], ...]:
        """Each weapon `drop_held()` let go of and the square it fell in,
        until `pick_up_weapon()` takes it back."""
        return tuple(self._dropped)

    def drop_held(self) -> None:
        """Drop every weapon held where this creature stands. A Shield is worn,
        not held (`docs/decisions.md` § w2b)."""
        for hand in (0, 1):
            if self.held(hand) is not None:
                self._dropped.append((self.let_go(hand), self.position))

    def pick_up_weapon(
        self, weapon: Weapon | None = None, at: Cell | None = None
    ) -> None:
        """Wield again `weapon` lying at `at`, or the first weapon this
        creature dropped, in the first free hand.

        Raises `ValueError` when it dropped no such weapon or has no hand free
        for it.
        """
        dropped = self._dropped
        index = next(
            (
                i
                for i, (lying, where) in enumerate(dropped)
                if (weapon is None or lying is weapon) and (at is None or where == at)
            ),
            None,
        )
        if index is None:
            raise ValueError(f"{self.name} has dropped nothing to pick up")
        lying = dropped[index][0]
        if HANDS - self.hands_held < hands_for(lying):
            raise ValueError(f"{self.name} has no hand free to pick up {lying.name}")
        del dropped[index]
        if self.weapon is None:
            self.weapon = lying
        else:
            self.off_hand = lying

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
