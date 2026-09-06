"""The w0f contract: one context per hook, and no way in but a mutator.

Four properties carry this file, and each of them is a bug that would otherwise
be found by a feature author months later, in a number that is merely wrong:

- **The registry and this module agree.** A `HookSpec` names its context as a
  string because `hooks/contexts.py` sits above the registry and imports it
  (w0e). That string is checked here, in both directions, so a hook cannot name
  a context nobody wrote and a context cannot exist that no hook hands out.
- **Assignment is refused.** `ctx.damage = ...` is how one feature silently
  deletes another's contribution, and the whole parallel-authoring model rests
  on it being impossible rather than discouraged (DESIGN.md § Contexts are
  attribute-locked).
- **A finished context is finished.** Contexts are pooled, so a handler that
  stashes one and writes to it later is writing to somebody else's attack. The
  seal turns the worst consequence of the pool into a `PhaseError`.
- **Dice double and flat does not.** The one arithmetic rule the data model is
  shaped to make unmissable, tested here at the level where it is decided
  rather than through an attack that has not been written yet.
"""

from dataclasses import dataclass

import pytest

from dndsim.core.dice import Dice
from dndsim.core.errors import PhaseError
from dndsim.core.timeline import TimeKey, TurnPhase
from dndsim.hooks import contexts as contexts_module
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    AttackDeclaredContext,
    Context,
    D20TestContext,
    DamagePart,
    DamageRollContext,
    HitContext,
    IncomingDamageContext,
    MissContext,
    ReducedToZeroContext,
    RoundContext,
    SaveResolvedContext,
    TurnContext,
)
from dndsim.hooks.registry import Phase, all_hooks
from dndsim.rules.enums import Ability, DamageType, Outcome

# The two bases are context classes that no hook hands out: `Context` is the
# shared lifecycle and `D20TestContext` the advantage machinery every roll of
# the d20 shares. Everything else in the module must be somebody's.
BASE_CONTEXTS = {"Context", "D20TestContext"}


@dataclass(frozen=True, slots=True)
class FakeCreature:
    """The narrowest thing that satisfies `Combatant` — which is the point of
    the protocol being narrow."""

    name: str
    initiative_index: int


@dataclass(frozen=True, slots=True)
class FakeWeapon:
    name: str


@dataclass(frozen=True, slots=True)
class FakeEffect:
    name: str


ROGUE = FakeCreature(name="rogue", initiative_index=0)
DUMMY = FakeCreature(name="dummy", initiative_index=1)
DAGGER = FakeWeapon(name="dagger")


def context_classes() -> dict[str, type[Context]]:
    """Every `*Context` class this module defines, by name."""
    return {
        name: value
        for name, value in vars(contexts_module).items()
        if isinstance(value, type)
        and issubclass(value, Context)
        and name.endswith("Context")
    }


def a_hit(*, outcome: Outcome = Outcome.HIT) -> HitContext:
    """A live `HitContext` with a source stamped, as a dispatcher would.

    The damage bag is passed in because the attack owns it, not the context —
    the same bag reaches `on_damage_roll` afterwards.
    """
    ctx = HitContext(
        attacker=ROGUE,
        defender=DUMMY,
        weapon=DAGGER,
        outcome=outcome,
        damage=contexts_module.DamageBag(),
    )
    ctx.stamp("rogue.sneak_attack", Phase.NORMAL)
    return ctx


# --- The registry and this module are one list ------------------------------


def test_every_hook_names_a_context_class_that_exists() -> None:
    """The string half of the `HookSpec` contract, checked (w0e's promise)."""
    classes = context_classes()
    for spec in all_hooks():
        assert spec.context in classes, (
            f"{spec.name} names the context {spec.context!r}, which "
            "hooks/contexts.py does not define"
        )


def test_every_context_class_is_handed_out_by_some_hook() -> None:
    """The other direction: no context exists that nothing dispatches.

    A context nobody hands out is either a hook somebody forgot to define or a
    Parameter Object written on speculation, and both are worth finding here.
    """
    named = {spec.context for spec in all_hooks()}
    orphans = set(context_classes()) - named - BASE_CONTEXTS
    assert not orphans, f"context classes no hook names: {sorted(orphans)}"


