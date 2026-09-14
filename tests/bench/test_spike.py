"""The w0i contract: the spike is as hostile as ROADMAP w0i says it is.

**A performance number is only as trustworthy as the configuration it was taken
on**, and the failure mode this file exists to prevent is the one DESIGN.md
§ The spike must be pessimistic names outright: a benchmark that quietly
measures a stub, clears the budget, and spends M0 — the one cheap moment to
revise the dispatch design — on nothing. Every assertion below is one clause of
that specification turned into something that breaks when it stops being true:

- 25 attached `HookSource`s, 15 of them on `on_hit`
- an 8-condition APL with a `target:` selector and a `move:` clause
- real movement on a real grid, and both directions of the attack
- the dispatcher's three escape valves — `once_per`, `costs`, `gate` — actually
  reached, rather than declared and never asked, and reached by a handler that
  *applies*: a gate that always answers no buys the question and none of what
  it guards
- a run that reproduces exactly from its seed, which is what makes two
  measurements comparable at all

**The throughput assertion itself is marked `bench` and deselected by default**
(see `pyproject.toml`). A wall-clock assertion on a shared CI runner fires on
noise, and "a gate that fires on noise gets disabled within a month, at which
point performance decays exactly as predicted" (DESIGN.md § The benchmark
gate). `w3i` is the task that turns it into a CI gate the right way — median of
N against a same-run microbenchmark baseline. Until then the M0 question is
asked deliberately:

    uv run pytest -m bench            # or: python -m dndsim.tools.spike

Sources: ROADMAP w0i; DESIGN.md § Performance, § The spike must be pessimistic,
§ The benchmark gate.
"""

from unittest.mock import patch

import pytest

from dndsim.core.timeline import Reset
from dndsim.hooks.contexts import Context, RoundContext
from dndsim.hooks.definitions import ON_HIT
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import ActionCost
from dndsim.tools.spike import (
    ATTACKS_PER_TURN,
    BUDGET_PER_SECOND,
    ENEMIES,
    ROUNDS_PER_ENCOUNTER,
    Spike,
    _hero_turn,
    build_spike,
    measure,
    measure_startup,
    run_encounter,
)
from dndsim.tools.spike_apl import Apl
from dndsim.tools.spike_features import ON_HIT_SOURCES, SOURCES

_RESOLUTIONS_PER_ENCOUNTER = ROUNDS_PER_ENCOUNTER * (ATTACKS_PER_TURN + ENEMIES)
"""Five rounds of three hero swings and one swing from each of three enemies."""

_MOST_A_MAUL_CAN_DO = 2 * 2 * 6 + 1
"""A critical hit with the enemies' 2d6 maul, plus their +1 — the ceiling on
what an enemy swing can ever put in front of the hero's reaction."""


def _fought(encounters: int = 10) -> Spike:
    """Ten encounters, not three, because the reaction is the rarest of the
    six guarded handlers: Uncanny Dodge needs the enemies to roll a swing worth
    a quarter of the hero's remaining hit points, which happens about twice in
    ten on the leanest of the seeds tried."""
    spike, rng = build_spike(0)
    for _ in range(encounters):
        run_encounter(spike, rng)
    return spike


# --- the configuration ROADMAP w0i specifies --------------------------------


def test_the_hostile_creature_carries_twenty_five_sources() -> None:
    spike, _ = build_spike(0)

    assert len(SOURCES) == 25
    assert len(spike.hero.dispatcher.sources()) == 25


def test_fifteen_of_them_are_on_the_busiest_hook() -> None:
    """`on_hit` is where Sneak Attack, Divine Smite and the masteries live, and
    w0i puts at least fifteen there because that is what a level-12 Barbarian
    actually looks like."""
    spike, _ = build_spike(0)

    assert len(ON_HIT_SOURCES) == 15
    assert len(spike.hero.dispatcher.handlers(ON_HIT)) == 15


