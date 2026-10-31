"""`Creature` — something that stands somewhere, has hit points, and answers when
the engine asks.

A creature holds its stat block, equipment and the AC derived from them, its
hit points and position, its `ActionEconomy`, its own `Dispatcher` (which
spends from that economy), its `EffectStore` and its `Resources`. Hit points
are plain attributes with no damage arithmetic here (`docs/decisions.md`
§ w0g2, § w1a).
"""

from collections.abc import Mapping
from typing import Final

from dndsim.core.geometry import Cell
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.effects import EffectStore
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
from dndsim.rules.enums import Ability, Size

__all__ = ["UNSEATED", "Creature"]


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
        "_dropped_at",
        "_dropped_weapon",
        "_death_save_failures",
        "_death_save_successes",
        "_stable",
        "dispatcher",
        "economy",
        "effects",
        "hp",
        "initiative",
        "name",
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
        armour: Armour = UNARMOURED,
        shield: bool = False,
        initiative: InitiativeKey = UNSEATED,
        player_character: bool = False,
    ) -> None:
        self.name = name
        self._stats = stats
        self.weapon = weapon
        # Moved together, and only by `drop_held()` and `pick_up_weapon()`.
        self._dropped_weapon: Weapon | None = None
        self._dropped_at: Cell | None = None
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
        self._armour_class = self._derive_armour_class()
        self.economy = ActionEconomy()
        self.dispatcher = Dispatcher(economy=self.economy)
        # Trap: `UNSEATED` is a seat in the order, not a position ahead of it,
        # so a duration anchored to a creature that sorts before it already
        # reads as begun (debt #29).
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
        """Whether a Shield is in hand, for `SHIELD_AC_BONUS`."""
        return self._shield

    @shield.setter
    def shield(self, shield: bool) -> None:
        self._shield = shield
        self._armour_class = self._derive_armour_class()

    def _derive_armour_class(self) -> int:
        """The one place an AC is computed; called when equipment changes."""
        armour_class = self._armour.armour_class(self._stats)
        return armour_class + SHIELD_AC_BONUS if self._shield else armour_class

    # -- what changes during a fight ------------------------------------------

    @property
    def reach_ft(self) -> int:
        """How far this creature threatens: its weapon's reach, 5 feet unarmed.

        Size is not added: reach is measured between footprints
        (`docs/decisions.md` § w1d).
        """
        return 5 if self.weapon is None else self.weapon.reach_ft

    @property
    def dropped_weapon(self) -> Weapon | None:
        """What `drop_held()` let go of, until `pick_up_weapon()` takes it
        back."""
        return self._dropped_weapon

    @property
    def dropped_at(self) -> Cell | None:
        """The square `dropped_weapon` fell in."""
        return self._dropped_at

    def drop_held(self) -> None:
        """Drop the wielded weapon where this creature stands. Holding nothing
        drops nothing; a Shield is worn, not held (`docs/decisions.md` § w2b).
        """
        weapon = self.weapon
        if weapon is None:
            return
        self._dropped_weapon = weapon
        self._dropped_at = self.position
        self.weapon = None

    def pick_up_weapon(self) -> None:
        """Wield the weapon this creature dropped again.

        Raises `ValueError` when it dropped nothing or is wielding another.
        """
        weapon = self._dropped_weapon
        if weapon is None:
            raise ValueError(f"{self.name} has dropped nothing to pick up")
        if self.weapon is not None:
            raise ValueError(
                f"{self.name} is wielding {self.weapon.name} and cannot pick up "
                f"{weapon.name} as well"
            )
        self.weapon = weapon
        self._dropped_weapon = None
        self._dropped_at = None

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

    def detach(self, source: HookSource) -> None:
        """Remove a source — an effect expiring, an item unequipped."""
        self.dispatcher.detach(source)

    def dispatch(self, spec: HookSpec, ctx: Context) -> None:
        """Run this creature's handlers for `spec` over `ctx`; a hook nothing
        listens to is a no-op."""
        self.dispatcher.dispatch(spec, ctx)

    def __repr__(self) -> str:
        return f"<{self.name} {self.hp}/{self.max_hp} hp at {self.position}>"
