"""The w0h contract: the walking skeleton actually walks.

Three properties carry this file, all stated in ROADMAP w0h:

- **The real dispatch path runs.** Probes attached to both creatures record
  which hooks fired, in which order, and prove the pairs (`on_hit` /
  `on_hit_taken`) run over the *same* context rather than two copies.
- **The real attack and damage pipelines run.** `roll_damage()` and `resolve_damage()`
  from `engine/damage.py` are exercised, not stubbed, so a hit's arithmetic is
  checked against a hand-computed number the way every damage-order test in
  this repo is.
- **A fight is reproducible end to end.** Two `MersenneRng`s built from the same
  seed drive `run_fight()` to the identical outcome, which is the property the
  whole determinism story (`core/rng.py`) is for.

Sources: ROADMAP w0h; DESIGN.md § Determinism is the backbone.
"""

from types import MappingProxyType
from typing import Final

from dndsim.core.geometry import Cell
from dndsim.core.rng import MersenneRng, ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import Armour, StatBlock
from dndsim.engine.attack import resolve_attack
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
    MissContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, Outcome
from dndsim.tools.skeleton import build_dummy, build_fighter, run_fight

# The fighter built by `build_fighter()` swings at +6 (str +3, prof +3) and the
# dummy from `build_dummy()` is AC 15, so a natural 15 hits (21 >= 15) without
# being a crit, and a natural 2 misses (8 < 15) without being a fumble.
_HIT_ROLL: Final = 15
_MISS_ROLL: Final = 2


class AttackerProbe(HookSource):
    """Every SELF-scoped attack hook, recorded in dispatch order."""

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_declare_attack")
    def declare(self, ctx: AttackDeclaredContext) -> R:
        self.seen.append("on_declare_attack")
        return R.SKIPPED

    @hook("on_attack_rolled")
    def rolled(self, ctx: AttackRollContext) -> R:
        self.seen.append("on_attack_rolled")
        return R.SKIPPED

    @hook("on_hit")
    def hit(self, ctx: HitContext) -> R:
        self.seen.append("on_hit")
        return R.SKIPPED

    @hook("on_miss")
    def miss(self, ctx: MissContext) -> R:
        self.seen.append("on_miss")
        return R.SKIPPED

    @hook("on_damage_roll")
    def damage_roll(self, ctx: DamageRollContext) -> R:
        self.seen.append("on_damage_roll")
        return R.SKIPPED

    @hook("on_attack_resolved")
    def resolved(self, ctx: AttackResolvedContext) -> R:
        self.seen.append("on_attack_resolved")
        return R.SKIPPED


class DefenderProbe(HookSource):
    """Every TARGET-scoped attack and damage hook, recorded in dispatch order."""

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_incoming_attack_declared")
    def declared(self, ctx: AttackDeclaredContext) -> R:
        self.seen.append("on_incoming_attack_declared")
        return R.SKIPPED

    @hook("on_hit_taken")
    def hit_taken(self, ctx: HitContext) -> R:
        self.seen.append("on_hit_taken")
        return R.SKIPPED

    @hook("on_incoming_damage")
    def incoming(self, ctx: IncomingDamageContext) -> R:
        self.seen.append("on_incoming_damage")
        return R.SKIPPED

    @hook("on_damage_taken")
    def taken(self, ctx: DamageTakenContext) -> R:
        self.seen.append("on_damage_taken")
        return R.SKIPPED


def _rig() -> tuple[Creature, Creature, AttackerProbe, DefenderProbe]:
    attacker = build_fighter()
    defender = build_dummy()
    attacker_probe = AttackerProbe(attacker)
    defender_probe = DefenderProbe(defender)
    attacker.attach(attacker_probe)
    defender.attach(defender_probe)
    return attacker, defender, attacker_probe, defender_probe


def test_a_hit_dispatches_every_hook_the_hit_side_touches() -> None:
    attacker, defender, attacker_probe, defender_probe = _rig()
    # d20, then the greatsword's 2d6 (rolled 4 and 5).
    rng = ScriptedRng([_HIT_ROLL, 4, 5])

    outcome, damage = resolve_attack(attacker, defender, rng=rng)

    assert outcome is Outcome.HIT
    # 2d6 (4 + 5 = 9) + STR modifier (+3) = 12, no mitigation on a dummy.
    assert damage == 12
    assert defender.hp == 40 - 12
    assert attacker_probe.seen == [
        "on_declare_attack",
        "on_attack_rolled",
        "on_hit",
        "on_damage_roll",
        "on_attack_resolved",
    ]
    assert defender_probe.seen == [
        "on_incoming_attack_declared",
        "on_hit_taken",
        "on_incoming_damage",
        "on_damage_taken",
    ]


def test_a_miss_never_reaches_the_damage_hooks() -> None:
    attacker, defender, attacker_probe, defender_probe = _rig()
    rng = ScriptedRng([_MISS_ROLL])

    outcome, damage = resolve_attack(attacker, defender, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert defender.hp == defender.max_hp
    assert attacker_probe.seen == [
        "on_declare_attack",
        "on_attack_rolled",
        "on_miss",
        "on_attack_resolved",
    ]
    # No `on_hit_taken` and no damage hooks: the attack never landed.
    assert defender_probe.seen == ["on_incoming_attack_declared"]


def test_a_natural_1_always_misses_even_against_a_total_that_would_hit() -> None:
    attacker = build_fighter()
    # AC 5: the fighter's total of 7 (natural 1 + modifier 6) would otherwise
    # be a hit, so this isolates the unconditional rule from an ordinary miss.
    frail = Creature(
        name="Frail",
        stats=StatBlock(
            abilities=MappingProxyType(dict.fromkeys(Ability, 10)),
            max_hp=10,
        ),
        position=Cell(1, 0),
        armour=Armour.natural(5),
    )
    rng = ScriptedRng([1])

    outcome, damage = resolve_attack(attacker, frail, rng=rng)

    assert outcome is Outcome.CRITICAL_MISS
    assert damage == 0


def test_a_natural_20_always_hits_and_crits() -> None:
    attacker, defender = build_fighter(), build_dummy()
    # d20, then the doubled 4d6 a crit rolls.
    rng = ScriptedRng([20, 1, 2, 3, 4])

    outcome, damage = resolve_attack(attacker, defender, rng=rng)

    assert outcome is Outcome.CRITICAL_HIT
    # 4d6 (1+2+3+4=10) + STR modifier (+3) = 13; the modifier does not double.
    assert damage == 13


def test_a_fight_is_reproducible_from_the_same_seed() -> None:
    first_rounds = run_fight(build_fighter(), build_dummy(), MersenneRng(12345))
    second_rounds = run_fight(build_fighter(), build_dummy(), MersenneRng(12345))

    assert first_rounds == second_rounds
    assert 0 < first_rounds < 50


def test_a_fight_ends_with_the_dummy_dead() -> None:
    attacker = build_fighter()
    defender = build_dummy()

    run_fight(attacker, defender, MersenneRng(1))

    assert not defender.is_alive
    assert attacker.is_alive
