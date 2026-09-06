"""The w0g2 damage contract: the crit rule, and the one order damage resolves in.

**immunity -> vulnerability -> resistance -> flat reductions -> halvings ->
temporary hit points -> hit points.** DESIGN.md fixes that order and fixes the
file it is written in; this is the file that says what it *means* in integers.
Every case below carries its arithmetic in a comment, because the point of a
contract test on an ordering is that the number and the reasoning are read
together — a bare `== 6` proves nothing about which order produced it.

Four properties carry the file:

- **Dice double on a critical hit and flat bonuses never do.** Rolled through a
  scripted generator so that the assertion is an exact integer and so that a
  crit is seen to *re-roll* a doubled pool rather than multiply a total: 1d6
  that rolled a 6 crits for 6 + the next die, not for 12.
- **Three steps are quotations and two are rulings.** Immunity, vulnerability
  and resistance come from the XPHB Rules Glossary. That a flat reduction
  applies after resistance, and a halving after the flat reduction, are the
  project's own rulings — they change every number a build with Heavy Armor
  Master or Uncanny Dodge reports, so each has a test of its own that says so
  and shows what the other order would have given.
- **The victim's handlers record; this module applies.** `mitigate()` is pure
  arithmetic over a ledger, which is what lets the order be pinned case by case
  with no fight around it.
- **What is not built yet fails loudly.** A queued effect has nothing to drain
  it until w1c, so it raises instead of being dropped.

Sources: XPHB (2024) Rules Glossary "Immunity", "Vulnerability", "Resistance",
"Round Down", "Temporary Hit Points"; DESIGN.md § Structural prevention over
testing, § Hook system; ROADMAP w0g2, w1e, w1j, w1c.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.dice import Dice
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock
from dndsim.engine.damage import mitigate, resolve_damage, roll_damage
from dndsim.hooks.contexts import (
    DamagePart,
    DamageTakenContext,
    IncomingDamageContext,
    MitigationLedger,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))

FIRE: Final = DamageType.FIRE
SLASHING: Final = DamageType.SLASHING
PIERCING: Final = DamageType.PIERCING
POISON: Final = DamageType.POISON


def creature(name: str = "dummy", *, hp: int = 20, temp_hp: int = 0) -> Creature:
    subject = Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=hp, armour_class=15),
        position=Cell(0, 0),
    )
    subject.temp_hp = temp_hp
    return subject


def part(
    dice: str | None = None,
    flat: int = 0,
    damage_type: DamageType | None = None,
    source: str = "test.weapon",
) -> DamagePart:
    """One contribution, built directly rather than through `ctx.damage.add()`:
    what is under test here is what `roll_damage()` makes of a part, not how a
    feature comes to write one."""
    return DamagePart(
        dice=None if dice is None else Dice.parse(dice),
        flat=flat,
        damage_type=damage_type,
        source=source,
    )


def ledger(
    *,
    immune: Iterable[DamageType] = (),
    vulnerable: Iterable[DamageType] = (),
    resist: Iterable[DamageType] = (),
    reductions: Iterable[tuple[str | None, int]] = (),
    halvings: Iterable[str] = (),
) -> MitigationLedger:
    """A ledger built the way a victim's handlers build one.

    Through a live, stamped `IncomingDamageContext` rather than by hand, because
    the mutators refuse an unattributed change — which is the machinery that
    makes provenance automatic, and is not this file's to route around.
    """
    ctx = IncomingDamageContext(defender=creature(), attacker=None, amounts={})
    ctx.stamp("test.victim", Phase.NORMAL)
    for damage_type in immune:
        ctx.add_immunity(damage_type)
    for damage_type in vulnerable:
        ctx.add_vulnerability(damage_type)
    for damage_type in resist:
        ctx.add_resistance(damage_type)
    for dice, flat in reductions:
        ctx.reduce(dice=dice, flat=flat)
    for reason in halvings:
        ctx.halve(reason)
    return ctx.mitigation


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled.

    Most of the order is arithmetic; a test that says so with an empty script
    also proves the step it is pinning rolls no dice.
    """
    return ScriptedRng([])


@dataclass(frozen=True, slots=True)
class Bandage:
    """The narrowest thing that satisfies the `Effect` protocol."""

    name: str


# --- Rolling what the attacker contributed ----------------------------------


