"""`Encounter` — initiative, the round loop, and who joins and leaves.

The order is never stored: each turn goes to the creature whose key is the
least one greater than the last to act this round, so a creature joining
mid-round acts this round if its place is still ahead, and one leaving is
simply never found again (`docs/decisions.md` § w2c; DESIGN.md § Initiative is
a key, not an index).
"""

from typing import Final, NamedTuple

from dndsim.core.rng import Rng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import settle
from dndsim.engine.query import standing_sides
from dndsim.engine.turn import Policy, roll_initiative, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import Context, EncounterContext, RoundContext
from dndsim.hooks.definitions import (
    ON_ENCOUNTER_END,
    ON_ENCOUNTER_START,
    ON_ROUND_END,
    ON_ROUND_START,
)
from dndsim.hooks.registry import HookSpec

__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "ROUND_END",
    "ROUND_START",
    "Encounter",
    "EncounterResult",
]

ROUND_START: Final = "round_start"
"""`(ROUND_START, round)`, at every level."""

ROUND_END: Final = "round_end"
"""`(ROUND_END, round)`, at every level."""

DEFAULT_MAX_ROUNDS: Final = 100
"""Where a fight nobody can finish is called off."""


class EncounterResult(NamedTuple):
    """How a fight ended: the rounds begun, and the one side left standing,
    or `None` when none or several are."""

    rounds: int
    winner: str | None


class Encounter:
    """One fight over `world`'s roster, each turn decided by `policy`."""

    __slots__ = ("_started", "max_rounds", "policy", "rng", "world")

    def __init__(
        self,
        world: World,
        *,
        policy: Policy,
        rng: Rng,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        if max_rounds < 1:
            raise ValueError(f"an encounter lasts at least one round, not {max_rounds}")
        self.world = world
        self.policy = policy
        self.rng = rng
        self.max_rounds = max_rounds
        self._started = False

    def join(
        self, creature: Creature, side: str, *, initiative: InitiativeKey | None = None
    ) -> None:
        """Seat `creature` on `side`. Once the fight has begun it rolls its own
        initiative, unless `initiative` places it — a summon's
        `sharing_turn()` — and no other creature's key moves."""
        self.world.seat(creature, side)
        if initiative is not None:
            creature.initiative = initiative
        elif self._started:
            creature.initiative = roll_initiative(creature, rng=self.rng)

    def leave(self, creature: Creature) -> None:
        """Drop `creature` from the roster; its key is left alone."""
        self.world.leave(creature)

    def run(self) -> EncounterResult:
        """Roll initiative and play rounds until at most one side is standing
        or `max_rounds` have been played."""
        if self._started:
            raise ValueError("an encounter is run once")
        self._started = True
        world = self.world
        rng = self.rng
        log = world.log
        self._everyone(
            ON_ENCOUNTER_START, EncounterContext(combatants=tuple(world))
        )
        for creature in tuple(world):
            creature.initiative = roll_initiative(creature, rng=rng)

        number = 0
        while number < self.max_rounds and len(standing_sides(world)) > 1:
            number += 1
            if log is not None:
                log.write((ROUND_START, number))
            self._everyone(ON_ROUND_START, RoundContext(round=number))
            last: InitiativeKey | None = None
            while len(standing_sides(world)) > 1:
                actor = _next(world, last)
                if actor is None:
                    break
                last = actor.initiative
                take_turn(world, actor, number, policy=self.policy, rng=rng)
            self._everyone(ON_ROUND_END, RoundContext(round=number))
            if log is not None:
                log.write((ROUND_END, number))

        self._everyone(
            ON_ENCOUNTER_END, EncounterContext(combatants=tuple(world))
        )
        sides = standing_sides(world)
        winner = next(iter(sides)) if len(sides) == 1 else None
        return EncounterResult(number, winner)

    def _everyone(self, spec: HookSpec, ctx: Context) -> None:
        """Dispatch a GLOBAL hook to every seated creature, in seating order."""
        everyone = tuple(self.world)
        for creature in everyone:
            creature.dispatch(spec, ctx)
        settle(
            ctx,
            f"vetoed at {spec.name}, which cannot be called off: the encounter's "
            "boundaries are positions on the clock.",
            *everyone,
        )
        ctx.close()


def _next(world: World, last: InitiativeKey | None) -> Creature | None:
    """The living creature whose turn follows `last` this round, or the first
    one when `last` is `None`."""
    following: Creature | None = None
    for creature in world:
        if creature.dead:
            continue
        key = creature.initiative
        if last is not None and not key > last:
            continue
        if following is None or key < following.initiative:
            following = creature
    return following
