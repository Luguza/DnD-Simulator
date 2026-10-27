"""Exhaustion (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from typing import Final

from dndsim.conditions.base import Condition
from dndsim.core.errors import EngineAssemblyError
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    AttackDeclaredContext,
    Combatant,
    ConcentrationCheckContext,
    D20TestContext,
    DeathSaveContext,
    EffectContext,
    InitiativeContext,
    RestContext,
    SavingThrowContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.rules import enums

__all__ = ["Exhaustion"]

PER_LEVEL_D20_PENALTY: Final = -2
"""Half of "the roll is reduced by 2 times your Exhaustion level", per level.

Negative because it is contributed to the roll like any other modifier."""


class Exhaustion(Condition):
    """Worn down, in levels: every D20 Test is reduced, and a Long Rest removes
    one level.

    The glossary's declared exception to "A condition doesn't stack with
    itself". The level is this effect's charge count, which is why a Long Rest
    `consume()`s one (`docs/decisions.md` § w1h).
    """

    __slots__ = ()

    CONDITION = enums.Condition.EXHAUSTION
    NEEDS_HOOK = (
        "nothing can change a creature's Speed: "
        '"Your Speed is reduced by a number of feet equal to 5 times your '
        'Exhaustion level." '
        "(ROADMAP.md § The W0 debt register #47)",
        "a handler cannot ask the engine to kill its owner: "
        '"You die if your Exhaustion level is 6." '
        "(ROADMAP.md § The W0 debt register #44)",
    )

    def __init__(self, owner: Combatant, *, caster: Combatant | None = None) -> None:
        """One level per application, and no deadline: levels are removed by
        Long Rests and by nothing else."""
        super().__init__(owner, charges=1, caster=caster)

    @property
    def level(self) -> int:
        """This creature's Exhaustion level, which is its charge count."""
        charges = self.charges
        if charges is None:
            raise EngineAssemblyError(
                "exhaustion was built without its level: `charges` is the "
                "level, and a level of none is not a level. Only "
                "Exhaustion(owner) assembles one."
            )
        return charges

    @hook("on_effect_applied", phase=Phase.EARLY)
    def accumulate(self, ctx: EffectContext) -> R:
        """Takes a second application as a level rather than letting it attach:
        "This condition is cumulative. Each time you receive it, you gain 1
        Exhaustion level" (`docs/decisions.md` § w1h)."""
        if ctx.effect.name != self.name:
            return R.SKIPPED
        self.charges = self.level + 1
        ctx.veto(self.name)
        return R.APPLIED

    @hook("on_long_rest")
    def removing_exhaustion_levels(self, ctx: RestContext) -> R:
        """Spends a level, which ends the condition at zero: "Finishing a
        Long Rest removes 1 of your Exhaustion levels. When your Exhaustion
        level reaches 0, the condition ends"."""
        self.consume()
        return R.APPLIED

    @hook("on_declare_attack")
    def attack_rolls(self, ctx: AttackDeclaredContext) -> R:
        """The attack roll, which is a D20 Test."""
        return self._reduce(ctx)

    @hook("on_saving_throw")
    def saving_throws(self, ctx: SavingThrowContext) -> R:
        """The saving throw, which is a D20 Test."""
        return self._reduce(ctx)

    @hook("on_ability_check")
    def ability_checks(self, ctx: AbilityCheckContext) -> R:
        """The ability check, which is a D20 Test."""
        return self._reduce(ctx)

    @hook("on_initiative_roll")
    def initiative_rolls(self, ctx: InitiativeContext) -> R:
        """The Initiative roll, which is a D20 Test."""
        return self._reduce(ctx)

    @hook("on_death_save")
    def death_saves(self, ctx: DeathSaveContext) -> R:
        """The death saving throw, which is a D20 Test."""
        return self._reduce(ctx)

    @hook("on_concentration_check")
    def concentration_checks(self, ctx: ConcentrationCheckContext) -> R:
        """The concentration check, which is a D20 Test."""
        return self._reduce(ctx)

    def _reduce(self, ctx: D20TestContext) -> R:
        ctx.bonus.add(flat=PER_LEVEL_D20_PENALTY * self.level)
        return R.APPLIED
