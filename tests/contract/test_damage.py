"""The damage contract: the crit rule, and the one order damage resolves in.

**immunity -> flat reductions -> halvings -> resistance -> vulnerability ->
temporary hit points -> hit points.** DESIGN.md fixes that order and fixes the
file it is written in; this is the file that says what it *means* in integers.
Every case below carries its arithmetic in a comment, because the point of a
contract test on an ordering is that the number and the reasoning are read
together — a bare `== 6` proves nothing about which order produced it. Where
`w0g2` resolved a case differently, the case says what its order gave: the
reorder changes reported numbers, and a test that hid that would be no use to
whoever wonders why a build's damage moved.

Five properties carry the file:

- **Dice double on a critical hit and flat bonuses never do.** Rolled through a
  scripted generator so that the assertion is an exact integer and so that a
  crit is seen to *re-roll* a doubled pool rather than multiply a total: 1d6
  that rolled a 6 crits for 6 + the next die, not for 12.
- **The middle of the order is a quotation and one step is a ruling.**
  Adjustments, then Resistance, then Vulnerability is XPHB "Damage and Healing",
  and the book's own worked example is the first case below. Which of a flat
  reduction and a halving goes first inside that single adjustment step is the
  project's to fix, so it has a test that says so and shows what the other order
  would have given.
- **An untyped adjustment is spent in proportion.** The reorder put the untyped
  steps before the typed ones, so a mixed-type hit needs a per-type picture on
  the far side of a step that has none. The spend may not look ahead to
  resistance and may not depend on the order the damage parts arrived; both
  properties have a case of their own.
- **The victim's handlers record; this module applies.** `mitigate()` is pure
  arithmetic over a ledger, which is what lets the order be pinned case by case
  with no fight around it.
- **What is not built yet fails loudly.** A queued effect has nothing to drain
  it until w1c, so it raises instead of being dropped.

Sources: XPHB (2024) "Damage and Healing", Rules Glossary "Immunity",
"Vulnerability", "Resistance", "Round Down", "Temporary Hit Points"; DESIGN.md
§ Structural prevention over testing, § Hook system; ROADMAP w0g2, w1e, w1j,
w1c.
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
COLD: Final = DamageType.COLD
PIERCING: Final = DamageType.PIERCING
POISON: Final = DamageType.POISON


def creature(name: str = "dummy", *, hp: int = 20, temp_hp: int = 0) -> Creature:
    subject = Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=hp),
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


def test_the_books_own_worked_example() -> None:
    """XPHB, "Damage and Healing", written out: 28 fire damage, reduced by 5 to
    23, halved by Resistance to 11, doubled by Vulnerability to 22. The
    quotation the order is taken from ships its own test case, so this is the
    one assertion in the file that is not an argument."""
    result = mitigate(
        {FIRE: 28},
        ledger(vulnerable=[FIRE], resist=[FIRE], reductions=[(None, 5)]),
        rng=no_dice(),
    )
    assert result.total == 22


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


def test_immunity_runs_first_so_the_adjustments_are_not_spent_on_it() -> None:
    """Why immunity leads rather than merely being somewhere before the typed
    steps. 10 fire and 10 poison against a poison-immune creature with Heavy
    Armor Master's -4: the poison is gone first, so the whole 4 comes off the
    fire and 6 lands. An immunity applied after the adjustments would have split
    the 4 across both piles and let 8 fire through — the reduction spent on
    damage that was never going to be taken."""
    result = mitigate(
        {FIRE: 10, POISON: 10},
        ledger(immune=[POISON], reductions=[(None, 4)]),
        rng=no_dice(),
    )
    assert dict(result.amounts) == {FIRE: 6}
    assert result.total == 6


def test_vulnerability_and_resistance_to_the_same_type_no_longer_cancel() -> None:
    """The first of the two consequences that read as bugs. 7 halved by
    Resistance is 3 and doubled by Vulnerability is 6, so the pair loses a point
    on every odd number. `w0g2` ran vulnerability first for exactly that reason
    — `(2n) // 2 == n` on every integer — and the 2024 text orders it the other
    way regardless. The missing point is the book's answer."""
    both = ledger(vulnerable=[FIRE], resist=[FIRE])
    assert mitigate({FIRE: 7}, both, rng=no_dice()).total == 6


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


