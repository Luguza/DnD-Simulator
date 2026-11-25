"""A scenario — the builds on one side and the dummies on the other — and the
fight it assembles into on a given generator.

A `Scenario` is plain data, so the runner can hand it to a worker process;
`load()` compiles it there once, and `fight()` runs one iteration of it
(`docs/decisions.md` § w2g).
"""

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, NamedTuple

from dndsim.apl.apl import Apl, Entry
from dndsim.apl.dummy import arm_dummy
from dndsim.builds.loader import Build, Catalogue, arm_character, load_build
from dndsim.builds.schema import BuildSpec
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import DummySpec, build_dummies
from dndsim.engine.attack import ATTACK
from dndsim.engine.damage import DAMAGE
from dndsim.engine.encounter import DEFAULT_MAX_ROUNDS, ROUND_START, Encounter
from dndsim.engine.turn import Policy
from dndsim.engine.world import World

__all__ = [
    "ENEMIES",
    "PARTY",
    "Loaded",
    "Outcome",
    "Scenario",
    "build_hash",
    "fight",
    "load",
]

PARTY: Final = "party"
"""The side the builds fight on."""

ENEMIES: Final = "enemies"
"""The side the dummies fight on."""


@dataclass(frozen=True, slots=True)
class Scenario:
    """One fight, as data.

    `builds` fight on `PARTY`, the `i`th standing at `dummy.position` plus
    `(-1, i)`; `dummy`'s dummies fight on `ENEMIES`. `apl` replaces a
    creature's policy by its name, compiled against the default schema.
    `surprised` names the creatures surprised at the start (XPHB "Surprise").

    Raises `ValueError` for no builds, two creatures of one name, or a name in
    `apl` or `surprised` that no creature in the scenario has.
    """

    builds: tuple[BuildSpec, ...]
    catalogue: Catalogue
    dummy: DummySpec = DummySpec()
    apl: tuple[tuple[str, tuple[Entry, ...]], ...] = ()
    surprised: frozenset[str] = frozenset()
    max_rounds: int = DEFAULT_MAX_ROUNDS

    def __post_init__(self) -> None:
        if not self.builds:
            raise ValueError("a scenario has at least one build")
        party = [spec.name for spec in self.builds]
        enemies = [dummy.name for dummy in build_dummies(self.dummy)]
        names = party + enemies
        for name in names:
            if names.count(name) > 1:
                raise ValueError(
                    f"two creatures in the scenario are named {name!r}; the "
                    "per-seed counts and the overrides tell creatures apart by name"
                )
        overridden = [name for name, _ in self.apl]
        for name in overridden:
            if name not in names:
                raise ValueError(
                    f"an APL override names {name!r}, but no creature in the "
                    f"scenario has that name; they are {names}"
                )
            if overridden.count(name) > 1:
                raise ValueError(f"{name!r} has two APL overrides")
        for name in self.surprised:
            if name not in names:
                raise ValueError(
                    f"{name!r} is surprised, but no creature in the scenario "
                    f"has that name; they are {names}"
                )

    def knobs(self) -> dict[str, Any]:
        """The scenario's knobs other than the builds, as JSON values."""
        return {
            "dummy": _plain(self.dummy),
            "dummy_apl": {
                name: Apl(entries).canonical_hash
                for name, entries in self.apl
                if name not in {spec.name for spec in self.builds}
            },
            "surprised": sorted(self.surprised),
            "max_rounds": self.max_rounds,
        }


def build_hash(spec: BuildSpec) -> str:
    """SHA-256 of `spec` in a canonical JSON form."""
    canonical = json.dumps(
        spec.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Loaded(NamedTuple):
    """A scenario with its builds loaded, their policies compiled, and each
    dummy's override compiled by name."""

    scenario: Scenario
    builds: tuple[Build, ...]
    dummy_apls: Mapping[str, Apl]


def load(scenario: Scenario) -> Loaded:
    """Load every build of `scenario` and put each override in place. Raises
    what `load_build()` and `Build.with_apl()` raise."""
    overrides = dict(scenario.apl)
    builds = []
    for spec in scenario.builds:
        build = load_build(spec, scenario.catalogue)
        entries = overrides.pop(spec.name, None)
        builds.append(build if entries is None else build.with_apl(Apl(entries)))
    dummies = {name: Apl(entries) for name, entries in overrides.items()}
    return Loaded(scenario, tuple(builds), dummies)


class Outcome(NamedTuple):
    """One fight's result, before its seed is attached."""

    rounds: int
    winner: str | None
    damage: int
    actions_per_round: tuple[int, ...]
    attacks_per_round: tuple[int, ...]


def fight(loaded: Loaded, *, rng: Rng, log: EventLog) -> Outcome:
    """Assemble `loaded` into a fresh encounter, run it on `rng`, writing to
    `log`, and count what the party did from the log and the turns played."""
    scenario = loaded.scenario
    world = World(log=log)
    origin = scenario.dummy.position
    party = [
        build.character(Cell(origin.x - 1, origin.y + index))
        for index, build in enumerate(loaded.builds)
    ]
    dummies = build_dummies(scenario.dummy)
    for character in party:
        world.seat(character, PARTY)
    for dummy in dummies:
        world.seat(dummy, ENEMIES)
    policies: dict[Creature, Policy] = {}
    for character, build in zip(party, loaded.builds, strict=True):
        policies[character] = arm_character(character, build.apl, world, rng=rng)
    for dummy in dummies:
        policies[dummy] = arm_dummy(
            dummy, world, rng=rng, apl=loaded.dummy_apls.get(dummy.name)
        )
    surprised = [creature for creature in world if creature.name in scenario.surprised]
    encounter = Encounter(
        world,
        policy=policies,
        rng=rng,
        max_rounds=scenario.max_rounds,
        surprised=surprised,
    )
    result = encounter.run()

    ours = {character.name for character in party}
    actions = [0] * result.rounds
    for turn in encounter.turns:
        if turn.creature.name in ours:
            actions[turn.round - 1] += turn.commands
    attacks = [0] * result.rounds
    damage = 0
    number = 0
    for record in log:
        kind = record[0]
        if kind == ROUND_START:
            number = record[1]  # type: ignore[assignment]
        # A vetoed attack is logged with no outcome and resolved nothing.
        elif kind == ATTACK and record[1] in ours and record[4] is not None:
            attacks[number - 1] += 1
            if record[2] not in ours:
                damage += record[5]  # type: ignore[operator]
        # An attack hands `resolve_damage()` no log, so the two never count
        # one instance twice (`docs/decisions.md` § w2g).
        elif kind == DAMAGE and record[1] in ours and record[2] not in ours:
            damage += record[3]  # type: ignore[operator]
    return Outcome(result.rounds, result.winner, damage, tuple(actions), tuple(attacks))


def _plain(value: object) -> Any:
    """`value` as a JSON value whose text does not depend on hash order."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Cell):
        return [value.x, value.y]
    if isinstance(value, Dice):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            spec.name: _plain(getattr(value, spec.name))
            for spec in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(_plain(key)): _plain(item) for key, item in value.items()}
    if isinstance(value, frozenset | set):
        return sorted(_plain(item) for item in value)
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"no JSON form for a scenario knob's {value!r}")
