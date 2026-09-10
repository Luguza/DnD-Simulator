"""The observer contract: who is offered a reaction to a move, when, and how much
it costs to find that out.

`engine/triggers.py` is the audience half of DESIGN.md § Module layout's split —
`actions/` holds the opportunity attack itself, this holds "which event fires an
offer, who is in the audience, in what order". Four things about it are worth
holding still, and the fourth is the reason the file exists in this shape:

- **The offer is one five-foot step.** A route of six squares that leaves a
  watcher's reach on the fourth step offers once, with the third and fourth
  squares in the context, and the mover is still standing on the third while the
  reaction resolves. Any other ordering puts the attack a square further away
  than the rules put it, which a five-foot reach notices about half the time.
- **The decision is the creature's, not the engine's.** Whether a step provokes
  is arithmetic; whether to spend the reaction on it is the action list's answer
  through the dispatcher's `gate`, and a declined offer costs nothing — the
  reaction is still in hand for the next square.
- **Two watchers left at once are asked in initiative order**, sorted once per
  move, because a fight that resolves in a different order on a re-run is not
  reproducible from its seed.
- **The cost is counted, not timed.** The efficiency claim is that the per-step
  work is linear in creatures that could actually react rather than in creatures
  in the room, so the assertions below count dispatches and distance queries. A
  wall-clock assertion of the same claim on a shared runner fires on noise and
  gets deleted (DESIGN.md § The benchmark gate).

Sources: XPHB (2024) "Combat" — Opportunity Attacks; DESIGN.md § Module layout,
§ Hook scope, § APL, § Determinism; ROADMAP w0g2, w2c, w2e.
"""

from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock
from dndsim.engine.triggers import walk_provoking, watch_movement
from dndsim.hooks.contexts import Cell as CellRef
from dndsim.hooks.contexts import Context, LeaveReachContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, ActionCost

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))

_GATE_ID: Final = "attack.opportunity_attack"


# --- the pieces w2b and w2e own, standing in for themselves ------------------


class Economy:
    """One reaction, as much of `w1a`'s `ActionEconomy` as the dispatcher asks."""

    __slots__ = ("reaction",)

    def __init__(self) -> None:
        self.reaction = True

    def can_spend(self, cost: ActionCost) -> bool:
        return self.reaction if cost is ActionCost.REACTION else True

    def spend(self, cost: ActionCost) -> None:
        if cost is ActionCost.REACTION:
            self.reaction = False


class Policy:
    """The reaction half of the APL, as the `Gate` protocol sees it (`w2e`).

    An `if:` clause the compiler will emit, standing in as a flag: what matters
    to this file is that the answer is the *creature's*, that a False answer
    spends nothing, and that a gate nobody mentions is OFF — the Null Object
    default of DESIGN.md § APL.
    """

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
    """The reaction, as a feature: costed, gated, and knowing no geometry.

    The whole point of `Scope.OBSERVER` in one class — there is no reach check
    here, no `if ctx.mover is not self.owner`, and no policy. The engine decided
    that this creature's reach was left; the action list decided whether that was
    worth a reaction; this only records the swing.
    """

    __slots__ = ("swings",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        # The protocol `LeaveReachContext` declares, not `core.geometry.Cell`:
        # a feature reads the squares it was handed and never the concrete
        # class behind them (`hooks/contexts.py`).
        self.swings: list[tuple[CellRef, CellRef]] = []

    @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
    def swing(self, ctx: LeaveReachContext) -> R:
        self.swings.append((ctx.origin, ctx.destination))
        return R.APPLIED


class Bystander(HookSource):
    """A movement observer that costs nothing — a tally, not a reaction.

    Here because it is what stops the audience filter from being written as "has
    a reaction left": a source with no `costs` must be offered the event whether
    or not its creature has a reaction in hand, and `Dispatcher.responsive()`
    answering otherwise would silently delete it.
    """

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


def _creature(name: str, at: Cell, *, initiative: int = 10) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=30, armour_class=15, level=5),
        position=at,
        weapon=GREATSWORD,
        initiative=_seat(initiative),
    )


