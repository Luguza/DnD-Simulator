"""`TargetDummy` — a creature built from a `DummySpec` of configurable knobs.

`DummySpec.from_mapping()` reads the knobs from a parsed YAML document and
`build_dummies()` turns a spec into `count` creatures. Damage defences and
Legendary Resistance are attached as hook sources; every other knob is data on
`TargetDummy.spec` (`docs/decisions.md` § w2d).
"""

import difflib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from types import MappingProxyType
from typing import Final, TypeVar

from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.creatures.creature import Creature
from dndsim.creatures.resources import Recovery, ResourceSpec
from dndsim.creatures.stats import Armour, StatBlock, Weapon
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    HitContext,
    IncomingDamageContext,
    SaveResolvedContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import SIZE_WIDTH_CELLS
from dndsim.rules.enums import Ability, DamageType, RecoveryTrigger, Size

__all__ = [
    "LEGENDARY_RESISTANCE",
    "ROUTINE_ABILITY",
    "ROUTINE_REACH_FT",
    "DamageDefences",
    "DummyAttack",
    "DummySpec",
    "LegendaryResistance",
    "Movement",
    "MovementProfile",
    "RoutineTerms",
    "TargetDummy",
    "ThreatProfile",
    "build_dummies",
    "routine_weapon",
]

_E = TypeVar("_E", bound=Enum)

LEGENDARY_RESISTANCE: Final = "legendary_resistance"

ROUTINE_REACH_FT: Final = 5
"""The reach of a routine's attack entry (`docs/decisions.md` § The `w2g`
spec correction)."""


class Movement(Enum):
    """How a dummy moves on its turn."""

    STATIC = "static"
    CLOSES_TO_MELEE = "closes_to_melee"
    KITES_AT = "kites_at"


@dataclass(frozen=True, slots=True)
class MovementProfile:
    """A `Movement`, and for `KITES_AT` the distance in feet it keeps.

    Build one through the named constructors; `parse()` reads `static`,
    `closes_to_melee` and `kites_at(30)`.
    """

    kind: Movement
    range_ft: int | None = None

    def __post_init__(self) -> None:
        if (self.kind is Movement.KITES_AT) != (self.range_ft is not None):
            raise ValueError(
                f"{self.kind.value} takes a range only when it is kites_at; "
                f"got range_ft={self.range_ft}"
            )
        if self.range_ft is not None and self.range_ft < 5:
            raise ValueError(f"kites_at needs a range of 5 ft or more, got {self}")

    @classmethod
    def static(cls) -> "MovementProfile":
        return cls(Movement.STATIC)

    @classmethod
    def closes_to_melee(cls) -> "MovementProfile":
        return cls(Movement.CLOSES_TO_MELEE)

    @classmethod
    def kites_at(cls, range_ft: int) -> "MovementProfile":
        return cls(Movement.KITES_AT, range_ft)

    @classmethod
    def parse(cls, text: str) -> "MovementProfile":
        """Read `static`, `closes_to_melee` or `kites_at(<feet>)`.

        Raises `ValueError` for anything else.
        """
        match = _KITES_AT.fullmatch(text.strip())
        if match is not None:
            return cls.kites_at(int(match["range"]))
        return cls(_member(Movement, text, "movement_profile"))

    def __str__(self) -> str:
        if self.range_ft is None:
            return self.kind.value
        return f"{self.kind.value}({self.range_ft})"


_KITES_AT: Final = re.compile(r"kites_at\(\s*(?P<range>\d+)\s*\)")


class ThreatProfile(Enum):
    """Which enemy a dummy picks as its target."""

    NEAREST = "nearest"
    LOWEST_HP = "lowest_hp"


@dataclass(frozen=True, slots=True)
class DummyAttack:
    """One entry in a dummy's scripted `damage_per_round` routine: an attack
    rolled at `to_hit`, or, with `save` in its place, a saving throw forced
    at the spec's `save_dc` (`docs/decisions.md` § The `w2g` spec correction).

    Raises `ValueError` unless exactly one of `to_hit` and `save` is given, or
    for fewer than one target.
    """

    to_hit: int | None
    dice: Dice
    damage_type: DamageType
    flat: int = 0
    targets: int = 1
    save: Ability | None = None

    def __post_init__(self) -> None:
        if self.targets < 1:
            raise ValueError(f"an attack has at least one target, got {self}")
        if (self.to_hit is None) == (self.save is None):
            raise ValueError(
                f"a damage_per_round entry has a to_hit or a save, not both or "
                f"neither; got {self}"
            )

    @property
    def is_save(self) -> bool:
        """Whether this entry forces a saving throw rather than an attack."""
        return self.save is not None


