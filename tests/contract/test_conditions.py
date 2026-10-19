"""The condition contract: what each of the 15 glossary entries does to a roll.

Every number below is derived from the quoted Rules Glossary clause rather than
from `conditions/`, because the agent that writes an implementation does not
write its tests (`CLAUDE.md` § Working rules).

Both creatures in every case are built from one helper: a level-5 martial with
Strength 18 (+4), Dexterity 14 (+2), proficiency +3, a greatsword and AC 15. So
an attack swings at natural + 7 against 15, a hit deals 2d6 + 4, a Dexterity
save adds +2 and a Strength save adds +4.

Three clauses are driven by dispatching their hook on the creature and reading
the context the handlers were handed — the Initiative roll, the ability check
and the Long Rest — because no engine code rolls any of the three before `w2c`.
Everything else goes through `resolve_attack()`, `saving_throw()` or
`EffectStore.apply()`.

Sources: XPHB (2024) Rules Glossary, the 15 condition entries and "Advantage",
"Resistance", "Round Down"; `docs/decisions.md` § w1h; ROADMAP w1h.
"""

import tokenize
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest

from dndsim import core, engine, hooks
from dndsim.conditions.base import Condition, SourcedCondition
from dndsim.conditions.blinded import Blinded
from dndsim.conditions.charmed import Charmed
from dndsim.conditions.deafened import Deafened
from dndsim.conditions.exhaustion import Exhaustion
from dndsim.conditions.frightened import Frightened
from dndsim.conditions.grappled import Grappled
from dndsim.conditions.incapacitated import Incapacitated
from dndsim.conditions.invisible import Invisible
from dndsim.conditions.paralyzed import Paralyzed
from dndsim.conditions.petrified import Petrified
from dndsim.conditions.poisoned import Poisoned
from dndsim.conditions.prone import Prone
from dndsim.conditions.registry import CONDITIONS
from dndsim.conditions.restrained import Restrained
from dndsim.conditions.stunned import Stunned
from dndsim.conditions.unconscious import Unconscious
from dndsim.core.geometry import Cell
from dndsim.core.rng import ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.creatures.stats import GREATSWORD, Armour, StatBlock
from dndsim.engine.attack import PREVENTED, resolve_attack
from dndsim.engine.saves import saving_throw
from dndsim.hooks import contexts
from dndsim.hooks.contexts import (
    AbilityCheckContext,
    D20TestContext,
    InitiativeContext,
    RestContext,
)
from dndsim.hooks.definitions import (
    ON_ABILITY_CHECK,
    ON_INITIATIVE_ROLL,
    ON_LONG_REST,
)
from dndsim.hooks.registry import all_hooks
from dndsim.rules import enums
from dndsim.rules.enums import Ability, Outcome, SaveOutcome

SCORES: Final = MappingProxyType(
    {
        Ability.STRENGTH: 18,  # +4
        Ability.DEXTERITY: 14,  # +2
        Ability.CONSTITUTION: 10,
        Ability.INTELLIGENCE: 10,
        Ability.WISDOM: 10,
        Ability.CHARISMA: 10,
    }
)

AC: Final = 15
HP: Final = 40

HIT: Final = 10
"""A natural 10: 10 + 4 (Strength 18) + 3 (proficiency at level 5) = 17, which
clears AC 15 without being a 20."""

LOW: Final = 3
"""A natural 3: 3 + 7 = 10, short of AC 15 and not the automatic 1."""

HIGH: Final = 17
"""A natural 17: 17 + 7 = 24. Scripted beside `LOW`, the pair resolves to a hit
under advantage and a miss under disadvantage, and neither way is the number a
single die would have given."""

DAMAGE: Final = 13
"""2d6 rolled 5 + 4 = 9, plus the +4 Strength modifier."""

DEX: Final = Ability.DEXTERITY


