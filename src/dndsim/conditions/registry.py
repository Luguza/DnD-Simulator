"""The closed mapping from a condition's name to the class that implements it.

Sources: XPHB (2024) Rules Glossary "Condition"; `docs/decisions.md` § w1h.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from dndsim.conditions.base import Condition
from dndsim.conditions.blinded import Blinded
from dndsim.conditions.charmed import Charmed
from dndsim.conditions.deafened import Deafened
from dndsim.conditions.exhaustion import Exhaustion
from dndsim.conditions.frightened import Frightened
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.invisible import Invisible
from dndsim.conditions.paralyzed import Paralyzed
from dndsim.conditions.petrified import Petrified
from dndsim.conditions.poisoned import Poisoned
from dndsim.conditions.prone import Prone
from dndsim.conditions.restrained import Restrained
from dndsim.conditions.stunned import Stunned
from dndsim.conditions.unconscious import Unconscious
from dndsim.rules import enums

__all__ = ["CONDITIONS"]

_IMPLEMENTATIONS: Final[tuple[type[Condition], ...]] = (
    Blinded,
    Charmed,
    Deafened,
    Exhaustion,
    Frightened,
    Grappled,
    Incapacitated,
    Invisible,
    Paralyzed,
    Petrified,
    Poisoned,
    Prone,
    Restrained,
    Stunned,
    Unconscious,
)

CONDITIONS: Final[Mapping[enums.Condition, type[Condition]]] = MappingProxyType(
    {implementation.CONDITION: implementation for implementation in _IMPLEMENTATIONS}
)
"""Every member of `rules.enums.Condition`, and the class that implements it."""
