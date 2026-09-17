"""`Effect` and `EffectStore` — what is attached to a creature, and for how long.

An `Effect` is a `HookSource` like a feature, a condition or an item
(DESIGN.md § Durations: "one API shape for all four"). What this module adds on
top of the attachment is the three ways one *ends*: a deadline, a number of
charges, and a second application that displaces it.

**Ending is the store's job, never the effect's.** An effect that removed itself
would be a second place attachment order is decided, and `hooks/dispatch.py`
rebuilds a creature's dispatch table on every attach and detach — so the store
owns both halves and an effect never touches `Creature.attach()`.

**Stacking is declared, not re-implemented** (DESIGN.md § Durations). `Stacking`
is a **Strategy** read off the class: an author says whether a second casting
refreshes, stacks, is one-per-caster or is refused, and `apply()` is the only
code that acts on the answer.

**The store keeps the clock it was last told, and never advances it.** A
`Duration` resolves against the moment it is applied (`core/timeline.py`), and
the moment an effect is applied is usually inside an attack — where no context
carries a `TimeKey` and `resolve_attack()` is handed none. So the turn loop
(`engine/turn.py`, w2c), which owns the clock, passes it to each sweep, and the
store remembers it for the applications that land between two boundaries. That
is a copy of the one fact, written only by the loop that owns it, and not the
second authority `core/timeline.py` refuses; nothing here reads a clock it was
not given. Until w2c exists nothing calls the sweeps at all — debt #29.

**The drain matches a queued effect's target against the event's participants.**
A handler names a `Combatant`, which is a read-only view and has no store behind
it; the engine holds the real creatures. Identity against the two participants
is complete for the events that drain here — every handler that can queue during
an attack or an instance of damage is owned by the attacker or the defender —
and the general answer is w2c's roster (debt #30).

Not here: which effects the conditions are (`w1h`), the resources an effect
spends (`w1b`), and the turn loop that sweeps them (`w2c`).

Sources: DESIGN.md § Durations, § Structural prevention; `core/timeline.py`;
ROADMAP w1c.
"""

from collections.abc import Iterator
from enum import Enum, unique
from typing import ClassVar, Protocol

from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.timeline import Duration, Expiry, TimeKey
from dndsim.hooks.contexts import Combatant, Context, EffectContext
from dndsim.hooks.definitions import ON_EFFECT_APPLIED, ON_EFFECT_EXPIRED
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource

__all__ = ["Effect", "EffectStore", "Holder", "Stacking", "apply_pending"]


@unique
class Stacking(Enum):
    """What a second application of the same effect does to the first.

    The closed set DESIGN.md § Durations names. `STACK` is the only member under
    which a creature holds two of one effect at once, which is why it is the
    member an author has to choose deliberately rather than the default.
    """

    REFRESH = "refresh"
    """The incumbent's deadline is recomputed and nothing new attaches — Rage
    re-entered, a Frightened re-applied by the same dragon."""

    STACK = "stack"
    """Both are held, independently, each with its own deadline and charges."""

    UNIQUE_PER_SOURCE = "unique_per_source"
    """One per caster: a second Hunter's Mark from the same ranger refreshes,
    one from a different ranger attaches beside it."""

    REJECT = "reject"
    """A second application does nothing at all and says so by landing
    nothing."""


class Holder(Combatant, Protocol):
    """A creature as its own effects need it: a `Combatant` that can be attached
    to and holds a store.

    Structural rather than an import of `Creature`: `creatures/creature.py`
    owns the store, so naming the class here would be a cycle. Widening
    `Combatant` rather than restating it is what lets `apply_pending()` compare
    a queued effect's target against the participants the engine already holds.
    """

    @property
    def effects(self) -> "EffectStore": ...

    def attach(self, source: HookSource) -> None: ...

    def detach(self, source: HookSource) -> None: ...

    def dispatch(self, spec: HookSpec, ctx: Context) -> None: ...


