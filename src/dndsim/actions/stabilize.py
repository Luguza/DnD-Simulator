"""Stabilising a dying creature with a Wisdom (Medicine) check (XPHB,
"Dropping to 0 Hit Points" -> Stabilizing a Character)."""

from typing import ClassVar, Final

from dndsim.actions.base import Action
from dndsim.core.geometry import in_reach
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.dying import stabilize
from dndsim.rules.enums import ActionCost

__all__ = ["REACH_FT", "Stabilize"]

REACH_FT: Final = 5
"""How close the helper must stand (`docs/decisions.md` § w2b)."""


class Stabilize(Action[bool]):
    """ "You can take the Help action to try to stabilize a creature with 0 Hit
    Points, which requires a successful DC 10 Wisdom (Medicine) check."

    The check is `engine/dying.py`'s `stabilize()`; `execute()` returns
    whether it succeeded.
    """

    __slots__ = ("target",)

    ID: ClassVar[str] = "stabilize"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def __init__(self, actor: Creature, target: Creature) -> None:
        super().__init__(actor)
        self.target = target

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        # `engine/dying.py`'s `_dying()`, which `stabilize()` raises on.
        dying = (
            target.hp <= 0
            and not target.dead
            and not target.stable
            and target.player_character
        )
        return dying and in_reach(
            actor.position,
            target.position,
            REACH_FT,
            origin_size=actor.size,
            destination_size=target.size,
        )

    def _perform(self, rng: Rng) -> bool:
        return stabilize(self.actor, self.target, rng=rng)
