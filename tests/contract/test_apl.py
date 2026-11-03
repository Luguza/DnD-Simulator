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
from dndsim.actions.base import Action
from dndsim.actions.move import Move, Retreat, Walk
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
from dndsim.apl.expr import AplLoadError, MoveMode, compile_move
from dndsim.apl.schema import Kind, NamespaceSchema
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
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.engine.attack import ATTACK
from dndsim.engine.turn import Policy, take_turn
from dndsim.engine.world import World
from dndsim.hooks.contexts import Context, LeaveReachContext
from dndsim.hooks.dispatch import Gate
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType, Outcome, Size
from dndsim.tools.spike import SPIKE_APL, compile_spike_apl

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
    mover still stands on and `destination` the one it is stepping into."""
    world, runner, quarry, apl, rng = chase(mover_hp=9, watcher_apl=WOUNDED_RUNNER)

    Move(runner, quarry, world=world).execute(rng=rng)

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
