"""M1's Fighter and the catalogue it loads against, shared by the runner's
contract tests and the M1 golden. The build is `builds/fighter_gwm_l5.yaml`."""

from pathlib import Path
from typing import Final

from dndsim.builds.loader import Catalogue, parse_build
from dndsim.builds.schema import BuildSpec
from origin_fixtures import (
    FIGHTER,
    GREAT_WEAPON_FIGHTING,
    GREAT_WEAPON_MASTER,
    HUMAN,
    SOLDIER,
)
from weapon_fixtures import CHAIN_MAIL, GREATSWORD, LONGBOW, MAUL

BUILDS: Final = Path(__file__).parents[1] / "builds"

CATALOGUE: Final = Catalogue(
    classes={FIGHTER.name: FIGHTER},
    species={HUMAN.name: HUMAN},
    backgrounds={SOLDIER.name: SOLDIER},
    feats={f.name: f for f in (GREAT_WEAPON_FIGHTING, GREAT_WEAPON_MASTER)},
    weapons={w.name: w for w in (GREATSWORD, LONGBOW, MAUL)},
    armour={CHAIN_MAIL.name: CHAIN_MAIL},
)


def fighter() -> BuildSpec:
    """M1's level 5 Fighter, as written in `builds/`."""
    return parse_build((BUILDS / "fighter_gwm_l5.yaml").read_text())
