"""`@hook` — the entire authoring surface, and the two words a handler returns.

Nine tenths of the ~630 content implementations in this project are one
decorated method, so this module is where the API is either learnable in a page
or not learnable at all. Everything it does is in service of that:

**The hook name is looked up while the decorator runs.** `@hook("on_hitt")` is
an `UnknownHookError` naming the near misses, raised as the feature module is
imported, before a single line of the handler has run (DESIGN.md § Hook system,
property 1). This is the whole argument against a pub-sub bus, and it costs one
dict lookup per handler in the project, once.

**The declaration is data, not behaviour.** The decorator returns the function
unchanged with a `Handler` attached to it, so a handler is still an ordinary
method — callable directly in a test, visible in a traceback under its own name,
and inheritable. `HookSource.__init_subclass__` (source.py) is what collects
those declarations and validates the signatures; the dispatcher binds them. The
three steps are three modules because each is a different moment: decoration,
class creation, and the run.

**`once_per`, `costs` and `gate` are declared here and consumed nowhere near
here.** All three are the same idea — a handler that may decline to act should
not pay for acting — and all three are enforced by the dispatcher against the
handler's *return value*, which is why `HookResult` has exactly two members and
why they are the last thing in this docstring worth arguing about:

- `APPLIED` — this handler changed the event. Its `once_per` budget is spent and
  its cost is paid.
- `SKIPPED` — the conditions were not met. Nothing is spent, which is what makes
  a Sneak Attack that could not apply still available on the next attack, and an
  opportunity attack that declines still leave the reaction in hand.

There is deliberately no third member for "vetoed": halting the chain is
`ctx.veto(...)`, a mutation of the event like any other, and a handler that
vetoes has certainly `APPLIED`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Final, TypeAlias, TypeVar

from dndsim.core.timeline import Reset
from dndsim.hooks.registry import HookSpec, Phase, hook_named
from dndsim.rules.enums import ActionCost

__all__ = [
    "Handler",
    "HookResult",
    "R",
    "declared_handler",
    "hook",
]


@unique
class HookResult(Enum):
    """What a handler did, in the only two words the dispatcher understands.

    The values are lowercase strings for the event log, like every other enum
    in the project, and the enum is a plain `Enum` for the same reason as the
    rest (`rules/enums.py`): `result == "applied"` should be a type error rather
    than a comparison that works until someone misspells it.
    """

    APPLIED = "applied"
    SKIPPED = "skipped"


R: TypeAlias = HookResult
"""The alias every feature imports: `def sneak(self, ctx: HitContext) -> R`.

One letter because it appears in every handler signature and every `return` in
the codebase, and a `TypeAlias` rather than a second enum so that `R.APPLIED`
and `HookResult.APPLIED` are the same object — the dispatcher compares by
identity.
"""


@dataclass(frozen=True, slots=True)
class Handler:
    """One `@hook` declaration: which hook, what to call, and what it costs.

    Frozen and shared: a subclass that inherits a handler inherits this exact
    object, and the dispatcher binds it to a source without copying it. Nothing
    about a declaration varies per creature — what varies is the budget it has
    spent, which lives in the creature's ledger rather than here.
    """

    spec: HookSpec
    fn: Callable[..., HookResult]
    name: str
    phase: Phase
    order: int
    once_per: Reset | None
    costs: ActionCost | None
    gate: str | None

    def __str__(self) -> str:
        return f"{self.name}() on {self.spec.name}"


_MARK: Final = "__dnd_hook__"
_GATE_ID: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

_F = TypeVar("_F", bound=Callable[..., HookResult])


def hook(
    name: str,
    *,
    phase: Phase = Phase.NORMAL,
    order: int = 0,
    once_per: Reset | None = None,
    costs: ActionCost | None = None,
    gate: str | None = None,
) -> Callable[[_F], _F]:
    """Declare that this method handles `name`.

    - `phase` — the ordering band (DESIGN.md § Ordering). `NORMAL` is the 95%
      case and the default; `EARLY` and `FINAL` are the only bands allowed to
      `ctx.veto()`, and `LATE`/`FINAL` require a `# PHASE:` justification
      comment the linter (w3b) enforces.
    - `order` — a tiebreak *within* a band, for the rare pair of handlers whose
      relative order matters and which share one. Ties after it are broken by
      attachment sequence, which is the build YAML's feature order, so a run is
      deterministic either way.
    - `once_per` — the budget this handler spends when it applies:
      `Reset.TURN` is D&D's "once per turn" (**any** turn boundary, which is how
      Sneak Attack lands on an opportunity attack and again on your own turn)
      and `Reset.OWN_TURN` is "once on your turn". The likeliest subtle bug in
      the project; see `core/timeline.py`.
    - `costs` — the action-economy slot spent when it applies. An opportunity
      attack costs a reaction only if it happens.
    - `gate` — an APL action id (w2f). The decision of *whether* to use a
      reaction belongs to the creature's policy, not to the feature, so a gated
      handler is one the APL can turn off with no code in the feature at all.

    The return type is `HookResult` and the type variable says so, which means
    a handler that forgets its `return` is a mypy error at the point of
    decoration as well as a `FeatureContractError` at the point of dispatch.
    """
    spec = hook_named(name)
    if gate is not None and not _GATE_ID.match(gate):
        raise ValueError(
            f"gate={gate!r} is not an APL action id — those are lowercase "
            "dotted words, as in gate='rogue.uncanny_dodge'"
        )

    def mark(fn: _F) -> _F:
        declared = declared_handler(fn)
        if declared is not None:
            raise ValueError(
                f"{fn.__name__}() already handles {declared.spec.name}: one "
                "method declares one hook. Two hooks that share a body are two "
                "methods calling a third — stacking @hook would hide the "
                "second declaration from every reader of the first line."
            )
        setattr(
            fn,
            _MARK,
            Handler(
                spec=spec,
                fn=fn,
                name=fn.__name__,
                phase=phase,
                order=order,
                once_per=once_per,
                costs=costs,
                gate=gate,
            ),
        )
        return fn

    return mark


def declared_handler(value: Any) -> Handler | None:
    """The `Handler` `@hook` left on `value`, or `None` if it is not a handler.

    The one reader of the mark, so that "how a declaration is attached to a
    function" is a fact of this module rather than a string literal repeated in
    `source.py`.
    """
    declared = getattr(value, _MARK, None)
    return declared if isinstance(declared, Handler) else None
