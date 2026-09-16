"""The attack resolution order, in the one file it is ever written in.

**declare -> roll -> advantage -> hit/crit/miss -> damage -> resolved**, with
the three damage hooks of `engine/damage.py` riding inside the hit branch and
the nine dispatches falling exactly where `hooks/definitions.py` describes them.
`w0h` hand-sequenced this in `tools/skeleton.py` so that `w0i` had something
integrated to measure; that copy is retired here (debt #6), because a second
attack order is a benchmark defending a sequence the project does not ship.

Where each step comes from:

- a natural 20 hits and is a Critical Hit, a natural 1 misses — XPHB, "D20
  Tests" -> Attack Rolls, "Rolling 20 or 1" (`rules/constants.py`)
- a Critical Hit below 20 still has to hit, which is what a Champion's lowered
  threshold does and does not buy — argued in `docs/decisions.md` § w1f
- Finesse chooses the ability, and the pipeline chooses the better modifier —
  XPHB, "Equipment" -> Weapon Properties, Finesse; the choice is argued there
- damage on a miss is rolled without `on_damage_roll` — argued there too, from
  the Graze mastery's own sentence

Not here: whether the attack is legal or affordable (`w2b`'s Command and the
action economy), who is offered a reaction to it (`w2c`), the damage steps
(`engine/damage.py`), the event log (`w1i`). Two first cuts live here and are
named so that nobody has to rediscover them: every attack resolves at the
weapon's reach with no ranged branch, because no weapon carries a range until
`w2a` imports one (debt #24), and the proficiency bonus is added to every
attack, because nothing can yet say a creature is not proficient with what it
holds (debt #25).
"""

