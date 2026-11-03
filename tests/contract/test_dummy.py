"""The target dummy contract: every knob, read from a parsed YAML mapping.

Sources: DESIGN.md § The target dummy; ROADMAP w2d; XPHB (2024) "Damage and
Healing", Rules Glossary "Resistance", "Vulnerability", "Immunity";
`docs/decisions.md` § w2d.
"""

import pytest

from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.dummy import (
    LEGENDARY_RESISTANCE,
    DummyAttack,
    DummySpec,
    Movement,
    MovementProfile,
    TargetDummy,
    ThreatProfile,
    build_dummies,
)
from dndsim.engine.damage import resolve_damage
from dndsim.engine.saves import save_bonus, saving_throw
from dndsim.rules.enums import (
    Ability,
    DamageType,
    RecoveryTrigger,
    SaveOutcome,
    Size,
)


def one(spec: DummySpec) -> TargetDummy:
    (dummy,) = build_dummies(spec)
    return dummy


# -- the AC sweep -------------------------------------------------------------


@pytest.mark.parametrize("ac", range(10, 23))
def test_ac_sweeps_10_to_22_from_a_mapping(ac: int) -> None:
    dummy = one(DummySpec.from_mapping({"ac": ac}))
    assert dummy.armour_class == ac


def test_ac_is_not_moved_by_the_dexterity_save_bonus() -> None:
    # Dex save +4 means a Dexterity score of 18; a printed AC already counts it.
    dummy = one(DummySpec(ac=13, saves={Ability.DEXTERITY: 4}))
    assert dummy.armour_class == 13


# -- the plain knobs ------------------------------------------------------------


def test_the_defaults() -> None:
    dummy = one(DummySpec())
    assert dummy.name == "Target Dummy"
    assert (dummy.armour_class, dummy.hp, dummy.max_hp) == (15, 50, 50)
    assert (dummy.size, dummy.speed_ft, dummy.position) == (Size.MEDIUM, 30, Cell(0, 0))
    assert dummy.spec.movement_profile == MovementProfile.closes_to_melee()
    assert dummy.spec.threat_profile is ThreatProfile.NEAREST
    assert not dummy.player_character
    assert dummy.dispatcher.sources() == ()


def test_every_knob_reads_from_a_mapping() -> None:
    spec = DummySpec.from_mapping(
        {
            "name": "Ogre",
            "ac": 11,
            "hp": 68,
            "count": 2,
            "size": "large",
            "speed_ft": 40,
            "position": [3, 4],
            "saves": {"str": 4, "wis": -2},
            "save_dc": 13,
            "damage_per_round": [
                {"to_hit": 6, "dice": "2d8", "flat": 4, "type": "bludgeoning"},
                {"to_hit": 6, "dice": "1d6", "type": "fire", "targets": 2},
            ],
            "resistances": ["cold"],
            "immunities": ["poison"],
            "vulnerabilities": ["fire"],
            "legendary_resistances": 3,
            "regeneration": 10,
            "threat_profile": "lowest_hp",
            "movement_profile": "kites_at(30)",
        }
    )
    assert spec == DummySpec(
        name="Ogre",
        ac=11,
        hp=68,
        count=2,
        size=Size.LARGE,
        speed_ft=40,
        position=Cell(3, 4),
        saves={Ability.STRENGTH: 4, Ability.WISDOM: -2},
        save_dc=13,
        damage_per_round=(
            DummyAttack(6, Dice(2, 8), DamageType.BLUDGEONING, flat=4),
            DummyAttack(6, Dice(1, 6), DamageType.FIRE, targets=2),
        ),
        resistances=frozenset({DamageType.COLD}),
        immunities=frozenset({DamageType.POISON}),
        vulnerabilities=frozenset({DamageType.FIRE}),
        legendary_resistances=3,
        regeneration=10,
        threat_profile=ThreatProfile.LOWEST_HP,
        movement_profile=MovementProfile.kites_at(30),
    )


def test_a_mapping_key_left_out_keeps_its_default() -> None:
    assert DummySpec.from_mapping({}) == DummySpec()


def test_save_bonuses_are_what_the_engine_adds() -> None:
    dummy = one(DummySpec(saves={Ability.DEXTERITY: 7, Ability.WISDOM: -3}))
    assert save_bonus(dummy, Ability.DEXTERITY) == 7
    assert save_bonus(dummy, Ability.WISDOM) == -3
    assert save_bonus(dummy, Ability.STRENGTH) == 0


def test_count_stands_in_a_row_one_footprint_apart() -> None:
    dummies = build_dummies(
        DummySpec(count=3, size=Size.LARGE, position=Cell(2, 5), name="Ogre")
    )
    assert [d.name for d in dummies] == ["Ogre 1", "Ogre 2", "Ogre 3"]
    assert [d.position for d in dummies] == [Cell(2, 5), Cell(4, 5), Cell(6, 5)]
    assert len({id(d.dispatcher) for d in dummies}) == 3