def combatant(name: str, *, cell: Cell = Cell(0, 0)) -> Creature:
    """A level-5 martial with a greatsword, AC 15 and 40 hit points.

    `Armour.natural()` is the one legitimate way for an AC to be a number
    somebody typed (`creatures/stats.py`).
    """
    return Creature(
        name=name,
        stats=StatBlock(abilities=SCORES, max_hp=HP, level=5),
        position=cell,
        weapon=GREATSWORD,
        armour=Armour.natural(AC),
    )


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises if anything is rolled."""
    return ScriptedRng([])


def impose(victim: Creature, condition: Condition) -> Effect:
    """Apply `condition` and return what is in force, refusing a no-op."""
    worn = victim.effects.apply(condition)
    assert worn is not None
    return worn


def attached(victim: Creature) -> list[str]:
    return [effect.name for effect in victim.effects]


def roll_initiative(victim: Creature) -> InitiativeContext:
    """Offer the Initiative roll's hook and hand back what the handlers saw."""
    ctx = InitiativeContext(creature=victim)
    victim.dispatch(ON_INITIATIVE_ROLL, ctx)
    ctx.close()
    return ctx


def make_check(victim: Creature, ability: Ability = DEX) -> AbilityCheckContext:
    """The same, for an ability check."""
    ctx = AbilityCheckContext(creature=victim, ability=ability)
    victim.dispatch(ON_ABILITY_CHECK, ctx)
    ctx.close()
    return ctx


def long_rest(victim: Creature) -> None:
    ctx = RestContext(creature=victim, is_long_rest=True)
    victim.dispatch(ON_LONG_REST, ctx)
    ctx.close()


# --- The attack-roll swings -------------------------------------------------

DISADVANTAGES_ITS_OWN_ATTACKS: Final = [
    Blinded,  # "your attack rolls have Disadvantage"
    Poisoned,  # "You have Disadvantage on attack rolls and ability checks"
    Prone,  # "You have Disadvantage on attack rolls"
    Restrained,  # "your attack rolls have Disadvantage"
    Unconscious,  # "You have the Incapacitated and Prone conditions"
]

IS_ATTACKED_WITH_ADVANTAGE: Final = [
    Blinded,  # "Attack rolls against you have Advantage"
    Paralyzed,  # "Attack rolls against you have Advantage"
    Prone,  # "An attack roll against you has Advantage if the attacker is
    # within 5 feet of you" — the arm Prone.APPROXIMATED gives everyone
    Restrained,  # "Attack rolls against you have Advantage"
    Stunned,  # "Attack rolls against you have Advantage"
    Unconscious,  # "Attack rolls against you have Advantage"
]


@pytest.mark.parametrize("condition", DISADVANTAGES_ITS_OWN_ATTACKS)
def test_the_afflicted_creatures_own_attack_rolls_have_disadvantage(
    condition: type[Condition],
) -> None:
    """Scripted 17 then 3 resolves on the 3: 3 + 7 = 10, short of AC 15.

    A single die would take the 17 for 24 and hit, and the exhausted script is
    what says two d20s were drawn rather than one.
    """
    swinger, target = combatant("swinger"), combatant("target", cell=Cell(1, 0))
    impose(swinger, condition(swinger))
    rng = ScriptedRng([HIGH, LOW])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert target.hp == HP
    assert rng.exhausted


