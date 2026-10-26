"""The w0d contract: the clock orders play, durations pin themselves to it, and
once-per budgets come back at the boundary they named.

Sources: `docs/decisions.md` § w0d; ROADMAP w0d.
"""

import math

import pytest

from dndsim.core.timeline import (
    Duration,
    Expiry,
    InitiativeKey,
    Reset,
    ResetManager,
    TimeKey,
    TurnPhase,
)


def seat(n: int) -> InitiativeKey:
    """The nth place in an order where everyone rolled the same initiative.

    Only the draw separates the seats, spaced a tenth apart.
    """
    return InitiativeKey(
        initiative=20, dexterity=10, player_character=True, tiebreak=n / 10
    )


class TestInitiativeKey:
    """Place in the order, not seat number — and the tie-break rule, which is
    the comparison itself."""

    def test_the_higher_initiative_acts_first(self) -> None:
        quick = InitiativeKey(21, 10, True, 0.0)
        slow = InitiativeKey(4, 10, True, 0.0)
        assert quick < slow

    def test_a_bad_roll_on_a_clumsy_creature_can_be_negative(self) -> None:
        # Natural 1 at Dexterity 1: 1 + (-5) = -4. Still an order, still last.
        assert InitiativeKey(1, 10, True, 0.0) < InitiativeKey(-4, 1, True, 0.0)

    def test_a_tie_breaks_on_the_dexterity_score(self) -> None:
        nimble = InitiativeKey(15, 18, True, 0.9)
        stiff = InitiativeKey(15, 8, True, 0.1)
        assert nimble < stiff

    def test_a_tie_on_dexterity_too_gives_it_to_the_player_character(self) -> None:
        pc = InitiativeKey(15, 14, True, 0.9)
        monster = InitiativeKey(15, 14, False, 0.1)
        assert pc < monster

    def test_the_last_resort_is_the_random_draw(self) -> None:
        first = InitiativeKey(15, 14, False, 0.25)
        second = InitiativeKey(15, 14, False, 0.75)
        assert first < second

    def test_the_tie_breaks_are_tried_in_order(self) -> None:
        # Every rung of the ladder in one sort: initiative, then Dexterity
        # score, then PC over monster, then the draw.
        shuffled = [
            InitiativeKey(15, 14, False, 0.25),
            InitiativeKey(15, 14, True, 0.9),
            InitiativeKey(15, 20, False, 0.9),
            InitiativeKey(15, 14, False, 0.75),
            InitiativeKey(22, 3, False, 0.9),
        ]
        assert sorted(shuffled) == [
            InitiativeKey(22, 3, False, 0.9),
            InitiativeKey(15, 20, False, 0.9),
            InitiativeKey(15, 14, True, 0.9),
            InitiativeKey(15, 14, False, 0.25),
            InitiativeKey(15, 14, False, 0.75),
        ]

    def test_the_order_is_total(self) -> None:
        earlier, later = seat(0), seat(1)
        assert earlier <= later and earlier < later
        assert later >= earlier and later > earlier
        assert earlier <= earlier and earlier >= earlier
        assert not earlier < earlier and not earlier > earlier

    def test_the_same_place_is_the_same_value(self) -> None:
        assert InitiativeKey(15, 14, True, 0.5) == InitiativeKey(15, 14, True, 0.5)
        assert len({seat(0), seat(0)}) == 1

    def test_a_place_in_the_order_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            seat(0).initiative = 30  # type: ignore[misc]

    def test_a_dexterity_score_is_never_a_modifier(self) -> None:
        # The likely mistake is passing +3 where the score 16 belongs; scores
        # below 1 are the part of that mistake this can actually catch.
        with pytest.raises(ValueError, match="Dexterity"):
            InitiativeKey(15, 0, True, 0.0)

    @pytest.mark.parametrize("outside", [1.0, -0.1, 2.5])
    def test_a_tiebreak_is_one_draw_from_the_unit_interval(
        self, outside: float
    ) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            InitiativeKey(15, 14, True, outside)

    def test_a_nan_draw_is_refused_because_it_is_not_an_order(self) -> None:
        # NaN compares false against everything, so one of them anywhere in the
        # order stops it being an order at all — sorting would silently produce
        # a different sequence depending on where NaN started in the list.
        with pytest.raises(ValueError, match=r"\[0, 1\)"):
            InitiativeKey(15, 14, True, float("nan"))

    def test_a_shared_turn_step_is_counted_from_zero(self) -> None:
        with pytest.raises(ValueError, match="counted from 0"):
            InitiativeKey(15, 14, True, 0.5, (-1,))

    def test_it_prints_compactly_for_the_log(self) -> None:
        assert str(InitiativeKey(17, 14, True, 0.25)) == "i17#0.250000"
        assert str(InitiativeKey(17, 14, True, 0.25, (1, 0))) == "i17#0.250000+1+0"


