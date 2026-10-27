"""Twenty-five hook sources, shaped like a level-12 martial's — the load the
performance spike measures against (ROADMAP w0i).

Fixtures, not content: each reproduces the *cost shape* of the feature it is
named for — the fields it reads, how often it applies rather than skips, and
whether it carries a `once_per` budget, a reaction cost or an APL gate — and
none implements the rules. Fifteen are on `on_hit`, the rest spread over the
other hot hooks. `docs/decisions.md` § w0i.

Rage is the one that is not a fixture: `SpikeRage` is a real `Effect`, applied
by the APL, refreshed by every swing and ended by the sweep, and Divine Smite
spends a real spell slot (`docs/decisions.md` § w1k).

DESIGN.md § The spike must be pessimistic.
"""

from typing import Final

from dndsim.core.timeline import Reset
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.creatures.resources import ALL, Recovery, ResourceSpec
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
    TurnContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import (
    ActionCost,
    DamageType,
    Outcome,
    RecoveryTrigger,
    WeaponProperty,
)

__all__ = [
    "ON_HIT_SOURCES",
    "RAGE",
    "SOURCES",
    "Fixture",
    "SpikeRage",
    "SpikeState",
    "raging",
]

RAGE: Final = ResourceSpec(
    key="rage",
    label="Rage",
    maximum=5,
    recovery=(
        Recovery(RecoveryTrigger.SHORT_REST, 1),
        Recovery(RecoveryTrigger.LONG_REST, ALL),
    ),
)
"""A level-12 Barbarian's uses, in DESIGN.md § Resources' shape."""


class SpikeRage(Effect):
    """Rage as the State pattern has it: attached while it lasts, and kept up
    by attacking (DESIGN.md § Durations)."""

    @hook("on_declare_attack")
    def keep_up(self, ctx: AttackDeclaredContext) -> R:
        self.refresh()
        return R.APPLIED


def raging(creature: Creature) -> bool:
    """Whether `creature` holds a `SpikeRage`: the store, asked."""
    return any(type(effect) is SpikeRage for effect in creature.effects)


class SpikeState:
    """The one stand-in the fixtures still share: whose name Hunter's Mark
    holds."""

    __slots__ = ("marked",)

    def __init__(self) -> None:
        self.marked: str | None = None


class Fixture(HookSource):
    """The shared shape: a counter, and no handlers of its own.

    Trap: a handler here would have to be re-declared, decorator and all, by
    every subclass that overrides it (`HookSource.__init_subclass__`).
    """

    __slots__ = ("creature", "state", "tally")

    def __init__(self, owner: Creature, state: SpikeState) -> None:
        super().__init__(owner)
        # The owner as a `Creature`, for the store and the pools a
        # `Combatant` does not carry.
        self.creature = owner
        self.state = state
        self.tally = 0


# --- on_hit: fifteen riders, the way a level-12 martial carries them ---------


class HeavyWeaponRider(Fixture):
    """Applies on every hit with a Heavy weapon: the always-on rider."""

    @hook("on_hit")
    def rider(self, ctx: HitContext) -> R:
        weapon = ctx.weapon
        if weapon is None or not weapon.has(WeaponProperty.HEAVY):
            return R.SKIPPED
        ctx.damage.add(flat=3)
        return R.APPLIED


class BrutalStrike(Fixture):
    """Once per turn, extra dice. The `Reset.TURN` ledger lookup."""

    @hook("on_hit", once_per=Reset.TURN)
    def strike(self, ctx: HitContext) -> R:
        ctx.damage.add(dice="1d10")
        return R.APPLIED


class PoisonedBlade(Fixture):
    """Once on the owner's turn: the `Reset.OWN_TURN` ledger lookup."""

    @hook("on_hit", once_per=Reset.OWN_TURN)
    def poison(self, ctx: HitContext) -> R:
        ctx.damage.add(dice="2d4", damage_type=DamageType.POISON)
        self.tally += 1
        return R.APPLIED


