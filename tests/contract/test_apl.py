"""The APL contract: the clause compiler's whitelist, the three clauses, the
default policy, reactions answered as entries, and the W8 seams.

Every plain creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Dexterity 14 (+2), every other score 10, 20 hit points and no
armour, so AC 10 + 2 = 12. A greatsword attack swings at natural + 3 + 2 =
natural + 5 against AC 12 and deals 2d6 + 3, so a natural 15 hits and 2d6
rolled 4 and 4 deals 4 + 4 + 3 = 11.

A `TargetDummy` has every ability score 10 (+0) and proficiency +2, so it
swings at natural + 2 and adds nothing to its damage: a natural 15 hits AC 12
and a pike's 1d10 rolled 7 deals 7.

Sources: DESIGN.md § APL and mid-attack decisions, § Reactions in the APL,
§ `best_scored`, § Failing loudly; ROADMAP `w2e` and § The W0 debt register
#14, #34, #59; `docs/decisions.md` § w1d, § w2c2, § w2d; XPHB (2024) "Combat"
-> Opportunity Attacks.
"""

import importlib
import inspect
import pkgutil
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim import actions
from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.actions.move import Move, Retreat, Walk, _stands
from dndsim.actions.opportunity_attack import OpportunityAttack, OpportunityAttacks
from dndsim.apl.apl import (
    BEST_SCORED,
    COMMAND_IDS,
    DEFAULT_MOVE,
    Apl,
    Entry,
    unprovided,
)
from dndsim.apl.dummy import arm_dummy, dummy_apl
from dndsim.apl.expr import (
    AplLoadError,
    MoveMode,
    compile_condition,
    compile_move,
    compile_param,
)
from dndsim.apl.namespace import SelfProxy, TargetProxy, TriggerProxy
from dndsim.apl.schema import CREATURE_FIELDS, Kind, NamespaceSchema
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell, distance_ft, in_reach
from dndsim.core.log import EventLog
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import InitiativeKey
from dndsim.creatures.creature import Creature
from dndsim.creatures.dummy import (
    DummySpec,
    MovementProfile,
    TargetDummy,
    ThreatProfile,
    build_dummies,
)
from dndsim.creatures.stats import StatBlock, Weapon
from dndsim.engine.attack import ATTACK, resolve_attack
from dndsim.engine.turn import Policy, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import (
    Context,
    HitContext,
    IncomingDamageContext,
    LeaveReachContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.dispatch import Gate
from dndsim.hooks.source import HookSource
from dndsim.rules.constants import SIZE_WIDTH_CELLS
from dndsim.rules.enums import Ability, DamageType, Outcome, Size
from dndsim.tools.spike import SPIKE_APL, compile_spike_apl
from weapon_fixtures import GREATSWORD

SCORES: Final = MappingProxyType(
    {
        **dict.fromkeys(Ability, 10),
        Ability.STRENGTH: 16,  # +3
        Ability.DEXTERITY: 14,  # +2
    }
)
HP: Final = 20
HIT: Final = 15
"""A natural 15: 15 + 5 = 20 against AC 12, and 15 + 2 = 17 for a dummy."""
ELEVEN: Final = (HIT, 4, 4)
"""One greatsword hit by a plain creature: 4 + 4 + 3 = 11."""
SEVEN: Final = (HIT, 7)
"""One pike hit by a dummy: 7 + 0 = 7."""

PIKE: Final = Weapon(
    name="Pike",
    damage=Dice(1, 10),
    damage_type=DamageType.PIERCING,
    reach_ft=10,
)
"""Ten feet of reach, so two enemies can be in reach at different distances."""

LONGBOW: Final = Weapon(
    name="Longbow",
    damage=Dice(1, 8),
    damage_type=DamageType.PIERCING,
    range_ft=(150, 600),
)


def key(initiative: int) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=14, player_character=False, tiebreak=0.5
    )


def creature(
    name: str,
    cell: Cell,
    *,
    hp: int = HP,
    weapon: Weapon | None = GREATSWORD,
    size: Size = Size.MEDIUM,
    speed: int = 30,
    initiative: int = 10,
) -> Creature:
    one = Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=HP, speed_ft=speed, size=size),
        position=cell,
        weapon=weapon,
        initiative=key(initiative),
    )
    one.hp = hp
    return one


def seated(*seats: tuple[Creature, str], log: EventLog | None = None) -> World:
    world = World(log=log)
    for one, side in seats:
        world.seat(one, side)
    return world


def no_dice() -> ScriptedRng:
    return ScriptedRng([])


def bound(text: str, owner: Creature, world: World, **kwargs: object) -> Apl:
    """`text` compiled and bound to `owner`, with `owner`'s dispatcher asking
    it as its gate."""
    apl = Apl.from_yaml(text, **kwargs).bind(owner, world)
    owner.dispatcher.bind(gate=apl)
    return apl


def raging_schema() -> NamespaceSchema:
    """The spike's one extension, `self.raging`."""
    return NamespaceSchema().extend("raging", Kind.BOOL, lambda creature: False)


def attacks(log: EventLog | None) -> list[tuple[object, ...]]:
    assert log is not None
    return [record for record in log if record[0] == ATTACK]


def one_dummy(spec: DummySpec) -> TargetDummy:
    (dummy,) = build_dummies(spec)
    return dummy


def apart(actor: Creature, target: Creature) -> int:
    return distance_ft(
        actor.position,
        target.position,
        origin_size=actor.size,
        destination_size=target.size,
    )


def reaches(actor: Creature, target: Creature) -> bool:
    return in_reach(
        actor.position,
        target.position,
        actor.reach_ft,
        origin_size=actor.size,
        destination_size=target.size,
    )


class Marker(HookSource):
    """A source with no handlers; `allows()` is given one and ignores it."""

    __slots__ = ()

    ID = "test.marker"


def leaving(watcher: Creature, mover: Creature) -> LeaveReachContext:
    """The context one five-foot step east of `mover` would hand `watcher`."""
    origin = mover.position
    return LeaveReachContext(
        creature=watcher,
        mover=mover,
        origin=origin,
        destination=Cell(origin.x + 1, origin.y),
    )


# --- 1. the AST whitelist, and load-time failure ------------------------------


@pytest.mark.parametrize(
    ("clause", "rejected"),
    [
        ("[c.hp for c in enemies]", "comprehension"),
        ("any(e.hp > 1 for e in enemies)", "generator"),
        ("(lambda: True)()", "lambda"),
        ("__import__('os')", "import"),
        ("self.__class__", "dunder attribute"),
        ("self._ref", "private attribute"),
        ("self.hp = 1", "assignment"),
        ("self.slots.highest := 3", "named assignment"),
        ("len(enemies)", "a call that is not a selector"),
        ("self.hp.bit_length()", "a call on a creature field"),
        ("self.hp % 2 == 0", "an operator outside the grammar"),
        ("self.hp << 1 > 0", "a bitwise operator"),
        ("'rage' == 'rage'", "a string constant"),
        ("enemies.alive[0].hp > 1", "a subscript"),
        ("self.hp if self else 1", "a node with no kind"),
    ],
)
def test_a_clause_outside_the_grammar_is_refused_at_load(
    clause: str, rejected: str
) -> None:
    """One `AplLoadError` per rejected node kind, raised while the list is
    compiled rather than when the clause would first be evaluated."""
    with pytest.raises(AplLoadError):
        Apl([Entry("attack", if_=clause)])
    assert rejected  # the parameter names what the clause smuggles in


def test_a_misspelt_self_extension_fails_at_load_with_a_suggestion() -> None:
    """DESIGN.md § Condition language: "`self.ragging` fails when the party
    loads"."""
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attack", if_="not self.ragging")], schema=raging_schema())

    assert "self.ragging" in str(raised.value)
    assert "did you mean 'raging'?" in str(raised.value)


def test_the_schema_without_the_extension_does_not_answer_self_raging() -> None:
    with pytest.raises(AplLoadError):
        Apl([Entry("attack", if_="not self.raging")])


def test_an_unknown_namespace_path_fails_at_load_with_a_suggestion() -> None:
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attack", if_="enemies.in_reech >= 1")])

    assert "enemies.in_reech" in str(raised.value)
    assert "did you mean 'in_reach'?" in str(raised.value)


def test_an_unknown_action_id_fails_at_load_with_a_suggestion() -> None:
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attak")])

    assert "attak" in str(raised.value)
    assert "did you mean 'attack'?" in str(raised.value)


def test_an_entry_naming_a_with_param_twice_is_refused_at_load() -> None:
    """Only the programmatic form can carry the duplicate: `yaml.safe_load`
    keeps the last of two equal keys, so a YAML row never reaches the
    compiler with both."""
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attack", with_=(("n", "1"), ("n", "2")))])

    assert "with:" in str(raised.value)


def test_a_misspelt_entry_key_fails_at_load_with_a_suggestion() -> None:
    with pytest.raises(AplLoadError) as raised:
        Apl.from_yaml('- { action: attack, iff: "enemies.alive >= 1" }')

    assert "iff" in str(raised.value)
    assert "did you mean 'if'?" in str(raised.value)


def test_a_misspelt_selector_fails_at_load_with_a_suggestion() -> None:
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attack", target="lowest_hps of enemies")])

    assert "did you mean 'lowest_hp'?" in str(raised.value)


