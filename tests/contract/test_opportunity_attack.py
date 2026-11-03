"""The opportunity attack contract: the Command, the rule a creature carries,
the turn that gives the Reaction back, Disengage, and an attack made from
inside another.

Every creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Dexterity 14 (+2), every other score 10, 20 hit points and no
armour, so AC 10 + 2 = 12. A greatsword attack swings at natural + 3 + 2 =
natural + 5 against AC 12 and deals 2d6 + 3. A natural 15 hits (20 against 12)
and 2d6 rolled 4 and 4 deals 4 + 4 + 3 = 11, leaving 20 - 11 = 9.

Sources: XPHB (2024) "Combat" -> Opportunity Attacks, Reactions; "Actions" ->
Disengage; ROADMAP `w2c2` and debts 18-20, 26 of § The W0 debt register;
`docs/decisions.md` § w0g3, § w1f, § w2c.
"""

from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim.actions.base import Action
from dndsim.actions.opportunity_attack import OpportunityAttack, OpportunityAttacks
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng, ScriptedRng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import GREATSWORD, StatBlock
from dndsim.engine.attack import (
    ATTACK,
    FRESH,
    AttackContexts,
    AttackResult,
    PooledContexts,
    resolve_attack,
)
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.turn import Move, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import AttackDeclaredContext, Context, HitContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, ActionCost, Outcome

SCORES: Final = MappingProxyType(
    {
        **dict.fromkeys(Ability, 10),
        Ability.STRENGTH: 16,  # +3
        Ability.DEXTERITY: 14,  # +2
    }
)
HP: Final = 20
HIT: Final = 15
"""A natural 15: 15 + 5 = 20 against AC 12."""
ELEVEN: Final = (HIT, 4, 4)
"""One greatsword hit for 4 + 4 + 3 = 11."""


def key(initiative: int) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=14, player_character=False, tiebreak=0.5
    )


def creature(name: str, cell: Cell, *, initiative: int = 10) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=HP),
        position=cell,
        weapon=GREATSWORD,
        initiative=key(initiative),
    )


class Answers:
    """A stand-in `Gate`: answers `takes` and records every id asked about."""

    def __init__(self, *, takes: bool = True) -> None:
        self.takes = takes
        self.asked: list[str] = []

    def allows(self, gate_id: str, source: HookSource, ctx: Context) -> bool:
        self.asked.append(gate_id)
        return self.takes


def guard(
    name: str,
    cell: Cell,
    seated: World,
    *,
    rng: Rng,
    initiative: int = 10,
    takes: bool = True,
    contexts: AttackContexts = FRESH,
    side: str = "monsters",
) -> tuple[Creature, Answers]:
    """A creature seated on `side` that owns opportunity attacks."""
    owner = creature(name, cell, initiative=initiative)
    seated.seat(owner, side)
    gate = Answers(takes=takes)
    owner.dispatcher.bind(gate=gate)
    owner.attach(OpportunityAttacks(owner, world=seated, rng=rng, contexts=contexts))
    return owner, gate


def attacks(log: EventLog | None) -> list[tuple[object, ...]]:
    assert log is not None
    return [record for record in log if record[0] == ATTACK]


def east(start: Cell, squares: int) -> tuple[Cell, ...]:
    return tuple(Cell(start.x + step, start.y) for step in range(1, squares + 1))


class Standing(HookSource):
    """Writes down where its owner stands each time an attack is declared on
    it."""

    __slots__ = ("seen",)

    ID = "test.standing"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[Cell] = []

    @hook("on_incoming_attack_declared")
    def note(self, ctx: AttackDeclaredContext) -> R:
        owner = self.owner
        assert isinstance(owner, Creature)
        self.seen.append(owner.position)
        return R.APPLIED


# --- the reaction, offered by the walk ---------------------------------------


def test_leaving_reach_draws_one_attack_from_the_square_being_left() -> None:
    """The runner starts adjacent at (0, 0) and the step to (1, 0) leaves the
    guard's five feet: one swing, while the runner still stands on (0, 0), for
    11. The walk goes on to (3, 0)."""
    seated = World(log=EventLog())
    rng = ScriptedRng(list(ELEVEN))
    sentry, gate = guard("guard", Cell(-1, 0), seated, rng=rng)
    runner = creature("runner", Cell(0, 0))
    seated.seat(runner, "heroes")
    standing = Standing(runner)
    runner.attach(standing)

    walked = walk_provoking(runner, east(Cell(0, 0), 3), seated, charge=False)

    assert standing.seen == [Cell(0, 0)]
    assert runner.hp == HP - 11 == 9
    assert attacks(seated.log) == [
        (ATTACK, "guard", "runner", "Greatsword", Outcome.HIT, 11)
    ]
    assert sentry.economy.reactions == 0
    assert gate.asked == [OpportunityAttack.ID]
    assert walked == east(Cell(0, 0), 3)
    assert rng.exhausted


