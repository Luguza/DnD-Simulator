"""The pessimistic performance spike (ROADMAP w0i) — the M0 gate.

Measures attack resolutions per second per core against `BUDGET_PER_SECOND`, on
a deliberately hostile configuration: a hero carrying the twenty-five sources
of `tools/spike_features.py` and the hand-compiled APL of `tools/spike_apl.py`,
three enemies swinging back, and both sides walking real routes on a real grid.
`measure_startup()` times a spawned worker separately.

`docs/decisions.md` § w0i has the configuration's reasoning and the
measurements, and § w1k what the completed W1 substrate added to them.

Run it: `python -m dndsim.tools.spike` (add `--startup` for the worker cost).

DESIGN.md § Performance, § The spike must be pessimistic.
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from statistics import median
from types import MappingProxyType
from typing import Final

from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell, distance_ft, route, steps_within
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import MersenneRng, Rng, mix
from dndsim.core.timeline import Duration, InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.resources import spell_slot_specs
from dndsim.creatures.stats import GREATSWORD, Armour, StatBlock, Weapon
from dndsim.engine.attack import FRESH, AttackContexts, PooledContexts, resolve_attack
from dndsim.engine.triggers import walk_provoking
from dndsim.hooks.contexts import MoveContext, TurnContext
from dndsim.hooks.definitions import ON_MOVE, ON_TURN_START
from dndsim.rules.enums import (
    Ability,
    ActionCost,
    CasterProgression,
    DamageType,
    RecoveryTrigger,
)
from dndsim.tools.spike_apl import (
    AlliesProxy,
    Apl,
    EnemiesProxy,
    Entry,
    Namespace,
    SelfProxy,
    first,
    lowest_hp,
    toward_until_in_reach,
)
from dndsim.tools.spike_features import RAGE, SOURCES, Fixture, SpikeRage, SpikeState

__all__ = [
    "ATTACKS_PER_TURN",
    "BUDGET_PER_SECOND",
    "Spike",
    "build_spike",
    "main",
    "measure",
    "measure_startup",
    "run_encounter",
]

BUDGET_PER_SECOND: Final = 30_000
"""The M0 gate: attack resolutions per second per core, on the configuration
this module builds.

Revised down from 50,000 once, at M0. **Lowering it again must not be done
quietly** (`docs/decisions.md` § w0i)."""

ATTACKS_PER_TURN: Final = 3
"""The hero's attacks per Attack action (DESIGN.md § Performance)."""

ROUNDS_PER_ENCOUNTER: Final = 5
ENEMIES: Final = 3

_HERO_SCORES: Final = MappingProxyType(
    {
        Ability.STRENGTH: 20,
        Ability.DEXTERITY: 14,
        Ability.CONSTITUTION: 18,
        Ability.INTELLIGENCE: 8,
        Ability.WISDOM: 12,
        Ability.CHARISMA: 10,
    }
)
_ENEMY_SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 12))

_HERO_HP: Final = 70
"""Trap: large enough that the hero never drops, small enough that Uncanny
Dodge's `self.hp * 0.25` gate still answers yes (`docs/decisions.md` § w0i)."""

_ENEMY_HP: Final = 1_000
_HERO_START: Final = Cell(0, 0)
# Spread out, so that focus-firing the lowest-hp enemy means walking.
_ENEMY_STARTS: Final = (Cell(5, 0), Cell(0, 5), Cell(7, 6))


def _seat(place: int) -> InitiativeKey:
    """The `place`th key in a fixed order, hero first: distinct and ordered,
    never rolled."""
    return InitiativeKey(
        initiative=20, dexterity=10, player_character=place == 0, tiebreak=place / 10
    )


_HERO_SEAT: Final = _seat(0)
_SEATS: Final = tuple(_seat(place) for place in range(ENEMIES + 1))

_RAGE_LASTS: Final = Duration.until_end_of_next_turn_of(_HERO_SEAT)

_SLOTS: Final = CasterProgression.FULL
"""Trap: sized so the smite's gate never runs dry (`docs/decisions.md` § w1k)."""

