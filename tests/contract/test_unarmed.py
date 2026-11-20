"""The Unarmed Strike's three options, ending a grapple, standing up, and the
Shield of an Unconscious creature.

Every creature here is built from one helper: level 1 (Proficiency Bonus +2),
Strength 16 (+3), Dexterity 14 (+2), every other score 10, 20 hit points, no
armour and no saving-throw or skill proficiency, so AC 10 + 2 = 12. A Grapple
or Shove by one of them is DC 8 + 3 + 2 = 13, and its target saves with
Strength, +3 beating +2. An Unarmed Strike swings at natural + 3 + 2 and deals
1 + 3 = 4.

Sources: XPHB (2024) "Unarmed Strike", "Grappling", "Opportunity Attacks";
Rules Glossary "Grappled", "Prone", "Unconscious"; ROADMAP `w2j`; § The W0 debt
register #49, #57, #61, #62; `docs/questions.md` #15, #28, #29;
`docs/decisions.md` § w2j.
"""

from collections.abc import Mapping
from operator import attrgetter
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.actions.move import Retreat
from dndsim.actions.stand_up import StandUp
from dndsim.actions.unarmed import (
    EscapeGrapple,
    Grapple,
    Grappling,
    ReleaseGrapple,
    ShoveDown,
    ShovePush,
    UnarmedStrike,
    grips,
    unarmed_options,
)
from dndsim.apl.apl import COMMAND_IDS, Apl, Entry
from dndsim.conditions.base import SourcedCondition
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.prone import Prone
from dndsim.conditions.registry import CONDITIONS
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import UNARMED_STRIKE, StatBlock, Weapon
from dndsim.engine.attack import AttackResult
from dndsim.engine.saves import SAVE
from dndsim.engine.turn import offers, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import AbilityCheckContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules import enums
from dndsim.rules.enums import Ability, Outcome, SaveOutcome
from weapon_fixtures import GREATSWORD

ORIGIN: Final = Cell(0, 0)
EAST: Final = Cell(1, 0)
SOUTH: Final = Cell(0, 1)
HP: Final = 20

SCORES: Final = MappingProxyType(
    {
        **dict.fromkeys(Ability, 10),
        Ability.STRENGTH: 16,
        Ability.DEXTERITY: 14,
    }
)


def creature(
    name: str,
    *,
    cell: Cell = ORIGIN,
    weapon: Weapon | None = None,
    scores: Mapping[Ability, int] = SCORES,
    skills: frozenset[str] = frozenset(),
    trained: frozenset[str] | None = None,
    shield: bool = False,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(
            abilities=scores, max_hp=HP, skills=skills, trained_weapons=trained
        ),
        position=cell,
        weapon=weapon,
        shield=shield,
    )


def seated(*creatures: Creature, log: EventLog | None = None) -> World:
    """The first creature on one side, every other on the other."""
    world = World(log=log)
    for index, one in enumerate(creatures):
        world.seat(one, "heroes" if index == 0 else "monsters")
    return world


def ready(*creatures: Creature) -> None:
    for one in creatures:
        one.economy.turn_started(one.speed_ft)


def grappled(hero: Creature, foe: Creature) -> None:
    """`hero` grapples `foe`: Strength save 9 + 3 = 12 against 13, a failure."""
    world = seated(hero, foe)
    ready(hero)
    outcome = Grapple(hero, foe, world=world).execute(rng=ScriptedRng([9]))
    assert outcome is SaveOutcome.FAILURE


def held_by(foe: Creature) -> list[Creature | None]:
    return [
        effect.caster  # type: ignore[misc]
        for effect in foe.effects
        if isinstance(effect, Grappled)
    ]


# --- the Damage option --------------------------------------------------------


