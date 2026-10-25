"""The w0g2 creature contract: the numbers, where they live, and what is attached.

`Creature` and `StatBlock` are the first cut of the objects every later wave
hangs something off, so the properties below are the ones that would be
expensive to change once ~630 features assume them:

- **A stat block that is missing something says so, as it is built.** A
  defaulted ability score or hit point total is a number nobody chose that
  silently changes every save and every attack made with it, so neither has a
  default and a partial ability map is refused; a level off the table is refused
  in the same breath rather than a fight later (DESIGN.md § Fail at the earliest
  moment).
- **AC is derived from equipment and cannot be typed in** (w1a). There is no
  `armour_class` on a stat block to set and no setter on the creature: what a
  creature is wearing decides it, which is what DESIGN.md § Reconnaissance is
  most emphatic about. The four shapes the rules have — heavy, medium, light and
  the Unarmored Defense variants — are one value object with a list of ability
  modifiers and their caps.
- **A weapon carries dice, never a modifier.** `Weapon.damage` is a `Dice` pool
  and `Dice` has nowhere to put a flat bonus, which is what makes "dice double
  on a crit and flat never does" structural rather than remembered: the
  wielder's ability modifier physically cannot ride along on the weapon.
- **The split between frozen and mutable is the build/run split.** A `StatBlock`
  is what a build *is* and is shared; hit points, position and the dispatch
  table are the creature's own.
- **A creature owns its dispatch table** and refuses handlers that belong to
  somebody else, because `self.owner` inside a handler being the wrong creature
  is the one mistake hook `Scope` exists to make impossible.
- **The damage order is not here.** A `take_damage()` method on this class would
  be a second place the resolution order could be written, and the second place
  is the one that gets it wrong (DESIGN.md § Structural prevention).

Sources: XPHB (2024) "Proficiency Bonus" and ability-modifier tables; DESIGN.md
§ Structural prevention over testing, § Hook system, § Levers; ROADMAP w0g2, w1a.
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
"""Six deliberate 10s. There is no default to fall back on: `StatBlock`
requires the whole map, so every test here says what it is scoring."""

PIKE: Final = Weapon(
    name="Pike",
    damage=Dice(1, 10),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.HEAVY, WeaponProperty.REACH}),
    reach_ft=10,
)
"""A Reach weapon built here rather than imported: `content/` (w2a) is what will
supply the 38 XPHB weapons, and this test needs one whose reach is not 5."""


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
    """The names a `Protocol` declares. Pinned rather than eyeballed, so that
    widening one fails here — a new member is a new promise somebody has to
    check the real class against."""
    return frozenset(name for name in vars(protocol) if not name.startswith("_"))


# --- The protocol debt w0f left to this task --------------------------------


def test_a_creature_is_the_combatant_a_context_carries() -> None:
    """`hooks/contexts.py` declares `Combatant` instead of importing `Creature`,
    because `hooks/` sits below `creatures/` and the dependency points one way
    only. w0f deferred the proof to this task: the annotation is what mypy
    checks, and the membership assertion is what fails loudly if the protocol
    grows a member `Creature` does not have."""
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
    """w1a's widening, and the reason for it: "half its hit points or fewer" is
    a condition dozens of features are written around, and through W0 no
    `on_hit` handler could ask it — `w0i`'s own bloodied rider had to
    interrogate the damage bag instead (ROADMAP § The W0 debt register, #2).
    The three numbers are one promise: what `engine/damage.py` spends, in the
    order it spends it, each declared read-only on the protocol, because a
    handler that could set hit points would be a second place the resolution
    order lives.

    **Bloodied is hit points alone.** Temporary hit points are a buffer in front
    of hit points rather than hit points (XPHB, Rules Glossary), so a creature
    at 11 of 30 is Bloodied whatever temporary pool is stacked on top of it.
    The sum of the two is the pool the *next hit* comes out of, which is a
    different question and the reason both numbers are exposed."""
    wounded = creature(max_hp=30)
    wounded.hp = 11
    wounded.temp_hp = 4

    defender: Combatant = wounded
    assert defender.hp * 2 <= defender.max_hp, "Bloodied at 11 of 30"
    assert defender.hp + defender.temp_hp == 15, "the pool a hit is spent from"


def test_a_real_weapon_is_the_weapon_a_context_carries() -> None:
    """`w1f`'s widening, and the reason for it: through W0 a context knew a
    weapon only by its name, so Great Weapon Master could not ask whether it was
    holding a Heavy weapon and no mastery could recognise its own (ROADMAP § The
    W0 debt register, #3). What the protocol promises now is what a handler has
    to ask — the dice and type it will add to, the mastery it carries, how far it
    reaches, and one property at a time — and `creatures/stats.Weapon` is what
    answers it."""
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
    """No silent 10: a build that names five abilities has lost one somewhere
    between the YAML and here, and a defaulted score would change every save and
    every attack made with it without ever being noticed."""
    partial: dict[Ability, int] = {
        ability: 14 for ability in Ability if ability is not Ability.CHARISMA
    }
    with pytest.raises(ValueError, match="all six ability scores"):
        StatBlock(abilities=partial, max_hp=20)


def test_the_refusal_names_what_is_absent() -> None:
    """The error is read by whoever wrote the build, so it says which scores are
    missing rather than that something is."""
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
    """Ability scores and hit points must both be given: a defaulted 10 is a
    number nobody chose that silently changes every save and every attack made
    with it, which is the same argument that refuses a *partial* ability map.
    Omitting one is a `TypeError` at the point of construction — the same
    failure as omitting it from the build."""
    with pytest.raises(TypeError, match="abilities"):
        StatBlock()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="max_hp"):
        StatBlock(abilities=SCORES)  # type: ignore[call-arg]


def test_an_armour_class_cannot_be_typed_onto_a_stat_block() -> None:
    """The W0 literal is *gone*, not defaulted (ROADMAP § The W0 debt register,
    #1). A stat block that still accepted the keyword would keep accepting it
    from a build YAML, which is precisely the number DESIGN.md refuses to let
    anybody choose by hand."""
    assert "armour_class" not in {field.name for field in fields(StatBlock)}
    with pytest.raises(TypeError, match="armour_class"):
        StatBlock(abilities=SCORES, max_hp=20, armour_class=15)  # type: ignore[call-arg]


def test_the_three_that_the_rules_themselves_default_still_do() -> None:
    """Level 1 is the bottom of the table, and 30 feet and Medium are what the
    rules default to — none of the three is a guess about this creature."""
    block = StatBlock(abilities=SCORES, max_hp=20)
    assert (block.level, block.speed_ft, block.size) == (1, 30, Size.MEDIUM)


def test_a_creature_has_at_least_one_hit_point() -> None:
    """A stat block with 0 maximum hit points is a creature that is dead before
    initiative, which is a build error and not a fight."""
    with pytest.raises(ValueError, match="at least 1 hit point"):
        stats(max_hp=0)
    with pytest.raises(ValueError, match="at least 1 hit point"):
        stats(max_hp=-12)


def test_the_proficiency_bonus_is_the_xphb_table() -> None:
    """+2 through level 4, +3 from 5, +4 from 9, +6 at 20 — the level-5 row
    being the one M1's Fighter is scored against."""
    assert stats(level=1).proficiency_bonus == 2
    assert stats(level=4).proficiency_bonus == 2
    assert stats(level=5).proficiency_bonus == 3
    assert stats(level=9).proficiency_bonus == 4
    assert stats(level=20).proficiency_bonus == 6


def test_a_level_off_the_table_is_refused_at_construction() -> None:
    """The tables stop at 20 and so does the simulator. Note *where* it is
    raised: in `__post_init__`, beside the ability-score check, so a build with
    a level of 21 or 0 fails as it is read rather than a fight later and
    attributed to whoever first asked for a proficiency bonus."""
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
    """`proficiency_bonus` walked the XPHB table and `modifier()` divided on
    every read, three times per attack resolution — ~1.5% of the budget
    (docs/decisions.md § w0i, ROADMAP § The W0 debt register, #4). Both are pure
    functions of a frozen stat block, so both are settled at construction; the
    scores are copied behind a read-only view first, so that a caller still
    holding the dict it passed in cannot leave them describing a creature that
    no longer exists."""
    scores = dict(SCORES) | {Ability.STRENGTH: 18}
    block = StatBlock(abilities=scores, max_hp=20, level=5)
    scores[Ability.STRENGTH] = 8

    assert block.proficiency_bonus == 3
    assert block.modifier(Ability.STRENGTH) == 4
    assert block.modifiers[Ability.STRENGTH] == 4
    with pytest.raises(TypeError):
        block.modifiers[Ability.STRENGTH] = 0  # type: ignore[index]


def test_a_stat_block_is_frozen_and_shareable() -> None:
    """One stat block is what four identical dummies in an encounter are; what
    changes during the fight lives on the creature instead."""
    shared = stats(max_hp=30)
    first = Creature(name="dummy 1", stats=shared, position=Cell(0, 0))
    second = Creature(name="dummy 2", stats=shared, position=Cell(1, 0))
    assert first.stats is second.stats is shared
    with pytest.raises(AttributeError):
        shared.max_hp = 40  # type: ignore[misc]


# --- Armour, and the AC derived from it --------------------------------------


def test_a_creature_wearing_nothing_is_ten_plus_dexterity() -> None:
    """The rule every other shape restates: a creature wearing no armour has an
    AC of 10 plus its Dexterity modifier (XPHB, "Armor Class"). It is a default
    in the sense that a speed of 30 feet is one — what the rules say about an
    unequipped creature, not a stand-in for a number somebody forgot."""
    assert creature().armour_class == 10
    nimble = Creature(name="monk", stats=stats(dex=18), position=Cell(0, 0))
    assert nimble.armour is UNARMOURED
    assert nimble.armour_class == 14


def test_light_armour_takes_all_of_dexterity_and_medium_caps_it_at_two() -> None:
    """Studded Leather is 12 plus the whole Dexterity modifier; Half Plate is 15
    plus a Dexterity modifier capped at +2. A Dexterity of 18 is +4, so the cap
    is the whole difference between the two rows and is worth a test that would
    notice it going."""
    block = stats(dex=18)
    assert Armour.light("Studded Leather", 12).armour_class(block) == 16
    assert Armour.medium("Half Plate", 15).armour_class(block) == 17


def test_heavy_armour_leaves_dexterity_out_rather_than_capping_it_at_zero() -> None:
    """The distinction only shows up on a *negative* modifier, and it is the
    likeliest way to get this class wrong: a Strength build in Plate with a
    Dexterity of 8 wears it for 18, not 17. The rules do not cap Dexterity for
    heavy armour, they do not add it at all."""
    clumsy = stats(dex=8)
    assert Armour.heavy("Plate", 18).armour_class(clumsy) == 18
    assert Armour.light("Leather", 11).armour_class(clumsy) == 10


def test_a_shield_is_two_whatever_is_underneath_it() -> None:
    """+2 in every row of the Armor table, and v1 has no magic items to make it
    anything else (DESIGN.md § Settled decisions)."""
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
    substitutes Wisdom. Both are this value object with a second ability and
    nothing else changed, which is what keeps class features out of the AC
    calculation — the feature (W4) equips one of these."""
    barbarian = stats(dex=14, con=16)
    assert Armour.unarmoured(Ability.CONSTITUTION).armour_class(barbarian) == 15
    monk = stats(dex=16, wis=14)
    assert Armour.unarmoured(Ability.WISDOM).armour_class(monk) == 15


def test_a_printed_armour_class_is_the_one_number_somebody_may_type() -> None:
    """A monster's stat block prints its AC and a target dummy's is a knob to
    sweep (w2d) — both legitimate, because the number is published rather than
    chosen. It is the heavy shape: a printed AC already includes whatever
    Dexterity the creature has."""
    dummy = Creature(
        name="dummy",
        stats=stats(dex=18),
        position=Cell(0, 0),
        armour=Armour.natural(15),
    )
    assert dummy.armour_class == 15


def test_an_armour_class_is_derived_and_has_nowhere_to_be_assigned() -> None:
    """No setter, on either class. An AC is what the armour, the shield and the
    Dexterity add up to; a creature that could be told its AC is a build that
    can type one into a YAML file."""
    fighter = creature()
    with pytest.raises(AttributeError):
        fighter.armour_class = 22  # type: ignore[misc]


def test_swapping_the_stat_block_out_from_under_a_creature_is_refused() -> None:
    """Three numbers are read off it once — the modifiers, the proficiency
    bonus and the derived AC — so a stat block swapped in afterwards would
    leave all three describing the creature this one used to be. A creature
    whose build changed is a new creature."""
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
    """The flagship piece of structural prevention, checked at the level where
    it is decided: `Dice` has exactly `count` and `sides`, so a wielder's +3
    cannot be stored on the weapon and therefore cannot reach `Dice.doubled()`.
    It is added by the attack as a `flat` contribution instead."""
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
    """`has()` is what w1f reads Finesse through; a property a weapon lacks is a
    False, not a KeyError."""
    assert GREATSWORD.has(WeaponProperty.HEAVY)
    assert GREATSWORD.has(WeaponProperty.TWO_HANDED)
    assert not GREATSWORD.has(WeaponProperty.FINESSE)


# --- A creature --------------------------------------------------------------


def test_a_creature_must_be_told_where_it_stands() -> None:
    """`position` has no default. Two creatures defaulted onto the same square
    would be a fight in which every distance is zero and everything is in
    everyone's reach — wrong numbers, silently, in every run."""
    with pytest.raises(TypeError, match="position"):
        Creature(name="nowhere", stats=stats())  # type: ignore[call-arg]


def test_a_creature_starts_undamaged_and_with_no_temporary_hit_points() -> None:
    fighter = creature(max_hp=44)
    assert (fighter.hp, fighter.max_hp, fighter.temp_hp) == (44, 44, 0)
    assert fighter.is_alive


def test_being_alive_is_not_dying_and_being_down_is_hit_points() -> None:
    """Two questions since w1j: `is_alive` is whether the creature has died,
    and a creature at 0 hit points making death saving throws has not;
    `is_down` is whether its hit points are 0. Temporary hit points keep
    neither creature up — they are spent before hit points, never after."""
    fighter = creature(max_hp=10)
    fighter.hp = 1
    on_one = (fighter.is_alive, fighter.is_down)
    fighter.hp = 0
    at_zero = (fighter.is_alive, fighter.is_down)
    fighter.temp_hp = 5
    at_zero_with_temporary = (fighter.is_alive, fighter.is_down)
    fighter.dead = True
    dead = (fighter.is_alive, fighter.is_down)
    assert (on_one, at_zero, at_zero_with_temporary, dead) == (
        (True, False),
        (True, True),
        (True, True),
        (False, True),
    )


def test_a_creature_starts_alive_undying_and_not_a_player_character() -> None:
    """Nothing on a `StatBlock` says who is a player character, so it is the
    constructor's keyword, and its default is the creature that dies at 0."""
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


def test_an_unarmed_creature_threatens_five_feet() -> None:
    assert creature().reach_ft == 5


def test_reach_comes_from_the_weapon() -> None:
    """A greatsword threatens the eight squares around its wielder; a pike
    threatens the ring beyond them. Size does not enter into it yet — w1d owns
    the Large creature whose square and whose reach stop being one question."""
    fighter = creature()
    fighter.weapon = GREATSWORD
    assert fighter.reach_ft == 5
    fighter.weapon = PIKE
    assert fighter.reach_ft == 10


def test_what_the_stat_block_knows_the_creature_answers_for() -> None:
    """One place holds the number: the creature answers for its stat block
    rather than carrying a second copy of it, and the AC is derived from the
    equipment beside it."""
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
    """A natural 20 by default, and 19 for a Champion — Improved Critical lowers
    the *character's* threshold, never one attack's. That is why it lives here
    and why `AttackRollContext` has no mutator for it: the engine reads it when
    it builds the context, and a handler that could edit it per attack would be
    a second way to spell a permanent feature."""
    fighter = creature()
    assert fighter.crit_threshold == CRITICAL_HIT_THRESHOLD == 20
    fighter.crit_threshold = 19
    assert fighter.crit_threshold == 19


def test_a_creature_owns_the_economy_its_dispatcher_spends_from() -> None:
    """Wired here rather than by the build loader: a creature paying out of
    somebody else's economy is the same class of mistake as a handler owned by
    somebody else, and construction is the one place that can rule it out.
    `Dispatcher.bind()` is left for the gate (w2f), which has a cycle this does
    not."""
    fighter = creature()
    assert isinstance(fighter.economy, ActionEconomy)
    with pytest.raises(EngineAssemblyError, match="already has an action economy"):
        fighter.dispatcher.bind(economy=ActionEconomy())


def test_a_creature_does_not_know_how_to_take_damage() -> None:
    """The resolution order lives in exactly one file, `engine/damage.py`. A
    `take_damage()` here would be a second place to write it, and the second
    place is the one that gets it wrong."""
    assert not hasattr(Creature, "take_damage")
    assert not hasattr(Creature, "heal")


def test_a_creature_is_slotted_so_a_typo_is_not_a_new_field() -> None:
    """`hp` misspelled once as `hitpoints` would otherwise be a field nothing
    reads and a creature that never dies."""
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
    """The Null Object ending of DESIGN.md § Hook system: engine code dispatches
    unconditionally rather than asking first, so a hook with no handlers costs
    one failed dict lookup and must not raise."""
    fighter = creature()
    fighter.attach(TurnCounter(fighter))
    ctx = turn_context(fighter)
    fighter.dispatch(ON_TURN_END, ctx)
    assert not ctx.vetoed


def test_attaching_somebody_elses_feature_is_refused() -> None:
    """`self.owner` inside every one of that source's handlers would be the
    wrong creature — the one mistake hook `Scope` exists to make impossible, so
    it is refused at attach time rather than discovered in a number."""
    fighter = creature(name="fighter")
    rogue = creature(name="rogue")
    with pytest.raises(ValueError, match="belongs to fighter"):
        rogue.attach(TurnCounter(fighter))


def test_each_creature_owns_its_own_table() -> None:
    """Per-creature tables, not a global bus: a feature attached to one creature
    is not on anybody else's hot path (DESIGN.md § Levers)."""
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
