"""Class, subclass and optional features as prose, with the reference
indirection resolved.

A feature's `entries` may hold `refClassFeature`, `refSubclassFeature` and
`refOptionalfeature` nodes pointing at other features. Each is resolved to the
feature it names, which follows its parent as a `FeatureText` of its own; the
parent's text keeps only the name. `docs/decisions.md` § w2a.
"""

from collections.abc import Iterator
from typing import Any, Final

from dndsim.content.loader import SOURCE, ContentError, Dataset, FeatureText, plain

__all__ = [
    "class_features",
    "class_files",
    "optional_feature",
    "prose",
    "subclass_features",
]

CLASS_INDEX: Final = "class/index.json"
OPTIONAL: Final = "optionalfeatures.json"

_REF_KEYS: Final = {
    "refClassFeature": "classFeature",
    "refSubclassFeature": "subclassFeature",
    "refOptionalfeature": "optionalfeature",
}

# 5etools' default when a reference leaves a source segment empty.
_DEFAULT_SOURCE: Final = "PHB"

Key = tuple[str, ...]


def class_files(data: Dataset) -> Iterator[Any]:
    """Every class file the class index names, parsed."""
    for name in data.read(CLASS_INDEX).values():
        yield data.read(f"class/{name}")


def _segments(ref: str, width: int) -> list[str]:
    parts = ref.split("|")
    if len(parts) > width:
        raise ContentError(f"reference {ref!r} has more than {width} segments")
    return [p.strip().lower() for p in parts] + [""] * (width - len(parts))


def _class_key(ref: str) -> Key:
    name, cls, cls_src, level, src = _segments(ref, 5)
    cls_src = cls_src or _DEFAULT_SOURCE.lower()
    return ("class", name, cls, cls_src, level, src or cls_src)


def _subclass_key(ref: str) -> Key:
    name, cls, cls_src, sub, sub_src, level, src = _segments(ref, 7)
    cls_src = cls_src or _DEFAULT_SOURCE.lower()
    sub_src = sub_src or _DEFAULT_SOURCE.lower()
    return ("subclass", name, cls, cls_src, sub, sub_src, level, src or sub_src)


def _optional_key(ref: str) -> Key:
    name, src = _segments(ref, 2)
    return ("optional", name, src or _DEFAULT_SOURCE.lower())


def _key(kind: str, ref: str) -> Key:
    if kind == "classFeature":
        return _class_key(ref)
    if kind == "subclassFeature":
        return _subclass_key(ref)
    return _optional_key(ref)


class _Index:
    __slots__ = ("features",)

    def __init__(self, data: Dataset) -> None:
        self.features: dict[Key, Any] = {}
        for file in class_files(data):
            for f in file.get("classFeature", []):
                ref = "|".join(
                    [f["name"], f["className"], f["classSource"], str(f["level"])]
                    + [f["source"]]
                )
                self.features[_class_key(ref)] = f
            for f in file.get("subclassFeature", []):
                ref = "|".join(
                    [f["name"], f["className"], f["classSource"]]
                    + [f["subclassShortName"], f["subclassSource"], str(f["level"])]
                    + [f["source"]]
                )
                self.features[_subclass_key(ref)] = f
        for f in data.read(OPTIONAL).get("optionalfeature", []):
            self.features[_optional_key(f"{f['name']}|{f['source']}")] = f

    def get(self, key: Key, ref: str) -> Any:
        try:
            return self.features[key]
        except KeyError:
            raise ContentError(f"reference {ref!r} names no feature") from None


def prose(node: Any) -> str:
    """`node` as plain text, tables included. Raises `ContentError` for a
    reference node, which has no feature index to be followed against."""
    return _render(node, None, [])


def _render(node: Any, index: _Index | None, found: list[Any]) -> str:
    """`node` as plain text; each reference met is appended to `found`."""
    if isinstance(node, str):
        return plain(node)
    if isinstance(node, list):
        return "\n".join(t for t in (_render(n, index, found) for n in node) if t)
    if not isinstance(node, dict):
        return str(node)
    kind = node.get("type")
    if kind in _REF_KEYS:
        ref = node[_REF_KEYS[kind]]
        if index is None:
            raise ContentError(f"reference {ref!r} where none can be followed")
        target = index.get(_key(_REF_KEYS[kind], ref), ref)
        found.append(target)
        return plain(target["name"])
    if kind == "table":
        rows = [node.get("colLabels", []), *node.get("rows", [])]
        return "\n".join(
            " | ".join(_render(cell, index, found) for cell in row) for row in rows
        )
    parts = [f"{plain(node['name'])}." if "name" in node else ""]
    for field in ("entry", "entries", "items"):
        if field in node:
            parts.append(_render(node[field], index, found))
    return " ".join(p for p in parts if p)


def _features(roots: list[Any], index: _Index) -> tuple[FeatureText, ...]:
    out: list[FeatureText] = []
    queue: list[tuple[Any, int | None]] = [(root, None) for root in roots]
    while queue:
        feature, inherited = queue.pop(0)
        found: list[Any] = []
        text = _render(feature.get("entries", []), index, found)
        level = feature.get("level", inherited)
        out.append(FeatureText(plain(feature["name"]), level, text))
        queue[0:0] = [(child, level) for child in found]
    return tuple(out)


def _xphb_class(data: Dataset, class_name: str) -> Any:
    for file in class_files(data):
        for entry in file.get("class", []):
            if entry.get("source") == SOURCE and entry["name"] == class_name:
                return entry
    raise ContentError(f"no {SOURCE} class named {class_name!r}")


def class_features(data: Dataset, class_name: str) -> tuple[FeatureText, ...]:
    """Every feature an XPHB class lists, in level order, each followed by the
    features it references."""
    index = _Index(data)
    roots = []
    for entry in _xphb_class(data, class_name)["classFeatures"]:
        ref = entry["classFeature"] if isinstance(entry, dict) else entry
        roots.append(index.get(_class_key(ref), ref))
    return _features(roots, index)


def subclass_features(
    data: Dataset, class_name: str, subclass: str
) -> tuple[FeatureText, ...]:
    """Every feature an XPHB subclass (by short name) lists, each followed by
    the features it references."""
    index = _Index(data)
    for file in class_files(data):
        for entry in file.get("subclass", []):
            if (
                entry.get("source") == SOURCE
                and entry["className"] == class_name
                and entry["shortName"] == subclass
            ):
                roots = [
                    index.get(_subclass_key(ref), ref)
                    for ref in entry["subclassFeatures"]
                ]
                return _features(roots, index)
    raise ContentError(f"no {SOURCE} subclass {class_name} ({subclass})")


def optional_feature(data: Dataset, name: str) -> FeatureText:
    """One XPHB optional feature (an invocation, maneuver or metamagic)."""
    ref = f"{name}|{SOURCE}"
    index = _Index(data)
    feature = index.get(_optional_key(ref), ref)
    return _features([feature], index)[0]