def test_a_path_that_is_not_a_value_names_its_members() -> None:
    with pytest.raises(AplLoadError) as raised:
        Apl([Entry("attack", if_="self.slots >= 1")])

    assert "highest" in str(raised.value)


def test_a_group_compared_with_a_number_is_its_length() -> None:
    """`enemies.within(30) >= 1` is the count, not a creature."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry("attack", if_="enemies.within(30) >= 1")]).bind(hero, world)

    assert apl.first(frozenset({"attack"})) is not None

    goblin.position = Cell(20, 0)
    assert apl.first(frozenset({"attack"})) is None


def test_the_compiled_clause_records_the_paths_it_reads() -> None:
    """DESIGN.md § Condition language: the compiler records which paths each
    condition reads, which is what lets the loader prove an APL never
    references state a build does not have."""
    apl = Apl(
        [
            Entry(
                "attack",
                if_="self.hp_pct > 0.4",
                target="lowest_hp of enemies.in_reach",
                move="toward nearest of enemies until in_reach",
            )
        ]
    )

    assert apl.reads == frozenset({"self.hp_pct", "enemies.in_reach", "enemies.alive"})


# --- 2. all three clauses, one whitelist -------------------------------------


def test_the_three_clauses_of_the_design_document_compile() -> None:
    apl = Apl(
        [
            Entry(
                "attack",
                if_="enemies.alive >= 1",
                target="lowest_hp of enemies.in_reach",
                move="toward nearest of enemies until in_reach",
            )
        ]
    )
    (compiled,) = apl.compiled

    assert compiled.if_ is not None
    assert compiled.target is not None and compiled.target.kind is Kind.CREATURE
    assert compiled.move is not None and compiled.move.mode is MoveMode.TOWARD


@pytest.mark.parametrize(
    ("clause", "mode", "keep_ft"),
    [
        ("toward nearest of enemies until in_reach", MoveMode.TOWARD, None),
        ("away_from nearest of enemies to_max_range", MoveMode.AWAY_FROM, None),
        ("away_from nearest of enemies to 30 ft", MoveMode.AWAY_FROM, 30),
        ("hold", MoveMode.HOLD, None),
    ],
)
def test_every_move_clause_of_the_grammar_compiles(
    clause: str, mode: MoveMode, keep_ft: int | None
) -> None:
    compiled = compile_move(clause, NamespaceSchema())

    assert compiled.mode is mode
    assert compiled.keep_ft == keep_ft


@pytest.mark.parametrize(
    "clause",
    [
        "toward nearest of enemies",
        "toward nearest of enemies until in_range",
        "away_from nearest of enemies",
        "hold still",
        "",
    ],
)
def test_a_move_clause_outside_the_grammar_is_refused_at_load(clause: str) -> None:
    with pytest.raises(AplLoadError):
        compile_move(clause, NamespaceSchema())


@pytest.mark.parametrize("clause", ["if", "target", "move"])
def test_all_three_clauses_resolve_against_the_same_schema(clause: str) -> None:
    """One typo, one load-time failure, whichever clause it is in."""
    texts = {
        "if": "enemies.in_reech >= 1",
        "target": "lowest_hp of enemies.in_reech",
        "move": "toward lowest_hp of enemies.in_reech until in_reach",
    }
    row = {"action": "attack", clause: texts[clause]}

    with pytest.raises(AplLoadError) as raised:
        Apl([Entry.from_mapping(row)])

    assert "did you mean 'in_reach'?" in str(raised.value)


def test_the_design_documents_own_list_compiles() -> None:
    """`tools/spike.py`'s `SPIKE_APL` is DESIGN.md § APL's YAML; debt #14 is
    retired by it compiling through `apl/` rather than by hand."""
    apl = compile_spike_apl()

    assert len(apl.entries) == 8
    assert [entry.action for entry in apl.entries] == [
        "rage",
        "reckless_attack",
        "divine_smite",
        "uncanny_dodge",
        "second_wind",
        "healing_word",
        "attack",
        "dodge",
    ]
    assert "move" in SPIKE_APL


# --- 3. defaults: OFF, with movement the one exception -----------------------


def test_a_gate_no_entry_claims_is_off() -> None:
    """DESIGN.md § APL: unmentioned riders and reactions are a Null Object,
    so adding a feature never silently changes an existing build's numbers."""
    hero = creature("hero", Cell(0, 0))
    runner = creature("runner", Cell(1, 0))
    world = seated((hero, "heroes"), (runner, "monsters"))
    apl = Apl([Entry("attack")]).bind(hero, world)
    source = Marker(hero)

    assert not apl.allows(OpportunityAttack.ID, source, leaving(hero, runner))
    assert not apl.allows("paladin.divine_smite", source, leaving(hero, runner))


def test_a_list_with_no_move_clause_closes_on_its_nearest_enemy() -> None:
    """The one exception to OFF-by-default: the alternative is a melee build
    that stands still and reports zero damage. The nearer goblin is at
    (4, 0), 20 feet away, and five feet of reach stops the walk on (3, 0)."""
    hero = creature("hero", Cell(0, 0))
    near = creature("near", Cell(4, 0))
    far = creature("far", Cell(9, 0))
    world = seated((hero, "heroes"), (near, "monsters"), (far, "monsters"))
    hero.economy.turn_started(hero.speed_ft)
    apl = Apl([Entry("attack", if_="enemies.alive >= 1")]).bind(hero, world)

    choice = apl.choose(hero, [])

    assert isinstance(choice, Move)
    assert choice.target is near
    choice.execute(rng=no_dice())
    assert apart(hero, near) == 5
    assert reaches(hero, near)


def test_the_default_move_is_the_design_documents_sentence() -> None:
    assert DEFAULT_MOVE == "toward nearest of enemies until in_reach"


def test_a_list_that_names_a_move_gets_no_default_move() -> None:
    """`hold` is a `move:` clause, so the default is not supplied; the
    creature stays where it is."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(9, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    hero.economy.turn_started(hero.speed_ft)
    apl = Apl([Entry("attack", move="hold")]).bind(hero, world)

    assert apl.choose(hero, []) is None
    assert hero.position == Cell(0, 0)


def test_an_unbound_list_refuses_to_be_asked() -> None:
    """DESIGN.md § Failing loudly: the proxies are built per creature, so an
    unbound list has no state to read rather than a default to invent."""
    apl = Apl([Entry("attack")])

    with pytest.raises(Exception):  # noqa: B017 - EngineAssemblyError or ValueError
        apl.first(frozenset({"attack"}))


# --- 4. reactions are entries like any other --------------------------------


def chase(
    *, mover_hp: int, watcher_apl: str
) -> tuple[World, Creature, Creature, Apl, ScriptedRng]:
    """A runner at (1, 0) closing on its enemy at (8, 0), past a guard at
    (0, 0) whose five feet the first step leaves."""
    rng = ScriptedRng(list(ELEVEN))
    log = EventLog()
    guard = creature("guard", Cell(0, 0))
    runner = creature("runner", Cell(1, 0), hp=mover_hp)
    quarry = creature("quarry", Cell(8, 0))
    world = seated(
        (guard, "monsters"), (runner, "heroes"), (quarry, "monsters"), log=log
    )
    runner.economy.turn_started(runner.speed_ft)
    apl = bound(watcher_apl, guard, world)
    guard.attach(
        OpportunityAttacks(guard, world=world, rng=rng, trigger=apl.namespace.trigger)
    )
    return world, runner, quarry, apl, rng


WOUNDED_RUNNER: Final = (
    '- { action: opportunity_attack, if: "trigger.mover.hp_pct < 0.5" }'
)


def test_a_reaction_entry_is_answered_at_the_moment_the_offer_is_made() -> None:
    """The runner is at 9 of 20 hit points, so `hp_pct` is 0.45 and the
    entry's `if:` passes: one swing for 11, leaving 9 - 11 clamped to 0."""
    world, runner, quarry, _, rng = chase(mover_hp=9, watcher_apl=WOUNDED_RUNNER)

    Move(runner, quarry, world=world).execute(rng=rng)

    assert attacks(world.log) == [
        (ATTACK, "guard", "runner", "Greatsword", Outcome.HIT, 11)
    ]
    assert runner.hp == 0
    assert rng.exhausted


def test_a_reaction_entry_whose_condition_fails_declines_the_offer() -> None:
    """The same walk with the runner at full hit points: `hp_pct` is 1.0, the
    entry does not pass, and a declined offer spends nothing."""
    world, runner, quarry, _, _ = chase(mover_hp=HP, watcher_apl=WOUNDED_RUNNER)
    guard = next(one for one in world if one.name == "guard")

    Move(runner, quarry, world=world).execute(rng=no_dice())

    assert attacks(world.log) == []
    assert runner.hp == HP
    assert guard.economy.reactions == 1


def test_a_list_that_never_names_the_opportunity_attack_never_swings() -> None:
    """DESIGN.md § Reactions in the APL: "A build whose list never claims
    `opportunity_attack` never makes one"."""
    world, runner, quarry, _, _ = chase(
        mover_hp=9, watcher_apl='- { action: attack, if: "enemies.alive >= 1" }'
    )

    Move(runner, quarry, world=world).execute(rng=no_dice())

    assert attacks(world.log) == []
    assert runner.hp == 9


