"""Loading a build file into a `Character` and its policy, and arming the two
together in a world.

Every name is checked against the imported content — a `Catalogue`, and the
skills and ability names — and an unknown one fails with suggestions
(DESIGN.md § Failing loudly). `docs/decisions.md` § w2f.
"""

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import ValidationError

from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.apl.apl import BEST_SCORED, Apl, Entry
from dndsim.apl.expr import Compiled, compile_condition
from dndsim.apl.schema import NamespaceSchema
from dndsim.builds.schema import BuildSpec, PartySpec
from dndsim.content.backgrounds import Background, backgrounds
from dndsim.content.classes import SKILLS, CharacterClass, classes
from dndsim.content.feats import Feat, feats
from dndsim.content.loader import Dataset
from dndsim.content.species import Species, species
from dndsim.content.weapons import armour, weapons
from dndsim.core.geometry import Cell
from dndsim.core.rng import Rng
from dndsim.creatures.character import BuildError, Character, CharacterBuilder
from dndsim.creatures.stats import Armour, Weapon
from dndsim.engine.world import World
from dndsim.rules.enums import Ability, Size

__all__ = [
    "DEFAULT_APL",
    "Build",
    "Catalogue",
    "Party",
    "arm_character",
    "load_build",
    "load_party",
    "parse_build",
]

DEFAULT_APL = Apl((Entry(BEST_SCORED),))
"""The policy of a build with no `apl:` (DESIGN.md § `best_scored`)."""

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Catalogue:
    """The imported content a build is checked against, each table by the
    name the XPHB prints."""

    classes: Mapping[str, CharacterClass]
    species: Mapping[str, Species]
    backgrounds: Mapping[str, Background]
    feats: Mapping[str, Feat]
    weapons: Mapping[str, Weapon]
    armour: Mapping[str, Armour]

    @classmethod
    def from_dataset(cls, data: Dataset) -> "Catalogue":
        """Every table, imported from `data`."""
        return cls(
            classes=classes(data),
            species=species(data),
            backgrounds=backgrounds(data),
            feats=feats(data),
            weapons=weapons(data),
            armour=armour(data),
        )


@dataclass(frozen=True, slots=True)
class Build:
    """A loaded build: its spec, its compiled policy, and the builder its
    characters come from."""

    spec: BuildSpec
    apl: Apl
    _builder: CharacterBuilder

    def character(self, position: Cell) -> Character:
        """A fresh character of this build at `position`."""
        return self._builder.build(position)


@dataclass(frozen=True, slots=True)
class Party:
    """A loaded party: its members' builds in the order written, and the
    compiled retreat condition, `None` for never."""

    members: tuple[Build, ...]
    retreat_when: Compiled | None


def _lookup(table: Mapping[str, _T] | Iterable[str], name: str, what: str) -> Any:
    names = list(table)
    if name in names:
        return table[name] if isinstance(table, Mapping) else name
    close = difflib.get_close_matches(name, names, n=3)
    hint = f"; did you mean {' or '.join(map(repr, close))}?" if close else ""
    raise BuildError(f"unknown {what} {name!r}{hint}")


_ABILITIES = {ability.name.lower(): ability for ability in Ability}


def _abilities(written: Mapping[str, int], what: str) -> dict[Ability, int]:
    return {_lookup(_ABILITIES, k, what): v for k, v in written.items()}


def parse_build(text: str) -> BuildSpec:
    """The build file `text`, shape-checked. Raises `BuildError`."""
    try:
        return BuildSpec.model_validate(yaml.safe_load(text))
    except ValidationError as error:
        raise BuildError(f"a build file is not shaped as read:\n{error}") from None


def load_build(spec: BuildSpec, catalogue: Catalogue) -> Build:
    """Check every choice in `spec` against `catalogue` and compile its
    policy. Raises `BuildError`, or `AplLoadError` for the policy."""
    cls = _lookup(catalogue.classes, spec.class_, "class")
    builder = CharacterBuilder(
        spec.name, cls, spec.level, _abilities(spec.abilities, "ability")
    )
    size = None if spec.size is None else _lookup(_SIZES, spec.size, "size")
    builder.species(_lookup(catalogue.species, spec.species, "species"), size)
    builder.background(
        _lookup(catalogue.backgrounds, spec.background, "background"),
        _abilities(spec.background_increases, "ability"),
    )
    builder.skills(_lookup(SKILLS, name, "skill") for name in spec.skills)
    if spec.subclass is not None:
        builder.subclass(_lookup(cls.subclasses, spec.subclass, "subclass"))
    if spec.fighting_style is not None:
        builder.fighting_style(_lookup(catalogue.feats, spec.fighting_style, "feat"))
    if spec.weapon_mastery:
        builder.masteries(
            _lookup(catalogue.weapons, name, "weapon") for name in spec.weapon_mastery
        )
    for choice in spec.feats:
        increases = choice.increases
        builder.feat(
            choice.level,
            _lookup(catalogue.feats, choice.feat, "feat"),
            None if increases is None else _abilities(increases, "ability"),
        )
    kit = spec.equipment
    builder.equip(
        armoury=catalogue.weapons.values(),
        weapon=None
        if kit.weapon is None
        else _lookup(catalogue.weapons, kit.weapon, "weapon"),
        two_handed=kit.two_handed,
        armour=None
        if kit.armour is None
        else _lookup(catalogue.armour, kit.armour, "armour"),
        shield=kit.shield,
    )
    # Built once here so that a choice its level owes fails at load.
    builder.build(Cell(0, 0))
    apl = (
        DEFAULT_APL
        if spec.apl is None
        else Apl(Entry.from_mapping(row) for row in spec.apl)
    )
    return Build(spec, apl, builder)


_SIZES = {size.value: size for size in Size}


def load_party(path: Path, catalogue: Catalogue) -> Party:
    """The party file at `path`, each member's build loaded from its path
    relative to it. Raises `BuildError`, or `AplLoadError` for a policy or the
    retreat condition."""
    try:
        spec = PartySpec.model_validate(yaml.safe_load(path.read_text()))
    except ValidationError as error:
        raise BuildError(f"{path} is not shaped as read:\n{error}") from None
    members = tuple(
        load_build(parse_build((path.parent / member).read_text()), catalogue)
        for member in spec.members
    )
    retreat = (
        None
        if spec.retreat_when is None
        else compile_condition(spec.retreat_when, NamespaceSchema())
    )
    return Party(members, retreat)


def arm_character(character: Character, apl: Apl, world: World, *, rng: Rng) -> Apl:
    """Bind `apl` to `character` in `world`, make it the gate of the
    character's dispatcher, and attach the opportunity attack it answers.
    Returns the bound policy, for the turn loop. Call once per character."""
    bound = apl.bind(character, world)
    character.dispatcher.bind(gate=bound)
    character.attach(
        OpportunityAttacks(
            character, world=world, rng=rng, trigger=bound.namespace.trigger
        )
    )
    return bound
