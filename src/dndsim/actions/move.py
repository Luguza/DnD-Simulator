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
    distance_ft,
    footprint,
    in_reach,
    route,
    routes_within,
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


class _Plan:
    """One planned route, what each step costs, and the squares it may not
    stop in."""

    __slots__ = ("costs", "crowded", "key", "step", "walked")

    def __init__(
        self,
        key: tuple[object, ...],
        walked: tuple[Cell, ...],
        costs: tuple[int, ...],
        crowded: frozenset[Cell],
    ) -> None:
        self.key = key
        self.walked = walked
        self.costs = costs
        self.crowded = crowded
        # Every step the same price, or 0 when they differ.
        self.step = costs[0] if costs and costs.count(costs[0]) == len(costs) else 0


class _Terrain:
    """The other creatures as a walker meets them (XPHB "Moving Around Other
    Creatures", "Difficult Terrain"): the squares it may not enter, the
    squares it may pass but not stop in, and those that cost it extra."""

    __slots__ = ("blocked", "crowded", "difficult", "passable", "towing")

    def __init__(
        self, world: World, actor: Creature, towed: tuple[Creature, ...]
    ) -> None:
        side = world.side_of(actor) if actor in world else None
        own_size = actor.size
        blocked: list[Cell] = []
        difficult: list[Cell] = []
        crowded: list[Cell] = []
        for creature in world:
            if creature is actor or creature.dead or creature in towed:
                continue
            size = creature.size
            if size is Size.MEDIUM or size is Size.SMALL or size is Size.TINY:
                cells: tuple[Cell, ...] = (creature.position,)
            else:
                cells = footprint(creature.position, size)
            crowded.extend(cells)
            if side is not None and world.side_of(creature) == side:
                continue
            if size is Size.TINY:
                continue
            if (
                size is not own_size
                and abs(SIZE_ORDER[size] - SIZE_ORDER[own_size]) >= 2
            ) or creature.incapacitated:
                difficult.extend(cells)
            else:
                blocked.extend(cells)
        self.blocked = frozenset(blocked)
        self.difficult = frozenset(difficult)
        self.crowded = frozenset(crowded)
        self.passable = len(self.crowded) != len(self.blocked)
        origin = actor.position
        self.towing = tuple(
            (Cell(c.position.x - origin.x, c.position.y - origin.y), c.size)
            for c in towed
        )

    def stands(self, anchor: Cell, size: Size) -> bool:
        """Whether a walker of `size` at `anchor`, with what it tows, stands
        in nobody's space."""
        crowded = self.crowded
        if any(cell in crowded for cell in footprint(anchor, size)):
            return False
        return not any(
            cell in crowded
            for offset, towed in self.towing
            for cell in footprint(Cell(anchor.x + offset.x, anchor.y + offset.y), towed)
        )


