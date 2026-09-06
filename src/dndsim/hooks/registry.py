"""The closed hook registry: the vocabulary of the extension points themselves.

Every one of the ~630 content implementations plugs into this registry and
nothing else, so what it refuses matters more than what it stores.

**A hook name is looked up, never trusted.** `@hook("on_hitt")` must fail, and
fail while the feature module is being imported, because a pub-sub bus that
accepts the typo and silently never calls the handler is the single likeliest
failure mode of ~400 features written by agents that cannot read each other's
code (DESIGN.md § Hook system). `hook_named()` is therefore a lookup that raises
`UnknownHookError` — with `difflib` suggestions, the same courtesy the APL
compiler gives an unknown namespace path.

This is *not* in tension with DESIGN.md's "an unregistered hook returns an empty
handler list rather than raising". That sentence is about *dispatch*: a hook with
no handlers attached to this creature costs one dict lookup and a return (the
Null Object pattern), which is a statement about a hook nobody listens to, not
about a name nobody defined. Names are validated here, once, at import; the hot
path never sees a string.

**A spec is a singleton and identity is its equality.** `define_hook()` is called
exactly 39 times, in `definitions.py`, and the resulting objects are the module
constants the engine dispatches on (`dispatch(ON_HIT, ctx)` — a typo there is an
`AttributeError` before the process gets going). The dataclass is therefore
`eq=False`: two specs are the same hook only when they are the same object, so a
dispatch table keyed by spec hashes one pointer rather than five fields, on a
path that runs several times per attack against a 50k/sec budget.

**The context type is named, not imported.** `hooks/contexts.py` (w0f) sits
*above* this module and imports it, so a spec carries the context class's name as
a string and w0f's contract test asserts the two sides agree — one hook, one
Parameter Object, checked rather than assumed. It is also exactly the column
`tools/gen_docs.py` (w3c) prints, which is why `group` and `summary` are here:
`docs/hooks.md` is generated from this registry, and a hand-maintained copy of a
39-row table is a copy that drifts.

What is deliberately absent: **no freeze**. The registry is closed in the sense
that content authors may not add to it, but that is enforced by the CI guard
`w3f` enables *after* the W3 paper pass has added the hooks the pass proves
missing (ROADMAP w0e, w3f). A `seal()` here would have to be unsealed by the one
task that is supposed to need it.
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
    """Raised when a name is not one of the 39 defined hooks.

    A `LookupError` rather than a bespoke base: it *is* a failed lookup, and
    `KeyError` was rejected because its `str()` re-quotes the message, which
    would bury the "did you mean" suggestion the caller actually needs.
    """


@unique
class Scope(Enum):
    """Who `self.owner` is inside a handler for this hook.

    The single biggest learnability lever in the system (DESIGN.md § Hook
    scope): because the hook declares it, no feature ever writes
    `if ctx.attacker is self.owner`, and an author reading
    `on_incoming_damage(self, ctx)` knows without prose that they are the
    victim.

    - `SELF` — the owner is the creature the event is *about*: the one acting,
      rolling, or undergoing something with no separate actor.
    - `TARGET` — the owner is on the receiving end of what another creature did.
    - `OBSERVER` — the owner merely perceives it. `on_leave_reach` is the
      canonical case: someone else moved, and it is your reach they left.
    - `GLOBAL` — the event is about no creature at all (a round beginning).
    """

    SELF = "self"
    TARGET = "target"
    OBSERVER = "observer"
    GLOBAL = "global"


@unique
class Phase(Enum):
    """The ordering band a handler runs in.

    Bands rather than free integers (DESIGN.md § Ordering): an author picks the
    one sentence that describes what their handler does, and two features that
    both "add a modifier" cannot fight over who is 150 and who is 200. Within a
    band the order is `(order, attachment_sequence)`, and attachment sequence is
    the build YAML's feature order, so a run is deterministic.

    Like `TurnPhase`, the values are **ordinals rather than lowercase strings**,
    because this enumeration is a position in a sequence and `"early"` sorts
    after `"before"` only by accident of the alphabet. It is still a plain
    `Enum`, so `phase == 0` stays a mypy error; the log serialises
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
        """True for the bands whose handlers need a `# PHASE:` comment.

        Order-sensitivity is the property that makes a feature interact with
        features its author never read, so `LATE` and `FINAL` are the set
        `rg "PHASE:"` must enumerate — it should stay under ~30 lines for the
        whole project. The linter (`tools/check_features.py`, w3b) enforces the
        comment; the rule about *which* bands need one lives here, next to the
        bands, rather than as a literal inside the linter.
        """
        return self in _JUSTIFIED_PHASES


_JUSTIFIED_PHASES: Final = frozenset({Phase.LATE, Phase.FINAL})


@unique
class HookGroup(Enum):
    """The eight groups the 39 hooks fall into.

    Purely descriptive — the dispatcher never reads it. It exists because
    `docs/hooks.md` is generated (w3c) and because a contract test can then
    check the group counts against the table in DESIGN.md, which is what keeps
    the design document and the registry from quietly disagreeing.
    """

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

    `eq=False` gives identity equality and an identity hash — see the module
    docstring. Construct these through `define_hook()`; a `HookSpec` built by
    hand is a hook the registry has never heard of and can never be dispatched.
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

    There is one of these — `REGISTRY`, populated by `definitions.py` — but it
    is a class rather than a bare module dict so that the validation below can
    be tested against a throwaway instance instead of by adding a fortieth hook
    to the real one.
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

        Every rejection here is a mistake made while editing `definitions.py`,
        which is why they are plain `ValueError`s: no content author can reach
        this code, and the file it guards is read by every feature ever written.
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
        """The spec called `name`, or `UnknownHookError` naming the near misses.

        This is what turns `@hook("on_hitt")` from a handler that is never
        called into a failure at import time.
        """
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
