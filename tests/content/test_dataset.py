"""The importer against the fetched XPHB data, asked deliberately.

Needs `python -m dndsim.content.fetch` first; run with `pytest -m content`.
Without the data, `Dataset()` raises rather than the test passing on nothing.
"""

import re

import pytest

from dndsim.content.backgrounds import Background, backgrounds
from dndsim.content.classes import CharacterClass, classes
from dndsim.content.feats import Feat, FeatCategory, feats
from dndsim.content.loader import Dataset
from dndsim.content.refs import class_features, subclass_features
from dndsim.content.species import species
from dndsim.content.tables import class_tables, table_value
from dndsim.content.weapons import NOT_IMPORTED, armour, masteries, weapons
from dndsim.core.dice import Dice
from dndsim.creatures.stats import Weapon
from dndsim.rules.enums import Mastery
from origin_fixtures import (
    FIGHTER,
    GREAT_WEAPON_FIGHTING,
    GREAT_WEAPON_MASTER,
    ROGUE,
    SAVAGE_ATTACKER,
    SOLDIER,
)
from weapon_fixtures import (
    BLOWGUN,
    DAGGER,
    DART,
    GREATSWORD,
    HANDAXE,
    LONGBOW,
    MAUL,
)

pytestmark = pytest.mark.content

SOURCE_CODE = re.compile(r"\b(?:X?PHB|EFA|XMM|XDMG)\b")
"""A source abbreviation, which a tag read at the wrong segment prints."""

CLASSES = (
    "Barbarian",
    "Bard",
    "Cleric",
    "Druid",
    "Fighter",
    "Monk",
    "Paladin",
    "Ranger",
    "Rogue",
    "Sorcerer",
    "Warlock",
    "Wizard",
)


@pytest.fixture(scope="module")
def data() -> Dataset:
    return Dataset()


def test_the_dataset_is_pinned_to_a_commit(data: Dataset) -> None:
    assert len(data.version) == 40
    int(data.version, 16)


def test_all_38_xphb_weapons_are_imported_or_declared(data: Dataset) -> None:
    table = weapons(data)
    assert len(table) + len(NOT_IMPORTED) == 38
    assert table["Greatsword"].damage == Dice(2, 6)
    assert table["Longbow"].range_ft == (150, 600)
    assert table["Whip"].reach_ft == 10
    assert table["Blowgun"].damage == 1


def test_the_melee_ranged_column_parts_a_dart_from_a_dagger(data: Dataset) -> None:
    """Both are Finesse, Thrown and 20/60; only the column differs."""
    table = weapons(data)
    assert table["Dart"].ranged
    assert not table["Dagger"].ranged


@pytest.mark.parametrize(
    "fixture",
    [GREATSWORD, MAUL, LONGBOW, DAGGER, DART, HANDAXE, BLOWGUN],
    ids=lambda weapon: weapon.name,
)
def test_each_weapon_fixture_is_its_imported_entry(
    data: Dataset, fixture: Weapon
) -> None:
    """The default suite and `-m bench` stand on hand-written copies; this is
    what keeps them the table's (`docs/decisions.md` § w2f2)."""
    assert weapons(data)[fixture.name] == fixture


def test_heavy_armour_carries_the_printed_strength(data: Dataset) -> None:
    table = armour(data)
    assert len(table) == 12
    assert {
        name: a.strength_requirement
        for name, a in table.items()
        if a.strength_requirement
    } == {
        "Chain Mail": 13,
        "Splint Armor": 15,
        "Plate Armor": 15,
    }


def test_the_eight_masteries_match(data: Dataset) -> None:
    assert masteries(data) == frozenset(Mastery)