def test_the_apl_has_eight_conditions_a_target_and_a_move() -> None:
    spike, _ = build_spike(0)
    entries = spike.apl.entries

    assert len(entries) == 8
    assert [e.action for e in entries] == [
        "rage",
        "reckless_attack",
        "divine_smite",
        "uncanny_dodge",
        "second_wind",
        "healing_word",
        "attack",
        "dodge",
    ]
    assert sum(1 for e in entries if e.target is not None) >= 1
    assert sum(1 for e in entries if e.move is not None) >= 1


def test_three_entries_are_answered_inside_an_attack() -> None:
    """A `gate:` is policy evaluated on the hot path rather than once a turn,
    which is the half of APL cost an inner-loop benchmark would miss."""
    spike, _ = build_spike(0)

    assert {e.gate for e in spike.apl.entries if e.gate is not None} == {
        "barbarian.reckless_attack",
        "paladin.divine_smite",
        "rogue.uncanny_dodge",
    }


def test_all_three_escape_valves_are_declared_and_reached() -> None:
    """`once_per`, `costs` and `gate` are the three questions the dispatcher
    asks before a handler runs; a spike declaring none of them would measure
    the cheap path only."""
    spike = _fought()
    bound = [
        handler
        for source in spike.hero.dispatcher.sources()
        for handler in spike.hero.dispatcher._bound[source]
    ]

    assert {b.once_per for b in bound} >= {Reset.TURN, Reset.OWN_TURN}
    assert ActionCost.REACTION in {b.costs for b in bound}
    assert sum(1 for b in bound if b.gate is not None) == 3
    assert sum(1 for b in bound if b.guarded) == 6


def test_the_riders_that_should_land_do_land() -> None:
    """The applies-versus-skips mix, checked through the no-op detector's own
    counter. A benchmark where nothing applied would never build a damage bag,
    never roll more than the weapon's dice, and never spend a budget."""
    spike = _fought()
    applied = {
        f"{handler.source_id}.{handler.handler.name}": handler.applied
        for source in spike.hero.dispatcher.sources()
        for handler in spike.hero.dispatcher._bound[source]
    }

    for name in (
        "heavy_weapon_rider.rider",  # every hit
        "elemental_weapon.burn",  # every hit
        "brutal_strike.strike",  # once per turn — the ledger let it through
        "poisoned_blade.poison",  # once per own turn
        "divine_smite.smite",  # gated — the APL let it through
        "reckless_attack.reckless",  # gated, on the declare side
        "rage_damage.rage",  # state-dependent
        "frenzy_dice.frenzy",  # state-dependent
        "sneak_attack.sneak",  # once per turn and state-dependent
        "bloodied_bonus.bloodied",  # conditional on the target being Bloodied
        "vex_spender.spend",  # conditional on the roll
        "uncanny_dodge.dodge",  # gated *and* costed — the victim's side
    ):
        assert applied[name] > 0, f"{name} never applied — the mix is flattered"


def test_the_observational_riders_never_apply_and_that_is_the_point() -> None:
    """Most of a martial's `on_hit` list does not fire on any given swing.
    These are the ones that never do, and they are in the measurement because
    a handler that skips still costs a call."""
    spike = _fought()
    applied = {
        f"{handler.source_id}.{handler.handler.name}": handler.applied
        for source in spike.hero.dispatcher.sources()
        for handler in spike.hero.dispatcher._bound[source]
    }

    for name in (
        "graze_mastery.graze",
        "relentless_tally.count",
        "attack_tally.resolved",
        "turn_upkeep.upkeep",
    ):
        assert applied[name] == 0


# --- what the encounter actually does ---------------------------------------


def test_an_encounter_resolves_attacks_in_both_directions() -> None:
    spike, rng = build_spike(0)

    resolutions = run_encounter(spike, rng)

    assert resolutions == _RESOLUTIONS_PER_ENCOUNTER == 30
    assert spike.enemies[0].hp < spike.enemies[0].max_hp, "the hero never landed one"
    assert spike.hero.hp < spike.hero.max_hp, "the hero was never hit back"


