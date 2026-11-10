"""Movement as a Command: `Move` closes on a creature, `Retreat` backs off one.

Both are a `Walk`: paid in feet rather than an economy slot, planned once for
the positions they are asked about, and walked square by square through
`engine/triggers.py`, provoking unless the walker is Disengaged
(`docs/decisions.md` § w2c2, § w2e). The `move:` clause of an APL picks one
(DESIGN.md § The APL).
"""

from abc import abstractmethod
from collections import deque
from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.actions.disengage import disengaged
from dndsim.core.geometry import Cell, distance_ft, footprint, in_reach, route
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import settle
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import MoveContext
from dndsim.hooks.definitions import ON_MOVE
from dndsim.rules.constants import CELL_SIZE_FT, SIZE_WIDTH_CELLS
from dndsim.rules.enums import ActionCost

__all__ = ["Move", "Retreat", "Walk"]

_NEIGHBOURS = tuple(
    (dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx, dy) != (0, 0)
)


class Walk(Action[tuple[Cell, ...]]):
    """`actor` walks a route it plans relative to `target`, as far as its
    movement this turn allows.

    Paid in feet, so `COST` is `None`; legal while Incapacitated, which stops
    no movement. Every other living creature's space is impassable. An actor
    holding a `CRAWLS` effect pays ten feet a square, and one holding a
    `NO_APPROACH` effect walks no step that ends closer to its caster than the
    square it left. `execute()` returns the squares walked.
    """

    __slots__ = ("_planned", "target", "world")

    ID: ClassVar[str] = "move"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, target: Creature, *, world: World) -> None:
        super().__init__(actor)
        self.target = target
        self.world = world
        self._planned: tuple[Cell, Cell, int, tuple[Cell, ...]] | None = None

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
        step_ft = _step_ft(actor)
        if not actor.economy.can_move(step_ft):
            return False
        return bool(self._walkable(step_ft))

    def _perform(self, rng: Rng) -> tuple[Cell, ...]:
        actor = self.actor
        step_ft = _step_ft(actor)
        walked = walk_provoking(
            actor,
            self._walkable(step_ft),
            self.world,
            provokes=not disengaged(actor),
            step_ft=step_ft,
        )
        if walked:
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

    def _walkable(self, step_ft: int) -> tuple[Cell, ...]:
        """The route as far as this turn's movement pays for it at `step_ft` a
        square and fear allows."""
        actor = self.actor
        planned = self._route()[: actor.economy.movement_ft // step_ft]
        sources = _sources_of_fear(self.world, actor)
        if not sources:
            return planned
        here = actor.position
        size = actor.size
        for index, square in enumerate(planned):
            for source in sources:
                before = distance_ft(
                    here,
                    source.position,
                    origin_size=size,
                    destination_size=source.size,
                )
                after = distance_ft(
                    square,
                    source.position,
                    origin_size=size,
                    destination_size=source.size,
                )
                if after < before:
                    return planned[:index]
            here = square
        return planned

    def _route(self) -> tuple[Cell, ...]:
        """The planned route, re-planned only when either creature has moved
        or the movement left has changed since it was."""
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
            return planned[3]
        walked = self._plan(origin)
        self._planned = (origin, self.target.position, movement, walked)
        return walked

    @abstractmethod
    def _plan(self, origin: Cell) -> tuple[Cell, ...]:
        """The route from `origin`, ignoring the movement left; empty for none."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} by {self.actor.name} re {self.target.name}>"


class Move(Walk):
    """`actor` walks toward `target` until it has it in reach.

    The destination is the square with the shortest route whose footprint has
    `target` in the actor's reach — between footprints — and stands in nobody's
    space; of equal routes, the one ending nearest, then lowest `y`, then `x`.
    No step is offered when the actor already has `target` in reach, or when
    no such square can be reached (`docs/decisions.md` § w2c2).
    """

    __slots__ = ()

    def _plan(self, origin: Cell) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
        if in_reach(
            origin,
            target.position,
            actor.reach_ft,
            origin_size=actor.size,
            destination_size=target.size,
        ):
            return ()
        blocked = _occupied(self.world, actor)
        best: tuple[Cell, ...] = ()
        for steps, y, x in _stands(actor, target, blocked):
            # No route is shorter than the Chebyshev floor `_stands` sorts by,
            # so once that floor reaches the best route found, nothing later
            # can beat it.
            if best and steps >= len(best):
                break
            destination = Cell(x, y)
            walked = route(origin, destination, blocked=blocked, size=actor.size)
            reached = walked and walked[-1] == destination
            if reached and (not best or len(walked) < len(best)):
                best = walked
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

    def _plan(self, origin: Cell) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
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
        steps = actor.economy.movement_ft // _step_ft(actor)
        if steps == 0:
            return ()
        blocked = _occupied(self.world, actor)
        came_from: dict[Cell, Cell] = {}
        depth = {origin: 0}
        frontier = deque((origin,))
        while frontier:
            current = frontier.popleft()
            if depth[current] == steps:
                continue
            for dx, dy in _NEIGHBOURS:
                candidate = Cell(current.x + dx, current.y + dy)
                if candidate in depth or any(
                    cell in blocked for cell in footprint(candidate, size)
                ):
                    continue
                depth[candidate] = depth[current] + 1
                came_from[candidate] = current
                frontier.append(candidate)
        reached = [cell for cell in depth if cell != origin]
        far_enough = (
            [] if keep is None else [cell for cell in reached if away(cell) >= keep]
        )
        if far_enough:
            best = min(far_enough, key=lambda c: (depth[c], -away(c), c.y, c.x))
        else:
            best = min(
                reached, key=lambda c: (-away(c), depth[c], c.y, c.x), default=origin
            )
            if away(best) <= here:
                return ()
        walked = [best]
        while (previous := came_from[walked[-1]]) != origin:
            walked.append(previous)
        walked.reverse()
        return tuple(walked)


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


def _occupied(world: World, mover: Creature) -> frozenset[Cell]:
    """Every square a living creature other than `mover` stands in."""
    return frozenset(
        cell
        for creature in world
        if creature is not mover and not creature.dead
        for cell in footprint(creature.position, creature.size)
    )


def _stands(
    actor: Creature, target: Creature, blocked: frozenset[Cell]
) -> list[tuple[int, int, int]]:
    """The anchors from which `actor`'s footprint has `target` in reach and
    stands in nobody's space, as `(steps, y, x)`: `steps` is the Chebyshev
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
    if width == 1:
        taken = {(cell.x, cell.y) for cell in blocked}
        found = [
            (max(abs(x - origin_x), abs(y - origin_y)), y, x)
            for y in ys
            for x in xs
            if (x, y) not in taken
        ]
    else:
        size = actor.size
        found = [
            (max(abs(x - origin_x), abs(y - origin_y)), y, x)
            for y in ys
            for x in xs
            if not any(square in blocked for square in footprint(Cell(x, y), size))
        ]
    found.sort()
    return found
