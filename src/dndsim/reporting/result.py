"""`SimResult`: what a run reports, as a frozen value with a JSON form and a
JSON Schema (DESIGN.md § Interfaces; `docs/decisions.md` § w2g)."""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

from pydantic import TypeAdapter

from dndsim.reporting.stats import Estimate, estimate

__all__ = ["SCHEMA_VERSION", "IterationResult", "Provenance", "SimResult"]

SCHEMA_VERSION: Final = 1
"""The version of `SimResult`'s JSON form; it changes with any field."""

PARTY: Final = "party"
"""The side the scenario's builds are seated on."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """What reproduces a run: the master seed (`None` when the seeds were
    listed), the hash of the scenario, and the engine and content versions."""

    seed: int | None
    build_hash: str
    engine_version: str
    content_version: str


@dataclass(frozen=True, slots=True)
class IterationResult:
    """One fight: the seed it ran on, the rounds begun, the side left standing
    (`None` for none or several), and what the party did — the damage it
    dealt, the Action-cost Commands it took, and the attacks it resolved."""

    seed: int
    rounds: int
    winner: str | None
    damage: int
    actions: int
    attacks: int

    @property
    def dpr(self) -> float:
        """The party's damage per round begun; 0 for a fight of no rounds."""
        return self.damage / self.rounds if self.rounds else 0.0


@dataclass(frozen=True, slots=True)
class SimResult:
    """A run's per-seed results, in the order of its seeds, and their
    aggregate. `actions_per_round` and `attacks_per_round` are the party's,
    over every round of every iteration."""

    schema_version: int
    provenance: Provenance
    iterations: tuple[IterationResult, ...]
    dpr: Estimate
    rounds: Estimate
    damage: Estimate
    win_rate: float
    actions_per_round: float
    attacks_per_round: float

    @classmethod
    def collect(
        cls, provenance: Provenance, iterations: Sequence[IterationResult]
    ) -> "SimResult":
        """Aggregate `iterations`. Raises `ValueError` for none."""
        if not iterations:
            raise ValueError("a result has at least one iteration")
        rounds = sum(it.rounds for it in iterations)
        return cls(
            schema_version=SCHEMA_VERSION,
            provenance=provenance,
            iterations=tuple(iterations),
            dpr=estimate([it.dpr for it in iterations]),
            rounds=estimate([it.rounds for it in iterations]),
            damage=estimate([it.damage for it in iterations]),
            win_rate=sum(it.winner == PARTY for it in iterations) / len(iterations),
            actions_per_round=(
                sum(it.actions for it in iterations) / rounds if rounds else 0.0
            ),
            attacks_per_round=(
                sum(it.attacks for it in iterations) / rounds if rounds else 0.0
            ),
        )

    @property
    def objective(self) -> tuple[float, ...]:
        """The per-seed objective vector: each iteration's party DPR, in seed
        order."""
        return tuple(it.dpr for it in self.iterations)

    def to_document(self) -> dict[str, Any]:
        """The JSON form as plain data."""
        return asdict(self)

    def to_json(self) -> str:
        """The JSON form, which `json_schema()` describes."""
        return json.dumps(self.to_document(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "SimResult":
        """Read `to_json()`'s form back. Raises `pydantic.ValidationError` for
        a document the schema does not describe, and `ValueError` for another
        schema version."""
        result = _ADAPTER.validate_json(text)
        if result.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"a SimResult of schema version {result.schema_version} is read "
                f"by another engine; this one reads {SCHEMA_VERSION}"
            )
        return result

    @staticmethod
    def json_schema() -> dict[str, Any]:
        """The JSON Schema of `to_json()`'s form."""
        return _ADAPTER.json_schema()


_ADAPTER: Final = TypeAdapter(SimResult)
