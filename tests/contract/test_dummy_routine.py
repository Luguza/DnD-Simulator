"""The dummy's turn routine and Regeneration (ROADMAP `w2g2`, debt #59;
`docs/decisions.md` § The `w2g` spec correction, § w2g2)."""

from dataclasses import replace
from typing import Final

import pytest

from dndsim.actions.routine import Routine
from dndsim.apl.dummy import arm_dummy
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import (
    DummyAttack,
    DummySpec,
    MovementProfile,
    TargetDummy,
    ThreatProfile,
    build_dummies,
)
from dndsim.creatures.stats import StatBlock
from dndsim.engine.attack import ATTACK, ATTACK_ROLL
from dndsim.engine.healing import HEAL
from dndsim.engine.saves import SAVE
from dndsim.engine.turn import offers, take_turn
from dndsim.engine.world import World
from dndsim.rules.enums import Ability, DamageType, Outcome, SaveOutcome
from dndsim.sim.runner import iteration_seeds, run
from dndsim.sim.scenario import Scenario
from m1_fixtures import CATALOGUE, fighter

HP: Final = 40
FIRE: Final = DamageType.FIRE
CLUB: Final = DummyAttack(4, Dice(1, 6), DamageType.BLUDGEONING, flat=3)
"""+4 to hit, 1d6 + 3."""
BREATH: Final = DummyAttack(None, Dice(2, 6), FIRE, save=Ability.DEXTERITY)
STRONG: Final = {Ability.STRENGTH: 5}
"""Strength +5: the attack sequence's own terms come to +7 to hit and +5
damage, neither of which the routine's numbers are."""


def key(initiative: int) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=10, player_character=False, tiebreak=0.5
    )


def hero(name: str, cell: Cell, *, hp: int = HP, ac: int = 10) -> Creature:
    """AC 10, Dexterity save +0."""
    one = Creature(
        name=name,
        stats=StatBlock(
            abilities={ability: 10 for ability in Ability}, max_hp=HP, speed_ft=30
        ),
        position=cell,
        initiative=key(5),
    )
    one.hp = hp
    return one


def dummy(**knobs: object) -> TargetDummy:
    (one,) = build_dummies(DummySpec(**knobs))  # type: ignore[arg-type]
    one.initiative = key(20)
    return one


def fight(actor: TargetDummy, *heroes: Creature, rng: ScriptedRng) -> EventLog:
    """Seat `actor` against `heroes`, arm it and run its turn of round 1."""
    log = EventLog(LogLevel.FULL)
    world = World(log=log)
    world.seat(actor, "monsters")
    for one in heroes:
        world.seat(one, "heroes")
    policy = arm_dummy(actor, world, rng=rng)
    take_turn(world, actor, 1, policy=policy, rng=rng)
    return log


def records(log: EventLog, kind: str) -> list[tuple[object, ...]]:
    return [record for record in log if record[0] == kind]


# --- Refused at load ---------------------------------------------------------


@pytest.mark.parametrize(
    ("knobs", "fragment"),
    [
        ({"damage_per_round": (BREATH,)}, "no save_dc"),
        ({"save_dc": 13}, "save_dc is set"),
        ({"save_dc": 13, "damage_per_round": (CLUB,)}, "save_dc is set"),
        (
            {
                "damage_per_round": (CLUB,),
                "movement_profile": MovementProfile.kites_at(10),
            },
            "movement_profile kites_at\\(10\\)",
        ),
    ],
)
def test_a_knob_nothing_acts_on_is_refused_by_name(
    knobs: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        DummySpec(**knobs)  # type: ignore[arg-type]


def test_a_dummy_that_kites_within_its_reach_is_accepted() -> None:
    DummySpec(damage_per_round=(CLUB,), movement_profile=MovementProfile.kites_at(5))


def test_a_save_entry_may_kite_at_any_range() -> None:
    DummySpec(
        damage_per_round=(BREATH,),
        save_dc=13,
        movement_profile=MovementProfile.kites_at(60),
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"dice": "1d6", "type": "fire"},
        {"to_hit": 4, "save": "dex", "dice": "1d6", "type": "fire"},
    ],
)
def test_an_entry_is_an_attack_or_a_save(entry: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="to_hit or a save"):
        DummySpec.from_mapping({"damage_per_round": [entry], "save_dc": 13})


