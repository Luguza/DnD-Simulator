"""The encounter contract: the roster, the order, the rounds, who joins and
leaves, and the clock and log the fight shares.

Every creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Dexterity 14 (+2) unless a test says otherwise, every other
score 10, no armour so AC 10 + 2 = 12, and a greatsword: natural + 5 to hit,
2d6 + 3 damage. Each initiative roll consumes two scripted faces, the d20 and
the tiebreak; face 1 draws a tiebreak of 0.0 and face 2**31 + 1 draws 0.5.
Creatures roll in the order they were seated.

Sources: XPHB (2024) "Combat" -> Initiative, Combat Step by Step; ROADMAP
`w2c`; DESIGN.md § Initiative is a key, not an index; `docs/decisions.md`
§ w2c.
"""

from collections.abc import Sequence
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim.actions.base import Action
from dndsim.actions.dodge import Dodging
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import Duration, InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.effects import (
    EFFECT_APPLIED,
    EFFECT_ENDED,
    Effect,
    EffectStore,
)
from dndsim.creatures.resources import RESOURCE_SPENT, Recovery, ResourceSpec
from dndsim.creatures.stats import GREATSWORD, StatBlock
from dndsim.engine.attack import ATTACK
from dndsim.engine.damage import DAMAGE, resolve_damage
from dndsim.engine.dying import DROPPED
from dndsim.engine.encounter import (
    ROUND_END,
    ROUND_START,
    Encounter,
    EncounterResult,
)
from dndsim.engine.query import allies_of, enemies_of, standing, standing_sides
from dndsim.engine.saves import SAVE, saving_throw
from dndsim.engine.turn import DEATH_SAVE, INITIATIVE, TURN_END, TURN_START
from dndsim.engine.world import World
from dndsim.hooks.contexts import EncounterContext, IncomingDamageContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import (
    Ability,
    DamageType,
    Outcome,
    RecoveryTrigger,
    SaveOutcome,
)

HP: Final = 20
ORIGIN: Final = Cell(0, 0)
EAST: Final = Cell(1, 0)
HALF: Final = 2**31 + 1
SLASHING: Final = DamageType.SLASHING


def scores(dexterity: int = 14) -> MappingProxyType[Ability, int]:
    return MappingProxyType(
        {
            **dict.fromkeys(Ability, 10),
            Ability.STRENGTH: 16,
            Ability.DEXTERITY: dexterity,
        }
    )


def creature(
    name: str,
    *,
    cell: Cell = ORIGIN,
    dexterity: int = 14,
    hp: int = HP,
    pc: bool = False,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=scores(dexterity), max_hp=hp),
        position=cell,
        weapon=GREATSWORD,
        player_character=pc,
    )


