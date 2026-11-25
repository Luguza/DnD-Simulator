"""The Monte Carlo runner: a `Scenario` assembled into a fight per seed, the
fights spread over worker processes, and their records merged back in
iteration order (`docs/decisions.md` § w2g).
"""

import hashlib
import json
import multiprocessing
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, fields
from typing import Any, Final

from dndsim.apl.apl import Apl
from dndsim.apl.dummy import arm_dummy
from dndsim.builds.loader import Build, Catalogue, arm_character, load_build
from dndsim.builds.schema import BuildSpec
from dndsim.core.geometry import Cell, footprint
from dndsim.core.log import EventLog, LogLevel, Record
from dndsim.core.rng import MersenneRng, Rng
from dndsim.creatures.character import Character
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import DummySpec, build_dummies
from dndsim.engine.attack import ATTACK
from dndsim.engine.damage import DAMAGE
from dndsim.engine.encounter import DEFAULT_MAX_ROUNDS, Encounter
from dndsim.engine.turn import Policy
from dndsim.engine.world import World
from dndsim.reporting.result import PARTY, IterationResult
from dndsim.rules.constants import SIZE_WIDTH_CELLS
from dndsim.rules.enums import ActionCost, Size

__all__ = [
    "ENEMIES",
    "ITERATION",
    "PARTY",
    "Run",
    "Scenario",
    "run",
    "run_iteration",
]

ENEMIES: Final = "enemies"
"""The side the scenario's dummies are seated on."""

ITERATION: Final = "iteration"
"""`(ITERATION, index, seed)`, opening each iteration's records in a trace."""

_CHUNKS_PER_JOB: Final = 4


@dataclass(frozen=True, slots=True)
class Scenario:
    """One fight, as specified: the party's builds against `dummy`.

    The party stands in a row along +x from `party_position`, one footprint
    apart; the dummies stand where `dummy` places them. `apl` replaces the
    policy of the party member it names. `surprised` names the sides surprised
    by the fight starting, `PARTY` or `ENEMIES`.

    Raises `ValueError` for an empty party, two combatants of one name, an
    override naming no party member, an unknown side, or a combatant standing on
    another.
    """

    builds: tuple[BuildSpec, ...]
    dummy: DummySpec
    catalogue: Catalogue
    party_position: Cell = Cell(0, 6)
    apl: Mapping[str, Apl] = field(default_factory=dict)
    surprised: frozenset[str] = frozenset()
    max_rounds: int = DEFAULT_MAX_ROUNDS

    def __post_init__(self) -> None:
        if not self.builds:
            raise ValueError("a scenario has at least one build in its party")
        unknown = self.surprised - {PARTY, ENEMIES}
        if unknown:
            raise ValueError(
                f"surprised names the sides {PARTY!r} and {ENEMIES!r}, "
                f"not {sorted(unknown)}"
            )
        party = [spec.name for spec in self.builds]
        enemies = [dummy.name for dummy in build_dummies(self.dummy)]
        names = party + enemies
        for name in names:
            if names.count(name) > 1:
                raise ValueError(
                    f"two combatants are named {name!r}; a trace and a result "
                    "name each one, so give every build a name of its own"
                )
        for name in self.apl:
            if name not in party:
                raise ValueError(
                    f"an APL override names {name!r}, who is not in the party; "
                    f"its members are {party}"
                )
        builds = [load_build(spec, self.catalogue) for spec in self.builds]
        taken: dict[Cell, str] = {}
        for name, anchor, size in (
            *(
                (spec.name, anchor, size)
                for spec, (anchor, size) in zip(
                    self.builds, _placements(self, builds), strict=True
                )
            ),
            *((d.name, d.position, d.size) for d in build_dummies(self.dummy)),
        ):
            for cell in footprint(anchor, size):
                if cell in taken:
                    raise ValueError(
                        f"{name} would stand on {taken[cell]} at {cell}; "
                        "move party_position or the dummy's position"
                    )
                taken[cell] = name

    @property
    def scenario_hash(self) -> str:
        """SHA-256 of everything this scenario specifies, in a canonical JSON
        form; the catalogue is identified by the content version instead."""
        document = {
            "builds": [spec.model_dump(by_alias=True) for spec in self.builds],
            "dummy": _dummy_document(self.dummy),
            "party_position": [self.party_position.x, self.party_position.y],
            "apl": {name: apl.canonical_hash for name, apl in self.apl.items()},
            "surprised": sorted(self.surprised),
            "max_rounds": self.max_rounds,
        }
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def __reduce__(self) -> tuple[Callable[..., "Scenario"], tuple[Any, ...]]:
        # A compiled APL and the dummy's read-only saves do not pickle; a
        # worker process rebuilds both from their written forms.
        return (
            _restore,
            (
                self.builds,
                {f.name: getattr(self.dummy, f.name) for f in fields(DummySpec)}
                | {"saves": dict(self.dummy.saves)},
                self.catalogue,
                self.party_position,
                {name: apl.to_yaml() for name, apl in self.apl.items()},
                self.surprised,
                self.max_rounds,
            ),
        )


