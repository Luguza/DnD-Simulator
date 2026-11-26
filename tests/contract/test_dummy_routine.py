"""The target dummy's turn routine and Regeneration (ROADMAP `w2g2`, debt #59;
`docs/decisions.md` § The `w2g` spec correction, § w2g2)."""

from dataclasses import replace
from typing import Final

import pytest

from dndsim.actions.move import Walk
from dndsim.actions.routine import Routine
from dndsim.apl.dummy import arm_dummy, dummy_apl
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog, Record
from dndsim.core.rng import MersenneRng, ScriptedRng
from dndsim.core.timeline import TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import (
    DummyAttack,
    DummySpec,
    MovementProfile,
    TargetDummy,
    ThreatProfile,
)
from dndsim.creatures.stats import Armour, StatBlock
from dndsim.engine.attack import ATTACK, ATTACK_ROLL
from dndsim.engine.damage import DAMAGE, resolve_damage
from dndsim.engine.healing import HEAL
from dndsim.engine.saves import SAVE
from dndsim.engine.turn import offers
from dndsim.engine.world import World
from dndsim.hooks.contexts import HitContext, TurnContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_TURN_START
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, ActionCost, DamageType, Outcome, SaveOutcome
from dndsim.sim.scenario import Scenario, fight, load
from m1_fixtures import CATALOGUE, fighter

SLASHING: Final = DamageType.SLASHING
FIRE: Final = DamageType.FIRE
HP: Final = 40
CLAW: Final = DummyAttack(6, Dice(2, 8), SLASHING, flat=4)
"""+6 to hit, 2d8 + 4 Slashing."""


def hero(name: str, cell: Cell, *, ac: int = 16, hp: int = HP) -> Creature:
    """A player character, so 0 hit points leaves it dying rather than dead."""
    return Creature(
        name=name,
        stats=StatBlock(abilities=dict.fromkeys(Ability, 10), max_hp=hp, speed_ft=30),
        position=cell,
        armour=Armour.natural(ac),
        player_character=True,
    )


def ready_dummy(spec: DummySpec, *heroes: Creature) -> tuple[TargetDummy, World]:
    """A dummy at the origin with its turn begun, the heroes on the other
    side, all in a world that keeps a full log."""
    dummy = TargetDummy(spec, name="dummy", position=Cell(0, 0))
    world = World(log=EventLog())
    world.seat(dummy, "monsters")
    for creature in heroes:
        world.seat(creature, "heroes")
    dummy.economy.turn_started(dummy.speed_ft)
    return dummy, world


def records(world: World, kind: str) -> list[Record]:
    log = world.log
    assert log is not None
    return [record for record in log if record[0] == kind]


# --- an attack entry -----------------------------------------------------------


def test_an_attack_totals_exactly_d20_plus_to_hit() -> None:
    """The dummy's Strength save is +4 and it has a proficiency bonus, neither
    of which reaches the scripted total."""
    target = hero("hero", Cell(1, 0), ac=17)
    dummy, world = ready_dummy(
        DummySpec(saves={Ability.STRENGTH: 4}, damage_per_round=(CLAW,)), target
    )

    Routine(dummy, world=world, log=world.log).execute(rng=ScriptedRng([11, 3, 5]))

    assert records(world, ATTACK_ROLL) == [
        (ATTACK_ROLL, "dummy", "hero", 11, 17, False, False)
    ]
    assert records(world, ATTACK) == [
        (ATTACK, "dummy", "hero", "dummy attack 1", Outcome.HIT, 12)
    ]
    assert target.hp == HP - (3 + 5 + 4) == 28


def test_one_short_of_the_ac_misses() -> None:
    target = hero("hero", Cell(1, 0), ac=18)
    dummy, world = ready_dummy(DummySpec(damage_per_round=(CLAW,)), target)

    Routine(dummy, world=world).execute(rng=ScriptedRng([11]))

    assert target.hp == HP


