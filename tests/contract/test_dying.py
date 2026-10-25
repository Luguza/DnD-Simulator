"""The dying contract: dropping to 0, death saving throws, stabilising, death.

Every case carries its arithmetic. The player character every test downs has
20 hit points and Wisdom 10 (+0); the helper who stabilises has Wisdom 14 (+2).
A creature built without `player_character=True` is a monster, which dies at 0.

Sources: XPHB (2024) "Dropping to 0 Hit Points" -> Instant Death, Falling
Unconscious, Death Saving Throws, Rolling 1 or 20, Damage at 0 Hit Points,
Stabilizing a Character; Rules Glossary "Temporary Hit Points", "Exhaustion",
"Unconscious"; DESIGN.md § Context, "What 'runs dry' means"; ROADMAP w1j.
"""

from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.conditions.exhaustion import Exhaustion
from dndsim.conditions.prone import Prone
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.errors import FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.resources import Recovery, ResourceSpec
from dndsim.creatures.stats import GREATSWORD, StatBlock
from dndsim.engine import attack, damage, dying, healing
from dndsim.engine.attack import resolve_attack
from dndsim.engine.damage import resolve_damage
from dndsim.engine.dying import (
    MEDICINE,
    STABILIZE_DC,
    DeathSaveResult,
    death_save,
    stabilize,
)
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    DeathSaveContext,
    KillContext,
    ReducedToZeroContext,
    StabilizeContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType, Outcome, RecoveryTrigger

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))
SLASHING: Final = DamageType.SLASHING
MAX_HP: Final = 20


def pc(name: str = "pc", *, hp: int = MAX_HP) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=hp),
        position=Cell(0, 0),
        player_character=True,
    )


def monster(name: str = "goblin", *, hp: int = MAX_HP) -> Creature:
    return Creature(
        name=name, stats=StatBlock(abilities=SCORES, max_hp=hp), position=Cell(1, 0)
    )


def healer(name: str = "cleric", *, wisdom: int = 14) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities={**SCORES, Ability.WISDOM: wisdom}, max_hp=10),
        position=Cell(2, 0),
    )