# -- the movement profile --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "profile"),
    [
        ("static", MovementProfile.static()),
        ("closes_to_melee", MovementProfile.closes_to_melee()),
        ("kites_at(30)", MovementProfile.kites_at(30)),
        (" kites_at( 60 ) ", MovementProfile.kites_at(60)),
    ],
)
def test_movement_profile_parses(text: str, profile: MovementProfile) -> None:
    assert MovementProfile.parse(text) == profile
    assert MovementProfile.parse(str(profile)) == profile


@pytest.mark.parametrize("text", ["kites_at", "kites_at(0)", "closes", "kites_at(x)"])
def test_movement_profile_refuses(text: str) -> None:
    with pytest.raises(ValueError):
        MovementProfile.parse(text)


def test_only_kites_at_takes_a_range() -> None:
    with pytest.raises(ValueError, match="only when it is kites_at"):
        MovementProfile(Movement.STATIC, 30)


# -- damage defences ------------------------------------------------------------


def test_resistance_halves_rounding_down() -> None:
    dummy = one(DummySpec(hp=50, resistances=frozenset({DamageType.FIRE})))
    taken = resolve_damage(dummy, {DamageType.FIRE: 11}, rng=ScriptedRng([]))
    assert (taken, dummy.hp) == (5, 45)


def test_immunity_takes_nothing() -> None:
    dummy = one(DummySpec(hp=50, immunities=frozenset({DamageType.POISON})))
    taken = resolve_damage(dummy, {DamageType.POISON: 11}, rng=ScriptedRng([]))
    assert (taken, dummy.hp) == (0, 50)


def test_vulnerability_doubles() -> None:
    dummy = one(DummySpec(hp=50, vulnerabilities=frozenset({DamageType.FIRE})))
    taken = resolve_damage(
        dummy,
        {DamageType.FIRE: 7, DamageType.SLASHING: 4},
        rng=ScriptedRng([]),
    )
    # 7 fire doubled to 14, 4 slashing untouched.
    assert (taken, dummy.hp) == (18, 32)


# -- legendary resistance -------------------------------------------------------


def test_legendary_resistance_turns_failures_into_successes_until_spent() -> None:
    dummy = one(DummySpec(legendary_resistances=2))
    pool = dummy.resources[LEGENDARY_RESISTANCE]
    # Every save: d20 of 2, +0, against DC 15 — a failure on the dice.
    for left in (1, 0):
        result = saving_throw(
            dummy, ability=Ability.WISDOM, dc=15, rng=ScriptedRng([2])
        )
        assert result.outcome is SaveOutcome.SUCCESS
        assert result.forced_by == ("legendary_resistance: a use spent",)
        assert pool.current == left
    result = saving_throw(dummy, ability=Ability.WISDOM, dc=15, rng=ScriptedRng([2]))
    assert result.outcome is SaveOutcome.FAILURE
    dummy.resources.recover(RecoveryTrigger.LONG_REST)
    assert pool.current == 2


def test_legendary_resistance_is_not_spent_on_a_success() -> None:
    dummy = one(DummySpec(legendary_resistances=1))
    result = saving_throw(dummy, ability=Ability.WISDOM, dc=15, rng=ScriptedRng([15]))
    assert result.outcome is SaveOutcome.SUCCESS
    assert result.forced_by == ()
    assert dummy.resources[LEGENDARY_RESISTANCE].current == 1


# -- refusals -------------------------------------------------------------------


def test_a_misspelt_knob_is_refused_with_a_suggestion() -> None:
    with pytest.raises(ValueError, match="did you mean 'movement_profile'"):
        DummySpec.from_mapping({"movment_profile": "static"})


def test_a_misspelt_damage_type_is_refused_with_a_suggestion() -> None:
    with pytest.raises(ValueError, match="did you mean 'fire'"):
        DummySpec.from_mapping({"resistances": ["fier"]})


@pytest.mark.parametrize(
    "document",
    [
        {"ac": 0},
        {"ac": "15"},
        {"ac": True},
        {"hp": 0},
        {"count": 0},
        {"saves": {"dex": 11}},
        {"saves": {"dexterity": 2}},
        {"position": [1]},
        {"damage_per_round": [{"to_hit": 5, "dice": "1d6"}]},
        {"damage_per_round": [{"to_hit": 5, "dice": "1d6", "type": "fire", "x": 1}]},
        {"damage_per_round": [{"to_hit": 5, "dice": "1d6+2", "type": "fire"}]},
        {"movement_profile": "kites_at"},
    ],
)
def test_a_bad_knob_is_refused(document: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DummySpec.from_mapping(document)
