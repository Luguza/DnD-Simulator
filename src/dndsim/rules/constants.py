"""The universal rules tables — XPHB (2024).

Rules data that is **not per class**: the proficiency bonus table, the ability
modifier formula, the four spell-slot progressions, and the fixed numbers the
rules state once. Per-class scaling is never here (`docs/decisions.md` § w0b).

Sources: XPHB (2024) "Playing the Game" (ability modifiers, the 5-foot grid),
"Character Creation" (the Proficiency Bonus table), "Equipment" (the Armor table
and the Armor Class rule), the class tables for the slot progressions, and the
Rules Glossary entries for Death Saving Throws.
Every table below was checked against the XPHB entries in the 5etools dataset.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from dndsim.rules.enums import CasterProgression, DamageType, Size

__all__ = [
    "BASE_ARMOUR_CLASS",
    "CELL_SIZE_FT",
    "CRITICAL_HIT_THRESHOLD",
    "DEATH_SAVE_DC",
    "DEATH_SAVE_FAILURES_TO_DIE",
    "DEATH_SAVE_SUCCESSES_TO_STABILIZE",
    "FULL_CASTER_SLOTS",
    "HALF_CASTER_SLOTS",
    "MAX_CHARACTER_LEVEL",
    "MAX_SPELL_LEVEL",
    "MEDIUM_ARMOUR_DEX_CAP",
    "NATURAL_ONE",
    "NATURAL_TWENTY",
    "PACT_MAGIC_SLOTS",
    "PHYSICAL_DAMAGE_TYPES",
    "PROFICIENCY_BONUS",
    "SHIELD_AC_BONUS",
    "SIZE_ORDER",
    "SIZE_WIDTH_CELLS",
    "SPELL_SAVE_DC_BASE",
    "THIRD_CASTER_SLOTS",
    "ability_modifier",
    "proficiency_bonus",
    "spell_slots",
]

MAX_CHARACTER_LEVEL: Final = 20
"""The rules cap; the tables here run to 20."""

MAX_SPELL_LEVEL: Final = 9

CELL_SIZE_FT: Final = 5
"""One grid square, in feet."""

BASE_ARMOUR_CLASS: Final = 10
"""The 10 an unarmoured Armor Class starts from (XPHB, "Armor Class")."""

MEDIUM_ARMOUR_DEX_CAP: Final = 2
"""How much of a Dexterity modifier medium armour lets through (XPHB Armor
table)."""

SHIELD_AC_BONUS: Final = 2
"""What a Shield adds (XPHB Armor table; `docs/decisions.md` § w1a)."""

CRITICAL_HIT_THRESHOLD: Final = 20
"""The natural roll a Critical Hit starts at (XPHB, "Attack Rolls"): the default
of a creature's derived `crit_threshold` (`docs/decisions.md` § w1a)."""

NATURAL_TWENTY: Final = 20
NATURAL_ONE: Final = 1
"""The two rolls an attack resolves on their own, whatever the modifiers or the
target's AC (XPHB, "D20 Tests" -> Attack Rolls, "Rolling 20 or 1").

Distinct from `CRITICAL_HIT_THRESHOLD`, which a feature may lower: a lowered
threshold never makes a roll hit (`docs/decisions.md` § w1f)."""

SPELL_SAVE_DC_BASE: Final = 8
"""The 8 of "8 + your spellcasting ability modifier + your Proficiency Bonus"
(XPHB, "Spells" -> Spell Save DC; `docs/decisions.md` § w1g)."""

DEATH_SAVE_DC: Final = 10
DEATH_SAVE_SUCCESSES_TO_STABILIZE: Final = 3
DEATH_SAVE_FAILURES_TO_DIE: Final = 3

PHYSICAL_DAMAGE_TYPES: Final[frozenset[DamageType]] = frozenset(
    {DamageType.BLUDGEONING, DamageType.PIERCING, DamageType.SLASHING}
)
"""Bludgeoning, Piercing, and Slashing."""

SIZE_ORDER: Final[Mapping[Size, int]] = MappingProxyType(
    {size: rank for rank, size in enumerate(Size)}
)
"""Sizes ranked smallest to largest."""

