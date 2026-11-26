"""The policy a `TargetDummy` is asked through: compiled from its profiles,
since a dummy has no build and no `apl:` (`docs/decisions.md` § w2d, § w2e,
§ w2g2).
"""

from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.actions.routine import Routine
from dndsim.apl.apl import Apl, Entry
from dndsim.core.rng import Rng
from dndsim.creatures.dummy import DummySpec, Movement, TargetDummy
from dndsim.engine.healing import heal
from dndsim.engine.world import World
from dndsim.hooks.contexts import TurnContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource

__all__ = ["Regeneration", "arm_dummy", "dummy_apl"]


class Regeneration(HookSource):
    """The dummy regains its spec's `regeneration` hit points through `heal()`
    at the start of its turn, while it has at least 1."""

    __slots__ = ("_amount", "_creature", "_rng")

    def __init__(self, owner: TargetDummy, *, rng: Rng) -> None:
        super().__init__(owner)
        self._creature = owner
        self._amount = owner.spec.regeneration
        self._rng = rng

    @hook("on_turn_start")
    def regain(self, ctx: TurnContext) -> R:
        """Heals the dummy unless it is at 0 hit points."""
        creature = self._creature
        if creature.hp < 1:
            return R.SKIPPED
        heal(creature, rng=self._rng, flat=self._amount, healer=creature)
        return R.APPLIED


def dummy_apl(spec: DummySpec) -> Apl:
    """The list `spec`'s profiles describe: every opportunity attack offered
    is taken, and the attack's `target:` and `move:` are its `threat_profile`
    and `movement_profile` — `static` holds, `closes_to_melee` closes on its
    threat, `kites_at(r)` backs away from the nearest enemy to `r` feet. The
    routine takes the same `move:`, and picks its own targets."""
    threat = spec.threat_profile.value
    profile = spec.movement_profile
    if profile.kind is Movement.STATIC:
        move = "hold"
    elif profile.kind is Movement.CLOSES_TO_MELEE:
        move = f"toward {threat} of enemies until in_reach"
    else:
        move = f"away_from nearest of enemies to {profile.range_ft} ft"
    return Apl(
        (
            Entry("opportunity_attack"),
            Entry("attack", target=f"{threat} of enemies.in_reach", move=move),
            Entry(Routine.ID, move=move),
        )
    )


def arm_dummy(
    dummy: TargetDummy, world: World, *, rng: Rng, apl: Apl | None = None
) -> Apl:
    """Bind `dummy`'s policy — `apl`, or by default `dummy_apl()` of its spec —
    to its dispatcher as the gate, attach the opportunity attack it answers,
    and attach its `Regeneration` when the spec has any. Returns the bound
    policy, for the turn loop."""
    apl = (dummy_apl(dummy.spec) if apl is None else apl).bind(dummy, world)
    dummy.dispatcher.bind(gate=apl)
    dummy.attach(
        OpportunityAttacks(dummy, world=world, rng=rng, trigger=apl.namespace.trigger)
    )
    if dummy.spec.regeneration:
        dummy.attach(Regeneration(dummy, rng=rng))
    return apl
