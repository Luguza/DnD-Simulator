"""`Condition` — the shape the 15 share, and the two ways a clause can fail.

A condition is an `Effect` (DESIGN.md § Durations: "one API shape for all
four"), so everything about attaching, expiring and stacking is already
`creatures/effects.py`'s. What this module adds is the link back to the rules
vocabulary and the two declarations a clause that cannot be written makes.

Sources: XPHB (2024) Rules Glossary "Condition"; DESIGN.md § Durations;
`docs/decisions.md` § w1h.
"""

from typing import Any, ClassVar

from dndsim.core.timeline import Duration
from dndsim.creatures.effects import Effect
from dndsim.hooks.contexts import Combatant
from dndsim.rules import enums

__all__ = ["Condition", "SourcedCondition"]


def _accumulate(cls: "type[Condition]", field: str) -> tuple[str, ...]:
    """Every entry `field` carries in the MRO, base-first, without duplicates.

    Why a subclass accumulates rather than replaces: `docs/decisions.md` § w1h.
    """
    seen: dict[str, None] = {}
    for klass in reversed(cls.__mro__):
        for entry in vars(klass).get(field, ()):
            seen[entry] = None
    return tuple(seen)


class Condition(Effect):
    """One of the 15 named conditions, carrying the name it is the behaviour of.

    `STACKING` is `Effect`'s inherited `REFRESH`, which is the glossary's "A
    condition doesn't stack with itself; a recipient either has a condition or
    doesn't. The Exhaustion condition is an exception to that rule."
    """

    __slots__ = ()

    CONDITION: ClassVar[enums.Condition]
    """The `rules/enums.py` member this class implements."""

    NEEDS_HOOK: ClassVar[tuple[str, ...]] = ()
    """Clauses that cannot be expressed at all, as `<what is missing>: <clause>`.

    The escape valve `ROADMAP.md` § Standing rules, 2 names, one entry per
    clause and each naming the debt row that owns it; `__init_subclass__`
    accumulates them down the hierarchy (`docs/decisions.md` § w1h).
    """

    APPROXIMATED: ClassVar[tuple[str, ...]] = ()
    """Clauses expressed lossily, as `<what is lost>`. The other escape valve."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.NEEDS_HOOK = _accumulate(cls, "NEEDS_HOOK")
        cls.APPROXIMATED = _accumulate(cls, "APPROXIMATED")


class SourcedCondition(Condition):
    """A condition whose rules text names the creature that imposed it — the
    charmer, the grappler, the source of fear.

    `caster` is `Effect`'s field for that creature, and is required rather than
    optional here.
    """

    __slots__ = ()

    def __init__(
        self,
        owner: Combatant,
        *,
        duration: Duration | None = None,
        charges: int | None = None,
        caster: Combatant | None = None,
    ) -> None:
        if caster is None:
            raise ValueError(
                f"{type(self).ID} was applied with no caster, and its rules "
                "text is written about the creature that imposed it"
            )
        super().__init__(owner, duration=duration, charges=charges, caster=caster)
