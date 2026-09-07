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
works, which is the cheapest possible thing to get wrong at tens of thousands
of resolutions per second.

Sources: DESIGN.md § The closed hook list, § Structural prevention over
testing; ROADMAP § The critical path, w0g2, w0h, w1f.
"""

import argparse
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final, Protocol

from dndsim.core.geometry import Cell, in_reach
from dndsim.core.rng import MersenneRng, Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.engine.damage import FreshDamageContexts, resolve_damage, roll_damage
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    DamageBag,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
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
from dndsim.rules.enums import Ability, DamageType, Outcome

__all__ = [
    "AttackContexts",
    "FreshContexts",
    "PooledContexts",
    "Trigger",
    "attack_once",
    "build_dummy",
    "build_fighter",
    "main",
    "run_fight",
]


class Trigger(Protocol):
    """The `trigger.*` namespace root, as the engine sees it.

    A `Protocol` rather than an import because `engine/triggers.py` (w2) is what
    will populate this and `apl/namespace.py` (w2e) is what will define it, and
    neither exists: the attack sequence's whole knowledge of the APL is that
    something wants to be told what it is being offered before the offer is
    dispatched. `None` is the ordinary case — nobody is asking.
    """

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None: ...


class AttackContexts(Protocol):
    """Where the attack's six contexts come from.

    DESIGN.md § Levers asks for "contexts reset and reused per attack rather
    than allocated, which matters at 4M resolutions", and `w0i` is what measures
    whether it does. Making the source of a context a **Strategy** rather than a
    `if pool is None` at six call sites keeps the sequence below readable as one
    thing, and keeps the two answers — allocate, or rebind — honest about
    costing exactly what they cost.
    """

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext: ...

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext: ...

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext: ...

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext: ...

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext: ...

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext: ...

    def bag(self) -> DamageBag: ...

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext: ...

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext: ...


class FreshContexts(FreshDamageContexts):
    """Allocate every context, every attack. The obvious answer, and the
    baseline `w0i` measures the pooled one against."""

    __slots__ = ()

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext:
        return AttackDeclaredContext(
            attacker=attacker, defender=defender, weapon=weapon, ability=ability
        )

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext:
        return AttackRollContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=natural,
            total=total,
            crit_threshold=_CRIT_THRESHOLD,
            had_advantage=had_advantage,
            had_disadvantage=had_disadvantage,
        )

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext:
        return HitContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext:
        return MissContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext:
        return DamageRollContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=is_critical,
            damage=damage,
        )

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext:
        return AttackResolvedContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage_dealt=damage_dealt,
        )

    def bag(self) -> DamageBag:
        return DamageBag()


class PooledContexts:
    """One context of each kind, rebound per attack (DESIGN.md § Levers).

    Built against whichever pair of creatures is convenient and rebound to the
    real ones on every attack, because `reopen()` demands every field a context
    does not default — nothing of the previous attack survives by omission
    (`hooks/contexts.py`). One pool belongs to one attack sequence: the contexts
    in it are live only while a hook is dispatching, so two nested attacks
    (a reaction inside a reaction) need two pools, which is `w1f`'s problem and
    is why this class is small enough to make one per attacker.
    """

    __slots__ = (
        "_bag",
        "_declared",
        "_hit",
        "_incoming",
        "_missed",
        "_resolved",
        "_roll",
        "_rolled",
        "_taken",
    )

    def __init__(self, attacker: Creature, defender: Creature, weapon: Weapon) -> None:
        fresh = FreshContexts()
        self._bag = DamageBag()
        self._declared = fresh.declared(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            ability=Ability.STRENGTH,
        )
        self._rolled = fresh.rolled(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=1,
            total=1,
            had_advantage=False,
            had_disadvantage=False,
        )
        self._hit = fresh.hit(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.HIT,
            damage=self._bag,
        )
        self._missed = fresh.missed(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.MISS,
            damage=self._bag,
        )
        self._roll = fresh.damage_roll(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=False,
            damage=self._bag,
        )
        self._resolved = fresh.resolved(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.MISS,
            damage_dealt=0,
        )
        self._incoming = fresh.incoming(
            defender=defender, attacker=attacker, amounts={}
        )
        self._taken = fresh.taken(
            defender=defender,
            attacker=attacker,
            amount=0,
            damage_types=frozenset(),
        )

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext:
        ctx = self._declared
        ctx.reopen(attacker=attacker, defender=defender, weapon=weapon, ability=ability)
        return ctx

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext:
        ctx = self._rolled
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=natural,
            total=total,
            crit_threshold=_CRIT_THRESHOLD,
            had_advantage=had_advantage,
            had_disadvantage=had_disadvantage,
        )
        return ctx

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext:
        ctx = self._hit
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )
        return ctx

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext:
        ctx = self._missed
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )
        return ctx

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext:
        ctx = self._roll
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=is_critical,
            damage=damage,
        )
        return ctx

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext:
        ctx = self._resolved
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage_dealt=damage_dealt,
        )
        return ctx

    def bag(self) -> DamageBag:
        """The one bag, emptied. It is handed to four contexts in turn, which
        is why `reopen()` rebinds a handed bag rather than clearing it."""
        bag = self._bag
        bag.clear()
        return bag

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext:
        ctx = self._incoming
        ctx.reopen(defender=defender, attacker=attacker, amounts=amounts)
        return ctx

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext:
        ctx = self._taken
        ctx.reopen(
            defender=defender,
            attacker=attacker,
            amount=amount,
            damage_types=damage_types,
        )
        return ctx


FRESH: Final[AttackContexts] = FreshContexts()
"""The default: allocate. Stateless, so one instance serves every caller."""

_CRIT_THRESHOLD: Final = 20
"""Where a natural roll starts critting. A derived stat on the creature once
`w1a` lands (Improved Critical lowers the character's, never the attack's)."""

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
    attacker: Creature,
    defender: Creature,
    rng: Rng,
    *,
    contexts: AttackContexts = FRESH,
    trigger: Trigger | None = None,
) -> tuple[Outcome, int]:
    """One full attack, dispatched exactly as `hooks/definitions.py` describes
    the attack and damage groups, over the real pipeline in `engine/damage.py`.

    Returns the outcome and the damage actually dealt (0 on a miss, or on a hit
    that landed on immunities/reductions worth nothing — `resolve_damage`
    already makes that distinction).

    `contexts` decides where the six contexts come from; `trigger` is the APL's
    `trigger.*` root, told what is being offered immediately before each hook
    that carries gated handlers. Both default to "nobody is asking", which is
    the walking skeleton's case (w0h) — they exist for `w0i`, whose creature has
    an action list and twenty-five features, and both are `w1f`'s to own for
    real.
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
    # Both dispatch tables, once. `Creature.dispatch()` is the API every other
    # caller uses; the attack sequence runs it nine times per resolution and
    # walks the attribute chain to it every time, which `w0i` measured at about
    # a twentieth of the per-attack budget. `w1f` inherits this line, not the
    # habit — nothing outside the engine's hot path reaches past the creature.
    strike = attacker.dispatcher.dispatch
    parry = defender.dispatcher.dispatch

    if trigger is not None:
        trigger.bind()
    declared = contexts.declared(
        attacker=attacker, defender=defender, weapon=weapon, ability=ability
    )
    strike(ON_DECLARE_ATTACK, declared)
    parry(ON_INCOMING_ATTACK_DECLARED, declared)
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

    rolled = contexts.rolled(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        natural=natural,
        total=total,
        had_advantage=had_advantage,
        had_disadvantage=had_disadvantage,
    )
    strike(ON_ATTACK_ROLLED, rolled)
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
        bag = contexts.bag()
        if trigger is not None:
            trigger.bind(crit=outcome.is_critical)
        hit = contexts.hit(
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
        strike(ON_HIT, hit)
        parry(ON_HIT_TAKEN, hit)
        hit.close()

        damage_roll = contexts.damage_roll(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=outcome.is_critical,
            damage=bag,
        )
        strike(ON_DAMAGE_ROLL, damage_roll)
        damage_roll.close()

        amounts = roll_damage(
            bag.parts,
            rng=rng,
            base_type=weapon.damage_type,
            critical=outcome.is_critical,
        )
        if trigger is not None:
            # What the victim's reactions are being offered: how much is on the
            # table if nobody does anything about it (DESIGN.md § APL,
            # `trigger.damage_estimate`).
            trigger.bind(damage_estimate=sum(amounts.values()))
        damage_dealt = resolve_damage(
            defender, amounts, rng=rng, attacker=attacker, contexts=contexts
        )
    else:
        miss = contexts.missed(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=contexts.bag(),
        )
        strike(ON_MISS, miss)
        miss.close()

    resolved = contexts.resolved(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        outcome=outcome,
        damage_dealt=damage_dealt,
    )
    strike(ON_ATTACK_RESOLVED, resolved)
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
    rng = MersenneRng(args.seed)

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
