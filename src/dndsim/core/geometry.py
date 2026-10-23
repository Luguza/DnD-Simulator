"""The grid: where a creature stands, how far away another one is, what it can
see, what a template covers, and the route between two squares.

Started in W0 rather than W1 because the performance spike is worthless without
it (`w0g2`): `w0i` measures an attack loop that includes distance and reach
queries and one real movement per turn, and a stub that answers `5` to every
question measures nothing. `w1d` completed it with the footprint a size
implies, line of sight, the three templates and the route resolution a wide
creature can walk; nothing `w0g2` wrote was rewritten by that task.

**Distance is Chebyshev, times five.** On a battle grid each square is 5 feet
and a diagonal step costs the same as an orthogonal one, so the distance between
two cells is the larger of the two axis differences (XPHB, the battle-grid rules
in "Combat"). The 5-10-5 alternating diagonal from the *Dungeon Master's Guide*
is a variant and is deliberately not implemented: it is not the default the
rules present, it makes distance depend on the order steps are taken in, and
every number this simulator reports would then be conditional on a variant rule
nobody asked for.

**One metric answers every question here.** Reach, a sphere's radius and a
cone's length are all measured with `distance_ft`, so "20 feet" names the same
set of squares to the attack pipeline, to the opportunity-attack trigger and to
Fireball (`docs/decisions.md` § w1d).

**A creature occupies the square its size gives it**, which from `w1d` is
`SIZE_WIDTH_CELLS` squares on a side, and `Creature.position` anchors that
footprint rather than centring it. Every distance here is measured between
footprints, so a Large creature with a 5-foot reach threatens the ring around
its 2x2 and not only the ring around one corner of it.

**Movement is a decision, not a pathfinding problem** (DESIGN.md § APL). The
APL's `move:` clause picks a *destination*; this module walks the shortest legal
route to it, and there is no tactical pathing here — nor is any planned. What
that means concretely is that `route()` answers one question, "how do I get
there", and never the question of where to go. It answers it greedily first,
because on an open grid the greedy diagonal walk is already a shortest path and
costs one step per cell, and falls back to a bounded breadth-first search when
something is in the way — a creature going around a body rather than solving a
maze. A destination nothing reaches leaves the creature as far along as it got,
because that is what a creature with movement left and nowhere to go does.

Sources: XPHB (2024) "Combat" battle-grid rules, "Creature Size", Rules Glossary
"Areas of Effect"; DESIGN.md § APL and mid-attack decisions, § Performance;
ROADMAP w0g2, w1d.
"""

from collections import deque
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Final

from dndsim.rules.constants import CELL_SIZE_FT, SIZE_WIDTH_CELLS
from dndsim.rules.enums import Size