@dataclass(frozen=True, slots=True)
class DummySpec:
    """Every knob of a target dummy (DESIGN.md § The target dummy).

    `saves` holds a save bonus per ability; an ability not named saves at +0.
    `position` is where the first dummy stands; the rest stand in a row along
    +x, one footprint apart.

    Raises `ValueError` for a knob out of range.
    """

    ac: int = 15
    hp: int = 50
    count: int = 1
    name: str = "Target Dummy"
    size: Size = Size.MEDIUM
    speed_ft: int = 30
    position: Cell = Cell(0, 0)
    saves: Mapping[Ability, int] = field(default_factory=dict)
    save_dc: int | None = None
    damage_per_round: tuple[DummyAttack, ...] = ()
    resistances: frozenset[DamageType] = frozenset()
    immunities: frozenset[DamageType] = frozenset()
    vulnerabilities: frozenset[DamageType] = frozenset()
    legendary_resistances: int = 0
    regeneration: int = 0
    threat_profile: ThreatProfile = ThreatProfile.NEAREST
    movement_profile: MovementProfile = MovementProfile(Movement.CLOSES_TO_MELEE)

    def __post_init__(self) -> None:
        for knob, value, least in (
            ("ac", self.ac, 1),
            ("hp", self.hp, 1),
            ("count", self.count, 1),
            ("speed_ft", self.speed_ft, 0),
            ("legendary_resistances", self.legendary_resistances, 0),
            ("regeneration", self.regeneration, 0),
        ):
            if value < least:
                raise ValueError(f"dummy {knob} is at least {least}, got {value}")
        if self.save_dc is not None and self.save_dc < 1:
            raise ValueError(f"dummy save_dc is at least 1, got {self.save_dc}")
        self._refuse_what_nothing_acts_on()
        object.__setattr__(self, "saves", MappingProxyType(dict(self.saves)))

    def _refuse_what_nothing_acts_on(self) -> None:
        """`docs/questions.md` #34, #35."""
        forces_a_save = any(entry.is_save for entry in self.damage_per_round)
        if forces_a_save and self.save_dc is None:
            raise ValueError(
                "damage_per_round has a save entry and the dummy has no save_dc "
                "to force it at"
            )
        if self.save_dc is not None and not forces_a_save:
            raise ValueError(
                "save_dc is set and no damage_per_round entry is a save, so "
                "nothing would read it"
            )
        profile = self.movement_profile
        if (
            profile.range_ft is not None
            and profile.range_ft > ROUTINE_REACH_FT
            and any(not entry.is_save for entry in self.damage_per_round)
        ):
            raise ValueError(
                f"damage_per_round has an attack entry, which reaches "
                f"{ROUTINE_REACH_FT} ft, and movement_profile {profile} keeps "
                "the dummy out of that reach"
            )

    @classmethod
    def from_mapping(cls, document: Mapping[str, object]) -> "DummySpec":
        """Read a spec from a parsed YAML mapping.

        Enum knobs are their values (`slashing`, `large`, `dex`); `position` is
        `[x, y]`; `saves` maps an ability to its bonus; each `damage_per_round`
        entry has `to_hit` or `save`, `dice`, `type` and optionally `flat` and
        `targets`.
        Raises `ValueError` naming the knob, with a suggestion for a misspelt
        one.
        """
        known = {spec.name for spec in fields(cls)}
        unknown = [key for key in document if key not in known]
        if unknown:
            key = unknown[0]
            raise ValueError(f"unknown dummy knob {key!r}{_suggest(key, known)}")
        kwargs: dict[str, object] = {}
        for key, raw in document.items():
            kwargs[key] = _READERS.get(key, _plain)(key, raw)
        return cls(**kwargs)  # type: ignore[arg-type]

    def __reduce__(self) -> tuple[type["DummySpec"], tuple[object, ...]]:
        """Pickled by its knobs, `saves` as a plain dict
        (`docs/decisions.md` § w2g)."""
        return type(self), tuple(
            dict(self.saves) if spec.name == "saves" else getattr(self, spec.name)
            for spec in fields(self)
        )

    def abilities(self) -> dict[Ability, int]:
        """The ability scores whose modifiers are the save bonuses; a score is
        an encoding here and may fall outside 1-30 (`docs/decisions.md` § w2d).
        """
        return {ability: 10 + 2 * self.saves.get(ability, 0) for ability in Ability}


