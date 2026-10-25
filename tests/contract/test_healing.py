"""The healing contract: regaining hit points, waking up, temporary hit points.

Every case carries its arithmetic. The creature every test heals has 20 hit
points and is a player character, so dropping it to 0 leaves it Unconscious and
dying rather than dead.

Sources: XPHB (2024) "Damage and Healing" -> Healing; "Dropping to 0 Hit
Points" -> Falling Unconscious, Death Saving Throws, Death; Rules Glossary
"Temporary Hit Points", "Unconscious"; ROADMAP w1j.
"""

from types import MappingProxyType
from typing import Final

import pytest

from dndsim.conditions.prone import Prone
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.errors import FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock
from dndsim.engine.damage import resolve_damage
from dndsim.engine.dying import death_save
from dndsim.engine.healing import Downed, grant_temp_hp, heal
from dndsim.hooks.contexts import HealContext, TempHpContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))
SLASHING: Final = DamageType.SLASHING
MAX_HP: Final = 20


def pc(name: str = "pc") -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=MAX_HP),
        position=Cell(0, 0),
        player_character=True,
    )


def downed() -> Creature:
    """A player character dropped by damage: 0 hit points and Unconscious."""
    subject = pc()
    resolve_damage(subject, {SLASHING: MAX_HP}, rng=no_dice())
    assert subject.hp == 0
    return subject


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled."""
    return ScriptedRng([])


def held(subject: Creature) -> list[type]:
    return [type(effect) for effect in subject.effects]


def probed(subject: Creature) -> "Probe":
    probe = Probe(subject)
    subject.attach(probe)
    return probe


# --- Regaining hit points ----------------------------------------------------


def test_healing_adds_the_dice_and_the_flat() -> None:
    """2d8 rolling 3 and 5, + 4 = 12, on a creature at 5: 17."""
    patient = pc()
    patient.hp = 5
    rng = ScriptedRng([3, 5])
    assert heal(patient, rng=rng, dice="2d8", flat=4) == 12
    assert patient.hp == 17
    assert rng.exhausted


def test_healing_cannot_exceed_the_maximum() -> None:
    """15 of 20, healed 10: 5 regained, not 10."""
    patient = pc()
    patient.hp = 15
    assert heal(patient, rng=no_dice(), flat=10) == 5
    assert patient.hp == MAX_HP


def test_healing_at_full_regains_nothing() -> None:
    patient = pc()
    assert heal(patient, rng=no_dice(), flat=10) == 0
    assert patient.hp == MAX_HP


def test_healing_does_not_double_on_anything() -> None:
    """The same 2d8 is two dice, whatever the caller rolled before."""
    patient = pc()
    patient.hp = 1
    rng = ScriptedRng([8, 8])
    assert heal(patient, rng=rng, dice="2d8") == 16
    assert rng.exhausted


def test_on_heal_fires_on_the_recipient_with_the_healer_named() -> None:
    patient, cleric = pc(), pc("cleric")
    patient.hp = 1
    on_patient, on_cleric = probed(patient), probed(cleric)
    heal(patient, rng=no_dice(), flat=3, healer=cleric)
    assert on_patient.heals == [(patient, cleric)]
    assert on_cleric.heals == []


def test_a_handler_can_add_to_the_healing() -> None:
    """3 from the caller and 2 from the recipient's handler: 5."""
    patient = pc()
    patient.hp = 1
    patient.attach(Blessed(patient))
    assert heal(patient, rng=no_dice(), flat=3) == 5
    assert patient.hp == 6


def test_a_veto_at_on_heal_means_no_hit_points_are_regained() -> None:
    """ "can't regain Hit Points": honoured, not refused."""
    patient = pc()
    patient.hp = 4
    patient.attach(Withered(patient))
    assert heal(patient, rng=no_dice(), flat=10) == 0
    assert patient.hp == 4


def test_a_veto_at_on_heal_leaves_a_downed_creature_unconscious() -> None:
    patient = downed()
    patient.attach(Withered(patient))
    assert heal(patient, rng=no_dice(), flat=10) == 0
    assert patient.hp == 0
    assert held(patient) == [Downed]


def test_a_dead_creature_regains_nothing_and_fires_nothing() -> None:
    patient = pc()
    resolve_damage(patient, {SLASHING: 2 * MAX_HP}, rng=no_dice())
    assert patient.dead
    probe = probed(patient)
    assert heal(patient, rng=no_dice(), flat=10) == 0
    assert patient.hp == 0
    assert patient.dead
    assert probe.heals == []


# --- Waking up ---------------------------------------------------------------


def test_healing_at_zero_ends_unconscious_and_leaves_the_creature_prone() -> None:
    """The row: a heal at 0 restores consciousness, and Prone remains through
    Unconscious's own "When this condition ends, you remain Prone"."""
    patient = downed()
    assert heal(patient, rng=no_dice(), flat=3) == 3
    assert patient.hp == 3
    assert held(patient) == [Prone]
    assert patient.is_alive and not patient.is_down


