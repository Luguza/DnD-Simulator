"""The imported weapons and armour the default suite and `-m bench` stand on.

CI holds no fetched data, so these are hand-written; `tests/content/` asserts
each equals its imported entry (`docs/decisions.md` § w2f2).
"""

from typing import Final

from dndsim.core.dice import Dice
from dndsim.creatures.stats import Armour, Weapon
from dndsim.rules.enums import DamageType, Mastery, WeaponProperty

_HEAVY_TWO_HANDED: Final = frozenset({WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED})

GREATSWORD: Final = Weapon(
    name="Greatsword",
    damage=Dice(2, 6),
    damage_type=DamageType.SLASHING,
    properties=_HEAVY_TWO_HANDED,
    mastery=Mastery.GRAZE,
    martial=True,
)
MAUL: Final = Weapon(
    name="Maul",
    damage=Dice(2, 6),
    damage_type=DamageType.BLUDGEONING,
    properties=_HEAVY_TWO_HANDED,
    mastery=Mastery.TOPPLE,
    martial=True,
)
LONGBOW: Final = Weapon(
    name="Longbow",
    damage=Dice(1, 8),
    damage_type=DamageType.PIERCING,
    properties=frozenset(
        {WeaponProperty.AMMUNITION, WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED}
    ),
    mastery=Mastery.SLOW,
    range_ft=(150, 600),
    ranged=True,
    martial=True,
)
DAGGER: Final = Weapon(
    name="Dagger",
    damage=Dice(1, 4),
    damage_type=DamageType.PIERCING,
    properties=frozenset(
        {WeaponProperty.FINESSE, WeaponProperty.LIGHT, WeaponProperty.THROWN}
    ),
    mastery=Mastery.NICK,
    range_ft=(20, 60),
)
DART: Final = Weapon(
    name="Dart",
    damage=Dice(1, 4),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.FINESSE, WeaponProperty.THROWN}),
    mastery=Mastery.VEX,
    range_ft=(20, 60),
    ranged=True,
)
HANDAXE: Final = Weapon(
    name="Handaxe",
    damage=Dice(1, 6),
    damage_type=DamageType.SLASHING,
    properties=frozenset({WeaponProperty.LIGHT, WeaponProperty.THROWN}),
    mastery=Mastery.VEX,
    range_ft=(20, 60),
)
BLOWGUN: Final = Weapon(
    name="Blowgun",
    damage=1,
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.AMMUNITION, WeaponProperty.LOADING}),
    mastery=Mastery.VEX,
    range_ft=(25, 100),
    ranged=True,
    martial=True,
)
LONGSWORD: Final = Weapon(
    name="Longsword",
    damage=Dice(1, 8),
    damage_type=DamageType.SLASHING,
    properties=frozenset({WeaponProperty.VERSATILE}),
    mastery=Mastery.SAP,
    versatile_damage=Dice(1, 10),
    martial=True,
)
RAPIER: Final = Weapon(
    name="Rapier",
    damage=Dice(1, 8),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.FINESSE}),
    mastery=Mastery.VEX,
    martial=True,
)
CHAIN_MAIL: Final = Armour.heavy("Chain Mail", 16, strength_requirement=13)
PLATE_ARMOR: Final = Armour.heavy("Plate Armor", 18, strength_requirement=15)
BREASTPLATE: Final = Armour.medium("Breastplate", 14)
LEATHER_ARMOR: Final = Armour.light("Leather Armor", 11)