class Walk(Action[tuple[Cell, ...]]):
    """`actor` walks a route it plans relative to `target`, as far as its
    movement this turn allows.

    Paid in feet, so `COST` is `None`; legal while Incapacitated, which stops
    no movement. A step costs five feet, and five more for each of: an effect
    the actor holds that `CRAWLS`, a creature it drags that is neither Tiny
    nor two or more sizes smaller, and a footprint entering the space of a
    creature it may pass that is neither Tiny nor its ally. It may pass an
    ally, a Tiny creature, an Incapacitated one, or one two or more sizes
    larger or smaller, and no one else, and it ends in nobody's space. One
    holding a `NO_APPROACH` effect walks no step that ends closer to its
    caster than the square it left. Every creature it grapples is dragged with
    it. `execute()` returns the squares walked (`docs/decisions.md` § w2j2).
    """

    __slots__ = ("_planned", "target", "world")

    ID: ClassVar[str] = "move"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, target: Creature, *, world: World) -> None:
        super().__init__(actor)
        self.target = target
        self.world = world
        self._planned: _Plan | None = None

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
        if not actor.economy.can_move(CELL_SIZE_FT):
            return False
        return bool(self._walkable()[0])

    def _perform(self, rng: Rng) -> tuple[Cell, ...]:
        actor = self.actor
        squares, costs = self._walkable()
        towed = self._towed()
        walked = walk_provoking(
            actor,
            squares,
            self.world,
            provokes=not disengaged(actor),
            costs=costs,
            towing=towed,
            holding=lambda creature: _holds(actor, creature),
        )
        if walked:
            loosen_grapples(self.world, actor)
            for creature in towed:
                loosen_grapples(self.world, creature)
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

    def _walkable(self) -> tuple[tuple[Cell, ...], tuple[int, ...]]:
        """The route as far as this turn's movement pays for it, fear allows,
        and the actor can stop, with what each step costs."""
        actor = self.actor
        plan = self._route()
        budget = actor.economy.movement_ft
        if plan.step:
            end = budget // plan.step
        else:
            end = 0
            for cost in plan.costs:
                budget -= cost
                if budget < 0:
                    break
                end += 1
        planned = plan.walked[:end]
        sources = _sources_of_fear(self.world, actor)
        if sources:
            planned = _unafraid(actor, planned, sources)
        crowded = plan.crowded
        if crowded:
            size = actor.size
            # A walk the movement left would stop inside a creature's space
            # stops at the last square clear of them (XPHB "Moving Around Other
            # Creatures").
            while planned and any(
                cell in crowded for cell in footprint(planned[-1], size)
            ):
                planned = planned[:-1]
        return planned, plan.costs[: len(planned)]

    def _towed(self) -> tuple[Creature, ...]:
        """The living creatures the actor grapples, which it drags."""
        held = grips(self.actor)
        if not held:
            return ()
        return tuple(grip.target for grip in held if not grip.target.dead)

    def _route(self) -> _Plan:
        """The planned route, re-planned only when either creature has moved,
        the movement left has changed, or what the actor drags has, since it
        was."""
        actor = self.actor
        towed = self._towed()
        key = (actor.position, self.target.position, actor.economy.movement_ft, towed)
        planned = self._planned
        if planned is not None and planned.key == key:
            return planned
        if self._arrived(towed):
            plan = self._planned = _Plan(key, (), (), frozenset())
            return plan
        terrain = _Terrain(self.world, actor, towed)
        step_ft = _step_ft(actor, towed)
        walked = self._plan(actor.position, terrain, step_ft)
        costs = step_costs(
            walked, size=actor.size, difficult=terrain.difficult, step_ft=step_ft
        )
        # With nobody passable on the field, no square of a route is in anyone's
        # space, and `_walkable()` has nothing to trim.
        crowded = terrain.crowded if terrain.passable else frozenset()
        plan = self._planned = _Plan(key, walked, costs, crowded)
        return plan

    @abstractmethod
    def _arrived(self, towed: tuple[Creature, ...]) -> bool:
        """Whether the walk has nowhere to go, asked before anything is
        planned."""

    @abstractmethod
    def _plan(self, origin: Cell, terrain: _Terrain, step_ft: int) -> tuple[Cell, ...]:
        """The route from `origin`, ignoring the movement left; empty for none."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} by {self.actor.name} re {self.target.name}>"


class Move(Walk):
    """`actor` walks toward `target` until it has it in reach.

    The destination is the square with the cheapest route whose footprint has
    `target` in the actor's reach — between footprints — and where the actor,
    and what it drags, stand in nobody's space; of equal routes, the one
    ending nearest, then lowest `y`, then `x`. No step is offered when the
    actor already has `target` in reach, or when no such square can be reached
    (`docs/decisions.md` § w2c2, § w2j2).
    """

    __slots__ = ()

    def _arrived(self, towed: tuple[Creature, ...]) -> bool:
        actor, target = self.actor, self.target
        return in_reach(
            actor.position,
            target.position,
            actor.reach_ft,
            origin_size=actor.size,
            destination_size=target.size,
        )

    def _plan(self, origin: Cell, terrain: _Terrain, step_ft: int) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
        size = actor.size
        towing = terrain.towing
        best: tuple[Cell, ...] = ()
        best_ft = 0
        for steps, y, x in _stands(actor, target, terrain.crowded):
            # No route costs less than the Chebyshev floor `_stands` sorts by,
            # so once that floor reaches the best route found, nothing later
            # can beat it.
            if best and steps * step_ft >= best_ft:
                break
            destination = Cell(x, y)
            if towing and not terrain.stands(destination, size):
                continue
            walked = route(
                origin,
                destination,
                blocked=terrain.blocked,
                size=size,
                difficult=terrain.difficult,
                step_ft=step_ft,
                towing=towing,
                crowded=terrain.crowded,
            )
            if not walked or walked[-1] != destination:
                continue
            spent = (
                sum(
                    step_costs(
                        walked, size=size, difficult=terrain.difficult, step_ft=step_ft
                    )
                )
                if terrain.difficult
                else len(walked) * step_ft
            )
            if not best or spent < best_ft:
                best, best_ft = walked, spent
        return best


class Retreat(Walk):
    """`actor` walks away from `target` until it is `keep_ft` from it, or, with
    no `keep_ft`, as far from it as this turn's movement takes it.

    The destination is searched among the squares this turn's movement
    reaches: of those at least `keep_ft` away, the nearest to walk to, then
    the farthest from `target`; with none, the farthest from `target`, then
    the nearest to walk to; then lowest `y`, then `x`. No step is offered
    when the actor is already `keep_ft` away, when it drags `target`, or when
    no square it can reach is farther from `target` than where it stands
    (`docs/decisions.md` § w2e, § w2j2).
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

    def _arrived(self, towed: tuple[Creature, ...]) -> bool:
        actor, target = self.actor, self.target
        keep = self.keep_ft
        if target in towed:
            return True
        return keep is not None and (
            distance_ft(
                actor.position,
                target.position,
                origin_size=actor.size,
                destination_size=target.size,
            )
            >= keep
        )

    def _plan(self, origin: Cell, terrain: _Terrain, step_ft: int) -> tuple[Cell, ...]:
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
        budget = actor.economy.movement_ft
        if budget < step_ft:
            return ()
        reached = routes_within(
            origin,
            budget,
            blocked=terrain.blocked,
            size=size,
            difficult=terrain.difficult,
            step_ft=step_ft,
            towing=terrain.towing,
            crowded=terrain.crowded,
        )
        cost = reached.cost
        stands = [
            cell for cell in cost if cell != origin and terrain.stands(cell, size)
        ]
        far_enough = (
            [] if keep is None else [cell for cell in stands if away(cell) >= keep]
        )
        if far_enough:
            best = min(far_enough, key=lambda c: (cost[c], -away(c), c.y, c.x))
        else:
            best = min(
                stands, key=lambda c: (-away(c), cost[c], c.y, c.x), default=origin
            )
            if away(best) <= here:
                return ()
        return reached.path(best)


