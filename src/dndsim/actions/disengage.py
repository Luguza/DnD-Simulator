"""The Disengage action (XPHB, "Actions" -> Disengage)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.rng import Rng
from dndsim.core.timeline import Duration
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.rules.enums import ActionCost

__all__ = ["Disengage", "Disengaged", "disengaged"]


class Disengaged(Effect):
    """ "your movement doesn't provoke Opportunity Attacks for the rest of the
    current turn." No handler: the opportunity attack asks `disengaged()`
    (`docs/decisions.md` § w2b)."""

    __slots__ = ()


def disengaged(creature: Creature) -> bool:
    """Whether `creature`'s movement provokes no Opportunity Attacks now."""
    return any(type(effect) is Disengaged for effect in creature.effects)


class Disengage(Action[None]):
    """Applies `Disengaged` until the end of the current turn."""

    __slots__ = ()

    ID: ClassVar[str] = "disengage"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def _permits(self) -> bool:
        return True

    def _perform(self, rng: Rng) -> None:
        actor = self.actor
        actor.effects.apply(
            Disengaged(actor, duration=Duration.until_end_of_current_turn())
        )
