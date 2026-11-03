"""`Apl` — the action priority list: one flat ordered list, compiled at load,
asked by the turn loop as its `Policy` and by the dispatcher as its `Gate`.

`Entry` is one row as written; an `Apl` is built from entries or from YAML,
serialises back, and hashes canonically. `bind()` gives a creature its own
copy over a namespace of its own (DESIGN.md § APL and mid-attack decisions,
§ Reactions in the APL; `docs/decisions.md` § w2e).
"""

import difflib
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from weakref import WeakKeyDictionary

import yaml

from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.actions.dash import Dash
from dndsim.actions.disengage import Disengage
from dndsim.actions.dodge import Dodge
from dndsim.actions.move import Move, Retreat, Walk
from dndsim.actions.opportunity_attack import OpportunityAttack
from dndsim.actions.pick_up import PickUpWeapon
from dndsim.actions.stabilize import Stabilize
from dndsim.apl.expr import (
    AplLoadError,
    Compiled,
    MoveClause,
    MoveMode,
    compile_condition,
    compile_move,
    compile_param,
    compile_selector,
)
from dndsim.apl.namespace import Namespace, TriggerProxy, build_namespace
from dndsim.apl.schema import Kind, NamespaceSchema
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import distance_ft, in_reach
from dndsim.creatures.creature import Creature
from dndsim.engine.world import World
from dndsim.hooks.contexts import Context, LeaveReachContext
from dndsim.hooks.source import HookSource

__all__ = [
    "BEST_SCORED",
    "COMMAND_IDS",
    "DEFAULT_MOVE",
    "Apl",
    "CompiledEntry",
    "Entry",
    "unprovided",
]

BEST_SCORED: Final = "best_scored"
"""Reserved: take the highest-scoring offered action (DESIGN.md
§ `best_scored`; `docs/decisions.md` § w2e)."""

COMMAND_IDS: Final = frozenset(
    command.ID
    for command in (
        Attack,
        Dash,
        Disengage,
        Dodge,
        PickUpWeapon,
        Stabilize,
        OpportunityAttack,
        Walk,
    )
)
"""The ids of the Commands in `actions/`, which no feature has to provide."""

DEFAULT_MOVE: Final = "toward nearest of enemies until in_reach"
"""What a list with no `move:` clause anywhere does when nothing else is
taken (DESIGN.md § APL)."""

_KEYS: Final = ("action", "if", "target", "move", "with")


@dataclass(frozen=True, slots=True)
class Entry:
    """One row as written: an action id, up to three clauses of text, and the
    `with:` parameters, each an expression, in the order written."""

    action: str
    if_: str | None = None
    target: str | None = None
    move: str | None = None
    with_: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Entry":
        """Read a row of a parsed `apl:` list. Raises `AplLoadError` for an
        unknown key, with a suggestion, or a value that is not text."""
        for key in row:
            if key not in _KEYS:
                close = difflib.get_close_matches(str(key), _KEYS, n=1)
                hint = f"; did you mean {close[0]!r}?" if close else ""
                raise AplLoadError(f"unknown APL entry key {key!r}{hint}")
        if "action" not in row:
            raise AplLoadError(f"an APL entry names its action: {dict(row)!r}")
        for key, value in row.items():
            if key != "with" and not isinstance(value, str):
                raise AplLoadError(f"APL entry {key} is text, got {value!r}")
        return cls(
            row["action"],
            row.get("if"),
            row.get("target"),
            row.get("move"),
            _params(row.get("with", {})),
        )

    def to_mapping(self) -> dict[str, Any]:
        """The row as written, without the clauses it leaves out."""
        values = (self.action, self.if_, self.target, self.move, dict(self.with_))
        return {key: value for key, value in zip(_KEYS, values, strict=True) if value}


def _params(written: Any) -> tuple[tuple[str, str], ...]:
    """A `with:` mapping as `(name, expression)` pairs; a number written bare
    is the expression of itself. Raises `AplLoadError` for anything else."""
    if not isinstance(written, Mapping):
        raise AplLoadError(f"APL entry with: is a mapping, got {written!r}")
    pairs: list[tuple[str, str]] = []
    for name, value in written.items():
        if not isinstance(name, str) or isinstance(value, bool):
            raise AplLoadError(f"APL entry with: {name!r}: {value!r} is not a param")
        if not isinstance(value, str | int | float):
            raise AplLoadError(f"APL entry with: {name} is text, got {value!r}")
        pairs.append((name, str(value)))
    return tuple(pairs)


@dataclass(frozen=True, slots=True)
class CompiledEntry:
    """One row compiled: the action, its clauses as callables of a
    `Namespace`, and the gate id it answers."""

    action: str
    condition: Callable[[Namespace], Any]
    if_: Compiled | None
    target: Compiled | None
    move: MoveClause | None
    gate: str
    params: tuple[tuple[str, Compiled], ...] = ()
    reads_target: bool = False


