"""The pessimistic performance spike (ROADMAP w0i) — the M0 gate.

**Budget: ≥30,000 attack resolutions per second per core**, on a deliberately
hostile configuration, measured before any content is written. DESIGN.md
§ Performance sizes the workload it protects: ~10k iterations x 5 encounters x
5 rounds x 5 creatures x 3 attacks is about 4M attack resolutions for one build
comparison — around two minutes a core, or under twenty seconds across eight of
them.

That figure was 50,000 until M0. The spike is what revised it: the original was
set before anything had been measured, and `docs/decisions.md` § w0i has the
measurement, the revision pass that took the architecture from 17k to ~31.5k,
and the argument for why the remaining distance was not reachable by tuning.

**Why the configuration is what it is.** "The obvious way to run this is against
a stub creature and an attack loop. That measurement is worthless… M0 exists to
be the cheap moment to revise; a stub spends that moment on nothing." So:

- **25 attached `HookSource`s, 15 of them on `on_hit`** — `tools/spike_features.py`,
  which is also where the applies-versus-skips mix is argued.
- **An 8-condition APL, re-evaluated per action**, with a `target:` selector and
  a `move:` clause, hand-compiled over the proxy shape `w2e` will emit —
  `tools/spike_apl.py`. Three of the eight are `gate:` answers given *inside*
  the attack, which is what puts policy evaluation on the hot path rather than
  once a turn.
- **Real movement on a real grid**: both sides reposition every round through
  `core/geometry.route()` over a real blocked-cell set, so distance and reach
  queries are in the measurement.
- **Both directions of the attack.** The hero swings three times a round and
  three enemies swing back, because four of the twenty-five sources listen on
  the victim's side and a benchmark that never took a hit would not run them.
- **Worker startup measured separately** (`measure_startup()`): on Windows a
  worker is `spawn`ed, so it re-imports the package and rebuilds every
  character. `compare --sweep dummy.ac=10:22` pays that thirteen times, and it
  is the cost an inner-loop benchmark never sees.

**What is deliberately not in the measurement, and why that is honest.** Effects
(`w1c`) and resources (`w1b`) do not exist, so there is no per-boundary expiry
sweep and no pool arithmetic; the spike therefore *understates* the eventual
per-round cost, and `w2e`'s re-run of this benchmark is where the real APL
compiler has to come in at or under the number below. What the spike does not
understate is the per-attack cost, which is where the budget is spent.

Run it: `python -m dndsim.tools.spike` (add `--startup` for the worker cost).

Sources: DESIGN.md § Performance, § The spike must be pessimistic, § Levers;
ROADMAP w0i, w2e.
"""

import argparse
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from types import MappingProxyType
from typing import Final

from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell, distance_ft, route, steps_within
from dndsim.core.rng import MersenneRng, Rng, mix
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.engine.triggers import walk_provoking
from dndsim.hooks.contexts import MoveContext, TurnContext
from dndsim.hooks.definitions import ON_MOVE, ON_TURN_START
from dndsim.rules.enums import Ability, ActionCost, DamageType
from dndsim.tools.skeleton import FRESH, AttackContexts, PooledContexts, attack_once
from dndsim.tools.spike_apl import (
    AlliesProxy,
    Apl,
    EnemiesProxy,
    Entry,
    Namespace,
    SelfProxy,
    SpikeState,
    first,
    lowest_hp,
    toward_until_in_reach,
)
from dndsim.tools.spike_features import SOURCES, Fixture

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
"""The M0 gate (ROADMAP w0i, DESIGN.md § Performance). Attack resolutions per
second per core, on the configuration this module builds.

**Revised down from 50,000 at M0, deliberately and once.** The original figure
was set before anything had been measured and turned out to sit at the
theoretical floor of this object model in CPython; the spike measures ~31.5k
after the revision pass in `docs/decisions.md` § w0i, and 30,000 is that number
with the run-to-run spread taken off. Lowering it again is the one edit in this
file that must not be made quietly — it is how a project stops noticing that it
has become slow."""

