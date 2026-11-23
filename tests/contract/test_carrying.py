"""What a creature holds and carries: a second weapon, the Light property's
extra attack, the Attack action's equip, Ammunition, Loading and Thrown
(debts #68, #69, #74; `docs/decisions.md` § w2k).

XPHB "Equipment" -> Weapon Properties (Ammunition, Light, Loading, Thrown);
"Actions" -> Attack (Equipping and Unequipping Weapons); "Combat" -> Your Turn
(Interacting with Things).
"""

from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Final

import pytest

from dndsim.actions.attack import Attack, LightAttack
from dndsim.actions.base import Action
from dndsim.actions.pick_up import PickUpWeapon
from dndsim.apl.apl import Apl
from dndsim.apl.expr import AplLoadError
from dndsim.conditions.charmed import Charmed
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.dice import Dice
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.stats import StatBlock, Weapon
from dndsim.engine.attack import PREVENTED
from dndsim.engine.turn import offers
from dndsim.engine.world import World
from dndsim.rules.enums import Ability, ActionCost, WeaponProperty
from weapon_fixtures import (
    DAGGER,
    HAND_CROSSBOW,
    HANDAXE,
    LONGBOW,
    LONGSWORD,
    SHORTSWORD,
)

SCORES: Final = MappingProxyType(
    {**dict.fromkeys(Ability, 10), Ability.STRENGTH: 16}  # +3
)
WEAK: Final = MappingProxyType(dict.fromkeys(Ability, 6))  # -2
HP: Final = 30
ORIGIN: Final = Cell(0, 0)
EAST: Final = Cell(1, 0)
FAR: Final = Cell(4, 0)
"""20 feet east: out of every reach, inside every range here."""


def creature(
    name: str = "hero",
    *,
    cell: Cell = ORIGIN,
    weapon: Weapon | None = None,
    off_hand: Weapon | None = None,
    carried: dict[Weapon, int] | None = None,
    ammunition: dict[str, int] | None = None,
    shield: bool = False,
    scores: MappingProxyType[Ability, int] = SCORES,
) -> Creature:
    return Creature(
        name=name,
        stats=StatBlock(abilities=scores, max_hp=HP),
        position=cell,
        weapon=weapon,
        off_hand=off_hand,
        carried=carried,
        ammunition=ammunition,
        shield=shield,
    )


def ready(subject: Creature) -> Creature:
    subject.economy.turn_started(subject.speed_ft)
    return subject


def duel(hero: Creature, cell: Cell = EAST) -> tuple[World, Creature, Creature]:
    """`hero`, its turn started, against a goblin (AC 10) at `cell`."""
    goblin = creature("goblin", cell=cell)
    world = World()
    world.seat(ready(hero), "heroes")
    world.seat(goblin, "monsters")
    return world, hero, goblin


def armed(offered: Sequence[Action[Any]]) -> list[tuple[str, str]]:
    """Each attack offered, as its id and the name of its weapon."""
    return [
        (offer.ID, offer.weapon.name)
        for offer in offered
        if isinstance(offer, Attack)
        and offer.weapon is not None
        and offer.ID in ("attack", "light_attack")
    ]


def take(offered: Sequence[Action[Any]], action: str, weapon: Weapon) -> Attack:
    return next(
        offer
        for offer in offered
        if isinstance(offer, Attack) and action == offer.ID and offer.weapon == weapon
    )


# --- the Light property's Bonus Action attack (#74) ---------------------------


def test_the_light_attack_follows_an_attack_with_a_light_weapon() -> None:
    """Shortsword: natural 15 hits AC 10, 1d6 (4) + 3 = 7. Then the Dagger,
    the different Light weapon, as the Bonus Action: 1d4 (3) and no +3."""
    world, hero, goblin = duel(creature(weapon=SHORTSWORD, off_hand=DAGGER))
    before = offers(world, hero)
    assert armed(before) == [("attack", "Shortsword"), ("attack", "Dagger")]

    rng = ScriptedRng([15, 4, 15, 3])
    take(before, "attack", SHORTSWORD).execute(rng=rng)
    after = offers(world, hero)
    assert armed(after) == [("light_attack", "Dagger")]
    take(after, "light_attack", DAGGER).execute(rng=rng)
    assert goblin.hp == HP - 7 - 3
    assert rng.exhausted
    assert armed(offers(world, hero)) == []


