"""The w0e contract: the hook list is closed, complete, and refuses a typo.

- An unknown hook name raises (DESIGN.md § Hook system).
- The list matches DESIGN.md § The closed hook list: 39 hooks in eight groups.
- Importing the registry alone populates it.
"""

import subprocess
import sys
from collections import Counter

import pytest

from dndsim.hooks.definitions import (
    ON_DECLARE_ATTACK,
    ON_HEAL,
    ON_HIT,
    ON_HIT_TAKEN,
    ON_INCOMING_ATTACK_DECLARED,
    ON_INITIATIVE_ROLL,
    ON_LEAVE_REACH,
    ON_TEMP_HP_GAINED,
)
from dndsim.hooks.registry import (
    HookGroup,
    HookRegistry,
    HookSpec,
    Phase,
    Scope,
    UnknownHookError,
    all_hooks,
    hook_named,
)

# The table in DESIGN.md § The closed hook list — 39 points, transcribed.
GROUP_SIZES = {
    HookGroup.TIMELINE: 9,
    HookGroup.ATTACK: 7,
    HookGroup.DAMAGE: 5,
    HookGroup.HEALING_AND_DYING: 4,
    HookGroup.CHECKS_AND_SAVES: 4,
    HookGroup.CASTING: 3,
    HookGroup.EFFECTS: 4,
    HookGroup.MOVEMENT_AND_RESOURCES: 3,
}


class TestTheClosedList:
    def test_there_are_thirty_nine_hooks(self) -> None:
        assert len(all_hooks()) == 39

    def test_the_groups_are_the_sizes_the_design_states(self) -> None:
        assert Counter(spec.group for spec in all_hooks()) == GROUP_SIZES

    def test_every_hook_is_reachable_by_name(self) -> None:
        for spec in all_hooks():
            assert hook_named(spec.name) is spec

    def test_names_are_unique(self) -> None:
        names = [spec.name for spec in all_hooks()]
        assert len(set(names)) == len(names)

    @pytest.mark.parametrize(
        "name",
        [
            "on_heal",
            "on_temp_hp_gained",
            "on_death_save",
            "on_stabilize",
            "on_initiative_roll",
        ],
    )
    def test_the_hooks_w6_and_w4_will_need_exist_before_they_do(
        self, name: str
    ) -> None:
        """The healing hooks and the initiative hook exist (ROADMAP.md w0e)."""
        assert hook_named(name).name == name


class TestATypoRaises:
    def test_an_unknown_name_raises(self) -> None:
        with pytest.raises(UnknownHookError):
            hook_named("on_hitt")

    def test_the_error_suggests_the_hook_that_was_meant(self) -> None:
        with pytest.raises(UnknownHookError, match="'on_hit'"):
            hook_named("on_hitt")

    def test_a_name_nothing_resembles_says_so_rather_than_guessing(self) -> None:
        with pytest.raises(UnknownHookError, match="none is close"):
            hook_named("xyzzy")

    def test_a_hook_name_that_is_a_defined_context_is_still_unknown(self) -> None:
        with pytest.raises(UnknownHookError):
            hook_named("HitContext")


class TestScope:
    """`self.owner` is decided by the hook, so no feature writes
    `if ctx.attacker is self.owner` (DESIGN.md § Hook scope)."""

    def test_the_attacker_and_the_attacked_are_two_hooks_one_moment(self) -> None:
        assert ON_DECLARE_ATTACK.scope is Scope.SELF
        assert ON_INCOMING_ATTACK_DECLARED.scope is Scope.TARGET
        assert ON_DECLARE_ATTACK.context == ON_INCOMING_ATTACK_DECLARED.context

    def test_sneak_attack_and_heavy_armor_master_never_ask_who_is_who(self) -> None:
        assert ON_HIT.scope is Scope.SELF
        assert ON_HIT_TAKEN.scope is Scope.TARGET

    def test_healing_belongs_to_the_creature_that_regains_the_points(self) -> None:
        assert ON_HEAL.scope is Scope.TARGET
        assert ON_TEMP_HP_GAINED.scope is Scope.TARGET

    def test_an_opportunity_attack_trigger_is_observed_not_suffered(self) -> None:
        assert ON_LEAVE_REACH.scope is Scope.OBSERVER

    def test_every_scope_is_used_by_at_least_one_hook(self) -> None:
        assert {spec.scope for spec in all_hooks()} == set(Scope)


