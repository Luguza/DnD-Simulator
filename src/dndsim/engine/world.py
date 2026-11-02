"""`World` — every creature in one encounter, the side each fights on, the clock
their stores share, and the log the fight is written to.

Seating a creature is what hands its effect store and its resources the
encounter's clock, log and roster (debts #29, #30, #50; `docs/decisions.md`
§ w2c).
"""

from collections.abc import Iterator

from dndsim.core.log import EventLog
from dndsim.core.timeline import TimeKey, TurnPhase
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.effects import Clock
from dndsim.hooks.contexts import Combatant

__all__ = ["World"]


class World:
    """The roster of one encounter, in the order creatures were seated.

    Satisfies `creatures/effects.py`'s `Roster`. `side` is any label; two
    creatures with the same one are allies.
    """

    __slots__ = ("_sides", "clock", "log")

    def __init__(self, *, log: EventLog | None = None) -> None:
        self.clock = Clock(TimeKey(1, UNSEATED, TurnPhase.START))
        self.log = log
        self._sides: dict[Creature, str] = {}

    def seat(self, creature: Creature, side: str) -> None:
        """Put `creature` in the encounter on `side`. Raises `ValueError` for a
        creature already seated."""
        if creature in self._sides:
            raise ValueError(f"{creature.name} is already seated")
        self._sides[creature] = side
        effects = creature.effects
        effects.join(self.clock)
        effects.roster = self
        effects.log = self.log
        creature.resources.log = self.log

    def leave(self, creature: Creature) -> None:
        """Take `creature` out of the encounter. Its `InitiativeKey` is left
        alone, so a deadline anchored to it still falls where it did."""
        del self._sides[creature]
        effects = creature.effects
        effects.join(Clock(self.clock.now))
        effects.roster = None
        effects.log = None
        creature.resources.log = None

    def __contains__(self, creature: object) -> bool:
        return creature in self._sides

    def __iter__(self) -> Iterator[Creature]:
        return iter(self._sides)

    def __len__(self) -> int:
        return len(self._sides)

    def side_of(self, creature: Creature) -> str:
        return self._sides[creature]

    def holder(self, combatant: Combatant) -> Creature | None:
        """The seated creature `combatant` is, or `None`."""
        for creature in self._sides:
            if creature is combatant:
                return creature
        return None
