"""The universal rules tables — XPHB (2024).

What belongs here is rules data that is **not per class**: the proficiency bonus
table, the ability modifier formula, the four spell-slot progressions, and the
handful of fixed numbers the rules state once and every subsystem then quotes.

What does *not* belong here is per-class scaling — Rage Damage, Sneak Attack
dice, Second Wind uses. Those are columns of a class table, they arrive through
`content/` (w2a) and features read them via `table_value()`. Standing rule 3
("never hardcode a scaling number") is about exactly that data, not about this
module.

**The slot tables are the one overlap, and it is deliberate.** 5etools ships the
same numbers per class as `classTableGroups.rowsSpellProgression`, so w2a will
import them. It should then *check the import against these tables* rather than
prefer one source or the other: the tables below are the rules, the import is a
third-party artefact that changes without notice, and a mismatch between them is
the earliest possible warning that the dataset moved under us. Deriving them at
runtime instead would make every W1 test depend on a fetch of gitignored data.

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
    "PACT_MAGIC_SLOTS",
    "PHYSICAL_DAMAGE_TYPES",
    "PROFICIENCY_BONUS",
    "SHIELD_AC_BONUS",
    "SIZE_ORDER",
    "THIRD_CASTER_SLOTS",
    "ability_modifier",
    "proficiency_bonus",
    "spell_slots",
]

MAX_CHARACTER_LEVEL: Final = 20
"""The rules cap. v1 simulates levels 1-12; that narrower cap is a build-schema
concern (w2f), not a fact about the rules, so the tables here run to 20."""

MAX_SPELL_LEVEL: Final = 9

CELL_SIZE_FT: Final = 5
"""One grid square. Distances are measured in feet everywhere; this is the only
place the grid's resolution is written down."""

BASE_ARMOUR_CLASS: Final = 10
"""The 10 every Armor Class starts from — a creature wearing no armour has an AC
of 10 plus its Dexterity modifier (XPHB, "Armor Class"). Every Unarmored Defense
variant is that same 10 with one more modifier added, which is why
`creatures/stats.py` builds all of them from this one number."""

MEDIUM_ARMOUR_DEX_CAP: Final = 2
"""How much of a Dexterity modifier medium armour lets through: every medium row
of the XPHB Armor table caps the Dexterity it adds at +2. Light armour caps
nothing and heavy armour adds no Dexterity at all, so this is the only cap the
rules state and therefore the only one written down."""

SHIELD_AC_BONUS: Final = 2
"""What a Shield adds: +2, in every shield row of the XPHB Armor table. It is
here rather than on an imported item because v1 has no magic items (DESIGN.md
§ Settled decisions), so the number the rules state once is the number every
shield gives; `w2a`'s import checks its entry against this the way the slot
tables check theirs."""

CRITICAL_HIT_THRESHOLD: Final = 20
"""The natural roll a Critical Hit starts at (XPHB, "Attack Rolls": a natural 20
hits and is a Critical Hit). It is where a *character's* threshold starts rather
than a fact about one attack — Improved Critical lowers the character's to 19 —
which is why it is a derived stat on the creature and never something a handler
edits per attack (`hooks/contexts.py`)."""

DEATH_SAVE_DC: Final = 10
DEATH_SAVE_SUCCESSES_TO_STABILIZE: Final = 3
DEATH_SAVE_FAILURES_TO_DIE: Final = 3

PHYSICAL_DAMAGE_TYPES: Final[frozenset[DamageType]] = frozenset(
    {DamageType.BLUDGEONING, DamageType.PIERCING, DamageType.SLASHING}
)
"""The grouping Rage and comparable features resist: "Bludgeoning, Piercing, and
Slashing damage"."""

SIZE_ORDER: Final[Mapping[Size, int]] = MappingProxyType(
    {size: rank for rank, size in enumerate(Size)}
)
"""Sizes ranked smallest to largest, so that "one size larger than you" is a
comparison of two ints rather than a hand-written ordering per rule."""

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
which fails loudly on a level outside the table."""

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
"""Paladin and Ranger. In 2024 both are casters from level 1, which is why the
first row is not empty."""

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
same level, and they return on a Short Rest rather than a Long Rest."""

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
    """The proficiency bonus at a character level."""
    _check_level(level)
    return PROFICIENCY_BONUS[level]


def ability_modifier(score: int) -> int:
    """The modifier for an ability score: `(score - 10) / 2`, rounded down."""
    return (score - 10) // 2


def spell_slots(progression: CasterProgression, level: int) -> Mapping[int, int]:
    """Slot level -> number of slots, for one progression at one class level.

    Empty levels are omitted, so a level-1 Eldritch Knight returns `{}` and a
    level-5 Warlock returns `{3: 2}`. Pact Magic comes back in the same shape as
    the other three even though its table is stored differently, so callers —
    `SpellSlots` above all — never branch on the progression.
    """
    _check_level(level)
    if progression is CasterProgression.PACT:
        count, slot_level = PACT_MAGIC_SLOTS[level - 1]
        return MappingProxyType({slot_level: count}) if count else _NO_SLOTS
    row = _SLOT_TABLES[progression][level - 1]
    return MappingProxyType(
        {slot_level: count for slot_level, count in enumerate(row, start=1) if count}
    )
