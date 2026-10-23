"""The w1b resource contract: the seven shapes, and the cost that commits whole.

Two properties carry the task, and the rest of the file is arithmetic around
them:

- **The seven shapes of DESIGN.md § Resources are one model.** Rage, the nine
  spell slots, Pact Magic, ki, Channel Divinity, sorcery points and a monster's
  recharge differ only in what their `ResourceSpec` says. If any of them needed
  a branch in `ResourcePool`, the model would have failed at seven and the ~630
  content implementations are the ones that would pay for it.
- **A half-paid ability cannot exist.** Quickened Fireball spends two sorcery
  points *and* a 3rd-level slot; the transaction commits both or rolls back
  both, even when what raises is a later step that knows nothing about either
  (DESIGN.md § Resources).

Underneath sits the same refusal `tests/contract/test_economy.py` pins for the
action economy: overdrawing raises rather than clamping, because asking is free.
What is *not* an error is a slot that is merely unavailable — `core/errors.py`
names that a fight outcome — so every refusal below has a `can_spend()` that
would have answered it for nothing.

Sources: DESIGN.md § Resources: one model, no special cases, § Failing loudly —
layered; XPHB (2024) "Spells" (Spell Slots, Pact Magic); ROADMAP w1b.
"""

from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.dice import Dice
from dndsim.core.errors import EconomyError, FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.creatures.resources import (
    ALL,
    Recovery,
    ResourcePool,
    Resources,
    ResourceSpec,
    slot_key,
    spell_slot_specs,
)
from dndsim.creatures.stats import StatBlock
from dndsim.hooks.contexts import Combatant, ResourceSpentContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import spell_slots
from dndsim.rules.enums import Ability, CasterProgression, RecoveryTrigger

SCORES: Final = MappingProxyType(dict.fromkeys(Ability, 10))

SHORT_REST: Final = RecoveryTrigger.SHORT_REST
LONG_REST: Final = RecoveryTrigger.LONG_REST
RECHARGE: Final = RecoveryTrigger.RECHARGE_5_6

FULL: Final = CasterProgression.FULL
PACT: Final = CasterProgression.PACT


def creature(name: str = "hero", *, level: int = 5) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=40, level=level),
        position=Cell(0, 0),
    )


# --- The seven shapes -------------------------------------------------------
#
# DESIGN.md § Resources tabulates these; what the table below adds is that each
# one is a `ResourceSpec` and nothing else.

_RAGES: Final = (2, 2, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6)
"""The XPHB (2024) Barbarian "Rages" column, levels 1-20.

A fixture, not the feature. The real Rage reads this off the imported
`classTableGroups` through `table_value()` (ROADMAP § Standing rules, 3), and
neither exists in W1 — so it is written out rather than fitted to a formula,
because a formula that happens to agree up to level 11 is what a later agent
would copy.
"""

RAGE: Final = ResourceSpec(
    key="rage",
    label="Rage",
    maximum=lambda stats: _RAGES[stats.level - 1],
    recovery=(Recovery(SHORT_REST, 1), Recovery(LONG_REST, ALL)),
)
KI: Final = ResourceSpec(
    key="ki",
    label="Focus Point",
    maximum=lambda stats: stats.level,
    recovery=(Recovery(SHORT_REST, ALL),),
    die=Dice.parse("1d8"),
)
CHANNEL_DIVINITY: Final = ResourceSpec(
    key="channel_divinity",
    label="Channel Divinity use",
    maximum=2,
    recovery=(Recovery(SHORT_REST, 1), Recovery(LONG_REST, ALL)),
)
SORCERY_POINTS: Final = ResourceSpec(
    key="sorcery_point",
    label="Sorcery Point",
    maximum=lambda stats: stats.level,
    recovery=(Recovery(LONG_REST, ALL),),
)
BREATH_WEAPON: Final = ResourceSpec(
    key="breath_weapon",
    label="Breath Weapon use",
    maximum=1,
    recovery=(Recovery(RECHARGE, ALL),),
)


