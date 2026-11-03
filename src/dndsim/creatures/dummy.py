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
from dndsim.creatures.stats import Armour, StatBlock
from dndsim.hooks.contexts import IncomingDamageContext, SaveResolvedContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import SIZE_WIDTH_CELLS
from dndsim.rules.enums import Ability, DamageType, RecoveryTrigger, Size

__all__ = [
    "LEGENDARY_RESISTANCE",
    "DamageDefences",
    "DummyAttack",
    "DummySpec",
    "LegendaryResistance",
    "Movement",
    "MovementProfile",
    "TargetDummy",
    "ThreatProfile",
    "build_dummies",
]

_E = TypeVar("_E", bound=Enum)

MIN_SAVE_BONUS: Final = -5
MAX_SAVE_BONUS: Final = 10
"""The modifiers an ability score of 1 and of 30 give."""

LEGENDARY_RESISTANCE: Final = "legendary_resistance"


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
    """One attack in a dummy's scripted `damage_per_round` routine.

    `dice` doubles on a Critical Hit and `flat` never does (DESIGN.md
    § Structural prevention).
    """

    to_hit: int
    dice: Dice
    damage_type: DamageType
    flat: int = 0
    targets: int = 1

    def __post_init__(self) -> None:
        if self.targets < 1:
            raise ValueError(f"an attack has at least one target, got {self}")


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
        for ability, bonus in self.saves.items():
            if not MIN_SAVE_BONUS <= bonus <= MAX_SAVE_BONUS:
                raise ValueError(
                    f"a {ability.value} save bonus of {bonus:+d} is outside "
                    f"{MIN_SAVE_BONUS:+d}..{MAX_SAVE_BONUS:+d}, the modifiers an "
                    "ability score can give"
                )
        object.__setattr__(self, "saves", MappingProxyType(dict(self.saves)))

    @classmethod
    def from_mapping(cls, document: Mapping[str, object]) -> "DummySpec":
        """Read a spec from a parsed YAML mapping.

        Enum knobs are their values (`slashing`, `large`, `dex`); `position` is
        `[x, y]`; `saves` maps an ability to its bonus; each `damage_per_round`
        entry has `to_hit`, `dice`, `type` and optionally `flat` and `targets`.
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

    def abilities(self) -> dict[Ability, int]:
        """The ability scores whose modifiers are the save bonuses."""
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


class TargetDummy(Creature):
    """A `Creature` built from a `DummySpec`, which it keeps as `spec`."""

    __slots__ = ("spec",)

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


_ATTACK_KEYS: Final = frozenset({"to_hit", "dice", "type", "flat", "targets"})


def _attack(knob: str, raw: object) -> DummyAttack:
    if not isinstance(raw, Mapping):
        raise ValueError(f"each {knob} entry is a mapping, got {raw!r}")
    unknown = [key for key in raw if key not in _ATTACK_KEYS]
    if unknown:
        key = str(unknown[0])
        raise ValueError(
            f"unknown {knob} key {key!r}{_suggest(key, sorted(_ATTACK_KEYS))}"
        )
    missing = sorted(key for key in ("to_hit", "dice", "type") if key not in raw)
    if missing:
        raise ValueError(f"a {knob} entry needs {missing}, got {raw!r}")
    dice = raw["dice"]
    if not isinstance(dice, str):
        raise ValueError(f"{knob}.dice is notation like '2d6', got {dice!r}")
    return DummyAttack(
        to_hit=_int(f"{knob}.to_hit", raw["to_hit"]),
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
