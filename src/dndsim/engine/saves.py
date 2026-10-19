"""The saving-throw sequence, in the one file it is ever written in.

**force -> roll -> compare -> resolved**, with the DC's side dispatched once for
the whole set of targets and the roll's two hooks once per target.

Where each step comes from:

- 8 + the ability modifier + the proficiency bonus — XPHB, "Spells" -> Spell
  Save DC (`rules/constants.py`)
- the save succeeds when the total equals or beats the DC — XPHB, "D20 Tests"
  -> Saving Throws
- a natural 20 does not succeed on its own and a natural 1 does not fail on its
  own — the same entry, which gives a save no effect for either, against the
  Attack Rolls entry that gives both (`rules/constants.py`'s `NATURAL_TWENTY`)
- half damage on a successful save, taken in the halvings step of the one
  resolution order — `engine/damage.py`; the signature that reaches it is
  argued in `docs/decisions.md` § w1g

Not here: the features that touch a save — each attaches to one of the three
hooks and this module names none of them; the repeat save a save-ends effect
offers at a turn boundary, which is `w2c`'s roll against `rules/enums.py`'s
`SaveRepeat` (debt #35); death saves (`w1j`); ability checks, which no task
before `w2b` rolls. One first cut lives here and is named so that
nobody has to rediscover it: a saving throw adds the bare ability modifier,
because nothing can yet say a creature is proficient in a save (debt #36).
"""

from collections.abc import Mapping, Sequence
from typing import Final, NamedTuple

from dndsim.core.errors import FeatureContractError
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import apply_pending
from dndsim.engine.damage import FRESH_DAMAGE, DamageContexts, resolve_damage
from dndsim.hooks.contexts import (
    Context,
    ForcedSaveContext,
    ModifierBag,
    SaveResolvedContext,
    SavingThrowContext,
)
from dndsim.hooks.definitions import (
    ON_FORCED_SAVE,
    ON_SAVE_RESOLVED,
    ON_SAVING_THROW,
)
from dndsim.rules.constants import SPELL_SAVE_DC_BASE
from dndsim.rules.enums import Ability, DamageType, SaveOutcome

__all__ = [
    "SaveResult",
    "force_save",
    "save_bonus",
    "save_dc",
    "save_for_half",
    "saving_throw",
]

HALVED_BY_SAVE: Final = "a successful saving throw halves it"
"""The reason `save_for_half()` records beside its halving in the ledger."""


class SaveResult(NamedTuple):
    """What one saving throw did: how it resolved, and against what.

    `forced_by` is every reason a handler gave for declaring a success the dice
    did not produce, and is empty for a save that resolved on its own.
    """

    outcome: SaveOutcome
    total: int
    dc: int
    forced_by: tuple[str, ...] = ()


def save_dc(creature: Creature, ability: Ability) -> int:
    """The DC of a save `creature` forces, from the ability it imposes it with.

    `ability` is the imposer's spellcasting ability, which is not the ability
    the target saves with.
    """
    return SPELL_SAVE_DC_BASE + creature.proficiency_bonus + creature.modifier(ability)


def save_bonus(creature: Creature, ability: Ability) -> int:
    """What `creature` adds to a saving throw of `ability` before any handler.

    The ability modifier, and nothing else: no creature can declare proficiency
    in a save yet, so the proficiency bonus the rules add for one is debt #36.
    A function rather than the expression inlined below, because `w8a`'s
    estimator has to read the number the resolver uses (DESIGN.md § The
    estimator and the resolver may never diverge).
    """
    return creature.modifier(ability)