def test_an_unarmed_strike_deals_one_plus_strength() -> None:
    """15 + 3 + 2 = 20 against AC 12, a hit, for 1 + 3 = 4 and no die."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)
    rng = ScriptedRng([15])

    assert UnarmedStrike(hero, foe, world=world).execute(rng=rng) == AttackResult(
        Outcome.HIT, 4
    )
    assert foe.hp == HP - 4
    assert rng.exhausted


def test_a_critical_unarmed_strike_doubles_nothing() -> None:
    """The 1 is flat damage, so a natural 20 still deals 1 + 3 = 4."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)
    rng = ScriptedRng([20])

    result = UnarmedStrike(hero, foe, world=world).execute(rng=rng)

    assert result == AttackResult(Outcome.CRITICAL_HIT, 4)
    assert rng.exhausted


def test_every_creature_is_proficient_with_its_unarmed_strike() -> None:
    """Trained with no weapon at all, the strike still adds +2: 8 + 3 + 2 = 13
    hits AC 12, where 8 + 3 = 11 would miss."""
    hero = creature("hero", trained=frozenset())
    foe = creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)

    assert hero.stats.trained_weapons == frozenset({UNARMED_STRIKE.name})
    result = UnarmedStrike(hero, foe, world=world).execute(rng=ScriptedRng([8]))
    assert result.outcome is Outcome.HIT


def test_a_wielded_weapon_does_not_stop_an_unarmed_strike() -> None:
    """The Greatsword stays in hand: 15 + 5 = 20 hits, for 1 + 3 = 4."""
    hero = creature("hero", weapon=GREATSWORD)
    foe = creature("foe", cell=EAST)
    seated(hero, foe)
    ready(hero)

    result = UnarmedStrike(hero, foe).execute(rng=ScriptedRng([15]))

    assert result == AttackResult(Outcome.HIT, 4)
    assert hero.weapon is GREATSWORD


# --- Grapple and Shove --------------------------------------------------------


def test_a_failed_save_against_a_grapple_grapples() -> None:
    """Strength 9 + 3 = 12 against DC 13: the foe is Grappled by the hero, and
    the hero holds it with an escape DC of 13."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)

    assert held_by(foe) == [hero]
    assert [(grip.target, grip.escape_dc) for grip in grips(hero)] == [(foe, 13)]
    assert foe.speed_ft == 0


def test_a_saved_grapple_does_nothing() -> None:
    """10 + 3 = 13 meets DC 13."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)

    outcome = Grapple(hero, foe, world=world).execute(rng=ScriptedRng([10]))

    assert outcome is SaveOutcome.SUCCESS
    assert held_by(foe) == []
    assert grips(hero) == []


@pytest.mark.parametrize(
    ("strength", "dexterity", "saves_with"),
    [
        (14, 14, Ability.STRENGTH),
        (10, 16, Ability.DEXTERITY),
        (16, 14, Ability.STRENGTH),
    ],
)
def test_the_target_saves_with_its_better_bonus_and_strength_on_a_tie(
    strength: int, dexterity: int, saves_with: Ability
) -> None:
    """`docs/questions.md` #28: the higher bonus, ties to Strength."""
    hero = creature("hero")
    foe = creature(
        "foe",
        cell=EAST,
        scores={
            **SCORES,
            Ability.STRENGTH: strength,
            Ability.DEXTERITY: dexterity,
        },
    )
    log = EventLog()
    world = seated(hero, foe, log=log)
    ready(hero)

    ShoveDown(hero, foe, world=world).execute(rng=ScriptedRng([20]))

    saves = [record for record in log if record[0] == SAVE]
    assert [(record[2], record[3]) for record in saves] == [(saves_with, 13)]


def test_a_shove_knocks_the_target_prone() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)

    ShoveDown(hero, foe, world=world).execute(rng=ScriptedRng([9]))

    assert any(isinstance(effect, Prone) for effect in foe.effects)


