"""The observer contract: who is offered a reaction to a move, when, and how much
it costs to find that out.

The cost assertions count grid queries and dispatches rather than timing them.

Sources: XPHB (2024) "Combat" — Opportunity Attacks; DESIGN.md § Module layout,
§ Hook scope, § APL, § Determinism; `docs/decisions.md` § w0g3; ROADMAP w0g2,
w2c, w2e.
"""

from collections.abc import Callable
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.errors import EconomyError, EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft, in_reach, route
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.effects import Effect
from dndsim.creatures.stats import StatBlock
from dndsim.engine.triggers import walk_provoking, watch_movement
from dndsim.engine.world import World
from dndsim.hooks.contexts import Cell as CellRef
from dndsim.hooks.contexts import Context, LeaveReachContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, ActionCost, Size
from weapon_fixtures import GREATSWORD

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))

_GATE_ID: Final = "attack.opportunity_attack"


# --- stand-ins for the policy and the reaction -------------------------------


class Policy:
    """A stand-in `Gate`: answers `takes` for the gate id it claims and False
    for every other, recording each id it is asked about."""

    __slots__ = ("asked", "claims", "takes")

    def __init__(self, *, takes: bool = True, claims: str = _GATE_ID) -> None:
        self.takes = takes
        # The gate ids this list actually carries an entry for. Everything else
        # is unmentioned, and unmentioned is OFF.
        self.claims = claims
        self.asked: list[str] = []

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        self.asked.append(gate_id)
        return self.takes and gate_id == self.claims


class OpportunityAttack(HookSource):
    """The reaction, as a feature: costed, gated, and recording each swing."""

    __slots__ = ("swings",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        # The context's `Cell` protocol, not `core.geometry.Cell`.
        self.swings: list[tuple[CellRef, CellRef]] = []

    @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
    def swing(self, ctx: LeaveReachContext) -> R:
        self.swings.append((ctx.origin, ctx.destination))
        return R.APPLIED


class Bystander(HookSource):
    """A movement observer that costs nothing — a tally, not a reaction."""

    __slots__ = ("seen",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen = 0

    @hook("on_leave_reach")
    def note(self, ctx: LeaveReachContext) -> R:
        self.seen += 1
        return R.APPLIED


def _seat(initiative: int) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=10, player_character=False, tiebreak=0.5
    )


def _creature(
    name: str, at: Cell, *, initiative: int = 10, size: Size = Size.MEDIUM
) -> Creature:
    """A creature whose turn has started: 30 feet of movement in hand, so a
    walk of up to six squares can be charged."""
    creature = Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=30, level=5, size=size),
        position=at,
        weapon=GREATSWORD,
        initiative=_seat(initiative),
    )
    creature.economy.turn_started(creature.speed_ft)
    return creature


def _threatener(
    name: str,
    at: Cell,
    *,
    initiative: int = 10,
    takes: bool = True,
    size: Size = Size.MEDIUM,
) -> tuple[Creature, OpportunityAttack, ActionEconomy, Policy]:
    """A creature that owns an opportunity attack, its economy and its policy."""
    creature = _creature(name, at, initiative=initiative, size=size)
    policy = Policy(takes=takes)
    creature.dispatcher.bind(gate=policy)
    reaction = OpportunityAttack(creature)
    creature.attach(reaction)
    return creature, reaction, creature.economy, policy


def _east(start: Cell, squares: int) -> tuple[Cell, ...]:
    """A route of `squares` five-foot steps due east — the shape of every move
    below, so that which square provokes is arithmetic a reader can do."""
    return tuple(Cell(start.x + step, start.y) for step in range(1, squares + 1))


# --- the offer itself --------------------------------------------------------