ATTACKS_PER_TURN: Final = 3
"""Extra Attack at level 12, which is what DESIGN.md § Performance sizes the
4M-resolution workload with."""

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
"""Sized against the two things that pull in opposite directions.

**Upwards:** the hero has to survive all five rounds of being swung at by three
enemies — one that dropped mid-encounter would end the benchmark early and make
the measurement depend on the dice. Across a thousand encounters on three seeds
this hero never falls below a third of it.

**Downwards:** the APL's reaction entry is `trigger.damage_estimate >= self.hp *
0.25`, so an inflated pool is a gate that can never answer yes — and a
reaction-costing handler that never applies means the dispatcher's economy valve
is only ever asked in the affirmative and `ctx.halve()` is never on the measured
path. At 70 the entry fires about 0.4 times an encounter: often enough to be
measured, rarely enough to still be a reaction rather than a rider."""

_ENEMY_HP: Final = 1_000
_HERO_START: Final = Cell(0, 0)
_ENEMY_STARTS: Final = (Cell(5, 0), Cell(0, 5), Cell(7, 6))


def _seat(place: int) -> InitiativeKey:
    """The `place`th key in a fixed order, hero first.

    The spike never rolls initiative — it runs the hero's turn and then the
    enemies' — so the keys only have to be distinct and ordered. Tying the roll
    and separating them on the draw is the cheapest way to say that; `w2c` draws
    the real ones (`core/timeline.py`).
    """
    return InitiativeKey(
        initiative=20, dexterity=10, player_character=place == 0, tiebreak=place / 10
    )


_HERO_SEAT: Final = _seat(0)
"""Spread out, so that focus-firing the lowest-hp enemy means walking."""

_MAUL: Final = Weapon(
    name="Maul", damage=Dice(2, 6), damage_type=DamageType.BLUDGEONING
)
"""What the enemies swing. Big enough that a good roll is worth a reaction —
which is what puts Uncanny Dodge's gate, its reaction cost and `ctx.halve()` on
the measured path — and no bigger, because the enemies exist to make the hero's
four defensive sources run rather than to end the encounter. `RageResistance`
halves it, which is most of why 70 hp lasts five rounds."""

_TURN_COSTS: Final = MappingProxyType(
    {
        "rage": ActionCost.BONUS_ACTION,
        "second_wind": ActionCost.BONUS_ACTION,
        "healing_word": ActionCost.BONUS_ACTION,
        "attack": ActionCost.ACTION,
    }
)
"""What the turn loop offers, and what each one costs to take.

The other four entries are riders and reactions, which are never offered as a
turn action — they are `gate:` answers given inside an event (DESIGN.md § APL:
"one flat ordered list; **the engine filters to currently-offered ids**").

**The costs are the XPHB ones, and that is load-bearing rather than flavour.**
Rage, Second Wind and Healing Word are all Bonus Actions in the 2024 rules; only
the Attack action is an Action. An earlier version of this file charged all four
an Action, which made the offer filter wrong in a way that quietly ate the
measurement: `second_wind`'s entry sits *above* `attack` in the list and its
`if:` is `self.hp_pct < 0.5`, so the first turn the hero dropped under half it
spent its Action on Second Wind — and, since nothing here heals, stayed under
half and never swung again for the rest of the encounter. It cost 21 of 30,000
resolutions a run, which is well under the noise floor and is not why this is
fixed; it is fixed because an encounter whose resolution count depends on the
dice is one the exact-integer assertions in `tests/bench/test_spike.py` can only
hold at the seed they were written against."""

_MAX_ACTIONS_PER_TURN: Final = 4


def _offer_table() -> tuple[frozenset[str], ...]:
    """The four possible offers, precomputed at import.

    There are two bits of economy the turn loop can be in — Action spent or
    not, Bonus Action spent or not — so there are four answers `_offered()` can
    give, and building the set per decision would put an allocation on a path
    that is asked up to four times a turn for nothing.
    """
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


