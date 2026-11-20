"""What the engine's D20 Tests share: the bag of modifiers summed, the d20 rolled
under advantage or disadvantage, and a dispatch settled. Why these are
extracted, and which copy stays inline, is `docs/decisions.md` § w1j.
"""

from dndsim.core.errors import FeatureContractError
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Holder, apply_pending
from dndsim.hooks.contexts import AbilityCheckContext, Context, ModifierBag
from dndsim.hooks.definitions import ON_ABILITY_CHECK
from dndsim.rules.enums import Ability

__all__ = ["ability_check", "modifier_total", "roll_d20", "settle"]


def modifier_total(bag: ModifierBag, rng: Rng) -> int:
    """A bag of modifiers as one number, its dice — Bless's `1d4` — drawn
    from `rng`, which must be the generator the roll itself uses."""
    total = 0
    for modifier in bag:
        total += modifier.flat
        if modifier.dice is not None:
            total += modifier.dice.roll(rng)
    return total


def roll_d20(rng: Rng, *, advantage: bool, disadvantage: bool) -> int:
    """The natural roll: the higher of two with advantage, the lower of two with
    disadvantage, one otherwise. The two flags are the ledger's, after
    cancellation, so at most one is true."""
    if advantage:
        return max(rng.roll(20), rng.roll(20))
    if disadvantage:
        return min(rng.roll(20), rng.roll(20))
    return rng.roll(20)


def settle(ctx: Context, refusal: str, *among: Holder | None) -> None:
    """Apply what the dispatch queued and refuse a veto.

    `refusal` is the error's explanation, after the veto's reason: what the
    sequence cannot honour, and what a feature should do instead. `among` is
    the event's participants, for `apply_pending()`. The caller closes the
    context once it has read it.
    """
    apply_pending(ctx, *among)
    if ctx.vetoed:
        raise FeatureContractError(f"{ctx.veto_reason} {refusal}")


def ability_check(
    creature: Creature,
    ability: Ability,
    *,
    skill: str,
    dc: int,
    rng: Rng,
    among: tuple[Holder | None, ...] = (),
) -> int:
    """Roll one ability check for `creature` after `on_ability_check`, and
    return its total: the d20, the ability modifier, the Proficiency Bonus
    with a skill it has, and what handlers added. `among` is the event's other
    participants, for `settle()`. Raises `FeatureContractError` on a veto."""
    check = AbilityCheckContext(creature=creature, ability=ability, skill=skill, dc=dc)
    creature.dispatch(ON_ABILITY_CHECK, check)
    settle(
        check,
        "vetoed at on_ability_check, which cannot be called off. A feature "
        "that means to change a check grants advantage or adds to the total "
        "through ctx.bonus.",
        creature,
        *among,
    )
    had_advantage = check.had_advantage
    had_disadvantage = check.had_disadvantage
    bonus = modifier_total(check.bonus, rng)
    check.close()
    natural = roll_d20(rng, advantage=had_advantage, disadvantage=had_disadvantage)
    if skill in creature.stats.skills:
        bonus += creature.proficiency_bonus
    return natural + creature.modifier(ability) + bonus
