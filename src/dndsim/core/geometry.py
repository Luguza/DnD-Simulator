"""The grid: where a creature stands, how far away another one is, and the route
between the two.

A first cut, in W0 rather than W1, because the performance spike is worthless
without it: `w0i` measures an attack loop that includes distance and reach
queries and one real movement per turn, and a stub that answers `5` to every
question measures nothing (ROADMAP w0i, DESIGN.md § Performance). `w1d` completes
this module with line of sight, the cone/sphere/line templates and the full
route resolution; nothing here is rewritten by that task.

**Distance is Chebyshev, times five.** On a battle grid each square is 5 feet and
a diagonal step costs the same as an orthogonal one, so the distance between two
cells is the larger of the two axis differences (XPHB, the battle-grid rules in
"Combat"). The 5-10-5 alternating diagonal from the *Dungeon Master's Guide* is a
variant and is deliberately not implemented: it is not the default the rules
present, it makes distance depend on the order steps are taken in, and every
number this simulator reports would then be conditional on a variant rule nobody
asked for.

**A creature occupies one cell.** Size is on the stat block and is read by
nothing here yet; a Large creature really occupies 2x2 and that is `w1d`'s to
add, along with the reach and template consequences. Until then every distance is
measured between the cells two creatures stand in, which is exact for the Small
and Medium combatants the W1-W3 slice fights with.

**Movement is a decision, not a pathfinding problem** (DESIGN.md § APL). The APL's
`move:` clause picks a *destination*; this module walks the shortest legal route
to it, and there is no tactical pathing here — nor is any planned. What that
means concretely is that `route()` answers one question, "how do I get there",
and never the question of where to go. It answers it greedily first, because on
an open grid the greedy diagonal walk is already a shortest path and costs one
step per cell, and falls back to a bounded breadth-first search when something
is in the way — a creature going around a body rather than solving a maze. A
destination nothing reaches leaves the creature as far along as it got, because
that is what a creature with movement left and nowhere to go does.

Sources: XPHB (2024) "Combat" battle-grid rules; DESIGN.md § APL and mid-attack
decisions, § Performance; ROADMAP w0g2, w1d.
"""

from collections import deque
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Final

from dndsim.rules.constants import CELL_SIZE_FT

__all__ = [
    "SEARCH_CELLS",
    "Cell",
    "distance_ft",
    "in_reach",
    "route",
    "steps_within",
]


@dataclass(frozen=True, slots=True)
class Cell:
    """One 5-foot square, addressed by its column and row.

    A **Value Object**: frozen, hashable, and safely shared, so a set of blocked
    cells is a set of these and the identity of a square is its coordinates
    rather than an object somebody has to hold on to. It satisfies the `Cell`
    protocol `hooks/contexts.py` declares, which is what `MoveContext.route` and
    `LeaveReachContext.origin` carry.
    """

    x: int
    y: int

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"


def distance_ft(origin: Cell, destination: Cell) -> int:
    """The distance between two cells in feet.

    The larger axis difference times 5: on the battle grid a diagonal step is
    one square like any other, so the distance from (0, 0) to (3, 3) is 15 feet
    and not 21.
    """
    return CELL_SIZE_FT * max(
        abs(origin.x - destination.x), abs(origin.y - destination.y)
    )


def in_reach(origin: Cell, destination: Cell, reach_ft: int) -> bool:
    """Whether `destination` is within `reach_ft` of `origin`.

    The one query the attack pipeline, the opportunity-attack trigger and the
    APL's `enemies.in_reach` selector all ask, in one place so that "reach" means
    the same thing to all three. Reach is inclusive: a 5-foot reach covers the
    eight squares around you, and a Reach weapon's 10 feet covers the ring
    beyond them.
    """
    return distance_ft(origin, destination) <= reach_ft


def steps_within(budget_ft: int) -> int:
    """How many cells of a route a creature with `budget_ft` of movement covers.

    Every step of a route costs one square whatever its direction, so this is a
    division — written here rather than at each call site because it is the one
    place the grid's resolution meets a movement budget.
    """
    return max(budget_ft, 0) // CELL_SIZE_FT


_ORIGIN_STEP: Final = (0, 0)