def test_every_encounter_resolves_the_same_number_of_attacks() -> None:
    """The count above is a property of the *shape* of the encounter — five
    rounds, three swings, three enemies — and it has to hold on every seed, not
    on the one the assertion was written against.

    What can break it is an entry above `attack` in the list taking the hero's
    Action, since nothing in the spike heals and a policy that stops swinging
    stays stopped: the encounter then quietly resolves three fewer attacks per
    remaining round, and the exact-integer assertions elsewhere in this file
    become a fact about seed 0. `_TURN_COSTS` is what prevents it, by charging
    the three Bonus Action entries a Bonus Action.
    """
    for seed in range(6):
        spike, rng = build_spike(seed)
        counts = {run_encounter(spike, rng) for _ in range(40)}

        assert counts == {_RESOLUTIONS_PER_ENCOUNTER}, (
            f"seed {seed} resolved {sorted(counts)} attacks across its "
            "encounters; the count is supposed to be a property of the shape"
        )


def test_a_wounded_hero_takes_second_wind_and_still_swings() -> None:
    """The same invariant from the other side, without waiting on the dice.

    `second_wind` sits above `attack` in the list and its `if:` is
    `self.hp_pct < 0.5`, so a hero under half is supposed to take it *and*
    attack: Second Wind is a Bonus Action in the 2024 rules and the Attack
    action is an Action. Rage is set here because it is the other Bonus Action
    entry and it outranks this one — from round two on it has already fired,
    which is when the hero is wounded enough for this entry to be reachable.
    """
    spike, rng = build_spike(0)
    spike.reset()
    spike.state.raging = True
    spike.hero.hp = spike.hero.max_hp // 4

    resolutions = _hero_turn(spike, rng, 1)

    assert not spike.economy.bonus_actions, "the wounded hero never took Second Wind"
    assert resolutions == ATTACKS_PER_TURN, (
        "a Bonus Action entry ate the hero's Attack action"
    )


def test_the_hero_walks_to_the_target_its_policy_chose() -> None:
    """`move:` is not decoration: the hero starts five squares from its nearest
    enemy and has to close before it can swing (DESIGN.md § APL)."""
    spike, rng = build_spike(0)
    start = spike.hero.position

    run_encounter(spike, rng)

    assert spike.hero.position != start
    assert spike.hero.is_alive


def test_the_enemies_close_on_the_hero_too() -> None:
    spike, rng = build_spike(0)
    starts = [enemy.position for enemy in spike.enemies]

    run_encounter(spike, rng)

    assert [e.position for e in spike.enemies] != starts


def test_rage_is_taken_on_the_first_turn_and_not_again() -> None:
    """The APL's first entry, and the one that proves the list is re-evaluated
    per action rather than once: `not self.raging` stops being true after it
    fires, and the loop falls through to the attack."""
    spike, rng = build_spike(0)
    assert not spike.state.raging

    run_encounter(spike, rng)

    assert spike.state.raging


def test_the_victims_gate_is_asked_about_the_swing_that_is_landing() -> None:
    """`trigger.*` is what the engine is offering *right now*, and the only
    gate on the victim's side of an attack is the hero's Uncanny Dodge.

    Its `if:` reads `trigger.damage_estimate`, which is bound inside
    `attack_once()` — so an enemy turn that dispatched without a trigger would
    hand the gate the estimate left over from the hero's own last swing, which
    is a different number by an order of magnitude and never changes during the
    enemy's round. What separates the two is the size: an enemy can put at most
    a critical maul on the table, while the hero's bag carries Sneak Attack and
    the smite on top of a greatsword.
    """
    spike, rng = build_spike(0)
    offered: list[int] = []
    asked = Apl.allows

    def recording(self: Apl, gate_id: str, source: HookSource, ctx: Context) -> bool:
        if gate_id == "rogue.uncanny_dodge":
            offered.append(self.namespace.trigger.damage_estimate)
        return asked(self, gate_id, source, ctx)

    with patch.object(Apl, "allows", recording):
        run_encounter(spike, rng)

    assert offered, "the reaction's gate was never asked"
    assert max(offered) <= _MOST_A_MAUL_CAN_DO, (
        f"the gate was offered {max(offered)}, which is more than a critical "
        "maul — it is reading the hero's own swing, not the one landing on it"
    )


