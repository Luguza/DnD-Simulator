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
ROADMAP `w2c`; debts #35, #41, #47, #50.
"""

from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim.actions.base import Action
from dndsim.actions.dodge import Dodge
from dndsim.conditions.exhaustion import Exhaustion
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.paralyzed import Paralyzed
from dndsim.conditions.petrified import Petrified
from dndsim.conditions.stunned import Stunned
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell
from dndsim.core.log import EventLog, LogLevel
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import InitiativeKey, Reset
from dndsim.creatures.creature import UNSEATED, Creature
from dndsim.creatures.effects import Effect, RepeatSave
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.engine.damage import resolve_damage
from dndsim.engine.saves import SAVE
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
    assert ids(offers(seated, fighter)) == ["dodge", "disengage", "dash"]


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


def test_an_incapacitated_creature_takes_no_command_and_the_policy_is_not_asked() -> (
    None
):
    seated, fighter, _ = duel()
    fighter.effects.apply(Stunned(fighter))
    policy = Recorder(first_of("attack"))
    assert take_turn(seated, fighter, 1, policy=policy, rng=no_dice()) == 0
    assert policy.calls == []


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
    + 3 = 10; 20 - 10 = 10. Then nothing costing an Action is offered again,
    so the policy is asked once."""
    seated, fighter, goblin = duel()
    focus = Focus(fighter)
    fighter.attach(focus)
    policy = Recorder(first_of("attack"))
    rng = ScriptedRng([15, 3, 4])

    assert take_turn(seated, fighter, 1, policy=policy, rng=rng) == 1

    assert focus.taken == 1
    assert policy.calls == [("fighter", ["attack", "dodge", "disengage", "dash"])]
    assert goblin.hp == 10
    economy = fighter.economy
    assert (economy.actions, economy.bonus_actions, economy.reactions) == (0, 0, 1)
    assert rng.exhausted


def test_the_loop_re_polls_after_every_command() -> None:
    """The weapon lies at the fighter's feet: first the free pick-up is offered
    and no Attack, then — the weapon back in hand — the Attack, and after it
    nothing. d20 15 + 5 = 20, a hit; 2d6 3 + 4 + 3 = 10."""
    seated, fighter, goblin = duel()
    fighter.drop_held()
    choices = iter(["pick_up_weapon", "attack"])
    policy = Recorder(lambda actor, offered: first_of(next(choices))(actor, offered))
    rng = ScriptedRng([15, 3, 4])

    assert take_turn(seated, fighter, 1, policy=policy, rng=rng) == 2

    assert policy.calls == [
        ("fighter", ["dodge", "disengage", "dash", "pick_up_weapon"]),
        ("fighter", ["attack", "dodge", "disengage", "dash"]),
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


# --- Repeat saves (debt #35) --------------------------------------------------

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
    creature acts on it; saved at the end, it has spent the turn stunned. A
    failure keeps it either way. One d20 in every case."""
    log = EventLog(LogLevel.SUMMARY)
    seated, fighter, _ = duel(log=log)
    stun = stun_with_a_save(fighter, when)
    policy = Recorder()
    rng = ScriptedRng([natural])

    take_turn(seated, fighter, 1, policy=policy, rng=rng)

    assert (policy.calls != []) is acts
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
