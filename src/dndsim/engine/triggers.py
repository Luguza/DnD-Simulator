"""Who is offered a reaction, when, and in what order — starting with movement.

DESIGN.md § Module layout draws the line this file sits on: `actions/` holds
every **Command** whatever economy slot it costs, and "`engine/triggers.py`
holds only the other half: which event fires an offer, who is in the audience,
in what order, and the turn-boundary reaction invariant." An opportunity attack
is therefore not implemented here. What is here is the audience for it.

**A first cut, and why it is in W0 rather than in `w2c` with the turn loop.**
The precedent is `w0g2`, which pulled first cuts of `creatures/`,
`engine/damage.py` and `core/geometry.py` forward for exactly one reason: the
performance spike measures nothing against a stub, and movement that fires no
triggers is a stub of the most flattering kind — the grid is walked, the
provocation it exists to produce costs zero. `w2c` completes this file with the
turn loop, the `opportunity_attack` Command, the reaction invariant and the
audiences of every other offer; nothing below is rewritten by it.

## The observer, and why it is not a broadcast

The request this file answers is an **Observer**: every creature that could
react is told, on every five-foot step, where the mover is standing and which
square it is stepping into, and each decides for itself whether to spend its
reaction. That is what happens. What does *not* happen is a message to every
combatant in the room per step, and the difference is worth stating because it
is the whole of "efficiently":

- **The trigger condition is the engine's, the decision is the creature's.**
  Whether a step leaves somebody's reach is arithmetic out of the rulebook, not
  policy, and `Scope.OBSERVER` exists precisely so that no feature writes its
  own version of it (DESIGN.md § Hook scope). Whether to *spend the reaction* on
  it is policy, and that goes to the creature's action list through the
  dispatcher's `gate` — see § The reaction, below.
- **The audience is filtered by things that cannot change the answer.** A
  bystander is dropped only when it provably could not have acted: it has no
  handler on `on_leave_reach` at all, or every handler it has costs a reaction
  it has already spent (`Dispatcher.responsive`), or it is far enough from the
  whole route that no square of it is ever within its reach. Each of the three
  is a filter on the *offer*, never on the answer, so the filtered audience and
  a broadcast produce the same fight — the filtered one just does not dispatch
  twenty times a step to learn that nineteen combatants own no reaction feature.

The shape that falls out is the standard broad-phase/narrow-phase split:

1. **Once per move** — walk the roster, keep the bystanders that pass the three
   filters, sort them into initiative order, and record for each whether it
   currently threatens the mover. In the overwhelmingly common case the list
   comes back empty and `watch_movement()` returns `None`, which is the caller's
   licence to move the creature in one assignment and pay nothing at all.
2. **Once per five-foot step, per surviving watcher** — one Chebyshev distance
   against the destination square. A watcher that threatened the square being
   left and does not threaten the one being entered is the opportunity-attack
   trigger, and is the only case that dispatches anything.

So the per-step cost is linear in *watchers*, not in combatants, and the count
of watchers in a real fight is nought, one or two. The contract tests count the
dispatches and the distance queries rather than timing them: an efficiency claim
that is asserted as a wall-clock number on a shared runner is a claim that gets
deleted the first time CI is busy.

## The order two watchers are asked in

One step can leave two creatures' reach at once, and the two are asked in
**initiative order** — the `InitiativeKey` total order of `core/timeline.py`,
sorted once per move rather than per step. Any order produces legal D&D; only a
stated one produces the same fight twice from the same seed, and determinism is
what `trace`, the golden tests and every regression diff are built on
(DESIGN.md § Determinism).

## The reaction

Nothing here asks whether a creature *wants* to react, and nothing here spends
anything. Both belong to the dispatcher, which already does them: an
opportunity attack is a handler declaring

    @hook("on_leave_reach", costs=ActionCost.REACTION, gate="attack.opportunity_attack")

so `_permits()` asks the economy whether a reaction is in hand and then asks the
creature's action list whether this one is worth it, and `_spend()` takes the
reaction **only if the handler returned `APPLIED`** — a declined opportunity
attack leaves the reaction in hand for the next one (DESIGN.md § The uniform
handler shape). A reaction entry no APL mentions answers False: reactions are
OFF by default, and a build that means to swing at runners says so, which is the
Null Object policy of DESIGN.md § APL.

## What `w2c` still owes this file

- **Draining `ctx.pending_effects`.** The dispatcher deliberately does not
  (`hooks/dispatch.py`), and the engine that owns the event must. There are no
  effects to queue until `w1c`, so rather than write a drain against a class
  that does not exist, `_offer()` raises if a handler queues one.
- **Speed set to zero**, which is what 2024 Sentinel does to a creature it hits
  on the way past. The walk below stops on the mover going down because hit
  points exist; it cannot stop on a speed of zero until `w1a`'s `ActionEconomy`
  and `w1c`'s effects do.
- **Disengage, forced movement and teleportation**, which do not provoke. The
  valve is here — `provokes=False` — but what sets it is the Command and the
  condition, and both are `w2b`/`w2c`'s.
- **Whether the watcher can see the mover.** The rule is "a creature *you can
  see* leaves your reach", and there is no visibility gate below. It is exact
  while nothing in the fight is Blinded or Invisible, which is every fight until
  `w1h` builds the conditions — and wrong the first time one is, so it is the
  debt most likely to be forgotten because it costs nothing yet.
- **Entering** reach, which 2024 Polearm Master reacts to. There is no hook for
  it: the registry is closed at 39 and this is not the task that opens it. It is
  a genuine gap and it is `w3e`'s paper pass to find and `w3f`'s to batch.

All five are rows 18-22 of ROADMAP.md § The W0 debt register, which is the
ledger an agent actually reads before touching a file; this list is the local
copy, not the record.

Sources: XPHB (2024) "Combat" — Opportunity Attacks, "you can make an
opportunity attack when a creature you can see leaves your reach"; DESIGN.md
§ Module layout, § Hook scope, § APL, § Determinism; ROADMAP w0g2, w2b, w2c,
w2e.
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

    Flattened at watch time for the reason `BoundHandler` is flattened at attach
    time: the per-step loop reads `reach_ft` and `threatening` once per watcher
    per square, and an attribute chain through a property is not free. `reach_ft`
    is safe to cache for the length of one move — it is the watcher's weapon, and
    the watcher is not the one taking a turn.
    """

    __slots__ = ("creature", "reach_ft", "spent", "threatening")

    def __init__(self, creature: Creature, *, threatening: bool) -> None:
        self.creature = creature
        self.reach_ft = creature.reach_ft
        self.threatening = threatening
        self.spent = False