def test_a_critical_hit_doubles_the_dice_and_not_the_flat() -> None:
    """A Strength save of -2 is no part of the flat either."""
    target = hero("hero", Cell(1, 0), ac=30)
    dummy, world = ready_dummy(
        DummySpec(saves={Ability.STRENGTH: -2}, damage_per_round=(CLAW,)), target
    )

    dealt = Routine(dummy, world=world).execute(rng=ScriptedRng([20, 1, 2, 3, 4]))

    assert dealt == (1 + 2 + 3 + 4) + 4 == 14
    assert target.hp == HP - 14


def test_the_defenders_hooks_and_the_damage_order_apply() -> None:
    """Resistance halves the scripted damage, rounded down."""
    target = TargetDummy(
        DummySpec(ac=10, hp=HP, resistances=frozenset({SLASHING})),
        name="hero",
        position=Cell(1, 0),
    )
    dummy, world = ready_dummy(DummySpec(damage_per_round=(CLAW,)), target)

    Routine(dummy, world=world).execute(rng=ScriptedRng([10, 3, 4]))

    assert target.hp == HP - (3 + 4 + 4) // 2 == 35


def test_an_attack_reaches_five_feet_and_no_further() -> None:
    near = hero("near", Cell(1, 0), ac=1)
    far = hero("far", Cell(2, 0), ac=1)
    dummy, world = ready_dummy(
        DummySpec(damage_per_round=(replace(CLAW, targets=2),)), near, far
    )

    Routine(dummy, world=world).execute(rng=ScriptedRng([10, 1, 1]))

    assert (near.hp, far.hp) == (HP - 6, HP)


def test_each_entry_once_in_order_against_distinct_targets() -> None:
    """Two targets for the first entry, one for the second, nearest first."""
    first = hero("first", Cell(1, 0), ac=1)
    second = hero("second", Cell(0, 1), ac=1)
    third = hero("third", Cell(-1, -1), ac=1)
    bite = DummyAttack(2, Dice(1, 4), FIRE)
    dummy, world = ready_dummy(
        DummySpec(damage_per_round=(replace(CLAW, targets=2), bite)),
        first,
        second,
        third,
    )

    Routine(dummy, world=world, log=world.log).execute(
        rng=ScriptedRng([10, 1, 1, 10, 2, 2, 10, 3])
    )

    assert [(r[2], r[3]) for r in records(world, ATTACK)] == [
        ("first", "dummy attack 1"),
        ("second", "dummy attack 1"),
        ("first", "dummy attack 2"),
    ]
    assert (first.hp, second.hp, third.hp) == (HP - 6 - 3, HP - 8, HP)


class Thorns(HookSource):
    """Drops whoever hits its owner to 0 hit points."""

    @hook("on_hit_taken")
    def prick(self, ctx: HitContext) -> R:
        attacker = ctx.attacker
        assert isinstance(attacker, Creature)
        resolve_damage(attacker, {SLASHING: attacker.hp}, rng=ScriptedRng([]))
        return R.APPLIED


def test_a_dummy_dropped_partway_stops() -> None:
    first = hero("first", Cell(1, 0), ac=1)
    first.attach(Thorns(first))
    second = hero("second", Cell(0, 1), ac=1)
    dummy, world = ready_dummy(
        DummySpec(damage_per_round=(replace(CLAW, targets=2), CLAW)), first, second
    )

    Routine(dummy, world=world).execute(rng=ScriptedRng([10, 1, 1]))

    assert dummy.hp == 0
    assert (first.hp, second.hp) == (HP - 6, HP)


def test_lowest_hp_picks_by_hit_points() -> None:
    hale = hero("hale", Cell(1, 0), ac=1)
    hurt = hero("hurt", Cell(0, 1), ac=1, hp=10)
    dummy, world = ready_dummy(
        DummySpec(damage_per_round=(CLAW,), threat_profile=ThreatProfile.LOWEST_HP),
        hale,
        hurt,
    )

    Routine(dummy, world=world).execute(rng=ScriptedRng([10, 1, 1]))

    assert (hale.hp, hurt.hp) == (HP, 10 - 6)


