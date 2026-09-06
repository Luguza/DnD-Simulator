"""The w0g2 grid contract: how far away something is, and how a creature gets there.

Three properties carry this file, and the first two are decisions rather than
arithmetic — which is why they are pinned here instead of being left to whoever
next reads `core/geometry.py`:

- **A diagonal step costs 5 feet.** Chebyshev distance is the battle-grid rule
  XPHB presents. The *Dungeon Master's Guide*'s alternating 5-10-5 diagonal is a
  variant this project deliberately does not implement: it makes a distance
  depend on the order the steps were taken in, and every number the simulator
  reported would then be conditional on a rule nobody asked for. The test that
  pins (0, 0) -> (2, 2) at 10 feet and not 15 is what stops that being quietly
  "fixed" into the module later.
- **Reach is inclusive.** One query answers the attack pipeline, the opportunity
  attack trigger and the APL's `enemies.in_reach` selector, so an off-by-one at
  exactly 5 or exactly 10 feet would be an off-by-one in all three at once.
- **A route is a decision already made, walked out.** `route()` answers "how do
  I get there" and never "where should I go" (DESIGN.md § APL). It walks
  greedily first, because on an open grid that walk is already a shortest path,
  and falls back to a bounded breadth-first search when something is in the way.
  The cases below pin both paths, the `SEARCH_CELLS` cap that bounds the second,
  and the one thing a caller has to check for itself: a route that did not
  arrive is a route that got as far as it could, told apart by looking at its
  last cell. `w1d` completes this module; all of it is its inheritance.

The route lengths asserted here were checked against an independent
breadth-first search rather than against the implementation: every fixture with
an obstacle names the shortest legal length in its docstring and says why no
shorter one exists.

Sources: XPHB (2024) "Combat" battle-grid rules; DESIGN.md § APL; ROADMAP w0g2,
w1d.
"""

import pytest

from dndsim.core.geometry import (
    SEARCH_CELLS,
    Cell,
    distance_ft,
    in_reach,
    route,
    steps_within,
)
from dndsim.hooks.contexts import Cell as CellProtocol


def arrived(walked: tuple[Cell, ...], destination: Cell) -> bool:
    """The check `route()` documents for its caller, written once.

    A route that did not arrive is a route that got as far as it could, so
    "did the creature get there" is a question about the last cell and not about
    the length. The turn loop (w2c) owes an attack it does not offer when this
    is False."""
    return bool(walked) and walked[-1] == destination


def protocol_members(protocol: type) -> frozenset[str]:
    """The names a `Protocol` declares, ignoring the machinery around them.

    Pinned rather than eyeballed so that *widening* the protocol fails this test
    at runtime: a new member is a new promise, and somebody has to check that
    the real class still keeps it.
    """
    return frozenset(name for name in vars(protocol) if not name.startswith("_"))


# --- The protocol debt w0f left to this task --------------------------------


def test_a_grid_cell_is_the_cell_a_context_carries() -> None:
    """`hooks/contexts.py` declares a narrow `Cell` protocol rather than
    importing this one, because `hooks/` sits below the grid. The annotation is
    what mypy checks; the membership assertion is what breaks loudly if the
    protocol grows a member `core.geometry.Cell` does not have."""
    cell: CellProtocol = Cell(3, 4)
    assert protocol_members(CellProtocol) == {"x", "y"}
    assert (cell.x, cell.y) == (3, 4)


def test_a_cell_is_a_value_object() -> None:
    """Frozen and hashable, so the identity of a square is its coordinates: a
    set of blocked cells is a set of these, and nobody has to hold a reference
    to *the* object for one square."""
    assert Cell(1, 2) == Cell(1, 2)
    assert len({Cell(1, 2), Cell(1, 2), Cell(2, 1)}) == 2
    with pytest.raises(AttributeError):
        Cell(1, 2).x = 5  # type: ignore[misc]


# --- Distance ----------------------------------------------------------------


def test_a_diagonal_costs_the_same_as_a_step_sideways() -> None:
    """Three diagonal steps are three squares, so 15 feet — not the 21 a
    straight-line measurement would give."""
    assert distance_ft(Cell(0, 0), Cell(3, 3)) == 15


def test_distance_is_the_larger_axis_difference() -> None:
    """Four squares up is 20 feet; three across and one up is still three
    squares, because the diagonal step closes both axes at once."""
    assert distance_ft(Cell(0, 0), Cell(0, 4)) == 20
    assert distance_ft(Cell(0, 0), Cell(3, 1)) == 15