class Effect(HookSource):
    """Something attached to a creature that ends.

    `duration` and `charges` are independent and either may be absent: Rage ends
    on a deadline, a once-per-combat rider ends on its charge, Vex carries both
    and ends on whichever runs out first (DESIGN.md § Durations). An effect with
    neither lasts until something removes it, which is what Prone is — the
    rules' "until you stand up" is not a deadline (`core/timeline.py`).

    The deadline is a `Duration` here and an `Expiry` once applied, because a
    duration is relative and only the store knows what the clock reads.
    """

    __slots__ = ("_store", "caster", "charges", "duration", "expiry")

    STACKING: ClassVar[Stacking] = Stacking.REFRESH
    """What a second application does. Declared per class (`Stacking`); the
    default is the one that cannot produce a creature holding two of something
    by accident."""

    def __init__(
        self,
        owner: Combatant,
        *,
        duration: Duration | None = None,
        charges: int | None = None,
        caster: Combatant | None = None,
    ) -> None:
        super().__init__(owner)
        if charges is not None and charges < 1:
            raise ValueError(
                f"{type(self).ID} was applied with {charges} charges, which is "
                "an effect that is over before it starts; charges=None is how "
                "an effect that does not count them is written"
            )
        if self.STACKING is Stacking.UNIQUE_PER_SOURCE and caster is None:
            raise ValueError(
                f"{type(self).ID} stacks UNIQUE_PER_SOURCE and was applied with "
                "no caster, so there is no source for it to be unique per"
            )
        self.duration = duration
        self.charges = charges
        self.caster = caster
        self.expiry: Expiry | None = None
        self._store: EffectStore | None = None

    @property
    def name(self) -> str:
        """Satisfies the `Effect` protocol `hooks/contexts.py` declares.

        The provenance id rather than a second string: it is already what the
        trace, the damage breakdown and the no-op report call this effect, and a
        display name beside it is one more thing that can disagree.
        """
        return type(self).ID

    @property
    def key(self) -> object:
        """What a second application is compared against.

        The class under every policy but `UNIQUE_PER_SOURCE`, which adds the
        caster — so two rangers' marks are two effects and one ranger's second
        mark is one.
        """
        if self.STACKING is Stacking.UNIQUE_PER_SOURCE:
            return (type(self), self.caster)
        return type(self)

    def refresh(self) -> None:
        """Recompute the deadline against the clock as it reads now.

        Rage's extension (DESIGN.md § Durations): the effect was applied with
        "until the end of your next turn" and saying it again slides the
        deadline forward rather than attaching a second Rage.
        """
        store = self._require_store("refreshed")
        duration = self.duration
        self.expiry = None if duration is None else duration.resolve(store.now)

    def consume(self, charges: int = 1) -> int:
        """Spend charges, ending the effect when the last one goes.

        Returns what is left. Spending more than remain is a caller that did not
        ask — `charges` is readable — so it raises rather than clamping, the
        same bargain `ActionEconomy` makes.
        """
        store = self._require_store("consumed")
        if self.charges is None:
            raise ValueError(
                f"{type(self).ID} counts no charges, so there is nothing to "
                "consume; it ends on its deadline or when something removes it"
            )
        if charges < 1 or charges > self.charges:
            raise ValueError(
                f"{type(self).ID} has {self.charges} charge(s) and {charges} "
                "were consumed"
            )
        self.charges -= charges
        if not self.charges:
            store.remove(self)
        return self.charges

    def _require_store(self, verb: str) -> "EffectStore":
        store = self._store
        if store is None:
            raise EngineAssemblyError(
                f"{type(self).ID} was {verb} while attached to nothing. An "
                "effect is applied through a creature's EffectStore, which is "
                "what holds the clock this reads and the creature it detaches "
                "from."
            )
        return store