class Passive:
    """Takes nothing, and records each actor it was asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        self.asked.append(actor.name)
        return None


class Attacks:
    """Takes the first Attack offered, every time."""

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        return next((one for one in offers if one.ID == "attack"), None)


def turns(log: EventLog) -> list[tuple[object, ...]]:
    """`(round, name)` for every turn started, in order."""
    return [record[1:] for record in log if record[0] == TURN_START]


def summary() -> EventLog:
    return EventLog(LogLevel.SUMMARY)


# --- The order ----------------------------------------------------------------


def test_the_order_is_initiative_then_dexterity_then_pc_then_the_tiebreak() -> None:
    """Seated a, b, c, d, e:

    - a: 13 + 2 = 15, Dex 14, monster, tiebreak 0.0
    - b: 11 + 4 = 15, Dex 18 — before every other 15 on the score
    - c: 13 + 2 = 15, Dex 14, player character, tiebreak 0.5 — before a and d
    - d: 13 + 2 = 15, Dex 14, monster, tiebreak 0.5 — after a
    - e: 20 + 2 = 22 — first
    """
    log = summary()
    world = World(log=log)
    for name, side, dexterity, pc in (
        ("a", "monsters", 14, False),
        ("b", "heroes", 18, False),
        ("c", "heroes", 14, True),
        ("d", "monsters", 14, False),
        ("e", "heroes", 14, False),
    ):
        world.seat(creature(name, dexterity=dexterity, pc=pc), side)
    rng = ScriptedRng([13, 1, 11, 1, 13, HALF, 13, HALF, 20, 1])

    result = Encounter(world, policy=Passive(), rng=rng, max_rounds=1).run()

    assert turns(log) == [(1, "e"), (1, "b"), (1, "c"), (1, "a"), (1, "d")]
    assert [record[1:] for record in log if record[0] == INITIATIVE] == [
        ("a", 13, 15, 0.0),
        ("b", 11, 15, 0.0),
        ("c", 13, 15, 0.5),
        ("d", 13, 15, 0.5),
        ("e", 20, 22, 0.0),
    ]
    assert result == EncounterResult(1, None)
    assert rng.exhausted


def test_every_creature_gets_its_key_from_the_roll() -> None:
    """a: 18 + 2 = 20, tiebreak 0.5; b: 8 + 2 = 10, tiebreak 0.0."""
    world = World()
    a, b = creature("a"), creature("b")
    world.seat(a, "x")
    world.seat(b, "y")
    Encounter(
        world, policy=Passive(), rng=ScriptedRng([18, HALF, 8, 1]), max_rounds=1
    ).run()
    assert a.initiative == InitiativeKey(20, 14, False, 0.5)
    assert b.initiative == InitiativeKey(10, 14, False, 0.0)


class SeesTheOrder(HookSource):
    """Records, at the start of the encounter, whether its owner had been
    placed in the order yet."""

    __slots__ = ("placed",)

    ID = "test.sees_the_order"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.placed: list[bool] = []

    @hook("on_encounter_start")
    def start(self, ctx: EncounterContext) -> R:
        self.placed.append(self.owner.initiative is not UNSEATED)
        return R.APPLIED


def test_on_encounter_start_fires_before_initiative_is_rolled() -> None:
    world = World()
    a, b = creature("a"), creature("b")
    seer = SeesTheOrder(a)
    a.attach(seer)
    world.seat(a, "x")
    world.seat(b, "y")
    Encounter(
        world, policy=Passive(), rng=ScriptedRng([18, 1, 8, 1]), max_rounds=1
    ).run()
    assert seer.placed == [False]


# --- Rounds and the end of the fight --------------------------------------------


def two_sides(log: EventLog | None = None) -> tuple[World, Creature, Creature]:
    world = World(log=log)
    a, b = creature("a"), creature("b", cell=EAST)
    world.seat(a, "x")
    world.seat(b, "y")
    return world, a, b


A_THEN_B: Final = (18, 1, 8, 1)
"""a: 18 + 2 = 20; b: 8 + 2 = 10."""


def test_a_fight_nobody_finishes_stops_at_max_rounds_with_no_winner() -> None:
    log = summary()
    world, _, _ = two_sides(log)
    result = Encounter(
        world, policy=Passive(), rng=ScriptedRng(A_THEN_B), max_rounds=3
    ).run()
    assert result == EncounterResult(3, None)
    assert [record for record in log if record[0] in (ROUND_START, ROUND_END)] == [
        (ROUND_START, 1),
        (ROUND_END, 1),
        (ROUND_START, 2),
        (ROUND_END, 2),
        (ROUND_START, 3),
        (ROUND_END, 3),
    ]
    assert turns(log) == [(1, "a"), (1, "b"), (2, "a"), (2, "b"), (3, "a"), (3, "b")]


def test_an_encounter_lasts_at_least_one_round() -> None:
    world, _, _ = two_sides()
    with pytest.raises(ValueError):
        Encounter(world, policy=Passive(), rng=ScriptedRng([]), max_rounds=0)


def test_an_encounter_is_run_once() -> None:
    world, _, _ = two_sides()
    encounter = Encounter(
        world, policy=Passive(), rng=ScriptedRng(A_THEN_B), max_rounds=1
    )
    encounter.run()
    with pytest.raises(ValueError):
        encounter.run()


def test_a_short_scripted_fight_is_logged_in_order() -> None:
    """The fighter (18 + 2 = 20) against a 5-hit-point goblin (8 + 2 = 10).

    Round 1: the fighter rolls 2, 2 + 5 = 7 against AC 12, a miss; the goblin
    rolls 2, a miss. Round 2: the fighter rolls 15, 15 + 5 = 20, a hit; 2d6 3
    and 4, + 3 = 10 on 5 hit points. A monster at 0 dies, so heroes win in the
    middle of round 2 and the goblin takes no second turn. The drop is written
    from inside the damage, before the `ATTACK` record that carries it.
    """
    log = summary()
    world = World(log=log)
    fighter = creature("fighter")
    goblin = creature("goblin", cell=EAST, hp=5)
    world.seat(fighter, "heroes")
    world.seat(goblin, "monsters")
    rng = ScriptedRng([18, 1, 8, 1, 2, 2, 15, 3, 4])

    result = Encounter(world, policy=Attacks(), rng=rng).run()

    assert result == EncounterResult(2, "heroes")
    assert goblin.dead
    sword = GREATSWORD.name
    assert log.records == (
        (INITIATIVE, "fighter", 18, 20, 0.0),
        (INITIATIVE, "goblin", 8, 10, 0.0),
        (ROUND_START, 1),
        (TURN_START, 1, "fighter"),
        (ATTACK, "fighter", "goblin", sword, Outcome.MISS, 0),
        (TURN_END, 1, "fighter"),
        (TURN_START, 1, "goblin"),
        (ATTACK, "goblin", "fighter", sword, Outcome.MISS, 0),
        (TURN_END, 1, "goblin"),
        (ROUND_END, 1),
        (ROUND_START, 2),
        (TURN_START, 2, "fighter"),
        (DROPPED, "fighter", "goblin", True),
        (ATTACK, "fighter", "goblin", sword, Outcome.HIT, 10),
        (TURN_END, 2, "fighter"),
        (ROUND_END, 2),
    )
    assert rng.exhausted


def test_a_dead_creature_takes_no_turn() -> None:
    log = summary()
    world = World(log=log)
    a, b, c = creature("a"), creature("b"), creature("c")
    world.seat(a, "x")
    world.seat(b, "y")
    world.seat(c, "y")
    b.die()
    Encounter(
        world, policy=Passive(), rng=ScriptedRng([18, 1, 13, 1, 8, 1]), max_rounds=1
    ).run()
    assert turns(log) == [(1, "a"), (1, "c")]


def test_a_dying_character_rolls_its_death_saves_in_the_loop() -> None:
    """The pc dropped to 0 before the fight, so it is Unconscious and rolls
    initiative with Disadvantage: 17 and 13, kept 13, + 2 = 15 — between the
    fighter (18 + 2 = 20) and the goblin (8 + 2 = 10). Its two turns roll 15 and 4:
    one success, then one failure. Its side still stands through the
    fighter."""
    log = summary()
    world = World(log=log)
    fighter, pc, goblin = (
        creature("fighter"),
        creature("pc", pc=True),
        creature("goblin"),
    )
    world.seat(fighter, "heroes")
    world.seat(pc, "heroes")
    world.seat(goblin, "monsters")
    resolve_damage(pc, {SLASHING: HP}, rng=ScriptedRng([]))
    rng = ScriptedRng([18, 1, 17, 13, 1, 8, 1, 15, 4])

    Encounter(world, policy=Passive(), rng=rng, max_rounds=2).run()

    assert [record for record in log if record[0] == DEATH_SAVE] == [
        (DEATH_SAVE, "pc", 15, 1, 0),
        (DEATH_SAVE, "pc", 4, 1, 1),
    ]
    assert turns(log)[:3] == [(1, "fighter"), (1, "pc"), (1, "goblin")]
    assert rng.exhausted


# --- Joining and leaving ----------------------------------------------------------


class OnFirstTurnOf:
    """Calls `then` once, the first time `who` is asked to choose, and takes
    nothing."""

    def __init__(self, who: str, then: Any) -> None:
        self._who = who
        self._then = then
        self._done = False

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        if actor.name == self._who and not self._done:
            self._done = True
            self._then()
        return None


@pytest.mark.parametrize(
    ("dice", "rolled", "order"),
    [
        (
            (13, HALF),
            InitiativeKey(15, 14, False, 0.5),
            [(1, "a"), (1, "c"), (1, "b"), (2, "a"), (2, "c"), (2, "b")],
        ),
        (
            (20, 1),
            InitiativeKey(22, 14, False, 0.0),
            [(1, "a"), (1, "b"), (2, "c"), (2, "a"), (2, "b")],
        ),
    ],
    ids=["ahead-of-the-next-acts-this-round", "ahead-of-the-last-waits"],
)
def test_a_joiner_rolls_only_its_own_initiative(
    dice: tuple[int, int], rolled: InitiativeKey, order: list[tuple[int, str]]
) -> None:
    """c joins during a's first turn. At 13 + 2 = 15 it falls between a (20)
    and b (10), so it acts this round; at 20 + 2 = 22 it is ahead of a, who
    has already acted, so it waits for round 2. Two faces are drawn for it and
    nobody else's key moves."""
    log = summary()
    world, a, b = two_sides(log)
    c = creature("c")
    rng = ScriptedRng([*A_THEN_B, *dice])
    keys: list[InitiativeKey] = []
    encounter: Encounter

    def join() -> None:
        keys.extend((a.initiative, b.initiative))
        encounter.join(c, "x")

    encounter = Encounter(world, policy=OnFirstTurnOf("a", join), rng=rng, max_rounds=2)
    encounter.run()

    assert c.initiative == rolled
    assert a.initiative is keys[0] and b.initiative is keys[1]
    assert turns(log) == order
    assert rng.exhausted


