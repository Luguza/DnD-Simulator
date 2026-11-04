"""The Opportunity Attack: one melee attack, made with the Reaction, against a
creature leaving your reach (XPHB, "Combat" -> Opportunity Attacks).

`OpportunityAttack` is the Command; `OpportunityAttacks` is the rule a creature
carries, which the movement audience offers it (`engine/triggers.py`) and its
policy answers. `docs/decisions.md` § w2c2.
"""

from typing import ClassVar

from dndsim.actions.attack import Attack
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import Weapon
from dndsim.engine.attack import (
    FRESH,
    AttackContexts,
    AttackResult,
    Trigger,
    within_reach,
)
from dndsim.engine.world import World
from dndsim.hooks.contexts import LeaveReachContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import ActionCost

__all__ = ["OpportunityAttack", "OpportunityAttacks"]


class OpportunityAttack(Attack):
    """`actor` attacks `target`, which is leaving its reach, with what it
    wields.

    Refused against a creature on `actor`'s own side of `world`, and with a
    Ranged weapon: it is a melee attack, so a Thrown melee weapon is swung.
    `resolve()` carries it out without spending the Reaction, for the
    dispatcher that spends it (`docs/decisions.md` § w2c2, § w2f2).
    """

    __slots__ = ()

    ID: ClassVar[str] = "opportunity_attack"
    COST: ClassVar[ActionCost | None] = ActionCost.REACTION

    def __init__(
        self,
        actor: Creature,
        target: Creature,
        *,
        world: World,
        contexts: AttackContexts = FRESH,
        trigger: Trigger | None = None,
        log: EventLog | None = None,
    ) -> None:
        super().__init__(
            actor, target, contexts=contexts, trigger=trigger, log=log, world=world
        )

    def _permits(self) -> bool:
        world, actor, target = self.world, self.actor, self.target
        return (
            world is not None
            and target in world
            and actor in world
            and world.side_of(target) != world.side_of(actor)
            and super()._permits()
        )

    def _reaches(self, weapon: Weapon) -> bool:
        return not weapon.ranged and within_reach(self.actor, self.target, weapon)

    def resolve(self, *, rng: Rng) -> AttackResult:
        """Make the attack with its Reaction already accounted for.

        Raises `ValueError` when `is_legal()` would have said no.
        """
        if not self.is_legal():
            raise ValueError(
                f"{self.actor.name} cannot make an opportunity attack against "
                f"{self.target.name} now; is_legal() answers first"
            )
        return self._perform(rng)


class OpportunityAttacks(HookSource):
    """Offers its owner an `OpportunityAttack` against each creature that
    leaves its reach, gated on the owner's policy under `OpportunityAttack.ID`.

    `contexts` is the reaction's own attack pool, never the one the owner's
    turn uses (`docs/decisions.md` § w1f, § w2c2); `trigger` is handed to
    `resolve_attack()`. The attack is written to `world`'s log.
    """

    __slots__ = ("_creature", "contexts", "rng", "trigger", "world")

    ID: ClassVar[str] = "opportunity_attack"

    def __init__(
        self,
        owner: Creature,
        *,
        world: World,
        rng: Rng,
        contexts: AttackContexts = FRESH,
        trigger: Trigger | None = None,
    ) -> None:
        super().__init__(owner)
        self._creature = owner
        self.world = world
        self.rng = rng
        self.contexts = contexts
        self.trigger = trigger

    @hook(
        "on_leave_reach",
        costs=ActionCost.REACTION,
        gate=OpportunityAttack.ID,
    )
    def swing(self, ctx: LeaveReachContext) -> R:
        """Attacks the mover; the dispatcher spends the Reaction on `APPLIED`."""
        mover = self.world.holder(ctx.mover)
        if mover is None:
            return R.SKIPPED
        attack = OpportunityAttack(
            self._creature,
            mover,
            world=self.world,
            contexts=self.contexts,
            trigger=self.trigger,
            log=self.world.log,
        )
        if not attack.is_legal():
            return R.SKIPPED
        attack.resolve(rng=self.rng)
        return R.APPLIED