class DamageDefences(HookSource):
    """The dummy's damage resistances, immunities and vulnerabilities."""

    __slots__ = ("_immunities", "_resistances", "_vulnerabilities")

    def __init__(self, owner: Creature, spec: DummySpec) -> None:
        super().__init__(owner)
        self._resistances = spec.resistances
        self._immunities = spec.immunities
        self._vulnerabilities = spec.vulnerabilities

    @hook("on_incoming_damage")
    def defences(self, ctx: IncomingDamageContext) -> R:
        """Records each defence against a type this hit carries."""
        applied = False
        for damage_type in ctx.amounts:
            if damage_type in self._immunities:
                ctx.add_immunity(damage_type)
                applied = True
            if damage_type in self._resistances:
                ctx.add_resistance(damage_type)
                applied = True
            if damage_type in self._vulnerabilities:
                ctx.add_vulnerability(damage_type)
                applied = True
        return R.APPLIED if applied else R.SKIPPED


class LegendaryResistance(HookSource):
    """ "If [the creature] fails a saving throw, it can choose to succeed
    instead."

    The dummy chooses to on every failed save while a use is left
    (`docs/decisions.md` § w2d).
    """

    __slots__ = ("_creature",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self._creature = owner

    @hook("on_save_resolved")
    def succeed_instead(self, ctx: SaveResolvedContext) -> R:
        """Spends a use to turn a failed save into a success."""
        resources = self._creature.resources
        if (
            ctx.succeeded
            or ctx.forced_success
            or not resources.can_spend(LEGENDARY_RESISTANCE)
        ):
            return R.SKIPPED
        resources.spend(LEGENDARY_RESISTANCE)
        ctx.succeed("a use spent")
        return R.APPLIED


ROUTINE_ABILITY: Final = Ability.STRENGTH
"""The ability a routine's attack entry is resolved with; `RoutineTerms`
cancels its modifier."""


def routine_weapon(index: int, entry: DummyAttack) -> Weapon:
    """The melee weapon `resolve_attack()` swings for attack entry `index`."""
    return Weapon(
        name=f"routine {index + 1}",
        damage=entry.dice,
        damage_type=entry.damage_type,
        reach_ft=ROUTINE_REACH_FT,
    )


class RoutineTerms(HookSource):
    """Makes a routine attack's total d20 + `to_hit` and its damage `dice` +
    `flat`, in place of the ability modifier and proficiency bonus the attack
    sequence adds (`docs/decisions.md` § w2g2)."""

    __slots__ = ("_creature", "_entries")

    def __init__(self, owner: Creature, entries: Mapping[str, DummyAttack]) -> None:
        super().__init__(owner)
        self._creature = owner
        self._entries = entries

    @hook("on_declare_attack")
    def to_hit(self, ctx: AttackDeclaredContext) -> R:
        """Adds what takes the attack sequence's own terms to `to_hit`."""
        weapon = ctx.weapon
        entry = None if weapon is None else self._entries.get(weapon.name)
        if entry is None or entry.to_hit is None or weapon is None:
            return R.SKIPPED
        creature = self._creature
        added = creature.modifier(ctx.ability)
        trained = creature.stats.trained_weapons
        if trained is None or weapon.name in trained:
            added += creature.proficiency_bonus
        if entry.to_hit == added:
            return R.SKIPPED
        ctx.bonus.add(flat=entry.to_hit - added)
        return R.APPLIED

    @hook("on_hit")
    def flat(self, ctx: HitContext) -> R:
        """Adds what takes the ability modifier already in the bag to `flat`."""
        weapon = ctx.weapon
        entry = None if weapon is None else self._entries.get(weapon.name)
        if entry is None:
            return R.SKIPPED
        difference = entry.flat - self._creature.modifier(ROUTINE_ABILITY)
        if difference == 0:
            return R.SKIPPED
        ctx.damage.add(flat=difference)
        return R.APPLIED


class TargetDummy(Creature):
    """A `Creature` built from a `DummySpec`, which it keeps as `spec`.

    `routine_weapons` holds, per `damage_per_round` entry, the weapon an
    attack entry is resolved with, `None` for a save entry.
    """

    __slots__ = ("routine_weapons", "spec")

    def __init__(self, spec: DummySpec, *, name: str, position: Cell) -> None:
        stats = StatBlock(
            abilities=spec.abilities(),
            max_hp=spec.hp,
            speed_ft=spec.speed_ft,
            size=spec.size,
        )
        super().__init__(
            name=name, stats=stats, position=position, armour=Armour.natural(spec.ac)
        )
        self.spec = spec
        if spec.resistances or spec.immunities or spec.vulnerabilities:
            self.attach(DamageDefences(self, spec))
        if spec.legendary_resistances:
            self.resources.add(
                ResourceSpec(
                    key=LEGENDARY_RESISTANCE,
                    label="Legendary Resistance",
                    maximum=spec.legendary_resistances,
                    recovery=(Recovery(RecoveryTrigger.LONG_REST),),
                )
            )
            self.attach(LegendaryResistance(self))
        self.routine_weapons = tuple(
            None if entry.is_save else routine_weapon(index, entry)
            for index, entry in enumerate(spec.damage_per_round)
        )
        swung = {
            weapon.name: entry
            for weapon, entry in zip(
                self.routine_weapons, spec.damage_per_round, strict=True
            )
            if weapon is not None
        }
        if swung:
            self.attach(RoutineTerms(self, MappingProxyType(swung)))


def build_dummies(spec: DummySpec) -> tuple[TargetDummy, ...]:
    """The `count` dummies `spec` describes, numbered when there is more than
    one."""
    width = SIZE_WIDTH_CELLS[spec.size]
    origin = spec.position
    if spec.count == 1:
        return (TargetDummy(spec, name=spec.name, position=origin),)
    return tuple(
        TargetDummy(
            spec,
            name=f"{spec.name} {index + 1}",
            position=Cell(origin.x + index * width, origin.y),
        )
        for index in range(spec.count)
    )


# -- reading a parsed YAML document ---------------------------------------------


def _suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


def _member(enum: type[_E], raw: object, knob: str) -> _E:
    values = [member.value for member in enum]
    if isinstance(raw, str):
        for member in enum:
            if member.value == raw:
                return member
    raise ValueError(
        f"{knob}: {raw!r} is not one of {values}{_suggest(str(raw), values)}"
    )


def _int(knob: str, raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{knob} is a whole number, got {raw!r}")
    return raw


def _plain(knob: str, raw: object) -> object:
    if knob == "name":
        if not isinstance(raw, str):
            raise ValueError(f"name is text, got {raw!r}")
        return raw
    if knob == "save_dc" and raw is None:
        return None
    return _int(knob, raw)


def _list(knob: str, raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError(f"{knob} is a list, got {raw!r}")
    return raw


def _types(knob: str, raw: object) -> frozenset[DamageType]:
    return frozenset(_member(DamageType, item, knob) for item in _list(knob, raw))


def _position(knob: str, raw: object) -> Cell:
    items = _list(knob, raw)
    if len(items) != 2:
        raise ValueError(f"position is [x, y], got {raw!r}")
    return Cell(_int(knob, items[0]), _int(knob, items[1]))


def _saves(knob: str, raw: object) -> dict[Ability, int]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"saves maps an ability to its bonus, got {raw!r}")
    return {
        _member(Ability, ability, knob): _int(f"{knob}.{ability}", bonus)
        for ability, bonus in raw.items()
    }


_ATTACK_KEYS: Final = frozenset({"to_hit", "save", "dice", "type", "flat", "targets"})


def _attack(knob: str, raw: object) -> DummyAttack:
    if not isinstance(raw, Mapping):
        raise ValueError(f"each {knob} entry is a mapping, got {raw!r}")
    unknown = [key for key in raw if key not in _ATTACK_KEYS]
    if unknown:
        key = str(unknown[0])
        raise ValueError(
            f"unknown {knob} key {key!r}{_suggest(key, sorted(_ATTACK_KEYS))}"
        )
    missing = sorted(key for key in ("dice", "type") if key not in raw)
    if missing:
        raise ValueError(f"a {knob} entry needs {missing}, got {raw!r}")
    if ("to_hit" in raw) == ("save" in raw):
        raise ValueError(f"a {knob} entry has a to_hit or a save, got {raw!r}")
    dice = raw["dice"]
    if not isinstance(dice, str):
        raise ValueError(f"{knob}.dice is notation like '2d6', got {dice!r}")
    return DummyAttack(
        to_hit=_int(f"{knob}.to_hit", raw["to_hit"]) if "to_hit" in raw else None,
        save=_member(Ability, raw["save"], f"{knob}.save") if "save" in raw else None,
        dice=Dice.parse(dice),
        damage_type=_member(DamageType, raw["type"], f"{knob}.type"),
        flat=_int(f"{knob}.flat", raw.get("flat", 0)),
        targets=_int(f"{knob}.targets", raw.get("targets", 1)),
    )


def _movement(knob: str, raw: object) -> MovementProfile:
    if not isinstance(raw, str):
        raise ValueError(f"{knob} is text like 'kites_at(30)', got {raw!r}")
    return MovementProfile.parse(raw)


_READERS: Final = MappingProxyType(
    {
        "size": lambda knob, raw: _member(Size, raw, knob),
        "position": _position,
        "saves": _saves,
        "damage_per_round": lambda knob, raw: tuple(
            _attack(knob, item) for item in _list(knob, raw)
        ),
        "resistances": _types,
        "immunities": _types,
        "vulnerabilities": _types,
        "threat_profile": lambda knob, raw: _member(ThreatProfile, raw, knob),
        "movement_profile": _movement,
    }
)