def test_the_trigger_root_carries_the_mover_and_the_squares_being_left() -> None:
    """One five-foot step, not the whole move: `origin` is the square the
    mover still stands on and `destination` the one it is stepping into. The
    runner is at full hit points, so the `if:` declines and no attack follows
    to replace the offer on the root."""
    world, runner, quarry, apl, _ = chase(mover_hp=HP, watcher_apl=WOUNDED_RUNNER)

    Move(runner, quarry, world=world).execute(rng=no_dice())

    assert attacks(world.log) == []
    assert runner.position == Cell(7, -1)
    trigger = apl.namespace.trigger
    assert trigger.mover is runner
    assert trigger.origin == Cell(1, 0)
    # Every stand beside the quarry is six squares off, so of equal routes
    # `Move` takes the lowest `y` (`docs/decisions.md` § w2c2): (7, -1), whose
    # route leaves (1, 0) for (2, -1).
    assert trigger.destination == Cell(2, -1)
    assert trigger.destination != runner.position
    assert distance_ft(Cell(1, 0), trigger.destination) == 5


def test_a_reaction_entry_with_no_condition_takes_every_offer() -> None:
    world, runner, quarry, _, rng = chase(
        mover_hp=HP, watcher_apl="- { action: opportunity_attack }"
    )

    Move(runner, quarry, world=world).execute(rng=rng)

    assert runner.hp == HP - 11 == 9


def test_the_first_passing_entry_for_a_gate_answers_it() -> None:
    """DESIGN.md § APL: "One flat ordered list; the engine filters to
    currently-offered ids and takes the first passing entry." Two entries can
    name one reaction, and the second is asked when the first does not pass:
    the mover is at full hit points, so the first fails, and the watcher is at
    full hit points, so the second passes."""
    guard = creature("guard", Cell(0, 0))
    runner = creature("runner", Cell(1, 0))
    world = seated((guard, "monsters"), (runner, "heroes"))
    apl = Apl.from_yaml(
        """
        - { action: opportunity_attack, if: "trigger.mover.hp_pct < 0.5" }
        - { action: opportunity_attack, if: "self.hp_pct > 0.9" }
        """
    ).bind(guard, world)

    assert apl.allows(OpportunityAttack.ID, Marker(guard), leaving(guard, runner))


@pytest.mark.parametrize(
    "clause",
    ["trigger.origin == trigger.destination", "trigger.origin != trigger.destination"],
)
def test_two_squares_compare_for_equality(clause: str) -> None:
    Apl([Entry(OpportunityAttack.ID, if_=clause)])


@pytest.mark.parametrize(
    "clause",
    [
        "trigger.origin > 3",
        "trigger.origin == 3",
        "trigger.origin < trigger.destination",
    ],
)
def test_a_square_is_neither_ordered_nor_compared_with_a_number(clause: str) -> None:
    with pytest.raises(AplLoadError):
        Apl([Entry(OpportunityAttack.ID, if_=clause)])


@pytest.mark.parametrize(
    ("clause", "answer"),
    [
        ("trigger.origin != trigger.destination", True),
        ("trigger.origin == trigger.destination", False),
    ],
)
def test_a_square_comparison_is_answered_on_a_movement_offer(
    clause: str, answer: bool
) -> None:
    """`leaving` steps the runner from (1, 0) to (2, 0): two different
    squares."""
    guard = creature("guard", Cell(0, 0))
    runner = creature("runner", Cell(1, 0))
    world = seated((guard, "monsters"), (runner, "heroes"))
    apl = Apl([Entry(OpportunityAttack.ID, if_=clause)]).bind(guard, world)

    assert (
        apl.allows(OpportunityAttack.ID, Marker(guard), leaving(guard, runner))
        is answer
    )


# --- 5. the protocols it fills ----------------------------------------------


def test_an_apl_is_the_dispatchers_gate_and_the_turn_loops_policy() -> None:
    """ROADMAP `w2e`: "`Apl` satisfies the `Gate` protocol `hooks/dispatch.py`
    declares and owes the contract test that says so"; `engine/turn.py`'s
    `Policy` is the other seat it fills."""
    apl = Apl([Entry("attack")])
    gate: Gate = apl
    policy: Policy = apl

    assert gate is apl and policy is apl
    assert callable(gate.allows)
    assert callable(policy.choose)


def test_a_turn_driven_by_an_apl_takes_the_action_its_target_clause_picks() -> None:
    """Both goblins are in reach; `lowest_hp of enemies.in_reach` picks the
    one at 14 hit points, which takes 11 and is left with 3."""
    hero = creature("hero", Cell(0, 0), initiative=20)
    healthy = creature("healthy", Cell(1, 0))
    wounded = creature("wounded", Cell(0, 1), hp=14)
    world = seated(
        (hero, "heroes"),
        (healthy, "monsters"),
        (wounded, "monsters"),
        log=EventLog(),
    )
    apl = Apl([Entry("attack", target="lowest_hp of enemies.in_reach")]).bind(
        hero, world
    )
    rng = ScriptedRng(list(ELEVEN))

    taken = take_turn(world, hero, 1, policy=apl, rng=rng)

    assert taken == 1
    assert wounded.hp == 3
    assert healthy.hp == HP
    assert rng.exhausted


def test_an_apl_attacks_and_then_walks_away_with_its_action_spent() -> None:
    """The hero stands beside one goblin. Asked with every Command, the first
    entry attacks: 11, leaving 9. Asked again with nothing offered and its 30
    ft of movement left, the attack entry finds no offer and the dash entry's
    `move:` walks five squares west, to 30 ft (six squares) from the goblin at
    (1, 0); of those squares the lowest `y` is taken (`docs/decisions.md`
    § w2e). Asked a third time with 5 ft left, it is already 30 ft away, no
    Dash is offered, and the turn ends. Nothing answers the goblin's
    Opportunity Attack, so no die is rolled for the walk."""
    hero = creature("hero", Cell(0, 0), initiative=20)
    goblin = creature("goblin", Cell(1, 0))
    log = EventLog()
    world = seated((hero, "heroes"), (goblin, "monsters"), log=log)
    apl = bound(
        """
        - { action: attack }
        - { action: dash, move: "away_from nearest of enemies to 30 ft" }
        """,
        hero,
        world,
    )
    rng = ScriptedRng(list(ELEVEN))

    taken = take_turn(world, hero, 1, policy=apl, rng=rng)

    assert taken == 2
    assert attacks(log) == [(ATTACK, "hero", "goblin", "Greatsword", Outcome.HIT, 11)]
    assert goblin.hp == HP - 11 == 9
    assert hero.position == Cell(-5, -5)
    assert apart(hero, goblin) == 30
    assert hero.economy.movement_ft == 5
    assert hero.economy.actions == 0
    assert goblin.position == Cell(1, 0)
    assert rng.exhausted


def test_a_policy_asked_about_another_creature_is_refused() -> None:
    hero = creature("hero", Cell(0, 0))
    other = creature("other", Cell(1, 0))
    world = seated((hero, "heroes"), (other, "monsters"))
    apl = Apl([Entry("attack")]).bind(hero, world)

    with pytest.raises(Exception):  # noqa: B017 - EngineAssemblyError
        apl.choose(other, [])


# --- 6. the W8 seams ---------------------------------------------------------


def test_best_scored_is_reserved_in_the_closed_vocabulary() -> None:
    """DESIGN.md § `best_scored`: `w2e` reserves the id, `w8b` implements the
    scorer that offers it."""
    assert BEST_SCORED == "best_scored"
    apl = Apl([Entry(BEST_SCORED)])

    assert [entry.action for entry in apl.entries] == [BEST_SCORED]


def test_best_scored_offers_nothing_until_the_scorer_arrives() -> None:
    """In between, such a build falls through to the Null Object."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry(BEST_SCORED)]).bind(hero, world)

    assert apl.first(frozenset({"attack", "dodge"})) is None


def test_best_scored_is_permanently_exempt_from_the_unprovided_check() -> None:
    """DESIGN.md § Failing loudly: no feature ever provides it, the scorer
    does, so the loader's "APL action id no attached feature provides" check
    must not refuse the default policy it was just given."""
    apl = Apl([Entry(BEST_SCORED), Entry("rage")], actions=frozenset({"rage"}))

    assert unprovided(apl, ()) == frozenset({"rage"})
    assert unprovided(apl, ("rage",)) == frozenset()
    assert BEST_SCORED not in unprovided(apl, ())


def test_a_command_in_actions_needs_no_feature_to_provide_it() -> None:
    assert {"attack", "move", "dodge", "opportunity_attack"} <= COMMAND_IDS
    assert unprovided(Apl([Entry("attack"), Entry("move")]), ()) == frozenset()


def test_an_apl_is_constructible_from_entry_objects_without_yaml() -> None:
    apl = Apl(
        [
            Entry("rage", if_="not self.raging"),
            Entry("attack", target="lowest_hp of enemies.in_reach"),
        ],
        schema=raging_schema(),
        actions=frozenset({"rage"}),
    )

    assert len(apl.compiled) == 2


def test_parse_build_serialise_parse_round_trips_to_an_equal_object() -> None:
    """ROADMAP `w2e`'s W8 seam, in one line."""
    schema = raging_schema()
    actions = frozenset({"rage", "second_wind", "healing_word"})
    gates = {
        "reckless_attack": "barbarian.reckless_attack",
        "divine_smite": "paladin.divine_smite",
        "uncanny_dodge": "rogue.uncanny_dodge",
    }
    parsed = Apl.from_yaml(
        SPIKE_APL, schema=schema, actions=actions | set(gates), gates=gates
    )

    rebuilt = Apl(
        parsed.entries, schema=schema, actions=actions | set(gates), gates=gates
    )
    again = Apl.from_yaml(
        rebuilt.to_yaml(), schema=schema, actions=actions | set(gates), gates=gates
    )

    assert rebuilt == parsed
    assert again == parsed
    assert again.canonical_hash == parsed.canonical_hash
    assert hash(again) == hash(parsed)


