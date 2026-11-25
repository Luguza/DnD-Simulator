"""`Encounter` — initiative, the round loop, and who joins and leaves.

The order is never stored: each turn goes to the creature whose key is the
least one greater than the last to act this round, so a creature joining
mid-round acts this round if its place is still ahead, and one leaving is
simply never found again (`docs/decisions.md` § w2c; DESIGN.md § Initiative is
a key, not an index).
"""

from collections.abc import Iterable, Mapping
from typing import Final, NamedTuple

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.rng import Rng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.engine.d20 import settle
from dndsim.engine.query import standing_sides
from dndsim.engine.turn import Policy, roll_initiative, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import (
    Context,
    EncounterContext,
    InitiativeContext,
    RoundContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import (
    ON_ENCOUNTER_END,
    ON_ENCOUNTER_START,
    ON_ROUND_END,
    ON_ROUND_START,
)
from dndsim.hooks.registry import HookSpec
from dndsim.hooks.source import HookSource

__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "ROUND_END",
    "ROUND_START",
    "Encounter",
    "EncounterResult",
    "Surprised",
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


class Surprised(HookSource):
    """XPHB "Surprise": Disadvantage on the owner's Initiative roll. Attached
    by `Encounter.run()` for the roll and detached after it."""

    __slots__ = ()

    @hook("on_initiative_roll")
    def initiative_roll(self, ctx: InitiativeContext) -> R:
        """ "If a combatant is surprised by combat starting, that combatant has
        Disadvantage on their Initiative roll"."""
        ctx.impose_disadvantage(self.ID)
        return R.APPLIED


class Encounter:
    """One fight over `world`'s roster, each turn decided by the creature's
    own entry in `policies`, or by `policy` for a creature with none.

    `surprised` are the creatures surprised by the fight starting. Raises
    `ValueError` for fewer than one round, or for a surprised creature not
    seated in `world`.
    """

    __slots__ = (
        "_placed",
        "_started",
        "max_rounds",
        "policies",
        "policy",
        "rng",
        "surprised",
        "world",
    )

    def __init__(
        self,
        world: World,
        *,
        rng: Rng,
        policy: Policy | None = None,
        policies: Mapping[Creature, Policy] | None = None,
        surprised: Iterable[Creature] = (),
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        if max_rounds < 1:
            raise ValueError(f"an encounter lasts at least one round, not {max_rounds}")
        self.world = world
        self.policy = policy
        self.policies: dict[Creature, Policy] = (
            {} if policies is None else dict(policies)
        )
        self.surprised = tuple(surprised)
        for creature in self.surprised:
            if creature not in world:
                raise ValueError(
                    f"{creature.name} is surprised but is not seated in the fight"
                )
        self.rng = rng
        self.max_rounds = max_rounds
        self._started = False
        self._placed: set[Creature] = set()

    def join(
        self,
        creature: Creature,
        side: str,
        *,
        initiative: InitiativeKey | None = None,
        policy: Policy | None = None,
    ) -> None:
        """Seat `creature` on `side`, its turns decided by `policy` when one
        is given. It rolls its own initiative — at once
        when the fight has begun, with everyone else otherwise — unless
        `initiative` places it, a summon's `sharing_turn()`; no other
        creature's key moves. Raises `ValueError` for a key another seated
        creature holds: the order would have no place for one of the two; and,
        before the start, for `UNSEATED` or a `sharing_turn()` key, since the
        creature it was taken from has yet to roll."""
        if (
            initiative is not None
            and not self._started
            and (initiative.after or _unrolled(initiative))
        ):
            raise ValueError(
                f"{creature.name} was placed at {initiative} before the fight "
                "began, but the creature whose turn it shares has yet to roll "
                "initiative; join it once the fight has begun"
            )
        if initiative is not None and any(
            other.initiative == initiative for other in self.world
        ):
            raise ValueError(
                f"{creature.name} was placed at {initiative}, which another "
                "creature holds; a second creature sharing a turn takes "
                "sharing_turn(order) with the next order"
            )
        self.world.seat(creature, side)
        if policy is not None:
            self.policies[creature] = policy
        if initiative is not None:
            creature.initiative = initiative
            self._placed.add(creature)
        elif self._started:
            creature.initiative = roll_initiative(creature, rng=self.rng)

    def leave(self, creature: Creature) -> None:
        """Drop `creature` from the roster; its key is left alone."""
        self.world.leave(creature)

    def run(self) -> EncounterResult:
        """Roll initiative and play rounds until at most one side is standing
        or `max_rounds` have been played.

        Raises `EngineAssemblyError` when a creature's turn comes and it has
        no policy."""
        if self._started:
            raise ValueError("an encounter is run once")
        self._started = True
        world = self.world
        rng = self.rng
        log = world.log
        self._everyone(ON_ENCOUNTER_START, EncounterContext(combatants=tuple(world)))
        surprised = self.surprised
        for creature in tuple(world):
            if creature in self._placed:
                continue
            if creature in surprised:
                surprise = Surprised(creature)
                creature.attach(surprise)
                creature.initiative = roll_initiative(creature, rng=rng)
                creature.detach(surprise)
            else:
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
                take_turn(world, actor, number, policy=self._policy(actor), rng=rng)
            self._everyone(ON_ROUND_END, RoundContext(round=number))
            if log is not None:
                log.write((ROUND_END, number))

        self._everyone(ON_ENCOUNTER_END, EncounterContext(combatants=tuple(world)))
        sides = standing_sides(world)
        winner = next(iter(sides)) if len(sides) == 1 else None
        return EncounterResult(number, winner)

    def _policy(self, actor: Creature) -> Policy:
        policy = self.policies.get(actor, self.policy)
        if policy is None:
            raise EngineAssemblyError(
                f"{actor.name} has no policy in this encounter; give it one in "
                "`policies` or at join(), or give the encounter a default `policy`"
            )
        return policy

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


def _unrolled(key: InitiativeKey) -> bool:
    """Whether `key` is `UNSEATED` or a place in its summon chain."""
    return (
        key.initiative,
        key.dexterity,
        key.player_character,
        key.tiebreak,
    ) == (
        UNSEATED.initiative,
        UNSEATED.dexterity,
        UNSEATED.player_character,
        UNSEATED.tiebreak,
    )


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