def test_a_save_entry_reads_from_a_mapping() -> None:
    spec = DummySpec.from_mapping(
        {
            "save_dc": 14,
            "damage_per_round": [{"save": "dex", "dice": "2d6", "type": "fire"}],
        }
    )
    assert spec.damage_per_round == (BREATH,)


# --- An attack entry ---------------------------------------------------------


def test_an_attack_entry_totals_d20_plus_to_hit_and_deals_dice_plus_flat() -> None:
    """A natural 6 + 4 = 10 hits AC 10 (+7 from the sequence's own terms
    would make it 13); a 5 on the d6 + 3 is 8."""
    actor = dummy(saves=STRONG, damage_per_round=(CLUB,))
    target = hero("hero", Cell(1, 0))
    rng = ScriptedRng([6, 5])
    log = fight(actor, target, rng=rng)

    assert records(log, ATTACK_ROLL) == [
        (ATTACK_ROLL, "Target Dummy", "hero", 6, 10, False, False)
    ]
    assert records(log, ATTACK) == [
        (ATTACK, "Target Dummy", "hero", "routine 1", Outcome.HIT, 8)
    ]
    assert target.hp == HP - 8
    assert rng.exhausted


def test_an_attack_entry_misses_below_the_ac() -> None:
    """A natural 5 + 4 = 9 misses AC 10, where +7 would have hit."""
    actor = dummy(saves=STRONG, damage_per_round=(CLUB,))
    target = hero("hero", Cell(1, 0))
    rng = ScriptedRng([5])
    fight(actor, target, rng=rng)

    assert target.hp == HP
    assert rng.exhausted


def test_a_critical_hit_doubles_the_dice_and_not_the_flat() -> None:
    """2d6 on a natural 20: 5 + 4 + 3 = 12, not 5 + 4 + 6."""
    actor = dummy(saves=STRONG, damage_per_round=(CLUB,))
    target = hero("hero", Cell(1, 0))
    rng = ScriptedRng([20, 5, 4])
    fight(actor, target, rng=rng)

    assert target.hp == HP - 12
    assert rng.exhausted


@pytest.mark.parametrize(("cell", "offered"), [(Cell(1, 1), True), (Cell(2, 0), False)])
def test_an_attack_entry_reaches_five_feet_and_no_further(
    cell: Cell, offered: bool
) -> None:
    actor = dummy(damage_per_round=(CLUB,))
    world = World()
    world.seat(actor, "monsters")
    world.seat(hero("hero", cell), "heroes")
    actor.economy.turn_started(actor.speed_ft)

    found = any(isinstance(offer, Routine) for offer in offers(world, actor))
    assert found is offered


def test_the_defender_s_hooks_apply() -> None:
    """A fire-resistant target halves 5 + 3 = 8 fire to 4: the routine's
    damage goes through the one resolution order."""
    actor = dummy(damage_per_round=(replace(CLUB, damage_type=FIRE),))
    target = dummy(resistances=frozenset({FIRE}), name="Resistant", hp=HP)
    target.position = Cell(1, 0)
    rng = ScriptedRng([15, 5])
    fight(actor, target, rng=rng)

    assert target.hp == HP - 4
    assert rng.exhausted


def test_targets_are_distinct_and_rolled_once_each_in_threat_order() -> None:
    """`targets: 2` with `lowest_hp`: the wounded hero first, then the healthy
    one, one d20 and one d6 apiece; the third hero is not attacked."""
    actor = dummy(
        damage_per_round=(replace(CLUB, targets=2),),
        threat_profile=ThreatProfile.LOWEST_HP,
        movement_profile=MovementProfile.static(),
    )
    healthy = hero("healthy", Cell(1, 0))
    wounded = hero("wounded", Cell(0, 1), hp=10)
    third = hero("third", Cell(1, 1), hp=30)
    rng = ScriptedRng([10, 1, 10, 2])
    log = fight(actor, healthy, wounded, third, rng=rng)

    assert [record[2] for record in records(log, ATTACK)] == ["wounded", "third"]
    assert wounded.hp == 10 - 4
    assert third.hp == 30 - 5
    assert healthy.hp == HP
    assert rng.exhausted


