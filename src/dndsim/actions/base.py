"""`Action` — the Command a turn is made of: asked first, then carried out.

`is_legal()` reads and spends nothing; `execute()` spends the action's cost
and does it. `docs/decisions.md` § w2b; DESIGN.md § The APL.
"""

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.rules.enums import ActionCost

__all__ = ["Action"]

_Result = TypeVar("_Result")


class Action(ABC, Generic[_Result]):
    """One thing `actor` could do now, bound to its arguments; `execute()`
    returns a `_Result`.

    `COST` is the economy slot it spends, or `None` for what is free — the
    object interaction a turn carries (`docs/decisions.md` § w2b).
    """

    __slots__ = ("actor",)

    ID: ClassVar[str]
    """The action's id, as an APL entry names it."""

    COST: ClassVar[ActionCost | None]

    def __init__(self, actor: Creature) -> None:
        self.actor = actor

    def is_legal(self) -> bool:
        """Whether `execute()` would go ahead now. Changes nothing.

        Asks that the actor is up and alive and can pay `COST`, then the
        action's own `_permits()`. Incapacitation is not asked here (debt #41).
        """
        actor = self.actor
        if actor.dead or actor.is_down:
            return False
        cost = self.COST
        if cost is not None and not actor.economy.can_spend(cost):
            return False
        return self._permits()

    def execute(self, *, rng: Rng) -> _Result:
        """Spend `COST` and carry the action out.

        Raises `ValueError` when `is_legal()` would have said no.
        """
        if not self.is_legal():
            raise ValueError(
                f"{self.actor.name} cannot take {self.ID} now; is_legal() "
                "answers before anything is spent"
            )
        cost = self.COST
        if cost is not None:
            self.actor.economy.spend(cost)
        return self._perform(rng)

    @abstractmethod
    def _permits(self) -> bool:
        """What this action alone asks of the moment. Must change nothing."""

    @abstractmethod
    def _perform(self, rng: Rng) -> _Result:
        """Do it, the cost already paid."""

    def __repr__(self) -> str:
        return f"<{self.ID} by {self.actor.name}>"
