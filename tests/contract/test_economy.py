"""The w1a action-economy contract: what a creature may still spend, and when.

`ActionEconomy` is the first real implementer of the `Economy` protocol
`hooks/dispatch.py` has been declaring since `w0g` — the contract test that
`w0g` was owed and that ROADMAP § The W0 debt register files under #10, because
"the protocol and the class drift, and `costs=` stops being paid" is a failure
nothing else in the project would notice.

Four properties are worth more than the arithmetic:

- **Action Surge is a grant and nothing else** (DESIGN.md § APL). A feature that
  hands its owner another Action calls one method; the turn loop re-polls and
  finds an Action on the table. No branch anywhere knows why.
- **A reaction comes back at the start of your own turn, never at the top of a
  round** (DESIGN.md § Failing loudly). The invariant is stated on the turn
  boundary deliberately: a creature high in initiative may react on an enemy's
  turn, take its own turn, and react again in the same round, and an accounting
  per round would raise on that legal sequence.
- **Overdrawing raises rather than clamping.** Asking is free, so reaching the
  refusal means a caller decided not to ask — and the bug it catches is the one
  M0 found by accident, an entry charging an Action for a Bonus Action, which
  cost 0.07% of the rate and would never be found by watching the rate
  (docs/decisions.md § The W0 plan review).
- **Movement is feet, not slots.** `ActionCost` has no member for it on purpose,
  so a Dash grants feet exactly as Action Surge grants an Action.

Sources: XPHB (2024) "Combat" (Your Turn, Reactions, Movement, the Dash action);
DESIGN.md § APL and mid-attack decisions, § Failing loudly — layered; ROADMAP
w1a, w2b, w2c.
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
    """An economy at the top of its owner's turn, which is the state almost
    every question below is asked in."""
    economy = ActionEconomy()
    economy.turn_started(SPEED)
    return economy


# --- the protocol debt w0g left to this task ---------------------------------


def test_the_action_economy_is_the_economy_the_dispatcher_asks() -> None:
    """`hooks/dispatch.py` declares `Economy` rather than importing this class,
    because `hooks/` sits below `creatures/` and the dependency points one way
    only. The annotation is what mypy checks; the membership assertion is what
    fails loudly if the protocol grows a member this class does not have."""
    economy: Economy = ActionEconomy()
    assert frozenset(name for name in vars(Economy) if not name.startswith("_")) == {
        "can_spend",
        "spend",
    }
    assert economy.can_spend(REACTION)


def test_a_creatures_reaction_runs_once_and_comes_back_on_its_turn() -> None:
    """The whole loop, through the real dispatcher rather than a stand-in: a
    reaction feature is offered, pays for itself out of the creature's own
    economy, and is not offered again until that creature's turn begins.
    `tests/contract/test_dispatch.py` proves the dispatcher's half against a
    fake; this is the half `w1a` owes, and the two meeting is the drift the
    debt register (#10) is about."""
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
    """A Rage, a Longstrider or a set of heavy armour changes a speed between
    turns, and a ledger holding its own copy would be the second place a speed
    could be written — the same argument that keeps the damage order in one
    file."""
    economy = started()
    economy.turn_started(SPEED + 10)
    assert economy.movement_ft == SPEED + 10


def test_a_reaction_comes_back_on_your_own_turn_and_not_before() -> None:
    """The invariant DESIGN.md § Failing loudly states on the turn boundary: a
    creature that reacted on an enemy's turn has nothing until its own turn
    begins, and then has it again — including for a second reaction later in the
    same round, which is legal and which a per-round account would refuse."""
    economy = started()
    economy.spend(REACTION)
    assert not economy.can_spend(REACTION)

    economy.turn_started(SPEED)
    assert economy.can_spend(REACTION)
    economy.spend(REACTION)
    assert not economy.can_spend(REACTION)


# --- grants ------------------------------------------------------------------


def test_action_surge_is_a_grant_and_needs_no_special_case() -> None:
    """Action Surge "simply grants another action slot and the loop asks again"
    (DESIGN.md § APL). The whole of it, from the economy's side."""
    economy = started()
    economy.spend(ACTION)
    assert not economy.can_spend(ACTION)

    economy.grant(ACTION)
    assert economy.can_spend(ACTION)
    economy.spend(ACTION)
    assert not economy.can_spend(ACTION)


def test_a_grant_of_two_leaves_one_after_the_first_is_spent() -> None:
    """Counts rather than flags: a creature granted two Actions and having spent
    one has one left, which is a thing no boolean can say."""
    economy = started()
    economy.spend(ACTION)
    economy.grant(ACTION, 2)
    economy.spend(ACTION)
    assert economy.actions == 1


def test_a_grant_of_nothing_is_refused_rather_than_ignored() -> None:
    """A feature granting zero of something has a bug in it, and the quiet
    version of that bug is a feature that appears to work."""
    with pytest.raises(EconomyError, match="grants nothing"):
        started().grant(ACTION, 0)


# --- spending what is not there ----------------------------------------------


def test_spending_an_action_that_is_not_there_raises() -> None:
    """Asking is free — `can_spend()` answers before anything is spent and the
    turn loop filters its offers through it — so this is a caller that decided
    not to ask. Clamping at zero instead would hide the M0 finding: an entry
    charging an Action for a Bonus Action, at 0.07% of the rate."""
    economy = started()
    economy.spend(ACTION)
    with pytest.raises(EconomyError, match="no Action left"):
        economy.spend(ACTION)


def test_the_refusal_for_a_second_reaction_names_the_rule_it_broke() -> None:
    """The error is read by whoever wrote the caller, and the caller's likeliest
    mistake here is accounting per round rather than per turn."""
    economy = started()
    economy.spend(REACTION)
    with pytest.raises(EconomyError, match="start of each of your own turns"):
        economy.spend(REACTION)


def test_a_bonus_action_and_an_action_are_not_interchangeable() -> None:
    """The M0 finding in one assertion: a creature that has spent its Action
    still has its Bonus Action, and spending the wrong one is not a rounding
    error in the rate but a different turn."""
    economy = started()
    economy.spend(ACTION)
    assert economy.can_spend(BONUS)
    economy.spend(BONUS)
    assert not economy.can_spend(BONUS)


# --- movement ----------------------------------------------------------------


def test_movement_is_spent_in_feet_and_runs_out() -> None:
    """A five-foot step is the unit the mover walks in, so this is asked once
    per square rather than once per move (`engine/triggers.py`)."""
    economy = started()
    for _ in range(SPEED // 5):
        assert economy.can_move(5)
        economy.move(5)
    assert not economy.can_move(5)
    assert economy.movement_ft == 0


def test_a_route_longer_than_the_speed_is_refused_at_the_step_that_pays() -> None:
    """A creature that runs out halfway has still crossed the cells it crossed
    (docs/decisions.md § w0g2), which is why the turn loop asks per step."""
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
    """Spending a negative number of feet would hand movement back, which is a
    Dash spelled as a bug."""
    with pytest.raises(EconomyError, match="not a distance"):
        started().move(-5)
    with pytest.raises(EconomyError, match="grants nothing"):
        started().grant_movement(0)


def test_movement_does_not_come_out_of_an_action() -> None:
    """`ActionCost` has no movement member on purpose (`rules/enums.py`):
    movement is measured in feet, not in slots, and a creature that has walked
    its whole speed still has everything else."""
    economy = started()
    economy.move(SPEED)
    assert economy.can_spend(ACTION)
    assert economy.can_spend(BONUS)
    assert economy.can_spend(REACTION)