@pytest.mark.parametrize(
    ("foe_at", "pushed_to"),
    [(EAST, Cell(2, 0)), (Cell(1, 1), Cell(2, 2)), (Cell(-1, 0), Cell(-2, 0))],
)
def test_a_push_moves_the_target_five_feet_directly_away_for_free(
    foe_at: Cell, pushed_to: Cell
) -> None:
    hero, foe = creature("hero"), creature("foe", cell=foe_at)
    world = seated(hero, foe)
    ready(hero, foe)

    ShovePush(hero, foe, world=world).execute(rng=ScriptedRng([9]))

    assert foe.position == pushed_to
    assert hero.position == ORIGIN
    assert (hero.economy.movement_ft, foe.economy.movement_ft) == (30, 30)


def test_a_push_into_an_occupied_square_is_not_offered() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    wall = creature("wall", cell=Cell(2, 0))
    world = seated(hero, foe, wall)
    ready(hero)

    assert not ShovePush(hero, foe, world=world).is_legal()
    assert ShoveDown(hero, foe, world=world).is_legal()


# --- ending a grapple ---------------------------------------------------------


@pytest.mark.parametrize(
    ("skills", "natural", "escaped"),
    [
        # Athletics +3 against Acrobatics +2: 10 + 3 = 13 meets 13.
        (frozenset(), 10, True),
        (frozenset(), 9, False),
        # Acrobatics +2 + 2 = 4 beats Athletics +3: 9 + 4 = 13.
        (frozenset({"acrobatics"}), 9, True),
    ],
)
def test_an_escape_is_the_better_check_against_the_escape_dc(
    skills: frozenset[str], natural: int, escaped: bool
) -> None:
    hero = creature("hero")
    foe = creature("foe", cell=EAST, skills=skills)
    grappled(hero, foe)
    ready(foe)

    assert EscapeGrapple(foe, hero).execute(rng=ScriptedRng([natural])) is escaped

    assert (held_by(foe) == []) is escaped
    assert (grips(hero) == []) is escaped
    assert foe.economy.actions == 0


def test_the_grapple_ends_when_the_grappler_is_incapacitated() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)

    hero.effects.apply(Incapacitated(hero))

    assert held_by(foe) == []
    assert not any(isinstance(effect, Grappling) for effect in hero.effects)


def test_the_grapple_ends_when_the_grappler_walks_away() -> None:
    """Until `w2j2`'s drag, walking off lets go."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)
    world = seated(hero, foe)
    ready(hero)

    Retreat(hero, foe, world=world, keep_ft=15).execute(rng=ScriptedRng([]))

    assert held_by(foe) == []
    assert grips(hero) == []


def test_the_grapple_ends_when_a_push_parts_the_two() -> None:
    """The brute's push moves the foe from (1, 0) to (2, -1), 10 feet from
    the hero."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    brute = creature("brute", cell=SOUTH)
    grappled(hero, foe)
    world = World()
    world.seat(hero, "heroes")
    world.seat(brute, "heroes")
    world.seat(foe, "monsters")
    ready(brute)

    ShovePush(brute, foe, world=world).execute(rng=ScriptedRng([9]))

    assert foe.position == Cell(2, -1)
    assert held_by(foe) == []
    assert grips(hero) == []


def test_a_dead_grappler_lets_go_before_the_held_creature_moves() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)
    world = seated(hero, foe)
    hero.die()

    take_turn(world, foe, 1, policy=Apl([]).bind(foe, world), rng=ScriptedRng([]))

    assert held_by(foe) == []


def test_a_release_is_free() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)
    before = hero.economy.actions

    ReleaseGrapple(hero, foe).execute(rng=ScriptedRng([]))

    assert held_by(foe) == []
    assert hero.economy.actions == before


def test_a_second_creature_grapples_alongside_the_first() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    other = creature("other", cell=Cell(2, 0))
    grappled(hero, foe)
    world = World()
    world.seat(other, "heroes")
    world.seat(foe, "monsters")
    ready(other)

    Grapple(other, foe, world=world).execute(rng=ScriptedRng([9]))

    assert held_by(foe) == [hero, other]


