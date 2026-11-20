"""The Unarmed Strike's three options, and how a grapple ends (XPHB, "Unarmed
Strike", "Grappling").

`UnarmedStrike` is the Damage option; `Grapple`, `ShoveDown` and `ShovePush`
are the other two, each a save rather than an attack roll. `Grappling` is the
grappler's side of a grapple, the hand it holds; `EscapeGrapple` and
`ReleaseGrapple` end one, and `loosen_grapples()` ends those nobody chose to
(`docs/decisions.md` § w2j).
"""

import math
from abc import abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from dndsim.actions.attack import Attack
from dndsim.actions.base import Action
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.prone import Prone
from dndsim.core.geometry import Cell, footprint, in_reach
from dndsim.core.log import EventLog
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect, Stacking
from dndsim.creatures.stats import UNARMED_STRIKE
from dndsim.engine.d20 import ability_check, check_bonus
from dndsim.engine.saves import save_bonus, save_dc, saving_throw
from dndsim.engine.triggers import walk_provoking
from dndsim.engine.world import World
from dndsim.hooks.contexts import ConditionContext
from dndsim.hooks.decorator import R, hook
from dndsim.rules.constants import CELL_SIZE_FT, SIZE_ORDER, SIZE_WIDTH_CELLS
from dndsim.rules.enums import Ability, ActionCost, SaveOutcome

__all__ = [
    "EscapeGrapple",
    "Grapple",
    "Grappling",
    "ReleaseGrapple",
    "ShoveDown",
    "ShovePush",
    "UnarmedStrike",
    "endings",
    "unarmed_options",
    "grips",
    "loosen_grapples",
]

_HANDS = 2

_TAN_22_5 = math.sqrt(2) - 1
"""Where the nearest compass direction turns from straight to diagonal."""

_ESCAPE_SKILLS = ((Ability.STRENGTH, "athletics"), (Ability.DEXTERITY, "acrobatics"))


class UnarmedStrike(Attack):
    """`actor` makes an Unarmed Strike's Damage option against `target`."""

    __slots__ = ()

    ID: ClassVar[str] = "unarmed_strike"

    def __init__(
        self,
        actor: Creature,
        target: Creature,
        *,
        log: EventLog | None = None,
        world: World | None = None,
    ) -> None:
        super().__init__(actor, target, weapon=UNARMED_STRIKE, log=log, world=world)


class Grappling(Effect):
    """The grappler's hold on `target`: one hand filled, and the DC to escape.

    Ends the grapple when its owner gains a condition that leaves it
    Incapacitated.
    """

    __slots__ = ("_creature", "escape_dc", "target")

    STACKING: ClassVar[Stacking] = Stacking.STACK

    def __init__(self, owner: Creature, *, target: Creature, escape_dc: int) -> None:
        super().__init__(owner)
        self._creature = owner
        self.target = target
        self.escape_dc = escape_dc

    @hook("on_condition_gained")
    def let_go(self, ctx: ConditionContext) -> R:
        """Ends the grapple once the grappler is Incapacitated (XPHB
        "Grappling", Ending a Grapple)."""
        if not self._creature.incapacitated:
            return R.SKIPPED
        end_grapple(self._creature, self.target)
        return R.APPLIED


class _Option(Action[SaveOutcome | None]):
    """A Grapple or a Shove: `target`, within 5 feet and no more than one size
    larger, saves with Strength or Dexterity against 8 plus the actor's
    Strength modifier and Proficiency Bonus. `execute()` returns how the save
    went."""

    __slots__ = ("log", "target", "world")

    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def __init__(self, actor: Creature, target: Creature, *, world: World) -> None:
        super().__init__(actor)
        self.target = target
        self.world = world
        self.log = world.log

    def _permits(self) -> bool:
        return _within_grasp(self.actor, self.target) and self._own()

    def _own(self) -> bool:
        """What this option asks besides the target being within grasp."""
        return True

    def _perform(self, rng: Rng) -> SaveOutcome | None:
        actor, target = self.actor, self.target
        dc = save_dc(actor, Ability.STRENGTH)
        # `max` keeps the first of equals: a tie goes to Strength
        # (`docs/questions.md` #28).
        ability = max(
            (Ability.STRENGTH, Ability.DEXTERITY),
            key=lambda each: save_bonus(target, each),
        )
        result = saving_throw(
            target, ability=ability, dc=dc, rng=rng, imposed_by=actor, log=self.log
        )
        if result.outcome is SaveOutcome.FAILURE:
            self._land(dc)
        return result.outcome

    @abstractmethod
    def _land(self, dc: int) -> None:
        """What a failed save does."""

    def __repr__(self) -> str:
        return f"<{self.ID} by {self.actor.name} on {self.target.name}>"


