"""The build schema, the `Character` Builder, and the engine reading what a
build says it is proficient in (`docs/decisions.md` § w2f).

Every record is a fixture bound to its imported entry by a `content` test.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.apl.apl import BEST_SCORED
from dndsim.apl.expr import AplLoadError
from dndsim.builds.loader import (
    DEFAULT_APL,
    Build,
    Catalogue,
    arm_character,
    load_build,
    load_party,
    parse_build,
)
from dndsim.core.dice import Dice
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.character import BuildError, Character
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock
from dndsim.engine.attack import resolve_attack
from dndsim.engine.dying import stabilize
from dndsim.engine.saves import save_bonus
from dndsim.engine.world import World
from dndsim.rules.enums import Ability, Outcome, Size
from origin_fixtures import (
    ABILITY_SCORE_IMPROVEMENT,
    BLESSED_WARRIOR,
    FIGHTER,
    GOLIATH,
    GREAT_WEAPON_FIGHTING,
    GREAT_WEAPON_MASTER,
    HEAVY_ARMOR_MASTER,
    HUMAN,
    ROGUE,
    SAVAGE_ATTACKER,
    SOLDIER,
    WAR_CASTER,
)
from weapon_fixtures import (
    BREASTPLATE,
    CHAIN_MAIL,
    DAGGER,
    GREATSWORD,
    LEATHER_ARMOR,
    LONGBOW,
    LONGSWORD,
    MAUL,
    PLATE_ARMOR,
    RAPIER,
)

BUILDS = Path(__file__).parents[2] / "builds"

CATALOGUE = Catalogue(
    classes={c.name: c for c in (FIGHTER, ROGUE)},
    species={s.name: s for s in (HUMAN, GOLIATH)},
    backgrounds={SOLDIER.name: SOLDIER},
    feats={
        f.name: f
        for f in (
            ABILITY_SCORE_IMPROVEMENT,
            BLESSED_WARRIOR,
            GREAT_WEAPON_FIGHTING,
            GREAT_WEAPON_MASTER,
            HEAVY_ARMOR_MASTER,
            SAVAGE_ATTACKER,
            WAR_CASTER,
        )
    },
    weapons={w.name: w for w in (DAGGER, GREATSWORD, LONGBOW, LONGSWORD, MAUL, RAPIER)},
    armour={a.name: a for a in (BREASTPLATE, CHAIN_MAIL, LEATHER_ARMOR, PLATE_ARMOR)},
)


def spec(**changes: Any) -> dict[str, Any]:
    """The level 5 fighter in `builds/`, with `changes` written over it; a
    change of `None` deletes the key."""
    written = yaml.safe_load((BUILDS / "fighter_gwm_l5.yaml").read_text())
    for key, value in changes.items():
        if value is None:
            written.pop(key, None)
        else:
            written[key] = value
    return dict(written)


def load(written: dict[str, Any]) -> Build:
    return load_build(parse_build(yaml.safe_dump(written)), CATALOGUE)


def fighter(**changes: Any) -> Character:
    return load(spec(**changes)).character(Cell(0, 0))


def refused(written: dict[str, Any], *fragments: str) -> None:
    with pytest.raises(BuildError) as raised:
        load(written)
    for fragment in fragments:
        assert fragment in str(raised.value)


ROGUE_L3: dict[str, Any] = yaml.safe_load((BUILDS / "rogue_l3.yaml").read_text())


def rogue(**changes: Any) -> Character:
    written = {**ROGUE_L3, **changes}
    return load(written).character(Cell(1, 0))


# -- the example build: every number derived ---------------------------------


def test_the_example_fighter_falls_out_of_its_choices() -> None:
    """Strength 15 + 2 (Soldier) = 17; Constitution 14 + 1 = 15, so +2.
    Hit points: 10 + 2 at level 1, then 4 levels of (6 + 2) = 44.
    AC: Chain Mail's 16, no Dexterity. Speed: Human 30, Strength 17 >= 13."""
    character = fighter()
    assert character.stats.abilities[Ability.STRENGTH] == 17
    assert character.stats.abilities[Ability.CONSTITUTION] == 15
    assert character.max_hp == 44
    assert character.armour_class == 16
    assert character.speed_ft == 30
    assert character.size is Size.MEDIUM
    assert character.proficiency_bonus == 3
    assert character.player_character
    assert character.feats == (
        "Savage Attacker",
        "Great Weapon Fighting",
        "Great Weapon Master",
    )
    assert character.stats.saving_throws == FIGHTER.saving_throws
    assert character.stats.skills == {
        "athletics",
        "intimidation",
        "perception",
        "survival",
    }
    assert character.subclass == "Champion"