def test_a_downed_enemy_is_not_picked() -> None:
    """A creature at 0 hit points is left out, as the APL's `enemies` groups
    leave it out, so `lowest_hp` swings at the standing one."""
    dying = hero("dying", Cell(1, 0), ac=1)
    resolve_damage(dying, {SLASHING: HP}, rng=ScriptedRng([]))
    assert dying.is_down and not dying.dead
    standing = hero("standing", Cell(0, 1), ac=1)
    dummy, world = ready_dummy(
        DummySpec(damage_per_round=(CLAW,), threat_profile=ThreatProfile.LOWEST_HP),
        dying,
        standing,
    )

    Routine(dummy, world=world).execute(rng=ScriptedRng([10, 1, 1]))

    assert (dying.hp, standing.hp) == (0, HP - 6)


def test_only_downed_enemies_offer_no_routine() -> None:
    dying = hero("dying", Cell(1, 0))
    resolve_damage(dying, {SLASHING: HP}, rng=ScriptedRng([]))
    dummy, world = ready_dummy(DummySpec(damage_per_round=(CLAW,)), dying)

    assert not Routine(dummy, world=world).is_legal()


# --- a save entry --------------------------------------------------------------


def test_a_save_entry_deals_full_on_a_failure_and_half_on_a_success() -> None:
    """At any distance: one hero is sixty feet away. Each target rolls its
    own damage, after every save is made."""
    breath = DummyAttack(
        None, Dice(2, 6), FIRE, flat=1, targets=2, save=Ability.DEXTERITY
    )
    near = hero("near", Cell(1, 0))
    far = hero("far", Cell(12, 0))
    dummy, world = ready_dummy(
        DummySpec(save_dc=13, damage_per_round=(breath,)), near, far
    )

    dealt = Routine(dummy, world=world, log=world.log).execute(
        rng=ScriptedRng([12, 13, 3, 4, 6, 6])
    )

    assert records(world, SAVE) == [
        (SAVE, "near", Ability.DEXTERITY, 13, 12, SaveOutcome.FAILURE),
        (SAVE, "far", Ability.DEXTERITY, 13, 13, SaveOutcome.SUCCESS),
    ]
    assert (near.hp, far.hp) == (HP - (3 + 4 + 1), HP - (6 + 6 + 1) // 2)
    assert dealt == 8 + 6
    assert [r[1:] for r in records(world, DAMAGE)] == [
        ("dummy", "near", 8),
        ("dummy", "far", 6),
    ]


# --- what is offered, and what the dummy's list does ---------------------------


def test_the_routine_is_offered_only_to_a_dummy_that_has_one() -> None:
    target = hero("hero", Cell(1, 0))
    armed, world = ready_dummy(DummySpec(damage_per_round=(CLAW,)), target)
    bare, bare_world = ready_dummy(DummySpec(), hero("hero", Cell(1, 0)))

    assert [o for o in offers(world, armed) if isinstance(o, Routine)]
    assert not [o for o in offers(bare_world, bare) if isinstance(o, Routine)]
    armed.economy.spend(ActionCost.ACTION)
    assert not [o for o in offers(world, armed) if isinstance(o, Routine)]


def test_the_dummy_closes_then_takes_its_routine() -> None:
    target = hero("hero", Cell(4, 0), ac=1)
    dummy, world = ready_dummy(DummySpec(damage_per_round=(CLAW,)), target)
    policy = arm_dummy(dummy, world, rng=ScriptedRng([]))

    walk = policy.choose(dummy, offers(world, dummy))
    assert isinstance(walk, Walk)
    walk.execute(rng=ScriptedRng([]))
    chosen = policy.choose(dummy, offers(world, dummy))

    assert isinstance(chosen, Routine)
    assert dummy_apl(dummy.spec).entries[1].action == Routine.ID


# --- refused at load -----------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "knob"),
    [
        (
            {"damage_per_round": [{"save": "dex", "dice": "1d6", "type": "fire"}]},
            "save_dc",
        ),
        ({"save_dc": 13}, "save_dc"),
        (
            {
                "damage_per_round": [{"to_hit": 5, "dice": "1d6", "type": "fire"}],
                "movement_profile": "kites_at(30)",
            },
            "movement_profile",
        ),
    ],
)
def test_a_knob_that_cannot_act_is_refused_by_name(
    document: dict[str, object], knob: str
) -> None:
    with pytest.raises(ValueError, match=knob):
        DummySpec.from_mapping(document)


