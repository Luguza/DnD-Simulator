"""The walking skeleton (ROADMAP w0h): one featureless creature attacking
another through the *real* dispatch path and the *real* damage pipeline.

W0 has built five substrates — the registry, the contexts, the dispatcher,
`Creature`/`StatBlock` and `engine/damage.py` — and none of them has ever run
together. `w0i`'s pessimistic spike is the load-bearing measurement that
decides whether the dispatch design survives to W1, and a spike against those
substrates in isolation would measure nothing: this module is what makes it an
integrated one, by making a fight actually happen first (ROADMAP § The
critical path).

**The attack sequence below belongs to `engine/attack.py`, not to this file.**
That module is w1f's, not yet built, so the seven attack hooks and the three
damage ones are sequenced here by hand, exactly as `hooks/definitions.py`
describes them: declare -> roll -> hit/miss -> resolved, with the damage
hooks riding inside the hit branch. Every line of `attack_once()` is glue that
`w1f` owns for good — riders (advantage, extra dice, weapon masteries),
proficiency, the action economy, none of it is here because none of it exists
yet. What *is* real: the `Dispatcher` these hooks run through, the
`DamageBag` -> `roll_damage()` -> `resolve_damage()` pipeline in
`engine/damage.py`, and the `in_reach()` query from `core/geometry.py`.

**Two creatures, no `HookSource` attached.** "Featureless" is the point
(ROADMAP w0h) — a creature with nothing listening on any of its ten dispatched
hooks costs one failed dict lookup per hook and proves the Null Object ending
works, which is the cheapest possible thing to get wrong at 50k
resolutions/sec.

Sources: DESIGN.md § The closed hook list, § Structural prevention over
testing; ROADMAP § The critical path, w0g2, w0h, w1f.
"""

import argparse
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from dndsim.core.geometry import Cell, in_reach
from dndsim.core.rng import PcgRng, Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock
from dndsim.engine.damage import resolve_damage, roll_damage
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    DamageBag,
    DamageRollContext,
    HitContext,
    MissContext,
)
from dndsim.hooks.definitions import (
    ON_ATTACK_RESOLVED,
    ON_ATTACK_ROLLED,
    ON_DAMAGE_ROLL,
    ON_DECLARE_ATTACK,
    ON_HIT,
    ON_HIT_TAKEN,
    ON_INCOMING_ATTACK_DECLARED,
    ON_MISS,
)
from dndsim.rules.enums import Ability, Outcome

__all__ = ["attack_once", "build_dummy", "build_fighter", "main", "run_fight"]

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

_MAX_ROUNDS: Final = 50
"""A safety cap, not a rule: a level-5 fighter vs. an AC-15, 40-hp dummy
resolves in a handful of attacks, and a fight that has not ended by 50 is a
bug in this script rather than an unlucky streak worth waiting out."""


def build_fighter(name: str = "Fighter") -> Creature:
    """A featureless level-5 fighter swinging the spike's one weapon."""
    stats = StatBlock(abilities=_FIGHTER_SCORES, max_hp=44, armour_class=16, level=5)
    return Creature(name=name, stats=stats, position=Cell(0, 0), weapon=GREATSWORD)


def build_dummy(name: str = "Dummy") -> Creature:
    """A featureless AC-15 dummy with nothing attached — the target."""
    stats = StatBlock(abilities=_DUMMY_SCORES, max_hp=40, armour_class=15)
    return Creature(name=name, stats=stats, position=Cell(1, 0))


def attack_once(
    attacker: Creature, defender: Creature, rng: Rng
) -> tuple[Outcome, int]:
    """One full attack, dispatched exactly as `hooks/definitions.py` describes
    the attack and damage groups, over the real pipeline in `engine/damage.py`.

    Returns the outcome and the damage actually dealt (0 on a miss, or on a hit
    that landed on immunities/reductions worth nothing — `resolve_damage`
    already makes that distinction).
    """
    weapon = attacker.weapon
    if weapon is None:
        raise ValueError(f"{attacker.name} has no weapon to attack with")
    if not in_reach(attacker.position, defender.position, attacker.reach_ft):
        raise ValueError(
            f"{attacker.name} at {attacker.position} cannot reach {defender.name} "
            f"at {defender.position} with {attacker.reach_ft} ft of reach"
        )
    ability = Ability.STRENGTH

    declared = AttackDeclaredContext(
        attacker=attacker, defender=defender, weapon=weapon, ability=ability
    )
    attacker.dispatch(ON_DECLARE_ATTACK, declared)
    defender.dispatch(ON_INCOMING_ATTACK_DECLARED, declared)
    had_advantage = declared.had_advantage
    had_disadvantage = declared.had_disadvantage
    attack_bonus = declared.bonus.flat_total
    ac_bonus = declared.ac_bonus.flat_total
    declared.close()

    if had_advantage:
        natural = max(rng.roll(20), rng.roll(20))
    elif had_disadvantage:
        natural = min(rng.roll(20), rng.roll(20))
    else:
        natural = rng.roll(20)
    total = (
        natural + attacker.modifier(ability) + attacker.proficiency_bonus + attack_bonus
    )

    rolled = AttackRollContext(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        natural=natural,
        total=total,
        crit_threshold=20,
        had_advantage=had_advantage,
        had_disadvantage=had_disadvantage,
    )
    attacker.dispatch(ON_ATTACK_ROLLED, rolled)
    is_critical = rolled.is_critical
    rolled.close()

    if natural == 1:
        outcome = Outcome.CRITICAL_MISS
    elif is_critical:
        outcome = Outcome.CRITICAL_HIT
    elif total >= defender.armour_class + ac_bonus:
        outcome = Outcome.HIT
    else:
        outcome = Outcome.MISS

    damage_dealt = 0
    if outcome.is_hit:
        bag = DamageBag()
        hit = HitContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=bag,
        )
        hit.stamp("engine.attack", None)
        bag.add(
            dice=weapon.damage,
            flat=attacker.modifier(ability),
            damage_type=weapon.damage_type,
        )
        attacker.dispatch(ON_HIT, hit)
        defender.dispatch(ON_HIT_TAKEN, hit)
        hit.close()

        damage_roll = DamageRollContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=outcome.is_critical,
            damage=bag,
        )
        attacker.dispatch(ON_DAMAGE_ROLL, damage_roll)
        damage_roll.close()

        amounts = roll_damage(
            bag.parts,
            rng=rng,
            base_type=weapon.damage_type,
            critical=outcome.is_critical,
        )
        damage_dealt = resolve_damage(defender, amounts, rng=rng, attacker=attacker)
    else:
        miss = MissContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=DamageBag(),
        )
        attacker.dispatch(ON_MISS, miss)
        miss.close()

    resolved = AttackResolvedContext(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        outcome=outcome,
        damage_dealt=damage_dealt,
    )
    attacker.dispatch(ON_ATTACK_RESOLVED, resolved)
    resolved.close()

    return outcome, damage_dealt


def run_fight(
    attacker: Creature,
    defender: Creature,
    rng: Rng,
    *,
    on_attack: Callable[[int, Outcome, int], None] | None = None,
) -> int:
    """Swing until the defender drops or `_MAX_ROUNDS` is hit. Returns the
    number of attacks made."""
    rounds = 0
    while defender.is_alive and rounds < _MAX_ROUNDS:
        rounds += 1
        outcome, damage = attack_once(attacker, defender, rng)
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
    rng = PcgRng(args.seed)

    def report(round_number: int, outcome: Outcome, damage: int) -> None:
        print(
            f"round {round_number}: {attacker.name} {outcome.value} {defender.name} "
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
