"""The grid contract: how far away something is, what it can see, what a
template covers, and how a creature gets there.

Every fixture with an obstacle names the shortest legal route length in its
docstring and says why no shorter one exists.

Sources: XPHB (2024) "Combat" battle-grid rules, "Creature Size", Rules Glossary
"Areas of Effect"; DESIGN.md § APL; `docs/decisions.md` § w0g2, § w1d; ROADMAP
w0g2, w1d.
"""

import pytest

from dndsim.core.geometry import (
    SEARCH_CELLS,
    Cell,
    cone,
    distance_ft,
    footprint,
    has_line_of_sight,
    in_reach,
    line,
    route,
    sphere,
    steps_within,
)
from dndsim.hooks.contexts import Cell as CellProtocol
from dndsim.rules.enums import Size


def arrived(walked: tuple[Cell, ...], destination: Cell) -> bool:
    """The check `route()` documents for its caller, written once.

    A route that did not arrive is a route that got as far as it could, so
    "did the creature get there" is a question about the last cell and not about
    the length."""
    return bool(walked) and walked[-1] == destination


def protocol_members(protocol: type) -> frozenset[str]:
    """The names a `Protocol` declares, ignoring the machinery around them."""
    return frozenset(name for name in vars(protocol) if not name.startswith("_"))


# --- The Cell protocol -----------------------------------------------------


def test_a_grid_cell_is_the_cell_a_context_carries() -> None:
    """`core.geometry.Cell` satisfies the context's `Cell` protocol, whose
    members are pinned so that widening it fails this test."""
    cell: CellProtocol = Cell(3, 4)
    assert protocol_members(CellProtocol) == {"x", "y"}
    assert (cell.x, cell.y) == (3, 4)


def test_a_cell_is_a_value_object() -> None:
    """Frozen and hashable, so the identity of a square is its coordinates."""
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
    """The DMG's 5-10-5 diagonal would make this 15 feet
    (`docs/decisions.md` § w0g2)."""
    assert distance_ft(Cell(0, 0), Cell(2, 2)) == 10


def test_distance_is_symmetric_and_zero_to_where_you_stand() -> None:
    here, there = Cell(2, -3), Cell(-4, 1)
    assert distance_ft(here, there) == distance_ft(there, here) == 30
    assert distance_ft(here, here) == 0


# --- Reach -------------------------------------------------------------------


