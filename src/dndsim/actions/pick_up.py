"""Picking up a dropped weapon: the object interaction a turn includes (XPHB,
"Combat" -> Your Turn, Interacting with Things)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.geometry import Cell, in_reach
from dndsim.core.rng import Rng
from dndsim.creatures.creature import HANDS, Creature, hands_for
from dndsim.creatures.stats import Weapon
from dndsim.rules.enums import ActionCost

__all__ = ["PickUpWeapon"]


class PickUpWeapon(Action[None]):
    """Wield again `weapon`, which the actor dropped at `at`, from within 5
    feet of it, in a free hand. Spends the turn's object interaction, and no
    Action (`docs/decisions.md` § w2b, § w2k)."""

    __slots__ = ("at", "weapon")

    ID: ClassVar[str] = "pick_up_weapon"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, weapon: Weapon, at: Cell) -> None:
        super().__init__(actor)
        self.weapon = weapon
        self.at = at

    def _permits(self) -> bool:
        actor, weapon = self.actor, self.weapon
        return (
            actor.economy.can_interact()
            and any(
                lying is weapon and where == self.at for lying, where in actor.dropped
            )
            and HANDS - actor.hands_held >= hands_for(weapon)
            and in_reach(actor.position, self.at, 5, origin_size=actor.size)
        )

    def _perform(self, rng: Rng) -> None:
        self.actor.economy.interact()
        self.actor.pick_up_weapon(self.weapon, self.at)