def test_the_canonical_hash_moves_with_the_entries_and_not_with_whitespace() -> None:
    one = Apl([Entry("attack", if_="enemies.alive >= 1")])
    same = Apl.from_yaml('- { action: attack, if: "enemies.alive >= 1" }')
    other = Apl([Entry("attack", if_="enemies.alive >= 2")])

    assert one.canonical_hash == same.canonical_hash
    assert one.canonical_hash != other.canonical_hash
    assert len(one.canonical_hash) == 64


def test_serialising_leaves_out_the_clauses_an_entry_does_not_have() -> None:
    assert Apl([Entry("dodge")]).to_yaml().strip() == "- action: dodge"


# --- 7. debt #34: the destination is a footprint question --------------------


def test_a_large_mover_closing_stops_one_square_short_of_the_anchor_answer() -> None:
    """Debt #34. A Large mover anchored at (0, 0) fills (0, 0)-(1, 1), so from
    an anchor at x its near edge is x + 1: against a Medium target at (5, 0)
    it is in five feet of reach at x = 3, three steps away. Anchor arithmetic
    answers x = 4 — a fourth step into a square whose footprint covers the
    target's own (`docs/decisions.md` § w1d)."""
    mover = creature("ogre", Cell(0, 0), size=Size.LARGE)
    target = creature("target", Cell(5, 0))
    world = seated((mover, "monsters"), (target, "heroes"))
    mover.economy.turn_started(mover.speed_ft)
    apl = Apl([Entry("attack", move="toward nearest of enemies until in_reach")]).bind(
        mover, world
    )

    choice = apl.choose(mover, [])
    assert isinstance(choice, Walk)
    walked = choice.execute(rng=no_dice())

    assert len(walked) == 3
    assert apart(mover, target) == 5
    assert reaches(mover, target)


def test_a_medium_mover_closing_on_the_same_square_takes_the_fourth_step() -> None:
    """The same geometry with a Medium mover: four steps, which is what makes
    the Large mover's three a footprint answer and not an off-by-one."""
    mover = creature("goblin", Cell(0, 0))
    target = creature("target", Cell(5, 0))
    world = seated((mover, "monsters"), (target, "heroes"))
    mover.economy.turn_started(mover.speed_ft)
    apl = Apl([Entry("attack", move="toward nearest of enemies until in_reach")]).bind(
        mover, world
    )

    choice = apl.choose(mover, [])
    assert isinstance(choice, Walk)

    assert len(choice.execute(rng=no_dice())) == 4
    assert apart(mover, target) == 5


def test_a_large_target_is_closed_on_against_its_near_edge() -> None:
    """Debt #34 from the other side. A Large target at (5, 0) fills
    (5, 0)-(6, 1); a Medium mover coming from (12, 0) is in reach at x = 7,
    five steps away, where anchor arithmetic answers x = 6 — the target's own
    square."""
    mover = creature("goblin", Cell(12, 0))
    target = creature("ogre", Cell(5, 0), size=Size.LARGE)
    world = seated((mover, "monsters"), (target, "heroes"))
    mover.economy.turn_started(mover.speed_ft)
    apl = Apl([Entry("attack", move="toward nearest of enemies until in_reach")]).bind(
        mover, world
    )

    choice = apl.choose(mover, [])
    assert isinstance(choice, Walk)
    walked = choice.execute(rng=no_dice())

    assert len(walked) == 5
    assert apart(mover, target) == 5
    assert reaches(mover, target)


def test_a_retreat_stops_as_soon_as_it_is_far_enough() -> None:
    """`away_from … to 15 ft` from five feet away: two steps, and the third is
    not walked."""
    kiter = creature("kiter", Cell(1, 0))
    threat = creature("threat", Cell(0, 0))
    world = seated((kiter, "heroes"), (threat, "monsters"))
    kiter.economy.turn_started(kiter.speed_ft)
    apl = Apl([Entry("attack", move="away_from nearest of enemies to 15 ft")]).bind(
        kiter, world
    )

    choice = apl.choose(kiter, [])
    assert isinstance(choice, Retreat)
    walked = choice.execute(rng=no_dice())

    assert len(walked) == 2
    assert apart(kiter, threat) == 15
    assert apl.choose(kiter, []) is None


def test_to_max_range_reads_the_range_off_the_weapon_rather_than_the_clause() -> None:
    """`to_max_range` names no distance: the clause carries none and the walk
    asks what the actor wields."""
    clause = compile_move(
        "away_from nearest of enemies to_max_range", NamespaceSchema()
    )
    assert clause.keep_ft is None

    kiter = creature("archer", Cell(1, 0), weapon=LONGBOW)
    threat = creature("threat", Cell(0, 0))
    world = seated((kiter, "heroes"), (threat, "monsters"))
    kiter.economy.turn_started(kiter.speed_ft)
    apl = Apl([Entry("attack", move=clause.source)]).bind(kiter, world)

    choice = apl.choose(kiter, [])

    assert isinstance(choice, Retreat)
    assert choice.target is threat
    assert choice.keep_ft is not None


def test_a_retreat_that_is_already_far_enough_offers_no_walk() -> None:
    kiter = creature("kiter", Cell(6, 0))
    threat = creature("threat", Cell(0, 0))
    world = seated((kiter, "heroes"), (threat, "monsters"))
    kiter.economy.turn_started(kiter.speed_ft)
    apl = Apl([Entry("attack", move="away_from nearest of enemies to 15 ft")]).bind(
        kiter, world
    )

    assert apl.choose(kiter, []) is None
    assert kiter.position == Cell(6, 0)


# --- 8. debt #59: the dummy's policy is compiled from its profiles -----------


def test_a_static_dummy_has_no_move_and_takes_no_step() -> None:
    dummy = one_dummy(DummySpec(movement_profile=MovementProfile.static()))
    enemy = creature("enemy", Cell(9, 0))
    world = seated((dummy, "monsters"), (enemy, "heroes"))
    apl = arm_dummy(dummy, world, rng=no_dice())

    assert dummy_apl(dummy.spec).entries[1].move == "hold"
    assert take_turn(world, dummy, 1, policy=apl, rng=no_dice()) == 0
    assert dummy.position == Cell(0, 0)
    assert dummy.economy.movement_ft == dummy.speed_ft


def test_a_dummy_that_closes_to_melee_gets_this_rows_own_default() -> None:
    """ROADMAP § The W0 debt register #59: `closes_to_melee` maps to the
    default `move:`, not to a second grammar."""
    spec = DummySpec(movement_profile=MovementProfile.closes_to_melee())

    assert dummy_apl(spec).entries[1].move == DEFAULT_MOVE


def test_a_closing_dummy_walks_into_reach() -> None:
    """From (0, 0) to five feet of an enemy at (4, 0): three steps."""
    dummy = one_dummy(DummySpec(movement_profile=MovementProfile.closes_to_melee()))
    enemy = creature("enemy", Cell(4, 0))
    world = seated((dummy, "monsters"), (enemy, "heroes"))
    apl = arm_dummy(dummy, world, rng=no_dice())

    take_turn(world, dummy, 1, policy=apl, rng=no_dice())

    assert apart(dummy, enemy) == 5
    assert reaches(dummy, enemy)


def test_a_kiting_dummy_retreats_to_the_range_its_profile_names() -> None:
    """`kites_at(30)` from five feet away: five steps to thirty feet, with
    five of its thirty feet of movement left over."""
    dummy = one_dummy(
        DummySpec(movement_profile=MovementProfile.kites_at(30), position=Cell(1, 0))
    )
    enemy = creature("enemy", Cell(0, 0))
    world = seated((dummy, "monsters"), (enemy, "heroes"))
    apl = arm_dummy(dummy, world, rng=no_dice())

    taken = take_turn(world, dummy, 1, policy=apl, rng=no_dice())

    assert taken == 1
    assert apart(dummy, enemy) == 30
    assert dummy.economy.movement_ft == 5


