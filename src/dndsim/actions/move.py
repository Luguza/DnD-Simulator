"""Movement as a Command: `Move` closes on a creature, `Retreat` backs off one.

Both are a `Walk`: paid in feet rather than an economy slot, planned once for
the positions they are asked about, and walked square by square through
`engine/triggers.py`, provoking unless the walker is Disengaged
(`docs/decisions.md` § w2c2, § w2e). The `move:` clause of an APL picks one
(DESIGN.md § The APL).
"""

from abc import abstractmethod
from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.actions.disengage import disengaged
from dndsim.actions.unarmed import grips, loosen_grapples
from dndsim.core.geometry import (
    Cell,
    Footing,
    distance_ft,
    footprint,
    in_reach,
    reachable,
    route,
    step_costs,
)
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import settle
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import MoveContext
from dndsim.hooks.definitions import ON_MOVE
from dndsim.rules.constants import CELL_SIZE_FT, SIZE_ORDER, SIZE_WIDTH_CELLS
from dndsim.rules.enums import ActionCost, Size

__all__ = ["Move", "Retreat", "Walk"]


class _Ground:
    """What one walk is planned on: the mover's `Footing` and the creatures it
    drags."""

    __slots__ = ("dragging", "footing")

    def __init__(self, footing: Footing, dragging: tuple[Creature, ...]) -> None:
        self.footing = footing
        self.dragging = dragging