class SneakAttack(Fixture):
    """Conditional dice: the biggest rider on the list, and it lands only while
    the Rage the APL applied on turn one is attached."""

    @hook("on_hit", once_per=Reset.TURN)
    def sneak(self, ctx: HitContext) -> R:
        if not raging(self.creature):
            return R.SKIPPED
        ctx.damage.add(dice="6d6")
        return R.APPLIED


class DivineSmite(Fixture):
    """Gated on the APL: `trigger.crit or (self.slots.highest >= 3 and ...)`,
    and paid for with the lowest slot left (`docs/decisions.md` § w1k)."""

    @hook("on_hit", gate="paladin.divine_smite")
    def smite(self, ctx: HitContext) -> R:
        self.creature.resources.spells.spend(1, allow_upcast=True)
        ctx.damage.add(dice="3d8", damage_type=DamageType.RADIANT)
        return R.APPLIED


class ElementalWeapon(Fixture):
    """A small typed rider that always applies — the second damage type."""

    @hook("on_hit")
    def burn(self, ctx: HitContext) -> R:
        ctx.damage.add(dice="1d4", damage_type=DamageType.FIRE)
        return R.APPLIED


class FrenzyDice(Fixture):
    """Applies only while Rage is up: the state-dependent rider."""

    @hook("on_hit")
    def frenzy(self, ctx: HitContext) -> R:
        if not raging(self.creature):
            return R.SKIPPED
        ctx.damage.add(dice="1d6")
        return R.APPLIED


class GrazeMastery(Fixture):
    """Attaches to `on_hit` and always skips there."""

    @hook("on_hit")
    def graze(self, ctx: HitContext) -> R:
        return R.SKIPPED


class VexMastery(Fixture):
    """Charges on a hit and is spent by the next attack roll."""

    @hook("on_hit")
    def vex(self, ctx: HitContext) -> R:
        self.tally += 1
        return R.APPLIED


class ToppleMastery(Fixture):
    """Skips unless the hit was hard enough to be worth the save."""

    @hook("on_hit")
    def topple(self, ctx: HitContext) -> R:
        if len(ctx.damage) < 4:
            return R.SKIPPED
        self.tally += 1
        return R.APPLIED


class HuntersMark(Fixture):
    """Reads the defender's identity and usually skips: the marked-target rider."""

    @hook("on_hit")
    def mark(self, ctx: HitContext) -> R:
        if ctx.defender.name != self.state.marked:
            return R.SKIPPED
        ctx.damage.add(dice="1d6")
        return R.APPLIED


class SavageAttacker(Fixture):
    """Skips on anything but a critical hit."""

    @hook("on_hit")
    def savage(self, ctx: HitContext) -> R:
        if not ctx.is_critical:
            return R.SKIPPED
        ctx.damage.add(dice="1d6")
        return R.APPLIED


class BloodiedBonus(Fixture):
    """Applies against a Bloodied target. Its condition must stay sometimes
    true (`docs/decisions.md` § w1a)."""

    @hook("on_hit")
    def bloodied(self, ctx: HitContext) -> R:
        defender = ctx.defender
        # Hit points only, never temporary ones (XPHB, Rules Glossary ->
        # Bloodied).
        if defender.hp * 2 > defender.max_hp:
            return R.SKIPPED
        ctx.damage.add(flat=2)
        return R.APPLIED


class RelentlessTally(Fixture):
    """Counts and skips: the observational rider that never adds damage."""

    @hook("on_hit")
    def count(self, ctx: HitContext) -> R:
        self.tally += 1
        return R.SKIPPED


class CleaveWatch(Fixture):
    """A `LATE` handler, so the phase bands are not all one value."""

    # PHASE: a follow-up attack must see the whole of the damage the ordinary
    # riders contributed.
    @hook("on_hit", phase=Phase.LATE)
    def cleave(self, ctx: HitContext) -> R:
        if ctx.outcome is not Outcome.CRITICAL_HIT:
            return R.SKIPPED
        self.tally += 1
        return R.APPLIED


