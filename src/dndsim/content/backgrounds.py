"""The XPHB backgrounds: three abilities and their increases, an Origin feat,
and two skills.

XPHB (2024) "Character Origins". `docs/decisions.md` § w2a2.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.classes import skill
from dndsim.content.feats import Feat, FeatCategory, feats
from dndsim.content.loader import SOURCE, ContentError, Dataset
from dndsim.rules.enums import Ability

__all__ = ["INCREASES", "Background", "backgrounds"]

BACKGROUNDS: Final = "backgrounds.json"

INCREASES: Final = ((2, 1), (1, 1, 1))
"""XPHB "Background Ability Scores": +2 and +1, or +1 to all three."""


@dataclass(frozen=True, slots=True)
class Background:
    """A background as printed.

    `increases` holds each pattern the three `abilities` may be raised by, as
    the 2 and 1 of +2/+1 or the 1, 1, 1 of +1/+1/+1. `feat` is the Origin
    feat's name in `feats()`, and `feat_option` the version of it the
    background grants, as Magic Initiate's Cleric, or `None`.
    """

    name: str
    abilities: frozenset[Ability]
    increases: tuple[tuple[int, ...], ...]
    feat: str
    feat_option: str | None
    skills: frozenset[str]


def _abilities(raw: Any, where: str) -> tuple[frozenset[Ability], tuple[Any, ...]]:
    chosen: set[frozenset[Ability]] = set()
    increases = []
    for entry in raw:
        if set(entry) != {"choose"} or set(entry["choose"]) != {"weighted"}:
            raise ContentError(f"{where}: cannot read the increase {entry!r}")
        weighted = entry["choose"]["weighted"]
        chosen.add(frozenset(Ability(code) for code in weighted["from"]))
        increases.append(tuple(int(w) for w in weighted["weights"]))
    if len(chosen) != 1 or len(next(iter(chosen))) != 3:
        raise ContentError(f"{where}: the increases are not over three abilities")
    if sorted(increases) != sorted(INCREASES):
        raise ContentError(f"{where}: increases {increases}, not {INCREASES}")
    return next(iter(chosen)), tuple(increases)


def _feat(raw: Any, origin: Mapping[str, Feat], where: str) -> tuple[str, str | None]:
    if len(raw) != 1 or len(raw[0]) != 1:
        raise ContentError(f"{where}: not one Origin feat: {raw!r}")
    (ref,) = raw[0]
    name, _, option = ref.split("|", 1)[0].partition("; ")
    feat = origin.get(name.lower())
    if feat is None:
        raise ContentError(f"{where}: {ref!r} names no {SOURCE} Origin feat")
    if not option:
        return feat.name, None
    for printed in feat.options:
        if printed.lower() == option.lower():
            return feat.name, printed
    raise ContentError(f"{where}: {feat.name} has no version {option!r}")


def _background(entry: Any, origin: Mapping[str, Feat]) -> Background:
    name = entry["name"]
    where = f"background {name!r}"
    try:
        abilities, increases = _abilities(entry["ability"], where)
        feat, option = _feat(entry["feats"], origin, where)
        (skills,) = entry["skillProficiencies"]
        known = frozenset(skill(s, where) for s, granted in skills.items() if granted)
    except (KeyError, TypeError, ValueError) as error:
        raise ContentError(f"{where} is not shaped as read: {error!r}") from error
    return Background(name, abilities, increases, feat, option, known)


def backgrounds(data: Dataset) -> Mapping[str, Background]:
    """Every XPHB background by name, its feat checked against `feats()`.
    Raises `ContentError` for a background shaped otherwise than read, or one
    whose feat is not an Origin feat."""
    origin = {
        feat.name.lower(): feat
        for feat in feats(data).values()
        if feat.category is FeatCategory.ORIGIN
    }
    table: dict[str, Background] = {}
    for entry in data.read(BACKGROUNDS)["background"]:
        if entry.get("source") != SOURCE:
            continue
        if "_copy" in entry:
            raise ContentError(f"background {entry['name']!r} is a copy of another")
        table[entry["name"]] = _background(entry, origin)
    return MappingProxyType(dict(sorted(table.items())))