def test_thirty_nine_hooks_share_thirty_one_contexts() -> None:
    """Fewer contexts than hooks, because eight hooks are one moment seen twice.

    The four `SELF`/`TARGET` pairs plus round start/end, encounter start/end,
    turn start/end, rest short/long, condition gained/lost and effect
    applied/expired share a type; the scope, not the context, is what tells the
    two ends apart (DESIGN.md § Hook scope).
    """
    assert len(all_hooks()) == 39
    assert len({spec.context for spec in all_hooks()}) == 31


def test_every_context_is_slotted_and_locked() -> None:
    """No `__dict__` anywhere, and the teaching `__setattr__` on every class."""
    for name, cls in context_classes().items():
        assert "__slots__" in vars(cls), f"{name} is not slotted"
        assert cls.__setattr__ is contexts_module._locked_setattr, (
            f"{name} did not go through the @_context decorator"
        )


# --- Assignment is refused, mutators are not --------------------------------


def test_assigning_a_field_raises_and_says_what_to_do_instead() -> None:
    ctx = a_hit()
    with pytest.raises(AttributeError) as excinfo:
        ctx.damage = contexts_module.DamageBag()  # type: ignore[misc]
    message = str(excinfo.value)
    assert "attribute-locked" in message
    assert "ctx.damage.add(...)" in message


def test_assigning_an_unknown_attribute_raises_too() -> None:
    """The slot would refuse it anyway; the lock gets there first with a reason."""
    ctx = a_hit()
    with pytest.raises(AttributeError, match="attribute-locked"):
        ctx.advantage = True  # type: ignore[attr-defined]


def test_a_mutator_appends_rather_than_replacing() -> None:
    ctx = a_hit()
    ctx.damage.add(dice="3d6")
    ctx.stamp("weapon.dagger", Phase.NORMAL)
    ctx.damage.add(dice="1d4", flat=3, damage_type=DamageType.PIERCING)
    assert len(ctx.damage) == 2
    assert [part.source for part in ctx.damage] == [
        "rogue.sneak_attack",
        "weapon.dagger",
    ]


# --- Provenance is stamped, never passed ------------------------------------


def test_the_stamp_attributes_the_contribution() -> None:
    ctx = a_hit()
    part = ctx.damage.add(dice="3d6")
    assert part.source == "rogue.sneak_attack"


def test_add_takes_no_source_argument() -> None:
    """There is no way for a feature to mis-attribute its own damage."""
    ctx = a_hit()
    with pytest.raises(TypeError):
        ctx.damage.add(dice="1d6", source="somebody.else")  # type: ignore[call-arg]


def test_mutating_an_unstamped_context_raises() -> None:
    """The engine reaching into a context it has not entered."""
    ctx = HitContext(
        attacker=ROGUE,
        defender=DUMMY,
        weapon=DAGGER,
        outcome=Outcome.HIT,
        damage=contexts_module.DamageBag(),
    )
    with pytest.raises(PhaseError, match="nothing stamped"):
        ctx.damage.add(dice="1d6")


# --- A finished context is finished -----------------------------------------


def test_mutating_a_closed_context_raises() -> None:
    ctx = a_hit()
    ctx.close()
    with pytest.raises(PhaseError, match="moment has passed"):
        ctx.damage.add(dice="1d6")


def test_every_mutator_family_refuses_after_the_moment() -> None:
    """Not just damage: the seal is on the frame, so it covers all of them."""
    attack = AttackDeclaredContext(
        attacker=ROGUE, defender=DUMMY, weapon=DAGGER, ability=Ability.DEXTERITY
    )
    attack.stamp("barbarian.reckless_attack", Phase.EARLY)
    attack.close()
    with pytest.raises(PhaseError, match="moment has passed"):
        attack.grant_advantage("reckless")
    with pytest.raises(PhaseError, match="moment has passed"):
        attack.bonus.add(flat=2)
    with pytest.raises(PhaseError, match="moment has passed"):
        attack.apply_effect(ROGUE, FakeEffect(name="reckless"))
    with pytest.raises(PhaseError, match="moment has passed"):
        attack.veto("too late")