def test_entries_run_once_each_in_order() -> None:
    actor = dummy(
        damage_per_round=(CLUB, replace(CLUB, flat=0)),
        movement_profile=MovementProfile.static(),
    )
    target = hero("hero", Cell(1, 0))
    rng = ScriptedRng([10, 1, 10, 2])
    log = fight(actor, target, rng=rng)

    assert [record[3] for record in records(log, ATTACK)] == [
        "routine 1",
        "routine 2",
    ]
    assert target.hp == HP - (1 + 3) - 2
    assert rng.exhausted


# --- A save entry ------------------------------------------------------------


@pytest.mark.parametrize(
    ("natural", "outcome", "taken"),
    [(12, SaveOutcome.FAILURE, 7), (13, SaveOutcome.SUCCESS, 3)],
)
def test_a_save_entry_is_save_for_half_at_the_dc(
    natural: int, outcome: SaveOutcome, taken: int
) -> None:
    """DC 13 against +0; 2d6 of 3 + 4 = 7, halved to 3 on a success."""
    actor = dummy(
        damage_per_round=(BREATH,),
        save_dc=13,
        movement_profile=MovementProfile.static(),
    )
    target = hero("hero", Cell(9, 0))
    rng = ScriptedRng([natural, 3, 4])
    log = fight(actor, target, rng=rng)

    assert records(log, SAVE) == [
        (SAVE, "hero", Ability.DEXTERITY, 13, natural, outcome)
    ]
    assert target.hp == HP - taken
    assert rng.exhausted


def test_a_save_entry_picks_from_every_enemy_at_any_distance() -> None:
    actor = dummy(
        damage_per_round=(replace(BREATH, targets=2),),
        save_dc=13,
        movement_profile=MovementProfile.static(),
    )
    near = hero("near", Cell(20, 0))
    far = hero("far", Cell(40, 0))
    rng = ScriptedRng([1, 1, 1, 1, 1, 1])
    fight(actor, far, near, rng=rng)

    assert near.hp == far.hp == HP - 2
    assert rng.exhausted


# --- Regeneration ------------------------------------------------------------


def test_regeneration_heals_at_the_start_of_the_dummy_s_turn() -> None:
    actor = dummy(regeneration=10, movement_profile=MovementProfile.static())
    actor.hp = 20
    log = fight(actor, hero("hero", Cell(9, 0)), rng=ScriptedRng([]))

    assert actor.hp == 30
    assert records(log, HEAL) == [(HEAL, "Target Dummy", "Target Dummy", 10)]


def test_regeneration_needs_at_least_one_hit_point() -> None:
    actor = dummy(regeneration=10)
    actor.hp = 0
    log = fight(actor, rng=ScriptedRng([]))

    assert actor.hp == 0
    assert records(log, HEAL) == []


# --- In the fight `w2g`'s scenario assembles ----------------------------------


def test_the_routine_and_regeneration_act_in_a_scenario_fight() -> None:
    """The M1 Fighter against a dummy that strikes back and regenerates: the
    log carries the dummy's routine attacks, its forced saves and its
    healing."""
    spec = DummySpec(
        ac=15,
        hp=60,
        save_dc=12,
        damage_per_round=(CLUB, BREATH),
        regeneration=5,
    )
    scenario = Scenario(builds=(fighter(),), catalogue=CATALOGUE, dummy=spec)
    iterations = run(scenario, iteration_seeds(4, 6), log_level=LogLevel.FULL)

    log = [record for i in iterations for record in i.log or ()]
    struck = [r for r in log if r[0] == ATTACK and r[1] == spec.name]
    assert {r[3] for r in struck} == {"routine 1"}
    assert any(r[0] == SAVE and r[3] == 12 for r in log)
    assert any(r[0] == HEAL and r[2] == spec.name for r in log)