class Walk(Action[tuple[Cell, ...]]):
    """`actor` walks a route it plans relative to `target`, as far as its
    movement this turn allows.

    Paid in feet, so `COST` is `None`; legal while Incapacitated, which stops
    no movement. The actor passes through the space of an ally, of an
    Incapacitated or a Tiny creature, or of one two or more sizes larger or
    smaller than itself, and through no other; it stops in nobody's, so a walk
    its movement would end inside one ends at the last square clear of them.
    Each square costs five feet, and five more for each of: a `CRAWLS` effect
    it holds, entering the space of a creature that is neither Tiny nor its
    ally, and dragging. An actor that grapples drags what it holds, at that
    cost unless every creature it drags is Tiny or two or more sizes smaller.
    One holding a `NO_APPROACH` effect walks no step that ends closer to its
    caster than the square it left. `execute()` returns the squares walked
    (`docs/decisions.md` § w2j2).
    """

    __slots__ = ("_planned", "target", "world")

    ID: ClassVar[str] = "move"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, target: Creature, *, world: World) -> None:
        super().__init__(actor)
        self.target = target
        self.world = world
        self._planned: (
            tuple[Cell, Cell, int, tuple[Cell, ...], tuple[int, ...], _Ground] | None
        ) = None

    def is_legal(self) -> bool:
        """Whether the actor can take a step of this walk now. Changes
        nothing."""
        actor = self.actor
        if actor.dead or actor.is_down:
            return False
        return self._permits()

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        if target is actor or target.dead or actor.speed_ft == 0:
            return False
        if not actor.economy.can_move(_step_ft(actor)):
            return False
        return bool(self._walkable()[0])

    def _perform(self, rng: Rng) -> tuple[Cell, ...]:
        actor = self.actor
        walkable, costs, ground = self._walkable()
        walked = walk_provoking(
            actor,
            walkable,
            self.world,
            provokes=not disengaged(actor),
            costs=costs,
            dragging=ground.dragging,
        )
        if walked:
            loosen_grapples(self.world, actor)
            moved = MoveContext(
                creature=actor, route=walked, distance_ft=len(walked) * CELL_SIZE_FT
            )
            actor.dispatch(ON_MOVE, moved)
            settle(
                moved,
                "vetoed at on_move, which cannot be called off: the creature "
                "has already moved.",
                actor,
            )
            moved.close()
        return walked

    def _walkable(self) -> tuple[tuple[Cell, ...], tuple[int, ...], _Ground]:
        """The route as far as this turn's movement pays for it and fear
        allows, ending on a square the actor may stop in; its step costs; and
        the ground it was planned on."""
        actor = self.actor
        planned, costs, ground = self._route()
        budget = actor.economy.movement_ft
        paid = 0
        steps = 0
        for cost in costs:
            paid += cost
            if paid > budget:
                break
            steps += 1
        whole = steps == len(planned)
        planned = planned[:steps]
        sources = _sources_of_fear(self.world, actor)
        if sources:
            planned = _unafraid(actor, planned, sources)
            whole = whole and steps == len(planned)
        if not whole:
            # A planned route ends where the actor may stop; a cut one may not.
            stands = ground.footing.stands
            end = len(planned)
            while end and not stands(planned[end - 1]):
                end -= 1
            planned = planned[:end]
        return planned, costs[: len(planned)], ground

    def _route(self) -> tuple[tuple[Cell, ...], tuple[int, ...], _Ground]:
        """The planned route, its step costs and its ground, re-planned only
        when either creature has moved or the movement left has changed since
        it was."""
        actor = self.actor
        origin = actor.position
        movement = actor.economy.movement_ft
        planned = self._planned
        if (
            planned is not None
            and planned[0] == origin
            and planned[1] == self.target.position
            and planned[2] == movement
        ):
            return planned[3], planned[4], planned[5]
        ground = _ground(self.world, actor)
        walked = self._plan(origin, ground)
        costs = step_costs(walked, ground.footing)
        self._planned = (origin, self.target.position, movement, walked, costs, ground)
        return walked, costs, ground

    @abstractmethod
    def _plan(self, origin: Cell, ground: _Ground) -> tuple[Cell, ...]:
        """The route from `origin` on `ground`; empty for none. A route that
        ends where the actor may stop, which `Retreat` plans within the
        movement left and `Move` does not."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} by {self.actor.name} re {self.target.name}>"


class Move(Walk):
    """`actor` walks toward `target` until it has it in reach.

    The destination is the square with the cheapest route whose footprint has
    `target` in the actor's reach — between footprints — and stands in nobody's
    space; of equal routes, the one ending nearest, then lowest `y`, then `x`.
    No step is offered when the actor already has `target` in reach, or when
    no such square can be reached (`docs/decisions.md` § w2c2).
    """

    __slots__ = ()

    def _plan(self, origin: Cell, ground: _Ground) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
        if in_reach(
            origin,
            target.position,
            actor.reach_ft,
            origin_size=actor.size,
            destination_size=target.size,
        ):
            return ()
        footing = ground.footing
        floor_ft = footing.step_ft
        best: tuple[Cell, ...] = ()
        best_ft = 0
        for steps, y, x in _stands(actor, target, footing):
            # No route costs less than the Chebyshev floor `_stands` sorts by,
            # at the cheapest step, so once that reaches the best route found,
            # nothing later can beat it.
            if best and steps * floor_ft >= best_ft:
                break
            destination = Cell(x, y)
            walked = route(origin, destination, footing=footing)
            if not walked or walked[-1] != destination:
                continue
            cost_ft = sum(step_costs(walked, footing))
            if not best or cost_ft < best_ft:
                best, best_ft = walked, cost_ft
        return best


class Retreat(Walk):
    """`actor` walks away from `target` until it is `keep_ft` from it, or, with
    no `keep_ft`, as far from it as this turn's movement takes it.

    The destination is searched among the squares this turn's movement
    reaches: of those at least `keep_ft` away, the nearest to walk to, then
    the farthest from `target`; with none, the farthest from `target`, then
    the nearest to walk to; then lowest `y`, then `x`. No step is offered
    when the actor is already `keep_ft` away, or when no square it can reach
    is farther from `target` than where it stands (`docs/decisions.md`
    § w2e).
    """

    __slots__ = ("keep_ft",)

    def __init__(
        self,
        actor: Creature,
        target: Creature,
        *,
        world: World,
        keep_ft: int | None = None,
    ) -> None:
        if keep_ft is not None and keep_ft < CELL_SIZE_FT:
            raise ValueError(f"a retreat keeps 5 ft or more, got {keep_ft}")
        super().__init__(actor, target, world=world)
        self.keep_ft = keep_ft

    def _plan(self, origin: Cell, ground: _Ground) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
        if any(held is target for held in ground.dragging):
            # It moves with the actor: no square is farther from it.
            return ()
        size = actor.size
        threat = target.position
        threat_size = target.size

        def away(cell: Cell) -> int:
            return distance_ft(
                cell, threat, origin_size=size, destination_size=threat_size
            )

        here = away(origin)
        keep = self.keep_ft
        if keep is not None and here >= keep:
            return ()
        reach = reachable(origin, actor.economy.movement_ft, ground.footing)
        cost_ft = reach.cost_ft
        if not cost_ft:
            return ()
        far_enough = (
            [] if keep is None else [cell for cell in cost_ft if away(cell) >= keep]
        )
        if far_enough:
            best = min(far_enough, key=lambda c: (cost_ft[c], -away(c), c.y, c.x))
        else:
            best = min(cost_ft, key=lambda c: (-away(c), cost_ft[c], c.y, c.x))
            if away(best) <= here:
                return ()
        return reach.route_to(best)


def _step_ft(actor: Creature) -> int:
    """What one square costs `actor`: double while an effect it holds
    `CRAWLS`."""
    for effect in actor.effects:
        if effect.CRAWLS:
            return 2 * CELL_SIZE_FT
    return CELL_SIZE_FT


def _sources_of_fear(world: World, actor: Creature) -> tuple[Creature, ...]:
    """The seated casters of the `NO_APPROACH` effects `actor` holds."""
    sources: list[Creature] = []
    for effect in actor.effects:
        if effect.NO_APPROACH and effect.caster is not None:
            source = world.holder(effect.caster)
            if source is not None:
                sources.append(source)
    return tuple(sources)


def _unafraid(
    actor: Creature, planned: tuple[Cell, ...], sources: tuple[Creature, ...]
) -> tuple[Cell, ...]:
    """`planned` up to the first step that ends closer to one of `sources`
    than the square it left."""
    here = actor.position
    size = actor.size
    for index, square in enumerate(planned):
        for source in sources:
            before = distance_ft(
                here, source.position, origin_size=size, destination_size=source.size
            )
            after = distance_ft(
                square, source.position, origin_size=size, destination_size=source.size
            )
            if after < before:
                return planned[:index]
        here = square
    return planned


def _ground(world: World, mover: Creature) -> _Ground:
    """The ground `mover` walks on now: whose space it passes, whose costs it
    more, and what it drags (XPHB "Moving Around Other Creatures", "Difficult
    Terrain", Rules Glossary "Grappled")."""
    dragging = tuple(grip.target for grip in grips(mover) if not grip.target.dead)
    side = world.side_of(mover) if mover in world else None
    rank = SIZE_ORDER[mover.size]
    blocked: set[Cell] = set()
    crowded: set[Cell] = set()
    difficult: set[Cell] = set()
    for creature in world:
        if creature is mover or creature.dead:
            continue
        if dragging and any(creature is held for held in dragging):
            continue
        size = creature.size
        cells = footprint(creature.position, size)
        if side is not None and world.side_of(creature) == side or size is Size.TINY:
            crowded.update(cells)
        elif abs(SIZE_ORDER[size] - rank) >= 2 or creature.incapacitated:
            crowded.update(cells)
            difficult.update(cells)
        else:
            blocked.update(cells)
    step_ft = _step_ft(mover)
    if any(not _carried(mover, held) for held in dragging):
        step_ft += CELL_SIZE_FT
    origin = mover.position
    towing = tuple(
        (held.position.x - origin.x, held.position.y - origin.y, held.size)
        for held in dragging
    )
    return _Ground(
        Footing(
            blocked=blocked,
            crowded=crowded,
            difficult=difficult,
            size=mover.size,
            towing=towing,
            step_ft=step_ft,
        ),
        dragging,
    )


def _carried(grappler: Creature, held: Creature) -> bool:
    """Whether `held` drags at no extra cost: Tiny, or two or more sizes
    smaller than `grappler`."""
    return (
        held.size is Size.TINY or SIZE_ORDER[grappler.size] - SIZE_ORDER[held.size] >= 2
    )


def _stands(
    actor: Creature, target: Creature, footing: Footing
) -> list[tuple[int, int, int]]:
    """The anchors from which `actor`'s footprint has `target` in reach and
    `footing` lets it stop, as `(steps, y, x)`: `steps` is the Chebyshev
    distance in squares from the actor's anchor, `distance_ft()`'s between
    anchors, and the list is sorted on it, then lowest `y`, then lowest `x`.
    Tuples, so that no `Cell` is built for an anchor the plan never routes to
    (`docs/decisions.md` § w2i2)."""
    width = SIZE_WIDTH_CELLS[actor.size]
    reach_cells = actor.reach_ft // CELL_SIZE_FT
    anchor = target.position
    target_width = SIZE_WIDTH_CELLS[target.size]
    # Exactly the anchors whose footprint has the target in reach: its far
    # edge within `reach_cells` of the target's near one, on each axis, which
    # is `distance_ft`'s gap between footprints (`docs/decisions.md` § w1d).
    xs = range(
        anchor.x - reach_cells - width + 1, anchor.x + target_width + reach_cells
    )
    ys = range(
        anchor.y - reach_cells - width + 1, anchor.y + target_width + reach_cells
    )
    origin = actor.position
    origin_x, origin_y = origin.x, origin.y
    taken = {(cell.x, cell.y) for cell in footing.unstandable()}
    found = [
        (max(abs(x - origin_x), abs(y - origin_y)), y, x)
        for y in ys
        for x in xs
        if (x, y) not in taken
    ]
    found.sort()
    return found
