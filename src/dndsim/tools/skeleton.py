"""The walking skeleton (ROADMAP w0h): one featureless creature attacking
another through the real dispatch path and the real attack and damage pipeline.

Two creatures with no `HookSource` attached, a fixed weapon, and a loop that
swings until the defender drops. `docs/decisions.md` § w0h, § w1f.
"""

import argparse
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from dndsim.core.geometry import Cell
from dndsim.core.rng import MersenneRng, Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, Armour, StatBlock
from dndsim.engine.attack import resolve_attack
from dndsim.rules.enums import Ability, Outcome

__all__ = [
    "build_dummy",
    "build_fighter",
    "main",
    "run_fight",
]

_FIGHTER_SCORES: Final[Mapping[Ability, int]] = MappingProxyType(
    {
        Ability.STRENGTH: 16,
        Ability.DEXTERITY: 12,
        Ability.CONSTITUTION: 14,
        Ability.INTELLIGENCE: 10,
        Ability.WISDOM: 10,
        Ability.CHARISMA: 10,
    }
)
_DUMMY_SCORES: Final[Mapping[Ability, int]] = MappingProxyType(
    dict.fromkeys(Ability, 10)
)

_CHAIN_MAIL: Final = Armour.heavy("Chain Mail", 16)

_MAX_ROUNDS: Final = 50
"""A safety cap, not a rule: a fight not over by 50 attacks is a bug in this
script (`docs/decisions.md` § w0h)."""


def build_fighter(name: str = "Fighter") -> Creature:
    """A featureless level-5 fighter swinging the spike's one weapon."""
    stats = StatBlock(abilities=_FIGHTER_SCORES, max_hp=44, level=5)
    return Creature(
        name=name,
        stats=stats,
        position=Cell(0, 0),
        weapon=GREATSWORD,
        armour=_CHAIN_MAIL,
    )


def build_dummy(name: str = "Dummy") -> Creature:
    """A featureless AC-15 dummy with nothing attached — the target."""
    stats = StatBlock(abilities=_DUMMY_SCORES, max_hp=40)
    return Creature(
        name=name, stats=stats, position=Cell(1, 0), armour=Armour.natural(15)
    )


def run_fight(
    attacker: Creature,
    defender: Creature,
    rng: Rng,
    *,
    on_attack: Callable[[int, Outcome | None, int], None] | None = None,
) -> int:
    """Swing until the defender drops or `_MAX_ROUNDS` is hit. Returns the
    number of attacks made."""
    rounds = 0
    while not defender.is_down and rounds < _MAX_ROUNDS:
        rounds += 1
        outcome, damage = resolve_attack(attacker, defender, rng=rng)
        if on_attack is not None:
            on_attack(rounds, outcome, damage)
    return rounds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one featureless creature at another through the real "
        "dispatch path and damage pipeline, and print the result (ROADMAP w0h)."
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="master seed for the fight's rng"
    )
    args = parser.parse_args(argv)

    attacker = build_fighter()
    defender = build_dummy()
    rng = MersenneRng(args.seed)

    def report(round_number: int, outcome: Outcome | None, damage: int) -> None:
        verb = "attacked" if outcome is None else outcome.value
        print(
            f"round {round_number}: {attacker.name} {verb} {defender.name} "
            f"for {damage} -- {defender.name} at {defender.hp}/{defender.max_hp} hp"
        )

    rounds = run_fight(attacker, defender, rng, on_attack=report)
    if not defender.is_down:
        print(f"{defender.name} is still standing after {rounds} attacks.")
    else:
        print(f"{defender.name} fell after {rounds} attacks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