@pytest.mark.parametrize(
    ("profile", "wounded_hp", "healthy_hp"),
    [(ThreatProfile.NEAREST, 14, HP - 7), (ThreatProfile.LOWEST_HP, 14 - 7, HP)],
)
def test_the_threat_profile_is_the_target_clause(
    profile: ThreatProfile, wounded_hp: int, healthy_hp: int
) -> None:
    """A pike's ten feet puts both enemies in reach: the healthy one at five
    feet and the wounded one at ten. `nearest` swings at the near one,
    `lowest_hp` at the far one, each for 7."""
    dummy = one_dummy(
        DummySpec(
            movement_profile=MovementProfile.closes_to_melee(), threat_profile=profile
        )
    )
    dummy.weapon = PIKE
    healthy = creature("healthy", Cell(1, 0))
    wounded = creature("wounded", Cell(2, 0), hp=14)
    world = seated(
        (dummy, "monsters"), (healthy, "heroes"), (wounded, "heroes"), log=EventLog()
    )
    apl = arm_dummy(dummy, world, rng=no_dice())
    rng = ScriptedRng(list(SEVEN))

    assert (
        dummy_apl(dummy.spec).entries[1].target
        == f"{profile.value} of enemies.in_reach"
    )
    take_turn(world, dummy, 1, policy=apl, rng=rng)

    assert wounded.hp == wounded_hp
    assert healthy.hp == healthy_hp
    assert rng.exhausted


def test_a_dummy_that_closes_to_melee_is_provoked() -> None:
    """`w2d`'s moved clause, first half. The dummy starts at (1, 0), inside
    the guard's five feet, and closes on the wounded enemy at (10, 0): the
    first step leaves the guard's reach and draws one swing for 11."""
    log = EventLog()
    rng = ScriptedRng(list(ELEVEN))
    spec = DummySpec(
        hp=60,
        position=Cell(1, 0),
        movement_profile=MovementProfile.closes_to_melee(),
        threat_profile=ThreatProfile.LOWEST_HP,
    )
    dummy = one_dummy(spec)
    guard = creature("guard", Cell(0, 0))
    quarry = creature("quarry", Cell(10, 0), hp=5)
    world = seated((dummy, "monsters"), (guard, "heroes"), (quarry, "heroes"), log=log)
    apl = arm_dummy(dummy, world, rng=rng)
    guard_apl = bound("- { action: opportunity_attack }", guard, world)
    guard.attach(
        OpportunityAttacks(
            guard, world=world, rng=rng, trigger=guard_apl.namespace.trigger
        )
    )

    take_turn(world, dummy, 1, policy=apl, rng=rng)

    assert attacks(log) == [
        (ATTACK, "guard", "Target Dummy", "Greatsword", Outcome.HIT, 11)
    ]
    assert dummy.hp == 60 - 11 == 49
    # Thirty feet along the lowest-`y` route to its stand (§ w2c2's tie-break).
    assert dummy.position == Cell(7, -1)
    assert guard.economy.reactions == 0
    assert rng.exhausted


def test_a_dummy_that_closes_to_melee_provokes() -> None:
    """`w2d`'s moved clause, second half: the gate the dummy's own compiled
    policy answers. A runner leaving its reach is swung at for 7."""
    log = EventLog()
    rng = ScriptedRng(list(SEVEN))
    dummy = one_dummy(DummySpec(movement_profile=MovementProfile.closes_to_melee()))
    dummy.weapon = PIKE
    runner = creature("runner", Cell(2, 0))
    world = seated((dummy, "monsters"), (runner, "heroes"), log=log)
    runner.economy.turn_started(runner.speed_ft)
    arm_dummy(dummy, world, rng=rng)

    Retreat(runner, dummy, world=world, keep_ft=25).execute(rng=rng)

    assert attacks(log) == [(ATTACK, "Target Dummy", "runner", "Pike", Outcome.HIT, 7)]
    assert runner.hp == HP - 7 == 13
    assert dummy.economy.reactions == 0
    assert rng.exhausted


def test_an_unarmed_dummy_offered_the_reaction_still_answers_its_gate() -> None:
    """A dummy's armament is `w2g`'s `damage_per_round`; the policy compiled
    for it says yes either way, so the offer reaches it and the attack is what
    cannot be made."""
    dummy = one_dummy(DummySpec())
    runner = creature("runner", Cell(1, 0))
    world = seated((dummy, "monsters"), (runner, "heroes"), log=EventLog())
    apl = arm_dummy(dummy, world, rng=no_dice())

    assert dummy.weapon is None
    assert apl.allows(OpportunityAttack.ID, Marker(dummy), leaving(dummy, runner))


# --- the gate the engine asks, end to end ------------------------------------


def test_the_dispatcher_asks_the_apl_under_the_commands_own_id() -> None:
    """`OpportunityAttacks` declares `gate="opportunity_attack"`, which is the
    action id an author writes, so no build maps it."""
    guard = creature("guard", Cell(0, 0))
    world = seated((guard, "monsters"))
    apl = Apl([Entry(OpportunityAttack.ID)]).bind(guard, world)

    assert OpportunityAttacks.ID == OpportunityAttack.ID == "opportunity_attack"
    assert apl.compiled[0].gate == OpportunityAttack.ID


def test_a_gate_id_a_feature_declares_is_mapped_from_the_action_id() -> None:
    """The spike's three in-attack entries: the author writes `divine_smite`
    and the feature declares `paladin.divine_smite`."""
    apl = compile_spike_apl()
    gates = {entry.action: entry.gate for entry in apl.compiled}

    assert gates["divine_smite"] == "paladin.divine_smite"
    assert gates["uncanny_dodge"] == "rogue.uncanny_dodge"
    assert gates["attack"] == "attack"


def test_an_entry_the_action_economy_cannot_pay_for_is_not_taken() -> None:
    """DESIGN.md § APL: filtering to what the economy can still pay is part of
    the filter. The hero has no Action left, so `first()` is offered none."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry("attack"), Entry("dodge")]).bind(hero, world)

    assert apl.first(frozenset()) is None
    assert apl.first(frozenset({"dodge"})) is not None


def answering(gate: Gate, gate_id: str, source: HookSource, ctx: Context) -> bool:
    """`Gate.allows` called through the protocol, which is what the dispatcher
    holds."""
    return gate.allows(gate_id, source, ctx)


def test_the_gate_protocol_is_what_the_dispatcher_calls() -> None:
    hero = creature("hero", Cell(0, 0))
    runner = creature("runner", Cell(1, 0))
    world = seated((hero, "heroes"), (runner, "monsters"))
    apl = Apl([Entry(OpportunityAttack.ID)]).bind(hero, world)

    assert answering(apl, OpportunityAttack.ID, Marker(hero), leaving(hero, runner))


# --- the closed vocabulary against the Commands that exist -------------------


def command_ids() -> set[str]:
    """The `ID` of every non-abstract `Action` subclass in `dndsim.actions`,
    found by importing the package's modules and walking the subclass tree —
    the same discovery `tests/contract/test_actions.py` makes."""
    for module in pkgutil.iter_modules(actions.__path__):
        importlib.import_module(f"{actions.__name__}.{module.name}")
    found: set[str] = set()
    stack: list[type[Action[Any]]] = list(Action.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if not inspect.isabstract(cls) and cls.__module__.startswith(
            f"{actions.__name__}."
        ):
            found.add(cls.ID)
    return found


def test_the_action_vocabulary_holds_every_command_in_actions() -> None:
    """The vocabulary is closed, so a Command `COMMAND_IDS` does not name
    cannot be written in a build's `apl:` at all, and nothing else would say
    so. `w2j` adds Commands; this fails when one of them is left out."""
    assert command_ids() == set(COMMAND_IDS)


@pytest.mark.parametrize("action_id", sorted(command_ids()))
def test_every_command_can_be_named_by_an_entry(action_id: str) -> None:
    apl = Apl([Entry(action_id)])

    assert apl.entries[0].action == action_id


# --- the enemies and allies roots follow the world's seating ------------------


def names(members: tuple[Creature, ...]) -> list[str]:
    """Read through a call, so mypy does not narrow a root's property across
    the seating change between two reads."""
    return [one.name for one in members]


def test_seat_and_leave_each_move_the_world_s_seating() -> None:
    world = World()
    one = creature("one", Cell(0, 0))
    before = world.seating

    world.seat(one, "heroes")
    seated_at = world.seating
    world.leave(one)

    assert seated_at != before
    assert world.seating not in (before, seated_at)


def test_an_enemy_seated_after_a_read_appears_on_the_next() -> None:
    hero = creature("hero", Cell(0, 0))
    first = creature("first", Cell(1, 0))
    world = seated((hero, "heroes"), (first, "monsters"))
    enemies = Apl([Entry("attack")]).bind(hero, world).namespace.enemies
    assert names(enemies.alive) == ["first"]

    late = creature("late", Cell(0, 1))
    world.seat(late, "monsters")

    assert names(enemies.alive) == ["first", "late"]
    assert names(enemies.in_reach) == ["first", "late"]


def test_an_ally_seated_down_after_a_read_appears_on_the_next() -> None:
    """At 0 hit points and not dead: `allies.down`, not `allies.alive`."""
    hero = creature("hero", Cell(0, 0))
    world = seated((hero, "heroes"))
    allies = Apl([Entry("attack")]).bind(hero, world).namespace.allies
    assert names(allies.down) == []

    fallen = creature("fallen", Cell(1, 0), hp=0)
    world.seat(fallen, "heroes")

    assert names(allies.down) == ["fallen"]
    assert names(allies.alive) == []


def test_an_enemy_that_leaves_after_a_read_is_gone_on_the_next() -> None:
    hero = creature("hero", Cell(0, 0))
    gone = creature("gone", Cell(1, 0))
    stays = creature("stays", Cell(0, 1))
    world = seated((hero, "heroes"), (gone, "monsters"), (stays, "monsters"))
    enemies = Apl([Entry("attack")]).bind(hero, world).namespace.enemies
    assert names(enemies.alive) == ["gone", "stays"]

    world.leave(gone)

    assert names(enemies.alive) == ["stays"]
    assert names(enemies.in_reach) == ["stays"]


def test_an_enemy_that_dies_is_gone_with_no_change_of_seating() -> None:
    """Death is not a seating change: `world.seating` stays put, and the
    roots still drop the dead creature on the next read."""
    hero = creature("hero", Cell(0, 0))
    doomed = creature("doomed", Cell(1, 0))
    stays = creature("stays", Cell(0, 1))
    world = seated((hero, "heroes"), (doomed, "monsters"), (stays, "monsters"))
    enemies = Apl([Entry("attack")]).bind(hero, world).namespace.enemies
    assert names(enemies.in_reach) == ["doomed", "stays"]
    seating = world.seating

    doomed.die()

    assert world.seating == seating
    assert names(enemies.alive) == ["stays"]
    assert names(enemies.in_reach) == ["stays"]


# --- `target` is bound before an entry's `if:` reads it ----------------------

PICKED_IF: Final = "target.hp > 10"


def picked_and_stale(
    picked_hp: int, stale_hp: int
) -> tuple[Creature, Creature, Creature, World]:
    """A hero, the one enemy its `target:` can pick, and a bystander seated on
    the hero's side that `target` is left bound to beforehand."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0), hp=picked_hp)
    stale = creature("stale", Cell(5, 5), hp=stale_hp)
    world = seated((hero, "heroes"), (stale, "heroes"), (goblin, "monsters"))
    return hero, goblin, stale, world


