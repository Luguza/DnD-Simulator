"""Standing up from Prone (XPHB, Rules Glossary "Prone")."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.conditions.prone import Prone
from dndsim.core.rng import Rng
from dndsim.rules.enums import ActionCost

__all__ = ["StandUp"]


class StandUp(Action[None]):
    """`actor` spends half its Speed, rounded down, of its movement to end the
    Prone condition. Refused at Speed 0 and with less movement left; never ends
    Unconscious, which is a kind of Prone.

    Paid in feet, so `COST` is `None`, and legal while Incapacitated, as a
    walk is (`docs/decisions.md` § w2j).
    """

    __slots__ = ()

    ID: ClassVar[str] = "stand_up"
    COST: ClassVar[ActionCost | None] = None

    def is_legal(self) -> bool:
        actor = self.actor
        if actor.dead or actor.is_down:
            return False
        return self._permits()

    def _permits(self) -> bool:
        actor = self.actor
        speed = actor.speed_ft
        return (
            speed > 0
            and actor.economy.can_move(speed // 2)
            and any(type(effect) is Prone for effect in actor.effects)
        )

    def _perform(self, rng: Rng) -> None:
        actor = self.actor
        actor.economy.move(actor.speed_ft // 2)
        for effect in tuple(actor.effects):
            if type(effect) is Prone:
                actor.effects.remove(effect)