def _restore(
    builds: tuple[BuildSpec, ...],
    dummy: dict[str, Any],
    catalogue: Catalogue,
    party_position: Cell,
    apl: dict[str, str],
    surprised: frozenset[str],
    max_rounds: int,
) -> Scenario:
    return Scenario(
        builds,
        DummySpec(**dummy),
        catalogue,
        party_position,
        {name: Apl.from_yaml(text) for name, text in apl.items()},
        surprised,
        max_rounds,
    )


def _placements(scenario: Scenario, builds: Sequence[Build]) -> list[tuple[Cell, Size]]:
    """Each member's anchor and size: a row along +x from `party_position`,
    one footprint apart."""
    placed: list[tuple[Cell, Size]] = []
    x, y = scenario.party_position.x, scenario.party_position.y
    for build in builds:
        size = build.character(Cell(x, y)).size
        placed.append((Cell(x, y), size))
        x += SIZE_WIDTH_CELLS[size]
    return placed


def _dummy_document(dummy: DummySpec) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for spec in fields(DummySpec):
        value = getattr(dummy, spec.name)
        if spec.name == "saves":
            value = {ability.name: bonus for ability, bonus in value.items()}
        elif spec.name == "damage_per_round":
            value = [repr(attack) for attack in value]
        elif isinstance(value, frozenset):
            value = sorted(member.value for member in value)
        elif isinstance(value, Cell):
            value = [value.x, value.y]
        elif not isinstance(value, int | str):
            value = str(getattr(value, "value", value))
        document[spec.name] = value
    return document


@dataclass(frozen=True, slots=True)
class Run:
    """What `run()` returns: one record per seed, in the order given, and the
    trace when one was asked for."""

    records: tuple[IterationResult, ...]
    trace: tuple[Record, ...] | None


class _Counted:
    """A policy that counts the Action-cost Commands its creature chooses."""

    __slots__ = ("actions", "inner")

    def __init__(self, inner: Policy) -> None:
        self.inner = inner
        self.actions = 0

    def choose(self, actor: Creature, offers: Sequence[Any]) -> Any:
        choice = self.inner.choose(actor, offers)
        if choice is not None and choice.COST is ActionCost.ACTION:
            self.actions += 1
        return choice