def test_the_seven_shapes_are_one_model() -> None:
    """Every shape in DESIGN.md § Resources is a spec; none is a branch."""
    hero = creature(level=6)
    hero.resources.add(
        RAGE,
        KI,
        CHANNEL_DIVINITY,
        SORCERY_POINTS,
        BREATH_WEAPON,
        *spell_slot_specs(FULL),
        *spell_slot_specs(PACT),
    )
    # 5 named pools + nine slot levels twice.
    assert len(hero.resources) == 23
    assert hero.resources["rage"].maximum == 4  # 4 rages at level 6
    assert hero.resources["ki"].maximum == 6
    assert hero.resources["channel_divinity"].maximum == 2
    assert hero.resources["sorcery_point"].maximum == 6
    assert hero.resources["breath_weapon"].maximum == 1
    assert hero.resources[slot_key(FULL, 3)].maximum == spell_slots(FULL, 6)[3]
    assert hero.resources[slot_key(PACT, 3)].maximum == spell_slots(PACT, 6)[3]
    assert all(pool.current == pool.maximum for pool in hero.resources)


def test_a_spec_is_a_flyweight_shared_by_every_creature_that_has_it() -> None:
    """One spec object, two creatures, two different maxima off one callable."""
    novice, veteran = creature("novice", level=3), creature("veteran", level=12)
    novice.resources.add(RAGE)
    veteran.resources.add(RAGE)
    assert novice.resources["rage"].spec is veteran.resources["rage"].spec is RAGE
    assert novice.resources["rage"].maximum == 3
    assert veteran.resources["rage"].maximum == 5
    assert spell_slot_specs(FULL) is spell_slot_specs(FULL)


def test_a_die_rides_on_the_spec_and_nothing_in_the_pool_reads_it() -> None:
    """Superiority dice and Focus Points are counted, not rolled, here."""
    hero = creature()
    hero.resources.add(KI)
    assert hero.resources["ki"].spec.die == Dice.parse("1d8")
    assert RAGE.die is None


# --- Spending, and the refusal --------------------------------------------


def test_spending_draws_down_the_pool() -> None:
    hero = creature(level=6)
    hero.resources.add(RAGE)
    hero.resources.spend("rage")
    assert hero.resources["rage"].current == 3
    hero.resources.spend("rage", 2)
    assert hero.resources["rage"].current == 1


def test_overdrawing_raises_and_asking_is_free() -> None:
    hero = creature(level=3)
    hero.resources.add(RAGE)  # 3 rages at level 3
    assert hero.resources.can_spend("rage", 3)
    assert not hero.resources.can_spend("rage", 4)
    with pytest.raises(EconomyError, match="may not go negative"):
        hero.resources.spend("rage", 4)
    assert hero.resources["rage"].current == 3, "a refused spend spends nothing"


def test_a_resource_the_build_never_granted_is_refused_by_name() -> None:
    hero = creature()
    assert not hero.resources.can_spend("rage")
    assert "rage" not in hero.resources
    with pytest.raises(EconomyError, match="no rage to spend"):
        hero.resources.spend("rage")


def test_one_key_is_one_pool() -> None:
    hero = creature()
    hero.resources.add(RAGE)
    with pytest.raises(EconomyError, match="already a pool"):
        hero.resources.add(RAGE)


def test_spending_nothing_is_not_a_spend() -> None:
    hero = creature()
    hero.resources.add(RAGE)
    assert not hero.resources.can_spend("rage", 0)
    with pytest.raises(EconomyError, match="not a spend"):
        hero.resources.spend("rage", 0)
    with pytest.raises(EconomyError, match="not a spend"):
        hero.resources.spend("rage", -1)
    assert hero.resources["rage"].current == RAGE.maximum_for(hero.stats)


def test_what_is_left_cannot_be_assigned() -> None:
    hero = creature()
    hero.resources.add(RAGE)
    with pytest.raises(AttributeError):
        hero.resources["rage"].current = 99  # type: ignore[misc]


# --- Recovery ---------------------------------------------------------------


def test_each_trigger_recovers_only_what_declares_it() -> None:
    """Rage's two entries are the table's own: one back on a short rest, all on
    a long one (DESIGN.md § Resources)."""
    hero = creature(level=6)
    hero.resources.add(RAGE)
    hero.resources.spend("rage", 4)
    assert hero.resources["rage"].current == 0
    hero.resources.recover(SHORT_REST)
    assert hero.resources["rage"].current == 1
    hero.resources.recover(LONG_REST)
    assert hero.resources["rage"].current == 4


def test_recovery_never_overfills() -> None:
    hero = creature(level=6)
    hero.resources.add(RAGE)
    hero.resources.spend("rage")
    hero.resources.recover(LONG_REST)
    hero.resources.recover(LONG_REST)
    assert hero.resources["rage"].current == 4


def test_a_trigger_nothing_declares_changes_nothing() -> None:
    hero = creature()
    hero.resources.add(SORCERY_POINTS)
    hero.resources.spend("sorcery_point", 3)
    before = hero.resources["sorcery_point"].current
    hero.resources.recover(SHORT_REST)
    assert hero.resources["sorcery_point"].current == before


