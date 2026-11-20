"""Opening a fetched dataset, and reducing 5etools markup to plain text.

`docs/decisions.md` § w2a.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from dndsim.core.errors import DndSimError

__all__ = [
    "DEFAULT_DATA_DIR",
    "SOURCE",
    "ContentError",
    "Dataset",
    "FeatureText",
    "plain",
]

DEFAULT_DATA_DIR: Final = Path("data") / "5etools"
"""Where `content fetch` writes, relative to the working directory."""

VERSION_FILE: Final = "VERSION"
"""The file in a dataset holding the upstream commit SHA it was fetched at."""

SOURCE: Final = "XPHB"
"""The only source book imported: the 2024 Player's Handbook."""


class ContentError(DndSimError):
    """The fetched data is missing, or is not shaped the way the importer reads
    it. Raised at the boundary so a dataset change never reaches the domain."""


@dataclass(frozen=True, slots=True)
class FeatureText:
    """A named block of rules prose, with every 5etools tag reduced to its
    display text. `level` is the class level it is gained at, `None` for a
    species trait."""

    name: str
    level: int | None
    text: str


class Dataset:
    """A fetched 5etools data directory and the commit it was fetched at.

    Raises `ContentError` when the directory holds no `VERSION`: data that
    cannot say what it is cannot be cited by a result.
    """

    __slots__ = ("_cache", "root", "version")

    def __init__(self, root: Path = DEFAULT_DATA_DIR) -> None:
        version = root / VERSION_FILE
        if not version.is_file():
            raise ContentError(
                f"no fetched content at {root}: run `python -m "
                "dndsim.content.fetch` to populate it"
            )
        self.root = root
        self.version = version.read_text(encoding="utf-8").strip()
        self._cache: dict[str, Any] = {}

    def read(self, path: str) -> Any:
        """The parsed JSON file at `path`, relative to the dataset root."""
        cached = self._cache.get(path)
        if cached is None:
            file = self.root / path
            if not file.is_file():
                raise ContentError(f"{file} is missing from the fetched content")
            cached = self._cache[path] = json.loads(file.read_text(encoding="utf-8"))
        return cached


_TAG: Final = re.compile(r"\{@(\w+)(?: ([^{}]*))?\}")

# Every tag `plain()` reads, by the segment holding its display text when the
# tag carries one; an unlisted tag raises rather than being guessed at.
_DISPLAY_SEGMENT: Final = {
    **dict.fromkeys(
        ("5etools", "b", "book", "damage", "dice", "filter", "i", "link", "note"),
        0,
    ),
    **dict.fromkeys(("tip",), 0),
    **dict.fromkeys(
        (
            "action", "class", "condition", "creature", "deck", "feat",
            "hazard", "item", "itemMastery", "itemProperty", "language",
            "object", "optfeature", "race", "sense", "skill", "spell",
            "status", "table", "variantrule",
        ),
        2,
    ),
    "deity": 3,
    "quickref": 4,
    "subclass": 4,
    "classFeature": 5,
    "subclassFeature": 7,
}  # fmt: skip


def _display(match: re.Match[str]) -> str:
    tag, body = match[1], match[2] or ""
    parts = body.split("|")
    if tag == "dc":
        return f"DC {parts[0]}"
    if tag == "hit":
        return f"+{parts[0]}" if not parts[0].startswith(("+", "-")) else parts[0]
    at = _DISPLAY_SEGMENT.get(tag)
    if at is None:
        raise ContentError(f"no reading for the tag {match[0]!r}")
    return parts[at] if len(parts) > at and parts[at] else parts[0]


def plain(text: str) -> str:
    """`text` with every `{@tag …}` replaced by what the page would print.

    Raises `ContentError` for a tag it has no reading for.

    Inner tags first, so a nested tag resolves before the one holding it.
    """
    while True:
        reduced = _TAG.sub(_display, text)
        if reduced == text:
            return reduced
        text = reduced
