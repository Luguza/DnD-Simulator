"""`Character` — a player character, and the Builder that assembles one from
the imported class, species, background and feats.

The Builder holds every rule a build's choices are checked against and
derives the numbers: ability scores, hit points, speed, size, proficiencies
and the equipment's AC. Names are resolved before it is called, by
`builds/loader.py`. `docs/decisions.md` § w2f.

XPHB (2024) "Creating a Character" (Steps 2-5), "Hit Points" (Fixed Hit
Points by Class), "Equipment" (Armor Training, Weapon Proficiency, Versatile),
"Feats" (Ability Score Improvement).
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Final, Self

from dndsim.content.backgrounds import Background
from dndsim.content.classes import SKILLS, ArmourTraining, CharacterClass
from dndsim.content.feats import Feat, FeatCategory, Prerequisite
from dndsim.content.species import Species
from dndsim.core.errors import DndSimError
from dndsim.core.geometry import Cell
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import UNARMOURED, Armour, StatBlock, Weapon
from dndsim.rules.constants import ability_modifier
from dndsim.rules.enums import Ability, Size, WeaponProperty

__all__ = ["ABILITY_SCORE_IMPROVEMENT", "BuildError", "Character", "CharacterBuilder"]

ABILITY_SCORE_IMPROVEMENT: Final = "Ability Score Improvement"
"""The feat whose increases a build chooses here; every other feat's own
choices are registered by the row implementing it (`docs/decisions.md`
§ The `w2f` spec correction)."""

MAX_ABILITY_SCORE: Final = 20
"""XPHB "Feats" -> Ability Score Improvement: "can't increase an ability
score above 20"; "Background Ability Scores" likewise."""

HEAVY_ARMOUR_SPEED_PENALTY_FT: Final = 10
"""XPHB "Equipment" -> Armor table, Strength column."""

_ASI_PATTERNS: Final = ((2,), (1, 1))


class BuildError(DndSimError, ValueError):
    """A build that is not a character the rules allow, or not one this
    simulator can yet express. Raised at load, naming the choice."""


def _training(armour: Armour) -> ArmourTraining:
    """Which training `armour` needs, read off the shape its named constructor
    gave it."""
    if not armour.abilities:
        return ArmourTraining.HEAVY
    ((_, cap),) = armour.abilities
    return ArmourTraining.LIGHT if cap is None else ArmourTraining.MEDIUM


class Character(Creature):
    """A player character: a `Creature` that remembers what it was built from.

    Makes death saving throws. Build one with `CharacterBuilder`.
    """

    __slots__ = ("background", "character_class", "feats", "species", "subclass")

    def __init__(
        self,
        *,
        name: str,
        stats: StatBlock,
        position: Cell,
        character_class: CharacterClass,
        subclass: str | None,
        species: Species,
        background: Background,
        feats: tuple[str, ...],
        weapon: Weapon | None = None,
        armour: Armour = UNARMOURED,
        shield: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            stats=stats,
            position=position,
            weapon=weapon,
            armour=armour,
            shield=shield,
            player_character=True,
        )
        self.character_class = character_class
        self.subclass = subclass
        self.species = species
        self.background = background
        self.feats = feats
        """Every feat the character has, its Origin feat first, then by the
        level it was taken at."""


