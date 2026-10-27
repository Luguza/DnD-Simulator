"""The creature contract: `Creature`, `StatBlock`, `Armour` and `Weapon`.

- An incomplete stat block is refused as it is built.
- AC is derived from equipment and cannot be typed in.
- A weapon carries dice, never a modifier.
- A `StatBlock` is frozen and shared; hit points, position and the dispatch
  table are the creature's own.
- A creature owns its dispatch table and refuses another creature's handlers.
- A creature has no `take_damage()`.

`docs/decisions.md` § w0g2, § w1a.
"""

from dataclasses import fields
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.dice import Dice
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.timeline import TimeKey, TurnPhase
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.stats import GREATSWORD, UNARMOURED, Armour, StatBlock, Weapon
from dndsim.hooks.contexts import Combatant, TurnContext
from dndsim.hooks.contexts import Weapon as WeaponProtocol
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_TURN_END, ON_TURN_START
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import CRITICAL_HIT_THRESHOLD
from dndsim.rules.enums import Ability, DamageType, Mastery, Size, WeaponProperty

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))
"""Six deliberate 10s."""

PIKE: Final = Weapon(
    name="Pike",
    damage=Dice(1, 10),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.HEAVY, WeaponProperty.REACH}),
    reach_ft=10,
)
"""A Reach weapon whose reach is not 5."""


def stats(*, level: int = 1, max_hp: int = 20, **scores: int) -> StatBlock:
    """A stat block with every ability score named, so a test that cares about
    one of them changes only that one."""
    return StatBlock(
        abilities={**SCORES, **{Ability(key): value for key, value in scores.items()}},
        max_hp=max_hp,
        level=level,
    )


def creature(name: str = "fighter", *, max_hp: int = 20) -> Creature:
    return Creature(name=name, stats=stats(max_hp=max_hp), position=Cell(0, 0))


def protocol_members(protocol: type) -> frozenset[str]:
    """The names a `Protocol` declares."""
    return frozenset(name for name in vars(protocol) if not name.startswith("_"))


# --- The protocols a context carries ----------------------------------------


def test_a_creature_is_the_combatant_a_context_carries() -> None:
    """`Creature` satisfies `Combatant`: mypy checks the annotation, and the
    membership assertion fails if the protocol grows a member `Creature` lacks
    (`docs/decisions.md` § w0f)."""
    combatant: Combatant = creature(name="rogue")
    assert protocol_members(Combatant) == {
        "name",
        "initiative",
        "hp",
        "max_hp",
        "temp_hp",
    }
    assert (combatant.name, combatant.initiative) == ("rogue", UNSEATED)
    assert (combatant.hp, combatant.max_hp, combatant.temp_hp) == (20, 20, 0)


def test_the_defenders_hit_points_are_readable_from_inside_an_event() -> None:
    """A `Combatant` exposes `hp`, `temp_hp` and `max_hp`; Bloodied reads hit
    points alone, whatever temporary pool is stacked on top
    (`docs/decisions.md` § w1a)."""
    wounded = creature(max_hp=30)
    wounded.hp = 11
    wounded.temp_hp = 4

    defender: Combatant = wounded
    assert defender.hp * 2 <= defender.max_hp, "Bloodied at 11 of 30"
    assert defender.hp + defender.temp_hp == 15, "the pool a hit is spent from"


def test_a_real_weapon_is_the_weapon_a_context_carries() -> None:
    """`creatures/stats.Weapon` satisfies the six-member `Weapon` protocol
    (`docs/decisions.md` § w1f)."""
    weapon: WeaponProtocol = GREATSWORD
    assert protocol_members(WeaponProtocol) == {
        "name",
        "damage",
        "damage_type",
        "mastery",
        "reach_ft",
        "has",
    }
    assert weapon.name == "Greatsword"
    assert weapon.damage == Dice(2, 6)
    assert weapon.damage_type is DamageType.SLASHING
    assert weapon.mastery is Mastery.GRAZE
    assert weapon.reach_ft == 5
    assert weapon.has(WeaponProperty.HEAVY)
    assert not weapon.has(WeaponProperty.FINESSE)


# --- A stat block ------------------------------------------------------------


def test_a_missing_ability_score_is_refused_rather_than_defaulted() -> None:
    """A stat block naming five abilities is refused."""
    partial: dict[Ability, int] = {
        ability: 14 for ability in Ability if ability is not Ability.CHARISMA
    }
    with pytest.raises(ValueError, match="all six ability scores"):
        StatBlock(abilities=partial, max_hp=20)