def test_an_ally_leaving_reach_is_not_attacked() -> None:
    seated = World(log=EventLog())
    sentry, _ = guard("guard", Cell(-1, 0), seated, rng=ScriptedRng([]))
    friend = creature("friend", Cell(0, 0))
    seated.seat(friend, "monsters")

    walk_provoking(friend, east(Cell(0, 0), 3), seated, charge=False)

    assert friend.hp == HP
    assert attacks(seated.log) == []
    assert sentry.economy.reactions == 1


def test_a_declined_opportunity_attack_spends_nothing() -> None:
    seated = World(log=EventLog())
    sentry, gate = guard("guard", Cell(-1, 0), seated, rng=ScriptedRng([]), takes=False)
    runner = creature("runner", Cell(0, 0))
    seated.seat(runner, "heroes")

    walk_provoking(runner, east(Cell(0, 0), 3), seated, charge=False)

    assert gate.asked == [OpportunityAttack.ID]
    assert runner.hp == HP
    assert attacks(seated.log) == []
    assert sentry.economy.reactions == 1


def test_one_reaction_is_one_opportunity_attack() -> None:
    """Out, back in and out again: the second leaving is never offered."""
    seated = World(log=EventLog())
    rng = ScriptedRng(list(ELEVEN))
    _, gate = guard("guard", Cell(-1, 0), seated, rng=rng)
    runner = creature("runner", Cell(0, 0))
    seated.seat(runner, "heroes")

    walk_provoking(runner, (Cell(1, 0), Cell(0, 0), Cell(1, 0)), seated, charge=False)

    assert len(attacks(seated.log)) == 1
    assert gate.asked == [OpportunityAttack.ID]
    assert runner.hp == 9


# --- the Command on its own --------------------------------------------------


def _pair() -> tuple[World, Creature, Creature]:
    seated = World(log=EventLog())
    actor = creature("guard", Cell(0, 0))
    target = creature("runner", Cell(1, 0))
    seated.seat(actor, "monsters")
    seated.seat(target, "heroes")
    return seated, actor, target


def test_the_command_is_a_reaction() -> None:
    assert OpportunityAttack.ID == "opportunity_attack"
    assert OpportunityAttack.COST is ActionCost.REACTION


def test_resolve_makes_the_attack_without_spending_the_reaction() -> None:
    """For the dispatcher, which spends it on `APPLIED`."""
    seated, actor, target = _pair()
    rng = ScriptedRng(list(ELEVEN))

    result = OpportunityAttack(actor, target, world=seated, log=seated.log).resolve(
        rng=rng
    )

    assert result == AttackResult(Outcome.HIT, 11)
    assert target.hp == 9
    assert actor.economy.reactions == 1
    assert rng.exhausted


def test_execute_spends_exactly_one_reaction() -> None:
    seated, actor, target = _pair()
    actor.economy.grant(ActionCost.REACTION)

    OpportunityAttack(actor, target, world=seated).execute(
        rng=ScriptedRng(list(ELEVEN))
    )

    assert actor.economy.reactions == 1
    assert (actor.economy.actions, actor.economy.bonus_actions) == (0, 0)


def test_resolve_refuses_an_illegal_opportunity_attack() -> None:
    seated, actor, target = _pair()
    seated.leave(target)
    seated.seat(target, "monsters")

    with pytest.raises(ValueError):
        OpportunityAttack(actor, target, world=seated).resolve(rng=ScriptedRng([]))
    assert target.hp == HP


# --- the Reaction comes back on the reactor's own turn -----------------------


class Takes:
    """A turn policy: the first offer whose id is in `wanted`, in that order of
    preference; nothing for a creature named in `idle`."""

    def __init__(self, *wanted: str, idle: tuple[str, ...] = ()) -> None:
        self.wanted = wanted
        self.idle = idle

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        if actor.name in self.idle:
            return None
        for action_id in self.wanted:
            for offer in offers:
                if action_id == offer.ID:
                    return offer
        return None


