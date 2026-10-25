"""Dropping to 0 hit points, death saving throws, stabilising, and death.

Where each rule comes from — XPHB, "Dropping to 0 Hit Points", under each of
these names unless another source is given:

- Instant Death: the damage left over past 0 kills when it equals or exceeds
  the hit point maximum
- Falling Unconscious: a creature that drops to 0 and does not die has the
  Unconscious condition until it regains hit points (`engine/healing.py`)
- Death Saving Throws: 10 or higher succeeds; three successes make the creature
  Stable, three failures kill it
- Rolling 1 or 20: a 1 is two failures, a 20 regains 1 hit point
- Damage at 0 Hit Points: one failure, two from a Critical Hit, death from
  damage that equals or exceeds the hit point maximum; a Stable creature that
  takes damage stops being Stable
- Stabilizing a Character: a DC 10 Wisdom (Medicine) check
- only a player character makes death saving throws, and anything else dies at
  0 — DESIGN.md § Context, "What 'runs dry' means"

`docs/decisions.md` § w1j has the rulings, and the one first cut in the code
below: the Medicine check adds the bare Wisdom modifier, because nothing can
yet say a creature is proficient in a skill.
"""

from typing import Final, NamedTuple

from dndsim.conditions.unconscious import Unconscious
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import modifier_total, roll_d20, settle
from dndsim.engine.healing import heal
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    DeathSaveContext,
    KillContext,
    ReducedToZeroContext,
    StabilizeContext,
)
from dndsim.hooks.definitions import (
    ON_ABILITY_CHECK,
    ON_DEATH_SAVE,
    ON_KILL,
    ON_REDUCED_TO_ZERO,
    ON_STABILIZE,
)
from dndsim.rules.constants import (
    DEATH_SAVE_DC,
    DEATH_SAVE_FAILURES_TO_DIE,
    DEATH_SAVE_SUCCESSES_TO_STABILIZE,
    NATURAL_ONE,
    NATURAL_TWENTY,
)
from dndsim.rules.enums import Ability

__all__ = [
    "MEDICINE",
    "STABILIZE_DC",
    "DeathSaveResult",
    "damaged_at_zero",
    "death_save",
    "die",
    "dropped_to_zero",
    "stabilize",
]

MEDICINE: Final = "medicine"
"""The skill a stabilising check is made with, as `AbilityCheckContext.skill`
spells it."""

STABILIZE_DC: Final = 10
"""The DC of the Wisdom (Medicine) check that stabilises a dying creature."""


class DeathSaveResult(NamedTuple):
    """What one death saving throw rolled, and the tally it left.

    `successes` and `failures` are read after the roll is counted, so a save
    that stabilised, revived or killed the creature reports the tally that did
    it rather than the reset that followed.
    """

    natural: int
    total: int
    successes: int
    failures: int


def _refusal(stage: str, instead: str) -> str:
    return f"vetoed at {stage}, which cannot be called off. {instead}"


_ZERO_REFUSAL: Final = _refusal(
    "on_reduced_to_zero",
    "A feature that keeps its owner up calls ctx.prevent().",
)
_KILL_REFUSAL: Final = _refusal(
    "on_kill", "The creature has already dropped; on_kill only reports it."
)
_STABILIZE_REFUSAL: Final = _refusal(
    "on_stabilize", "The creature is already Stable when it fires."
)


def dropped_to_zero(
    creature: Creature, *, attacker: Creature | None, overflow: int
) -> None:
    """Settle a creature that damage has just reduced to 0 hit points.

    `overflow` is the damage left after its hit points ran out. Called by
    `resolve_damage()` with the creature's hit points already at 0, which is
    what `on_reduced_to_zero` handlers read; `ctx.prevent()` leaves it at 1
    instead, and then nothing below happens.
    """
    zero = ReducedToZeroContext(defender=creature, attacker=attacker, overflow=overflow)
    creature.dispatch(ON_REDUCED_TO_ZERO, zero)
    settle(zero, _ZERO_REFUSAL, creature, attacker)
    prevented = zero.prevented
    zero.close()
    if prevented:
        creature.hp = 1
        return

    if not creature.player_character or overflow >= creature.max_hp:
        die(creature)
    else:
        creature.effects.apply(Unconscious(creature))

    if attacker is not None:
        kill = KillContext(killer=attacker, victim=creature)
        attacker.dispatch(ON_KILL, kill)
        settle(kill, _KILL_REFUSAL, attacker, creature)
        kill.close()