class TestAChangingEncounter:
    """Arrivals and deaths move nobody else on the clock."""

    def test_a_new_arrival_does_not_move_anybody_else(self) -> None:
        before = [seat(0), seat(1), seat(2)]
        arrival = InitiativeKey(25, 12, False, 0.0)
        after = sorted([*before, arrival])
        assert after == [arrival, seat(0), seat(1), seat(2)]
        # Every original key compares exactly as it did before the arrival.
        assert before == sorted(before)

    def test_a_joiner_draws_its_own_tiebreak_and_nobody_is_redrawn(self) -> None:
        tied = [seat(2), seat(4)]
        joiner = InitiativeKey(20, 10, True, 0.35)
        assert sorted([*tied, joiner]) == [seat(2), joiner, seat(4)]

    def test_a_death_does_not_renumber_the_survivors(self) -> None:
        survivors = sorted([seat(0), seat(2)])
        assert survivors == [seat(0), seat(2)]
        # Nobody was promoted into seat(1): it is a place, not a queue position.
        assert seat(0) < seat(1) < seat(2)

    def test_a_deadline_survives_the_death_it_was_anchored_to(self) -> None:
        # Vex on a creature that dies before its next turn: the deadline is a
        # position, so the clock passes it whether or not anyone stands there.
        doomed = seat(3)
        vex = Duration.until_end_of_next_turn_of(doomed).resolve(
            TimeKey(1, seat(0), TurnPhase.START)
        )
        assert vex.end == TimeKey(1, doomed, TurnPhase.END)
        # The encounter runs on past that position without that turn happening.
        assert vex.expires_after(TimeKey(1, seat(4), TurnPhase.START))

    def test_a_summon_takes_its_turn_immediately_after_its_summoner(self) -> None:
        # `docs/decisions.md` § w0d (the summon chain).
        summoner = seat(1)
        spirit = summoner.sharing_turn()
        assert summoner < spirit < seat(2)

    def test_a_summon_sits_inside_the_count_however_close_the_next_draw_is(
        self,
    ) -> None:
        # The next creature's draw is one ULP away, and the spirit still fits
        # between them.
        summoner = InitiativeKey(20, 10, True, 0.5)
        crowded = InitiativeKey(20, 10, True, math.nextafter(0.5, 1.0))
        assert summoner < summoner.sharing_turn() < crowded

    def test_several_summons_share_one_count_in_the_order_they_arrived(self) -> None:
        summoner = seat(1)
        first, second = summoner.sharing_turn(0), summoner.sharing_turn(1)
        assert summoner < first < second < seat(2)

    def test_the_chain_nests_so_a_summons_own_summon_lands_behind_it(self) -> None:
        summoner = seat(1)
        spirit = summoner.sharing_turn(0)
        its_own = spirit.sharing_turn(0)
        assert summoner < spirit < its_own < summoner.sharing_turn(1) < seat(2)

    def test_a_summon_inherits_the_count_it_shares(self) -> None:
        summoner = InitiativeKey(17, 14, True, 0.25)
        spirit = summoner.sharing_turn()
        assert (
            spirit.initiative,
            spirit.dexterity,
            spirit.player_character,
            spirit.tiebreak,
        ) == (17, 14, True, 0.25)

    def test_a_summon_that_rolls_its_own_initiative_is_an_ordinary_key(self) -> None:
        # The summon rolls for itself instead of sharing its summoner's count:
        # it lands wherever its roll puts it and moves nobody.
        rolled = InitiativeKey(21, 16, True, 0.4)
        assert sorted([seat(0), rolled, seat(1)]) == [rolled, seat(0), seat(1)]

    def test_a_creature_joining_later_in_the_round_still_acts_this_round(
        self,
    ) -> None:
        now = TimeKey(2, seat(1), TurnPhase.START)
        arrival = seat(5)
        expiry = Duration.until_end_of_next_turn_of(arrival).resolve(now)
        assert expiry.end == TimeKey(2, arrival, TurnPhase.END)

    def test_a_creature_joining_behind_the_current_turn_waits_a_round(self) -> None:
        now = TimeKey(2, seat(5), TurnPhase.START)
        arrival = seat(1)
        expiry = Duration.until_end_of_next_turn_of(arrival).resolve(now)
        assert expiry.end == TimeKey(3, arrival, TurnPhase.END)


