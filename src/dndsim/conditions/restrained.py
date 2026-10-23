"""Restrained (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import AttackDeclaredContext, SavingThrowContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Restrained"]


class Restrained(Condition):
    """Held fast: Speed 0, the attack rolls swing both ways, and Dexterity
    saves are at Disadvantage."""

    __slots__ = ()

    CONDITION = enums.Condition.RESTRAINED
    NEEDS_HOOK = (
        "nothing can change a creature's Speed: "
        '"Your Speed is 0 and can\'t increase." '
        "(ROADMAP.md § The W0 debt register #47)",
    )

    @hook("on_declare_attack")
    def own_attacks(self, ctx: AttackDeclaredContext) -> R:
        """The Disadvantage half of "Attack rolls against you have Advantage,
        and your attack rolls have Disadvantage"."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_incoming_attack_declared")
    def incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """The Advantage half of the same sentence."""
        ctx.grant_advantage(self.name)
        return R.APPLIED

    @hook("on_saving_throw")
    def dexterity_saves(self, ctx: SavingThrowContext) -> R:
        """Imposes "You have Disadvantage on Dexterity saving throws", and
        nothing on the other five."""
        if ctx.ability is not enums.Ability.DEXTERITY:
            return R.SKIPPED
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