def test_the_example_rogue_takes_the_default_policy() -> None:
    """Dexterity 15 + 2 = 17, +3; Leather Armor 11 + 3 = 14. Hit points:
    8 + 2, then 2 levels of (5 + 2) = 24."""
    build = load(dict(ROGUE_L3))
    character = build.character(Cell(0, 0))
    assert (character.armour_class, character.max_hp) == (14, 24)
    assert character.size is Size.SMALL
    assert build.apl == DEFAULT_APL
    assert [entry.action for entry in build.apl.entries] == [BEST_SCORED]


def test_each_character_is_a_fresh_creature() -> None:
    build = load(spec())
    one, two = build.character(Cell(0, 0)), build.character(Cell(1, 0))
    assert one is not two
    assert one.dispatcher is not two.dispatcher


def test_the_party_loads_its_members_and_compiles_its_retreat() -> None:
    party = load_party(BUILDS / "party.yaml", CATALOGUE)
    assert [m.spec.name for m in party.members] == ["Fighter (GWM)", "Rogue"]
    assert party.retreat_when is not None
    assert party.retreat_when.reads == frozenset({"allies.down"})


def test_a_retreat_condition_that_does_not_compile_fails_at_load(
    tmp_path: Path,
) -> None:
    (tmp_path / "party.yaml").write_text("members: []\nretreat_when: 'allies.dwn'\n")
    with pytest.raises(AplLoadError):
        load_party(tmp_path / "party.yaml", CATALOGUE)


# -- unknown names -----------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "fragments"),
    [
        ({"class": "Figher"}, ("unknown class 'Figher'", "'Fighter'")),
        ({"subclass": "Champoin"}, ("unknown subclass", "'Champion'")),
        ({"fighting_style": "Great Weapon Fightin"}, ("'Great Weapon Fighting'",)),
        (
            {"feats": [{"level": 4, "feat": "Great Weapon Mastr"}]},
            ("unknown feat", "'Great Weapon Master'"),
        ),
        ({"species": "Humna"}, ("unknown species", "'Human'")),
        ({"background": "Soldeir"}, ("unknown background", "'Soldier'")),
        ({"equipment": {"weapon": "Greatsord"}}, ("'Greatsword'",)),
        ({"equipment": {"armour": "Chainmail"}}, ("'Chain Mail'",)),
        ({"background_increases": {"strenght": 2, "constitution": 1}}, ("'strength'",)),
        ({"size": "medum"}, ("'medium'",)),
    ],
)
def test_an_unknown_name_fails_with_suggestions(
    changes: dict[str, Any], fragments: tuple[str, ...]
) -> None:
    refused(spec(**changes), *fragments)


def test_an_unknown_key_fails_at_load() -> None:
    refused(spec(feets=[]), "feets")


def test_an_unknown_apl_action_fails_at_load() -> None:
    with pytest.raises(AplLoadError):
        load(spec(apl=[{"action": "rage"}]))


# -- origin ------------------------------------------------------------------


def test_a_species_of_two_sizes_needs_one_chosen() -> None:
    refused(spec(size=None), "chooses its size")


def test_a_species_of_one_size_takes_it_and_its_speed() -> None:
    character = fighter(species="Goliath", size=None)
    assert (character.size, character.speed_ft) == (Size.MEDIUM, 35)


@pytest.mark.parametrize(
    ("increases", "fragment"),
    [
        ({"strength": 2, "wisdom": 1}, "not ['wis']"),
        ({"strength": 2, "constitution": 2}, "not (2, 2)"),
        ({"strength": 3}, "not (3,)"),
    ],
)
def test_background_increases_follow_its_abilities_and_patterns(
    increases: dict[str, int], fragment: str
) -> None:
    refused(spec(background_increases=increases), fragment)


def test_background_increases_may_be_one_one_one() -> None:
    character = fighter(
        background_increases={"strength": 1, "dexterity": 1, "constitution": 1}
    )
    assert character.stats.abilities[Ability.STRENGTH] == 16


@pytest.mark.parametrize(
    ("skills", "fragment"),
    [
        (["perception"], "chooses 2 different skills"),
        (["perception", "perception"], "chooses 2 different skills"),
        (["perception", "stealth"], "cannot choose ['stealth']"),
        (["perception", "athletics"], "already grants ['athletics']"),
        (["perception", "perceptoin"], "did you mean 'perception'"),
    ],
)
def test_class_skills_are_checked(skills: list[str], fragment: str) -> None:
    refused(spec(skills=skills), fragment)


def test_a_score_off_the_creation_range_is_refused() -> None:
    abilities = {**spec()["abilities"], "strength": 19}
    refused(spec(abilities=abilities), "3-18")


