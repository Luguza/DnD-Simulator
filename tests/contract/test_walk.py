"""Passing through, Difficult Terrain, crawling, and the drag.

Every creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Dexterity 14 (+2), every other score 10, 20 hit points, no
armour, Speed 30. A Grapple by one of them is DC 8 + 3 + 2 = 13, saved with
Strength +3, so a natural 9 (12) fails it.

A square costs 5 feet; entering the space of a creature that isn't Tiny or the
mover's ally costs 5 more (Difficult Terrain), crawling costs 5 more, and a
drag costs 5 more unless what is dragged is Tiny or two or more sizes smaller.

Sources: XPHB (2024) "Moving Around Other Creatures", "Difficult Terrain",
"Crawling"; Rules Glossary "Grappled", "Prone"; ROADMAP `w2j2`; § The W0 debt
register #57, #60, #78.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.actions.move import Move
from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.actions.unarmed import Grapple, grips
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.prone import Prone
from dndsim.core.geometry import Cell, Footing, reachable, route, step_costs
from dndsim.core.log import EventLog
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock, Weapon
from dndsim.engine.damage import resolve_damage
from dndsim.engine.world import World
from dndsim.hooks.contexts import Context
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType, SaveOutcome, Size
from weapon_fixtures import GREATSWORD

ORIGIN: Final = Cell(0, 0)
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
    cell: Cell = ORIGIN,
    *,
    size: Size = Size.MEDIUM,
    weapon: Weapon | None = None,
    scores: Mapping[Ability, int] = SCORES,
    initiative: int = 10,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=scores, max_hp=HP, size=size),
        position=cell,
        weapon=weapon,
        initiative=InitiativeKey(
            initiative=initiative,
            dexterity=14,
            player_character=False,
            tiebreak=0.5,
        ),
    )


def ready(*creatures: Creature) -> None:
    for one in creatures:
        one.economy.turn_started(one.speed_ft)


def held_by(foe: Creature) -> list[Creature | None]:
    return [
        effect.caster  # type: ignore[misc]
        for effect in foe.effects
        if isinstance(effect, Grappled)
    ]


def grapple(world: World, hero: Creature, foe: Creature) -> None:
    """`hero` grapples `foe`: Strength 9 + 3 = 12 against DC 13 fails."""
    ready(hero)
    outcome = Grapple(hero, foe, world=world).execute(rng=ScriptedRng([9]))
    assert outcome is SaveOutcome.FAILURE
    assert held_by(foe) == [hero]


# --- route(), Footing, reachable() --------------------------------------------


def test_a_route_passes_a_crowded_square() -> None:
    footing = Footing(crowded=frozenset({Cell(1, 0)}))

    walked = route(ORIGIN, Cell(2, 0), footing=footing)

    assert walked[-1] == Cell(2, 0)
    assert len(walked) == 2
    assert sum(step_costs(walked, footing)) == 10


def test_a_route_never_ends_in_a_crowded_square() -> None:
    footing = Footing(crowded=frozenset({Cell(1, 0)}))

    assert footing.passes(Cell(1, 0))
    assert not footing.stands(Cell(1, 0))
    with pytest.raises(ValueError):
        route(ORIGIN, Cell(1, 0), footing=footing)


def test_a_route_never_enters_a_blocked_square() -> None:
    footing = Footing(blocked=frozenset({Cell(1, 0)}))

    assert not footing.passes(Cell(1, 0))
    assert not footing.stands(Cell(1, 0))
    with pytest.raises(ValueError):
        route(ORIGIN, Cell(1, 0), footing=footing)


GATES: Final = frozenset(
    Cell(x, y) for x in (2, 3, 4) for y in range(-10, 11) if y not in (0, -3)
)
"""A wall three deep down x = 2..4 with two ways through, along y = 0 and
y = -3."""
MUD: Final = frozenset({Cell(2, 0), Cell(3, 0), Cell(4, 0)})


def test_the_cheapest_route_is_walked_rather_than_the_shortest() -> None:
    """To (6, 0): along y = 0 is six steps, three of them into mud,
    3 * 5 + 3 * 10 = 45; round by y = -3 is eight clear steps, 40."""
    footing = Footing(blocked=GATES, difficult=MUD)

    walked = route(ORIGIN, Cell(6, 0), footing=footing)

    assert walked[-1] == Cell(6, 0)
    assert sum(step_costs(walked, footing)) == 40
    assert len(walked) == 8
    assert not any(cell in MUD for cell in walked)


def test_of_equally_cheap_routes_the_fewer_steps_are_taken() -> None:
    """Two deep: straight through costs 5 + 10 + 10 = 25 in three steps, and
    round the block costs 25 too, in five. Either is legal; the shorter is
    the one with fewer squares to provoke on."""
    deep = frozenset(Cell(x, y) for x in (1, 2) for y in (-1, 0, 1))
    footing = Footing(difficult=deep)

    walked = route(ORIGIN, Cell(3, 0), footing=footing)

    assert walked[-1] == Cell(3, 0)
    assert sum(step_costs(walked, footing)) == 25
    assert len(walked) == 3
    assert route(ORIGIN, Cell(3, 0), footing=footing) == walked


def test_a_difficult_square_on_the_straight_line_is_stepped_round_for_free() -> None:
    """A diagonal round one difficult square costs 10, as the straight line
    would without it."""
    footing = Footing(difficult=frozenset({Cell(1, 0)}))

    walked = route(ORIGIN, Cell(2, 0), footing=footing)

    assert walked[-1] == Cell(2, 0)
    assert sum(step_costs(walked, footing)) == 10
    assert Cell(1, 0) not in walked


def test_reachable_prices_each_square_by_its_cheapest_route() -> None:
    footing = Footing(difficult=frozenset({Cell(1, 0)}))

    reach = reachable(ORIGIN, 10, footing)

    assert ORIGIN not in reach.cost_ft
    assert reach.cost_ft[Cell(1, 1)] == 5
    assert reach.cost_ft[Cell(1, 0)] == 10
    assert reach.cost_ft[Cell(2, 0)] == 10
    assert reach.cost_ft[Cell(2, 2)] == 10
    assert Cell(3, 0) not in reach.cost_ft
    assert len(reach.cost_ft) == 24


def test_reachable_passes_a_crowded_square_but_does_not_stop_there() -> None:
    """A wall down x = 1 with one gap at (1, 0) that is crowded and
    difficult: (2, 0) costs 10 + 5 = 15 through it."""
    wall = frozenset(Cell(1, y) for y in range(-20, 21) if y != 0)
    gap = frozenset({Cell(1, 0)})
    footing = Footing(blocked=wall, crowded=gap, difficult=gap)

    reach = reachable(ORIGIN, 15, footing)

    assert Cell(1, 0) not in reach.cost_ft
    assert reach.cost_ft[Cell(2, 0)] == 15
    assert reach.route_to(Cell(2, 0)) == (Cell(1, 0), Cell(2, 0))
    assert reach.cost_ft[Cell(2, 1)] == 15
    assert Cell(3, 0) not in reach.cost_ft, "15 + 5 is over the budget"


def test_a_towed_footprint_refuses_a_square_the_mover_could_enter() -> None:
    """The mover could pass the crowded (2, 0) itself; with a creature towed
    one square east, standing on (1, 0) puts that creature in it."""
    crowded = frozenset({Cell(2, 0)})
    free = Footing(crowded=crowded)
    towing = Footing(crowded=crowded, towing=((1, 0, Size.MEDIUM),))

    assert free.passes(Cell(1, 0))
    assert free.stands(Cell(1, 0))
    assert not towing.passes(Cell(1, 0))
    assert not towing.stands(Cell(1, 0))
    assert towing.passes(Cell(2, 0))
    assert not towing.stands(Cell(2, 0))


# --- moving round other creatures ---------------------------------------------


def corridor(
    gap: Creature, *, mover: Creature | None = None, walls: Size = Size.MEDIUM
) -> tuple[World, Creature, Creature]:
    """The mover on the origin, a goblin at (3, 0), and walls of awake enemies
    of size `walls` at (1, -2), (1, -1), (1, 1), (1, 2), with `gap` at
    (1, 0). Through the gap to (2, 0) is two steps; round the wall to a square
    in reach of the goblin is five."""
    fighter = mover if mover is not None else creature("fighter")
    goblin = creature("goblin", Cell(3, 0))
    seated = World()
    seated.seat(fighter, "heroes")
    seated.seat(goblin, "monsters")
    for y in (-2, -1, 1, 2):
        seated.seat(creature(f"wall{y}", Cell(1, y), size=walls), "monsters")
    gap.position = Cell(1, 0)
    seated.seat(gap, "heroes" if gap.name == "ally" else "monsters")
    ready(fighter)
    return seated, fighter, goblin


def test_a_move_passes_through_an_ally_at_no_extra_cost() -> None:
    seated, fighter, goblin = corridor(creature("ally"))

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert fighter.economy.movement_ft == 20


def test_a_move_passes_an_incapacitated_enemy_as_difficult_terrain() -> None:
    """Entering its square costs 10, the next 5: 30 - 15 = 15 left."""
    foe = creature("foe")
    foe.effects.apply(Incapacitated(foe))
    seated, fighter, goblin = corridor(foe)

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert walked[-1] != Cell(1, 0)
    assert fighter.economy.movement_ft == 15


def test_a_move_passes_a_tiny_enemy_at_no_extra_cost() -> None:
    seated, fighter, goblin = corridor(creature("rat", size=Size.TINY))

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert fighter.economy.movement_ft == 20


def test_a_move_passes_an_enemy_two_sizes_larger_as_difficult_terrain() -> None:
    """A Tiny mover, walls of Small enemies, and a Medium enemy in the gap:
    Tiny to Medium is two sizes, so it is passed at 10 + 5 = 15."""
    sprite = creature("sprite", size=Size.TINY)
    seated, fighter, goblin = corridor(
        creature("orc"), mover=sprite, walls=Size.SMALL
    )

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert fighter.economy.movement_ft == 15


def test_an_enemy_one_size_apart_blocks() -> None:
    """A Tiny mover, walls of Small enemies, and a Small enemy in the gap:
    one size apart, so the way round is five steps."""
    sprite = creature("sprite", size=Size.TINY)
    seated, fighter, goblin = corridor(
        creature("kobold", size=Size.SMALL), mover=sprite, walls=Size.SMALL
    )

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert Cell(1, 0) not in walked
    assert len(walked) == 5
    assert fighter.economy.movement_ft == 5


def test_an_awake_enemy_of_the_same_size_blocks() -> None:
    seated, fighter, goblin = corridor(creature("foe"))

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert Cell(1, 0) not in walked
    assert len(walked) == 5
    assert fighter.economy.movement_ft == 5


def test_a_walk_that_would_end_in_an_ally_stops_short_of_it() -> None:
    """An ally fills the one gap at (3, 0) in a wall down x = 3, the goblin
    at (6, 0). 15 feet walk three steps, the third into the ally's square, so
    the walk ends after two, paying 10."""
    fighter = creature("fighter")
    goblin = creature("goblin", Cell(6, 0))
    ally = creature("ally", Cell(3, 0))
    seated = World()
    seated.seat(fighter, "heroes")
    seated.seat(ally, "heroes")
    seated.seat(goblin, "monsters")
    for y in (-3, -2, -1, 1, 2, 3):
        seated.seat(creature(f"wall{y}", Cell(3, y)), "monsters")
    ready(fighter)
    fighter.economy.move(15)

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert len(walked) == 2
    assert fighter.position == walked[-1]
    assert fighter.position.x == 2
    assert fighter.economy.movement_ft == 5
    assert ally.position == Cell(3, 0)


def test_crawling_into_difficult_terrain_costs_fifteen_feet() -> None:
    """Prone: the incapacitated enemy's square costs 5 + 5 + 5 = 15, the next
    5 + 5 = 10; 30 - 25 = 5 left."""
    foe = creature("foe")
    foe.effects.apply(Incapacitated(foe))
    fighter = creature("fighter")
    fighter.effects.apply(Prone(fighter))
    seated, fighter, goblin = corridor(foe, mover=fighter)

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert fighter.economy.movement_ft == 5


def test_crawling_past_an_ally_costs_ten_feet_a_square() -> None:
    fighter = creature("fighter")
    fighter.effects.apply(Prone(fighter))
    seated, fighter, goblin = corridor(creature("ally"), mover=fighter)

    walked = Move(fighter, goblin, world=seated).execute(rng=ScriptedRng([]))

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 2
    assert fighter.economy.movement_ft == 10


# --- the drag -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("hero_size", "foe_size", "foe_at", "steps"),
    [
        # Same size: 10 a square, three squares.
        (Size.MEDIUM, Size.MEDIUM, Cell(1, 0), 3),
        # One size smaller: still 10.
        (Size.MEDIUM, Size.SMALL, Cell(1, 0), 3),
        # Tiny: 5 a square, six squares.
        (Size.MEDIUM, Size.TINY, Cell(1, 0), 6),
        # Two sizes smaller: 5. The Large hero covers x 0..1.
        (Size.LARGE, Size.SMALL, Cell(2, 0), 6),
    ],
)
def test_a_grappler_drags_what_it_holds_at_the_drag_s_cost(
    hero_size: Size, foe_size: Size, foe_at: Cell, steps: int
) -> None:
    hero = creature("hero", size=hero_size)
    foe = creature("foe", foe_at, size=foe_size)
    goblin = creature("goblin", Cell(20, 0))
    seated = World()
    seated.seat(hero, "heroes")
    seated.seat(foe, "monsters")
    seated.seat(goblin, "monsters")
    grapple(seated, hero, foe)

    walked = Move(hero, goblin, world=seated).execute(rng=ScriptedRng([]))

    end = hero.position
    assert len(walked) == steps
    assert walked[-1] == end
    assert end.x == steps
    assert foe.position == Cell(foe_at.x + end.x, foe_at.y + end.y)
    assert hero.economy.movement_ft == 0
    assert held_by(foe) == [hero]
    assert [grip.target for grip in grips(hero)] == [foe]


class Answers:
    """A stand-in `Gate` that takes every reaction and records each ask."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        self.asked.append(gate_id)
        return True