def downed() -> Creature:
    """A player character dropped by damage: 0 hit points and Unconscious."""
    subject = pc()
    resolve_damage(subject, {SLASHING: MAX_HP}, rng=no_dice())
    assert subject.hp == 0 and not subject.dead
    return subject


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled."""
    return ScriptedRng([])


def probed(subject: Creature) -> "Probe":
    probe = Probe(subject)
    subject.attach(probe)
    return probe


def held(subject: Creature) -> list[type]:
    return [type(effect) for effect in subject.effects]


# --- Dropping to 0: the hook, and overflow -----------------------------------


def test_dropping_to_zero_fires_on_reduced_to_zero_with_the_overflow() -> None:
    """20 hit points, 13 left, takes 20: 13 to reach 0, 7 left over."""
    victim, striker = pc(), monster()
    victim.hp = 13
    probe = probed(victim)
    resolve_damage(victim, {SLASHING: 20}, rng=no_dice(), attacker=striker)
    assert probe.zero == [(13 - 13, 7, striker)]
    assert victim.hp == 0


def test_overflow_is_what_temporary_hit_points_did_not_absorb() -> None:
    """5 hit points and 3 temporary, 10 damage: 3 absorbed, 5 to reach 0,
    10 - 3 - 5 = 2 left over."""
    victim = pc()
    victim.hp, victim.temp_hp = 5, 3
    probe = probed(victim)
    resolve_damage(victim, {SLASHING: 10}, rng=no_dice())
    assert probe.zero == [(0, 2, None)]
    assert victim.temp_hp == 0


def test_damage_exactly_to_zero_has_no_overflow() -> None:
    victim = pc()
    probe = probed(victim)
    resolve_damage(victim, {SLASHING: MAX_HP}, rng=no_dice())
    assert probe.zero == [(0, 0, None)]


def test_damage_that_leaves_a_hit_point_fires_nothing() -> None:
    victim = pc()
    probe = probed(victim)
    resolve_damage(victim, {SLASHING: MAX_HP - 1}, rng=no_dice())
    assert probe.zero == []
    assert victim.hp == 1


def test_damage_at_zero_does_not_drop_the_creature_again() -> None:
    victim = downed()
    probe = probed(victim)
    resolve_damage(victim, {SLASHING: 1}, rng=no_dice())
    assert probe.zero == []


# --- Instant Death and Falling Unconscious ----------------------------------


def test_overflow_equal_to_the_maximum_kills_outright() -> None:
    """20 hit points, takes 40: 20 left over, which equals the maximum."""
    victim = pc()
    resolve_damage(victim, {SLASHING: 40}, rng=no_dice())
    assert victim.dead
    assert not victim.is_alive
    assert Unconscious not in held(victim)


def test_overflow_one_short_of_the_maximum_knocks_out() -> None:
    """Takes 39: 19 left over, one short of 20."""
    victim = pc()
    resolve_damage(victim, {SLASHING: 39}, rng=no_dice())
    assert not victim.dead
    assert victim.is_alive and victim.is_down
    assert held(victim) == [Unconscious]


def test_a_player_character_at_zero_is_unconscious_with_clean_counters() -> None:
    victim = downed()
    assert (victim.death_save_successes, victim.death_save_failures) == (0, 0)
    assert not victim.stable
    assert held(victim) == [Unconscious]


def test_a_monster_dies_at_zero() -> None:
    """Only a player character makes death saving throws (DESIGN.md
    § Context): a goblin dropped by exactly its hit points dies, with no
    overflow at all."""
    goblin = monster()
    resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice())
    assert goblin.dead
    assert held(goblin) == []


# --- on_kill -----------------------------------------------------------------


def test_on_kill_fires_on_the_attacker_when_a_monster_dies() -> None:
    goblin, fighter = monster(), pc("fighter")
    probe = probed(fighter)
    resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice(), attacker=fighter)
    assert probe.kills == [(fighter, goblin)]


def test_on_kill_fires_when_a_player_character_is_dropped_too() -> None:
    """The row: `on_kill` fires at the same moment as `on_reduced_to_zero`,
    unless the drop was prevented — a downed character was dropped."""
    victim, goblin = pc(), monster()
    probe = probed(goblin)
    resolve_damage(victim, {SLASHING: MAX_HP}, rng=no_dice(), attacker=goblin)
    assert probe.kills == [(goblin, victim)]


def test_on_kill_fires_on_the_attacker_and_not_the_victim() -> None:
    goblin, fighter = monster(), pc("fighter")
    probe = probed(goblin)
    resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice(), attacker=fighter)
    assert probe.kills == []


def test_on_kill_does_not_fire_for_damage_with_no_attacker() -> None:
    goblin = monster()
    probe = probed(goblin)
    resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice())
    assert goblin.dead
    assert probe.kills == []


def test_on_kill_does_not_fire_when_the_drop_is_prevented() -> None:
    orc, fighter = pc("orc"), pc("fighter")
    orc.resources.add(RELENTLESS)
    orc.attach(RelentlessEndurance(orc))
    probe = probed(fighter)
    resolve_damage(orc, {SLASHING: MAX_HP}, rng=no_dice(), attacker=fighter)
    assert probe.kills == []


# --- Relentless Endurance: the acceptance test ------------------------------


def test_relentless_endurance_keeps_its_owner_up_at_one_hit_point() -> None:
    """XPHB Orc: "When you are reduced to 0 Hit Points but not killed outright,
    you can drop to 1 Hit Point instead." 20 damage on 20 hit points."""
    orc = pc("orc")
    orc.resources.add(RELENTLESS)
    orc.attach(RelentlessEndurance(orc))
    resolve_damage(orc, {SLASHING: MAX_HP}, rng=no_dice())
    assert orc.hp == 1
    assert not orc.dead
    assert held(orc) == []
    assert orc.resources["relentless_endurance"].current == 0


def test_relentless_endurance_is_once_per_long_rest() -> None:
    """The second drop stands; a Long Rest gives the use back."""
    orc = pc("orc")
    orc.resources.add(RELENTLESS)
    orc.attach(RelentlessEndurance(orc))
    resolve_damage(orc, {SLASHING: MAX_HP}, rng=no_dice())
    resolve_damage(orc, {SLASHING: 1}, rng=no_dice())
    assert orc.hp == 0
    assert held(orc) == [Unconscious]

    orc.resources.recover(RecoveryTrigger.LONG_REST)
    healing.heal(orc, rng=no_dice(), flat=MAX_HP)
    resolve_damage(orc, {SLASHING: MAX_HP}, rng=no_dice())
    assert orc.hp == 1