def test_a_joiner_given_a_key_rolls_nothing_and_acts_right_after_its_sharer() -> None:
    log = summary()
    world, a, b = two_sides(log)
    summon = creature("summon")
    encounter: Encounter

    def join() -> None:
        encounter.join(summon, "x", initiative=a.initiative.sharing_turn())

    encounter = Encounter(
        world, policy=OnFirstTurnOf("a", join), rng=ScriptedRng(A_THEN_B), max_rounds=2
    )
    encounter.run()

    assert turns(log) == [
        (1, "a"),
        (1, "summon"),
        (1, "b"),
        (2, "a"),
        (2, "summon"),
        (2, "b"),
    ]


def test_a_leaver_is_never_found_again_and_its_key_is_left_alone() -> None:
    """a (20) and b (15) on one side, c (10) on the other; b leaves during
    a's first turn and acts in neither round."""
    log = summary()
    world = World(log=log)
    a, b, c = creature("a"), creature("b"), creature("c")
    world.seat(a, "x")
    world.seat(b, "x")
    world.seat(c, "y")
    kept: list[InitiativeKey] = []
    encounter: Encounter

    def leave() -> None:
        kept.append(b.initiative)
        encounter.leave(b)

    encounter = Encounter(
        world,
        policy=OnFirstTurnOf("a", leave),
        rng=ScriptedRng([18, 1, 13, 1, 8, 1]),
        max_rounds=2,
    )
    encounter.run()

    assert turns(log) == [(1, "a"), (1, "c"), (2, "a"), (2, "c")]
    assert b not in world
    assert b.initiative is kept[0]