SIZE_WIDTH_CELLS: Final[Mapping[Size, int]] = MappingProxyType(
    {
        # The XPHB "Creature Size" table in squares: Tiny's 2.5 feet is one
        # square, and Gargantuan's "20 feet or more" is floored at 20.
        Size.TINY: 1,
        Size.SMALL: 1,
        Size.MEDIUM: 1,
        Size.LARGE: 2,
        Size.HUGE: 3,
        Size.GARGANTUAN: 4,
    }
)
"""How many squares on a side a creature of each size occupies
(`docs/decisions.md` § w1d)."""

PROFICIENCY_BONUS: Final[Mapping[int, int]] = MappingProxyType(
    {
        # The XPHB Proficiency Bonus table, one row of the book per line.
        1: 2,
        2: 2,
        3: 2,
        4: 2,
        5: 3,
        6: 3,
        7: 3,
        8: 3,
        9: 4,
        10: 4,
        11: 4,
        12: 4,
        13: 5,
        14: 5,
        15: 5,
        16: 5,
        17: 6,
        18: 6,
        19: 6,
        20: 6,
    }
)
"""Proficiency bonus by character level. Read it through `proficiency_bonus()`,
which raises on a level outside the table."""

FULL_CASTER_SLOTS: Final[tuple[tuple[int, ...], ...]] = (
    # 1st  2nd  3rd  4th  5th  6th  7th  8th  9th
    (2, 0, 0, 0, 0, 0, 0, 0, 0),  # level 1
    (3, 0, 0, 0, 0, 0, 0, 0, 0),  # level 2
    (4, 2, 0, 0, 0, 0, 0, 0, 0),  # level 3
    (4, 3, 0, 0, 0, 0, 0, 0, 0),  # level 4
    (4, 3, 2, 0, 0, 0, 0, 0, 0),  # level 5
    (4, 3, 3, 0, 0, 0, 0, 0, 0),  # level 6
    (4, 3, 3, 1, 0, 0, 0, 0, 0),  # level 7
    (4, 3, 3, 2, 0, 0, 0, 0, 0),  # level 8
    (4, 3, 3, 3, 1, 0, 0, 0, 0),  # level 9
    (4, 3, 3, 3, 2, 0, 0, 0, 0),  # level 10
    (4, 3, 3, 3, 2, 1, 0, 0, 0),  # level 11
    (4, 3, 3, 3, 2, 1, 0, 0, 0),  # level 12
    (4, 3, 3, 3, 2, 1, 1, 0, 0),  # level 13
    (4, 3, 3, 3, 2, 1, 1, 0, 0),  # level 14
    (4, 3, 3, 3, 2, 1, 1, 1, 0),  # level 15
    (4, 3, 3, 3, 2, 1, 1, 1, 0),  # level 16
    (4, 3, 3, 3, 2, 1, 1, 1, 1),  # level 17
    (4, 3, 3, 3, 3, 1, 1, 1, 1),  # level 18
    (4, 3, 3, 3, 3, 2, 1, 1, 1),  # level 19
    (4, 3, 3, 3, 3, 2, 2, 1, 1),  # level 20
)
"""Bard, Cleric, Druid, Sorcerer, Wizard. Row index is `level - 1`; column index
is `slot_level - 1`."""

HALF_CASTER_SLOTS: Final[tuple[tuple[int, ...], ...]] = (
    # 1st  2nd  3rd  4th  5th
    (2, 0, 0, 0, 0),  # level 1
    (2, 0, 0, 0, 0),  # level 2
    (3, 0, 0, 0, 0),  # level 3
    (3, 0, 0, 0, 0),  # level 4
    (4, 2, 0, 0, 0),  # level 5
    (4, 2, 0, 0, 0),  # level 6
    (4, 3, 0, 0, 0),  # level 7
    (4, 3, 0, 0, 0),  # level 8
    (4, 3, 2, 0, 0),  # level 9
    (4, 3, 2, 0, 0),  # level 10
    (4, 3, 3, 0, 0),  # level 11
    (4, 3, 3, 0, 0),  # level 12
    (4, 3, 3, 1, 0),  # level 13
    (4, 3, 3, 1, 0),  # level 14
    (4, 3, 3, 2, 0),  # level 15
    (4, 3, 3, 2, 0),  # level 16
    (4, 3, 3, 3, 1),  # level 17
    (4, 3, 3, 3, 1),  # level 18
    (4, 3, 3, 3, 2),  # level 19
    (4, 3, 3, 3, 2),  # level 20
)
"""Paladin and Ranger, casters from level 1 in 2024."""

