"""The action contract: what each Command asks before it acts, and what it does.

Every creature here is built from one helper: level 1 (proficiency +2),
Strength 16 (+3), Wisdom 14 (+2), every other score 10 (+0), 20 hit points and
no armour, so AC 10 + 0 = 10. A greatsword attack swings at natural + 3 + 2 =
natural + 5 against AC 10 and deals 2d6 + 3; a Dexterity save adds +0 and a
Strength save +3. A creature acts only once `ready()` has started its turn: a
fresh economy holds no Action.

Sources: XPHB (2024) "Actions" -> Attack, Dash, Disengage, Dodge; "Combat" ->
Your Turn, Interacting with Things; "Dropping to 0 Hit Points" -> Stabilizing a
Character; ROADMAP w2b; `docs/decisions.md` § The `w2b` spec correction.
"""

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterator
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim import actions
from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.actions.dash import Dash
from dndsim.actions.disengage import Disengage, disengaged
from dndsim.actions.dodge import Dodge, Dodging
from dndsim.actions.pick_up import PickUpWeapon
from dndsim.actions.stabilize import Stabilize
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.paralyzed import Paralyzed
from dndsim.conditions.petrified import Petrified
from dndsim.conditions.stunned import Stunned
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.creatures.economy import ActionEconomy
from dndsim.creatures.effects import Effect, EffectStore
from dndsim.creatures.resources import Recovery, Resources, ResourceSpec
from dndsim.creatures.stats import GREATSWORD, StatBlock, Weapon
from dndsim.engine import attack, damage, dying, healing, saves
from dndsim.engine.damage import resolve_damage
from dndsim.engine.dying import MEDICINE, STABILIZE_DC
from dndsim.engine.saves import saving_throw
from dndsim.hooks.contexts import AbilityCheckContext, StabilizeContext
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.dispatch import Dispatcher
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import (
    Ability,
    ActionCost,
    DamageType,
    Outcome,
    RecoveryTrigger,
    SaveOutcome,
    WeaponProperty,
)

SCORES: Final = MappingProxyType(
    {
        **dict.fromkeys(Ability, 10),
        Ability.STRENGTH: 16,  # +3
        Ability.WISDOM: 14,  # +2
    }
)
HP: Final = 20
SLASHING: Final = DamageType.SLASHING

PIKE: Final = Weapon(
    name="Pike",
    damage=Dice(1, 10),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.HEAVY, WeaponProperty.REACH}),
    reach_ft=10,
)
"""A 10-foot reach and a different die from the greatsword's."""

POOL: Final = ResourceSpec(
    key="test.pool",
    label="Pool",
    maximum=2,
    recovery=(Recovery(RecoveryTrigger.LONG_REST),),
)
"""A resource every creature here holds, so a spend from it would show."""

ORIGIN: Final = Cell(0, 0)
EAST: Final = Cell(1, 0)
"""Five feet from `ORIGIN`."""
NORTHEAST: Final = Cell(1, 1)
"""Five feet from `ORIGIN`: a diagonal step costs five feet."""
TWO_EAST: Final = Cell(2, 0)
"""Ten feet from `ORIGIN`: out of a 5-foot reach, inside a 10-foot one."""

KEY: Final = InitiativeKey(
    initiative=15, dexterity=10, player_character=True, tiebreak=0.5
)


def fighter(
    name: str = "fighter",
    *,
    cell: Cell = ORIGIN,
    weapon: Weapon | None = GREATSWORD,
    speed: int = 30,
    pc: bool = False,
) -> Creature:
    subject = Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=HP, speed_ft=speed),
        position=cell,
        weapon=weapon,
        initiative=KEY,
        player_character=pc,
    )
    subject.resources.add(POOL)
    return subject


def ready(subject: Creature) -> Creature:
    """Start `subject`'s turn: one Action, one Bonus Action, its Speed in
    feet."""
    subject.economy.turn_started(subject.speed_ft)
    return subject