def test_the_refusal_names_what_is_absent() -> None:
    """The error names which scores are missing."""
    with pytest.raises(ValueError, match=r"\['con', 'wis'\]"):
        StatBlock(
            abilities={
                ability: 10
                for ability in Ability
                if ability not in {Ability.CONSTITUTION, Ability.WISDOM}
            },
            max_hp=20,
        )


def test_the_two_numbers_nobody_can_guess_have_no_defaults() -> None:
    """Omitting ability scores or hit points is a `TypeError` at construction
    (`docs/decisions.md` § w0g2)."""
    with pytest.raises(TypeError, match="abilities"):
        StatBlock()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="max_hp"):
        StatBlock(abilities=SCORES)  # type: ignore[call-arg]


def test_an_armour_class_cannot_be_typed_onto_a_stat_block() -> None:
    """`armour_class` is not a `StatBlock` keyword (`docs/decisions.md` § w1a)."""
    assert "armour_class" not in {field.name for field in fields(StatBlock)}
    with pytest.raises(TypeError, match="armour_class"):
        StatBlock(abilities=SCORES, max_hp=20, armour_class=15)  # type: ignore[call-arg]


def test_the_three_that_the_rules_themselves_default_still_do() -> None:
    """Level defaults to 1, speed to 30 feet and size to Medium."""
    block = StatBlock(abilities=SCORES, max_hp=20)
    assert (block.level, block.speed_ft, block.size) == (1, 30, Size.MEDIUM)


def test_a_creature_has_at_least_one_hit_point() -> None:
    """A stat block with 0 maximum hit points is refused."""
    with pytest.raises(ValueError, match="at least 1 hit point"):
        stats(max_hp=0)
    with pytest.raises(ValueError, match="at least 1 hit point"):
        stats(max_hp=-12)


def test_the_proficiency_bonus_is_the_xphb_table() -> None:
    """+2 through level 4, +3 from 5, +4 from 9, +6 at 20."""
    assert stats(level=1).proficiency_bonus == 2
    assert stats(level=4).proficiency_bonus == 2
    assert stats(level=5).proficiency_bonus == 3
    assert stats(level=9).proficiency_bonus == 4
    assert stats(level=20).proficiency_bonus == 6


def test_a_level_off_the_table_is_refused_at_construction() -> None:
    """A level of 0 or 21 raises when the stat block is built, not when a
    proficiency bonus is first read."""
    with pytest.raises(ValueError, match="outside 1-20"):
        stats(level=21)
    with pytest.raises(ValueError, match="outside 1-20"):
        stats(level=0)


def test_an_ability_modifier_rounds_down_including_below_ten() -> None:
    """`(score - 10) // 2`, floored, which is what the XPHB table says in both
    directions: a 7 is -2 and a 1 is -5, not -1 and -4."""
    expected = {1: -5, 3: -4, 7: -2, 8: -1, 10: 0, 11: 0, 15: 2, 20: 5}
    for score, modifier in expected.items():
        block = StatBlock(abilities={**SCORES, Ability.STRENGTH: score}, max_hp=20)
        assert block.modifier(Ability.STRENGTH) == modifier, score


def test_the_derived_numbers_are_settled_once_and_cannot_go_stale() -> None:
    """The modifiers and proficiency bonus are settled at construction, over a
    read-only copy of the scores that the caller's dict cannot change
    (`docs/decisions.md` § w1a)."""
    scores = dict(SCORES) | {Ability.STRENGTH: 18}
    block = StatBlock(abilities=scores, max_hp=20, level=5)
    scores[Ability.STRENGTH] = 8

    assert block.proficiency_bonus == 3
    assert block.modifier(Ability.STRENGTH) == 4
    assert block.modifiers[Ability.STRENGTH] == 4
    with pytest.raises(TypeError):
        block.modifiers[Ability.STRENGTH] = 0  # type: ignore[index]


def test_a_stat_block_is_frozen_and_shareable() -> None:
    """A stat block is frozen and can be shared between creatures."""
    shared = stats(max_hp=30)
    first = Creature(name="dummy 1", stats=shared, position=Cell(0, 0))
    second = Creature(name="dummy 2", stats=shared, position=Cell(1, 0))
    assert first.stats is second.stats is shared
    with pytest.raises(AttributeError):
        shared.max_hp = 40  # type: ignore[misc]


# --- Armour, and the AC derived from it --------------------------------------


