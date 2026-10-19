"""Unconscious (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.prone import Prone
from dndsim.hooks.contexts import AttackDeclaredContext, EffectContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Unconscious"]


class Unconscious(Incapacitated, Prone):
    """Out cold: Incapacitated and Prone, rooted, attacked with Advantage and
    automatic critical hits, and still Prone when it ends."""

    __slots__ = ()

    CONDITION = enums.Condition.UNCONSCIOUS
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
        "a creature's weapon is not on the `Combatant` view a handler is "
        "given, and nothing picks a dropped one back up: "
        '"you drop whatever you\'re holding." '
        "(ROADMAP.md § The W0 debt register #48)",
    )

    @hook("on_incoming_attack_declared")
    def unconscious_incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """Grants "Attack rolls against you have Advantage" in its own right.

        Not redundant with the grant inherited from `Prone`, whose clause is
        conditional where this one is not (`docs/decisions.md` § w1h).
        """
        ctx.grant_advantage(self.name)
        return R.APPLIED

    @hook("on_effect_expired")
    def remain_prone(self, ctx: EffectContext) -> R:
        """Applies the Prone condition this one was standing in for, which is
        "When this condition ends, you remain Prone"."""
        if ctx.effect is not self:
            return R.SKIPPED
        ctx.apply_effect(self.owner, Prone(self.owner))
        return R.APPLIED