def goblin(cell: Cell = EAST) -> Creature:
    return fighter("goblin", cell=cell, weapon=None)


def downed(name: str = "pc", *, cell: Cell = EAST) -> Creature:
    """A player character dropped to 0 by 20 damage on 20 hit points: overflow
    0, short of the maximum, so Unconscious and making death saves."""
    subject = fighter(name, cell=cell, weapon=None, pc=True)
    resolve_damage(subject, {SLASHING: HP}, rng=no_dice())
    assert subject.hp == 0 and not subject.dead and not subject.stable
    return subject


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled."""
    return ScriptedRng([])


def at(round_: int, phase: TurnPhase) -> TimeKey:
    return TimeKey(round_, KEY, phase)


def ledger(subject: Creature) -> tuple[int, int, int, int]:
    economy = subject.economy
    return (
        economy.actions,
        economy.bonus_actions,
        economy.reactions,
        economy.movement_ft,
    )


# --- The base: what every action asks ----------------------------------------


def test_a_fresh_creature_has_no_action_to_take_one_with() -> None:
    """A fresh economy holds 0 Actions until its turn starts."""
    actor = fighter()
    assert not Dodge(actor).is_legal()
    ready(actor)
    assert Dodge(actor).is_legal()


def test_execute_spends_exactly_one_action() -> None:
    """1 Action, 1 Bonus Action, 1 Reaction, 30 ft; a Dodge leaves 0, 1, 1,
    30."""
    actor = ready(fighter())
    Dodge(actor).execute(rng=no_dice())
    assert ledger(actor) == (0, 1, 1, 30)


def test_a_second_action_in_one_turn_is_refused() -> None:
    actor = ready(fighter())
    Dodge(actor).execute(rng=no_dice())
    assert not Disengage(actor).is_legal()
    with pytest.raises(ValueError):
        Disengage(actor).execute(rng=no_dice())
    assert ledger(actor) == (0, 1, 1, 30)
    assert not disengaged(actor)


def test_an_illegal_execute_raises_and_spends_nothing() -> None:
    """Out of reach with an Action in hand: refused before the Action is
    spent, and nothing is rolled."""
    actor, target = ready(fighter()), goblin(TWO_EAST)
    with pytest.raises(ValueError):
        Attack(actor, target).execute(rng=no_dice())
    assert ledger(actor) == (1, 1, 1, 30)
    assert target.hp == HP


@pytest.mark.parametrize("state", ["dead", "down"])
def test_a_dead_or_downed_actor_can_take_no_action(state: str) -> None:
    """A creature marked dead with its hit points untouched, and a player
    character at 0 hit points who is still alive — each with an Action in
    hand."""
    if state == "dead":
        actor = ready(fighter())
        actor.die()
        assert actor.hp == HP
    else:
        actor = ready(fighter(pc=True))
        resolve_damage(actor, {SLASHING: HP}, rng=no_dice())
        assert actor.is_alive and actor.hp == 0
    assert actor.economy.actions == 1
    assert not Dodge(actor).is_legal()
    assert not Dash(actor).is_legal()
    assert not Attack(actor, goblin(), weapon=GREATSWORD).is_legal()


def test_the_costs_are_the_xphbs() -> None:
    """Attack, Dash, Disengage, Dodge and the Help-shaped stabilise are each
    "Take an action"; picking up a dropped weapon is the turn's free object
    interaction."""
    for taken in (Attack, Dash, Dodge, Disengage, Stabilize):
        assert taken.COST is ActionCost.ACTION
    assert PickUpWeapon.COST is None


# --- Attack ------------------------------------------------------------------


def test_an_attack_that_hits_deals_the_weapons_damage() -> None:
    """Natural 10: 10 + 5 = 15 against AC 10, a hit. 2d6 rolled 2 and 5, + 3
    Strength = 10. 20 - 10 = 10 hit points left."""
    actor, target = ready(fighter()), goblin()
    rng = ScriptedRng([10, 2, 5])
    outcome, dealt = Attack(actor, target).execute(rng=rng)
    assert outcome is Outcome.HIT
    assert dealt == 10
    assert target.hp == 10
    assert rng.exhausted
    assert actor.economy.actions == 0


def test_an_attack_that_misses_deals_nothing() -> None:
    """Natural 4: 4 + 5 = 9 against AC 10. No damage dice are rolled."""
    actor, target = ready(fighter()), goblin()
    rng = ScriptedRng([4])
    outcome, dealt = Attack(actor, target).execute(rng=rng)
    assert outcome is Outcome.MISS
    assert dealt == 0
    assert target.hp == HP
    assert rng.exhausted


def test_an_attack_is_one_attack() -> None:
    """One d20 and one damage roll: a script holding a second attack's dice
    is left with them."""
    actor, target = ready(fighter()), goblin()
    rng = ScriptedRng([10, 2, 5, 10, 2, 5])
    Attack(actor, target).execute(rng=rng)
    assert not rng.exhausted
    assert target.hp == HP - 10


def test_a_named_weapon_is_swung_instead_of_the_wielded_one() -> None:
    """The pike at 10 feet: natural 10, 15 against AC 10, a hit. 1d10 rolled
    6, + 3 = 9; 20 - 9 = 11. The greatsword could not reach and rolls 2d6."""
    actor, target = ready(fighter()), goblin(TWO_EAST)
    assert not Attack(actor, target).is_legal()
    rng = ScriptedRng([10, 6])
    outcome, dealt = Attack(actor, target, weapon=PIKE).execute(rng=rng)
    assert (outcome, dealt) == (Outcome.HIT, 9)
    assert target.hp == 11
    assert rng.exhausted


def test_an_attack_needs_a_weapon() -> None:
    actor, target = ready(fighter(weapon=None)), goblin()
    assert not Attack(actor, target).is_legal()
    assert Attack(actor, target, weapon=GREATSWORD).is_legal()


def test_an_attack_needs_the_target_in_reach() -> None:
    """Greatsword reach 5: 5 feet away is in, 10 feet is out."""
    actor = ready(fighter())
    assert Attack(actor, goblin(EAST)).is_legal()
    assert Attack(actor, goblin(NORTHEAST)).is_legal()
    assert not Attack(actor, goblin(TWO_EAST)).is_legal()


def test_a_creature_does_not_attack_itself() -> None:
    actor = ready(fighter())
    assert not Attack(actor, actor).is_legal()


def test_a_dead_target_is_not_attacked_and_a_dying_one_is() -> None:
    actor = ready(fighter())
    corpse = goblin()
    corpse.die()
    assert not Attack(actor, corpse).is_legal()
    assert Attack(actor, downed()).is_legal()


# --- Dash --------------------------------------------------------------------


def test_dash_adds_the_creatures_speed_to_what_it_has_left() -> None:
    """Speed 25, 10 feet already moved: 25 - 10 = 15 left, + 25 = 40."""
    actor = ready(fighter(speed=25))
    actor.economy.move(10)
    Dash(actor).execute(rng=no_dice())
    assert actor.economy.movement_ft == 40
    assert actor.economy.actions == 0


def test_dash_is_not_offered_at_speed_zero() -> None:
    actor = ready(fighter(speed=0))
    assert not Dash(actor).is_legal()
    with pytest.raises(ValueError):
        Dash(actor).execute(rng=no_dice())
    assert ledger(actor) == (1, 1, 1, 0)


# --- Dodge -------------------------------------------------------------------


def dodger(**kwargs: Any) -> Creature:
    actor = ready(fighter("dodger", **kwargs))
    Dodge(actor).execute(rng=no_dice())
    return actor


def test_an_attack_against_a_dodger_has_disadvantage() -> None:
    """d20s 17 and 3, kept 3: 3 + 5 = 8 against AC 10, a miss. A single 17
    would have hit."""
    target = dodger()
    swinger = fighter("swinger", cell=EAST)
    rng = ScriptedRng([17, 3])
    outcome, dealt = attack.resolve_attack(swinger, target, rng=rng)
    assert (outcome, dealt) == (Outcome.MISS, 0)
    assert rng.exhausted


def test_a_dodger_makes_dexterity_saves_with_advantage() -> None:
    """d20s 3 and 15, kept 15: 15 + 0 = 15 against DC 10."""
    actor = dodger()
    rng = ScriptedRng([3, 15])
    result = saving_throw(actor, ability=Ability.DEXTERITY, dc=10, rng=rng)
    assert (result.outcome, result.total) == (SaveOutcome.SUCCESS, 15)
    assert rng.exhausted


def test_a_dodgers_other_saves_are_unchanged() -> None:
    """One d20, 3: 3 + 3 (Strength 16) = 6 against DC 10."""
    actor = dodger()
    rng = ScriptedRng([3])
    result = saving_throw(actor, ability=Ability.STRENGTH, dc=10, rng=rng)
    assert (result.outcome, result.total) == (SaveOutcome.FAILURE, 6)
    assert rng.exhausted


@pytest.mark.parametrize(
    "condition", [Incapacitated, Stunned, Paralyzed, Petrified, Unconscious]
)
def test_an_incapacitated_dodger_loses_the_save_advantage(
    condition: type[Effect],
) -> None:
    """Incapacitated and the four conditions that include it. One d20, 3: 3 +
    0 = 3 against DC 10."""
    actor = dodger()
    actor.effects.apply(condition(actor))
    rng = ScriptedRng([3])
    result = saving_throw(actor, ability=Ability.DEXTERITY, dc=10, rng=rng)
    assert (result.outcome, result.total) == (SaveOutcome.FAILURE, 3)
    assert rng.exhausted


def test_an_incapacitated_dodger_is_attacked_without_disadvantage() -> None:
    """Incapacitated changes nothing about an attack against its holder, so the
    lapsed Dodge leaves one d20: 17 + 5 = 22, a hit. 2d6 rolled 1 and 1, + 3 =
    5; 20 - 5 = 15."""
    target = dodger()
    target.effects.apply(Incapacitated(target))
    swinger = fighter("swinger", cell=EAST)
    rng = ScriptedRng([17, 1, 1])
    outcome, dealt = attack.resolve_attack(swinger, target, rng=rng)
    assert (outcome, dealt) == (Outcome.HIT, 5)
    assert target.hp == 15
    assert rng.exhausted


def test_a_dodger_at_speed_zero_loses_both_benefits() -> None:
    """Speed 0. The save: one d20, 3 + 0 = 3. The attack: one d20, 17 + 5 =
    22, a hit; 2d6 rolled 1 and 1, + 3 = 5."""
    actor = dodger(speed=0)
    rng = ScriptedRng([3])
    result = saving_throw(actor, ability=Ability.DEXTERITY, dc=10, rng=rng)
    assert (result.outcome, result.total) == (SaveOutcome.FAILURE, 3)
    assert rng.exhausted

    swinger = fighter("swinger", cell=EAST)
    rng = ScriptedRng([17, 1, 1])
    assert attack.resolve_attack(swinger, actor, rng=rng) == (Outcome.HIT, 5)
    assert rng.exhausted


def test_dodge_lasts_until_the_start_of_the_actors_next_turn() -> None:
    """Taken on round 1: held through that turn's end, gone before round 2's
    start handlers run."""
    actor = fighter()
    actor.effects.expire_before(at(1, TurnPhase.START))
    ready(actor)
    Dodge(actor).execute(rng=no_dice())

    actor.effects.expire_after(at(1, TurnPhase.END))
    assert [type(effect) for effect in actor.effects] == [Dodging]

    actor.effects.expire_before(at(2, TurnPhase.START))
    assert list(actor.effects) == []


# --- Disengage ---------------------------------------------------------------


def test_disengage_lasts_until_the_end_of_the_current_turn() -> None:
    actor = fighter()
    actor.effects.expire_before(at(1, TurnPhase.START))
    ready(actor)
    assert not disengaged(actor)

    Disengage(actor).execute(rng=no_dice())
    assert disengaged(actor)
    assert actor.economy.actions == 0

    actor.effects.expire_before(at(1, TurnPhase.END))
    assert disengaged(actor)
    actor.effects.expire_after(at(1, TurnPhase.END))
    assert not disengaged(actor)


def test_disengaged_is_about_disengage_and_no_other_effect() -> None:
    actor = dodger()
    assert not disengaged(actor)


# --- Stabilize ---------------------------------------------------------------


def test_a_medicine_check_that_makes_dc_ten_stabilises() -> None:
    """8 + 2 (Wisdom 14) = 10 against DC 10."""
    helper, patient = ready(fighter("cleric")), downed()
    assert Stabilize(helper, patient).execute(rng=ScriptedRng([8])) is True
    assert patient.stable
    assert patient.hp == 0
    assert helper.economy.actions == 0


def test_a_medicine_check_one_short_spends_the_action_and_fails() -> None:
    """7 + 2 = 9 against DC 10."""
    helper, patient = ready(fighter("cleric")), downed()
    assert Stabilize(helper, patient).execute(rng=ScriptedRng([7])) is False
    assert not patient.stable
    assert helper.economy.actions == 0


def test_stabilising_fires_the_check_on_the_helper_and_the_hook_on_the_patient() -> (
    None
):
    """Wisdom (Medicine), DC 10, on the helper; `on_stabilize` on the patient
    only. 15 + 2 = 17 succeeds."""
    helper, patient = ready(fighter("cleric")), downed()
    on_helper, on_patient = Probe(helper), Probe(patient)
    helper.attach(on_helper)
    patient.attach(on_patient)

    Stabilize(helper, patient).execute(rng=ScriptedRng([15]))

    assert on_helper.checks == [(Ability.WISDOM, MEDICINE, STABILIZE_DC)]
    assert (MEDICINE, STABILIZE_DC) == ("medicine", 10)
    assert on_patient.checks == []
    assert (on_helper.stabilised, on_patient.stabilised) == (0, 1)


@pytest.mark.parametrize("state", ["standing", "stable", "dead", "monster"])
def test_only_a_dying_player_character_is_stabilised(state: str) -> None:
    helper = ready(fighter("cleric"))
    if state == "standing":
        patient = fighter("pc", cell=EAST, pc=True)
    elif state == "monster":
        patient = goblin()
        patient.hp = 0
    else:
        patient = downed()
        if state == "stable":
            patient.stabilize()
        else:
            patient.die()
    assert not Stabilize(helper, patient).is_legal()


def test_the_helper_stands_within_five_feet() -> None:
    helper = ready(fighter("cleric"))
    assert Stabilize(helper, downed(cell=NORTHEAST)).is_legal()
    assert not Stabilize(helper, downed(cell=TWO_EAST)).is_legal()


# --- PickUpWeapon ------------------------------------------------------------


def test_picking_up_a_dropped_weapon_rearms_and_costs_nothing() -> None:
    """A fresh economy has no Action, and the pick-up needs none."""
    actor = fighter()
    actor.drop_held()
    before = ledger(actor)
    pick_up = PickUpWeapon(actor)
    assert pick_up.is_legal()
    pick_up.execute(rng=no_dice())
    assert actor.weapon is GREATSWORD
    assert (actor.dropped_weapon, actor.dropped_at) == (None, None)
    assert ledger(actor) == before


def test_the_weapon_is_picked_up_from_within_five_feet() -> None:
    """Dropped at (0, 0): from (1, 1) it is 5 feet away, from (2, 0) 10."""
    actor = fighter()
    actor.drop_held()
    actor.position = NORTHEAST
    assert PickUpWeapon(actor).is_legal()
    actor.position = TWO_EAST
    assert not PickUpWeapon(actor).is_legal()


def test_nothing_to_pick_up_or_a_full_hand_is_not_a_pick_up() -> None:
    armed, unarmed = fighter(), fighter("monk", weapon=None)
    assert not PickUpWeapon(armed).is_legal()
    assert not PickUpWeapon(unarmed).is_legal()

    armed.drop_held()
    armed.weapon = PIKE
    assert not PickUpWeapon(armed).is_legal()
    with pytest.raises(ValueError):
        PickUpWeapon(armed).execute(rng=no_dice())
    assert (armed.weapon, armed.dropped_weapon) == (PIKE, GREATSWORD)


# --- The acceptance cycle: dropped, healed, rearmed --------------------------


def test_a_downed_fighter_drops_its_weapon_and_picks_it_up_once_healed() -> None:
    """The fighter is dropped to 0 by 20 damage and drops the greatsword where
    it lies. Healed for 5 it wakes, still Prone. It picks the sword up for free
    and attacks with its Action at Disadvantage (Prone): d20s 12 and 15, kept
    12, + 5 = 17 against AC 10, a hit. 2d6 rolled 3 and 4, + 3 = 10; the
    goblin's 20 - 10 = 10."""
    hero, target = fighter("hero", pc=True), goblin()
    resolve_damage(hero, {SLASHING: HP}, rng=no_dice(), attacker=target)
    assert (hero.weapon, hero.dropped_weapon, hero.dropped_at) == (
        None,
        GREATSWORD,
        ORIGIN,
    )
    ready(hero)
    assert not PickUpWeapon(hero).is_legal()
    assert not Attack(hero, target, weapon=GREATSWORD).is_legal()

    healing.heal(hero, rng=no_dice(), flat=5)
    assert hero.hp == 5
    assert not any(isinstance(effect, Unconscious) for effect in hero.effects)
    assert not Attack(hero, target).is_legal()

    PickUpWeapon(hero).execute(rng=no_dice())
    assert hero.weapon is GREATSWORD
    assert hero.economy.actions == 1

    rng = ScriptedRng([12, 15, 3, 4])
    assert Attack(hero, target).execute(rng=rng) == (Outcome.HIT, 10)
    assert target.hp == 10
    assert rng.exhausted
    assert hero.economy.actions == 0


