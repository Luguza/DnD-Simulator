"""The XPHB classes' facts a build is checked against: hit die, saving throws,
weapon and armour training, skill choices, and the levels of the features that
ask the player to choose.

XPHB (2024) "Classes". `docs/decisions.md` § w2a2.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.loader import SOURCE, ContentError, Dataset
from dndsim.content.refs import class_files
from dndsim.core.dice import Dice
from dndsim.creatures.stats import Weapon
from dndsim.rules.enums import Ability, WeaponProperty

__all__ = [
    "SKILLS",
    "ArmourTraining",
    "CharacterClass",
    "SkillChoice",
    "WeaponTraining",
    "classes",
    "skill",
]

SKILLS: Final = frozenset(
    {
        "acrobatics",
        "animal handling",
        "arcana",
        "athletics",
        "deception",
        "history",
        "insight",
        "intimidation",
        "investigation",
        "medicine",
        "nature",
        "perception",
        "performance",
        "persuasion",
        "religion",
        "sleight of hand",
        "stealth",
        "survival",
    }
)
"""The 18 XPHB skills, by the lowercase name every record here uses."""


class ArmourTraining(Enum):
    """One kind of armour a class is trained with."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    SHIELD = "shield"


@dataclass(frozen=True, slots=True)
class WeaponTraining:
    """Simple or Martial weapons, narrowed to those with any of `properties`
    when it is not empty."""

    martial: bool
    properties: frozenset[WeaponProperty] = frozenset()

    def covers(self, weapon: Weapon) -> bool:
        """Whether `weapon` is one this entry trains."""
        return weapon.martial == self.martial and (
            not self.properties or not self.properties.isdisjoint(weapon.properties)
        )


@dataclass(frozen=True, slots=True)
class SkillChoice:
    """Choose `count` skills from `options`; `None` is any skill."""

    count: int
    options: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class CharacterClass:
    """A class as its table and Core Traits print it.

    `weapon_training` is a union: a weapon is trained if any entry covers it.
    The `*_level(s)` fields are the class levels whose features grant the
    choice; `None` for a class without the feature.
    """

    name: str
    hit_die: Dice
    saving_throws: frozenset[Ability]
    weapon_training: tuple[WeaponTraining, ...]
    armour_training: frozenset[ArmourTraining]
    skills: SkillChoice
    ability_score_improvement_levels: tuple[int, ...]
    subclass_level: int
    fighting_style_level: int | None
    weapon_mastery_level: int | None

    def trained_with(self, weapon: Weapon) -> bool:
        """Whether this class's weapon training covers `weapon`."""
        return any(entry.covers(weapon) for entry in self.weapon_training)


_CATEGORIES: Final = MappingProxyType({"simple": False, "martial": True})

# "Martial weapons that have the {@filter Finesse or Light|items|type=martial
# weapon|property=finesse;light} property": the filter's query is what is read.
_NARROWED: Final = re.compile(
    r"(?P<kind>Simple|Martial) weapons that have the "
    r"\{@filter [^|{}]+\|items\|(?P<query>[^{}]+)\} property"
)

_ASI: Final = "Ability Score Improvement"
_FIGHTING_STYLE: Final = "Fighting Style"
_WEAPON_MASTERY: Final = "Weapon Mastery"


def _property(name: str, where: str) -> WeaponProperty:
    try:
        return WeaponProperty(name.strip().replace("-", "_").replace(" ", "_"))
    except ValueError:
        raise ContentError(f"{where}: no weapon property {name!r}") from None


def _narrowed(text: str, where: str) -> WeaponTraining:
    match = _NARROWED.fullmatch(text)
    if match is None:
        raise ContentError(f"{where}: cannot read the weapon training {text!r}")
    query: dict[str, str] = {}
    for segment in match["query"].split("|"):
        key, sep, value = segment.partition("=")
        if not sep or key in query:
            raise ContentError(f"{where}: cannot read the filter {segment!r}")
        query[key] = value
    if set(query) != {"type", "property"}:
        raise ContentError(f"{where}: the filter in {text!r} is not type+property")
    martial = match["kind"] == "Martial"
    if query["type"] != f"{match['kind'].lower()} weapon":
        raise ContentError(f"{where}: {text!r} filters another category")
    properties = frozenset(_property(p, where) for p in query["property"].split(";"))
    return WeaponTraining(martial, properties)


