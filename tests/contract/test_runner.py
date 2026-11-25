"""The runner, `SimResult` and the API (ROADMAP `w2g`; `docs/decisions.md`
§ w2g; DESIGN.md § Performance, § Interfaces: library, CLI, and files)."""

import json
import multiprocessing
import pickle
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import pytest

from dndsim.api import SimConfig, run_sim
from dndsim.apl.apl import Apl, Entry
from dndsim.apl.expr import AplLoadError
from dndsim.core.log import LogLevel
from dndsim.creatures.dummy import DummySpec, MovementProfile, ThreatProfile
from dndsim.reporting.render_json import render_json
from dndsim.reporting.render_text import render_text
from dndsim.reporting.result import SimResult
from dndsim.reporting.stats import summarise
from dndsim.rules.enums import Ability, DamageType
from dndsim.sim.runner import iteration_seeds, run
from dndsim.sim.scenario import PARTY, Scenario
from m1_fixtures import CATALOGUE, fighter

DUMMY: Final = DummySpec(ac=15, hp=30)
SCHEMA: Final = (
    Path(__file__).parents[2] / "src" / "dndsim" / "reporting" / "simresult.schema.json"
)


def scenario(**changes: Any) -> Scenario:
    return replace(
        Scenario(builds=(fighter(),), catalogue=CATALOGUE, dummy=DUMMY), **changes
    )


def config(**changes: Any) -> SimConfig:
    base = SimConfig(
        builds=[fighter()], catalogue=CATALOGUE, dummy=DUMMY, iterations=40
    )
    return replace(base, **changes)


# --- The runner ----------------------------------------------------------------


def test_the_log_is_byte_identical_at_any_jobs() -> None:
    """Worker results are merged in iteration order, so neither the per-seed
    results nor the full event logs depend on how many processes ran them."""
    seeds = iteration_seeds(7, 24)
    alone = run(scenario(), seeds, jobs=1, log_level=LogLevel.FULL)
    pooled = run(scenario(), seeds, jobs=3, log_level=LogLevel.FULL)
    assert [i.result.seed for i in pooled] == list(seeds)
    assert pooled == alone
    assert (
        repr([i.log for i in pooled]).encode() == repr([i.log for i in alone]).encode()
    )


def test_an_iteration_depends_on_its_seed_alone() -> None:
    seeds = iteration_seeds(7, 6)
    forward = run(scenario(), seeds)
    backward = run(scenario(), seeds[::-1])
    assert [i.result for i in backward] == [i.result for i in forward][::-1]
    assert all(i.log is None for i in forward)


def test_a_run_takes_at_least_one_job() -> None:
    with pytest.raises(ValueError, match="at least one job"):
        run(scenario(), (1,), jobs=0)


def test_the_counts_are_per_round_and_the_party_s() -> None:
    """The Fighter takes one Command, its attack, every round, so a starved
    policy would show here as a zero rather than only as a low DPR."""
    for iteration in run(scenario(), iteration_seeds(3, 10)):
        result = iteration.result
        assert result.winner == PARTY
        assert result.damage >= DUMMY.hp
        assert result.actions_per_round == (1,) * result.rounds
        assert result.attacks_per_round == (1,) * result.rounds
        assert result.dpr == result.damage / result.rounds


def test_a_policy_that_never_attacks_shows_as_a_count() -> None:
    dodging = (("Fighter (GWM)", (Entry("dodge"),)),)
    (iteration,) = run(scenario(apl=dodging, max_rounds=3), (5,))
    assert iteration.result.attacks_per_round == (0, 0, 0)
    assert iteration.result.actions_per_round == (1, 1, 1)
    assert iteration.result.damage == 0
    assert iteration.result.winner is None


def test_a_dummy_dealt_nothing_by_its_routine_leaves_the_party_s_count_alone() -> None:
    """Only the party's attacks are counted: a dummy kiting out of reach
    makes none, and the Fighter's walk is a Command."""
    kiting = replace(DUMMY, movement_profile=MovementProfile.kites_at(60))
    (iteration,) = run(scenario(dummy=kiting, max_rounds=2), (5,))
    assert iteration.result.attacks_per_round[0] == 1