def test_reopen_rebinds_and_clears() -> None:
    """The pooling primitive: nothing of the previous event survives it."""
    ctx = a_hit()
    ctx.damage.add(dice="3d6")
    ctx.apply_effect(DUMMY, FakeEffect(name="vex"))
    ctx.stamp("gate", Phase.EARLY)
    ctx.veto("no")
    ctx.close()

    ctx.reopen(
        attacker=DUMMY,
        defender=ROGUE,
        weapon=DAGGER,
        outcome=Outcome.CRITICAL_HIT,
        damage=contexts_module.DamageBag(),
    )

    assert len(ctx.damage) == 0
    assert ctx.pending_effects == ()
    assert not ctx.vetoed
    assert ctx.attacker is DUMMY
    assert ctx.is_critical
    ctx.stamp("someone", Phase.NORMAL)
    assert ctx.damage.add(flat=1).source == "someone"


def test_reopen_refuses_a_field_that_does_not_exist() -> None:
    """A typo here would leave the previous attack's value in place."""
    ctx = a_hit()
    with pytest.raises(AttributeError, match="no field 'attaker'"):
        ctx.reopen(attaker=DUMMY)


def test_reopen_demands_every_field_the_context_does_not_default() -> None:
    """A pooled context rebound with a new attacker and no new defender would
    otherwise be an attack on the previous target."""
    ctx = a_hit()
    with pytest.raises(AttributeError, match=r"\['defender', 'damage'\]"):
        ctx.reopen(attacker=DUMMY, weapon=DAGGER, outcome=Outcome.HIT)


def test_reopen_resets_a_defaulted_field_that_is_not_named() -> None:
    """The other half of the same rule: silence means the default, never the
    previous event's value."""
    ctx = AbilityCheckContext(
        creature=ROGUE, ability=Ability.DEXTERITY, skill="stealth", dc=15
    )
    ctx.reopen(creature=DUMMY, ability=Ability.STRENGTH)
    assert ctx.skill is None
    assert ctx.dc is None


# --- Dice double, flat does not ---------------------------------------------


def test_crit_doubles_dice_and_never_flat() -> None:
    """The rule the whole `DamagePart` split exists for."""
    part = DamagePart(
        dice=Dice(count=3, sides=6),
        flat=3,
        damage_type=DamageType.PIERCING,
        source="rogue.sneak_attack",
    )
    crit = part.on_crit()
    assert crit.dice == Dice(count=6, sides=6)
    assert crit.flat == 3


def test_a_flat_only_part_is_unchanged_by_a_crit() -> None:
    part = DamagePart(dice=None, flat=2, damage_type=None, source="barbarian.rage")
    assert part.on_crit() is part


def test_dice_notation_still_refuses_a_flat_modifier() -> None:
    """`add(dice="1d8+3")` would smuggle a flat bonus into the field that
    doubles, which is exactly what `Dice.parse` refuses (w0c)."""
    ctx = a_hit()
    with pytest.raises(ValueError, match="no flat modifier"):
        ctx.damage.add(dice="1d8+3")


def test_an_empty_contribution_is_refused() -> None:
    ctx = a_hit()
    with pytest.raises(ValueError, match="SKIPPED"):
        ctx.damage.add()


def test_a_modifier_has_no_crit_path_at_all() -> None:
    """`Modifier` is a separate type so that `Dice.doubled()` is unreachable
    from a save bonus or a heal."""
    assert not hasattr(contexts_module.Modifier, "on_crit")


# --- Advantage is a collection, not a boolean -------------------------------


def make_attack() -> AttackDeclaredContext:
    ctx = AttackDeclaredContext(
        attacker=ROGUE, defender=DUMMY, weapon=DAGGER, ability=Ability.DEXTERITY
    )
    ctx.stamp("barbarian.reckless_attack", Phase.EARLY)
    return ctx


def test_two_grants_are_one_advantage() -> None:
    ctx = make_attack()
    ctx.grant_advantage("reckless")
    ctx.grant_advantage("target is prone")
    assert ctx.had_advantage
    assert len(ctx.advantage_sources) == 2


def test_advantage_and_disadvantage_cancel_whatever_the_order() -> None:
    first = make_attack()
    first.grant_advantage("reckless")
    first.impose_disadvantage("prone at range")
    second = make_attack()
    second.impose_disadvantage("prone at range")
    second.grant_advantage("reckless")
    for ctx in (first, second):
        assert not ctx.had_advantage
        assert not ctx.had_disadvantage