def test_the_offer_is_made_from_the_square_the_mover_is_still_standing_on() -> None:
    """The reaction resolves before the step, so a five-foot reach can still find
    the mover."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, _, _ = _threatener("Guard", Cell(-1, 0))

    walk_provoking(mover, _east(Cell(0, 0), 3), [guard])

    # Adjacent at (0, 0); the step to (1, 0) is 10 feet from the guard and out
    # of a greatsword's five, so that is the step that provokes — and the
    # context carries the square being left, not the one being entered.
    assert reaction.swings == [(Cell(0, 0), Cell(1, 0))]
    assert mover.position == Cell(3, 0)


def test_only_the_step_that_leaves_reach_provokes() -> None:
    """A move within reach and a move already outside it are both silent; the
    trigger is the transition, not the distance."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, _, _ = _threatener("Guard", Cell(0, -1))

    # Six squares east: (1,0) is still within the guard's reach diagonally, and
    # (2,0) is the first that is not.
    walk_provoking(mover, _east(Cell(0, 0), 6), [guard])

    assert reaction.swings == [(Cell(1, 0), Cell(2, 0))]


def test_reach_is_re_read_each_step_and_one_reaction_is_all_there_is() -> None:
    """A route that leaves, returns and leaves again is two triggers and one
    reaction: reach is recomputed per square rather than latched at the top of
    the move, and the creature is out of the audience the moment it has spent.

    The second offer is never made at all — not made and declined."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, policy = _threatener("Guard", Cell(0, -1))

    walk_provoking(mover, (Cell(1, 0), Cell(2, 0), Cell(1, 0), Cell(2, 0)), [guard])

    assert len(reaction.swings) == 1, "a second reaction was spent in one turn"
    assert not economy.can_spend(ActionCost.REACTION)
    assert policy.asked == [_GATE_ID], "the policy was asked about a spent reaction"


def test_a_move_that_never_leaves_reach_provokes_nothing() -> None:
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, _ = _threatener("Guard", Cell(0, 1))

    walk_provoking(mover, (Cell(1, 1), Cell(1, 0)), [guard])

    assert reaction.swings == []
    assert economy.can_spend(ActionCost.REACTION)


# --- whose decision it is ----------------------------------------------------


def test_a_declined_offer_spends_nothing_and_comes_again() -> None:
    """A declined offer spends nothing, so the next leaving step offers again."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, policy = _threatener("Guard", Cell(0, -1), takes=False)

    walk_provoking(mover, (Cell(1, 0), Cell(2, 0), Cell(1, 0), Cell(2, 0)), [guard])

    assert reaction.swings == []
    assert economy.can_spend(ActionCost.REACTION), (
        "a declined opportunity attack spent the reaction"
    )
    assert policy.asked == [_GATE_ID, _GATE_ID], "the second offer was never made"


def test_a_reaction_no_action_list_mentions_is_off() -> None:
    """An opportunity attack whose gate id the policy does not claim is never
    taken (`docs/decisions.md` § w0g3)."""
    mover = _creature("Runner", Cell(0, 0))
    guard = _creature("Guard", Cell(0, -1))
    # The one difference from `_threatener`: this build's list claims a
    # different reaction, so `_GATE_ID` is an id nobody asked for.
    guard.dispatcher.bind(gate=Policy(claims="rogue.uncanny_dodge"))
    reaction = OpportunityAttack(guard)
    guard.attach(reaction)

    walk_provoking(mover, _east(Cell(0, 0), 3), [guard])

    assert reaction.swings == []
    assert guard.economy.can_spend(ActionCost.REACTION), (
        "an unmentioned reaction spent something"
    )


def test_two_watchers_left_at_once_are_asked_in_initiative_order() -> None:
    """Higher initiative first (`docs/decisions.md` § w0g3)."""
    mover = _creature("Runner", Cell(0, 0))
    slow, slow_swing, _, _ = _threatener("Slow", Cell(0, -1), initiative=2)
    quick, quick_swing, _, _ = _threatener("Quick", Cell(0, 1), initiative=20)

    watch = watch_movement(mover, [slow, quick], squares=2)

    assert watch is not None
    assert watch.watching == (quick, slow)
    walk_provoking(mover, _east(Cell(0, 0), 2), [slow, quick])
    assert quick_swing.swings and slow_swing.swings