class Grapple(_Option):
    """`actor` grapples `target`, with a hand free and not holding it already."""

    __slots__ = ()

    ID: ClassVar[str] = "grapple"

    def _own(self) -> bool:
        actor, target = self.actor, self.target
        held = grips(actor)
        return actor.hands_held + len(held) < _HANDS and not any(
            grip.target is target for grip in held
        )

    def _land(self, dc: int) -> None:
        actor, target = self.actor, self.target
        if target.effects.apply(Grappled(target, caster=actor)) is not None:
            actor.effects.apply(Grappling(actor, target=target, escape_dc=dc))


class ShoveDown(_Option):
    """`actor` shoves `target` to give it the Prone condition."""

    __slots__ = ()

    ID: ClassVar[str] = "shove_prone"

    def _land(self, dc: int) -> None:
        self.target.effects.apply(Prone(self.target))


class ShovePush(_Option):
    """`actor` shoves `target` 5 feet directly away, to a square nobody stands
    in, as forced movement that provokes nothing and costs nothing."""

    __slots__ = ()

    ID: ClassVar[str] = "shove_push"

    def _own(self) -> bool:
        return self._square() is not None

    def _land(self, dc: int) -> None:
        square = self._square()
        if square is None:
            return
        target = self.target
        walk_provoking(target, (square,), self.world, provokes=False, charge=False)
        loosen_grapples(self.world, target)

    def _square(self) -> Cell | None:
        """The anchor 5 feet directly away from the actor — one square along
        the compass direction nearest the line between the two centres — when
        it is clear."""
        actor, target = self.actor, self.target
        width = SIZE_WIDTH_CELLS[actor.size]
        target_width = SIZE_WIDTH_CELLS[target.size]
        # Twice each centre, so a footprint of even width stays on integers.
        dx = (2 * target.position.x + target_width) - (2 * actor.position.x + width)
        dy = (2 * target.position.y + target_width) - (2 * actor.position.y + width)
        square = Cell(
            target.position.x + _step(dx, dy), target.position.y + _step(dy, dx)
        )
        cells = footprint(square, target.size)
        for creature in self.world:
            if creature is target or creature.dead:
                continue
            if SIZE_WIDTH_CELLS[creature.size] == 1:
                if creature.position in cells:
                    return None
            elif any(
                cell in cells for cell in footprint(creature.position, creature.size)
            ):
                return None
        return square


class EscapeGrapple(Action[bool]):
    """`actor` tries to escape the grapple `grappler` holds it in: a Strength
    (Athletics) or Dexterity (Acrobatics) check, the better of the two, against
    the grapple's escape DC. `execute()` returns whether it escaped."""

    __slots__ = ("grappler", "log")

    ID: ClassVar[str] = "escape_grapple"
    COST: ClassVar[ActionCost | None] = ActionCost.ACTION

    def __init__(
        self, actor: Creature, grappler: Creature, *, log: EventLog | None = None
    ) -> None:
        super().__init__(actor)
        self.grappler = grappler
        self.log = log

    @property
    def target(self) -> Creature:
        """The grappler, which is who an APL's `target:` names."""
        return self.grappler

    def _permits(self) -> bool:
        grappler = self.grappler
        return any(
            isinstance(effect, Grappled) and effect.caster is grappler
            for effect in self.actor.effects
        )

    def _perform(self, rng: Rng) -> bool:
        actor, grappler = self.actor, self.grappler
        hold = next(
            (grip for grip in grips(grappler) if grip.target is actor),
            None,
        )
        dc = save_dc(grappler, Ability.STRENGTH) if hold is None else hold.escape_dc
        ability, skill = max(_ESCAPE_SKILLS, key=lambda pair: check_bonus(actor, *pair))
        total = ability_check(
            actor, ability, skill=skill, dc=dc, rng=rng, among=(grappler,)
        )
        escaped = total >= dc
        if escaped:
            end_grapple(grappler, actor)
        return escaped

    def __repr__(self) -> str:
        return f"<{self.ID} by {self.actor.name} from {self.grappler.name}>"


class ReleaseGrapple(Action[None]):
    """`actor` lets go of `target`, which needs no action."""

    __slots__ = ("target",)

    ID: ClassVar[str] = "release_grapple"
    COST: ClassVar[ActionCost | None] = None

    def __init__(self, actor: Creature, target: Creature) -> None:
        super().__init__(actor)
        self.target = target

    def _permits(self) -> bool:
        target = self.target
        return any(grip.target is target for grip in grips(self.actor))

    def _perform(self, rng: Rng) -> None:
        end_grapple(self.actor, self.target)

    def __repr__(self) -> str:
        return f"<{self.ID} by {self.actor.name} of {self.target.name}>"


