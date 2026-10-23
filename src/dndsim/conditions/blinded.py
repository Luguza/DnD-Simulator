"""Blinded (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Blinded"]


class Blinded(Condition):
    """You cannot see: your attacks are at Disadvantage and attacks on you have
    Advantage."""

    __slots__ = ()

    CONDITION = enums.Condition.BLINDED
    NEEDS_HOOK = (
        "AbilityCheckContext has no way to fail a check, and no check declares "
        "that it requires sight: \"You can't see and automatically fail any "
        'ability check that requires sight." '
        "(ROADMAP.md § The W0 debt register #38)",
        "no creature can be asked what it can see, so a blinded creature is "
        "still offered the opportunity attacks it cannot legally take "
        "(ROADMAP.md § The W0 debt register #39, and #21 is the consequence)",
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
