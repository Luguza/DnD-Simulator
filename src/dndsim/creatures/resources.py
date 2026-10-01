"""Resources — one model for rage, slots, ki, sorcery points and recharge.

DESIGN.md § Resources: one model, no special cases. The immutable
`ResourceSpec` is the **Flyweight** every creature with that resource shares;
the mutable `ResourcePool` holds the count; `Resources` is the creature's ledger
of pools; `SpellSlots` is the **Facade** over the level-tagged ones; and
`transaction()` is the **Unit of Work** that makes a half-paid ability
impossible.

**A pool is one resource, not the ledger.** DESIGN.md § Resources says the nine
slot pools are nine pools and that `SpellSlots` is a facade over them, so the
container needed a name of its own; `Resources` is it, and `resources.spend` in
§ The authoring skill's mutator families is an attribute on a creature rather
than a class name (`docs/decisions.md` § w1b).

**Overdrawing raises, asking is free.** `EconomyError`, for the reason
`core/errors.py` gives when it calls the action economy the *first* thing that
can be overdrawn: a slot that is not available is a fight outcome and
`can_spend()` answers it for nothing, so reaching the refusal means a caller
decided not to ask (DESIGN.md § Failing loudly — layered).

**`on_resource_spent` fires on the commit and never on the take.** A spend
rolled back inside a transaction did not happen, and `docs/decisions.md` § w1c
records what a handler told about an event that did not take place is worth.

Sources: DESIGN.md § Resources: one model, no special cases, § Failing loudly —
layered; XPHB (2024) "Spells" (spell slots, Pact Magic); ROADMAP w1b, w6a.
"""

import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Final

from dndsim.core.dice import Dice
from dndsim.core.errors import EconomyError, FeatureContractError
from dndsim.creatures.effects import Holder, apply_pending
from dndsim.creatures.stats import StatBlock
from dndsim.hooks.contexts import ResourceSpentContext
from dndsim.hooks.definitions import ON_RESOURCE_SPENT
from dndsim.rules.constants import spell_slots
from dndsim.rules.enums import CasterProgression, RecoveryTrigger

__all__ = [
    "ALL",
    "MAX_SLOT_LEVEL",
    "Recovery",
    "ResourcePool",
    "ResourceSpec",
    "Resources",
    "SpellSlots",
    "Transaction",
    "slot_key",
    "spell_slot_specs",
]

ALL: Final = sys.maxsize
"""The whole pool back, as an amount rather than as a word.

`Recovery(LONG_REST, ALL)` is DESIGN.md § Resources' own notation, and picking a
number no maximum can reach is what lets `recover()` be one `min()` with no
branch in it — the same move as `Recovery.amount` being an `int` and not an
`int | None`."""

MAX_SLOT_LEVEL: Final = 9

_SHORT_REST: Final = RecoveryTrigger.SHORT_REST
_LONG_REST: Final = RecoveryTrigger.LONG_REST


@dataclass(frozen=True, slots=True)
class Recovery:
    """How much of a resource one trigger hands back."""

    trigger: RecoveryTrigger
    amount: int = ALL

    def __post_init__(self) -> None:
        if self.amount < 1:
            raise ValueError(
                f"a recovery of {self.amount} recovers nothing; a resource that "
                "does not come back declares no `Recovery` at all"
            )


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """What a resource *is*: shared by every creature that has it.

    `maximum` is a literal or a function of the stat block, which is what a
    count that scales with level needs and the only reason one `slot:3` spec can
    be shared by every full caster in the run (DESIGN.md § Resources).
    """

    key: str
    label: str
    maximum: int | Callable[[StatBlock], int]
    recovery: tuple[Recovery, ...] = ()
    die: Dice | None = None
    slot_level: int | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("a resource needs a key; it is what a pool is found by")
        if self.slot_level is not None and not 1 <= self.slot_level <= MAX_SLOT_LEVEL:
            raise ValueError(
                f"slot level {self.slot_level} is outside 1-{MAX_SLOT_LEVEL}"
            )

    def maximum_for(self, stats: StatBlock) -> int:
        """How many of this resource the creature with those numbers has."""
        maximum = self.maximum
        return maximum(stats) if callable(maximum) else maximum


