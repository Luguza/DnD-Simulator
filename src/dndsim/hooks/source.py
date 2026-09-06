"""`HookSource` — the one shape a feature, a condition, an effect or an item has.

Four kinds of thing plug into the hook pipeline and DESIGN.md is emphatic that
they are the same kind of thing: "`Effect`, `Condition`, `Feature` and magic
items are all `HookSource` subclasses with one identical API shape, so learning
one teaches all four". This class is that shape, and it is deliberately almost
empty — an id, an owner, and the handlers the class declared. Everything else a
feature needs is on the context it is handed.

**Validation is a Template Method that subclasses cannot skip.** DESIGN.md §
Hook system, property 2 asks for signature checking at class creation, and
`__init_subclass__` is the one hook in Python a subclass cannot forget to call:
there is no `super()` for an author to omit, no decorator to leave off, and no
base method to override into silence. Every declaration is re-validated for
every subclass, which costs nothing (it happens once, at import) and means an
inherited handler is checked in the class that actually uses it.

Four mistakes are refused here, and each of them is a handler that would
otherwise never run and never say so:

- **A signature that is not `(self, ctx) -> R`.** Anything else means the
  dispatcher would call it wrongly.
- **An annotation naming the wrong context.** `on_hit` hands out a
  `HitContext`; a handler annotated `AttackRollContext` would read fields that
  are not there. The check is an identity comparison against the class the
  registry names, so a same-named look-alike in the author's own module is not
  the context either.
- **A missing return annotation.** Without it neither mypy nor the reader knows
  the method is a handler, and a handler that forgets to return is the most
  common way a `once_per` budget quietly stops being spent.
- **An override that dropped its `@hook`.** The base's declaration is still in
  the MRO, so the *base's* body would keep running while the author reads their
  own. This is the only one of the four that is invisible in a diff.

**The id is provenance.** `parts_by_source["rogue.sneak_attack"]` in a golden
test, the `trace` output attributing a line to the feature that produced it, and
the no-op detector's report are all this string, stamped automatically by the
dispatcher. A class that does not declare one gets its own name in snake_case,
which is enough for a condition or an effect; features override it with their
registry id (w3a).
"""

import inspect
import re
from typing import Any, ClassVar, Final, get_type_hints

from dndsim.core.errors import FeatureDeclarationError
from dndsim.hooks import contexts as contexts_module
from dndsim.hooks.contexts import Combatant
from dndsim.hooks.decorator import Handler, HookResult, declared_handler

__all__ = ["HookSource"]

_ID: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_ACRONYM: Final = re.compile(r"(.)([A-Z][a-z]+)")
_BOUNDARY: Final = re.compile(r"([a-z0-9])([A-Z])")


class HookSource:
    """Something attached to a creature that answers when the engine asks.

    Subclasses declare handlers with `@hook` and are attached to a
    `Dispatcher`; `self.owner` is the creature the handlers belong to, and
    *which* creature that is in any given handler is decided by the hook's
    `Scope` rather than by the feature (DESIGN.md § Hook scope). In an
    `on_hit_taken` handler the owner is the victim, with no prose and no
    `if ctx.attacker is self.owner` needed.
    """

    __slots__ = ("owner",)

    ID: ClassVar[str] = "hook_source"
    """This source's provenance string. Declare it to override the default."""

    HANDLERS: ClassVar[tuple[Handler, ...]] = ()
    """Every declaration this class has, its bases' included, in a fixed order.

    Built once per class at import. The dispatcher reads it at attach time and
    never afterwards, so the per-creature dispatch table is assembled from
    tuples that were already sorted out before the simulation started.
    """

    def __init__(self, owner: Combatant) -> None:
        self.owner = owner

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.ID = _identify(cls)
        cls.HANDLERS = _collect(cls)

    def __repr__(self) -> str:
        return f"<{self.ID} of {self.owner.name}>"