def test_kiting_within_the_reach_is_accepted() -> None:
    spec = DummySpec(
        damage_per_round=(CLAW,), movement_profile=MovementProfile.kites_at(5)
    )
    assert spec.movement_profile.range_ft == 5


def test_a_save_entry_kites_at_any_distance() -> None:
    breath = DummyAttack(None, Dice(1, 6), FIRE, save=Ability.DEXTERITY)
    spec = DummySpec(
        save_dc=12,
        damage_per_round=(breath,),
        movement_profile=MovementProfile.kites_at(30),
    )
    assert spec.save_dc == 12


# --- Regeneration --------------------------------------------------------------


def turn_start(dummy: TargetDummy) -> None:
    time = TimeKey(1, dummy.initiative, TurnPhase.START)
    dummy.dispatch(ON_TURN_START, TurnContext(creature=dummy, time=time))


def regenerating(amount: int) -> TargetDummy:
    dummy, world = ready_dummy(DummySpec(hp=50, regeneration=amount))
    arm_dummy(dummy, world, rng=ScriptedRng([]))
    return dummy


def test_regeneration_heals_at_the_start_of_the_dummys_turn() -> None:
    dummy = regenerating(10)
    resolve_damage(dummy, {SLASHING: 25}, rng=ScriptedRng([]))

    turn_start(dummy)
    assert dummy.hp == 25 + 10
    turn_start(dummy)
    turn_start(dummy)
    assert dummy.hp == 50


def test_regeneration_does_not_raise_a_dummy_at_zero() -> None:
    dummy = regenerating(10)
    resolve_damage(dummy, {SLASHING: 50}, rng=ScriptedRng([]))

    turn_start(dummy)
    assert dummy.hp == 0


def test_no_regeneration_attaches_nothing() -> None:
    dummy = regenerating(0)
    resolve_damage(dummy, {SLASHING: 25}, rng=ScriptedRng([]))

    turn_start(dummy)
    assert dummy.hp == 25


# --- in a fight `w2g`'s scenario assembles --------------------------------------


def run_fight(dummy: DummySpec, seed: int = 5) -> list[Record]:
    log = EventLog()
    loaded = load(Scenario(builds=(fighter(),), catalogue=CATALOGUE, dummy=dummy))
    fight(loaded, rng=MersenneRng(seed), log=log)
    return list(log)


def test_in_a_fight_the_dummy_strikes_back() -> None:
    log = run_fight(DummySpec(ac=15, hp=60, damage_per_round=(CLAW,)))
    name = fighter().name
    swings = [r for r in log if r[0] == ATTACK and r[1] == "Target Dummy"]
    assert swings
    assert all(r[2] == name and r[3] == "Target Dummy attack 1" for r in swings)
    assert any(r[5] != 0 for r in swings)


def test_in_a_fight_the_dummy_forces_its_save() -> None:
    breath = DummyAttack(None, Dice(2, 6), FIRE, save=Ability.DEXTERITY)
    log = run_fight(DummySpec(ac=15, hp=60, save_dc=14, damage_per_round=(breath,)))
    saves = [r for r in log if r[0] == SAVE]
    assert saves
    assert all(r[1] == fighter().name and r[3] == 14 for r in saves)
    assert any(r[0] == DAMAGE and r[1] == "Target Dummy" for r in log)


def test_in_a_fight_the_dummy_regenerates() -> None:
    log = run_fight(DummySpec(ac=15, hp=60, regeneration=7))
    heals = [r for r in log if r[0] == HEAL and r[2] == "Target Dummy"]
    assert heals
    assert {r[3] for r in heals} <= set(range(8))
    assert any(r[3] == 7 for r in heals)
