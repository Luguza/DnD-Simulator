"""Picking up a dropped weapon: the object interaction a turn includes (XPHB,
"Combat" -> Your Turn, Interacting with Things)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import Weapon
from dndsim.rules.enums import ActionCost

__all__ = ["PickUpWeapon"]


class PickUpWeapon(Action[None]):
    """Hold again `weapon`, or the first weapon, the actor dropped, from
    within 5 feet of where it fell, with a hand free for it. Spends the turn's
    object interaction (`docs/decisions.md` § w2b, § w2k)."""

    __slots__ = ("weapon",)

    ID: ClassVar[str] = "pick_up_weapon"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, weapon: Weapon | None = None) -> None:
        super().__init__(actor)
        self.weapon = weapon

    def _permits(self) -> bool:
        actor = self.actor
        return (
            actor.economy.can_interact() and actor.within_grasp(self.weapon) is not None
        )

    def _perform(self, rng: Rng) -> None:
        self.actor.economy.interact()
        self.actor.pick_up_weapon(self.weapon)
