"""The w0d contract: the clock orders play, durations pin themselves to it, and
once-per budgets come back at the boundary they named.

Two properties here are the ones the roadmap asks for by name, and both are
bugs that would never announce themselves:

- **`through_phase` in both directions.** Rage must still be resisting damage
  while your own `on_turn_end` handlers run; Reckless Attack must already be
  gone before your `on_turn_start` handlers do. Get the flag backwards and every
  number in the simulator shifts by a few percent, silently.
- **`Reset.TURN` vs `Reset.OWN_TURN`.** "Once per turn" is once per *any* turn,
  so Sneak Attack lands on an opportunity attack during an enemy's turn and
  again on the rogue's own. "Once on your turn" does not.
"""

import pytest

from dndsim.core.timeline import (
    Duration,
    Expiry,
    Reset,
    ResetManager,
    TimeKey,
    TurnPhase,
)


class TestTimeKey:
    def test_the_phases_of_a_turn_are_in_the_order_they_happen(self) -> None:
        assert TurnPhase.START.value < TurnPhase.END.value

    def test_a_turn_starts_before_it_ends(self) -> None:
        assert TimeKey(1, 0, TurnPhase.START) < TimeKey(1, 0, TurnPhase.END)

    def test_earlier_in_the_initiative_order_is_earlier_in_the_round(self) -> None:
        assert TimeKey(1, 0, TurnPhase.END) < TimeKey(1, 1, TurnPhase.START)

    def test_a_round_is_later_than_every_moment_of_the_one_before(self) -> None:
        assert TimeKey(1, 7, TurnPhase.END) < TimeKey(2, 0, TurnPhase.START)

    def test_the_natural_ordering_is_the_order_of_play(self) -> None:
        # One and a half rounds of a three-creature encounter, shuffled.
        shuffled = [
            TimeKey(2, 0, TurnPhase.START),
            TimeKey(1, 1, TurnPhase.END),
            TimeKey(1, 0, TurnPhase.START),
            TimeKey(1, 2, TurnPhase.END),
            TimeKey(1, 1, TurnPhase.START),
            TimeKey(1, 0, TurnPhase.END),
            TimeKey(1, 2, TurnPhase.START),
        ]
        assert sorted(shuffled) == [
            TimeKey(1, 0, TurnPhase.START),
            TimeKey(1, 0, TurnPhase.END),
            TimeKey(1, 1, TurnPhase.START),
            TimeKey(1, 1, TurnPhase.END),
            TimeKey(1, 2, TurnPhase.START),
            TimeKey(1, 2, TurnPhase.END),
            TimeKey(2, 0, TurnPhase.START),
        ]

    def test_the_order_is_total(self) -> None:
        earlier = TimeKey(3, 1, TurnPhase.START)
        later = TimeKey(3, 1, TurnPhase.END)
        assert earlier <= later and earlier < later
        assert later >= earlier and later > earlier
        assert earlier <= earlier and earlier >= earlier
        assert not earlier > earlier and not earlier < earlier

    def test_the_same_moment_is_the_same_value(self) -> None:
        assert TimeKey(2, 3, TurnPhase.END) == TimeKey(2, 3, TurnPhase.END)
        assert len({TimeKey(2, 3, TurnPhase.END), TimeKey(2, 3, TurnPhase.END)}) == 1

    def test_a_time_key_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            TimeKey(1, 0, TurnPhase.START).round = 2  # type: ignore[misc]

    @pytest.mark.parametrize("bad_round", [0, -1])
    def test_encounters_start_at_round_one(self, bad_round: int) -> None:
        with pytest.raises(ValueError, match="round 1"):
            TimeKey(bad_round, 0, TurnPhase.START)

    def test_an_initiative_index_is_a_seat_counted_from_zero(self) -> None:
        with pytest.raises(ValueError, match="counted from 0"):
            TimeKey(1, -1, TurnPhase.START)

    def test_it_prints_compactly_for_the_log(self) -> None:
        assert str(TimeKey(3, 2, TurnPhase.END)) == "r3:i2:end"