def targeted(hero: Creature, world: World) -> Apl:
    return Apl([Entry("attack", if_=PICKED_IF, target="lowest_hp of enemies")]).bind(
        hero, world
    )


def test_choose_reads_target_hp_off_the_creature_the_target_clause_picks() -> None:
    hero, goblin, stale, world = picked_and_stale(picked_hp=12, stale_hp=1)
    apl = targeted(hero, world)
    apl.namespace.target.bind(stale)

    choice = apl.choose(hero, [Attack(hero, goblin)])

    assert isinstance(choice, Attack)
    assert choice.target is goblin


def test_choose_declines_when_the_picked_creature_fails_the_if() -> None:
    hero, goblin, stale, world = picked_and_stale(picked_hp=8, stale_hp=20)
    apl = targeted(hero, world)
    apl.namespace.target.bind(stale)

    assert apl.choose(hero, [Attack(hero, goblin)]) is None


def test_first_reads_target_hp_off_the_creature_the_target_clause_picks() -> None:
    hero, _, stale, world = picked_and_stale(picked_hp=12, stale_hp=1)
    apl = targeted(hero, world)
    apl.namespace.target.bind(stale)

    assert apl.first(frozenset({"attack"})) is apl.compiled[0]


def test_first_declines_when_the_picked_creature_fails_the_if() -> None:
    hero, _, stale, world = picked_and_stale(picked_hp=8, stale_hp=20)
    apl = targeted(hero, world)
    apl.namespace.target.bind(stale)

    assert apl.first(frozenset({"attack"})) is None


def nearest_and_far(near_hp: int, far_hp: int) -> tuple[Creature, Creature, Creature]:
    hero = creature("hero", Cell(0, 0))
    near = creature("near", Cell(1, 0), hp=near_hp)
    far = creature("far", Cell(2, 0), hp=far_hp)
    return hero, near, far


def test_with_no_target_clause_choose_binds_the_nearest_offers_target() -> None:
    hero, near, far = nearest_and_far(near_hp=12, far_hp=1)
    world = seated((hero, "heroes"), (near, "monsters"), (far, "monsters"))
    apl = Apl([Entry("attack", if_=PICKED_IF)]).bind(hero, world)
    apl.namespace.target.bind(far)

    choice = apl.choose(hero, [Attack(hero, far), Attack(hero, near)])

    assert isinstance(choice, Attack)
    assert choice.target is near


def test_with_no_target_clause_the_nearest_offer_failing_the_if_declines() -> None:
    hero, near, far = nearest_and_far(near_hp=8, far_hp=20)
    world = seated((hero, "heroes"), (near, "monsters"), (far, "monsters"))
    apl = Apl([Entry("attack", if_=PICKED_IF)]).bind(hero, world)
    apl.namespace.target.bind(far)

    assert apl.choose(hero, [Attack(hero, far), Attack(hero, near)]) is None


# --- division only by a non-zero number written out --------------------------


@pytest.mark.parametrize("clause", ["self.hp / target.hp > 2", "self.hp / 0 > 1"])
def test_division_by_anything_but_a_written_non_zero_number_is_refused(
    clause: str,
) -> None:
    with pytest.raises(AplLoadError):
        Apl([Entry("attack", if_=clause)])


@pytest.mark.parametrize("clause", ["self.hp / 2 > 5", "self.hp / -2 < 0"])
def test_division_by_a_written_non_zero_number_compiles(clause: str) -> None:
    assert len(Apl([Entry("attack", if_=clause)]).compiled) == 1


# --- `with:` parameters -------------------------------------------------------

WITH_YAML: Final = """
- action: attack
  with:
    slot_level: "3 if trigger.crit else self.slots.highest"
    upcast: 2
"""


def test_a_with_mapping_is_held_as_pairs_in_written_order() -> None:
    (entry,) = Apl.from_yaml(WITH_YAML).entries

    assert entry.with_ == (
        ("slot_level", "3 if trigger.crit else self.slots.highest"),
        ("upcast", "2"),
    )


def test_a_bare_number_in_with_is_its_own_expression() -> None:
    (entry,) = Apl.from_yaml("- {action: attack, with: {slot_level: 3}}").entries

    assert entry.with_ == (("slot_level", "3"),)


def test_with_round_trips_through_serialisation() -> None:
    parsed = Apl.from_yaml(WITH_YAML)
    again = Apl.from_yaml(parsed.to_yaml())

    assert again == parsed
    assert again.entries[0].with_ == parsed.entries[0].with_
    assert again.canonical_hash == parsed.canonical_hash


def test_the_canonical_hash_moves_with_a_with_value() -> None:
    three = Apl.from_yaml("- {action: attack, with: {slot_level: 3}}")
    four = Apl.from_yaml("- {action: attack, with: {slot_level: 4}}")
    none = Apl.from_yaml("- {action: attack}")

    assert three.canonical_hash != four.canonical_hash
    assert three.canonical_hash != none.canonical_hash


def test_an_unknown_path_in_a_with_expression_fails_at_load() -> None:
    with pytest.raises(AplLoadError):
        Apl.from_yaml("- {action: attack, with: {slot_level: self.slots.hihgest}}")


@pytest.mark.parametrize("written", ["3", "[3]", '"slot_level"'])
def test_a_with_that_is_not_a_mapping_fails_at_load(written: str) -> None:
    with pytest.raises(AplLoadError):
        Apl.from_yaml(f"- {{action: attack, with: {written}}}")


def test_with_is_constructible_from_entry_objects() -> None:
    apl = Apl([Entry("attack", with_=(("slot_level", "3"),))])

    assert apl.entries[0].with_ == (("slot_level", "3"),)
    assert apl == Apl.from_yaml("- {action: attack, with: {slot_level: 3}}")


# --- the creature field list agrees everywhere ------------------------------


@pytest.mark.parametrize("name", sorted(CREATURE_FIELDS))
def test_every_creature_field_is_on_both_proxies(name: str) -> None:
    assert hasattr(SelfProxy, name)
    assert hasattr(TargetProxy, name)


def field_clause(path: str) -> str:
    kind = CREATURE_FIELDS[path.split(".", 1)[1]].kind
    return f"{path} >= 0" if kind is Kind.NUMBER else f"{path} or not {path}"


@pytest.mark.parametrize("root", ["self", "target"])
@pytest.mark.parametrize("name", sorted(CREATURE_FIELDS))
@pytest.mark.parametrize("target_bound", [True, False])
def test_every_creature_field_compiles_and_evaluates_on_both_roots(
    root: str, name: str, target_bound: bool
) -> None:
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry("attack", if_=field_clause(f"{root}.{name}"))]).bind(hero, world)
    if target_bound:
        apl.namespace.target.bind(goblin)

    assert apl.compiled[0].condition(apl.namespace) is True


# --- an attack the APL picks rebinds the APL's own `trigger` root ------------


def test_an_attack_the_apl_picks_carries_its_trigger_root() -> None:
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry("attack")]).bind(hero, world)
    offer = Attack(hero, goblin, log=world.log)
    assert offer.trigger is None

    choice = apl.choose(hero, [offer])

    assert isinstance(choice, Attack)
    assert choice.trigger is apl.namespace.trigger


def test_an_attack_offered_with_its_own_trigger_keeps_it() -> None:
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0))
    world = seated((hero, "heroes"), (goblin, "monsters"))
    apl = Apl([Entry("attack")]).bind(hero, world)
    own = TriggerProxy()

    choice = apl.choose(hero, [Attack(hero, goblin, trigger=own)])

    assert isinstance(choice, Attack)
    assert choice.trigger is own
    assert own is not apl.namespace.trigger