def test_the_sources_carry_who_asked() -> None:
    ctx = make_attack()
    ctx.grant_advantage("reckless")
    assert ctx.advantage_sources == ("barbarian.reckless_attack: reckless",)


# --- Resistance is a set ----------------------------------------------------


def incoming() -> IncomingDamageContext:
    ctx = IncomingDamageContext(
        defender=DUMMY,
        attacker=ROGUE,
        amounts={DamageType.SLASHING: 11, DamageType.FIRE: 6},
    )
    ctx.stamp("barbarian.rage", Phase.NORMAL)
    return ctx


def test_a_type_cannot_be_resisted_twice() -> None:
    ctx = incoming()
    ctx.add_resistance(DamageType.SLASHING)
    ctx.stamp("some.other.feature", Phase.NORMAL)
    ctx.add_resistance(DamageType.SLASHING)
    assert ctx.mitigation.resistances == frozenset({DamageType.SLASHING})


def test_the_victim_records_rather_than_applies() -> None:
    """Nothing here computes a number: `engine/damage.py` owns the order."""
    ctx = incoming()
    ctx.add_resistance(DamageType.SLASHING)
    ctx.reduce(flat=3)
    ctx.halve("uncanny dodge")
    assert ctx.mitigation.resistances == frozenset({DamageType.SLASHING})
    assert [m.flat for m in ctx.mitigation.reductions] == [3]
    assert ctx.mitigation.halvings == ("barbarian.rage: uncanny dodge",)
    assert ctx.total == 17, "the sum is not a step of the resolution order"
    assert not hasattr(ctx, "apply")


def test_the_victim_is_handed_rolled_damage_not_the_attackers_bag() -> None:
    """By `on_incoming_damage` the dice are thrown and `damage_type=None` has
    been resolved, so the resolution order has a number per type to work on."""
    assert not hasattr(incoming(), "damage")


def test_a_bag_handed_on_follows_the_context_that_is_live() -> None:
    """The damage built during `on_hit` is the damage rolled during
    `on_damage_roll`: the same bag outlives the context that made it, so
    provenance and the seal must move with it rather than stay behind."""
    hit = a_hit()
    hit.damage.add(dice="3d6")
    hit.close()

    roll = DamageRollContext(
        attacker=ROGUE,
        defender=DUMMY,
        weapon=DAGGER,
        is_critical=False,
        damage=hit.damage,
    )
    roll.stamp("barbarian.rage", Phase.NORMAL)
    assert roll.damage.add(flat=2).source == "barbarian.rage"
    assert [part.source for part in roll.damage] == [
        "rogue.sneak_attack",
        "barbarian.rage",
    ]


def test_reopen_clears_what_it_owns_and_never_what_it_was_handed() -> None:
    """Clearing the caller's freshly built damage would be the pool eating the
    attack, so a bag the context does not own is one of the fields `reopen()`
    demands rather than one it clears."""
    handed = contexts_module.DamageBag()
    roll = DamageRollContext(
        attacker=ROGUE,
        defender=DUMMY,
        weapon=DAGGER,
        is_critical=False,
        damage=handed,
    )
    roll.stamp("weapon.dagger", Phase.NORMAL)
    roll.damage.add(dice="1d4")
    roll.close()

    fresh = contexts_module.DamageBag()
    roll.reopen(
        attacker=DUMMY,
        defender=ROGUE,
        weapon=DAGGER,
        is_critical=True,
        damage=fresh,
    )
    assert len(handed) == 1, "the previous attack's damage was cleared by the pool"
    assert roll.damage is fresh
    roll.stamp("weapon.dagger", Phase.NORMAL)
    assert roll.damage.add(dice="1d4").source == "weapon.dagger"


def test_a_bag_a_context_owns_is_cleared_in_place() -> None:
    """The pool exists so as not to allocate: an owned bag is emptied, not
    replaced."""
    check = AbilityCheckContext(creature=ROGUE, ability=Ability.DEXTERITY)
    check.stamp("rogue.expertise", Phase.NORMAL)
    check.bonus.add(flat=4)
    owned = check.bonus
    check.reopen(creature=DUMMY, ability=Ability.STRENGTH)
    assert check.bonus is owned
    assert len(owned) == 0