def test_a_creature_wearing_nothing_is_ten_plus_dexterity() -> None:
    """A creature wearing no armour has an AC of 10 plus its Dexterity
    modifier (XPHB, "Armor Class")."""
    assert creature().armour_class == 10
    nimble = Creature(name="monk", stats=stats(dex=18), position=Cell(0, 0))
    assert nimble.armour is UNARMOURED
    assert nimble.armour_class == 14


def test_light_armour_takes_all_of_dexterity_and_medium_caps_it_at_two() -> None:
    """Studded Leather is 12 plus the whole Dexterity modifier; Half Plate is 15
    plus a Dexterity modifier capped at +2. A Dexterity of 18 is +4."""
    block = stats(dex=18)
    assert Armour.light("Studded Leather", 12).armour_class(block) == 16
    assert Armour.medium("Half Plate", 15).armour_class(block) == 17


def test_heavy_armour_leaves_dexterity_out_rather_than_capping_it_at_zero() -> None:
    """Plate with a Dexterity of 8 is AC 18, not 17: heavy armour adds no
    Dexterity at all (`docs/decisions.md` § w1a)."""
    clumsy = stats(dex=8)
    assert Armour.heavy("Plate", 18).armour_class(clumsy) == 18
    assert Armour.light("Leather", 11).armour_class(clumsy) == 10


def test_a_shield_is_two_whatever_is_underneath_it() -> None:
    """A shield adds +2 over any armour."""
    fighter = Creature(
        name="fighter",
        stats=stats(dex=12),
        position=Cell(0, 0),
        armour=Armour.heavy("Chain Mail", 16),
    )
    assert fighter.armour_class == 16
    fighter.shield = True
    assert fighter.armour_class == 18
    fighter.armour = Armour.medium("Breastplate", 14)
    assert fighter.armour_class == 17


def test_unarmored_defense_is_the_unarmoured_shape_with_one_more_modifier() -> None:
    """The Barbarian's is 10 plus Dexterity and Constitution; the Monk's
    substitutes Wisdom. Both are the unarmoured shape with a second ability."""
    barbarian = stats(dex=14, con=16)
    assert Armour.unarmoured(Ability.CONSTITUTION).armour_class(barbarian) == 15
    monk = stats(dex=16, wis=14)
    assert Armour.unarmoured(Ability.WISDOM).armour_class(monk) == 15


def test_a_printed_armour_class_is_the_one_number_somebody_may_type() -> None:
    """A printed AC is the heavy shape: it adds no Dexterity
    (`docs/decisions.md` § w1a)."""
    dummy = Creature(
        name="dummy",
        stats=stats(dex=18),
        position=Cell(0, 0),
        armour=Armour.natural(15),
    )
    assert dummy.armour_class == 15


def test_an_armour_class_is_derived_and_has_nowhere_to_be_assigned() -> None:
    """`armour_class` has no setter on either class."""
    fighter = creature()
    with pytest.raises(AttributeError):
        fighter.armour_class = 22  # type: ignore[misc]


def test_swapping_the_stat_block_out_from_under_a_creature_is_refused() -> None:
    """`Creature.stats` is read-only (`docs/decisions.md` § w1a)."""
    fighter = creature()
    with pytest.raises(AttributeError):
        fighter.stats = stats(level=9)  # type: ignore[misc]


def test_armour_is_a_frozen_value_object() -> None:
    """Shared by every creature wearing one, exactly like a weapon."""
    plate = Armour.heavy("Plate", 18)
    with pytest.raises(AttributeError):
        plate.base_ac = 20  # type: ignore[misc]
    assert str(plate) == "Plate"


# --- A weapon ----------------------------------------------------------------


def test_a_weapon_carries_dice_and_has_nowhere_to_put_a_modifier() -> None:
    """`Weapon.damage` is a `Dice` with exactly `count` and `sides`, so a flat
    modifier cannot be stored on the weapon."""
    assert GREATSWORD.damage == Dice(2, 6)
    assert {field.name for field in fields(Dice)} == {"count", "sides"}
    assert not hasattr(GREATSWORD.damage, "flat")


def test_a_weapon_is_a_frozen_value_object() -> None:
    """Shared by every creature that wields one, for the life of the run."""
    assert GREATSWORD.damage_type is DamageType.SLASHING
    assert GREATSWORD.mastery is Mastery.GRAZE
    with pytest.raises(AttributeError):
        GREATSWORD.reach_ft = 10  # type: ignore[misc]


def test_a_weapon_answers_for_the_properties_it_has() -> None:
    """`has()` is False, not a KeyError, for a property a weapon lacks."""
    assert GREATSWORD.has(WeaponProperty.HEAVY)
    assert GREATSWORD.has(WeaponProperty.TWO_HANDED)
    assert not GREATSWORD.has(WeaponProperty.FINESSE)