# --- The legality query provably cannot spend a resource ---------------------

Build = Callable[[], Action[Any]]


def _dropped(subject: Creature) -> Creature:
    subject.drop_held()
    return subject


def _downed_actor() -> Creature:
    """An actor with its turn started, then dropped to 0."""
    actor = ready(fighter("downed", pc=True))
    resolve_damage(actor, {SLASHING: HP}, rng=no_dice())
    return actor


def _dead_actor() -> Creature:
    actor = ready(fighter("dead"))
    actor.die()
    return actor


def _dead(subject: Creature) -> Creature:
    subject.die()
    return subject


def _stable(subject: Creature) -> Creature:
    subject.stabilize()
    return subject


def _monster_at_zero() -> Creature:
    subject = goblin()
    subject.hp = 0
    return subject


def _moved(subject: Creature, cell: Cell) -> Creature:
    subject.position = cell
    return subject


def _rearmed(subject: Creature) -> Creature:
    subject.weapon = PIKE
    return subject


SCENARIOS: Final[dict[type[Action[Any]], tuple[tuple[str, Build, bool], ...]]] = {
    Attack: (
        ("legal", lambda: Attack(ready(fighter()), goblin()), True),
        ("dying-target", lambda: Attack(ready(fighter()), downed()), True),
        (
            "named-weapon",
            lambda: Attack(ready(fighter(weapon=None)), goblin(), weapon=GREATSWORD),
            True,
        ),
        (
            "reach-weapon",
            lambda: Attack(ready(fighter()), goblin(TWO_EAST), weapon=PIKE),
            True,
        ),
        ("no-action", lambda: Attack(fighter(), goblin()), False),
        ("no-weapon", lambda: Attack(ready(fighter(weapon=None)), goblin()), False),
        ("out-of-reach", lambda: Attack(ready(fighter()), goblin(TWO_EAST)), False),
        ("itself", lambda: (lambda a: Attack(a, a))(ready(fighter())), False),
        ("dead-target", lambda: Attack(ready(fighter()), _dead(goblin())), False),
        ("dead-actor", lambda: Attack(_dead_actor(), goblin()), False),
        (
            "down-actor",
            lambda: Attack(_downed_actor(), goblin(), weapon=GREATSWORD),
            False,
        ),
    ),
    Dash: (
        ("legal", lambda: Dash(ready(fighter())), True),
        ("speed-zero", lambda: Dash(ready(fighter(speed=0))), False),
        ("no-action", lambda: Dash(fighter()), False),
        ("dead-actor", lambda: Dash(_dead_actor()), False),
        ("down-actor", lambda: Dash(_downed_actor()), False),
    ),
    Dodge: (
        ("legal", lambda: Dodge(ready(fighter())), True),
        ("no-action", lambda: Dodge(fighter()), False),
        ("dead-actor", lambda: Dodge(_dead_actor()), False),
        ("down-actor", lambda: Dodge(_downed_actor()), False),
    ),
    Disengage: (
        ("legal", lambda: Disengage(ready(fighter())), True),
        ("no-action", lambda: Disengage(fighter()), False),
        ("dead-actor", lambda: Disengage(_dead_actor()), False),
        ("down-actor", lambda: Disengage(_downed_actor()), False),
    ),
    Stabilize: (
        ("legal", lambda: Stabilize(ready(fighter()), downed()), True),
        ("diagonal", lambda: Stabilize(ready(fighter()), downed(cell=NORTHEAST)), True),
        ("too-far", lambda: Stabilize(ready(fighter()), downed(cell=TWO_EAST)), False),
        (
            "standing",
            lambda: Stabilize(ready(fighter()), fighter("pc", cell=EAST, pc=True)),
            False,
        ),
        ("stable", lambda: Stabilize(ready(fighter()), _stable(downed())), False),
        ("dead-target", lambda: Stabilize(ready(fighter()), _dead(downed())), False),
        ("monster", lambda: Stabilize(ready(fighter()), _monster_at_zero()), False),
        ("no-action", lambda: Stabilize(fighter(), downed()), False),
        ("dead-actor", lambda: Stabilize(_dead_actor(), downed()), False),
        ("down-actor", lambda: Stabilize(_downed_actor(), downed()), False),
    ),
    PickUpWeapon: (
        ("legal", lambda: PickUpWeapon(_dropped(fighter())), True),
        (
            "five-feet",
            lambda: PickUpWeapon(_moved(_dropped(fighter()), NORTHEAST)),
            True,
        ),
        (
            "ten-feet",
            lambda: PickUpWeapon(_moved(_dropped(fighter()), TWO_EAST)),
            False,
        ),
        ("nothing-dropped", lambda: PickUpWeapon(fighter(weapon=None)), False),
        ("still-wielding", lambda: PickUpWeapon(fighter()), False),
        (
            "wielding-another",
            lambda: PickUpWeapon(_rearmed(_dropped(fighter()))),
            False,
        ),
        ("dead-actor", lambda: PickUpWeapon(_dropped(_dead_actor())), False),
        ("down-actor", lambda: PickUpWeapon(_downed_actor()), False),
    ),
}
"""Every concrete action in legal and illegal states, with what `is_legal()`
should answer in each."""


