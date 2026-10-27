"""The closed rules vocabulary — XPHB (2024).

Plain `Enum`s, never `StrEnum`s, so comparing a member against a string is a
mypy error; members carry lowercase string values for serialisation and parsing
(`docs/decisions.md` § w0b).

Sources: XPHB (2024) "Playing the Game" (abilities, damage types, sizes),
"Rules Glossary" (conditions), "Equipment" (weapon properties, mastery
properties), and "Spells" (schools of magic).
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
    "RecoveryTrigger",
    "SaveOutcome",
    "SaveRepeat",
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
    """The 13 damage types. `constants.PHYSICAL_DAMAGE_TYPES` groups the three
    physical ones."""

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

    The **name** of a condition only; its behaviour is a `HookSource` subclass
    in `conditions/` (`docs/decisions.md` § w0b).
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
    """The 9 weapon properties carried by the 38 XPHB weapons. Range is a pair
    of numbers on the weapon, not a member."""

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
    """Creature sizes. `constants.SIZE_ORDER` ranks them."""

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

    Attack rolls only; a saving throw resolves into `SaveOutcome`
    (`docs/decisions.md` § w0b). Ask `is_hit` and `is_critical` rather than
    testing membership.
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
        """True on a critical hit only."""
        return self is Outcome.CRITICAL_HIT


@unique
class SaveOutcome(Enum):
    """How a **saving throw** resolved. No critical member: XPHB "D20 Tests"
    -> Saving Throws (`docs/decisions.md` § w1g).
    """

    FAILURE = "failure"
    SUCCESS = "success"


@unique
class SaveRepeat(Enum):
    """When a save-ends effect offers its save again (debt #35)."""

    TURN_END = "turn_end"
    TURN_START = "turn_start"


@unique
class CasterProgression(Enum):
    """Which spell-slot table a class advances on.

    `HALF` is the 2024 Paladin/Ranger progression, with slots from level 1;
    `THIRD` is Eldritch Knight and Arcane Trickster. The tables are
    `constants.spell_slots()`.
    """

    FULL = "full"
    HALF = "half"
    THIRD = "third"
    PACT = "pact"


@unique
class ActionCost(Enum):
    """Which slot of the action economy something spends.

    Declared by a handler (`@hook("on_hit", costs=ActionCost.REACTION)`). A
    handler that costs nothing declares `costs=None` (`docs/decisions.md`
    § w0g).
    """

    ACTION = "action"
    BONUS_ACTION = "bonus_action"
    REACTION = "reaction"


@unique
class RecoveryTrigger(Enum):
    """What hands a spent resource back.

    The trigger half of a `Recovery`. `RECHARGE_5_6` is fired by whoever rolled
    the d6 (`docs/decisions.md` § w1b).
    """

    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"
    RECHARGE_5_6 = "recharge_5_6"
