"""The closed hook registry: `Scope`, `Phase`, `HookGroup`, `HookSpec`, and the
registry that defines and looks up the 39 hooks.

`hook_named()` raises `UnknownHookError`, with near-miss suggestions, on a name
that is not defined. Specs compare by identity and name their context class as
a string (`docs/decisions.md` § w0e).
"""

import difflib
import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Final

__all__ = [
    "HookGroup",
    "HookRegistry",
    "HookSpec",
    "Phase",
    "Scope",
    "UnknownHookError",
    "all_hooks",
    "define_hook",
    "hook_named",
]


class UnknownHookError(LookupError):
    """Raised when a name is not one of the 39 defined hooks."""


@unique
class Scope(Enum):
    """Who `self.owner` is inside a handler for this hook (DESIGN.md § Hook
    scope).

    - `SELF` — the owner is the creature the event is *about*: the one acting,
      rolling, or undergoing something with no separate actor.
    - `TARGET` — the owner is on the receiving end of what another creature did.
    - `OBSERVER` — the owner merely perceives it (`on_leave_reach`).
    - `GLOBAL` — the event is about no creature at all (a round beginning).
    """

    SELF = "self"
    TARGET = "target"
    OBSERVER = "observer"
    GLOBAL = "global"


@unique
class Phase(Enum):
    """The ordering band a handler runs in (DESIGN.md § Ordering).

    Within a band the order is `(order, attachment_sequence)`. The values are
    ordinals (`docs/decisions.md` § w0e); the log serialises
    `phase.name.lower()`.

    - `EARLY` — declare, gate, veto.
    - `NORMAL` — add a modifier. The 95% case, and the default.
    - `LATE` — replace, multiply, halve.
    - `FINAL` — clamp, veto.
    """

    EARLY = 0
    NORMAL = 1
    LATE = 2
    FINAL = 3

    @property
    def requires_justification(self) -> bool:
        """True for the bands whose handlers need a `# PHASE:` comment: `LATE`
        and `FINAL`."""
        return self in _JUSTIFIED_PHASES


_JUSTIFIED_PHASES: Final = frozenset({Phase.LATE, Phase.FINAL})


@unique
class HookGroup(Enum):
    """The eight groups the 39 hooks fall into. Descriptive only; dispatch never
    reads it."""

    TIMELINE = "timeline"
    ATTACK = "attack"
    DAMAGE = "damage"
    HEALING_AND_DYING = "healing_and_dying"
    CHECKS_AND_SAVES = "checks_and_saves"
    CASTING = "casting"
    EFFECTS = "effects"
    MOVEMENT_AND_RESOURCES = "movement_and_resources"


@dataclass(frozen=True, slots=True, eq=False)
class HookSpec:
    """One extension point: its name, whose it is, and what it hands over.

    `eq=False` gives identity equality and an identity hash. Construct these
    through `define_hook()`; a `HookSpec` built by hand is not a registered hook
    and is never dispatched.
    """

    name: str
    scope: Scope
    group: HookGroup
    context: str
    summary: str

    def __str__(self) -> str:
        return self.name


_HOOK_NAME: Final = re.compile(r"^on_[a-z][a-z0-9_]*$")
_CONTEXT_NAME: Final = re.compile(r"^[A-Z][A-Za-z0-9]*Context$")


class HookRegistry:
    """A closed set of `HookSpec`s, keyed by name and ordered by definition.

    The real one is `REGISTRY`, populated by `definitions.py`.
    """

    __slots__ = ("_by_name",)

    def __init__(self) -> None:
        self._by_name: dict[str, HookSpec] = {}

    def define(
        self,
        name: str,
        *,
        scope: Scope,
        group: HookGroup,
        context: str,
        summary: str,
    ) -> HookSpec:
        """Register a hook and return its spec.

        Raises `ValueError` on a malformed name, context name or summary, or a
        name already defined.
        """
        if not _HOOK_NAME.match(name):
            raise ValueError(
                f"a hook name is `on_` followed by lowercase words, got {name!r}"
            )
        if name in self._by_name:
            raise ValueError(f"the hook {name!r} is already defined")
        if not _CONTEXT_NAME.match(context):
            raise ValueError(
                f"the context of {name!r} must be the name of a `*Context` class "
                f"in hooks/contexts.py, got {context!r}"
            )
        if not summary.strip() or "\n" in summary:
            raise ValueError(
                f"the summary of {name!r} is one non-empty line — it is the row "
                f"this hook gets in the generated docs/hooks.md"
            )
        spec = HookSpec(
            name=name, scope=scope, group=group, context=context, summary=summary
        )
        self._by_name[name] = spec
        return spec

    def named(self, name: str) -> HookSpec:
        """The spec called `name`, or `UnknownHookError` naming the near misses."""

        spec = self._by_name.get(name)
        if spec is None:
            raise UnknownHookError(f"unknown hook {name!r}{self._suggest(name)}")
        return spec

    def specs(self) -> tuple[HookSpec, ...]:
        """Every hook, in the order `definitions.py` defines them."""
        return tuple(self._by_name.values())

    def _suggest(self, name: str) -> str:
        near = difflib.get_close_matches(name, self._by_name, n=3, cutoff=0.6)
        if not near:
            return f"; there are {len(self._by_name)} hooks and none is close"
        return "; did you mean " + " or ".join(repr(n) for n in near) + "?"

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)


REGISTRY: Final = HookRegistry()


def define_hook(
    name: str,
    *,
    scope: Scope,
    group: HookGroup,
    context: str,
    summary: str,
) -> HookSpec:
    """Define a hook on the one real registry. Only `definitions.py` calls this."""
    return REGISTRY.define(
        name, scope=scope, group=group, context=context, summary=summary
    )


def hook_named(name: str) -> HookSpec:
    """Look a hook up by name, raising `UnknownHookError` on a typo."""
    return REGISTRY.named(name)


def all_hooks() -> tuple[HookSpec, ...]:
    """Every defined hook, in definition order."""
    return REGISTRY.specs()