def guarded(owner: Creature, seated: World) -> Answers:
    gate = Answers()
    owner.dispatcher.bind(gate=gate)
    owner.attach(OpportunityAttacks(owner, world=seated, rng=ScriptedRng([])))
    return gate


def test_a_drag_provokes_from_neither_the_held_creature_nor_a_bystander() -> None:
    """The hero drags the foe west, away from the foe's start square: the
    hero never leaves the foe's reach, since it comes along, and the foe is
    pulled out of the sentry's reach without moving itself. Nobody swings."""
    log = EventLog()
    hero = creature("hero")
    foe = creature("foe", Cell(1, 0), weapon=GREATSWORD)
    sentry = creature("sentry", Cell(2, 0), weapon=GREATSWORD)
    goblin = creature("goblin", Cell(-12, 0))
    seated = World(log=log)
    seated.seat(hero, "heroes")
    seated.seat(sentry, "heroes")
    seated.seat(foe, "monsters")
    seated.seat(goblin, "monsters")
    foe_gate = guarded(foe, seated)
    sentry_gate = guarded(sentry, seated)
    for one in (foe, sentry):
        one.economy.turn_started(one.speed_ft)
    grapple(seated, hero, foe)

    walked = Move(hero, goblin, world=seated).execute(rng=ScriptedRng([]))

    end = hero.position
    assert len(walked) == 3
    assert end.x == -3
    assert foe.position == Cell(end.x + 1, end.y)
    assert hero.economy.movement_ft == 0
    assert foe_gate.asked == []
    assert sentry_gate.asked == []
    assert (foe.economy.reactions, sentry.economy.reactions) == (1, 1)
    assert (hero.hp, foe.hp) == (HP, HP)
    assert held_by(foe) == [hero]


