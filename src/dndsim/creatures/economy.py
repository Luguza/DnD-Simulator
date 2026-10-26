"""`ActionEconomy` — what a creature still has to spend this turn.

A ledger of an Action, a Bonus Action, a Reaction and a number of feet, with no
rules about what they buy or when a turn begins. Spending what is not there
raises `EconomyError`; `can_spend()` and `can_move()` ask for free. The reaction
comes back only in `turn_started()`.

XPHB (2024) "Combat" (Your Turn, Reactions, Movement); `docs/decisions.md`
§ w1a.
"""

from types import MappingProxyType
from typing import Final

from dndsim.core.errors import EconomyError
from dndsim.rules.enums import ActionCost

__all__ = ["ActionEconomy"]

_ACTION: Final = ActionCost.ACTION
_BONUS_ACTION: Final = ActionCost.BONUS_ACTION

_NAMES: Final = MappingProxyType(
    {
        ActionCost.ACTION: "Action",
        ActionCost.BONUS_ACTION: "Bonus Action",
        ActionCost.REACTION: "Reaction",
    }
)
"""What the rules call each slot, for the errors a caller reads."""


class ActionEconomy:
    """One creature's unspent Actions, Bonus Actions, Reactions and movement.

    Satisfies the `Economy` protocol (`can_spend`, `spend`). Each slot is a
    count, not a flag, so a grant adds one more (`docs/decisions.md` § w1a).
    """

    __slots__ = ("actions", "bonus_actions", "movement_ft", "reactions")

    def __init__(self) -> None:
        # Reaction in hand and nothing else until `turn_started()`.
        self.actions = 0
        self.bonus_actions = 0
        self.reactions = 1
        self.movement_ft = 0

    # -- the `Economy` protocol ------------------------------------------------

    def can_spend(self, cost: ActionCost) -> bool:
        """Whether one of `cost` is still in hand. Changes nothing."""
        if cost is _ACTION:
            return self.actions > 0
        if cost is _BONUS_ACTION:
            return self.bonus_actions > 0
        return self.reactions > 0

    def spend(self, cost: ActionCost) -> None:
        """Spend one of `cost`, or raise if there was none to spend."""
        if cost is _ACTION:
            if self.actions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.actions -= 1
        elif cost is _BONUS_ACTION:
            if self.bonus_actions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.bonus_actions -= 1
        else:
            if self.reactions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.reactions -= 1

    # -- what features and the turn loop do to it ------------------------------

    def grant(self, cost: ActionCost, count: int = 1) -> None:
        """Hand the creature `count` more of an Action, Bonus Action or
        Reaction."""
        if count < 1:
            raise EconomyError(f"a grant of {count} {_NAMES[cost]}s grants nothing")
        if cost is _ACTION:
            self.actions += count
        elif cost is _BONUS_ACTION:
            self.bonus_actions += count
        else:
            self.reactions += count

    def can_move(self, distance_ft: int) -> bool:
        """Whether the creature still has that many feet of movement."""
        return distance_ft <= self.movement_ft

    def move(self, distance_ft: int) -> None:
        """Spend feet of movement, or raise if the creature has not got them."""
        if distance_ft < 0:
            raise EconomyError(f"{distance_ft} ft is not a distance to move")
        if distance_ft > self.movement_ft:
            raise EconomyError(
                f"moving {distance_ft} ft costs more than the "
                f"{self.movement_ft} ft this creature has left this turn"
            )
        self.movement_ft -= distance_ft

    def grant_movement(self, distance_ft: int) -> None:
        """Add feet of movement."""
        if distance_ft < 1:
            raise EconomyError(f"a grant of {distance_ft} ft grants nothing")
        self.movement_ft += distance_ft

    def turn_started(self, movement_ft: int) -> None:
        """The creature's own turn has begun: everything back.

        `movement_ft` is the creature's speed now; the ledger keeps no copy of
        it (`docs/decisions.md` § w1a).
        """
        if movement_ft < 0:
            raise EconomyError(f"a speed of {movement_ft} ft is not a speed")
        self.actions = 1
        self.bonus_actions = 1
        self.reactions = 1
        self.movement_ft = movement_ft

    def _nothing_to_spend(self, cost: ActionCost) -> str:
        if cost is ActionCost.REACTION:
            return (
                "this creature has already spent its reaction and has not "
                "started a turn since; a reaction comes back at the start of "
                "each of your own turns, never at the top of a round "
                "(DESIGN.md § Failing loudly). Ask `can_spend()` first."
            )
        return (
            f"this creature has no {_NAMES[cost]} left to spend. Asking is free: "
            "`can_spend()` answers before anything is spent, and the turn loop "
            "offers only what the economy can still pay for."
        )

    def __repr__(self) -> str:
        return (
            f"<economy {self.actions}a {self.bonus_actions}b "
            f"{self.reactions}r {self.movement_ft}ft>"
        )
