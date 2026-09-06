"""The w0g2 creature contract: the numbers, where they live, and what is attached.

`Creature` and `StatBlock` are the first cut of the objects every later wave
hangs something off, so the properties below are the ones that would be
expensive to change once ~630 features assume them:

- **A stat block that is missing something says so, as it is built.** A
  defaulted ability score, hit point total or AC is a number nobody chose that
  silently changes every save, every attack and every hit made against it, so
  the three have no defaults at all and a partial ability map is refused; a
  level off the table is refused in the same breath rather than a fight later
  (DESIGN.md § Fail at the earliest moment).
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
from dndsim.core.geometry import Cell
from dndsim.core.timeline import TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.hooks.contexts import Combatant, TurnContext
from dndsim.hooks.contexts import Weapon as WeaponProtocol
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_TURN_END, ON_TURN_START
from dndsim.hooks.source import HookSource
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


def stats(*, level: int = 1, max_hp: int = 20, armour_class: int = 15) -> StatBlock:
    """A stat block with every ability score named, so a test that cares about
    one of them changes only that one."""
    return StatBlock(
        abilities=SCORES,
        max_hp=max_hp,
        armour_class=armour_class,
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
    assert protocol_members(Combatant) == {"name", "initiative_index"}
    assert (combatant.name, combatant.initiative_index) == ("rogue", 0)


def test_a_real_weapon_is_the_weapon_a_context_carries() -> None:
    """The same debt for `Weapon`: a context knows a weapon only by its name,
    and `creatures/stats.py` is free to grow as long as it still has one."""
    weapon: WeaponProtocol = GREATSWORD
    assert protocol_members(WeaponProtocol) == {"name"}
    assert weapon.name == "Greatsword"


# --- A stat block ------------------------------------------------------------


def test_a_missing_ability_score_is_refused_rather_than_defaulted() -> None:
    """No silent 10: a build that names five abilities has lost one somewhere
    between the YAML and here, and a defaulted score would change every save and
    every attack made with it without ever being noticed."""
    partial: dict[Ability, int] = {
        ability: 14 for ability in Ability if ability is not Ability.CHARISMA
    }
    with pytest.raises(ValueError, match="all six ability scores"):
        StatBlock(abilities=partial, max_hp=20, armour_class=15)


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
            armour_class=15,
        )


def test_the_three_numbers_nobody_can_guess_have_no_defaults() -> None:
    """Ability scores, hit points and AC must all be given: a defaulted 10 is a
    number nobody chose that silently changes every save, every attack and every
    hit made against it, which is the same argument that refuses a *partial*
    ability map. Omitting one is a `TypeError` at the point of construction —
    the same failure as omitting it from the build."""
    with pytest.raises(TypeError, match="abilities"):
        StatBlock()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="max_hp"):
        StatBlock(abilities=SCORES)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="armour_class"):
        StatBlock(abilities=SCORES, max_hp=20)  # type: ignore[call-arg]


def test_the_three_that_the_rules_themselves_default_still_do() -> None:
    """Level 1 is the bottom of the table, and 30 feet and Medium are what the
    rules default to — none of the three is a guess about this creature."""
    block = StatBlock(abilities=SCORES, max_hp=20, armour_class=15)
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
        block = StatBlock(
            abilities={**SCORES, Ability.STRENGTH: score},
            max_hp=20,
            armour_class=15,
        )
        assert block.modifier(Ability.STRENGTH) == modifier, score


def test_a_stat_block_is_frozen_and_shareable() -> None:
    """One stat block is what four identical dummies in an encounter are; what
    changes during the fight lives on the creature instead."""
    shared = stats(max_hp=30)
    first = Creature(name="dummy 1", stats=shared, position=Cell(0, 0))
    second = Creature(name="dummy 2", stats=shared, position=Cell(1, 0))
    assert first.stats is second.stats is shared
    with pytest.raises(AttributeError):
        shared.max_hp = 40  # type: ignore[misc]


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


def test_being_alive_is_hit_points_and_nothing_else() -> None:
    """Death saves, stabilising and the Dying condition are w1j's. Until then 0
    hit points is the end of a combatant's participation, and temporary hit
    points do not keep a creature at 0 standing — they are spent before hit
    points, never after them."""
    fighter = creature(max_hp=10)
    fighter.hp = 1
    on_one_hit_point = fighter.is_alive
    fighter.hp = 0
    at_zero = fighter.is_alive
    fighter.temp_hp = 5
    at_zero_with_temporary = fighter.is_alive
    assert (on_one_hit_point, at_zero, at_zero_with_temporary) == (True, False, False)


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
    """One place holds the number: the creature delegates rather than copying,
    so a derived AC (w1a) has one place to land."""
    fighter = Creature(
        name="fighter",
        stats=StatBlock(
            abilities={**SCORES, Ability.STRENGTH: 18},
            level=5,
            max_hp=44,
            armour_class=18,
            speed_ft=25,
            size=Size.SMALL,
        ),
        position=Cell(2, 2),
    )
    assert fighter.armour_class == 18
    assert fighter.speed_ft == 25
    assert fighter.size is Size.SMALL
    assert fighter.proficiency_bonus == 3
    assert fighter.modifier(Ability.STRENGTH) == 4


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
        creature=subject, time=TimeKey(1, subject.initiative_index, TurnPhase.START)
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