def test_a_critical_hit_doubles_the_dice_and_never_the_flat() -> None:
    """A greatsword's 2d6 plus a +3 Strength modifier, critting.

    2d6 doubled is 4d6, rolled 6 + 5 + 4 + 3 = 18; the +3 is untouched, so 21.
    Doubling the whole contribution would be 28 and doubling the rolled total
    would be 25 — the two mistakes this data model exists to prevent.
    """
    rng = ScriptedRng([6, 5, 4, 3])
    amounts = roll_damage(
        [part("2d6", flat=3, damage_type=SLASHING)],
        rng=rng,
        base_type=SLASHING,
        critical=True,
    )
    assert amounts == {SLASHING: 21}
    assert rng.exhausted  # four dice, not two: the pool doubled, not the total


def test_the_same_contribution_on_an_ordinary_hit() -> None:
    """2d6 rolled 6 + 5 = 11, plus the +3 = 14 — the same part, half the dice."""
    rng = ScriptedRng([6, 5])
    amounts = roll_damage(
        [part("2d6", flat=3, damage_type=SLASHING)],
        rng=rng,
        base_type=SLASHING,
        critical=False,
    )
    assert amounts == {SLASHING: 14}
    assert rng.exhausted


def test_a_crit_re_rolls_dice_rather_than_multiplying_a_total() -> None:
    """The distinction the scripted generator exists to catch: 1d6 doubled is
    2d6, rolling a 6 and then a 1 for 7. Multiplying the first roll would give
    12, which is a different distribution wearing the same average."""
    rng = ScriptedRng([6, 1])
    amounts = roll_damage(
        [part("1d6", damage_type=FIRE)], rng=rng, base_type=FIRE, critical=True
    )
    assert amounts == {FIRE: 7}


def test_a_part_with_no_type_of_its_own_takes_the_attacks() -> None:
    """`damage_type=None` is a rule, not a missing value: Sneak Attack's d6s are
    the dagger's piercing. It is resolved here, at the last moment before the
    amounts stop being the attacker's, because every step downstream is keyed by
    type. 3d6 rolled 1 + 2 + 3 = 6."""
    rng = ScriptedRng([1, 2, 3])
    amounts = roll_damage(
        [part("3d6", source="rogue.sneak_attack")],
        rng=rng,
        base_type=PIERCING,
        critical=False,
    )
    assert amounts == {PIERCING: 6}


def test_two_parts_of_the_same_type_accumulate() -> None:
    """A dagger's 1d4 piercing (rolled 3) and Sneak Attack's untyped 2d6 (rolled
    4 + 5) are one pile of piercing damage, 3 + 9 = 12, not two — the victim's
    resistance applies once to the total of a type, and a split would halve them
    separately and round each down."""
    rng = ScriptedRng([3, 4, 5])
    amounts = roll_damage(
        [
            part("1d4", damage_type=PIERCING),
            part("2d6", source="rogue.sneak_attack"),
        ],
        rng=rng,
        base_type=PIERCING,
        critical=False,
    )
    assert amounts == {PIERCING: 12}


def test_different_types_stay_apart() -> None:
    """1d4 piercing rolled 2, plus 1d6 fire rolled 5 — kept separate, because
    resistance to one is not resistance to the other."""
    rng = ScriptedRng([2, 5])
    amounts = roll_damage(
        [part("1d4", damage_type=PIERCING), part("1d6", damage_type=FIRE)],
        rng=rng,
        base_type=PIERCING,
        critical=False,
    )
    assert amounts == {PIERCING: 2, FIRE: 5}


def test_a_flat_contribution_rolls_nothing() -> None:
    """Rage's +2 touches no die, on a crit or otherwise — asserted with an empty
    script, which raises the moment anything is rolled."""
    amounts = roll_damage(
        [part(flat=2, damage_type=SLASHING, source="barbarian.rage")],
        rng=no_dice(),
        base_type=SLASHING,
        critical=True,
    )
    assert amounts == {SLASHING: 2}


def test_no_contributions_is_no_damage() -> None:
    assert roll_damage([], rng=no_dice(), base_type=SLASHING, critical=True) == {}


# --- The resolution order, step by step -------------------------------------


def test_immunity_removes_a_type_before_anything_doubles_or_halves_it() -> None:
    """Immunity is not a multiplier of zero applied somewhere in the middle: the
    type leaves the event. Here the victim is both immune *and* vulnerable to
    fire, and the 9 fire is gone rather than doubled to 18 — 4 slashing is all
    that is left."""
    result = mitigate(
        {FIRE: 9, SLASHING: 4},
        ledger(immune=[FIRE], vulnerable=[FIRE]),
        rng=no_dice(),
    )
    assert result.total == 4
    assert dict(result.amounts) == {SLASHING: 4}
    assert result.types == frozenset({SLASHING})