# --- A creature --------------------------------------------------------------


def test_a_creature_must_be_told_where_it_stands() -> None:
    """`position` has no default (`docs/decisions.md` § w0g2)."""
    with pytest.raises(TypeError, match="position"):
        Creature(name="nowhere", stats=stats())  # type: ignore[call-arg]


def test_a_creature_starts_undamaged_and_with_no_temporary_hit_points() -> None:
    fighter = creature(max_hp=44)
    assert (fighter.hp, fighter.max_hp, fighter.temp_hp) == (44, 44, 0)
    assert fighter.is_alive


def test_being_alive_is_not_dying_and_being_down_is_hit_points() -> None:
    """`is_alive` is whether the creature has died, and a creature at 0 hit
    points making death saving throws has not; `is_down` is whether its hit
    points are 0, whatever its temporary hit points
    (`docs/decisions.md` § w1j)."""
    fighter = creature(max_hp=10)
    fighter.hp = 1
    on_one = (fighter.is_alive, fighter.is_down)
    fighter.hp = 0
    at_zero = (fighter.is_alive, fighter.is_down)
    fighter.temp_hp = 5
    at_zero_with_temporary = (fighter.is_alive, fighter.is_down)
    fighter.die()
    dead = (fighter.is_alive, fighter.is_down)
    assert (on_one, at_zero, at_zero_with_temporary, dead) == (
        (True, False),
        (True, True),
        (True, True),
        (False, True),
    )


def test_a_creature_starts_alive_undying_and_not_a_player_character() -> None:
    """`player_character` is a constructor keyword, defaulting to the creature
    that dies at 0."""
    subject = creature()
    pc = Creature(name="pc", stats=stats(), position=Cell(0, 0), player_character=True)
    assert (
        subject.player_character,
        subject.dead,
        subject.stable,
        subject.death_save_successes,
        subject.death_save_failures,
    ) == (False, False, False, 0, 0)
    assert pc.player_character


def tallies(subject: Creature) -> tuple[bool, bool, int, int]:
    return (
        subject.dead,
        subject.stable,
        subject.death_save_successes,
        subject.death_save_failures,
    )


@pytest.mark.parametrize(
    "field", ["dead", "stable", "death_save_successes", "death_save_failures"]
)
def test_the_dying_state_cannot_be_assigned(field: str) -> None:
    """It moves only through the six methods, which keep its invariants."""
    with pytest.raises(AttributeError):
        setattr(creature(), field, 1)


def test_the_third_failure_kills() -> None:
    """One, then two at once: three (XPHB "Dropping to 0 Hit Points")."""
    subject = creature()
    subject.fail_death_save()
    alive_on_one = tallies(subject)
    subject.fail_death_save(2)
    assert alive_on_one == (False, False, 0, 1)
    assert subject.dead and not subject.is_alive
    assert subject.death_save_failures == 3


def test_the_third_success_stabilises_and_resets_both_tallies() -> None:
    subject = creature()
    subject.fail_death_save()
    made = [subject.succeed_death_save() for _ in range(3)]
    assert made == [False, False, True]
    assert tallies(subject) == (False, True, 0, 0)


def test_a_failure_ends_stable() -> None:
    subject = creature()
    subject.stabilize()
    subject.fail_death_save()
    assert tallies(subject) == (False, False, 0, 1)


def test_reset_death_saves_resets_both_tallies_and_stable() -> None:
    subject = creature()
    subject.succeed_death_save()
    subject.fail_death_save(2)
    subject.reset_death_saves()
    reset = tallies(subject)
    subject.stabilize()
    subject.reset_death_saves()
    assert (reset, tallies(subject)) == ((False, False, 0, 0),) * 2


def test_death_ends_stable_and_revive_clears_death() -> None:
    subject = creature()
    subject.stabilize()
    subject.die()
    dead = tallies(subject)
    subject.revive()
    assert dead == (True, False, 0, 0)
    assert tallies(subject) == (False, False, 0, 0)
    assert subject.is_alive


def test_revive_after_three_failures_clears_the_tallies() -> None:
    subject = creature()
    subject.fail_death_save(3)
    subject.revive()
    assert tallies(subject) == (False, False, 0, 0)


def test_an_unarmed_creature_threatens_five_feet() -> None:
    assert creature().reach_ft == 5


def test_reach_comes_from_the_weapon() -> None:
    """A greatsword threatens the eight squares around its wielder; a pike
    threatens the ring beyond them."""
    fighter = creature()
    fighter.weapon = GREATSWORD
    assert fighter.reach_ft == 5
    fighter.weapon = PIKE
    assert fighter.reach_ft == 10


