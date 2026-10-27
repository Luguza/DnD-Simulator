"""The grid: where a creature stands, how far away another one is, what it can
see, what a template covers, and the route between two squares.

Distance is Chebyshev times five, measured between footprints; a footprint is
`SIZE_WIDTH_CELLS` squares on a side, anchored at its low corner. Reach and every
template are measured by `distance_ft`. `route()` walks to a destination it is
given and never chooses one. `docs/decisions.md` § w0g2, § w1d.

Sources: XPHB (2024) "Combat" battle-grid rules, "Creature Size", Rules Glossary
"Areas of Effect".
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

    Frozen and hashable: a square's identity is its coordinates.
    """

    x: int
    y: int

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"


def footprint(anchor: Cell, size: Size) -> tuple[Cell, ...]:
    """The squares a creature of `size` standing at `anchor` occupies.

    The anchor is the footprint's low corner, not its centre: cells run `+x`
    and `+y` from it (`docs/decisions.md` § w1d).
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

    The larger axis gap between the two footprints, times 5: (0, 0) to (3, 3)
    is 15 feet, and the gap is zero where footprints touch or overlap. Sizes
    default to Medium, one square.

    The one-square fast path below must agree with the general form;
    `test_a_size_that_fits_in_one_square_takes_the_same_distance` holds them
    together (`docs/decisions.md` § w1d).
    """
    across = origin.x - destination.x
    down = origin.y - destination.y
    if origin_size is Size.MEDIUM and destination_size is Size.MEDIUM:
        return CELL_SIZE_FT * max(across, -across, down, -down, 0)
    origin_width = SIZE_WIDTH_CELLS[origin_size]
    destination_width = SIZE_WIDTH_CELLS[destination_size]
    # One `max` over both axes' gaps is the same answer as the max of two.
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

    Inclusive, and measured with `distance_ft` between footprints. The one
    reach query: callers ask it rather than comparing a distance themselves
    (`docs/decisions.md` § w0g3).
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

    Every step costs one square whatever its direction; a negative budget is 0.
    """
    return max(budget_ft, 0) // CELL_SIZE_FT


def has_line_of_sight(
    origin: Cell, target: Cell, *, opaque: AbstractSet[Cell] = frozenset()
) -> bool:
    """Whether an unblocked straight line runs from `origin` to `target`.

    XPHB Rules Glossary "Areas of Effect". The line runs centre to centre and
    is blocked by an `opaque` square it passes through, or by two opaque squares
    meeting at a corner it passes through; a face or a lone corner does not
    block. The two end squares never block. `opaque` is not `route()`'s
    `blocked`. Integer arithmetic in half-cells (`docs/decisions.md` § w1d).
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

    XPHB Rules Glossary "Areas of Effect". The centre is included, and a square
    is in it when `distance_ft` puts it within the radius, so 20 feet is the 9x9
    block (`docs/decisions.md` § w1d). Squares no line from `origin` reaches are
    dropped.
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

    XPHB Rules Glossary "Areas of Effect"; the origin is excluded. The width
    rounds up to odd — `2 * lateral <= along`, so one square at 5 feet, three at
    10 and 15, five at 20 and 25 — a choice, `docs/decisions.md` § w1d. `toward`
    picks one of the eight grid directions; raises `ValueError` if it is
    `origin`.
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

    XPHB Rules Glossary "Areas of Effect"; the origin is excluded. The squares
    that follow `origin` in one of the eight grid directions; raises
    `ValueError` if `toward` is `origin`. Five feet wide only
    (`docs/decisions.md` § w1d).
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
"""The eight steps out of a cell, in a **fixed** order: the search breaks ties
between equally short routes by it, so a set here would break reproducibility.
"""

SEARCH_CELLS: Final = 400
"""How many cells `route()` will examine before giving up on a destination.

The grid is unbounded, so without a cap an enclosed destination would search
forever (`docs/decisions.md` § w0g2).
"""


def route(
    origin: Cell,
    destination: Cell,
    *,
    blocked: AbstractSet[Cell] = frozenset(),
    size: Size = Size.MEDIUM,
) -> tuple[Cell, ...]:
    """The shortest legal route from `origin` to `destination`, origin excluded.

    A greedy diagonal walk first, kept only when it arrives in exactly the
    Chebyshev floor's step count; otherwise a breadth-first search capped at
    `SEARCH_CELLS`. If nothing arrives, the greedy walk's partial path is
    returned, so a caller checks `walked and walked[-1] == destination`.

    `blocked` is the set of cells that may not be entered, built by the caller
    and not containing the mover's own squares. `size` is the mover's: its
    whole footprint must be clear at every step. The route ignores the movement
    budget; truncate it with `steps_within()`. Raises `ValueError` when the
    destination itself is blocked. `docs/decisions.md` § w0g2, § w1d.
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

    Needs no visited set: every step closes an axis (`docs/decisions.md`
    § w0g2).
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
    """Breadth-first, so the first route found is a shortest one, or `None`."""
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

    When the destination is aligned on an axis, the fallbacks are the two
    diagonals that still close it.
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

    The one-square case skips building a footprint: it is on the hot path.
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
    two axes (`docs/decisions.md` § w1d).
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
    too (`docs/decisions.md` § w1d).
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

    Half-cell units. The band test also keeps a corner *beyond* an endpoint
    from being read as one the segment passes through. Each value is a corner's
    cross product: its sign is the side, and zero is a corner on the line.
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