# --- #78: a grappler that dies lets go then ----------------------------------


def test_damage_that_kills_a_grappler_ends_its_grapple_at_once() -> None:
    """A creature that makes no death saves dies at 0 hit points, and its
    hold goes with it before anything else happens."""
    hero = creature("hero")
    foe = creature("foe", Cell(1, 0))
    seated = World()
    seated.seat(hero, "monsters")
    seated.seat(foe, "heroes")
    grapple(seated, hero, foe)

    resolve_damage(hero, {DamageType.BLUDGEONING: HP}, rng=ScriptedRng([]))

    assert hero.dead
    assert held_by(foe) == []
    assert grips(hero) == []


def test_damage_that_does_not_kill_leaves_the_grapple() -> None:
    hero = creature("hero")
    foe = creature("foe", Cell(1, 0))
    seated = World()
    seated.seat(hero, "monsters")
    seated.seat(foe, "heroes")
    grapple(seated, hero, foe)

    resolve_damage(hero, {DamageType.BLUDGEONING: HP - 1}, rng=ScriptedRng([]))

    assert held_by(foe) == [hero]


def test_grappled_declares_no_gap_about_the_drag_or_the_dead_grappler() -> None:
    declared = (*Grappled.NEEDS_HOOK, *Grappled.APPROXIMATED)

    assert not any("#57" in entry or "#78" in entry for entry in declared)
    assert not any("drag" in entry or "dies" in entry for entry in declared)