class TestPhase:
    def test_the_bands_run_in_the_order_they_are_named(self) -> None:
        assert [p.value for p in Phase] == sorted(p.value for p in Phase)
        assert Phase.EARLY.value < Phase.NORMAL.value < Phase.LATE.value
        assert Phase.LATE.value < Phase.FINAL.value

    def test_only_the_order_sensitive_bands_need_a_phase_comment(self) -> None:
        needed = {p for p in Phase if p.requires_justification}
        assert needed == {Phase.LATE, Phase.FINAL}


class TestDefineHookRefusesBadDefinitions:
    """Every rejection below is a mistake made while editing `definitions.py`.
    They are tested against a throwaway registry so that proving the guard works
    does not mean adding a fortieth hook to the real one."""

    def _define(self, registry: HookRegistry, **overrides: str) -> HookSpec:
        kwargs = {
            "name": "on_example",
            "context": "ExampleContext",
            "summary": "An example.",
        }
        kwargs.update(overrides)
        return registry.define(
            kwargs["name"],
            scope=Scope.SELF,
            group=HookGroup.ATTACK,
            context=kwargs["context"],
            summary=kwargs["summary"],
        )

    def test_a_well_formed_definition_is_accepted(self) -> None:
        registry = HookRegistry()
        spec = self._define(registry)
        assert registry.named("on_example") is spec
        assert len(registry) == 1

    @pytest.mark.parametrize("name", ["hit", "On_Hit", "on_Hit", "on_", "onhit", ""])
    def test_a_hook_name_is_on_underscore_lowercase_words(self, name: str) -> None:
        registry = HookRegistry()
        with pytest.raises(ValueError, match="hook name"):
            self._define(registry, name=name)

    def test_defining_the_same_hook_twice_raises(self) -> None:
        registry = HookRegistry()
        self._define(registry)
        with pytest.raises(ValueError, match="already defined"):
            self._define(registry)

    @pytest.mark.parametrize("context", ["hit_context", "Hit", "HitContextt", ""])
    def test_the_context_must_name_a_context_class(self, context: str) -> None:
        registry = HookRegistry()
        with pytest.raises(ValueError, match="Context"):
            self._define(registry, context=context)

    @pytest.mark.parametrize("summary", ["", "   ", "two\nlines"])
    def test_the_summary_is_one_non_empty_line(self, summary: str) -> None:
        registry = HookRegistry()
        with pytest.raises(ValueError, match="one non-empty line"):
            self._define(registry, summary=summary)

    def test_a_rejected_definition_leaves_nothing_behind(self) -> None:
        registry = HookRegistry()
        with pytest.raises(ValueError):
            self._define(registry, name="On_Hit")
        assert len(registry) == 0


class TestSpecsAreSingletons:
    """Identity is equality: a `HookSpec` built by hand is not the registered
    hook."""

    def test_a_look_alike_spec_is_not_the_hook(self) -> None:
        counterfeit = HookSpec(
            name=ON_HIT.name,
            scope=ON_HIT.scope,
            group=ON_HIT.group,
            context=ON_HIT.context,
            summary=ON_HIT.summary,
        )
        assert counterfeit != ON_HIT
        assert {ON_HIT: "real"}.get(counterfeit) is None

    def test_a_spec_is_hashable_and_frozen(self) -> None:
        assert {ON_HIT, ON_HIT} == {ON_HIT}
        with pytest.raises(AttributeError):
            ON_HIT.name = "on_something_else"  # type: ignore[misc]

    def test_a_spec_prints_as_its_name(self) -> None:
        assert str(ON_INITIATIVE_ROLL) == "on_initiative_roll"


class TestImportingTheRegistryIsEnough:
    def test_the_registry_is_populated_without_importing_definitions(self) -> None:
        """A test module that imports only `registry` must still see 39 hooks;
        otherwise `@hook("on_hitt")` would raise or not depending on which
        module pytest happened to collect first."""
        source = (
            "from dndsim.hooks.registry import all_hooks, hook_named\n"
            "assert len(all_hooks()) == 39\n"
            "assert hook_named('on_hit').name == 'on_hit'\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", source], capture_output=True, text=True
        )
        assert completed.returncode == 0, completed.stderr
