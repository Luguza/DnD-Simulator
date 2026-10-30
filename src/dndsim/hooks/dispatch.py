"""The dispatcher: a per-creature table of handlers, run in order over one context.

The table is kept sorted by `attach()` and `detach()`, which touch only the hooks
the source has handlers on, and `dispatch()` walks a tuple. Both build new
tuples, so an effect applied mid-hook never joins the dispatch that applied it.
Nothing is spent by a handler that declined: `once_per` and `costs` are paid
only on `APPLIED`. The dispatcher stamps provenance before each handler and
restores the stamp it found afterwards.

It does not drain `ctx.pending_effects` or `close()` the context; the engine
that owns the event does both, once (`docs/decisions.md` § w0g).
"""

from bisect import insort
from collections.abc import Callable
from operator import attrgetter
from typing import Final, Protocol

from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.timeline import ResetManager
from dndsim.hooks.contexts import Context
from dndsim.hooks.decorator import Handler, HookResult
from dndsim.hooks.registry import HookSpec, Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import ActionCost

__all__ = ["BoundHandler", "Dispatcher", "Economy", "Gate"]

_APPLIED: Final = HookResult.APPLIED
_SKIPPED: Final = HookResult.SKIPPED
_VETO_PHASES: Final = frozenset({Phase.EARLY, Phase.FINAL})
_SORT_KEY: Final = attrgetter("sort_key")


class Economy(Protocol):
    """As much of the action economy as the dispatcher needs to see
    (`docs/decisions.md` § w0g)."""

    def can_spend(self, cost: ActionCost) -> bool: ...

    def spend(self, cost: ActionCost) -> None: ...


class Gate(Protocol):
    """The action list, asked whether a gated handler may act."""

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool: ...


class BoundHandler:
    """One declaration, bound to the source that will receive it.

    Everything the dispatch loop reads is flattened onto a slot here at attach
    time. It is also the key its `once_per` budget is filed under, so it is
    created once per (source, declaration) and survives table rebuilds
    (`docs/decisions.md` § w0g).
    """

    __slots__ = (
        "applied",
        "can_veto",
        "costs",
        "fn",
        "gate",
        "guarded",
        "handler",
        "once_per",
        "order",
        "phase",
        "sequence",
        "sort_key",
        "source",
        "source_id",
        "spec",
    )

    def __init__(self, source: HookSource, handler: Handler, sequence: int) -> None:
        self.source = source
        self.handler = handler
        self.spec = handler.spec
        # `object`, not `HookResult`: with the annotation trusted, mypy would
        # prove the return-value check in `dispatch()` unreachable.
        self.fn: Callable[..., object] = handler.fn
        self.source_id = type(source).ID
        self.phase = handler.phase
        self.order = handler.order
        self.sequence = sequence
        self.once_per = handler.once_per
        self.costs = handler.costs
        self.gate = handler.gate
        self.can_veto = handler.phase in _VETO_PHASES
        # Must stay equal to "any of the three fields above is set".
        self.guarded = (
            handler.once_per is not None
            or handler.costs is not None
            or handler.gate is not None
        )
        # `APPLIED` returns, for DESIGN.md § No-op detector.
        self.applied = 0
        # Phase band, then declared order, then attachment sequence.
        self.sort_key = (handler.phase.value, handler.order, sequence)

    def __repr__(self) -> str:
        return f"<{self.source_id}.{self.handler.name} on {self.spec.name}>"


