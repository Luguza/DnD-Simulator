"""The Dodge action (XPHB, "Actions" -> Dodge)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.core.rng import Rng
from dndsim.core.timeline import Duration
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.hooks.contexts import AttackDeclaredContext, SavingThrowContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules.enums import Ability, ActionCost

__all__ = ["Dodge", "Dodging"]


class Dodging(Effect):
    """ "any attack roll made against you has Disadvantage if you can see the
    attacker, and you make Dexterity saving throws with Advantage. You lose
    these benefits if you have the Incapacitated condition or if your Speed
    is 0."

    Every attacker is taken as seen (`docs/decisions.md` § w2b).
    """

    __slots__ = ("_creature",)

    def __init__(self, owner: Creature, *, duration: Duration) -> None:
        super().__init__(owner, duration=duration)
        self._creature = owner

    def _lapsed(self) -> bool:
        creature = self._creature
        return creature.speed_ft == 0 or any(
            isinstance(effect, Incapacitated) for effect in creature.effects
        )

    @hook("on_incoming_attack_declared")
    def attacks_against(self, ctx: AttackDeclaredContext) -> R:
        """Imposes the Disadvantage on an attack roll against the owner."""
        if self._lapsed():
            return R.SKIPPED
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_saving_throw")
    def dexterity_saves(self, ctx: SavingThrowContext) -> R:
        """Grants the Advantage on a Dexterity save, and on no other."""
        if ctx.ability is not Ability.DEXTERITY or self._lapsed():
            return R.SKIPPED
        ctx.grant_advantage(self.name)
        return R.APPLIED


class Dodge(Action[None]):
    """Applies `Dodging` "until the start of your next turn"."""

    __slots__ = ()

    ID: ClassVar[str] = "dodge"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def _permits(self) -> bool:
        return True

    def _perform(self, rng: Rng) -> None:
        actor = self.actor
        duration = Duration.until_start_of_next_turn_of(actor.initiative)
        actor.effects.apply(Dodging(actor, duration=duration))
