"""`@hook`, the declaration every handler carries, and `HookResult`, the two
words a handler returns.

`@hook` looks the hook name up as it runs, so a typo is an `UnknownHookError`
at import, and returns the function unchanged with a `Handler` attached.
`once_per`, `costs` and `gate` are declared here and enforced by the dispatcher:
`APPLIED` spends the budget and pays the cost, `SKIPPED` spends nothing
(`docs/decisions.md` § w0g).
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
    """What a handler did: `APPLIED` changed the event, `SKIPPED` did not.

    The values are lowercase strings for the event log.
    """

    APPLIED = "applied"
    SKIPPED = "skipped"


R: TypeAlias = HookResult
"""The alias every feature imports: `def sneak(self, ctx: HitContext) -> R`.

`R.APPLIED is HookResult.APPLIED`; the dispatcher compares by identity.
"""


@dataclass(frozen=True, slots=True)
class Handler:
    """One `@hook` declaration: which hook, what to call, and what it costs.

    Frozen and shared by every subclass that inherits it; the budget it has
    spent lives in the creature's reset ledger, not here.
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

    - `phase` — the ordering band (DESIGN.md § Ordering). Only `EARLY` and
      `FINAL` may `ctx.veto()`; `LATE` and `FINAL` need a `# PHASE:` comment.
    - `order` — a tiebreak within a band; ties after it go to attachment
      sequence.
    - `once_per` — the budget spent when the handler applies. `Reset.TURN` is
      **any** turn boundary, `Reset.OWN_TURN` only the owner's
      (`core/timeline.py`).
    - `costs` — the action-economy slot spent when the handler applies.
    - `gate` — an APL action id the creature's policy is asked before the
      handler runs.

    Raises `UnknownHookError` for an undefined hook, and `ValueError` for a
    malformed `gate` or a method that already declares a hook.
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
    """The `Handler` `@hook` left on `value`, or `None` if it is not a handler."""

    declared = getattr(value, _MARK, None)
    return declared if isinstance(declared, Handler) else None
