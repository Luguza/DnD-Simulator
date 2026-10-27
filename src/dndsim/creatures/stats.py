"""What a creature is made of: its numbers, its armour and the one weapon the
spike swings.

All three are frozen, slotted value objects shared by every creature that has
them. AC is derived from an `Armour`, never typed into a `StatBlock`; a stat
block's modifiers and proficiency bonus are settled at construction.
`docs/decisions.md` § w0g2, § w1a.

XPHB (2024) "Equipment" (the Armor table, Armor Class).
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

    `damage` is dice only: the wielder's modifier is a `flat` contribution the
    attack adds, so it never doubles on a crit (DESIGN.md § Structural
    prevention). `mastery` names the weapon's mastery property and implements
    nothing.
    """

    name: str
    damage: Dice
    damage_type: DamageType
    properties: frozenset[WeaponProperty] = frozenset()
    mastery: Mastery | None = None
    reach_ft: int = 5

    def has(self, prop: WeaponProperty) -> bool:
        """Whether this weapon has a property."""
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
"""The spike's one weapon: 2d6 slashing, Heavy, Two-Handed, Graze. Hand-written
until weapons are imported (`docs/decisions.md` § w0g2)."""


@dataclass(frozen=True, slots=True)
class StatBlock:
    """The numbers a creature has before anything happens to it.

    What changes during a fight lives on `Creature`. All six ability scores and
    `max_hp` are required; `modifiers` and `proficiency_bonus` are derived in
    `__post_init__`. There is no AC field: AC comes from the `Armour` the
    creature wears (`docs/decisions.md` § w1a).

    Raises `ValueError` for a missing ability score or `max_hp` below 1.
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
        # Raises for a level off the table.
        object.__setattr__(self, "proficiency_bonus", proficiency_bonus(self.level))
        # Copy before deriving: the caller still holds the dict it passed in.
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

    `base_ac` is the number the Armor table prints, and `abilities` the ability
    modifiers added to it, each with its cap. Build one through the named
    constructors below.

    **Heavy armour has an empty `abilities`, not a cap of zero**: a cap of 0
    would subtract a negative Dexterity modifier (`docs/decisions.md` § w1a).
    """

    name: str
    base_ac: int
    abilities: tuple[tuple[Ability, int | None], ...] = ()
    """Which ability modifiers this armour adds, each with its cap or `None` for
    no cap."""

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
        """A printed AC, which already includes any Dexterity: the heavy shape.

        The only legitimate way for an AC to arrive as a number somebody typed
        — a monster's stat block, never a character build (`docs/decisions.md`
        § w1a).
        """
        return cls(name=name, base_ac=base_ac)

    @classmethod
    def unarmoured(cls, *also: Ability, name: str = "Unarmoured") -> "Armour":
        """No armour: 10 plus the Dexterity modifier, plus the uncapped modifier
        of each ability in `also` (an Unarmored Defense variant)."""
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
"""What a creature with nothing on is wearing: 10 + DEX."""
