"""The imported classes, species, backgrounds and feats the default suite
stands on.

CI holds no fetched data, so these are hand-written; `tests/content/` asserts
each equals its imported entry (`docs/decisions.md` § w2a2). A species
fixture carries no traits: the loader reads none, and the test compares the
rest.
"""

from typing import Final

from dndsim.content.backgrounds import Background
from dndsim.content.classes import (
    ArmourTraining,
    CharacterClass,
    SkillChoice,
    WeaponTraining,
)
from dndsim.content.feats import Feat, FeatCategory, Prerequisite
from dndsim.content.species import Species
from dndsim.core.dice import Dice
from dndsim.rules.enums import Ability, Size, WeaponProperty

_SIMPLE: Final = WeaponTraining(martial=False)

FIGHTER: Final = CharacterClass(
    name="Fighter",
    hit_die=Dice(1, 10),
    saving_throws=frozenset({Ability.STRENGTH, Ability.CONSTITUTION}),
    weapon_training=(_SIMPLE, WeaponTraining(martial=True)),
    armour_training=frozenset(ArmourTraining),
    skills=SkillChoice(
        2,
        frozenset(
            {
                "acrobatics",
                "animal handling",
                "athletics",
                "history",
                "insight",
                "intimidation",
                "persuasion",
                "perception",
                "survival",
            }
        ),
    ),
    ability_score_improvement_levels=(4, 6, 8, 12, 14, 16),
    subclass_level=3,
    fighting_style_level=1,
    weapon_mastery_level=1,
    subclasses=frozenset(
        {"Battle Master", "Champion", "Eldritch Knight", "Psi Warrior"}
    ),
)
ROGUE: Final = CharacterClass(
    name="Rogue",
    hit_die=Dice(1, 8),
    saving_throws=frozenset({Ability.DEXTERITY, Ability.INTELLIGENCE}),
    weapon_training=(
        _SIMPLE,
        WeaponTraining(
            martial=True,
            properties=frozenset({WeaponProperty.FINESSE, WeaponProperty.LIGHT}),
        ),
    ),
    armour_training=frozenset({ArmourTraining.LIGHT}),
    skills=SkillChoice(
        4,
        frozenset(
            {
                "acrobatics",
                "athletics",
                "deception",
                "insight",
                "intimidation",
                "investigation",
                "perception",
                "persuasion",
                "sleight of hand",
                "stealth",
            }
        ),
    ),
    ability_score_improvement_levels=(4, 8, 10, 12, 16),
    subclass_level=3,
    fighting_style_level=None,
    weapon_mastery_level=1,
    subclasses=frozenset({"Arcane Trickster", "Assassin", "Soulknife", "Thief"}),
)
SOLDIER: Final = Background(
    name="Soldier",
    abilities=frozenset({Ability.STRENGTH, Ability.DEXTERITY, Ability.CONSTITUTION}),
    increases=((2, 1), (1, 1, 1)),
    feat="Savage Attacker",
    feat_option=None,
    skills=frozenset({"athletics", "intimidation"}),
)
SAVAGE_ATTACKER: Final = Feat(
    name="Savage Attacker",
    category=FeatCategory.ORIGIN,
    prerequisites=(),
    repeatable=False,
)
GREAT_WEAPON_FIGHTING: Final = Feat(
    name="Great Weapon Fighting",
    category=FeatCategory.FIGHTING_STYLE,
    prerequisites=(),
    repeatable=False,
)
GREAT_WEAPON_MASTER: Final = Feat(
    name="Great Weapon Master",
    category=FeatCategory.GENERAL,
    prerequisites=(Prerequisite(level=4, abilities=((Ability.STRENGTH, 13),)),),
    repeatable=False,
)
ABILITY_SCORE_IMPROVEMENT: Final = Feat(
    name="Ability Score Improvement",
    category=FeatCategory.GENERAL,
    prerequisites=(Prerequisite(level=4),),
    repeatable=True,
)
HEAVY_ARMOR_MASTER: Final = Feat(
    name="Heavy Armor Master",
    category=FeatCategory.GENERAL,
    prerequisites=(Prerequisite(level=4, armour_training=ArmourTraining.HEAVY),),
    repeatable=False,
)
WAR_CASTER: Final = Feat(
    name="War Caster",
    category=FeatCategory.GENERAL,
    prerequisites=(Prerequisite(level=4, spellcasting=True),),
    repeatable=False,
)
BLESSED_WARRIOR: Final = Feat(
    name="Blessed Warrior",
    category=FeatCategory.FIGHTING_STYLE,
    prerequisites=(),
    repeatable=False,
    class_name="Paladin",
)
HUMAN: Final = Species(
    name="Human",
    sizes=frozenset({Size.SMALL, Size.MEDIUM}),
    speed_ft=30,
    darkvision_ft=0,
    traits=(),
)
GOLIATH: Final = Species(
    name="Goliath",
    sizes=frozenset({Size.MEDIUM}),
    speed_ft=35,
    darkvision_ft=0,
    traits=(),
)
