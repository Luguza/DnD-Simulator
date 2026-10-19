"""Frightened (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import SourcedCondition
from dndsim.hooks.contexts import AbilityCheckContext, AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Frightened"]


class Frightened(SourcedCondition):
    """Afraid of this effect's `caster`: Disadvantage while it is in sight, and
    no approach."""

    __slots__ = ()

    CONDITION = enums.Condition.FRIGHTENED
    NEEDS_HOOK = (
        "the APL chooses where a creature moves and no route is refused for "
        "where it ends: \"You can't willingly move closer to the source of "
        'fear." '
        "(ROADMAP.md § The W0 debt register #28)",
    )
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