def test_a_recharge_is_a_trigger_and_never_a_roll() -> None:
    """Whoever rolled the d6 fires it; no pool reaches for a generator."""
    ogre = creature("dragon")
    ogre.resources.add(BREATH_WEAPON)
    ogre.resources.spend("breath_weapon")
    assert ogre.resources["breath_weapon"].current == 0
    ogre.resources.recover(RECHARGE)
    assert ogre.resources["breath_weapon"].current == 1


def test_a_recovery_that_recovers_nothing_is_refused_at_declaration() -> None:
    with pytest.raises(ValueError, match="recovers nothing"):
        Recovery(LONG_REST, 0)


def test_a_pool_reports_what_a_trigger_gave_back() -> None:
    hero = creature(level=6)
    pool = ResourcePool(RAGE, hero.stats)
    assert pool.recover(LONG_REST) == 0
    pool._take(pool.maximum - 1)
    assert pool.recover(SHORT_REST) == 1
    assert pool.recover(LONG_REST) == 2


# --- The spell-slot facade --------------------------------------------------


def test_the_facade_answers_over_every_level_tagged_pool() -> None:
    """A level-5 full caster: 4/3/2 (XPHB, the spellcasting table)."""
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    slots = wizard.resources.spells
    assert slots.count(1) == 4
    assert slots.count(2) == 3
    assert slots.count(3) == 2
    assert slots.count(4) == 0
    assert slots.highest() == 3
    assert slots.total_above(2) == 5
    assert slots.total_above(1) == 9


def test_pact_magic_answers_the_same_four_questions() -> None:
    """A level-5 warlock has two 3rd-level Pact slots, back on a short rest."""
    warlock = creature("warlock", level=5)
    warlock.resources.add(*spell_slot_specs(PACT))
    slots = warlock.resources.spells
    assert slots.count(3) == 2
    assert slots.highest() == 3
    assert slots.spend(3) == 3
    assert slots.count(3) == 1
    warlock.resources.recover(SHORT_REST)
    assert slots.count(3) == 2


def test_a_creature_with_both_progressions_keeps_two_pools_at_one_level() -> None:
    """The facade sums them; the keys keep them apart, so a short rest gives
    back the Pact slot and not the wizard's."""
    hybrid = creature("hybrid", level=5)
    hybrid.resources.add(*spell_slot_specs(FULL), *spell_slot_specs(PACT))
    slots = hybrid.resources.spells
    assert slots.count(3) == 4  # two of each
    hybrid.resources.spend(slot_key(PACT, 3))
    hybrid.resources.spend(slot_key(FULL, 3))
    assert slots.count(3) == 2
    hybrid.resources.recover(SHORT_REST)
    assert hybrid.resources[slot_key(PACT, 3)].current == 2
    assert hybrid.resources[slot_key(FULL, 3)].current == 1


def test_a_slot_is_spent_at_the_level_asked_for() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    assert wizard.resources.spells.spend(1) == 1
    assert wizard.resources.spells.count(1) == 3
    assert wizard.resources.spells.count(2) == 3, "no other level moved"


def test_upcasting_takes_the_lowest_slot_that_carries_the_spell() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    wizard.resources.spend(slot_key(FULL, 1), 4)
    slots = wizard.resources.spells
    assert slots.count(1) == 0
    assert slots.spend(1, allow_upcast=True) == 2
    assert slots.count(2) == 2
    assert slots.count(3) == 2, "the 3rd-level slots were not reached for"


def test_without_upcast_an_empty_level_is_a_refusal_even_with_slots_above() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    wizard.resources.spend(slot_key(FULL, 1), 4)
    slots = wizard.resources.spells
    assert slots.total_above(1) == 5
    with pytest.raises(EconomyError, match="not asked to upcast"):
        slots.spend(1)


def test_a_caster_out_of_slots_reports_zero_rather_than_raising() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    for level in (1, 2, 3):
        wizard.resources.spend(slot_key(FULL, level), spell_slots(FULL, 5)[level])
    slots = wizard.resources.spells
    assert slots.highest() == 0
    assert slots.total_above(1) == 0
    with pytest.raises(EconomyError, match="no level-1 spell slot left"):
        slots.spend(1, allow_upcast=True)


def test_a_level_outside_the_table_is_refused() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    with pytest.raises(EconomyError, match="no level-10 spell slot"):
        wizard.resources.spells.spend(10)


