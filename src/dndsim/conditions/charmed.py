"""Charmed (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import SourcedCondition
from dndsim.hooks.contexts import AttackDeclaredContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.rules import enums

__all__ = ["Charmed"]


class Charmed(SourcedCondition):
    """You cannot harm the charmer, who is this effect's `caster`."""

    __slots__ = ()

    CONDITION = enums.Condition.CHARMED
    NEEDS_HOOK = (
        "CastSpellContext.targets cannot have a target taken out of it, and "
        "vetoing the cast would also refuse the targets that are allowed: "
        "\"You can't ... target the charmer with damaging abilities or magical "
        'effects." '
        "(ROADMAP.md § The W0 debt register #45)",
    )

    @hook("on_declare_attack", phase=Phase.EARLY)
    def cant_harm_the_charmer(self, ctx: AttackDeclaredContext) -> R:
        """Refuses the attack half of "You can't attack the charmer or target
        the charmer with damaging abilities or magical effects"."""
        if ctx.defender is not self.caster:
            return R.SKIPPED
        ctx.veto(self.name)
        return R.APPLIED
