"""The importer against the fetched XPHB data, asked deliberately.

Needs `python -m dndsim.content.fetch` first; run with `pytest -m content`.
Without the data, `Dataset()` raises rather than the test passing on nothing.
"""

import pytest

from dndsim.content.loader import Dataset
from dndsim.content.refs import class_features, subclass_features
from dndsim.content.species import species
from dndsim.content.tables import class_tables, table_value
from dndsim.content.weapons import NOT_IMPORTED, armour, masteries, weapons
from dndsim.core.dice import Dice
from dndsim.rules.enums import Mastery

pytestmark = pytest.mark.content

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
    leaked = [f.name for f in texts if "{@" in f.text or "{@" in f.name]
    assert texts
    assert leaked == []
