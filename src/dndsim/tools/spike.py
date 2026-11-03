"""The pessimistic performance spike (ROADMAP w0i) — the M0 gate.

Measures attack resolutions per second per core on a deliberately hostile
configuration: a hero carrying the twenty-five sources of
`tools/spike_features.py` and the APL of `SPIKE_APL`, compiled by `apl/`,
three enemies swinging back, and both sides walking real routes on a real grid,
paying for each step. By default one enemy also falls back and the hero chases
it out of the others' reach, drawing their opportunity attacks.
`BUDGET_PER_SECOND` is checked only on `provoking=False`, the configuration
before `w2c2`: no fall-back and no opportunity attacks, the enemies closing by
the spike's own stepping rather than `Move`, and a `World` holding no log, so a
change to the world's log is priced on the default only. The hero walks by
`Move` in both, so a change to `Move` is priced on both.
`measure_startup()` times a spawned worker separately.

`docs/decisions.md` § w0i has the configuration's reasoning and the
measurements, § w1k what the completed W1 substrate added to them, and § w2c2
what the walk and the opportunity attacks did.

Run it: `python -m dndsim.tools.spike` (add `--startup` for the worker cost, and
`--no-provoking` for the configuration the budget is quoted on).

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

from dndsim.actions.move import Move
from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.apl.apl import Apl, CompiledEntry
from dndsim.apl.schema import Kind, NamespaceSchema
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell, distance_ft, in_reach, route, steps_within
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import MersenneRng, Rng, mix
from dndsim.core.timeline import Duration, InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.effects import Clock
from dndsim.creatures.resources import spell_slot_specs
from dndsim.creatures.stats import GREATSWORD, Armour, StatBlock, Weapon
from dndsim.engine.attack import FRESH, AttackContexts, PooledContexts, resolve_attack
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import Context, MoveContext, TurnContext
from dndsim.hooks.definitions import ON_MOVE, ON_TURN_START
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import CELL_SIZE_FT
from dndsim.rules.enums import (
    Ability,
    ActionCost,
    CasterProgression,
    DamageType,
    RecoveryTrigger,
)
from dndsim.tools.spike_features import (
    RAGE,
    SOURCES,
    Fixture,
    SpikeRage,
    SpikeState,
    raging,
)

__all__ = [
    "ATTACKS_PER_TURN",
    "BUDGET_PER_SECOND",
    "SPIKE_APL",
    "Spike",
    "compile_spike_apl",
    "build_spike",
    "main",
    "measure",
    "measure_startup",
    "run_encounter",
]

BUDGET_PER_SECOND: Final = 22_000
"""The M0 gate: attack resolutions per second per core, on the configuration
this module builds with `provoking=False`.

Revised down twice: from 50,000 at M0 (`docs/decisions.md` § w0i), and from
30,000 by the owner's answer to `docs/questions.md` #10 (`docs/decisions.md`
§ The W2 mid-wave review). **Lowering it again must not be done quietly**."""

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
_FALL_BACK: Final = 2
"""Squares the skirmisher retreats after it swings: out of a 5-foot reach."""


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
    clock: Clock
    world: World
    skirmisher: Creature | None

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
            enemy.economy.turn_started(enemy.speed_ft)


class _Swing:
    """The enemies' policy: every opportunity attack offered is taken."""

    __slots__ = ()

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        return True