class TestTimeKey:
    def test_the_phases_of_a_turn_are_in_the_order_they_happen(self) -> None:
        assert TurnPhase.START.value < TurnPhase.END.value

    def test_a_turn_starts_before_it_ends(self) -> None:
        assert TimeKey(1, seat(0), TurnPhase.START) < TimeKey(1, seat(0), TurnPhase.END)

    def test_earlier_in_the_initiative_order_is_earlier_in_the_round(self) -> None:
        assert TimeKey(1, seat(0), TurnPhase.END) < TimeKey(1, seat(1), TurnPhase.START)

    def test_a_round_is_later_than_every_moment_of_the_one_before(self) -> None:
        assert TimeKey(1, seat(7), TurnPhase.END) < TimeKey(2, seat(0), TurnPhase.START)

    def test_the_natural_ordering_is_the_order_of_play(self) -> None:
        # One and a half rounds of a three-creature encounter, shuffled.
        shuffled = [
            TimeKey(2, seat(0), TurnPhase.START),
            TimeKey(1, seat(1), TurnPhase.END),
            TimeKey(1, seat(0), TurnPhase.START),
            TimeKey(1, seat(2), TurnPhase.END),
            TimeKey(1, seat(1), TurnPhase.START),
            TimeKey(1, seat(0), TurnPhase.END),
            TimeKey(1, seat(2), TurnPhase.START),
        ]
        assert sorted(shuffled) == [
            TimeKey(1, seat(0), TurnPhase.START),
            TimeKey(1, seat(0), TurnPhase.END),
            TimeKey(1, seat(1), TurnPhase.START),
            TimeKey(1, seat(1), TurnPhase.END),
            TimeKey(1, seat(2), TurnPhase.START),
            TimeKey(1, seat(2), TurnPhase.END),
            TimeKey(2, seat(0), TurnPhase.START),
        ]

    def test_the_order_is_total(self) -> None:
        earlier = TimeKey(3, seat(1), TurnPhase.START)
        later = TimeKey(3, seat(1), TurnPhase.END)
        assert earlier <= later and earlier < later
        assert later >= earlier and later > earlier
        assert earlier <= earlier and earlier >= earlier
        assert not earlier > earlier and not earlier < earlier

    def test_the_same_moment_is_the_same_value(self) -> None:
        assert TimeKey(2, seat(3), TurnPhase.END) == TimeKey(2, seat(3), TurnPhase.END)
        moments = {
            TimeKey(2, seat(3), TurnPhase.END),
            TimeKey(2, seat(3), TurnPhase.END),
        }
        assert len(moments) == 1

    def test_a_time_key_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            TimeKey(1, seat(0), TurnPhase.START).round = 2  # type: ignore[misc]

    @pytest.mark.parametrize("bad_round", [0, -1])
    def test_encounters_start_at_round_one(self, bad_round: int) -> None:
        with pytest.raises(ValueError, match="round 1"):
            TimeKey(bad_round, seat(0), TurnPhase.START)

    def test_it_prints_compactly_for_the_log(self) -> None:
        assert str(TimeKey(3, seat(2), TurnPhase.END)) == "r3:i20#0.200000:end"


