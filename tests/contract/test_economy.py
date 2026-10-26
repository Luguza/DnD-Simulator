"""The `ActionEconomy` contract: what a creature may still spend, and when.

Sources: XPHB (2024) "Combat" (Your Turn, Reactions, Movement, the Dash action);
DESIGN.md § APL and mid-attack decisions, § Failing loudly — layered;
`docs/decisions.md` § w1a.
"""

from typing import Final

import pytest

from dndsim.core.errors import EconomyError
from dndsim.core.geometry import Cell
from dndsim.creatures.creature import Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.stats import StatBlock
from dndsim.hooks.contexts import DamageBag, HitContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_HIT
from dndsim.hooks.dispatch import Economy
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, ActionCost, Outcome

ACTION: Final = ActionCost.ACTION
BONUS: Final = ActionCost.BONUS_ACTION
REACTION: Final = ActionCost.REACTION

SPEED: Final = 30
SCORES: Final = dict.fromkeys(Ability, 10)


class Riposte(HookSource):
    """A reaction that always wants to be taken, so that what stops it is the
    economy and nothing else."""

    __slots__ = ("swings",)

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.swings = 0

    @hook("on_hit", costs=ActionCost.REACTION)
    def riposte(self, ctx: HitContext) -> R:
        self.swings += 1
        return R.APPLIED


def hit_on(defender: Creature) -> HitContext:
    return HitContext(
        attacker=defender,
        defender=defender,
        weapon=None,
        outcome=Outcome.HIT,
        damage=DamageBag(),
    )


def started() -> ActionEconomy:
    """An economy at the top of its owner's turn."""
    economy = ActionEconomy()
    economy.turn_started(SPEED)
    return economy


# --- the Economy protocol ----------------------------------------------------


def test_the_action_economy_is_the_economy_the_dispatcher_asks() -> None:
    """`ActionEconomy` satisfies the dispatcher's `Economy` protocol, and the
    protocol has no member beyond `can_spend` and `spend`."""
    economy: Economy = ActionEconomy()
    assert frozenset(name for name in vars(Economy) if not name.startswith("_")) == {
        "can_spend",
        "spend",
    }
    assert economy.can_spend(REACTION)


def test_a_creatures_reaction_runs_once_and_comes_back_on_its_turn() -> None:
    """Through the real dispatcher, a reaction feature pays for itself out of
    its creature's own economy and is not offered again until that creature's
    turn begins."""
    fighter = Creature(
        name="fighter",
        stats=StatBlock(abilities=SCORES, max_hp=20),
        position=Cell(0, 0),
    )
    reaction = Riposte(fighter)
    fighter.attach(reaction)
    fighter.economy.turn_started(SPEED)

    fighter.dispatch(ON_HIT, hit_on(fighter))
    fighter.dispatch(ON_HIT, hit_on(fighter))
    assert reaction.swings == 1, "a spent reaction was offered again"
    assert not fighter.economy.can_spend(REACTION)

    fighter.economy.turn_started(SPEED)
    fighter.dispatch(ON_HIT, hit_on(fighter))
    assert reaction.swings == 2


# --- what a turn hands back --------------------------------------------------


def test_a_creature_starts_an_encounter_with_its_reaction_and_nothing_else() -> None:
    """Initiative has been rolled and nobody's turn has begun. A creature may
    take an opportunity attack in the first round before its own turn comes
    round, and may not take an Action, a Bonus Action or a step until it does."""
    economy = ActionEconomy()
    assert economy.can_spend(REACTION)
    assert not economy.can_spend(ACTION)
    assert not economy.can_spend(BONUS)
    assert economy.movement_ft == 0


def test_a_turn_hands_back_one_of_each_and_a_full_speed() -> None:
    economy = started()
    assert (economy.actions, economy.bonus_actions, economy.reactions) == (1, 1, 1)
    assert economy.movement_ft == SPEED


def test_the_speed_is_the_creatures_speed_now_rather_than_a_stored_copy() -> None:
    """`turn_started()` hands back the speed it is given, not a stored copy
    (`docs/decisions.md` § w1a)."""
    economy = started()
    economy.turn_started(SPEED + 10)
    assert economy.movement_ft == SPEED + 10