class ResourcePool:
    """One resource on one creature: the spec, and what is left of it.

    A counter and nothing else. What fires `on_resource_spent` is `Resources`,
    because a spend inside a transaction is announced when the transaction
    commits and a pool cannot know it is in one.
    """

    __slots__ = ("_current", "maximum", "spec")

    def __init__(self, spec: ResourceSpec, stats: StatBlock) -> None:
        self.spec = spec
        self.maximum = spec.maximum_for(stats)
        self._current = self.maximum

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def current(self) -> int:
        """What is left. Read-only: `_take()`, `recover()` and a transaction's
        rollback are the only writers, and each clamps to `0..maximum`."""
        return self._current

    def can_spend(self, amount: int = 1) -> bool:
        """Whether that many are still there. Asking changes nothing."""
        return 0 < amount <= self.current

    def recover(self, trigger: RecoveryTrigger) -> int:
        """Apply every `Recovery` this trigger fires; return what came back."""
        before = self._current
        for recovery in self.spec.recovery:
            if recovery.trigger is trigger:
                self._current = min(self._current + recovery.amount, self.maximum)
        return self._current - before

    def _take(self, amount: int) -> None:
        if amount <= 0:
            raise EconomyError(
                f"spending {amount} {self.spec.label} is not a spend: an amount "
                "is at least 1, and a negative one would be a refund, which "
                "there is none of (docs/decisions.md § w1b)."
            )
        if amount > self._current:
            raise EconomyError(
                f"spending {amount} {self.spec.label} leaves this creature with "
                f"{self._current - amount}, and a resource may not go negative "
                "(DESIGN.md § Failing loudly — layered). Asking is free: "
                "`can_spend()` answers before anything is spent."
            )
        self._current -= amount

    def _refund(self, amount: int) -> None:
        self._current = min(self._current + amount, self.maximum)

    def __repr__(self) -> str:
        return f"<{self.key} {self.current}/{self.maximum}>"


