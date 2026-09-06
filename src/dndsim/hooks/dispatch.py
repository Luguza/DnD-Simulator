"""The dispatcher: a per-creature table of handlers, run in order over one context.

This is the **Intercepting Filter** of DESIGN.md § Hook system — a pipeline of
handlers over one shared mutable context — with the **Chain of Responsibility**
element restricted to a veto. It is also the hottest code in the project: a
level-12 barbarian carries ~25 sources, at least 15 of them on `on_hit`, and the
budget is 50k attack resolutions per second per core (ROADMAP w0i). Almost every
decision below is one of those two facts.

**The table is per creature and precomputed.** `attach()` sorts once; `dispatch()`
is a dict lookup and a walk over a tuple. A global bus scanned per event would
put every feature in the encounter on every creature's hot path, which is the
second reason DESIGN.md rejects pub-sub. A hook nobody listens to is one failed
lookup and a return — the **Null Object** ending of that same sentence.

**The tuple is the snapshot.** `attach()` and `detach()` build new tuples rather
than mutating the ones in the table, so a handler that applies an effect mid-hook
cannot extend the event it is running in. That is the snapshot rule from the
feature-authoring skill made structural: Reckless Attack has to apply its effect
*and* call `ctx.grant_advantage()` for the triggering attack, because the effect
it just applied is not in the list being walked. An author who forgets the second
half gets a wrong number in a golden; an author whose effect *did* join the
running event would get a different wrong number depending on attachment order.

**Nothing is spent by a handler that declined.** `once_per`, `costs` and `gate`
are three questions asked in ascending order of cost — a ledger lookup, an
economy query, then the APL — and the first two are *also* asked again after the
handler returns, because DESIGN.md § The uniform handler shape puts the budget on
the return value: `APPLIED` spends, `SKIPPED` does not. A skipped opportunity
attack leaves the reaction in hand and a Sneak Attack that could not apply is
still available on the next attack in the same turn.

**Provenance is stamped, never passed.** One `ctx.stamp(source.ID, phase)` before
each call is what makes `ctx.damage.add(...)` attributable with no author effort
(DESIGN.md § Provenance is automatic), and the stamp standing on the way in is
put back on the way out, so a dispatch leaves the context's provenance exactly as
it found it.

**What this module deliberately does not do.** It does not drain
`ctx.pending_effects` — one context is dispatched to two creatures for the paired
hooks (`on_hit` then `on_hit_taken`), and effects queued by either must be
applied once, after both, by the engine that owns the event. It does not
`close()` the context either, for the same reason. And it does not check that the
context it was handed is the one the hook names: that pairing is fixed at import
by `source.py` and pinned by w0f's contract test, and re-deriving it per dispatch
would be paying at 50k/sec for a mistake that cannot survive a single test run.
"""

from collections.abc import Callable
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


class Economy(Protocol):
    """As much of the action economy as the dispatcher needs to see.

    A `Protocol` rather than an import for the reason `hooks/contexts.py` gives
    for `Combatant`: `creatures/economy.py` (w1a) sits above this package and
    imports it, so the dependency can only point one way. `ActionEconomy` will
    satisfy this by having the methods, and w1a owes the contract test that says
    so — the same debt w0g2 owes for the participant protocols.
    """

    def can_spend(self, cost: ActionCost) -> bool: ...

    def spend(self, cost: ActionCost) -> None: ...


class Gate(Protocol):
    """The APL, asked whether a gated handler may act (w2f).

    `gate="rogue.uncanny_dodge"` is how a feature hands the decision of *whether*
    to react to the creature's policy without containing a line of policy itself
    — which is what makes two builds differing only in their action list a
    comparison of action lists.
    """

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool: ...


class BoundHandler:
    """One declaration, bound to the source that will receive it.

    Everything the dispatch loop reads is a slot on this object, flattened out
    of the `Handler` and the `HookSource` at attach time, because the loop runs
    tens of times per attack and an attribute chain is not free. It is also the
    key its own `once_per` budget is filed under in the creature's ledger: bound
    handlers are created once per (source, declaration) and survive every table
    rebuild, so attaching a second source mid-turn cannot hand a spent budget
    back.
    """

    __slots__ = (
        "applied",
        "can_veto",
        "costs",
        "fn",
        "gate",
        "handler",
        "once_per",
        "order",
        "phase",
        "sequence",
        "source",
        "source_id",
        "spec",
    )

    def __init__(self, source: HookSource, handler: Handler, sequence: int) -> None:
        self.source = source
        self.handler = handler
        self.spec = handler.spec
        # Typed as returning `object` deliberately: the dispatcher is the one
        # caller that may not assume a handler honours its own annotation, and
        # `Callable[..., HookResult]` would have the type checker prove the
        # return-value check below unreachable — against the code whose whole
        # job is that it is not.
        self.fn: Callable[..., object] = handler.fn
        self.source_id = type(source).ID
        self.phase = handler.phase
        self.order = handler.order
        self.sequence = sequence
        self.once_per = handler.once_per
        self.costs = handler.costs
        self.gate = handler.gate
        self.can_veto = handler.phase in _VETO_PHASES
        # What the no-op detector reports on (DESIGN.md § No-op detector): a
        # feature whose handlers never once applied is the commonest agent bug
        # in the project — a condition that cannot be true — and counting is
        # cheaper than any test that would find it.
        self.applied = 0

    def sort_key(self) -> tuple[int, int, int]:
        """Phase band, then declared order, then attachment sequence.

        The last of the three is the build YAML's feature order, which is what
        makes a run with two equally-ordered handlers deterministic rather than
        dependent on a dict's history.
        """
        return (self.phase.value, self.order, self.sequence)

    def __repr__(self) -> str:
        return f"<{self.source_id}.{self.handler.name} on {self.spec.name}>"