class TestResolvingDurations:
    """A creature's "next turn" is the next *turn*, which is not the next
    boundary of the same phase — said on your own turn it means a round from
    now."""

    def test_said_on_your_own_turn_the_deadline_is_the_round_after(self) -> None:
        # The barbarian is third in the order and rages on its own turn in
        # round 1.
        now = TimeKey(1, seat(2), TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(seat(2)).resolve(now)
        assert expiry.end == TimeKey(2, seat(2), TurnPhase.END)

    def test_a_creature_that_has_not_acted_yet_has_its_turn_this_round(self) -> None:
        # The first in the order vexes the fourth, whose turn is still to come.
        now = TimeKey(1, seat(0), TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(seat(3)).resolve(now)
        assert expiry.end == TimeKey(1, seat(3), TurnPhase.END)

    def test_a_creature_that_has_already_acted_gets_its_turn_next_round(self) -> None:
        now = TimeKey(1, seat(5), TurnPhase.START)
        expiry = Duration.until_end_of_next_turn_of(seat(3)).resolve(now)
        assert expiry.end == TimeKey(2, seat(3), TurnPhase.END)

    def test_a_turn_in_progress_is_not_the_next_turn(self) -> None:
        # That creature is acting: its own turn has begun, so "next" is the
        # round after.
        now = TimeKey(1, seat(3), TurnPhase.END)
        expiry = Duration.until_end_of_next_turn_of(seat(3)).resolve(now)
        assert expiry.end == TimeKey(2, seat(3), TurnPhase.END)

    def test_the_current_turn_belongs_to_whoever_is_acting(self) -> None:
        expiry = Duration.until_end_of_current_turn().resolve(
            TimeKey(4, seat(1), TurnPhase.START)
        )
        assert expiry.end == TimeKey(4, seat(1), TurnPhase.END)
        assert expiry.through_phase

    def test_refreshing_slides_the_deadline_forward(self) -> None:
        # The same Duration, re-resolved a round later, moves a round on.
        rage = Duration.until_end_of_next_turn_of(seat(2))
        assert rage.resolve(TimeKey(1, seat(2), TurnPhase.START)).end == TimeKey(
            2, seat(2), TurnPhase.END
        )
        assert rage.resolve(TimeKey(2, seat(2), TurnPhase.START)).end == TimeKey(
            3, seat(2), TurnPhase.END
        )

    def test_the_named_constructors_pair_the_phase_with_the_flag(self) -> None:
        start = Duration.until_start_of_next_turn_of(seat(0))
        assert (start.phase, start.through_phase) == (TurnPhase.START, False)
        end = Duration.until_end_of_next_turn_of(seat(0))
        assert (end.phase, end.through_phase) == (TurnPhase.END, True)

    def test_a_duration_is_frozen_and_shareable(self) -> None:
        with pytest.raises(AttributeError):
            Duration.until_end_of_current_turn().through_phase = False  # type: ignore[misc]


class TestThroughPhase:
    """`through_phase` in both directions."""

    def test_rage_survives_the_end_of_turn_hooks_it_expires_on(self) -> None:
        # "until the end of your next turn": the barbarian, third in the order,
        # rages in round 1, so the deadline is the end of its turn in round 2.
        rage = Duration.until_end_of_next_turn_of(seat(2)).resolve(
            TimeKey(1, seat(2), TurnPhase.START)
        )
        boundary = TimeKey(2, seat(2), TurnPhase.END)
        # Still attached while on_turn_end runs — damage taken during your own
        # end of turn is still resisted...
        assert not rage.expires_before(boundary)
        # ...and gone the moment those handlers are done.
        assert rage.expires_after(boundary)

    def test_reckless_attack_is_gone_before_the_start_of_turn_hooks(self) -> None:
        # "until the start of your next turn", declared in round 1.
        reckless = Duration.until_start_of_next_turn_of(seat(2)).resolve(
            TimeKey(1, seat(2), TurnPhase.START)
        )
        boundary = TimeKey(2, seat(2), TurnPhase.START)
        assert reckless.expires_before(boundary)
        assert reckless.expires_after(boundary)

    def test_neither_expires_before_its_boundary(self) -> None:
        rage = Duration.until_end_of_next_turn_of(seat(2)).resolve(
            TimeKey(1, seat(2), TurnPhase.START)
        )
        reckless = Duration.until_start_of_next_turn_of(seat(2)).resolve(
            TimeKey(1, seat(2), TurnPhase.START)
        )
        # The rest of the barbarian's own turn, then every earlier turn in the
        # round the deadlines fall in.
        for now in (
            TimeKey(1, seat(2), TurnPhase.END),
            TimeKey(1, seat(9), TurnPhase.END),
            TimeKey(2, seat(0), TurnPhase.START),
            TimeKey(2, seat(1), TurnPhase.END),
        ):
            assert not rage.expires_before(now) and not rage.expires_after(now)
            assert not reckless.expires_before(now) and not reckless.expires_after(now)

    def test_a_missed_boundary_still_expires(self) -> None:
        # A sweep long after the deadline still expires it.
        rage = Expiry(TimeKey(2, seat(2), TurnPhase.END), through_phase=True)
        later = TimeKey(5, seat(0), TurnPhase.START)
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
        # The same boundary hands one back and not the other.
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
        # A second spend fails loudly rather than double-dipping.
        resets = ResetManager()
        resets.spend("sneak_attack", Reset.TURN)
        with pytest.raises(ValueError, match="already spent"):
            resets.spend("sneak_attack", Reset.TURN)

    def test_scopes_are_not_strings(self) -> None:
        # Plain Enum, never StrEnum (`docs/decisions.md` § w0b).
        assert all(not isinstance(scope, str) for scope in Reset)
        assert [scope.value for scope in Reset] == [
            "turn",
            "own_turn",
            "short_rest",
            "long_rest",
        ]