_MAUL: Final = Weapon(
    name="Maul", damage=Dice(2, 6), damage_type=DamageType.BLUDGEONING
)
"""What the enemies swing, sized with `_HERO_HP` (`docs/decisions.md` § w0i)."""

_PLATE: Final = Armour.heavy("Plate", 18)

_TURN_COSTS: Final = MappingProxyType(
    {
        "rage": ActionCost.BONUS_ACTION,
        "second_wind": ActionCost.BONUS_ACTION,
        "healing_word": ActionCost.BONUS_ACTION,
        "attack": ActionCost.ACTION,
    }
)
"""What the turn loop offers, and what each one costs to take; the other four
entries are `gate:` answers given inside an event.

Trap: the costs are the XPHB ones and are load-bearing — charging Second Wind
an Action makes the attack count depend on the dice (`docs/decisions.md`
§ w0i)."""

_MAX_ACTIONS_PER_TURN: Final = 4


def _offer_table() -> tuple[frozenset[str], ...]:
    """The four possible offers, indexed by (Action left, Bonus Action left),
    precomputed so `_offered()` allocates nothing."""
    return tuple(
        frozenset(
            action
            for action, cost in _TURN_COSTS.items()
            if (cost is ActionCost.ACTION and has_action)
            or (cost is ActionCost.BONUS_ACTION and has_bonus)
        )
        for has_action in (False, True)
        for has_bonus in (False, True)
    )


_OFFERS: Final = _offer_table()


@dataclass(slots=True)
class Spike:
    """One assembled encounter: a hostile hero, its enemies, and its policy."""

    hero: Creature
    enemies: tuple[Creature, ...]
    combatants: tuple[Creature, ...]
    apl: Apl
    state: SpikeState
    economy: ActionEconomy
    contexts: AttackContexts
    enemy_contexts: tuple[AttackContexts, ...]
    sources: tuple[Fixture, ...]
    log: EventLog

    def reset(self) -> None:
        """Put the encounter back to round one. Runs inside the timed region
        (`docs/decisions.md` § w0i)."""
        self.log.clear()
        hero = self.hero
        hero.hp = hero.max_hp
        hero.revive()
        hero.position = _HERO_START
        for creature in self.combatants:
            effects = creature.effects
            for effect in tuple(effects):
                effects.remove(effect)
        hero.resources.recover(RecoveryTrigger.LONG_REST)
        self.economy.turn_started(hero.speed_ft)
        for enemy, start in zip(self.enemies, _ENEMY_STARTS, strict=True):
            enemy.hp = enemy.max_hp
            enemy.revive()
            enemy.position = start


def build_spike(
    seed: int = 0, *, pooled: bool = True, log_level: LogLevel = LogLevel.FULL
) -> tuple[Spike, Rng]:
    """Assemble the hostile creature, its three enemies and its compiled APL.

    Everything built once per encounter is built here (DESIGN.md § Levers).
    `pooled` reuses each side's attack contexts rather than allocating them per
    attack; `log_level` is the level of the `EventLog` every attack writes to
    (`docs/decisions.md` § w1i). Returns the spike and its seeded rng.
    """
    hero = Creature(
        name="Spike",
        stats=StatBlock(abilities=_HERO_SCORES, max_hp=_HERO_HP, level=12),
        position=_HERO_START,
        weapon=GREATSWORD,
        armour=_PLATE,
        initiative=_HERO_SEAT,
    )
    hero.resources.add(RAGE, *spell_slot_specs(_SLOTS))
    enemies = tuple(
        Creature(
            name=f"Enemy{index}",
            stats=StatBlock(abilities=_ENEMY_SCORES, max_hp=_ENEMY_HP, level=5),
            position=start,
            armour=Armour.natural(16),
            weapon=_MAUL,
            initiative=_SEATS[index + 1],
        )
        for index, start in enumerate(_ENEMY_STARTS)
    )

    state = SpikeState()
    state.marked = enemies[0].name
    sources = tuple(source(hero, state) for source in SOURCES)
    for source in sources:
        hero.attach(source)

    namespace = Namespace(
        own=SelfProxy(hero),
        enemies=EnemiesProxy(hero, enemies),
        allies=AlliesProxy((hero,)),
    )
    apl = Apl(_entries(), namespace)
    hero.dispatcher.bind(gate=apl)

    spike = Spike(
        hero=hero,
        enemies=enemies,
        # Built once: it is walked on every move.
        combatants=(hero, *enemies),
        apl=apl,
        state=state,
        economy=hero.economy,
        contexts=PooledContexts(hero, enemies[0], GREATSWORD) if pooled else FRESH,
        enemy_contexts=tuple(
            PooledContexts(enemy, hero, _MAUL) if pooled else FRESH for enemy in enemies
        ),
        sources=sources,
        log=EventLog(log_level),
    )
    return spike, MersenneRng(mix(seed, 0))