class Dispatcher:
    """One creature's handlers, and the only thing that calls them.

    Built by the creature (w0g2) with its own reset ledger and, once those
    exist, its action economy (w1a) and action list (w2f). Sources are attached
    in build order; the sequence they were attached in is the last tiebreak in
    the dispatch order, so the YAML is the tiebreak an author can actually see.
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
        """The ledger this dispatcher spends `once_per` budgets against.

        The turn loop (w1f) sweeps it at boundaries; it is exposed here because
        the dispatcher is the only thing that writes to it.
        """
        return self._resets

    def attach(self, source: HookSource) -> None:
        """Add a source's handlers to the table, at the end of the order."""
        if source in self._bound:
            raise ValueError(
                f"{source!r} is already attached to this dispatcher; attaching "
                "it twice would run every one of its handlers twice"
            )
        self._sequence += 1
        sequence = self._sequence
        self._bound[source] = tuple(
            BoundHandler(source, handler, sequence) for handler in source.HANDLERS
        )
        self._rebuild()

    def detach(self, source: HookSource) -> None:
        """Remove a source's handlers — an effect expiring, an item unequipped."""
        if source not in self._bound:
            raise ValueError(f"{source!r} is not attached to this dispatcher")
        del self._bound[source]
        self._rebuild()

    def sources(self) -> tuple[HookSource, ...]:
        """Everything attached, in attachment order."""
        return tuple(self._bound)

    def handlers(self, spec: HookSpec) -> tuple[BoundHandler, ...]:
        """What `dispatch(spec, ...)` would run, in the order it would run them.

        This is what `dndsim inspect` (w2h) prints and what a test asserts
        ordering against, and it is the same tuple the hot path walks rather
        than a reconstruction of it.
        """
        return self._table.get(spec, ())

    def dispatch(self, spec: HookSpec, ctx: Context) -> None:
        """Run every handler for `spec` over `ctx`, in order, until one vetoes.

        The context carries the result: what handlers contributed is in its
        bags, and `ctx.vetoed` says whether the event happens at all.
        """
        handlers = self._table.get(spec)
        if handlers is None:
            return
        if ctx.veto_reason is not None:
            # A veto is a decision about the event, not about one creature's
            # view of it, so the second half of a paired dispatch (`on_hit` then
            # `on_hit_taken`) does not run either. Spelled through the reason
            # rather than `ctx.vetoed` because mypy narrows a property across
            # the calls below, and would then read the in-loop check as dead.
            return
        resets = self._resets
        stamp = ctx.stamp
        was, band = ctx.stamped()
        try:
            for bound in handlers:
                once_per = bound.once_per
                if once_per is not None and resets.is_spent(bound):
                    continue
                cost = bound.costs
                economy = self._economy
                if cost is not None:
                    if economy is None:
                        raise EngineAssemblyError(
                            f"{bound!r} costs a {cost.value} and this dispatcher "
                            "has no action economy to spend it from (w1a)"
                        )
                    if not economy.can_spend(cost):
                        continue
                gate_id = bound.gate
                if gate_id is not None:
                    gate = self._gate
                    if gate is None:
                        raise EngineAssemblyError(
                            f"{bound!r} is gated on {gate_id!r} and this "
                            "dispatcher has no action list to ask (w2f). A gate "
                            "nobody answers is not an open one."
                        )
                    if not gate.allows(gate_id, bound.source, ctx):
                        continue
                stamp(bound.source_id, bound.phase)
                result = bound.fn(bound.source, ctx)
                if result is _APPLIED:
                    bound.applied += 1
                    if once_per is not None:
                        resets.spend(bound, once_per)
                    if cost is not None and economy is not None:
                        # The pair is set together: a cost was checked against
                        # an economy above, or there was no cost to check.
                        economy.spend(cost)
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
            stamp(was, band)

    def _rebuild(self) -> None:
        """Re-sort the table into fresh tuples, leaving any in flight untouched."""
        grouped: dict[HookSpec, list[BoundHandler]] = {}
        for bound_handlers in self._bound.values():
            for bound in bound_handlers:
                grouped.setdefault(bound.spec, []).append(bound)
        self._table = {
            spec: tuple(sorted(bounds, key=BoundHandler.sort_key))
            for spec, bounds in grouped.items()
        }
