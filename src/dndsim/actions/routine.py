"""A target dummy's scripted routine, taken as its Action
(`docs/decisions.md` § The `w2g` spec correction, § w2g2)."""

from typing import ClassVar

from dndsim.actions.base import Action
from dndsim.core.geometry import distance_ft
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import (
    ROUTINE_ABILITY,
    DummyAttack,
    TargetDummy,
    ThreatProfile,
)
from dndsim.creatures.stats import Weapon
from dndsim.engine.attack import resolve_attack, within_reach
from dndsim.engine.query import enemies_of
from dndsim.engine.saves import force_save, save_for_half
from dndsim.engine.world import World
from dndsim.rules.enums import ActionCost

__all__ = ["Routine"]


class Routine(Action[int]):
    """`actor` makes every `damage_per_round` entry of its spec once, in order,
    each against up to `targets` distinct standing enemies its `threat_profile`
    picks,
    one roll per target; `execute()` returns the damage dealt.

    An attack entry is made through `resolve_attack()` against the enemies
    within its 5-foot reach; a save entry is forced through `force_save()`
    against any enemy, at any distance, and dealt through `save_for_half()`.
    Legal while some entry has an enemy to act on. `log` is handed to each.
    """

    __slots__ = ("dummy", "log", "world")

    ID: ClassVar[str] = "routine"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def __init__(
        self, actor: TargetDummy, *, world: World, log: EventLog | None = None
    ) -> None:
        super().__init__(actor)
        self.dummy = actor
        self.world = world
        self.log = log

    def _permits(self) -> bool:
        dummy = self.dummy
        return dummy in self.world and any(
            self._targets(entry, weapon)
            for entry, weapon in zip(
                dummy.spec.damage_per_round, dummy.routine_weapons, strict=True
            )
        )

    def _targets(self, entry: DummyAttack, weapon: Weapon | None) -> list[Creature]:
        """Up to `entry.targets` standing enemies, in the order the threat
        profile ranks them; ties keep seating order."""
        dummy = self.dummy
        enemies = tuple(e for e in enemies_of(self.world, dummy) if not e.is_down)
        if weapon is not None:
            enemies = tuple(e for e in enemies if within_reach(dummy, e, weapon))
        if dummy.spec.threat_profile is ThreatProfile.LOWEST_HP:
            ranked = sorted(enemies, key=lambda enemy: enemy.hp)
        else:
            ranked = sorted(
                enemies,
                key=lambda enemy: distance_ft(
                    dummy.position,
                    enemy.position,
                    origin_size=dummy.size,
                    destination_size=enemy.size,
                ),
            )
        return ranked[: entry.targets]

    def _perform(self, rng: Rng) -> int:
        dummy = self.dummy
        log = self.log
        dealt = 0
        for entry, weapon in zip(
            dummy.spec.damage_per_round, dummy.routine_weapons, strict=True
        ):
            # A defender's handler can drop the dummy partway through.
            if _stopped(dummy):
                break
            targets = self._targets(entry, weapon)
            if weapon is not None:
                for target in targets:
                    if _stopped(dummy):
                        break
                    dealt += resolve_attack(
                        dummy,
                        target,
                        rng=rng,
                        weapon=weapon,
                        ability=ROUTINE_ABILITY,
                        log=log,
                        world=self.world,
                    ).damage_dealt
                continue
            ability, dc = entry.save, dummy.spec.save_dc
            if ability is None or dc is None:
                raise AssertionError(f"{entry} is neither an attack nor a save")
            results = force_save(
                dummy, targets, ability=ability, dc=dc, rng=rng, log=log
            )
            for target, result in zip(targets, results, strict=True):
                amount = entry.dice.roll(rng) + entry.flat
                dealt += save_for_half(
                    target,
                    {entry.damage_type: amount},
                    rng=rng,
                    outcome=result.outcome,
                    attacker=dummy,
                    log=log,
                )
        return dealt


def _stopped(creature: Creature) -> bool:
    return creature.dead or creature.is_down or creature.incapacitated