def _skirmish(
    first: int, second: int, reactor: int, *, rng: Rng
) -> tuple[World, Creature, Creature, Creature, Answers]:
    """Two heroes either side of a guard's corner, and a distant decoy east for
    them to walk toward. The guard at (-1, 1) has both (0, 0) and (0, 2) in
    reach, diagonally; any first step toward (11, ...) lands on x = 1, ten feet
    from it."""
    seated = World(log=EventLog())
    sentry, gate = guard(
        "guard",
        Cell(-1, 1),
        seated,
        rng=rng,
        initiative=reactor,
        contexts=PooledContexts(
            creature("a", Cell(0, 0)), creature("b", Cell(1, 0)), GREATSWORD
        ),
    )
    decoy = creature("decoy", Cell(12, 1), initiative=1)
    seated.seat(decoy, "monsters")
    one = creature("one", Cell(0, 0), initiative=first)
    two = creature("two", Cell(0, 2), initiative=second)
    seated.seat(one, "heroes")
    seated.seat(two, "heroes")
    return seated, sentry, one, two, gate


def test_opportunity_attacks_before_and_after_the_reactors_turn_both_resolve() -> None:
    """ROADMAP `w2c2`'s acceptance sentence. Initiative 20, 10, 5: the first
    hero walks out of reach before the guard's turn and draws its Reaction; the
    guard's turn gives it back; the second hero walks out after it and draws
    the second. Each hits for 11."""
    rng = ScriptedRng([*ELEVEN, *ELEVEN])
    seated, sentry, one, two, gate = _skirmish(20, 5, 10, rng=rng)
    policy = Takes("move", idle=("guard",))

    take_turn(seated, one, 1, policy=policy, rng=rng)
    assert sentry.economy.reactions == 0
    take_turn(seated, sentry, 1, policy=policy, rng=rng)
    assert sentry.economy.reactions == 1
    take_turn(seated, two, 1, policy=policy, rng=rng)

    assert attacks(seated.log) == [
        (ATTACK, "guard", "one", "Greatsword", Outcome.HIT, 11),
        (ATTACK, "guard", "two", "Greatsword", Outcome.HIT, 11),
    ]
    assert (one.hp, two.hp) == (9, 9)
    assert gate.asked == [OpportunityAttack.ID, OpportunityAttack.ID]
    assert rng.exhausted


def test_a_spent_reaction_is_not_offered_again_until_the_reactors_turn() -> None:
    """Initiative 20, 15, 10: both heroes walk out before the guard acts. The
    first draws the Reaction; the second is never offered — not offered and
    declined."""
    rng = ScriptedRng(list(ELEVEN))
    seated, sentry, one, two, gate = _skirmish(20, 15, 10, rng=rng)
    policy = Takes("move", idle=("guard",))

    take_turn(seated, one, 1, policy=policy, rng=rng)
    take_turn(seated, two, 1, policy=policy, rng=rng)

    assert gate.asked == [OpportunityAttack.ID]
    assert (one.hp, two.hp) == (9, HP)
    assert len(attacks(seated.log)) == 1
    assert sentry.economy.reactions == 0

    take_turn(seated, sentry, 1, policy=policy, rng=rng)
    assert sentry.economy.reactions == 1
    assert rng.exhausted


# --- Disengage (debt #20) ----------------------------------------------------


def test_a_disengaged_mover_provokes_nobody() -> None:
    """The hero Disengages and then walks out of the guard's reach: the guard is
    not even asked."""
    rng = ScriptedRng([])
    seated, sentry, one, _, gate = _skirmish(20, 5, 10, rng=rng)

    take_turn(seated, one, 1, policy=Takes("disengage", "move"), rng=rng)

    assert one.position.x >= 1, "the hero never walked"
    assert gate.asked == []
    assert attacks(seated.log) == []
    assert (one.hp, sentry.economy.reactions) == (HP, 1)


def test_a_move_that_is_not_disengaged_provokes() -> None:
    rng = ScriptedRng(list(ELEVEN))
    seated, sentry, one, _, gate = _skirmish(20, 5, 10, rng=rng)
    decoy = next(c for c in seated if c.name == "decoy")
    one.economy.turn_started(one.speed_ft)

    walked = Move(one, decoy, world=seated).execute(rng=rng)

    assert len(walked) == 6
    assert gate.asked == [OpportunityAttack.ID]
    assert one.hp == 9
    assert sentry.economy.reactions == 0


# --- an attack made from inside another (debt #26) ---------------------------