def test_the_alternating_diagonal_variant_is_not_implemented() -> None:
    """The DMG's 5-10-5 diagonal would make this 15 feet. It is a variant, it
    makes distance depend on the order steps were taken in, and the project
    reports numbers under the default rules only."""
    assert distance_ft(Cell(0, 0), Cell(2, 2)) == 10


def test_distance_is_symmetric_and_zero_to_where_you_stand() -> None:
    here, there = Cell(2, -3), Cell(-4, 1)
    assert distance_ft(here, there) == distance_ft(there, here) == 30
    assert distance_ft(here, here) == 0


# --- Reach -------------------------------------------------------------------


def test_a_five_foot_reach_covers_the_eight_squares_around_you() -> None:
    """All eight, diagonals included — which is the same statement as "a
    diagonal step costs 5 feet", seen from the attack pipeline."""
    origin = Cell(0, 0)
    neighbours = [
        Cell(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if (x, y) != (0, 0)
    ]
    assert all(in_reach(origin, cell, 5) for cell in neighbours)
    assert not in_reach(origin, Cell(2, 2), 5)


def test_reach_is_inclusive_at_exactly_five_and_ten_feet() -> None:
    """The boundary is in, not out: a creature exactly 10 feet from a Reach
    weapon is a creature that can be hit by it and that provokes leaving it."""
    origin = Cell(0, 0)
    assert in_reach(origin, Cell(1, 0), 5)
    assert not in_reach(origin, Cell(2, 0), 5)
    assert in_reach(origin, Cell(2, 0), 10)
    assert not in_reach(origin, Cell(3, 0), 10)


# --- A movement budget -------------------------------------------------------


def test_steps_within_converts_a_budget_to_whole_cells() -> None:
    """30 feet of speed is six squares. 32 feet is still six: a creature does
    not stand on two fifths of a square."""
    assert steps_within(30) == 6
    assert steps_within(32) == 6
    assert steps_within(5) == 1
    assert steps_within(4) == 0


def test_a_budget_that_has_run_out_is_no_steps() -> None:
    """Never negative. A creature whose movement has been spent past zero —
    difficult terrain charged against what was left — takes no steps rather than
    a negative number of them."""
    assert steps_within(0) == 0
    assert steps_within(-15) == 0


# --- Routes ------------------------------------------------------------------


def test_a_route_leaves_where_you_stood_and_arrives_where_you_were_sent() -> None:
    """The origin is excluded and the destination included, because the route is
    the list of squares *entered* — which is what the opportunity-attack check
    and the difficult-terrain cost are both counted over."""
    walked = route(Cell(0, 0), Cell(2, 2))
    assert walked == (Cell(1, 1), Cell(2, 2))


def test_a_route_closes_both_axes_at_once() -> None:
    """Diagonal-first: (0, 0) -> (3, 1) is one diagonal step and then two
    straight ones, three squares in total, which is the distance."""
    walked = route(Cell(0, 0), Cell(3, 1))
    assert walked == (Cell(1, 1), Cell(2, 1), Cell(3, 1))
    assert len(walked) == distance_ft(Cell(0, 0), Cell(3, 1)) // 5


def test_an_open_field_route_is_as_long_as_the_distance() -> None:
    """No detours where nothing is in the way. A step changes each axis by at
    most one, so no route can be shorter than the Chebyshev distance — this one
    is exactly that long, and therefore minimal."""
    walked = route(Cell(-2, 4), Cell(3, 4))
    assert len(walked) == 5
    assert walked[-1] == Cell(3, 4)


def test_a_body_in_the_way_costs_no_extra_squares() -> None:
    """Blocked: the square directly between the two. The sidestep is the same
    move with one axis given up, so the greedy walk is (1, 1) then (2, 0) — two
    squares, which equals the distance and is therefore the shortest legal
    route, not merely a legal one. The search is never reached."""
    walked = route(Cell(0, 0), Cell(2, 0), blocked=frozenset({Cell(1, 0)}))
    assert walked == (Cell(1, 1), Cell(2, 0))
    assert len(walked) == distance_ft(Cell(0, 0), Cell(2, 0)) // 5


def test_blocked_cells_may_be_any_set_the_turn_loop_built() -> None:
    """A plain `set` as well as a `frozenset`: the turn loop builds one per move
    out of who is standing where, and copying it into a frozenset per query
    would be an allocation on the path `w0i` measures."""
    walked = route(Cell(0, 0), Cell(2, 0), blocked={Cell(1, 0)})
    assert walked == (Cell(1, 1), Cell(2, 0))


def test_a_blocked_diagonal_costs_exactly_one_extra_square() -> None:
    """(0, 0) -> (2, 2) with (1, 1) blocked. Two squares is impossible: the one
    intermediate cell within a step of both ends is (1, 1) itself, so three is
    the minimum, and three is what the greedy walk takes."""
    walked = route(Cell(0, 0), Cell(2, 2), blocked=frozenset({Cell(1, 1)}))
    assert walked == (Cell(1, 0), Cell(2, 1), Cell(2, 2))
    assert len(walked) == 3


def test_a_route_never_re_enters_a_square_it_has_walked() -> None:
    """Cells are entered once. A route that crossed itself would charge the
    difficult terrain twice and offer a second opportunity attack for a square
    the creature was already standing in. Nothing keeps a visited set any more:
    the greedy walk cannot cycle because every candidate closes the axis it was
    chosen for, and the search cannot because it is breadth-first."""
    walked = route(Cell(0, 0), Cell(4, 1), blocked=frozenset({Cell(1, 1), Cell(2, 1)}))
    assert len(set(walked)) == len(walked)
    assert Cell(0, 0) not in walked
    assert arrived(walked, Cell(4, 1))


# --- ...and when the greedy walk cannot, the search does ---------------------


def test_the_search_finds_the_way_around_a_wall_the_greedy_walk_cannot() -> None:
    """All three squares that would close the gap are occupied, so the greedy
    walk stops at once — and the fallback search goes round the three-square
    wall in four steps. Four is the shortest: the wall spans y = -1..1 at
    x = 1, so any route must enter x = 1 at y = 2 or y = -2, which is two steps
    from the origin and two more back to (2, 0)."""
    walls = frozenset({Cell(1, -1), Cell(1, 0), Cell(1, 1)})
    walked = route(Cell(0, 0), Cell(2, 0), blocked=walls)
    assert walked == (Cell(0, 1), Cell(1, 2), Cell(2, 1), Cell(2, 0))
    assert arrived(walked, Cell(2, 0))


def test_the_search_rounds_a_corner_the_greedy_walk_walks_into() -> None:
    """The greedy walk takes (1, 1) and then finds every candidate that closes
    an axis blocked; the search returns the whole four-step route instead of
    that one square. Four is the shortest: the wall at x = 2 spans y = -1..1, so
    a route must pass through (2, 2) or (2, -2), and (0, 0) to either is two
    steps, as is either to (3, 0)."""
    wall = frozenset({Cell(1, 0), Cell(2, -1), Cell(2, 0), Cell(2, 1)})
    walked = route(Cell(0, 0), Cell(3, 0), blocked=wall)
    assert walked == (Cell(1, 1), Cell(2, 2), Cell(3, 1), Cell(3, 0))


def test_a_greedy_walk_that_arrives_the_long_way_is_not_the_answer() -> None:
    """Arriving is not enough, which is the whole of why the length is compared
    against the Chebyshev floor rather than the last cell.

    (0, 0) -> (1, -2) with (1, -1) blocked. The greedy walk *arrives*, by
    (1, 0), (2, -1), (1, -2) — three squares. Two exist: an intermediate cell
    within one step of both ends needs x in {0, 1} and y = -1, so it is (0, -1)
    or (1, -1), and (0, -1) is free. A route trusted for arriving would cost a
    square of movement here, which is a melee creature left one square short of
    the target it chose."""
    walked = route(Cell(0, 0), Cell(1, -2), blocked=frozenset({Cell(1, -1)}))
    assert walked == (Cell(0, -1), Cell(1, -2))
    assert len(walked) * 5 == distance_ft(Cell(0, 0), Cell(1, -2))


def test_the_detour_is_caught_whichever_way_the_body_leans() -> None:
    """The same shape mirrored, so that the fix is a rule about length and not a
    special case about one diagonal. (0, 0) -> (-1, 2) with (-1, 1) blocked:
    the only other intermediate cell within a step of both ends is (0, 1), so
    two squares is possible and two is what comes back."""
    walked = route(Cell(0, 0), Cell(-1, 2), blocked=frozenset({Cell(-1, 1)}))
    assert walked == (Cell(0, 1), Cell(-1, 2))


def test_an_open_field_still_takes_the_greedy_walk() -> None:
    """The other side of that comparison: nothing in the way means the greedy
    walk lands on the Chebyshev floor and is returned without a search being
    run at all, which is what keeps a movement query off `w0i`'s budget. The
    route is the diagonal-first walk rather than whatever a breadth-first
    expansion would have reached the destination by first."""
    walked = route(Cell(0, 0), Cell(4, 2))
    assert walked == (Cell(1, 1), Cell(2, 2), Cell(3, 2), Cell(4, 2))
    assert len(walked) * 5 == distance_ft(Cell(0, 0), Cell(4, 2))


# --- Arriving, and not arriving ---------------------------------------------


def test_a_creature_walled_in_on_every_side_stays_where_it_is() -> None:
    """Nothing to step into and nothing for the search to expand: the route is
    empty rather than an exception the turn loop would have to catch."""
    box = frozenset(
        Cell(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if (x, y) != (0, 0)
    )
    walked = route(Cell(0, 0), Cell(5, 5), blocked=box)
    assert walked == ()
    assert not arrived(walked, Cell(5, 5))


def test_a_destination_beyond_the_search_cap_leaves_the_partial_walk() -> None:
    """The cap is what stops an unbounded grid becoming an unbounded search on
    the path a fight walks every round. Here a legal route exists — the wall is
    61 squares long, so it ends somewhere — but finding it would mean expanding
    far more than `SEARCH_CELLS`, so the creature gets the greedy walk's one
    square toward the wall and the caller sees that it did not arrive."""
    wall = frozenset(Cell(2, y) for y in range(-30, 31))
    walked = route(Cell(0, 0), Cell(5, 0), blocked=wall)
    assert walked == (Cell(1, 0),)
    assert not arrived(walked, Cell(5, 0))
    assert SEARCH_CELLS == 400


def test_a_wall_short_enough_to_search_around_is_searched_around() -> None:
    """The same shape inside the cap. The wall spans y = -3..3 at x = 1, so a
    route must enter x = 1 at y = 4 or y = -4: four steps out and four back,
    eight in total, and eight is what comes back."""
    wall = frozenset(Cell(1, y) for y in range(-3, 4))
    walked = route(Cell(0, 0), Cell(5, 0), blocked=wall)
    assert len(walked) == 8
    assert arrived(walked, Cell(5, 0))


def test_a_greedy_walk_stuck_short_of_the_destination_is_not_trusted() -> None:
    """A regression for a review finding: the greedy walk can get stuck and stop
    exactly `distance_ft(origin, destination)` worth of steps short of arriving,
    which used to be enough on its own to be trusted and returned.

    (0, 0) -> (5, 5) with a wall at x in {1..6}, y = 1 that spans past both
    edges the greedy walk could sidestep through. The greedy walk climbs to
    (5, 0) and gets stuck there — five squares, the same length as the
    Chebyshev floor to (5, 5) — without ever reaching the destination. Length
    matching the floor is not proof of arrival, so this must fall through to
    the search rather than hand back a route a creature never completes."""
    wall = frozenset(
        {Cell(1, 1), Cell(2, 1), Cell(3, 1), Cell(4, 1), Cell(5, 1), Cell(6, 1)}
    )
    walked = route(Cell(0, 0), Cell(5, 5), blocked=wall)
    assert walked
    assert walked[-1] == Cell(5, 5)


def test_the_same_question_gets_the_same_answer_every_time() -> None:
    """Determinism is the backbone: a seed and a build reproduce a fight byte
    for byte, which a route that depended on a set's iteration history would
    break. The search expands its neighbours in a fixed order, so the same
    blocked cells give the same route however that set was built."""
    wall = [Cell(1, -1), Cell(1, 0), Cell(1, 1)]
    forwards = route(Cell(0, 0), Cell(2, 0), blocked=frozenset(wall))
    backwards = route(Cell(0, 0), Cell(2, 0), blocked=set(reversed(wall)))
    again = route(Cell(0, 0), Cell(2, 0), blocked=frozenset(wall))
    assert forwards == backwards == again


def test_routing_into_an_occupied_square_is_refused() -> None:
    """A destination comes from the APL's `move:` clause, chosen from squares a
    creature may stand in, so a blocked one is a selector that has already gone
    wrong — a raise at the earliest moment rather than a route that stops one
    square short and looks like a movement shortfall."""
    with pytest.raises(ValueError, match="it is blocked"):
        route(Cell(0, 0), Cell(2, 2), blocked=frozenset({Cell(2, 2)}))


def test_a_route_to_where_you_already_stand_is_no_route() -> None:
    assert route(Cell(3, 3), Cell(3, 3)) == ()


def test_the_route_is_the_whole_path_and_the_budget_truncates_it() -> None:
    """`route()` does not know what the creature's speed is, on purpose: a
    creature that runs out of movement halfway has still entered the squares it
    crossed and still provoked on the way out of everyone's reach, so the walk
    is computed whole and the turn loop cuts it with `steps_within()`."""
    walked = route(Cell(0, 0), Cell(9, 0))
    assert len(walked) == 9
    assert walked[: steps_within(30)] == tuple(Cell(x, 0) for x in range(1, 7))