class SpikeEconomy:
    """The action economy, as much of it as the dispatcher asks for.

    Satisfies the `Economy` protocol in `hooks/dispatch.py`; `w1a` owns the real
    `ActionEconomy` with movement, grants and Action Surge. What matters here is
    that a costed handler pays for a `can_spend()` call on the hot path and that
    a reaction really does run out — Uncanny Dodge spends one a few times in ten
    encounters, and `can_spend()` then answers False for the enemies still to
    swing that round — because a spike whose reaction was free would measure the
    cheap half of the dispatcher's three escape valves.
    """

    __slots__ = ("action", "bonus", "reaction")

    def __init__(self) -> None:
        self.action = True
        self.bonus = True
        self.reaction = True

    def can_spend(self, cost: ActionCost) -> bool:
        if cost is ActionCost.ACTION:
            return self.action
        if cost is ActionCost.BONUS_ACTION:
            return self.bonus
        return self.reaction

    def spend(self, cost: ActionCost) -> None:
        if cost is ActionCost.ACTION:
            self.action = False
        elif cost is ActionCost.BONUS_ACTION:
            self.bonus = False
        else:
            self.reaction = False

    def turn_started(self) -> None:
        """Everything back. The reaction comes back at the *start of your turn*
        (`core/timeline.py` on `Reset.TURN` vs `Reset.OWN_TURN`), which is the
        boundary this method is called on."""
        self.action = True
        self.bonus = True
        self.reaction = True


@dataclass(slots=True)
class Spike:
    """One assembled encounter: a hostile hero, its enemies, and its policy."""

    hero: Creature
    enemies: tuple[Creature, ...]
    combatants: tuple[Creature, ...]
    apl: Apl
    state: SpikeState
    economy: SpikeEconomy
    contexts: AttackContexts
    enemy_contexts: tuple[AttackContexts, ...]
    sources: tuple[Fixture, ...]

    def reset(self) -> None:
        """Put the encounter back to round one.

        What a runner does between the five encounters of an iteration, and
        deliberately inside the measurement: 4M resolutions come with 50k
        encounter resets, and a benchmark that reset outside the clock would be
        measuring a fight nobody ever starts.
        """
        self.hero.hp = self.hero.max_hp
        self.hero.position = _HERO_START
        self.state.raging = False
        self.state.slots_highest = 5
        self.economy.turn_started()
        for enemy, start in zip(self.enemies, _ENEMY_STARTS, strict=True):
            enemy.hp = enemy.max_hp
            enemy.position = start


def build_spike(seed: int = 0, *, pooled: bool = True) -> tuple[Spike, Rng]:
    """Assemble the hostile creature, its three enemies and its compiled APL.

    Everything expensive happens here rather than per attack, which is the point
    of DESIGN.md § Levers: the dispatch table is sorted once at attach time, the
    namespace proxies are built once per creature, and the APL is compiled once
    per build. A spike that rebuilt any of them per turn would be measuring the
    loader.

    `pooled` picks which half of the § Levers claim about contexts is being
    measured — reused per attack, or allocated per attack. Both are real
    configurations of the same engine, and the difference between the two
    numbers is the whole argument for the lever.
    """
    hero = Creature(
        name="Spike",
        stats=StatBlock(
            abilities=_HERO_SCORES, max_hp=_HERO_HP, armour_class=18, level=12
        ),
        position=_HERO_START,
        weapon=GREATSWORD,
        initiative=_HERO_SEAT,
    )
    enemies = tuple(
        Creature(
            name=f"Enemy{index}",
            stats=StatBlock(
                abilities=_ENEMY_SCORES, max_hp=_ENEMY_HP, armour_class=16, level=5
            ),
            position=start,
            weapon=_MAUL,
            initiative=_seat(index + 1),
        )
        for index, start in enumerate(_ENEMY_STARTS)
    )

    state = SpikeState()
    state.marked = enemies[0].name
    sources = tuple(source(hero, state) for source in SOURCES)
    for source in sources:
        hero.attach(source)

    namespace = Namespace(
        own=SelfProxy(hero, state),
        enemies=EnemiesProxy(hero, enemies),
        allies=AlliesProxy((hero,)),
    )
    apl = Apl(_entries(), namespace)
    economy = SpikeEconomy()
    hero.dispatcher.bind(economy=economy, gate=apl)

    spike = Spike(
        hero=hero,
        enemies=enemies,
        # The roster, built once: `engine/triggers.py` walks it per move to work
        # out who could react to it, and rebuilding it there would put a tuple
        # allocation on a path whose whole claim is that it costs nothing when
        # nobody in the room owns a reaction.
        combatants=(hero, *enemies),
        apl=apl,
        state=state,
        economy=economy,
        contexts=PooledContexts(hero, enemies[0], GREATSWORD) if pooled else FRESH,
        enemy_contexts=tuple(
            PooledContexts(enemy, hero, _MAUL) if pooled else FRESH for enemy in enemies
        ),
        sources=sources,
    )
    return spike, MersenneRng(mix(seed, 0))


