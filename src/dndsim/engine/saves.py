"""The saving-throw sequence.

**force -> roll -> compare -> resolved**, with the DC's side dispatched once for
the whole set of targets and the roll's two hooks once per target.

Sources:

- 8 + the ability modifier + the proficiency bonus — XPHB, "Spells" -> Spell
  Save DC (`rules/constants.py`)
- the save succeeds when the total equals or beats the DC — XPHB, "D20 Tests"
  -> Saving Throws
- a natural 20 does not succeed on its own and a natural 1 does not fail on its
  own — the same entry (`docs/decisions.md` § w1g)
- half damage on a successful save, in the halvings step of `engine/damage.py`
  — `docs/decisions.md` § w1g

One first cut: a saving throw adds the bare ability modifier (debt #36).
"""

from collections.abc import Mapping, Sequence
from typing import Final, NamedTuple

from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import modifier_total, roll_d20, settle
from dndsim.engine.damage import FRESH_DAMAGE, DamageContexts, resolve_damage
from dndsim.hooks.contexts import (
    ForcedSaveContext,
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

    `forced_by` is every reason a handler gave for declaring this save a
    success, in the order they were claimed. A claim is recorded whether or not
    the dice had already made the DC.
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

    The ability modifier, and nothing else: debt #36. Why this is a function is
    `docs/decisions.md` § w1g.
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
    settle(rolling, _refusal("on_saving_throw"), creature, imposed_by)
    had_advantage = rolling.had_advantage
    had_disadvantage = rolling.had_disadvantage
    bonus = modifier_total(rolling.bonus, rng)
    rolling.close()

    natural = roll_d20(rng, advantage=had_advantage, disadvantage=had_disadvantage)
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
    settle(resolved, _refusal("on_save_resolved"), creature, imposed_by)
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
    and a bonus to the DC applies to every save; with no targets it does not
    fire at all.
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
    settle(forced, _refusal("on_forced_save"), imposer, *targets)
    dc_bonus = modifier_total(forced.dc_bonus, rng)
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

    The halving is applied in `engine/damage.py`'s halvings step
    (`docs/decisions.md` § w1g).
    """
    return resolve_damage(
        defender,
        amounts,
        rng=rng,
        attacker=attacker,
        contexts=contexts,
        halved_by=HALVED_BY_SAVE if outcome is SaveOutcome.SUCCESS else None,
    )


def _refusal(stage: str) -> str:
    """The refusal message for a veto at `stage` (`docs/decisions.md` § w1g)."""
    return (
        f"vetoed at {stage}, which the saving-throw sequence cannot honour: a "
        "save either succeeds or fails, and neither describes one that did not "
        "happen. A feature that means to change a save grants advantage, adds "
        "to the total through ctx.bonus, or declares a success at "
        "on_save_resolved."
    )
