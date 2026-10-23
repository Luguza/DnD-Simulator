"""Prone (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from typing import ClassVar

from dndsim.conditions.base import Condition
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules import enums

__all__ = ["Prone"]


class Prone(Condition):
    """On the ground: your attacks are at Disadvantage, and an attack on you
    swings on how close the attacker is."""

    __slots__ = ()

    CONDITION = enums.Condition.PRONE
    NEEDS_HOOK: ClassVar[tuple[str, ...]] = (
        "nothing spends movement and no condition can be ended by its own "
        'owner: "Your only movement options are to crawl or to spend an amount '
        "of movement equal to half your Speed (round down) to right yourself "
        'and thereby end the condition." '
        "(ROADMAP.md § The W0 debt register #28)",
    )
    APPROXIMATED: ClassVar[tuple[str, ...]] = (
        "an attack on a prone creature is always given Advantage, where "
        '"An attack roll against you has Advantage if the attacker is within 5 '
        'feet of you. Otherwise, that attack roll has Disadvantage" — the '
        "attacker's distance is not on the context, so a shot from across the "
        "room gets the melee answer "
        "(ROADMAP.md § The W0 debt register #40)",
    )

    @hook("on_declare_attack")
    def prone_own_attacks(self, ctx: AttackDeclaredContext) -> R:
        """Imposes "You have Disadvantage on attack rolls"."""
        ctx.impose_disadvantage(self.name)
        return R.APPLIED

    @hook("on_incoming_attack_declared")
    def prone_incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """The within-5-feet arm of the clause `APPROXIMATED` quotes, given to
        every attacker."""
        ctx.grant_advantage(self.name)
        return R.APPLIED
