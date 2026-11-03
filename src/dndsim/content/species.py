"""The XPHB species: size, speed, darkvision and traits as prose.

The traits' behaviour is hand-coded in `features/species/`; what imports is
the numbers and the text. XPHB (2024) "Character Origins".
`docs/decisions.md` § w2a.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from dndsim.content.loader import SOURCE, ContentError, Dataset, FeatureText, plain
from dndsim.rules.enums import Size

__all__ = ["Species", "species"]

SPECIES: Final = "races.json"

_SIZES: Final = MappingProxyType(
    {
        "T": Size.TINY,
        "S": Size.SMALL,
        "M": Size.MEDIUM,
        "L": Size.LARGE,
        "H": Size.HUGE,
        "G": Size.GARGANTUAN,
    }
)


@dataclass(frozen=True, slots=True)
class Species:
    """A species as printed. `sizes` holds every size a member may choose;
    `darkvision_ft` is 0 for a species without Darkvision."""

    name: str
    sizes: frozenset[Size]
    speed_ft: int
    darkvision_ft: int
    traits: tuple[FeatureText, ...]


def _text(node: Any) -> str:
    if isinstance(node, str):
        return plain(node)
    if isinstance(node, list):
        return "\n".join(t for t in map(_text, node) if t)
    if isinstance(node, dict):
        head = f"{plain(node['name'])}." if "name" in node else ""
        body = [_text(node[k]) for k in ("entry", "entries", "items") if k in node]
        return " ".join(p for p in (head, *body) if p)
    return str(node)


def _species(entry: Any) -> Species:
    name = entry["name"]
    speed = entry.get("speed")
    if isinstance(speed, dict):
        speed = speed.get("walk")
    try:
        sizes = frozenset(_SIZES[code] for code in entry["size"])
        walk = int(speed)
    except (KeyError, TypeError, ValueError) as error:
        raise ContentError(f"species {name!r} is not shaped as read") from error
    traits = tuple(
        FeatureText(plain(e["name"]), None, _text(e.get("entries", [])))
        for e in entry.get("entries", [])
        if isinstance(e, dict) and "name" in e
    )
    return Species(name, sizes, walk, int(entry.get("darkvision", 0)), traits)


def species(data: Dataset) -> Mapping[str, Species]:
    """Every XPHB species by name. Raises `ContentError` for a species shaped
    otherwise than read, including one built by copying another (`_copy`)."""
    table: dict[str, Species] = {}
    for entry in data.read(SPECIES)["race"]:
        if entry.get("source") != SOURCE:
            continue
        if "_copy" in entry:
            raise ContentError(f"species {entry['name']!r} is a copy of another")
        table[entry["name"]] = _species(entry)
    return MappingProxyType(dict(sorted(table.items())))