def build_spike(
    seed: int = 0,
    *,
    pooled: bool = True,
    log_level: LogLevel = LogLevel.FULL,
    provoking: bool = True,
) -> tuple[Spike, Rng]:
    """Assemble the hostile creature, its three enemies and its compiled APL.

    Everything built once per encounter is built here (DESIGN.md § Levers).
    `pooled` reuses each side's attack contexts rather than allocating them per
    attack; `log_level` is the level of the `EventLog` every attack writes to
    (`docs/decisions.md` § w1i). `provoking=False` is the configuration before
    `w2c2`: no enemy owns an opportunity attack and none falls back, for the
    witness and for `BUDGET_PER_SECOND` (`docs/decisions.md` § w2c2).
    Returns the spike and its seeded rng.
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

    log = EventLog(log_level)
    # Seated with the log only when provoking: the configuration before
    # `w2c2` wrote no effect or resource records (`docs/decisions.md` § w2c2).
    world = World(log=log if provoking else None)
    world.seat(hero, "hero")
    for enemy in enemies:
        world.seat(enemy, "enemies")
    apl = compile_spike_apl().bind(hero, world)
    namespace = apl.namespace
    hero.dispatcher.bind(gate=apl)
    rng = MersenneRng(mix(seed, 0))
    if provoking:
        # Each enemy's reaction has a pool of its own, apart from its turn's
        # (`docs/decisions.md` § w2c2).
        for enemy in enemies:
            enemy.dispatcher.bind(gate=_Swing())
            enemy.attach(
                OpportunityAttacks(
                    enemy,
                    world=world,
                    rng=rng,
                    contexts=PooledContexts(enemy, hero, _MAUL) if pooled else FRESH,
                    trigger=namespace.trigger,
                )
            )

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
        log=log,
        clock=world.clock,
        world=world,
        skirmisher=enemies[0] if provoking else None,
    )
    return spike, rng


# --- the eight entries, as an author writes them -----------------------------


SPIKE_APL: Final = """
- { action: rage,            if: "not self.raging and enemies.within(30) >= 1" }
- { action: reckless_attack, if: "self.hp_pct > 0.4 or target.ac >= 17" }
- action: divine_smite
  if:     "trigger.crit or (self.slots.highest >= 3 and target.hp > 25)"
- { action: uncanny_dodge,   if: "trigger.damage_estimate >= self.hp * 0.25" }
- { action: second_wind,     if: "self.hp_pct < 0.5" }
- action: healing_word
  if:     "allies.down >= 1"
  target: "first(allies.down)"
- action: attack
  if:     "enemies.alive >= 1"
  target: "lowest_hp of enemies.in_reach"
  move:   "toward lowest_hp of enemies until in_reach"
- { action: dodge,           if: "enemies.within(30) == 0" }
"""
"""The hero's list, the YAML of DESIGN.md § APL. Compiled by `apl/`, which is
what this spike prices (ROADMAP w2e, debt #14)."""

_SPIKE_GATES: Final = MappingProxyType(
    {
        "reckless_attack": "barbarian.reckless_attack",
        "divine_smite": "paladin.divine_smite",
        "uncanny_dodge": "rogue.uncanny_dodge",
    }
)
"""The gate id each of the three in-attack entries answers, where the spike's
features declare one that is not the action's own id."""

_SPIKE_ACTIONS: Final = frozenset(
    {"rage", "second_wind", "healing_word", *_SPIKE_GATES}
)
"""What the spike's features provide on top of the Commands in `actions/`."""


def compile_spike_apl() -> Apl:
    """`SPIKE_APL` compiled, with `self.raging` declared as the one piece of
    state the spike's features add to the namespace."""
    schema = NamespaceSchema().extend("raging", Kind.BOOL, raging)
    return Apl.from_yaml(
        SPIKE_APL,
        schema=schema,
        actions=_SPIKE_ACTIONS,
        gates=_SPIKE_GATES,
    )


# --- the turn loop ----------------------------------------------------------


def run_encounter(spike: Spike, rng: Rng, rounds: int = ROUNDS_PER_ENCOUNTER) -> int:
    """One encounter. Returns the attack resolutions it took to run.

    Counts every attack made on a turn, on both sides; an enemy's opportunity
    attack is not counted (`docs/decisions.md` § w2c2).
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
    """Sweep the stores holding an effect before `now`'s hooks (
    `docs/decisions.md` § w2c)."""
    spike.clock.expire_before(now)


def _expire_after(spike: Spike, now: TimeKey) -> None:
    spike.clock.expire_after(now)


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
        entry = spike.apl.first(_offered(spike))
        if entry is None:
            break
        resolutions += _execute(spike, entry, rng)
    _expire_after(spike, end)
    return resolutions


def _offered(spike: Spike) -> frozenset[str]:
    """The ids the hero's economy can still pay for (DESIGN.md § APL)."""
    economy = spike.economy
    return _OFFERS[2 * (economy.actions > 0) + (economy.bonus_actions > 0)]


def _execute(spike: Spike, entry: CompiledEntry, rng: Rng) -> int:
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

    apl = spike.apl
    walk = apl.walk(entry)
    if walk is not None and walk.is_legal():
        walk.execute(rng=rng)
    target = apl.target(entry)
    if target is None:
        # The move: clause could not close the distance; the Action is spent.
        spike.economy.spend(ActionCost.ACTION)
        return 0

    spike.economy.spend(ActionCost.ACTION)
    contexts = spike.contexts
    trigger = apl.namespace.trigger
    log = spike.log
    for _ in range(ATTACKS_PER_TURN):
        resolve_attack(
            spike.hero, target, rng=rng, contexts=contexts, trigger=trigger, log=log
        )
    return ATTACKS_PER_TURN


