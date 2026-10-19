"""Paralyzed (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.incapacitated import Incapacitated
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Paralyzed"]


class Paralyzed(Incapacitated):
    """Incapacitated and rooted, automatically failing Strength and Dexterity
    saves, and attacked with Advantage and automatic critical hits."""

    __slots__ = ()

    CONDITION = enums.Condition.PARALYZED
    NEEDS_HOOK = (
        "nothing can change a creature's Speed: "
        '"Your Speed is 0 and can\'t increase." '
        "(ROADMAP.md § The W0 debt register #47)",
        "SaveResolvedContext has succeed() and no counterpart, so a save "
        'cannot be made to fail: "You automatically fail Strength and '
        'Dexterity saving throws." '
        "(ROADMAP.md § The W0 debt register #37)",
        "a hit cannot be turned into a critical hit and the attacker's "
        'distance is not on the context: "Any attack roll that hits you is a '
        'Critical Hit if the attacker is within 5 feet of you." '
        "(ROADMAP.md § The W0 debt register #40)",
    )

    @hook("on_incoming_attack_declared")
    def paralyzed_incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """Grants "Attack rolls against you have Advantage"."""
        ctx.grant_advantage(self.name)
        return R.APPLIED
