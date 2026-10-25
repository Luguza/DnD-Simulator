"""What the engine's D20 Tests share: the bag of modifiers summed, the d20 rolled
under advantage or disadvantage, and a dispatch settled.

Extracted at the third copy (`docs/decisions.md` § w1g, § w1j). The rule these
read — advantage cancellation — is `AdvantageLedger`'s, in `hooks/contexts.py`;
what is shared here is the arithmetic on either side of it.
"""

from dndsim.core.errors import FeatureContractError
from dndsim.core.rng import Rng
from dndsim.creatures.effects import Holder, apply_pending
from dndsim.hooks.contexts import Context, ModifierBag

__all__ = ["modifier_total", "roll_d20", "settle"]


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