def _always(ns: Namespace) -> bool:
    return True


class Apl:
    """A compiled action priority list.

    Built from `entries` against `schema`; every action id must be in
    `actions` (the Commands, the reserved id and whatever the caller adds),
    and `gates` maps an action id to the gate id a feature declares under it
    when the two differ. Raises `AplLoadError` at construction for anything
    that cannot be compiled. Equal, and hashed, by its entries alone.

    Unbound, it can be inspected and serialised; `bind()` returns the copy a
    creature is asked through.
    """

    __slots__ = (
        "_by_gate",
        "_compiled",
        "_default_move",
        "_entries",
        "_namespace",
        "_owner",
        "_schema",
    )

    def __init__(
        self,
        entries: Iterable[Entry],
        *,
        schema: NamespaceSchema | None = None,
        actions: Iterable[str] = (),
        gates: Mapping[str, str] | None = None,
    ) -> None:
        self._entries = tuple(entries)
        self._schema = NamespaceSchema() if schema is None else schema
        vocabulary = COMMAND_IDS | {BEST_SCORED} | frozenset(actions)
        gates = {} if gates is None else gates
        self._compiled = tuple(
            _compile_entry(entry, self._schema, vocabulary, gates)
            for entry in self._entries
        )
        by_gate: dict[str, list[CompiledEntry]] = {}
        for compiled in self._compiled:
            by_gate.setdefault(compiled.gate, []).append(compiled)
        self._by_gate = {gate: _any_of(found) for gate, found in by_gate.items()}
        self._default_move = (
            None
            if any(entry.move for entry in self._entries)
            else compile_move(DEFAULT_MOVE, self._schema)
        )
        self._namespace: Namespace | None = None
        self._owner: Creature | None = None

    # --- reading and writing it -------------------------------------------

    @classmethod
    def from_yaml(cls, text: str, **kwargs: Any) -> "Apl":
        """Parse an `apl:` list. Takes `__init__`'s keywords."""
        rows = yaml.safe_load(text)
        if not isinstance(rows, list):
            raise AplLoadError(f"an APL is a list of entries, got {rows!r}")
        for row in rows:
            if not isinstance(row, dict):
                raise AplLoadError(f"an APL entry is a mapping, got {row!r}")
        return cls((Entry.from_mapping(row) for row in rows), **kwargs)

    def to_yaml(self) -> str:
        """The list as YAML, one entry per item, keys in `Entry`'s order."""
        rows = [entry.to_mapping() for entry in self._entries]
        return yaml.safe_dump(rows, sort_keys=False, default_flow_style=False)

    @property
    def canonical_hash(self) -> str:
        """SHA-256 of the entries in a canonical JSON form."""
        rows = [entry.to_mapping() for entry in self._entries]
        for row in rows:
            if "with" in row:
                # Ordered pairs, as `==` compares them; a mapping would sort.
                row["with"] = [list(pair) for pair in row["with"].items()]
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def entries(self) -> tuple[Entry, ...]:
        return self._entries

    @property
    def compiled(self) -> tuple[CompiledEntry, ...]:
        return self._compiled

    @property
    def reads(self) -> frozenset[str]:
        """Every namespace path any clause reads."""
        paths: set[str] = set()
        for compiled in self._compiled:
            if compiled.if_ is not None:
                paths |= compiled.if_.reads
            if compiled.target is not None:
                paths |= compiled.target.reads
            if compiled.move is not None and compiled.move.selector is not None:
                paths |= compiled.move.selector.reads
            for _, param in compiled.params:
                paths |= param.reads
        return frozenset(paths)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Apl) and other._entries == self._entries

    def __hash__(self) -> int:
        return hash(self._entries)

    def __repr__(self) -> str:
        return f"<Apl of {len(self._entries)} entries>"

    # --- binding it to a creature -----------------------------------------

    def bind(self, owner: Creature, world: World) -> "Apl":
        """This list as `owner`'s, in `world`: a copy sharing the compiled
        clauses, over a namespace of its own."""
        bound = object.__new__(Apl)
        bound._entries = self._entries
        bound._schema = self._schema
        bound._compiled = self._compiled
        bound._by_gate = self._by_gate
        bound._default_move = self._default_move
        bound._namespace = build_namespace(self._schema, owner, world)
        bound._owner = owner
        _ROOTS[owner] = bound._namespace.trigger
        return bound

    def _actor(self) -> Creature:
        owner = self._owner
        if owner is None:
            raise EngineAssemblyError(
                "this APL is not bound to a creature; bind() it before asking it"
            )
        return owner

    @property
    def namespace(self) -> Namespace:
        """The roots the engine binds `target` and `trigger` on."""
        namespace = self._namespace
        if namespace is None:
            raise EngineAssemblyError(
                "this APL is not bound to a creature; bind() it before asking it"
            )
        return namespace

    # --- the questions it answers ------------------------------------------

    def first(self, offered: frozenset[str]) -> CompiledEntry | None:
        """The first entry whose action is in `offered` and whose `if:`
        passes."""
        namespace = self.namespace
        for entry in self._compiled:
            if entry.action not in offered:
                continue
            if entry.reads_target:
                _aim(entry, namespace, ())
            if entry.condition(namespace):
                return entry
        return None

    def walk(self, entry: CompiledEntry) -> Walk | None:
        """The walk `entry`'s `move:` clause asks for, or `None` when it has
        none, holds, or has nowhere legal to go."""
        clause = entry.move
        if clause is None:
            return None
        return _walk(clause, self.namespace, self._actor())

    def target(self, entry: CompiledEntry) -> Creature | None:
        """The creature `entry`'s `target:` clause picks, bound on `target`;
        `None` for a clause that picks nobody, and for an entry with no
        clause, whose target is the engine's own offer."""
        selector = entry.target
        if selector is None:
            return None
        namespace = self.namespace
        picked = next(iter(_candidates(selector.fn(namespace), selector.kind)), None)
        namespace.target.bind(picked)
        return picked

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        """The turn loop's question (`engine/turn.py`'s `Policy`).

        Each entry whose `if:` passes walks first when its `move:` finds a
        walk, then takes its action on the offer its `target:` picks. A list
        with no `move:` closes on its nearest enemy when no entry is taken.
        """
        namespace = self.namespace
        if actor is not self._actor():
            raise EngineAssemblyError(
                f"{actor.name} was asked through an APL bound to another creature"
            )
        for entry in self._compiled:
            if entry.reads_target:
                _aim(entry, namespace, offers)
            if not entry.condition(namespace):
                continue
            if entry.move is not None:
                walk = _walk(entry.move, namespace, actor)
                if walk is not None:
                    return walk
            command = _pick(entry, namespace, actor, offers)
            if command is not None:
                return command
        if self._default_move is not None:
            return _walk(self._default_move, namespace, actor)
        return None

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        """The dispatcher's question (`hooks/dispatch.py`'s `Gate`): may a
        gated handler act? Unmentioned is OFF. A movement offer binds
        `trigger.mover`, `trigger.origin`, `trigger.destination` and `target`
        to the mover first."""
        condition = self._by_gate.get(gate_id)
        if condition is None:
            return False
        namespace = self._namespace
        if namespace is None:
            namespace = self.namespace
        if isinstance(ctx, LeaveReachContext):
            mover = namespace.world.holder(ctx.mover)
            if mover is not None:
                namespace.trigger.offer_movement(mover, ctx.origin, ctx.destination)
                namespace.target.bind(mover)
        return bool(condition(namespace))


def unprovided(apl: Apl, provided: Iterable[str]) -> frozenset[str]:
    """The action ids `apl` names that neither a Command nor `provided`
    gives; the reserved `best_scored` is never one (DESIGN.md § Failing
    loudly)."""
    known = COMMAND_IDS | {BEST_SCORED} | frozenset(provided)
    return frozenset(entry.action for entry in apl.entries if entry.action not in known)


def _any_of(entries: Sequence[CompiledEntry]) -> Callable[[Namespace], Any]:
    """One gate's question: the first of `entries` whose `if:` passes."""
    if len(entries) == 1:
        return entries[0].condition
    conditions = tuple(entry.condition for entry in entries)
    return lambda namespace: any(condition(namespace) for condition in conditions)


def _compile_entry(
    entry: Entry,
    schema: NamespaceSchema,
    vocabulary: frozenset[str],
    gates: Mapping[str, str],
) -> CompiledEntry:
    action = entry.action
    if action not in vocabulary:
        close = difflib.get_close_matches(action, sorted(vocabulary), n=1)
        hint = f"; did you mean {close[0]!r}?" if close else ""
        raise AplLoadError(f"unknown APL action {action!r}{hint}")
    condition = None if entry.if_ is None else compile_condition(entry.if_, schema)
    target = None if entry.target is None else compile_selector(entry.target, schema)
    move = None if entry.move is None else compile_move(entry.move, schema)
    names = [name for name, _ in entry.with_]
    if len(set(names)) != len(names):
        raise AplLoadError(f"APL entry {action!r} names a with: param twice: {names}")
    params = tuple((name, compile_param(text, schema)) for name, text in entry.with_)
    return CompiledEntry(
        action=action,
        condition=_always if condition is None else condition.fn,
        if_=condition,
        target=target,
        move=move,
        gate=gates.get(action, action),
        params=params,
        reads_target=condition is not None
        and any(path.startswith("target.") for path in condition.reads),
    )


def _candidates(chosen: Any, kind: Kind) -> tuple[Creature, ...]:
    if kind is Kind.GROUP:
        return tuple(chosen)
    return () if chosen is None else (chosen,)


def _walk(clause: MoveClause, namespace: Namespace, actor: Creature) -> Walk | None:
    """The first legal walk the clause's selector gives, or `None`."""
    selector = clause.selector
    if clause.mode is MoveMode.HOLD or selector is None:
        return None
    world = namespace.world
    for creature in _candidates(selector.fn(namespace), selector.kind):
        walk: Walk
        if clause.mode is MoveMode.TOWARD:
            if in_reach(
                actor.position,
                creature.position,
                actor.reach_ft,
                origin_size=actor.size,
                destination_size=creature.size,
            ):
                # Arrived: walking on toward a later pick would leave this one.
                return None
            walk = Move(actor, creature, world=world)
        else:
            walk = Retreat(actor, creature, world=world, keep_ft=_keep(clause, actor))
        if walk.is_legal():
            return walk
    return None


def _keep(clause: MoveClause, actor: Creature) -> int | None:
    """`to <n> ft`, or with `to_max_range` the normal range of what the actor
    wields; `None`, as far as it can, for a weapon with no range."""
    if clause.keep_ft is not None:
        return clause.keep_ft
    weapon = actor.weapon
    if weapon is None or weapon.range_ft is None:
        return None
    return weapon.range_ft[0]


def _target_of(command: Action[Any]) -> Creature | None:
    if isinstance(command, Attack | Stabilize | Walk):
        return command.target
    return None


def _aim(
    entry: CompiledEntry, namespace: Namespace, offers: Sequence[Action[Any]]
) -> None:
    """Bind `target` to the creature `entry` would take its action on, before
    its `if:` reads it: the first of its `target:` picks that an offer is
    against, else its first pick; with no `target:`, the nearest offer's."""
    matching = [offer for offer in offers if entry.action == offer.ID]
    picked: Creature | None = None
    selector = entry.target
    if selector is not None:
        candidates = _candidates(selector.fn(namespace), selector.kind)
        offered = {id(_target_of(offer)) for offer in matching}
        picked = next(
            (creature for creature in candidates if id(creature) in offered),
            candidates[0] if candidates else None,
        )
    elif matching:
        actor = namespace.self.creature
        picked = _target_of(
            min(matching, key=lambda offer: _distance(actor, _target_of(offer)))
        )
    namespace.target.bind(picked)


def _pick(
    entry: CompiledEntry,
    namespace: Namespace,
    actor: Creature,
    offers: Sequence[Action[Any]],
) -> Action[Any] | None:
    """The offer of `entry`'s action its `target:` picks, binding `target`."""
    matching = [offer for offer in offers if entry.action == offer.ID]
    if not matching:
        return None
    selector = entry.target
    chosen: Action[Any] | None = None
    if selector is None:
        chosen = min(matching, key=lambda offer: _distance(actor, _target_of(offer)))
    else:
        for creature in _candidates(selector.fn(namespace), selector.kind):
            chosen = next((o for o in matching if _target_of(o) is creature), None)
            if chosen is not None:
                break
    if chosen is not None:
        namespace.target.bind(_target_of(chosen))
        if isinstance(chosen, Attack) and chosen.trigger is None:
            # The attack rebinds these roots, so a gated rider or reaction on
            # either side reads this attack (`docs/decisions.md` § w2e).
            defender = _ROOTS.get(chosen.target)
            mine = namespace.trigger
            chosen.trigger = mine if defender is None else _Both(mine, defender)
    return chosen


_ROOTS: "WeakKeyDictionary[Creature, TriggerProxy]" = WeakKeyDictionary()
"""Each creature's `trigger.*` root, from the last `bind()` of an APL to it."""


class _Both:
    """Two `trigger.*` roots told the same offer: the attacker's and the
    defender's."""

    __slots__ = ("_first", "_second")

    def __init__(self, first: TriggerProxy, second: TriggerProxy) -> None:
        self._first = first
        self._second = second

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None:
        self._first.bind(crit=crit, damage_estimate=damage_estimate)
        self._second.bind(crit=crit, damage_estimate=damage_estimate)


def _distance(actor: Creature, target: Creature | None) -> int:
    """How far an offer's target is; an untargeted offer counts as 0, so the
    first one offered is taken."""
    if target is None:
        return 0
    return distance_ft(
        actor.position,
        target.position,
        origin_size=actor.size,
        destination_size=target.size,
    )