def test_relentless_endurance_does_not_save_its_owner_from_instant_death() -> None:
    """ "but not killed outright": 40 damage on 20 hit points leaves 20 over."""
    orc = pc("orc")
    orc.resources.add(RELENTLESS)
    orc.attach(RelentlessEndurance(orc))
    resolve_damage(orc, {SLASHING: 40}, rng=no_dice())
    assert orc.dead
    assert orc.resources["relentless_endurance"].current == 1


def test_the_engine_knows_nothing_about_relentless_endurance() -> None:
    """The row's acceptance test: the feature is a resource and a handler."""
    for module in (damage, dying, healing, attack):
        assert module.__file__ is not None
        source = Path(module.__file__).read_text().lower()
        assert "relentless" not in source
        assert "endurance" not in source
        assert "resource" not in source


# --- Damage at 0 Hit Points --------------------------------------------------


def test_damage_at_zero_is_one_death_save_failure() -> None:
    victim = downed()
    resolve_damage(victim, {SLASHING: 1}, rng=no_dice())
    assert victim.death_save_failures == 1
    assert not victim.dead


def test_a_critical_hit_at_zero_is_two_failures() -> None:
    victim = downed()
    resolve_damage(victim, {SLASHING: 1}, rng=no_dice(), critical=True)
    assert victim.death_save_failures == 2
    assert not victim.dead


def test_a_critical_hit_through_the_attack_pipeline_is_two_failures() -> None:
    """A natural 20 against the downed character, who is Unconscious and so
    attacked with Advantage: d20s 20 and 1, kept 20. The greatsword's 2d6
    doubles to 4d6, all 1s, + 0 Strength = 4, short of the maximum."""
    victim = downed()
    swinger = monster("orc")
    swinger.weapon = GREATSWORD
    rng = ScriptedRng([20, 1, 1, 1, 1, 1])
    outcome, dealt = resolve_attack(swinger, victim, rng=rng)
    assert outcome is Outcome.CRITICAL_HIT
    assert dealt == 4
    assert victim.death_save_failures == 2
    assert rng.exhausted


def test_a_third_failure_from_damage_kills() -> None:
    victim = downed()
    victim.fail_death_save(2)
    resolve_damage(victim, {SLASHING: 1}, rng=no_dice())
    assert victim.dead


def test_damage_at_zero_equal_to_the_maximum_kills() -> None:
    victim = downed()
    resolve_damage(victim, {SLASHING: MAX_HP}, rng=no_dice())
    assert victim.dead


def test_damage_at_zero_one_short_of_the_maximum_is_one_failure() -> None:
    victim = downed()
    resolve_damage(victim, {SLASHING: MAX_HP - 1}, rng=no_dice())
    assert not victim.dead
    assert victim.death_save_failures == 1


def test_temporary_hit_points_at_zero_absorb_damage_without_a_failure() -> None:
    """XPHB "Temporary Hit Points": at 0 they "can still absorb damage directed
    at you". 5 temporary, 3 damage: all absorbed, none taken, so no failure."""
    victim = downed()
    victim.temp_hp = 5
    resolve_damage(victim, {SLASHING: 3}, rng=no_dice())
    assert victim.temp_hp == 2
    assert victim.death_save_failures == 0


def test_instant_death_at_zero_reads_what_temporary_hit_points_left() -> None:
    """5 temporary, 20 damage: 15 reaches the creature, short of the 20
    maximum, so one failure and not death."""
    victim = downed()
    victim.temp_hp = 5
    resolve_damage(victim, {SLASHING: MAX_HP}, rng=no_dice())
    assert not victim.dead
    assert victim.death_save_failures == 1


def test_a_stable_creature_that_is_damaged_stops_being_stable() -> None:
    victim = downed()
    victim.stabilize()
    resolve_damage(victim, {SLASHING: 1}, rng=no_dice())
    assert not victim.stable
    assert victim.death_save_failures == 1


def test_damage_to_a_dead_creature_changes_nothing() -> None:
    goblin = monster()
    resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice())
    resolve_damage(goblin, {SLASHING: 5}, rng=no_dice())
    assert goblin.dead
    assert goblin.death_save_failures == 0


# --- Death saving throws -----------------------------------------------------


def test_a_ten_succeeds() -> None:
    victim = downed()
    result = death_save(victim, rng=ScriptedRng([10]))
    assert result == DeathSaveResult(natural=10, total=10, successes=1, failures=0)
    assert victim.death_save_successes == 1