def test_a_five_foot_reach_covers_the_eight_squares_around_you() -> None:
    """All eight, diagonals included."""
    origin = Cell(0, 0)
    neighbours = [
        Cell(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if (x, y) != (0, 0)
    ]
    assert all(in_reach(origin, cell, 5) for cell in neighbours)
    assert not in_reach(origin, Cell(2, 2), 5)


def test_reach_is_inclusive_at_exactly_five_and_ten_feet() -> None:
    """The boundary is in, not out, at exactly 5 and exactly 10 feet."""
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
    """The origin is excluded and the destination included: the route is the
    list of squares *entered*."""
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
    """A plain `set` is accepted as well as a `frozenset`."""
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
    """Cells are entered once (`docs/decisions.md` § w0g2)."""
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
    """A greedy walk that arrives in more squares than the Chebyshev floor is
    replaced by the search (`docs/decisions.md` § w0g2).

    (0, 0) -> (1, -2) with (1, -1) blocked. The greedy walk *arrives*, by
    (1, 0), (2, -1), (1, -2) — three squares. Two exist: an intermediate cell
    within one step of both ends needs x in {0, 1} and y = -1, so it is (0, -1)
    or (1, -1), and (0, -1) is free."""
    walked = route(Cell(0, 0), Cell(1, -2), blocked=frozenset({Cell(1, -1)}))
    assert walked == (Cell(0, -1), Cell(1, -2))
    assert len(walked) * 5 == distance_ft(Cell(0, 0), Cell(1, -2))


def test_the_detour_is_caught_whichever_way_the_body_leans() -> None:
    """The same shape mirrored. (0, 0) -> (-1, 2) with (-1, 1) blocked:
    the only other intermediate cell within a step of both ends is (0, 1), so
    two squares is possible and two is what comes back."""
    walked = route(Cell(0, 0), Cell(-1, 2), blocked=frozenset({Cell(-1, 1)}))
    assert walked == (Cell(0, 1), Cell(-1, 2))


def test_an_open_field_still_takes_the_greedy_walk() -> None:
    """Nothing in the way: the greedy walk lands on the Chebyshev floor and is
    returned without a search. The route is the diagonal-first walk rather than
    whatever a breadth-first expansion would have reached the destination by
    first."""
    walked = route(Cell(0, 0), Cell(4, 2))
    assert walked == (Cell(1, 1), Cell(2, 2), Cell(3, 2), Cell(4, 2))
    assert len(walked) * 5 == distance_ft(Cell(0, 0), Cell(4, 2))


# --- Arriving, and not arriving ---------------------------------------------


def test_a_creature_walled_in_on_every_side_stays_where_it_is() -> None:
    """Nothing to step into and nothing for the search to expand: the route is
    empty rather than an exception."""
    box = frozenset(
        Cell(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if (x, y) != (0, 0)
    )
    walked = route(Cell(0, 0), Cell(5, 5), blocked=box)
    assert walked == ()
    assert not arrived(walked, Cell(5, 5))


def test_a_destination_beyond_the_search_cap_leaves_the_partial_walk() -> None:
    """A legal route exists — the wall is 61 squares long, so it ends
    somewhere — but finding it would mean expanding
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
    """A greedy walk whose length matches the Chebyshev floor but that stopped
    short of the destination falls through to the search.

    (0, 0) -> (5, 5) with a wall at x in {1..6}, y = 1 that spans past both
    edges the greedy walk could sidestep through. The greedy walk climbs to
    (5, 0) and gets stuck there — five squares, the same length as the
    Chebyshev floor to (5, 5) — without ever reaching the destination."""
    wall = frozenset(
        {Cell(1, 1), Cell(2, 1), Cell(3, 1), Cell(4, 1), Cell(5, 1), Cell(6, 1)}
    )
    walked = route(Cell(0, 0), Cell(5, 5), blocked=wall)
    assert walked
    assert walked[-1] == Cell(5, 5)


def test_the_same_question_gets_the_same_answer_every_time() -> None:
    """The same blocked cells give the same route however that set was built
    (`docs/decisions.md` § w0g2)."""
    wall = [Cell(1, -1), Cell(1, 0), Cell(1, 1)]
    forwards = route(Cell(0, 0), Cell(2, 0), blocked=frozenset(wall))
    backwards = route(Cell(0, 0), Cell(2, 0), blocked=set(reversed(wall)))
    again = route(Cell(0, 0), Cell(2, 0), blocked=frozenset(wall))
    assert forwards == backwards == again


def test_routing_into_an_occupied_square_is_refused() -> None:
    """A blocked destination raises (`docs/decisions.md` § w0g2)."""
    with pytest.raises(ValueError, match="it is blocked"):
        route(Cell(0, 0), Cell(2, 2), blocked=frozenset({Cell(2, 2)}))


def test_a_route_to_where_you_already_stand_is_no_route() -> None:
    assert route(Cell(3, 3), Cell(3, 3)) == ()


def test_the_route_is_the_whole_path_and_the_budget_truncates_it() -> None:
    """The walk is computed whole and `steps_within()` truncates it
    (`docs/decisions.md` § w0g2)."""
    walked = route(Cell(0, 0), Cell(9, 0))
    assert len(walked) == 9
    assert walked[: steps_within(30)] == tuple(Cell(x, 0) for x in range(1, 7))


# --- Size (debt 9) ----------------------------------------------------------


def test_everything_up_to_medium_stands_in_one_square() -> None:
    """The XPHB "Creature Size" table: Tiny's 2.5 feet and Medium's 5 are both
    the one square a creature is placed in."""
    for size in (Size.TINY, Size.SMALL, Size.MEDIUM):
        assert footprint(Cell(4, 7), size) == (Cell(4, 7),)


def test_a_large_creature_occupies_two_squares_by_two() -> None:
    """10 by 10 feet, anchored at the footprint's low corner
    (`docs/decisions.md` § w1d)."""
    assert footprint(Cell(0, 0), Size.LARGE) == (
        Cell(0, 0),
        Cell(0, 1),
        Cell(1, 0),
        Cell(1, 1),
    )
    assert len(footprint(Cell(0, 0), Size.HUGE)) == 9
    assert len(footprint(Cell(0, 0), Size.GARGANTUAN)) == 16


def test_distance_is_measured_between_footprints_not_between_anchors() -> None:
    """An ogre anchored at (0, 0) fills (0, 0)-(1, 1), so a goblin at (2, 0) is
    one square from the nearest part of it: 5 feet, not the 10 its anchor is
    away."""
    assert distance_ft(Cell(0, 0), Cell(2, 0), origin_size=Size.LARGE) == 5
    assert distance_ft(Cell(0, 0), Cell(2, 0)) == 10


def test_a_size_that_fits_in_one_square_takes_the_same_distance() -> None:
    """The one-square fast path agrees with the general form everywhere. Small
    takes the general path with a one-square footprint, so this test tells the
    two branches apart."""
    for x in range(-4, 5):
        for y in range(-4, 5):
            assert distance_ft(Cell(0, 0), Cell(x, y)) == distance_ft(
                Cell(0, 0),
                Cell(x, y),
                origin_size=Size.SMALL,
                destination_size=Size.SMALL,
            )


def test_two_footprints_that_overlap_are_no_distance_apart() -> None:
    """A square inside an ogre is zero feet from it, never below zero."""
    assert distance_ft(Cell(0, 0), Cell(1, 1), origin_size=Size.LARGE) == 0


def test_distance_does_not_depend_on_which_creature_asks() -> None:
    """Swapping the two creatures swaps the two sizes, and the answer is the
    same: a reach is a fact about a pair of footprints."""
    for destination in (Cell(3, 0), Cell(-2, 5), Cell(1, 1)):
        assert distance_ft(
            Cell(0, 0), destination, origin_size=Size.LARGE, destination_size=Size.HUGE
        ) == distance_ft(
            destination, Cell(0, 0), origin_size=Size.HUGE, destination_size=Size.LARGE
        )


def test_a_large_creature_threatens_the_ring_around_its_whole_square() -> None:
    """An ogre with a 5-foot reach reaches (2, 0) and (2, 2), which its anchor
    alone is 10 feet from (debt 9)."""
    assert in_reach(Cell(0, 0), Cell(2, 0), 5, origin_size=Size.LARGE)
    assert in_reach(Cell(0, 0), Cell(2, 2), 5, origin_size=Size.LARGE)
    assert not in_reach(Cell(0, 0), Cell(3, 0), 5, origin_size=Size.LARGE)
    assert not in_reach(Cell(0, 0), Cell(2, 0), 5)


# --- Line of sight -----------------------------------------------------------


def test_an_open_field_is_all_line_of_sight() -> None:
    assert has_line_of_sight(Cell(0, 0), Cell(9, 4))


def test_something_in_the_way_blocks_the_line() -> None:
    assert not has_line_of_sight(Cell(0, 0), Cell(2, 0), opaque=frozenset({Cell(1, 0)}))


def test_a_line_that_only_grazes_a_corner_is_not_blocked() -> None:
    """(0, 0) -> (2, 2) runs corner to corner through (1, 1), so it touches the
    corner of (1, 0) and of (0, 1) without entering either
    (`docs/decisions.md` § w1d)."""
    assert has_line_of_sight(Cell(0, 0), Cell(2, 2), opaque=frozenset({Cell(1, 0)}))
    assert not has_line_of_sight(Cell(0, 0), Cell(2, 2), opaque=frozenset({Cell(1, 1)}))


def test_two_opaque_squares_meeting_at_a_corner_are_a_wall_with_no_gap() -> None:
    """Two opaque squares that touch only at a corner are a diagonal wall: a line
    through that corner is blocked by the pair, and `sphere()` stops at it.
    (0, 0) -> (2, 2) runs exactly through the corner (1, 1) shares with
    (1, 0) and (0, 1)."""
    assert not has_line_of_sight(
        Cell(0, 0), Cell(2, 2), opaque=frozenset({Cell(1, 0), Cell(0, 1)})
    )
    area = sphere(Cell(0, 0), 10, opaque=frozenset({Cell(1, 0), Cell(0, 1)}))
    assert Cell(1, 1) not in area
    assert Cell(2, 2) not in area
    assert Cell(0, 0) in area


def test_a_line_along_a_face_is_let_past_by_both_squares_it_separates() -> None:
    """Two walls either side of a corridor do not close it: the segment runs
    along a face of each, through two of its corners rather than one."""
    corridor = frozenset({Cell(1, 1), Cell(2, 1), Cell(1, -1), Cell(2, -1)})
    assert has_line_of_sight(Cell(0, 0), Cell(4, 0), opaque=corridor)


def test_the_two_ends_never_block_their_own_line() -> None:
    """A wall is in sight of itself and a creature in a doorway is visible from
    it: the squares at the ends of the line are what is being asked about, not
    what is in the way."""
    assert has_line_of_sight(Cell(0, 0), Cell(1, 0), opaque=frozenset({Cell(1, 0)}))
    assert has_line_of_sight(Cell(1, 0), Cell(3, 0), opaque=frozenset({Cell(1, 0)}))


def test_sight_runs_both_ways() -> None:
    wall = frozenset({Cell(1, 1), Cell(2, 1)})
    for target in (Cell(3, 3), Cell(2, 2), Cell(-1, 4)):
        assert has_line_of_sight(Cell(0, 0), target, opaque=wall) == has_line_of_sight(
            target, Cell(0, 0), opaque=wall
        )


# --- Templates ---------------------------------------------------------------


def test_fireballs_sphere_is_every_square_within_twenty_feet() -> None:
    """A Sphere's point of origin is its centre and the centre is in the area.
    "Within 20 feet" is `distance_ft`, the question reach asks, so the area is
    the 9x9 block: a square four diagonal steps out is 20 feet away on this grid
    and a fifth step is 25 (`docs/decisions.md` § w1d)."""
    area = sphere(Cell(0, 0), 20)
    assert len(area) == 81
    assert Cell(0, 0) in area
    assert Cell(4, 4) in area
    assert Cell(5, 0) not in area


def test_a_sphere_does_not_reach_what_no_straight_line_reaches() -> None:
    """The wall's own square stays in the area: it blocks the line past itself,
    not the line to itself."""
    area = sphere(Cell(0, 0), 10, opaque=frozenset({Cell(1, 0)}))
    assert Cell(2, 0) not in area
    assert Cell(1, 0) in area
    assert Cell(0, 2) in area


def test_a_cone_is_as_wide_as_it_is_long() -> None:
    """The origin is not in the area, and the width rounds up to an odd number
    of squares: one at 5 feet, three at 10 and three at 15
    (`docs/decisions.md` § w1d)."""
    assert cone(Cell(0, 0), Cell(1, 0), 15) == (
        Cell(1, 0),
        Cell(2, -1),
        Cell(2, 0),
        Cell(2, 1),
        Cell(3, -1),
        Cell(3, 0),
        Cell(3, 1),
    )


def test_a_cone_pointed_at_a_corner_is_the_shape_of_one_pointed_along_a_row() -> None:
    """A diagonal cone is laid out in its own rotated frame, so it is seven
    squares like the one above rather than a staircase of single steps — and a
    diagonal step is 5 feet like any other, so it is as long."""
    assert len(cone(Cell(0, 0), Cell(1, 1), 15)) == 7
    assert Cell(3, 3) in cone(Cell(0, 0), Cell(1, 1), 15)


def test_a_line_is_the_squares_that_follow_its_point_of_origin() -> None:
    """A Line's point of origin is at one end of it and is not in the area, so
    Lightning Bolt does not catch the caster."""
    assert line(Cell(0, 0), Cell(1, 0), 15) == (Cell(1, 0), Cell(2, 0), Cell(3, 0))
    assert line(Cell(0, 0), Cell(1, 1), 15) == (Cell(1, 1), Cell(2, 2), Cell(3, 3))


def test_a_template_with_no_direction_is_refused() -> None:
    """A cone pointed at the square it starts from raises rather than returning
    an empty area."""
    with pytest.raises(ValueError, match="needs a direction"):
        cone(Cell(0, 0), Cell(0, 0), 15)


def test_a_large_creature_is_caught_by_a_template_its_anchor_misses() -> None:
    """A creature is in an area when any square it occupies is, so an ogre
    anchored outside a
    Fireball still burns on the one square of it that is inside."""
    area = sphere(Cell(0, 0), 10)
    assert Cell(-3, 0) not in area
    assert any(cell in area for cell in footprint(Cell(-3, 0), Size.LARGE))
    assert not any(cell in area for cell in footprint(Cell(-3, 0), Size.MEDIUM))


def test_a_template_comes_back_in_the_same_order_every_time() -> None:
    """A template's order does not depend on how the caller built its set of
    walls."""
    wall = [Cell(1, 1), Cell(1, 0), Cell(2, 2)]
    assert sphere(Cell(0, 0), 15, opaque=frozenset(wall)) == sphere(
        Cell(0, 0), 15, opaque=set(reversed(wall))
    )


# --- Routing a creature wider than one square --------------------------------


def test_an_ogre_does_not_fit_through_the_gap_a_goblin_walks() -> None:
    """A wall down x = 2 with one square open at (2, 0). A Medium creature walks
    straight through it in four steps; a Large one needs two free squares side
    by side at every step, and there are none at x = 2, so its route goes round
    the end of the wall instead of through the hole."""
    wall = frozenset(
        {Cell(2, y) for y in (-3, -2, -1)} | {Cell(2, y) for y in (1, 2, 3)}
    )
    goblin = route(Cell(0, 0), Cell(4, 0), blocked=wall)
    assert arrived(goblin, Cell(4, 0))
    assert goblin == (Cell(1, 0), Cell(2, 0), Cell(3, 0), Cell(4, 0))

    ogre = route(Cell(0, 0), Cell(4, 0), blocked=wall, size=Size.LARGE)
    assert arrived(ogre, Cell(4, 0))
    assert Cell(2, 0) not in ogre


def test_a_destination_a_wide_creature_does_not_fit_in_is_refused() -> None:
    """A destination the creature's footprint does not fit in raises, as a
    blocked destination does."""
    blocked = frozenset({Cell(1, 1)})
    assert route(Cell(0, 0), Cell(1, 0), blocked=blocked) == (Cell(1, 0),)
    with pytest.raises(ValueError, match="it is blocked"):
        route(Cell(0, 0), Cell(1, 0), blocked=blocked, size=Size.LARGE)