class MovementWatch:
    """The audience for one creature's move, narrowed as the move goes on.

    Built by `watch_movement()` and driven by `walk_provoking()`, one call to
    `leaving()` per five-foot step. It is deliberately not reusable across moves:
    who threatens whom is a fact about the positions at the top of a move, and a
    watch held over from the last one would be answering about a fight that has
    since moved.
    """

    __slots__ = ("_context", "_mover", "_watchers")

    def __init__(self, mover: Creature, watchers: list[_Watcher]) -> None:
        self._mover = mover
        self._watchers = watchers
        # Allocated on the first offer and rebound on every one after it. Most
        # moves make no offer at all, so a context built with the watch would be
        # an allocation per move to carry an event that usually does not happen;
        # DESIGN.md § Levers' pooling argument, applied to a cold path where the
        # thing worth not paying for is the first allocation rather than the
        # hundredth.
        self._context: LeaveReachContext | None = None

    @property
    def watching(self) -> tuple[Creature, ...]:
        """Who is still owed an offer, in the order they would be asked.

        What `dndsim inspect` (w2h) prints and what a test asserts the audience
        against — the live list, not a reconstruction of it, so a watcher that
        has spent its reaction is visibly gone from it.
        """
        return tuple(watcher.creature for watcher in self._watchers)

    def leaving(self, destination: Cell) -> None:
        """Offer `on_leave_reach` to every watcher this step steps out of.

        Called **before** the mover's position is updated, because that is when
        the offer is true: "you can make an opportunity attack when a creature
        you can see leaves your reach" happens right before it leaves, so the
        mover is still standing on `ctx.origin` while the attack resolves and a
        five-foot reach can still find it there.
        """
        mover = self._mover
        origin = mover.position
        pruning = False
        for watcher in self._watchers:
            standing = watcher.creature.position
            now = in_reach(standing, destination, watcher.reach_ft)
            if watcher.threatening and not now:
                watcher.threatening = False
                self._offer(watcher, origin, destination)
                pruning = pruning or watcher.spent
                if not mover.is_alive:
                    # An opportunity attack that dropped the mover ends the
                    # move, and the watchers after this one in initiative order
                    # never get their offer: there is no longer a creature
                    # leaving anybody's reach.
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

    `None` is not an error and is the answer most of the time — it says that no
    creature in `bystanders` can react to this move however it goes, so the
    caller may put the mover on its destination in one assignment. Returning it
    rather than an empty watch is deliberate: an empty **Null Object** would
    still cost a per-step call and a per-step loop over nothing, on a path whose
    whole claim is that it costs nothing when nobody is watching.

    `bystanders` is the roster minus the mover, and hostility is not asked about
    here. An opportunity attack against an ally is legal and pointless, and the
    two places that already say so are the `opportunity_attack` Command's
    legality check (`w2b`) and the APL entry's `if:` — a side filter here would
    be a third, in the file least able to see a build's intent.

    `provokes=False` is the one valve: Disengage, forced movement and
    teleportation move a creature without ever leaving anybody's reach in the
    sense the rule means. Naming it here keeps that rule in one place rather
    than in every Command that moves somebody.
    """
    if not provokes:
        return None
    origin = mover.position
    # The furthest any square of a route `squares` long can be from where it
    # starts, because a step is one square in Chebyshev whatever its direction
    # (`core/geometry.py`). A bystander beyond its own reach plus this can never
    # have the mover inside its reach, so it can never be left.
    horizon = squares * CELL_SIZE_FT
    watchers: list[_Watcher] = []
    for creature in bystanders:
        # Ordered cheapest-question-first, like the dispatcher's own three
        # valves: an identity test, then a dict lookup, then an economy call,
        # then two properties and the geometry. The dict lookup is what carries
        # the loop — a combatant with nothing attached to `on_leave_reach`
        # leaves here, and in most fights that is every combatant.
        if creature is mover or not creature.dispatcher.responsive(ON_LEAVE_REACH):
            continue
        if not creature.is_alive:
            continue
        reach = creature.reach_ft
        standing = creature.position
        # `distance_ft` for the horizon and `in_reach` for the flag, rather than
        # one subtraction doing both. They are the same arithmetic today and
        # will stop being: `core/geometry.py` says `in_reach` is the one query
        # "the attack pipeline, the opportunity-attack trigger and the APL's
        # `enemies.in_reach` selector all ask, in one place so that 'reach'
        # means the same thing to all three", and `w1d` is the task that makes
        # that sentence earn its keep by giving a Large creature a reach its
        # square does not describe (debt 9). An inlined Chebyshev here would be
        # the copy that quietly does not get that. The horizon is a bounding-box
        # question rather than a reach one and stays raw distance — `w1d` owes
        # it the same second look, since a footprint wider than one square
        # widens the bound too.
        if distance_ft(standing, origin) > reach + horizon:
            continue
        watchers.append(
            _Watcher(creature, threatening=in_reach(standing, origin, reach))
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
    unless an opportunity attack dropped the mover partway. The caller keeps the
    two things around this: choosing the destination and resolving the route
    (`core/geometry.route()`, the APL's `move:` clause), and dispatching
    `on_move` over what came back — this function moves a creature, it does not
    decide that it should.

    The rule it exists to hold in one place is the ordering: the offer is made
    while the mover is still on the square it is leaving, and the position moves
    afterwards. Written at each call site instead, that is an off-by-one square
    that nothing would catch — the attack would resolve from where the mover
    ended up, and a five-foot reach would find it out of range about as often as
    not.
    """
    watch = watch_movement(mover, bystanders, squares=len(route), provokes=provokes)
    if watch is None:
        # The fast path, and the one taken by most moves in most fights: nobody
        # can react, so there is nothing to observe on the way and the route is
        # a single assignment.
        if not route:
            return ()
        mover.position = route[-1]
        return tuple(route)
    walked = []
    for square in route:
        watch.leaving(square)
        if not mover.is_alive:
            break
        mover.position = square
        walked.append(square)
    return tuple(walked)


def _seat(watcher: _Watcher) -> InitiativeKey:
    """Initiative order, sorted once per move — see § The order, above."""
    return watcher.creature.initiative