def test_vulnerability_and_resistance_to_the_same_type_cancel() -> None:
    """The 2024 cancellation rule, falling out of the order rather than being
    written: 7 doubled is 14, halved is 7. Resistance first would give
    7 // 2 * 2 = 6 and quietly lose a point on every odd number."""
    both = ledger(vulnerable=[FIRE], resist=[FIRE])
    assert mitigate({FIRE: 7}, both, rng=no_dice()).total == 7


def test_vulnerability_doubles() -> None:
    """7 fire against a vulnerable creature is 14."""
    assert mitigate({FIRE: 7}, ledger(vulnerable=[FIRE]), rng=no_dice()).total == 14


def test_resistance_halves_and_rounds_down() -> None:
    """7 // 2 = 3, like every division in the game (XPHB "Round Down")."""
    assert mitigate({FIRE: 7}, ledger(resist=[FIRE]), rng=no_dice()).total == 3


def test_two_resistances_to_the_same_type_halve_once() -> None:
    """Structural rather than checked for: the ledger keeps a *set*, so a
    barbarian raging inside a Protection from Energy takes 3, not 1. Neither
    author has to know the other exists."""
    twice = ledger(resist=[FIRE, FIRE])
    assert twice.resistances == frozenset({FIRE})
    assert mitigate({FIRE: 7}, twice, rng=no_dice()).total == 3


def test_a_flat_reduction_applies_after_resistance() -> None:
    """**A ruling, pinned here.** No XPHB sentence settles a subtraction against
    a halving, and the two orders disagree by half the reduction on every hit.
    10 slashing, resisted to 5, then Heavy Armor Master's -3 leaves 2. The other
    order — (10 - 3) // 2 — would leave 3, and would make the reduction worth
    less to the creature that invested in the resistance."""
    result = mitigate(
        {SLASHING: 10},
        ledger(resist=[SLASHING], reductions=[(None, 3)]),
        rng=no_dice(),
    )
    assert result.total == 2