# --- standing up --------------------------------------------------------------


def test_standing_up_costs_half_the_speed_and_ends_prone() -> None:
    hero = creature("hero")
    hero.effects.apply(Prone(hero))
    ready(hero)

    StandUp(hero).execute(rng=ScriptedRng([]))

    assert not any(isinstance(effect, Prone) for effect in hero.effects)
    assert hero.economy.movement_ft == 15


def test_standing_up_is_refused_short_of_half_the_speed() -> None:
    hero = creature("hero")
    hero.effects.apply(Prone(hero))
    ready(hero)
    hero.economy.move(20)

    assert not StandUp(hero).is_legal()


def test_standing_up_is_refused_at_speed_zero() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(foe, hero)
    hero.effects.apply(Prone(hero))
    ready(hero)

    assert not StandUp(hero).is_legal()


def test_standing_up_never_ends_unconscious() -> None:
    """Unconscious is a kind of Prone, with Speed 0; once it ends, the Prone it
    leaves behind is stood up from."""
    hero = creature("hero")
    unconscious = Unconscious(hero)
    hero.effects.apply(unconscious)
    ready(hero)
    assert not StandUp(hero).is_legal()

    hero.effects.remove(unconscious)
    ready(hero)
    StandUp(hero).execute(rng=ScriptedRng([]))

    assert list(hero.effects) == []


# --- the policy ---------------------------------------------------------------


def test_a_policy_with_no_move_clause_stands_up_first() -> None:
    hero, foe = creature("hero", weapon=UNARMED_STRIKE), creature("foe", cell=EAST)
    world = seated(hero, foe)
    hero.effects.apply(Prone(hero))
    ready(hero)

    choice = Apl([Entry("attack")]).bind(hero, world).choose(hero, offers(world, hero))

    assert isinstance(choice, StandUp)


def test_a_policy_that_says_how_to_move_stands_only_if_it_says_so() -> None:
    hero, foe = creature("hero", weapon=UNARMED_STRIKE), creature("foe", cell=EAST)
    world = seated(hero, foe)
    hero.effects.apply(Prone(hero))
    ready(hero)
    apl = Apl([Entry("attack", move="toward nearest of enemies until in_reach")])

    choice = apl.bind(hero, world).choose(hero, offers(world, hero))

    assert choice is not None and choice.ID == "attack"


def test_an_apl_names_each_option_and_takes_it_on_its_target() -> None:
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    world = seated(hero, foe)
    ready(hero)
    apl = Apl([Entry("grapple", target="nearest of enemies")]).bind(hero, world)

    choice = apl.choose(hero, offers(world, hero))

    assert isinstance(choice, Grapple)
    assert choice.target is foe
    assert {
        "unarmed_strike",
        "grapple",
        "shove_prone",
        "shove_push",
        "escape_grapple",
        "release_grapple",
        "stand_up",
    } <= COMMAND_IDS


def test_a_grappled_creature_is_offered_the_escape_and_its_grappler_the_release() -> (
    None
):
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)
    world = seated(hero, foe)
    ready(hero, foe)

    escapes = [one for one in offers(world, foe) if isinstance(one, EscapeGrapple)]
    releases = [one for one in offers(world, hero) if isinstance(one, ReleaseGrapple)]

    assert [one.grappler for one in escapes] == [hero]
    assert [one.target for one in releases] == [foe]


# --- the Shield of an Unconscious creature (#61) ------------------------------


def test_an_unconscious_creature_loses_its_shield_bonus_and_keeps_the_shield() -> None:
    """10 + 2 + 2 = 14 with the Shield, 12 while Unconscious, 14 again once it
    ends, still Prone (`docs/questions.md` #15)."""
    hero = creature("hero", shield=True)
    assert hero.armour_class == 14
    unconscious = Unconscious(hero)

    hero.effects.apply(unconscious)
    assert (hero.armour_class, hero.shield) == (12, True)

    hero.effects.remove(unconscious)
    assert hero.armour_class == 14
    assert any(isinstance(effect, Prone) for effect in hero.effects)