@pytest.mark.parametrize("condition", IS_ATTACKED_WITH_ADVANTAGE)
def test_an_attack_on_the_afflicted_creature_has_advantage(
    condition: type[Condition],
) -> None:
    """Scripted 3 then 17 resolves on the 17: 17 + 7 = 24 against AC 15 hits,
    where the 3 alone would total 10 and miss. 2d6 (5 + 4 = 9) + 4 = 13."""
    swinger, target = combatant("swinger"), combatant("target", cell=Cell(1, 0))
    impose(target, condition(target))
    rng = ScriptedRng([LOW, HIGH, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == DAMAGE
    assert target.hp == HP - DAMAGE
    assert rng.exhausted


def test_an_invisible_creatures_own_attack_rolls_have_advantage() -> None:
    """"your attack rolls have Advantage": scripted 3 then 17 resolves on the
    17 for 24 against AC 15, and the hit deals 2d6 (5 + 4) + 4 = 13."""
    swinger, target = combatant("unseen"), combatant("target", cell=Cell(1, 0))
    impose(swinger, Invisible(swinger))
    rng = ScriptedRng([LOW, HIGH, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == DAMAGE
    assert rng.exhausted


def test_an_attack_on_an_invisible_creature_has_disadvantage() -> None:
    """The other half of the same sentence: scripted 17 then 3 resolves on the
    3 for 3 + 7 = 10 against AC 15."""
    swinger, target = combatant("swinger"), combatant("unseen", cell=Cell(1, 0))
    impose(target, Invisible(target))
    rng = ScriptedRng([HIGH, LOW])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert target.hp == HP
    assert rng.exhausted


def test_a_petrified_creature_is_attacked_with_advantage_and_resists_it() -> None:
    """Both of Petrified's roll-facing clauses on one attack.

    "Attack rolls against you have Advantage": scripted 3 then 17 resolves on
    the 17 for 24 against AC 15. "You have Resistance to all damage": the
    2d6 (5 + 4) + 4 = 13 slashing is halved to 13 // 2 = 6 (XPHB, "Round
    Down"), so the statue loses 6 where an unresisting target loses 13.
    """
    swinger, statue = combatant("swinger"), combatant("statue", cell=Cell(1, 0))
    impose(statue, Petrified(statue))
    rng = ScriptedRng([LOW, HIGH, 5, 4])

    outcome, damage = resolve_attack(swinger, statue, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 6
    assert statue.hp == HP - 6
    assert rng.exhausted


def test_a_frightened_creature_attacks_with_disadvantage() -> None:
    """"You have Disadvantage on ability checks and attack rolls while the
    source of fear is within line of sight." Scripted 17 then 3 resolves on
    the 3: 3 + 7 = 10 against AC 15."""
    victim, dragon = combatant("victim"), combatant("dragon", cell=Cell(1, 0))
    impose(victim, Frightened(victim, caster=dragon))
    rng = ScriptedRng([HIGH, LOW])

    outcome, damage = resolve_attack(victim, dragon, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert rng.exhausted


# --- The two conditions written about the creature that imposed them --------


def test_a_grappled_creature_attacks_anyone_but_the_grappler_with_disadvantage(
) -> None:
    """"You have Disadvantage on attack rolls against any target other than the
    grappler." Scripted 17 then 3 resolves on the 3 for 10 against AC 15."""
    victim = combatant("victim")
    grappler = combatant("grappler", cell=Cell(1, 0))
    bystander = combatant("bystander", cell=Cell(0, 1))
    impose(victim, Grappled(victim, caster=grappler))
    rng = ScriptedRng([HIGH, LOW])

    outcome, damage = resolve_attack(victim, bystander, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert bystander.hp == HP
    assert rng.exhausted


def test_a_grappled_creature_attacks_the_grappler_with_no_disadvantage() -> None:
    """The exception the same sentence names. One d20, not two: the scripted 17
    is the roll, for 17 + 7 = 24 against AC 15, and 2d6 (5 + 4) + 4 = 13."""
    victim = combatant("victim")
    grappler = combatant("grappler", cell=Cell(1, 0))
    impose(victim, Grappled(victim, caster=grappler))
    rng = ScriptedRng([HIGH, 5, 4])

    outcome, damage = resolve_attack(victim, grappler, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == DAMAGE
    assert grappler.hp == HP - DAMAGE
    assert rng.exhausted


def test_a_charmed_creature_cannot_attack_the_charmer() -> None:
    """"You can't attack the charmer." Not a miss — an attack that does not
    happen, so `outcome` is None and no d20 is drawn (the empty script raises
    if one is)."""
    victim = combatant("victim")
    charmer = combatant("charmer", cell=Cell(1, 0))
    impose(victim, Charmed(victim, caster=charmer))

    result = resolve_attack(victim, charmer, rng=no_dice())

    assert result is PREVENTED
    assert result.outcome is None
    assert result.damage_dealt == 0
    assert charmer.hp == HP


def test_a_charmed_creature_may_attack_anybody_else() -> None:
    """The clause names the charmer and nobody else: the same victim swings at
    a stranger for natural 10 + 7 = 17 against AC 15 and 2d6 (5 + 4) + 4 = 13,
    on one undisturbed d20."""
    victim = combatant("victim")
    charmer = combatant("charmer", cell=Cell(1, 0))
    stranger = combatant("stranger", cell=Cell(0, 1))
    impose(victim, Charmed(victim, caster=charmer))
    rng = ScriptedRng([HIT, 5, 4])

    outcome, damage = resolve_attack(victim, stranger, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == DAMAGE
    assert stranger.hp == HP - DAMAGE
    assert rng.exhausted


# --- Saving throws ----------------------------------------------------------


def test_a_restrained_creatures_dexterity_saves_have_disadvantage() -> None:
    """"You have Disadvantage on Dexterity saving throws." Scripted 7 then 18
    resolves on the 7: 7 + 2 (Dexterity 14) = 9, which fails DC 11."""
    victim = combatant("victim")
    impose(victim, Restrained(victim))
    rng = ScriptedRng([7, 18])

    result = saving_throw(victim, ability=DEX, dc=11, rng=rng)

    assert result.total == 9
    assert result.outcome is SaveOutcome.FAILURE
    assert rng.exhausted


def test_a_restrained_creatures_other_saves_are_untouched() -> None:
    """The clause names one ability. The same creature's Strength save draws
    one d20 — the scripted 18 is never reached — for 7 + 4 (Strength 18) = 11,
    which makes DC 11 where the Dexterity save above failed it."""
    victim = combatant("victim")
    impose(victim, Restrained(victim))
    rng = ScriptedRng([7, 18])

    result = saving_throw(victim, ability=Ability.STRENGTH, dc=11, rng=rng)

    assert result.total == 11
    assert result.outcome is SaveOutcome.SUCCESS
    assert rng.remaining == 1


# --- Ability checks ---------------------------------------------------------


def test_a_poisoned_creatures_ability_checks_have_disadvantage() -> None:
    """The check half of "You have Disadvantage on attack rolls and ability
    checks"."""
    victim = combatant("victim")
    impose(victim, Poisoned(victim))

    ctx = make_check(victim)

    assert ctx.had_disadvantage
    assert ctx.disadvantage_sources == ("poisoned: poisoned",)


def test_a_frightened_creatures_ability_checks_have_disadvantage() -> None:
    """The check half of "You have Disadvantage on ability checks and attack
    rolls while the source of fear is within line of sight"."""
    victim, dragon = combatant("victim"), combatant("dragon", cell=Cell(1, 0))
    impose(victim, Frightened(victim, caster=dragon))

    ctx = make_check(victim)

    assert ctx.had_disadvantage
    assert ctx.disadvantage_sources == ("frightened: frightened",)


# --- The Initiative roll ----------------------------------------------------

SURPRISED_ON_INITIATIVE: Final = [
    Incapacitated,  # "If you're Incapacitated when you roll Initiative, you
    # have Disadvantage on the roll"
    Paralyzed,  # each of the four below says "You have the Incapacitated
    Petrified,  # condition", so the clause above is theirs too
    Stunned,
    Unconscious,
]


@pytest.mark.parametrize("condition", SURPRISED_ON_INITIATIVE)
def test_an_incapacitated_creature_rolls_initiative_with_disadvantage(
    condition: type[Condition],
) -> None:
    """The behavioural half of the inheritance: a composed condition is handed
    its base's handler, so all four of Incapacitated's subclasses are surprised
    without declaring anything."""
    victim = combatant("victim")
    impose(victim, condition(victim))

    ctx = roll_initiative(victim)

    assert ctx.had_disadvantage
    assert not ctx.had_advantage


def test_an_invisible_creature_rolls_initiative_with_advantage() -> None:
    """"If you're Invisible when you roll Initiative, you have Advantage on the
    roll"."""
    victim = combatant("victim")
    impose(victim, Invisible(victim))

    ctx = roll_initiative(victim)

    assert ctx.had_advantage
    assert ctx.advantage_sources == ("invisible: invisible",)


# --- Exhaustion -------------------------------------------------------------


def test_exhaustion_is_the_exception_and_a_second_application_is_a_level(
) -> None:
    """"This condition is cumulative. Each time you receive it, you gain 1
    Exhaustion level" — the glossary's declared exception to "A condition
    doesn't stack with itself".

    Three applications are one condition at level 3, which the roll is what
    proves: 13 + 2 (Dexterity 14) - 2 * 3 = 9. A second condition beside the
    first would be two reductions of some other size, and a refusal to
    accumulate would leave 13 + 2 - 2 = 13.
    """
    weary = combatant("weary")
    first = impose(weary, Exhaustion(weary))

    assert weary.effects.apply(Exhaustion(weary)) is None
    assert weary.effects.apply(Exhaustion(weary)) is None
    assert list(weary.effects) == [first]

    result = saving_throw(weary, ability=DEX, dc=11, rng=ScriptedRng([13]))
    assert result.total == 9


@pytest.mark.parametrize(("levels", "total"), [(1, 13), (2, 11), (3, 9)])
def test_a_d20_test_is_reduced_by_two_times_the_exhaustion_level(
    levels: int, total: int
) -> None:
    """"When you make a D20 Test, the roll is reduced by 2 times your
    Exhaustion level": 13 + 2 (Dexterity 14) - 2 * level, so 13, 11 and 9.

    DC 13 is cleared at one level and missed at two, which is the clause doing
    something rather than being carried.
    """
    weary = combatant("weary")
    for _ in range(levels):
        weary.effects.apply(Exhaustion(weary))

    result = saving_throw(weary, ability=DEX, dc=13, rng=ScriptedRng([13]))

    assert result.total == total
    assert result.outcome is (
        SaveOutcome.SUCCESS if total >= 13 else SaveOutcome.FAILURE
    )


def test_exhaustion_reduces_the_attack_roll_too() -> None:
    """An attack roll is a D20 Test: natural 10 + 7 - 2 = 15 meets AC 15 at one
    level, and 10 + 7 - 4 = 14 misses it at two. 2d6 (5 + 4) + 4 = 13 — the
    reduction is on the roll and not on the damage."""
    swinger, target = combatant("weary"), combatant("target", cell=Cell(1, 0))
    impose(swinger, Exhaustion(swinger))

    first = resolve_attack(swinger, target, rng=ScriptedRng([HIT, 5, 4]))
    assert first == (Outcome.HIT, DAMAGE)

    swinger.effects.apply(Exhaustion(swinger))
    second = resolve_attack(swinger, target, rng=ScriptedRng([HIT]))
    assert second == (Outcome.MISS, 0)


def test_exhaustion_reaches_every_d20_test_the_registry_defines() -> None:
    """The clause is about D20 Tests and not about a list of six rolls.

    Which hooks those are is the registry's answer: a hook that hands out a
    `D20TestContext` is a D20 Test. The one such hook that is not the *owner's*
    test is the defender's end of an attack declaration — the roll there is the
    attacker's, and reducing it would exhaust the wrong creature.
    """
    d20_tests = {
        spec.name
        for spec in all_hooks()
        if issubclass(getattr(contexts, spec.context), D20TestContext)
    }
    somebody_elses = {"on_incoming_attack_declared"}
    declared = {handler.spec.name for handler in Exhaustion.HANDLERS}

    assert d20_tests - somebody_elses <= declared
    assert declared & somebody_elses == set()


def test_a_long_rest_removes_one_exhaustion_level() -> None:
    """"Finishing a Long Rest removes 1 of your Exhaustion levels." From level
    2 that leaves level 1, so the save the level-2 case failed now reads
    13 + 2 - 2 = 13 and clears DC 13."""
    weary = combatant("weary")
    weary.effects.apply(Exhaustion(weary))
    weary.effects.apply(Exhaustion(weary))

    long_rest(weary)

    assert attached(weary) == ["exhaustion"]
    result = saving_throw(weary, ability=DEX, dc=13, rng=ScriptedRng([13]))
    assert result.total == 13


def test_the_condition_ends_when_its_last_level_is_removed() -> None:
    """"When your Exhaustion level reaches 0, the condition ends." Nothing is
    attached and nothing is reduced: 13 + 2 = 15."""
    weary = combatant("weary")
    weary.effects.apply(Exhaustion(weary))

    long_rest(weary)

    assert attached(weary) == []
    result = saving_throw(weary, ability=DEX, dc=13, rng=ScriptedRng([13]))
    assert result.total == 15


# --- Immunity, and what one condition leaves behind -------------------------


def test_a_petrified_creature_cannot_be_poisoned() -> None:
    """"You have Immunity to the Poisoned condition": the application lands
    nothing, so the statue's attack roll is not given the Disadvantage the
    Poisoned condition would have imposed — natural 10 + 7 = 17 on one d20
    hits AC 15 for 2d6 (5 + 4) + 4 = 13."""
    statue, target = combatant("statue"), combatant("target", cell=Cell(1, 0))
    impose(statue, Petrified(statue))

    assert statue.effects.apply(Poisoned(statue)) is None
    assert attached(statue) == ["petrified"]

    rng = ScriptedRng([HIT, 5, 4])
    assert resolve_attack(statue, target, rng=rng) == (Outcome.HIT, DAMAGE)
    assert rng.exhausted


def test_the_same_poison_lands_on_a_creature_that_is_not_petrified() -> None:
    """The control for the case above, which a store that refused every
    Poisoned condition would also pass."""
    victim, target = combatant("victim"), combatant("target", cell=Cell(1, 0))

    assert victim.effects.apply(Poisoned(victim)) is not None
    assert attached(victim) == ["poisoned"]

    rng = ScriptedRng([HIGH, LOW])
    assert resolve_attack(victim, target, rng=rng) == (Outcome.MISS, 0)
    assert rng.exhausted


def test_unconscious_leaves_prone_attached_when_it_ends() -> None:
    """"When this condition ends, you remain Prone." Unconscious is gone and
    Prone is on, so the creature's own attacks still have Disadvantage:
    scripted 17 then 3 resolves on the 3 for 3 + 7 = 10 against AC 15."""
    sleeper, target = combatant("sleeper"), combatant("target", cell=Cell(1, 0))
    worn = impose(sleeper, Unconscious(sleeper))

    sleeper.effects.remove(worn)

    assert attached(sleeper) == ["prone"]
    rng = ScriptedRng([HIGH, LOW])
    assert resolve_attack(sleeper, target, rng=rng) == (Outcome.MISS, 0)
    assert rng.exhausted


def test_deafened_changes_nothing_a_combat_round_can_see() -> None:
    """Deafened's one clause is an ability check that requires hearing, and
    nothing in a combat round is decided by hearing — so the condition carries
    no handler and declares the gap instead.

    The attack is the behavioural half: one d20, natural 10 + 7 = 17 against
    AC 15, and 2d6 (5 + 4) + 4 = 13.
    """
    victim, target = combatant("victim"), combatant("target", cell=Cell(1, 0))
    impose(victim, Deafened(victim))

    assert Deafened.HANDLERS == ()
    assert len(Deafened.NEEDS_HOOK) == 1

    rng = ScriptedRng([HIT, 5, 4])
    assert resolve_attack(victim, target, rng=rng) == (Outcome.HIT, DAMAGE)
    assert rng.exhausted
    assert not make_check(victim).had_disadvantage


# --- The structural properties ----------------------------------------------


def test_every_condition_name_has_exactly_one_implementation() -> None:
    """The glossary "defines these conditions" and names fifteen; `CONDITIONS`
    is the one place a name is tied to the behaviour."""
    assert set(CONDITIONS) == set(enums.Condition)
    assert len(CONDITIONS) == 15
    assert len(set(CONDITIONS.values())) == 15
    assert all(cls.CONDITION is name for name, cls in CONDITIONS.items())


@pytest.mark.parametrize("condition", [Charmed, Frightened, Grappled])
def test_a_condition_written_about_its_imposer_refuses_to_arrive_without_one(
    condition: type[SourcedCondition],
) -> None:
    """The charmer, the source of fear and the grappler are each named by every
    clause their condition has that does anything, so a `None` would be a
    condition that silently does nothing."""
    victim = combatant("victim")

    with pytest.raises(ValueError, match="no caster"):
        condition(victim)


@pytest.mark.parametrize(
    "condition",
    [
        Blinded,
        Deafened,
        Incapacitated,
        Invisible,
        Paralyzed,
        Petrified,
        Poisoned,
        Prone,
        Restrained,
        Stunned,
        Unconscious,
    ],
)
def test_a_second_application_of_a_condition_does_not_stack(
    condition: type[Condition],
) -> None:
    """"A condition doesn't stack with itself; a recipient either has a
    condition or doesn't" — so the second application is absorbed by the first
    and the creature holds one."""
    victim = combatant("victim")
    first = impose(victim, condition(victim))

    assert victim.effects.apply(condition(victim)) is first
    assert list(victim.effects) == [first]


def test_a_composed_condition_inherits_its_bases_handlers() -> None:
    """Paralyzed, Petrified, Stunned and Unconscious each say "You have the
    Incapacitated condition", and Unconscious also has Prone's — so each holds
    every hook its bases declared and not only its own."""
    incapacitated = {handler.spec.name for handler in Incapacitated.HANDLERS}
    prone = {handler.spec.name for handler in Prone.HANDLERS}

    for composed in (Paralyzed, Petrified, Stunned, Unconscious):
        assert incapacitated <= {h.spec.name for h in composed.HANDLERS}
    assert prone <= {handler.spec.name for handler in Unconscious.HANDLERS}


def test_a_composed_condition_inherits_its_bases_declared_gaps() -> None:
    """A clause its base could not express is still unexpressed in the
    subclass, so the gap is inherited rather than shadowed by the subclass's
    own declaration — otherwise `w3f`'s collection would report a composed
    condition as whole."""
    for composed in (Paralyzed, Petrified, Stunned, Unconscious):
        assert set(Incapacitated.NEEDS_HOOK) <= set(composed.NEEDS_HOOK)
    assert set(Prone.NEEDS_HOOK) <= set(Unconscious.NEEDS_HOOK)
    assert set(Prone.APPROXIMATED) <= set(Unconscious.APPROXIMATED)
    assert Incapacitated.NEEDS_HOOK and Paralyzed.NEEDS_HOOK != (
        Incapacitated.NEEDS_HOOK
    )


# --- The ROADMAP row's acceptance test --------------------------------------

CONDITION_WORDS: Final = frozenset(member.value for member in enums.Condition)


def _identifiers(path: Path) -> set[str]:
    """Every identifier in `path`, lowercased, with comments and strings left
    out: the claim below is about code, not about the prose beside it."""
    with path.open("rb") as handle:
        return {
            token.string.lower()
            for token in tokenize.tokenize(handle.readline)
            if token.type == tokenize.NAME
        }


def test_no_condition_is_named_anywhere_in_the_engine() -> None:
    """The acceptance test the ROADMAP row names — "no `if creature.is_prone`
    anywhere in the engine".

    A condition is behaviour attached to a creature, so every one of the
    fifteen is a `HookSource` the engine dispatches to and none of them is a
    flag it branches on. The check is per identifier rather than per line
    because `is_prone` and `Condition.PRONE` are both the branch this forbids,
    and a docstring that mentions Prone is not.
    """
    roots = (engine.__file__, hooks.__file__, core.__file__)
    named = {
        (path.name, identifier)
        for root in roots
        for path in sorted(Path(root).parent.glob("*.py"))
        for identifier in _identifiers(path)
        for word in CONDITION_WORDS
        if word in identifier
    }

    assert named == set()
