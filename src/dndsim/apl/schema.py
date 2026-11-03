"""`NamespaceSchema` — every path an APL clause may read, and the kind of value
each one is.

The compiler resolves a clause's dotted paths against it at load time
(DESIGN.md § Condition language); the proxies in `apl/namespace.py` are what
answers them at run time. A build widens `self` with the state its features
declare through `NamespaceSchema.extend()` (`docs/decisions.md` § w2e).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Final, TypeAlias

from dndsim.creatures.creature import Creature

__all__ = [
    "CREATURE_FIELDS",
    "PICKERS",
    "ROOTS",
    "Extension",
    "Field",
    "Kind",
    "NamespaceSchema",
]


class Kind(Enum):
    """The kind of value a path, call or sub-expression evaluates to."""

    NUMBER = "number"
    BOOL = "bool"
    CREATURE = "creature"
    GROUP = "group"
    """An ordered tuple of creatures; compared with a number, its length."""
    CELL = "cell"
    NODE = "node"
    """A proxy with members of its own, read only through one of them."""


@dataclass(frozen=True, slots=True)
class Field:
    """One member of a root or node: its kind, its parameters when it is a
    method, and its members when it is a `NODE`."""

    kind: Kind
    params: tuple[Kind, ...] | None = None
    members: Mapping[str, "Field"] = field(default_factory=dict)


Reader: TypeAlias = Callable[[Creature], bool | int | float]


@dataclass(frozen=True, slots=True)
class Extension:
    """A `self.<name>` a build's features provide: its kind and how to read it
    off the creature."""

    kind: Kind
    read: Reader


_NUMBER: Final = Field(Kind.NUMBER)
_BOOL: Final = Field(Kind.BOOL)
_GROUP: Final = Field(Kind.GROUP)

CREATURE_FIELDS: Final[Mapping[str, Field]] = MappingProxyType(
    {
        "hp": _NUMBER,
        "max_hp": _NUMBER,
        "hp_pct": _NUMBER,
        "ac": _NUMBER,
        "reach_ft": _NUMBER,
        "speed_ft": _NUMBER,
        "down": _BOOL,
    }
)
"""What any creature answers: `self`, `target`, `trigger.mover`, a picked
one."""

_GROUP_ROOT: Final = MappingProxyType(
    {"alive": _GROUP, "within": Field(Kind.GROUP, params=(Kind.NUMBER,))}
)

ROOTS: Final[Mapping[str, Mapping[str, Field]]] = MappingProxyType(
    {
        "self": MappingProxyType(
            {
                **CREATURE_FIELDS,
                "slots": Field(Kind.NODE, members={"highest": _NUMBER}),
            }
        ),
        "target": CREATURE_FIELDS,
        "trigger": MappingProxyType(
            {
                "crit": _BOOL,
                "damage_estimate": _NUMBER,
                "mover": Field(Kind.CREATURE),
                "origin": Field(Kind.CELL),
                "destination": Field(Kind.CELL),
            }
        ),
        "enemies": MappingProxyType({**_GROUP_ROOT, "in_reach": _GROUP}),
        "allies": MappingProxyType({**_GROUP_ROOT, "down": _GROUP}),
    }
)
"""The five roots. `enemies` and `allies` written bare are their `alive`."""

GROUP_ROOTS: Final = frozenset({"enemies", "allies"})

PICKERS: Final = frozenset({"lowest_hp", "highest_hp", "nearest", "farthest", "first"})
"""Selectors from a group to one creature: `<picker> of <group>` or
`<picker>(<group>)`."""


@dataclass(frozen=True, slots=True)
class NamespaceSchema:
    """The base roots, widened by the `self.*` extensions a build declares."""

    extensions: Mapping[str, Extension] = field(default_factory=dict)

    def extend(self, name: str, kind: Kind, read: Reader) -> "NamespaceSchema":
        """A schema that also answers `self.<name>`. Raises `ValueError` for a
        name the schema already answers, or a private one."""
        if name.startswith("_") or name in ROOTS["self"] or name in self.extensions:
            raise ValueError(f"self.{name} is already a path, or is private")
        if kind not in (Kind.NUMBER, Kind.BOOL):
            raise ValueError(f"an extension is a number or a bool, not {kind.value}")
        return NamespaceSchema({**self.extensions, name: Extension(kind, read)})

    def members(self, root: str) -> Mapping[str, Field]:
        """The members of `root`, with the extensions on `self`."""
        base = ROOTS[root]
        if root != "self" or not self.extensions:
            return base
        return {
            **base,
            **{name: Field(ext.kind) for name, ext in self.extensions.items()},
        }
