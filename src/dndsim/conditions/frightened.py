"""Frightened (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import SourcedCondition
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    AttackDeclaredContext,
    InitiativeContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Frightened"]


class Frightened(SourcedCondition):
    """Afraid of this effect's `caster`: Disadvantage while it is in sight, and
    no approach, which `NO_APPROACH` declares (`docs/decisions.md` § w2c2)."""

    __slots__ = ()

    CONDITION = enums.Condition.FRIGHTENED
    NO_APPROACH = True
    APPROXIMATED = (
        "the line-of-sight test is not made, because no context carries a "
        "position and nothing can be asked what it sees, so Disadvantage is "
        "imposed even while the source of fear is out of sight "
        "(ROADMAP.md § The W0 debt register #39)",
    )

    @hook("on_declare_attack")
    def attacks(self, ctx: AttackDeclaredContext) -> R:
        """The attack half of "You have Disadvantage on ability checks and
        attack rolls while the source of fear is within line of sight"."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_ability_check")
    def ability_checks(self, ctx: AbilityCheckContext) -> R:
        """The check half of the same sentence."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_initiative_roll")
    def initiative_rolls(self, ctx: InitiativeContext) -> R:
        """The check half again, for Initiative, which is a Dexterity check."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