# --- #49: no condition is immune to what a Shove or Grapple applies -----------


@pytest.mark.parametrize("condition", sorted(CONDITIONS, key=attrgetter("value")))
def test_prone_and_grappled_land_under_every_condition(
    condition: enums.Condition,
) -> None:
    """Petrified's immunity to Poisoned, the one a held condition can miss
    (§ The W0 debt register #49), touches neither Prone nor Grappled."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    implementation = CONDITIONS[condition]
    if issubclass(implementation, SourcedCondition):
        foe.effects.apply(implementation(foe, caster=hero))
    else:
        foe.effects.apply(implementation(foe))

    assert foe.effects.apply(Prone(foe)) is not None
    assert foe.effects.apply(Grappled(foe, caster=hero)) is not None


# --- what `offers()` is handed ------------------------------------------------


def _every_option(
    world: World, actor: Creature, enemy: Creature
) -> list[UnarmedStrike | Grapple | ShoveDown | ShovePush]:
    return [
        UnarmedStrike(actor, enemy, world=world),
        Grapple(actor, enemy, world=world),
        ShoveDown(actor, enemy, world=world),
        ShovePush(actor, enemy, world=world),
    ]


@pytest.mark.parametrize(
    "state",
    [
        "ready",
        "no-action",
        "incapacitated",
        "two-handed",
        "far",
        "huge",
        "dead",
        "push-blocked",
        "holding",
    ],
)
def test_unarmed_options_are_exactly_the_legal_ones(state: str) -> None:
    """`unarmed_options()` asks the actor's half once; what it returns is what
    asking each option's `is_legal()` keeps, in the same order."""
    hero = creature("hero", weapon=GREATSWORD if state == "two-handed" else None)
    near = creature("near", cell=EAST)
    side = creature("side", cell=SOUTH)
    others: list[Creature] = []
    if state == "far":
        near.position = Cell(3, 0)
    if state == "huge":
        near = Creature(
            name="huge",
            stats=StatBlock(abilities=SCORES, max_hp=HP, size=enums.Size.HUGE),
            position=EAST,
        )
    if state == "push-blocked":
        others.append(creature("wall", cell=Cell(2, 0)))
    world = seated(hero, near, side, *others)
    if state != "no-action":
        ready(hero)
    if state == "incapacitated":
        hero.effects.apply(Incapacitated(hero))
    if state == "dead":
        near.die()
    if state == "holding":
        grappled(hero, side)

    expected = [
        option
        for enemy in (near, side)
        for option in _every_option(world, hero, enemy)
        if option.is_legal()
    ]
    got = unarmed_options(world, hero, (near, side))

    assert [(type(o), o.target) for o in got] == [(type(o), o.target) for o in expected]


class Wriggle(HookSource):
    """Queues the Prone condition onto the grappler of the escape it hears."""

    __slots__ = ("grappler",)

    ID = "test.wriggle"

    def __init__(self, owner: Creature, grappler: Creature) -> None:
        super().__init__(owner)
        self.grappler = grappler

    @hook("on_ability_check")
    def trip(self, ctx: AbilityCheckContext) -> R:
        ctx.apply_effect(self.grappler, Prone(self.grappler))
        return R.APPLIED


def test_an_escape_check_settles_among_the_grappler_too() -> None:
    """The grappler is a participant of the escape, so an effect queued onto
    it lands, with no world to find it in."""
    hero, foe = creature("hero"), creature("foe", cell=EAST)
    grappled(hero, foe)
    foe.attach(Wriggle(foe, hero))
    ready(foe)

    EscapeGrapple(foe, hero).execute(rng=ScriptedRng([1]))

    assert any(isinstance(effect, Prone) for effect in hero.effects)