def _weapon_training(raw: Any, where: str) -> tuple[WeaponTraining, ...]:
    out: list[WeaponTraining] = []
    for entry in raw or ():
        if isinstance(entry, str) and entry in _CATEGORIES:
            out.append(WeaponTraining(_CATEGORIES[entry]))
        elif isinstance(entry, str):
            out.append(_narrowed(entry, where))
        else:
            raise ContentError(f"{where}: cannot read the weapon training {entry!r}")
    return tuple(out)


def _armour_training(raw: Any, where: str) -> frozenset[ArmourTraining]:
    try:
        return frozenset(ArmourTraining(entry) for entry in raw or ())
    except (TypeError, ValueError) as error:
        raise ContentError(f"{where}: cannot read the armour training") from error


def skill(name: Any, where: str) -> str:
    """`name` checked against `SKILLS`. Raises `ContentError` otherwise."""
    if name not in SKILLS:
        raise ContentError(f"{where}: no skill {name!r}")
    return str(name)


def _skills(raw: Any, where: str) -> SkillChoice:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ContentError(f"{where}: cannot read the skill choice {raw!r}")
    (entry,) = raw
    if set(entry) == {"any"}:
        return SkillChoice(int(entry["any"]), None)
    choose = entry.get("choose")
    if set(entry) != {"choose"} or set(choose) != {"from", "count"}:
        raise ContentError(f"{where}: cannot read the skill choice {raw!r}")
    options = frozenset(skill(name, where) for name in choose["from"])
    return SkillChoice(int(choose["count"]), options)


def _grant_levels(entry: Any, where: str) -> dict[str, list[int]]:
    """Each feature name in `classFeatures` with every level it is listed at."""
    levels: dict[str, list[int]] = {}
    for feature in entry["classFeatures"]:
        ref = feature["classFeature"] if isinstance(feature, dict) else feature
        parts = ref.split("|")
        if len(parts) < 4 or not parts[3].isdigit():
            raise ContentError(f"{where}: cannot read the feature {ref!r}")
        levels.setdefault(parts[0], []).append(int(parts[3]))
    return levels


def _at_most_one(levels: dict[str, list[int]], name: str, where: str) -> int | None:
    found = levels.get(name, [])
    if len(found) > 1:
        raise ContentError(f"{where}: {name} is gained at {found}")
    return found[0] if found else None


def _class(entry: Any) -> CharacterClass:
    name = entry["name"]
    where = f"class {name!r}"
    try:
        hd = entry["hd"]
        if hd["number"] != 1:
            raise ContentError(f"{where}: a hit die of {hd!r}")
        hit_die = Dice(1, int(hd["faces"]))
        saves = frozenset(Ability(code) for code in entry["proficiency"])
        proficiencies = entry["startingProficiencies"]
        levels = _grant_levels(entry, where)
    except (KeyError, TypeError, ValueError) as error:
        raise ContentError(f"{where} is not shaped as read: {error!r}") from error
    if len(saves) != 2:
        raise ContentError(f"{where}: {len(saves)} saving throws, not two")
    subclass = _at_most_one(levels, f"{name} Subclass", where)
    if subclass is None:
        raise ContentError(f"{where}: no {name} Subclass feature")
    return CharacterClass(
        name=name,
        hit_die=hit_die,
        saving_throws=saves,
        weapon_training=_weapon_training(proficiencies.get("weapons"), where),
        armour_training=_armour_training(proficiencies.get("armor"), where),
        skills=_skills(proficiencies.get("skills"), where),
        ability_score_improvement_levels=tuple(sorted(levels.get(_ASI, []))),
        subclass_level=subclass,
        fighting_style_level=_at_most_one(levels, _FIGHTING_STYLE, where),
        weapon_mastery_level=_at_most_one(levels, _WEAPON_MASTERY, where),
    )


def classes(data: Dataset) -> Mapping[str, CharacterClass]:
    """Every XPHB class by name. Raises `ContentError` for a class shaped
    otherwise than read, including a weapon training it cannot parse."""
    table: dict[str, CharacterClass] = {}
    for file in class_files(data):
        for entry in file.get("class", []):
            if entry.get("source") == SOURCE:
                table[entry["name"]] = _class(entry)
    return MappingProxyType(dict(sorted(table.items())))