def test_a_flat_reduction_applies_before_resistance() -> None:
    """A penalty is an *adjustment*, and adjustments are applied first. 10
    slashing less Heavy Armor Master's 3 is 7, and resistance halves that to 3.
    `w0g2` ruled the other way — resist to 5, then subtract 3, for 2 — on the
    argument that "the damage you take" is what arrives after your resistances.
    The 2024 text settles it against that reading, and the second consequence
    follows: the reduction is worth half to the creature that also has the
    resistance."""
    result = mitigate(
        {SLASHING: 10},
        ledger(resist=[SLASHING], reductions=[(None, 3)]),
        rng=no_dice(),
    )
    assert result.total == 3


def test_a_halving_applies_after_the_flat_reduction() -> None:
    """**The one surviving ruling, pinned here.** The text bundles penalties and
    multipliers into a single adjustment step without sequencing them against
    each other, so the order of these two is the project's. 10 slashing, Heavy
    Armor Master's -4 leaves 6, and Uncanny Dodge halves that to 3. The other
    order — 10 // 2 = 5 then -4 — would leave 1, which is two mitigation
    features worth more together than the sum of their parts."""
    result = mitigate(
        {SLASHING: 10},
        ledger(reductions=[(None, 4)], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 3


def test_a_halving_is_an_adjustment_and_so_precedes_the_typed_steps() -> None:
    """A multiplier is named in the quotation, so Uncanny Dodge sits in the
    first step with the reductions rather than after resistance. 9 slashing
    halved is 4, and vulnerability doubles that to 8. Halving after the typed
    steps would double first — 18, halved to 9 — so the placement is worth a
    point here and is not merely bookkeeping."""
    result = mitigate(
        {SLASHING: 9},
        ledger(vulnerable=[SLASHING], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 8


def test_resistance_and_a_halving_quarter_the_hit() -> None:
    """The known-correct case both collections exist for: Uncanny Dodge halves
    10 to 5 and a raging barbarian's resistance halves that to 2 (rounding down
    twice, not once from 2.5). A halving is not a resistance and does not merge
    with one."""
    result = mitigate(
        {SLASHING: 10},
        ledger(resist=[SLASHING], halvings=["uncanny dodge"]),
        rng=no_dice(),
    )
    assert result.total == 2


def test_two_halvings_halve_twice() -> None:
    """The open question `w0g2` left, now answered from the text rather than
    from silence: the "counts once" clause names *Resistance*, and a halving is
    not Resistance. So halvings are a *list* where the three type collections
    are sets, and two of them apply twice, 9 -> 4 -> 2."""
    result = mitigate(
        {SLASHING: 9},
        ledger(halvings=["uncanny dodge", "a second halving"]),
        rng=no_dice(),
    )
    assert result.total == 2


def test_rage_uncanny_dodge_and_vulnerability_over_one_type() -> None:
    """Every band of the order against a single type, which is what w1e's "Rage
    + Uncanny Dodge + vulnerability produces the hand-computed number" asks
    for:

        slashing 25   less Heavy Armor Master's 3   -> 22
                      halved by Uncanny Dodge       -> 11
                      halved by Rage's resistance   ->  5
                      doubled by vulnerability      -> 10

    `w0g2`'s order gave 11 for the same four features: 25 doubled to 50, resisted
    to 25, less 3 is 22, halved is 11.
    """
    result = mitigate(
        {SLASHING: 25},
        ledger(
            vulnerable=[SLASHING],
            resist=[SLASHING],
            reductions=[(None, 3)],
            halvings=["uncanny dodge"],
        ),
        rng=no_dice(),
    )
    assert result.total == 10


def test_the_whole_order_over_one_mixed_hit() -> None:
    """The same, with the untyped steps spread across two surviving types:

        poison 6      immune                        -> gone
        fire 5, slashing 11                         -> 16 left
        less a flat 4                               -> 12
        halved once                                 -> 6
        apportioned 16 -> 6                         -> fire 2, slashing 4
        slashing resisted                           -> 2
        fire doubled                                -> 4

    Fire takes the odd point because 5/16 of 6 is 1.875 against slashing's
    4.125: both floor, and the larger remainder gets the one point left over.
    """
    result = mitigate(
        {POISON: 6, FIRE: 5, SLASHING: 11},
        ledger(
            immune=[POISON],
            vulnerable=[FIRE],
            resist=[SLASHING],
            reductions=[(None, 4)],
            halvings=["uncanny dodge"],
        ),
        rng=no_dice(),
    )
    assert dict(result.amounts) == {FIRE: 4, SLASHING: 2}
    assert result.total == 6


# --- Spending an untyped adjustment across a mixed hit -----------------------


def test_an_untyped_adjustment_is_spent_in_proportion() -> None:
    """**The ruling the reorder forced, pinned here.** An adjustment changes the
    size of the hit, not its composition, so 5 fire and 5 slashing under Uncanny
    Dodge become 3 and 2 rather than 5 and 0. The odd point goes to the type
    furthest from its share and, on a dead tie like this one, to the type whose
    name sorts first — arbitrary, but fixed, which is the whole requirement.

    Asserted from **both** insertion orders, because this is the only shape of
    case whose outcome the tie-break decides: with distinct remainders the sort
    would land the same way without it, so a test that fed the types in one
    order only would leave `damage_type.value` deletable and the suite green.

    The rejected alternative was spending the whole adjustment against the
    largest component: it would delete the fire outright here, and would flip to
    deleting the slashing on a one-point change in either."""
    halved = ledger(halvings=["uncanny dodge"])
    forwards = mitigate({FIRE: 5, SLASHING: 5}, halved, rng=no_dice())
    backwards = mitigate({SLASHING: 5, FIRE: 5}, halved, rng=no_dice())
    assert dict(forwards.amounts) == {FIRE: 3, SLASHING: 2}
    assert dict(backwards.amounts) == {FIRE: 3, SLASHING: 2}
    assert forwards.total == backwards.total == 5


def test_more_mitigation_never_means_more_damage() -> None:
    """**The property that picked the apportionment method.** Jefferson's method
    hands out the nth point without disturbing the first n-1, so a bigger
    reduction can only ever move the creature down.

    That is not automatic, and the case below is the one that caught it: 1 fire
    beside 5 slashing and 5 cold, resisting the latter two. Under the
    largest-remainder method this started as, a 5-point reduction landed 2 damage
    and a 6-point reduction landed **3** — the odd point moved off a resisted
    pile onto the unresisted one, and a point of extra mitigation cost the
    creature a point of extra damage (with fire vulnerable instead of plain, 2
    became 4). That is the Alabama paradox, it needs three types to show, and
    no two-type case in this file could have caught it.
    """
    hit = {FIRE: 1, SLASHING: 5, COLD: 5}
    resisted = [SLASHING, COLD]
    five = mitigate(hit, ledger(resist=resisted, reductions=[(None, 5)]), rng=no_dice())
    six = mitigate(hit, ledger(resist=resisted, reductions=[(None, 6)]), rng=no_dice())
    assert dict(five.amounts) == dict(six.amounts) == {FIRE: 0, SLASHING: 1, COLD: 1}
    assert five.total == six.total == 2

    # The whole sweep, not just the pair the paradox happened to surface on —
    # and the same sweep with fire vulnerable, which is where it cost double.
    plain = [
        mitigate(hit, ledger(resist=resisted, reductions=[(None, r)]), rng=no_dice())
        for r in range(1, 12)
    ]
    doubled = [
        mitigate(
            hit,
            ledger(resist=resisted, vulnerable=[FIRE], reductions=[(None, r)]),
            rng=no_dice(),
        )
        for r in range(1, 12)
    ]
    assert [r.total for r in plain] == [4, 4, 4, 3, 2, 2, 2, 1, 0, 0, 0]
    assert [r.total for r in doubled] == [4, 4, 4, 3, 2, 2, 2, 1, 0, 0, 0]


def test_the_spend_does_not_depend_on_the_order_the_parts_arrived() -> None:
    """The second property that fixed the ruling. Which of the dagger and Sneak
    Attack appended its contribution first is an authoring artefact, and a
    reported number that moved when a feature registered earlier would be a
    reproducibility break with no error anywhere. The two dicts differ only in
    insertion order."""
    reduced = ledger(reductions=[(None, 5)])
    forwards = mitigate({FIRE: 7, SLASHING: 4}, reduced, rng=no_dice())
    backwards = mitigate({SLASHING: 4, FIRE: 7}, reduced, rng=no_dice())
    assert dict(forwards.amounts) == dict(backwards.amounts) == {FIRE: 4, SLASHING: 2}
    assert forwards.total == backwards.total == 6


def test_the_spend_does_not_look_ahead_to_resistance() -> None:
    """The first property, and the one that costs the defender points. 10 fire
    and 10 slashing against a fire-resistant creature with a 10-point reduction:
    the reduction is split 5/5 in proportion, fire resists to 2, and 7 lands.
    Spending all 10 on the unresisted slashing would have left 5 — the better
    play, and exactly what "adjustments are applied first" forbids, because an
    adjustment cannot know what Resistance will do with what it leaves."""
    result = mitigate(
        {FIRE: 10, SLASHING: 10},
        ledger(resist=[FIRE], reductions=[(None, 10)]),
        rng=no_dice(),
    )
    assert dict(result.amounts) == {FIRE: 2, SLASHING: 5}
    assert result.total == 7


def test_an_adjustment_can_round_a_type_out_of_the_report() -> None:
    """What inverted with the order: under `w0g2` an untyped step could never
    change `types`, and now it can. One point of fire beside 99 of slashing,
    halved: half a point of fire is no fire, so the creature took 50 slashing
    and nothing else — which is what `on_damage_taken` and the log should say."""
    result = mitigate(
        {FIRE: 1, SLASHING: 99}, ledger(halvings=["uncanny dodge"]), rng=no_dice()
    )
    assert dict(result.amounts) == {FIRE: 0, SLASHING: 50}
    assert result.types == frozenset({SLASHING})
    assert result.total == 50


def test_the_two_numbers_agree_because_the_typed_steps_run_last() -> None:
    """`w0g2` had these two deliberately disagree — the untyped steps ran last,
    so `total` was smaller than the per-type picture and nothing could reconcile
    them. The 2024 order puts the typed steps last, the apportionment is the
    reconciliation, and `total` is now exactly the sum of `amounts`. Anything
    downstream may read either."""
    result = mitigate({FIRE: 8}, ledger(reductions=[(None, 3)]), rng=no_dice())
    assert dict(result.amounts) == {FIRE: 5}
    assert result.total == 5 == sum(result.amounts.values())


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


def test_a_type_resisted_to_nothing_did_not_get_through() -> None:
    """`types` is what the log and `on_damage_taken` report, so a single point
    of fire resisted to zero is not a type this creature took damage from."""
    result = mitigate({FIRE: 1, SLASHING: 4}, ledger(resist=[FIRE]), rng=no_dice())
    assert dict(result.amounts) == {FIRE: 0, SLASHING: 4}
    assert result.types == frozenset({SLASHING})


def test_the_floor_survives_the_untouched_shortcut() -> None:
    """The empty ledger skips the order; it must not skip the clamp.

    `DamagePart.flat` is a signed `int` and nothing rejects a negative one, so
    a contribution that subtracts more than the hit deals reaches `mitigate()`
    as a negative total. Down the shortcut that would arrive at `resolve_damage`
    as negative damage — which is temporary hit points granted by a sword. The
    slow path clamps at zero, so the fast path does too.
    """
    result = mitigate({SLASHING: -4}, ledger(), rng=no_dice())
    assert result.total == 0
    assert mitigate({SLASHING: -4}, ledger(resist=[FIRE]), rng=no_dice()).total == 0


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
    """The other way to land on nothing: Heavy Armor Master's -10 against a
    3-point hit leaves 0, so nothing was taken and nothing is reported. Since
    `w1e` the reduction is spent over the types rather than over a total they
    never see again, so `Mitigated.types` is empty here too — the hit was
    slashing, and no slashing damage was taken."""
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