# --- the audience, and what it costs to compute ------------------------------


def test_a_room_of_bystanders_that_cannot_react_is_not_an_audience() -> None:
    """The efficiency claim, asserted as a count. Twenty combatants standing on
    top of the mover, none of them owning a movement reaction: the observer does
    no per-step work at all, because there is nobody to do it for."""
    mover = _creature("Runner", Cell(0, 0))
    room = [_creature(f"Extra{i}", Cell(0, 1)) for i in range(20)]

    assert watch_movement(mover, room, squares=6) is None


def test_the_per_step_cost_is_linear_in_watchers_not_in_the_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The efficiency claim, counted rather than timed.

    Twenty creatures in the room, one of which owns a movement observer, and a
    six-square move. A broadcast asks the grid 120 times. What this costs instead
    is **eight**: two in the broad phase for the single creature that got past
    the handler-table filter — one bounding-box question and one reach question —
    and one per square for that same creature afterwards. The other nineteen
    never reach the geometry at all, because "has anything attached to
    `on_leave_reach`" is asked first.

    Both `in_reach()` and `distance_ft()` are counted, because the module asks
    the grid through both.
    """
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    watcher.dispatcher.bind(gate=Policy())
    watcher.attach(Bystander(watcher))
    room = [_creature(f"Extra{i}", Cell(0, 1)) for i in range(19)]

    queries = 0

    def counting_distance(
        origin: Cell,
        destination: Cell,
        *,
        origin_size: Size = Size.MEDIUM,
        destination_size: Size = Size.MEDIUM,
    ) -> int:
        nonlocal queries
        queries += 1
        return distance_ft(
            origin,
            destination,
            origin_size=origin_size,
            destination_size=destination_size,
        )

    def counting_reach(
        origin: Cell,
        destination: Cell,
        reach_ft: int,
        *,
        origin_size: Size = Size.MEDIUM,
        destination_size: Size = Size.MEDIUM,
    ) -> bool:
        nonlocal queries
        queries += 1
        return in_reach(
            origin,
            destination,
            reach_ft,
            origin_size=origin_size,
            destination_size=destination_size,
        )

    monkeypatch.setattr("dndsim.engine.triggers.distance_ft", counting_distance)
    monkeypatch.setattr("dndsim.engine.triggers.in_reach", counting_reach)
    walk_provoking(mover, _east(Cell(0, 0), 6), [watcher, *room])

    assert queries == 2 + 6


def test_a_watcher_out_of_range_of_the_whole_route_is_never_asked() -> None:
    """The broad phase: a creature further away than its reach plus the length
    of the move can never have the mover inside its reach, so it can never be
    left."""
    mover = _creature("Runner", Cell(0, 0))
    near, _, _, _ = _threatener("Near", Cell(0, -1))
    far, _, _, _ = _threatener("Far", Cell(40, 0))

    watch = watch_movement(mover, [near, far], squares=3)

    assert watch is not None
    assert watch.watching == (near,)


def test_a_watcher_that_has_spent_its_reaction_drops_out_of_the_audience() -> None:
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(0, -1))

    watch = watch_movement(mover, [guard], squares=4)
    assert watch is not None and watch.watching == (guard,)
    watch.leaving(Cell(1, 0))
    mover.position = Cell(1, 0)
    watch.leaving(Cell(2, 0))

    assert not watch.watching


def test_a_costless_observer_is_offered_the_move_with_no_reaction_in_hand() -> None:
    """An `on_leave_reach` handler that spends nothing is offered every step it
    is owed whatever the economy says."""
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    watcher.economy.spend(ActionCost.REACTION)
    watcher.dispatcher.bind(gate=Policy())
    tally = Bystander(watcher)
    watcher.attach(tally)

    walk_provoking(mover, _east(Cell(0, 0), 3), [watcher])

    assert tally.seen == 1


def test_a_watcher_with_only_a_spent_reaction_is_dropped_before_it_is_asked() -> None:
    mover = _creature("Runner", Cell(0, 0))
    guard, _, economy, policy = _threatener("Guard", Cell(0, -1))
    economy.spend(ActionCost.REACTION)

    assert watch_movement(mover, [guard], squares=3) is None
    assert policy.asked == [], "the expensive question was asked anyway"


def test_the_dead_and_the_mover_itself_are_never_in_the_audience() -> None:
    mover = _creature("Runner", Cell(0, 0))
    mover.dispatcher.bind(gate=Policy())
    mover.attach(OpportunityAttack(mover))
    corpse, _, _, _ = _threatener("Corpse", Cell(0, -1))
    corpse.hp = 0

    assert watch_movement(mover, [mover, corpse], squares=3) is None


# --- the valves --------------------------------------------------------------


def test_disengaging_provokes_nobody() -> None:
    """`provokes=False` walks the route and offers nothing."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, _ = _threatener("Guard", Cell(0, -1))

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), [guard], provokes=False)

    assert walked == _east(Cell(0, 0), 3)
    assert mover.position == Cell(3, 0)
    assert reaction.swings == []
    assert economy.can_spend(ActionCost.REACTION)