THIRD_CASTER_SLOTS: Final[tuple[tuple[int, ...], ...]] = (
    # 1st  2nd  3rd  4th
    (0, 0, 0, 0),  # level 1
    (0, 0, 0, 0),  # level 2
    (2, 0, 0, 0),  # level 3
    (3, 0, 0, 0),  # level 4
    (3, 0, 0, 0),  # level 5
    (3, 0, 0, 0),  # level 6
    (4, 2, 0, 0),  # level 7
    (4, 2, 0, 0),  # level 8
    (4, 2, 0, 0),  # level 9
    (4, 3, 0, 0),  # level 10
    (4, 3, 0, 0),  # level 11
    (4, 3, 0, 0),  # level 12
    (4, 3, 2, 0),  # level 13
    (4, 3, 2, 0),  # level 14
    (4, 3, 2, 0),  # level 15
    (4, 3, 3, 0),  # level 16
    (4, 3, 3, 0),  # level 17
    (4, 3, 3, 0),  # level 18
    (4, 3, 3, 1),  # level 19
    (4, 3, 3, 1),  # level 20
)
"""Eldritch Knight and Arcane Trickster, whose subclasses begin at level 3."""

PACT_MAGIC_SLOTS: Final[tuple[tuple[int, int], ...]] = (
    # (slots, slot level)
    (1, 1),  # level 1
    (2, 1),  # level 2
    (2, 2),  # level 3
    (2, 2),  # level 4
    (2, 3),  # level 5
    (2, 3),  # level 6
    (2, 4),  # level 7
    (2, 4),  # level 8
    (2, 5),  # level 9
    (2, 5),  # level 10
    (3, 5),  # level 11
    (3, 5),  # level 12
    (3, 5),  # level 13
    (3, 5),  # level 14
    (3, 5),  # level 15
    (3, 5),  # level 16
    (4, 5),  # level 17
    (4, 5),  # level 18
    (4, 5),  # level 19
    (4, 5),  # level 20
)
"""Warlock Pact Magic as `(slots, slot_level)` per level: every slot is of the
same level, and they return on a Short Rest."""

_SLOT_TABLES: Final[Mapping[CasterProgression, tuple[tuple[int, ...], ...]]] = (
    MappingProxyType(
        {
            CasterProgression.FULL: FULL_CASTER_SLOTS,
            CasterProgression.HALF: HALF_CASTER_SLOTS,
            CasterProgression.THIRD: THIRD_CASTER_SLOTS,
        }
    )
)

_NO_SLOTS: Final[Mapping[int, int]] = MappingProxyType({})


def _check_level(level: int) -> None:
    if not 1 <= level <= MAX_CHARACTER_LEVEL:
        raise ValueError(
            f"level {level} is outside 1-{MAX_CHARACTER_LEVEL}; "
            "the rules tables do not extend past 20"
        )


def proficiency_bonus(level: int) -> int:
    """The proficiency bonus at a character level; `ValueError` outside 1-20."""
    _check_level(level)
    return PROFICIENCY_BONUS[level]


def ability_modifier(score: int) -> int:
    """The modifier for an ability score: `(score - 10) / 2`, rounded down."""
    return (score - 10) // 2


def spell_slots(progression: CasterProgression, level: int) -> Mapping[int, int]:
    """Slot level -> number of slots, for one progression at one class level.

    Empty levels are omitted, so a level-1 Eldritch Knight returns `{}` and a
    level-5 Warlock returns `{3: 2}`. Pact Magic comes back in the same shape,
    so callers never branch on the progression. Raises `ValueError` outside
    levels 1-20.
    """
    _check_level(level)
    if progression is CasterProgression.PACT:
        count, slot_level = PACT_MAGIC_SLOTS[level - 1]
        return MappingProxyType({slot_level: count}) if count else _NO_SLOTS
    row = _SLOT_TABLES[progression][level - 1]
    return MappingProxyType(
        {slot_level: count for slot_level, count in enumerate(row, start=1) if count}
    )