# -- what the levels owe -----------------------------------------------------


def test_a_subclass_is_owed_at_its_level_and_refused_before() -> None:
    refused(spec(subclass=None), "has a subclass")
    refused(spec(level=2, feats=[], subclass="Champion"), "at level 3")


def test_a_fighting_style_is_owed_and_must_be_one() -> None:
    refused(spec(fighting_style=None), "has a Fighting Style")
    refused(spec(fighting_style="Savage Attacker"), "not a Fighting Style feat")
    refused(spec(fighting_style="Blessed Warrior"), "a Paladin's Fighting Style")


def test_weapon_mastery_is_owed_and_checked() -> None:
    refused(spec(weapon_mastery=None), "names its masteries")
    refused(spec(weapon_mastery=["Maul", "Maul"]), "different weapons")
    refused({**ROGUE_L3, "weapon_mastery": ["Greatsword"]}, "not proficient")


def test_a_feat_is_owed_at_every_ability_score_improvement_level() -> None:
    refused(spec(feats=[]), "takes a feat at [4]")
    refused(spec(feats=[{"level": 3, "feat": "Ability Score Improvement"}]), "not 3")


def test_the_ability_score_improvement_feat_takes_its_increases() -> None:
    """Strength 17 + 2 = 19, and +1/+1 is the other pattern."""
    asi = {"level": 4, "feat": "Ability Score Improvement"}
    character = fighter(feats=[{**asi, "increases": {"strength": 2}}])
    assert character.stats.abilities[Ability.STRENGTH] == 19
    character = fighter(feats=[{**asi, "increases": {"strength": 1, "wisdom": 1}}])
    assert character.stats.abilities[Ability.WISDOM] == 13
    refused(spec(feats=[{**asi, "increases": {"strength": 3}}]), "by 2 or two")
    refused(spec(feats=[asi]), "by 2 or two")


def test_no_score_is_raised_above_twenty() -> None:
    """15 + 2 (Soldier) + 2 = 19 is allowed; 18 + 2 + 2 = 22 is not."""
    abilities = {**spec()["abilities"], "strength": 18}
    asi = {
        "level": 4,
        "feat": "Ability Score Improvement",
        "increases": {"strength": 2},
    }
    refused(spec(abilities=abilities, feats=[asi]), "above 20")


def test_a_feat_prerequisite_is_checked_at_the_level_it_is_taken() -> None:
    """Strength 11 + 1 (Soldier) = 12 < 13."""
    abilities = {**spec()["abilities"], "strength": 11}
    increases = {"strength": 1, "dexterity": 1, "constitution": 1}
    refused(
        spec(abilities=abilities, background_increases=increases),
        "Great Weapon Master's prerequisite",
    )


def test_armour_training_prerequisite() -> None:
    feats = [{"level": 4, "feat": "Heavy Armor Master"}]
    assert "Heavy Armor Master" in fighter(feats=feats).feats
    rogue_feats = {**ROGUE_L3, "level": 4, "feats": feats}
    refused(rogue_feats, "Heavy Armor Master's prerequisite")


def test_a_spellcasting_prerequisite_waits_on_w5a() -> None:
    refused(spec(feats=[{"level": 4, "feat": "War Caster"}]), "cannot be checked yet")


def test_a_feat_is_not_taken_twice_unless_repeatable() -> None:
    twice = [
        {"level": 4, "feat": "Great Weapon Master"},
        {"level": 6, "feat": "Great Weapon Master"},
    ]
    refused(spec(level=6, feats=twice), "not repeatable")
    refused(
        spec(feats=[{"level": 4, "feat": "Savage Attacker"}]),
        "Savage Attacker is taken twice",
    )


def test_another_feats_own_choice_is_unknown_until_registered() -> None:
    feats = [{"level": 4, "feat": "Great Weapon Master", "increases": {"strength": 1}}]
    refused(spec(feats=feats), "registers no choices")


# -- equipment ---------------------------------------------------------------


def test_heavy_armour_below_its_strength_costs_ten_feet() -> None:
    """Plate Armor needs Strength 15: 13 + 2 = 15 walks at 30, 12 + 2 = 14
    at 20. Chain Mail needs 13."""
    plate = {"weapon": "Greatsword", "armour": "Plate Armor"}
    assert fighter(equipment=plate).speed_ft == 30
    weak = {**spec()["abilities"], "strength": 12}
    assert fighter(equipment=plate, abilities=weak).speed_ft == 20
    asi = {"level": 4, "feat": "Ability Score Improvement"}
    asi_feats = [{**asi, "increases": {"wisdom": 2}}]
    slow = fighter(equipment=plate, abilities=weak, feats=asi_feats)
    assert (slow.speed_ft, slow.armour_class) == (20, 18)


