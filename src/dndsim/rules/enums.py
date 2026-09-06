"""The closed rules vocabulary — XPHB (2024).

Every package downstream of this one speaks these enumerations instead of
strings. A misspelled string is a silent no-op; a misspelled enum member is an
`AttributeError` at import. That is the whole reason this module exists, and it
is why it is written first (ROADMAP w0b): a rename here is free, and a rename
after ~630 content implementations exist is a mass edit.

**These are plain `Enum`s, never `StrEnum`s, and that is load-bearing.** Under
mypy's strict mode (`strict_equality`), comparing a plain `Enum` against a string
is a non-overlapping comparison and fails type checking:

    if ctx.damage_type == "fire":   # error: Non-overlapping equality check

With `StrEnum` that comparison is legal and *true*, so the day someone writes
`== "fires"` it silently becomes false and a feature quietly stops working. The
enum members still carry lowercase string values, which are what the event log
and the `SimResult` JSON serialise, and what `content/` parses back with
`DamageType("fire")` — an unknown value raises `ValueError` at the boundary
rather than travelling inward.

Sources: XPHB (2024) "Playing the Game" (abilities, damage types, sizes),
"Rules Glossary" (conditions), "Equipment" (weapon properties, mastery
properties), and "Spells" (schools of magic). Membership of every enumeration
below was checked against the XPHB entries in the 5etools dataset.
"""

from enum import Enum, unique

__all__ = [
    "Ability",
    "ActionCost",
    "CasterProgression",
    "Condition",
    "DamageType",
    "Mastery",
    "Outcome",
    "School",
    "Size",
    "WeaponProperty",
]


@unique
class Ability(Enum):
    """The six ability scores, in stat-block order."""

    STRENGTH = "str"
    DEXTERITY = "dex"
    CONSTITUTION = "con"
    INTELLIGENCE = "int"
    WISDOM = "wis"
    CHARISMA = "cha"


@unique
class DamageType(Enum):
    """The 13 damage types. Bludgeoning, piercing and slashing are the three
    physical types; `constants.PHYSICAL_DAMAGE_TYPES` is that grouping, which is
    what Rage and similar features resist."""

    ACID = "acid"
    BLUDGEONING = "bludgeoning"
    COLD = "cold"
    FIRE = "fire"
    FORCE = "force"
    LIGHTNING = "lightning"
    NECROTIC = "necrotic"
    PIERCING = "piercing"
    POISON = "poison"
    PSYCHIC = "psychic"
    RADIANT = "radiant"
    SLASHING = "slashing"
    THUNDER = "thunder"


@unique
class Condition(Enum):
    """The 15 conditions of the 2024 Rules Glossary.

    Two 2024 changes are encoded by what is *absent*: Surprised is no longer a
    condition (it is disadvantage on Initiative), and Bloodied and Concentration
    are *statuses*, not conditions. None of the three belongs here.

    This is the **name** of a condition — the vocabulary the APL, the log and
    `content/` use. The behaviour of a condition is a `HookSource` subclass in
    `conditions/` (w1h); the engine never branches on a member of this enum.
    """

    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    EXHAUSTION = "exhaustion"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


@unique
class Mastery(Enum):
    """The 8 weapon mastery properties (new in 2024)."""

    CLEAVE = "cleave"
    GRAZE = "graze"
    NICK = "nick"
    PUSH = "push"
    SAP = "sap"
    SLOW = "slow"
    TOPPLE = "topple"
    VEX = "vex"


@unique
class WeaponProperty(Enum):
    """The 9 weapon properties carried by the 38 XPHB weapons.

    2014's Special property is gone in 2024, and Range is a pair of numbers on
    the weapon rather than a property.
    """

    AMMUNITION = "ammunition"
    FINESSE = "finesse"
    HEAVY = "heavy"
    LIGHT = "light"
    LOADING = "loading"
    REACH = "reach"
    THROWN = "thrown"
    TWO_HANDED = "two_handed"
    VERSATILE = "versatile"


@unique
class Size(Enum):
    """Creature sizes. `constants.SIZE_ORDER` ranks them, because several rules
    ("no more than one size larger than you") are comparisons rather than
    equalities."""

    TINY = "tiny"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    HUGE = "huge"
    GARGANTUAN = "gargantuan"


@unique
class School(Enum):
    """The 8 schools of magic."""

    ABJURATION = "abjuration"
    CONJURATION = "conjuration"
    DIVINATION = "divination"
    ENCHANTMENT = "enchantment"
    EVOCATION = "evocation"
    ILLUSION = "illusion"
    NECROMANCY = "necromancy"
    TRANSMUTATION = "transmutation"


@unique
class Outcome(Enum):
    """How an **attack roll** resolved.

    Scoped to attack rolls on purpose. 2024 unifies attack rolls, ability checks
    and saving throws as "D20 Tests", but a natural 20 is only a critical hit for
    an attack roll, so a single outcome type across all three would be a member
    that means something different in each. Saving throws get their own result
    type with `engine/saves.py` (w1g).

    `is_hit` and `is_critical` exist so that no caller writes
    `outcome in (Outcome.HIT, Outcome.CRITICAL_HIT)` — the crit predicate lives
    in one place, which is the same reason `DamagePart` splits dice from flat.
    """

    CRITICAL_MISS = "critical_miss"
    MISS = "miss"
    HIT = "hit"
    CRITICAL_HIT = "critical_hit"

    @property
    def is_hit(self) -> bool:
        """True when damage is dealt — a hit or a critical hit."""
        return self in (Outcome.HIT, Outcome.CRITICAL_HIT)

    @property
    def is_critical(self) -> bool:
        """True on a critical hit only. This is the predicate that doubles
        `DamagePart.dice` and never `DamagePart.flat`."""
        return self is Outcome.CRITICAL_HIT


@unique
class CasterProgression(Enum):
    """Which spell-slot table a class advances on.

    `HALF` is the 2024 Paladin/Ranger progression, which grants slots from level
    1 — not 2014's, which started at level 2. `THIRD` is Eldritch Knight and
    Arcane Trickster, both inside the martial slice. The tables themselves are
    `constants.spell_slots()`.
    """

    FULL = "full"
    HALF = "half"
    THIRD = "third"
    PACT = "pact"


@unique
class ActionCost(Enum):
    """Which slot of the action economy something spends.

    Declared by a handler (`@hook("on_hit", costs=ActionCost.REACTION)`) and
    consumed by the creature's action economy (w1a); it lives here, at the
    bottom of the stack, because both `hooks/` and `creatures/` need the same
    three words and neither may import the other.

    There is no `FREE` member: a handler that costs nothing declares
    `costs=None`, and a second way of spelling the same thing is a second way
    for an author to get it wrong. Movement is absent for the opposite reason —
    it is measured in feet, not in slots, so it is not one of these.
    """

    ACTION = "action"
    BONUS_ACTION = "bonus_action"
    REACTION = "reaction"