class Shove(HookSource):
    """On a hit, pushes `pushed` three squares north, free of charge: forced
    movement that leaves a reactor's reach."""

    __slots__ = ("pushed", "world")

    ID = "test.shove"

    def __init__(self, owner: Creature, *, pushed: Creature, world: World) -> None:
        super().__init__(owner)
        self.pushed = pushed
        self.world = world

    @hook("on_hit")
    def push(self, ctx: HitContext) -> R:
        start = self.pushed.position
        route = tuple(Cell(start.x, start.y + step) for step in (1, 2, 3))
        walk_provoking(self.pushed, route, self.world, charge=False)
        return R.APPLIED


def _nested(
    reaction_pool: Callable[[PooledContexts], AttackContexts], rng: ScriptedRng
) -> tuple[World, Creature, Creature, Creature, PooledContexts]:
    """The striker at (-1, 0) swings at the guard at (0, 0) on its own pool; on
    a hit it shoves its ally from (0, 1) to (0, 4), out of the guard's reach
    on the step to (0, 2). The guard's opportunity attack uses
    `reaction_pool(outer)`."""
    seated = World(log=EventLog())
    striker = creature("striker", Cell(-1, 0))
    ally = creature("ally", Cell(0, 1))
    outer = PooledContexts(striker, creature("x", Cell(5, 5)), GREATSWORD)
    sentry, _ = guard(
        "guard", Cell(0, 0), seated, rng=rng, contexts=reaction_pool(outer)
    )
    seated.seat(striker, "heroes")
    seated.seat(ally, "heroes")
    striker.attach(Shove(striker, pushed=ally, world=seated))
    return seated, striker, ally, sentry, outer


def test_a_reaction_attack_inside_another_attack_resolves_on_its_own_pool() -> None:
    """The outer swing rolls 15 and hits; inside its `on_hit`, the shove draws
    the guard's opportunity attack on a second pool: 15, 4 + 4 + 3 = 11 on the
    ally. The outer attack then rolls its own damage, 1 + 1 + 3 = 5, on the
    guard. Neither takes the other's numbers."""
    rng = ScriptedRng([HIT, *ELEVEN, 1, 1])
    seated, striker, ally, sentry, outer = _nested(
        lambda outer: PooledContexts(
            creature("p", Cell(7, 7)), creature("q", Cell(8, 7)), GREATSWORD
        ),
        rng,
    )

    result = resolve_attack(striker, sentry, rng=rng, contexts=outer, log=seated.log)

    assert result == AttackResult(Outcome.HIT, 5)
    assert sentry.hp == HP - 5 == 15
    assert ally.hp == 9
    assert ally.position == Cell(0, 4)
    assert attacks(seated.log) == [
        (ATTACK, "guard", "ally", "Greatsword", Outcome.HIT, 11),
        (ATTACK, "striker", "guard", "Greatsword", Outcome.HIT, 5),
    ]
    assert sentry.economy.reactions == 0
    assert rng.exhausted


def test_the_outer_pool_serves_the_next_attack_after_a_nested_one() -> None:
    """Both pools are released: a second outer swing (natural 15, 2 + 2 + 3 =
    7) resolves on the same pool, and the guard, its Reaction spent, makes no
    second opportunity attack on the shove back out."""
    rng = ScriptedRng([HIT, *ELEVEN, 1, 1, HIT, 2, 2])
    seated, striker, ally, sentry, outer = _nested(
        lambda outer: PooledContexts(
            creature("p", Cell(7, 7)), creature("q", Cell(8, 7)), GREATSWORD
        ),
        rng,
    )
    resolve_attack(striker, sentry, rng=rng, contexts=outer, log=seated.log)
    ally.position = Cell(0, 1)

    again = resolve_attack(striker, sentry, rng=rng, contexts=outer, log=seated.log)

    assert again == AttackResult(Outcome.HIT, 7)
    assert sentry.hp == HP - 5 - 7 == 8
    assert ally.hp == 9
    assert rng.exhausted


def test_a_reaction_attack_on_the_outer_attacks_own_pool_still_raises() -> None:
    """A second pool, never a deeper one: the same pool re-entered is
    refused."""
    rng = ScriptedRng([HIT, *ELEVEN, 1, 1])
    seated, striker, _, sentry, outer = _nested(lambda outer: outer, rng)

    with pytest.raises(EngineAssemblyError, match="sharing this context pool"):
        resolve_attack(striker, sentry, rng=rng, contexts=outer, log=seated.log)
