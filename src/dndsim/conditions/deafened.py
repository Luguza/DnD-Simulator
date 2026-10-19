"""Deafened (XPHB, Rules Glossary). See `docs/decisions.md` § w1h."""

from dndsim.conditions.base import Condition
from dndsim.rules import enums

__all__ = ["Deafened"]


class Deafened(Condition):
    """You cannot hear.

    The condition's one clause is an ability check, so it carries no handler:
    nothing in a combat round is decided by hearing.
    """

    __slots__ = ()

    CONDITION = enums.Condition.DEAFENED
    NEEDS_HOOK = (
        "AbilityCheckContext has no way to fail a check, and no check declares "
        "that it requires hearing: \"You can't hear and automatically fail any "
        'ability check that requires hearing." '
        "(ROADMAP.md § The W0 debt register #38)",
    )