def test_a_turn_s_attack_rebinds_a_stale_trigger_root() -> None:
    """`trigger` is left as an earlier crit would leave it; the turn's hit is a
    natural 15, not a crit, for 4 + 4 + 3 = 11."""
    hero = creature("hero", Cell(0, 0), initiative=20)
    goblin = creature("goblin", Cell(1, 0))
    log = EventLog()
    world = seated((hero, "heroes"), (goblin, "monsters"), log=log)
    apl = Apl([Entry("attack")]).bind(hero, world)
    apl.namespace.trigger.bind(crit=True, damage_estimate=99)
    rng = ScriptedRng(list(ELEVEN))

    taken = take_turn(world, hero, 1, policy=apl, rng=rng)

    assert taken == 1
    assert attacks(log) == [(ATTACK, "hero", "goblin", "Greatsword", Outcome.HIT, 11)]
    assert goblin.hp == HP - 11 == 9
    assert apl.namespace.trigger.crit is False
    assert apl.namespace.trigger.damage_estimate == 11
    assert rng.exhausted


# --- the world's seating is moved only by `seat()` and `leave()` -------------


def test_the_world_s_seating_cannot_be_assigned() -> None:
    world = World()

    with pytest.raises(AttributeError):
        world.seating = 0  # type: ignore[misc]

    assert world.seating == 0


# --- a group operand of `and` / `or` / `not` reads as a bool -------------------


def reach_namespace(goblin_cell: Cell) -> Apl:
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", goblin_cell)
    world = seated((hero, "heroes"), (goblin, "monsters"))
    return Apl([Entry("attack")]).bind(hero, world)


def test_a_group_or_a_bool_compared_with_true_holds_with_an_enemy_in_reach() -> None:
    apl = reach_namespace(Cell(1, 0))
    compiled = compile_condition(
        "(enemies.in_reach or self.down) == True", NamespaceSchema()
    )

    assert compiled.fn(apl.namespace) is True


def test_a_group_and_true_is_exactly_true_with_an_enemy_in_reach() -> None:
    apl = reach_namespace(Cell(1, 0))
    compiled = compile_param("enemies.in_reach and True", NamespaceSchema())

    assert compiled.fn(apl.namespace) is True


def test_a_group_or_a_bool_is_exactly_false_with_no_enemy_in_reach() -> None:
    apl = reach_namespace(Cell(20, 0))
    compiled = compile_condition("enemies.in_reach or self.down", NamespaceSchema())

    assert compiled.fn(apl.namespace) is False


@pytest.mark.parametrize(
    ("goblin_cell", "expected"), [(Cell(1, 0), False), (Cell(20, 0), True)]
)
def test_not_a_group_is_true_only_when_it_is_empty(
    goblin_cell: Cell, expected: bool
) -> None:
    apl = reach_namespace(goblin_cell)
    compiled = compile_condition("not enemies.in_reach", NamespaceSchema())

    assert compiled.fn(apl.namespace) is expected


# --- an offer on the `trigger` root replaces the one before it whole ---------


def test_an_attack_offer_clears_a_movement_offer() -> None:
    runner = creature("runner", Cell(1, 0))
    trigger = TriggerProxy()
    trigger.offer_movement(runner, Cell(1, 0), Cell(2, 0))

    trigger.bind(crit=True, damage_estimate=9)

    assert trigger.crit is True
    assert trigger.damage_estimate == 9
    assert trigger.mover is None
    assert trigger.origin is None
    assert trigger.destination is None


def test_a_movement_offer_clears_an_attack_offer() -> None:
    runner = creature("runner", Cell(1, 0))
    trigger = TriggerProxy()
    trigger.bind(crit=True, damage_estimate=9)

    trigger.offer_movement(runner, Cell(1, 0), Cell(2, 0))

    assert trigger.crit is False
    assert trigger.damage_estimate == 0
    assert trigger.mover is runner
    assert trigger.origin == Cell(1, 0)
    assert trigger.destination == Cell(2, 0)


def test_a_movement_offer_after_a_crit_does_not_read_the_crit() -> None:
    guard = creature("guard", Cell(0, 0))
    runner = creature("runner", Cell(1, 0))
    world = seated((guard, "monsters"), (runner, "heroes"))
    apl = Apl([Entry(OpportunityAttack.ID, if_="not trigger.crit")]).bind(guard, world)
    apl.namespace.trigger.bind(crit=True, damage_estimate=9)

    assert apl.allows(OpportunityAttack.ID, Marker(guard), leaving(guard, runner))
    assert apl.namespace.trigger.damage_estimate == 0


# --- during an attack, `target` is the other party and a defender's
# `trigger` reads the attacker's newer offer ----------------------------------

DODGE: Final = "rogue.uncanny_dodge"
SMITE: Final = "paladin.divine_smite"


class Dodge(HookSource):
    """A gated defender's reaction asked during an attack: it halves the hit
    and records what its owner's APL read when it passed."""

    __slots__ = ("apl", "seen")

    ID = "test.dodge"

    def __init__(self, owner: Creature, apl: Apl) -> None:
        super().__init__(owner)
        self.apl = apl
        self.seen: list[tuple[bool, int, Creature | None, Creature | None]] = []

    @hook("on_incoming_damage", gate=DODGE)
    def dodge(self, ctx: IncomingDamageContext) -> R:
        namespace = self.apl.namespace
        trigger = namespace.trigger
        self.seen.append(
            (
                trigger.crit,
                trigger.damage_estimate,
                trigger.mover,
                namespace.target.creature,
            )
        )
        ctx.halve("uncanny dodge")
        return R.APPLIED


class Smite(HookSource):
    """A gated attacker's rider: two radiant damage on a hit."""

    __slots__ = ()

    ID = "test.smite"

    @hook("on_hit", gate=SMITE)
    def smite(self, ctx: HitContext) -> R:
        ctx.damage.add(flat=2, damage_type=DamageType.RADIANT)
        return R.APPLIED


def dodging(text: str, owner: Creature, world: World) -> tuple[Apl, Dodge]:
    """`text` bound to `owner` as its gate, `uncanny_dodge` mapped to
    `DODGE`, with a `Dodge` attached."""
    apl = bound(
        text,
        owner,
        world,
        actions=("uncanny_dodge",),
        gates={"uncanny_dodge": DODGE},
    )
    reaction = Dodge(owner, apl)
    owner.attach(reaction)
    return apl, reaction


def stale(trigger: TriggerProxy, *, mover: Creature) -> None:
    """Both offers at once, which neither `bind()` nor `offer_movement()`
    leaves: a crit for 99 and a mover."""
    trigger.crit = True
    trigger.damage_estimate = 99
    trigger.mover = mover


def test_a_defender_s_reaction_asked_during_an_attack_reads_that_attack() -> None:
    """The goblin's root is left as an earlier crit while the hero moved would
    leave it; the hero's natural 15 is a hit, not a crit, for 4 + 4 + 3 = 11,
    which the goblin's gate reads and halves to 5."""
    hero = creature("hero", Cell(0, 0), initiative=20)
    goblin = creature("goblin", Cell(1, 0))
    log = EventLog()
    world = seated((hero, "heroes"), (goblin, "monsters"), log=log)
    apl = Apl([Entry("attack")]).bind(hero, world)
    theirs, reaction = dodging(
        '- { action: uncanny_dodge, if: "trigger.damage_estimate == 11 '
        'and not trigger.crit and target.hp == 20" }',
        goblin,
        world,
    )
    stale(theirs.namespace.trigger, mover=hero)
    rng = ScriptedRng(list(ELEVEN))

    taken = take_turn(world, hero, 1, policy=apl, rng=rng)

    assert taken == 1
    assert reaction.seen == [(False, 11, None, hero)]
    assert attacks(log) == [(ATTACK, "hero", "goblin", "Greatsword", Outcome.HIT, 5)]
    assert goblin.hp == HP - 11 // 2 == 15
    assert apl.namespace.trigger.crit is False
    assert apl.namespace.trigger.damage_estimate == 11
    assert rng.exhausted


@pytest.mark.parametrize(("rolled", "dodged"), [(7, True), (5, False)])
def test_a_reaction_reads_the_opportunity_attack_not_its_owner_s_earlier_crit(
    rolled: int, dodged: bool
) -> None:
    """The hero crits the goblin on its turn: a natural 20 and 2d6 doubled to
    6 + 6 + 6 + 6, + 3 = 27. It then walks out of the dummy's pike reach, and
    the dummy's opportunity attack, a natural 15 at + 2 = 17 against AC 12,
    deals 1d10 as rolled. The hero's gate reads that, not its own 27: a 7 is
    halved to 3, and a 5 is taken whole."""
    log = EventLog()
    dummy = one_dummy(DummySpec(hp=60, position=Cell(0, 0)))
    dummy.weapon = PIKE
    hero = creature("hero", Cell(2, 0), initiative=20)
    goblin = creature("goblin", Cell(2, 1), hp=30)
    world = seated((dummy, "monsters"), (hero, "heroes"), (goblin, "monsters"), log=log)
    rng = ScriptedRng([20, 6, 6, 6, 6, HIT, rolled])
    arm_dummy(dummy, world, rng=rng)
    apl, reaction = dodging(
        """
        - { action: attack }
        - { action: uncanny_dodge, if: "trigger.damage_estimate >= 7" }
        """,
        hero,
        world,
    )

    assert take_turn(world, hero, 1, policy=apl, rng=rng) == 1
    assert apl.namespace.trigger.crit is True
    assert apl.namespace.trigger.damage_estimate == 27
    hero.economy.turn_started(hero.speed_ft)
    Retreat(hero, dummy, world=world, keep_ft=25).execute(rng=rng)

    taken = rolled // 2 if dodged else rolled
    assert attacks(log) == [
        (ATTACK, "hero", "goblin", "Greatsword", Outcome.CRITICAL_HIT, 27),
        (ATTACK, "Target Dummy", "hero", "Pike", Outcome.HIT, taken),
    ]
    assert reaction.seen == ([(False, rolled, None, dummy)] if dodged else [])
    assert hero.hp == HP - taken
    assert rng.exhausted


