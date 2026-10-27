"""Who is offered a reaction to a move, on every five-foot step, and in what order.

`watch_movement()` builds the audience once per move; `MovementWatch.leaving()`
offers `on_leave_reach` to each watcher a step leaves, in initiative order. An
opportunity attack is not implemented here: it is a gated `on_leave_reach`
handler, and the dispatcher spends the reaction (`docs/decisions.md` § w0g3).

Sources: XPHB (2024) "Combat" -> Opportunity Attacks.

Open debts: rows 18-22 of ROADMAP.md § The W0 debt register. Until #18,
`_offer()` raises if a handler queues an effect, since nothing here drains
`ctx.pending_effects`.
"""

from collections.abc import Iterable, Sequence

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft, in_reach
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.hooks.contexts import LeaveReachContext
from dndsim.hooks.definitions import ON_LEAVE_REACH
from dndsim.rules.constants import CELL_SIZE_FT

__all__ = ["MovementWatch", "walk_provoking", "watch_movement"]


class _Watcher:
    """One bystander that could still react, and whether it threatens right now.

    `reach_ft` and `size` are cached for the length of one move.
    """

    __slots__ = ("creature", "reach_ft", "size", "spent", "threatening")

    def __init__(self, creature: Creature, *, threatening: bool) -> None:
        self.creature = creature
        self.reach_ft = creature.reach_ft
        self.size = creature.size
        self.threatening = threatening
        self.spent = False


class MovementWatch:
    """The audience for one creature's move, narrowed as the move goes on.

    Built by `watch_movement()` and driven by `walk_provoking()`, one call to
    `leaving()` per five-foot step. Valid for one move only.
    """

    __slots__ = ("_context", "_mover", "_watchers")

    def __init__(self, mover: Creature, watchers: list[_Watcher]) -> None:
        self._mover = mover
        self._watchers = watchers
        # Allocated on the first offer and rebound after: most moves make none.
        self._context: LeaveReachContext | None = None

    @property
    def watching(self) -> tuple[Creature, ...]:
        """Who is still owed an offer, in the order they would be asked; a
        watcher that has spent its reaction is gone from it."""
        return tuple(watcher.creature for watcher in self._watchers)

    def leaving(self, destination: Cell) -> None:
        """Offer `on_leave_reach` to every watcher this step steps out of.

        Called **before** the mover's position is updated, so the mover is
        still on `ctx.origin` while the offer resolves (`docs/decisions.md`
        § w0g3).
        """
        mover = self._mover
        origin = mover.position
        size = mover.size
        pruning = False
        for watcher in self._watchers:
            standing = watcher.creature.position
            now = in_reach(
                standing,
                destination,
                watcher.reach_ft,
                origin_size=watcher.size,
                destination_size=size,
            )
            if watcher.threatening and not now:
                watcher.threatening = False
                self._offer(watcher, origin, destination)
                pruning = pruning or watcher.spent
                if mover.is_down:
                    # A dropped mover ends the move; later watchers get no
                    # offer.
                    break
            else:
                watcher.threatening = now
        if pruning:
            self._watchers = [w for w in self._watchers if not w.spent]

    def _offer(self, watcher: _Watcher, origin: Cell, destination: Cell) -> None:
        """Hand one watcher the event and let its own handlers decide."""
        creature = watcher.creature
        context = self._context
        if context is None:
            context = self._context = LeaveReachContext(
                creature=creature,
                mover=self._mover,
                origin=origin,
                destination=destination,
            )
        else:
            context.reopen(
                creature=creature,
                mover=self._mover,
                origin=origin,
                destination=destination,
            )
        creature.dispatch(ON_LEAVE_REACH, context)
        if context._pending:
            raise EngineAssemblyError(
                f"{creature.name} queued an effect from an opportunity-attack "
                "offer and nothing here applies it. Draining "
                "`ctx.pending_effects` belongs to the engine that owns the "
                "event (hooks/dispatch.py), and for this one that is w2c's turn "
                "loop — see the module docstring."
            )
        context.close()
        # Asked once per offer rather than per step: the answer only changes
        # when a handler has just run, and this is the moment it just did.
        watcher.spent = not creature.dispatcher.responsive(ON_LEAVE_REACH)


def watch_movement(
    mover: Creature,
    bystanders: Iterable[Creature],
    *,
    squares: int,
    provokes: bool = True,
) -> MovementWatch | None:
    """The audience for `mover`'s next `squares` steps, or `None` for nobody.

    `None` says that no creature in `bystanders` can react to this move however
    it goes, so the caller may put the mover on its destination in one
    assignment (`docs/decisions.md` § w0g3).

    `bystanders` is the roster; the mover is skipped, and hostility is not
    asked about (`docs/decisions.md` § w0g3). `provokes=False` returns `None`:
    the valve for movement that does not provoke (debt #20).
    """
    if not provokes:
        return None
    origin = mover.position
    size = mover.size
    # The furthest any square of the route can be from its start; a bystander
    # beyond its reach plus this can never be left (`docs/decisions.md` § w0g3,
    # § w1d on footprints).
    horizon = squares * CELL_SIZE_FT
    watchers: list[_Watcher] = []
    for creature in bystanders:
        # Cheapest question first; `responsive()` empties the audience in most
        # fights.
        if creature is mover or not creature.dispatcher.responsive(ON_LEAVE_REACH):
            continue
        if creature.is_down:
            continue
        reach = creature.reach_ft
        standing = creature.position
        # `distance_ft` for the horizon, `in_reach` for the flag, never an
        # inlined Chebyshev (`docs/decisions.md` § w0g3).
        if (
            distance_ft(
                standing,
                origin,
                origin_size=creature.size,
                destination_size=size,
            )
            > reach + horizon
        ):
            continue
        watchers.append(
            _Watcher(
                creature,
                threatening=in_reach(
                    standing,
                    origin,
                    reach,
                    origin_size=creature.size,
                    destination_size=size,
                ),
            )
        )
    if not watchers:
        return None
    watchers.sort(key=_seat)
    return MovementWatch(mover, watchers)


def walk_provoking(
    mover: Creature,
    route: Sequence[Cell],
    bystanders: Iterable[Creature],
    *,
    provokes: bool = True,
) -> tuple[Cell, ...]:
    """Walk `mover` along `route` square by square, provoking on the way out.

    Returns the part of the route actually walked, which is the whole of it
    unless an opportunity attack dropped the mover partway. It does not choose
    the route or dispatch `on_move`.

    Each offer is made while the mover is still on the square it is leaving,
    and the position moves afterwards (`docs/decisions.md` § w0g3).
    """
    watch = watch_movement(mover, bystanders, squares=len(route), provokes=provokes)
    if watch is None:
        # Nobody can react: the route is a single assignment.
        if not route:
            return ()
        mover.position = route[-1]
        return tuple(route)
    walked = []
    for square in route:
        watch.leaving(square)
        if mover.is_down:
            break
        mover.position = square
        walked.append(square)
    return tuple(walked)


def _seat(watcher: _Watcher) -> InitiativeKey:
    """Initiative order, sorted once per move (`docs/decisions.md` § w0g3)."""
    return watcher.creature.initiative
