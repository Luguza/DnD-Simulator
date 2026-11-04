"""The turn contract: initiative, what a turn offers, and what its two
boundaries do.

Every creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Dexterity 14 (+2), Wisdom 14 (+2), every other score 10,
20 hit points and no armour, so AC 10 + 2 = 12. A greatsword attack swings at
natural + 3 + 2 = natural + 5 against AC 12 and deals 2d6 + 3; a Wisdom save
adds +2. An initiative roll is a d20 + 2 and then one tiebreak face of a
2**32-sided die.

Sources: XPHB (2024) "Combat" -> Initiative, Your Turn, Reactions; Rules
Glossary "Incapacitated"; "Dropping to 0 Hit Points" -> Death Saving Throws;
ROADMAP `w2c`; `docs/decisions.md` § w2c.
"""

from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim.actions.base import Action
from dndsim.actions.dash import Dash
from dndsim.actions.dodge import Dodge
from dndsim.actions.move import Move, Retreat
from dndsim.conditions.exhaustion import Exhaustion
from dndsim.conditions.frightened import Frightened
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.paralyzed import Paralyzed
from dndsim.conditions.petrified import Petrified
from dndsim.conditions.prone import Prone
from dndsim.conditions.stunned import Stunned
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, in_reach
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import Duration, InitiativeKey, Reset, TimeKey, TurnPhase
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.effects import Effect, RepeatSave
from dndsim.creatures.stats import StatBlock, Weapon
from dndsim.engine.attack import AttackResult
from dndsim.engine.damage import resolve_damage
from dndsim.engine.dying import DROPPED
from dndsim.engine.healing import HEAL, heal
from dndsim.engine.saves import SAVE, saving_throw
from dndsim.engine.turn import (
    DEATH_SAVE,
    INITIATIVE,
    TURN_END,
    TURN_START,
    offers,
    roll_initiative,
    take_turn,
)
from dndsim.engine.world import World
from dndsim.hooks.contexts import (
    DamageBag,
    HitContext,
    InitiativeContext,
    MoveContext,
    SavingThrowContext,
    TurnContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.definitions import ON_HIT
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import (
    Ability,
    ActionCost,
    DamageType,
    Outcome,
    SaveOutcome,
    SaveRepeat,
)
from weapon_fixtures import GREATSWORD, LONGBOW

SCORES: Final = MappingProxyType(
    {
        **dict.fromkeys(Ability, 10),
        Ability.STRENGTH: 16,  # +3
        Ability.DEXTERITY: 14,  # +2
        Ability.WISDOM: 14,  # +2
    }
)
HP: Final = 20
ORIGIN: Final = Cell(0, 0)
EAST: Final = Cell(1, 0)
"""Five feet from `ORIGIN`, inside a greatsword's reach."""
FAR: Final = Cell(4, 0)
"""Twenty feet from `ORIGIN`, out of every reach here."""

TIEBREAK_FACES: Final = 2**32
HALF: Final = 2**31 + 1
"""The tiebreak face that draws exactly 0.5: (2**31 + 1 - 1) / 2**32."""


def key(initiative: int, *, tiebreak: float = 0.5) -> InitiativeKey:
    return InitiativeKey(
        initiative=initiative, dexterity=14, player_character=False, tiebreak=tiebreak
    )


FIRST: Final = key(20)
SECOND: Final = key(10)


def creature(
    name: str,
    *,
    cell: Cell = ORIGIN,
    initiative: InitiativeKey = FIRST,
    weapon: Weapon | None = GREATSWORD,
    pc: bool = False,
    speed: int = 30,
    scores: MappingProxyType[Ability, int] = SCORES,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=scores, max_hp=HP, speed_ft=speed),
        position=cell,
        weapon=weapon,
        initiative=initiative,
        player_character=pc,
    )


def world(*seats: tuple[Creature, str], log: EventLog | None = None) -> World:
    seated = World(log=log)
    for one, side in seats:
        seated.seat(one, side)
    return seated


def duel(*, log: EventLog | None = None) -> tuple[World, Creature, Creature]:
    """A fighter acting first and a goblin acting second, five feet apart."""
    fighter = creature("fighter", initiative=FIRST)
    goblin = creature("goblin", cell=EAST, initiative=SECOND)
    return world((fighter, "heroes"), (goblin, "monsters"), log=log), fighter, goblin


def no_dice() -> ScriptedRng:
    return ScriptedRng([])


Chooser = Callable[[Creature, Sequence[Action[Any]]], Action[Any] | None]


