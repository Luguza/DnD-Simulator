"""The APL, hand-compiled — the one stand-in the performance spike is allowed.

ROADMAP w0i asks for "an 8-condition APL with `target:` and `move:`
**hand-compiled to callables over the namespace-proxy shape `w2e` will emit**",
and is explicit about why that is a budget rather than a stub: `w2e` re-runs
this benchmark when the real compiler lands and must come in at or under the
same number. So everything here is written to be the *shape* the compiler emits,
not a shortcut around it.

**What w2e will emit, and what this file therefore is.** DESIGN.md § Condition
language compiles each `if:`, `target:` and `move:` clause to a **Specification**
— a reusable predicate over domain objects — after resolving every dotted path
against a `NamespaceSchema` at load time. The namespace roots (`self`, `target`,
`trigger`, `enemies`, `allies`) are **Proxy** objects built once per creature
holding weak references to live state, "so evaluation is attribute access rather
than object construction". Two consequences are load-bearing for the
measurement:

- **A compiled clause is a callable of one argument, the namespace.** `self`,
  `target` and the rest are attributes of it, so `self.raging` compiles to
  `ns.self.raging` — one attribute chain, no dict lookup, no re-binding per
  evaluation. `self` is a perfectly ordinary identifier in Python, which is what
  lets the emitted code read like the YAML it came from.
- **The proxies hold weak references.** A namespace outlives no creature and
  keeps none alive; the deref is a call on the hot path and is deliberately in
  the measurement. It is also why `Creature` carries `__weakref__`.

**`trigger.*` is populated by the engine, never scraped off a context.**
DESIGN.md § Module layout puts that job in `engine/triggers.py`: "which event
fires an offer, who is in the audience, in what order… The name matches the
APL's `trigger.*` namespace root, which this module populates." So the spike's
attack sequence binds `ns.trigger` before it dispatches a hook with gated
handlers, and `allows()` evaluates a condition over the namespace rather than
introspecting the context it was handed. A gate that had to switch on context
type would be an `isinstance` chain on the hottest path in the project.

**Unmentioned is OFF.** `allows()` on a gate id no entry claims returns `False`
— the **Null Object** default policy of DESIGN.md § APL, so attaching a gated
feature never silently changes a build's numbers.

Sources: DESIGN.md § APL and mid-attack decisions, § Condition language,
§ Module layout; ROADMAP w0i, w2e.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeAlias
from weakref import ReferenceType, ref

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft, in_reach
from dndsim.creatures.creature import Creature
from dndsim.hooks.contexts import Context
from dndsim.hooks.source import HookSource

__all__ = [
    "AlliesProxy",
    "Apl",
    "EnemiesProxy",
    "Entry",
    "Namespace",
    "SelfProxy",
    "SpikeState",
    "TargetProxy",
    "TriggerProxy",
    "first",
    "lowest_hp",
    "nearest",
    "toward_until_in_reach",
]


def _live(reference: "ReferenceType[Creature]") -> Creature:
    """Deref a proxy's weak reference, or say which namespace outlived its fight.

    The branch is on the hot path and stays there: DESIGN.md § Condition
    language specifies weak references, and a spike that held strong ones would
    measure a proxy nobody is going to ship.
    """
    creature = reference()
    if creature is None:
        raise EngineAssemblyError(
            "an APL namespace outlived the creature it was built for; a "
            "namespace is compiled per build and lives exactly as long as the "
            "encounter that uses it"
        )
    return creature


class SelfProxy:
    """The `self.*` root: the acting creature, and the state features hold.

    `raging` and `slots.highest` are the two paths DESIGN.md's example APL reads
    that this benchmark still answers from a stand-in (debt #15) — `w1c` landed
    the `EffectStore` and `w1b` has the resources. The proxy is built over the
    object that holds them rather than over a flag on the creature, which is
    where `w2e`'s schema will resolve them to as well: a proxy answers a dotted
    path out of somebody else's state.
    """

    __slots__ = ("_ref", "_state")

    def __init__(self, creature: Creature, state: "SpikeState") -> None:
        self._ref: ReferenceType[Creature] = ref(creature)
        self._state = state

    @property
    def hp(self) -> int:
        return _live(self._ref).hp

    @property
    def hp_pct(self) -> float:
        creature = _live(self._ref)
        return creature.hp / creature.max_hp

    @property
    def position(self) -> Cell:
        return _live(self._ref).position

    @property
    def reach_ft(self) -> int:
        return _live(self._ref).reach_ft

    @property
    def raging(self) -> bool:
        return self._state.raging

    @property
    def slots_highest(self) -> int:
        """`self.slots.highest` — one path, one attribute, no sub-proxy.

        The dotted name in the YAML is `self.slots.highest`; what the schema
        resolves it to is a single lookup, because a `slots` sub-proxy would be
        an object construction per evaluation on the path § Condition language
        says is attribute access.
        """
        return self._state.slots_highest


class TargetProxy:
    """The `target.*` root: whoever the current offer is against.

    Rebound by the engine per event rather than held weakly, because it *is* the
    event's subject — the creature that triggered a reaction, or the one an
    attack was declared against (DESIGN.md § APL, on the default `target:`).
    """

    __slots__ = ("_creature",)

    def __init__(self) -> None:
        self._creature: Creature | None = None

    def bind(self, creature: Creature | None) -> None:
        self._creature = creature

    @property
    def ac(self) -> int:
        return 0 if self._creature is None else self._creature.armour_class

    @property
    def hp(self) -> int:
        return 0 if self._creature is None else self._creature.hp


class TriggerProxy:
    """The `trigger.*` root: what the engine is offering, right now.

    Populated by the engine before it dispatches a hook carrying gated handlers
    (DESIGN.md § Module layout). Two fields, because two of the eight entries
    read them: Divine Smite reads `trigger.crit` and Uncanny Dodge reads
    `trigger.damage_estimate`.
    """

    __slots__ = ("crit", "damage_estimate")

    def __init__(self) -> None:
        self.crit = False
        self.damage_estimate = 0

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None:
        self.crit = crit
        self.damage_estimate = damage_estimate


class EnemiesProxy:
    """The `enemies.*` root: everything hostile, as a set of live queries.

    `within(ft)` and `in_reach` are the two the example APL uses, and both are
    real geometry over real positions — ROADMAP w0i asks for "real grid
    movement… so distance and reach queries are in the measurement rather than
    optimised away", and a proxy answering from a cached count would optimise
    away exactly the query being bought.
    """

    __slots__ = ("_ref", "_side")

    def __init__(self, owner: Creature, side: Sequence[Creature]) -> None:
        self._ref: ReferenceType[Creature] = ref(owner)
        self._side = side

    def within(self, feet: int) -> int:
        origin = _live(self._ref).position
        return sum(
            1
            for c in self._side
            if c.is_alive and distance_ft(origin, c.position) <= feet
        )

    @property
    def alive(self) -> tuple[Creature, ...]:
        return tuple(c for c in self._side if c.is_alive)

    @property
    def in_reach(self) -> tuple[Creature, ...]:
        owner = _live(self._ref)
        origin = owner.position
        reach = owner.reach_ft
        return tuple(
            c for c in self._side if c.is_alive and in_reach(origin, c.position, reach)
        )


class AlliesProxy:
    """The `allies.*` root. `allies.down` is what Healing Word's entry reads."""

    __slots__ = ("_side",)

    def __init__(self, side: Sequence[Creature]) -> None:
        self._side = side

    @property
    def down(self) -> tuple[Creature, ...]:
        return tuple(c for c in self._side if not c.is_alive)


class Namespace:
    """The five roots, built once per creature, passed to every compiled clause.

    Slotted and assembled at load time: a namespace built per evaluation would
    make the "attribute access rather than object construction" sentence in
    DESIGN.md false at the one place it is measured.
    """

    __slots__ = ("allies", "enemies", "self", "target", "trigger")

    def __init__(
        self,
        *,
        own: SelfProxy,
        enemies: EnemiesProxy,
        allies: AlliesProxy,
    ) -> None:
        # `self.self` reads oddly and is exactly right: the attribute is the
        # namespace root the APL author writes, and naming it anything else
        # would make the emitted code stop looking like the YAML it came from.
        self.self = own
        self.enemies = enemies
        self.allies = allies
        self.target = TargetProxy()
        self.trigger = TriggerProxy()


class SpikeState:
    """The sliver of `w1b` and `w1c` the namespace and the fixtures share.

    `self.raging` is a question about an `Effect` and `self.slots.highest` a
    question about a `ResourcePool` (w1b), and the benchmark asks neither of the
    real thing — `w1k` is where it does (debt #15). Rather than
    scatter stand-in flags across twenty-five fixture classes, both live in one
    object that the features write and the namespace reads — which is also the
    shape the real thing has, since a proxy resolves a dotted path to somebody
    else's state rather than holding its own.
    """

    __slots__ = ("marked", "raging", "slots_highest")

    def __init__(self) -> None:
        self.raging = False
        self.slots_highest = 5
        self.marked: str | None = None


Condition: TypeAlias = Callable[[Namespace], bool]
Selector: TypeAlias = Callable[[Namespace], Creature | None]
Move: TypeAlias = Callable[[Namespace], Cell | None]


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the APL: an action id and up to three compiled clauses.

    Frozen and shared, like every compiled artefact in the project: an APL is
    compiled once per build and evaluated a few million times (DESIGN.md
    § Levers).
    """

    action: str
    condition: Condition
    target: Selector | None = None
    move: Move | None = None
    gate: str | None = None


# --- the selector and movement grammar, as callables ------------------------


def lowest_hp(candidates: Sequence[Creature]) -> Creature | None:
    """`lowest_hp of <selector>` — focus fire."""
    return min(candidates, key=_hp_of, default=None)


def nearest(origin: Cell, candidates: Sequence[Creature]) -> Creature | None:
    """`nearest of <selector>` — the closest, ties broken by list order."""
    return min(candidates, key=lambda c: distance_ft(origin, c.position), default=None)


def first(candidates: Sequence[Creature]) -> Creature | None:
    """`first(<selector>)` — the head of an ordered list, or nothing."""
    return candidates[0] if candidates else None


def _hp_of(creature: Creature) -> int:
    return creature.hp


def toward_until_in_reach(origin: Cell, reach_ft: int, target: Creature) -> Cell | None:
    """`toward <selector> until in_reach` — the destination, not the route.

    "Movement is a **decision, not a pathfinding problem**: the selector picks a
    destination and the engine walks the shortest legal route to it" (DESIGN.md
    § APL). So this returns the cell to stand in — the nearest cell to the mover
    that is within its reach of the target — and `core/geometry.route()` is what
    gets there. `None` means "already there", which is `hold` by another name.

    It takes the mover's position and reach rather than the mover, because a
    compiled `move:` clause reads both off the `self` proxy and the proxy does
    not hand out the creature behind it.
    """
    goal = target.position
    if in_reach(origin, goal, reach_ft):
        return None
    steps = reach_ft // 5
    return Cell(
        goal.x + _clamp(origin.x - goal.x, steps),
        goal.y + _clamp(origin.y - goal.y, steps),
    )


def _clamp(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))


class Apl:
    """A compiled action priority list: one flat ordered list, asked twice.

    DESIGN.md § APL's unifying idea is that "a turn action, a reaction, and a
    mid-attack rider are all 'the engine has an offer; does the APL take it?'",
    so this class answers exactly two questions and the entries do not know
    which one they were asked:

    - `choose(offered)` — the turn loop's question, filtering the list to the
      ids currently on the table and taking the first passing entry.
    - `allows(gate, source, ctx)` — the dispatcher's question, satisfying the
      `Gate` protocol in `hooks/dispatch.py`, so a gated handler's `if:` clause
      is evaluated inside the attack it might modify.
    """

    __slots__ = ("_by_gate", "_entries", "_namespace")

    def __init__(self, entries: Sequence[Entry], namespace: Namespace) -> None:
        self._entries = tuple(entries)
        self._namespace = namespace
        self._by_gate = {e.gate: e for e in self._entries if e.gate is not None}

    @property
    def namespace(self) -> Namespace:
        """The roots the engine binds `target` and `trigger` on."""
        return self._namespace

    @property
    def entries(self) -> tuple[Entry, ...]:
        return self._entries

    def choose(self, offered: frozenset[str]) -> Entry | None:
        """The first entry whose action is offered and whose `if:` passes."""
        namespace = self._namespace
        for entry in self._entries:
            if entry.action in offered and entry.condition(namespace):
                return entry
        return None

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        """Whether a gated handler may act. Unmentioned is OFF."""
        entry = self._by_gate.get(gate_id)
        return entry is not None and entry.condition(self._namespace)