__all__ = [
    "SEARCH_CELLS",
    "Cell",
    "cone",
    "distance_ft",
    "footprint",
    "has_line_of_sight",
    "in_reach",
    "line",
    "route",
    "sphere",
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


def footprint(anchor: Cell, size: Size) -> tuple[Cell, ...]:
    """The squares a creature of `size` standing at `anchor` occupies.

    **The anchor is the footprint's low corner, not its centre.** A Large
    creature's 2x2 has no centre square, so an anchor that meant "centre" would
    be undefined for every even-sided size and well-defined for exactly Huge;
    the cells run `+x` and `+y` from the anchor instead, and a build that cares
    which corner says so by choosing the anchor.

    This is what a caller asks to find out whether a creature is caught by a
    template: a creature is in an area when any square it occupies is, so a
    Large creature standing at the edge of a Fireball is in it on one square.
    """
    return _cells(anchor, SIZE_WIDTH_CELLS[size])


def distance_ft(
    origin: Cell,
    destination: Cell,
    *,
    origin_size: Size = Size.MEDIUM,
    destination_size: Size = Size.MEDIUM,
) -> int:
    """The distance between two creatures' squares in feet.

    The larger axis difference times 5: on the battle grid a diagonal step is
    one square like any other, so the distance from (0, 0) to (3, 3) is 15 feet
    and not 21.

    Between **footprints**, which is what makes a size mean anything on the
    grid: the gap on an axis is zero where the two footprints overlap or touch,
    so an ogre anchored at (0, 0) is 5 feet from a goblin at (2, 0) rather than
    10. The sizes default to Medium because one square is the footprint of
    everything up to one, and a caller that does not know a size is asking about
    a square rather than about a creature.

    **The one-square case is answered before the sizes are looked up.** This is
    the hottest query in the engine — `w0i`'s spike asks it about three times per
    attack resolution — and an `Enum` is hashed by a Python-level `__hash__`, so
    the two table lookups cost more than the arithmetic they feed
    (`docs/decisions.md` § w1d). The general form below reduces to it exactly
    when both footprints are one square, and
    `test_a_size_that_fits_in_one_square_takes_the_same_distance` is what holds
    the two to that.
    """
    across = origin.x - destination.x
    down = origin.y - destination.y
    if origin_size is Size.MEDIUM and destination_size is Size.MEDIUM:
        return CELL_SIZE_FT * max(across, -across, down, -down, 0)
    origin_width = SIZE_WIDTH_CELLS[origin_size]
    destination_width = SIZE_WIDTH_CELLS[destination_size]
    # The gap on an axis is `max(a - b, b - a, 0)` once each side has given up
    # the squares its own footprint covers, and the larger of the two gaps is
    # the distance — so one `max` over both axes is the same answer as two.
    return CELL_SIZE_FT * max(
        across - destination_width + 1,
        -across - origin_width + 1,
        down - destination_width + 1,
        -down - origin_width + 1,
        0,
    )


def in_reach(
    origin: Cell,
    destination: Cell,
    reach_ft: int,
    *,
    origin_size: Size = Size.MEDIUM,
    destination_size: Size = Size.MEDIUM,
) -> bool:
    """Whether `destination` is within `reach_ft` of `origin`.

    The one query the attack pipeline, the opportunity-attack trigger and the
    APL's `enemies.in_reach` selector all ask, in one place so that "reach"
    means the same thing to all three. Reach is inclusive: a 5-foot reach covers
    the eight squares around you, and a Reach weapon's 10 feet covers the ring
    beyond them.
    """
    return (
        distance_ft(
            origin,
            destination,
            origin_size=origin_size,
            destination_size=destination_size,
        )
        <= reach_ft
    )


def steps_within(budget_ft: int) -> int:
    """How many cells of a route a creature with `budget_ft` of movement covers.

    Every step of a route costs one square whatever its direction, so this is a
    division — written here rather than at each call site because it is the one
    place the grid's resolution meets a movement budget.
    """
    return max(budget_ft, 0) // CELL_SIZE_FT


def has_line_of_sight(
    origin: Cell, target: Cell, *, opaque: AbstractSet[Cell] = frozenset()
) -> bool:
    """Whether an unblocked straight line runs from `origin` to `target`.

    The rule the templates below are built on: an area of effect spreads in
    straight lines from its point of origin, and a location no unblocked line
    reaches is not in the area (XPHB, Rules Glossary "Areas of Effect").

    The line runs centre to centre and is blocked by a square it passes
    *through*. Sliding along a face does not block, and nor does grazing the
    corner of a lone obstruction, because a line that merely touches the edge of
    one has not entered it — but **two opaque squares that meet at that corner
    are a wall with no gap in it**, and a line through the point where they meet
    is blocked by the pair. The two end squares never block their own line, so a
    wall is in sight of itself and a creature standing in a doorway is visible
    from it.

    **`opaque` is not `route()`'s `blocked`.** A creature's square may not be
    walked into and does not stop a line; a creature in the way is Cover, which
    is a different rule with a different answer (`w2a`). Nothing in the tree
    produces terrain yet, so the default is an open field.

    The arithmetic is integer: squares are measured in half-cells, which puts
    every centre on an odd coordinate and keeps a seeded run reproducible where
    a float comparison on a diagonal would not be.
    """
    if not opaque:
        return True
    origin_x, origin_y = 2 * origin.x + 1, 2 * origin.y + 1
    target_x, target_y = 2 * target.x + 1, 2 * target.y + 1
    return not any(
        _blocks(cell, origin_x, origin_y, target_x, target_y, opaque)
        for cell in opaque
        if cell not in (origin, target)
    )


def sphere(
    origin: Cell, radius_ft: int, *, opaque: AbstractSet[Cell] = frozenset()
) -> tuple[Cell, ...]:
    """The squares a Sphere of `radius_ft` centred on `origin` covers.

    A Sphere's point of origin is its centre, and the centre is in the area
    (XPHB, Rules Glossary "Areas of Effect"). A square is in it when it is
    within the radius by `distance_ft`, the same question reach asks, so
    Fireball's 20-foot radius covers the 9x9 block around its centre. Measuring
    a template with a second metric would make "20 feet" name one distance to
    the attack pipeline and another to the spell (`docs/decisions.md` § w1d).
    """
    span = radius_ft // CELL_SIZE_FT
    return _lit(
        origin,
        (
            Cell(origin.x + dx, origin.y + dy)
            for dx in range(-span, span + 1)
            for dy in range(-span, span + 1)
        ),
        opaque,
    )


def cone(
    origin: Cell,
    toward: Cell,
    length_ft: int,
    *,
    opaque: AbstractSet[Cell] = frozenset(),
) -> tuple[Cell, ...]:
    """The squares a Cone of `length_ft` from `origin` toward `toward` covers.

    A Cone's width at any point along its length equals that point's distance
    from the point of origin, and the point of origin is not in the area (XPHB,
    Rules Glossary "Areas of Effect"). A square grid cannot draw that width
    exactly: a cone laid along a row of squares is symmetric about that row, so
    its width in squares is always odd and the rule's even widths have to round
    one way or the other. **It rounds up** — `2 * lateral <= along`, so one
    square at 5 feet, three at 10 and 15, five at 20 and 25 — because rounding
    down would keep a 10-foot cone one square wide and a 20-foot cone three,
    narrower than the rule draws at every second step. That is a choice rather
    than a consequence (`docs/decisions.md` § w1d).

    `toward` names one of the eight grid directions rather than an angle. A
    creature chooses a direction by pointing at something, and an arbitrary
    angle would be a second geometry that nothing on a square grid can act on.
    """
    dx, dy = _direction(origin, toward, "a cone")
    span = length_ft // CELL_SIZE_FT
    return _lit(
        origin,
        (
            _cone_cell(origin, dx, dy, along, lateral)
            for along in range(1, span + 1)
            for lateral in range(-(along // 2), along // 2 + 1)
        ),
        opaque,
    )


def line(
    origin: Cell,
    toward: Cell,
    length_ft: int,
    *,
    opaque: AbstractSet[Cell] = frozenset(),
) -> tuple[Cell, ...]:
    """The squares a 5-foot-wide Line of `length_ft` from `origin` covers.

    A Line's point of origin is at one end of it and is not in the area (XPHB,
    Rules Glossary "Areas of Effect"), so this is the squares that follow the
    origin in one of the eight grid directions — and a diagonal Line is as long
    as an orthogonal one of the same square count, because a diagonal step is
    5 feet like any other.

    **Five feet is the only width here**, which is the width of every Line in
    the spell list this project imports. A `width_ft` parameter would be one
    with no caller until `w5a` lands a spell that needs it.
    """
    dx, dy = _direction(origin, toward, "a line")
    span = length_ft // CELL_SIZE_FT
    return _lit(
        origin,
        (
            Cell(origin.x + dx * step, origin.y + dy * step)
            for step in range(1, span + 1)
        ),
        opaque,
    )


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
    size: Size = Size.MEDIUM,
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

    **The greedy walk is trusted only when it arrives and its length proves it
    optimal** — when it reached the destination in exactly
    `distance_ft(origin, destination)` worth of steps, the Chebyshev floor no
    route can beat. Length alone is not enough: a walk that got stuck can
    coincidentally stop at the Chebyshev floor's step count without ever
    reaching the destination, and arrival alone is not enough either — a body
    diagonally adjacent can send the sidesteps the long way round, reaching the
    destination in three squares where two exist, and a square of movement lost
    to that is a melee creature left one square short of the target it chose.
    Both checks cost one comparison and one subtraction on the path that was
    already going to return.

    **A route that does not arrive is a route that got as far as it could.** If
    nothing reaches the destination the greedy walk's partial path is returned,
    which is what a creature with movement and nowhere to go does. The caller
    tells the two apart by looking: `walked and walked[-1] == destination`. That
    distinction is the turn loop's to act on (w2c) — an attack offered against a
    target it never reached is the bug this sentence exists to prevent.

    `blocked` is *what may not be entered* — any set of cells, since the turn
    loop builds one per move — and deciding what goes in it belongs to the
    caller: an enemy's square is blocked, an ally's is not, and the engine (w2c)
    knows which is which. `size` is the mover's, and a mover wider than one
    square needs its **whole footprint** clear at every step, so an ogre does
    not squeeze through a gap a goblin walks. The mover's own squares are not in
    `blocked` for the same reason they never were: the caller builds the set out
    of everybody else.

    The route is the whole path regardless of how much movement the creature
    has; the turn loop truncates it with `steps_within()`, because a creature
    that runs out of movement halfway has still crossed the cells it crossed and
    provoked on the way out of everyone's reach.
    """
    width = SIZE_WIDTH_CELLS[size]
    if not _clear(destination, width, blocked):
        raise ValueError(
            f"cannot route to {destination}: it is blocked. A destination is "
            "chosen by the APL's move: clause from cells a creature may stand "
            "in, so an occupied one is a selector that has already gone wrong."
        )
    if origin == destination:
        return ()
    walked = _greedy(origin, destination, blocked, width)
    if (
        walked
        and walked[-1] == destination
        and len(walked) * CELL_SIZE_FT == distance_ft(origin, destination)
    ):
        return walked
    found = _search(origin, destination, blocked, width)
    return walked if found is None else found


def _greedy(
    origin: Cell, destination: Cell, blocked: AbstractSet[Cell], width: int
) -> tuple[Cell, ...]:
    """Walk toward the destination, giving up an axis to get round what blocks.

    It terminates without a visited set and cannot cycle: every candidate step
    closes the axis it is chosen for, so the distance on that axis strictly
    decreases each step until it is zero and the other axis takes over.
    """
    walked: list[Cell] = []
    current = origin
    while current != destination:
        step = _step(current, destination, blocked, width)
        if step is None:
            break
        walked.append(step)
        current = step
    return tuple(walked)


def _search(
    origin: Cell, destination: Cell, blocked: AbstractSet[Cell], width: int
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
            if candidate in seen or not _clear(candidate, width, blocked):
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


def _step(
    current: Cell, destination: Cell, blocked: AbstractSet[Cell], width: int
) -> Cell | None:
    """The next cell of the greedy walk, or `None` when nothing legal is closer."""
    dx = _sign(destination.x - current.x)
    dy = _sign(destination.y - current.y)
    for step_x, step_y in _candidates(dx, dy):
        candidate = Cell(current.x + step_x, current.y + step_y)
        if _clear(candidate, width, blocked):
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


def _cells(anchor: Cell, width: int) -> tuple[Cell, ...]:
    """The squares of a footprint `width` on a side, anchored at its low corner."""
    if width == 1:
        return (anchor,)
    return tuple(
        Cell(anchor.x + dx, anchor.y + dy) for dx in range(width) for dy in range(width)
    )


def _clear(anchor: Cell, width: int, blocked: AbstractSet[Cell]) -> bool:
    """Whether a creature `width` squares on a side can stand at `anchor`.

    The one-square case is answered without building a footprint, because it is
    every step of every route a Medium creature walks and the tuple would be an
    allocation per candidate cell on `w0i`'s path.
    """
    if width == 1:
        return anchor not in blocked
    return all(cell not in blocked for cell in _cells(anchor, width))


def _direction(origin: Cell, toward: Cell, shape: str) -> tuple[int, int]:
    """Which of the eight grid directions `toward` points in, from `origin`."""
    dx, dy = _sign(toward.x - origin.x), _sign(toward.y - origin.y)
    if not (dx or dy):
        raise ValueError(
            f"{shape} from {origin} needs a direction, and {toward} is where it "
            "starts: the caller picked a point of origin as its own target."
        )
    return dx, dy


def _cone_cell(origin: Cell, dx: int, dy: int, along: int, lateral: int) -> Cell:
    """One square of a cone, placed by how far down the axis and how far off it.

    A diagonal cone is laid out in its own rotated frame — `along` is the
    Chebyshev distance from the origin and `lateral` the difference between the
    two axes — so that a cone pointed at a corner is the same shape as one
    pointed along a row rather than a staircase of one-square steps.
    """
    if dx and dy:
        near = along - abs(lateral)
        if lateral >= 0:
            return Cell(origin.x + dx * along, origin.y + dy * near)
        return Cell(origin.x + dx * near, origin.y + dy * along)
    if dx:
        return Cell(origin.x + dx * along, origin.y + lateral)
    return Cell(origin.x + lateral, origin.y + dy * along)


def _lit(
    origin: Cell, cells: Iterable[Cell], opaque: AbstractSet[Cell]
) -> tuple[Cell, ...]:
    """A template's squares, minus the ones no straight line from the origin
    reaches, in the fixed order they were generated in."""
    return tuple(
        cell for cell in cells if has_line_of_sight(origin, cell, opaque=opaque)
    )


_CORNER_NEIGHBOURS: Final = ((-1, -1), (1, -1), (-1, 1), (1, 1))
"""The square diagonally across each corner, in the corner order `_sides()`
reports: low-low, high-low, low-high, high-high."""


def _blocks(
    cell: Cell,
    origin_x: int,
    origin_y: int,
    target_x: int,
    target_y: int,
    opaque: AbstractSet[Cell],
) -> bool:
    """Whether `cell` stops the segment.

    Either the segment passes through the square, or it passes through exactly
    one corner of it and the square diagonally across that corner is opaque
    too. The second case is a diagonal wall — squares that touch only at their
    corners, which is the first thing anyone builds out of walls — and letting a
    line through the point where two of them meet would be a gap in something
    solid. One opaque square grazed at a corner still lets the line past: that
    is a pillar, and a line that touches its corner has not entered it.
    """
    sides = _sides(cell, origin_x, origin_y, target_x, target_y)
    if sides is None:
        return False
    if min(sides) < 0 < max(sides):
        return True
    if sides.count(0) != 1:
        # None, or the segment running along a face, which both of the squares
        # it separates let past.
        return False
    across_x, across_y = _CORNER_NEIGHBOURS[sides.index(0)]
    return Cell(cell.x + across_x, cell.y + across_y) in opaque


def _sides(
    cell: Cell, origin_x: int, origin_y: int, target_x: int, target_y: int
) -> tuple[int, int, int, int] | None:
    """Which side of the segment's line each corner of `cell` falls, or `None`
    when the segment cannot touch the square at all.

    Two exact tests in half-cell units. The square's own bands have to overlap
    the segment's, since a segment wholly to one side of a face is outside —
    which is also what keeps a corner *beyond* an endpoint from being read as
    one the segment passes through. Then the sign of each corner's cross product
    says which side of the line it is on, and a zero is a corner the line runs
    exactly through.
    """
    low_x, low_y = 2 * cell.x, 2 * cell.y
    high_x, high_y = low_x + 2, low_y + 2
    if origin_x <= low_x and target_x <= low_x:
        return None
    if origin_x >= high_x and target_x >= high_x:
        return None
    if origin_y <= low_y and target_y <= low_y:
        return None
    if origin_y >= high_y and target_y >= high_y:
        return None
    run_x, run_y = target_x - origin_x, target_y - origin_y
    return (
        run_x * (low_y - origin_y) - run_y * (low_x - origin_x),
        run_x * (low_y - origin_y) - run_y * (high_x - origin_x),
        run_x * (high_y - origin_y) - run_y * (low_x - origin_x),
        run_x * (high_y - origin_y) - run_y * (high_x - origin_x),
    )


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)
