"""The walk past other creatures, Difficult Terrain, and the drag (`w2j2`).

XPHB "Moving Around Other Creatures", "Difficult Terrain", Rules Glossary
"Grappled"; `docs/decisions.md` § w2j2.
"""

from types import MappingProxyType
from typing import Final

import pytest

from dndsim.actions.move import Move, Retreat
from dndsim.actions.unarmed import Grapple, Grappling, grips
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.prone import Prone
from dndsim.core.geometry import Cell, route, routes_within, step_costs
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock
from dndsim.engine.damage import resolve_damage
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import LeaveReachContext, ReducedToZeroContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType, SaveOutcome, Size

ORIGIN: Final = Cell(0, 0)
HP: Final = 20

SCORES: Final = MappingProxyType(
    {**dict.fromkeys(Ability, 10), Ability.STRENGTH: 16, Ability.DEXTERITY: 14}
)


def creature(
    name: str,
    *,
    cell: Cell = ORIGIN,
    size: Size = Size.MEDIUM,
    speed: int = 30,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=HP, speed_ft=speed, size=size),
        position=cell,
    )


def ready(*creatures: Creature) -> None:
    for one in creatures:
        one.economy.turn_started(one.speed_ft)


def column(world: World, side: str, x: int, ys: range, **kwargs: object) -> None:
    """Seat a creature on every square of column `x` over `ys`."""
    for y in ys:
        world.seat(creature(f"{side}{x},{y}", cell=Cell(x, y), **kwargs), side)  # type: ignore[arg-type]


def closing(goblin_at: Cell, **kwargs: object) -> tuple[World, Creature, Creature]:
    """A fighter at the origin with its turn's movement, and a goblin."""
    fighter = creature("fighter", **kwargs)  # type: ignore[arg-type]
    goblin = creature("goblin", cell=goblin_at)
    world = World()
    world.seat(fighter, "heroes")
    world.seat(goblin, "monsters")
    ready(fighter)
    return world, fighter, goblin


def walk(world: World, mover: Creature, target: Creature) -> tuple[Cell, ...]:
    return Move(mover, target, world=world).execute(rng=ScriptedRng([]))


# --- route() ------------------------------------------------------------------


def test_route_takes_the_cheapest_route_not_the_fewest_squares() -> None:
    """Straight across three Difficult squares is four steps and 35 feet;
    round by `y = -1`, crossing one, is four steps and 25 feet."""
    difficult = frozenset(Cell(x, y) for x in (1, 2, 3) for y in (0, 1))
    blocked = frozenset(Cell(x, 2) for x in range(-1, 6))

    walked = route(ORIGIN, Cell(4, 1), blocked=blocked, difficult=difficult, step_ft=5)

    assert walked[-1] == Cell(4, 1)
    assert sum(step_costs(walked, difficult=difficult)) == 25


def test_route_crosses_difficult_terrain_when_that_is_cheapest() -> None:
    """A wall at `x = 1` with one Difficult gap: 15 feet through it."""
    blocked = frozenset(Cell(1, y) for y in range(-8, 9) if y != 0)
    difficult = frozenset({Cell(1, 0)})

    walked = route(ORIGIN, Cell(2, 0), blocked=blocked, difficult=difficult)

    assert walked == (Cell(1, 0), Cell(2, 0))
    assert step_costs(walked, difficult=difficult) == (10, 5)


def test_extra_costs_add() -> None:
    """Crawling's 10 feet a square plus Difficult Terrain's 5."""
    difficult = frozenset({Cell(1, 0)})

    assert step_costs((Cell(1, 0), Cell(2, 0)), difficult=difficult, step_ft=10) == (
        15,
        10,
    )


def test_routes_within_stops_at_the_budget() -> None:
    reached = routes_within(ORIGIN, 10, difficult=frozenset({Cell(1, 0)}))

    assert Cell(2, 0) in reached
    assert Cell(3, 0) not in reached
    assert reached.cost[Cell(1, 0)] == 10
    assert reached.path(Cell(2, 0)) != (Cell(1, 0), Cell(2, 0))


def test_a_towed_footprint_must_stay_clear() -> None:
    """A walker towing a creature one square east cannot step where that
    creature would land on someone."""
    crowded = frozenset({Cell(2, 0)})

    walked = route(
        ORIGIN, Cell(2, 0), towing=((Cell(1, 0), Size.MEDIUM),), crowded=crowded
    )

    assert Cell(1, 0) not in walked
    assert walked[-1] == Cell(2, 0)


