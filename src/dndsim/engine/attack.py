"""The attack resolution order.

**declare -> roll -> advantage -> hit/crit/miss -> damage -> resolved**, the
damage resolved through `engine/damage.py`.

Sources:

- a natural 20 hits and is a Critical Hit, a natural 1 misses — XPHB, "D20
  Tests" -> Attack Rolls, "Rolling 20 or 1" (`rules/constants.py`)
- a Critical Hit below 20 still has to hit — `docs/decisions.md` § w1f
- Finesse — XPHB, "Equipment" -> Weapon Properties, Finesse; taking the better
  modifier is `docs/decisions.md` § w1f
- damage on a miss is rolled without `on_damage_roll` — `docs/decisions.md`
  § w1f

Two first cuts: every attack resolves at the weapon's reach with no ranged
branch (debt #24), and the proficiency bonus is added to every attack (debt
#25).
"""

from collections.abc import Mapping
from typing import Final, NamedTuple, Protocol

from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.geometry import in_reach
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import apply_pending
from dndsim.creatures.stats import Weapon
from dndsim.engine.d20 import modifier_total
from dndsim.engine.damage import (
    FreshDamageContexts,
    resolve_damage,
    roll_damage,
)
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    Context,
    DamageBag,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
    MissContext,
)
from dndsim.hooks.definitions import (
    ON_ATTACK_RESOLVED,
    ON_ATTACK_ROLLED,
    ON_DAMAGE_ROLL,
    ON_DECLARE_ATTACK,
    ON_HIT,
    ON_HIT_TAKEN,
    ON_INCOMING_ATTACK_DECLARED,
    ON_MISS,
)
from dndsim.rules.constants import NATURAL_ONE, NATURAL_TWENTY
from dndsim.rules.enums import Ability, DamageType, Outcome, WeaponProperty

__all__ = [
    "ATTACK",
    "ATTACK_ROLL",
    "DAMAGE_ROLL",
    "FRESH",
    "PREVENTED",
    "AttackContexts",
    "AttackResult",
    "FreshContexts",
    "PooledContexts",
    "Trigger",
    "attack_ability",
    "resolve_attack",
    "within_reach",
]


class AttackResult(NamedTuple):
    """What one attack did: how it resolved, and what the defender lost.

    `outcome` is `None` for an attack an `EARLY` handler vetoed before the roll
    (`docs/decisions.md` § w1f). `damage_dealt` is what the defender took: 0 on
    a miss that dealt no damage, and 0 on a hit that landed on an immunity.
    """

    outcome: Outcome | None
    damage_dealt: int


PREVENTED: Final = AttackResult(None, 0)
"""The result of an attack a handler vetoed. Shared, because it is a constant."""

ATTACK: Final = "attack"
"""`(ATTACK, attacker, defender, weapon, outcome, damage_dealt)`, at every level.

Names rather than objects; `outcome` is `None` for an attack that was vetoed."""

ATTACK_ROLL: Final = "attack_roll"
"""`(ATTACK_ROLL, attacker, defender, natural, total, advantage, disadvantage)`,
at `LogLevel.FULL` only."""

DAMAGE_ROLL: Final = "damage_roll"
"""`(DAMAGE_ROLL, attacker, defender, critical, ((type, amount), ...))`, at
`LogLevel.FULL` only: what `roll_damage()` rolled, before any defence."""


class Trigger(Protocol):
    """The `trigger.*` namespace root, as the engine sees it: told what is being
    offered before a dispatch that carries gated handlers."""

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None: ...


class AttackContexts(Protocol):
    """Where the attack's contexts come from: allocated (`FRESH`) or rebound
    (`PooledContexts`) — DESIGN.md § Levers, `docs/decisions.md` § w0i."""

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext: ...

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext: ...

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext: ...

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext: ...

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext: ...

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext: ...

    def bag(self) -> DamageBag: ...

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext: ...

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext: ...

    def release(self) -> None: ...


class FreshContexts(FreshDamageContexts):
    """Allocate every context, every attack."""

    __slots__ = ()

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext:
        return AttackDeclaredContext(
            attacker=attacker, defender=defender, weapon=weapon, ability=ability
        )

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext:
        return AttackRollContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=natural,
            total=total,
            crit_threshold=attacker.crit_threshold,
            had_advantage=had_advantage,
            had_disadvantage=had_disadvantage,
        )

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext:
        return HitContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext:
        return MissContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext:
        return DamageRollContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=is_critical,
            damage=damage,
        )

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext:
        return AttackResolvedContext(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage_dealt=damage_dealt,
        )

    def bag(self) -> DamageBag:
        return DamageBag()

    def release(self) -> None:
        """Nothing to release: every context this source hands out is its own."""