def test_the_last_enemy_leaving_ends_the_fight() -> None:
    log = summary()
    world, a, b = two_sides(log)
    encounter: Encounter

    def leave() -> None:
        encounter.leave(b)

    encounter = Encounter(
        world, policy=OnFirstTurnOf("a", leave), rng=ScriptedRng(A_THEN_B)
    )
    assert encounter.run() == EncounterResult(1, "x")
    assert turns(log) == [(1, "a")]


# --- Durations driven by the loop (`docs/decisions.md` § w2c) -------------------------


class Mark(Effect):
    __slots__ = ()

    ID = "test.mark"


def test_an_effect_applied_in_round_two_ends_at_the_end_of_the_next_turn() -> None:
    """a (20) marks b during a's round-2 turn "until the end of a's next
    turn": a's round-2 turn has begun, so that is round 3's. b holds it
    through its own round-2 turn and a's round-3 turn, and it is gone by b's
    round-3 turn."""
    log = summary()
    world, a, b = two_sides(log)
    seen: list[tuple[str, bool]] = []
    turns_of_a = 0

    class Marks:
        def choose(
            self, actor: Creature, offers: Sequence[Action[Any]]
        ) -> Action[Any] | None:
            nonlocal turns_of_a
            seen.append((actor.name, any(isinstance(e, Mark) for e in b.effects)))
            if actor is a:
                turns_of_a += 1
                if turns_of_a == 2:
                    b.effects.apply(
                        Mark(
                            b, duration=Duration.until_end_of_next_turn_of(a.initiative)
                        )
                    )
            return None

    Encounter(world, policy=Marks(), rng=ScriptedRng(A_THEN_B), max_rounds=4).run()

    assert seen == [
        ("a", False),
        ("b", False),
        ("a", False),
        ("b", True),
        ("a", True),
        ("b", False),
        ("a", False),
        ("b", False),
    ]
    ended = [index for index, record in enumerate(log) if record[0] == EFFECT_ENDED]
    assert len(ended) == 1
    assert log.records[ended[0] - 1] == (TURN_END, 3, "a")