# --- the eight entries, hand-compiled ---------------------------------------
#
# Each is one row of the YAML in DESIGN.md § APL; the comment above each is the
# clause it was compiled from.


def _entries() -> tuple[Entry, ...]:
    return (
        # - { action: rage, if: "not self.raging and enemies.within(30) >= 1" }
        Entry(
            action="rage",
            condition=lambda ns: not ns.self.raging and ns.enemies.within(30) >= 1,
        ),
        # - { action: reckless_attack, if: "self.hp_pct > 0.4 or target.ac >= 17" }
        Entry(
            action="reckless_attack",
            condition=lambda ns: ns.self.hp_pct > 0.4 or ns.target.ac >= 17,
            gate="barbarian.reckless_attack",
        ),
        # - action: divine_smite
        #   if: "trigger.crit or (self.slots.highest >= 3 and target.hp > 25)"
        Entry(
            action="divine_smite",
            condition=lambda ns: (
                ns.trigger.crit or (ns.self.slots_highest >= 3 and ns.target.hp > 25)
            ),
            gate="paladin.divine_smite",
        ),
        # - { action: uncanny_dodge,
        #     if: "trigger.damage_estimate >= self.hp * 0.25" }
        Entry(
            action="uncanny_dodge",
            condition=lambda ns: ns.trigger.damage_estimate >= ns.self.hp * 0.25,
            gate="rogue.uncanny_dodge",
        ),
        # - { action: second_wind, if: "self.hp_pct < 0.5" }
        Entry(
            action="second_wind",
            condition=lambda ns: ns.self.hp_pct < 0.5,
        ),
        # - action: healing_word
        #   if: "allies.down >= 1"
        #   target: "first(allies.down)"
        Entry(
            action="healing_word",
            condition=lambda ns: len(ns.allies.down) >= 1,
            target=lambda ns: first(ns.allies.down),
        ),
        # - action: attack
        #   target: "lowest_hp of enemies.in_reach"
        #   move:   "toward lowest_hp of enemies until in_reach"
        Entry(
            action="attack",
            condition=lambda ns: len(ns.enemies.alive) >= 1,
            target=lambda ns: lowest_hp(ns.enemies.in_reach),
            move=_close_on_the_weakest,
        ),
        # - { action: dodge, if: "enemies.within(30) == 0" }
        Entry(
            action="dodge",
            condition=lambda ns: ns.enemies.within(30) == 0,
        ),
    )


def _close_on_the_weakest(ns: Namespace) -> Cell | None:
    """`move: "toward lowest_hp of enemies until in_reach"`."""
    target = lowest_hp(ns.enemies.alive)
    if target is None:
        return None
    return toward_until_in_reach(ns.self.position, ns.self.reach_ft, target)


# --- the turn loop ----------------------------------------------------------


def run_encounter(spike: Spike, rng: Rng, rounds: int = ROUNDS_PER_ENCOUNTER) -> int:
    """One encounter. Returns the attack resolutions it took to run.

    Counts every resolved attack on both sides.
    """
    spike.reset()
    resolutions = 0
    for number in range(1, rounds + 1):
        resolutions += _hero_turn(spike, rng, number)
        resolutions += _enemy_turns(spike, rng, number)
    return resolutions


