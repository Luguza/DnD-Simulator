"""The XPHB feats by name, category and prerequisites. Nothing here implements
a feat (W4).

XPHB (2024) "Feats". `docs/decisions.md` § w2a2.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.classes import ArmourTraining, WeaponTraining
from dndsim.content.loader import SOURCE, ContentError, Dataset
from dndsim.content.weapons import CATEGORIES
from dndsim.rules.enums import Ability

__all__ = ["Feat", "FeatCategory", "Prerequisite", "feats"]

FEATS: Final = "feats.json"


class FeatCategory(Enum):
    """XPHB "Feat Descriptions": the four categories."""

    ORIGIN = "origin"
    GENERAL = "general"
    FIGHTING_STYLE = "fighting_style"
    EPIC_BOON = "epic_boon"


# A Fighting Style feat only one class's Fighting Style feature may take.
_CATEGORIES: Final = MappingProxyType(
    {
        "O": (FeatCategory.ORIGIN, None),
        "G": (FeatCategory.GENERAL, None),
        "FS": (FeatCategory.FIGHTING_STYLE, None),
        "FS:P": (FeatCategory.FIGHTING_STYLE, "Paladin"),
        "FS:R": (FeatCategory.FIGHTING_STYLE, "Ranger"),
        "EB": (FeatCategory.EPIC_BOON, None),
    }
)


@dataclass(frozen=True, slots=True)
class Prerequisite:
    """One way of meeting a feat's prerequisite; every field set must hold.

    `abilities` pairs each ability with its minimum score; `spellcasting` is
    "Spellcasting or Pact Magic Feature"; `armour_training` is the training
    the feat requires.
    """

    level: int | None = None
    abilities: tuple[tuple[Ability, int], ...] = ()
    spellcasting: bool = False
    armour_training: ArmourTraining | None = None


@dataclass(frozen=True, slots=True)
class Feat:
    """A feat's name and who may take it.

    `prerequisites` is met by meeting any one of them, and an empty tuple is
    no prerequisite. `class_name` is the class whose Fighting Style feature
    alone may take it, `None` for any. A Fighting Style feat's "Fighting Style
    Feature" prerequisite is its category, not a `Prerequisite`.
    """

    name: str
    category: FeatCategory
    prerequisites: tuple[Prerequisite, ...]
    repeatable: bool
    class_name: str | None = None
    options: tuple[str, ...] = ()
    """The versions printed under one name, as Magic Initiate's Cleric, Druid
    and Wizard."""
    armour_training: frozenset[ArmourTraining] = frozenset()
    """The armour training the feat grants outright."""
    weapon_training: tuple[WeaponTraining, ...] = ()
    """The weapon categories the feat grants proficiency with outright."""


# Tavern Brawler's improvised weapons: no imported `Weapon` is one.
_UNREAD_WEAPON_GRANTS: Final = frozenset({"improvised"})


def _granted(raw: Any, where: str) -> list[str]:
    """The keys of a fixed grant; a grant the player chooses is the feat's
    own option, which this import does not read (`docs/decisions.md`
    § w2f)."""
    keys: list[str] = []
    for entry in raw or ():
        if not isinstance(entry, dict):
            raise ContentError(f"{where}: cannot read the grant {entry!r}")
        if "choose" in entry or "any" in entry:
            continue
        if not all(value is True for value in entry.values()):
            raise ContentError(f"{where}: cannot read the grant {entry!r}")
        keys.extend(entry)
    return keys


def _alternatives(raw: Any, category: FeatCategory, where: str) -> list[Prerequisite]:
    """One `Prerequisite` per way the 5etools entry `raw` can be met."""
    keys = set(raw)
    if keys == {"feature"}:
        if category is not FeatCategory.FIGHTING_STYLE or raw["feature"] != [
            "Fighting Style"
        ]:
            raise ContentError(f"{where}: requires the feature {raw['feature']!r}")
        return []
    if keys == {"otherSummary"}:
        if category is not FeatCategory.FIGHTING_STYLE:
            raise ContentError(f"{where}: a prerequisite read only in prose")
        return []
    unknown = keys - {"level", "ability", "spellcasting2020", "proficiency"}
    if unknown:
        raise ContentError(f"{where}: cannot read the prerequisite {sorted(unknown)}")
    level = raw.get("level")
    if level is not None and not isinstance(level, int):
        raise ContentError(f"{where}: cannot read the level {level!r}")
    armour = None
    if "proficiency" in raw:
        (proficiency,) = raw["proficiency"]
        if set(proficiency) != {"armor"}:
            raise ContentError(f"{where}: cannot read {proficiency!r}")
        armour = ArmourTraining(proficiency["armor"])
    spellcasting = bool(raw.get("spellcasting2020", False))
    # `ability` is a list of alternatives, each a mapping that must all hold.
    options = raw.get("ability", [{}])
    return [
        Prerequisite(
            level=level,
            abilities=tuple(
                (Ability(code), int(score)) for code, score in option.items()
            ),
            spellcasting=spellcasting,
            armour_training=armour,
        )
        for option in options
    ]


def _option(version: Any, name: str, where: str) -> str:
    head, sep, option = str(version["name"]).partition("; ")
    if head != name or not sep:
        raise ContentError(f"{where}: cannot read the version {version['name']!r}")
    return option


def _feat(entry: Any) -> Feat:
    name = entry["name"]
    where = f"feat {name!r}"
    try:
        category, class_name = _CATEGORIES[entry["category"]]
        prerequisites = tuple(
            p
            for raw in entry.get("prerequisite") or ()
            for p in _alternatives(raw, category, where)
        )
        options = tuple(_option(v, name, where) for v in entry.get("_versions", ()))
        armour = frozenset(
            ArmourTraining(key)
            for key in _granted(entry.get("armorProficiencies"), where)
        )
        weapon_training = tuple(
            WeaponTraining(CATEGORIES[key])
            for key in _granted(entry.get("weaponProficiencies"), where)
            if key not in _UNREAD_WEAPON_GRANTS
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ContentError(f"{where} is not shaped as read: {error!r}") from error
    return Feat(
        name=name,
        category=category,
        prerequisites=prerequisites,
        repeatable=bool(entry.get("repeatable", False)),
        class_name=class_name,
        options=options,
        armour_training=armour,
        weapon_training=weapon_training,
    )


def feats(data: Dataset) -> Mapping[str, Feat]:
    """Every XPHB feat by name. Raises `ContentError` for a feat shaped
    otherwise than read, including a prerequisite it has no reading for."""
    table: dict[str, Feat] = {}
    for entry in data.read(FEATS)["feat"]:
        if entry.get("source") == SOURCE:
            if entry["name"] in table:
                raise ContentError(f"two {SOURCE} feats named {entry['name']!r}")
            table[entry["name"]] = _feat(entry)
    return MappingProxyType(dict(sorted(table.items())))