class PooledContexts:
    """One context of each kind, rebound per attack (DESIGN.md § Levers).

    The constructor's creatures and weapon are placeholders: every attack
    rebinds every field.

    **One pool resolves one attack at a time**: `declared()` raises
    `EngineAssemblyError` on re-entry until `release()` (debt #26,
    `docs/decisions.md` § w1f).
    """

    __slots__ = (
        "_bag",
        "_declared",
        "_hit",
        "_incoming",
        "_live",
        "_missed",
        "_resolved",
        "_roll",
        "_rolled",
        "_taken",
    )

    def __init__(self, attacker: Creature, defender: Creature, weapon: Weapon) -> None:
        fresh = FreshContexts()
        self._live = False
        self._bag = DamageBag()
        self._declared = fresh.declared(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            ability=Ability.STRENGTH,
        )
        self._rolled = fresh.rolled(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=1,
            total=1,
            had_advantage=False,
            had_disadvantage=False,
        )
        self._hit = fresh.hit(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.HIT,
            damage=self._bag,
        )
        self._missed = fresh.missed(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.MISS,
            damage=self._bag,
        )
        self._roll = fresh.damage_roll(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=False,
            damage=self._bag,
        )
        self._resolved = fresh.resolved(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=Outcome.MISS,
            damage_dealt=0,
        )
        self._incoming = fresh.incoming(
            defender=defender, attacker=attacker, amounts={}
        )
        self._taken = fresh.taken(
            defender=defender,
            attacker=attacker,
            amount=0,
            damage_types=frozenset(),
        )

    def declared(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        ability: Ability,
    ) -> AttackDeclaredContext:
        if self._live:
            raise EngineAssemblyError(
                f"{attacker.name} began an attack on {defender.name} from inside "
                "another attack sharing this context pool. The outer attack's "
                "contexts are live; rebinding them here would resolve it against "
                "the inner attack's numbers. Give the nested attack its own "
                "PooledContexts (w2c) or resolve it through FRESH."
            )
        self._live = True
        ctx = self._declared
        ctx.reopen(attacker=attacker, defender=defender, weapon=weapon, ability=ability)
        return ctx

    def rolled(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        natural: int,
        total: int,
        had_advantage: bool,
        had_disadvantage: bool,
    ) -> AttackRollContext:
        ctx = self._rolled
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            natural=natural,
            total=total,
            crit_threshold=attacker.crit_threshold,
            had_advantage=had_advantage,
            had_disadvantage=had_disadvantage,
        )
        return ctx

    def hit(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> HitContext:
        ctx = self._hit
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )
        return ctx

    def missed(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage: DamageBag,
    ) -> MissContext:
        ctx = self._missed
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=damage,
        )
        return ctx

    def damage_roll(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        is_critical: bool,
        damage: DamageBag,
    ) -> DamageRollContext:
        ctx = self._roll
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=is_critical,
            damage=damage,
        )
        return ctx

    def resolved(
        self,
        *,
        attacker: Creature,
        defender: Creature,
        weapon: Weapon,
        outcome: Outcome,
        damage_dealt: int,
    ) -> AttackResolvedContext:
        ctx = self._resolved
        ctx.reopen(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage_dealt=damage_dealt,
        )
        return ctx

    def bag(self) -> DamageBag:
        """The one bag, emptied; the hit, the miss and the damage roll share it."""
        bag = self._bag
        bag.clear()
        return bag

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext:
        ctx = self._incoming
        ctx.reopen(defender=defender, attacker=attacker, amounts=amounts)
        return ctx

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext:
        ctx = self._taken
        ctx.reopen(
            defender=defender,
            attacker=attacker,
            amount=amount,
            damage_types=damage_types,
        )
        return ctx

    def release(self) -> None:
        """The attack is over; the pool may be entered again.

        `resolve_attack()` calls it on every path it returns by, and not when an
        exception escapes (`docs/decisions.md` § w1f).
        """
        self._live = False


FRESH: Final[AttackContexts] = FreshContexts()
"""The default: allocate. Stateless, so one instance serves every caller."""


