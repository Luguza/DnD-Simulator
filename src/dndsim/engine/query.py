"""What the turn loop asks of the roster: who is still fighting, and on whose
side."""

from dndsim.creatures.creature import Creature
from dndsim.engine.world import World

__all__ = ["allies_of", "enemies_of", "standing", "standing_sides"]


def standing(creature: Creature) -> bool:
    """Alive and above 0 hit points."""
    return not creature.dead and not creature.is_down


def enemies_of(world: World, creature: Creature) -> tuple[Creature, ...]:
    """Every seated creature on another side, alive, in seating order."""
    side = world.side_of(creature)
    return tuple(
        other for other in world if not other.dead and world.side_of(other) != side
    )


def allies_of(world: World, creature: Creature) -> tuple[Creature, ...]:
    """Every other seated creature on `creature`'s side, alive, in seating
    order."""
    side = world.side_of(creature)
    return tuple(
        other
        for other in world
        if other is not creature
        and not other.dead
        and world.side_of(other) == side
    )


def standing_sides(world: World) -> frozenset[str]:
    """The sides with at least one creature standing."""
    return frozenset(world.side_of(creature) for creature in world if standing(creature))