def test_a_nine_fails() -> None:
    victim = downed()
    result = death_save(victim, rng=ScriptedRng([9]))
    assert result == DeathSaveResult(natural=9, total=9, successes=0, failures=1)
    assert victim.death_save_failures == 1


def test_three_successes_make_the_creature_stable() -> None:
    """Stable, both counters reset, still at 0 and still Unconscious."""
    victim = downed()
    probe = probed(victim)
    results = [death_save(victim, rng=ScriptedRng([15])) for _ in range(3)]
    assert results[-1] == DeathSaveResult(15, 15, 3, 0)
    assert victim.stable
    assert (victim.death_save_successes, victim.death_save_failures) == (0, 0)
    assert victim.hp == 0
    assert held(victim) == [Unconscious]
    assert probe.stabilised == 1


def test_a_stable_creature_makes_no_death_saves() -> None:
    victim = downed()
    victim.stabilize()
    with pytest.raises(ValueError):
        death_save(victim, rng=no_dice())


def test_three_failures_kill() -> None:
    victim = downed()
    for _ in range(3):
        death_save(victim, rng=ScriptedRng([5]))
    assert victim.dead
    assert not victim.is_alive


def test_successes_and_failures_count_separately() -> None:
    """Two successes and two failures in any order: neither has reached
    three."""
    victim = downed()
    for face in (12, 4, 13, 2):
        death_save(victim, rng=ScriptedRng([face]))
    assert (victim.death_save_successes, victim.death_save_failures) == (2, 2)
    assert not victim.dead and not victim.stable


def test_a_natural_one_is_two_failures() -> None:
    victim = downed()
    result = death_save(victim, rng=ScriptedRng([1]))
    assert result.failures == 2
    assert victim.death_save_failures == 2
    assert not victim.dead


def test_a_natural_one_on_one_failure_kills() -> None:
    victim = downed()
    death_save(victim, rng=ScriptedRng([3]))
    death_save(victim, rng=ScriptedRng([1]))
    assert victim.dead


def test_a_natural_twenty_regains_one_hit_point() -> None:
    """Back to 1, counters reset, Unconscious gone and Prone left behind."""
    victim = downed()
    death_save(victim, rng=ScriptedRng([4]))
    death_save(victim, rng=ScriptedRng([12]))
    result = death_save(victim, rng=ScriptedRng([20]))
    assert result.natural == 20
    assert victim.hp == 1
    assert (victim.death_save_successes, victim.death_save_failures) == (0, 0)
    assert held(victim) == [Prone]


def test_exhaustion_reduces_a_death_save() -> None:
    """Exhaustion level 1 is -2: 11 - 2 = 9 fails, 12 - 2 = 10 succeeds."""
    victim = downed()
    victim.effects.apply(Exhaustion(victim))
    failed = death_save(victim, rng=ScriptedRng([11]))
    made = death_save(victim, rng=ScriptedRng([12]))
    assert (failed.total, made.total) == (9, 10)
    assert (victim.death_save_successes, victim.death_save_failures) == (1, 1)


def test_a_natural_twenty_still_revives_through_exhaustion() -> None:
    """Level 3 is -6, 20 - 6 = 14; the 20 is the die's, not the total's."""
    victim = downed()
    for _ in range(3):
        victim.effects.apply(Exhaustion(victim))
    result = death_save(victim, rng=ScriptedRng([20]))
    assert result.total == 14
    assert victim.hp == 1


def test_a_handler_bonus_and_advantage_reach_a_death_save() -> None:
    """Advantage: 3 and 8, kept 8; plus 2 from the handler = 10, a success."""
    victim = downed()
    victim.attach(Blessed(victim))
    rng = ScriptedRng([3, 8])
    result = death_save(victim, rng=rng)
    assert result == DeathSaveResult(natural=8, total=10, successes=1, failures=0)
    assert rng.exhausted


def test_on_death_save_fires_on_the_dying_creature() -> None:
    victim = downed()
    probe = probed(victim)
    death_save(victim, rng=ScriptedRng([10]))
    assert probe.death_saves == [victim]


def test_a_veto_at_on_death_save_is_refused() -> None:
    victim = downed()
    victim.attach(Vetoer(victim))
    with pytest.raises(FeatureContractError):
        death_save(victim, rng=ScriptedRng([10]))