def test_a_reaction_comes_back_on_your_own_turn_and_not_before() -> None:
    """A spent reaction is unavailable until the creature's own turn begins,
    and is then spendable again in the same round (DESIGN.md § Failing
    loudly)."""
    economy = started()
    economy.spend(REACTION)
    assert not economy.can_spend(REACTION)

    economy.turn_started(SPEED)
    assert economy.can_spend(REACTION)
    economy.spend(REACTION)
    assert not economy.can_spend(REACTION)


# --- grants ------------------------------------------------------------------


def test_action_surge_is_a_grant_and_needs_no_special_case() -> None:
    """A granted Action is spendable after the turn's own is spent
    (DESIGN.md § APL)."""
    economy = started()
    economy.spend(ACTION)
    assert not economy.can_spend(ACTION)

    economy.grant(ACTION)
    assert economy.can_spend(ACTION)
    economy.spend(ACTION)
    assert not economy.can_spend(ACTION)


def test_a_grant_of_two_leaves_one_after_the_first_is_spent() -> None:
    """A creature granted two Actions and having spent one has one left."""
    economy = started()
    economy.spend(ACTION)
    economy.grant(ACTION, 2)
    economy.spend(ACTION)
    assert economy.actions == 1


def test_a_grant_of_nothing_is_refused_rather_than_ignored() -> None:
    """A grant of zero raises rather than being ignored."""
    with pytest.raises(EconomyError, match="grants nothing"):
        started().grant(ACTION, 0)


# --- spending what is not there ----------------------------------------------


def test_spending_an_action_that_is_not_there_raises() -> None:
    """Overdrawing raises rather than clamping (`docs/decisions.md` § w1a)."""
    economy = started()
    economy.spend(ACTION)
    with pytest.raises(EconomyError, match="no Action left"):
        economy.spend(ACTION)


def test_the_refusal_for_a_second_reaction_names_the_rule_it_broke() -> None:
    """The refusal names the per-turn rule a per-round account breaks."""
    economy = started()
    economy.spend(REACTION)
    with pytest.raises(EconomyError, match="start of each of your own turns"):
        economy.spend(REACTION)


def test_a_bonus_action_and_an_action_are_not_interchangeable() -> None:
    """A creature that has spent its Action still has its Bonus Action."""
    economy = started()
    economy.spend(ACTION)
    assert economy.can_spend(BONUS)
    economy.spend(BONUS)
    assert not economy.can_spend(BONUS)


# --- movement ----------------------------------------------------------------


def test_movement_is_spent_in_feet_and_runs_out() -> None:
    """Movement is spent five feet at a time until the speed is gone."""
    economy = started()
    for _ in range(SPEED // 5):
        assert economy.can_move(5)
        economy.move(5)
    assert not economy.can_move(5)
    assert economy.movement_ft == 0


def test_a_route_longer_than_the_speed_is_refused_at_the_step_that_pays() -> None:
    """The step that would overdraw the remaining speed raises."""
    economy = started()
    economy.move(SPEED)
    with pytest.raises(EconomyError, match="more than the 0 ft"):
        economy.move(5)


def test_dash_grants_feet_exactly_as_action_surge_grants_an_action() -> None:
    economy = started()
    economy.move(SPEED)
    economy.grant_movement(SPEED)
    assert economy.movement_ft == SPEED
    assert economy.can_move(SPEED)


def test_a_negative_distance_is_not_a_move() -> None:
    """A negative move and a zero movement grant both raise."""
    with pytest.raises(EconomyError, match="not a distance"):
        started().move(-5)
    with pytest.raises(EconomyError, match="grants nothing"):
        started().grant_movement(0)


def test_movement_does_not_come_out_of_an_action() -> None:
    """A creature that has walked its whole speed still has its Action, Bonus
    Action and Reaction."""
    economy = started()
    economy.move(SPEED)
    assert economy.can_spend(ACTION)
    assert economy.can_spend(BONUS)
    assert economy.can_spend(REACTION)
