"""The public surface: `run_sim`, `SimConfig`, `SimResult` (DESIGN.md
§ Interfaces: library, CLI, and files)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dndsim import __version__
from dndsim.apl.apl import Apl
from dndsim.builds.loader import Catalogue
from dndsim.builds.schema import BuildSpec
from dndsim.creatures.dummy import DummySpec
from dndsim.engine.encounter import DEFAULT_MAX_ROUNDS
from dndsim.reporting.result import (
    BuildProvenance,
    Provenance,
    SeedResult,
    SimResult,
    aggregate,
)
from dndsim.sim.runner import iteration_seeds, run
from dndsim.sim.scenario import PARTY, Scenario, build_hash, load

__all__ = ["SeedResult", "SimConfig", "SimResult", "run_sim"]


@dataclass(frozen=True, slots=True)
class SimConfig:
    """What to simulate and how often.

    `builds` fight `dummy`'s dummies, their names checked against
    `catalogue`. The run is `iterations` iterations seeded from master `seed`,
    or, when `seeds` is given, one iteration per seed in that order, each
    seeded from it directly — the same list given twice pairs the two runs
    seed by seed. `apl` replaces a build's policy by build name. `surprised`
    names the creatures surprised at the start. `jobs` is how many worker
    processes run the iterations; it changes no result.
    """

    builds: Sequence[BuildSpec]
    catalogue: Catalogue
    dummy: DummySpec = DummySpec()
    iterations: int = 1000
    seed: int = 0
    seeds: Sequence[int] | None = None
    apl: Mapping[str, Apl] = field(default_factory=dict)
    surprised: frozenset[str] = frozenset()
    max_rounds: int = DEFAULT_MAX_ROUNDS
    jobs: int = 1


def run_sim(config: SimConfig) -> SimResult:
    """Run `config` and return its result, per seed and summarised.

    Raises `ValueError` for fewer than one iteration or an empty seed list,
    and what `Scenario` and the build loader raise for what they refuse."""
    scenario = Scenario(
        builds=tuple(config.builds),
        catalogue=config.catalogue,
        dummy=config.dummy,
        apl=tuple((name, apl.entries) for name, apl in config.apl.items()),
        surprised=frozenset(config.surprised),
        max_rounds=config.max_rounds,
    )
    if config.seeds is None:
        if config.iterations < 1:
            raise ValueError(
                f"a run has at least one iteration, not {config.iterations}"
            )
        seeds = iteration_seeds(config.seed, config.iterations)
    else:
        if not config.seeds:
            raise ValueError("a seed list has at least one seed")
        seeds = tuple(config.seeds)
    loaded = load(scenario)
    provenance = Provenance(
        engine_version=__version__,
        content_version=config.catalogue.version,
        builds=tuple(
            BuildProvenance(
                name=build.spec.name,
                build_hash=build_hash(build.spec),
                apl_hash=build.apl.canonical_hash,
            )
            for build in loaded.builds
        ),
        scenario=scenario.knobs(),
        seed=None if config.seeds is not None else config.seed,
    )
    iterations = run(scenario, seeds, jobs=config.jobs)
    return aggregate(
        provenance, [iteration.result for iteration in iterations], side=PARTY
    )
