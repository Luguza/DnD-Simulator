"""Petrified (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.poisoned import Poisoned
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    EffectContext,
    IncomingDamageContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.rules import enums

__all__ = ["Petrified"]


class Petrified(Incapacitated):
    """Turned to stone: Incapacitated and rooted, attacked with Advantage,
    resistant to everything and immune to the Poisoned condition."""

    __slots__ = ()

    CONDITION = enums.Condition.PETRIFIED
    NEEDS_HOOK = (
        "nothing can change a creature's Speed: "
        '"Your Speed is 0 and can\'t increase." '
        "(ROADMAP.md § The W0 debt register #47)",
        "SaveResolvedContext has succeed() and no counterpart, so a save "
        'cannot be made to fail: "You automatically fail Strength and '
        'Dexterity saving throws." '
        "(ROADMAP.md § The W0 debt register #37)",
    )

    @hook("on_incoming_attack_declared")
    def petrified_incoming_attacks(self, ctx: AttackDeclaredContext) -> R:
        """Grants "Attack rolls against you have Advantage"."""
        ctx.grant_advantage(self.name)
        return R.APPLIED

    @hook("on_incoming_damage")
    def resist_damage(self, ctx: IncomingDamageContext) -> R:
        """Resists every member of `DamageType` rather than only the ones
        this hit carries — "You have Resistance to all damage", and
        `engine/damage.py` reads the ledger per type."""
        for damage_type in enums.DamageType:
            ctx.add_resistance(damage_type)
        return R.APPLIED

    @hook("on_effect_applied", phase=Phase.EARLY)
    def poison_immunity(self, ctx: EffectContext) -> R:
        """Refuses the Poisoned condition on its way on, which is what
        "You have Immunity to the Poisoned condition" means here."""
        if ctx.effect.name != Poisoned.ID:
            return R.SKIPPED
        ctx.veto(self.name)
        return R.APPLIED
