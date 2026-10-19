"""The saving-throw contract: the DC, the roll, and half damage on a success.

Every case carries its arithmetic, because a bare `== 15` proves nothing about
which rule produced it. The creature every test saves with is a level-5 caster:
proficiency +3, Strength 10 (+0), Dexterity 14 (+2), Constitution 12 (+1),
Charisma 18 (+4).

The save-for-half cases are the point of the file, and most of them pass under a
halving taken at the call site: halving commutes with immunity, with resistance
and with a second halving. The four that do not are a flat reduction, which the
halving must follow; an odd mixed-type hit, where one halving of the total and
one of each part disagree; the ledger the victim's handlers read; and
vulnerability, which the halving must precede.

Sources: XPHB (2024) "D20 Tests" -> Saving Throws, "Spells" -> Spell Save DC,
"Damage and Healing", Rules Glossary "Round Down", "Immunity", "Resistance";
ROADMAP w1g; `docs/decisions.md` § w1e, § w1g.
"""

from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.errors import FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.creatures.resources import Recovery, ResourceSpec
from dndsim.creatures.stats import StatBlock
from dndsim.engine import saves
from dndsim.engine.saves import (
    HALVED_BY_SAVE,
    SaveResult,
    force_save,
    save_bonus,
    save_dc,
    save_for_half,
    saving_throw,
)
from dndsim.hooks.contexts import (
    ForcedSaveContext,
    IncomingDamageContext,
    SaveResolvedContext,
    SavingThrowContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import (
    Ability,
    DamageType,
    RecoveryTrigger,
    SaveOutcome,
    SaveRepeat,
)

SCORES: Final = MappingProxyType(
    {
        Ability.STRENGTH: 10,
        Ability.DEXTERITY: 14,
        Ability.CONSTITUTION: 12,
        Ability.INTELLIGENCE: 10,
        Ability.WISDOM: 10,
        Ability.CHARISMA: 18,
    }
)

DEX: Final = Ability.DEXTERITY
FIRE: Final = DamageType.FIRE
COLD: Final = DamageType.COLD


def creature(
    name: str = "dummy", *, hp: int = 40, level: int = 5, dexterity: int = 14
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(
            abilities={**SCORES, Ability.DEXTERITY: dexterity},
            max_hp=hp,
            level=level,
        ),
        position=Cell(0, 0),
    )


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled."""
    return ScriptedRng([])


# --- The DC and the bonus ----------------------------------------------------


def test_the_dc_is_eight_plus_proficiency_plus_the_ability_modifier() -> None:
    """8 + 3 (level 5) + 4 (Charisma 18) = 15."""
    assert save_dc(creature(), Ability.CHARISMA) == 15


def test_the_dc_ability_is_the_imposers_and_not_the_targets() -> None:
    """The same caster imposing with Strength 10: 8 + 3 + 0 = 11."""
    assert save_dc(creature(), Ability.STRENGTH) == 11


def test_the_dc_follows_the_proficiency_table_rather_than_a_fixed_bonus() -> None:
    """The same Charisma 18 at level 1: 8 + 2 + 4 = 14, not 15."""
    assert save_dc(creature(level=1), Ability.CHARISMA) == 14


def test_a_save_adds_the_ability_modifier_and_no_proficiency_yet() -> None:
    """Dexterity 14 is +2. The +3 proficiency a proficient creature would add
    is debt #36 — nothing can declare a save proficiency yet."""
    assert save_bonus(creature(), DEX) == 2
    assert save_bonus(creature(), Ability.STRENGTH) == 0


# --- The roll ----------------------------------------------------------------


def test_a_total_that_equals_the_dc_succeeds() -> None:
    """13 + 2 = 15 against DC 15."""
    result = saving_throw(creature(), ability=DEX, dc=15, rng=ScriptedRng([13]))
    assert result == SaveResult(outcome=SaveOutcome.SUCCESS, total=15, dc=15)


def test_the_modifier_added_is_the_savers_and_not_the_imposers() -> None:
    """A clumsy target, Dexterity 8 (-1): 13 - 1 = 12 against DC 15 fails,
    where the caster's own +2 would have made it."""
    caster = creature("caster")
    result = force_save(
        caster,
        [creature("clumsy", dexterity=8)],
        ability=DEX,
        dc=15,
        rng=ScriptedRng([13]),
    )
    assert result[0].total == 12
    assert result[0].outcome is SaveOutcome.FAILURE


def test_a_total_one_below_the_dc_fails() -> None:
    """12 + 2 = 14 against DC 15."""
    result = saving_throw(creature(), ability=DEX, dc=15, rng=ScriptedRng([12]))
    assert result == SaveResult(outcome=SaveOutcome.FAILURE, total=14, dc=15)


def test_a_natural_twenty_that_misses_the_dc_still_fails() -> None:
    """20 + 2 = 22 against DC 25. A save has no automatic success: the rule that
    makes a 20 decide an attack roll on its own is the Attack Rolls entry's, and
    the Saving Throws entry beside it gives a 20 no effect at all."""
    result = saving_throw(creature(), ability=DEX, dc=25, rng=ScriptedRng([20]))
    assert result.outcome is SaveOutcome.FAILURE
    assert result.total == 22


def test_a_natural_one_that_makes_the_dc_still_succeeds() -> None:
    """1 + 2 = 3 against DC 3 — the other half of the same rule."""
    result = saving_throw(creature(), ability=DEX, dc=3, rng=ScriptedRng([1]))
    assert result.outcome is SaveOutcome.SUCCESS
    assert result.total == 3


def test_advantage_rolls_twice_and_keeps_the_higher() -> None:
    """7 and 18, kept 18, + 2 = 20."""
    saver = creature()
    saver.attach(DangerSense(saver))
    rng = ScriptedRng([7, 18])
    result = saving_throw(saver, ability=DEX, dc=15, rng=rng)
    assert result.total == 20
    assert rng.exhausted


def test_disadvantage_rolls_twice_and_keeps_the_lower() -> None:
    """7 and 18, kept 7, + 2 = 9."""
    saver = creature()
    saver.attach(Frightening(saver))
    rng = ScriptedRng([7, 18])
    result = saving_throw(saver, ability=DEX, dc=15, rng=rng)
    assert result.total == 9
    assert rng.exhausted


def test_advantage_and_disadvantage_cancel_to_one_die() -> None:
    """One d20, not two: 7 + 2 = 9, and the second face is never drawn."""
    saver = creature()
    saver.attach(DangerSense(saver))
    saver.attach(Frightening(saver))
    rng = ScriptedRng([7, 18])
    result = saving_throw(saver, ability=DEX, dc=15, rng=rng)
    assert result.total == 9
    assert rng.remaining == 1


def test_a_handlers_bonus_is_added_to_the_total_dice_and_all() -> None:
    """Aura of Protection's +3 and Bless's 1d4 rolling a 4: 13 + 2 + 3 + 4 = 22.

    The bonus dice are drawn before the d20, so the script reads [4, 13] — the
    same order `engine/attack.py` draws an attack's modifiers in.
    """
    saver = creature()
    saver.attach(Aura(saver))
    rng = ScriptedRng([4, 13])
    result = saving_throw(saver, ability=DEX, dc=15, rng=rng)
    assert result.total == 22
    assert rng.exhausted


def test_a_save_nothing_imposed_carries_no_imposer() -> None:
    """A hazard's save: `imposed_by` is None and the handlers are told so."""
    saver = creature()
    watcher = Watcher(saver)
    saver.attach(watcher)
    saving_throw(saver, ability=DEX, dc=10, rng=ScriptedRng([10]))
    assert watcher.imposers == [None]


# --- Forcing a save on several creatures -------------------------------------


def test_forcing_a_save_dispatches_the_dcs_hook_once_for_every_target() -> None:
    """One DC for the area, so one `on_forced_save` however many save."""
    caster = creature("caster")
    counter = DcWatcher(caster)
    caster.attach(counter)
    targets = [creature("a"), creature("b"), creature("c")]
    results = force_save(
        caster, targets, ability=DEX, dc=15, rng=ScriptedRng([10, 11, 12])
    )
    assert counter.calls == 1
    assert counter.targets == ("a", "b", "c")
    assert [r.total for r in results] == [12, 13, 14]


def test_a_bonus_to_the_dc_applies_to_every_save_rolled_against_it() -> None:
    """A +2 to the DC: 15 becomes 17, and 14 + 2 = 16 now fails where it made 15."""
    caster = creature("caster")
    caster.attach(Focused(caster))
    results = force_save(
        caster,
        [creature("a"), creature("b")],
        ability=DEX,
        dc=15,
        rng=ScriptedRng([14, 15]),
    )
    assert [r.dc for r in results] == [17, 17]
    assert [r.outcome for r in results] == [SaveOutcome.FAILURE, SaveOutcome.SUCCESS]


def test_each_target_rolls_its_own_save() -> None:
    """Two creatures, two d20s, two outcomes against the same DC 15."""
    caster = creature("caster")
    results = force_save(
        caster,
        [creature("a"), creature("b")],
        ability=DEX,
        dc=15,
        rng=ScriptedRng([3, 19]),
    )
    assert [r.total for r in results] == [5, 21]
    assert [r.outcome for r in results] == [SaveOutcome.FAILURE, SaveOutcome.SUCCESS]


def test_a_forced_save_tells_the_target_who_forced_it() -> None:
    caster = creature("caster")
    target = creature("target")
    watcher = Watcher(target)
    target.attach(watcher)
    force_save(caster, [target], ability=DEX, dc=15, rng=ScriptedRng([10]))
    assert watcher.imposers == [caster]


def test_a_bonus_of_dice_to_the_dc_is_rolled_once_for_the_whole_area() -> None:
    """One 1d4 rolling a 4 for both targets: DC 15 + 4 = 19 each, and the
    script's remaining two faces are the two d20s."""
    caster = creature("caster")
    caster.attach(Rallied(caster))
    results = force_save(
        caster,
        [creature("a"), creature("b")],
        ability=DEX,
        dc=15,
        rng=ScriptedRng([4, 10, 11]),
    )
    assert [r.dc for r in results] == [19, 19]
    assert [r.total for r in results] == [12, 13]


def test_forcing_a_save_on_nobody_rolls_nothing_and_fires_nothing() -> None:
    """Nobody is forced, so the imposer's hook does not fire either: a handler
    on it can carry a `once_per` budget or an action cost."""
    caster = creature("caster")
    counter = DcWatcher(caster)
    caster.attach(counter)
    assert force_save(caster, [], ability=DEX, dc=15, rng=no_dice()) == ()
    assert counter.calls == 0


# --- Legendary Resistance: the acceptance test -------------------------------


def test_legendary_resistance_flips_a_failure_with_no_engine_branch() -> None:
    """3 + 2 = 5 against DC 15 is a failure the dragon declares a success.

    One `LONG_REST` pool and one handler on `on_save_resolved`; the roll still
    happened and `total` still says what it was.
    """
    dragon = creature("dragon")
    dragon.resources.add(LEGENDARY)
    dragon.attach(LegendaryResistance(dragon))
    result = saving_throw(dragon, ability=DEX, dc=15, rng=ScriptedRng([3]))
    assert result.outcome is SaveOutcome.SUCCESS
    assert result.total == 5
    assert result.forced_by == ("test.legendary_resistance: Legendary Resistance",)
    assert dragon.resources["legendary_resistance"].current == 2


def test_legendary_resistance_runs_out() -> None:
    """Three uses a day: the fourth failure stands, and nothing is overdrawn."""
    dragon = creature("dragon")
    dragon.resources.add(LEGENDARY)
    dragon.attach(LegendaryResistance(dragon))
    outcomes = [
        saving_throw(dragon, ability=DEX, dc=15, rng=ScriptedRng([3])).outcome
        for _ in range(4)
    ]
    assert outcomes == [SaveOutcome.SUCCESS] * 3 + [SaveOutcome.FAILURE]
    assert dragon.resources["legendary_resistance"].current == 0


def test_a_success_the_dice_rolled_spends_nothing() -> None:
    """The handler asks `ctx.succeeded` first, which is why it is on the context."""
    dragon = creature("dragon")
    dragon.resources.add(LEGENDARY)
    dragon.attach(LegendaryResistance(dragon))
    result = saving_throw(dragon, ability=DEX, dc=15, rng=ScriptedRng([19]))
    assert result.outcome is SaveOutcome.SUCCESS
    assert result.forced_by == ()
    assert dragon.resources["legendary_resistance"].current == 3


def test_the_saving_throw_sequence_knows_nothing_about_legendary_resistance() -> None:
    """The acceptance test the ROADMAP row names: the feature above is a
    resource and a handler, and `engine/saves.py` contains no branch for it."""
    source = Path(saves.__file__).read_text()
    assert "legendary" not in source.lower()
    assert "resource" not in source.lower()


# --- Save for half -----------------------------------------------------------


def test_a_failed_save_takes_all_of_it() -> None:
    target = creature(hp=40)
    dealt = save_for_half(
        target, {FIRE: 28}, rng=no_dice(), outcome=SaveOutcome.FAILURE
    )
    assert dealt == 28
    assert target.hp == 12


def test_a_successful_save_takes_half_rounded_down() -> None:
    """7 halved is 3 (XPHB, "Round Down"), not 3.5 and not 4."""
    target = creature(hp=40)
    dealt = save_for_half(target, {FIRE: 7}, rng=no_dice(), outcome=SaveOutcome.SUCCESS)
    assert dealt == 3
    assert target.hp == 37


def test_the_halving_follows_a_flat_reduction() -> None:
    """28 fire, reduced by 10, then halved: (28 - 10) // 2 = 9.

    The case a halving taken at the call site gets wrong: it would halve 28 to
    14 and then reduce to 4, because the reduction is an adjustment that
    precedes the halving (`docs/decisions.md` § w1e).
    """
    target = creature(hp=40)
    target.attach(HeavyArmour(target))
    dealt = save_for_half(
        target, {FIRE: 28}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 9


def test_the_halving_cannot_resurrect_an_immunity() -> None:
    """Immunity comes first, so there is nothing left to halve."""
    target = creature(hp=40)
    target.attach(FireImmune(target))
    dealt = save_for_half(
        target, {FIRE: 28}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 0
    assert target.hp == 40


def test_the_halving_and_a_resistance_are_two_steps() -> None:
    """28 halved to 14, then resisted to 7 — not one halving of 28."""
    target = creature(hp=40)
    target.attach(FireResistant(target))
    dealt = save_for_half(
        target, {FIRE: 28}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 7


def test_two_halvings_stack() -> None:
    """Uncanny Dodge beside a successful save: 28 -> 14 -> 7. The "counts once"
    clause names Resistance, and a halving is not Resistance."""
    target = creature(hp=40)
    target.attach(UncannyDodge(target))
    dealt = save_for_half(
        target, {FIRE: 28}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 7


def test_the_halving_is_spent_across_a_mixed_type_hit_in_proportion() -> None:
    """20 fire and 10 cold halved to 15 total, apportioned 10 and 5, then the
    fire resistance halves its 10 to 5: 10 taken in all."""
    target = creature(hp=40)
    target.attach(FireResistant(target))
    dealt = save_for_half(
        target, {FIRE: 20, COLD: 10}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 10


def test_the_halving_precedes_a_vulnerability() -> None:
    """7 fire halved to 3, then doubled to 6. Halving after the vulnerability
    would be 14 // 2 = 7 — the step order, from the save's side."""
    target = creature(hp=40)
    target.attach(FireVulnerable(target))
    dealt = save_for_half(target, {FIRE: 7}, rng=no_dice(), outcome=SaveOutcome.SUCCESS)
    assert dealt == 6


def test_an_odd_mixed_hit_halves_the_total_and_not_each_part() -> None:
    """7 fire and 7 cold: 14 halved is 7, apportioned 4 cold and 3 fire, and the
    fire resistance takes its 3 to 1 — 5 in all.

    Halving each part first would be 3 and 3, resisted to 1 and 3, for 4. The
    even split in the case above cannot tell the two apart; this one can.
    """
    target = creature(hp=40)
    target.attach(FireResistant(target))
    dealt = save_for_half(
        target, {FIRE: 7, COLD: 7}, rng=no_dice(), outcome=SaveOutcome.SUCCESS
    )
    assert dealt == 5


def test_the_reason_reaches_the_ledger_before_the_victims_handlers_run() -> None:
    """A handler reads the halving the engine seeded, attributed to the engine."""
    target = creature(hp=40)
    watcher = LedgerWatcher(target)
    target.attach(watcher)
    save_for_half(
        target,
        {FIRE: 28},
        rng=no_dice(),
        outcome=SaveOutcome.SUCCESS,
        attacker=creature("caster"),
    )
    assert watcher.halvings == [(f"engine.damage: {HALVED_BY_SAVE}",)]
    assert watcher.attackers == ["caster"]


# --- What a handler may not do -----------------------------------------------


@pytest.mark.parametrize(
    ("source", "stage"),
    [
        (lambda c: VetoTheRoll(c), "on_saving_throw"),
        (lambda c: VetoTheResult(c), "on_save_resolved"),
    ],
)
def test_a_veto_at_either_of_the_savers_hooks_is_refused(
    source: object, stage: str
) -> None:
    """`SaveOutcome` has no member for a save that did not happen, so a veto
    raises rather than resolving to something arbitrary."""
    saver = creature()
    saver.attach(source(saver))  # type: ignore[operator]
    with pytest.raises(FeatureContractError, match=stage):
        saving_throw(saver, ability=DEX, dc=15, rng=ScriptedRng([13]))


def test_a_veto_at_the_imposers_hook_is_refused_too() -> None:
    caster = creature("caster")
    caster.attach(VetoTheDc(caster))
    with pytest.raises(FeatureContractError, match="on_forced_save"):
        force_save(caster, [creature("a")], ability=DEX, dc=15, rng=ScriptedRng([13]))


def test_an_effect_queued_by_a_save_hook_lands_on_the_saver() -> None:
    """The drain `engine/damage.py` and `engine/attack.py` already do, at the
    save's hooks (`creatures/effects.py`)."""
    saver = creature()
    saver.attach(Shaken(saver))
    saving_throw(saver, ability=DEX, dc=15, rng=ScriptedRng([3]))
    assert [effect.name for effect in saver.effects] == ["rattled"]


# --- The vocabulary the scorer and the turn loop share -----------------------


def test_the_repeat_save_timing_is_an_enum_and_not_a_string_literal() -> None:
    """Debt #35's declared half: `w2c` rolls the repeat save and `w3f`'s
    `Perturbation.repeat` names the timing, and both read these two members."""
    assert {timing.value for timing in SaveRepeat} == {"turn_end", "turn_start"}
    assert len(SaveRepeat) == 2


def test_a_save_has_two_outcomes_and_neither_is_critical() -> None:
    assert {outcome.value for outcome in SaveOutcome} == {"success", "failure"}
    assert not hasattr(SaveOutcome.SUCCESS, "is_critical")


# --- The fixtures ------------------------------------------------------------

LEGENDARY: Final = ResourceSpec(
    key="legendary_resistance",
    label="Legendary Resistance",
    maximum=3,
    recovery=(Recovery(RecoveryTrigger.LONG_REST),),
)


class LegendaryResistance(HookSource):
    """The acceptance test's feature: a `w1b` pool and one handler, with nothing
    in `engine/saves.py` that knows it exists."""

    __slots__ = ("ledger",)

    ID = "test.legendary_resistance"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        # Captured at construction because `Combatant` carries no resources and
        # `self.owner` is one (`docs/decisions.md` § w1a, § w1g).
        self.ledger = owner.resources

    @hook("on_save_resolved")
    def insist(self, ctx: SaveResolvedContext) -> R:
        if ctx.succeeded or not self.ledger.can_spend(LEGENDARY.key):
            return R.SKIPPED
        self.ledger.spend(LEGENDARY.key)
        ctx.succeed("Legendary Resistance")
        return R.APPLIED


class DangerSense(HookSource):
    """Advantage on a Dexterity save, the shape the Barbarian's has."""

    ID = "test.danger_sense"

    @hook("on_saving_throw")
    def alert(self, ctx: SavingThrowContext) -> R:
        ctx.grant_advantage("Danger Sense")
        return R.APPLIED


class Frightening(HookSource):
    """Disadvantage on the save, the shape a condition imposes."""

    ID = "test.frightening"

    @hook("on_saving_throw")
    def cow(self, ctx: SavingThrowContext) -> R:
        ctx.impose_disadvantage("Frightened")
        return R.APPLIED


class Aura(HookSource):
    """A flat bonus and a die, the shape Aura of Protection plus Bless has."""

    ID = "test.aura"

    @hook("on_saving_throw")
    def bless(self, ctx: SavingThrowContext) -> R:
        ctx.bonus.add(flat=3)
        ctx.bonus.add(dice="1d4")
        return R.APPLIED


class Focused(HookSource):
    """A bonus to the DC, the imposer's side of the pair."""

    ID = "test.focused"

    @hook("on_forced_save")
    def sharpen(self, ctx: ForcedSaveContext) -> R:
        ctx.dc_bonus.add(flat=2)
        return R.APPLIED


class Watcher(HookSource):
    """Records who imposed each save this creature rolled."""

    __slots__ = ("imposers",)

    ID = "test.watcher"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.imposers: list[Creature | None] = []

    @hook("on_saving_throw")
    def note(self, ctx: SavingThrowContext) -> R:
        self.imposers.append(ctx.imposed_by)  # type: ignore[arg-type]
        return R.APPLIED


class DcWatcher(HookSource):
    """Records how often the imposer's hook fired, and over which targets."""

    __slots__ = ("calls", "targets")

    ID = "test.dc_watcher"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.calls = 0
        self.targets: tuple[str, ...] = ()

    @hook("on_forced_save")
    def note(self, ctx: ForcedSaveContext) -> R:
        self.calls += 1
        self.targets = tuple(target.name for target in ctx.targets)
        return R.APPLIED


class LedgerWatcher(HookSource):
    """Records the halvings already in the ledger when the victim's hook runs,
    and who the damage came from."""

    __slots__ = ("attackers", "halvings")

    ID = "test.ledger_watcher"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.halvings: list[tuple[str, ...]] = []
        self.attackers: list[str | None] = []

    @hook("on_incoming_damage")
    def note(self, ctx: IncomingDamageContext) -> R:
        self.halvings.append(ctx.mitigation.halvings)
        self.attackers.append(None if ctx.attacker is None else ctx.attacker.name)
        return R.APPLIED


class HeavyArmour(HookSource):
    """A flat reduction, the shape Heavy Armor Master has."""

    ID = "test.heavy_armour"

    @hook("on_incoming_damage")
    def reduce(self, ctx: IncomingDamageContext) -> R:
        ctx.reduce(flat=10)
        return R.APPLIED


class FireImmune(HookSource):
    ID = "test.fire_immunity"

    @hook("on_incoming_damage")
    def immune(self, ctx: IncomingDamageContext) -> R:
        ctx.add_immunity(FIRE)
        return R.APPLIED


class FireResistant(HookSource):
    ID = "test.fire_resistance"

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        ctx.add_resistance(FIRE)
        return R.APPLIED


class FireVulnerable(HookSource):
    ID = "test.fire_vulnerability"

    @hook("on_incoming_damage")
    def expose(self, ctx: IncomingDamageContext) -> R:
        ctx.add_vulnerability(FIRE)
        return R.APPLIED


class Rallied(HookSource):
    """A die added to the DC, the shape a bardic bonus has."""

    ID = "test.rallied"

    @hook("on_forced_save")
    def inspire(self, ctx: ForcedSaveContext) -> R:
        ctx.dc_bonus.add(dice="1d4")
        return R.APPLIED


class UncannyDodge(HookSource):
    """A second halving, from the victim's side."""

    ID = "test.uncanny_dodge"

    @hook("on_incoming_damage")
    def dodge(self, ctx: IncomingDamageContext) -> R:
        ctx.halve("Uncanny Dodge")
        return R.APPLIED


class Rattled(Effect):
    """The narrowest effect a save hook can queue."""

    ID = "rattled"


class Shaken(HookSource):
    """Queues an effect from `on_save_resolved`, which is what a failed save
    against a control spell does."""

    ID = "test.shaken"

    @hook("on_save_resolved")
    def rattle(self, ctx: SaveResolvedContext) -> R:
        if ctx.succeeded:
            return R.SKIPPED
        ctx.apply_effect(self.owner, Rattled(self.owner))
        return R.APPLIED


class VetoTheRoll(HookSource):
    ID = "test.veto_the_roll"

    @hook("on_saving_throw", phase=Phase.EARLY)
    def refuse(self, ctx: SavingThrowContext) -> R:
        ctx.veto("nothing may stop a save")
        return R.APPLIED


class VetoTheResult(HookSource):
    ID = "test.veto_the_result"

    @hook("on_save_resolved", phase=Phase.EARLY)
    def refuse(self, ctx: SaveResolvedContext) -> R:
        ctx.veto("nothing may stop a save")
        return R.APPLIED


class VetoTheDc(HookSource):
    ID = "test.veto_the_dc"

    @hook("on_forced_save", phase=Phase.EARLY)
    def refuse(self, ctx: ForcedSaveContext) -> R:
        ctx.veto("nothing may stop a save")
        return R.APPLIED