def test_a_mover_dropped_on_the_way_stops_where_it_fell() -> None:
    """The reaction resolves before the step, so a mover reduced to 0 never takes
    it — and the watchers after it in initiative order are not offered a creature
    that is no longer leaving anybody's reach."""
    mover = _creature("Runner", Cell(0, 0))
    killer, _, _, _ = _threatener("Killer", Cell(0, -1), initiative=20)
    second, second_swing, _, _ = _threatener("Second", Cell(0, 1), initiative=2)

    class Lethal(HookSource):
        __slots__ = ()

        @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
        def kill(self, ctx: LeaveReachContext) -> R:
            mover.hp = 0
            return R.APPLIED

    killer.detach(killer.dispatcher.sources()[0])
    killer.attach(Lethal(killer))

    walked = walk_provoking(mover, _east(Cell(0, 0), 4), [killer, second])

    # (1, 0) is still five feet from a creature at (0, -1) — a diagonal is one
    # square — so the move gets one square in before the second step leaves both
    # watchers at once and the higher initiative drops the mover on the way out.
    assert walked == (Cell(1, 0),)
    assert mover.position == Cell(1, 0)
    assert second_swing.swings == [], "a downed mover kept leaving people's reach"


class Halted(Effect):
    """Speed 0 while held, and nothing else: the shape of 2024 Sentinel's
    rider."""

    __slots__ = ()

    ID = "test.halted"
    SPEED_ZERO = True


class Marked(Effect):
    """An effect with no behaviour, for seeing where a queued one lands."""

    __slots__ = ()

    ID = "test.marked"


class Queues(HookSource):
    """A reaction that queues `make(ctx)` onto `onto(ctx)` and spends."""

    __slots__ = ("_make", "_onto")

    ID = "test.queues"

    def __init__(
        self,
        owner: Creature,
        *,
        onto: Callable[[LeaveReachContext], Creature],
        make: Callable[[Creature], Effect],
    ) -> None:
        super().__init__(owner)
        self._onto = onto
        self._make = make

    @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
    def queue(self, ctx: LeaveReachContext) -> R:
        target = self._onto(ctx)
        ctx.apply_effect(target, self._make(target))
        return R.APPLIED


def _rearm(guard: Creature, source: HookSource) -> None:
    """Swap `guard`'s recording reaction for `source`."""
    guard.detach(guard.dispatcher.sources()[0])
    guard.attach(source)


