"""The shape of a build file and a party file, as written.

Shape only: every name here is checked against the imported content by
`builds/loader.py`. `docs/decisions.md` § w2f.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BuildSpec", "Equipment", "FeatChoice", "PartySpec"]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Equipment(_Spec):
    """What the character wields, wears and carries on its arm; each named
    as the XPHB tables print it. `two_handed` holds a Versatile weapon in two
    hands."""

    weapon: str | None = None
    two_handed: bool = False
    armour: str | None = None
    shield: bool = False


class FeatChoice(_Spec):
    """The feat taken at one Ability Score Improvement level. `increases` is
    the Ability Score Improvement feat's choice, by ability name."""

    level: int
    feat: str
    increases: dict[str, int] | None = None


class BuildSpec(_Spec):
    """One single-classed character, levels 1-12.

    `abilities` are the scores before the background's increases, by
    ability name. `skills` are the class's choices; the background's are its
    own. `apl` is the action priority list's rows, `None` for the default
    policy.
    """

    name: str
    class_: str = Field(alias="class")
    level: int
    subclass: str | None = None
    species: str
    size: str | None = None
    background: str
    abilities: dict[str, int]
    background_increases: dict[str, int]
    skills: list[str]
    fighting_style: str | None = None
    weapon_mastery: list[str] = []
    feats: list[FeatChoice] = []
    equipment: Equipment = Equipment()
    apl: list[dict[str, Any]] | None = None


class PartySpec(_Spec):
    """A party: each member's build file, relative to the party file, and the
    condition on which the party retreats, `None` for never."""

    members: list[str]
    retreat_when: str | None = None