# --- The scenario's refusals ---------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"builds": ()}, "at least one build"),
        ({"builds": (fighter(), fighter())}, "two creatures"),
        ({"dummy": replace(DUMMY, name="Fighter (GWM)")}, "two creatures"),
        ({"apl": (("Wizard", (Entry("attack"),)),)}, "not a build"),
        ({"surprised": frozenset({"Wizard"})}, "no creature"),
    ],
)
def test_a_scenario_refuses_what_it_cannot_tell_apart(
    changes: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        scenario(**changes)


def test_a_surprised_dummy_is_named_by_its_name() -> None:
    surprised = scenario(surprised=frozenset({DUMMY.name}))
    assert run(surprised, (1,))[0].result.winner == PARTY


def test_an_override_attacking_with_a_weapon_not_carried_fails_at_load() -> None:
    entries = (Entry("attack", with_=(("weapon", "'Maul'"),)),)
    with pytest.raises(AplLoadError, match="'Maul'"):
        run(scenario(apl=(("Fighter (GWM)", entries),)), (1,))


def test_a_dummy_spec_survives_pickling() -> None:
    spec = DummySpec(
        saves={Ability.DEXTERITY: 3},
        resistances=frozenset({DamageType.FIRE}),
        threat_profile=ThreatProfile.NEAREST,
        movement_profile=MovementProfile.kites_at(30),
    )
    assert pickle.loads(pickle.dumps(spec)) == spec


# --- The API -------------------------------------------------------------------


def test_a_seed_list_reproduces_the_iterations_of_a_master_seed() -> None:
    by_master = run_sim(config(seed=11, iterations=8))
    seeds = [seed.seed for seed in by_master.per_seed]
    by_list = run_sim(config(seeds=seeds))
    assert seeds == list(iteration_seeds(11, 8))
    assert by_list.per_seed == by_master.per_seed
    assert by_master.provenance.seed == 11
    assert by_list.provenance.seed is None


def test_a_pair_of_policies_compares_seed_by_seed() -> None:
    seeds = list(iteration_seeds(2, 12))
    plain = run_sim(config(seeds=seeds))
    dodging = Apl((Entry("dodge"), Entry("attack")))
    overridden = run_sim(config(seeds=seeds, apl={"Fighter (GWM)": dodging}))
    assert [s.seed for s in overridden.per_seed] == seeds
    assert all(s.damage == 0 for s in overridden.per_seed)
    assert all(s.damage > 0 for s in plain.per_seed)
    (ours,) = overridden.provenance.builds
    assert ours.apl_hash == dodging.canonical_hash
    assert ours.build_hash == plain.provenance.builds[0].build_hash


def test_jobs_change_no_result() -> None:
    assert run_sim(config(jobs=2)) == run_sim(config(jobs=1))


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [({"iterations": 0}, "at least one iteration"), ({"seeds": []}, "one seed")],
)
def test_a_run_has_at_least_one_iteration(
    changes: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        run_sim(config(**changes))


def test_the_result_summarises_its_per_seed_results() -> None:
    result = run_sim(config())
    per_seed = result.per_seed
    assert result.iterations == len(per_seed) == 40
    assert result.dpr == summarise([s.dpr for s in per_seed])
    assert result.rounds == summarise([s.rounds for s in per_seed])
    assert result.win_rate == 1.0
    assert result.actions_per_round.mean == 1.0
    assert result.provenance.scenario["dummy"]["ac"] == 15
    assert result.provenance.content_version is None


def test_one_value_has_no_spread_and_none_has_no_statistic() -> None:
    stat = summarise([4.0])
    assert (stat.mean, stat.stdev, stat.ci_low, stat.ci_high) == (4.0, 0.0, 4.0, 4.0)
    with pytest.raises(ValueError):
        summarise([])


# --- The JSON and the text derived from it ------------------------------------


def test_the_json_round_trips_and_the_schema_is_the_checked_in_one() -> None:
    result = run_sim(config(iterations=3))
    assert SimResult.model_validate_json(render_json(result)) == result
    assert json.loads(SCHEMA.read_text()) == SimResult.model_json_schema()


def test_the_text_is_read_from_the_json() -> None:
    document = json.loads(render_json(run_sim(config(iterations=3))))
    document["dpr"]["mean"] = 123.456
    text = render_text(json.dumps(document))
    assert "123.46" in text
    assert "Fighter (GWM) vs Target Dummy" in text


def _serial_in_worker() -> int:
    return len(run(scenario(), (1, 2), jobs=1))


def _pooled_in_worker() -> str:
    try:
        run(scenario(), (1, 2), jobs=2)
    except RuntimeError as error:
        return str(error)
    return "started"


def test_a_worker_runs_serially_but_starts_no_pool() -> None:
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        assert pool.submit(_serial_in_worker).result() == 2
        assert "inside a worker process" in pool.submit(_pooled_in_worker).result()