def test_an_effect_queued_by_a_reaction_lands_on_the_mover() -> None:
    """Debt #18: the queue is drained, not refused and not dropped."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, economy, _ = _threatener("Guard", Cell(-1, 0))
    _rearm(guard, Queues(guard, onto=lambda ctx: mover, make=Marked))

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), [guard])

    assert [type(effect) for effect in mover.effects] == [Marked]
    assert walked == _east(Cell(0, 0), 3)
    assert not economy.can_spend(ActionCost.REACTION)


def test_an_effect_queued_onto_a_third_seated_creature_lands_on_it() -> None:
    """The roster finds a creature that is neither the watcher nor the
    mover."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(-1, 0))
    bystander = _creature("Bystander", Cell(9, 9))
    seated = World()
    for one, side in ((mover, "a"), (guard, "b"), (bystander, "b")):
        seated.seat(one, side)
    _rearm(guard, Queues(guard, onto=lambda ctx: bystander, make=Marked))

    walk_provoking(mover, _east(Cell(0, 0), 3), seated)

    assert [type(effect) for effect in bystander.effects] == [Marked]
    assert list(mover.effects) == []
    assert list(guard.effects) == []


def test_an_effect_queued_onto_a_creature_nobody_seated_still_raises() -> None:
    """With no roster to look it up on, the queued target cannot be found."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(-1, 0))
    stranger = _creature("Stranger", Cell(9, 9))
    _rearm(guard, Queues(guard, onto=lambda ctx: stranger, make=Marked))

    with pytest.raises(EngineAssemblyError, match="no roster"):
        walk_provoking(mover, _east(Cell(0, 0), 3), [guard])


def test_a_reaction_that_sets_speed_to_zero_stops_the_walk() -> None:
    """Debt #19: the mover stays on the square it was leaving, and the step
    it never took is not charged. Adjacent at (0, 0), the first step leaves
    the guard's reach, so nothing is walked and 30 feet are left."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(-1, 0))
    _rearm(guard, Queues(guard, onto=lambda ctx: mover, make=Halted))

    walked = walk_provoking(mover, _east(Cell(0, 0), 4), [guard])

    assert walked == ()
    assert mover.position == Cell(0, 0)
    assert mover.speed_ft == 0
    assert mover.economy.movement_ft == 30