def test_levels_a_progression_never_reaches_are_pools_of_zero() -> None:
    """So levelling up needs no pool added, only a bigger maximum."""
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    assert wizard.resources[slot_key(FULL, 9)].maximum == 0
    assert not wizard.resources.can_spend(slot_key(FULL, 9))


def test_select_names_the_pool_without_spending_it() -> None:
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    pool = wizard.resources.spells.select(1, allow_upcast=True)
    assert pool.key == slot_key(FULL, 1)
    assert wizard.resources.spells.count(1) == 4


# --- The unit of work -------------------------------------------------------


def test_a_multi_resource_cost_commits_whole() -> None:
    sorcerer = creature("sorcerer", level=5)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with sorcerer.resources.transaction() as txn:
        txn.spend("sorcery_point", 2)
        txn.spend(sorcerer.resources.spells.select(3).key)
    assert sorcerer.resources["sorcery_point"].current == 3
    assert sorcerer.resources.spells.count(3) == 1


def test_a_later_step_raising_rolls_both_back() -> None:
    """The property the Unit of Work exists for: nothing is half paid."""
    sorcerer = creature("sorcerer", level=5)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with (
        pytest.raises(RuntimeError, match="the spell fizzled"),
        sorcerer.resources.transaction() as txn,
    ):
        txn.spend("sorcery_point", 2)
        txn.spend(slot_key(FULL, 3))
        raise RuntimeError("the spell fizzled")
    assert sorcerer.resources["sorcery_point"].current == 5
    assert sorcerer.resources.spells.count(3) == 2


def test_the_refusal_inside_a_transaction_rolls_the_rest_back() -> None:
    """The second cost is the one that cannot be paid, so the first is not."""
    sorcerer = creature("sorcerer", level=5)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with (
        pytest.raises(EconomyError, match="may not go negative"),
        sorcerer.resources.transaction() as txn,
    ):
        txn.spend("sorcery_point", 2)
        txn.spend(slot_key(FULL, 9))
    assert sorcerer.resources["sorcery_point"].current == 5


def test_a_transaction_sees_what_it_has_already_taken() -> None:
    """Two 3rd-level slots asked of a pool holding two is fine; a third is not,
    and a ledger that only added up at the commit would let it through."""
    wizard = creature("wizard", level=5)
    wizard.resources.add(*spell_slot_specs(FULL))
    with pytest.raises(EconomyError), wizard.resources.transaction() as txn:
        txn.spend(slot_key(FULL, 3))
        txn.spend(slot_key(FULL, 3))
        txn.spend(slot_key(FULL, 3))
    assert wizard.resources.spells.count(3) == 2


def test_a_direct_spend_while_a_transaction_is_open_is_refused() -> None:
    """Otherwise it announces at once and the rollback cannot reach it, so an
    exception leaves it spent beside costs that came back."""
    sorcerer = creature("sorcerer", level=5)
    counter = Counter(sorcerer)
    sorcerer.attach(counter)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with (
        pytest.raises(EconomyError, match="while a transaction is open"),
        sorcerer.resources.transaction() as txn,
    ):
        txn.spend("sorcery_point", 2)
        sorcerer.resources.spend(slot_key(FULL, 3))
    assert sorcerer.resources["sorcery_point"].current == 5
    assert sorcerer.resources.spells.count(3) == 2
    assert counter.seen == []


def test_the_facade_routes_through_the_same_refusal() -> None:
    """`SpellSlots.spend()` is `Resources.spend()`, so it is refused with it —
    and `select()` is the seam that gets the slot into the transaction."""
    sorcerer = creature("sorcerer", level=5)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with (
        pytest.raises(EconomyError, match="while a transaction is open"),
        sorcerer.resources.transaction() as txn,
    ):
        txn.spend("sorcery_point", 2)
        sorcerer.resources.spells.spend(3)
    assert sorcerer.resources.spells.count(3) == 2
    with sorcerer.resources.transaction() as txn:
        txn.spend("sorcery_point", 2)
        txn.spend(sorcerer.resources.spells.select(3).key)
    assert sorcerer.resources.spells.count(3) == 1


def test_a_transaction_outside_a_with_statement_is_refused() -> None:
    """Nothing would commit it, so `on_resource_spent` would never fire."""
    hero = creature()
    hero.resources.add(RAGE)
    txn = hero.resources.transaction()
    with pytest.raises(EconomyError, match="has not been entered"):
        txn.spend("rage")


