"""Stunned (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.incapacitated import Incapacitated
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Stunned"]


class Stunned(Incapacitated):
    """Incapacitated, automatically failing Strength and Dexterity saves, and
    attacked with Advantage."""

    __slots__ = ()

    CONDITION = enums.Condition.STUNNED
    NEEDS_HOOK = (
        "SaveResolvedContext has succeed() and no counterpart, so a save "
        'cannot be made to fail: "You automatically fail Strength and '
        'Dexterity saving throws." '
        "(ROADMAP.md § The W0 debt register #37)",
    )

    @hook("on_incoming_attack_declared")
    def stunned_incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """Grants "Attack rolls against you have Advantage"."""
        ctx.grant_advantage(self.name)
        return R.APPLIED