def test_a_mover_halted_partway_keeps_the_steps_it_took() -> None:
    """The guard at (0, -1) still reaches (1, 0) diagonally, so one step is
    walked and charged (30 - 5 = 25) before the step to (2, 0) is stopped."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(0, -1))
    _rearm(guard, Queues(guard, onto=lambda ctx: mover, make=Halted))

    walked = walk_provoking(mover, _east(Cell(0, 0), 5), [guard])

    assert walked == (Cell(1, 0),)
    assert mover.position == Cell(1, 0)
    assert mover.economy.movement_ft == 25


def test_a_halted_mover_is_not_offered_to_the_watchers_after_the_halt() -> None:
    """The parallel of the dropped mover below: a creature held to its square
    leaves nobody's reach, so the lower-initiative watcher left on the same
    step is not offered it."""
    mover = _creature("Runner", Cell(0, 0))
    sentinel, _, _, _ = _threatener("Sentinel", Cell(0, -1), initiative=20)
    second, second_swing, second_economy, _ = _threatener(
        "Second", Cell(0, 1), initiative=2
    )
    _rearm(sentinel, Queues(sentinel, onto=lambda ctx: mover, make=Halted))

    walked = walk_provoking(mover, _east(Cell(0, 0), 4), [sentinel, second])

    assert walked == (Cell(1, 0),)
    assert second_swing.swings == [], "a halted mover kept leaving people's reach"
    assert second_economy.can_spend(ActionCost.REACTION)


# --- the per-step charge (debt #28) ------------------------------------------


def test_an_unwatched_walk_charges_five_feet_a_square() -> None:
    """Four squares on the fast path: 30 - 4 * 5 = 10."""
    mover = _creature("Runner", Cell(0, 0))

    walk_provoking(mover, _east(Cell(0, 0), 4), [])

    assert mover.economy.movement_ft == 10


class Ledger(HookSource):
    """A costless observer that writes down the mover's movement left at each
    offer."""

    __slots__ = ("seen",)

    ID = "test.ledger"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[int] = []

    @hook("on_leave_reach")
    def note(self, ctx: LeaveReachContext) -> R:
        mover = ctx.mover
        assert isinstance(mover, Creature)
        self.seen.append(mover.economy.movement_ft)
        return R.APPLIED


def test_a_watched_walk_charges_each_step_as_it_is_taken() -> None:
    """The watcher at (0, -1) is left by the step (1, 0) -> (2, 0): by then one
    step is charged (25), and the second is not yet. Six squares end on 0."""
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    ledger = Ledger(watcher)
    watcher.attach(ledger)

    walked = walk_provoking(mover, _east(Cell(0, 0), 6), [watcher])

    assert ledger.seen == [25]
    assert walked == _east(Cell(0, 0), 6)
    assert mover.economy.movement_ft == 0


def test_a_mover_dropped_partway_is_charged_only_the_steps_it_took() -> None:
    mover = _creature("Runner", Cell(0, 0))
    killer, _, _, _ = _threatener("Killer", Cell(0, -1))

    class Lethal(HookSource):
        __slots__ = ()

        @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
        def kill(self, ctx: LeaveReachContext) -> R:
            mover.hp = 0
            return R.APPLIED

    _rearm(killer, Lethal(killer))

    walked = walk_provoking(mover, _east(Cell(0, 0), 5), [killer])

    assert walked == (Cell(1, 0),)
    assert mover.economy.movement_ft == 25


@pytest.mark.parametrize("watched", [False, True])
def test_a_route_longer_than_the_movement_left_raises_before_anything_moves(
    watched: bool,
) -> None:
    """Seven squares is 35 feet against 30, on either path."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, policy = _threatener("Guard", Cell(-1, 0))

    with pytest.raises(EconomyError):
        walk_provoking(mover, _east(Cell(0, 0), 7), [guard] if watched else [])

    assert mover.position == Cell(0, 0)
    assert mover.economy.movement_ft == 30
    assert reaction.swings == [] and policy.asked == []
    assert economy.can_spend(ActionCost.REACTION)


def test_a_route_that_spends_exactly_the_movement_left_is_walked() -> None:
    mover = _creature("Runner", Cell(0, 0))
    mover.economy.move(20)

    walked = walk_provoking(mover, _east(Cell(0, 0), 2), [])

    assert walked == _east(Cell(0, 0), 2)
    assert mover.economy.movement_ft == 0


@pytest.mark.parametrize("watched", [False, True])
def test_a_mover_at_speed_zero_raises_before_anything_moves(watched: bool) -> None:
    """Movement in hand from the turn's start does not outlast a Speed of 0."""
    mover = _creature("Runner", Cell(0, 0))
    mover.effects.apply(Halted(mover))
    guard, reaction, _, _ = _threatener("Guard", Cell(-1, 0))

    with pytest.raises(EconomyError):
        walk_provoking(mover, _east(Cell(0, 0), 1), [guard] if watched else [])

    assert mover.position == Cell(0, 0)
    assert mover.economy.movement_ft == 30
    assert reaction.swings == []


def test_an_empty_route_at_speed_zero_is_no_move_and_no_error() -> None:
    mover = _creature("Runner", Cell(0, 0))
    mover.effects.apply(Halted(mover))

    assert walk_provoking(mover, (), []) == ()


