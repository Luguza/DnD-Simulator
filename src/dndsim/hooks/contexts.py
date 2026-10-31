"""The 31 Parameter Objects the 39 hooks hand to a handler, the contributions
and ledgers a handler appends to, and the participant protocols they carry.

Every context is attribute-locked: assigning a field raises `AttributeError`,
and a feature changes an event only through a mutator. A mutator raises
`PhaseError` after `close()` or with no source stamped, and every contribution
is attributed to the stamped source. `reopen()` rebinds a pooled context to a
new event. `docs/decisions.md` § w0f has the design, § w0i the pooling.
"""

import dataclasses
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import (
    ClassVar,
    Final,
    NoReturn,
    Protocol,
    TypeVar,
    dataclass_transform,
)

from dndsim.core.dice import Dice
from dndsim.core.errors import EngineAssemblyError, PhaseError
from dndsim.core.timeline import InitiativeKey, TimeKey
from dndsim.hooks.registry import Phase
from dndsim.rules.enums import (
    Ability,
    Condition,
    DamageType,
    Mastery,
    Outcome,
    WeaponProperty,
)

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
# Members are read-only properties so that a frozen dataclass satisfies them.


class Combatant(Protocol):
    """A creature as a context needs to know it: a name, a place in the
    initiative order, and its hit points.

    `hp` excludes `temp_hp`: Bloodied reads `hp` alone, and `hp + temp_hp` is
    the pool a hit is spent against (`docs/decisions.md` § w1a).
    """

    @property
    def name(self) -> str: ...

    @property
    def initiative(self) -> InitiativeKey: ...

    @property
    def hp(self) -> int: ...

    @property
    def max_hp(self) -> int: ...

    @property
    def temp_hp(self) -> int: ...


class Weapon(Protocol):
    """The weapon an attack is made with: its damage, mastery, reach, and
    `has()` for one weapon property. No range yet (debt #24;
    `docs/decisions.md` § w1f)."""

    @property
    def name(self) -> str: ...

    @property
    def damage(self) -> Dice: ...

    @property
    def damage_type(self) -> DamageType: ...

    @property
    def mastery(self) -> Mastery | None: ...

    @property
    def reach_ft(self) -> int: ...

    def has(self, prop: WeaponProperty) -> bool: ...


class Spell(Protocol):
    """A spell being cast or concentrated on."""

    @property
    def name(self) -> str: ...

    @property
    def level(self) -> int: ...


class Effect(Protocol):
    """An effect on its way onto a creature."""

    @property
    def name(self) -> str: ...


class Cell(Protocol):
    """One 5-foot square of the grid."""

    @property
    def x(self) -> int: ...

    @property
    def y(self) -> int: ...


# --- What a feature contributes --------------------------------------------


@dataclass(frozen=True, slots=True)
class DamagePart:
    """One contribution to a damage roll, attributed to whoever made it.

    **`dice` double on a critical hit and `flat` never does** (DESIGN.md
    § Structural prevention over testing). `damage_type=None` means the type of
    the attack this rides on (`docs/decisions.md` § w0f).
    """

    dice: Dice | None
    flat: int
    damage_type: DamageType | None
    source: str

    def on_crit(self) -> "DamagePart":
        """This contribution as it lands on a critical hit: dice doubled."""
        dice = self.dice
        if dice is None:
            return self
        return DamagePart(dice.doubled(), self.flat, self.damage_type, self.source)

    def __str__(self) -> str:
        return f"{_amount(self.dice, self.flat)} [{self.source}]"