from collections.abc import Mapping
from typing import Final, NamedTuple, Protocol

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import in_reach
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import Weapon
from dndsim.engine.damage import (
    FreshDamageContexts,
    refuse_pending,
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
    ModifierBag,
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
    — no d20 was thrown, so there is no `Outcome` that describes it, and
    `Outcome` is scoped to attack rolls precisely so that no member has to mean
    two things (`rules/enums.py`). `damage_dealt` is what `engine/damage.py`
    reports it actually took, which is 0 on a miss the Graze mastery did not
    turn into damage, and 0 on a hit that landed on an immunity.

    A `NamedTuple` rather than a frozen dataclass: this is allocated on every
    resolution, and a two-field tuple is built by the interpreter where a frozen
    dataclass runs `object.__setattr__` twice (`docs/decisions.md` § w0i on what
    a per-resolution allocation is worth).
    """

    outcome: Outcome | None
    damage_dealt: int


PREVENTED: Final = AttackResult(None, 0)
"""The result of an attack a handler vetoed. Shared, because it is a constant."""


class Trigger(Protocol):
    """The `trigger.*` namespace root, as the engine sees it.

    A `Protocol` rather than an import because `apl/namespace.py` (w2e) is what
    defines it and does not exist: the attack sequence's whole knowledge of the
    APL is that something wants to be told what it is being offered before a
    dispatch that carries gated handlers. `None` is the ordinary case — nobody
    is asking.
    """

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None: ...


class AttackContexts(Protocol):
    """Where the attack's contexts come from.

    DESIGN.md § Levers asks for "contexts reset and reused per attack rather
    than allocated, which matters at 4M resolutions", and `w0i` measured that it
    does. Making the source a **Strategy** rather than an `if pool is None` at
    eight call sites keeps the sequence below readable as one thing, and keeps
    the two answers — allocate, or rebind — honest about what each costs.
    """

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
    """Allocate every context, every attack. The obvious answer, and the
    baseline `w0i` measures the pooled one against."""

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

    Built against whichever pair of creatures is convenient and rebound to the
    real ones on every attack, because `reopen()` demands every field a context
    does not default — nothing of the previous attack survives by omission
    (`hooks/contexts.py`).

    **One pool resolves one attack at a time, and it says so out loud.** Every
    context in it is live while a hook is dispatching, so an attack begun from
    inside a handler of another attack would hand the inner one the outer one's
    contexts and overwrite the event in flight. `w2c` is the wave that can
    produce that — a reaction is an attack made during someone else's — and its
    answer is a second pool, not a deeper one: `declared()` refuses re-entry
    until `release()` says the attack is over.
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
        """The one bag, emptied. It is handed to the three contexts that carry
        one — the hit, the miss and the damage roll — which is why `reopen()`
        rebinds a handed bag rather than clearing it."""
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

        Called by `resolve_attack()` on every path it returns by. An exception
        escaping the sequence deliberately does not release it: the contexts are
        then in whatever state the failure left them, and a pool that reopened
        over that would turn one loud failure into a quiet wrong fight.
        """
        self._live = False


FRESH: Final[AttackContexts] = FreshContexts()
"""The default: allocate. Stateless, so one instance serves every caller."""


def attack_ability(attacker: Creature, weapon: Weapon) -> Ability:
    """Which ability score this attack is made with.

    Strength, or Dexterity for a Finesse weapon when Dexterity is the better of
    the two (XPHB, "Equipment" -> Weapon Properties, Finesse). Taking the better
    modifier is a choice the rules leave to the wielder and `docs/decisions.md`
    § w1f argues; `resolve_attack()` takes an explicit `ability` for a caller
    that has a reason to make the other one.

    **Incomplete, and loudly so**: a ranged weapon attacks with Dexterity and
    there is no branch for one here, because no weapon carries a range to
    recognise it by. `w2a` widens this function and the `Weapon` protocol
    together — debt #24, not weapon data alone.
    """
    if weapon.has(WeaponProperty.FINESSE) and attacker.modifier(
        Ability.DEXTERITY
    ) > attacker.modifier(Ability.STRENGTH):
        return Ability.DEXTERITY
    return Ability.STRENGTH


def within_reach(attacker: Creature, defender: Creature, weapon: Weapon) -> bool:
    """Whether `defender` can be hit by `attacker` swinging `weapon`.

    The reach is the weapon being swung's, which is not always the one the
    attacker has equipped, and it is measured from the attacker's square through
    `in_reach()` — the one query `core/geometry.py` owns, so that the pipeline,
    the opportunity-attack trigger and the APL's `enemies.in_reach` selector
    cannot come to different answers (`docs/decisions.md` § w0g3). `w1d` is where
    a Large creature's square stops being one cell, and nothing here changes when
    it does.
    """
    return in_reach(attacker.position, defender.position, weapon.reach_ft)


def _modifier_total(bag: ModifierBag, rng: Rng) -> int:
    """A bag of modifiers as one number, Bless's `1d4` included.

    The dice come from the attack's own generator, which is the requirement
    `mitigate()` states for a reduction's dice and the same argument: a second
    stream reproduces a different fight from the same archived seed with no
    error anywhere (`engine/damage.py`).
    """
    total = 0
    for modifier in bag:
        total += modifier.flat
        if modifier.dice is not None:
            total += modifier.dice.roll(rng)
    return total


def _seal(ctx: Context, stage: str) -> None:
    """End the moment, refusing the two things a dispatch can leave behind that
    nobody would otherwise notice.

    A queued effect is refused for as long as nothing drains one (`w1c`, debt
    #5). A veto is refused everywhere but the declaration: by every later stage
    the attack has been made, `Outcome` has no member for one that was withdrawn
    (`rules/enums.py`), and continuing past `ctx.veto()` would make the design's
    one Chain of Responsibility element a silent no-op at five of the seven
    attack hooks.

    Both are read off the context's private state rather than through
    `pending_effects`, which builds a tuple, and `vetoed`, which is a property
    call over the same slot — the two reasons `hooks/dispatch.py` gives for
    reaching into the frame itself. That, and closing here rather than at the
    call site, is what keeps the two refusals free: this is the `ctx.close()`
    the sequence made anyway, with two slot reads in front of it.
    """
    if ctx._pending:
        refuse_pending(ctx)
    if ctx._frame.veto_reason is not None:
        raise EngineAssemblyError(
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
) -> AttackResult:
    """Resolve one attack by `attacker` against `defender`.

    `weapon` defaults to what the attacker has equipped and `ability` to what
    `attack_ability()` derives from it. `contexts` decides where the contexts
    come from; `trigger` is the APL's `trigger.*` root, told what is being
    offered immediately before each dispatch that can carry gated handlers.

    Legality is not asked here — whether this creature may attack at all, and
    what it costs, is `w2b`'s Command and the action economy. What is refused is
    an attack that cannot be described: no weapon, or a target out of the
    weapon's reach.
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
    # Both dispatch tables, once. `Creature.dispatch()` is the API every other
    # caller uses; the attack sequence runs it nine times per resolution and
    # would otherwise walk the attribute chain to it every time, which `w0i`
    # measured at about a twentieth of the per-attack budget.
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
        refuse_pending(declared)
    if declared.vetoed:
        declared.close()
        contexts.release()
        return PREVENTED
    had_advantage = declared.had_advantage
    had_disadvantage = declared.had_disadvantage
    attack_bonus = _modifier_total(declared.bonus, rng)
    # Rolled here rather than at the comparison below, which a natural 1 or 20
    # never reaches: a die drawn on some paths and not others would make the
    # stream depend on the outcome it is deciding.
    ac_bonus = _modifier_total(declared.ac_bonus, rng)
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
    strike(ON_ATTACK_ROLLED, rolled)
    met_crit_threshold = rolled.is_critical
    _seal(rolled, "on_attack_rolled")

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
        # Two contributions rather than one number: `dice` doubles on a critical
        # hit and `flat` never does, so the ability modifier has to arrive as
        # `flat` (DESIGN.md § Structural prevention).
        bag.add(
            dice=weapon.damage,
            flat=attacker.modifier(ability),
            damage_type=weapon.damage_type,
        )
        strike(ON_HIT, hit)
        parry(ON_HIT_TAKEN, hit)
        _seal(hit, "on_hit or on_hit_taken")

        damage_roll = contexts.damage_roll(
            attacker=attacker,
            defender=defender,
            weapon=weapon,
            is_critical=critical,
            damage=bag,
        )
        strike(ON_DAMAGE_ROLL, damage_roll)
        _seal(damage_roll, "on_damage_roll")

        amounts = roll_damage(
            bag.parts, rng=rng, base_type=weapon.damage_type, critical=critical
        )
        if trigger is not None:
            # What the victim's reactions are being offered: how much is on the
            # table if nobody does anything about it (DESIGN.md § APL,
            # `trigger.damage_estimate`). `crit` is re-bound with it because
            # `bind()` replaces the offer and both facts are true of this one
            # attack — a reaction asked about a critical hit's damage must not
            # be told the hit was ordinary.
            trigger.bind(crit=critical, damage_estimate=sum(amounts.values()))
        damage_dealt = resolve_damage(
            defender, amounts, rng=rng, attacker=attacker, contexts=contexts
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
        _seal(miss, "on_miss")
        # A miss is not always nothing: the Graze mastery deals the ability
        # modifier on one, and it is resolved through the same defences a hit
        # meets. `on_damage_roll` does not fire for it (`docs/decisions.md`
        # § w1f).
        parts = miss.damage.parts
        if parts:
            amounts = roll_damage(
                parts, rng=rng, base_type=weapon.damage_type, critical=False
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
    _seal(resolved, "on_attack_resolved")
    contexts.release()

    return AttackResult(outcome, damage_dealt)