# --- the eight entries, hand-compiled ---------------------------------------
#
# Each is one row of the YAML in DESIGN.md § APL, written as the callable the
# w2e compiler will emit for it. The comment above each is the clause it came
# from, so a reviewer can check the translation without running anything.


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

    The unit the budget is quoted in is the attack resolution, so this counts
    every dispatched attack — the hero's three a round and the enemies' one
    each — rather than turns or rounds.
    """
    spike.reset()
    resolutions = 0
    for number in range(1, rounds + 1):
        resolutions += _hero_turn(spike, rng, number)
        resolutions += _enemy_turns(spike, rng, number)
    return resolutions


def _hero_turn(spike: Spike, rng: Rng, number: int) -> int:
    hero = spike.hero
    hero.dispatcher.resets.turn_started(own=True)
    spike.economy.turn_started()
    turn = TurnContext(
        creature=hero,
        time=TimeKey(round=number, initiative=_HERO_SEAT, phase=TurnPhase.START),
    )
    hero.dispatch(ON_TURN_START, turn)
    turn.close()

    resolutions = 0
    for _ in range(_MAX_ACTIONS_PER_TURN):
        entry = spike.apl.choose(_offered(spike))
        if entry is None:
            break
        resolutions += _execute(spike, entry, rng)
    return resolutions


def _offered(spike: Spike) -> frozenset[str]:
    """The ids currently on the table, which is what the APL filters against.

    "The engine filters to currently-offered ids" (DESIGN.md § APL), and what
    makes an id offered is that the economy can still pay for it — so this is
    `_TURN_COSTS` read through `SpikeEconomy`, indexed rather than rebuilt.
    """
    economy = spike.economy
    return _OFFERS[2 * economy.action + economy.bonus]


def _execute(spike: Spike, entry: Entry, rng: Rng) -> int:
    """Take the offer the APL accepted. Returns attack resolutions."""
    action = entry.action
    if action != "attack":
        if action == "rage":
            spike.state.raging = True
        # rage flips the flag its dependent riders read; second_wind and
        # healing_word do nothing at all. What each *does* is w2b's and none of
        # it is on the path the budget is about — what each *costs* is this
        # file's, because the economy is what decides whether the hero still
        # gets to swing this turn (`_TURN_COSTS`).
        spike.economy.spend(_TURN_COSTS[action])
        return 0

    namespace = spike.apl.namespace
    if entry.move is not None:
        _walk(spike, entry.move(namespace))
    target = None if entry.target is None else entry.target(namespace)
    if target is None:
        # The move: clause could not close the distance — a real outcome on a
        # real grid, and the reason `APL_FALLTHROUGH` is a counter in the report
        # rather than an exception (DESIGN.md § APL).
        spike.economy.spend(ActionCost.ACTION)
        return 0

    namespace.target.bind(target)
    spike.economy.spend(ActionCost.ACTION)
    contexts = spike.contexts
    trigger = namespace.trigger
    for _ in range(ATTACKS_PER_TURN):
        attack_once(spike.hero, target, rng, contexts=contexts, trigger=trigger)
    return ATTACKS_PER_TURN


def _enemy_turns(spike: Spike, rng: Rng, number: int) -> int:
    """The other side of the fight: three featureless attackers, closing.

    They carry no sources of their own — what they are for is the hero's four
    defensive ones, which is also why their attacks go through the same
    `attack_once()` and the same damage pipeline.

    **The hero's `trigger` goes with them.** `trigger.*` is what the engine is
    offering the creature being asked, and the only gate on this side of the
    attack — Uncanny Dodge's `trigger.damage_estimate >= self.hp * 0.25` — is
    the hero's, answered while an enemy swings. Leaving it unbound here would
    hand that gate the estimate from the hero's own last swing, which is both
    the wrong number and a policy call that never changes.
    """
    hero = spike.hero
    trigger = spike.apl.namespace.trigger
    resolutions = 0
    for enemy, contexts in zip(spike.enemies, spike.enemy_contexts, strict=True):
        enemy.dispatcher.resets.turn_started(own=True)
        destination = toward_until_in_reach(enemy.position, enemy.reach_ft, hero)
        if destination is not None:
            _step(spike, enemy, destination)
        if distance_ft(enemy.position, hero.position) <= enemy.reach_ft:
            attack_once(enemy, hero, rng, contexts=contexts, trigger=trigger)
            resolutions += 1
    return resolutions


def _walk(spike: Spike, destination: Cell | None) -> None:
    if destination is not None:
        _step(spike, spike.hero, destination)


def _step(spike: Spike, mover: Creature, destination: Cell) -> None:
    """Resolve one `move:` decision into a walked route.

    "Movement is a decision, not a pathfinding problem: the selector picks a
    destination and the engine walks the shortest legal route to it"
    (DESIGN.md § APL). The blocked set is rebuilt per move because it is:
    everybody else's square is occupied, and who is standing where changes
    every turn.
    """
    blocked = frozenset(
        creature.position for creature in spike.combatants if creature is not mover
    )
    if destination in blocked:
        return
    walked = route(mover.position, destination, blocked=blocked)
    if not walked:
        return
    reachable = walked[: steps_within(mover.speed_ft)]
    if not reachable:
        return
    # `engine/triggers.py` is what actually moves the creature, because the
    # opportunity-attack offer has to be made from the square being left rather
    # than the one being entered. Nothing in this spike owns an `on_leave_reach`
    # handler, so what is in the measurement here is the audience computation
    # and its verdict — the case the design claims costs nothing, measured
    # rather than asserted. A configuration where the offers actually fire is
    # `w2c`'s to add when the turn loop and the Command exist to fire them.
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
    encounters: int = 1000, repeats: int = 5, seed: int = 0, *, pooled: bool = True
) -> tuple[float, int]:
    """Attack resolutions per second, median of `repeats` runs after a warmup.

    Median rather than mean and warmed up rather than cold, because DESIGN.md
    § The benchmark gate asks for exactly that of the CI gate `w3i` builds on
    top of this: "a gate that fires on noise gets disabled within a month, at
    which point performance decays exactly as predicted".

    **The default is a thousand encounters because two hundred was not enough.**
    At 200 the same machine returned 29.4k, 30.6k and 32.1k on three consecutive
    runs — a 9% spread, wide enough to swallow the regression a gate is for. At
    1000 (about a second of work per repeat) the spread is 0.2%. Whoever builds
    `w3i` should treat that as the floor on how little work a timed run may do,
    not as a number to trim when the suite feels slow.
    """
    spike, rng = build_spike(seed, pooled=pooled)
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

    Measured separately and never folded into the rate above, because it is a
    per-worker cost rather than a per-attack one: "at 10k iterations that
    amortises to nothing; at `compare --sweep dummy.ac=10:22` it is paid
    thirteen times over" (DESIGN.md § Performance). What it times is what
    Windows actually pays — a fresh interpreter, a fresh import of the package,
    and every character rebuilt — because `spawn` shares no memory with its
    parent.

    **A subprocess rather than `multiprocessing.Pool`, deliberately.** `spawn`
    brings a worker up by re-importing the parent's `__main__` module, so a
    pool started from a script without an `if __name__ == "__main__"` guard —
    or from inside a test runner, whose `__main__` is the runner — re-runs that
    module in every worker. A benchmark that can fork-bomb the machine it is
    measuring is not one anybody will run twice. Launching the interpreter
    directly measures the same three costs with none of that, and is what
    `sim/runner.py` (w2g) should be checked against when the real pool lands.
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
        "--startup",
        action="store_true",
        help="also measure spawn worker startup, separately",
    )
    args = parser.parse_args(argv)

    rate, resolutions = measure(
        args.encounters, args.repeats, args.seed, pooled=not args.fresh
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