@dataclass(frozen=True, slots=True)
class Modifier:
    """A contribution whose dice do **not** double: a bonus to a D20 Test,
    healing, temporary hit points, a bump to a save DC or to AC
    (`docs/decisions.md` § w0f)."""

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

    A bag holds its context's frame, from which every mutation reads whether
    the context is live and which source to attribute it to.
    """

    __slots__ = ("_frame",)

    def __init__(self) -> None:
        self._frame: _Frame | None = None

    def _bind(self, frame: "_Frame") -> None:
        """Attach this bag to its context's frame. The context does this once."""
        self._frame = frame

    def _acting_source(self, action: str) -> str:
        """Who is acting, or a `PhaseError` saying why nobody is.

        The two `add()` methods inline the happy path of this
        (`docs/decisions.md` § w0i); a change
        to the checks here belongs there too.
        """
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

    Append-only: a feature contributes a part and can read the whole. Nothing
    here rolls or totals the dice.
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
        Raises `ValueError` when both `dice` and `flat` are empty.
        """
        frame = self._frame
        if frame is None or frame.source is None or not frame.open:
            source = self._acting_source("ctx.damage.add()")
        else:
            source = frame.source
        if dice is not None and not isinstance(dice, Dice):
            dice = Dice.parse(dice)
        elif dice is None and flat == 0:
            raise ValueError(
                f"{source} added no damage: pass dice, flat, or both. An empty "
                "contribution is a feature that meant to return SKIPPED."
            )
        part = DamagePart(dice, flat, damage_type, source)
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

    The same append-only shape as `DamageBag`, over `Modifier`.
    """

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        _Bag.__init__(self)
        self._parts: list[Modifier] = []

    def add(self, *, dice: str | Dice | None = None, flat: int = 0) -> Modifier:
        """Contribute a modifier."""
        frame = self._frame
        if frame is None or frame.source is None or not frame.open:
            source = self._acting_source("add()")
        else:
            source = frame.source
        if dice is not None and not isinstance(dice, Dice):
            dice = Dice.parse(dice)
        elif dice is None and flat == 0:
            raise ValueError(
                f"{source} added nothing: pass dice, flat, or both. An empty "
                "contribution is a feature that meant to return SKIPPED."
            )
        modifier = Modifier(dice, flat, source)
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

    Two collections rather than a boolean; `had_advantage` and
    `had_disadvantage` derive the result with cancellation applied (DESIGN.md
    § Structural prevention over testing).
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
    """Accept `"2d6"` or a shared `Dice`; `Dice.parse` rejects `"1d8+3"`."""
    if dice is None or isinstance(dice, Dice):
        return dice
    return Dice.parse(dice)


# --- The lifecycle a mutator is checked against -----------------------------


class _Frame:
    """The one mutable cell in an otherwise frozen context: the provenance
    stamp, the current handler's band, whether the moment is open, and the
    veto if one was cast."""

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
    """The source to attribute `action` to, or `PhaseError` when the context is
    closed or has no source stamped."""

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


def _uncompiled_reopen(self: object, **fields: object) -> NoReturn:
    """The `reopen()` a context class that escaped `@_context` is left holding.
    Raises `EngineAssemblyError` (`docs/decisions.md` § w0i)."""
    raise EngineAssemblyError(
        f"{type(self).__name__} has no compiled reopen(). Every context "
        "class is built by the @_context decorator, which generates one "
        "from the field list; a class that reached here was declared some "
        "other way."
    )


@dataclass_transform(frozen_default=True, kw_only_default=True, eq_default=False)
def _context(cls: type[_T]) -> type[_T]:
    """Make a context class: frozen, slotted, keyword-only, identity-compared,
    with a locked `__setattr__` and a compiled `reopen()` (`docs/decisions.md`
    § w0f)."""
    made = dataclass(frozen=True, slots=True, kw_only=True, eq=False)(cls)
    made.__setattr__ = _locked_setattr  # type: ignore[method-assign]
    fields = dataclasses.fields(made)  # type: ignore[arg-type]
    made._field_names = frozenset(f.name for f in fields)  # type: ignore[attr-defined]
    bags: list[str] = []
    owned: list[str] = []
    required: list[str] = []
    defaults: list[tuple[str, object]] = []
    for spec in fields:
        # Typed as `object` because `default_factory` is a callable union that
        # mypy narrows past `type` into nothing at all.
        factory: object = spec.default_factory
        annotation: object = spec.type
        if isinstance(factory, type) and issubclass(factory, _Bag):
            bags.append(spec.name)
            owned.append(spec.name)
        elif isinstance(annotation, type) and issubclass(annotation, _Bag):
            # A bag with nowhere to come from is one this context does not own.
            bags.append(spec.name)
            required.append(spec.name)
        elif factory is not dataclasses.MISSING:
            pass  # `_frame` and `_pending`: reset in place by `reopen()`.
        elif spec.default is not dataclasses.MISSING:
            defaults.append((spec.name, spec.default))
        else:
            required.append(spec.name)
    made._bag_fields = tuple(bags)  # type: ignore[attr-defined]
    made._owned_bags = tuple(owned)  # type: ignore[attr-defined]
    made._required_fields = tuple(required)  # type: ignore[attr-defined]
    made._defaulted_fields = tuple(defaults)  # type: ignore[attr-defined]
    made.reopen = _make_reopen(  # type: ignore[attr-defined]
        made, required, defaults, owned, [b for b in bags if b in required]
    )
    return made