def test_a_context_never_owns_the_attacks_damage() -> None:
    """The regression guard for the classification itself.

    The damage built at `on_hit` is the damage rolled at `on_damage_roll`, so it
    belongs to the attack. Declaring it with a `default_factory` on any of these
    would classify it *owned* and hand `reopen()` a bag to clear that it has no
    business clearing — and would leave nothing exercising the handed-bag path.
    """
    for cls in (HitContext, MissContext, DamageRollContext):
        assert "damage" in cls._bag_fields
        assert "damage" not in cls._owned_bags, f"{cls.__name__} claims the damage"
        assert "damage" in cls._required_fields


# --- The veto is EARLY and FINAL only ---------------------------------------


@pytest.mark.parametrize("phase", [Phase.EARLY, Phase.FINAL])
def test_a_gating_handler_may_halt_the_chain(phase: Phase) -> None:
    ctx = a_hit()
    ctx.stamp("some.gate", phase)
    ctx.veto("the target is immune")
    assert ctx.vetoed
    assert ctx.veto_reason == "some.gate: the target is immune"


@pytest.mark.parametrize("phase", [Phase.NORMAL, Phase.LATE])
def test_a_contributing_handler_may_not(phase: Phase) -> None:
    """A NORMAL handler that could cancel the event would silently delete every
    contribution ordered after it."""
    ctx = a_hit()
    ctx.stamp("some.feature", phase)
    with pytest.raises(PhaseError, match="only EARLY and FINAL"):
        ctx.veto("nope")


def test_an_unvetoed_context_reports_no_reason() -> None:
    ctx = a_hit()
    assert not ctx.vetoed
    assert ctx.veto_reason is None


# --- Effects are queued, not applied ----------------------------------------


def test_apply_effect_queues_for_the_engine_to_drain() -> None:
    """The snapshot rule: an effect applied during a hook must not fire for the
    event that applied it, which is why Reckless Attack also has to call
    `grant_advantage()` for the triggering attack."""
    ctx = make_attack()
    effect = FakeEffect(name="reckless")
    ctx.apply_effect(ROGUE, effect)
    assert ctx.pending_effects == ((ROGUE, effect),)


# --- The overrides ----------------------------------------------------------


def test_a_save_can_be_declared_a_success() -> None:
    ctx = SaveResolvedContext(
        creature=DUMMY,
        ability=Ability.DEXTERITY,
        dc=15,
        total=9,
        succeeded=False,
    )
    ctx.stamp("dragon.legendary_resistance", Phase.LATE)
    before = ctx.forced_success
    ctx.succeed("legendary resistance")
    assert (before, ctx.forced_success) == (False, True)
    # The roll itself is a fact: the override is recorded beside it, not over it.
    assert not ctx.succeeded


def test_two_features_can_keep_the_same_creature_up() -> None:
    ctx = ReducedToZeroContext(defender=DUMMY, attacker=ROGUE, overflow=4)
    ctx.stamp("orc.relentless_endurance", Phase.NORMAL)
    ctx.prevent("relentless endurance")
    ctx.stamp("zombie.undead_fortitude", Phase.NORMAL)
    ctx.prevent("undead fortitude")
    assert ctx.prevented
    assert len(ctx.prevented_by) == 2


# --- A context is the event, not a value ------------------------------------


def test_two_identical_contexts_are_not_the_same_event() -> None:
    first = RoundContext(round=3)
    second = RoundContext(round=3)
    assert first != second
    assert len({first, second}) == 2


def test_a_context_carries_the_clock_rather_than_a_copy_of_it() -> None:
    time = TimeKey(round=2, initiative_index=0, phase=TurnPhase.START)
    ctx = TurnContext(creature=ROGUE, time=time)
    assert ctx.time is time


def test_the_d20_base_is_shared_by_every_roll_of_the_d20() -> None:
    """Attack, check, save, initiative, death save and concentration all share
    the advantage machinery rather than repeating it six times."""
    rolls = {
        name
        for name, cls in context_classes().items()
        if issubclass(cls, D20TestContext) and name != "D20TestContext"
    }
    assert rolls == {
        "AbilityCheckContext",
        "AttackDeclaredContext",
        "ConcentrationCheckContext",
        "DeathSaveContext",
        "InitiativeContext",
        "SavingThrowContext",
    }