class Resources:
    """A creature's pools, and the only thing that announces a spend.

    Built with the creature's stat block rather than reading it back off the
    creature, because `Creature.stats` is settled at construction and read-only
    (`docs/decisions.md` § w1a) — so a maximum derived from it cannot go stale,
    and `creatures/` needs no protocol wider than the one `effects.py` declares.
    """

    __slots__ = ("_holder", "_in_transaction", "_pools", "_stats")

    def __init__(self, holder: Holder, stats: StatBlock) -> None:
        self._holder = holder
        self._stats = stats
        self._pools: dict[str, ResourcePool] = {}
        self._in_transaction = False

    def add(self, *specs: ResourceSpec) -> None:
        """Give the creature these resources, full."""
        for spec in specs:
            if spec.key in self._pools:
                raise EconomyError(
                    f"{spec.key} is already a pool on this creature; two pools "
                    "under one key is two places its count could be written"
                )
            self._pools[spec.key] = ResourcePool(spec, self._stats)

    def __contains__(self, key: str) -> bool:
        return key in self._pools

    def __iter__(self) -> Iterator[ResourcePool]:
        return iter(self._pools.values())

    def __len__(self) -> int:
        return len(self._pools)

    def __getitem__(self, key: str) -> ResourcePool:
        pool = self._pools.get(key)
        if pool is None:
            raise EconomyError(
                f"this creature has no {key} to spend. A feature whose resource "
                "the build never granted is a build error, not an empty pool: "
                "an empty pool is something that can come back on a rest."
            )
        return pool

    def can_spend(self, key: str, amount: int = 1) -> bool:
        """Whether `key` exists here with that many left."""
        pool = self._pools.get(key)
        return pool is not None and pool.can_spend(amount)

    def spend(self, key: str, amount: int = 1) -> None:
        """Spend from one pool and announce it, or raise having spent nothing.

        Refused while a transaction is open: this method announces immediately,
        and the rollback can only give back what the transaction took, so the
        spend would survive an exception that un-spent everything beside it —
        the half-paid ability `transaction()` exists to rule out. Inside a
        transaction, `txn.spend()` is the way, and `SpellSlots.select()` is how
        a slot reaches it.
        """
        if self._in_transaction:
            raise EconomyError(
                f"{key} was spent directly while a transaction is open. This "
                "announces at once and the rollback cannot reach it, so an "
                "exception would leave it spent beside costs that came back. "
                "Spend through the transaction — `SpellSlots.select()` names "
                "the pool a slot would come out of "
                "(docs/decisions.md § w1b)."
            )
        self[key]._take(amount)
        self._announce(key, amount)

    def recover(self, trigger: RecoveryTrigger) -> None:
        """Fire one recovery trigger across every pool."""
        for pool in self._pools.values():
            pool.recover(trigger)

    def transaction(self) -> "Transaction":
        """A multi-resource cost, paid whole or not at all.

        Quickened Fireball's 2 sorcery points and 3rd-level slot: both, or
        neither, even if a later step raises (DESIGN.md § Resources).
        """
        return Transaction(self)

    @property
    def spells(self) -> "SpellSlots":
        """The facade over whichever of these pools are spell slots."""
        return SpellSlots(self)

    def _announce(self, key: str, amount: int) -> None:
        holder = self._holder
        ctx = ResourceSpentContext(creature=holder, resource=key, amount=amount)
        holder.dispatch(ON_RESOURCE_SPENT, ctx)
        apply_pending(ctx, holder)
        reason = ctx.veto_reason
        ctx.close()
        if reason is not None:
            raise FeatureContractError(
                f"{reason} vetoed {key} being spent, which cannot be honoured: "
                "the resource is already gone by the time this hook fires. A "
                "use that costs nothing is a `ResourceSpec` with one more in it "
                "or one more `Recovery` on it, declared where the rest of the "
                "resource is; there is no refund, and `pool.current` is not "
                "one (docs/decisions.md § w1b)."
            )

    def __repr__(self) -> str:
        return f"<resources {' '.join(repr(p) for p in self._pools.values())}>"


class Transaction:
    """The open half of `Resources.transaction()`.

    Amounts are taken as they are asked for rather than at the commit, so that a
    second `spend()` sees what the first one left — two 1st-level slots asked of
    a pool holding one is a refusal, and a ledger that only added up at the end
    would let it through.
    """

    __slots__ = ("_open", "_resources", "_taken")

    def __init__(self, resources: Resources) -> None:
        self._resources = resources
        self._taken: dict[str, int] = {}
        self._open = False

    def __enter__(self) -> "Transaction":
        if self._resources._in_transaction:
            raise EconomyError(
                "this creature is already inside a resource transaction. An "
                "outer rollback would un-spend what the inner one has already "
                "announced through `on_resource_spent`, so the refusal is here "
                "rather than in the handler that reads the wrong count "
                "(docs/decisions.md § w1b)."
            )
        self._resources._in_transaction = True
        self._open = True
        return self

    def spend(self, key: str, amount: int = 1) -> None:
        """Take from a pool, provisionally."""
        if not self._open:
            raise EconomyError(
                "this transaction has not been entered, so nothing would commit "
                "it and `on_resource_spent` would never fire. A transaction is "
                "a `with` statement."
            )
        self._resources[key]._take(amount)
        self._taken[key] = self._taken.get(key, 0) + amount

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        resources = self._resources
        resources._in_transaction = False
        self._open = False
        taken, self._taken = self._taken, {}
        if exc_type is not None:
            for key, amount in taken.items():
                resources[key]._refund(amount)
            return
        for key, amount in taken.items():
            resources._announce(key, amount)