def _make_reopen(
    made: type,
    required: Sequence[str],
    defaults: Sequence[tuple[str, object]],
    owned_bags: Sequence[str],
    handed_bags: Sequence[str],
) -> Callable[..., None]:
    """Compile this context class's `reopen()` — see `Context.reopen`.

    The fields are keyword-only parameters, so a missing or unknown field is a
    `TypeError` from argument binding (`docs/decisions.md` § w0i).
    """
    parameters = list(required)
    namespace: dict[str, object] = {}
    lines = []
    for name in required:
        namespace[f"_set_{name}"] = getattr(made, name).__set__
        lines.append(f"    _set_{name}(self, {name})")
    for name, default in defaults:
        parameters.append(f"{name}=_default_{name}")
        namespace[f"_default_{name}"] = default
        namespace[f"_set_{name}"] = getattr(made, name).__set__
        lines.append(f"    _set_{name}(self, {name})")
    lines.append("    frame = self._frame")
    lines.append("    frame.source = None")
    lines.append("    frame.phase = None")
    lines.append("    frame.open = True")
    lines.append("    frame.veto_reason = None")
    lines.append("    self._pending.clear()")
    for name in handed_bags:
        # A handed bag is rebound, never cleared: it holds the event's damage.
        lines.append(f"    {name}._bind(frame)")
    for name in owned_bags:
        lines.append(f"    self.{name}.clear()")
    # `RoundContext` and its kin carry one field or none, and `def f(self, *,)`
    # is a syntax error rather than an empty keyword list.
    signature = "self, *, " + ", ".join(parameters) if parameters else "self"
    body = "\n".join(lines)
    source = f"def reopen({signature}) -> None:\n{body}\n"
    exec(source, namespace)  # noqa: S102 — built here from this class's own
    # field names, which come from the class body and never from input.
    reopen: Callable[..., None] = namespace["reopen"]  # type: ignore[assignment]
    reopen.__qualname__ = f"{made.__name__}.reopen"
    reopen.__doc__ = (
        f"Rebind this pooled {made.__name__} to a new event. Every field it "
        "does not default must be named; see `Context.reopen`."
    )
    return reopen