class Dispatcher:
    """One creature's handlers, and the only thing that calls them.

    Takes a reset ledger, an action economy and an action list; attachment
    sequence is the last tiebreak in the dispatch order.
    """

    __slots__ = ("_bound", "_economy", "_gate", "_resets", "_sequence", "_table")

    def __init__(
        self,
        *,
        resets: ResetManager | None = None,
        economy: Economy | None = None,
        gate: Gate | None = None,
    ) -> None:
        self._resets = ResetManager() if resets is None else resets
        self._economy = economy
        self._gate = gate
        self._bound: dict[HookSource, tuple[BoundHandler, ...]] = {}
        self._table: dict[HookSpec, tuple[BoundHandler, ...]] = {}
        self._sequence = 0

    @property
    def resets(self) -> ResetManager:
        """The ledger this dispatcher spends `once_per` budgets against."""
        return self._resets

    def bind(self, *, economy: Economy | None = None, gate: Gate | None = None) -> None:
        """Wire in the economy and the action list after construction.

        Raises `EngineAssemblyError` if either is already set
        (`docs/decisions.md` § w0i, § w1a).
        """
        if economy is not None:
            if self._economy is not None:
                raise EngineAssemblyError(
                    "this dispatcher already has an action economy; a creature "
                    "has one economy for the length of an encounter"
                )
            self._economy = economy
        if gate is not None:
            if self._gate is not None:
                raise EngineAssemblyError(
                    "this dispatcher already has an action list; a build's "
                    "policy is compiled once and does not change mid-run"
                )
            self._gate = gate

    def attach(self, source: HookSource) -> None:
        """Add a source's handlers to the table, at the end of the order."""
        if source in self._bound:
            raise ValueError(
                f"{source!r} is already attached to this dispatcher; attaching "
                "it twice would run every one of its handlers twice"
            )
        self._sequence += 1
        sequence = self._sequence
        bound_handlers = tuple(
            BoundHandler(source, handler, sequence) for handler in source.HANDLERS
        )
        self._bound[source] = bound_handlers
        table = self._table
        grouped: dict[HookSpec, list[BoundHandler]] = {}
        for bound in bound_handlers:
            grouped.setdefault(bound.spec, []).append(bound)
        for spec, bounds in grouped.items():
            merged = list(table.get(spec, ()))
            for bound in bounds:
                # Right of equal keys: one source's handlers on one hook keep
                # their declared order.
                insort(merged, bound, key=_SORT_KEY)
            table[spec] = tuple(merged)

    def detach(self, source: HookSource) -> None:
        """Remove a source's handlers — an effect expiring, an item unequipped."""
        if source not in self._bound:
            raise ValueError(f"{source!r} is not attached to this dispatcher")
        table = self._table
        for spec in {bound.spec for bound in self._bound.pop(source)}:
            kept = tuple(bound for bound in table[spec] if bound.source is not source)
            if kept:
                table[spec] = kept
            else:
                del table[spec]

    def sources(self) -> tuple[HookSource, ...]:
        """Everything attached, in attachment order."""
        return tuple(self._bound)

    def handlers(self, spec: HookSpec) -> tuple[BoundHandler, ...]:
        """What `dispatch(spec, ...)` would run, in the order it would run them:
        the same tuple the dispatch walks."""
        return self._table.get(spec, ())

    def responsive(self, spec: HookSpec) -> bool:
        """Whether dispatching `spec` could run anything at all on this creature.

        False only when no handler is attached or the economy can pay for none
        of them; `once_per` and `gate` are not asked, so True may still run
        nothing (`docs/decisions.md` § w0g3). With no economy, a costed handler
        answers True, so that `dispatch()` raises `EngineAssemblyError` rather
        than the creature being silently left out.
        """
        handlers = self._table.get(spec)
        if handlers is None:
            return False
        economy = self._economy
        for bound in handlers:
            cost = bound.costs
            if cost is None or economy is None or economy.can_spend(cost):
                return True
        return False

    def dispatch(self, spec: HookSpec, ctx: Context) -> None:
        """Run every handler for `spec` over `ctx`, in order, until one vetoes.

        The context carries the result: what handlers contributed is in its
        bags, and `ctx.vetoed` says whether the event happens at all. Raises
        `FeatureContractError` for a handler that returns anything but `R`.

        Hot path (`docs/decisions.md` § w0i): `bound.guarded` stands in for the
        three questions `_permits()` asks, and the stamp and the entry veto
        are read off the frame directly rather than through `ctx.stamp()` and
        `ctx.vetoed`. The in-loop veto goes through `ctx.vetoed` on purpose:
        mypy narrows the frame's field to `None` across the handler calls and
        would read the `break` as dead code.
        """
        handlers = self._table.get(spec)
        if handlers is None:
            return
        frame = ctx._frame
        if frame.veto_reason is not None:
            # The second half of a paired dispatch does not run either.
            return
        was_source = frame.source
        was_phase = frame.phase
        try:
            for bound in handlers:
                if bound.guarded and not self._permits(bound, ctx):
                    continue
                frame.source = bound.source_id
                frame.phase = bound.phase
                result = bound.fn(bound.source, ctx)
                if result is _APPLIED:
                    bound.applied += 1
                    if bound.guarded:
                        self._spend(bound)
                elif result is not _SKIPPED:
                    raise FeatureContractError(
                        f"{bound!r} returned {result!r}. A handler returns "
                        "R.APPLIED when it changed the event and R.SKIPPED when "
                        "its conditions were not met — a missing return is the "
                        "usual cause, and it would silently stop the handler's "
                        "once_per budget and cost from ever being spent."
                    )
                if bound.can_veto and ctx.vetoed:
                    break
        finally:
            frame.source = was_source
            frame.phase = was_phase

    def _permits(self, bound: BoundHandler, ctx: Context) -> bool:
        """Whether a guarded handler may run: `once_per`, then `costs`, then
        `gate`. Raises `EngineAssemblyError` when the economy or the action
        list it needs is not wired in."""
        once_per = bound.once_per
        if once_per is not None and self._resets.is_spent(bound):
            return False
        cost = bound.costs
        if cost is not None:
            economy = self._economy
            if economy is None:
                raise EngineAssemblyError(
                    f"{bound!r} costs a {cost.value} and this dispatcher "
                    "has no action economy to spend it from (w1a)"
                )
            if not economy.can_spend(cost):
                return False
        gate_id = bound.gate
        if gate_id is not None:
            gate = self._gate
            if gate is None:
                raise EngineAssemblyError(
                    f"{bound!r} is gated on {gate_id!r} and this dispatcher has "
                    "no action list to ask (w2f). A gate nobody answers is not "
                    "an open one."
                )
            if not gate.allows(gate_id, bound.source, ctx):
                return False
        return True

    def _spend(self, bound: BoundHandler) -> None:
        """Pay for a guarded handler that returned `APPLIED`, and only then."""
        once_per = bound.once_per
        if once_per is not None:
            self._resets.spend(bound, once_per)
        cost = bound.costs
        if cost is not None and self._economy is not None:
            # `_permits()` already refused a cost with no economy; the second
            # test is for the type checker.
            self._economy.spend(cost)