class TestResolvingDurations:
    """A creature's "next turn" is the next *turn*, which is not the next
    boundary of the same phase — said on your own turn it means a round from
    now."""

    def test_said_on_your_own_turn_the_deadline_is_the_round_after(self) -> None:
        # The barbarian is seat 2 and rages on its own turn in round 1.
        now = TimeKey(1, 2, TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(2).resolve(now)
        assert expiry.end == TimeKey(2, 2, TurnPhase.END)

    def test_a_creature_that_has_not_acted_yet_has_its_turn_this_round(self) -> None:
        # Seat 0 vexes seat 3, whose turn in this round is still to come.
        now = TimeKey(1, 0, TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(3).resolve(now)
        assert expiry.end == TimeKey(1, 3, TurnPhase.END)

    def test_a_creature_that_has_already_acted_gets_its_turn_next_round(self) -> None:
        now = TimeKey(1, 5, TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(3).resolve(now)
        assert expiry.end == TimeKey(2, 3, TurnPhase.END)

    def test_a_turn_in_progress_is_not_the_next_turn(self) -> None:
        # Seat 3 acting: its own turn has begun, so "next" is the round after.
        now = TimeKey(1, 3, TurnPhase.END)
        expiry = Duration.until_end_of_next_turn_of(3).resolve(now)
        assert expiry.end == TimeKey(2, 3, TurnPhase.END)

    def test_the_current_turn_belongs_to_whoever_is_acting(self) -> None:
        expiry = Duration.until_end_of_current_turn().resolve(
            TimeKey(4, 1, TurnPhase.START)
        )
        assert expiry.end == TimeKey(4, 1, TurnPhase.END)
        assert expiry.through_phase

    def test_refreshing_slides_the_deadline_forward(self) -> None:
        # Rage refreshes off on_declare_attack: the same declarative Duration,
        # re-resolved against the clock of the moment, moves a round on.
        rage = Duration.until_end_of_next_turn_of(2)
        assert rage.resolve(TimeKey(1, 2, TurnPhase.START)).end == TimeKey(
            2, 2, TurnPhase.END
        )
        assert rage.resolve(TimeKey(2, 2, TurnPhase.START)).end == TimeKey(
            3, 2, TurnPhase.END
        )

    def test_the_named_constructors_pair_the_phase_with_the_flag(self) -> None:
        start = Duration.until_start_of_next_turn_of(0)
        assert (start.phase, start.through_phase) == (TurnPhase.START, False)
        end = Duration.until_end_of_next_turn_of(0)
        assert (end.phase, end.through_phase) == (TurnPhase.END, True)

    def test_an_initiative_index_is_a_seat_counted_from_zero(self) -> None:
        with pytest.raises(ValueError, match="counted from 0"):
            Duration.until_end_of_next_turn_of(-1)

    def test_a_duration_is_frozen_and_shareable(self) -> None:
        with pytest.raises(AttributeError):
            Duration.until_end_of_current_turn().through_phase = False  # type: ignore[misc]


class TestThroughPhase:
    """The load-bearing flag, proven in both directions."""

    def test_rage_survives_the_end_of_turn_hooks_it_expires_on(self) -> None:
        # "until the end of your next turn": the barbarian in seat 2 rages in
        # round 1, so the deadline is the end of its turn in round 2.
        rage = Duration.until_end_of_next_turn_of(2).resolve(
            TimeKey(1, 2, TurnPhase.START)
        )
        boundary = TimeKey(2, 2, TurnPhase.END)
        # Still attached while on_turn_end runs — damage taken during your own
        # end of turn is still resisted...
        assert not rage.expires_before(boundary)
        # ...and gone the moment those handlers are done.
        assert rage.expires_after(boundary)

    def test_reckless_attack_is_gone_before_the_start_of_turn_hooks(self) -> None:
        # "until the start of your next turn", declared in round 1 by seat 2.
        reckless = Duration.until_start_of_next_turn_of(2).resolve(
            TimeKey(1, 2, TurnPhase.START)
        )
        boundary = TimeKey(2, 2, TurnPhase.START)
        assert reckless.expires_before(boundary)
        assert reckless.expires_after(boundary)

    def test_neither_expires_before_its_boundary(self) -> None:
        rage = Duration.until_end_of_next_turn_of(2).resolve(
            TimeKey(1, 2, TurnPhase.START)
        )
        reckless = Duration.until_start_of_next_turn_of(2).resolve(
            TimeKey(1, 2, TurnPhase.START)
        )
        # The rest of the barbarian's own turn, then every earlier seat's turn
        # in the round the deadlines fall in.
        for now in (
            TimeKey(1, 2, TurnPhase.END),
            TimeKey(1, 9, TurnPhase.END),
            TimeKey(2, 0, TurnPhase.START),
            TimeKey(2, 1, TurnPhase.END),
        ):
            assert not rage.expires_before(now) and not rage.expires_after(now)
            assert not reckless.expires_before(now) and not reckless.expires_after(now)

    def test_a_missed_boundary_still_expires(self) -> None:
        # The sweep runs at every boundary, but an effect applied to a creature
        # that drops out of the order must not outlive its deadline.
        rage = Expiry(TimeKey(2, 2, TurnPhase.END), through_phase=True)
        later = TimeKey(5, 0, TurnPhase.START)
        assert rage.expires_before(later) and rage.expires_after(later)


class TestResetScopes:
    def test_once_per_turn_comes_back_on_any_turn(self) -> None:
        # Sneak Attack: spent on the rogue's own turn, back for an opportunity
        # attack as soon as somebody else's turn begins.
        resets = ResetManager()
        resets.spend("sneak_attack", Reset.TURN)
        assert resets.is_spent("sneak_attack")
        resets.turn_started(own=False)
        assert not resets.is_spent("sneak_attack")

    def test_once_on_your_turn_does_not_come_back_on_someone_elses(self) -> None:
        resets = ResetManager()
        resets.spend("second_wind", Reset.OWN_TURN)
        resets.turn_started(own=False)
        assert resets.is_spent("second_wind")
        resets.turn_started(own=True)
        assert not resets.is_spent("second_wind")

    def test_the_two_turn_scopes_are_told_apart_in_one_ledger(self) -> None:
        # The whole point of two members: the same boundary hands one back and
        # not the other.
        resets = ResetManager()
        resets.spend("any_turn", Reset.TURN)
        resets.spend("own_turn", Reset.OWN_TURN)
        resets.turn_started(own=False)
        assert not resets.is_spent("any_turn")
        assert resets.is_spent("own_turn")

    def test_your_own_turn_hands_back_both(self) -> None:
        resets = ResetManager()
        resets.spend("any_turn", Reset.TURN)
        resets.spend("own_turn", Reset.OWN_TURN)
        resets.turn_started(own=True)
        assert not resets.is_spent("any_turn")
        assert not resets.is_spent("own_turn")

    def test_a_turn_boundary_does_not_hand_back_a_rest_budget(self) -> None:
        resets = ResetManager()
        resets.spend("channel_divinity", Reset.SHORT_REST)
        resets.turn_started(own=True)
        assert resets.is_spent("channel_divinity")

    def test_a_short_rest_hands_back_only_the_short_rest_budgets(self) -> None:
        resets = ResetManager()
        resets.spend("channel_divinity", Reset.SHORT_REST)
        resets.spend("indomitable", Reset.LONG_REST)
        resets.short_rest()
        assert not resets.is_spent("channel_divinity")
        assert resets.is_spent("indomitable")

    def test_a_long_rest_hands_back_everything_a_short_rest_would(self) -> None:
        resets = ResetManager()
        resets.spend("channel_divinity", Reset.SHORT_REST)
        resets.spend("indomitable", Reset.LONG_REST)
        resets.long_rest()
        assert not resets.is_spent("channel_divinity")
        assert not resets.is_spent("indomitable")

    def test_an_unspent_budget_is_available(self) -> None:
        assert not ResetManager().is_spent("never_used")

    def test_budgets_are_independent(self) -> None:
        resets = ResetManager()
        resets.spend("sneak_attack", Reset.TURN)
        assert not resets.is_spent("great_weapon_master")

    def test_spending_a_spent_budget_is_a_caller_bug(self) -> None:
        # The dispatcher asks is_spent() first, so arriving here twice means the
        # budget check was skipped — that fails loudly rather than double-dips.
        resets = ResetManager()
        resets.spend("sneak_attack", Reset.TURN)
        with pytest.raises(ValueError, match="already spent"):
            resets.spend("sneak_attack", Reset.TURN)

    def test_scopes_are_not_strings(self) -> None:
        # Plain Enum, never StrEnum, for the reason rules/enums.py gives:
        # `scope == "turn"` must be a mypy error rather than a comparison that
        # quietly works until someone misspells it.
        assert all(not isinstance(scope, str) for scope in Reset)
        assert [scope.value for scope in Reset] == [
            "turn",
            "own_turn",
            "short_rest",
            "long_rest",
        ]