def damaged_at_zero(creature: Creature, amount: int, *, critical: bool) -> None:
    """Count damage taken by a creature already at 0 hit points.

    Nothing happens to a creature that is already dead, and one that makes no
    death saving throws dies.
    """
    if creature.dead:
        return
    if not creature.player_character or amount >= creature.max_hp:
        die(creature)
        return
    creature.stable = False
    _fail(creature, 2 if critical else 1)


def death_save(creature: Creature, *, rng: Rng) -> DeathSaveResult:
    """Roll one death saving throw for a dying creature, and count it.

    Raises `ValueError` for a creature that is not dying: one with hit points,
    one that is Stable or dead, or one that does not make death saving throws.
    """
    if (
        creature.hp > 0
        or creature.dead
        or creature.stable
        or not creature.player_character
    ):
        raise ValueError(f"{creature!r} is not dying and makes no death saving throw")
    rolling = DeathSaveContext(creature=creature)
    creature.dispatch(ON_DEATH_SAVE, rolling)
    settle(
        rolling,
        _refusal(
            "on_death_save",
            "A feature that means to change a death saving throw grants "
            "advantage or adds to the total through ctx.bonus.",
        ),
        creature,
    )
    had_advantage = rolling.had_advantage
    had_disadvantage = rolling.had_disadvantage
    bonus = modifier_total(rolling.bonus, rng)
    rolling.close()

    natural = roll_d20(rng, advantage=had_advantage, disadvantage=had_disadvantage)
    total = natural + bonus
    successes = creature.death_save_successes
    failures = creature.death_save_failures
    if natural == NATURAL_TWENTY:
        heal(creature, rng=rng, flat=1)
    elif natural == NATURAL_ONE:
        failures = _fail(creature, 2)
    elif total >= DEATH_SAVE_DC:
        successes += 1
        creature.death_save_successes = successes
        if successes >= DEATH_SAVE_SUCCESSES_TO_STABILIZE:
            _become_stable(creature)
    else:
        failures = _fail(creature, 1)
    return DeathSaveResult(natural, total, successes, failures)


def stabilize(helper: Creature, creature: Creature, *, rng: Rng) -> bool:
    """`helper` makes the DC 10 Wisdom (Medicine) check to stabilise `creature`,
    and returns whether it succeeded.

    Raises `ValueError` when `creature` is not dying, as `death_save()` does.
    """
    if (
        creature.hp > 0
        or creature.dead
        or creature.stable
        or not creature.player_character
    ):
        raise ValueError(f"{creature!r} is not dying and cannot be stabilised")
    check = AbilityCheckContext(
        creature=helper, ability=Ability.WISDOM, skill=MEDICINE, dc=STABILIZE_DC
    )
    helper.dispatch(ON_ABILITY_CHECK, check)
    settle(
        check,
        _refusal(
            "on_ability_check",
            "A feature that means to change a check grants advantage or adds "
            "to the total through ctx.bonus.",
        ),
        helper,
        creature,
    )
    had_advantage = check.had_advantage
    had_disadvantage = check.had_disadvantage
    bonus = modifier_total(check.bonus, rng)
    check.close()

    natural = roll_d20(rng, advantage=had_advantage, disadvantage=had_disadvantage)
    if natural + helper.modifier(Ability.WISDOM) + bonus < STABILIZE_DC:
        return False
    _become_stable(creature)
    return True


def die(creature: Creature) -> None:
    """Kill `creature`. Its hit points are not touched: death by damage has
    already put them at 0, and a death by any other cause is not damage."""
    creature.dead = True
    creature.stable = False


def _fail(creature: Creature, failures: int) -> int:
    failures += creature.death_save_failures
    creature.death_save_failures = failures
    if failures >= DEATH_SAVE_FAILURES_TO_DIE:
        die(creature)
    return failures


def _become_stable(creature: Creature) -> None:
    creature.stable = True
    creature.death_save_successes = 0
    creature.death_save_failures = 0
    stable = StabilizeContext(creature=creature)
    creature.dispatch(ON_STABILIZE, stable)
    settle(stable, _STABILIZE_REFUSAL, creature)
    stable.close()