def test_a_defender_s_target_is_its_attacker_not_its_last_pick() -> None:
    """The hero last picked the goblin at 5 hit points; the ogre at 20 attacks
    it, and `target.hp > 10` reads the ogre: 11 halved to 5. The ogre has no
    APL, so there is no offer of its to take and the hero's root keeps its
    own."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0), hp=5)
    ogre = creature("ogre", Cell(0, 1))
    log = EventLog()
    world = seated((hero, "heroes"), (goblin, "monsters"), (ogre, "monsters"), log=log)
    apl, reaction = dodging(
        """
        - { action: attack, target: "lowest_hp of enemies" }
        - { action: uncanny_dodge, if: "target.hp > 10" }
        """,
        hero,
        world,
    )
    apl.choose(hero, [Attack(hero, goblin), Attack(hero, ogre)])
    assert apl.namespace.target.creature is goblin
    rng = ScriptedRng(list(ELEVEN))

    resolve_attack(ogre, hero, rng=rng, log=log)

    assert reaction.seen == [(False, 0, None, ogre)]
    assert attacks(log) == [(ATTACK, "ogre", "hero", "Greatsword", Outcome.HIT, 5)]
    assert hero.hp == HP - 5
    assert rng.exhausted


def test_an_attacker_s_target_is_its_defender_not_its_last_pick() -> None:
    """The hero last picked the ogre at 20 hit points; an attack it makes on
    the goblin at 5 asks its rider with `target` bound to the goblin, so
    `target.hp < 10` passes: 11 + 2 radiant = 13."""
    hero = creature("hero", Cell(0, 0))
    goblin = creature("goblin", Cell(1, 0), hp=5)
    ogre = creature("ogre", Cell(0, 1))
    log = EventLog()
    world = seated((hero, "heroes"), (goblin, "monsters"), (ogre, "monsters"), log=log)
    apl = bound(
        """
        - { action: attack, target: "highest_hp of enemies" }
        - { action: smite, if: "target.hp < 10" }
        """,
        hero,
        world,
        actions=("smite",),
        gates={"smite": SMITE},
    )
    hero.attach(Smite(hero))
    apl.choose(hero, [Attack(hero, goblin), Attack(hero, ogre)])
    assert apl.namespace.target.creature is ogre
    rng = ScriptedRng(list(ELEVEN))

    resolve_attack(hero, goblin, rng=rng, trigger=apl.namespace.trigger, log=log)

    assert attacks(log) == [(ATTACK, "hero", "goblin", "Greatsword", Outcome.HIT, 13)]
    assert apl.namespace.target.creature is goblin
    assert rng.exhausted


def test_a_defender_s_newer_offer_is_not_overwritten_by_an_older_attacker_s() -> None:
    """The ogre's root is told its offer first, the hero's second: asked
    during the ogre's attack, the hero keeps its own newer one."""
    hero = creature("hero", Cell(0, 0))
    ogre = creature("ogre", Cell(0, 1))
    world = seated((hero, "heroes"), (ogre, "monsters"))
    theirs = Apl([Entry("attack")]).bind(ogre, world).namespace.trigger
    mine = Apl(
        [Entry("uncanny_dodge", if_="trigger.damage_estimate == 4")],
        actions=("uncanny_dodge",),
        gates={"uncanny_dodge": DODGE},
    ).bind(hero, world)
    theirs.bind(crit=True, damage_estimate=30)
    mine.namespace.trigger.bind(damage_estimate=4)
    incoming = IncomingDamageContext(
        defender=hero, attacker=ogre, amounts={DamageType.SLASHING: 30}
    )

    trigger = mine.namespace.trigger

    assert mine.allows(DODGE, Marker(hero), incoming)
    assert (trigger.crit, trigger.damage_estimate) == (False, 4)
    assert mine.namespace.target.creature is ogre

    theirs.bind(crit=True, damage_estimate=30)

    assert not mine.allows(DODGE, Marker(hero), incoming)
    assert (trigger.crit, trigger.damage_estimate) == (True, 30)


# --- `==` and the canonical hash agree on the order of `with:` --------------


def with_pairs(*pairs: tuple[str, str]) -> Apl:
    return Apl([Entry("attack", with_=pairs)])


def test_with_pairs_in_another_order_are_unequal_and_hash_apart() -> None:
    written = with_pairs(("a", "1"), ("b", "2"))
    swapped = with_pairs(("b", "2"), ("a", "1"))

    assert written != swapped
    assert written.canonical_hash != swapped.canonical_hash


def test_equal_with_pairs_hash_alike() -> None:
    first = with_pairs(("a", "1"), ("b", "2"))
    second = with_pairs(("a", "1"), ("b", "2"))

    assert first == second
    assert first.canonical_hash == second.canonical_hash


# --- `toward <group>` stops at an enemy already in reach ---------------------


TOWARD_ENEMIES: Final = '- { action: attack, move: "toward enemies until in_reach" }'


def test_toward_a_group_attacks_the_enemy_already_in_reach() -> None:
    """`near` is beside the hero and seated first, so the walk does not set
    off toward `far`: the hero stays and swings at `near` for 11."""
    hero = creature("hero", Cell(0, 0), initiative=20)
    near = creature("near", Cell(1, 0))
    far = creature("far", Cell(7, 0))
    log = EventLog()
    world = seated((hero, "heroes"), (near, "monsters"), (far, "monsters"), log=log)
    apl = bound(TOWARD_ENEMIES, hero, world)
    rng = ScriptedRng(list(ELEVEN))

    taken = take_turn(world, hero, 1, policy=apl, rng=rng)

    assert taken == 1
    assert attacks(log) == [(ATTACK, "hero", "near", "Greatsword", Outcome.HIT, 11)]
    assert hero.position == Cell(0, 0)
    assert hero.economy.movement_ft == 30
    assert near.hp == 9
    assert far.hp == HP
    assert rng.exhausted


def test_toward_a_group_with_an_enemy_in_reach_offers_no_walk() -> None:
    hero = creature("hero", Cell(0, 0))
    near = creature("near", Cell(1, 0))
    far = creature("far", Cell(7, 0))
    world = seated((hero, "heroes"), (near, "monsters"), (far, "monsters"))
    apl = Apl.from_yaml(TOWARD_ENEMIES).bind(hero, world)
    hero.economy.turn_started(hero.speed_ft)

    choice = apl.choose(hero, [Attack(hero, near), Attack(hero, far)])

    assert isinstance(choice, Attack)
    assert choice.target is near


def test_toward_a_group_with_nobody_in_reach_walks_toward_the_first() -> None:
    hero = creature("hero", Cell(0, 0))
    first = creature("first", Cell(3, 0))
    second = creature("second", Cell(7, 0))
    world = seated((hero, "heroes"), (first, "monsters"), (second, "monsters"))
    apl = Apl.from_yaml(TOWARD_ENEMIES).bind(hero, world)
    hero.economy.turn_started(hero.speed_ft)

    choice = apl.choose(hero, [])

    assert isinstance(choice, Move)
    assert choice.target is first


@pytest.mark.parametrize("reach", [5, 10, 15])
@pytest.mark.parametrize("target_size", list(SIZE_WIDTH_CELLS))
@pytest.mark.parametrize("actor_size", list(SIZE_WIDTH_CELLS))
def test_the_stand_search_is_exactly_the_anchors_in_reach(
    actor_size: Size, target_size: Size, reach: int
) -> None:
    """`_stands` derives its box from `distance_ft`'s gap without asking
    `in_reach`; a brute-force scan asks it (`docs/decisions.md` § w2e)."""
    polearm = Weapon(
        name="Polearm",
        damage=Dice(1, 10),
        damage_type=DamageType.PIERCING,
        reach_ft=reach,
    )
    target = creature("target", Cell(20, 20), size=target_size)
    actor = creature("actor", Cell(0, 0), weapon=polearm, size=actor_size)
    scan = range(20 - 16, 20 + 17)

    expected = {
        Cell(x, y)
        for x in scan
        for y in scan
        if in_reach(
            Cell(x, y),
            target.position,
            actor.reach_ft,
            origin_size=actor.size,
            destination_size=target.size,
        )
    }

    stands = _stands(actor, target, frozenset())
    found = [Cell(x, y) for _, y, x in stands]
    assert len(found) == len(set(found))
    assert set(found) == expected
    assert [steps for steps, _, _ in stands] == [
        distance_ft(actor.position, cell) // 5 for cell in found
    ]
    assert stands == sorted(stands)