class Recorder:
    """A policy that records the ids it was offered on every call and asks
    `chooser` which to take; by default it takes nothing."""

    def __init__(self, chooser: Chooser | None = None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._chooser = chooser

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        self.calls.append((actor.name, [offer.ID for offer in offers]))
        return None if self._chooser is None else self._chooser(actor, offers)


def first_of(action_id: str) -> Chooser:
    """Take the first offer with `action_id`, or end the turn."""

    def chooser(actor: Creature, offered: Sequence[Action[Any]]) -> Action[Any] | None:
        return next((one for one in offered if action_id == one.ID), None)

    return chooser


def ids(offered: Sequence[Action[Any]]) -> list[str]:
    return [one.ID for one in offered]


def kinds(log: EventLog, *wanted: str) -> list[tuple[object, ...]]:
    return [record for record in log if record[0] in wanted]


# --- Initiative --------------------------------------------------------------


def test_initiative_is_a_d20_plus_the_dexterity_modifier_and_one_tiebreak() -> None:
    """d20 12 + 2 = 14. Tiebreak face 2**31 + 1: (2**31 + 1 - 1) / 2**32 = 0.5.
    The key carries the Dexterity *score*, 14, not the modifier."""
    goblin = creature("goblin")
    rng = ScriptedRng([12, HALF])
    rolled = roll_initiative(goblin, rng=rng)
    assert rolled == InitiativeKey(
        initiative=14, dexterity=14, player_character=False, tiebreak=0.5
    )
    assert rolled.after == ()
    assert rng.exhausted


def test_initiative_sets_nothing_on_the_roller() -> None:
    goblin = creature("goblin", initiative=UNSEATED)
    roll_initiative(goblin, rng=ScriptedRng([12, HALF]))
    assert goblin.initiative is UNSEATED


@pytest.mark.parametrize(
    ("face", "tiebreak"),
    [(1, 0.0), (TIEBREAK_FACES, (TIEBREAK_FACES - 1) / TIEBREAK_FACES)],
)
def test_the_tiebreak_spans_zero_up_to_but_not_including_one(
    face: int, tiebreak: float
) -> None:
    """(1 - 1) / 2**32 = 0.0, and (2**32 - 1) / 2**32, the largest float below
    1 the draw can give."""
    rolled = roll_initiative(creature("goblin"), rng=ScriptedRng([10, face]))
    assert rolled.tiebreak == tiebreak
    assert 0.0 <= rolled.tiebreak < 1.0


def test_a_player_character_carries_that_into_its_key() -> None:
    rolled = roll_initiative(creature("pc", pc=True), rng=ScriptedRng([10, 1]))
    assert rolled.player_character is True


class Alert(HookSource):
    """+3 and Advantage on Initiative."""

    __slots__ = ()

    ID = "test.alert"

    @hook("on_initiative_roll")
    def sharp(self, ctx: InitiativeContext) -> R:
        ctx.bonus.add(flat=3)
        ctx.grant_advantage(self.ID)
        return R.APPLIED


def test_on_initiative_roll_handlers_add_to_the_total_and_grant_advantage() -> None:
    """d20s 4 and 17 with Advantage, kept 17: 17 + 2 + 3 = 22. Tiebreak face
    1: 0.0."""
    rogue = creature("rogue")
    rogue.attach(Alert(rogue))
    rng = ScriptedRng([4, 17, 1])
    rolled = roll_initiative(rogue, rng=rng)
    assert (rolled.initiative, rolled.tiebreak) == (22, 0.0)
    assert rng.exhausted


def test_an_incapacitated_roller_rolls_initiative_with_disadvantage() -> None:
    """d20s 17 and 4 with Disadvantage, kept 4: 4 + 2 = 6."""
    stunned = creature("stunned")
    stunned.effects.apply(Stunned(stunned))
    rng = ScriptedRng([17, 4, 1])
    assert roll_initiative(stunned, rng=rng).initiative == 6
    assert rng.exhausted


def test_a_seated_roller_writes_its_initiative_to_the_log() -> None:
    """`(INITIATIVE, name, natural, total, tiebreak)`: 12, 12 + 2 = 14, 0.5."""
    log = EventLog()
    goblin = creature("goblin")
    world((goblin, "monsters"), log=log)
    roll_initiative(goblin, rng=ScriptedRng([12, HALF]))
    assert log.records == ((INITIATIVE, "goblin", 12, 14, 0.5),)


# --- What a turn offers ------------------------------------------------------


def test_a_turn_offers_every_command_it_can_pay_for_in_a_fixed_order() -> None:
    """One Attack per enemy in reach, in seating order, then Dodge, Disengage
    and Dash. An ally that is not dying is not offered a Stabilize, and
    nothing was dropped to pick up."""
    fighter = creature("fighter")
    ally = creature("ally", cell=Cell(0, 1))
    orc = creature("orc", cell=EAST)
    goblin = creature("goblin", cell=Cell(1, 1))
    seated = world(
        (orc, "monsters"), (fighter, "heroes"), (goblin, "monsters"), (ally, "heroes")
    )
    fighter.economy.turn_started(fighter.speed_ft)
    offered = offers(seated, fighter)
    assert ids(offered) == ["attack", "attack", "dodge", "disengage", "dash"]
    assert [getattr(one, "target", None) for one in offered[:2]] == [orc, goblin]


def test_a_dead_enemy_and_one_out_of_reach_are_not_offered() -> None:
    fighter = creature("fighter")
    dead = creature("dead", cell=EAST)
    distant = creature("distant", cell=FAR)
    seated = world((fighter, "heroes"), (dead, "monsters"), (distant, "monsters"))
    dead.die()
    fighter.economy.turn_started(fighter.speed_ft)
    offered = offers(seated, fighter)
    # The distant enemy is offered as somewhere to walk to, not to swing at.
    assert ids(offered) == ["dodge", "disengage", "dash", "move"]
    assert getattr(offered[-1], "target", None) is distant


def test_an_enemy_in_range_of_a_bow_is_offered_and_shot_in_the_world() -> None:
    """Twenty feet is out of every reach and inside a Longbow's 150. The
    offered Attack carries the world close combat is asked of: 15 + 2 + 2 = 19
    against AC 12, for 1d8 (4) + 2 = 6 (`docs/decisions.md` § w2f2)."""
    archer = creature("archer", weapon=LONGBOW)
    distant = creature("distant", cell=FAR)
    seated = world((archer, "heroes"), (distant, "monsters"))
    archer.economy.turn_started(archer.speed_ft)
    offered = offers(seated, archer)
    assert ids(offered) == ["attack", "dodge", "disengage", "dash", "move"]

    rng = ScriptedRng([15, 4])
    assert offered[0].execute(rng=rng) == AttackResult(Outcome.HIT, 6)
    assert rng.exhausted


def test_a_dying_ally_in_reach_is_offered_a_stabilize() -> None:
    """A player character dropped to 0 by exactly 20 on 20 hit points."""
    fighter = creature("fighter")
    pc = creature("pc", cell=EAST, pc=True)
    seated = world((fighter, "heroes"), (pc, "heroes"))
    resolve_damage(pc, {DamageType.SLASHING: HP}, rng=no_dice())
    fighter.economy.turn_started(fighter.speed_ft)
    assert ids(offers(seated, fighter)) == ["stabilize", "dodge", "disengage", "dash"]


def test_nothing_is_offered_before_the_turn_hands_back_an_action() -> None:
    """A fresh economy holds no Action, and every one of the six but the free
    pick-up costs one."""
    seated, fighter, _ = duel()
    assert offers(seated, fighter) == []


@pytest.mark.parametrize("condition", [Incapacitated, Stunned, Paralyzed, Petrified])
def test_an_incapacitated_creature_is_offered_nothing(
    condition: type[Effect],
) -> None:
    """XPHB Incapacitated: "You can't take any action, Bonus Action, or
    Reaction." — with an Action in hand and an enemy in reach. Unconscious is
    the next test's: it drops its weapon, which is then offered."""
    seated, fighter, _ = duel()
    fighter.effects.apply(condition(fighter))
    fighter.economy.turn_started(fighter.speed_ft)
    assert offers(seated, fighter) == []


@pytest.mark.parametrize("condition", [Unconscious, Paralyzed])
def test_an_incapacitated_creature_is_not_offered_its_dropped_weapon(
    condition: type[Effect],
) -> None:
    """An Unconscious (or Paralyzed) creature, above 0 hit points, whose weapon
    lies at its feet — which is where Unconscious puts it on its own, so every
    Unconscious creature that was holding one is offered the pick-up. Speed 0
    and no action: there is no move or action to make the object interaction
    during (XPHB "Interacting with Things")."""
    seated, fighter, _ = duel()
    fighter.drop_held()
    fighter.effects.apply(condition(fighter))
    fighter.economy.turn_started(fighter.speed_ft)
    assert fighter.weapon is None and fighter.hp == HP
    assert offers(seated, fighter) == []


# --- What the start of a turn hands back --------------------------------------


@pytest.mark.parametrize(
    ("levels", "movement"),
    [(0, 30), (1, 25), (2, 20), (6, 0)],
)
def test_the_turn_hands_back_the_speed_the_creature_has_now(
    levels: int, movement: int
) -> None:
    """Speed 30, less 5 ft per Exhaustion level, never below 0."""
    seated, fighter, _ = duel()
    for _ in range(levels):
        fighter.effects.apply(Exhaustion(fighter))
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert fighter.economy.movement_ft == movement


def test_a_grappled_creature_starts_its_turn_with_no_movement() -> None:
    seated, fighter, goblin = duel()
    fighter.effects.apply(Grappled(fighter, caster=goblin))
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert fighter.economy.movement_ft == 0
    assert (fighter.economy.actions, fighter.economy.bonus_actions) == (1, 1)


class Riposte(HookSource):
    """A reaction that always wants to be taken, so what stops it is the
    economy and nothing else."""

    __slots__ = ("swings",)

    ID = "test.riposte"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.swings = 0

    @hook("on_hit", costs=ActionCost.REACTION)
    def riposte(self, ctx: HitContext) -> R:
        self.swings += 1
        return R.APPLIED


def offer_a_reaction(defender: Creature) -> None:
    """Fire `on_hit` on `defender`'s dispatcher, as a hit against it would."""
    defender.dispatch(
        ON_HIT,
        HitContext(
            attacker=defender,
            defender=defender,
            weapon=None,
            outcome=Outcome.HIT,
            damage=DamageBag(),
        ),
    )


def test_a_reaction_spent_on_another_turn_is_gone_until_the_owners_turn() -> None:
    """The goblin's riposte, offered twice during the fighter's turn, runs
    once; it is still spent after that turn ends, and back once the goblin's
    own turn starts."""
    seated, fighter, goblin = duel()
    riposte = Riposte(goblin)
    goblin.attach(riposte)

    def hit_the_goblin_twice(
        actor: Creature, offered: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        offer_a_reaction(goblin)
        offer_a_reaction(goblin)
        return None

    take_turn(seated, fighter, 1, policy=Recorder(hit_the_goblin_twice), rng=no_dice())
    assert riposte.swings == 1
    assert goblin.economy.reactions == 0

    take_turn(seated, goblin, 1, policy=Recorder(), rng=no_dice())
    assert goblin.economy.reactions == 1
    offer_a_reaction(goblin)
    assert riposte.swings == 2


@pytest.mark.parametrize(
    ("scope", "cleared_by_other", "cleared_by_own"),
    [(Reset.TURN, True, True), (Reset.OWN_TURN, False, True)],
)
def test_once_per_budgets_clear_at_the_turns_they_name(
    scope: Reset, cleared_by_other: bool, cleared_by_own: bool
) -> None:
    """`Reset.TURN` comes back at any creature's turn; `Reset.OWN_TURN` only at
    its owner's."""
    seated, fighter, goblin = duel()
    budget = goblin.dispatcher.resets
    budget.spend("budget", scope)

    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert budget.is_spent("budget") is not cleared_by_other
    take_turn(seated, goblin, 1, policy=Recorder(), rng=no_dice())
    assert budget.is_spent("budget") is not cleared_by_own


def test_own_turn_budgets_are_already_back_when_on_turn_start_runs() -> None:
    seated, fighter, _ = duel()
    fighter.dispatcher.resets.spend("budget", Reset.OWN_TURN)
    seen: list[bool] = []

    class Peek(HookSource):
        __slots__ = ()
        ID = "test.peek"

        @hook("on_turn_start")
        def peek(self, ctx: TurnContext) -> R:
            seen.append(fighter.dispatcher.resets.is_spent("budget"))
            return R.APPLIED

    fighter.attach(Peek(fighter))
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert seen == [False]


# --- Incapacitation, through the loop and the dispatcher --------------------


def test_an_incapacitated_creature_takes_no_command_and_is_asked_with_nothing() -> None:
    """Stunned stops actions, not movement: with its Speed left the policy is
    asked once, with an empty offer list, and no Command is taken."""
    seated, fighter, _ = duel()
    fighter.effects.apply(Stunned(fighter))
    policy = Recorder(first_of("attack"))
    assert take_turn(seated, fighter, 1, policy=policy, rng=no_dice()) == 0
    assert policy.calls == [("fighter", [])]
    assert fighter.economy.movement_ft == 30


def test_with_nothing_offered_and_no_movement_left_the_policy_is_not_asked() -> None:
    """Paralyzed: Incapacitated and Speed 0, so nothing is offered and there is
    no movement to walk."""
    seated, fighter, _ = duel()
    fighter.effects.apply(Paralyzed(fighter))
    policy = Recorder()
    assert take_turn(seated, fighter, 1, policy=policy, rng=no_dice()) == 0
    assert fighter.economy.movement_ft == 0
    assert policy.calls == []


def test_a_stunned_creature_walks_its_speed_and_is_then_not_asked_again() -> None:
    """Stunned stops actions, not movement (XPHB Rules Glossary
    "Incapacitated"). The goblin is already in reach, so nothing is offered;
    asked with the empty list, the policy returns its own `Retreat` from it,
    which walks all six squares, and with no movement left the policy is not
    asked a second time. Of the squares six steps out, every one at x = -6 is
    35 ft from the goblin at (1, 0); the lowest `y` is taken
    (`docs/decisions.md` § w2e). Nothing here answers the goblin's
    Opportunity Attack, so no die is rolled."""
    seated, fighter, goblin = duel()
    fighter.effects.apply(Stunned(fighter))

    def chooser(actor: Creature, offered: Sequence[Action[Any]]) -> Action[Any]:
        return Retreat(actor, goblin, world=seated)

    policy = Recorder(chooser)

    assert take_turn(seated, fighter, 1, policy=policy, rng=no_dice()) == 1

    assert policy.calls == [("fighter", [])]
    assert fighter.position == Cell(-6, -6)
    assert fighter.economy.movement_ft == 0
    assert fighter.economy.actions == 1
    assert fighter.hp == HP


def test_an_incapacitated_creature_cannot_react_and_can_once_it_ends() -> None:
    """The reaction is in hand all along; the condition is what refuses it,
    through the dispatcher's costed-handler path. Removing it lets the very
    next offer run."""
    goblin = creature("goblin")
    riposte = Riposte(goblin)
    goblin.attach(riposte)
    stun = goblin.effects.apply(Stunned(goblin))
    assert stun is not None

    offer_a_reaction(goblin)
    assert riposte.swings == 0
    assert goblin.economy.reactions == 1
    assert not goblin.economy.can_spend(ActionCost.REACTION)

    goblin.effects.remove(stun)
    offer_a_reaction(goblin)
    assert riposte.swings == 1
    assert goblin.economy.reactions == 0


# --- The loop charges real costs and re-polls --------------------------------


class Focus(HookSource):
    """A Bonus Action self-buff taken at the top of every turn — the shape that
    starved the Action in `w0i` when the loop charged it an Action."""

    __slots__ = ("taken",)

    ID = "test.focus"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.taken = 0

    @hook("on_turn_start", costs=ActionCost.BONUS_ACTION)
    def focus(self, ctx: TurnContext) -> R:
        self.taken += 1
        return R.APPLIED


def test_a_bonus_action_spent_never_starves_the_action() -> None:
    """The Bonus Action buff is paid out of the Bonus Action; the Attack is
    still offered and taken. d20 15 + 5 = 20 against AC 12, a hit; 2d6 3 + 4
    + 3 = 10; 20 - 10 = 10. Then nothing is offered, and with its movement
    left the policy is asked once more, with an empty list."""
    seated, fighter, goblin = duel()
    focus = Focus(fighter)
    fighter.attach(focus)
    policy = Recorder(first_of("attack"))
    rng = ScriptedRng([15, 3, 4])

    assert take_turn(seated, fighter, 1, policy=policy, rng=rng) == 1

    assert focus.taken == 1
    assert policy.calls == [
        ("fighter", ["attack", "dodge", "disengage", "dash"]),
        ("fighter", []),
    ]
    assert goblin.hp == 10
    economy = fighter.economy
    assert (economy.actions, economy.bonus_actions, economy.reactions) == (0, 0, 1)
    assert rng.exhausted


def test_the_loop_re_polls_after_every_command() -> None:
    """The weapon lies at the fighter's feet: first the free pick-up is offered
    and no Attack, then — the weapon back in hand — the Attack, and after it
    nothing offered, which still asks the policy while movement is left.
    d20 15 + 5 = 20, a hit; 2d6 3 + 4 + 3 = 10."""
    seated, fighter, goblin = duel()
    fighter.drop_held()
    choices = iter(["pick_up_weapon", "attack"])

    def chooser(actor: Creature, offered: Sequence[Action[Any]]) -> Action[Any] | None:
        wanted = next(choices, None)
        return None if wanted is None else first_of(wanted)(actor, offered)

    policy = Recorder(chooser)
    rng = ScriptedRng([15, 3, 4])

    assert take_turn(seated, fighter, 1, policy=policy, rng=rng) == 2

    assert policy.calls == [
        ("fighter", ["dodge", "disengage", "dash", "pick_up_weapon"]),
        ("fighter", ["attack", "dodge", "disengage", "dash"]),
        ("fighter", []),
    ]
    assert fighter.weapon is GREATSWORD
    assert goblin.hp == 10
    assert rng.exhausted


def test_a_policy_choosing_something_it_was_not_offered_is_refused() -> None:
    """An equal Dodge built by the policy is not one of the offers, and it is
    not carried out."""
    seated, fighter, _ = duel()
    policy = Recorder(lambda actor, offered: Dodge(actor))
    with pytest.raises(EngineAssemblyError, match="not offered"):
        take_turn(seated, fighter, 1, policy=policy, rng=no_dice())
    assert fighter.economy.actions == 1
    assert list(fighter.effects) == []


def test_a_stale_offer_from_an_earlier_poll_is_refused() -> None:
    """The pick-up from the first poll, chosen again on the second."""
    seated, fighter, _ = duel()
    fighter.drop_held()
    kept: list[Action[Any]] = []

    def chooser(actor: Creature, offered: Sequence[Action[Any]]) -> Action[Any]:
        if not kept:
            kept.append(next(one for one in offered if one.ID == "pick_up_weapon"))
        return kept[0]

    with pytest.raises(EngineAssemblyError, match="not offered"):
        take_turn(seated, fighter, 1, policy=Recorder(chooser), rng=no_dice())


def test_a_turn_ends_when_the_policy_declines() -> None:
    seated, fighter, _ = duel()
    policy = Recorder()
    assert take_turn(seated, fighter, 1, policy=policy, rng=no_dice()) == 0
    assert policy.calls == [("fighter", ["attack", "dodge", "disengage", "dash"])]
    assert fighter.economy.actions == 1


# --- Repeat saves (`docs/decisions.md` § w2c) -----------------------------------------

DC: Final = 13
SAVED: Final = 11
"""11 + 2 (Wisdom 14) = 13, meeting DC 13."""
FAILED: Final = 10
"""10 + 2 = 12, one short."""


def stun_with_a_save(victim: Creature, when: SaveRepeat) -> Effect:
    worn = victim.effects.apply(
        Stunned(victim, repeat_save=RepeatSave(Ability.WISDOM, DC, when))
    )
    assert worn is not None
    return worn


@pytest.mark.parametrize(
    ("when", "natural", "acts", "ends"),
    [
        (SaveRepeat.TURN_START, SAVED, True, True),
        (SaveRepeat.TURN_START, FAILED, False, False),
        (SaveRepeat.TURN_END, SAVED, False, True),
        (SaveRepeat.TURN_END, FAILED, False, False),
    ],
)
def test_a_repeat_save_ends_the_effect_on_a_success_at_its_boundary(
    when: SaveRepeat, natural: int, acts: bool, ends: bool
) -> None:
    """A Stun with a DC 13 Wisdom save. Saved at the start of the turn, the
    creature is offered its Commands; saved at the end, it has spent the turn
    stunned and is asked only with an empty list (its movement is left). A
    failure keeps it either way. One d20 in every case."""
    log = EventLog(LogLevel.SUMMARY)
    seated, fighter, _ = duel(log=log)
    stun = stun_with_a_save(fighter, when)
    policy = Recorder()
    rng = ScriptedRng([natural])

    take_turn(seated, fighter, 1, policy=policy, rng=rng)

    offered = ["attack", "dodge", "disengage", "dash"] if acts else []
    assert policy.calls == [("fighter", offered)]
    assert (stun in list(fighter.effects)) is not ends
    outcome = SaveOutcome.SUCCESS if ends else SaveOutcome.FAILURE
    assert kinds(log, SAVE) == [
        (SAVE, "fighter", Ability.WISDOM, DC, natural + 2, outcome)
    ]
    assert rng.exhausted


def test_a_repeat_save_is_rolled_once_a_turn_and_only_at_its_own_boundary() -> None:
    """Two turns, two failures: two d20s and no more; the third face is left."""
    seated, fighter, _ = duel()
    stun_with_a_save(fighter, SaveRepeat.TURN_END)
    rng = ScriptedRng([FAILED, FAILED, SAVED])
    take_turn(seated, fighter, 1, policy=Recorder(), rng=rng)
    take_turn(seated, fighter, 2, policy=Recorder(), rng=rng)
    assert rng.remaining == 1
    assert fighter.incapacitated


def test_a_repeat_save_is_rolled_by_its_holder_and_on_nobody_elses_turn() -> None:
    seated, fighter, goblin = duel()
    stun_with_a_save(goblin, SaveRepeat.TURN_START)
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert goblin.incapacitated


def test_an_effect_without_a_repeat_save_rolls_nothing() -> None:
    seated, fighter, _ = duel()
    fighter.effects.apply(Stunned(fighter))
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert fighter.incapacitated


# --- Death saves --------------------------------------------------------------


def downed(seated_with: str = "heroes") -> tuple[World, Creature, EventLog]:
    """A player character dropped to 0 by exactly its 20 hit points: overflow
    0, so dying, not dead."""
    log = EventLog(LogLevel.SUMMARY)
    pc = creature("pc", pc=True)
    goblin = creature("goblin", cell=EAST, initiative=SECOND)
    seated = world((pc, seated_with), (goblin, "monsters"), log=log)
    resolve_damage(pc, {DamageType.SLASHING: HP}, rng=no_dice())
    assert pc.hp == 0 and not pc.dead and not pc.stable
    return seated, pc, log


def test_a_dying_character_rolls_a_death_save_on_its_turn() -> None:
    """d20 15 >= 10: one success. `(DEATH_SAVE, name, natural, successes,
    failures)`. It is Unconscious, so it is offered nothing."""
    seated, pc, log = downed()
    policy = Recorder()
    rng = ScriptedRng([15])
    assert take_turn(seated, pc, 1, policy=policy, rng=rng) == 0
    assert (pc.death_save_successes, pc.death_save_failures) == (1, 0)
    assert kinds(log, DEATH_SAVE) == [(DEATH_SAVE, "pc", 15, 1, 0)]
    assert policy.calls == []
    assert rng.exhausted


def test_death_saves_accumulate_across_its_turns_and_stop_once_stable() -> None:
    """15 (success), 4 (failure), 12 (success), 10 (success: third, Stable).
    A fifth turn rolls nothing."""
    seated, pc, log = downed()
    rng = ScriptedRng([15, 4, 12, 10])
    for round_number in range(1, 6):
        take_turn(seated, pc, round_number, policy=Recorder(), rng=rng)
    assert pc.stable and not pc.dead
    assert kinds(log, DEATH_SAVE) == [
        (DEATH_SAVE, "pc", 15, 1, 0),
        (DEATH_SAVE, "pc", 4, 1, 1),
        (DEATH_SAVE, "pc", 12, 2, 1),
        (DEATH_SAVE, "pc", 10, 3, 1),
    ]
    assert rng.exhausted


def test_a_character_with_hit_points_rolls_no_death_save() -> None:
    seated, fighter, _ = duel()
    take_turn(seated, fighter, 1, policy=Recorder(), rng=no_dice())
    assert fighter.death_save_successes == 0


# --- The turn's records --------------------------------------------------------


def test_a_turn_is_bracketed_by_its_start_and_end_records() -> None:
    """`(TURN_START, round, name)` and `(TURN_END, round, name)`, the Dodge
    applied between them."""
    log = EventLog(LogLevel.SUMMARY)
    seated, fighter, _ = duel(log=log)
    take_turn(seated, fighter, 3, policy=Recorder(first_of("dodge")), rng=no_dice())
    assert [record[0] for record in log] == [
        TURN_START,
        "effect_applied",
        TURN_END,
    ]
    assert log.records[0] == (TURN_START, 3, "fighter")
    assert log.records[-1] == (TURN_END, 3, "fighter")


def test_a_speed_zero_condition_saved_off_at_the_turns_start_leaves_full_speed() -> (
    None
):
    """Paralyzed with a DC 13 Wisdom save at the start of each turn: 11 + 2 =
    13, saved. The rest of the turn is the creature's, at Speed 30 (XPHB
    "Movement and Position": "you can move a distance up to your Speed")."""
    seated, fighter, _ = duel()
    fighter.effects.apply(
        Paralyzed(
            fighter,
            repeat_save=RepeatSave(Ability.WISDOM, DC, SaveRepeat.TURN_START),
        )
    )
    take_turn(seated, fighter, 1, policy=Recorder(), rng=ScriptedRng([SAVED]))
    assert not fighter.incapacitated
    assert fighter.speed_ft == 30
    assert fighter.economy.movement_ft == 30


# --- The order of the start's obligations (review findings) ----------------------


def test_a_natural_20_death_save_leaves_the_turn_its_full_speed() -> None:
    """A natural 20 on the death save: the pc regains 1 hit point and the
    Downed condition, which set its Speed to 0, ends. The save is rolled before
    the economy reads Speed, so the turn has all 30 feet and the policy is
    asked (XPHB "Death Saving Throws": "you regain 1 Hit Point")."""
    seated, pc, log = downed()
    policy = Recorder()
    rng = ScriptedRng([20])

    take_turn(seated, pc, 1, policy=policy, rng=rng)

    assert pc.hp == 1
    assert not pc.incapacitated
    assert pc.speed_ft == 30
    assert pc.economy.movement_ft == 30
    assert [actor for actor, _ in policy.calls] == ["pc"]
    assert kinds(log, DEATH_SAVE) == [(DEATH_SAVE, "pc", 20, 0, 0)]
    assert rng.exhausted


class Steady(HookSource):
    """+5 to one saving throw a turn — any creature's turn (`Reset.TURN`)."""

    __slots__ = ("fired",)

    ID = "test.steady"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.fired = 0

    @hook("on_saving_throw", once_per=Reset.TURN)
    def steady(self, ctx: SavingThrowContext) -> R:
        self.fired += 1
        ctx.bonus.add(flat=5)
        return R.APPLIED


def test_once_per_turn_budgets_are_back_before_the_starts_repeat_save() -> None:
    """The goblin spends its once-per-turn +5 on a save during the fighter's
    turn: 10 + 2 + 5 = 17. Its own turn begins with a DC 13 Wisdom repeat save
    against a Stun; the budget has come back with the turn, so 6 + 2 + 5 = 13,
    saved — without the +5 it would be 8, failed."""
    seated, fighter, goblin = duel()
    steady = Steady(goblin)
    goblin.attach(steady)
    stun_with_a_save(goblin, SaveRepeat.TURN_START)

    def goblin_saves(
        actor: Creature, offered: Sequence[Action[Any]]
    ) -> Action[Any] | None:
        result = saving_throw(
            goblin, ability=Ability.WISDOM, dc=DC, rng=ScriptedRng([10])
        )
        assert result.total == 17
        return None

    take_turn(seated, fighter, 1, policy=Recorder(goblin_saves), rng=no_dice())
    assert steady.fired == 1

    rng = ScriptedRng([6])
    take_turn(seated, goblin, 1, policy=Recorder(), rng=rng)

    assert steady.fired == 2
    assert not goblin.incapacitated
    assert rng.exhausted


# --- A death on the turn's own death save (hold findings, PR #62) -------------


class Boundaries(HookSource):
    """Counts the turn hooks that reach its owner."""

    __slots__ = ("seen",)
    ID = "test.boundaries"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_turn_start")
    def started(self, ctx: TurnContext) -> R:
        self.seen.append("start")
        return R.APPLIED

    @hook("on_turn_end")
    def ended(self, ctx: TurnContext) -> R:
        self.seen.append("end")
        return R.APPLIED


def test_a_character_that_dies_on_its_own_death_save_takes_nothing_after_it() -> None:
    """Two failures already; a natural 1 counts two more, a third: dead (XPHB
    "Death Saving Throws"). The turn then rolls no end-of-turn repeat save
    (no `SAVE` record, no second draw), fires neither turn hook and asks the
    policy nothing, but its boundaries are still written."""
    seated, pc, log = downed()
    pc.fail_death_save(2)
    pc.effects.apply(
        Stunned(pc, repeat_save=RepeatSave(Ability.WISDOM, DC, SaveRepeat.TURN_END))
    )
    boundaries = Boundaries(pc)
    pc.attach(boundaries)
    policy = Recorder()
    rng = ScriptedRng([1])

    assert take_turn(seated, pc, 1, policy=policy, rng=rng) == 0

    assert pc.dead
    assert rng.exhausted
    assert kinds(log, SAVE) == []
    assert boundaries.seen == []
    assert policy.calls == []
    assert kinds(log, TURN_START, TURN_END) == [
        (TURN_START, 1, "pc"),
        (TURN_END, 1, "pc"),
    ]


# --- One clock moves every seated store (hold findings, PR #62) ---------------


def test_a_seated_stores_own_sweep_sweeps_every_store_on_the_clock() -> None:
    """The goblin's Stun ends at the end of its next turn, round 1 at the
    latest. Sweeping the fighter's store through the end of round 3 moves the
    encounter's clock, so it sweeps the goblin's store too."""
    seated, fighter, goblin = duel()
    goblin.effects.apply(
        Stunned(goblin, duration=Duration.until_end_of_next_turn_of(SECOND))
    )
    later = TimeKey(3, FIRST, TurnPhase.END)

    fighter.effects.expire_after(later)

    assert seated.clock.now == later
    assert goblin.effects.now == later
    assert not goblin.incapacitated


def test_the_clocks_time_cannot_be_set_except_by_a_sweep() -> None:
    seated, _, _ = duel()
    with pytest.raises(AttributeError):
        seated.clock.now = TimeKey(3, FIRST, TurnPhase.END)  # type: ignore[misc]


# --- Heals and drops are written (hold findings, PR #62) -----------------------


def test_a_drop_to_zero_is_written_to_the_seated_creatures_log() -> None:
    """`(DROPPED, attacker, creature, dead)`: 20 damage on 20 hit points,
    overflow 0, so a dying pc, not a dead one."""
    _, _, log = downed()
    assert kinds(log, DROPPED) == [(DROPPED, None, "pc", False)]


def test_a_heal_is_written_to_the_seated_creatures_log() -> None:
    """`(HEAL, healer, creature, regained)`: 1d8 rolls 6 on a pc at 0."""
    seated, pc, log = downed()
    goblin = next(one for one in seated if one is not pc)
    assert heal(pc, rng=ScriptedRng([6]), dice="1d8", healer=goblin) == 6
    assert kinds(log, HEAL) == [(HEAL, "goblin", "pc", 6)]


# --- Move: closing on an enemy (ROADMAP w2c2) ----------------------------------


class Held(Effect):
    """Speed 0 and nothing else."""

    __slots__ = ()

    ID = "test.held"
    SPEED_ZERO = True


class Footsteps(HookSource):
    """Writes down every `on_move` its owner makes."""

    __slots__ = ("moves",)

    ID = "test.footsteps"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.moves: list[tuple[tuple[Cell, ...], int]] = []

    @hook("on_move")
    def note(self, ctx: MoveContext) -> R:
        self.moves.append((tuple(Cell(c.x, c.y) for c in ctx.route), ctx.distance_ft))
        return R.APPLIED


def closing(goblin_at: Cell) -> tuple[World, Creature, Creature]:
    """A fighter at the origin with its turn's 30 feet, and a goblin."""
    fighter = creature("fighter")
    goblin = creature("goblin", cell=goblin_at, initiative=SECOND)
    seated = world((fighter, "heroes"), (goblin, "monsters"))
    fighter.economy.turn_started(fighter.speed_ft)
    return seated, fighter, goblin


DIAGONAL: Final = (Cell(1, 1), Cell(2, 2), Cell(3, 3))
"""Three diagonal steps from the origin, five feet each."""


def reaches(actor: Creature, target: Creature) -> bool:
    return in_reach(
        actor.position,
        target.position,
        actor.reach_ft,
        origin_size=actor.size,
        destination_size=target.size,
    )


def test_move_is_a_free_command_named_move() -> None:
    assert Move.ID == "move"
    assert Move.COST is None


def test_a_move_closes_to_reach_and_stops_there() -> None:
    """Goblin at (4, 4): the one nearest square in reach is (3, 3), three
    diagonal steps away; 30 - 15 = 15 feet are left."""
    seated, fighter, goblin = closing(Cell(4, 4))

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == DIAGONAL
    assert fighter.position == Cell(3, 3)
    assert fighter.economy.movement_ft == 15
    assert fighter.economy.actions == 1, "a move spent the Action"
    assert reaches(fighter, goblin)
    assert not Move(fighter, goblin, world=seated).is_legal()


def test_a_move_is_cut_short_by_the_movement_left() -> None:
    """Goblin at (10, 10): six diagonal steps of 30 feet reach (6, 6), out of
    reach, and with nothing left to walk another Move is not legal."""
    seated, fighter, goblin = closing(Cell(10, 10))

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert len(walked) == 6
    assert fighter.position == Cell(6, 6)
    assert fighter.economy.movement_ft == 0
    assert not reaches(fighter, goblin)
    assert not Move(fighter, goblin, world=seated).is_legal()


def test_a_move_walks_what_is_left_after_an_earlier_one() -> None:
    """20 feet spent already: four steps, to (4, 0)."""
    seated, fighter, goblin = closing(Cell(10, 10))
    fighter.economy.move(10)

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == (Cell(1, 1), Cell(2, 2), Cell(3, 3), Cell(4, 4))
    assert fighter.economy.movement_ft == 0


def test_a_move_ends_on_a_free_square_when_the_nearest_is_taken() -> None:
    """The fighter's ally stands on (3, 3), the one square in reach of the
    goblin at (4, 4) that is three steps away. The next nearest, (3, 4) and
    (4, 3), are four: the move takes four steps, never enters the ally's
    square, and ends in reach of the goblin."""
    seated, fighter, goblin = closing(Cell(4, 4))
    ally = creature("ally", cell=Cell(3, 3))
    seated.seat(ally, "heroes")

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert len(walked) == 4
    assert Cell(3, 3) not in walked
    assert fighter.position in (Cell(3, 4), Cell(4, 3))
    assert reaches(fighter, goblin)


def test_a_target_with_no_free_square_around_it_is_not_moved_toward() -> None:
    """Every square around the goblin at (5, 0) is filled by its friends."""
    seated, fighter, goblin = closing(Cell(5, 0))
    ring = [
        Cell(5 + dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
    ]
    for index, cell in enumerate(ring):
        seated.seat(creature(f"wall{index}", cell=cell), "monsters")

    assert not Move(fighter, goblin, world=seated).is_legal()


@pytest.mark.parametrize(
    "spoil",
    ["in-reach", "dead-target", "speed-zero", "no-movement", "itself", "dead-actor"],
)
def test_a_move_that_cannot_go_anywhere_useful_is_not_legal(spoil: str) -> None:
    seated, fighter, goblin = closing(Cell(4, 0))
    target = goblin
    if spoil == "in-reach":
        goblin.position = EAST
    elif spoil == "dead-target":
        goblin.die()
    elif spoil == "speed-zero":
        fighter.effects.apply(Held(fighter))
    elif spoil == "no-movement":
        fighter.economy.move(30)
    elif spoil == "itself":
        target = fighter
    else:
        fighter.die()

    assert not Move(fighter, target, world=seated).is_legal()


def test_a_move_with_less_than_a_square_left_is_not_legal() -> None:
    seated, fighter, goblin = closing(Cell(4, 0))
    fighter.economy.move(26)

    assert not Move(fighter, goblin, world=seated).is_legal()


def test_an_incapacitated_creature_can_still_move() -> None:
    """Incapacitated takes Actions, Bonus Actions and Reactions, not
    movement: the walk is offered and taken."""
    seated, fighter, goblin = closing(Cell(4, 4))
    fighter.effects.apply(Incapacitated(fighter))

    offered = offers(seated, fighter)

    assert ids(offered) == ["move"]
    offered[0].execute(rng=no_dice())
    assert fighter.position == Cell(3, 3)


def test_a_move_dispatches_on_move_with_the_squares_walked() -> None:
    seated, fighter, goblin = closing(Cell(4, 4))
    steps = Footsteps(fighter)
    fighter.attach(steps)

    Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert steps.moves == [(DIAGONAL, 15)]


def test_dash_then_move_walks_twice_the_speed() -> None:
    """30 + 30 = 60 feet: twelve diagonal steps put the fighter on (12, 12), in
    reach of the goblin at (13, 13)."""
    seated, fighter, goblin = closing(Cell(13, 13))
    Dash(fighter).execute(rng=no_dice())

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert len(walked) == 12
    assert fighter.position == Cell(12, 12)
    assert fighter.economy.movement_ft == 0
    assert reaches(fighter, goblin)


def test_offers_end_with_a_move_toward_each_enemy_out_of_reach() -> None:
    """Seated orc (in reach), far, ally, near-but-out: the attack on the orc,
    the three of the six that are legal, then a move toward each enemy out of
    reach in seating order."""
    fighter = creature("fighter")
    orc = creature("orc", cell=EAST)
    far = creature("far", cell=Cell(9, 9))
    ally = creature("ally", cell=Cell(0, 1))
    near = creature("near", cell=Cell(-3, 0))
    seated = world(
        (orc, "monsters"),
        (fighter, "heroes"),
        (far, "monsters"),
        (ally, "heroes"),
        (near, "monsters"),
    )
    fighter.economy.turn_started(fighter.speed_ft)

    offered = offers(seated, fighter)

    assert ids(offered) == ["attack", "dodge", "disengage", "dash", "move", "move"]
    assert [getattr(one, "target", None) for one in offered[-2:]] == [far, near]


def test_a_turn_can_close_and_then_swing() -> None:
    """The loop re-polls after the move: the goblin at (4, 4) comes into reach
    and the Attack is offered. A natural 15 hits AC 12 for 4 + 4 + 3 = 11."""
    seated, fighter, goblin = closing(Cell(4, 4))
    order = iter(("move", "attack"))

    def chooser(actor: Creature, offered: Sequence[Action[Any]]) -> Action[Any] | None:
        wanted = next(order, None)
        return next((one for one in offered if wanted == one.ID), None)

    rng = ScriptedRng([15, 4, 4])
    take_turn(seated, fighter, 1, policy=Recorder(chooser), rng=rng)

    assert fighter.position == Cell(3, 3)
    assert goblin.hp == HP - 11
    assert rng.exhausted


# --- Move while crawling or afraid (XPHB Rules Glossary: Prone, Frightened) ----


def test_a_prone_creature_crawls_at_ten_feet_a_square() -> None:
    """ "Your only movement options are to crawl" — each foot costs one extra:
    30 feet buys three squares, (1, 1) to (3, 3), and leaves nothing."""
    seated, fighter, goblin = closing(Cell(10, 10))
    fighter.effects.apply(Prone(fighter))

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == DIAGONAL
    assert fighter.economy.movement_ft == 0


def test_a_prone_creature_with_ten_feet_left_crawls_one_square() -> None:
    seated, fighter, goblin = closing(Cell(10, 10))
    fighter.effects.apply(Prone(fighter))
    fighter.economy.move(20)

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == (Cell(1, 1),)
    assert fighter.economy.movement_ft == 0


def test_a_prone_creature_with_five_feet_left_is_offered_no_move() -> None:
    """Five feet is half a crawled square."""
    seated, fighter, goblin = closing(Cell(10, 10))
    fighter.effects.apply(Prone(fighter))
    fighter.economy.move(25)

    assert not Move(fighter, goblin, world=seated).is_legal()
    assert "move" not in ids(offers(seated, fighter))


def test_a_frightened_creature_is_offered_no_move_toward_its_source() -> None:
    """ "You can't willingly move closer to the source of fear." Every step
    toward the goblin at (4, 4) is a step closer to it."""
    seated, fighter, goblin = closing(Cell(4, 4))
    fighter.effects.apply(Frightened(fighter, caster=goblin))

    assert not Move(fighter, goblin, world=seated).is_legal()
    assert "move" not in ids(offers(seated, fighter))


def test_once_the_fear_ends_the_move_is_offered_again() -> None:
    seated, fighter, goblin = closing(Cell(4, 4))
    fear = Frightened(fighter, caster=goblin)
    fighter.effects.apply(fear)
    assert "move" not in ids(offers(seated, fighter))

    fighter.effects.remove(fear)

    offered = [one for one in offers(seated, fighter) if one.ID == "move"]
    assert [getattr(one, "target", None) for one in offered] == [goblin]
    assert offered[0].execute(rng=no_dice()) == DIAGONAL


def test_a_frightened_creature_walks_freely_away_from_its_source() -> None:
    """The source stands at (-5, 0); the walk to (3, 3) puts it 30, 35 and 40
    feet away, never closer."""
    seated, fighter, goblin = closing(Cell(4, 4))
    dread = creature("dread", cell=Cell(-5, 0), initiative=SECOND)
    seated.seat(dread, "monsters")
    fighter.effects.apply(Frightened(fighter, caster=dread))

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == DIAGONAL
    assert fighter.economy.movement_ft == 15


def _cornered() -> tuple[World, Creature, Creature, Creature]:
    """The fighter at the origin, the goblin at (6, 0), and a wall of the
    fighter's friends on (2, -1) to (2, 5) and on (3, -2) and (3, -3). The only
    shortest way round is (1, -1), (2, -2), (3, -1), then two more squares to
    stand beside the goblin. The source of fear `dread` waits at (4, 4)."""
    seated, fighter, goblin = closing(Cell(6, 0))
    wall = [Cell(2, y) for y in range(-1, 6)] + [Cell(3, -2), Cell(3, -3)]
    for index, cell in enumerate(wall):
        seated.seat(creature(f"wall{index}", cell=cell), "heroes")
    dread = creature("dread", cell=Cell(4, 4), initiative=SECOND)
    seated.seat(dread, "monsters")
    return seated, fighter, goblin, dread


def test_the_corner_is_walked_whole_without_fear() -> None:
    """The control for the next test: the route it truncates."""
    seated, fighter, goblin, _ = _cornered()

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked[:3] == (Cell(1, -1), Cell(2, -2), Cell(3, -1))
    assert len(walked) == 5
    assert reaches(fighter, goblin)


def test_a_frightened_creature_stops_before_the_first_step_toward_its_source() -> None:
    """From the origin the dread at (4, 4) is 20 feet away; (1, -1) is 25 and
    (2, -2) 30, but (3, -1) would be 25, closer than the square it leaves. The
    walk stops on (2, -2), having paid for two squares."""
    seated, fighter, goblin, dread = _cornered()
    fighter.effects.apply(Frightened(fighter, caster=dread))

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert walked == (Cell(1, -1), Cell(2, -2))
    assert fighter.position == Cell(2, -2)
    assert fighter.economy.movement_ft == 20
    assert not Move(fighter, goblin, world=seated).is_legal()


def test_fear_of_a_creature_outside_the_encounter_does_not_hold_a_move_back() -> None:
    """Only a seated source is asked where it stands."""
    seated, fighter, goblin = closing(Cell(4, 4))
    absent = creature("absent", cell=Cell(5, 5))
    fighter.effects.apply(Frightened(fighter, caster=absent))

    assert Move(fighter, goblin, world=seated).execute(rng=no_dice()) == DIAGONAL


# --- Move takes the shortest route, not the first square (review round 3) -----


def test_a_move_takes_the_shortest_route_among_the_squares_in_reach() -> None:
    """Goblin at (5, 0); creatures on (3, -2), (3, -1) and (3, 0). The three
    stands (4, -1), (4, 0) and (4, 1) are all 20 feet away, but (4, -1) is
    five steps round the wall and the other two four. Four steps are walked
    and 30 - 20 = 10 feet are left."""
    seated, fighter, goblin = closing(Cell(5, 0))
    for index, cell in enumerate((Cell(3, -2), Cell(3, -1), Cell(3, 0))):
        seated.seat(creature(f"post{index}", cell=cell), "monsters")

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert len(walked) == 4
    assert fighter.position in (Cell(4, 0), Cell(4, 1))
    assert fighter.economy.movement_ft == 10
    assert reaches(fighter, goblin)


def test_a_square_earlier_in_the_tie_order_loses_to_a_shorter_route() -> None:
    """Goblin at (4, 0): the stands (3, -1), (3, 0) and (3, 1) are 15 feet away.
    With (2, -2), (2, -1) and (2, 0) taken, (3, -1) — lowest y — needs four
    steps, while (3, 0) is three, by (2, 1). Shortest route first, then lowest
    y: the walk ends on (3, 0) with 15 feet left."""
    seated, fighter, goblin = closing(Cell(4, 0))
    for index, cell in enumerate((Cell(2, -2), Cell(2, -1), Cell(2, 0))):
        seated.seat(creature(f"post{index}", cell=cell), "heroes")

    walked = Move(fighter, goblin, world=seated).execute(rng=no_dice())

    assert len(walked) == 3
    assert walked[-1] == fighter.position == Cell(3, 0)
    assert fighter.economy.movement_ft == 15