@cache
def _boundaries(number: int) -> tuple[tuple[TimeKey, TimeKey], ...]:
    """Each seat's turn in round `number`, as its start and end on the clock."""
    return tuple(
        (TimeKey(number, seat, TurnPhase.START), TimeKey(number, seat, TurnPhase.END))
        for seat in _SEATS
    )


def _expire_before(spike: Spike, now: TimeKey) -> None:
    """Sweep every combatant's store before `now`'s hooks
    (`docs/decisions.md` § w1k)."""
    for creature in spike.combatants:
        creature.effects.expire_before(now)


def _expire_after(spike: Spike, now: TimeKey) -> None:
    for creature in spike.combatants:
        creature.effects.expire_after(now)


def _hero_turn(spike: Spike, rng: Rng, number: int) -> int:
    hero = spike.hero
    start, end = _boundaries(number)[0]
    _expire_before(spike, start)
    hero.dispatcher.resets.turn_started(own=True)
    spike.economy.turn_started(hero.speed_ft)
    turn = TurnContext(creature=hero, time=start)
    hero.dispatch(ON_TURN_START, turn)
    turn.close()

    resolutions = 0
    for _ in range(_MAX_ACTIONS_PER_TURN):
        entry = spike.apl.choose(_offered(spike))
        if entry is None:
            break
        resolutions += _execute(spike, entry, rng)
    _expire_after(spike, end)
    return resolutions


def _offered(spike: Spike) -> frozenset[str]:
    """The ids the hero's economy can still pay for (DESIGN.md § APL)."""
    economy = spike.economy
    return _OFFERS[2 * (economy.actions > 0) + (economy.bonus_actions > 0)]


def _execute(spike: Spike, entry: Entry, rng: Rng) -> int:
    """Take the offer the APL accepted. Returns attack resolutions."""
    action = entry.action
    if action != "attack":
        if action == "rage":
            hero = spike.hero
            hero.resources.spend(RAGE.key)
            hero.effects.apply(SpikeRage(hero, duration=_RAGE_LASTS))
        # Only the cost is modelled; second_wind and healing_word do nothing.
        spike.economy.spend(_TURN_COSTS[action])
        return 0

    namespace = spike.apl.namespace
    if entry.move is not None:
        _walk(spike, entry.move(namespace))
    target = None if entry.target is None else entry.target(namespace)
    if target is None:
        # The move: clause could not close the distance; the Action is spent.
        spike.economy.spend(ActionCost.ACTION)
        return 0

    namespace.target.bind(target)
    spike.economy.spend(ActionCost.ACTION)
    contexts = spike.contexts
    trigger = namespace.trigger
    log = spike.log
    for _ in range(ATTACKS_PER_TURN):
        resolve_attack(
            spike.hero, target, rng=rng, contexts=contexts, trigger=trigger, log=log
        )
    return ATTACKS_PER_TURN


def _enemy_turns(spike: Spike, rng: Rng, number: int) -> int:
    """The other side of the fight: three featureless attackers, closing.

    Trap: the hero's `trigger` is passed with their attacks, because the gate
    answered on this side is the hero's (`docs/decisions.md` § w0i).
    """
    hero = spike.hero
    trigger = spike.apl.namespace.trigger
    log = spike.log
    resolutions = 0
    turns = _boundaries(number)[1:]
    for enemy, contexts, (start, end) in zip(
        spike.enemies, spike.enemy_contexts, turns, strict=True
    ):
        _expire_before(spike, start)
        enemy.dispatcher.resets.turn_started(own=True)
        destination = toward_until_in_reach(enemy.position, enemy.reach_ft, hero)
        if destination is not None:
            _step(spike, enemy, destination)
        if distance_ft(enemy.position, hero.position) <= enemy.reach_ft:
            resolve_attack(
                enemy, hero, rng=rng, contexts=contexts, trigger=trigger, log=log
            )
            resolutions += 1
        _expire_after(spike, end)
    return resolutions


def _walk(spike: Spike, destination: Cell | None) -> None:
    if destination is not None:
        _step(spike, spike.hero, destination)