def test_a_negative_modifier_still_counts_on_the_light_attack() -> None:
    """Strength and Dexterity 6, -2: 1d4 (3) - 2 = 1."""
    hero = creature(weapon=SHORTSWORD, off_hand=DAGGER, scores=WEAK)
    world, hero, goblin = duel(hero)
    rng = ScriptedRng([15, 4, 15, 3])
    Attack(hero, goblin, weapon=SHORTSWORD).execute(rng=rng)
    goblin.hp = HP
    LightAttack(hero, goblin, weapon=DAGGER, world=world).execute(rng=rng)
    assert goblin.hp == HP - 1


def test_no_light_attack_follows_a_weapon_that_is_not_light() -> None:
    world, hero, goblin = duel(creature(weapon=LONGSWORD, off_hand=DAGGER))
    Attack(hero, goblin, weapon=LONGSWORD).execute(rng=ScriptedRng([1]))
    assert hero.economy.light_attack is None
    assert armed(offers(world, hero)) == []


def test_two_of_one_light_weapon_are_two_weapons() -> None:
    """A Dagger in each hand: the Light attack is made with the other one."""
    world, hero, goblin = duel(creature(weapon=DAGGER, off_hand=DAGGER))
    Attack(hero, goblin, weapon=DAGGER).execute(rng=ScriptedRng([1]))
    assert armed(offers(world, hero)) == [("light_attack", "Dagger")]


# --- the Attack action's equip, and the turn's object interaction (#74) ------


def test_an_archer_draws_a_melee_weapon_and_the_policy_chooses_it() -> None:
    """The goblin has closed: the bow is still offered, at Disadvantage, and
    so is the carried Longsword. Drawing it stows the two-handed bow with the
    turn's object interaction (`docs/questions.md` #33)."""
    archer = creature(weapon=LONGBOW, carried={LONGSWORD: 1}, ammunition={"Arrow": 5})
    world, archer, goblin = duel(archer)
    offered = offers(world, archer)
    assert armed(offered) == [("attack", "Longbow"), ("attack", "Longsword")]

    held = Apl.from_yaml("- {action: attack}").bind(archer, world)
    sword = Apl.from_yaml("- {action: attack, with: {weapon: Longsword}}").bind(
        archer, world
    )
    bow_choice = held.choose(archer, offered)
    sword_choice = sword.choose(archer, offered)
    assert isinstance(bow_choice, Attack) and bow_choice.weapon == LONGBOW
    assert isinstance(sword_choice, Attack) and sword_choice.weapon == LONGSWORD

    sword_choice.execute(rng=ScriptedRng([15, 5]))
    assert (archer.weapon, dict(archer.stowed)) == (LONGSWORD, {LONGBOW: 1})
    assert archer.ammunition("Arrow") == 5
    assert not archer.economy.can_interact()
    assert goblin.hp == HP - 8


def test_a_second_switch_in_a_turn_has_no_interaction_left() -> None:
    archer = creature(weapon=LONGBOW, carried={LONGSWORD: 1}, ammunition={"Arrow": 5})
    world, archer, goblin = duel(archer)
    archer.economy.grant(ActionCost.ACTION)
    Attack(archer, goblin, weapon=LONGSWORD).execute(rng=ScriptedRng([1]))
    assert not Attack(archer, goblin, weapon=LONGBOW).is_legal()
    assert Attack(archer, goblin, weapon=LONGSWORD).is_legal()