def test_a_halving_applies_after_the_flat_reduction() -> None:
    """**The second ruling, pinned here.** 10 slashing, Heavy Armor Master's -4
    leaves 6, and Uncanny Dodge halves that to 3. The other order — 10 // 2 = 5
    then -4 — would leave 1, which is two mitigation features worth more
    together than the sum of their parts."""
    result = mitigate(
        {SLASHING: 10},
        ledger(reductions=[(None, 4)], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 3


def test_the_whole_order_over_one_hit() -> None:
    """Every step at once, which is what w1e's "Rage + Uncanny Dodge +
    vulnerability produces the hand-computed number" asks for:

        poison 6   immune                      -> gone
        fire 5     vulnerable                  -> 10
        slashing 11  resisted                  -> 5
        total 15, less a flat 3                -> 12
        halved once                            -> 6
    """
    result = mitigate(
        {POISON: 6, FIRE: 5, SLASHING: 11},
        ledger(
            immune=[POISON],
            vulnerable=[FIRE],
            resist=[SLASHING],
            reductions=[(None, 3)],
            halvings=["uncanny dodge"],
        ),
        rng=no_dice(),
    )
    assert dict(result.amounts) == {FIRE: 10, SLASHING: 5}
    assert result.total == 6


def test_damage_never_goes_negative() -> None:
    """A reduction bigger than the hit is a hit that did nothing, not healing."""
    result = mitigate({SLASHING: 3}, ledger(reductions=[(None, 10)]), rng=no_dice())
    assert result.total == 0


def test_a_reduction_bigger_than_the_hit_does_not_survive_a_halving() -> None:
    """The clamp sits between the reductions and the halvings, so -10 against 3
    is 0 and half of 0 is 0 — not half of -7."""
    result = mitigate(
        {SLASHING: 3},
        ledger(reductions=[(None, 10)], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 0


def test_a_reduction_may_roll_dice_and_they_come_from_this_generator() -> None:
    """A reduction is a `Modifier`: dice and flat, and its dice never double.
    1d4 rolled 3, plus a flat 1, taken off 10 leaves 6."""
    rng = ScriptedRng([3])
    result = mitigate({SLASHING: 10}, ledger(reductions=[("1d4", 1)]), rng=rng)
    assert result.total == 6
    assert rng.exhausted


def test_resistance_and_a_halving_quarter_the_hit() -> None:
    """The case `mitigate()` names as known-correct: a raging barbarian's
    resistance halves 10 to 5 and Uncanny Dodge halves that to 2 (rounding down
    twice, not once from 2.5). It is the argument for halvings being a list —
    a halving is not a resistance and does not merge with one."""
    result = mitigate(
        {SLASHING: 10},
        ledger(resist=[SLASHING], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 2


def test_two_halvings_halve_twice() -> None:
    """The ruling extended one step further than the example: halvings are a
    *list* where the three type collections are sets, so two of them apply
    twice, 9 -> 4 -> 2. No 2024 sentence covers two literal "halve the damage"
    features — the module argues it by analogy with the case above — so it is
    pinned here for w1e rather than discovered in a build's numbers."""
    result = mitigate(
        {SLASHING: 9},
        ledger(halvings=["uncanny dodge", "a second halving"]),
        rng=no_dice(),
    )
    assert result.total == 2


def test_the_two_numbers_report_different_halves_of_the_order() -> None:
    """`amounts` is the per-type picture after the typed steps and `total` is
    what the creature takes, so they deliberately disagree once an untyped
    reduction has run: Heavy Armor Master reduces *the damage*, not the fire
    part of it, and there is no honest way to spread the subtraction back over
    the types. Anything downstream that sums `amounts` is reading the wrong
    number."""
    result = mitigate({FIRE: 8}, ledger(reductions=[(None, 3)]), rng=no_dice())
    assert dict(result.amounts) == {FIRE: 8}
    assert result.total == 5


def test_a_type_reduced_to_nothing_did_not_get_through() -> None:
    """`types` is what the log and `on_damage_taken` report, so a single point
    of fire resisted to zero is not a type this creature took damage from."""
    result = mitigate({FIRE: 1, SLASHING: 4}, ledger(resist=[FIRE]), rng=no_dice())
    assert dict(result.amounts) == {FIRE: 0, SLASHING: 4}
    assert result.types == frozenset({SLASHING})


def test_an_empty_ledger_changes_nothing() -> None:
    """The overwhelmingly common case: no handler recorded anything, so the
    rolled amounts arrive intact."""
    result = mitigate({SLASHING: 7, FIRE: 2}, ledger(), rng=no_dice())
    assert result.total == 9
    assert dict(result.amounts) == {SLASHING: 7, FIRE: 2}


# --- Dealing it to a creature -----------------------------------------------


class Resistant(HookSource):
    """A victim's feature: fire resistance, the shape Rage has."""

    ID = "test.fire_resistance"

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        ctx.add_resistance(FIRE)
        return R.APPLIED


class Watcher(HookSource):
    """Records what `on_damage_taken` was told, which is what a concentration
    check and the log both read."""

    __slots__ = ("seen",)

    ID = "test.watcher"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[tuple[int, frozenset[DamageType]]] = []

    @hook("on_damage_taken")
    def note(self, ctx: DamageTakenContext) -> R:
        self.seen.append((ctx.amount, ctx.damage_types))
        return R.APPLIED


class SlashResistant(HookSource):
    """Slashing resistance, the shape Rage has against a weapon."""

    ID = "test.slashing_resistance"

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        ctx.add_resistance(SLASHING)
        return R.APPLIED


class Immune(HookSource):
    """Fire immunity."""

    ID = "test.fire_immunity"

    @hook("on_incoming_damage")
    def immune(self, ctx: IncomingDamageContext) -> R:
        ctx.add_immunity(FIRE)
        return R.APPLIED


class HeavyArmour(HookSource):
    """A flat reduction, the shape Heavy Armor Master has."""

    ID = "test.heavy_armour"

    @hook("on_incoming_damage")
    def reduce(self, ctx: IncomingDamageContext) -> R:
        ctx.reduce(flat=10)
        return R.APPLIED


class Warded(HookSource):
    """An EARLY handler that halts the chain — the one Chain of Responsibility
    element in a pipeline that is otherwise contributions."""

    ID = "test.ward"

    @hook("on_incoming_damage", phase=Phase.EARLY)
    def ward(self, ctx: IncomingDamageContext) -> R:
        ctx.veto("warded")
        return R.APPLIED


def test_damage_with_nothing_attached_is_the_arithmetic() -> None:
    dummy = creature(hp=20)
    assert resolve_damage(dummy, {SLASHING: 7}, rng=no_dice()) == 7
    assert dummy.hp == 13


def test_temporary_hit_points_absorb_before_hit_points_and_are_spent_down() -> None:
    """Last step but one of the order. 8 damage against 5 temporary hit points:
    the 5 are spent, and the remaining 3 come off hit points."""
    dummy = creature(hp=20, temp_hp=5)
    assert resolve_damage(dummy, {SLASHING: 8}, rng=no_dice()) == 8
    assert (dummy.temp_hp, dummy.hp) == (0, 17)


def test_temporary_hit_points_that_cover_the_hit_leave_hit_points_alone() -> None:
    """3 against 5 temporary hit points: 2 remain and nothing reaches hit
    points. They are spent down rather than replaced."""
    dummy = creature(hp=20, temp_hp=5)
    assert resolve_damage(dummy, {SLASHING: 3}, rng=no_dice()) == 3
    assert (dummy.temp_hp, dummy.hp) == (2, 20)


def test_hit_points_stop_at_zero_and_the_creature_is_out() -> None:
    """20 damage to a creature with 5 hit points leaves it at 0, not at -15.
    The return is the whole instance of damage — what the attack dealt, which
    is what a damage-per-round number sums — while the overflow that matters
    for instant death is w1j's to read."""
    dummy = creature(hp=5)
    assert resolve_damage(dummy, {SLASHING: 20}, rng=no_dice()) == 20
    assert dummy.hp == 0
    assert not dummy.is_alive


def test_the_victims_handlers_get_their_chance_before_the_order_runs() -> None:
    """8 fire against a creature with fire resistance: 8 // 2 = 4."""
    dummy = creature(hp=20)
    dummy.attach(Resistant(dummy))
    assert resolve_damage(dummy, {FIRE: 8}, rng=no_dice()) == 4
    assert dummy.hp == 16


def test_on_damage_taken_sees_what_landed_and_what_it_was() -> None:
    """After the order, not before: 7 fire resisted to 3, plus 4 slashing
    untouched, is 7 taken of two types. A handler reading the pre-mitigation 11
    would over-charge every concentration check in the game."""
    dummy = creature(hp=20)
    dummy.attach(Resistant(dummy))
    watcher = Watcher(dummy)
    dummy.attach(watcher)
    assert resolve_damage(dummy, {FIRE: 7, SLASHING: 4}, rng=no_dice()) == 7
    assert watcher.seen == [(7, frozenset({FIRE, SLASHING}))]
    assert dummy.hp == 13


def test_a_veto_means_the_damage_did_not_happen() -> None:
    """Not zero damage dealt — no damage event at all: hit points and temporary
    hit points are both untouched, and nothing downstream is told anything."""
    dummy = creature(hp=20, temp_hp=4)
    dummy.attach(Warded(dummy))
    watcher = Watcher(dummy)
    dummy.attach(watcher)
    assert resolve_damage(dummy, {SLASHING: 9}, rng=no_dice()) == 0
    assert (dummy.hp, dummy.temp_hp) == (20, 4)
    assert watcher.seen == []


def test_damage_is_dispatched_to_the_victim_and_not_the_attacker() -> None:
    """`on_incoming_damage` and `on_damage_taken` are both TARGET-scoped, so the
    attacker's identically-named handlers stay out of it: the owner of a handler
    is decided by the hook's scope, never by a feature comparing creatures."""
    attacker = creature("attacker", hp=20)
    defender = creature("defender", hp=20)
    attacker.attach(Resistant(attacker))
    watching_attacker = Watcher(attacker)
    attacker.attach(watching_attacker)
    assert resolve_damage(defender, {FIRE: 8}, rng=no_dice(), attacker=attacker) == 8
    assert defender.hp == 12
    assert watching_attacker.seen == []


def test_a_hit_that_landed_on_nothing_does_not_reach_on_damage_taken() -> None:
    """ "When you take damage" means damage was taken. A creature immune to the
    only type in the hit takes none, so the hook does not fire and the
    concentration check that hangs off it is never asked for — the condition
    lives here rather than inside every handler ever written for the hook.

    The same 9 fire twice: once as damage, and once as nothing at all."""
    dummy = creature(hp=20)
    watcher = Watcher(dummy)
    dummy.attach(watcher)
    assert resolve_damage(dummy, {FIRE: 9}, rng=no_dice()) == 9
    assert dummy.hp == 11
    dummy.attach(Immune(dummy))
    assert resolve_damage(dummy, {FIRE: 9}, rng=no_dice()) == 0
    assert dummy.hp == 11
    assert watcher.seen == [(9, frozenset({FIRE}))]


def test_a_hit_a_reduction_ate_whole_does_not_reach_it_either() -> None:
    """The other way to land on nothing, and the one where `types` still names
    a type: Heavy Armor Master's -10 against a 3-point hit leaves 0, so nothing
    was taken and nothing is reported, even though the hit was slashing and
    `Mitigated.types` says so."""
    dummy = creature(hp=20)
    dummy.attach(HeavyArmour(dummy))
    watcher = Watcher(dummy)
    dummy.attach(watcher)
    assert resolve_damage(dummy, {SLASHING: 3}, rng=no_dice()) == 0
    assert dummy.hp == 20
    assert watcher.seen == []


def test_a_hit_the_temporary_hit_points_ate_whole_does_reach_it() -> None:
    """The distinction the rule turns on: temporary hit points are not immunity.
    The 5 damage happened and those hit points are gone, so the creature took
    damage and the hook fires — a concentration check is owed for a hit False
    Life absorbed."""
    dummy = creature(hp=20, temp_hp=9)
    watcher = Watcher(dummy)
    dummy.attach(watcher)
    assert resolve_damage(dummy, {SLASHING: 5}, rng=no_dice()) == 5
    assert (dummy.temp_hp, dummy.hp) == (4, 20)
    assert watcher.seen == [(5, frozenset({SLASHING}))]


# --- What is not built yet -------------------------------------------------


class Queuer(HookSource):
    """A handler that applies an effect — Hellish Rebuke's shape, and nothing
    yet exists to carry it out."""

    ID = "test.queuer"

    @hook("on_incoming_damage")
    def queue(self, ctx: IncomingDamageContext) -> R:
        ctx.apply_effect(ctx.defender, Bandage(name="on fire"))
        return R.APPLIED


class LateQueuer(HookSource):
    """The same mistake one hook later, after hit points have already moved."""

    ID = "test.late_queuer"

    @hook("on_damage_taken")
    def queue(self, ctx: DamageTakenContext) -> R:
        ctx.apply_effect(ctx.defender, Bandage(name="bleeding"))
        return R.APPLIED


def test_an_effect_queued_during_damage_is_refused_until_w1c() -> None:
    """**Delete this test in w1c**, along with `_refuse_pending()` itself.
    `EffectStore` is what drains `ctx.pending_effects`; until it exists a
    handler that queues an effect would have it silently discarded, which is a
    feature whose whole point did nothing and said nothing. Loud beats silent,
    so it raises."""
    dummy = creature(hp=20)
    dummy.attach(Queuer(dummy))
    with pytest.raises(EngineAssemblyError, match="w1c"):
        resolve_damage(dummy, {SLASHING: 6}, rng=no_dice())


def test_the_same_refusal_covers_the_second_dispatch() -> None:
    """`on_damage_taken` is drained by nobody either. Note that hit points have
    already moved by the time it raises: this is an assembly error about a
    half-built engine, not a rules error to be caught and continued from —
    which is the other half of the reason w1c deletes it rather than handles
    it."""
    dummy = creature(hp=20)
    dummy.attach(LateQueuer(dummy))
    with pytest.raises(EngineAssemblyError, match="w1c"):
        resolve_damage(dummy, {SLASHING: 6}, rng=no_dice())
    assert dummy.hp == 14


# --- The seam between the two halves ----------------------------------------


def test_what_was_rolled_is_what_is_resolved() -> None:
    """The two halves of this module meeting, which is the path `engine/attack.py`
    (w1f) will take: a greatsword crit for 2d6 (doubled to 4d6, rolled
    6 + 6 + 1 + 1 = 14) plus a +3 modifier is 17 slashing; a victim resisting
    slashing takes 17 // 2 = 8, and 20 - 8 leaves 12 hit points."""
    rng = ScriptedRng([6, 6, 1, 1])
    amounts = roll_damage(
        [part("2d6", flat=3, damage_type=SLASHING)],
        rng=rng,
        base_type=SLASHING,
        critical=True,
    )
    assert amounts == {SLASHING: 17}
    dummy = creature(hp=20)
    dummy.attach(SlashResistant(dummy))
    assert resolve_damage(dummy, amounts, rng=rng, attacker=creature("fighter")) == 8
    assert dummy.hp == 12
    assert rng.exhausted