class SpellSlots:
    """The queries a caster and an APL actually ask, over the level-tagged pools.

    A slot is any pool whose spec declares a `slot_level`, which is what lets
    Pact Magic answer these four questions beside the other three progressions
    without a caller branching on which it is (`rules/constants.py`).
    """

    __slots__ = ("_resources",)

    def __init__(self, resources: Resources) -> None:
        self._resources = resources

    def highest(self) -> int:
        """The highest level with a slot left, or 0 for a caster out of them."""
        return max((lvl for pool, lvl in self._slots() if pool.current), default=0)

    def count(self, level: int) -> int:
        """Slots left at exactly that level, summed over every pool at it."""
        return sum(pool.current for pool, lvl in self._slots() if lvl == level)

    def total_above(self, level: int) -> int:
        """Slots left at that level or higher — what "can I still upcast" asks."""
        return sum(pool.current for pool, lvl in self._slots() if lvl >= level)

    def select(self, level: int, *, allow_upcast: bool = False) -> ResourcePool:
        """Which pool a spell of `level` would come out of, without spending it.

        Separate from `spend()` because a slot is half of a multi-resource cost
        — Quickened Fireball's other half is two sorcery points — and only the
        transaction may take it if the two are to commit together.
        """
        return self._select(level, allow_upcast=allow_upcast)[0]

    def spend(self, level: int, *, allow_upcast: bool = False) -> int:
        """Spend a slot for a spell of `level`; return the level actually spent.

        `allow_upcast` takes the lowest slot that will carry the spell rather
        than the level asked for, which is the caster's own choice and never the
        engine's — the returned level is what the damage then scales off.
        """
        pool, slot_level = self._select(level, allow_upcast=allow_upcast)
        self._resources.spend(pool.key)
        return slot_level

    def _select(self, level: int, *, allow_upcast: bool) -> tuple[ResourcePool, int]:
        if not 1 <= level <= MAX_SLOT_LEVEL:
            raise EconomyError(
                f"there is no level-{level} spell slot to spend (1-{MAX_SLOT_LEVEL})"
            )
        top = MAX_SLOT_LEVEL if allow_upcast else level
        for candidate in range(level, top + 1):
            for pool, lvl in self._slots():
                if lvl == candidate and pool.current:
                    return pool, lvl
        raise EconomyError(
            f"this creature has no level-{level} spell slot left"
            + ("" if allow_upcast else " and was not asked to upcast")
            + ". Asking is free: `count()` and `total_above()` answer first."
        )

    def _slots(self) -> Iterator[tuple[ResourcePool, int]]:
        for pool in self._resources:
            level = pool.spec.slot_level
            if level is not None:
                yield pool, level

    def __repr__(self) -> str:
        return f"<slots {' '.join(repr(pool) for pool, _ in self._slots())}>"


def slot_key(progression: CasterProgression, level: int) -> str:
    """The pool key for one slot level of one progression.

    Pact Magic is keyed apart from the other three because the key is all
    `on_resource_spent` hands a handler, and a Pact slot is a different resource
    from a spell slot: it comes back on a Short Rest.
    """
    prefix = "pact" if progression is CasterProgression.PACT else "slot"
    return f"{prefix}:{level}"


def spell_slot_specs(progression: CasterProgression) -> tuple[ResourceSpec, ...]:
    """The nine shared slot specs for one progression.

    Nine per progression for the whole run, which is the Flyweight claim made
    real: the count is a function of the stat block, so a level-3 warlock and a
    level-20 one read their different numbers off the same `pact:2` spec. Levels
    the progression never reaches resolve to a maximum of 0 rather than being
    left out, so levelling up needs no pool added.
    """
    return _SLOT_SPECS[progression]


def _slot_spec(progression: CasterProgression, level: int) -> ResourceSpec:
    pact = progression is CasterProgression.PACT
    return ResourceSpec(
        key=slot_key(progression, level),
        label=f"level-{level} {'Pact Magic slot' if pact else 'spell slot'}",
        maximum=lambda stats: spell_slots(progression, stats.level).get(level, 0),
        recovery=(Recovery(_SHORT_REST if pact else _LONG_REST, ALL),),
        slot_level=level,
    )


_SLOT_SPECS: Final[dict[CasterProgression, tuple[ResourceSpec, ...]]] = {
    progression: tuple(
        _slot_spec(progression, level) for level in range(1, MAX_SLOT_LEVEL + 1)
    )
    for progression in CasterProgression
}
