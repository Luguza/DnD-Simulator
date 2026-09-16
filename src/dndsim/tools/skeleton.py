"""The walking skeleton (ROADMAP w0h): one featureless creature attacking
another through the *real* dispatch path and the *real* damage pipeline.

W0 built five substrates — the registry, the contexts, the dispatcher,
`Creature`/`StatBlock` and `engine/damage.py` — and none of them had ever run
together. `w0i`'s pessimistic spike is the load-bearing measurement that decided
whether the dispatch design survived to W1, and a spike against those substrates
in isolation would have measured nothing: this module is what made it an
integrated one, by making a fight actually happen first (ROADMAP § The critical
path).

**The attack sequence this file used to hand-sequence is gone** (debt #6). It was
written here because `engine/attack.py` did not exist yet, and `w1f` built it;
what is left is the fight — two featureless creatures, a fixed weapon, and a
loop that swings until one of them drops — calling the sequence the project
ships. A benchmark that defends a budget on its own copy of the pipeline defends
nothing.

**Two creatures, no `HookSource` attached.** "Featureless" is the point
(ROADMAP w0h) — a creature with nothing listening on any of its ten dispatched
hooks costs one failed dict lookup per hook and proves the Null Object ending
works, which is the cheapest possible thing to get wrong at tens of thousands of
resolutions per second.

Sources: DESIGN.md § The closed hook list, § Structural prevention over testing;
ROADMAP § The critical path, w0g2, w0h, w1f.
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
"""AC 16, and no Dexterity: the fighter's AC was a literal on the stat block
until `w1a` derived it from equipment, and chain mail is the XPHB entry that
gives the number this script has always run with (`w2a` imports the rest)."""

_MAX_ROUNDS: Final = 50
"""A safety cap, not a rule: a level-5 fighter vs. an AC-15, 40-hp dummy
resolves in a handful of attacks, and a fight that has not ended by 50 is a
bug in this script rather than an unlucky streak worth waiting out."""


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
    while defender.is_alive and rounds < _MAX_ROUNDS:
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
    if defender.is_alive:
        print(f"{defender.name} is still standing after {rounds} attacks.")
    else:
        print(f"{defender.name} fell after {rounds} attacks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