class EffectStore:
    """One creature's attached effects: what is on it, and what ends when.

    One per creature, like `ActionEconomy` and `ResetManager` and for the same
    reason: every question this answers is about one creature, and a global
    ledger keyed by owner would put a lookup the size of the encounter in front
    of each of them.

    Attaching and detaching go through the creature rather than the dispatcher
    so that `Creature.attach()`'s ownership check stands for an effect too — an
    effect whose owner is somebody else would run its handlers for the wrong
    creature, which is what hook `Scope` exists to make impossible.
    """

    __slots__ = ("_held", "_holder", "_now")

    def __init__(self, holder: Holder, *, now: TimeKey) -> None:
        self._holder = holder
        self._held: list[Effect] = []
        self._now = now

    @property
    def now(self) -> TimeKey:
        """The clock as this store was last told it reads.

        Written by the sweeps below and by nothing else, so the turn loop that
        owns the clock is the only thing that can move it.
        """
        return self._now

    def __iter__(self) -> Iterator[Effect]:
        return iter(self._held)

    def __len__(self) -> int:
        return len(self._held)

    def apply(self, effect: Effect) -> Effect | None:
        """Put `effect` on this creature, and return what is in force.

        The return is the incumbent when one absorbed the application and
        `None` when it did not land — `REJECT` refusing it, or an
        `on_effect_applied` handler vetoing it.

        **What is already held is asked twice**, before the dispatch and after
        it, because the dispatch is where the answer can change: a handler may
        dispel the incumbent, leaving a remembered one that no longer belongs to
        anybody, or apply one of its own, leaving a remembered `None` that would
        attach a second `REFRESH` effect of a class that may hold only one.
        """
        if self._incumbent(effect) is not None and effect.STACKING is Stacking.REJECT:
            return None
        if not self._announce(ON_EFFECT_APPLIED, effect, vetoable=True):
            return None
        incumbent = self._incumbent(effect)
        if incumbent is None:
            self._holder.attach(effect)
            effect._store = self
            effect.refresh()
            self._held.append(effect)
            return effect
        if effect.STACKING is Stacking.REJECT:
            return None
        incumbent.duration = effect.duration
        incumbent.charges = effect.charges
        incumbent.refresh()
        return incumbent

    def remove(self, effect: Effect) -> None:
        """Take `effect` off this creature — expired, consumed, or dispelled.

        **The store lets go before the dispatch and the dispatcher after it**,
        which is what makes a removal happen once. An `on_effect_expired`
        handler may remove something, itself included, and reaching that handler
        with the effect still held would either fire the hook a second time or
        leave the second call to die inside `list.remove`. So the store's
        bookkeeping is settled first, and this is the state a handler sees: the
        creature no longer holds the effect, and the effect still answers, which
        is the last word it gets about its own ending.
        """
        if effect._store is not self:
            raise ValueError(
                f"{effect.name} is not attached to {self._holder.name} and "
                "cannot be removed from it — it has expired already, or it "
                "belongs to somebody else"
            )
        self._held.remove(effect)
        effect._store = None
        self._announce(ON_EFFECT_EXPIRED, effect, vetoable=False)
        self._holder.detach(effect)

    def expire_before(self, now: TimeKey) -> None:
        """Drop everything due to end *before* `now`'s handlers run.

        Reckless Attack's half of `Expiry.through_phase`: the advantage it gave
        the enemies is gone before the owner's own `on_turn_start` handlers see
        it.
        """
        self._sweep(now, through=False)

    def expire_after(self, now: TimeKey) -> None:
        """Drop everything due to end once `now`'s handlers have run.

        Rage's half: it is still attached while the owner's `on_turn_end`
        handlers run, so damage taken during that dispatch is still resisted.
        """
        self._sweep(now, through=True)

    def _sweep(self, now: TimeKey, *, through: bool) -> None:
        self._now = now
        if not self._held:
            return
        expired = [
            held
            for held in self._held
            if held.expiry is not None and _is_over(held.expiry, now, through)
        ]
        for effect in expired:
            # An earlier effect's `on_effect_expired` handler may have removed a
            # later one — dispelling it, or ending a chain — and the snapshot
            # above cannot know. Removing it twice is what fires its hook twice.
            if effect._store is self:
                self.remove(effect)

    def _announce(self, spec: HookSpec, effect: Effect, *, vetoable: bool) -> bool:
        """Dispatch one of the two effect hooks, and say whether to go ahead.

        Both fire between the store's bookkeeping and the dispatcher's:
        `on_effect_applied` before the attach, `on_effect_expired` after the
        `remove()` and before the detach. So an effect's own handlers run for
        exactly the span it is attached — it cannot answer its own arrival,
        which is the snapshot rule (`hooks/contexts.py`), and it can answer its
        own ending, which is the only moment left to.

        A veto is honoured on the application, because an effect is "on its way
        onto a creature" until this hook has run and refusing one is what a
        feature immune to it does. It is refused on the expiry, where it has no
        meaning and never will have one — the same split `engine/attack.py`
        makes at `_seal()`, and the reason the two raises there are different
        types.
        """
        ctx = EffectContext(creature=self._holder, effect=effect)
        self._holder.dispatch(spec, ctx)
        apply_pending(ctx, self._holder)
        reason = ctx.veto_reason
        ctx.close()
        if reason is not None and not vetoable:
            raise FeatureContractError(
                f"{reason} vetoed {effect.name} expiring, which cannot be "
                "honoured: a deadline is a position on the clock and the clock "
                "has passed it. A feature that means to keep an effect alive "
                "refreshes it."
            )
        return reason is None

    def _incumbent(self, effect: Effect) -> Effect | None:
        if effect.STACKING is Stacking.STACK:
            return None
        key = effect.key
        return next((held for held in self._held if held.key == key), None)


def _is_over(expiry: Expiry, now: TimeKey, through: bool) -> bool:
    return expiry.expires_after(now) if through else expiry.expires_before(now)


def apply_pending(ctx: Context, *among: Holder | None) -> None:
    """Apply everything `ctx`'s handlers queued, to the creature each named.

    The drain `hooks/contexts.py` promises — "the engine drains the queue after
    the dispatch" — and the replacement for the refusal `w0g2` and `w1f` left in
    its place (debt #5). It reads the private list rather than
    `ctx.pending_effects`, which builds a tuple, for the sake of the two callers
    that cannot test it themselves: `resolve_damage()` calls this on every
    instance of damage, where the list is empty almost every time. The attack's
    six call sites test the same list and call only when there is something to
    apply, which is why the callers do not read alike.

    `among` is the participants of the event the caller owns. A queued effect
    names a `Combatant`, which carries no store, so the target is matched by
    identity against them; a target that is neither is refused rather than
    dropped, and w2c's roster is what makes it answerable (debt #30).
    """
    pending = ctx._pending
    if not pending:
        return
    for target, queued in pending:
        holder = next((one for one in among if one is target), None)
        if holder is None:
            raise EngineAssemblyError(
                f"{type(ctx).__name__} queued {queued.name} onto "
                f"{target.name}, which is not one of this event's participants. "
                "Only w2c's roster can find a creature the event does not "
                "carry (debt #30)."
            )
        if not isinstance(queued, Effect):
            raise FeatureContractError(
                f"{type(ctx).__name__} queued {queued.name}, which is not an "
                "Effect. `hooks/contexts.py`'s protocol asks only for a name, "
                "because that is all a context reads; what lands on a creature "
                "is `creatures/effects.py`'s class."
            )
        holder.effects.apply(queued)
    pending.clear()
