"""What a creature is made of: its numbers, its armour and the one weapon the
spike swings.

`w0g2` cut the first version of this module and `w1a` completes it — nothing here
was rewritten. `w2a` imports the 38 XPHB weapons and every armour entry through
the content ACL into the two classes below. All three shapes are **Value
Objects**: frozen, slotted, and shared by every creature that has them rather
than copied per creature (DESIGN.md § Levers).

**AC is derived, and `Armour` is what it is derived from.** `StatBlock` carried
an `armour_class` literal through W0 because there was nothing to derive one
from; the field is gone rather than defaulted, because a default is how a number
nobody chose gets into a build and stays there. What replaced it is one small
object that answers a single question — *how much does wearing this raise a
creature's Armor Class* — and that is enough for all four of the shapes the
rules have: armour with no Dexterity at all (heavy), armour with a capped
Dexterity (medium), armour with an uncapped one (light and unarmoured), and the
Unarmored Defense variants, which are the unarmoured shape with a second ability
modifier added. A monster's printed AC is the heavy shape with a different name.

**The derived numbers are computed once, here.** `proficiency_bonus` walked the
XPHB table and `modifier()` divided on every read, three times per attack
resolution, which `w0i` measured at ~1.5% of the budget (docs/decisions.md §
w0i). A stat block is frozen, so both can be settled in `__post_init__` and read
as attributes for the life of the run.

Sources: XPHB (2024) "Equipment" (the Armor table, Armor Class); DESIGN.md
§ Structural prevention over testing, § Levers; ROADMAP w0g2, w1a, w2a.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from dndsim.core.dice import Dice
from dndsim.rules.constants import (
    BASE_ARMOUR_CLASS,
    MEDIUM_ARMOUR_DEX_CAP,
    ability_modifier,
    proficiency_bonus,
)
from dndsim.rules.enums import (
    Ability,
    DamageType,
    Mastery,
    Size,
    WeaponProperty,
)

__all__ = ["GREATSWORD", "UNARMOURED", "Armour", "StatBlock", "Weapon"]

_DEX: Final = Ability.DEXTERITY


@dataclass(frozen=True, slots=True)
class Weapon:
    """A weapon as the engine needs it: a name, a die, a type, and its reach.

    `damage` is a `Dice` pool with no flat part, which is not a simplification
    but the same structural rule the whole project turns on: a weapon's dice
    double on a critical hit and the wielder's ability modifier does not, so the
    modifier is added by the attack as a `flat` contribution and never lives
    here (DESIGN.md § Structural prevention, `hooks/contexts.py`).

    `mastery` is the 2024 weapon mastery property this weapon carries; the
    feature that implements it is a `HookSource` in `features/masteries/` (W4),
    and this field is only the name of which one.
    """

    name: str
    damage: Dice
    damage_type: DamageType
    properties: frozenset[WeaponProperty] = frozenset()
    mastery: Mastery | None = None
    reach_ft: int = 5

    def has(self, prop: WeaponProperty) -> bool:
        """Whether this weapon has a property. `w1f` reads Finesse here."""
        return prop in self.properties

    def __str__(self) -> str:
        return self.name


GREATSWORD: Final = Weapon(
    name="Greatsword",
    damage=Dice(2, 6),
    damage_type=DamageType.SLASHING,
    properties=frozenset({WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED}),
    mastery=Mastery.GRAZE,
    reach_ft=5,
)
"""The spike's one weapon: 2d6 slashing, Heavy, Two-Handed, Graze.