def test_a_free_hand_needs_no_interaction_to_draw() -> None:
    world, hero, goblin = duel(creature(weapon=DAGGER, carried={SHORTSWORD: 1}))
    Attack(hero, goblin, weapon=SHORTSWORD).execute(rng=ScriptedRng([1]))
    assert hero.held == (DAGGER, SHORTSWORD)
    assert hero.economy.can_interact()


def test_a_weapon_named_but_not_carried_is_not_attacked_with() -> None:
    world, hero, goblin = duel(creature(weapon=DAGGER))
    assert not Attack(hero, goblin, weapon=LONGSWORD).is_legal()


def test_only_an_attack_names_a_weapon() -> None:
    with pytest.raises(AplLoadError, match="names a weapon"):
        Apl.from_yaml("- {action: dodge, with: {weapon: Dagger}}")


# --- Ammunition and Loading (#68) --------------------------------------------


def test_each_shot_spends_a_piece_until_the_quiver_runs_out() -> None:
    archer = creature(weapon=LONGBOW, ammunition={"Arrow": 2})
    world, archer, goblin = duel(archer, FAR)
    archer.economy.grant(ActionCost.ACTION, 2)
    for left in (1, 0):
        Attack(archer, goblin, world=world).execute(rng=ScriptedRng([1]))
        assert archer.ammunition("Arrow") == left
    assert archer.economy.can_spend(ActionCost.ACTION)
    assert armed(offers(world, archer)) == []


def test_a_weapon_fires_only_its_own_ammunition() -> None:
    archer = creature(weapon=LONGBOW, ammunition={"Bolt": 20})
    world, archer, goblin = duel(archer, FAR)
    assert armed(offers(world, archer)) == []


@pytest.mark.parametrize(
    ("kit", "legal"),
    [
        ({}, True),
        ({"shield": True}, False),
        ({"off_hand": DAGGER}, False),
    ],
)
def test_a_one_handed_weapon_loads_only_with_a_hand_free(
    kit: dict[str, Any], legal: bool
) -> None:
    shooter = creature(weapon=HAND_CROSSBOW, ammunition={"Bolt": 20}, **kit)
    world, shooter, goblin = duel(shooter, FAR)
    assert Attack(shooter, goblin, world=world).is_legal() is legal