_NEIGHBOURS: Final = (
    (1, 1),
    (1, 0),
    (1, -1),
    (0, 1),
    (0, -1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
)
"""The eight steps out of a cell, in a **fixed** order.

Fixed because the search below breaks ties between equally short routes by the
order it expands in, and a run that reproduces byte for byte from a seed cannot
have a creature's path depend on a set's iteration history (DESIGN.md §
Determinism is structural).
"""

SEARCH_CELLS: Final = 400
"""How many cells `route()` will examine before giving up on a destination.

A 20x20 area, which is more grid than any creature with a 60-foot Speed can
cross in a turn, so the cap only ever fires on a destination that is walled off
rather than merely far. It exists because the grid is unbounded: without it, a
destination enclosed by other creatures would be an infinite search on the one
path a turn loop walks every round.
"""


def route(
    origin: Cell,
    destination: Cell,
    *,
    blocked: AbstractSet[Cell] = frozenset(),
) -> tuple[Cell, ...]:
    """The shortest legal route from `origin` to `destination`, origin excluded.

    Two walks, because the common case and the hard case deserve different
    prices. The **greedy** walk steps diagonally toward the destination and
    sidesteps what is in the way; on an open Chebyshev grid that walk *is* the
    shortest route, and it costs one step per cell rather than a search.
    Otherwise a breadth-first **search** over the eight neighbours finds the
    shortest legal route if one exists within `SEARCH_CELLS`. The fast path is
    what keeps a per-turn movement query off `w0i`'s budget; the search is what
    keeps "shortest legal route" true rather than aspirational, because a melee
    creature that stands still behind an ally reports zero damage and blames the
    build (DESIGN.md § APL).

    **The greedy walk is trusted only when its length proves it optimal** — when
    it took exactly `distance_ft(origin, destination)` worth of steps, the
    Chebyshev floor no route can beat. Arriving is not enough: a body diagonally
    adjacent can send the sidesteps the long way round, reaching the destination
    in three squares where two exist, and a square of movement lost to that is a
    melee creature left one square short of the target it chose. The comparison
    costs one subtraction on the path that was already going to return.

    **A route that does not arrive is a route that got as far as it could.** If
    nothing reaches the destination the greedy walk's partial path is returned,
    which is what a creature with movement and nowhere to go does. The caller
    tells the two apart by looking: `walked and walked[-1] == destination`. That
    distinction is the turn loop's to act on (w2c) — an attack offered against a
    target it never reached is the bug this sentence exists to prevent.

    `blocked` is *what may not be entered* — any set of cells, since the turn
    loop builds one per move — and deciding what goes in it belongs to the
    caller: an enemy's square is blocked, an ally's is not, and the engine (w2c)
    knows which is which. The route is the whole path regardless of how much
    movement the creature has; the turn loop truncates it with `steps_within()`,
    because a creature that runs out of movement halfway has still crossed the
    cells it crossed and provoked on the way out of everyone's reach.
    """
    if destination in blocked:
        raise ValueError(
            f"cannot route to {destination}: it is blocked. A destination is "
            "chosen by the APL's move: clause from cells a creature may stand "
            "in, so an occupied one is a selector that has already gone wrong."
        )
    if origin == destination:
        return ()
    walked = _greedy(origin, destination, blocked)
    if len(walked) * CELL_SIZE_FT == distance_ft(origin, destination):
        return walked
    found = _search(origin, destination, blocked)
    return walked if found is None else found


def _greedy(
    origin: Cell, destination: Cell, blocked: AbstractSet[Cell]
) -> tuple[Cell, ...]:
    """Walk toward the destination, giving up an axis to get round what blocks.

    It terminates without a visited set and cannot cycle: every candidate step
    closes the axis it is chosen for, so the distance on that axis strictly
    decreases each step until it is zero and the other axis takes over.
    """
    walked: list[Cell] = []
    current = origin
    while current != destination:
        step = _step(current, destination, blocked)
        if step is None:
            break
        walked.append(step)
        current = step
    return tuple(walked)


def _search(
    origin: Cell, destination: Cell, blocked: AbstractSet[Cell]
) -> tuple[Cell, ...] | None:
    """Breadth-first, so the first route found is a shortest one, or `None`.

    Breadth-first rather than A*: the frontier is capped at a few hundred cells,
    where the heuristic buys less than the branch it costs, and a queue with no
    priority is one fewer thing to get subtly wrong on the path a fight walks
    every round.
    """
    came_from: dict[Cell, Cell] = {}
    seen = {origin}
    frontier = deque((origin,))
    while frontier and len(seen) < SEARCH_CELLS:
        current = frontier.popleft()
        for step_x, step_y in _NEIGHBOURS:
            candidate = Cell(current.x + step_x, current.y + step_y)
            if candidate in seen or candidate in blocked:
                continue
            came_from[candidate] = current
            if candidate == destination:
                return _retrace(came_from, origin, destination)
            seen.add(candidate)
            frontier.append(candidate)
    return None


def _retrace(
    came_from: dict[Cell, Cell], origin: Cell, destination: Cell
) -> tuple[Cell, ...]:
    """The route the search found, origin excluded, in walking order."""
    walked = [destination]
    current = destination
    while (current := came_from[current]) != origin:
        walked.append(current)
    walked.reverse()
    return tuple(walked)


def _step(current: Cell, destination: Cell, blocked: AbstractSet[Cell]) -> Cell | None:
    """The next cell of the greedy walk, or `None` when nothing legal is closer."""
    dx = _sign(destination.x - current.x)
    dy = _sign(destination.y - current.y)
    for step_x, step_y in _candidates(dx, dy):
        candidate = Cell(current.x + step_x, current.y + step_y)
        if candidate not in blocked:
            return candidate
    return None


def _candidates(dx: int, dy: int) -> tuple[tuple[int, int], ...]:
    """The diagonal step toward the destination, then the two that give up an
    axis.

    Ordered rather than searched: the first entry is the move that closes both
    axes, and the fallbacks each close one, so a creature going around an
    obstacle drifts along it instead of away from it. When the destination is
    already aligned on an axis the fallbacks are the two diagonals that hold
    that axis' distance, which is how a route gets past a body standing directly
    in the doorway.
    """
    if dx and dy:
        return ((dx, dy), (dx, 0), (0, dy))
    if dx:
        return ((dx, 0), (dx, 1), (dx, -1))
    if dy:
        return ((0, dy), (1, dy), (-1, dy))
    return (_ORIGIN_STEP,)


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)
