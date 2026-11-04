"""The imported weapons the default suite and `-m bench` stand on.

CI holds no fetched data, so these are hand-written; `tests/content/` asserts
each equals its imported entry (`docs/decisions.md` § w2f2).
"""

from typing import Final

from dndsim.core.dice import Dice
from dndsim.creatures.stats import Weapon
from dndsim.rules.enums import DamageType, Mastery, WeaponProperty

_HEAVY_TWO_HANDED: Final = frozenset({WeaponProperty.HEAVY, WeaponProperty.TWO_HANDED})

GREATSWORD: Final = Weapon(
    name="Greatsword",
    damage=Dice(2, 6),
    damage_type=DamageType.SLASHING,
    properties=_HEAVY_TWO_HANDED,
    mastery=Mastery.GRAZE,
)
MAUL: Final = Weapon(
    name="Maul",
    damage=Dice(2, 6),
    damage_type=DamageType.BLUDGEONING,
    properties=_HEAVY_TWO_HANDED,
    mastery=Mastery.TOPPLE,
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
)