def test_an_uncharged_walk_needs_no_movement_and_spends_none() -> None:
    """`charge=False` is movement the mover does not pay for."""
    mover = _creature("Runner", Cell(0, 0))
    mover.economy.move(30)
    guard, reaction, _, _ = _threatener("Guard", Cell(-1, 0))

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), [guard], charge=False)

    assert walked == _east(Cell(0, 0), 3)
    assert mover.economy.movement_ft == 0
    assert reaction.swings == [(Cell(0, 0), Cell(1, 0))]


# --- what the caller keeps ---------------------------------------------------


def test_an_unwatched_move_lands_on_its_destination_in_one_assignment() -> None:
    """The fast path is a behavioural no-op: the same fight, without the loop."""
    mover = _creature("Runner", Cell(0, 0))

    walked = walk_provoking(mover, _east(Cell(0, 0), 5), [])

    assert mover.position == Cell(5, 0)
    assert walked == _east(Cell(0, 0), 5)


def test_an_empty_route_moves_nobody() -> None:
    mover = _creature("Runner", Cell(2, 2))

    assert walk_provoking(mover, (), []) == ()
    assert mover.position == Cell(2, 2)


def test_a_large_watcher_gets_the_opportunity_attack_its_anchor_would_miss() -> None:
    """The route and the offer, end to end on the case debt 9 named
    (`docs/decisions.md` § w1d).

    Both guards are anchored at (-2, 0) with a 5-foot reach, and the runner
    starts at (0, 0) and is sent to (3, 0). The Large guard fills
    (-2, 0)-(-1, 1), so its nearest square is one step from the runner and the
    first step of the route leaves its reach: one swing, from (0, 0) to (1, 0).
    The Medium guard stands only on (-2, 0), which is 10 feet from the runner,
    so it never had the runner in reach and is never offered anything."""
    walked = route(Cell(0, 0), Cell(3, 0))
    assert walked == (Cell(1, 0), Cell(2, 0), Cell(3, 0))

    runner = _creature("Runner", Cell(0, 0))
    ogre, swings, _, _ = _threatener("Ogre", Cell(-2, 0), size=Size.LARGE)
    walk_provoking(runner, walked, [ogre])
    assert swings.swings == [(Cell(0, 0), Cell(1, 0))]

    runner = _creature("Runner", Cell(0, 0))
    goblin, missed, _, _ = _threatener("Goblin", Cell(-2, 0))
    walk_provoking(runner, walked, [goblin])
    assert missed.swings == []


# --- the price of a step (crawling) ------------------------------------------


@pytest.mark.parametrize("watched", [False, True])
def test_a_ten_foot_step_charges_ten_feet_a_square(watched: bool) -> None:
    """Three squares at ten feet each: 30 - 30 = 0, on either path."""
    mover = _creature("Runner", Cell(0, 0))
    bystanders: list[Creature] = []
    if watched:
        watcher = _creature("Watcher", Cell(0, -1))
        watcher.attach(Ledger(watcher))
        bystanders.append(watcher)

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), bystanders, step_ft=10)

    assert walked == _east(Cell(0, 0), 3)
    assert mover.economy.movement_ft == 0


def test_a_ten_foot_step_is_charged_as_it_is_taken() -> None:
    """The watcher is left on the second step, with one ten-foot step paid."""
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    ledger = Ledger(watcher)
    watcher.attach(ledger)

    walk_provoking(mover, _east(Cell(0, 0), 3), [watcher], step_ft=10)

    assert ledger.seen == [20]