def saving_throw(
    creature: Creature,
    *,
    ability: Ability,
    dc: int,
    rng: Rng,
    imposed_by: Creature | None = None,
) -> SaveResult:
    """Roll one saving throw for `creature` and return how it resolved.

    `imposed_by` is whoever forced it, for the handlers that read it; a save
    nothing in the encounter imposed — a trap, a hazard — passes `None`.
    """
    rolling = SavingThrowContext(
        creature=creature, ability=ability, dc=dc, imposed_by=imposed_by
    )
    creature.dispatch(ON_SAVING_THROW, rolling)
    _settle(rolling, "on_saving_throw", creature, imposed_by)
    had_advantage = rolling.had_advantage
    had_disadvantage = rolling.had_disadvantage
    bonus = _modifier_total(rolling.bonus, rng)
    rolling.close()

    if had_advantage:
        natural = max(rng.roll(20), rng.roll(20))
    elif had_disadvantage:
        natural = min(rng.roll(20), rng.roll(20))
    else:
        natural = rng.roll(20)
    total = natural + save_bonus(creature, ability) + bonus
    outcome = SaveOutcome.SUCCESS if total >= dc else SaveOutcome.FAILURE

    resolved = SaveResolvedContext(
        creature=creature,
        ability=ability,
        dc=dc,
        total=total,
        succeeded=outcome is SaveOutcome.SUCCESS,
    )
    creature.dispatch(ON_SAVE_RESOLVED, resolved)
    _settle(resolved, "on_save_resolved", creature, imposed_by)
    forced_by = resolved.forced_by
    if resolved.forced_success:
        outcome = SaveOutcome.SUCCESS
    resolved.close()
    return SaveResult(outcome=outcome, total=total, dc=dc, forced_by=forced_by)


def force_save(
    imposer: Creature,
    targets: Sequence[Creature],
    *,
    ability: Ability,
    dc: int,
    rng: Rng,
) -> tuple[SaveResult, ...]:
    """Make each of `targets` save against `dc`, and return one result each.

    `on_forced_save` fires once for the whole set rather than once per target,
    because the DC is one number for the area and a bonus to it applies to
    every save rolled against it; with no targets it does not fire at all.
    `dc` is the imposer's own — `save_dc()` derives a spellcaster's, and a
    monster's is printed.
    """
    if not targets:
        # Nobody is forced to save, so the imposer's hook does not fire: a
        # handler on it can carry a `once_per` budget or an action cost.
        return ()
    forced = ForcedSaveContext(
        creature=imposer, targets=tuple(targets), ability=ability, dc=dc
    )
    imposer.dispatch(ON_FORCED_SAVE, forced)
    _settle(forced, "on_forced_save", imposer, *targets)
    dc_bonus = _modifier_total(forced.dc_bonus, rng)
    forced.close()
    return tuple(
        saving_throw(
            target, ability=ability, dc=dc + dc_bonus, rng=rng, imposed_by=imposer
        )
        for target in targets
    )


def save_for_half(
    defender: Creature,
    amounts: Mapping[DamageType, int],
    *,
    rng: Rng,
    outcome: SaveOutcome,
    attacker: Creature | None = None,
    contexts: DamageContexts = FRESH_DAMAGE,
) -> int:
    """Deal `amounts` to `defender`, halved on a successful save.

    The halving is recorded for `engine/damage.py` to apply in its own step
    rather than taken off `amounts` here, which would put it ahead of immunity
    and the flat reductions (`docs/decisions.md` § w1g).
    """
    return resolve_damage(
        defender,
        amounts,
        rng=rng,
        attacker=attacker,
        contexts=contexts,
        halved_by=HALVED_BY_SAVE if outcome is SaveOutcome.SUCCESS else None,
    )


def _modifier_total(bag: ModifierBag, rng: Rng) -> int:
    """A bag of modifiers as one number, Bless's `1d4` included.

    The dice come from the generator the save is being rolled with, which is
    the requirement `mitigate()` states for a reduction's dice and the same
    argument (`engine/damage.py`).
    """
    total = 0
    for modifier in bag:
        total += modifier.flat
        if modifier.dice is not None:
            total += modifier.dice.roll(rng)
    return total


def _settle(ctx: Context, stage: str, *among: Creature | None) -> None:
    """Apply what the dispatch queued and refuse a veto.

    A veto is refused at all three hooks: `SaveOutcome` has no member for a
    save that was called off, so honouring one would leave this function with
    nothing to return (`rules/enums.py`). Called before anything is rolled, so
    that a refused save draws no dice — the order `engine/attack.py` settles
    its declaration in. The caller closes the context once it has read it.
    """
    apply_pending(ctx, *among)
    if ctx.vetoed:
        raise FeatureContractError(
            f"{ctx.veto_reason} vetoed at {stage}, which the saving-throw "
            "sequence cannot honour: a save either succeeds or fails, and "
            "neither describes one that did not happen. A feature that means "
            "to change a save grants advantage, adds to the total through "
            "ctx.bonus, or declares a success at on_save_resolved."
        )
