"""One creature's turn, and the initiative roll that places it in the order.

A turn is its two boundaries and the offers between them. Each boundary sweeps
the encounter's clock before its hooks and after them; the start hands back
the creature's once-per budgets and its economy, rolls a dying creature's death
save, and both roll the repeat saves of the effects whose `RepeatSave` names
them. Between the two the loop offers every Command the creature can still pay
for, asks the policy, carries out what it chose and asks again, until it
declines or nothing is left (`docs/decisions.md` § w2c; DESIGN.md § The APL).
Movement is offered the same way, as a `Move` toward each enemy out of reach
(`docs/decisions.md` § w2c2).
"""

from collections.abc import Sequence
from typing import Any, ClassVar, Final, Protocol

from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.actions.dash import Dash
from dndsim.actions.disengage import Disengage, disengaged
from dndsim.actions.dodge import Dodge
from dndsim.actions.pick_up import PickUpWeapon
from dndsim.actions.stabilize import Stabilize
from dndsim.core.errors import EngineAssemblyError
from dndsim.core.geometry import Cell, distance_ft, footprint, in_reach, route
from dndsim.core.rng import Rng
from dndsim.core.timeline import InitiativeKey, TimeKey, TurnPhase
from dndsim.creatures.creature import Creature
from dndsim.engine.d20 import modifier_total, roll_d20, settle
from dndsim.engine.dying import death_save, dying
from dndsim.engine.query import allies_of, enemies_of
from dndsim.engine.saves import saving_throw
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import InitiativeContext, MoveContext, TurnContext
from dndsim.hooks.definitions import (
    ON_INITIATIVE_ROLL,
    ON_MOVE,
    ON_TURN_END,
    ON_TURN_START,
)
from dndsim.hooks.registry import HookSpec
from dndsim.rules.constants import CELL_SIZE_FT, SIZE_WIDTH_CELLS
from dndsim.rules.enums import Ability, ActionCost, SaveOutcome, SaveRepeat