def _threatener(
    name: str, at: Cell, *, initiative: int = 10, takes: bool = True
) -> tuple[Creature, OpportunityAttack, Economy, Policy]:
    """A creature that owns an opportunity attack, its economy and its policy."""
    creature = _creature(name, at, initiative=initiative)
    economy, policy = Economy(), Policy(takes=takes)
    creature.dispatcher.bind(economy=economy, gate=policy)
    reaction = OpportunityAttack(creature)
    creature.attach(reaction)
    return creature, reaction, economy, policy


def _east(start: Cell, squares: int) -> tuple[Cell, ...]:
    """A route of `squares` five-foot steps due east — the shape of every move
    below, so that which square provokes is arithmetic a reader can do."""
    return tuple(Cell(start.x + step, start.y) for step in range(1, squares + 1))


# --- the offer itself --------------------------------------------------------


def test_the_offer_is_made_from_the_square_the_mover_is_still_standing_on() -> None:
    """The ordering the whole file exists to hold: the reaction resolves before
    the step, so a five-foot reach can still find the mover."""
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

    The second offer is never made at all — not made and declined. Once the
    reaction is gone the watcher stops being someone who could answer, which is
    the same filter `watch_movement()` applies before the move starts."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, policy = _threatener("Guard", Cell(0, -1))

    walk_provoking(mover, (Cell(1, 0), Cell(2, 0), Cell(1, 0), Cell(2, 0)), [guard])

    assert len(reaction.swings) == 1, "a second reaction was spent in one turn"
    assert not economy.reaction
    assert policy.asked == [_GATE_ID], "the policy was asked about a spent reaction"


def test_a_move_that_never_leaves_reach_provokes_nothing() -> None:
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, _ = _threatener("Guard", Cell(0, 1))

    walk_provoking(mover, (Cell(1, 1), Cell(1, 0)), [guard])

    assert reaction.swings == []
    assert economy.reaction


# --- whose decision it is ----------------------------------------------------


def test_a_declined_offer_spends_nothing_and_comes_again() -> None:
    """ "Nothing is spent by a handler that declined" (`hooks/dispatch.py`), which
    is what makes holding a reaction for a better target expressible at all."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, policy = _threatener("Guard", Cell(0, -1), takes=False)

    walk_provoking(mover, (Cell(1, 0), Cell(2, 0), Cell(1, 0), Cell(2, 0)), [guard])

    assert reaction.swings == []
    assert economy.reaction, "a declined opportunity attack spent the reaction"
    assert policy.asked == [_GATE_ID, _GATE_ID], "the second offer was never made"


def test_a_reaction_no_action_list_mentions_is_off() -> None:
    """The Null Object default policy: attaching an opportunity attack to a build
    whose APL never claims it does not silently change that build's numbers.

    `Policy` answers True only for the gate id it was built to claim, which is
    what a compiled action list does — `allows()` on an id no entry carries
    returns False (`tools/spike_apl.py`). So the same feature, the same move and
    the same geometry produce a swing or no swing purely on the strength of one
    line of YAML, which is the property that makes two builds comparable.
    """
    mover = _creature("Runner", Cell(0, 0))
    guard = _creature("Guard", Cell(0, -1))
    economy = Economy()
    # The one difference from `_threatener`: this build's list claims a
    # different reaction, so `_GATE_ID` is an id nobody asked for.
    guard.dispatcher.bind(economy=economy, gate=Policy(claims="rogue.uncanny_dodge"))
    reaction = OpportunityAttack(guard)
    guard.attach(reaction)

    walk_provoking(mover, _east(Cell(0, 0), 3), [guard])

    assert reaction.swings == []
    assert economy.reaction, "an unmentioned reaction spent something"


def test_two_watchers_left_at_once_are_asked_in_initiative_order() -> None:
    """Any order is legal D&D; only a stated one reproduces from a seed."""
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
    six-square move. A broadcast would put 120 reach questions on the table. What
    this costs instead is **one** — the broad phase asking the single creature
    that got past the handler-table filter where it is standing — plus one per
    square for that same creature: seven. The other nineteen never reach the
    geometry at all, because "has anything attached to `on_leave_reach`" is a
    dict lookup and it is asked first, in the same ascending-cost order the
    dispatcher asks its own three valves in.
    """
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    watcher.dispatcher.bind(economy=Economy(), gate=Policy())
    watcher.attach(Bystander(watcher))
    room = [_creature(f"Extra{i}", Cell(0, 1)) for i in range(19)]

    queries = 0

    def counting(origin: Cell, destination: Cell) -> int:
        nonlocal queries
        queries += 1
        return distance_ft(origin, destination)

    monkeypatch.setattr("dndsim.engine.triggers.distance_ft", counting)
    walk_provoking(mover, _east(Cell(0, 0), 6), [watcher, *room])

    assert queries == 1 + 6