def test_medium_armour_caps_dexterity() -> None:
    """Breastplate 14 + Dexterity 13's +1."""
    kit = {"weapon": "Greatsword", "armour": "Breastplate"}
    assert fighter(equipment=kit).armour_class == 15


def test_a_versatile_weapon_in_two_hands_rolls_its_two_handed_die() -> None:
    one = fighter(equipment={"weapon": "Longsword", "shield": True})
    two = fighter(equipment={"weapon": "Longsword", "two_handed": True})
    assert one.weapon is not None and one.weapon.damage == Dice(1, 8)
    assert two.weapon is not None and two.weapon.damage == Dice(1, 10)
    assert (one.armour_class, two.armour_class) == (13, 11)


@pytest.mark.parametrize(
    ("kit", "fragment"),
    [
        ({"weapon": "Greatsword", "shield": True}, "Shield leaves one hand"),
        ({"weapon": "Longsword", "two_handed": True, "shield": True}, "one hand"),
        ({"weapon": "Rapier", "two_handed": True}, "not held in two hands"),
        ({"two_handed": True}, "nothing is held"),
    ],
)
def test_hands_are_counted(kit: dict[str, Any], fragment: str) -> None:
    refused(spec(equipment=kit), fragment)


def test_untrained_armour_and_shields_are_refused() -> None:
    refused({**ROGUE_L3, "equipment": {"armour": "Chain Mail"}}, "not trained")
    refused({**ROGUE_L3, "equipment": {"shield": True}}, "Shields")


# -- the engine reads what the build says ------------------------------------


def test_the_proficiency_bonus_is_added_only_with_a_trained_weapon() -> None:
    """Against the fighter's AC 16, a natural 15 with a Greatsword: the
    untrained rogue adds Strength 8's -1 only, 14, a miss; with the bonus
    it would be 16, a hit. With its Rapier: 11 + Dexterity 3 + 2 = 16."""
    target = fighter()
    clumsy = rogue(equipment={"weapon": "Greatsword"})
    assert GREATSWORD.name not in (clumsy.stats.trained_weapons or ())
    miss = resolve_attack(clumsy, target, rng=ScriptedRng([15]))
    assert miss.outcome is Outcome.MISS
    deft = rogue()
    hit = resolve_attack(deft, target, rng=ScriptedRng([11, 1]))
    assert hit.outcome is Outcome.HIT


def test_a_creature_with_no_training_list_is_proficient_with_everything() -> None:
    stats = StatBlock(abilities=dict.fromkeys(Ability, 10), max_hp=10)
    assert stats.trained_weapons is None
    creature = Creature(name="m", stats=stats, position=Cell(1, 0), weapon=GREATSWORD)
    target = fighter()
    # 14 + 0 + 2 = 16 against 16.
    hit = resolve_attack(creature, target, rng=ScriptedRng([14, 1, 1]))
    assert hit.outcome is Outcome.HIT


def test_a_class_save_adds_the_proficiency_bonus() -> None:
    """Strength 17's +3 and the bonus +3; Wisdom 12's +1 alone."""
    character = fighter()
    assert save_bonus(character, Ability.STRENGTH) == 6
    assert save_bonus(character, Ability.CONSTITUTION) == 5
    assert save_bonus(character, Ability.WISDOM) == 1


def test_a_medicine_proficiency_adds_to_the_stabilising_check() -> None:
    """Wisdom 13's +1 and the bonus +2: a natural 7 makes 10. Without the
    proficiency it is 8."""
    victim = fighter()
    victim.hp = 0
    medic = rogue(skills=["acrobatics", "insight", "perception", "stealth"])
    assert "medicine" not in medic.stats.skills
    assert stabilize(medic, victim, rng=ScriptedRng([7])) is False
    # A Rogue cannot choose Medicine; a stat block can say it directly.
    trained = Creature(
        name="medic",
        stats=replace(medic.stats, skills=frozenset({"medicine"})),
        position=Cell(2, 0),
    )
    assert stabilize(trained, victim, rng=ScriptedRng([7])) is True


# -- arming -------------------------------------------------------------------


def test_arming_binds_the_gate_once_and_attaches_opportunity_attacks() -> None:
    build = load(spec())
    character = build.character(Cell(0, 0))
    world = World()
    world.seat(character, "party")
    bound = arm_character(character, build.apl, world, rng=ScriptedRng([]))
    assert bound.namespace is not None
    assert any(
        isinstance(source, OpportunityAttacks)
        for source in character.dispatcher.sources()
    )
    with pytest.raises(EngineAssemblyError):
        arm_character(character, build.apl, world, rng=ScriptedRng([]))
