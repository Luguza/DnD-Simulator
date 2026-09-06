"""The w0b contract: the rules vocabulary is closed, complete and hard to misuse.

These are membership and shape assertions, not behaviour tests. They exist
because every later wave imports this vocabulary: if a damage type goes missing
or a slot table row shifts, ~630 downstream implementations are wrong together,
and this is the cheapest place to notice.

The counts are quoted from XPHB and were cross-checked against the XPHB entries
in the 5etools dataset (38 weapons, 391 spells, the class tables).
"""

import pytest

from dndsim.rules import constants as c
from dndsim.rules.enums import (
    Ability,
    CasterProgression,
    Condition,
    DamageType,
    Mastery,
    Outcome,
    School,
    Size,
    WeaponProperty,
)

ALL_ENUMS = (
    Ability,
    CasterProgression,
    Condition,
    DamageType,
    Mastery,
    Outcome,
    School,
    Size,
    WeaponProperty,
)


def test_every_2024_damage_type_is_present() -> None:
    assert {d.value for d in DamageType} == {
        "acid",
        "bludgeoning",
        "cold",
        "fire",
        "force",
        "lightning",
        "necrotic",
        "piercing",
        "poison",
        "psychic",
        "radiant",
        "slashing",
        "thunder",
    }


def test_every_2024_condition_is_present() -> None:
    assert {x.value for x in Condition} == {
        "blinded",
        "charmed",
        "deafened",
        "exhaustion",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }


def test_2024_statuses_are_not_conditions() -> None:
    # Surprised stopped being a condition in 2024; Bloodied and Concentration
    # are statuses. A feature that treats one as a condition is wrong.
    assert {"surprised", "bloodied", "concentration"}.isdisjoint(
        {x.value for x in Condition}
    )


def test_every_2024_mastery_is_present() -> None:
    assert {m.value for m in Mastery} == {
        "cleave",
        "graze",
        "nick",
        "push",
        "sap",
        "slow",
        "topple",
        "vex",
    }


def test_weapon_properties_are_the_nine_carried_by_xphb_weapons() -> None:
    # 2014's Special is gone; Range is a pair of numbers, not a property.
    assert {p.value for p in WeaponProperty} == {
        "ammunition",
        "finesse",
        "heavy",
        "light",
        "loading",
        "reach",
        "thrown",
        "two_handed",
        "versatile",
    }


def test_abilities_sizes_and_schools_are_complete() -> None:
    assert [a.value for a in Ability] == ["str", "dex", "con", "int", "wis", "cha"]
    assert [s.value for s in Size] == [
        "tiny",
        "small",
        "medium",
        "large",
        "huge",
        "gargantuan",
    ]
    assert len(School) == 8


@pytest.mark.parametrize("enum", ALL_ENUMS)
def test_members_are_unique_and_serialise_as_lowercase_strings(
    enum: type[Ability],
) -> None:
    values = [m.value for m in enum]
    assert len(set(values)) == len(values)
    assert all(isinstance(v, str) and v == v.lower() for v in values)


@pytest.mark.parametrize("enum", ALL_ENUMS)
def test_members_are_not_strings(enum: type[Ability]) -> None:
    # Plain Enum, never StrEnum: `damage_type == "fire"` must be a mypy error
    # rather than a comparison that quietly works until someone misspells it.
    assert all(not isinstance(m, str) for m in enum)


def test_parsing_an_unknown_value_fails_loudly() -> None:
    assert DamageType("fire") is DamageType.FIRE
    with pytest.raises(ValueError):
        DamageType("holy")


def test_outcome_knows_which_results_deal_damage() -> None:
    assert [o.is_hit for o in Outcome] == [False, False, True, True]
    assert [o.is_critical for o in Outcome] == [False, False, False, True]


def test_sizes_are_ordered_smallest_to_largest() -> None:
    assert c.SIZE_ORDER[Size.LARGE] - c.SIZE_ORDER[Size.MEDIUM] == 1
    assert c.SIZE_ORDER[Size.TINY] < c.SIZE_ORDER[Size.GARGANTUAN]
    assert set(c.SIZE_ORDER) == set(Size)