def attack_ability(attacker: Creature, weapon: Weapon) -> Ability:
    """Which ability score this attack is made with.

    Strength, or Dexterity for a Finesse weapon when Dexterity is the better of
    the two (XPHB, "Equipment" -> Weapon Properties, Finesse; `docs/decisions.md`
    § w1f). There is no ranged branch: debt #24.
    """
    if weapon.has(WeaponProperty.FINESSE) and attacker.modifier(
        Ability.DEXTERITY
    ) > attacker.modifier(Ability.STRENGTH):
        return Ability.DEXTERITY
    return Ability.STRENGTH


def within_reach(attacker: Creature, defender: Creature, weapon: Weapon) -> bool:
    """Whether `defender` can be hit by `attacker` swinging `weapon`.

    The reach is the weapon being swung's, which is not always the one the
    attacker has equipped, measured between the two footprints through
    `in_reach()` (`docs/decisions.md` § w0g3, § w1d).
    """
    return in_reach(
        attacker.position,
        defender.position,
        weapon.reach_ft,
        origin_size=attacker.size,
        destination_size=defender.size,
    )


def _seal(ctx: Context, stage: str, attacker: Creature, defender: Creature) -> None:
    """Apply what the dispatch queued, raise `FeatureContractError` on a veto,
    and close `ctx` (`docs/decisions.md` § w1f).

    `attacker` and `defender` are the participants a queued effect is matched
    against. The queue and the veto are read off private slots, for the hot
    path (`docs/decisions.md` § w1j).
    """
    if ctx._pending:
        apply_pending(ctx, attacker, defender)
    if ctx._frame.veto_reason is not None:
        raise FeatureContractError(
            f"{ctx.veto_reason} vetoed at {stage}, which the attack pipeline "
            "cannot honour: the attack has been made and only its declaration "
            "can be called off. A feature that means to stop an attack gates it "
            "at on_declare_attack or on_incoming_attack_declared."
        )
    ctx.close()