@pytest.mark.parametrize("state", ["standing", "dead", "stable", "monster"])
def test_only_a_dying_player_character_rolls_a_death_save(state: str) -> None:
    subject = not_dying(state)
    with pytest.raises(ValueError):
        death_save(subject, rng=ScriptedRng([10]))


# --- Stabilizing -------------------------------------------------------------


def test_a_medicine_check_that_makes_dc_ten_stabilises() -> None:
    """8 + 2 (Wisdom 14) = 10 against DC 10."""
    victim, cleric = downed(), healer()
    victim.fail_death_save(2)
    assert stabilize(cleric, victim, rng=ScriptedRng([8])) is True
    assert victim.stable
    assert (victim.death_save_successes, victim.death_save_failures) == (0, 0)
    assert victim.hp == 0
    assert held(victim) == [Unconscious]


def test_a_medicine_check_one_short_does_not() -> None:
    """7 + 2 = 9."""
    victim, cleric = downed(), healer()
    assert stabilize(cleric, victim, rng=ScriptedRng([7])) is False
    assert not victim.stable


def test_the_check_uses_the_helpers_wisdom_and_not_the_dying_creatures() -> None:
    """Wisdom 8 (-1): 10 - 1 = 9 fails where the dying creature's +0 would
    have made it."""
    victim, clumsy = downed(), healer(wisdom=8)
    assert stabilize(clumsy, victim, rng=ScriptedRng([10])) is False


def test_stabilising_fires_on_ability_check_on_the_helper() -> None:
    """The first caller of `on_ability_check`: Wisdom (Medicine), DC 10."""
    victim, cleric = downed(), healer()
    probe = probed(cleric)
    stabilize(cleric, victim, rng=ScriptedRng([15]))
    assert probe.checks == [(cleric, Ability.WISDOM, MEDICINE, STABILIZE_DC)]
    assert (MEDICINE, STABILIZE_DC) == ("medicine", 10)


def test_on_stabilize_fires_on_the_dying_creature_only_on_a_success() -> None:
    victim, cleric = downed(), healer()
    on_victim = probed(victim)
    on_cleric = probed(cleric)
    stabilize(cleric, victim, rng=ScriptedRng([2]))
    assert on_victim.stabilised == 0
    stabilize(cleric, victim, rng=ScriptedRng([18]))
    assert (on_victim.stabilised, on_cleric.stabilised) == (1, 0)


def test_exhaustion_on_the_helper_reduces_the_check() -> None:
    """Level 1: 9 + 2 - 2 = 9 fails."""
    victim, cleric = downed(), healer()
    cleric.effects.apply(Exhaustion(cleric))
    assert stabilize(cleric, victim, rng=ScriptedRng([9])) is False


def test_a_check_bonus_from_a_handler_reaches_the_total() -> None:
    """Guidance-shaped: 6 + 2 + 2 = 10."""
    victim, cleric = downed(), healer()
    cleric.attach(Guided(cleric))
    assert stabilize(cleric, victim, rng=ScriptedRng([6])) is True


@pytest.mark.parametrize("state", ["standing", "dead", "stable", "monster"])
def test_only_a_dying_player_character_can_be_stabilised(state: str) -> None:
    subject = not_dying(state)
    with pytest.raises(ValueError):
        stabilize(healer(), subject, rng=ScriptedRng([20]))


def test_a_veto_at_on_ability_check_is_refused() -> None:
    victim, cleric = downed(), healer()
    cleric.attach(Vetoer(cleric))
    with pytest.raises(FeatureContractError):
        stabilize(cleric, victim, rng=ScriptedRng([15]))


def test_a_veto_at_on_stabilize_is_refused() -> None:
    victim, cleric = downed(), healer()
    victim.attach(StabilizeVetoer(victim))
    with pytest.raises(FeatureContractError):
        stabilize(cleric, victim, rng=ScriptedRng([15]))


# --- Vetoes the dropping sequence refuses ------------------------------------


def test_a_veto_at_on_reduced_to_zero_is_refused() -> None:
    victim = pc()
    victim.attach(Vetoer(victim))
    with pytest.raises(FeatureContractError):
        resolve_damage(victim, {SLASHING: MAX_HP}, rng=no_dice())


def test_a_veto_at_on_kill_is_refused() -> None:
    goblin, fighter = monster(), pc("fighter")
    fighter.attach(Vetoer(fighter))
    with pytest.raises(FeatureContractError):
        resolve_damage(goblin, {SLASHING: MAX_HP}, rng=no_dice(), attacker=fighter)