def test_physical_damage_types_are_the_three_rage_resists() -> None:
    assert {
        DamageType.BLUDGEONING,
        DamageType.PIERCING,
        DamageType.SLASHING,
    } == c.PHYSICAL_DAMAGE_TYPES


@pytest.mark.parametrize(
    ("level", "expected"),
    # The XPHB Proficiency Bonus table: +2 at 1-4, +3 at 5-8, +4 at 9-12,
    # +5 at 13-16, +6 at 17-20 — checked at both ends of every band.
    [
        (1, 2),
        (4, 2),
        (5, 3),
        (8, 3),
        (9, 4),
        (12, 4),
        (13, 5),
        (16, 5),
        (17, 6),
        (20, 6),
    ],
)
def test_proficiency_bonus_table(level: int, expected: int) -> None:
    assert c.proficiency_bonus(level) == expected


@pytest.mark.parametrize("level", [0, -1, 21])
def test_proficiency_bonus_rejects_levels_outside_the_table(level: int) -> None:
    with pytest.raises(ValueError, match="outside 1-20"):
        c.proficiency_bonus(level)


@pytest.mark.parametrize(
    ("score", "expected"),
    # (score - 10) / 2 rounded down: 1 -> -5, 3 -> -4, 10 and 11 -> +0, 20 -> +5.
    [(1, -5), (3, -4), (8, -1), (10, 0), (11, 0), (15, 2), (20, 5), (24, 7)],
)
def test_ability_modifier(score: int, expected: int) -> None:
    assert c.ability_modifier(score) == expected


def test_slot_tables_have_the_shape_of_the_printed_class_tables() -> None:
    for table, width in (
        (c.FULL_CASTER_SLOTS, 9),
        (c.HALF_CASTER_SLOTS, 5),
        (c.THIRD_CASTER_SLOTS, 4),
    ):
        assert len(table) == c.MAX_CHARACTER_LEVEL
        assert {len(row) for row in table} == {width}
    assert len(c.PACT_MAGIC_SLOTS) == c.MAX_CHARACTER_LEVEL


@pytest.mark.parametrize(
    ("progression", "level", "expected"),
    [
        # Wizard 5: 4 first-, 3 second-, 2 third-level slots.
        (CasterProgression.FULL, 5, {1: 4, 2: 3, 3: 2}),
        (CasterProgression.FULL, 1, {1: 2}),
        (
            CasterProgression.FULL,
            20,
            {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
        ),
        # 2024 Paladins and Rangers cast from level 1 — 2014's did not.
        (CasterProgression.HALF, 1, {1: 2}),
        (CasterProgression.HALF, 5, {1: 4, 2: 2}),
        # An Eldritch Knight has no subclass, and so no slots, before level 3.
        (CasterProgression.THIRD, 2, {}),
        (CasterProgression.THIRD, 3, {1: 2}),
        (CasterProgression.THIRD, 13, {1: 4, 2: 3, 3: 2}),
        # Pact Magic: every slot is the same level, and it is the highest one.
        (CasterProgression.PACT, 1, {1: 1}),
        (CasterProgression.PACT, 5, {3: 2}),
        (CasterProgression.PACT, 11, {5: 3}),
    ],
)
def test_spell_slots(
    progression: CasterProgression, level: int, expected: dict[int, int]
) -> None:
    assert dict(c.spell_slots(progression, level)) == expected


def test_spell_slots_covers_every_progression_at_every_level() -> None:
    for progression in CasterProgression:
        for level in range(1, c.MAX_CHARACTER_LEVEL + 1):
            slots = c.spell_slots(progression, level)
            assert all(1 <= n <= c.MAX_SPELL_LEVEL for n in slots)
            assert all(count > 0 for count in slots.values())


def test_slot_tables_are_monotonic_in_level() -> None:
    # No class ever loses a slot on levelling up. This is what catches a row
    # transcribed one line out of place.
    for table in (c.FULL_CASTER_SLOTS, c.HALF_CASTER_SLOTS, c.THIRD_CASTER_SLOTS):
        for lower, higher in zip(table, table[1:]):  # noqa: B905
            assert all(a <= b for a, b in zip(lower, higher, strict=True))


def test_tables_cannot_be_mutated_by_a_caller() -> None:
    with pytest.raises(TypeError):
        c.PROFICIENCY_BONUS[1] = 99  # type: ignore[index]