def _enemy_turns(spike: Spike, rng: Rng, number: int) -> int:
    """The other side of the fight: three featureless attackers, closing.
    With `provoking`, they close by the turn loop's `Move`, and the skirmisher
    falls back out of reach after it swings, so the hero walks out of the
    other two's reach to follow it (`docs/decisions.md` § w2c2).

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
        enemy.economy.turn_started(enemy.speed_ft)
        if spike.skirmisher is None:
            destination = _closing_square(spike, enemy, hero)
            if destination is not None:
                _step(spike, enemy, destination)
        else:
            closing = Move(enemy, hero, world=spike.world)
            if closing.is_legal():
                closing.execute(rng=rng)
        if distance_ft(enemy.position, hero.position) <= enemy.reach_ft:
            resolve_attack(
                enemy, hero, rng=rng, contexts=contexts, trigger=trigger, log=log
            )
            resolutions += 1
        if enemy is spike.skirmisher:
            _step(spike, enemy, _away(enemy.position, hero.position))
        _expire_after(spike, end)
    return resolutions


def _closing_square(spike: "Spike", mover: Creature, target: Creature) -> Cell | None:
    """The unoccupied square within `mover`'s reach of `target` nearest to
    it, then lowest `y`, then `x`; `None` when it already has `target` in
    reach. Anchors, not footprints (`docs/decisions.md` § w2e).
    """
    origin = mover.position
    goal = target.position
    reach = mover.reach_ft
    if in_reach(origin, goal, reach):
        return None
    taken = {
        creature.position for creature in spike.combatants if creature is not mover
    }
    steps = reach // CELL_SIZE_FT
    candidates = [
        square
        for dy in range(-steps, steps + 1)
        for dx in range(-steps, steps + 1)
        if (square := Cell(goal.x + dx, goal.y + dy)) not in taken
    ]
    return min(
        candidates,
        key=lambda square: (distance_ft(origin, square), square.y, square.x),
        default=None,
    )


def _away(position: Cell, threat: Cell) -> Cell:
    """The square `_FALL_BACK` steps straight away from `threat`."""
    dx = (position.x > threat.x) - (position.x < threat.x)
    dy = (position.y > threat.y) - (position.y < threat.y)
    return Cell(position.x + _FALL_BACK * dx, position.y + _FALL_BACK * dy)


def _step(spike: Spike, mover: Creature, destination: Cell) -> None:
    """Walk `mover` along the shortest legal route toward `destination`, as
    far as its movement left allows, past everybody else's square (DESIGN.md
    § APL). Each step is charged to its economy.
    """
    blocked = frozenset(
        creature.position for creature in spike.combatants if creature is not mover
    )
    if destination in blocked:
        return
    walked = route(mover.position, destination, blocked=blocked, size=mover.size)
    if not walked:
        return
    reachable = walked[: steps_within(mover.economy.movement_ft)]
    if not reachable:
        return
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
    provoking: bool = True,
) -> tuple[float, int]:
    """Attack resolutions per second, median of `repeats` runs after a warmup.

    Returns the rate and the resolutions in one repeat. The default of a
    thousand encounters is a floor, not a number to trim (`docs/decisions.md`
    § w0i). `provoking` is `build_spike()`'s.
    """
    spike, rng = build_spike(
        seed, pooled=pooled, log_level=log_level, provoking=provoking
    )
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
        "--no-provoking",
        action="store_true",
        help="the configuration before w2c2: no enemy makes an opportunity "
        "attack and none falls back",
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
        provoking=not args.no_provoking,
    )
    print(
        f"{resolutions} attack resolutions per run, "
        f"median of {args.repeats} runs after a warmup"
    )
    if args.no_provoking:
        verdict = "PASS" if rate >= BUDGET_PER_SECOND else "MISS"
        budget = f"budget {BUDGET_PER_SECOND:,}) -- {verdict}"
    else:
        verdict = "PASS"
        budget = "the budget is quoted on --no-provoking)"
    print(f"{rate:,.0f} attack resolutions/sec/core  ({budget}")
    if args.startup:
        print(f"{measure_startup():.3f} s per spawn worker (startup, measured apart)")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
