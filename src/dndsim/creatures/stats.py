"""What a creature is made of, and the one weapon the spike swings.

A first cut (ROADMAP w0g2). `w1a` completes this module with derived stats, the
`Armour` value object and the AC derivation that follows from it; `w2a` imports
the 38 XPHB weapons through the content ACL into the `Weapon` class below. The
two shapes here are the ones `w0h`'s walking skeleton and `w0i`'s benchmark
cannot run without, and both are **Value Objects**: frozen, slotted, and shared
by every creature that has them rather than copied per creature (DESIGN.md §
Levers).

**`armour_class` is a literal here, and that is temporary.** DESIGN.md is
emphatic that AC is *derived* from equipment — armour plus the DEX cap plus a
shield plus the Unarmored Defense variants — and never a number typed into a
build (ROADMAP w1a). Until `Armour` exists there is nothing to derive it from,
so the spike carries the number and this paragraph is the reason a reviewer
finds it here rather than a silent literal in a YAML file.

Sources: DESIGN.md § Structural prevention over testing, § Levers; ROADMAP w0g2,
w1a, w2a.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from dndsim.core.dice import Dice
from dndsim.rules.constants import ability_modifier, proficiency_bonus
from dndsim.rules.enums import (
    Ability,
    DamageType,
    Mastery,
    Size,
    WeaponProperty,
)

__all__ = ["GREATSWORD", "StatBlock", "Weapon"]


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

    **The three numbers nobody can guess have no defaults.** All six ability
    scores, the hit points and the AC must be given: a defaulted 10 is a number
    nobody chose that silently changes every save, every attack and every hit
    made against it, which is the same argument that refuses a *partial* ability
    map below. Speed and size do have defaults, because 30 feet and Medium are
    what the rules themselves default to, and level defaults to 1 because that
    is the bottom of the table rather than a guess.
    """

    abilities: Mapping[Ability, int]
    max_hp: int
    armour_class: int
    level: int = 1
    speed_ft: int = 30
    size: Size = Size.MEDIUM

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
        # `proficiency_bonus` would raise on a level off the table, but only
        # once somebody read it — a fight in, and attributed to the reader.
        proficiency_bonus(self.level)

    @property
    def proficiency_bonus(self) -> int:
        """The XPHB proficiency bonus for this creature's level."""
        return proficiency_bonus(self.level)

    def modifier(self, ability: Ability) -> int:
        """The modifier for one ability score."""
        return ability_modifier(self.abilities[ability])