ON_HIT_SOURCES: tuple[type[Fixture], ...] = (
    HeavyWeaponRider,
    BrutalStrike,
    PoisonedBlade,
    SneakAttack,
    DivineSmite,
    ElementalWeapon,
    FrenzyDice,
    GrazeMastery,
    VexMastery,
    ToppleMastery,
    HuntersMark,
    SavageAttacker,
    BloodiedBonus,
    RelentlessTally,
    CleaveWatch,
)
"""The fifteen on the busiest hook, in attachment order."""


# --- the other ten, spread across the rest of the attack sequence -----------


class RecklessAttack(Fixture):
    """`on_declare_attack`: grants advantage, which the roll then pays for."""

    @hook("on_declare_attack", gate="barbarian.reckless_attack")
    def reckless(self, ctx: AttackDeclaredContext) -> R:
        ctx.grant_advantage("reckless attack")
        return R.APPLIED


class FightingStyle(Fixture):
    """`on_declare_attack`: a flat bonus to the roll, every swing."""

    @hook("on_declare_attack")
    def style(self, ctx: AttackDeclaredContext) -> R:
        ctx.bonus.add(flat=2)
        return R.APPLIED


class VexSpender(Fixture):
    """`on_attack_rolled`: reads the roll and spends a charge on a good one.

    Trap: keyed on the roll, not on advantage, which this build has on every
    swing.
    """

    @hook("on_attack_rolled")
    def spend(self, ctx: AttackRollContext) -> R:
        if ctx.natural < 10:
            return R.SKIPPED
        self.tally += 1
        return R.APPLIED


class RageDamage(Fixture):
    """`on_damage_roll`: the flat bonus that must never double on a crit."""

    @hook("on_damage_roll")
    def rage(self, ctx: DamageRollContext) -> R:
        if not raging(self.creature):
            return R.SKIPPED
        ctx.damage.add(flat=3)
        return R.APPLIED


class AttackTally(Fixture):
    """`on_attack_resolved`: the counter every build ends up carrying."""

    @hook("on_attack_resolved")
    def resolved(self, ctx: AttackResolvedContext) -> R:
        self.tally += ctx.damage_dealt
        return R.SKIPPED


class RetaliationWatch(Fixture):
    """`on_hit_taken`: the victim's side of the paired hook."""

    @hook("on_hit_taken")
    def taken(self, ctx: HitContext) -> R:
        if ctx.outcome is not Outcome.CRITICAL_HIT:
            return R.SKIPPED
        self.tally += 1
        return R.APPLIED


class UncannyDodge(Fixture):
    """`on_incoming_damage`: gated on the APL *and* costing a reaction — the
    only handler whose valve ever denies (`docs/decisions.md` § w0i)."""

    @hook("on_incoming_damage", costs=ActionCost.REACTION, gate="rogue.uncanny_dodge")
    def dodge(self, ctx: IncomingDamageContext) -> R:
        ctx.halve("uncanny dodge")
        return R.APPLIED


class RageResistance(Fixture):
    """`on_incoming_damage`: three resistances recorded, never applied."""

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        if not raging(self.creature):
            return R.SKIPPED
        ctx.add_resistance(DamageType.SLASHING)
        ctx.add_resistance(DamageType.PIERCING)
        ctx.add_resistance(DamageType.BLUDGEONING)
        return R.APPLIED


class BloodiedWatch(Fixture):
    """`on_damage_taken`: reads what landed and records nothing."""

    @hook("on_damage_taken")
    def watch(self, ctx: DamageTakenContext) -> R:
        self.tally += ctx.amount
        return R.SKIPPED


class TurnUpkeep(Fixture):
    """`on_turn_start`: the per-turn cost every build pays before it acts."""

    @hook("on_turn_start")
    def upkeep(self, ctx: TurnContext) -> R:
        self.tally += 1
        return R.SKIPPED


SOURCES: tuple[type[Fixture], ...] = (
    *ON_HIT_SOURCES,
    RecklessAttack,
    FightingStyle,
    VexSpender,
    RageDamage,
    AttackTally,
    RetaliationWatch,
    UncannyDodge,
    RageResistance,
    BloodiedWatch,
    TurnUpkeep,
)
"""All twenty-five, in attachment order, which is the last tiebreak in dispatch
order."""