# --- passing other creatures ----------------------------------------------------


def test_a_mover_passes_through_its_allies_for_nothing_extra() -> None:
    world, fighter, goblin = closing(Cell(4, 0))
    column(world, "heroes", 1, range(-3, 4))

    walked = walk(world, fighter, goblin)

    assert len(walked) == 3
    assert fighter.economy.movement_ft == 15


def test_a_capable_enemy_of_a_near_size_is_impassable() -> None:
    world, fighter, goblin = closing(Cell(4, 0))
    column(world, "monsters", 1, range(-1, 2))

    walked = walk(world, fighter, goblin)

    assert not {Cell(1, y) for y in range(-1, 2)}.intersection(walked)


def test_an_incapacitated_enemy_is_passed_as_difficult_terrain() -> None:
    world, fighter, goblin = closing(Cell(4, 0))
    column(world, "monsters", 1, range(-6, 7))
    held = next(c for c in world if c.position == Cell(1, 0))
    held.effects.apply(Incapacitated(held))

    walked = walk(world, fighter, goblin)

    assert walked[0] == Cell(1, 0)
    assert len(walked) == 3
    assert fighter.economy.movement_ft == 10


def test_a_tiny_enemy_is_passed_for_nothing_extra() -> None:
    world, fighter, goblin = closing(Cell(4, 0))
    column(world, "monsters", 1, range(-6, 7), size=Size.TINY)

    walked = walk(world, fighter, goblin)

    assert len(walked) == 3
    assert fighter.economy.movement_ft == 15


def test_an_enemy_two_sizes_apart_is_passed_as_difficult_terrain() -> None:
    """A Tiny fighter and a wall of Medium goblins: two sizes apart, so
    passable, and neither Tiny nor an ally, so Difficult."""
    world, fighter, goblin = closing(Cell(3, 0), size=Size.TINY)
    column(world, "monsters", 1, range(-6, 7))

    walked = walk(world, fighter, goblin)

    assert len(walked) == 2
    assert fighter.economy.movement_ft == 15


def test_an_enemy_one_size_apart_is_impassable() -> None:
    world, fighter, goblin = closing(Cell(3, 0), size=Size.SMALL)
    column(world, "monsters", 1, range(-1, 2))

    walked = walk(world, fighter, goblin)

    assert not {Cell(1, y) for y in range(-1, 2)}.intersection(walked)


def test_a_walk_that_would_stop_in_a_space_stops_short_of_it() -> None:
    """The route to (3, -1) runs (1, -1), (2, -1): ten feet of movement would
    end on the ally there, so the walk ends a square short of it."""
    world, fighter, goblin = closing(Cell(4, 0), speed=10)
    column(world, "heroes", 2, range(-1, 2))

    walked = walk(world, fighter, goblin)

    assert walked == (Cell(1, -1),)
    assert fighter.economy.movement_ft == 5


def test_a_move_never_ends_in_a_space_it_may_pass() -> None:
    world, fighter, goblin = closing(Cell(4, 0))
    for y in (-1, 0, 1):
        world.seat(creature(f"ally{y}", cell=Cell(3, y)), "heroes")

    walk(world, fighter, goblin)

    assert fighter.position not in {Cell(3, y) for y in (-1, 0, 1)}


# --- the drag -------------------------------------------------------------------


def grappling(
    *, foe_size: Size = Size.MEDIUM, speed: int = 30
) -> tuple[World, Creature, Creature, Creature]:
    """The hero at the origin holds the foe east of it; the goblin waits at
    (-5, 0), west."""
    hero = creature("hero", speed=speed)
    foe = creature("foe", cell=Cell(1, 0), size=foe_size)
    goblin = creature("goblin", cell=Cell(-5, 0))
    world = World()
    world.seat(hero, "heroes")
    world.seat(foe, "monsters")
    world.seat(goblin, "monsters")
    ready(hero)
    outcome = Grapple(hero, foe, world=world).execute(rng=ScriptedRng([9]))
    assert outcome is SaveOutcome.FAILURE
    return world, hero, foe, goblin


def test_a_grappler_drags_what_it_holds_at_ten_feet_a_square() -> None:
    world, hero, foe, goblin = grappling()

    walked = walk(world, hero, goblin)

    assert len(walked) == 3
    assert hero.position.x == -3
    assert (foe.position.x - hero.position.x, foe.position.y - hero.position.y) == (
        1,
        0,
    )
    assert hero.economy.movement_ft == 0
    assert [grip.target for grip in grips(hero)] == [foe]