def unarmed_options(
    world: World, actor: Creature, enemies: Sequence[Creature]
) -> list[UnarmedStrike | Grapple | ShoveDown | ShovePush]:
    """The Unarmed Strike's options `actor` could take now against each of
    `enemies`: exactly those whose `is_legal()` is true, with the actor's own
    half of the question asked once rather than once an option."""
    if (
        actor.dead
        or actor.is_down
        or actor.incapacitated
        or not actor.economy.can_spend(ActionCost.ACTION)
    ):
        return []
    log = world.log
    reach = SIZE_ORDER[actor.size] + 1
    options: list[UnarmedStrike | Grapple | ShoveDown | ShovePush] = []
    for enemy in enemies:
        if enemy is actor or enemy.dead or not _adjacent(actor, enemy):
            continue
        # Trap: added without asking `is_legal()`;
        # `test_unarmed_options_are_exactly_the_legal_ones` holds the two equal.
        options.append(UnarmedStrike(actor, enemy, log=log, world=world))
        if SIZE_ORDER[enemy.size] > reach:
            continue
        for option in (
            Grapple(actor, enemy, world=world),
            ShoveDown(actor, enemy, world=world),
            ShovePush(actor, enemy, world=world),
        ):
            if option._own():
                options.append(option)
    return options


def endings(world: World, actor: Creature) -> list[EscapeGrapple | ReleaseGrapple]:
    """An escape from each grapple `actor` is held in, by a grappler seated in
    `world`, and a release of each it holds; legal or not."""
    ending: list[EscapeGrapple | ReleaseGrapple] = []
    for effect in actor.effects:
        if isinstance(effect, Grappled) and effect.caster is not None:
            grappler = world.holder(effect.caster)
            if grappler is not None:
                ending.append(EscapeGrapple(actor, grappler, log=world.log))
        elif isinstance(effect, Grappling):
            ending.append(ReleaseGrapple(actor, effect.target))
    return ending


def grips(grappler: Creature) -> list[Grappling]:
    """The grapples `grappler` holds whose target is still Grappled by it."""
    held: list[Grappling] = []
    for effect in grappler.effects:
        if isinstance(effect, Grappling) and any(
            isinstance(other, Grappled) and other.caster is grappler
            for other in effect.target.effects
        ):
            held.append(effect)
    return held


def end_grapple(grappler: Creature, target: Creature) -> None:
    """End the grapple `grappler` holds `target` in, on both sides."""
    for effect in tuple(target.effects):
        if isinstance(effect, Grappled) and effect.caster is grappler:
            target.effects.remove(effect)
    for effect in tuple(grappler.effects):
        if isinstance(effect, Grappling) and effect.target is target:
            grappler.effects.remove(effect)


def loosen_grapples(world: World, creature: Creature) -> None:
    """End every grapple `creature` holds or is held in whose other party is
    dead, gone from `world`, or more than 5 feet away."""
    for effect in tuple(creature.effects):
        if isinstance(effect, Grappling):
            if _slipped(world, creature, effect.target):
                end_grapple(creature, effect.target)
        elif isinstance(effect, Grappled) and effect.caster is not None:
            grappler = world.holder(effect.caster)
            if grappler is None:
                creature.effects.remove(effect)
            elif _slipped(world, grappler, creature):
                end_grapple(grappler, creature)


def _slipped(world: World, grappler: Creature, target: Creature) -> bool:
    return (
        grappler.dead
        or target.dead
        or target not in world
        or not _adjacent(grappler, target)
    )


def _step(along: int, across: int) -> int:
    """The step on one axis of the compass direction, of the eight, nearest
    the line between two centres `along` and `across` apart on the two axes."""
    if abs(along) < _TAN_22_5 * abs(across):
        return 0
    return (along > 0) - (along < 0)


def _within_grasp(actor: Creature, target: Creature) -> bool:
    """What a Grapple and both Shoves ask of `target`: another creature,
    alive, within 5 feet and no more than one size larger than `actor`."""
    return (
        target is not actor
        and not target.dead
        and SIZE_ORDER[target.size] <= SIZE_ORDER[actor.size] + 1
        and _adjacent(actor, target)
    )


def _adjacent(one: Creature, other: Creature) -> bool:
    """Whether `other` is within the Unarmed Strike's 5 feet of `one`."""
    return in_reach(
        one.position,
        other.position,
        CELL_SIZE_FT,
        origin_size=one.size,
        destination_size=other.size,
    )