def _step_ft(actor: Creature, towed: tuple[Creature, ...]) -> int:
    """What one square costs `actor` before terrain: five feet more while an
    effect it holds `CRAWLS`, and five more while it drags a creature that is
    neither Tiny nor two or more sizes smaller (XPHB Rules Glossary
    "Grappled")."""
    step = CELL_SIZE_FT
    for effect in actor.effects:
        if effect.CRAWLS:
            step += CELL_SIZE_FT
            break
    rank = SIZE_ORDER[actor.size]
    if any(
        creature.size is not Size.TINY and rank - SIZE_ORDER[creature.size] < 2
        for creature in towed
    ):
        step += CELL_SIZE_FT
    return step


def _holds(actor: Creature, creature: Creature) -> bool:
    """Whether `actor` still grapples `creature`, alive."""
    return not creature.dead and any(grip.target is creature for grip in grips(actor))


def _unafraid(
    actor: Creature, planned: tuple[Cell, ...], sources: tuple[Creature, ...]
) -> tuple[Cell, ...]:
    """`planned` up to its first step that ends closer to a source of fear."""
    here = actor.position
    size = actor.size
    for index, square in enumerate(planned):
        for source in sources:
            before = distance_ft(
                here, source.position, origin_size=size, destination_size=source.size
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


def _sources_of_fear(world: World, actor: Creature) -> tuple[Creature, ...]:
    """The seated casters of the `NO_APPROACH` effects `actor` holds."""
    sources: list[Creature] = []
    for effect in actor.effects:
        if effect.NO_APPROACH and effect.caster is not None:
            source = world.holder(effect.caster)
            if source is not None:
                sources.append(source)
    return tuple(sources)


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
