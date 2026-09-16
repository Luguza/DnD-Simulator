"""Twenty-five hook sources, shaped like a level-12 martial's — the load the
performance spike measures against (ROADMAP w0i).

**These are fixtures, not content.** Nothing here is an implementation of an
XPHB feature: there is no `source_ref`, no golden test and no `expectation`, and
none of it survives into `features/` (W4). What each class reproduces is the
*cost shape* of the real thing — how many contexts fields a handler reads, how
often it applies rather than skips, whether it carries a `once_per` ledger
lookup, an economy check or an APL gate — because that shape is what the budget
is spent on. Names echo the features they stand in for so the distribution is
reviewable against a real character sheet; the bodies deliberately do not
implement the rules.

**The distribution is the pessimistic case DESIGN.md § The spike must be
pessimistic asks for**: 25 attached sources, 15 of them on `on_hit`, "which is
what a level-12 Barbarian actually looks like", and the remaining ten spread
over the other hot hooks so that no hook in the attack sequence is measured
empty.

Two properties of the mix matter more than the count:

- **Most handlers skip, a few apply.** A real martial's `on_hit` list is mostly
  riders whose conditions are not met on any given swing — the damage that
  actually lands comes from three or four of them. A benchmark where every
  handler applies would measure `DamageBag.add()` fifteen times and call it
  dispatch; one where none applies would never pay for `once_per` or the
  economy. Both are wrong in the flattering direction.
- **Every escape valve in the dispatcher is exercised.** Three handlers carry a
  `once_per` budget (two `TURN`, one `OWN_TURN`), one costs a `REACTION`, and
  three are gated on the APL — so the ledger lookup, the economy query and the
  gate call are all in the measurement rather than in a comment about future
  work. Each of the six is reached by a handler that *applies* at least once,
  not merely by one that declares the valve: `tests/bench/test_spike.py` checks
  that through the dispatcher's own counter, because a gate that always answers
  no buys the cheap half of the valve and none of what it guards.

Sources: DESIGN.md § The spike must be pessimistic, § The uniform handler shape;
ROADMAP w0i.
"""

from dndsim.core.timeline import Reset
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    Combatant,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
    TurnContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import ActionCost, DamageType, Outcome, WeaponProperty
from dndsim.tools.spike_apl import SpikeState

__all__ = ["ON_HIT_SOURCES", "SOURCES", "Fixture"]

"""The one weapon `w0g2` defines, and the stand-in for a Heavy property."""


class Fixture(HookSource):
    """The shared shape: a counter, and no handlers of its own.

    No `@hook` on this class deliberately. `HookSource.__init_subclass__`
    refuses an override that dropped its decorator, so a base carrying a
    handler would make every subclass below re-declare it; a base carrying only
    state costs nothing and keeps the twenty-five declarations independent.
    """

    __slots__ = ("state", "tally")

    def __init__(self, owner: Combatant, state: SpikeState) -> None:
        super().__init__(owner)
        self.state = state
        self.tally = 0


# --- on_hit: fifteen riders, the way a level-12 martial carries them ---------


class HeavyWeaponRider(Fixture):
    """Applies on every hit with a Heavy weapon: the always-on rider.

    A real Great Weapon Master asks the weapon whether it is Heavy, and until
    `w1f` widened the `Weapon` protocol it could not: the spike matched the
    weapon's name instead and said so here, which is the gap that became debt #3
    and one this fixture found rather than invented. It asks properly now, at
    the cost the note promised the identity check was standing in for.
    """

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
    the state flag the APL set on turn one is up."""

    @hook("on_hit", once_per=Reset.TURN)
    def sneak(self, ctx: HitContext) -> R:
        if not self.state.raging:
            return R.SKIPPED
        ctx.damage.add(dice="6d6")
        return R.APPLIED


class DivineSmite(Fixture):
    """Gated on the APL: `trigger.crit or (self.slots.highest >= 3 and ...)`."""

    @hook("on_hit", gate="paladin.divine_smite")
    def smite(self, ctx: HitContext) -> R:
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
        if not self.state.raging:
            return R.SKIPPED
        ctx.damage.add(dice="1d6")
        return R.APPLIED


class GrazeMastery(Fixture):
    """Attaches to `on_hit` and never fires there — its moment is `on_miss`.

    The commonest skip in the project: a handler on the busiest hook whose
    condition is about a different outcome entirely.
    """

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
    """Applies against a target already below half its hit points — the
    sometimes-true rider.

    It reads the defender's pool, which is what a real one does and what this
    fixture could not do until `w1a` widened the `Combatant` protocol: through
    W0 the condition was asked of the damage bag instead, with a paragraph here
    saying why. What has not changed is the property that paragraph was
    defending — the condition is **sometimes true**, because a rider whose
    condition can never hold is the exact thing the no-op detector exists to
    find (DESIGN.md § No-op detector), and one hiding in the benchmark would
    quietly make the measurement cheaper than the thing it stands for.
    """

    @hook("on_hit")
    def bloodied(self, ctx: HitContext) -> R:
        defender = ctx.defender
        # Hit points only. A creature is Bloodied while it has half its hit
        # points or fewer, and temporary hit points are not hit points — they
        # are a buffer in front of them (XPHB, Rules Glossary). Adding the two
        # would read a creature at 30 of 100 behind 21 temporary as unhurt.
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
    # riders contributed, so it is ordered after them by band rather than by a
    # fragile `order=` number.
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
"""The fifteen ROADMAP w0i asks for on the busiest hook, in attachment order."""


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

    Its condition is about the roll rather than about advantage, so that it
    applies on roughly half of all attacks: this build grants itself advantage
    on every swing, and a fixture keyed on `not ctx.had_advantage` would be a
    handler that can never fire in the one configuration it is measured in.
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
        if not self.state.raging:
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
    """`on_incoming_damage`: gated on the APL *and* costing a reaction.

    The only handler that pays for two of the dispatcher's escape valves in the
    same call — economy then gate, the expensive two of the three DESIGN.md
    § The uniform handler shape says are asked "in ascending order of cost". It
    is also the only one whose valve ever *denies*: it applies roughly twice
    every five encounters, and the reaction it spends is then gone until the
    hero's turn comes back round, so `can_spend()` answers False for the
    enemies still to swing that round. No `once_per` — the reaction is the
    budget, which is why the gate is what has to be reachable.
    """

    @hook("on_incoming_damage", costs=ActionCost.REACTION, gate="rogue.uncanny_dodge")
    def dodge(self, ctx: IncomingDamageContext) -> R:
        ctx.halve("uncanny dodge")
        return R.APPLIED


class RageResistance(Fixture):
    """`on_incoming_damage`: three resistances recorded, never applied."""

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        if not self.state.raging:
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
"""All twenty-five, in attachment order — which is the last tiebreak in dispatch
order (`hooks/dispatch.py`), so this tuple is what makes the spike's ordering
reproducible from run to run."""
