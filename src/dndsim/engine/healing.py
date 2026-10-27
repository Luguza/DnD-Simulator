"""Regaining hit points and gaining temporary ones.

The rules are XPHB, "Damage and Healing" -> Healing, "Dropping to 0 Hit Points"
-> Falling Unconscious and Death Saving Throws, and the Rules Glossary's
"Temporary Hit Points". That a dead creature regains nothing is the project's
reading, `docs/decisions.md` § w1j.
"""

from typing import Final

from dndsim.conditions.unconscious import Unconscious
from dndsim.core.dice import Dice
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import apply_pending
from dndsim.engine.d20 import modifier_total, settle
from dndsim.hooks.contexts import HealContext, TempHpContext
from dndsim.hooks.definitions import ON_HEAL, ON_TEMP_HP_GAINED

__all__ = ["Downed", "grant_temp_hp", "heal"]


class Downed(Unconscious):
    """The Unconscious condition dropping to 0 hit points gives, held apart from
    an Unconscious another source gave (`docs/decisions.md` § w1j)."""

    __slots__ = ()


_TEMP_HP_REFUSAL: Final = (
    "vetoed on_temp_hp_gained, which is observational: the pool has already "
    "been kept by the time it fires. A feature that means to change what a "
    "creature gains changes the amount it grants."
)


def heal(
    creature: Creature,
    *,
    rng: Rng,
    dice: Dice | str | None = None,
    flat: int = 0,
    healer: Creature | None = None,
) -> int:
    """Restore `dice` + `flat` hit points to `creature`, and return how many it
    regained.

    `on_heal` fires on the recipient with the caller's healing already in the
    bag. A veto stops the healing — "can't regain Hit Points" — and returns
    zero. A dead creature regains nothing and fires nothing.
    """
    if creature.dead:
        return 0
    ctx = HealContext(creature=creature, healer=healer)
    ctx.stamp("engine.healing", None)
    ctx.healing.add(dice=dice, flat=flat)
    ctx.stamp(None, None)
    creature.dispatch(ON_HEAL, ctx)
    apply_pending(ctx, creature, healer)
    if ctx.vetoed:
        ctx.close()
        return 0
    amount = modifier_total(ctx.healing, rng)
    ctx.close()

    was_down = creature.hp <= 0
    regained = min(max(amount, 0), creature.max_hp - creature.hp)
    if regained <= 0:
        return 0
    creature.hp += regained
    if was_down:
        _wake(creature)
    return regained


def grant_temp_hp(creature: Creature, amount: int) -> int:
    """Give `creature` `amount` temporary hit points, if that is more than it
    has, and return the pool it now holds.

    `on_temp_hp_gained` fires only when the new pool is kept.
    """
    if amount < 0:
        raise ValueError(f"{creature.name} was granted {amount} temporary hit points")
    replaced = creature.temp_hp
    if amount <= replaced:
        return replaced
    creature.temp_hp = amount
    ctx = TempHpContext(creature=creature, amount=amount, replaced=replaced)
    creature.dispatch(ON_TEMP_HP_GAINED, ctx)
    settle(ctx, _TEMP_HP_REFUSAL, creature)
    ctx.close()
    return amount


def _wake(creature: Creature) -> None:
    """What regaining hit points at 0 undoes: the death saving throws, Stable,
    and the Unconscious condition."""
    creature.reset_death_saves()
    for held in tuple(creature.effects):
        if isinstance(held, Downed):
            creature.effects.remove(held)