def concrete_actions() -> set[type[Action[Any]]]:
    """Every non-abstract `Action` subclass in `dndsim.actions`, found by
    importing the package's modules and walking the subclass tree."""
    for module in pkgutil.iter_modules(actions.__path__):
        importlib.import_module(f"{actions.__name__}.{module.name}")
    found: set[type[Action[Any]]] = set()
    stack: list[type[Action[Any]]] = list(Action.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if not inspect.isabstract(cls) and cls.__module__.startswith(
            f"{actions.__name__}."
        ):
            found.add(cls)
    return found


def test_every_action_has_a_legality_table_with_both_answers() -> None:
    """A new action is a failure here until it is given legal and illegal
    states for the test below."""
    assert concrete_actions() == set(SCENARIOS)
    for cls, cases in SCENARIOS.items():
        answers = {expected for _, _, expected in cases}
        assert answers == {True, False}, cls.__name__


WRITERS: Final = (
    (ActionEconomy, ("spend", "grant", "move", "grant_movement", "turn_started")),
    (Resources, ("spend", "add", "recover", "transaction")),
    (EffectStore, ("apply", "remove", "expire_before", "expire_after")),
    (
        Creature,
        (
            "pick_up_weapon",
            "drop_held",
            "stabilize",
            "die",
            "revive",
            "reset_death_saves",
            "succeed_death_save",
            "fail_death_save",
            "attach",
            "detach",
            "dispatch",
        ),
    ),
    (Dispatcher, ("dispatch", "attach", "detach")),
)
"""Every method that spends, grants or changes what a creature holds. A
dispatch is among them: handlers spend from the economy."""

ENGINE_CALLS: Final = (
    attack.resolve_attack,
    damage.resolve_damage,
    dying.stabilize,
    dying.death_save,
    healing.heal,
    healing.grant_temp_hp,
    saves.saving_throw,
)
"""The engine entry points an action module may hold a name for."""


def _forbidden(name: str) -> Callable[..., Any]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"is_legal() called {name}")

    return refuse


