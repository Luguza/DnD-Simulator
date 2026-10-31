"""`Effect` and `EffectStore` — what is attached to a creature, and for how long.

An `Effect` is a `HookSource` that ends, on a deadline or on its last charge;
what a second application does is its declared `Stacking`. The store attaches
and detaches through the creature and keeps the clock it was last told by a
sweep; `apply_pending()` drains a context's queued effects onto the event's
participants. A condition's gain and loss are announced as well
(`docs/decisions.md` § w1c, § w2b; debts #29, #30).

DESIGN.md § Durations.
"""

from collections.abc import Iterator
from enum import Enum, unique
from typing import ClassVar, Protocol

from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.timeline import Duration, Expiry, TimeKey
from dndsim.hooks.contexts import (
    Combatant,
    ConditionContext,
    Context,
    EffectContext,
)
from dndsim.hooks.definitions import (
    ON_CONDITION_GAINED,
    ON_CONDITION_LOST,
    ON_EFFECT_APPLIED,
    ON_EFFECT_EXPIRED,
)
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Condition

__all__ = ["Effect", "EffectStore", "Holder", "Stacking", "apply_pending"]


@unique
class Stacking(Enum):
    """What a second application of the same effect does to the first.

    The closed set DESIGN.md § Durations names. `STACK` is the only member under
    which a creature holds two of one effect at once.
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

    A protocol because importing `Creature` here would be a cycle.
    """

    @property
    def effects(self) -> "EffectStore": ...

    def attach(self, source: HookSource) -> None: ...

    def detach(self, source: HookSource) -> None: ...

    def dispatch(self, spec: HookSpec, ctx: Context) -> None: ...

    def drop_held(self) -> None: ...


