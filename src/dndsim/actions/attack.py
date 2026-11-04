"""The Attack action: one attack with a weapon (XPHB, "Actions" -> Attack)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import Weapon
from dndsim.engine.attack import (
    FRESH,
    AttackContexts,
    AttackResult,
    Trigger,
    resolve_attack,
    within_range,
)
from dndsim.engine.world import World
from dndsim.rules.enums import ActionCost

__all__ = ["Attack"]


class Attack(Action[AttackResult]):
    """`actor` attacks `target` with `weapon`, or with what it wields.

    One attack per action; Extra Attack is not modelled. A named weapon is
    refused while it lies where the actor dropped it (`docs/decisions.md`
    § w2b). `contexts`, `trigger`, `log` and `world` are handed to
    `resolve_attack()`, which a ranged attack cannot be resolved without a
    `world` for (`docs/decisions.md` § w2f2).
    """

    __slots__ = ("contexts", "log", "target", "trigger", "weapon", "world")

    ID: ClassVar[str] = "attack"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def __init__(
        self,
        actor: Creature,
        target: Creature,
        *,
        weapon: Weapon | None = None,
        contexts: AttackContexts = FRESH,
        trigger: Trigger | None = None,
        log: EventLog | None = None,
        world: World | None = None,
    ) -> None:
        super().__init__(actor)
        self.target = target
        self.weapon = weapon
        self.contexts = contexts
        self.trigger = trigger
        self.log = log
        self.world = world

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        weapon = actor.weapon if self.weapon is None else self.weapon
        return (
            weapon is not None
            and weapon is not actor.dropped_weapon
            and target is not actor
            and not target.dead
            and self._reaches(weapon)
        )

    def _reaches(self, weapon: Weapon) -> bool:
        return within_range(self.actor, self.target, weapon)

    def _perform(self, rng: Rng) -> AttackResult:
        return resolve_attack(
            self.actor,
            self.target,
            rng=rng,
            weapon=self.weapon,
            contexts=self.contexts,
            trigger=self.trigger,
            log=self.log,
            world=self.world,
        )