def test_a_dodge_lasts_until_the_start_of_the_dodgers_next_turn() -> None:
    """a Dodges in round 1; it still holds Dodging during b's turn and has
    shed it by the time its own round-2 turn asks the policy."""
    world, a, b = two_sides()
    seen: list[tuple[str, bool]] = []

    class DodgesOnce:
        def choose(
            self, actor: Creature, offers: Sequence[Action[Any]]
        ) -> Action[Any] | None:
            seen.append((actor.name, any(isinstance(e, Dodging) for e in a.effects)))
            if actor is a and len(seen) == 1:
                return next(one for one in offers if one.ID == "dodge")
            return None

    Encounter(world, policy=DodgesOnce(), rng=ScriptedRng(A_THEN_B), max_rounds=2).run()

    assert seen == [("a", False), ("b", True), ("a", False), ("b", False)]


# --- The roster answers a third creature (`docs/decisions.md` § w2c) ------------------


class QueuesOnto(HookSource):
    """When its owner takes damage, queues a `Mark` onto `other`."""

    __slots__ = ("_other",)

    ID = "test.queues_onto"

    def __init__(self, owner: Creature, other: Creature) -> None:
        super().__init__(owner)
        self._other = other

    @hook("on_incoming_damage")
    def queue(self, ctx: IncomingDamageContext) -> R:
        ctx.apply_effect(self._other, Mark(self._other))
        return R.APPLIED


def test_an_effect_queued_onto_a_third_seated_creature_lands_on_it() -> None:
    world, a, b = two_sides()
    ally = creature("ally")
    world.seat(ally, "y")
    b.attach(QueuesOnto(b, ally))

    resolve_damage(b, {SLASHING: 3}, rng=ScriptedRng([]), attacker=a)

    assert [type(effect) for effect in ally.effects] == [Mark]
    assert b.hp == 17


def test_an_effect_queued_onto_a_creature_off_the_roster_still_raises() -> None:
    world, a, b = two_sides()
    stranger = creature("stranger")
    b.attach(QueuesOnto(b, stranger))

    with pytest.raises(EngineAssemblyError):
        resolve_damage(b, {SLASHING: 3}, rng=ScriptedRng([]), attacker=a)
    assert list(stranger.effects) == []


def test_a_creature_that_has_left_is_off_the_roster() -> None:
    world, a, b = two_sides()
    gone = creature("gone")
    world.seat(gone, "y")
    world.leave(gone)
    b.attach(QueuesOnto(b, gone))

    with pytest.raises(EngineAssemblyError):
        resolve_damage(b, {SLASHING: 3}, rng=ScriptedRng([]), attacker=a)
    assert list(gone.effects) == []


# --- The world: seating, the shared clock, the sweep (`docs/decisions.md` § w2c) ------


def test_a_creature_is_seated_once() -> None:
    world, a, _ = two_sides()
    with pytest.raises(ValueError):
        world.seat(a, "x")


def test_seated_stores_share_the_worlds_clock_and_a_leaver_gets_its_own() -> None:
    world, a, b = two_sides()
    assert a.effects.clock is world.clock
    assert b.effects.clock is world.clock
    now = TimeKey(2, InitiativeKey(9, 14, False, 0.0), TurnPhase.END)
    world.clock.expire_before(now)
    assert a.effects.now == now and b.effects.now == now

    world.leave(b)
    assert b.effects.clock is not world.clock
    assert b.effects.now == now
    assert len(world) == 1 and list(world) == [a]


def spy_on_sweeps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the holder of every store a clock sweeps."""
    swept: list[str] = []
    real = EffectStore._sweep

    def sweep(self: EffectStore, now: TimeKey, *, through: bool) -> None:
        swept.append(self._holder.name)
        real(self, now, through=through)

    monkeypatch.setattr(EffectStore, "_sweep", sweep)
    return swept


ANCHOR: Final = InitiativeKey(10, 14, False, 0.0)


def at(round_: int, phase: TurnPhase = TurnPhase.START) -> TimeKey:
    return TimeKey(round_, ANCHOR, phase)


def test_the_clock_sweeps_only_the_stores_holding_an_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three seated, one holding a Mark: each sweep reaches only that store.
    Once the Mark is removed, nobody is swept."""
    world, a, b = two_sides()
    c = creature("c")
    world.seat(c, "y")
    mark = b.effects.apply(Mark(b))
    assert mark is not None
    swept = spy_on_sweeps(monkeypatch)

    world.clock.expire_before(at(1))
    world.clock.expire_after(at(1))
    assert swept == ["b", "b"]

    b.effects.remove(mark)
    swept.clear()
    world.clock.expire_before(at(2))
    assert swept == []

    c.effects.apply(Mark(c))
    world.clock.expire_after(at(2))
    assert swept == ["c"]