def test_what_the_stat_block_knows_the_creature_answers_for() -> None:
    """The creature answers for its stat block's numbers rather than holding a
    copy, and derives its AC from its equipment."""
    fighter = Creature(
        name="fighter",
        stats=StatBlock(
            abilities={**SCORES, Ability.STRENGTH: 18},
            level=5,
            max_hp=44,
            speed_ft=25,
            size=Size.SMALL,
        ),
        position=Cell(2, 2),
        armour=Armour.heavy("Plate", 18),
    )
    assert fighter.armour_class == 18
    assert fighter.speed_ft == 25
    assert fighter.size is Size.SMALL
    assert fighter.proficiency_bonus == 3
    assert fighter.modifier(Ability.STRENGTH) == 4


def test_where_a_creature_starts_critting_is_a_stat_it_carries() -> None:
    """A natural 20 by default, and 19 for a Champion: the threshold is the
    creature's, not one attack's (`docs/decisions.md` § w1a)."""
    fighter = creature()
    assert fighter.crit_threshold == CRITICAL_HIT_THRESHOLD == 20
    fighter.crit_threshold = 19
    assert fighter.crit_threshold == 19


def test_a_creature_owns_the_economy_its_dispatcher_spends_from() -> None:
    """Construction binds the creature's own economy to its dispatcher, which
    refuses a second one (`docs/decisions.md` § w1a)."""
    fighter = creature()
    assert isinstance(fighter.economy, ActionEconomy)
    with pytest.raises(EngineAssemblyError, match="already has an action economy"):
        fighter.dispatcher.bind(economy=ActionEconomy())


def test_a_creature_does_not_know_how_to_take_damage() -> None:
    """`Creature` has no `take_damage()` (`docs/decisions.md` § w0g2)."""
    assert not hasattr(Creature, "take_damage")
    assert not hasattr(Creature, "heal")


def test_a_creature_is_slotted_so_a_typo_is_not_a_new_field() -> None:
    """Assigning a misspelt field such as `hitpoints` raises."""
    with pytest.raises(AttributeError):
        creature().hitpoints = 3  # type: ignore[attr-defined]


# --- The hook pipeline hangs off the creature --------------------------------


class TurnCounter(HookSource):
    """The narrowest useful source: it records that it was called."""

    __slots__ = ("calls",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.calls: list[str] = []

    @hook("on_turn_start")
    def note(self, ctx: TurnContext) -> R:
        self.calls.append(ctx.creature.name)
        return R.APPLIED


def turn_context(subject: Creature) -> TurnContext:
    return TurnContext(
        creature=subject, time=TimeKey(1, subject.initiative, TurnPhase.START)
    )


def test_a_creature_dispatches_to_what_is_attached_to_it() -> None:
    fighter = creature()
    counter = TurnCounter(fighter)
    fighter.attach(counter)
    fighter.dispatch(ON_TURN_START, turn_context(fighter))
    assert counter.calls == ["fighter"]


def test_a_hook_nothing_listens_to_is_a_silent_no_op() -> None:
    """Dispatching a hook with no handlers does not raise (DESIGN.md § Hook
    system)."""
    fighter = creature()
    fighter.attach(TurnCounter(fighter))
    ctx = turn_context(fighter)
    fighter.dispatch(ON_TURN_END, ctx)
    assert not ctx.vetoed


def test_attaching_somebody_elses_feature_is_refused() -> None:
    """Attaching a source owned by another creature raises at attach time."""
    fighter = creature(name="fighter")
    rogue = creature(name="rogue")
    with pytest.raises(ValueError, match="belongs to fighter"):
        rogue.attach(TurnCounter(fighter))


def test_each_creature_owns_its_own_table() -> None:
    """A source attached to one creature is not in another's table
    (DESIGN.md § Levers)."""
    fighter = creature(name="fighter")
    rogue = creature(name="rogue")
    fighter.attach(TurnCounter(fighter))
    assert fighter.dispatcher is not rogue.dispatcher
    assert rogue.dispatcher.handlers(ON_TURN_START) == ()


def test_a_detached_source_stops_being_called() -> None:
    """An effect expiring or an item unequipped: the handlers go with it."""
    fighter = creature()
    counter = TurnCounter(fighter)
    fighter.attach(counter)
    fighter.detach(counter)
    fighter.dispatch(ON_TURN_START, turn_context(fighter))
    assert counter.calls == []
