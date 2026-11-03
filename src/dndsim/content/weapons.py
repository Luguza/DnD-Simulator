"""The XPHB Weapon and Armor tables, and the eight weapon masteries.

XPHB (2024) "Equipment" (Weapons, Armor). `docs/decisions.md` § w2a.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.loader import SOURCE, ContentError, Dataset
from dndsim.core.dice import Dice
from dndsim.creatures.stats import Armour, Weapon
from dndsim.rules.constants import CELL_SIZE_FT, SHIELD_AC_BONUS
from dndsim.rules.enums import DamageType, Mastery, WeaponProperty

__all__ = ["NOT_IMPORTED", "armour", "masteries", "weapons"]

ITEMS: Final = "items-base.json"

_DAMAGE_TYPES: Final = MappingProxyType(
    {
        "A": DamageType.ACID,
        "B": DamageType.BLUDGEONING,
        "C": DamageType.COLD,
        "F": DamageType.FIRE,
        "O": DamageType.FORCE,
        "L": DamageType.LIGHTNING,
        "N": DamageType.NECROTIC,
        "P": DamageType.PIERCING,
        "I": DamageType.POISON,
        "Y": DamageType.PSYCHIC,
        "R": DamageType.RADIANT,
        "S": DamageType.SLASHING,
        "T": DamageType.THUNDER,
    }
)

_PROPERTIES: Final = MappingProxyType(
    {
        "2H": WeaponProperty.TWO_HANDED,
        "A": WeaponProperty.AMMUNITION,
        "F": WeaponProperty.FINESSE,
        "H": WeaponProperty.HEAVY,
        "L": WeaponProperty.LIGHT,
        "LD": WeaponProperty.LOADING,
        "R": WeaponProperty.REACH,
        "T": WeaponProperty.THROWN,
        "V": WeaponProperty.VERSATILE,
    }
)

_WEAPON_TYPES: Final = frozenset({"M", "R"})
_ARMOUR_TYPES: Final = frozenset({"LA", "MA", "HA"})
_SHIELD_TYPE: Final = "S"

NOT_IMPORTED: Final = MappingProxyType(
    {
        "Blowgun": (
            "its damage is a flat 1 with no die, and `Weapon.damage` is dice "
            "only; a d1 would double to 2 on a Critical Hit (debt #59)"
        ),
    }
)
"""Weapons on the table the domain cannot yet represent, with the reason."""


def _code(entry: Any) -> str:
    """`'V|XPHB'` or `{'uid': 'V|XPHB', ...}` -> `'V'`."""
    uid = entry["uid"] if isinstance(entry, dict) else entry
    return str(uid).split("|", 1)[0]


def _xphb(data: Dataset, kinds: frozenset[str]) -> list[Any]:
    return [
        item
        for item in data.read(ITEMS)["baseitem"]
        if item.get("source") == SOURCE and _code(item.get("type", "")) in kinds
    ]


def _weapon(item: Any) -> Weapon:
    name = item["name"]
    try:
        properties = frozenset(_PROPERTIES[_code(p)] for p in item.get("property", []))
        damage_type = _DAMAGE_TYPES[item["dmgType"]]
        mastery = [Mastery(_code(m).lower()) for m in item.get("mastery", [])]
        damage = Dice.parse(item["dmg1"])
        versatile = Dice.parse(item["dmg2"]) if "dmg2" in item else None
        if "range" in item:
            normal, long = (int(n) for n in item["range"].split("/"))
    except (KeyError, ValueError) as error:
        raise ContentError(f"weapon {name!r} is not shaped as read: {error}") from error
    if len(mastery) > 1:
        raise ContentError(f"weapon {name!r} names more than one mastery")
    reach = CELL_SIZE_FT * (2 if WeaponProperty.REACH in properties else 1)
    return Weapon(
        name=name,
        damage=damage,
        damage_type=damage_type,
        properties=properties,
        mastery=mastery[0] if mastery else None,
        reach_ft=reach,
        range_ft=(normal, long) if "range" in item else None,
        versatile_damage=versatile,
    )


def weapons(data: Dataset) -> Mapping[str, Weapon]:
    """Every XPHB simple and martial weapon by name, less `NOT_IMPORTED`.

    Raises `ContentError` for a weapon shaped otherwise than read, and for an
    entry in `NOT_IMPORTED` the data no longer holds.
    """
    items = {item["name"]: item for item in _xphb(data, _WEAPON_TYPES)}
    stale = NOT_IMPORTED.keys() - items.keys()
    if stale:
        raise ContentError(f"NOT_IMPORTED names weapons the data lacks: {stale}")
    return MappingProxyType(
        {
            name: _weapon(item)
            for name, item in sorted(items.items())
            if name not in NOT_IMPORTED
        }
    )


def armour(data: Dataset) -> Mapping[str, Armour]:
    """Every XPHB light, medium and heavy armour by name.

    The Shield is checked rather than returned: holding one is
    `Creature.shield`, worth `SHIELD_AC_BONUS`, and a dataset whose Shield
    prints another number raises `ContentError`.
    """
    for shield in _xphb(data, frozenset({_SHIELD_TYPE})):
        if shield.get("ac") != SHIELD_AC_BONUS:
            raise ContentError(
                f"{shield['name']} grants {shield.get('ac')} AC; the rules "
                f"constant says {SHIELD_AC_BONUS}"
            )
    table: dict[str, Armour] = {}
    for item in _xphb(data, _ARMOUR_TYPES):
        name, kind, strength = item["name"], _code(item["type"]), item.get("strength")
        if kind != "HA" and strength is not None:
            raise ContentError(f"{name!r} prints a Strength requirement")
        try:
            base = int(item["ac"])
            needed = None if strength is None else int(strength)
        except (KeyError, ValueError) as error:
            raise ContentError(f"armour {name!r} has no readable AC") from error
        if kind == "LA":
            table[name] = Armour.light(name, base)
        elif kind == "MA":
            table[name] = Armour.medium(name, base)
        else:
            table[name] = Armour.heavy(name, base, strength_requirement=needed)
    return MappingProxyType(dict(sorted(table.items())))


def masteries(data: Dataset) -> frozenset[Mastery]:
    """The XPHB weapon masteries, checked against `Mastery`.

    Only the names import: what each does is hand-coded in W3. Raises
    `ContentError` unless the data and the enum hold the same eight.
    """
    names = {
        entry["name"].lower()
        for entry in data.read(ITEMS)["itemMastery"]
        if entry.get("source") == SOURCE
    }
    known = {mastery.value for mastery in Mastery}
    if names != known:
        raise ContentError(
            f"the data's masteries differ from Mastery: {sorted(names ^ known)}"
        )
    return frozenset(Mastery)
