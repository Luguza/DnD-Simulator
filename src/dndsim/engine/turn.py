"""One creature's turn, and the initiative roll that places it in the order.

A turn is its two boundaries and the offers between them. Each boundary sweeps
the encounter's clock before its hooks and after them; the start hands back
the creature's once-per budgets and its economy, rolls a dying creature's death
save, and both roll the repeat saves of the effects whose `RepeatSave` names
them. Between the two the loop offers every Command the creature can still pay
for, asks the policy, carries out what it chose and asks again, until it
declines or nothing is left (`docs/decisions.md` § w2c; DESIGN.md § The APL).
Movement is offered the same way, as a `Move` toward each enemy out of reach
(`docs/decisions.md` § w2c2), and a policy may instead walk its own `Walk`,
whose destination is the policy's (`docs/decisions.md` § w2e).
"""

from collections.abc import Sequence
from typing import Any, Final, Protocol

from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.actions.dash import Dash
from dndsim.actions.disengage import Disengage
from dndsim.actions.dodge import Dodge
from dndsim.actions.move import Move, Walk
from dndsim.actions.pick_up import PickUpWeapon
from dndsim.actions.stabilize import Stabilize
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.rng import Rng
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import modifier_total, roll_d20, settle
from dndsim.engine.dying import death_save, dying
from dndsim.engine.query import allies_of, enemies_of
from dndsim.engine.saves import saving_throw
from dndsim.engine.world import World
from dndsim.hooks.contexts import InitiativeContext, TurnContext
from dndsim.hooks.definitions import (
    ON_INITIATIVE_ROLL,
    ON_TURN_END,
    ON_TURN_START,
)
from dndsim.hooks.registry import HookSpec
from dndsim.rules.enums import Ability, SaveOutcome, SaveRepeat

__all__ = [
    "DEATH_SAVE",
    "INITIATIVE",
    "MAX_COMMANDS_PER_TURN",
    "TURN_END",
    "TURN_START",
    "Policy",
    "offers",
    "roll_initiative",
    "take_turn",
]

INITIATIVE: Final = "initiative"
"""`(INITIATIVE, creature, natural, total, tiebreak)`, at every level."""

TURN_START: Final = "turn_start"
"""`(TURN_START, round, creature)`, at every level, after the start's sweep."""

TURN_END: Final = "turn_end"
"""`(TURN_END, round, creature)`, at every level, before the end's sweep."""

DEATH_SAVE: Final = "death_save"
"""`(DEATH_SAVE, creature, natural, successes, failures)`, at every level."""

MAX_COMMANDS_PER_TURN: Final = 32
"""How many Commands one turn may carry out before `take_turn()` raises."""

_TIEBREAK_FACES: Final = 2**32


class Policy(Protocol):
    """What decides a turn: given the Commands on offer, the one to take, or
    `None` to end the turn. Must return one of `offers` itself, or a `Walk`
    of the acting creature's own."""

    def choose(
        self, actor: Creature, offers: Sequence[Action[Any]]
    ) -> Action[Any] | None: ...


def roll_initiative(creature: Creature, *, rng: Rng) -> InitiativeKey:
    """Roll `creature`'s initiative and return its place in the order.

    A Dexterity check after `on_initiative_roll`, then one independent
    uniform draw for the tiebreak, `(rng.roll(2**32) - 1) / 2**32` (DESIGN.md
    § Initiative is a key, not an index). Sets nothing on `creature`.
    """
    rolling = InitiativeContext(creature=creature)
    creature.dispatch(ON_INITIATIVE_ROLL, rolling)
    settle(
        rolling,
        "vetoed at on_initiative_roll, which cannot be called off: every "
        "combatant has a place in the order. A feature that means to change "
        "the roll grants advantage or adds to the total through ctx.bonus.",
        creature,
    )
    advantage = rolling.had_advantage
    disadvantage = rolling.had_disadvantage
    bonus = modifier_total(rolling.bonus, rng)
    rolling.close()
    natural = roll_d20(rng, advantage=advantage, disadvantage=disadvantage)
    total = natural + creature.modifier(Ability.DEXTERITY) + bonus
    tiebreak = (rng.roll(_TIEBREAK_FACES) - 1) / _TIEBREAK_FACES
    key = InitiativeKey(
        initiative=total,
        dexterity=creature.stats.abilities[Ability.DEXTERITY],
        player_character=creature.player_character,
        tiebreak=tiebreak,
    )
    log = creature.effects.log
    if log is not None:
        log.write((INITIATIVE, creature.name, natural, total, tiebreak))
    return key