def test_a_store_holding_an_effect_carries_it_when_seated_and_when_it_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seated with a Mark already on it, the store is swept by the world's
    clock; once it leaves, the world's clock no longer reaches it."""
    a = creature("a")
    a.effects.apply(Mark(a))
    world = World()
    world.seat(a, "x")
    swept = spy_on_sweeps(monkeypatch)

    world.clock.expire_before(at(1))
    assert swept == ["a"]

    world.leave(a)
    swept.clear()
    world.clock.expire_before(at(2))
    assert swept == []
    a.effects.clock.expire_before(at(2))
    assert swept == ["a"]


def test_an_effect_brought_into_the_world_expires_on_the_worlds_clock() -> None:
    """A Mark lasting until the start of ANCHOR's next turn, applied while the
    store still read its own clock. Once seated, the world's sweep to that
    deadline removes it."""
    a = creature("a")
    a.effects.apply(Mark(a, duration=Duration.until_start_of_next_turn_of(ANCHOR)))
    expiry = next(iter(a.effects)).expiry
    assert expiry is not None
    world = World()
    world.seat(a, "x")
    world.clock.expire_before(expiry.end)
    assert list(a.effects) == []


# --- Queries ----------------------------------------------------------------------


def test_the_queries_read_sides_and_who_is_still_standing() -> None:
    """a and pc on x; b and c on y. c is dead and pc is down but alive."""
    world = World()
    a, b, c, pc = creature("a"), creature("b"), creature("c"), creature("pc", pc=True)
    for one, side in ((a, "x"), (b, "y"), (c, "y"), (pc, "x")):
        world.seat(one, side)
    c.die()
    resolve_damage(pc, {SLASHING: HP}, rng=ScriptedRng([]))

    assert [standing(one) for one in (a, b, c, pc)] == [True, True, False, False]
    assert enemies_of(world, a) == (b,)
    assert enemies_of(world, b) == (a, pc)
    assert allies_of(world, a) == (pc,)
    assert allies_of(world, b) == ()
    assert standing_sides(world) == frozenset({"x", "y"})
    b.die()
    assert standing_sides(world) == frozenset({"x"})


def test_holder_finds_a_seated_creature_by_identity_only() -> None:
    world, a, _ = two_sides()
    assert world.holder(a) is a
    assert world.holder(creature("a")) is None


# --- The log reaches every writer (`docs/decisions.md` § w2c) -------------------------

POOL: Final = ResourceSpec(
    key="test.pool",
    label="Pool",
    maximum=2,
    recovery=(Recovery(RecoveryTrigger.LONG_REST),),
)


def test_a_seated_creature_writes_its_spends_and_its_effects_to_the_log() -> None:
    log = summary()
    a = creature("a")
    a.resources.add(POOL)
    World(log=log).seat(a, "x")

    a.resources.spend("test.pool", 2)
    mark = a.effects.apply(Mark(a))
    assert mark is not None
    a.effects.remove(mark)

    assert log.records == (
        (RESOURCE_SPENT, "a", "test.pool", 2),
        (EFFECT_APPLIED, "a", "test.mark"),
        (EFFECT_ENDED, "a", "test.mark"),
    )


def test_a_refresh_of_a_held_effect_writes_nothing() -> None:
    log = summary()
    a = creature("a")
    World(log=log).seat(a, "x")
    a.effects.apply(Mark(a))
    a.effects.apply(Mark(a))
    assert log.records == ((EFFECT_APPLIED, "a", "test.mark"),)


def test_a_leaver_writes_nothing_more() -> None:
    log = summary()
    a = creature("a")
    a.resources.add(POOL)
    world = World(log=log)
    world.seat(a, "x")
    world.leave(a)
    a.resources.spend("test.pool")
    a.effects.apply(Mark(a))
    assert log.records == ()