def run_iteration(
    scenario: Scenario,
    builds: Sequence[Build],
    rng: Rng,
    *,
    seed: int = 0,
    log_level: LogLevel = LogLevel.SUMMARY,
) -> tuple[IterationResult, EventLog]:
    """Assemble `scenario` from its loaded `builds` and fight it on `rng`.

    Returns the record, stamped with `seed`, and the fight's event log.
    """
    log = EventLog(log_level)
    world = World(log=log)
    party: list[Character] = []
    for build, (cell, _) in zip(builds, _placements(scenario, builds), strict=True):
        character = build.character(cell)
        world.seat(character, PARTY)
        party.append(character)
    dummies = build_dummies(scenario.dummy)
    for dummy in dummies:
        world.seat(dummy, ENEMIES)

    counted: dict[Creature, _Counted] = {}
    policies: dict[Creature, Policy] = {}
    overrides = scenario.apl
    for character, build in zip(party, builds, strict=True):
        apl = overrides.get(character.name, build.apl)
        counted[character] = _Counted(arm_character(character, apl, world, rng=rng))
        policies[character] = counted[character]
    for dummy in dummies:
        policies[dummy] = arm_dummy(dummy, world, rng=rng)

    surprised = [
        creature for creature in world if world.side_of(creature) in scenario.surprised
    ]
    result = Encounter(
        world,
        rng=rng,
        policies=policies,
        surprised=surprised,
        max_rounds=scenario.max_rounds,
    ).run()

    names = {character.name for character in party}
    damage = attacks = 0
    # An attack's damage is on its `ATTACK` record and never on a `DAMAGE`
    # one (`docs/decisions.md` § w2c), so the two sums do not overlap.
    for written in log:
        kind = written[0]
        if written[1] not in names:
            continue
        if kind == ATTACK:
            attacks += 1
            damage += written[5]  # type: ignore[operator]
        elif kind == DAMAGE:
            damage += written[3]  # type: ignore[operator]
    record = IterationResult(
        seed=seed,
        rounds=result.rounds,
        winner=result.winner,
        damage=damage,
        actions=sum(policy.actions for policy in counted.values()),
        attacks=attacks,
    )
    return record, log


def _chunk(
    scenario: Scenario,
    start: int,
    seeds: Sequence[int],
    trace: LogLevel | None,
) -> tuple[list[IterationResult], list[Record] | None]:
    """Run `seeds`, the iterations from index `start`, in this process."""
    builds = [load_build(spec, scenario.catalogue) for spec in scenario.builds]
    records: list[IterationResult] = []
    traced: list[Record] | None = None if trace is None else []
    for offset, seed in enumerate(seeds):
        record, log = run_iteration(
            scenario,
            builds,
            MersenneRng(seed),
            seed=seed,
            log_level=LogLevel.SUMMARY if trace is None else trace,
        )
        records.append(record)
        if traced is not None:
            traced.append((ITERATION, start + offset, seed))
            traced.extend(log)
    return records, traced


def run(
    scenario: Scenario,
    seeds: Iterable[int],
    *,
    jobs: int = 1,
    trace: LogLevel | None = None,
) -> Run:
    """Fight `scenario` once per seed, each on `MersenneRng(seed)`, across
    `jobs` worker processes; the records and the trace come back in the
    order of `seeds` whatever `jobs` is.

    Raises `ValueError` for no seeds, a negative seed, or fewer than one job.
    """
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError("a run has at least one seed")
    if any(seed < 0 for seed in seeds):
        raise ValueError("a seed is non-negative")
    if jobs < 1:
        raise ValueError(f"a run has at least one job, not {jobs}")
    if jobs == 1:
        records, traced = _chunk(scenario, 0, seeds, trace)
        return Run(tuple(records), None if traced is None else tuple(traced))

    count = min(len(seeds), jobs * _CHUNKS_PER_JOB)
    bounds = [len(seeds) * i // count for i in range(count + 1)]
    starts = bounds[:-1]
    pieces = [seeds[bounds[i] : bounds[i + 1]] for i in range(count)]
    # Spawned, never forked, and only from inside this call: a worker
    # re-imports this module, which therefore starts nothing at import.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
        # `map` yields in submission order, never completion order.
        done = list(
            pool.map(
                _chunk,
                [scenario] * count,
                starts,
                pieces,
                [trace] * count,
            )
        )
    records = [record for chunk, _ in done for record in chunk]
    if trace is None:
        return Run(tuple(records), None)
    return Run(
        tuple(records),
        tuple(record for _, traced in done for record in traced or ()),
    )
