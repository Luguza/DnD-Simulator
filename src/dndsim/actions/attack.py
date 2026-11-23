"""The Attack action's one attack, and the Light property's extra attack
(XPHB, "Actions" -> Attack; "Equipment" -> Weapon Properties, Ammunition,
Light, Thrown)."""

from typing import ClassVar, NamedTuple

from dndsim.actions.base import Action
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import HANDS, Creature, hands_for
from dndsim.creatures.stats import UNARMED_STRIKE, Weapon
from dndsim.engine.attack import (
    FRESH,
    AttackContexts,
    AttackResult,
    Trigger,
    resolve_attack,
    thrown_or_shot,
    within_range,
)
from dndsim.engine.world import World
from dndsim.rules.enums import ActionCost, WeaponProperty

__all__ = ["Attack", "LightAttack"]


class _Readied(NamedTuple):
    """How a weapon comes to hand for one attack: the `hand` holding it, else
    whether it is drawn from what is carried, after `stow` empties a hand with
    the turn's object interaction."""

    weapon: Weapon
    hand: int | None
    draw: bool = False
    stow: int | None = None


class Attack(Action[AttackResult]):
    """`actor` attacks `target` with `weapon`, or with what it wields.

    One attack per action; Extra Attack is not modelled. A named weapon is
    one held, or one carried that the attack's equip draws, after stowing a
    held one with the turn's object interaction when no hand is free. An
    Ammunition weapon spends a piece and needs a free hand to load unless it
    is Two-Handed; a thrown weapon leaves the hand, and the next carried of
    its name is drawn (`docs/decisions.md` § w2b, § w2k). `contexts`,
    `trigger`, `log` and `world` are handed to `resolve_attack()`, which a
    ranged attack cannot be resolved without a `world` for
    (`docs/decisions.md` § w2f2).
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

    @property
    def weapon_name(self) -> str | None:
        """The name of the weapon the attack would be made with now."""
        weapon = self.actor.weapon if self.weapon is None else self.weapon
        return None if weapon is None else weapon.name

    def _ready(self) -> _Readied | None:
        """How the weapon comes to hand, or `None` when it cannot."""
        actor, named = self.actor, self.weapon
        if named is None:
            held = actor.weapon
            return None if held is None else _Readied(held, 0)
        hand = self._hand(named.name)
        if hand is not None:
            held = actor.held(hand)
            assert held is not None
            return _Readied(held, hand)
        if named is UNARMED_STRIKE:
            return _Readied(named, None)
        if not actor.counts_carried:
            # A creature whose kit is not counted has every weapon it names
            # at hand, as a stat block's attacks are, but one it dropped
            # (`docs/decisions.md` § w2b, § w2k).
            if any(lying is named for lying, _ in actor.dropped):
                return None
            return _Readied(named, None)
        if not self._draws(named) or actor.carried(named.name) < 1:
            return None
        drawn = next(w for w in actor.stowed if w.name == named.name)
        need = hands_for(drawn)
        free = HANDS - actor.hands_held
        if free >= need:
            return _Readied(drawn, None, True)
        if not actor.economy.can_interact():
            return None
        for stow in (0, 1):
            held = actor.held(stow)
            if held is not None and free + hands_for(held) >= need:
                return _Readied(drawn, None, True, stow)
        return None

    def _hand(self, name: str) -> int | None:
        """The hand holding the weapon called `name` this attack uses."""
        return self.actor.holding(name)

    def _draws(self, weapon: Weapon) -> bool:
        """Whether a carried `weapon` may be drawn for this attack: the Attack
        action's equip."""
        return True

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        if target is actor or target.dead:
            return False
        readied = self._ready()
        if readied is None:
            return False
        weapon = readied.weapon
        if not self._reaches(weapon):
            return False
        if not weapon.has(WeaponProperty.AMMUNITION):
            return True
        piece = weapon.ammunition
        if piece is not None and not actor.has_ammunition(piece):
            return False
        if weapon.has(WeaponProperty.TWO_HANDED):
            return True
        # Loading a one-handed weapon needs a hand free once it is in hand.
        held = actor.hands_held
        if readied.stow is not None:
            stowed = actor.held(readied.stow)
            assert stowed is not None
            held -= hands_for(stowed)
        if readied.draw:
            held += hands_for(weapon)
        return held < HANDS

    def _reaches(self, weapon: Weapon) -> bool:
        return within_range(self.actor, self.target, weapon)

    def _perform(self, rng: Rng) -> AttackResult:
        actor, target = self.actor, self.target
        readied = self._ready()
        assert readied is not None
        weapon, hand = readied.weapon, readied.hand
        if readied.stow is not None:
            actor.economy.interact()
            actor.stow(readied.stow)
        if readied.draw:
            hand = actor.draw(weapon.name)
        thrown = weapon.has(WeaponProperty.THROWN) and thrown_or_shot(
            actor, target, weapon
        )
        piece = weapon.ammunition
        if piece is not None and weapon.has(WeaponProperty.AMMUNITION):
            actor.spend_ammunition(piece)
        result = self._resolve(weapon, rng)
        if hand is not None and actor.held(hand) is weapon:
            if self._records_light(weapon):
                actor.economy.light_hand = hand
            if thrown and actor.counts_carried:
                actor.let_go(hand)
                if actor.carried(weapon.name):
                    # XPHB "Thrown": the weapon is drawn as part of the attack.
                    actor.draw(weapon.name)
        return result

    def _records_light(self, weapon: Weapon) -> bool:
        return type(self) is Attack and weapon.has(WeaponProperty.LIGHT)

    def _resolve(self, weapon: Weapon, rng: Rng) -> AttackResult:
        return resolve_attack(
            self.actor,
            self.target,
            rng=rng,
            weapon=weapon,
            contexts=self.contexts,
            trigger=self.trigger,
            log=self.log,
            world=self.world,
        )


class LightAttack(Attack):
    """The Light property's extra attack: `actor` attacks `target` with
    `weapon`, a Light weapon other than the one this turn's Attack action
    attacked with, adding its ability modifier to the damage only when
    negative. Offered only after that attack; a carried Thrown weapon is
    drawn as part of a throw, never by an equip (`docs/decisions.md` § w2k).
    """

    __slots__ = ()

    ID: ClassVar[str] = "light_attack"
    COST: ClassVar[ActionCost | None] = ActionCost.BONUS_ACTION

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

    def _draws(self, weapon: Weapon) -> bool:
        return weapon.has(WeaponProperty.THROWN) and thrown_or_shot(
            self.actor, self.target, weapon
        )

    def _hand(self, name: str) -> int | None:
        first = self.actor.economy.light_hand
        for hand in (0, 1):
            held = self.actor.held(hand)
            if hand != first and held is not None and held.name == name:
                return hand
        return None

    def _permits(self) -> bool:
        weapon = self.weapon
        if self.actor.economy.light_hand is None or weapon is None:
            return False
        return weapon.has(WeaponProperty.LIGHT) and super()._permits()

    def _resolve(self, weapon: Weapon, rng: Rng) -> AttackResult:
        return resolve_attack(
            self.actor,
            self.target,
            rng=rng,
            weapon=weapon,
            contexts=self.contexts,
            trigger=self.trigger,
            log=self.log,
            world=self.world,
            positive_modifier=False,
        )