def _identify(cls: type[HookSource]) -> str:
    """This class's own declared id, or its name in snake_case.

    `cls.__dict__` rather than `cls.ID`, because an inherited id is the *base's*
    identity: `Feature` deriving "feature" must not make every feature in the
    project "feature".
    """
    declared = cls.__dict__.get("ID")
    if declared is None:
        return _BOUNDARY.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", cls.__name__)).lower()
    if not isinstance(declared, str) or not _ID.match(declared):
        raise FeatureDeclarationError(
            f"{cls.__name__}.ID = {declared!r} is not a source id — those are "
            "lowercase dotted words, as in 'rogue.sneak_attack'. It is what the "
            "damage breakdown, the trace and the no-op report call this feature."
        )
    return declared


def _collect(cls: type[HookSource]) -> tuple[Handler, ...]:
    """Every declaration in the MRO, base-first, with subclass overrides applied.

    Reverse MRO order so that a base's handlers run in the order the base
    declared them and a subclass's overriding method keeps its base's position:
    what an author changed is the body, and a silent reordering of everything
    else is not something an override should mean.
    """
    collected: dict[str, Handler] = {}
    for klass in reversed(cls.__mro__):
        for name, value in vars(klass).items():
            declared = declared_handler(value)
            if declared is not None:
                _validate(cls, name, declared)
                collected[name] = declared
            elif name in collected:
                raise FeatureDeclarationError(
                    f"{cls.__name__}.{name}() overrides a handler for "
                    f"{collected[name].spec.name} without declaring @hook, so "
                    "the base's version would keep running and this one would "
                    "never be called. Repeat the @hook line on the override."
                )
            elif _hidden_handler(value) is not None:
                raise FeatureDeclarationError(
                    f"{cls.__name__}.{name}() is a handler wrapped in "
                    "@staticmethod or @classmethod, where nothing will ever "
                    "find it. A handler is an instance method: it is called "
                    "with the source whose owner it acts for."
                )
    return tuple(collected.values())


def _hidden_handler(value: object) -> Handler | None:
    if isinstance(value, staticmethod | classmethod):
        return declared_handler(value.__func__)
    return None


def _validate(cls: type[HookSource], name: str, handler: Handler) -> None:
    """Refuse anything but `(self, ctx: <the hook's context>) -> R`."""
    where = f"{cls.__name__}.{name}() handling {handler.spec.name}"
    params = list(inspect.signature(handler.fn).parameters.values())
    if len(params) != 2 or params[0].name != "self":
        shape = ", ".join(p.name for p in params)
        raise FeatureDeclarationError(
            f"{where} takes ({shape}); a handler takes exactly (self, ctx). "
            "Everything the engine knows about the event is on the context, so "
            "there is never a second argument to add."
        )
    ctx_param = params[1]
    if ctx_param.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
        raise FeatureDeclarationError(
            f"{where} declares its context as {ctx_param!s}; the dispatcher "
            "passes it positionally, so it is a plain parameter."
        )
    expected = getattr(contexts_module, handler.spec.context, None)
    if expected is None:  # pragma: no cover - w0f's contract test forbids it
        raise FeatureDeclarationError(
            f"{handler.spec.name} names a context, {handler.spec.context!r}, "
            "that hooks/contexts.py does not define"
        )
    try:
        hints = get_type_hints(handler.fn)
    except NameError as unresolved:
        raise FeatureDeclarationError(
            f"{where} has an annotation that does not resolve ({unresolved}). "
            "Import the context for real rather than under TYPE_CHECKING — it "
            "is checked at class creation, not only by the type checker."
        ) from unresolved
    annotated = hints.get(ctx_param.name)
    if annotated is not expected:
        got = "nothing" if annotated is None else getattr(annotated, "__name__", "?")
        raise FeatureDeclarationError(
            f"{where} annotates its context as {got}, but that hook hands out a "
            f"{handler.spec.context}. Every hook has exactly one Parameter "
            "Object and the name of it is the fastest way to find out what you "
            "may read."
        )
    if hints.get("return") is not HookResult:
        raise FeatureDeclarationError(
            f"{where} does not return R. A handler answers R.APPLIED when it "
            "changed the event and R.SKIPPED when its conditions were not met; "
            "the difference is what decides whether a once_per budget or a "
            "reaction was spent."
        )
