"""The w0g contract: what a handler must look like, and what running one costs.

- **A declaration that cannot work fails while it is being read.** A typo'd hook
  name, a wrong context annotation, a missing return, an override that dropped
  its `@hook`.
- **Order is decided, not observed.** Phase band, then declared order, then
  attachment sequence.
- **Nothing is spent by a handler that declined.** `once_per` and `costs` follow
  the return value.
- **Provenance is automatic.** The dispatcher stamps; the author writes nothing.
- **The handler list is a snapshot.** An effect applied mid-hook does not join
  the event that applied it.
"""

from dataclasses import dataclass

import pytest

from dndsim.core.errors import (
    EngineAssemblyError,
    FeatureContractError,
    FeatureDeclarationError,
    PhaseError,
)
from dndsim.core.timeline import InitiativeKey, Reset, ResetManager, TimeKey, TurnPhase
from dndsim.hooks.contexts import (
    AttackRollContext,
    Context,
    DamageBag,
    HitContext,
    TurnContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import (
    ON_ATTACK_ROLLED,
    ON_HIT,
    ON_HIT_TAKEN,
    ON_TURN_START,
)
from dndsim.hooks.dispatch import Dispatcher
from dndsim.hooks.registry import Phase, UnknownHookError
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import ActionCost, DamageType, Outcome


def seat(n: int) -> InitiativeKey:
    """The nth place in an order where everyone rolled the same initiative."""
    return InitiativeKey(
        initiative=20, dexterity=10, player_character=True, tiebreak=n / 10
    )


@dataclass(frozen=True, slots=True)
class FakeCreature:
    """The narrowest thing that satisfies `Combatant`; its hit points default."""

    name: str
    initiative: InitiativeKey = seat(0)
    hp: int = 20
    max_hp: int = 20
    temp_hp: int = 0


ATTACKER = FakeCreature("attacker", seat(0))
DEFENDER = FakeCreature("defender", seat(1))


def hit_context() -> HitContext:
    return HitContext(
        attacker=ATTACKER,
        defender=DEFENDER,
        weapon=None,
        outcome=Outcome.HIT,
        damage=DamageBag(),
    )


def turn_context() -> TurnContext:
    return TurnContext(creature=ATTACKER, time=TimeKey(1, seat(0), TurnPhase.START))


def applied_counts(dispatcher: Dispatcher) -> dict[str, int]:
    """How often each `on_hit` handler has applied, by method name."""
    return {bound.handler.name: bound.applied for bound in dispatcher.handlers(ON_HIT)}


class Recorder(HookSource):
    """A source that writes down the order it was called in."""

    __slots__ = ("log",)

    def __init__(self, owner: FakeCreature, log: list[str]) -> None:
        super().__init__(owner)
        self.log = log


# --- What a declaration must look like --------------------------------------


def test_a_typo_in_a_hook_name_raises_where_it_is_written() -> None:
    with pytest.raises(UnknownHookError, match="did you mean 'on_hit'"):

        @hook("on_hitt")
        def handler(self: object, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED


def test_a_missing_return_annotation_is_an_import_error() -> None:
    with pytest.raises(FeatureDeclarationError, match="does not return R"):

        class NoReturn(HookSource):
            @hook("on_hit")
            def handler(self, ctx: HitContext):  # type: ignore[no-untyped-def]
                return R.APPLIED

    assert issubclass(FeatureDeclarationError, ImportError)


def test_the_wrong_context_annotation_names_the_right_one() -> None:
    with pytest.raises(FeatureDeclarationError, match="hands out a HitContext"):

        class WrongContext(HookSource):
            @hook("on_hit")
            def handler(self, ctx: AttackRollContext) -> R:  # pragma: no cover
                return R.APPLIED


def test_an_unannotated_context_is_refused_too() -> None:
    with pytest.raises(
        FeatureDeclarationError, match="annotates its context as nothing"
    ):

        class Unannotated(HookSource):
            @hook("on_hit")
            def handler(self, ctx) -> R:  # type: ignore[no-untyped-def]
                return R.APPLIED


def test_a_handler_takes_exactly_self_and_a_context() -> None:
    with pytest.raises(FeatureDeclarationError, match=r"a handler takes exactly"):

        class TooMany(HookSource):
            @hook("on_hit")
            def handler(self, ctx: HitContext, extra: int) -> R:  # pragma: no cover
                return R.APPLIED


def test_a_static_handler_is_refused_rather_than_ignored() -> None:
    with pytest.raises(FeatureDeclarationError, match="staticmethod"):

        class Static(HookSource):
            @staticmethod
            @hook("on_hit")
            def handler(self: object, ctx: HitContext) -> R:  # pragma: no cover
                return R.APPLIED


def test_one_method_declares_one_hook() -> None:
    with pytest.raises(ValueError, match="already handles on_hit"):

        @hook("on_miss")
        @hook("on_hit")
        def handler(self: object, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED


def test_an_override_that_drops_the_hook_is_refused() -> None:
    class Base(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED

    with pytest.raises(FeatureDeclarationError, match="without declaring @hook"):

        class Derived(Base):
            def handler(self, ctx: HitContext) -> R:
                return R.SKIPPED


def test_an_override_that_keeps_the_hook_replaces_the_body() -> None:
    log: list[str] = []

    class Base(Recorder):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            self.log.append("base")
            return R.APPLIED

    class Derived(Base):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            self.log.append("derived")
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Derived(ATTACKER, log))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["derived"]


def test_a_handler_is_still_an_ordinary_method() -> None:
    """The decorator marks and returns; it does not wrap."""

    class Feature(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            return R.SKIPPED

    assert Feature(ATTACKER).handler(hit_context()) is R.SKIPPED


# --- Identity ----------------------------------------------------------------


def test_an_undeclared_id_is_the_class_name_in_snake_case() -> None:
    class SneakAttack(HookSource):
        pass

    assert SneakAttack.ID == "sneak_attack"


def test_a_declared_id_is_not_inherited_by_a_subclass() -> None:
    class Feature(HookSource):
        ID = "feature"

    class GreatWeaponMaster(Feature):
        pass

    assert Feature.ID == "feature"
    assert GreatWeaponMaster.ID == "great_weapon_master"


def test_a_malformed_id_is_refused_at_class_creation() -> None:
    with pytest.raises(FeatureDeclarationError, match="not a source id"):

        class Feature(HookSource):
            ID = "Rogue Sneak Attack"


# --- Order -------------------------------------------------------------------


def test_handlers_run_in_phase_band_order_whatever_the_attachment_order() -> None:
    log: list[str] = []

    def source(label: str, phase: Phase) -> HookSource:
        class Banded(Recorder):
            @hook("on_hit", phase=phase)
            def handler(self, ctx: HitContext) -> R:
                self.log.append(label)
                return R.SKIPPED

        return Banded(ATTACKER, log)

    dispatcher = Dispatcher()
    dispatcher.attach(source("final", Phase.FINAL))
    dispatcher.attach(source("normal", Phase.NORMAL))
    dispatcher.attach(source("late", Phase.LATE))
    dispatcher.attach(source("early", Phase.EARLY))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["early", "normal", "late", "final"]


def test_order_then_attachment_sequence_break_ties_within_a_band() -> None:
    log: list[str] = []

    def source(label: str, order: int) -> HookSource:
        class Ordered(Recorder):
            @hook("on_hit", order=order)
            def handler(self, ctx: HitContext) -> R:
                self.log.append(label)
                return R.SKIPPED

        return Ordered(ATTACKER, log)

    dispatcher = Dispatcher()
    dispatcher.attach(source("first_attached", 0))
    dispatcher.attach(source("second_attached", 0))
    dispatcher.attach(source("ordered_last", 5))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["first_attached", "second_attached", "ordered_last"]


def test_a_hook_nobody_listens_to_is_a_lookup_and_a_return() -> None:
    dispatcher = Dispatcher()
    ctx = turn_context()
    dispatcher.dispatch(ON_TURN_START, ctx)
    assert dispatcher.handlers(ON_TURN_START) == ()
    assert not ctx.vetoed


# --- Provenance --------------------------------------------------------------


def test_the_dispatcher_stamps_so_the_author_does_not() -> None:
    class SneakAttack(HookSource):
        ID = "rogue.sneak_attack"

        @hook("on_hit")
        def sneak(self, ctx: HitContext) -> R:
            ctx.damage.add(dice="3d6", damage_type=DamageType.PIERCING)
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(SneakAttack(ATTACKER))
    ctx = hit_context()
    dispatcher.dispatch(ON_HIT, ctx)
    assert [part.source for part in ctx.damage] == ["rogue.sneak_attack"]


def test_the_stamp_standing_before_a_dispatch_is_put_back_after_it() -> None:
    class Feature(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            ctx.damage.add(flat=2, damage_type=DamageType.FIRE)
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Feature(ATTACKER))
    ctx = hit_context()
    ctx.stamp("engine.attack", None)
    dispatcher.dispatch(ON_HIT, ctx)
    ctx.damage.add(flat=1, damage_type=DamageType.SLASHING)
    assert [part.source for part in ctx.damage] == ["feature", "engine.attack"]


def test_a_mutation_after_an_unstamped_dispatch_still_raises() -> None:
    class Feature(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Feature(ATTACKER))
    ctx = hit_context()
    dispatcher.dispatch(ON_HIT, ctx)
    with pytest.raises(PhaseError, match="nothing stamped"):
        ctx.damage.add(flat=1, damage_type=DamageType.SLASHING)


# --- What a return value costs -----------------------------------------------


def test_a_once_per_budget_is_spent_only_when_the_handler_applies() -> None:
    class Sneak(HookSource):
        __slots__ = ("apply", "calls")

        def __init__(self, owner: FakeCreature) -> None:
            super().__init__(owner)
            self.apply = False
            self.calls = 0

        @hook("on_hit", once_per=Reset.TURN)
        def sneak(self, ctx: HitContext) -> R:
            self.calls += 1
            return R.APPLIED if self.apply else R.SKIPPED

    sneak = Sneak(ATTACKER)
    dispatcher = Dispatcher()
    dispatcher.attach(sneak)

    dispatcher.dispatch(ON_HIT, hit_context())
    dispatcher.dispatch(ON_HIT, hit_context())
    assert sneak.calls == 2, "a skipped handler burns nothing"

    sneak.apply = True
    dispatcher.dispatch(ON_HIT, hit_context())
    dispatcher.dispatch(ON_HIT, hit_context())
    assert sneak.calls == 3, "an applied handler is done for the turn"


def test_any_turn_boundary_hands_a_turn_budget_back_and_own_turn_waits() -> None:
    class Sneak(HookSource):
        @hook("on_hit", once_per=Reset.TURN)
        def sneak(self, ctx: HitContext) -> R:
            return R.APPLIED

    class OncePerOwnTurn(HookSource):
        @hook("on_hit", once_per=Reset.OWN_TURN)
        def rider(self, ctx: HitContext) -> R:
            return R.APPLIED

    resets = ResetManager()
    dispatcher = Dispatcher(resets=resets)
    dispatcher.attach(Sneak(ATTACKER))
    dispatcher.attach(OncePerOwnTurn(ATTACKER))
    dispatcher.dispatch(ON_HIT, hit_context())
    applied = applied_counts(dispatcher)
    assert applied == {"sneak": 1, "rider": 1}

    resets.turn_started(own=False)
    dispatcher.dispatch(ON_HIT, hit_context())
    applied = applied_counts(dispatcher)
    assert applied == {"sneak": 2, "rider": 1}, "an enemy's turn is not your turn"

    resets.turn_started(own=True)
    dispatcher.dispatch(ON_HIT, hit_context())
    applied = applied_counts(dispatcher)
    assert applied == {"sneak": 3, "rider": 2}


class FakeEconomy:
    """One slot of an action economy, enough to prove what is spent when."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.spent: list[ActionCost] = []

    def can_spend(self, cost: ActionCost) -> bool:
        return self.available

    def spend(self, cost: ActionCost) -> None:
        self.spent.append(cost)


def test_a_reaction_is_paid_for_only_when_the_handler_applies() -> None:
    class OpportunityAttack(HookSource):
        __slots__ = ("apply",)

        def __init__(self, owner: FakeCreature) -> None:
            super().__init__(owner)
            self.apply = False

        @hook("on_hit", costs=ActionCost.REACTION)
        def react(self, ctx: HitContext) -> R:
            return R.APPLIED if self.apply else R.SKIPPED

    economy = FakeEconomy()
    source = OpportunityAttack(ATTACKER)
    dispatcher = Dispatcher(economy=economy)
    dispatcher.attach(source)

    dispatcher.dispatch(ON_HIT, hit_context())
    assert economy.spent == [], "declining costs nothing"

    source.apply = True
    dispatcher.dispatch(ON_HIT, hit_context())
    assert economy.spent == [ActionCost.REACTION]


def test_an_unaffordable_handler_is_never_called() -> None:
    calls: list[str] = []

    class Reaction(HookSource):
        @hook("on_hit", costs=ActionCost.REACTION)
        def react(self, ctx: HitContext) -> R:
            calls.append("called")  # pragma: no cover
            return R.APPLIED

    dispatcher = Dispatcher(economy=FakeEconomy(available=False))
    dispatcher.attach(Reaction(ATTACKER))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert calls == []


def test_a_cost_with_no_economy_to_pay_it_raises_rather_than_running_free() -> None:
    class Reaction(HookSource):
        @hook("on_hit", costs=ActionCost.REACTION)
        def react(self, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Reaction(ATTACKER))
    with pytest.raises(EngineAssemblyError, match="no action economy"):
        dispatcher.dispatch(ON_HIT, hit_context())


class FakeGate:
    def __init__(self, *open_ids: str) -> None:
        self.open_ids = set(open_ids)
        self.asked: list[str] = []

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        self.asked.append(gate_id)
        return gate_id in self.open_ids


def test_a_closed_gate_stops_the_handler_and_an_open_one_does_not() -> None:
    calls: list[str] = []

    class Gated(HookSource):
        @hook("on_hit", gate="rogue.uncanny_dodge")
        def handler(self, ctx: HitContext) -> R:
            calls.append("called")
            return R.APPLIED

    closed = FakeGate()
    dispatcher = Dispatcher(gate=closed)
    dispatcher.attach(Gated(ATTACKER))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert calls == []
    assert closed.asked == ["rogue.uncanny_dodge"]

    open_dispatcher = Dispatcher(gate=FakeGate("rogue.uncanny_dodge"))
    open_dispatcher.attach(Gated(ATTACKER))
    open_dispatcher.dispatch(ON_HIT, hit_context())
    assert calls == ["called"]


def test_a_gate_nobody_answers_is_not_an_open_one() -> None:
    class Gated(HookSource):
        @hook("on_hit", gate="rogue.uncanny_dodge")
        def handler(self, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Gated(ATTACKER))
    with pytest.raises(EngineAssemblyError, match="no action list"):
        dispatcher.dispatch(ON_HIT, hit_context())


def test_a_gate_id_is_checked_where_it_is_written() -> None:
    with pytest.raises(ValueError, match="not an APL action id"):
        hook("on_hit", gate="Uncanny Dodge")


def test_returning_something_that_is_not_a_hook_result_raises() -> None:
    class Forgetful(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            ctx.damage.add(flat=1, damage_type=DamageType.FIRE)
            return None  # type: ignore[return-value]

    dispatcher = Dispatcher()
    dispatcher.attach(Forgetful(ATTACKER))
    with pytest.raises(FeatureContractError, match="a missing return"):
        dispatcher.dispatch(ON_HIT, hit_context())


# --- The veto ----------------------------------------------------------------


def test_an_early_veto_halts_the_chain() -> None:
    log: list[str] = []

    class Gate(Recorder):
        @hook("on_hit", phase=Phase.EARLY)
        def stop(self, ctx: HitContext) -> R:
            self.log.append("gate")
            ctx.veto("nothing happens")
            return R.APPLIED

    class Rider(Recorder):
        @hook("on_hit")
        def rider(self, ctx: HitContext) -> R:
            self.log.append("rider")  # pragma: no cover
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Gate(ATTACKER, log))
    dispatcher.attach(Rider(ATTACKER, log))
    ctx = hit_context()
    dispatcher.dispatch(ON_HIT, ctx)
    assert log == ["gate"]
    assert ctx.vetoed
    assert ctx.veto_reason == "gate: nothing happens"


def test_a_vetoed_event_does_not_happen_on_the_other_creature_either() -> None:
    """`on_hit` and `on_hit_taken` are one moment dispatched twice."""
    log: list[str] = []

    class Victim(Recorder):
        @hook("on_hit_taken")
        def react(self, ctx: HitContext) -> R:
            self.log.append("victim")  # pragma: no cover
            return R.APPLIED

    class Gate(Recorder):
        @hook("on_hit", phase=Phase.EARLY)
        def stop(self, ctx: HitContext) -> R:
            ctx.veto("the attack is undone")
            return R.APPLIED

    attacker_side = Dispatcher()
    attacker_side.attach(Gate(ATTACKER, log))
    defender_side = Dispatcher()
    defender_side.attach(Victim(DEFENDER, log))

    ctx = hit_context()
    attacker_side.dispatch(ON_HIT, ctx)
    defender_side.dispatch(ON_HIT_TAKEN, ctx)
    assert log == []


def test_a_normal_handler_that_vetoes_is_refused_by_the_context() -> None:
    class Overreaching(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:
            ctx.veto("not mine to cast")
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Overreaching(ATTACKER))
    with pytest.raises(PhaseError, match="only EARLY and FINAL"):
        dispatcher.dispatch(ON_HIT, hit_context())


# --- The snapshot rule -------------------------------------------------------


def test_a_source_attached_mid_hook_does_not_join_the_running_event() -> None:
    log: list[str] = []

    class Latecomer(Recorder):
        @hook("on_hit")
        def rider(self, ctx: HitContext) -> R:
            self.log.append("latecomer")
            return R.APPLIED

    class Reckless(Recorder):
        __slots__ = ("dispatcher",)

        def __init__(
            self, owner: FakeCreature, log: list[str], dispatcher: Dispatcher
        ) -> None:
            super().__init__(owner, log)
            self.dispatcher = dispatcher

        @hook("on_hit", phase=Phase.EARLY)
        def apply_effect(self, ctx: HitContext) -> R:
            self.log.append("reckless")
            self.dispatcher.attach(Latecomer(ATTACKER, self.log))
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Reckless(ATTACKER, log, dispatcher))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["reckless"], "the effect applied during the event is not in it"

    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["reckless", "reckless", "latecomer"]


def test_detaching_a_source_removes_its_handlers() -> None:
    log: list[str] = []

    class Condition(Recorder):
        @hook("on_hit")
        def rider(self, ctx: HitContext) -> R:
            self.log.append("condition")
            return R.APPLIED

    condition = Condition(ATTACKER, log)
    dispatcher = Dispatcher()
    dispatcher.attach(condition)
    dispatcher.dispatch(ON_HIT, hit_context())
    dispatcher.detach(condition)
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["condition"]
    assert dispatcher.sources() == ()


def test_attaching_the_same_source_twice_is_refused() -> None:
    class Feature(HookSource):
        @hook("on_hit")
        def handler(self, ctx: HitContext) -> R:  # pragma: no cover
            return R.APPLIED

    feature = Feature(ATTACKER)
    dispatcher = Dispatcher()
    dispatcher.attach(feature)
    with pytest.raises(ValueError, match="already attached"):
        dispatcher.attach(feature)


def test_a_spent_budget_survives_a_table_rebuild() -> None:
    """Attaching a second source mid-turn must not hand the first its turn back."""

    class Sneak(HookSource):
        __slots__ = ("calls",)

        def __init__(self, owner: FakeCreature) -> None:
            super().__init__(owner)
            self.calls = 0

        @hook("on_hit", once_per=Reset.TURN)
        def sneak(self, ctx: HitContext) -> R:
            self.calls += 1
            return R.APPLIED

    class Other(HookSource):
        pass

    sneak = Sneak(ATTACKER)
    dispatcher = Dispatcher()
    dispatcher.attach(sneak)
    dispatcher.dispatch(ON_HIT, hit_context())
    dispatcher.attach(Other(ATTACKER))
    dispatcher.dispatch(ON_HIT, hit_context())
    assert sneak.calls == 1


# --- The handlers of one source ---------------------------------------------


def test_one_source_may_handle_several_hooks() -> None:
    log: list[str] = []

    class Rage(Recorder):
        @hook("on_hit")
        def damage(self, ctx: HitContext) -> R:
            self.log.append("hit")
            return R.APPLIED

        @hook("on_attack_rolled")
        def watch(self, ctx: AttackRollContext) -> R:
            self.log.append("rolled")
            return R.APPLIED

    dispatcher = Dispatcher()
    dispatcher.attach(Rage(ATTACKER, log))
    dispatcher.dispatch(
        ON_ATTACK_ROLLED,
        AttackRollContext(
            attacker=ATTACKER,
            defender=DEFENDER,
            weapon=None,
            natural=15,
            total=20,
            crit_threshold=20,
            had_advantage=False,
            had_disadvantage=False,
        ),
    )
    dispatcher.dispatch(ON_HIT, hit_context())
    assert log == ["rolled", "hit"]