def _participants(action: Action[Any]) -> list[Creature]:
    """The actor and every other creature the action is bound to."""
    found: list[Creature] = []
    for klass in type(action).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            value = getattr(action, slot, None)
            if isinstance(value, Creature) and all(value is not c for c in found):
                found.append(value)
    return found


def _state(subject: Creature) -> tuple[object, ...]:
    return (
        ledger(subject),
        subject.hp,
        subject.temp_hp,
        subject.position,
        subject.weapon,
        subject.dropped_weapon,
        subject.dropped_at,
        tuple(subject.effects),
        subject.dispatcher.sources(),
        subject.dead,
        subject.stable,
        subject.death_save_successes,
        subject.death_save_failures,
        tuple((pool.key, pool.current) for pool in subject.resources),
    )


def _cases() -> Iterator[Any]:
    for cls, cases in SCENARIOS.items():
        for label, build, expected in cases:
            yield pytest.param(build, expected, id=f"{cls.__name__}-{label}")


@pytest.mark.parametrize(("build", "expected"), list(_cases()))
def test_the_legality_query_cannot_spend_or_change_anything(
    build: Build, expected: bool
) -> None:
    """Every writer above raises while `is_legal()` runs, and every
    participant's state is the same before and after it, asked twice."""
    action = build()
    participants = _participants(action)
    before = [_state(subject) for subject in participants]

    with pytest.MonkeyPatch.context() as patch:
        for owner, names in WRITERS:
            for name in names:
                patch.setattr(owner, name, _forbidden(f"{owner.__name__}.{name}"))
        for module in pkgutil.iter_modules(actions.__path__):
            loaded = importlib.import_module(f"{actions.__name__}.{module.name}")
            for name, value in vars(loaded).items():
                if any(value is call for call in ENGINE_CALLS):
                    patch.setattr(loaded, name, _forbidden(name))
        first = action.is_legal()
        second = action.is_legal()

    assert first is expected
    assert second is expected
    assert [_state(subject) for subject in participants] == before


# --- Helpers -----------------------------------------------------------------


class Probe(HookSource):
    """What the stabilise handed this creature."""

    __slots__ = ("checks", "stabilised")

    ID = "test.probe"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.checks: list[tuple[Ability, str | None, int | None]] = []
        self.stabilised = 0

    @hook("on_ability_check")
    def checking(self, ctx: AbilityCheckContext) -> R:
        self.checks.append((ctx.ability, ctx.skill, ctx.dc))
        return R.APPLIED

    @hook("on_stabilize")
    def stable(self, ctx: StabilizeContext) -> R:
        self.stabilised += 1
        return R.APPLIED