def test_a_save_and_an_instance_of_damage_write_when_handed_the_log() -> None:
    """A Strength save: 9 + 3 = 12 against DC 12, a success. 7 slashing
    from a, untouched by any defence."""
    log = summary()
    a, b = creature("a"), creature("b")
    saving_throw(b, ability=Ability.STRENGTH, dc=12, rng=ScriptedRng([9]), log=log)
    resolve_damage(b, {SLASHING: 7}, rng=ScriptedRng([]), attacker=a, log=log)
    resolve_damage(b, {SLASHING: 1}, rng=ScriptedRng([]), log=log)
    assert log.records == (
        (SAVE, "b", Ability.STRENGTH, 12, 12, SaveOutcome.SUCCESS),
        (DAMAGE, "a", "b", 7),
        (DAMAGE, None, "b", 1),
    )


def test_two_creatures_placed_on_one_key_both_act_or_are_refused() -> None:
    """Two summons both placed with `a.initiative.sharing_turn()` — the same
    order, 0, so the same key. Either the second placement is refused, or
    both take their turn each round; silently losing one is neither."""
    log = summary()
    world, a, b = two_sides(log)
    first, second = creature("first"), creature("second")
    encounter: Encounter

    def join() -> None:
        encounter.join(first, "x", initiative=a.initiative.sharing_turn())
        encounter.join(second, "x", initiative=a.initiative.sharing_turn())

    encounter = Encounter(
        world, policy=OnFirstTurnOf("a", join), rng=ScriptedRng(A_THEN_B), max_rounds=1
    )
    try:
        encounter.run()
    except (ValueError, EngineAssemblyError):
        return
    assert {name for _, name in turns(log)} == {"a", "first", "second", "b"}


@pytest.mark.parametrize(
    "key",
    [
        UNSEATED,
        UNSEATED.sharing_turn(),
        UNSEATED.sharing_turn(1).sharing_turn(),
    ],
    ids=["unseated", "sharing-unseated", "nested-chain-off-unseated"],
)
def test_a_placement_before_the_start_off_an_unrolled_key_is_refused(
    key: InitiativeKey,
) -> None:
    """Before `run()`, a key that is `UNSEATED` or a chain off it raises
    `ValueError` and seats nobody."""
    world, a, b = two_sides()
    beast = creature("beast")
    encounter = Encounter(
        world, policy=Passive(), rng=ScriptedRng(A_THEN_B), max_rounds=1
    )

    with pytest.raises(ValueError):
        encounter.join(beast, "x", initiative=key)

    assert beast not in world
    assert list(world) == [a, b]


def test_a_placement_before_the_start_with_a_real_key_is_kept() -> None:
    log = summary()
    world, _, _ = two_sides(log)
    beast = creature("beast")
    key = InitiativeKey(15, 14, False, 0.0)
    encounter = Encounter(
        world, policy=Passive(), rng=ScriptedRng(A_THEN_B), max_rounds=1
    )

    encounter.join(beast, "x", initiative=key)
    encounter.run()

    assert beast in world
    assert beast.initiative == key
    assert turns(log) == [(1, "a"), (1, "beast"), (1, "b")]


@pytest.mark.parametrize(
    "order",
    [(0,), (1, 0)],
    ids=["sharing-turn", "nested-chain"],
)
def test_a_placement_before_the_start_sharing_a_real_key_is_refused(
    order: tuple[int, ...],
) -> None:
    """Before `run()`, a key shared off a summoner holding a real key raises
    `ValueError` and seats nobody."""
    world, _, _ = two_sides()
    summoner = Creature(
        name="summoner",
        stats=StatBlock(abilities=scores(), max_hp=HP),
        position=ORIGIN,
        weapon=GREATSWORD,
        initiative=InitiativeKey(15, 14, False, 0.5),
    )
    beast = creature("beast")
    encounter = Encounter(
        world, policy=Passive(), rng=ScriptedRng(A_THEN_B), max_rounds=1
    )
    encounter.join(summoner, "x")
    roster = list(world)
    key = summoner.initiative
    for step in order:
        key = key.sharing_turn(step)

    with pytest.raises(ValueError):
        encounter.join(beast, "x", initiative=key)

    assert beast not in world
    assert list(world) == roster
