"""The w0i contract: the spike is as hostile as ROADMAP w0i says it is.

Each assertion below pins one clause of that specification:

- 25 attached `HookSource`s, 15 of them on `on_hit`
- an 8-condition APL with a `target:` selector and a `move:` clause
- real movement on a real grid, and both directions of the attack
- the dispatcher's three escape valves — `once_per`, `costs`, `gate` — actually
  reached, and reached by a handler that *applies*
- a run that reproduces exactly from its seed

The throughput assertion itself is marked `bench` and deselected by default
(see `pyproject.toml`), so the M0 question is asked deliberately:

    uv run pytest -m bench            # or: python -m dndsim.tools.spike

Sources: ROADMAP w0i; DESIGN.md § Performance, § The spike must be pessimistic,
§ The benchmark gate; `docs/decisions.md` § w0i.
"""

from unittest.mock import patch

import pytest

from dndsim.core.log import LogLevel
from dndsim.core.timeline import Reset
from dndsim.engine.attack import ATTACK, ATTACK_ROLL
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
    _boundaries,
    _enemy_turns,
    _expire_after,
    _expire_before,
    _hero_turn,
    build_spike,
    measure,
    measure_startup,
    run_encounter,
)
from dndsim.tools.spike_apl import Apl
from dndsim.tools.spike_features import (
    ON_HIT_SOURCES,
    RAGE,
    SOURCES,
    SpikeRage,
    raging,
)

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
    spike, _ = build_spike(0)

    assert {e.gate for e in spike.apl.entries if e.gate is not None} == {
        "barbarian.reckless_attack",
        "paladin.divine_smite",
        "rogue.uncanny_dodge",
    }


def test_all_three_escape_valves_are_declared_and_reached() -> None:
    """`once_per`, `costs` and `gate` are each declared, and reached in a
    fight."""
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
    counter."""
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
    """The observational handlers are dispatched and never apply."""
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


def test_a_reset_brings_a_dead_hero_and_its_enemies_back() -> None:
    """A hero that died in one encounter fights the next: the reset puts back
    what dying took, not only the hit points."""
    spike, _ = build_spike(0)
    spike.hero.hp = 0
    spike.hero.die()
    spike.enemies[0].hp = 0
    spike.enemies[0].die()

    spike.reset()

    assert spike.hero.is_alive and not spike.hero.is_down
    assert spike.enemies[0].is_alive and not spike.enemies[0].is_down


def test_every_encounter_resolves_the_same_number_of_attacks() -> None:
    """The count above is a property of the *shape* of the encounter — five
    rounds, three swings, three enemies — and it has to hold on every seed, not
    on the one the assertion was written against (`docs/decisions.md` § w0i,
    `_TURN_COSTS`).
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
    `self.hp_pct < 0.5`, so a hero under half takes it *and* attacks. Rage, the
    other Bonus Action entry, is set so that it does not outrank this one.
    """
    spike, rng = build_spike(0)
    spike.reset()
    spike.hero.effects.apply(SpikeRage(spike.hero))
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
    assert not raging(spike.hero)

    run_encounter(spike, rng)

    assert raging(spike.hero)


def test_the_victims_gate_is_asked_about_the_swing_that_is_landing() -> None:
    """The hero's Uncanny Dodge gate reads `trigger.damage_estimate`, and on an
    enemy swing that is the enemy's estimate: at most a critical maul, never the
    hero's own last swing, whose bag carries Sneak Attack and the smite on top of
    a greatsword.
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

    # `allows()` ignores the context it is handed.
    assert not spike.apl.allows("nobody.asked.for.this", source, RoundContext(round=1))


def test_a_run_reproduces_exactly_from_its_seed() -> None:
    """Two spikes built from one seed fight identically."""
    first, first_rng = build_spike(7)
    second, second_rng = build_spike(7)

    for _ in range(3):
        run_encounter(first, first_rng)
        run_encounter(second, second_rng)

    assert first.hero.hp == second.hero.hp
    assert first.hero.position == second.hero.position
    assert [e.hp for e in first.enemies] == [e.hp for e in second.enemies]


def test_pooling_contexts_does_not_change_the_fight() -> None:
    """Reusing a context rather than allocating one changes nothing in the
    fight."""
    pooled, pooled_rng = build_spike(3, pooled=True)
    fresh, fresh_rng = build_spike(3, pooled=False)

    for _ in range(3):
        run_encounter(pooled, pooled_rng)
        run_encounter(fresh, fresh_rng)

    assert pooled.hero.hp == fresh.hero.hp
    assert [e.hp for e in pooled.enemies] == [e.hp for e in fresh.enemies]


def test_every_attack_writes_its_records_to_the_log() -> None:
    """The log is inside the measurement (ROADMAP w1i): one `ATTACK` record per
    resolution at both levels, and one `ATTACK_ROLL` under each at `full`."""
    for level, rolls in ((LogLevel.FULL, True), (LogLevel.SUMMARY, False)):
        spike, rng = build_spike(0, log_level=level)

        resolutions = run_encounter(spike, rng)

        kinds = [record[0] for record in spike.log]
        assert kinds.count(ATTACK) == resolutions
        assert kinds.count(ATTACK_ROLL) == (resolutions if rolls else 0)


def test_the_log_holds_one_encounter_at_a_time() -> None:
    """Cleared by `reset()`, like the hit points, so a thousand-encounter run
    does not measure a list growing to a hundred thousand records."""
    spike, rng = build_spike(0, log_level=LogLevel.SUMMARY)

    run_encounter(spike, rng)
    resolutions = run_encounter(spike, rng)

    assert len(spike.log) == resolutions