# --- Helpers -----------------------------------------------------------------


def not_dying(state: str) -> Creature:
    if state == "standing":
        return pc()
    if state == "monster":
        goblin = monster()
        goblin.hp = 0
        return goblin
    subject = downed()
    if state == "dead":
        subject.die()
    else:
        subject.stabilize()
    return subject


RELENTLESS: Final = ResourceSpec(
    key="relentless_endurance",
    label="Relentless Endurance",
    maximum=1,
    recovery=(Recovery(RecoveryTrigger.LONG_REST),),
)


class RelentlessEndurance(HookSource):
    """The acceptance test's feature: a once-per-Long-Rest pool and one
    handler, with nothing in the engine that knows it exists."""

    __slots__ = ("ledger",)

    ID = "test.relentless_endurance"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.ledger = owner.resources

    @hook("on_reduced_to_zero")
    def endure(self, ctx: ReducedToZeroContext) -> R:
        if ctx.overflow >= ctx.defender.max_hp or not self.ledger.can_spend(
            RELENTLESS.key
        ):
            return R.SKIPPED
        self.ledger.spend(RELENTLESS.key)
        ctx.prevent("Relentless Endurance")
        return R.APPLIED


class Probe(HookSource):
    """What the dying hooks handed this creature, in order."""

    __slots__ = ("checks", "death_saves", "kills", "stabilised", "zero")

    ID = "test.probe"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.zero: list[tuple[int, int, object]] = []
        self.kills: list[tuple[object, object]] = []
        self.death_saves: list[object] = []
        self.checks: list[tuple[object, Ability, str | None, int | None]] = []
        self.stabilised = 0

    @hook("on_reduced_to_zero")
    def dropped(self, ctx: ReducedToZeroContext) -> R:
        self.zero.append((ctx.defender.hp, ctx.overflow, ctx.attacker))
        return R.APPLIED

    @hook("on_kill")
    def killed(self, ctx: KillContext) -> R:
        self.kills.append((ctx.killer, ctx.victim))
        return R.APPLIED

    @hook("on_death_save")
    def rolling(self, ctx: DeathSaveContext) -> R:
        self.death_saves.append(ctx.creature)
        return R.APPLIED

    @hook("on_ability_check")
    def checking(self, ctx: AbilityCheckContext) -> R:
        self.checks.append((ctx.creature, ctx.ability, ctx.skill, ctx.dc))
        return R.APPLIED

    @hook("on_stabilize")
    def stable(self, ctx: StabilizeContext) -> R:
        self.stabilised += 1
        return R.APPLIED


class Vetoer(HookSource):
    """An EARLY veto at every hook this sequence refuses one at."""

    ID = "test.vetoer"

    @hook("on_reduced_to_zero", phase=Phase.EARLY)
    def no_drop(self, ctx: ReducedToZeroContext) -> R:
        ctx.veto("no")
        return R.APPLIED

    @hook("on_kill", phase=Phase.EARLY)
    def no_kill(self, ctx: KillContext) -> R:
        ctx.veto("no")
        return R.APPLIED

    @hook("on_death_save", phase=Phase.EARLY)
    def no_save(self, ctx: DeathSaveContext) -> R:
        ctx.veto("no")
        return R.APPLIED

    @hook("on_ability_check", phase=Phase.EARLY)
    def no_check(self, ctx: AbilityCheckContext) -> R:
        ctx.veto("no")
        return R.APPLIED


class StabilizeVetoer(HookSource):
    ID = "test.stabilize_vetoer"

    @hook("on_stabilize", phase=Phase.EARLY)
    def no_stable(self, ctx: StabilizeContext) -> R:
        ctx.veto("no")
        return R.APPLIED


class Blessed(HookSource):
    """Advantage and +2 on a death save."""

    ID = "test.blessed"

    @hook("on_death_save")
    def bless(self, ctx: DeathSaveContext) -> R:
        ctx.grant_advantage("blessed")
        ctx.bonus.add(flat=2)
        return R.APPLIED


class Guided(HookSource):
    """+2 on an ability check."""

    ID = "test.guided"

    @hook("on_ability_check")
    def guide(self, ctx: AbilityCheckContext) -> R:
        ctx.bonus.add(flat=2)
        return R.APPLIED
