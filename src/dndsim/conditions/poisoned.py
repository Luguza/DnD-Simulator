"""Poisoned (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import AbilityCheckContext, AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Poisoned"]


class Poisoned(Condition):
    """Disadvantage on attack rolls and ability checks."""

    __slots__ = ()

    CONDITION = enums.Condition.POISONED

    @hook("on_declare_attack")
    def attacks(self, ctx: AttackDeclaredContext) -> R:
        """The attack half of "You have Disadvantage on attack rolls and
        ability checks"."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_ability_check")
    def ability_checks(self, ctx: AbilityCheckContext) -> R:
        """The check half of the same sentence."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