def test_loading_limits_nothing_while_every_action_makes_one_attack() -> None:
    """One bolt with the Attack action and no second shot from the Hand
    Crossbow that turn: the Light attack needs a different Light weapon.
    The row that lands Extra Attack meets this test (`docs/decisions.md`
    § The `w2k` spec correction)."""
    shooter = creature(weapon=HAND_CROSSBOW, ammunition={"Bolt": 20})
    world, shooter, goblin = duel(shooter, FAR)
    Attack(shooter, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert shooter.ammunition("Bolt") == 19
    assert shooter.economy.light_attack == 0
    assert armed(offers(world, shooter)) == []


# --- Thrown (#69) ------------------------------------------------------------


def test_a_single_handaxe_is_thrown_once() -> None:
    world, hero, goblin = duel(creature(weapon=HANDAXE), FAR)
    hero.economy.grant(ActionCost.ACTION)
    Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert (hero.held, dict(hero.stowed), hero.dropped) == ((), {}, ())
    assert armed(offers(world, hero)) == []


def test_a_stack_of_handaxes_runs_out() -> None:
    """Held, and two carried: three throws, the next drawn with each."""
    world, hero, goblin = duel(creature(weapon=HANDAXE, carried={HANDAXE: 2}), FAR)
    hero.economy.grant(ActionCost.ACTION, 2)
    for carried in (1, 0):
        Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
        assert (hero.held, hero.stowed.get(HANDAXE, 0)) == ((HANDAXE,), carried)
    Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert hero.held == ()
    assert armed(offers(world, hero)) == []


def test_a_thrown_weapon_swung_in_reach_stays_in_hand() -> None:
    world, hero, goblin = duel(creature(weapon=HANDAXE))
    Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert hero.held == (HANDAXE,)


def test_a_carried_light_weapon_is_drawn_to_be_thrown_as_the_light_attack() -> None:
    """The Thrown property draws the weapon as part of the attack, so the
    Bonus Action attack needs no equip."""
    hero = creature(weapon=HANDAXE, carried={DAGGER: 1})
    world, hero, goblin = duel(hero, FAR)
    Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert armed(offers(world, hero)) == [("light_attack", "Dagger")]
    LightAttack(hero, goblin, weapon=DAGGER, world=world).execute(rng=ScriptedRng([1]))
    assert (hero.held, dict(hero.stowed)) == ((), {})


# --- Unconscious drops both; one is picked up at a time ---------------------


def test_unconscious_drops_both_and_each_pick_up_takes_back_one() -> None:
    hero = creature(weapon=SHORTSWORD, off_hand=DAGGER)
    world, hero, goblin = duel(hero)
    sleep = Unconscious(hero)
    hero.effects.apply(sleep)
    assert hero.dropped == ((SHORTSWORD, ORIGIN), (DAGGER, ORIGIN))
    hero.effects.remove(sleep)
    hero.economy.turn_started(hero.speed_ft)

    picks = [o for o in offers(world, hero) if isinstance(o, PickUpWeapon)]
    assert [pick.weapon for pick in picks] == [SHORTSWORD, DAGGER]
    picks[1].execute(rng=ScriptedRng([]))
    assert hero.held == (DAGGER,)
    assert [*hero.dropped] == [(SHORTSWORD, ORIGIN)]
    assert not any(isinstance(o, PickUpWeapon) for o in offers(world, hero))


def test_the_pick_up_takes_the_one_within_reach_of_two_alike() -> None:
    """Two Daggers dropped, one 20 feet back and one underfoot: the one
    underfoot is picked up."""
    hero = creature(weapon=DAGGER)
    world, hero, goblin = duel(hero)
    hero.drop_held()
    hero.position = FAR
    hero.off_hand = DAGGER
    hero.drop_held()
    pick = PickUpWeapon(hero, DAGGER)
    assert pick.is_legal()
    pick.execute(rng=ScriptedRng([]))
    assert hero.held == (DAGGER,)
    assert hero.dropped == ((DAGGER, ORIGIN),)


def test_a_vetoed_attack_spends_nothing_and_opens_no_light_attack() -> None:
    """Charmed by the goblin: the attack is refused at its declaration, so
    no arrow, no throw, no interaction, and no Light attack after it."""
    hero = creature(
        weapon=SHORTSWORD,
        carried={LONGBOW: 1, HANDAXE: 1},
        ammunition={"Arrow": 3},
    )
    world, hero, goblin = duel(hero, FAR)
    hero.effects.apply(Charmed(hero, caster=goblin))
    hero.economy.grant(ActionCost.ACTION, 2)
    for weapon in (LONGBOW, HANDAXE):
        attack = Attack(hero, goblin, weapon=weapon, world=world)
        assert attack.execute(rng=ScriptedRng([])) is PREVENTED
        assert hero.held == (SHORTSWORD,)
        assert dict(hero.stowed) == {LONGBOW: 1, HANDAXE: 1}
    assert hero.ammunition("Arrow") == 3
    assert hero.economy.can_interact()
    assert hero.economy.light_attack is None


def test_a_versatile_weapon_thrown_from_two_hands_draws_a_carried_one() -> None:
    """A Spear held in two hands rolls its 1d8; the three carried are the
    table's 1d6. A throw draws one of them."""
    spear = replace(
        HANDAXE,
        name="Spear",
        damage=Dice(1, 6),
        properties=frozenset({WeaponProperty.THROWN, WeaponProperty.VERSATILE}),
        versatile_damage=Dice(1, 8),
    )
    gripped = replace(spear, damage=Dice(1, 8))
    world, hero, goblin = duel(creature(weapon=gripped, carried={spear: 3}), FAR)
    Attack(hero, goblin, world=world).execute(rng=ScriptedRng([1]))
    assert hero.held == (spear,)
    assert hero.stowed[spear] == 2