def resolve_attack(
    attacker: Creature,
    defender: Creature,
    *,
    rng: Rng,
    weapon: Weapon | None = None,
    ability: Ability | None = None,
    contexts: AttackContexts = FRESH,
    trigger: Trigger | None = None,
    log: EventLog | None = None,
) -> AttackResult:
    """Resolve one attack by `attacker` against `defender`.

    `weapon` defaults to what the attacker has equipped and `ability` to what
    `attack_ability()` derives from it. `contexts` decides where the contexts
    come from; `trigger` is the APL's `trigger.*` root, told what is being
    offered immediately before each dispatch that can carry gated handlers.
    `log`, when given, is written the `ATTACK`, `ATTACK_ROLL` and `DAMAGE_ROLL`
    records above.

    Raises `ValueError` when there is no weapon or the target is out of its
    reach. Legality and cost are not asked.
    """
    if weapon is None:
        weapon = attacker.weapon
        if weapon is None:
            raise ValueError(f"{attacker.name} has no weapon to attack with")
    if not within_reach(attacker, defender, weapon):
        raise ValueError(
            f"{attacker.name} at {attacker.position} cannot reach {defender.name} "
            f"at {defender.position} with {weapon.reach_ft} ft of {weapon.name}"
        )
    if ability is None:
        ability = attack_ability(attacker, weapon)
    # Hoisted past `Creature.dispatch()`: nine dispatches per resolution
    # (`docs/decisions.md` § w0i).
    strike = attacker.dispatcher.dispatch
    parry = defender.dispatcher.dispatch

    if trigger is not None:
        trigger.bind()
    declared = contexts.declared(
        attacker=attacker, defender=defender, weapon=weapon, ability=ability
    )
    strike(ON_DECLARE_ATTACK, declared)
    parry(ON_INCOMING_ATTACK_DECLARED, declared)
    if declared._pending:
        apply_pending(declared, attacker, defender)
    if declared.vetoed:
        declared.close()
        contexts.release()
        if log is not None:
            log.write((ATTACK, attacker.name, defender.name, weapon.name, None, 0))
        return PREVENTED
    had_advantage = declared.had_advantage
    had_disadvantage = declared.had_disadvantage
    attack_bonus = modifier_total(declared.bonus, rng)
    # Rolled here rather than at the comparison below, which a natural 1 or 20
    # never reaches: a die drawn on some paths and not others would make the
    # stream depend on the outcome it is deciding.
    ac_bonus = modifier_total(declared.ac_bonus, rng)
    declared.close()

    if had_advantage:
        natural = max(rng.roll(20), rng.roll(20))
    elif had_disadvantage:
        natural = min(rng.roll(20), rng.roll(20))
    else:
        natural = rng.roll(20)
    total = (
        natural + attacker.modifier(ability) + attacker.proficiency_bonus + attack_bonus
    )

    rolled = contexts.rolled(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        natural=natural,
        total=total,
        had_advantage=had_advantage,
        had_disadvantage=had_disadvantage,
    )
    if log is not None and log.full:
        log.write(
            (
                ATTACK_ROLL,
                attacker.name,
                defender.name,
                natural,
                total,
                had_advantage,
                had_disadvantage,
            )
        )
    strike(ON_ATTACK_ROLLED, rolled)
    met_crit_threshold = rolled.is_critical
    _seal(rolled, "on_attack_rolled", attacker, defender)

    if natural == NATURAL_ONE:
        outcome = Outcome.CRITICAL_MISS
    elif natural == NATURAL_TWENTY or total >= defender.armour_class + ac_bonus:
        # The threshold decides whether a hit is a critical one. It never
        # decides whether the roll hit (`docs/decisions.md` § w1f).
        outcome = Outcome.CRITICAL_HIT if met_crit_threshold else Outcome.HIT
    else:
        outcome = Outcome.MISS

    damage_dealt = 0
    if outcome.is_hit:
        critical = outcome.is_critical
        bag = contexts.bag()
        if trigger is not None:
            trigger.bind(crit=critical)
        hit = contexts.hit(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=bag,
        )
        hit.stamp("engine.attack", None)
        # The ability modifier goes in `flat`, which a crit never doubles
        # (DESIGN.md § Structural prevention).
        bag.add(
            dice=weapon.damage,
            flat=attacker.modifier(ability),
            damage_type=weapon.damage_type,
        )
        strike(ON_HIT, hit)
        parry(ON_HIT_TAKEN, hit)
        _seal(hit, "on_hit or on_hit_taken", attacker, defender)

        damage_roll = contexts.damage_roll(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=critical,
            damage=bag,
        )
        strike(ON_DAMAGE_ROLL, damage_roll)
        _seal(damage_roll, "on_damage_roll", attacker, defender)

        amounts = roll_damage(
            bag.parts, rng=rng, base_type=weapon.damage_type, critical=critical
        )
        if log is not None and log.full:
            log.write(
                (
                    DAMAGE_ROLL,
                    attacker.name,
                    defender.name,
                    critical,
                    tuple(amounts.items()),
                )
            )
        if trigger is not None:
            # `bind()` replaces the offer, so `crit` is re-bound with the
            # estimate (`docs/decisions.md` § w1f).
            trigger.bind(crit=critical, damage_estimate=sum(amounts.values()))
        damage_dealt = resolve_damage(
            defender,
            amounts,
            rng=rng,
            attacker=attacker,
            contexts=contexts,
            critical=critical,
        )
    else:
        miss = contexts.missed(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            outcome=outcome,
            damage=contexts.bag(),
        )
        strike(ON_MISS, miss)
        _seal(miss, "on_miss", attacker, defender)
        # Damage added on a miss is resolved like a hit's, without
        # `on_damage_roll` (`docs/decisions.md` § w1f).
        parts = miss.damage.parts
        if parts:
            amounts = roll_damage(
                parts, rng=rng, base_type=weapon.damage_type, critical=False
            )
            if log is not None and log.full:
                log.write(
                    (
                        DAMAGE_ROLL,
                        attacker.name,
                        defender.name,
                        False,
                        tuple(amounts.items()),
                    )
                )
            if trigger is not None:
                trigger.bind(damage_estimate=sum(amounts.values()))
            damage_dealt = resolve_damage(
                defender, amounts, rng=rng, attacker=attacker, contexts=contexts
            )

    resolved = contexts.resolved(
        attacker=attacker,
        defender=defender,
        weapon=weapon,
        outcome=outcome,
        damage_dealt=damage_dealt,
    )
    strike(ON_ATTACK_RESOLVED, resolved)
    _seal(resolved, "on_attack_resolved", attacker, defender)
    contexts.release()
    if log is not None:
        log.write(
            (ATTACK, attacker.name, defender.name, weapon.name, outcome, damage_dealt)
        )

    return AttackResult(outcome, damage_dealt)