@_context
class Context:
    """What every hook hands its handlers: the lifecycle every mutator is
    checked against, the veto, and the queue of effects.

    **Effects are queued, not applied.** `ctx.apply_effect(...)` appends; the
    engine drains the queue after the dispatch, so an effect applied during a
    hook never runs in that hook — the snapshot rule (`docs/decisions.md`
    § w0g, § w1c).
    """

    _field_names: ClassVar[frozenset[str]] = frozenset()
    _bag_fields: ClassVar[tuple[str, ...]] = ()
    _owned_bags: ClassVar[tuple[str, ...]] = ()
    _required_fields: ClassVar[tuple[str, ...]] = ()
    _defaulted_fields: ClassVar[tuple[tuple[str, object], ...]] = ()

    _frame: _Frame = field(default_factory=_Frame, repr=False)
    _pending: list[tuple[Combatant, Effect]] = field(default_factory=list, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Leave every subclass holding a `reopen()` that raises until
        `@_context` replaces it; a subclass declaring its own `reopen` keeps it
        (`docs/decisions.md` § w0i)."""
        # Not zero-argument `super()`, whatever UP008 says: `@_context`
        # re-creates this class to add slots, so a bare `super()`'s `__class__`
        # cell points at the discarded class and every subclass raises
        # `TypeError` at class creation.
        super(Context, cls).__init_subclass__(**kwargs)  # noqa: UP008
        if "reopen" not in cls.__dict__:
            cls.reopen = _uncompiled_reopen  # type: ignore[method-assign]

    def __post_init__(self) -> None:
        self._frame.owner = type(self).__name__
        self._bind_bags()

    def _bind_bags(self) -> None:
        """Point every bag on this context, owned or handed in, at this
        context's frame."""
        frame = self._frame
        for name in self._bag_fields:
            bag: _Bag = getattr(self, name)
            bag._bind(frame)

    # -- the dispatcher's side; features never call these --------------------

    def stamp(self, source: str | None, phase: Phase | None) -> None:
        """Name the source about to run, and the band it declared.

        Every mutation made while the stamp stands is attributed to `source`.
        `phase=None` is the engine's own seeding, from which `veto()` refuses; a
        `source` of `None` releases the stamp (`docs/decisions.md` § w0g).
        """
        frame = self._frame
        frame.source = source
        frame.phase = phase

    def stamped(self) -> tuple[str | None, Phase | None]:
        """The stamp currently standing, for a dispatcher to restore afterwards."""
        frame = self._frame
        return frame.source, frame.phase

    def close(self) -> None:
        """End the moment. Every mutator raises `PhaseError` from here on."""
        frame = self._frame
        frame.open = False
        frame.source = None
        frame.phase = None

    def reopen(self, **fields: object) -> None:
        """Rebind a pooled context to a new event and clear what it accumulated.

        **Demands every field the context does not default** — a missing one
        is a `TypeError`. Defaulted fields go back to their defaults, bags this
        context created are cleared in place, and a bag it was handed is
        required and rebound rather than cleared. Reopens the frame and empties
        the effect queue.

        Each context class replaces this with a version compiled by
        `_make_reopen`; this body runs only for a class that escaped
        `@_context`, and raises (`docs/decisions.md` § w0f, § w0i).
        """
        _uncompiled_reopen(self, **fields)

    # -- what a feature may do -----------------------------------------------

    def veto(self, reason: str) -> None:
        """Halt the chain: this event does not happen.

        Raises `PhaseError` from any band but `EARLY` or `FINAL` (DESIGN.md
        § Ordering).
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
    """A context for a D20 Test: attack, check, save, initiative, death save,
    concentration. Carries advantage and a `bonus` to the total."""

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

    Recorded, never applied. The three type collections are **sets**, so a type
    is resisted once however many handlers grant it; `_halvings` is a **list**,
    so two halvings halve twice (`docs/decisions.md` § w0g2, § w1e).
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

    @property
    def untouched(self) -> bool:
        """True when nothing has been recorded."""
        return not (
            self._resist
            or self._immune
            or self._vulnerable
            or self._reductions
            or self._halvings
        )

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

    Every claim's reason is kept, in order; one claim is enough to hold.
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
    """`on_encounter_start` / `on_encounter_end` — GLOBAL: the fight itself."""

    combatants: tuple[Combatant, ...]


@_context
class InitiativeContext(D20TestContext):
    """`on_initiative_roll` — SELF: the owner rolls for its seat."""

    creature: Combatant


@_context
class RoundContext(Context):
    """`on_round_start` / `on_round_end` — GLOBAL: a round of the encounter."""

    round: int


@_context
class TurnContext(Context):
    """`on_turn_start` / `on_turn_end` — SELF: the owner's turn.

    `Reset.OWN_TURN` budgets are already back when `on_turn_start` runs
    (`core/timeline.py`).
    """

    creature: Combatant
    time: TimeKey


@_context
class RestContext(Context):
    """`on_short_rest` / `on_long_rest` — SELF: the owner rests; `is_long_rest`
    tells the two apart."""

    creature: Combatant
    is_long_rest: bool


# --- Attack -----------------------------------------------------------------


@_context
class AttackDeclaredContext(D20TestContext):
    """`on_declare_attack` (SELF) and `on_incoming_attack_declared` (TARGET) —
    one attack, before the roll, seen from each end.

    `bonus` is added to the attack roll; `ac_bonus` to the defender's AC.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    ability: Ability
    ac_bonus: ModifierBag = field(default_factory=ModifierBag)


@_context
class AttackRollContext(Context):
    """`on_attack_rolled` — SELF: the d20 is on the table, AC not yet consulted.

    Read-only. `crit_threshold` is the attacker's derived stat and has no
    mutator (`docs/decisions.md` § w1a).
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

    `damage` is the attack's own bag, handed in; a rider added here is rolled
    with the rest of the attack's damage.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage: DamageBag

    @property
    def is_critical(self) -> bool:
        """`outcome.is_critical`."""
        return self.outcome.is_critical


@_context
class MissContext(Context):
    """`on_miss` — SELF: the attack did not land.

    `damage` is the attack's own bag, handed in, for damage dealt on a miss
    (`docs/decisions.md` § w1f).
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage: DamageBag


@_context
class AttackResolvedContext(Context):
    """`on_attack_resolved` — SELF: the attack is over, hit or miss."""

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    outcome: Outcome
    damage_dealt: int


# --- Damage -----------------------------------------------------------------


@_context
class DamageRollContext(Context):
    """`on_damage_roll` — SELF: the attacker builds the damage.

    `is_critical` is for reading only: the engine doubles the dice of every
    part on a critical hit.
    """

    attacker: Combatant
    defender: Combatant
    weapon: Weapon | None
    is_critical: bool
    damage: DamageBag


@_context
class IncomingDamageContext(Context):
    """`on_incoming_damage` — TARGET: the victim's one chance to change it.

    `amounts` is the damage already rolled, per type. Everything a handler does
    here is recorded in `mitigation`, never applied (`docs/decisions.md` § w0f,
    § w1e).
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
        """Record a reduction of the whole hit, untyped (`docs/decisions.md`
        § w1e)."""
        return self._mitigation.reduce(dice, flat)

    def halve(self, reason: str) -> None:
        """Record a halving of the whole hit (`docs/decisions.md` § w1e)."""
        self._mitigation.halve(reason)

    @property
    def mitigation(self) -> MitigationLedger:
        """What the handlers recorded, for the engine to apply."""
        return self._mitigation

    @property
    def total(self) -> int:
        """The sum of `amounts`, before any mitigation."""
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

    `overflow` is the damage left over past 0.
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
    """`on_kill` — SELF: the owner dropped a creature to 0."""

    killer: Combatant
    victim: Combatant


# --- Healing & dying --------------------------------------------------------


@_context
class HealContext(Context):
    """`on_heal` — TARGET: the owner regains hit points; `healing` is what it
    regains (`docs/decisions.md` § w0e, debt #17)."""

    creature: Combatant
    healer: Combatant | None
    healing: ModifierBag = field(default_factory=ModifierBag)


@_context
class TempHpContext(Context):
    """`on_temp_hp_gained` — TARGET: the owner gains temporary hit points.

    Observational: `amount` is already decided, and `replaced` is the pool it
    displaced.
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
    `dc_bonus` is added to the DC."""

    creature: Combatant
    targets: tuple[Combatant, ...]
    ability: Ability
    dc: int
    dc_bonus: ModifierBag = field(default_factory=ModifierBag)


@_context
class SavingThrowContext(D20TestContext):
    """`on_saving_throw` — TARGET: the owner rolls the save."""

    creature: Combatant
    ability: Ability
    dc: int
    imposed_by: Combatant | None


@_context
class SaveResolvedContext(Context):
    """`on_save_resolved` — TARGET: the save has succeeded or failed;
    `succeed()` declares a success whatever was rolled."""

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

    `replaced` is what it was concentrating on until now, or `None`.
    """

    creature: Combatant
    spell: Spell
    replaced: Spell | None


@_context
class ConcentrationCheckContext(D20TestContext):
    """`on_concentration_check` — SELF: the owner rolls to keep concentrating.

    `damage_taken` is what set the DC.
    """

    creature: Combatant
    dc: int
    damage_taken: int


# --- Effects ----------------------------------------------------------------


@_context
class ConditionContext(Context):
    """`on_condition_gained` / `on_condition_lost` — SELF: the owner's condition.

    `condition` is `rules.enums.Condition`, the condition's **name**, not the
    `HookSource` that implements it. Neither hook is vetoable.
    """

    creature: Combatant
    condition: Condition
    _drop: OverrideLedger = field(default_factory=OverrideLedger, repr=False)

    def drop_held(self, reason: str) -> None:
        """Queue the owner dropping whatever it is holding, once this event is
        over (`docs/decisions.md` § w2b)."""
        self._drop.claim("ctx.drop_held()", reason)

    @property
    def drops_held(self) -> bool:
        """True once a handler has queued the drop."""
        return self._drop.claimed

    @property
    def dropped_by(self) -> tuple[str, ...]:
        return self._drop.reasons


@_context
class EffectContext(Context):
    """`on_effect_applied` / `on_effect_expired` — SELF: an effect on the owner."""

    creature: Combatant
    effect: Effect


# --- Movement & resources ---------------------------------------------------


@_context
class MoveContext(Context):
    """`on_move` — SELF: the owner moves along a route the engine resolved.

    `route` is the whole path, not just the destination.
    """

    creature: Combatant
    route: tuple[Cell, ...]
    distance_ft: int


@_context
class LeaveReachContext(Context):
    """`on_leave_reach` — OBSERVER: somebody is leaving the owner's reach.

    **One five-foot step, not a whole move.** `origin` is the square the mover
    is standing on *now*, and still stands on while the reaction resolves;
    `destination` is the adjacent square it is about to step into
    (`docs/decisions.md` § w0g3).
    """

    creature: Combatant
    mover: Combatant
    origin: Cell
    destination: Cell


@_context
class ResourceSpentContext(Context):
    """`on_resource_spent` — SELF: the owner spent a slot, a rage, a die.

    `resource` is the pool's id (`slot:3`, `rage`, `superiority_die`).
    """

    creature: Combatant
    resource: str
    amount: int
