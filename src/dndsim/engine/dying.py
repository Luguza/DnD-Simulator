"""Dropping to 0 hit points, death saving throws, stabilising, and death.

The rules are XPHB, "Dropping to 0 Hit Points", under its headings Instant
Death, Falling Unconscious, Death Saving Throws, Rolling 1 or 20, Damage at 0
Hit Points and Stabilizing a Character; that only a player character makes
death saving throws is DESIGN.md § Context, "What 'runs dry' means".
`docs/decisions.md` § w1j has the rulings, and the one first cut in the code
below: the Medicine check adds the bare Wisdom modifier (debt #36).
"""

from typing import Final, NamedTuple

from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import modifier_total, roll_d20, settle
from dndsim.engine.healing import Downed, heal
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
        creature.die()
    else:
        creature.effects.apply(Downed(creature))

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
        creature.die()
        return
    creature.fail_death_save(2 if critical else 1)


def death_save(creature: Creature, *, rng: Rng) -> DeathSaveResult:
    """Roll one death saving throw for a dying creature, and count it.

    Raises `ValueError` for a creature that is not dying: one with hit points,
    one that is Stable or dead, or one that does not make death saving throws.
    """
    if not _dying(creature):
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
        creature.fail_death_save(2)
        failures = creature.death_save_failures
    elif total >= DEATH_SAVE_DC:
        successes += 1
        if creature.succeed_death_save():
            _announce_stable(creature)
    else:
        creature.fail_death_save()
        failures = creature.death_save_failures
    return DeathSaveResult(natural, total, successes, failures)


def stabilize(helper: Creature, creature: Creature, *, rng: Rng) -> bool:
    """`helper` makes the DC 10 Wisdom (Medicine) check to stabilise `creature`,
    and returns whether it succeeded.

    Raises `ValueError` when `creature` is not dying, as `death_save()` does.
    """
    if not _dying(creature):
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
    creature.stabilize()
    _announce_stable(creature)
    return True


def _dying(creature: Creature) -> bool:
    """At 0 hit points, neither Stable nor dead, and making death saves."""
    return (
        creature.hp <= 0
        and not creature.dead
        and not creature.stable
        and creature.player_character
    )


def _announce_stable(creature: Creature) -> None:
    stable = StabilizeContext(creature=creature)
    creature.dispatch(ON_STABILIZE, stable)
    settle(stable, _STABILIZE_REFUSAL, creature)
    stable.close()
