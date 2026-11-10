"""The policy a `TargetDummy` is asked through: compiled from its profiles,
since a dummy has no build and no `apl:` (`docs/decisions.md` § w2d, § w2e).
"""

from dndsim.actions.opportunity_attack import OpportunityAttacks
from dndsim.apl.apl import Apl, Entry
from dndsim.core.rng import Rng
from dndsim.creatures.dummy import DummySpec, Movement, TargetDummy
from dndsim.engine.world import World

__all__ = ["arm_dummy", "dummy_apl"]


def dummy_apl(spec: DummySpec) -> Apl:
    """The list `spec`'s profiles describe: every opportunity attack offered
    is taken, and the attack's `target:` and `move:` are its `threat_profile`
    and `movement_profile` — `static` holds, `closes_to_melee` closes on its
    threat, `kites_at(r)` backs away from the nearest enemy to `r` feet."""
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
        )
    )


def arm_dummy(dummy: TargetDummy, world: World, *, rng: Rng) -> Apl:
    """Bind `dummy`'s policy to its dispatcher as the gate and attach the
    opportunity attack it answers. Returns the bound policy, for the turn
    loop."""
    apl = dummy_apl(dummy.spec).bind(dummy, world)
    dummy.dispatcher.bind(gate=apl)
    dummy.attach(
        OpportunityAttacks(dummy, world=world, rng=rng, trigger=apl.namespace.trigger)
    )
    return apl
