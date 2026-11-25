"""The runner, `SimResult`, its renderers and the API (ROADMAP `w2g`;
`docs/decisions.md` § w2g).

Every fight is `scenario_fixtures`' level 5 Fighter against a target dummy.
"""

import json
from dataclasses import replace
from typing import Final

import pytest
from pydantic import TypeAdapter

from dndsim import __version__
from dndsim.api import SimConfig, run_sim, trace_sim
from dndsim.apl.apl import Apl, Entry
from dndsim.core.geometry import Cell
from dndsim.core.log import LogLevel
from dndsim.core.rng import mix
from dndsim.creatures.dummy import DummySpec
from dndsim.reporting import render_text as text_module
from dndsim.reporting.render_json import render_json
from dndsim.reporting.render_text import RENDERERS, render_text
from dndsim.reporting.result import SCHEMA_VERSION, SimResult
from dndsim.sim.runner import ENEMIES, ITERATION, PARTY, Scenario, run
from scenario_fixtures import ADJACENT_DUMMY, CATALOGUE, fighter, scenario

CONTENT: Final = "0123abc"
CLOSING: Final = DummySpec(ac=15, hp=40)
"""A dummy at (0, 0) that closes to melee; the party starts at (0, 6)."""


def config(**changes: object) -> SimConfig:
    base = SimConfig(
        Scenario((fighter(),), CLOSING, CATALOGUE), CONTENT, seed=3, iterations=24
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# --- Reproduction at any --jobs ------------------------------------------------


def test_the_event_log_is_byte_identical_at_any_jobs() -> None:
    one = trace_sim(config(jobs=1))
    three = trace_sim(config(jobs=3))
    assert repr(one) == repr(three)
    openings = [record for record in one if record[0] == ITERATION]
    assert openings == [(ITERATION, index, mix(3, index)) for index in range(24)]


def test_the_result_is_byte_identical_at_any_jobs() -> None:
    assert run_sim(config(jobs=1)).to_json() == run_sim(config(jobs=2)).to_json()


def test_records_come_back_in_the_order_the_seeds_were_given() -> None:
    seeds = (11, 5, 99, 0)
    forward = run(config().scenario, seeds, jobs=2).records
    backward = run(config().scenario, seeds[::-1], jobs=1).records
    assert [r.seed for r in forward] == list(seeds)
    assert forward == backward[::-1]


def test_an_iteration_reproduces_alone_from_its_seed() -> None:
    whole = run_sim(config())
    third = whole.iterations[2]
    alone = run_sim(config(iterations=None, seeds=(third.seed,)))
    assert alone.iterations == (third,)
    assert alone.provenance.seed is None


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"iterations": 0}, "at least one iteration"),
        ({"jobs": 0}, "at least one job"),
        ({"seed": -1}, "non-negative"),
        ({"seeds": ()}, "at least one seed"),
        ({"seeds": (1, 2)}, "24 iterations were asked of 2 seeds"),
    ],
)
def test_a_config_out_of_range_is_refused(
    changes: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        config(**changes)


# --- What a result carries ------------------------------------------------------


def test_the_result_carries_its_provenance() -> None:
    result = run_sim(config())
    assert result.schema_version == SCHEMA_VERSION
    assert result.provenance.seed == 3
    assert result.provenance.engine_version == __version__
    assert result.provenance.content_version == CONTENT
    assert result.provenance.build_hash == config().scenario.scenario_hash
    assert len(result.iterations) == 24


def test_the_scenario_hash_moves_with_anything_the_scenario_says() -> None:
    base = scenario()
    assert base.scenario_hash == scenario().scenario_hash
    for changed in (
        scenario(dummy=replace(ADJACENT_DUMMY, ac=16)),
        scenario(party_position=Cell(1, 1)),
        scenario(surprised=frozenset({PARTY})),
        scenario(max_rounds=5),
        scenario(builds=(fighter(level=3, feats=[]),)),
        scenario(apl={"Fighter (GWM)": Apl((Entry("dodge"),))}),
    ):
        assert changed.scenario_hash != base.scenario_hash


def test_the_aggregate_is_read_off_the_iterations() -> None:
    result = run_sim(config())
    rounds = sum(it.rounds for it in result.iterations)
    assert result.objective == tuple(it.dpr for it in result.iterations)
    assert result.dpr.mean == pytest.approx(sum(result.objective) / 24)
    assert result.dpr.ci_low < result.dpr.mean < result.dpr.ci_high
    assert (
        result.actions_per_round == sum(it.actions for it in result.iterations) / rounds
    )
    assert (
        result.attacks_per_round == sum(it.attacks for it in result.iterations) / rounds
    )
    assert result.win_rate == 1.0


def test_a_policy_that_starves_itself_shows_as_a_count() -> None:
    """Dodging every turn spends the Action and attacks nothing: one Action
    a round and no attacks, and the fight runs to the round cap."""
    dodging = Apl((Entry("dodge"),))
    starved = scenario(apl={"Fighter (GWM)": dodging}, max_rounds=4)
    result = run_sim(SimConfig(starved, CONTENT, iterations=3))
    assert [(it.rounds, it.winner) for it in result.iterations] == [(4, None)] * 3
    assert (result.actions_per_round, result.attacks_per_round) == (1.0, 0.0)
    assert result.dpr.mean == 0.0


def test_an_apl_override_survives_the_worker_processes() -> None:
    dodging = Apl((Entry("dodge"),))
    starved = scenario(apl={"Fighter (GWM)": dodging}, max_rounds=2)
    result = run_sim(SimConfig(starved, CONTENT, iterations=4, jobs=2))
    assert result.attacks_per_round == 0.0


def test_surprise_reaches_the_initiative_roll() -> None:
    """A surprised side rolls two d20s for its initiative: the trace of the
    same seed differs from the unsurprised one's."""
    plain = trace_sim(SimConfig(scenario(), CONTENT, seeds=(1,)))
    surprised = trace_sim(
        SimConfig(scenario(surprised=frozenset({PARTY})), CONTENT, seeds=(1,))
    )
    assert plain != surprised


# --- The JSON form and the renderers ---------------------------------------------


def test_the_json_form_reads_back_to_the_same_result() -> None:
    result = run_sim(config())
    assert SimResult.from_json(result.to_json()) == result


def test_the_json_form_is_described_by_its_schema() -> None:
    schema = SimResult.json_schema()
    document = json.loads(run_sim(config()).to_json())
    assert set(schema["required"]) == set(document)
    TypeAdapter(SimResult).validate_python(document)


def test_another_schema_version_is_refused() -> None:
    document = json.loads(run_sim(config(iterations=1)).to_json())
    document["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="schema version"):
        SimResult.from_json(json.dumps(document))


def test_the_text_is_rendered_from_the_json(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_sim(config(iterations=2))
    document = json.loads(render_json(result))
    document["win_rate"] = 0.25
    monkeypatch.setattr(text_module, "render_json", lambda _: json.dumps(document))
    assert "party wins  25.0%" in render_text(result)


def test_each_format_is_a_renderer() -> None:
    result = run_sim(config(iterations=2))
    assert RENDERERS["json"](result) == result.to_json()
    assert RENDERERS["text"](result).startswith("iterations  2  (seed 3)")


# --- The scenario, refused at construction ---------------------------------------


def test_a_scenario_is_refused_for_what_cannot_be_fought() -> None:
    with pytest.raises(ValueError, match="at least one build"):
        scenario(builds=())
    with pytest.raises(ValueError, match="two combatants are named"):
        scenario(builds=(fighter(), fighter()))
    with pytest.raises(ValueError, match="names 'Nobody'"):
        scenario(apl={"Nobody": Apl((Entry("dodge"),))})
    with pytest.raises(ValueError, match="names the sides"):
        scenario(surprised=frozenset({"heroes"}))
    with pytest.raises(ValueError, match="Target Dummy would stand on Fighter"):
        scenario(party_position=Cell(0, 0))


def test_the_sides_are_the_party_and_the_enemies() -> None:
    trace = trace_sim(
        SimConfig(scenario(), CONTENT, seeds=(2,)), level=LogLevel.SUMMARY
    )
    assert trace[0] == (ITERATION, 0, 2)
    assert (PARTY, ENEMIES) == ("party", "enemies")