def test_the_same_seed_writes_the_same_log() -> None:
    """Seed reproduces the event log record for record."""
    first, first_rng = build_spike(7)
    second, second_rng = build_spike(7)

    for _ in range(3):
        run_encounter(first, first_rng)
        run_encounter(second, second_rng)

        assert first.log.records == second.log.records


def test_the_measurement_returns_a_rate_and_a_resolution_count() -> None:
    """The harness runs, whatever the number is. Not marked `bench`, so a broken
    harness fails the ordinary suite."""
    rate, resolutions = measure(encounters=5, repeats=1)

    assert resolutions == 5 * _RESOLUTIONS_PER_ENCOUNTER
    assert rate > 0


# --- the W1 substrate in place of the stand-ins -----------------------------


def _smites(spike: Spike) -> int:
    return next(
        handler.applied
        for source in spike.hero.dispatcher.sources()
        for handler in spike.hero.dispatcher._bound[source]
        if f"{handler.source_id}.{handler.handler.name}" == "divine_smite.smite"
    )


def test_a_rage_that_is_not_kept_up_ends_with_the_heros_next_turn() -> None:
    """The sweeps are wired: a Rage taken on turn one and not refreshed since —
    the enemies' swings are not the hero's — is still up through the hero's
    next turn and gone once it ends (`Duration.until_end_of_next_turn_of`)."""
    spike, rng = build_spike(0)
    spike.reset()
    _hero_turn(spike, rng, 1)
    _enemy_turns(spike, rng, 1)
    start, end = _boundaries(2)[0]

    _expire_before(spike, start)
    assert raging(spike.hero), "the Rage ended before the turn it lasts through"

    _expire_after(spike, end)
    assert not raging(spike.hero), "the sweep at the end of the turn missed it"


def test_a_rage_kept_up_by_swinging_lasts_the_whole_encounter() -> None:
    """One Rage, applied once and refreshed by every round's swings, so its
    deadline after the last round is the end of the hero's turn after it."""
    spike, rng = build_spike(0)
    spike.reset()

    for number in range(1, ROUNDS_PER_ENCOUNTER + 1):
        _hero_turn(spike, rng, number)
        _enemy_turns(spike, rng, number)
        assert raging(spike.hero), f"the Rage was gone after round {number}"

    (rage,) = spike.hero.effects
    assert isinstance(rage, SpikeRage)
    assert rage.expiry is not None
    assert rage.expiry.end == _boundaries(ROUNDS_PER_ENCOUNTER + 1)[0][1]


def test_rage_spends_one_use_an_encounter_and_the_reset_gives_it_back() -> None:
    spike, rng = build_spike(0)
    pool = spike.hero.resources[RAGE.key]

    for _ in range(3):
        run_encounter(spike, rng)
        assert pool.current == pool.maximum - 1

    spike.reset()
    assert pool.current == pool.maximum


def test_a_reset_leaves_no_effect_on_any_creature() -> None:
    spike, rng = build_spike(0)
    run_encounter(spike, rng)
    spike.enemies[0].effects.apply(SpikeRage(spike.enemies[0]))

    spike.reset()

    assert [len(c.effects) for c in spike.combatants] == [0] * (ENEMIES + 1)


def test_divine_smite_spends_a_real_slot_each_time_it_lands() -> None:
    """One slot per smite applied, and the reset puts every one of them back."""
    spike, rng = build_spike(0)
    slots = spike.hero.resources.spells
    spike.reset()
    full, highest = slots.total_above(1), slots.highest()

    run_encounter(spike, rng)

    assert _smites(spike) > 0, "the smite never landed, so nothing was spent"
    assert slots.total_above(1) == full - _smites(spike)

    spike.reset()
    assert (slots.total_above(1), slots.highest()) == (full, highest)


def test_the_smites_slot_gate_never_runs_dry_within_an_encounter() -> None:
    """`self.slots.highest >= 3` must answer yes to the last swing, as the
    stand-in it replaced always did (`docs/decisions.md` § w1k)."""
    for seed in range(6):
        spike, rng = build_spike(seed)
        for _ in range(10):
            run_encounter(spike, rng)

            assert spike.hero.resources.spells.highest() >= 3, (
                f"seed {seed} ran its highest slot down to "
                f"{spike.hero.resources.spells.highest()} in one encounter"
            )


def test_the_sweeps_move_every_stores_clock() -> None:
    """The last boundary swept is the end of the last seat's last turn."""
    spike, rng = build_spike(0)

    run_encounter(spike, rng)

    last = _boundaries(ROUNDS_PER_ENCOUNTER)[-1][1]
    assert [c.effects.now for c in spike.combatants] == [last] * (ENEMIES + 1)


# --- the M0 gate itself ------------------------------------------------------


@pytest.mark.bench
def test_the_spike_meets_the_m0_budget() -> None:
    """The budget, on the configuration ROADMAP w0i specifies
    (`docs/decisions.md` § w0i)."""
    rate, _ = measure(encounters=1000, repeats=5)

    assert rate >= BUDGET_PER_SECOND, (
        f"{rate:,.0f} attack resolutions/sec/core against a budget of "
        f"{BUDGET_PER_SECOND:,}. M0 is the moment to revise the dispatch "
        "design rather than to lower this number quietly."
    )


@pytest.mark.bench
def test_worker_startup_is_measured_apart_from_the_rate() -> None:
    """Worker start-up, measured apart from the rate (`docs/decisions.md`
    § w0i).

    No budget is attached. What is asserted is only that it is a plausible
    interpreter start rather than a pool that silently did nothing.
    """
    seconds = measure_startup(workers=2)

    assert 0.0 < seconds < 30.0
