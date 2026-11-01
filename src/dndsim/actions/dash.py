"""The Dash action (XPHB, "Actions" -> Dash)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.rng import Rng
from dndsim.rules.enums import ActionCost

__all__ = ["Dash"]


class Dash(Action[None]):
    """ "you gain extra movement for the current turn. The increase equals your
    Speed." Not offered at Speed 0, where it would buy nothing."""

    __slots__ = ()

    ID: ClassVar[str] = "dash"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def _permits(self) -> bool:
        return self.actor.speed_ft > 0

    def _perform(self, rng: Rng) -> None:
        self.actor.economy.grant_movement(self.actor.speed_ft)