def test_healing_at_zero_resets_both_death_save_counters() -> None:
    patient = downed()
    death_save(patient, rng=ScriptedRng([15]))
    death_save(patient, rng=ScriptedRng([4]))
    death_save(patient, rng=ScriptedRng([5]))
    assert (patient.death_save_successes, patient.death_save_failures) == (1, 2)
    heal(patient, rng=no_dice(), flat=1)
    assert (patient.death_save_successes, patient.death_save_failures) == (0, 0)


def test_healing_a_stable_creature_wakes_it_and_ends_stable() -> None:
    patient = downed()
    patient.stabilize()
    heal(patient, rng=no_dice(), flat=1)
    assert not patient.stable
    assert held(patient) == [Prone]


def test_a_healed_creature_that_drops_again_starts_its_saves_afresh() -> None:
    """Two failures, healed, dropped again: one failure from damage at 0 is
    one, not three."""
    patient = downed()
    patient.fail_death_save(2)
    heal(patient, rng=no_dice(), flat=1)
    resolve_damage(patient, {SLASHING: 1}, rng=no_dice())
    resolve_damage(patient, {SLASHING: 1}, rng=no_dice())
    assert patient.death_save_failures == 1
    assert not patient.dead


def test_a_heal_ends_the_drops_unconscious_and_not_a_spells() -> None:
    """Regaining hit points ends the Unconscious dropping to 0 gave; one a
    spell gave ends on the spell's terms, so the creature is still out cold.
    The Prone the drop's Unconscious leaves behind is beside it."""
    patient = pc()
    spell = patient.effects.apply(Unconscious(patient))
    resolve_damage(patient, {SLASHING: MAX_HP}, rng=no_dice())
    heal(patient, rng=no_dice(), flat=3)
    assert patient.hp == 3
    assert spell in list(patient.effects)
    assert Downed not in held(patient)
    assert held(patient) == [Unconscious, Prone]


# --- Temporary hit points ----------------------------------------------------


def test_temporary_hit_points_are_granted() -> None:
    patient = pc()
    probe = probed(patient)
    assert grant_temp_hp(patient, 5) == 5
    assert patient.temp_hp == 5
    assert probe.temps == [(5, 0)]


def test_a_larger_pool_replaces_a_smaller_one() -> None:
    """5, then 8: 8, not 13."""
    patient = pc()
    grant_temp_hp(patient, 5)
    probe = probed(patient)
    assert grant_temp_hp(patient, 8) == 8
    assert patient.temp_hp == 8
    assert probe.temps == [(8, 5)]


def test_a_smaller_pool_is_not_kept_and_fires_nothing() -> None:
    """8, then 5: still 8, not 13 and not 5."""
    patient = pc()
    grant_temp_hp(patient, 8)
    probe = probed(patient)
    assert grant_temp_hp(patient, 5) == 8
    assert patient.temp_hp == 8
    assert probe.temps == []


def test_an_equal_pool_fires_nothing() -> None:
    patient = pc()
    grant_temp_hp(patient, 5)
    probe = probed(patient)
    assert grant_temp_hp(patient, 5) == 5
    assert probe.temps == []


def test_a_negative_grant_is_refused() -> None:
    with pytest.raises(ValueError):
        grant_temp_hp(pc(), -1)


def test_temporary_hit_points_do_not_wake_a_creature_at_zero() -> None:
    """ "receiving Temporary Hit Points doesn't restore you to consciousness or
    stabilize you": still at 0, Unconscious, and still rolling."""
    patient = downed()
    grant_temp_hp(patient, 5)
    assert patient.hp == 0
    assert patient.temp_hp == 5
    assert held(patient) == [Downed]
    assert not patient.stable
    death_save(patient, rng=ScriptedRng([12]))
    assert patient.death_save_successes == 1


def test_a_veto_at_on_temp_hp_gained_is_refused() -> None:
    patient = pc()
    patient.attach(Withered(patient))
    with pytest.raises(FeatureContractError):
        grant_temp_hp(patient, 5)


# --- The sources the cases attach --------------------------------------------


class Probe(HookSource):
    """What the healing hooks handed this creature, in order."""

    __slots__ = ("heals", "temps")

    ID = "test.probe"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.heals: list[tuple[object, object]] = []
        self.temps: list[tuple[int, int]] = []

    @hook("on_heal")
    def healed(self, ctx: HealContext) -> R:
        self.heals.append((ctx.creature, ctx.healer))
        return R.APPLIED

    @hook("on_temp_hp_gained")
    def padded(self, ctx: TempHpContext) -> R:
        self.temps.append((ctx.amount, ctx.replaced))
        return R.APPLIED


class Blessed(HookSource):
    """+2 to every heal its owner receives."""

    ID = "test.blessed"

    @hook("on_heal")
    def more(self, ctx: HealContext) -> R:
        ctx.healing.add(flat=2)
        return R.APPLIED


class Withered(HookSource):
    """An EARLY veto: "can't regain Hit Points", and the same veto where the
    engine refuses one."""

    ID = "test.withered"

    @hook("on_heal", phase=Phase.EARLY)
    def no_healing(self, ctx: HealContext) -> R:
        ctx.veto("withered")
        return R.APPLIED

    @hook("on_temp_hp_gained", phase=Phase.EARLY)
    def no_padding(self, ctx: TempHpContext) -> R:
        ctx.veto("withered")
        return R.APPLIED
