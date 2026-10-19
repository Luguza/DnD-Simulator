"""Incapacitated (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from typing import ClassVar

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import InitiativeContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Incapacitated"]


class Incapacitated(Condition):
    """Unable to act, and surprised when it rolls Initiative.

    The base of Paralyzed, Petrified, Stunned and Unconscious, each of whose
    rules text says "You have the Incapacitated condition"
    (`docs/decisions.md` § w1h).
    """

    __slots__ = ()

    CONDITION = enums.Condition.INCAPACITATED
    NEEDS_HOOK: ClassVar[tuple[str, ...]] = (
        "ActionEconomy can be granted and spent but not emptied, and nothing "
        "filters what a creature is offered: \"You can't take any action, "
        'Bonus Action, or Reaction." '
        "(ROADMAP.md § The W0 debt register #41)",
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
