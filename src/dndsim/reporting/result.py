"""`SimResult` — the machine contract every renderer reads (DESIGN.md
§ Interfaces: library, CLI, and files).

`SimResult.model_json_schema()` is the schema; `simresult.schema.json` beside
this file is its checked-in copy, and a test keeps the two equal.
"""

from collections.abc import Sequence
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from dndsim.reporting.stats import Stat, summarise

__all__ = [
    "SCHEMA_VERSION",
    "BuildProvenance",
    "Provenance",
    "SeedResult",
    "SimResult",
    "aggregate",
]

SCHEMA_VERSION: Final = 1
"""Raised on any change to the shape of a `SimResult`."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SeedResult(_Model):
    """One iteration: the seed its generator was started from, the rounds
    begun, the side left standing (`None` for none or several), the damage the
    party dealt its enemies, that damage per round, and the Commands the party
    carried out and the attacks it resolved in each round."""

    seed: int
    rounds: int
    winner: str | None
    damage: int
    dpr: float
    actions_per_round: tuple[int, ...]
    attacks_per_round: tuple[int, ...]


class BuildProvenance(_Model):
    """A build by name, the SHA-256 of its spec, and of the policy it ran."""

    name: str
    build_hash: str
    apl_hash: str


class Provenance(_Model):
    """What reproduces the run: the engine and content versions, each build,
    the scenario's knobs, and the master seed (`None` when the seeds were
    given as a list, which `SimResult.per_seed` holds)."""

    engine_version: str
    content_version: str | None
    builds: tuple[BuildProvenance, ...]
    scenario: dict[str, Any]
    seed: int | None


class SimResult(_Model):
    """A run: its provenance, the per-seed results in iteration order, and
    each per-seed quantity summarised."""

    schema_version: int
    provenance: Provenance
    iterations: int
    win_rate: float
    rounds: Stat
    damage: Stat
    dpr: Stat
    actions_per_round: Stat
    attacks_per_round: Stat
    per_seed: tuple[SeedResult, ...]


def aggregate(
    provenance: Provenance, per_seed: Sequence[SeedResult], *, side: str
) -> SimResult:
    """The `SimResult` of `per_seed`, `win_rate` counting the iterations
    `side` won. Raises `ValueError` for no iterations."""
    if not per_seed:
        raise ValueError("a run has at least one iteration")
    n = len(per_seed)
    return SimResult(
        schema_version=SCHEMA_VERSION,
        provenance=provenance,
        iterations=n,
        win_rate=sum(seed.winner == side for seed in per_seed) / n,
        rounds=summarise([seed.rounds for seed in per_seed]),
        damage=summarise([seed.damage for seed in per_seed]),
        dpr=summarise([seed.dpr for seed in per_seed]),
        actions_per_round=summarise(
            [_per_round(seed.actions_per_round) for seed in per_seed]
        ),
        attacks_per_round=summarise(
            [_per_round(seed.attacks_per_round) for seed in per_seed]
        ),
        per_seed=tuple(per_seed),
    )


def _per_round(counts: tuple[int, ...]) -> float:
    return sum(counts) / len(counts) if counts else 0.0