def offers(world: World, actor: Creature) -> list[Action[Any]]:
    """Every Command `actor` could take now and pay for, in a fixed order.

    Built from the six in `actions/` and a `Move` toward each enemy against
    the roster, then filtered by `is_legal()`. Spends nothing.
    """
    log = world.log
    enemies = enemies_of(world, actor)
    candidates: list[Action[Any]] = [Attack(actor, enemy, log=log) for enemy in enemies]
    candidates.extend(Stabilize(actor, ally) for ally in allies_of(world, actor))
    candidates.extend(
        (Dodge(actor), Disengage(actor), Dash(actor), PickUpWeapon(actor))
    )
    candidates.extend(Move(actor, enemy, world=world) for enemy in enemies)
    return [command for command in candidates if command.is_legal()]


def take_turn(
    world: World, creature: Creature, round_number: int, *, policy: Policy, rng: Rng
) -> int:
    """Run `creature`'s turn in round `round_number`, and return how many
    Commands it carried out.

    Raises `EngineAssemblyError` when the policy chooses something it was not
    offered and that is not the creature's own `Walk`, or keeps choosing past
    `MAX_COMMANDS_PER_TURN`. A `Walk` it chose that cannot be taken raises
    `ValueError` from `execute()`.
    """
    key = creature.initiative
    start = TimeKey(round_number, key, TurnPhase.START)
    end = TimeKey(round_number, key, TurnPhase.END)
    clock = world.clock
    log = world.log

    clock.expire_before(start)
    if log is not None:
        log.write((TURN_START, round_number, creature.name))
    for other in world:
        other.dispatcher.resets.turn_started(own=other is creature)
    # Both saves before the economy, which reads Speed: a Speed-0 condition
    # saved off, or a death save that wakes the creature, leaves the turn its
    # full movement.
    _repeat_saves(world, creature, SaveRepeat.TURN_START, rng)
    if dying(creature):
        result = death_save(creature, rng=rng)
        if log is not None:
            log.write(
                (
                    DEATH_SAVE,
                    creature.name,
                    result.natural,
                    result.successes,
                    result.failures,
                )
            )
        if creature.dead:
            # A dead creature takes no turn, but the clock still passes it.
            clock.expire_after(start)
            clock.expire_before(end)
            if log is not None:
                log.write((TURN_END, round_number, creature.name))
            clock.expire_after(end)
            return 0
    creature.economy.turn_started(creature.speed_ft)
    _announce(ON_TURN_START, creature, start)
    clock.expire_after(start)

    taken = 0
    while True:
        offered = offers(world, creature)
        # Nothing on offer still leaves the policy its own `Walk` while
        # movement is left (`docs/decisions.md` § w2e).
        if not offered and creature.economy.movement_ft <= 0:
            break
        choice = policy.choose(creature, offered)
        if choice is None:
            break
        if not any(choice is offer for offer in offered) and not (
            isinstance(choice, Walk) and choice.actor is creature
        ):
            raise EngineAssemblyError(
                f"the policy for {creature.name} chose {choice!r}, which was not "
                "offered; a policy picks one of the Commands it is handed"
            )
        if taken == MAX_COMMANDS_PER_TURN:
            raise EngineAssemblyError(
                f"the policy for {creature.name} chose a {MAX_COMMANDS_PER_TURN + 1}"
                "th Command in one turn; only a free Command that stays legal "
                "can be chosen that often, and choosing it forever ends nothing"
            )
        choice.execute(rng=rng)
        taken += 1

    clock.expire_before(end)
    _repeat_saves(world, creature, SaveRepeat.TURN_END, rng)
    _announce(ON_TURN_END, creature, end)
    if log is not None:
        log.write((TURN_END, round_number, creature.name))
    clock.expire_after(end)
    return taken


def _announce(spec: HookSpec, creature: Creature, time: TimeKey) -> None:
    turn = TurnContext(creature=creature, time=time)
    creature.dispatch(spec, turn)
    settle(
        turn,
        f"vetoed at {spec.name}, which cannot be called off: a turn boundary "
        "is a position on the clock.",
        creature,
    )
    turn.close()


def _repeat_saves(world: World, creature: Creature, when: SaveRepeat, rng: Rng) -> None:
    """Roll the repeat save of every effect on `creature` that asks for one at
    `when`, ending each that is saved against."""
    effects = creature.effects
    for effect in tuple(effects):
        repeat = effect.repeat_save
        if repeat is None or repeat.when is not when:
            continue
        # An earlier save's handlers may have ended this one already.
        if not any(held is effect for held in effects):
            continue
        caster = effect.caster
        imposer = None if caster is None else world.holder(caster)
        result = saving_throw(
            creature,
            ability=repeat.ability,
            dc=repeat.dc,
            rng=rng,
            imposed_by=imposer,
            log=world.log,
        )
        if result.outcome is SaveOutcome.SUCCESS and any(
            held is effect for held in effects
        ):
            effects.remove(effect)
