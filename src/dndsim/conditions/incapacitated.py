"""Incapacitated (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from typing import ClassVar

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import InitiativeContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Incapacitated"]


class Incapacitated(Condition):
    """Unable to act, and surprised when it rolls Initiative."""

    __slots__ = ()

    CONDITION = enums.Condition.INCAPACITATED
    INCAPACITATES = True
    NEEDS_HOOK: ClassVar[tuple[str, ...]] = (
        "nothing concentrates on anything yet, so there is nothing to break: "
        '"Your Concentration is broken." '
        "(ROADMAP.md § The W0 debt register #46)",
    )

    @hook("on_initiative_roll")
    def surprised(self, ctx: InitiativeContext) -> R:
        """Imposes "If you're Incapacitated when you roll Initiative, you
        have Disadvantage on the roll"."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
