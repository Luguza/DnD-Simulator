"""Picking up a dropped weapon: the object interaction a turn includes (XPHB,
"Combat" -> Your Turn, Interacting with Things)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.geometry import in_reach
from dndsim.core.rng import Rng
from dndsim.rules.enums import ActionCost

__all__ = ["PickUpWeapon"]


class PickUpWeapon(Action[None]):
    """Wield again the weapon the actor dropped, from within 5 feet of where it
    fell. Free, and the once-a-turn limit is not counted
    (`docs/decisions.md` § w2b)."""

    __slots__ = ()

    ID: ClassVar[str] = "pick_up_weapon"
    COST: ClassVar[ActionCost | None] = None

    def _permits(self) -> bool:
        actor = self.actor
        where = actor.dropped_at
        return (
            actor.weapon is None
            and where is not None
            and in_reach(actor.position, where, 5, origin_size=actor.size)
        )

    def _perform(self, rng: Rng) -> None:
        self.actor.pick_up_weapon()