def test_a_tiny_creature_is_dragged_for_nothing_extra() -> None:
    world, hero, foe, goblin = grappling(foe_size=Size.TINY)

    walked = walk(world, hero, goblin)

    assert len(walked) == 4
    assert foe.position.x - hero.position.x == 1
    assert hero.economy.movement_ft == 10


def test_crawling_and_dragging_add() -> None:
    """Prone and dragging: 15 feet a square, two squares of 30 feet."""
    world, hero, foe, goblin = grappling()
    hero.effects.apply(Prone(hero))

    walked = walk(world, hero, goblin)

    assert len(walked) == 2
    assert hero.economy.movement_ft == 0


def test_a_grappler_does_not_retreat_from_what_it_drags() -> None:
    world, hero, foe, _ = grappling()

    assert not Retreat(hero, foe, world=world, keep_ft=15).is_legal()


def test_a_retreat_from_another_drags_too() -> None:
    world, hero, foe, goblin = grappling()
    goblin.position = Cell(-2, 0)

    Retreat(hero, goblin, world=world).execute(rng=ScriptedRng([]))

    assert foe.position.x - hero.position.x == 1
    assert foe.position.y == hero.position.y
    assert [grip.target for grip in grips(hero)] == [foe]


class Tally(HookSource):
    """Counts the `on_leave_reach` offers its owner is made."""

    __slots__ = ("seen",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen = 0

    @hook("on_leave_reach")
    def note(self, ctx: LeaveReachContext) -> R:
        self.seen += 1
        return R.APPLIED


def test_the_dragged_creature_provokes_nothing() -> None:
    """The watcher at (2, 1) has the foe in reach and not the hero: the foe
    leaves its reach on the first step, and the watcher is offered nothing."""
    world, hero, foe, goblin = grappling()
    watcher = creature("watcher", cell=Cell(2, 1))
    world.seat(watcher, "monsters")
    tally = Tally(watcher)
    watcher.attach(tally)

    walk(world, hero, goblin)

    assert foe.position.x < 1
    assert tally.seen == 0


def test_a_walk_stops_when_the_grapple_ends_partway() -> None:
    world, hero, foe, _ = grappling()
    route_ = (Cell(-1, 0), Cell(-2, 0))
    calls = []

    def holding(creature: Creature) -> bool:
        calls.append(creature)
        return len(calls) < 2

    watcher = creature("watcher", cell=Cell(-1, 1))
    world.seat(watcher, "monsters")
    watcher.attach(Tally(watcher))

    walked = walk_provoking(
        hero, route_, world, costs=(10, 10), towing=(foe,), holding=holding
    )

    assert walked == (Cell(-1, 0),)
    assert foe.position == Cell(0, 0)


def test_step_costs_must_match_the_route() -> None:
    hero = creature("hero")
    ready(hero)

    with pytest.raises(ValueError, match="step costs"):
        walk_provoking(hero, (Cell(1, 0),), [], costs=(5, 5))


def test_each_step_is_charged_its_own_cost() -> None:
    hero = creature("hero")
    ready(hero)

    walk_provoking(hero, (Cell(1, 0), Cell(2, 0)), [], costs=(10, 5))

    assert hero.economy.movement_ft == 15


# --- a grappler that falls --------------------------------------------------------


def test_a_grappler_that_dies_lets_go_at_once() -> None:
    world, hero, foe, _ = grappling()

    resolve_damage(hero, {DamageType.BLUDGEONING: 100}, rng=ScriptedRng([]))

    assert hero.dead
    assert not any(isinstance(effect, Grappled) for effect in foe.effects)
    assert not any(isinstance(effect, Grappling) for effect in hero.effects)


class Endure(HookSource):
    """Keeps its owner at 1 hit point whenever it would drop."""

    __slots__ = ()

    @hook("on_reduced_to_zero")
    def endure(self, ctx: ReducedToZeroContext) -> R:
        ctx.prevent("test")
        return R.APPLIED


def test_a_grappler_kept_on_its_feet_keeps_its_grapple() -> None:
    world, hero, foe, _ = grappling()
    hero.attach(Endure(hero))

    resolve_damage(hero, {DamageType.BLUDGEONING: 25}, rng=ScriptedRng([]))

    assert hero.hp == 1
    assert [grip.target for grip in grips(hero)] == [foe]
