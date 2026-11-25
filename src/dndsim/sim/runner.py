"""The Monte Carlo runner: one fight per seed, across worker processes.

Each iteration's generator is seeded from its own seed, never the worker's, and
the workers' results are taken back in iteration order, never completion
order, so a run's records do not depend on `jobs` (DESIGN.md § Performance,
§ What is a file, and what is not). The pool uses `spawn`, which re-imports the
caller's `__main__` in every worker: a caller starting a run from a script
keeps the call behind `if __name__ == "__main__":`, and this module starts
nothing when imported (`docs/decisions.md` § w2g).
"""

import multiprocessing
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import NamedTuple

from dndsim.core.log import EventLog, LogLevel, Record
from dndsim.core.rng import MersenneRng, mix
from dndsim.reporting.result import SeedResult
from dndsim.sim.scenario import Loaded, Scenario, fight, load

__all__ = ["Iteration", "iteration_seeds", "run"]


class Iteration(NamedTuple):
    """One iteration's result, and its event log when one was asked for."""

    result: SeedResult
    log: tuple[Record, ...] | None


def iteration_seeds(seed: int, iterations: int) -> tuple[int, ...]:
    """The seed of each of `iterations` iterations under master `seed`."""
    return tuple(mix(seed, index) for index in range(iterations))


def run(
    scenario: Scenario,
    seeds: Sequence[int],
    *,
    jobs: int = 1,
    log_level: LogLevel | None = None,
) -> tuple[Iteration, ...]:
    """Fight `scenario` once per seed, in `jobs` processes, and return the
    iterations in the order of `seeds`, each with its log at `log_level`
    (`None` keeps none).

    Raises `ValueError` for `jobs` below 1, and `RuntimeError` when a run
    that would start a pool is called from inside a worker process."""
    if jobs < 1:
        raise ValueError(f"a run takes at least one job, not {jobs}")
    if jobs == 1 or len(seeds) <= 1:
        loaded = load(scenario)
        return tuple(_iterate(loaded, seed, log_level) for seed in seeds)
    if multiprocessing.parent_process() is not None:
        raise RuntimeError(
            f"a run of {jobs} jobs was started inside a worker process; a "
            "script that runs a simulation keeps the call behind "
            '`if __name__ == "__main__":`, and a worker runs with jobs=1'
        )
    chunk = max(1, len(seeds) // (jobs * 4))
    with ProcessPoolExecutor(
        max_workers=jobs,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_start_worker,
        initargs=(scenario, log_level),
    ) as pool:
        # `map` yields in the order of its input: the merge is by iteration.
        return tuple(pool.map(_in_worker, seeds, chunksize=chunk))


def _iterate(loaded: Loaded, seed: int, log_level: LogLevel | None) -> Iteration:
    log = EventLog(LogLevel.SUMMARY if log_level is None else log_level)
    outcome = fight(loaded, rng=MersenneRng(seed), log=log)
    rounds = outcome.rounds
    result = SeedResult(
        seed=seed,
        rounds=rounds,
        winner=outcome.winner,
        damage=outcome.damage,
        dpr=outcome.damage / rounds if rounds else 0.0,
        actions_per_round=outcome.actions_per_round,
        attacks_per_round=outcome.attacks_per_round,
    )
    return Iteration(result, None if log_level is None else log.records)


_worker: tuple[Loaded, LogLevel | None] | None = None
"""This worker's loaded scenario and log level, set once by `_start_worker`."""


def _start_worker(scenario: Scenario, log_level: LogLevel | None) -> None:
    global _worker
    _worker = (load(scenario), log_level)


def _in_worker(seed: int) -> Iteration:
    assert _worker is not None, "the pool's initializer loads the scenario"
    loaded, log_level = _worker
    return _iterate(loaded, seed, log_level)