__all__ = [
    "DEATH_SAVE",
    "INITIATIVE",
    "MAX_COMMANDS_PER_TURN",
    "TURN_END",
    "TURN_START",
    "Move",
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
    `None` to end the turn. Must return one of `offers` itself."""

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


class Move(Action[tuple[Cell, ...]]):
    """`actor` walks toward `target` until it has it in reach, as far as its
    movement this turn allows, provoking on the way unless it is Disengaged.

    Paid in feet rather than an economy slot, so `COST` is `None`; legal while
    Incapacitated, which stops no movement. Every other living creature's
    space is impassable and no destination is inside one. An actor holding a
    `CRAWLS` effect pays ten feet a square, and one holding a `NO_APPROACH`
    effect walks no step that ends closer to its caster than the square it
    left
    (`docs/decisions.md` § w2c2). `execute()` returns the squares walked.

    The route is planned once for the positions the two creatures hold, so
    asking `is_legal()` and then executing plans it once.
    """

    __slots__ = ("_planned", "target", "world")

    ID: ClassVar[str] = "move"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, target: Creature, *, world: World) -> None:
        super().__init__(actor)
        self.target = target
        self.world = world
        self._planned: tuple[Cell, Cell, tuple[Cell, ...]] | None = None

    def is_legal(self) -> bool:
        """Whether the actor can take a step toward `target` now. Changes
        nothing."""
        actor = self.actor
        if actor.dead or actor.is_down:
            return False
        return self._permits()

    def _permits(self) -> bool:
        actor, target = self.actor, self.target
        if target is actor or target.dead or actor.speed_ft == 0:
            return False
        if not actor.economy.can_move(_step_ft(actor)):
            return False
        return bool(self._walkable())

    def _perform(self, rng: Rng) -> tuple[Cell, ...]:
        actor = self.actor
        walked = walk_provoking(
            actor,
            self._walkable(),
            self.world,
            provokes=not disengaged(actor),
            step_ft=_step_ft(actor),
        )
        if walked:
            moved = MoveContext(
                creature=actor, route=walked, distance_ft=len(walked) * CELL_SIZE_FT
            )
            actor.dispatch(ON_MOVE, moved)
            settle(
                moved,
                "vetoed at on_move, which cannot be called off: the creature "
                "has already moved.",
                actor,
            )
            moved.close()
        return walked

    def _walkable(self) -> tuple[Cell, ...]:
        """The route as far as this turn's movement pays for it and fear
        allows."""
        actor = self.actor
        planned = self._route()[: actor.economy.movement_ft // _step_ft(actor)]
        sources = _sources_of_fear(self.world, actor)
        if not sources:
            return planned
        here = actor.position
        size = actor.size
        for index, square in enumerate(planned):
            for source in sources:
                before = distance_ft(
                    here,
                    source.position,
                    origin_size=size,
                    destination_size=source.size,
                )
                after = distance_ft(
                    square,
                    source.position,
                    origin_size=size,
                    destination_size=source.size,
                )
                if after < before:
                    return planned[:index]
            here = square
        return planned

    def _route(self) -> tuple[Cell, ...]:
        """The shortest legal route to the nearest square from which the
        actor has `target` in reach; empty when it already has, or when no
        such square can be reached."""
        actor, target = self.actor, self.target
        origin = actor.position
        planned = self._planned
        if (
            planned is not None
            and planned[0] == origin
            and planned[1] == target.position
        ):
            return planned[2]
        walked = self._plan(origin)
        self._planned = (origin, target.position, walked)
        return walked

    def _plan(self, origin: Cell) -> tuple[Cell, ...]:
        actor, target = self.actor, self.target
        reach = actor.reach_ft
        if in_reach(
            origin,
            target.position,
            reach,
            origin_size=actor.size,
            destination_size=target.size,
        ):
            return ()
        blocked = _occupied(self.world, actor)
        for destination in _stands(actor, target, blocked):
            walked = route(origin, destination, blocked=blocked, size=actor.size)
            if walked and walked[-1] == destination:
                return walked
        return ()


def _step_ft(actor: Creature) -> int:
    """What one square costs `actor`: double while an effect it holds
    `CRAWLS`."""
    for effect in actor.effects:
        if effect.CRAWLS:
            return 2 * CELL_SIZE_FT
    return CELL_SIZE_FT


def _sources_of_fear(world: World, actor: Creature) -> tuple[Creature, ...]:
    """The seated casters of the `NO_APPROACH` effects `actor` holds."""
    sources: list[Creature] = []
    for effect in actor.effects:
        if effect.NO_APPROACH and effect.caster is not None:
            source = world.holder(effect.caster)
            if source is not None:
                sources.append(source)
    return tuple(sources)


def _occupied(world: World, mover: Creature) -> frozenset[Cell]:
    """Every square a living creature other than `mover` stands in."""
    return frozenset(
        cell
        for creature in world
        if creature is not mover and not creature.dead
        for cell in footprint(creature.position, creature.size)
    )


def _stands(actor: Creature, target: Creature, blocked: frozenset[Cell]) -> list[Cell]:
    """The anchors from which `actor`'s footprint has `target` in reach and
    stands in nobody's space, nearest to the actor first, then lowest `y`,
    then lowest `x`."""
    width = SIZE_WIDTH_CELLS[actor.size]
    reach_cells = actor.reach_ft // CELL_SIZE_FT
    anchor = target.position
    target_width = SIZE_WIDTH_CELLS[target.size]
    # The box no footprint in reach can lie outside: its far edge within
    # `reach_cells` of the target's near one, on each axis.
    low_x = anchor.x - reach_cells - width + 1
    high_x = anchor.x + target_width - 1 + reach_cells
    low_y = anchor.y - reach_cells - width + 1
    high_y = anchor.y + target_width - 1 + reach_cells
    origin = actor.position
    found = [
        Cell(x, y)
        for y in range(low_y, high_y + 1)
        for x in range(low_x, high_x + 1)
        if in_reach(
            Cell(x, y),
            anchor,
            actor.reach_ft,
            origin_size=actor.size,
            destination_size=target.size,
        )
        and not any(cell in blocked for cell in footprint(Cell(x, y), actor.size))
    ]
    found.sort(key=lambda cell: (distance_ft(origin, cell), cell.y, cell.x))
    return found


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
    offered, or keeps choosing past `MAX_COMMANDS_PER_TURN`.
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
        if not offered:
            break
        choice = policy.choose(creature, offered)
        if choice is None:
            break
        if not any(choice is offer for offer in offered):
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