def test_a_watcher_out_of_range_of_the_whole_route_is_never_asked() -> None:
    """The broad phase, which is the filter that keeps a large fight cheap: a
    creature further away than its reach plus the length of the move can never
    have the mover inside its reach, so it can never be left."""
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
    """`Dispatcher.responsive()` filters on what the handler *costs*, never on
    the hook it is attached to — an `on_leave_reach` handler that spends nothing
    is offered every step it is owed whatever the economy says."""
    mover = _creature("Runner", Cell(0, 0))
    watcher = _creature("Watcher", Cell(0, -1))
    economy = Economy()
    economy.reaction = False
    watcher.dispatcher.bind(economy=economy, gate=Policy())
    tally = Bystander(watcher)
    watcher.attach(tally)

    walk_provoking(mover, _east(Cell(0, 0), 3), [watcher])

    assert tally.seen == 1


def test_a_watcher_with_only_a_spent_reaction_is_dropped_before_it_is_asked() -> None:
    mover = _creature("Runner", Cell(0, 0))
    guard, _, economy, policy = _threatener("Guard", Cell(0, -1))
    economy.reaction = False

    assert watch_movement(mover, [guard], squares=3) is None
    assert policy.asked == [], "the expensive question was asked anyway"


def test_the_dead_and_the_mover_itself_are_never_in_the_audience() -> None:
    mover = _creature("Runner", Cell(0, 0))
    mover.dispatcher.bind(economy=Economy(), gate=Policy())
    mover.attach(OpportunityAttack(mover))
    corpse, _, _, _ = _threatener("Corpse", Cell(0, -1))
    corpse.hp = 0

    assert watch_movement(mover, [mover, corpse], squares=3) is None


# --- the valves --------------------------------------------------------------


def test_disengaging_provokes_nobody() -> None:
    """Disengage, forced movement and teleportation share one valve rather than
    a check in every Command that moves a creature."""
    mover = _creature("Runner", Cell(0, 0))
    guard, reaction, economy, _ = _threatener("Guard", Cell(0, -1))

    walked = walk_provoking(mover, _east(Cell(0, 0), 3), [guard], provokes=False)

    assert walked == _east(Cell(0, 0), 3)
    assert mover.position == Cell(3, 0)
    assert reaction.swings == []
    assert economy.reaction


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


def test_an_effect_queued_by_a_reaction_is_refused_rather_than_dropped() -> None:
    """`hooks/dispatch.py` deliberately does not drain `ctx.pending_effects`; the
    engine that owns the event must, and for this one that is `w2c`. Until then
    the debt is loud, because a silently dropped Sentinel is a wrong number
    nobody would chase to this file."""
    mover = _creature("Runner", Cell(0, 0))
    guard, _, _, _ = _threatener("Guard", Cell(0, -1))

    class Sentinel(HookSource):
        __slots__ = ()

        @hook("on_leave_reach", costs=ActionCost.REACTION, gate=_GATE_ID)
        def slow(self, ctx: LeaveReachContext) -> R:
            ctx.apply_effect(ctx.mover, object())  # type: ignore[arg-type]
            return R.APPLIED

    guard.detach(guard.dispatcher.sources()[0])
    guard.attach(Sentinel(guard))

    with pytest.raises(EngineAssemblyError, match="queued an effect"):
        walk_provoking(mover, _east(Cell(0, 0), 3), [guard])


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