class Effect(HookSource):
    """Something attached to a creature that ends.

    `duration` and `charges` are independent and either may be absent; with
    both it ends on whichever runs out first, and with neither it lasts until
    something removes it (DESIGN.md § Durations).

    `expiry` is `None` until applied: the store resolves `duration` against
    its clock.
    """

    __slots__ = (
        "_resolved_at",
        "_resolved_for",
        "_store",
        "caster",
        "charges",
        "duration",
        "expiry",
    )

    STACKING: ClassVar[Stacking] = Stacking.REFRESH
    """What a second application does. Declared per class (`Stacking`)."""

    CONDITION: ClassVar[Condition | None] = None
    """The condition this effect is the behaviour of, or `None` for an effect
    that is not one; `conditions/` declares it (`docs/decisions.md` § w2b)."""

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
        # What `expiry` was last resolved from; see `refresh()`.
        self._resolved_at: TimeKey | None = None
        self._resolved_for: Duration | None = None

    @property
    def name(self) -> str:
        """The class's provenance `ID`; satisfies the `Effect` protocol."""
        return type(self).ID

    @property
    def key(self) -> object:
        """What a second application is compared against.

        The class under every policy but `UNIQUE_PER_SOURCE`, which adds the
        caster.
        """
        if self.STACKING is Stacking.UNIQUE_PER_SOURCE:
            return (type(self), self.caster)
        return type(self)

    def refresh(self) -> None:
        """Recompute the deadline against the store's clock as it reads now.
        Raises `EngineAssemblyError` if the effect is not attached."""
        store = self._require_store("refreshed")
        duration = self.duration
        now = store.now
        # The same duration against the same clock resolves to the same expiry,
        # and a store's clock moves only by a sweep replacing it.
        if now is self._resolved_at and duration is self._resolved_for:
            return
        self._resolved_at = now
        self._resolved_for = duration
        self.expiry = None if duration is None else duration.resolve(now)

    def consume(self, charges: int = 1) -> int:
        """Spend charges, ending the effect when the last one goes.

        Returns what is left. Raises `ValueError` for an effect without charges
        or for more charges than remain.
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

    One per creature. Attaching and detaching go through the holder, so its
    ownership check applies to an effect too (`docs/decisions.md` § w1c).
    """

    __slots__ = ("_held", "_holder", "_now")

    def __init__(self, holder: Holder, *, now: TimeKey) -> None:
        self._holder = holder
        self._held: list[Effect] = []
        self._now = now

    @property
    def now(self) -> TimeKey:
        """The clock as this store was last told it reads. Written only by the
        sweeps below."""
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

        The incumbent is looked up again after the dispatch, which may have
        removed or applied one (`docs/decisions.md` § w1c).
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
            condition = effect.CONDITION
            if condition is not None and not self._holds(condition, besides=effect):
                self._announce_condition(ON_CONDITION_GAINED, condition)
            return effect
        if effect.STACKING is Stacking.REJECT:
            return None
        incumbent.duration = effect.duration
        incumbent.charges = effect.charges
        incumbent.refresh()
        return incumbent

    def remove(self, effect: Effect) -> None:
        """Take `effect` off this creature — expired, consumed, or dispelled.

        Ordering: the store lets go before `on_effect_expired` fires and the
        creature detaches after it, so a handler sees the effect no longer held
        but its own handlers still attached (`docs/decisions.md` § w1c). A
        condition's loss is announced after the detach. Raises `ValueError` for
        an effect this store does not hold.
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
        condition = effect.CONDITION
        if condition is not None and not self._holds(condition, besides=None):
            self._announce_condition(ON_CONDITION_LOST, condition)

    def expire_before(self, now: TimeKey) -> None:
        """Drop everything due to end *before* `now`'s handlers run, and set
        the store's clock to `now`."""
        self._sweep(now, through=False)

    def expire_after(self, now: TimeKey) -> None:
        """Drop everything due to end once `now`'s handlers have run, and set
        the store's clock to `now`."""
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
            # An earlier effect's `on_effect_expired` handler may have removed
            # this one already; removing it twice fires its hook twice.
            if effect._store is self:
                self.remove(effect)

    def _announce(self, spec: HookSpec, effect: Effect, *, vetoable: bool) -> bool:
        """Dispatch one of the two effect hooks, and say whether to go ahead.

        Returns `False` when a vetoable dispatch was vetoed; a veto of a
        non-vetoable one raises `FeatureContractError` (`docs/decisions.md`
        § w1c).
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

    def _holds(self, condition: Condition, *, besides: Effect | None) -> bool:
        """Whether an effect other than `besides` already carries `condition`.

        A gain or a loss is of the condition, not of one effect carrying it
        (`docs/decisions.md` § w2b).
        """
        return any(
            held.CONDITION is condition and held is not besides for held in self._held
        )

    def _announce_condition(self, spec: HookSpec, condition: Condition) -> None:
        """Dispatch `on_condition_gained` or `on_condition_lost`, then apply
        what it queued and drop what it said to drop.

        Neither hook is vetoable: a veto raises `FeatureContractError`
        (`docs/decisions.md` § w2b).
        """
        holder = self._holder
        ctx = ConditionContext(creature=holder, condition=condition)
        holder.dispatch(spec, ctx)
        reason = ctx.veto_reason
        drops = ctx.drops_held
        if reason is not None:
            ctx.close()
            raise FeatureContractError(
                f"{reason} vetoed {spec.name} for {condition.value}, which "
                "reports a condition already held or already gone. A feature "
                "that means to stop a condition landing vetoes "
                "on_effect_applied."
            )
        apply_pending(ctx, holder)
        ctx.close()
        if drops:
            holder.drop_held()

    def _incumbent(self, effect: Effect) -> Effect | None:
        if effect.STACKING is Stacking.STACK:
            return None
        key = effect.key
        return next((held for held in self._held if held.key == key), None)


def _is_over(expiry: Expiry, now: TimeKey, through: bool) -> bool:
    return expiry.expires_after(now) if through else expiry.expires_before(now)


def apply_pending(ctx: Context, *among: Holder | None) -> None:
    """Apply everything `ctx`'s handlers queued, to the creature each named.

    `among` is the participants of the event the caller owns; each queued
    target is matched by identity against them. Raises `EngineAssemblyError`
    for a target that is none of them (debt #30) and `FeatureContractError` for
    a queued item that is not an `Effect`. Clears the queue.
    """
    # The private list: `ctx.pending_effects` builds a tuple per call, and most
    # calls find the queue empty.
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