@pytest.mark.parametrize("watched", [False, True])
def test_a_route_of_ten_foot_steps_costing_too_much_raises(watched: bool) -> None:
    """Four squares at ten feet is 40 against 30; nothing moves or is charged."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, _, _ = _threatener("Guard", Cell(-1, 0))

    with pytest.raises(EconomyError):
        walk_provoking(
            mover, _east(Cell(0, 0), 4), [guard] if watched else [], step_ft=10
        )

    assert mover.position == Cell(0, 0)
    assert mover.economy.movement_ft == 30
    assert reaction.swings == []


# --- unpaid movement ignores Speed (review round 2) --------------------------


@pytest.mark.parametrize("watched", [False, True])
def test_an_unpaid_walk_moves_a_speed_zero_mover_the_whole_route(
    watched: bool,
) -> None:
    """Forced movement is not the mover's own: a Speed of 0 does not stop it,
    whether or not anybody is watching."""
    mover = _creature("Runner", Cell(0, 0))
    mover.effects.apply(Halted(mover))
    bystanders: list[Creature] = []
    if watched:
        guard, _, _, _ = _threatener("Guard", Cell(-1, 0))
        bystanders.append(guard)

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), bystanders, charge=False)

    assert walked == _east(Cell(0, 0), 3)
    assert mover.position == Cell(3, 0)
    assert mover.economy.movement_ft == 30


def test_an_unpaid_walk_at_speed_zero_is_still_offered_to_the_watcher() -> None:
    """Adjacent at (0, 0), the step to (1, 0) leaves the guard's reach."""
    mover = _creature("Runner", Cell(0, 0))
    mover.effects.apply(Halted(mover))
    guard, reaction, economy, _ = _threatener("Guard", Cell(-1, 0))

    walk_provoking(mover, _east(Cell(0, 0), 3), [guard], charge=False)

    assert reaction.swings == [(Cell(0, 0), Cell(1, 0))]
    assert not economy.can_spend(ActionCost.REACTION)


def test_an_unpaid_walk_at_speed_zero_offers_every_watcher_left() -> None:
    """Two watchers left on the same step are both offered: no Speed-0 cutoff
    applies to movement that is not the mover's own."""
    mover = _creature("Runner", Cell(0, 0))
    mover.effects.apply(Halted(mover))
    quick, quick_swing, _, _ = _threatener("Quick", Cell(0, -1), initiative=20)
    slow, slow_swing, _, _ = _threatener("Slow", Cell(0, 1), initiative=2)

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), [quick, slow], charge=False)

    assert walked == _east(Cell(0, 0), 3)
    assert quick_swing.swings == [(Cell(1, 0), Cell(2, 0))]
    assert slow_swing.swings == [(Cell(1, 0), Cell(2, 0))]


def test_an_unpaid_walk_still_stops_on_a_dropped_mover() -> None:
    mover = _creature("Runner", Cell(0, 0))
    killer, _, _, _ = _threatener("Killer", Cell(0, -1))

    class Lethal(HookSource):
        __slots__ = ()

        @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
        def kill(self, ctx: LeaveReachContext) -> R:
            mover.hp = 0
            return R.APPLIED

    _rearm(killer, Lethal(killer))

    walked = walk_provoking(mover, _east(Cell(0, 0), 5), [killer], charge=False)

    assert walked == (Cell(1, 0),)
    assert mover.position == Cell(1, 0)


def test_a_paid_walk_halted_by_a_reaction_still_stops_and_offers_no_one_else() -> None:
    """The paid case is unchanged: Sentinel's Speed 0 ends the walk on (1, 0),
    and the lower-initiative watcher left on that step is not offered it."""
    mover = _creature("Runner", Cell(0, 0))
    sentinel, _, _, _ = _threatener("Sentinel", Cell(0, -1), initiative=20)
    second, second_swing, second_economy, _ = _threatener(
        "Second", Cell(0, 1), initiative=2
    )
    _rearm(sentinel, Queues(sentinel, onto=lambda ctx: mover, make=Halted))

    walked = walk_provoking(mover, _east(Cell(0, 0), 4), [sentinel, second])

    assert walked == (Cell(1, 0),)
    assert mover.position == Cell(1, 0)
    assert mover.economy.movement_ft == 25
    assert second_swing.swings == []
    assert second_economy.can_spend(ActionCost.REACTION)