def test_every_class_table_imports_and_its_slots_agree(data: Dataset) -> None:
    tables = class_tables(data)
    assert {cls for cls, sub in tables if sub is None} == set(CLASSES)
    assert table_value(tables, "Barbarian", "Rage Damage", 9) == 3
    assert table_value(tables, "Rogue", "Sneak Attack", 11) == Dice(6, 6)
    assert table_value(tables, "Monk", "Martial Arts", 17) == Dice(1, 12)


def test_the_ten_species_import(data: Dataset) -> None:
    table = species(data)
    assert len(table) == 10
    assert table["Goliath"].speed_ft == 35


def test_no_5etools_markup_escapes_a_feature(data: Dataset) -> None:
    texts = [f for cls in CLASSES for f in class_features(data, cls)]
    texts += subclass_features(data, "Barbarian", "Berserker")
    texts += [t for s in species(data).values() for t in s.traits]
    leaked = [
        f.name
        for f in texts
        if "{@" in f.text + f.name or SOURCE_CODE.search(f.text + f.name)
    ]
    assert texts
    assert leaked == []


def test_every_weapon_is_simple_or_martial(data: Dataset) -> None:
    table = weapons(data)
    assert sum(w.martial for w in table.values()) == 24
    assert not table["Dagger"].martial
    assert table["Rapier"].martial


def test_every_class_imports_its_facts(data: Dataset) -> None:
    table = classes(data)
    assert set(table) == set(CLASSES)
    assert table["Barbarian"].hit_die == Dice(1, 12)
    assert table["Paladin"].fighting_style_level == 2
    assert table["Monk"].weapon_mastery_level is None
    assert table["Bard"].skills.options is None


def test_class_weapon_training_reads_the_filter(data: Dataset) -> None:
    """XPHB Rogue and Monk "Core Traits": Martial weapons with Finesse or
    Light, and with Light."""
    table, arms = classes(data), weapons(data)
    rogue = {n for n, w in arms.items() if w.martial and table["Rogue"].trained_with(w)}
    monk = {n for n, w in arms.items() if w.martial and table["Monk"].trained_with(w)}
    assert rogue == {"Rapier", "Scimitar", "Shortsword", "Whip", "Hand Crossbow"}
    assert monk == {"Scimitar", "Shortsword", "Hand Crossbow"}
    assert table["Rogue"].trained_with(arms["Dagger"])
    assert not table["Wizard"].trained_with(arms["Longsword"])


def test_the_sixteen_backgrounds_name_origin_feats(data: Dataset) -> None:
    table, feat_table = backgrounds(data), feats(data)
    assert len(table) == 16
    for background in table.values():
        assert feat_table[background.feat].category is FeatCategory.ORIGIN
    assert table["Acolyte"].feat_option == "Cleric"


def test_the_feats_import_by_category(data: Dataset) -> None:
    table = feats(data)
    counts = {c: sum(f.category is c for f in table.values()) for c in FeatCategory}
    assert counts == {
        FeatCategory.ORIGIN: 10,
        FeatCategory.GENERAL: 43,
        FeatCategory.FIGHTING_STYLE: 12,
        FeatCategory.EPIC_BOON: 12,
    }
    assert table["Blessed Warrior"].class_name == "Paladin"
    assert table["Ability Score Improvement"].repeatable


@pytest.mark.parametrize(
    "fixture",
    [
        FIGHTER,
        ROGUE,
        SOLDIER,
        SAVAGE_ATTACKER,
        GREAT_WEAPON_FIGHTING,
        GREAT_WEAPON_MASTER,
    ],
    ids=lambda record: record.name,
)
def test_each_origin_fixture_is_its_imported_entry(
    data: Dataset, fixture: CharacterClass | Background | Feat
) -> None:
    """The default suite stands on hand-written copies (`docs/decisions.md`
    § w2a2)."""
    if isinstance(fixture, CharacterClass):
        assert classes(data)[fixture.name] == fixture
    elif isinstance(fixture, Background):
        assert backgrounds(data)[fixture.name] == fixture
    else:
        assert feats(data)[fixture.name] == fixture