def test_a_nested_transaction_is_refused() -> None:
    hero = creature()
    hero.resources.add(RAGE, SORCERY_POINTS)
    with hero.resources.transaction() as txn:
        txn.spend("rage")
        with (
            pytest.raises(EconomyError, match="already inside"),
            hero.resources.transaction(),
        ):
            pass
    assert hero.resources["rage"].current == RAGE.maximum_for(hero.stats) - 1


def test_a_rolled_back_transaction_leaves_the_ledger_open() -> None:
    hero = creature()
    hero.resources.add(RAGE)
    with pytest.raises(RuntimeError), hero.resources.transaction() as txn:
        txn.spend("rage")
        raise RuntimeError
    with hero.resources.transaction() as txn:
        txn.spend("rage")
    assert hero.resources["rage"].current == RAGE.maximum_for(hero.stats) - 1


# --- `on_resource_spent` ----------------------------------------------------


class Counter(HookSource):
    """Records every spend it is told about."""

    __slots__ = ("seen",)

    ID = "test.counter"

    def __init__(self, owner: Combatant) -> None:
        super().__init__(owner)
        self.seen: list[tuple[str, int]] = []

    @hook("on_resource_spent")
    def note(self, ctx: ResourceSpentContext) -> R:
        self.seen.append((ctx.resource, ctx.amount))
        return R.APPLIED


class Refuser(HookSource):
    """A handler that tries to veto a spend that has already happened."""

    __slots__ = ()

    ID = "test.refuser"

    @hook("on_resource_spent", phase=Phase.EARLY)
    def refuse(self, ctx: ResourceSpentContext) -> R:
        ctx.veto("not on my watch")
        return R.APPLIED


class Exhaustion(Effect):
    """Something a handler queues when its owner spends."""

    __slots__ = ()

    ID = "test.exhaustion"


class Tiring(HookSource):
    """Queues an effect on the spender, to prove the drain runs here too."""

    __slots__ = ()

    ID = "test.tiring"

    @hook("on_resource_spent")
    def tire(self, ctx: ResourceSpentContext) -> R:
        ctx.apply_effect(ctx.creature, Exhaustion(ctx.creature))
        return R.APPLIED


def test_a_spend_is_announced_to_its_owner() -> None:
    hero = creature()
    counter = Counter(hero)
    hero.attach(counter)
    hero.resources.add(RAGE)
    hero.resources.spend("rage", 2)
    assert counter.seen == [("rage", 2)]


def test_a_committed_transaction_announces_once_per_pool() -> None:
    """One dispatch per resource carrying the committed total, not one per
    `txn.spend()` call."""
    sorcerer = creature("sorcerer", level=5)
    counter = Counter(sorcerer)
    sorcerer.attach(counter)
    sorcerer.resources.add(SORCERY_POINTS, *spell_slot_specs(FULL))
    with sorcerer.resources.transaction() as txn:
        txn.spend("sorcery_point")
        txn.spend("sorcery_point")
        txn.spend(slot_key(FULL, 3))
    assert counter.seen == [("sorcery_point", 2), (slot_key(FULL, 3), 1)]


def test_a_rolled_back_spend_is_announced_to_nobody() -> None:
    """It did not happen, and a handler told about an event that did not take
    place is the same wrong number as one not told about one that did
    (docs/decisions.md § w1c)."""
    sorcerer = creature("sorcerer", level=5)
    counter = Counter(sorcerer)
    sorcerer.attach(counter)
    sorcerer.resources.add(SORCERY_POINTS)
    with pytest.raises(RuntimeError), sorcerer.resources.transaction() as txn:
        txn.spend("sorcery_point", 2)
        raise RuntimeError
    assert counter.seen == []


def test_vetoing_a_spend_that_has_happened_is_refused() -> None:
    hero = creature()
    hero.attach(Refuser(hero))
    hero.resources.add(RAGE)
    with pytest.raises(FeatureContractError, match="already gone"):
        hero.resources.spend("rage")


def test_an_effect_queued_by_a_spend_handler_lands() -> None:
    hero = creature()
    hero.attach(Tiring(hero))
    hero.resources.add(RAGE)
    hero.resources.spend("rage")
    assert [effect.ID for effect in hero.effects] == ["test.exhaustion"]


def test_a_creature_starts_with_no_resources_at_all() -> None:
    """What a creature has is what its build granted it; an empty ledger is not
    an empty pool, and the two refusals say different things."""
    hero = creature()
    assert len(hero.resources) == 0
    assert isinstance(hero.resources, Resources)
