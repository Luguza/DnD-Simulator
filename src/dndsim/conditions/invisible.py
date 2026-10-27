"""Invisible (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import AttackDeclaredContext, InitiativeContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Invisible"]


class Invisible(Condition):
    """Unseen: Advantage on Initiative, your attacks have Advantage and attacks
    on you have Disadvantage."""

    __slots__ = ()

    CONDITION = enums.Condition.INVISIBLE
    NEEDS_HOOK = (
        "no effect declares that it requires its target to be seen, and no "
        "creature can be asked what it can see: \"You aren't affected by any "
        "effect that requires its target to be seen unless the effect's "
        'creator can somehow see you." '
        "(ROADMAP.md § The W0 debt register #39)",
    )
    APPROXIMATED = (
        "the attack-roll swing is given against every attacker, where "
        "\"If a creature can somehow see you, you don't gain this benefit "
        'against that creature" — exact until something can see the unseen '
        "(ROADMAP.md § The W0 debt register #39)",
    )

    @hook("on_initiative_roll")
    def surprise(self, ctx: InitiativeContext) -> R:
        """Grants "If you're Invisible when you roll Initiative, you have
        Advantage on the roll"."""
        ctx.grant_advantage(self.name)
        return R.APPLIED

    @hook("on_declare_attack")
    def own_attacks(self, ctx: AttackDeclaredContext) -> R:
        """The Advantage half of "Attack rolls against you have Disadvantage,
        and your attack rolls have Advantage"."""
        ctx.grant_advantage(self.name)
        return R.APPLIED

    @hook("on_incoming_attack_declared")
    def incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """The Disadvantage half of the same sentence."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED
