"""The 31 Parameter Objects the 39 hooks hand to a handler, and the mutators
that are the only way to change one.

One hook, one context type, and the context is the *entire* surface a feature is
allowed to touch during an event. That is the second of DESIGN.md's five
load-bearing ideas, and the reason ~630 independently-authored features can run
over the same attack without clobbering each other: **nobody writes a field.**

**Attribute-locked, mutator-only.** Every context is a frozen, slotted
dataclass whose `__setattr__` refuses with an explanation. `ctx.damage = ...`
and `ctx.advantage = True` both raise `AttributeError` at the moment they are
written rather than silently winning a race with the feature that ran before.
What a feature *may* do is call a mutator — `ctx.damage.add(...)`,
`ctx.grant_advantage(...)`, `ctx.apply_effect(...)` — each of which appends to a
collection instead of replacing a value, so two features doing the same thing is
addition and never assignment. The lock is against accident, not malice: the
engine's own `reopen()` writes through `object.__setattr__`, and so could a
determined author. Making the wrong thing *awkward and loud* is the whole
mechanism.

**A context is live only while its hook is dispatching.** Contexts are pooled
and reused across attacks (DESIGN.md § Levers), so a handler that stashes one
and mutates it on a later turn is not writing to a finished event — it is
writing to whichever event owns that object now. `close()` seals a context when
its moment ends and every mutator raises `PhaseError` afterwards, which turns
the most dangerous consequence of pooling into a loud failure the first time it
happens. It does not catch the case where the object has already been recycled
and re-opened; nothing cheap does, and the seal costs one boolean test on a path
that runs a handful of times per attack.

**Provenance is stamped, never passed.** The dispatcher calls `stamp()` with the
acting source before each handler, so `ctx.damage.add(dice="2d6")` is attributed
to `rogue.sneak_attack` with no author effort and the per-feature damage
breakdown falls out for free. There is deliberately no `source=` argument for a
feature to get wrong or to lie with; a mutation made with nothing stamped is a
`PhaseError`, because it is the engine reaching into a context it has not
entered.

**Two contribution types, because two crit rules.** `DamagePart` splits `dice`
from `flat` — dice double on a critical hit, flat never does, and the author
never touches the crit path (DESIGN.md § Structural prevention). `Modifier` is
the same dice/flat split for everything whose dice *don't* double: a bonus to a
D20 Test, healing, temporary hit points, a bump to a save DC. They are separate
types rather than one with a flag precisely so that `Dice.doubled()` is
reachable from exactly one of them.

**Participants are structural, not imported.** `hooks/` sits below `creatures/`
— a creature holds a dispatch table, so the dependency runs upward — and the
roadmap builds contexts (w0f) two tasks before the first `Creature` exists
(w0g2). A context therefore names what it carries through narrow `Protocol`s:
`Combatant`, `Weapon`, `Spell`, `Effect`, `Cell`. Each declares only what is
already settled elsewhere in the design (`Duration` anchors on
`creature.initiative_index`; the log and provenance need a name), and each is
meant to *stay* narrow — a protocol that grows to mirror `Creature` is a second
domain model, and the check that keeps it honest is w0g2 asserting its real
classes satisfy these.

What is deliberately absent:

- **No pool.** `reopen()` is the reuse primitive; who owns the pool is the
  dispatcher's question (w0g) and the turn loop's (w1f). A pool here would be a
  second place that knows when an event ends.
- **No rolling.** A context collects contributions; `engine/damage.py` (w1e) and
  `engine/attack.py` (w1f) own the arithmetic and the resolution order. A
  context that could roll would be a second place the damage order could be
  written.
- **No speculative mutators.** Casting, movement and effect contexts carry their
  participants and little else, because the W1–W3 slice has no feature that
  changes those events. Fields are additive; a mutator that shipped early and
  wrong is not.

Sources: DESIGN.md § Hook system, § Structural prevention over testing,
§ Failing loudly — layered, § Levers; ROADMAP w0f.
"""

import dataclasses
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import (
    ClassVar,
    Final,
    NoReturn,
    Protocol,
    TypeVar,
    dataclass_transform,
)

from dndsim.core.dice import Dice
from dndsim.core.errors import PhaseError
from dndsim.core.timeline import TimeKey
from dndsim.hooks.registry import Phase
from dndsim.rules.enums import Ability, Condition, DamageType, Outcome

__all__ = [
    "AbilityCheckContext",
    "AdvantageLedger",
    "AttackDeclaredContext",
    "AttackResolvedContext",
    "AttackRollContext",
    "CastSpellContext",
    "Cell",
    "Combatant",
    "ConcentrationCheckContext",
    "ConcentrationContext",
    "ConditionContext",
    "Context",
    "D20TestContext",
    "DamageBag",
    "DamagePart",
    "DamageRollContext",
    "DamageTakenContext",
    "DeathSaveContext",
    "Effect",
    "EffectContext",
    "EncounterContext",
    "ForcedSaveContext",
    "HealContext",
    "HitContext",
    "IncomingDamageContext",
    "InitiativeContext",
    "KillContext",
    "LeaveReachContext",
    "MissContext",
    "MitigationLedger",
    "Modifier",
    "ModifierBag",
    "MoveContext",
    "OverrideLedger",
    "ReducedToZeroContext",
    "ResourceSpentContext",
    "RestContext",
    "RoundContext",
    "SaveResolvedContext",
    "SavingThrowContext",
    "Spell",
    "StabilizeContext",
    "TempHpContext",
    "TurnContext",
    "Weapon",
]


# --- What a context carries ------------------------------------------------
# Structural, narrow, and meant to stay that way; see the module docstring.
# Members are read-only properties so that a frozen dataclass satisfies them.


class Combatant(Protocol):
    """A creature as a context needs to know it: something with a seat and a name.

    `initiative_index` is here because `core/timeline.py` already builds on it —
    `Duration` anchors on a seat in the initiative order rather than on a
    creature, precisely so that `core/` need not know what a creature is.
    """

    @property
    def name(self) -> str: ...

    @property
    def initiative_index(self) -> int: ...


class Weapon(Protocol):
    """The weapon an attack is made with, or `None` for one that is not made
    with a weapon."""

    @property
    def name(self) -> str: ...


class Spell(Protocol):
    """A spell being cast or concentrated on."""

    @property
    def name(self) -> str: ...

    @property
    def level(self) -> int: ...


class Effect(Protocol):
    """An `Effect` (`creatures/effects.py`, w1c) on its way onto a creature."""

    @property
    def name(self) -> str: ...


class Cell(Protocol):
    """One 5-foot square of the grid (`core/geometry.py`, w0g2/w1d)."""

    @property
    def x(self) -> int: ...

    @property
    def y(self) -> int: ...


# --- What a feature contributes --------------------------------------------


@dataclass(frozen=True, slots=True)
class DamagePart:
    """One contribution to a damage roll, attributed to whoever made it.

    The `dice` / `flat` split is the project's flagship piece of structural
    prevention (DESIGN.md § Structural prevention over testing): **dice double
    on a critical hit and flat bonuses never do**, so a feature that adds `2d6`
    of Sneak Attack and a feature that adds `+2` of Rage damage get the crit
    rule right without either author knowing there is a crit rule. `on_crit()`
    is the only place the doubling happens, and `Dice.doubled()` is the only
    thing it calls.

    `damage_type` of `None` means "the same type as the attack this rides on" —
    Sneak Attack's d6s are the dagger's piercing — which is a real rule rather
    than a missing value, and it is why the field is optional instead of
    defaulted to something.
    """

    dice: Dice | None
    flat: int
    damage_type: DamageType | None
    source: str

    def on_crit(self) -> "DamagePart":
        """This contribution as it lands on a critical hit."""
        if self.dice is None:
            return self
        return replace(self, dice=self.dice.doubled())

    def __str__(self) -> str:
        return f"{_amount(self.dice, self.flat)} [{self.source}]"


@dataclass(frozen=True, slots=True)
class Modifier:
    """A contribution whose dice do **not** double: a bonus to a D20 Test,
    healing, temporary hit points, a bump to a save DC or to AC.

    The same dice/flat shape as `DamagePart` and a deliberately different type.
    Bless adds `1d4` to an attack roll and a critical hit does not double it;
    Cure Wounds heals `2d8` and a critical hit is not a thing that happens to
    healing. Folding both into one class with a `doubles: bool` flag would put
    the crit rule back in the hands of every author who sets it.
    """

    dice: Dice | None
    flat: int
    source: str

    def __str__(self) -> str:
        return f"{_amount(self.dice, self.flat)} [{self.source}]"


def _amount(dice: Dice | None, flat: int) -> str:
    if dice is None:
        return f"{flat:+d}"
    return str(dice) if flat == 0 else f"{dice}{flat:+d}"


# --- The mutable collections a feature appends to ---------------------------


class _Bag:
    """Shared machinery for the append-only collections hung off a context.

    A bag holds the owning context's frame so that every `add()` can answer two
    questions without the caller supplying anything: is this context still live,
    and who is acting? That is what makes provenance automatic and what makes a
    stashed context loud instead of quietly corrupting somebody else's attack.
    """

    __slots__ = ("_frame",)

    def __init__(self) -> None:
        self._frame: _Frame | None = None

    def _bind(self, frame: "_Frame") -> None:
        """Attach this bag to its context's frame. The context does this once."""
        self._frame = frame

    def _acting_source(self, action: str) -> str:
        frame = self._frame
        if frame is None:
            raise PhaseError(
                f"{action} on a bag that belongs to no context. Bags are fields "
                "of a context and are bound when it is constructed; a loose one "
                "has no event to contribute to."
            )
        return _acting_source(frame, action)

    def clear(self) -> None:
        raise NotImplementedError


class DamageBag(_Bag):
    """The damage an event has accumulated so far. `ctx.damage.add(...)`.

    Append-only by design: a feature contributes a part and can read the whole,
    but cannot replace what another feature added. Nothing here rolls or
    totals the dice — `engine/damage.py` (w1e) owns the resolution order, and a
    bag that could compute a total would be a second place to write it.
    """

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._parts: list[DamagePart] = []

    def add(
        self,
        *,
        dice: str | Dice | None = None,
        flat: int = 0,
        damage_type: DamageType | None = None,
    ) -> DamagePart:
        """Contribute damage. `dice` doubles on a crit; `flat` never does.

        `damage_type=None` inherits the type of the attack this rides on.
        """
        source = self._acting_source("ctx.damage.add()")
        part = DamagePart(
            dice=_as_dice(dice),
            flat=flat,
            damage_type=damage_type,
            source=source,
        )
        if part.dice is None and part.flat == 0:
            raise ValueError(
                f"{source} added no damage: pass dice, flat, or both. An empty "
                "contribution is a feature that meant to return SKIPPED."
            )
        self._parts.append(part)
        return part

    @property
    def parts(self) -> tuple[DamagePart, ...]:
        """Every contribution, in the order it was made."""
        return tuple(self._parts)

    def clear(self) -> None:
        self._parts.clear()

    def __iter__(self) -> Iterator[DamagePart]:
        return iter(self._parts)

    def __len__(self) -> int:
        return len(self._parts)

    def __repr__(self) -> str:
        return f"DamageBag({', '.join(str(p) for p in self._parts)})"


class ModifierBag(_Bag):
    """Contributions whose dice do not double: `ctx.bonus.add(flat=2)`.

    The same append-only shape as `DamageBag` over `Modifier` instead of
    `DamagePart`, which is what keeps `Dice.doubled()` unreachable from a save
    bonus or a heal.
    """

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._parts: list[Modifier] = []

    def add(self, *, dice: str | Dice | None = None, flat: int = 0) -> Modifier:
        """Contribute a modifier."""
        source = self._acting_source("add()")
        modifier = Modifier(dice=_as_dice(dice), flat=flat, source=source)
        if modifier.dice is None and modifier.flat == 0:
            raise ValueError(
                f"{source} added nothing: pass dice, flat, or both. An empty "
                "contribution is a feature that meant to return SKIPPED."
            )
        self._parts.append(modifier)
        return modifier

    @property
    def parts(self) -> tuple[Modifier, ...]:
        """Every contribution, in the order it was made."""
        return tuple(self._parts)

    @property
    def flat_total(self) -> int:
        """The flat contributions summed. The dice are the engine's to roll."""
        return sum(m.flat for m in self._parts)

    def clear(self) -> None:
        self._parts.clear()

    def __iter__(self) -> Iterator[Modifier]:
        return iter(self._parts)

    def __len__(self) -> int:
        return len(self._parts)

    def __repr__(self) -> str:
        return f"ModifierBag({', '.join(str(m) for m in self._parts)})"


class AdvantageLedger(_Bag):
    """Who asked for advantage and who imposed disadvantage, by name.

    **Not a boolean**, which is the second piece of structural prevention
    (DESIGN.md): D&D's rule — any advantage plus any disadvantage cancels to a
    flat roll, and multiples never stack — falls out of a derived property over
    two collections. Two features granting advantage is therefore idempotent and
    order-independent, so no author has to reason about who ran first, and the
    log gets the reasons for free.
    """

    __slots__ = ("_advantage", "_disadvantage")

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._advantage: list[str] = []
        self._disadvantage: list[str] = []

    def grant(self, reason: str) -> None:
        """Record one source of advantage."""
        self._advantage.append(f"{self._acting_source('grant_advantage()')}: {reason}")

    def impose(self, reason: str) -> None:
        """Record one source of disadvantage."""
        self._disadvantage.append(
            f"{self._acting_source('impose_disadvantage()')}: {reason}"
        )

    @property
    def advantage_sources(self) -> tuple[str, ...]:
        return tuple(self._advantage)

    @property
    def disadvantage_sources(self) -> tuple[str, ...]:
        return tuple(self._disadvantage)

    @property
    def had_advantage(self) -> bool:
        """True when the roll is made with advantage, cancellation applied."""
        return bool(self._advantage) and not self._disadvantage

    @property
    def had_disadvantage(self) -> bool:
        """True when the roll is made with disadvantage, cancellation applied."""
        return bool(self._disadvantage) and not self._advantage

    def clear(self) -> None:
        self._advantage.clear()
        self._disadvantage.clear()

    def __repr__(self) -> str:
        return (
            f"AdvantageLedger(advantage={self._advantage!r}, "
            f"disadvantage={self._disadvantage!r})"
        )


def _as_dice(dice: str | Dice | None) -> Dice | None:
    """Accept `"2d6"` or a shared `Dice`; reject `"1d8+3"` where it is written.

    `Dice.parse` refuses a flat modifier, which is the whole point of taking a
    string here: `add(dice="1d8+3")` fails at the call site naming where the
    `+3` belongs rather than smuggling a flat bonus into the field that doubles.
    """
    if dice is None or isinstance(dice, Dice):
        return dice
    return Dice.parse(dice)


# --- The lifecycle a mutator is checked against -----------------------------


class _Frame:
    """The one mutable cell in an otherwise frozen context.

    Everything that changes between handler calls lives here rather than as
    fields of the context: the ambient provenance stamp, the ordering band the
    current handler declared, whether the moment is still open, and the veto if
    one was cast. That keeps the context itself frozen — so `ctx.attacker = x`
    is a static error as well as a runtime one — while the per-call stamp stays
    a plain slot store on the hot path, which `object.__setattr__` on a frozen
    dataclass would not be.
    """

    __slots__ = ("open", "owner", "phase", "source", "veto_reason")

    def __init__(self) -> None:
        self.owner = "context"
        self.source: str | None = None
        self.phase: Phase | None = None
        self.open = True
        self.veto_reason: str | None = None

    def reset(self) -> None:
        self.source = None
        self.phase = None
        self.open = True
        self.veto_reason = None


def _acting_source(frame: _Frame, action: str) -> str:
    """The source to attribute `action` to, or `PhaseError` explaining why not.

    The two refusals are the two halves of `PhaseError` (see `core/errors.py`),
    and both are one comparison on a path a handler walks a couple of times per
    attack.
    """
    if not frame.open:
        raise PhaseError(
            f"{action} on a {frame.owner} whose moment has passed. A context is "
            "live only while its hook is dispatching; because contexts are "
            "pooled, holding on to one and mutating it later reaches an event "
            "that is over — or somebody else's."
        )
    source = frame.source
    if source is None:
        raise PhaseError(
            f"{action} on a {frame.owner} with nothing stamped on it. The "
            "dispatcher stamps the acting source before every handler so that "
            "provenance is automatic; a mutation with nobody to attribute it to "
            "is the engine reaching into a context it has not entered."
        )
    return source


_VETO_PHASES: Final = frozenset({Phase.EARLY, Phase.FINAL})

_T = TypeVar("_T")


def _locked_setattr(self: object, name: str, value: object) -> NoReturn:
    raise AttributeError(
        f"{type(self).__name__} is attribute-locked: assigning {name!r} is not "
        "how a feature changes an event. Call a mutator — ctx.damage.add(...), "
        "ctx.grant_advantage(...), ctx.apply_effect(...) — so that your change "
        "is added to what other features contributed instead of replacing it "
        "(DESIGN.md § Contexts are attribute-locked)."
    )


@dataclass_transform(frozen_default=True, kw_only_default=True, eq_default=False)
def _context(cls: type[_T]) -> type[_T]:
    """Make a context class: frozen, slotted, keyword-only, identity-compared.

    `frozen` is what stops a field assignment statically and gives the generated
    `__init__` a path that bypasses `__setattr__`; replacing `__setattr__`
    afterwards is what turns the runtime refusal from dataclasses' "cannot
    assign to field" into a sentence that names the mutators instead. Both
    matter: one catches the mistake in review, the other catches it in the batch
    agent's first test run.

    `kw_only` because a Parameter Object read at the call site is the point, and
    because it frees base and subclass fields from declaration order. `eq=False`
    because a context is the event, not a value: two attacks that look alike are
    not the same attack, and identity hashing keeps a pooled object cheap.
    """
    made = dataclass(frozen=True, slots=True, kw_only=True, eq=False)(cls)
    made.__setattr__ = _locked_setattr  # type: ignore[method-assign]
    fields = dataclasses.fields(made)  # type: ignore[arg-type]
    made._field_names = frozenset(f.name for f in fields)  # type: ignore[attr-defined]
    owned: list[str] = []
    shared: list[str] = []
    for spec in fields:
        # Typed as `object` because `default_factory` is a callable union that
        # mypy narrows past `type` into nothing at all.
        factory: object = spec.default_factory
        if isinstance(factory, type) and issubclass(factory, _Bag):
            owned.append(spec.name)
        elif isinstance(spec.type, type) and issubclass(spec.type, _Bag):
            shared.append(spec.name)
    made._owned_bags = tuple(owned)  # type: ignore[attr-defined]
    made._shared_bags = tuple(shared)  # type: ignore[attr-defined]
    return made


@_context
class Context:
    """What every hook hands its handlers, and the three things they all share.

    A context is a **Parameter Object**: one argument carrying the whole of an
    event, so that adding a field to a hook is not a signature change rippling
    through ~630 handlers. Beyond that it carries the lifecycle every mutator is
    checked against, the veto that lets an `EARLY` or `FINAL` handler halt the
    chain, and the queue of effects to apply once the event is over.

    **Effects are queued, not applied.** `ctx.apply_effect(...)` appends; the
    engine drains the queue after the dispatch. That is the snapshot rule from
    the authoring skill made structural — the handler list is fixed per event,
    so an effect applied during a hook must not fire for that same event, which
    is exactly why Reckless Attack applies its effect *and* separately calls
    `grant_advantage()` for the attack that triggered it.
    """

    _field_names: ClassVar[frozenset[str]] = frozenset()
    _owned_bags: ClassVar[tuple[str, ...]] = ()
    _shared_bags: ClassVar[tuple[str, ...]] = ()

    _frame: _Frame = field(default_factory=_Frame, repr=False)
    _pending: list[tuple[Combatant, Effect]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._frame.owner = type(self).__name__
        self._bind_bags()

    def _bind_bags(self) -> None:
        """Point every bag on this context at this context's frame.

        A bag created by this context's own `default_factory` is bound once and
        never moves. A bag *handed* to it is the interesting case: the damage
        built during `on_hit` is the same damage rolled during `on_damage_roll`,
        so the bag outlives the context that made it. Rebinding is what keeps
        provenance and the lifecycle with the context currently dispatching
        rather than with one that has already closed.
        """
        frame = self._frame
        for name in self._owned_bags:
            owned: _Bag = getattr(self, name)
            owned._bind(frame)
        for name in self._shared_bags:
            shared: _Bag = getattr(self, name)
            shared._bind(frame)

    # -- the dispatcher's side; features never call these --------------------

    def stamp(self, source: str, phase: Phase | None) -> None:
        """Name the source about to run, and the band it declared.

        The **Ambient Context** of DESIGN.md § Provenance is automatic, scoped
        to one handler invocation: every mutation made while this stamp stands
        is attributed to `source`, which is where the per-feature damage
        breakdown comes from. `phase` is `None` for the engine's own seeding of
        a context, which is also why `veto()` refuses when it is.
        """
        frame = self._frame
        frame.source = source
        frame.phase = phase

    def close(self) -> None:
        """End the moment. Every mutator raises `PhaseError` from here on."""
        frame = self._frame
        frame.open = False
        frame.source = None
        frame.phase = None

    def reopen(self, **fields: object) -> None:
        """Rebind a pooled context to a new event and clear what it accumulated.

        The reuse primitive behind DESIGN.md § Levers' "contexts reset and
        reused per attack rather than allocated". It writes through
        `object.__setattr__` because the lock is for features, not for the
        engine that owns the object — and it names every field it is given, so
        a typo here is an `AttributeError` rather than a field silently left
        holding the previous attack's value.

        Bags this context created are cleared; a bag handed to it belongs to
        whoever made it and is only rebound, because clearing the caller's
        freshly built damage would be the pool eating the attack.
        """
        for name, value in fields.items():
            if name not in self._field_names:
                raise AttributeError(
                    f"{type(self).__name__} has no field {name!r} to rebind"
                )
            object.__setattr__(self, name, value)
        self._frame.reset()
        self._pending.clear()
        for name in self._owned_bags:
            bag: _Bag = getattr(self, name)
            bag.clear()
        self._bind_bags()

    # -- what a feature may do -----------------------------------------------

    def veto(self, reason: str) -> None:
        """Halt the chain: this event does not happen.

        The one **Chain of Responsibility** element in a system that is
        otherwise a pipeline of contributions, and the reason it is restricted
        to `EARLY` and `FINAL` (DESIGN.md § Ordering): a `NORMAL` handler that
        could cancel the event would silently delete the contributions of every
        feature ordered after it, which is the failure mode the phase bands
        exist to prevent.
        """
        frame = self._frame
        source = _acting_source(frame, "ctx.veto()")
        if frame.phase not in _VETO_PHASES:
            band = "no band" if frame.phase is None else frame.phase.name
            raise PhaseError(
                f"{source} vetoed from {band}: only EARLY and FINAL handlers may "
                "halt the chain. A NORMAL or LATE handler that cancels an event "
                "deletes the contributions of everything ordered after it — "
                "declare phase=Phase.EARLY if this handler's job is to gate."
            )
        frame.veto_reason = f"{source}: {reason}"

    def apply_effect(self, target: Combatant, effect: Effect) -> None:
        """Queue an effect to be applied to `target` once this event is over."""
        _acting_source(self._frame, "ctx.apply_effect()")
        self._pending.append((target, effect))

    # -- what anyone may read ------------------------------------------------

    @property
    def vetoed(self) -> bool:
        """True once a handler has halted the chain."""
        return self._frame.veto_reason is not None

    @property
    def veto_reason(self) -> str | None:
        """Who vetoed and why, or `None`."""
        return self._frame.veto_reason

    @property
    def pending_effects(self) -> tuple[tuple[Combatant, Effect], ...]:
        """The effects queued during this event, for the engine to apply."""
        return tuple(self._pending)


@_context
class D20TestContext(Context):
    """A context for a roll of the d20: attack, check, save, initiative, death
    save, concentration.

    2024 calls all of them **D20 Tests**, and they share the two things a
    feature wants to do to one — swing the odds and add to the total — so they
    share a base rather than repeating the advantage machinery six times. What
    they do *not* share is the outcome: a natural 20 crits only on an attack
    roll, which is why `Outcome` stays the attack's and saves get their own
    result type in `engine/saves.py` (w1g).
    """

    _advantage: AdvantageLedger = field(default_factory=AdvantageLedger, repr=False)
    bonus: ModifierBag = field(default_factory=ModifierBag)

    def grant_advantage(self, reason: str) -> None:
        """Ask for advantage on this roll. Idempotent and order-independent."""
        self._advantage.grant(reason)

    def impose_disadvantage(self, reason: str) -> None:
        """Impose disadvantage on this roll. Cancels any advantage, once."""
        self._advantage.impose(reason)

    @property
    def had_advantage(self) -> bool:
        """True when the roll is made with advantage, cancellation applied."""
        return self._advantage.had_advantage

    @property
    def had_disadvantage(self) -> bool:
        """True when the roll is made with disadvantage, cancellation applied."""
        return self._advantage.had_disadvantage

    @property
    def advantage_sources(self) -> tuple[str, ...]:
        """Every reason advantage was granted, for the log."""
        return self._advantage.advantage_sources

    @property
    def disadvantage_sources(self) -> tuple[str, ...]:
        """Every reason disadvantage was imposed, for the log."""
        return self._advantage.disadvantage_sources


# --- Two more ledgers, for the two events a handler can overrule ------------


class MitigationLedger(_Bag):
    """Everything a victim's handlers did to incoming damage before it landed.

    A ledger rather than an arithmetic result because `engine/damage.py` (w1e)
    owns the resolution order — immunity → vulnerability → resistance → flat
    reductions → halvings → temp HP → HP — and a context that pre-applied any
    of it would be a second place that order is written.

    The three type collections are **sets**, which is the third piece of
    structural prevention (DESIGN.md): a damage type cannot be resisted twice,
    so two features granting fire resistance halve the damage once, and neither
    author has to know the other exists.
    """

    __slots__ = ("_halvings", "_immune", "_reductions", "_resist", "_vulnerable")

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._resist: set[DamageType] = set()
        self._immune: set[DamageType] = set()
        self._vulnerable: set[DamageType] = set()
        self._reductions: list[Modifier] = []
        self._halvings: list[str] = []

    def resist(self, damage_type: DamageType) -> None:
        self._acting_source("ctx.add_resistance()")
        self._resist.add(damage_type)

    def immune(self, damage_type: DamageType) -> None:
        self._acting_source("ctx.add_immunity()")
        self._immune.add(damage_type)

    def vulnerable(self, damage_type: DamageType) -> None:
        self._acting_source("ctx.add_vulnerability()")
        self._vulnerable.add(damage_type)

    def reduce(self, dice: str | Dice | None, flat: int) -> Modifier:
        source = self._acting_source("ctx.reduce()")
        reduction = Modifier(dice=_as_dice(dice), flat=flat, source=source)
        if reduction.dice is None and reduction.flat == 0:
            raise ValueError(f"{source} reduced damage by nothing")
        self._reductions.append(reduction)
        return reduction

    def halve(self, reason: str) -> None:
        self._halvings.append(f"{self._acting_source('ctx.halve()')}: {reason}")

    @property
    def resistances(self) -> frozenset[DamageType]:
        return frozenset(self._resist)

    @property
    def immunities(self) -> frozenset[DamageType]:
        return frozenset(self._immune)

    @property
    def vulnerabilities(self) -> frozenset[DamageType]:
        return frozenset(self._vulnerable)

    @property
    def reductions(self) -> tuple[Modifier, ...]:
        return tuple(self._reductions)

    @property
    def halvings(self) -> tuple[str, ...]:
        return tuple(self._halvings)

    def clear(self) -> None:
        self._resist.clear()
        self._immune.clear()
        self._vulnerable.clear()
        self._reductions.clear()
        self._halvings.clear()


class OverrideLedger(_Bag):
    """A yes/no claim handlers can make about an outcome the engine would
    otherwise apply: "this creature stays up", "this save succeeds".

    Reasons accumulate rather than the last writer winning, so Relentless
    Endurance and Undead Fortitude on the same creature is one survival with two
    citations in the log instead of a race.
    """

    __slots__ = ("_reasons",)

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._reasons: list[str] = []

    def claim(self, action: str, reason: str) -> None:
        self._reasons.append(f"{self._acting_source(action)}: {reason}")

    @property
    def claimed(self) -> bool:
        return bool(self._reasons)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self._reasons)

    def clear(self) -> None:
        self._reasons.clear()


# --- Timeline ---------------------------------------------------------------


@_context
class EncounterContext(Context):
    """`on_encounter_start` / `on_encounter_end` — GLOBAL: the fight itself.

    A combat belongs to nobody, so there is no owner to be and no participant
    field beyond everyone in it.
    """

    combatants: tuple[Combatant, ...]


@_context
class InitiativeContext(D20TestContext):
    """`on_initiative_roll` — SELF: the owner rolls for its seat.

    Feral Instinct grants advantage here, the Alert feat adds to the roll, and
    Uncanny Metabolism watches it. All three are inside the levels 1–12 martial
    slice, which is why this hook exists before W4 needs it (DESIGN.md § The
    closed hook list).
    """

    creature: Combatant


@_context
class RoundContext(Context):
    """`on_round_start` / `on_round_end` — GLOBAL: a round of the encounter."""

    round: int


@_context
class TurnContext(Context):
    """`on_turn_start` / `on_turn_end` — SELF: the owner's turn.

    By the time a handler here runs, `Reset.OWN_TURN` budgets have already been
    handed back (`core/timeline.py`), so a feature may ask for one without
    caring whether the ledger has been swept yet.
    """

    creature: Combatant
    time: TimeKey


@_context
class RestContext(Context):
    """`on_short_rest` / `on_long_rest` — SELF: the owner rests.

    One type for two hooks, with `is_long_rest` telling them apart, because
    almost everything that attaches here (hit dice, Song of Rest, recharges)
    reads the same fields and differs only in which boundary it woke on.
    """

    creature: Combatant
    is_long_rest: bool


# --- Attack -----------------------------------------------------------------


@_context
class AttackDeclaredContext(D20TestContext):
    """`on_declare_attack` (SELF) and `on_incoming_attack_declared` (TARGET) —
    one attack, before the roll, seen from each end.

    The scope is the whole difference between the two: Reckless Attack grants
    itself advantage on the `SELF` side, cover and Dodge work the `TARGET` side,
    and neither ever writes `if ctx.attacker is self.owner` (DESIGN.md § Hook
    scope). `bonus` is the attacker's — it is the roll — and `ac_bonus` is the
    defender's, which is the only lever the target has here that is not
    disadvantage.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    ability: Ability
    ac_bonus: ModifierBag = field(default_factory=ModifierBag)


@_context
class AttackRollContext(Context):
    """`on_attack_rolled` — SELF: the d20 is on the table, AC not yet consulted.

    Read-only about the roll itself. There is deliberately no mutator for the
    critical threshold: Improved Critical lowers the *character's* threshold, so
    it is a derived stat on the creature (w1a) that the engine reads when it
    builds this context, not something a handler edits per attack.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    natural: int
    total: int
    crit_threshold: int
    had_advantage: bool
    had_disadvantage: bool

    @property
    def is_critical(self) -> bool:
        """True when the natural roll met the attacker's critical threshold."""
        return self.natural >= self.crit_threshold


@_context
class HitContext(Context):
    """`on_hit` (SELF) and `on_hit_taken` (TARGET) — the attack landed.

    The busiest hook in the project: Sneak Attack, Divine Smite and the weapon
    masteries all attach here, which is why the pessimistic spike puts at least
    15 handlers on it (ROADMAP w0i). Damage rides on the shared bag, so a rider
    added here is rolled by `engine/damage.py` with everything else and doubles
    or not according to whether the author passed `dice` or `flat`.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage: DamageBag = field(default_factory=DamageBag)

    @property
    def is_critical(self) -> bool:
        return self.outcome is Outcome.CRITICAL_HIT


@_context
class MissContext(Context):
    """`on_miss` — SELF: the attack did not land.

    It carries a damage bag because a miss is not always nothing: the Graze
    mastery deals the ability modifier on a miss, and Great Weapon Fighting
    rerolls attach here too.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage: DamageBag = field(default_factory=DamageBag)


@_context
class AttackResolvedContext(Context):
    """`on_attack_resolved` — SELF: the attack is over, hit or miss.

    The place to spend or to count, after the outcome is known and nothing about
    it can change any more.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage_dealt: int


# --- Damage -----------------------------------------------------------------


@_context
class DamageRollContext(Context):
    """`on_damage_roll` — SELF: the attacker builds the damage.

    Rage damage, Savage Attacker and brutal dice attach here. `is_critical` is a
    field a handler may read but never has to act on: doubling happens in
    `DamagePart.on_crit()`, over dice only.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    is_critical: bool
    damage: DamageBag = field(default_factory=DamageBag)


@_context
class IncomingDamageContext(Context):
    """`on_incoming_damage` — TARGET: the victim's one chance to change it.

    Everything a handler does here is recorded, never applied: `engine/damage.py`
    (w1e) runs the single resolution order over the ledger afterwards. Uncanny
    Dodge halves, Heavy Armor Master reduces, Rage resists — and the order those
    three interact in is a decision made once, in one file, with the ruling for
    each non-quoted step cited inline.

    The damage arrives here **rolled**, as an amount per type, rather than as
    the `DamageBag` the attacker built. The dice were thrown at `on_damage_roll`
    and the resolution order is arithmetic over numbers per type; carrying the
    bag this far would leave `damage_type=None` — "the type of the attack this
    rides on" — unresolved at the one step that reads the type, and would offer
    the victim an `add()` that belongs to the attacker's side of the event.
    """

    defender: Combatant
    attacker: Combatant | None
    amounts: Mapping[DamageType, int]
    _mitigation: MitigationLedger = field(default_factory=MitigationLedger, repr=False)

    def add_resistance(self, damage_type: DamageType) -> None:
        """Resist `damage_type` for this instance of damage. Idempotent."""
        self._mitigation.resist(damage_type)

    def add_immunity(self, damage_type: DamageType) -> None:
        """Take no damage of `damage_type` from this instance."""
        self._mitigation.immune(damage_type)

    def add_vulnerability(self, damage_type: DamageType) -> None:
        """Take double `damage_type` damage from this instance."""
        self._mitigation.vulnerable(damage_type)

    def reduce(self, *, dice: str | Dice | None = None, flat: int = 0) -> Modifier:
        """Subtract a flat amount after resistance (Heavy Armor Master)."""
        return self._mitigation.reduce(dice, flat)

    def halve(self, reason: str) -> None:
        """Halve what is left after the reductions (Uncanny Dodge)."""
        self._mitigation.halve(reason)

    @property
    def mitigation(self) -> MitigationLedger:
        """What the handlers recorded, for `engine/damage.py` to apply."""
        return self._mitigation

    @property
    def total(self) -> int:
        """The damage before any of the above is applied.

        A sum, not a step of the resolution order — it is what a handler reads
        to decide whether this is the hit worth spending Uncanny Dodge on.
        """
        return sum(self.amounts.values())


@_context
class DamageTakenContext(Context):
    """`on_damage_taken` — TARGET: what actually landed, after the order ran."""

    defender: Combatant
    attacker: Combatant | None
    amount: int
    damage_types: frozenset[DamageType]


@_context
class ReducedToZeroContext(Context):
    """`on_reduced_to_zero` — TARGET: the owner drops to 0 hit points.

    `prevent()` is Relentless Endurance and Undead Fortitude: the creature stays
    up at 1 hit point instead. `overflow` is the damage left over past 0, which
    is what the instant-death rule reads.
    """

    defender: Combatant
    attacker: Combatant | None
    overflow: int
    _survival: OverrideLedger = field(default_factory=OverrideLedger, repr=False)

    def prevent(self, reason: str) -> None:
        """Stay up at 1 hit point instead of dropping."""
        self._survival.claim("ctx.prevent()", reason)

    @property
    def prevented(self) -> bool:
        """True when a handler kept the creature up."""
        return self._survival.claimed

    @property
    def prevented_by(self) -> tuple[str, ...]:
        return self._survival.reasons


@_context
class KillContext(Context):
    """`on_kill` — SELF: the owner dropped a creature to 0.

    Great Weapon Master's extra attack attaches here.
    """

    killer: Combatant
    victim: Combatant


# --- Healing & dying --------------------------------------------------------


@_context
class HealContext(Context):
    """`on_heal` — TARGET: the owner regains hit points.

    The owner is the creature whose hit points go up, matching
    `on_temp_hp_gained` and keeping the whole group about the recipient. The
    healer's side — 2024 Cleric's Disciple of Life — has nowhere to attach; that
    is a known gap for the W3 paper pass to confirm (`w3e`) and `w3f` to fill,
    not something invented here on speculation (`hooks/definitions.py`).

    `healing.add(dice="2d8")` does not double on anything, which is why healing
    is a `ModifierBag` and not a `DamageBag`.
    """

    creature: Combatant
    healer: Combatant | None
    healing: ModifierBag = field(default_factory=ModifierBag)


@_context
class TempHpContext(Context):
    """`on_temp_hp_gained` — TARGET: the owner gains temporary hit points.

    Observational: temp HP is the one step of the damage resolution order
    nothing else can see, and this hook exists so that something can
    (DESIGN.md § The closed hook list). The engine has already decided the
    amount by the time handlers run — 2024 temp HP does not stack, the larger
    pool simply wins — so there is nothing here for a handler to add to.
    """

    creature: Combatant
    amount: int
    replaced: int


@_context
class DeathSaveContext(D20TestContext):
    """`on_death_save` — SELF: the owner rolls a death saving throw."""

    creature: Combatant


@_context
class StabilizeContext(Context):
    """`on_stabilize` — SELF: the owner stops dying without regaining hit points."""

    creature: Combatant


# --- Checks & saves ---------------------------------------------------------


@_context
class AbilityCheckContext(D20TestContext):
    """`on_ability_check` — SELF: the owner makes an ability check.

    `skill` is the skill's name or `None` for a bare ability check; `dc` is
    `None` for a contested check, which has no fixed number to beat.
    """

    creature: Combatant
    ability: Ability
    skill: str | None = None
    dc: int | None = None


@_context
class ForcedSaveContext(Context):
    """`on_forced_save` — SELF: the owner is making somebody else save.

    The DC's side of the pair; `on_saving_throw` is the roll's. This is where a
    feature that raises its own save DC attaches, which is why the mutator is a
    bonus to the DC rather than anything about the roll.
    """

    creature: Combatant
    targets: tuple[Combatant, ...]
    ability: Ability
    dc: int
    dc_bonus: ModifierBag = field(default_factory=ModifierBag)


@_context
class SavingThrowContext(D20TestContext):
    """`on_saving_throw` — TARGET: the owner rolls the save.

    Danger Sense grants advantage here; Aura of Protection adds to the total
    through `bonus`.
    """

    creature: Combatant
    ability: Ability
    dc: int
    imposed_by: Combatant | None


@_context
class SaveResolvedContext(Context):
    """`on_save_resolved` — TARGET: the save has succeeded or failed.

    `succeed()` is Legendary Resistance: the roll happened and failed, and the
    creature declares a success anyway. Evasion is not here — turning a failed
    save into half damage is a damage-side change and belongs to
    `on_incoming_damage`.
    """

    creature: Combatant
    ability: Ability
    dc: int
    total: int
    succeeded: bool
    _override: OverrideLedger = field(default_factory=OverrideLedger, repr=False)

    def succeed(self, reason: str) -> None:
        """Declare this save a success regardless of what was rolled."""
        self._override.claim("ctx.succeed()", reason)

    @property
    def forced_success(self) -> bool:
        return self._override.claimed

    @property
    def forced_by(self) -> tuple[str, ...]:
        return self._override.reasons


# --- Casting ----------------------------------------------------------------


@_context
class CastSpellContext(Context):
    """`on_cast_spell` — SELF: the owner casts a spell."""

    caster: Combatant
    spell: Spell
    slot_level: int
    targets: tuple[Combatant, ...]


@_context
class ConcentrationContext(Context):
    """`on_concentration_start` — SELF: the owner begins concentrating.

    `replaced` is whatever it was concentrating on until now, which 2024 drops
    automatically; carrying it means a handler need not reconstruct what was
    lost.
    """

    creature: Combatant
    spell: Spell
    replaced: Spell | None


@_context
class ConcentrationCheckContext(D20TestContext):
    """`on_concentration_check` — SELF: the owner rolls to keep concentrating.

    War Caster grants advantage; `damage_taken` is what set the DC.
    """

    creature: Combatant
    dc: int
    damage_taken: int


# --- Effects ----------------------------------------------------------------


@_context
class ConditionContext(Context):
    """`on_condition_gained` / `on_condition_lost` — SELF: the owner's condition.

    `condition` is `rules.enums.Condition`, the **name** of a condition. The
    behaviour is a `HookSource` subclass in `conditions/` (w1h) — two different
    things wearing one word, which is why this annotation is worth reading
    twice.
    """

    creature: Combatant
    condition: Condition


@_context
class EffectContext(Context):
    """`on_effect_applied` / `on_effect_expired` — SELF: an effect on the owner.

    Both are `SELF` because an effect ending has no actor to be the other side
    of. Rage extending rather than stacking is decided here.
    """

    creature: Combatant
    effect: Effect


# --- Movement & resources ---------------------------------------------------


@_context
class MoveContext(Context):
    """`on_move` — SELF: the owner moves along a route the engine resolved.

    The route is the whole path, not just the destination, because what happens
    on the way out of somebody's reach is the point (`core/geometry.py`, w1d).
    """

    creature: Combatant
    route: tuple[Cell, ...]
    distance_ft: int


@_context
class LeaveReachContext(Context):
    """`on_leave_reach` — OBSERVER: somebody left the owner's reach.

    The project's one `OBSERVER` hook and the opportunity-attack trigger: the
    owner neither acted nor was acted upon, it merely has reach where the
    `mover` used to be.
    """

    creature: Combatant
    mover: Combatant
    origin: Cell
    destination: Cell


@_context
class ResourceSpentContext(Context):
    """`on_resource_spent` — SELF: the owner spent a slot, a rage, a die.

    `resource` is the pool's id (`slot:3`, `rage`, `superiority_die`), which is
    what `creatures/resources.py` (w1b) keys its pools by.
    """

    creature: Combatant
    resource: str
    amount: int