def test_an_unmentioned_gate_is_off() -> None:
    """The Null Object default policy: attaching a gated feature no APL entry
    claims never silently changes a build's numbers (DESIGN.md § APL)."""
    spike, _ = build_spike(0)
    source = spike.sources[0]

    # `allows()` ignores the context it is handed: `trigger.*` is populated by
    # the engine before the offer, never scraped off the event (`spike_apl.py`).
    assert not spike.apl.allows("nobody.asked.for.this", source, RoundContext(round=1))


def test_a_run_reproduces_exactly_from_its_seed() -> None:
    """The property every comparison of two measurements rests on."""
    first, first_rng = build_spike(7)
    second, second_rng = build_spike(7)

    for _ in range(3):
        run_encounter(first, first_rng)
        run_encounter(second, second_rng)

    assert first.hero.hp == second.hero.hp
    assert first.hero.position == second.hero.position
    assert [e.hp for e in first.enemies] == [e.hp for e in second.enemies]


def test_pooling_contexts_does_not_change_the_fight() -> None:
    """The `w0i` lever is supposed to be invisible: reusing a context rather
    than allocating one changes what it costs and nothing else."""
    pooled, pooled_rng = build_spike(3, pooled=True)
    fresh, fresh_rng = build_spike(3, pooled=False)

    for _ in range(3):
        run_encounter(pooled, pooled_rng)
        run_encounter(fresh, fresh_rng)

    assert pooled.hero.hp == fresh.hero.hp
    assert [e.hp for e in pooled.enemies] == [e.hp for e in fresh.enemies]


def test_the_measurement_returns_a_rate_and_a_resolution_count() -> None:
    """The harness runs, whatever the number is. Kept out of the `bench` mark
    so that a broken harness fails the ordinary suite rather than waiting for
    somebody to ask the M0 question."""
    rate, resolutions = measure(encounters=5, repeats=1)

    assert resolutions == 5 * _RESOLUTIONS_PER_ENCOUNTER
    assert rate > 0


# --- the M0 gate itself ------------------------------------------------------


@pytest.mark.bench
def test_the_spike_meets_the_m0_budget() -> None:
    """The budget, on the configuration ROADMAP w0i specifies.

    30,000 after M0 revised it down from the unmeasured 50,000 — the story is in
    `docs/decisions.md` § w0i, and `BUDGET_PER_SECOND` carries the warning about
    revising it a second time. Deselected by default for the reason in this
    module's docstring; `w3i` turns it into a CI gate against a same-run
    baseline, which is the form that survives a shared runner.
    """
    rate, _ = measure(encounters=1000, repeats=5)

    assert rate >= BUDGET_PER_SECOND, (
        f"{rate:,.0f} attack resolutions/sec/core against a budget of "
        f"{BUDGET_PER_SECOND:,}. M0 is the moment to revise the dispatch "
        "design rather than to lower this number quietly."
    )


@pytest.mark.bench
def test_worker_startup_is_measured_apart_from_the_rate() -> None:
    """Windows `spawn`s: every worker re-imports the package and rebuilds every
    character, and `compare --sweep dummy.ac=10:22` pays it thirteen times.

    No budget attached — this is a number to know, not a gate. What is asserted
    is only that it is a plausible interpreter start rather than a pool that
    silently did nothing.
    """
    seconds = measure_startup(workers=2)

    assert 0.0 < seconds < 30.0
