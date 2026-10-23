"""Grappled (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import SourcedCondition
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Grappled"]


class Grappled(SourcedCondition):
    """Held: Speed 0, and Disadvantage against anything but the grappler, who is
    this effect's `caster`."""

    __slots__ = ()

    CONDITION = enums.Condition.GRAPPLED
    NEEDS_HOOK = (
        "nothing can change a creature's Speed: "
        '"Your Speed is 0 and can\'t increase." '
        "(ROADMAP.md § The W0 debt register #47)",
        "nothing moves a creature with another creature, and no movement is "
        'charged against the economy yet: "The grappler can drag or carry you '
        'when it moves, but every foot of movement costs it 1 extra foot." '
        "(ROADMAP.md § The W0 debt register #28)",
    )

    @hook("on_declare_attack")
    def attacks(self, ctx: AttackDeclaredContext) -> R:
        """Imposes "You have Disadvantage on attack rolls against any target
        other than the grappler"."""
        if ctx.defender is self.caster:
            return R.SKIPPED
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