def _step(spike: Spike, mover: Creature, destination: Cell) -> None:
    """Walk `mover` along the shortest legal route toward `destination`, as
    far as its speed allows, past everybody else's square (DESIGN.md § APL).
    """
    blocked = frozenset(
        creature.position for creature in spike.combatants if creature is not mover
    )
    if destination in blocked:
        return
    walked = route(mover.position, destination, blocked=blocked, size=mover.size)
    if not walked:
        return
    reachable = walked[: steps_within(mover.speed_ft)]
    if not reachable:
        return
    # Nobody here owns a movement reaction, so only the audience computation
    # is measured (`docs/decisions.md` § w0g3).
    stepped = walk_provoking(mover, reachable, spike.combatants)
    if not stepped:
        return
    move = MoveContext(
        creature=mover,
        route=stepped,
        distance_ft=len(stepped) * 5,
    )
    mover.dispatch(ON_MOVE, move)
    move.close()


# --- the measurement --------------------------------------------------------


def measure(
    encounters: int = 1000,
    repeats: int = 5,
    seed: int = 0,
    *,
    pooled: bool = True,
    log_level: LogLevel = LogLevel.FULL,
) -> tuple[float, int]:
    """Attack resolutions per second, median of `repeats` runs after a warmup.

    Returns the rate and the resolutions in one repeat. The default of a
    thousand encounters is a floor, not a number to trim (`docs/decisions.md`
    § w0i).
    """
    spike, rng = build_spike(seed, pooled=pooled, log_level=log_level)
    _burn(spike, rng, max(encounters // 10, 1))

    rates: list[float] = []
    resolutions = 0
    for _ in range(repeats):
        started = time.perf_counter()
        resolutions = _burn(spike, rng, encounters)
        elapsed = time.perf_counter() - started
        rates.append(resolutions / elapsed)
    return median(rates), resolutions


def _burn(spike: Spike, rng: Rng, encounters: int) -> int:
    resolutions = 0
    for _ in range(encounters):
        resolutions += run_encounter(spike, rng)
    return resolutions


def measure_startup(workers: int = 4) -> float:
    """Seconds to bring one `spawn`ed worker to the point of simulating.

    A fresh interpreter, a fresh import of the package and the spike rebuilt,
    averaged over `workers` subprocesses. Trap: a subprocess, never
    `multiprocessing.Pool` (`docs/decisions.md` § w0i).
    """
    command = [
        sys.executable,
        "-c",
        "from dndsim.tools.spike import build_spike; build_spike(0)",
    ]
    started = time.perf_counter()
    for _ in range(workers):
        subprocess.run(command, check=True, capture_output=True)
    return (time.perf_counter() - started) / workers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the M0 performance spike (ROADMAP w0i) and print the "
        "attack resolutions per second per core."
    )
    parser.add_argument("--encounters", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="allocate contexts per attack instead of pooling them, which is "
        "the baseline the pooling lever is worth measuring against",
    )
    parser.add_argument(
        "--log-level",
        choices=[level.value for level in LogLevel],
        default=LogLevel.FULL.value,
        help="how much of the event log every attack writes",
    )
    parser.add_argument(
        "--startup",
        action="store_true",
        help="also measure spawn worker startup, separately",
    )
    args = parser.parse_args(argv)

    rate, resolutions = measure(
        args.encounters,
        args.repeats,
        args.seed,
        pooled=not args.fresh,
        log_level=LogLevel(args.log_level),
    )
    verdict = "PASS" if rate >= BUDGET_PER_SECOND else "MISS"
    print(
        f"{resolutions} attack resolutions per run, "
        f"median of {args.repeats} runs after a warmup"
    )
    print(
        f"{rate:,.0f} attack resolutions/sec/core  (budget {BUDGET_PER_SECOND:,}) "
        f"-- {verdict}"
    )
    if args.startup:
        print(f"{measure_startup():.3f} s per spawn worker (startup, measured apart)")
    return 0 if rate >= BUDGET_PER_SECOND else 1


if __name__ == "__main__":
    raise SystemExit(main())
