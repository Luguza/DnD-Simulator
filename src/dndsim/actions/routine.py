"""The target dummy's routine: its `damage_per_round` entries, taken as its
Action (`docs/decisions.md` § The `w2g` spec correction, § w2g2)."""

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
from dndsim.engine.damage import roll_damage
from dndsim.engine.query import enemies_of
from dndsim.engine.saves import save_for_half, saving_throw
from dndsim.engine.world import World
from dndsim.hooks.contexts import DamagePart
from dndsim.rules.enums import ActionCost

__all__ = ["Routine"]


class Routine(Action[int]):
    """`actor` takes every `damage_per_round` entry once, in order, each
    against up to `targets` distinct enemies its `threat_profile` picks, one
    roll per target: an attack entry against those within its reach, a save
    entry against any. `execute()` returns the damage dealt.
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
        enemies = enemies_of(self.world, dummy)
        return any(_within(dummy, weapon, enemies) for weapon in dummy.routine_weapons)

    def _perform(self, rng: Rng) -> int:
        dummy = self.dummy
        spec = dummy.spec
        dealt = 0
        for entry, weapon in zip(
            spec.damage_per_round, dummy.routine_weapons, strict=True
        ):
            enemies = _within(dummy, weapon, enemies_of(self.world, dummy))
            for target in _threats(dummy, enemies)[: entry.targets]:
                if weapon is None:
                    dealt += self._force(entry, target, rng)
                else:
                    dealt += resolve_attack(
                        dummy,
                        target,
                        rng=rng,
                        weapon=weapon,
                        ability=ROUTINE_ABILITY,
                        log=self.log,
                        world=self.world,
                    ).damage_dealt
        return dealt

    def _force(self, entry: DummyAttack, target: Creature, rng: Rng) -> int:
        dummy = self.dummy
        ability, dc = entry.save, dummy.spec.save_dc
        if ability is None or dc is None:
            raise ValueError(f"{entry} is not a save entry with a DC to force")
        result = saving_throw(
            target, ability=ability, dc=dc, rng=rng, imposed_by=dummy, log=self.log
        )
        part = DamagePart(entry.dice, entry.flat, entry.damage_type, self.ID)
        amounts = roll_damage(
            (part,), rng=rng, base_type=entry.damage_type, critical=False
        )
        return save_for_half(
            target,
            amounts,
            rng=rng,
            outcome=result.outcome,
            attacker=dummy,
            log=self.log,
        )


def _within(
    dummy: TargetDummy, weapon: Weapon | None, enemies: tuple[Creature, ...]
) -> tuple[Creature, ...]:
    """The enemies an entry can be taken against: within `weapon`'s reach, or
    every one for a save entry."""
    if weapon is None:
        return enemies
    return tuple(enemy for enemy in enemies if within_reach(dummy, enemy, weapon))


def _threats(dummy: TargetDummy, enemies: tuple[Creature, ...]) -> list[Creature]:
    """`enemies` in the order the dummy's `threat_profile` picks them, ties in
    seating order, as the APL's `nearest` and `lowest_hp` break them."""
    if dummy.spec.threat_profile is ThreatProfile.LOWEST_HP:
        return sorted(enemies, key=lambda enemy: enemy.hp)
    origin, size = dummy.position, dummy.size
    return sorted(
        enemies,
        key=lambda enemy: distance_ft(
            origin, enemy.position, origin_size=size, destination_size=enemy.size
        ),
    )
