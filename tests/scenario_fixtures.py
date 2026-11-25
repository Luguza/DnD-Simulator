"""The catalogue and scenario `sim/`'s tests fight: `builds/`'s level 5
Fighter against a target dummy. Every record is a fixture bound to its imported
entry by a `content` test."""

from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import yaml

from dndsim.builds.loader import Catalogue, parse_build
from dndsim.builds.schema import BuildSpec
from dndsim.core.geometry import Cell
from dndsim.creatures.dummy import DummySpec, MovementProfile
from dndsim.sim.runner import Scenario
from origin_fixtures import (
    FIGHTER,
    GREAT_WEAPON_FIGHTING,
    GREAT_WEAPON_MASTER,
    HUMAN,
    SAVAGE_ATTACKER,
    SOLDIER,
)
from weapon_fixtures import CHAIN_MAIL, GREATSWORD, LONGBOW, MAUL

BUILDS: Final = Path(__file__).parents[1] / "builds"

CATALOGUE: Final = Catalogue(
    classes={FIGHTER.name: FIGHTER},
    species={HUMAN.name: HUMAN},
    backgrounds={SOLDIER.name: SOLDIER},
    feats={
        f.name: f for f in (SAVAGE_ATTACKER, GREAT_WEAPON_FIGHTING, GREAT_WEAPON_MASTER)
    },
    weapons={w.name: w for w in (GREATSWORD, MAUL, LONGBOW)},
    armour={CHAIN_MAIL.name: CHAIN_MAIL},
)

FIGHTER_TEXT: Final = (BUILDS / "fighter_gwm_l5.yaml").read_text()


def fighter(**changes: Any) -> BuildSpec:
    """`builds/fighter_gwm_l5.yaml`, with `changes` written over it."""
    return parse_build(yaml.safe_dump({**yaml.safe_load(FIGHTER_TEXT), **changes}))


ADJACENT_DUMMY: Final = DummySpec(
    ac=15, hp=30, movement_profile=MovementProfile.static()
)
"""A static dummy at (0, 0), one square from a party at (0, 1)."""


def scenario(**changes: Any) -> Scenario:
    """The Fighter, at (0, 1), against `ADJACENT_DUMMY`."""
    base = Scenario((fighter(),), ADJACENT_DUMMY, CATALOGUE, Cell(0, 1))
    return replace(base, **changes)
