"""The public surface: `run_sim`, `trace_sim`, `SimConfig` and `SimResult`
(DESIGN.md § Interfaces; `docs/decisions.md` § w2g).

A process calling `run_sim` with `jobs > 1` must do so from under an
`if __name__ == "__main__":` guard: its workers are spawned, and a spawned
worker re-runs the caller's main module.
"""

from dataclasses import dataclass
from typing import Final

from dndsim import __version__
from dndsim.core.log import LogLevel, Record
from dndsim.core.rng import mix
from dndsim.reporting.result import IterationResult, Provenance, SimResult
from dndsim.sim.runner import Scenario, run

__all__ = [
    "DEFAULT_ITERATIONS",
    "IterationResult",
    "Scenario",
    "SimConfig",
    "SimResult",
    "run_sim",
    "trace_sim",
]

DEFAULT_ITERATIONS: Final = 1000


@dataclass(frozen=True, slots=True)
class SimConfig:
    """A run: `scenario` fought once per seed, across `jobs` processes.

    The seeds are `seeds` when given, and otherwise `iterations` of them
    derived from `seed` by `mix()`; `content_version` is the upstream commit
    the scenario's catalogue was imported at. Raises `ValueError` for fewer
    than one iteration or job, a negative seed, or both `seeds` and an
    `iterations` that disagrees with it.
    """

    scenario: Scenario
    content_version: str
    seed: int = 0
    iterations: int | None = None
    seeds: tuple[int, ...] | None = None
    jobs: int = 1

    def __post_init__(self) -> None:
        if self.seeds is not None:
            if not self.seeds:
                raise ValueError("a seed list has at least one seed")
            if self.iterations is not None and self.iterations != len(self.seeds):
                raise ValueError(
                    f"{self.iterations} iterations were asked of "
                    f"{len(self.seeds)} seeds; give one or the other"
                )
        elif self.iterations is not None and self.iterations < 1:
            raise ValueError(f"a run has at least one iteration, not {self.iterations}")
        if self.jobs < 1:
            raise ValueError(f"a run has at least one job, not {self.jobs}")
        if self.seed < 0 or any(seed < 0 for seed in self.seeds or ()):
            raise ValueError("a seed is non-negative")

    @property
    def run_seeds(self) -> tuple[int, ...]:
        """Each iteration's seed, in iteration order."""
        if self.seeds is not None:
            return self.seeds
        count = DEFAULT_ITERATIONS if self.iterations is None else self.iterations
        return tuple(mix(self.seed, index) for index in range(count))


def run_sim(config: SimConfig) -> SimResult:
    """Run `config` and report it; the same config gives the same result at
    any `jobs`."""
    outcome = run(config.scenario, config.run_seeds, jobs=config.jobs)
    provenance = Provenance(
        seed=None if config.seeds is not None else config.seed,
        build_hash=config.scenario.scenario_hash,
        engine_version=__version__,
        content_version=config.content_version,
    )
    return SimResult.collect(provenance, outcome.records)


def trace_sim(
    config: SimConfig, *, level: LogLevel = LogLevel.FULL
) -> tuple[Record, ...]:
    """Every record of every iteration of `config`, each iteration opened by
    an `(ITERATION, index, seed)` record, in iteration order at any `jobs`."""
    outcome = run(config.scenario, config.run_seeds, jobs=config.jobs, trace=level)
    assert outcome.trace is not None
    return outcome.trace
