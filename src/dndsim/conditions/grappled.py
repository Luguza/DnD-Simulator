"""Grappled (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import SourcedCondition
from dndsim.creatures.effects import Stacking
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Grappled"]


class Grappled(SourcedCondition):
    """Held: Speed 0, and Disadvantage against anything but the grappler, who is
    this effect's `caster`. One per grappler, so a second grapple by another
    creature holds alongside the first (`docs/decisions.md` § w2j)."""

    __slots__ = ()

    CONDITION = enums.Condition.GRAPPLED
    STACKING = Stacking.UNIQUE_PER_SOURCE
    SPEED_ZERO = True
    NEEDS_HOOK = (
        'nothing moves a creature with another creature: "The grappler can '
        "drag or carry you when it moves, but every foot of movement costs it "
        '1 extra foot." (ROADMAP.md § The W0 debt register #57; `w2j2` '
        "lands the drag)",
    )

    @hook("on_declare_attack")
    def attacks(self, ctx: AttackDeclaredContext) -> R:
        """Imposes "You have Disadvantage on attack rolls against any target
        other than the grappler"."""
        if ctx.defender is self.caster:
            return R.SKIPPED
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