Hand-written rather than imported because `content/` does not exist yet (w2a),
and a greatsword because M1's milestone is a level-5 Fighter killing a dummy
with one. It is the *only* hardcoded weapon the project will ever have: when the
ACL lands, the 38 XPHB weapons arrive as instances of the class above and this
constant goes with the spike that needed it.
"""


@dataclass(frozen=True, slots=True)
class StatBlock:
    """The numbers a creature has before anything happens to it.

    Frozen because it is what a build *is*: a level-5 Fighter's ability scores do
    not change during a fight, and everything that does change — hit points,
    position, what is attached — lives on the mutable `Creature` instead. That
    split is what lets one stat block be shared by the four identical dummies in
    an encounter.

    **The two numbers nobody can guess have no defaults.** All six ability scores
    and the hit points must be given: a defaulted 10 is a number nobody chose
    that silently changes every save and every attack made with it, which is the
    same argument that refuses a *partial* ability map below. Speed and size do
    have defaults, because 30 feet and Medium are what the rules themselves
    default to, and level defaults to 1 because that is the bottom of the table
    rather than a guess. **AC is not among them at all**: it is derived from what
    the creature is wearing, and what it is wearing is equipment on the
    `Creature`, beside the weapon.

    `modifiers` and `proficiency_bonus` are settled in `__post_init__` rather
    than recomputed per read. Both are pure functions of fields that cannot
    change — the class is frozen — and the attack pipeline reads them three
    times per resolution (docs/decisions.md § w0i).
    """

    abilities: Mapping[Ability, int]
    max_hp: int
    level: int = 1
    speed_ft: int = 30
    size: Size = Size.MEDIUM
    modifiers: Mapping[Ability, int] = field(init=False, repr=False)
    proficiency_bonus: int = field(init=False)

    def __post_init__(self) -> None:
        missing = [
            ability.value for ability in Ability if ability not in self.abilities
        ]
        if missing:
            raise ValueError(
                f"a stat block has all six ability scores; {missing} are absent. "
                "A default would be a 10 nobody chose, silently changing every "
                "save and every attack made with it."
            )
        if self.max_hp < 1:
            raise ValueError(f"a creature has at least 1 hit point, got {self.max_hp}")
        # A level off the table raises here — at the earliest moment the
        # information exists — rather than a fight later, attributed to whoever
        # first read the number.
        object.__setattr__(self, "proficiency_bonus", proficiency_bonus(self.level))
        # The scores are copied behind a read-only view before anything is
        # derived from them: a caller holding the dict it passed in could
        # otherwise raise a Strength score after construction and leave the
        # modifier below saying what it used to be.
        object.__setattr__(self, "abilities", MappingProxyType(dict(self.abilities)))
        object.__setattr__(
            self,
            "modifiers",
            MappingProxyType(
                {
                    ability: ability_modifier(score)
                    for ability, score in self.abilities.items()
                }
            ),
        )

    def modifier(self, ability: Ability) -> int:
        """The modifier for one ability score."""
        return self.modifiers[ability]


@dataclass(frozen=True, slots=True)
class Armour:
    """What a creature is wearing, as an Armor Class.

    One question, asked of four different-looking rules: *how much does this
    raise the wearer's AC*. `base_ac` is the number the Armor table prints, and
    `abilities` is which ability modifiers are added to it and how far each one
    counts — which is the whole of the difference between light, medium and
    heavy armour, and what makes an Unarmored Defense variant a value rather
    than a special case.

    **Heavy armour has an empty `abilities`, not a cap of zero.** The rules do
    not cap Dexterity for heavy armour, they leave it out: a wearer with a
    Dexterity of 8 wears plate for 18, not 17. A cap of 0 would quietly answer
    the second.

    Build one through the named constructors below rather than by hand — they
    are what stops `w2a`'s importer from having to know that a medium-armour row
    caps Dexterity at +2, and what makes the four shapes enumerable by reading
    five lines.
    """

    name: str
    base_ac: int
    abilities: tuple[tuple[Ability, int | None], ...] = ()
    """Which ability modifiers this armour adds, each with its cap or `None` for
    no cap. A tuple rather than a mapping because it is compared, hashed and
    shared, and because two entries is as long as it has ever needed to be."""

    @classmethod
    def light(cls, name: str, base_ac: int) -> "Armour":
        """Light armour: the table's number plus the whole Dexterity modifier."""
        return cls(name=name, base_ac=base_ac, abilities=((_DEX, None),))

    @classmethod
    def medium(cls, name: str, base_ac: int) -> "Armour":
        """Medium armour: the table's number plus Dexterity, capped at +2."""
        return cls(
            name=name, base_ac=base_ac, abilities=((_DEX, MEDIUM_ARMOUR_DEX_CAP),)
        )

    @classmethod
    def heavy(cls, name: str, base_ac: int) -> "Armour":
        """Heavy armour: the printed number, and no Dexterity at all."""
        return cls(name=name, base_ac=base_ac)

    @classmethod
    def natural(cls, base_ac: int, name: str = "Natural Armor") -> "Armour":
        """A creature whose AC the book simply states — a monster's stat block
        (w7), and the target dummy's AC knob (w2d).

        The same shape as heavy armour because a printed AC already includes
        whatever Dexterity the creature has. This is the *only* legitimate way
        for an AC to arrive as a number somebody typed, and it is legitimate
        because for a monster the number is what the rules publish; a character
        build that reached for it would be the thing DESIGN.md § Reconnaissance
        refuses, and `builds/schema.py` (w2f) is where that is refused.
        """
        return cls(name=name, base_ac=base_ac)

    @classmethod
    def unarmoured(cls, *also: Ability, name: str = "Unarmoured") -> "Armour":
        """No armour: 10 plus the Dexterity modifier.

        Every Unarmored Defense variant is this with one more modifier added —
        the Barbarian's adds Constitution, the Monk's adds Wisdom — so the class
        feature (W4) equips one of these rather than reaching into an AC
        calculation that would then have to know about classes.

        What this shape does *not* carry is who may also hold a Shield: the
        Barbarian's works with one and the Monk's does not. That is a legality
        rule about a build rather than an arithmetic one about an AC, so it
        belongs to `builds/schema.py` (w2f) beside every other "this class may
        not take that"; nothing here can express it and nothing here should.
        """
        return cls(
            name=name,
            base_ac=BASE_ARMOUR_CLASS,
            abilities=((_DEX, None), *((ability, None) for ability in also)),
        )

    def armour_class(self, stats: StatBlock) -> int:
        """This armour's AC on a given creature, shield not included."""
        total = self.base_ac
        for ability, cap in self.abilities:
            modifier = stats.modifiers[ability]
            total += modifier if cap is None or modifier < cap else cap
        return total

    def __str__(self) -> str:
        return self.name


UNARMOURED: Final = Armour.unarmoured()
"""What a creature with nothing on is wearing: 10 + DEX.

A default in the same sense that a speed of 30 feet is one — it is what the
rules themselves say about a creature nobody has equipped, not a guess standing
in for a number somebody forgot.
"""
