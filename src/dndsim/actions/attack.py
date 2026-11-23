"""The Attack action: one attack with a weapon (XPHB, "Actions" -> Attack), and
the Light property's Bonus Action attack (XPHB, "Equipment" -> Light)."""

from typing import ClassVar, NamedTuple

from dndsim.actions.base import Action
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import HANDS, Creature, hands_for
from dndsim.creatures.stats import UNARMED_STRIKE, Weapon
from dndsim.engine.attack import (
    FRESH,
    PREVENTED,
    AttackContexts,
    AttackResult,
    Trigger,
    resolve_attack,
    within_range,
    within_reach,
)
from dndsim.engine.world import World
from dndsim.rules.enums import ActionCost, WeaponProperty

__all__ = ["Attack", "LightAttack"]

_THROWN = WeaponProperty.THROWN
_LIGHT = WeaponProperty.LIGHT
_LEFT_THE_HAND = -1


class _Plan(NamedTuple):
    """How the weapon comes to hand for one attack: the hand it is held in,
    `None` to draw it; the hand to stow first with the turn's object
    interaction, `None` for none; and whether it is thrown."""

    hand: int | None
    stow: int | None
    thrown: bool


_UNARMED = _Plan(None, None, False)


class Attack(Action[AttackResult]):
    """`actor` attacks `target` with `weapon`, or with what it holds in its
    main hand.

    One attack per action; Extra Attack is not modelled. The weapon is held,
    or carried and drawn as the attack's one equip, stowing a held one first
    with the turn's object interaction when the hands are full; a Thrown
    weapon thrown leaves the hand and the next carried of its name is drawn;
    an Ammunition weapon spends a piece and needs a hand free to load when it
    is one-handed (`docs/decisions.md` § w2k). `contexts`, `trigger`, `log`
    and `world` are handed to `resolve_attack()`, which a ranged attack cannot
    be resolved without a `world` for (`docs/decisions.md` § w2f2).
    """

    __slots__ = ("contexts", "log", "target", "trigger", "weapon", "world")

    ID: ClassVar[str] = "attack"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION
    EQUIPS: ClassVar[bool] = True
    """Whether the attack may draw a weapon that is not thrown."""

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

    def _wielding(self) -> Weapon | None:
        return self.actor.weapon if self.weapon is None else self.weapon

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        weapon = self._wielding()
        return (
            weapon is not None
            and target is not actor
            and not target.dead
            and self._reaches(weapon)
            and self._plan(weapon) is not None
        )

    def _reaches(self, weapon: Weapon) -> bool:
        return within_range(self.actor, self.target, weapon)

    def _hands(self) -> tuple[int, ...]:
        """The hands whose weapon this attack may be made with."""
        return (0, 1)

    def _plan(self, weapon: Weapon) -> _Plan | None:
        """How `weapon` comes to hand, or `None` when it cannot."""
        if weapon == UNARMED_STRIKE:
            return _UNARMED
        actor = self.actor
        thrown = _THROWN in weapon.properties and (
            weapon.ranged or not within_reach(actor, self.target, weapon)
        )
        kind = weapon.ammunition
        if kind is not None and actor.ammunition(kind) < 1:
            return None
        # Loading a one-handed weapon needs the other hand (XPHB "Ammunition").
        loads = 1 if kind is not None and hands_for(weapon) == 1 else 0
        held = (actor.weapon, actor.off_hand)
        for hand in self._hands():
            holding = held[hand]
            if holding is weapon or holding == weapon:
                if loads and actor.hands_held + loads > HANDS:
                    return None
                return _Plan(hand, None, thrown)
        if actor.stowed.get(weapon, 0) < 1 or not (thrown or self.EQUIPS):
            return None
        free = HANDS - actor.hands_held
        need = hands_for(weapon) + loads
        if free >= need:
            return _Plan(None, None, thrown)
        if not actor.economy.can_interact():
            return None
        for hand in (1, 0):
            if free + hands_for(held[hand]) >= need and held[hand] is not None:
                return _Plan(None, hand, thrown)
        return None

    def _perform(self, rng: Rng) -> AttackResult:
        actor = self.actor
        weapon = self._wielding()
        if weapon is None:
            raise ValueError(f"{actor.name} has no weapon to attack with")
        plan = self._plan(weapon)
        if plan is None:
            raise ValueError(f"{actor.name} cannot bring {weapon.name} to hand")
        if plan is _UNARMED:
            return self._resolve(rng, weapon)
        # In hand before the attack is declared; nothing is spent until it is
        # not vetoed, and a vetoed attack puts the hands back as they were.
        hand = plan.hand
        stowed = None
        if plan.stow is not None:
            stowed = actor.weapon if plan.stow == 0 else actor.off_hand
            actor.stow(plan.stow)
        drawn = hand is None
        if hand is None:
            hand = actor.draw(weapon)
        result = self._resolve(rng, weapon)
        if result is PREVENTED:
            if drawn:
                actor.stow(hand)
            if stowed is not None:
                actor.draw(stowed)
            return result
        if stowed is not None:
            actor.economy.interact()
        if weapon.ammunition is not None:
            actor.spend_ammunition(weapon.ammunition)
        if self.COST is ActionCost.ACTION and weapon.has(_LIGHT):
            actor.economy.light_attack = _LEFT_THE_HAND if plan.thrown else hand
        if plan.thrown and (actor.weapon, actor.off_hand)[hand] == weapon:
            actor.let_go(hand)
            # By name: a Versatile weapon held in two hands is not equal to
            # the one-handed copies carried (`docs/decisions.md` § w2k).
            spare = next((w for w in actor.stowed if w.name == weapon.name), None)
            if spare is not None:
                actor.draw(spare)
        return result

    def _resolve(self, rng: Rng, weapon: Weapon) -> AttackResult:
        return resolve_attack(
            self.actor,
            self.target,
            rng=rng,
            weapon=weapon,
            contexts=self.contexts,
            trigger=self.trigger,
            log=self.log,
            world=self.world,
            light=self.COST is ActionCost.BONUS_ACTION,
        )


class LightAttack(Attack):
    """The Light property's extra attack: `actor` attacks `target` with
    `weapon`, a Light weapon other than the one this turn's Attack action
    attacked with, held or drawn to be thrown. Its damage takes the ability
    modifier only when negative (`docs/decisions.md` § w2k)."""

    __slots__ = ()

    ID: ClassVar[str] = "light_attack"
    COST: ClassVar[ActionCost | None] = ActionCost.BONUS_ACTION
    EQUIPS: ClassVar[bool] = False

    def __init__(
        self,
        actor: Creature,
        target: Creature,
        *,
        weapon: Weapon,
        contexts: AttackContexts = FRESH,
        trigger: Trigger | None = None,
        log: EventLog | None = None,
        world: World | None = None,
    ) -> None:
        super().__init__(
            actor,
            target,
            weapon=weapon,
            contexts=contexts,
            trigger=trigger,
            log=log,
            world=world,
        )

    def _permits(self) -> bool:
        weapon = self.weapon
        return (
            weapon is not None
            and weapon.has(_LIGHT)
            and self.actor.economy.light_attack is not None
            and super()._permits()
        )

    def _hands(self) -> tuple[int, ...]:
        used = self.actor.economy.light_attack
        return tuple(hand for hand in (0, 1) if hand != used)