class CharacterBuilder:
    """Assembles a `Character` one choice at a time; `build()` makes it.

    Each method checks its choice against the records it is given and raises
    `BuildError` for one the rules do not allow. `build()` raises for a
    choice the character's level owes and was not made.
    """

    def __init__(
        self,
        name: str,
        character_class: CharacterClass,
        level: int,
        abilities: Mapping[Ability, int],
    ) -> None:
        if not 1 <= level <= 12:
            raise BuildError(f"{name}: level {level} is outside 1-12")
        missing = [a.value for a in Ability if a not in abilities]
        if missing:
            raise BuildError(f"{name}: no score for {missing}")
        for ability, score in abilities.items():
            if not 3 <= score <= 18:
                raise BuildError(
                    f"{name}: a {ability.value} of {score} is no score character "
                    "creation produces (3-18)"
                )
        self._name = name
        self._class = character_class
        self._level = level
        self._scores = dict(abilities)
        self._species: Species | None = None
        self._size: Size | None = None
        self._background: Background | None = None
        self._skills: frozenset[str] | None = None
        self._subclass: str | None = None
        self._feats: list[tuple[int, Feat]] = []
        self._fighting_style: Feat | None = None
        self._masteries: frozenset[str] | None = None
        self._weapon: Weapon | None = None
        self._armour: Armour = UNARMOURED
        self._shield = False
        self._trained: frozenset[str] = frozenset()

    def _fail(self, message: str) -> BuildError:
        return BuildError(f"{self._name}: {message}")

    def _increase(self, increases: Mapping[Ability, int], why: str) -> None:
        for ability, amount in increases.items():
            raised = self._scores[ability] + amount
            if raised > MAX_ABILITY_SCORE:
                raise self._fail(
                    f"{why} raises {ability.value} to {raised}, above "
                    f"{MAX_ABILITY_SCORE}"
                )
            self._scores[ability] = raised

    # -- origin ---------------------------------------------------------------

    def species(self, species: Species, size: Size | None = None) -> Self:
        """The species, and the size chosen when it offers more than one."""
        if size is None:
            if len(species.sizes) != 1:
                options = sorted(s.value for s in species.sizes)
                raise self._fail(f"a {species.name} chooses its size from {options}")
            (size,) = species.sizes
        elif size not in species.sizes:
            options = sorted(s.value for s in species.sizes)
            raise self._fail(f"a {species.name} is one of {options}, not {size.value}")
        self._species = species
        self._size = size
        return self

    def background(
        self, background: Background, increases: Mapping[Ability, int]
    ) -> Self:
        """The background, and how its three abilities are increased."""
        outside = sorted(a.value for a in increases.keys() - background.abilities)
        if outside:
            allowed = sorted(a.value for a in background.abilities)
            raise self._fail(f"{background.name} increases {allowed}, not {outside}")
        pattern = tuple(sorted(increases.values(), reverse=True))
        if pattern not in background.increases:
            raise self._fail(
                f"{background.name} increases by one of {background.increases}, "
                f"not {pattern}"
            )
        self._increase(increases, background.name)
        self._background = background
        return self

    def skills(self, chosen: Iterable[str]) -> Self:
        """The class's skill choices. Needs the background first."""
        if self._background is None:
            raise self._fail("choose the background before the class skills")
        picked = tuple(chosen)
        choice = self._class.skills
        unknown = sorted(set(picked) - SKILLS)
        if unknown:
            raise self._fail(f"no skills {unknown}")
        if len(set(picked)) != len(picked) or len(picked) != choice.count:
            raise self._fail(
                f"a {self._class.name} chooses {choice.count} different skills, "
                f"got {list(picked)}"
            )
        if choice.options is not None:
            outside = sorted(set(picked) - choice.options)
            if outside:
                raise self._fail(f"a {self._class.name} cannot choose {outside}")
        doubled = sorted(set(picked) & self._background.skills)
        if doubled:
            raise self._fail(f"{self._background.name} already grants {doubled}")
        self._skills = frozenset(picked)
        return self

    # -- what the class levels grant -----------------------------------------

    def subclass(self, name: str) -> Self:
        """The subclass, chosen at the class's subclass level."""
        if self._level < self._class.subclass_level:
            raise self._fail(
                f"a {self._class.name} chooses a subclass at level "
                f"{self._class.subclass_level}, not {self._level}"
            )
        if name not in self._class.subclasses:
            raise self._fail(f"{self._class.name} has no subclass {name!r}")
        self._subclass = name
        return self

    def fighting_style(self, feat: Feat) -> Self:
        """The Fighting Style feat the class's Fighting Style feature grants."""
        level = self._class.fighting_style_level
        if level is None or self._level < level:
            raise self._fail(
                f"a level {self._level} {self._class.name} has no "
                "Fighting Style feature"
            )
        if feat.category is not FeatCategory.FIGHTING_STYLE:
            raise self._fail(f"{feat.name} is not a Fighting Style feat")
        self._check_style_class(feat)
        self._fighting_style = feat
        return self

    def _check_style_class(self, feat: Feat) -> None:
        if feat.class_name is not None and feat.class_name != self._class.name:
            raise self._fail(f"{feat.name} is a {feat.class_name}'s Fighting Style")

    def feat(
        self,
        level: int,
        feat: Feat,
        increases: Mapping[Ability, int] | None = None,
    ) -> Self:
        """The feat an Ability Score Improvement feature grants at `level`.
        `increases` is the Ability Score Improvement feat's choice. Call in
        level order."""
        levels = self._class.ability_score_improvement_levels
        if level not in levels or level > self._level:
            due = [n for n in levels if n <= self._level]
            raise self._fail(
                f"a level {self._level} {self._class.name} takes a feat at "
                f"{due}, not {level}"
            )
        if any(taken == level for taken, _ in self._feats):
            raise self._fail(f"two feats at level {level}")
        if self._feats and self._feats[-1][0] > level:
            raise self._fail("feats are taken in level order")
        self._check_feat(level, feat)
        if feat.name == ABILITY_SCORE_IMPROVEMENT:
            chosen = dict(increases or {})
            if tuple(sorted(chosen.values(), reverse=True)) not in _ASI_PATTERNS:
                raise self._fail(
                    f"level {level} {feat.name} increases one score by 2 or two "
                    f"by 1, got {_named(chosen)}"
                )
            self._increase(chosen, f"level {level} {feat.name}")
        elif increases:
            raise self._fail(
                f"{feat.name}'s own choices are unknown until the row "
                "implementing it registers them (docs/decisions.md § w2f)"
            )
        self._feats.append((level, feat))
        return self

    def _check_feat(self, level: int, feat: Feat) -> None:
        if feat.category is FeatCategory.FIGHTING_STYLE:
            style = self._class.fighting_style_level
            if style is None or style > level:
                raise self._fail(
                    f"{feat.name} needs the Fighting Style feature, which a "
                    f"level {level} {self._class.name} lacks"
                )
            self._check_style_class(feat)
        if feat.prerequisites and not any(
            self._meets(level, p) for p in feat.prerequisites
        ):
            raise self._fail(f"level {level}: {feat.name}'s prerequisite is not met")
        if not feat.repeatable and feat.name in self._taken():
            raise self._fail(f"{feat.name} is taken twice and is not repeatable")

    def _meets(self, level: int, prerequisite: Prerequisite) -> bool:
        if prerequisite.spellcasting:
            # Nothing imported says which classes cast before `w5a`
            # (`docs/decisions.md` § w2f).
            raise self._fail(
                "a feat requiring the Spellcasting or Pact Magic feature cannot "
                "be checked until w5a"
            )
        if prerequisite.level is not None and level < prerequisite.level:
            return False
        if any(self._scores[a] < score for a, score in prerequisite.abilities):
            return False
        training = prerequisite.armour_training
        return training is None or training in self._class.armour_training

    def _taken(self) -> list[str]:
        names = [] if self._background is None else [self._background.feat]
        if self._fighting_style is not None:
            names.append(self._fighting_style.name)
        return names + [feat.name for _, feat in self._feats]

    def masteries(self, weapons: Iterable[Weapon]) -> Self:
        """The kinds of weapon the Weapon Mastery feature is used with."""
        level = self._class.weapon_mastery_level
        if level is None or self._level < level:
            raise self._fail(
                f"a level {self._level} {self._class.name} has no "
                "Weapon Mastery feature"
            )
        names: list[str] = []
        for weapon in weapons:
            if weapon.mastery is None:
                raise self._fail(f"{weapon.name} has no mastery property")
            if not self._class.trained_with(weapon):
                raise self._fail(
                    f"a {self._class.name} is not proficient with {weapon.name}"
                )
            names.append(weapon.name)
        if not names or len(set(names)) != len(names):
            raise self._fail(f"Weapon Mastery names different weapons, got {names}")
        self._masteries = frozenset(names)
        return self

    # -- equipment ------------------------------------------------------------

    def equip(
        self,
        *,
        armoury: Iterable[Weapon],
        weapon: Weapon | None = None,
        two_handed: bool = False,
        armour: Armour | None = None,
        shield: bool = False,
    ) -> Self:
        """What the character wields, wears and carries on its arm.

        `armoury` is every weapon the class's training is read against. A
        weapon it is not proficient with is held without the proficiency
        bonus; armour or a Shield it is not trained with is refused.
        `two_handed` holds a Versatile weapon in two hands.
        """
        self._trained = frozenset(
            w.name for w in armoury if self._class.trained_with(w)
        )
        if weapon is not None:
            if weapon.has(WeaponProperty.TWO_HANDED):
                two_handed = True
            elif two_handed:
                if weapon.versatile_damage is None:
                    raise self._fail(f"{weapon.name} is not held in two hands")
                weapon = replace(weapon, damage=weapon.versatile_damage)
        elif two_handed:
            raise self._fail("nothing is held in two hands")
        if shield and two_handed:
            raise self._fail("a Shield leaves one hand for the weapon")
        training = self._class.armour_training
        if armour is not None and _training(armour) not in training:
            raise self._fail(f"a {self._class.name} is not trained with {armour.name}")
        if shield and ArmourTraining.SHIELD not in training:
            raise self._fail(f"a {self._class.name} is not trained with Shields")
        self._weapon = weapon
        self._armour = UNARMOURED if armour is None else armour
        self._shield = shield
        return self

    # -- the character --------------------------------------------------------

    def build(self, position: Cell) -> Character:
        """The character, at `position`. Raises `BuildError` for a choice its
        level owes and that was not made."""
        cls = self._class
        species, size, background = self._species, self._size, self._background
        if species is None or size is None or background is None:
            raise self._fail("a character has a species and a background")
        if self._skills is None:
            raise self._fail(f"a {cls.name} chooses {cls.skills.count} skills")
        if self._level >= cls.subclass_level and self._subclass is None:
            raise self._fail(f"a level {self._level} {cls.name} has a subclass")
        style = cls.fighting_style_level
        if style is not None and self._level >= style and self._fighting_style is None:
            raise self._fail(f"a level {self._level} {cls.name} has a Fighting Style")
        mastery = cls.weapon_mastery_level
        if mastery is not None and self._level >= mastery and not self._masteries:
            raise self._fail(f"a level {self._level} {cls.name} names its masteries")
        owed = [n for n in cls.ability_score_improvement_levels if n <= self._level]
        taken = [level for level, _ in self._feats]
        if taken != owed:
            raise self._fail(f"a level {self._level} {cls.name} takes a feat at {owed}")

        scores = MappingProxyType(dict(self._scores))
        constitution = ability_modifier(scores[Ability.CONSTITUTION])
        faces = cls.hit_die.sides
        # Level 1 takes the die's maximum, each later level the fixed value.
        max_hp = (
            faces + constitution + (self._level - 1) * (faces // 2 + 1 + constitution)
        )
        speed = species.speed_ft
        needed = self._armour.strength_requirement
        if needed is not None and scores[Ability.STRENGTH] < needed:
            speed -= HEAVY_ARMOUR_SPEED_PENALTY_FT
        stats = StatBlock(
            abilities=scores,
            max_hp=max(max_hp, 1),
            level=self._level,
            speed_ft=speed,
            size=size,
            saving_throws=cls.saving_throws,
            skills=background.skills | self._skills,
            trained_weapons=self._trained,
        )
        feats = [background.feat]
        if self._fighting_style is not None:
            feats.append(self._fighting_style.name)
        feats.extend(feat.name for _, feat in self._feats)
        return Character(
            name=self._name,
            stats=stats,
            position=position,
            character_class=cls,
            subclass=self._subclass,
            species=species,
            background=background,
            feats=tuple(feats),
            weapon=self._weapon,
            armour=self._armour,
            shield=self._shield,
        )


def _named(increases: Mapping[Ability, int]) -> dict[str, int]:
    return {ability.value: amount for ability, amount in increases.items()}
