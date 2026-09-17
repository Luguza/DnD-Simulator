"""The attack contract: the one order an attack resolves in, and what each step
is allowed to change.

**declare -> roll -> advantage -> hit/crit/miss -> damage -> resolved.** Every
case carries the arithmetic that produced its number — the attacker below swings
at natural + 4 (Strength 18) + 3 (proficiency at level 5) — because a bare
`== 13` proves nothing about which of the modifier, the die and the defence
produced it.

Sources: XPHB (2024) "D20 Tests" -> Attack Rolls ("Rolling 20 or 1", Critical
Hits), "Equipment" -> Weapon Properties (Finesse, Reach), Rules Glossary
"Advantage", "Resistance"; DESIGN.md § Structural prevention over testing;
`docs/decisions.md` § w1e; ROADMAP w1f.
"""

from collections.abc import Sequence
from types import MappingProxyType
from typing import Final

import pytest

from dndsim.core.dice import Dice
from dndsim.core.errors import EngineAssemblyError, FeatureContractError
from dndsim.core.geometry import Cell
from dndsim.core.rng import MersenneRng, Rng, ScriptedRng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import Effect
from dndsim.creatures.stats import GREATSWORD, Armour, StatBlock, Weapon
from dndsim.engine.attack import (
    FRESH,
    PREVENTED,
    AttackContexts,
    AttackResult,
    PooledContexts,
    attack_ability,
    resolve_attack,
    within_reach,
)
from dndsim.hooks.contexts import (
    AttackDeclaredContext,
    AttackResolvedContext,
    AttackRollContext,
    DamageRollContext,
    DamageTakenContext,
    HitContext,
    IncomingDamageContext,
    MissContext,
)
from dndsim.hooks.decorator import R, hook
from dndsim.hooks.registry import Phase
from dndsim.hooks.source import HookSource
from dndsim.rules.enums import Ability, DamageType, Mastery, Outcome, WeaponProperty

# --- The two creatures every case below is derived from ---------------------

_STRONG: Final = MappingProxyType(
    {
        Ability.STRENGTH: 18,  # +4
        Ability.DEXTERITY: 14,  # +2
        Ability.CONSTITUTION: 10,
        Ability.INTELLIGENCE: 10,
        Ability.WISDOM: 10,
        Ability.CHARISMA: 10,
    }
)
_NIMBLE: Final = MappingProxyType(
    {
        Ability.STRENGTH: 14,  # +2
        Ability.DEXTERITY: 18,  # +4
        Ability.CONSTITUTION: 10,
        Ability.INTELLIGENCE: 10,
        Ability.WISDOM: 10,
        Ability.CHARISMA: 10,
    }
)

_STR_MOD: Final = 4
"""(18 - 10) // 2, XPHB "Ability Scores and Modifiers". With the +3 proficiency
bonus of level 5 that is the +7 every total below is derived from."""

_DEFENDER_AC: Final = 15
_DEFENDER_HP: Final = 40

_HIT: Final = 10
"""A natural 10: 10 + 7 = 17, which clears AC 15 without being a 20."""
_MISS: Final = 3
"""A natural 3: 3 + 7 = 10, short of AC 15 and not the automatic 1."""

RAPIER: Final = Weapon(
    name="Rapier",
    damage=Dice(1, 8),
    damage_type=DamageType.PIERCING,
    properties=frozenset({WeaponProperty.FINESSE}),
    mastery=Mastery.VEX,
)
"""XPHB "Weapons": 1d8 piercing, Finesse. Hand-written like `GREATSWORD`,
because `content/` (w2a) is what imports the other 36."""

GLAIVE: Final = Weapon(
    name="Glaive",
    damage=Dice(1, 10),
    damage_type=DamageType.SLASHING,
    properties=frozenset(
        {WeaponProperty.HEAVY, WeaponProperty.REACH, WeaponProperty.TWO_HANDED}
    ),
    mastery=Mastery.GRAZE,
    reach_ft=10,
)
"""XPHB "Weapons": 1d10 slashing, Heavy, Reach, Two-Handed — 10 feet of reach,
which is the only thing this file swings it for."""


def attacker(
    *,
    scores: MappingProxyType[Ability, int] = _STRONG,
    weapon: Weapon | None = GREATSWORD,
) -> Creature:
    """A level-5 martial at the origin: +4 Strength, +3 proficiency."""
    return Creature(
        name="Fighter",
        stats=StatBlock(abilities=scores, max_hp=44, level=5),
        position=Cell(0, 0),
        weapon=weapon,
    )


def defender(*, ac: int = _DEFENDER_AC, cells: int = 1) -> Creature:
    """A dummy `cells` squares east of the attacker, with a stated AC.

    `Armour.natural()` is the one legitimate way for an AC to be a number
    somebody typed (`creatures/stats.py`): for a monster the number is what the
    rules publish.
    """
    return Creature(
        name="Dummy",
        stats=StatBlock(
            abilities=MappingProxyType(dict.fromkeys(Ability, 10)), max_hp=_DEFENDER_HP
        ),
        position=Cell(cells, 0),
        armour=Armour.natural(ac),
    )


def no_dice() -> ScriptedRng:
    """A generator scripted with nothing, which raises the moment anything is
    rolled — how a case says "no d20 was drawn"."""
    return ScriptedRng([])


# --- The probes and features the cases attach -------------------------------


class AttackerProbe(HookSource):
    """Every SELF-scoped attack hook, recorded in dispatch order."""

    __slots__ = ("seen",)

    ID = "test.attacker_probe"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_declare_attack")
    def declare(self, ctx: AttackDeclaredContext) -> R:
        self.seen.append("on_declare_attack")
        return R.SKIPPED

    @hook("on_attack_rolled")
    def rolled(self, ctx: AttackRollContext) -> R:
        self.seen.append("on_attack_rolled")
        return R.SKIPPED

    @hook("on_hit")
    def hit(self, ctx: HitContext) -> R:
        self.seen.append("on_hit")
        return R.SKIPPED

    @hook("on_miss")
    def miss(self, ctx: MissContext) -> R:
        self.seen.append("on_miss")
        return R.SKIPPED

    @hook("on_damage_roll")
    def damage_roll(self, ctx: DamageRollContext) -> R:
        self.seen.append("on_damage_roll")
        return R.SKIPPED

    @hook("on_attack_resolved")
    def resolved(self, ctx: AttackResolvedContext) -> R:
        self.seen.append("on_attack_resolved")
        return R.SKIPPED


class DefenderProbe(HookSource):
    """Every TARGET-scoped attack and damage hook, in dispatch order."""

    __slots__ = ("seen",)

    ID = "test.defender_probe"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[str] = []

    @hook("on_incoming_attack_declared")
    def declared(self, ctx: AttackDeclaredContext) -> R:
        self.seen.append("on_incoming_attack_declared")
        return R.SKIPPED

    @hook("on_hit_taken")
    def hit_taken(self, ctx: HitContext) -> R:
        self.seen.append("on_hit_taken")
        return R.SKIPPED

    @hook("on_incoming_damage")
    def incoming(self, ctx: IncomingDamageContext) -> R:
        self.seen.append("on_incoming_damage")
        return R.SKIPPED

    @hook("on_damage_taken")
    def taken(self, ctx: DamageTakenContext) -> R:
        self.seen.append("on_damage_taken")
        return R.SKIPPED


class Rider(HookSource):
    """An extra die on a hit — Sneak Attack's shape, and what the defender's
    `on_hit_taken` handler should be able to see."""

    __slots__ = ("seen",)

    ID = "test.rider"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[HitContext] = []

    @hook("on_hit")
    def ride(self, ctx: HitContext) -> R:
        self.seen.append(ctx)
        ctx.damage.add(dice="1d6")
        return R.APPLIED


class Witness(HookSource):
    """The victim's end of the paired hook: which context it was handed, and
    what was already in the bag when it arrived."""

    __slots__ = ("seen", "sources")

    ID = "test.witness"

    def __init__(self, owner: Creature) -> None:
        super().__init__(owner)
        self.seen: list[HitContext] = []
        self.sources: list[tuple[str, ...]] = []

    @hook("on_hit_taken")
    def watch(self, ctx: HitContext) -> R:
        self.seen.append(ctx)
        self.sources.append(tuple(part.source for part in ctx.damage.parts))
        return R.SKIPPED


class Reckless(HookSource):
    """Advantage granted by the attacker — Reckless Attack's shape."""

    __slots__ = ()

    ID = "test.reckless"

    @hook("on_declare_attack")
    def grant(self, ctx: AttackDeclaredContext) -> R:
        ctx.grant_advantage("reckless")
        return R.APPLIED


class Restrained(HookSource):
    """Advantage granted from the defender's side — the Restrained condition.

    The same roll, swung by the other creature's handler, which is what makes a
    second grant a real second source rather than the same feature twice.
    """

    __slots__ = ()

    ID = "test.restrained"

    @hook("on_incoming_attack_declared")
    def grant(self, ctx: AttackDeclaredContext) -> R:
        ctx.grant_advantage("restrained")
        return R.APPLIED


class Dodging(HookSource):
    """Disadvantage imposed by the defender — the Dodge action's shape."""

    __slots__ = ()

    ID = "test.dodging"

    @hook("on_incoming_attack_declared")
    def impose(self, ctx: AttackDeclaredContext) -> R:
        ctx.impose_disadvantage("dodge")
        return R.APPLIED


class Blessed(HookSource):
    """A bonus that carries dice: Bless's `1d4` on the attack roll."""

    __slots__ = ()

    ID = "test.blessed"

    @hook("on_declare_attack")
    def bless(self, ctx: AttackDeclaredContext) -> R:
        ctx.bonus.add(dice="1d4")
        return R.APPLIED


class Shielded(HookSource):
    """The Shield spell: +5 AC against this attack, on the defender's side."""

    __slots__ = ()

    ID = "test.shielded"

    @hook("on_incoming_attack_declared")
    def shield(self, ctx: AttackDeclaredContext) -> R:
        ctx.ac_bonus.add(flat=5)
        return R.APPLIED


class SlashResistant(HookSource):
    """Slashing resistance, the shape Rage has against a weapon."""

    __slots__ = ()

    ID = "test.slashing_resistance"

    @hook("on_incoming_damage")
    def resist(self, ctx: IncomingDamageContext) -> R:
        ctx.add_resistance(DamageType.SLASHING)
        return R.APPLIED


class Reducer(HookSource):
    """A reduction that rolls: `ctx.reduce(dice="1d4")`, whose die must come out
    of the attack's own generator (`engine/damage.py`)."""

    __slots__ = ()

    ID = "test.reducer"

    @hook("on_incoming_damage")
    def reduce(self, ctx: IncomingDamageContext) -> R:
        ctx.reduce(dice="1d4")
        return R.APPLIED


class Graze(HookSource):
    """The Graze mastery: the ability modifier as damage on a miss.

    The +4 is the attacker's Strength modifier, written as a literal because a
    handler is handed a `Combatant` and the mastery feature that reads it for
    real is W4's.
    """

    __slots__ = ()

    ID = "test.graze"

    @hook("on_miss")
    def graze(self, ctx: MissContext) -> R:
        ctx.damage.add(flat=_STR_MOD)
        return R.APPLIED


class VetoDeclaration(HookSource):
    """An EARLY gate on the attacker's own declaration."""

    __slots__ = ()

    ID = "test.veto_declaration"

    @hook("on_declare_attack", phase=Phase.EARLY)
    def stop(self, ctx: AttackDeclaredContext) -> R:
        ctx.veto("the attacker cannot attack")
        return R.APPLIED


class VetoIncoming(HookSource):
    """An EARLY gate on the defender's side of the declaration."""

    __slots__ = ()

    ID = "test.veto_incoming"

    @hook("on_incoming_attack_declared", phase=Phase.EARLY)
    def stop(self, ctx: AttackDeclaredContext) -> R:
        ctx.veto("the defender cannot be attacked")
        return R.APPLIED


class VetoRoll(HookSource):
    """A veto one stage too late: the d20 is already on the table."""

    __slots__ = ()

    ID = "test.veto_roll"

    @hook("on_attack_rolled", phase=Phase.EARLY)
    def stop(self, ctx: AttackRollContext) -> R:
        ctx.veto("too late")
        return R.APPLIED


class VetoHit(HookSource):
    """A veto after the attack has landed, from the attacker's end."""

    __slots__ = ()

    ID = "test.veto_hit"

    @hook("on_hit", phase=Phase.EARLY)
    def stop(self, ctx: HitContext) -> R:
        ctx.veto("too late")
        return R.APPLIED


class VetoHitTaken(HookSource):
    """The same veto from the victim's end of the same context."""

    __slots__ = ()

    ID = "test.veto_hit_taken"

    @hook("on_hit_taken", phase=Phase.EARLY)
    def stop(self, ctx: HitContext) -> R:
        ctx.veto("too late")
        return R.APPLIED


class QueuesAtDeclaration(HookSource):
    """A handler that applies an effect before the roll."""

    __slots__ = ()

    ID = "test.queues_at_declaration"

    @hook("on_declare_attack")
    def queue(self, ctx: AttackDeclaredContext) -> R:
        ctx.apply_effect(ctx.defender, Effect(ctx.defender))
        return R.APPLIED


class QueuesOnHit(HookSource):
    """The same mistake on the busiest hook in the project."""

    __slots__ = ()

    ID = "test.queues_on_hit"

    @hook("on_hit")
    def queue(self, ctx: HitContext) -> R:
        ctx.apply_effect(ctx.defender, Effect(ctx.defender))
        return R.APPLIED


class Interrupts(HookSource):
    """An attack begun from inside another attack's first hook.

    `w2c`'s reaction is exactly this shape — a second attack made during
    somebody else's — which is why the pooled source refuses it and the
    allocating one does not.
    """

    __slots__ = ("_contexts", "_defender", "_rng", "_swinger", "fired", "result")

    ID = "test.interrupts"

    def __init__(
        self,
        owner: Creature,
        *,
        defender: Creature,
        rng: Rng,
        contexts: AttackContexts,
    ) -> None:
        super().__init__(owner)
        self._swinger = owner
        self._defender = defender
        self._rng = rng
        self._contexts = contexts
        self.fired = False
        self.result: AttackResult | None = None

    @hook("on_declare_attack")
    def interrupt(self, ctx: AttackDeclaredContext) -> R:
        if self.fired:
            return R.SKIPPED
        self.fired = True
        self.result = resolve_attack(
            self._swinger,
            self._defender,
            rng=self._rng,
            contexts=self._contexts,
        )
        return R.APPLIED


class RecordingTrigger:
    """The APL's `trigger.*` root, recording what it was told and when."""

    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls: list[tuple[bool, int]] = []

    def bind(self, *, crit: bool = False, damage_estimate: int = 0) -> None:
        self.calls.append((crit, damage_estimate))


# --- The dispatch order -----------------------------------------------------


def test_a_hit_dispatches_every_hook_the_hit_side_touches() -> None:
    """The nine dispatches of one landed attack, in the order the module's
    docstring claims: the attacker's five and the defender's four.

    Natural 10 + 4 + 3 = 17 against AC 15 is a hit; 2d6 rolled 5 + 4 = 9 plus
    the +4 Strength modifier is 13, and an unarmoured dummy keeps all of it.
    """
    swinger, target = attacker(), defender()
    attacker_probe, defender_probe = AttackerProbe(swinger), DefenderProbe(target)
    swinger.attach(attacker_probe)
    target.attach(defender_probe)
    rng = ScriptedRng([_HIT, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert target.hp == _DEFENDER_HP - 13
    assert attacker_probe.seen == [
        "on_declare_attack",
        "on_attack_rolled",
        "on_hit",
        "on_damage_roll",
        "on_attack_resolved",
    ]
    assert defender_probe.seen == [
        "on_incoming_attack_declared",
        "on_hit_taken",
        "on_incoming_damage",
        "on_damage_taken",
    ]
    assert rng.exhausted


def test_a_miss_with_an_empty_bag_touches_no_damage_hook() -> None:
    """Natural 3 + 7 = 10 against AC 15. Nothing was contributed on the miss
    path, so no damage event happens at all — the victim's `on_incoming_damage`
    is not offered a zero to mitigate."""
    swinger, target = attacker(), defender()
    attacker_probe, defender_probe = AttackerProbe(swinger), DefenderProbe(target)
    swinger.attach(attacker_probe)
    target.attach(defender_probe)
    rng = ScriptedRng([_MISS])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert target.hp == _DEFENDER_HP
    assert attacker_probe.seen == [
        "on_declare_attack",
        "on_attack_rolled",
        "on_miss",
        "on_attack_resolved",
    ]
    assert defender_probe.seen == ["on_incoming_attack_declared"]
    assert rng.exhausted


def test_the_paired_hit_hooks_share_one_context() -> None:
    """`on_hit` and `on_hit_taken` are one event seen from two ends, so the
    defender's handler is handed the *same* object — and therefore the rider the
    attacker's handler just put in the bag.

    2d6 rolled 5 + 4 = 9, plus the +4 modifier, plus the rider's 1d6 rolled 6,
    is 19. Two copies of the context would leave the rider out of the roll and
    deal 13.
    """
    swinger, target = attacker(), defender()
    rider, witness = Rider(swinger), Witness(target)
    swinger.attach(rider)
    target.attach(witness)
    rng = ScriptedRng([_HIT, 5, 4, 6])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 19
    assert rider.seen[0] is witness.seen[0]
    # The engine's own contribution is stamped too, so the victim can tell the
    # weapon apart from what rode on it.
    assert witness.sources == [("engine.attack", "test.rider")]
    assert rng.exhausted


# --- The two rolls that decide themselves -----------------------------------


def test_a_natural_1_misses_whatever_the_total() -> None:
    """XPHB, "Rolling 20 or 1": a natural 1 always misses. Against AC 5 the
    total of 1 + 7 = 8 would otherwise be a comfortable hit, which is what
    separates the unconditional rule from an ordinary miss."""
    swinger, target = attacker(), defender(ac=5)
    rng = ScriptedRng([1])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.CRITICAL_MISS
    assert damage == 0
    assert target.hp == _DEFENDER_HP
    assert rng.exhausted


def test_a_natural_20_hits_and_crits_whatever_the_ac() -> None:
    """The other half of the same sentence. Against AC 30 the total of
    20 + 7 = 27 is nowhere near, and the attack still hits and still crits:
    4d6 rolled 1 + 1 + 1 + 1 = 4 plus the undoubled +4 is 8."""
    swinger, target = attacker(), defender(ac=30)
    rng = ScriptedRng([20, 1, 1, 1, 1])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.CRITICAL_HIT
    assert damage == 8
    assert target.hp == _DEFENDER_HP - 8
    assert rng.exhausted


# --- The critical threshold a Champion lowers -------------------------------


def test_a_lowered_threshold_crits_on_a_19_that_hits() -> None:
    """Improved Critical: the character's threshold is 19, so a natural 19 that
    hits is a Critical Hit. 19 + 7 = 26 clears AC 15, and the crit doubles the
    2d6 to 4d6 — rolled 6 + 5 + 4 + 3 = 18, plus the +4, is 22."""
    swinger, target = attacker(), defender()
    swinger.crit_threshold = 19
    rng = ScriptedRng([19, 6, 5, 4, 3])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.CRITICAL_HIT
    assert damage == 22
    assert rng.exhausted


def test_a_19_that_does_not_reach_the_ac_is_an_ordinary_miss() -> None:
    """**The distinction `rules/constants.py` keeps two numbers for.** A lowered
    threshold says which *hits* are critical; it never makes a roll hit. 19 + 7
    = 26 against AC 30 misses, and it is a plain miss rather than a critical
    anything — no damage is rolled and none is dealt."""
    swinger, target = attacker(), defender(ac=30)
    swinger.crit_threshold = 19
    rng = ScriptedRng([19])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert target.hp == _DEFENDER_HP
    assert rng.exhausted


def test_an_untouched_threshold_leaves_a_19_an_ordinary_hit() -> None:
    """The control for the pair above: at the default threshold of 20 the same
    natural 19 hits for 2d6 (5 + 4 = 9) + 4 = 13, and the empty script proves
    two dice were rolled rather than a crit's four."""
    swinger, target = attacker(), defender()
    rng = ScriptedRng([19, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert rng.exhausted


def test_a_critical_hit_doubles_the_dice_and_never_the_modifier() -> None:
    """The project's flagship structural rule, through the whole pipeline.

    2d6 doubled is 4d6, rolled 6 + 5 + 4 + 3 = 18, and the +4 Strength modifier
    is added once: 22. Doubling the rolled total would give 2 * 11 + 4 = 26 and
    doubling the whole contribution 2 * 15 = 30.
    """
    swinger, target = attacker(), defender()
    rng = ScriptedRng([20, 6, 5, 4, 3])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.CRITICAL_HIT
    assert damage == 22
    assert target.hp == _DEFENDER_HP - 22
    assert rng.exhausted  # four dice, not two: the pool doubled, not the total


# --- Advantage and disadvantage ---------------------------------------------


def test_advantage_rolls_two_d20s_and_keeps_the_higher() -> None:
    """XPHB Rules Glossary, "Advantage": roll a second d20 and use the higher.

    Scripted 3 then 17, so the attack resolves on 17 for 17 + 7 = 24 and hits;
    a single roll would take the 3, total 10, and miss. Damage is 2d6 (5 + 4)
    + 4 = 13.
    """
    swinger, target = attacker(), defender()
    swinger.attach(Reckless(swinger))
    rng = ScriptedRng([3, 17, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert rng.exhausted


def test_disadvantage_rolls_two_d20s_and_keeps_the_lower() -> None:
    """The mirror: scripted 17 then 3 resolves on the 3, for 3 + 7 = 10 against
    AC 15. A single roll would take the 17 and hit for 24."""
    swinger, target = attacker(), defender()
    target.attach(Dodging(target))
    rng = ScriptedRng([17, 3])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert rng.exhausted


def test_two_sources_of_advantage_are_still_one_advantage() -> None:
    """Advantage does not stack (XPHB Rules Glossary), and here that is a
    property of the ledger rather than a rule anybody wrote: two grants from two
    creatures' handlers still draw exactly two d20s. Scripted 3 then 17 resolves
    on 17 for a hit and 2d6 (5 + 4) + 4 = 13; a third d20 would exhaust the
    script."""
    swinger, target = attacker(), defender()
    swinger.attach(Reckless(swinger))
    target.attach(Restrained(target))
    rng = ScriptedRng([3, 17, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert rng.exhausted


def test_one_grant_and_one_imposition_cancel_to_a_single_roll() -> None:
    """Advantage and disadvantage cancel however many of each there are, so the
    attack is a flat roll: one d20, scripted 10, for 10 + 7 = 17 and a hit.
    Damage is 2d6 (5 + 4) + 4 = 13, and the exhausted script is what says a
    second d20 was never drawn."""
    swinger, target = attacker(), defender()
    swinger.attach(Reckless(swinger))
    target.attach(Dodging(target))
    rng = ScriptedRng([_HIT, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert rng.exhausted


# --- The two levers on the comparison ---------------------------------------


def test_a_bonus_carrying_dice_reaches_the_attack_total() -> None:
    """Bless adds 1d4 to the attack roll, and the die is rolled from the
    attack's own generator.

    Natural 4 + 4 + 3 = 11 is four short of AC 15; Bless's 1d4 rolled 4 makes it
    exactly 15, which hits. Damage is 2d6 (5 + 4) + 4 = 13 — the bonus moves the
    roll and nothing else.
    """
    swinger, target = attacker(), defender()
    swinger.attach(Blessed(swinger))
    rng = ScriptedRng([4, 4, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 13
    assert rng.exhausted


def test_an_ac_bonus_reaches_the_ac_the_total_is_compared_to() -> None:
    """The defender's one lever that is not disadvantage. Natural 10 + 7 = 17
    beats the printed AC 15 and loses to the 20 the Shield spell makes it."""
    swinger, target = attacker(), defender()
    target.attach(Shielded(target))
    rng = ScriptedRng([_HIT])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 0
    assert target.hp == _DEFENDER_HP
    assert rng.exhausted


# --- What the defender actually loses ---------------------------------------


def test_the_damage_dealt_is_what_the_resolution_order_left() -> None:
    """The attack reports what `engine/damage.py` resolved, not what it rolled:
    2d6 (5 + 4) + 4 = 13 slashing, halved by resistance to 13 // 2 = 6."""
    swinger, target = attacker(), defender()
    target.attach(SlashResistant(target))
    rng = ScriptedRng([_HIT, 5, 4])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 6
    assert target.hp == _DEFENDER_HP - 6
    assert rng.exhausted


def test_a_mitigation_die_comes_out_of_the_attacks_own_stream() -> None:
    """`mitigate()` is handed the generator the attack is using — a second
    stream would reproduce a different fight from the same archived seed with no
    error anywhere (`engine/damage.py`).

    The script runs out exactly when the attack is over: d20 10, the greatsword's
    5 and 4, and then the reduction's 1d4 rolled 3. 9 + 4 = 13 less 3 is 10.
    """
    swinger, target = attacker(), defender()
    target.attach(Reducer(target))
    rng = ScriptedRng([_HIT, 5, 4, 3])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 10
    assert target.hp == _DEFENDER_HP - 10
    assert rng.exhausted


# --- Damage on a miss -------------------------------------------------------


def test_damage_added_on_a_miss_is_resolved_through_the_defences() -> None:
    """The Graze mastery deals the ability modifier on a miss, and it is damage
    like any other: 4 slashing, halved by the victim's resistance to 2.

    The empty script is the second half of the case — a flat contribution rolls
    nothing, on a miss as on a hit.
    """
    swinger, target = attacker(), defender()
    swinger.attach(Graze(swinger))
    target.attach(SlashResistant(target))
    rng = ScriptedRng([_MISS])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 2
    assert target.hp == _DEFENDER_HP - 2
    assert rng.exhausted


def test_on_damage_roll_does_not_fire_for_damage_dealt_on_a_miss() -> None:
    """Graze's sentence gives the number itself, so nothing on the attacker's
    side gets to build it: the miss path skips `on_damage_roll` and goes
    straight to the victim's two damage hooks. `on_hit_taken` is absent for the
    same reason — the attack did not land. 4 flat, unresisted here."""
    swinger, target = attacker(), defender()
    swinger.attach(Graze(swinger))
    attacker_probe, defender_probe = AttackerProbe(swinger), DefenderProbe(target)
    swinger.attach(attacker_probe)
    target.attach(defender_probe)
    rng = ScriptedRng([_MISS])

    outcome, damage = resolve_attack(swinger, target, rng=rng)

    assert outcome is Outcome.MISS
    assert damage == 4
    assert attacker_probe.seen == [
        "on_declare_attack",
        "on_attack_rolled",
        "on_miss",
        "on_attack_resolved",
    ]
    assert defender_probe.seen == [
        "on_incoming_attack_declared",
        "on_incoming_damage",
        "on_damage_taken",
    ]


# --- Vetoes -----------------------------------------------------------------


def test_an_early_veto_at_the_declaration_prevents_the_attack() -> None:
    """`PREVENTED` is not a miss: there is no `Outcome` for an attack that was
    never made. No d20 is drawn — the empty script raises if one is — and the
    dispatch stops where it was halted, so neither creature's NORMAL probe
    runs."""
    swinger, target = attacker(), defender()
    swinger.attach(VetoDeclaration(swinger))
    attacker_probe, defender_probe = AttackerProbe(swinger), DefenderProbe(target)
    swinger.attach(attacker_probe)
    target.attach(defender_probe)

    result = resolve_attack(swinger, target, rng=no_dice())

    assert result is PREVENTED
    assert result.outcome is None
    assert result.damage_dealt == 0
    assert target.hp == _DEFENDER_HP
    assert attacker_probe.seen == []
    assert defender_probe.seen == []


def test_the_defenders_early_veto_prevents_it_too() -> None:
    """The same gate from the other end of the same context. The attacker's own
    declaration hook has already run by then, which is what the asymmetry in the
    two probes records; nothing after it does."""
    swinger, target = attacker(), defender()
    target.attach(VetoIncoming(target))
    attacker_probe, defender_probe = AttackerProbe(swinger), DefenderProbe(target)
    swinger.attach(attacker_probe)
    target.attach(defender_probe)

    result = resolve_attack(swinger, target, rng=no_dice())

    assert result is PREVENTED
    assert target.hp == _DEFENDER_HP
    assert attacker_probe.seen == ["on_declare_attack"]
    assert defender_probe.seen == []


def test_a_veto_once_the_d20_is_on_the_table_is_refused() -> None:
    """By `on_attack_rolled` the attack has been made and `Outcome` has no
    member for one that was withdrawn, so a veto there is refused rather than
    quietly cancelling the attack.

    It is the **feature's** contract that was broken, not the engine's assembly
    (`core/errors.py`): `ctx.veto()` has no meaning at this hook and no later
    task gives it one (`docs/decisions.md` § w1f), so there is nothing here that
    is waiting to be wired in. The author's fix is to gate at
    `on_declare_attack`.
    """
    swinger, target = attacker(), defender()
    swinger.attach(VetoRoll(swinger))

    with pytest.raises(FeatureContractError, match="on_attack_rolled"):
        resolve_attack(swinger, target, rng=ScriptedRng([_HIT]))


def test_a_veto_after_the_attack_landed_is_refused() -> None:
    """The same refusal on the busiest hook in the project, where a silent no-op
    would delete a Divine Smite rather than an attack."""
    swinger, target = attacker(), defender()
    swinger.attach(VetoHit(swinger))

    with pytest.raises(FeatureContractError, match="on_hit or on_hit_taken"):
        resolve_attack(swinger, target, rng=ScriptedRng([_HIT]))
    assert target.hp == _DEFENDER_HP


def test_the_victims_veto_on_the_shared_context_names_its_own_hook_too() -> None:
    """`on_hit` and `on_hit_taken` are one context sealed once, so the refusal
    names the pair: a veto cast by the defender's handler must not be reported
    against a hook on the attacker, which is the first place whoever reads the
    error will go looking."""
    swinger, target = attacker(), defender()
    target.attach(VetoHitTaken(target))

    with pytest.raises(
        FeatureContractError,
        match="test.veto_hit_taken: too late vetoed at on_hit or on_hit_taken",
    ):
        resolve_attack(swinger, target, rng=ScriptedRng([_HIT]))
    assert target.hp == _DEFENDER_HP


def test_the_two_refusals_a_sealed_context_makes_are_different_kinds() -> None:
    """**What `w1c` read.** One dispatch can leave two things behind, and only
    one of them was the engine's fault: a queued effect, legitimate and drained
    since `w1c`, and a late veto, a mutator used where it has no meaning and
    never will be given one.

    The queued-effect half is gone with the refusal it pinned. The `issubclass`
    assertions stay, and are the half that had to survive the sweep: the types
    are siblings under `DndSimError`, and a later change making either one
    inherit the other would let a sweep by type collapse a transitional refusal
    and a permanent one together.
    """
    vetoer, other = attacker(), defender()
    vetoer.attach(VetoHit(vetoer))
    with pytest.raises(FeatureContractError, match="vetoed at"):
        resolve_attack(vetoer, other, rng=ScriptedRng([_HIT]))

    assert not issubclass(EngineAssemblyError, FeatureContractError)
    assert not issubclass(FeatureContractError, EngineAssemblyError)


# --- What a handler queues, applied -----------------------------------------


def test_an_effect_queued_at_the_declaration_lands_before_the_roll() -> None:
    """The first of the two call sites `w1c` swept (debt #5). The declaration is
    drained where the refusal used to be raised — before the d20 — so the effect
    is on the defender whatever the attack goes on to do, and this one misses."""
    swinger, target = attacker(), defender()
    swinger.attach(QueuesAtDeclaration(swinger))

    outcome, _ = resolve_attack(swinger, target, rng=ScriptedRng([_MISS]))

    assert outcome is Outcome.MISS
    assert len(target.effects) == 1


class Brand(Effect):
    """An effect that adds a die at `on_damage_roll` — the shape a rider has
    when it applies during an attack and contributes afterwards."""

    __slots__ = ()

    ID = "test.brand"

    @hook("on_damage_roll")
    def burn(self, ctx: DamageRollContext) -> R:
        ctx.damage.add(dice=Dice(1, 6), damage_type=DamageType.FIRE)
        return R.APPLIED


class BrandsOnHit(HookSource):
    """Applies the brand to the swinger from `on_hit`, one stage before the
    damage is rolled.

    On the swinger rather than the target because `on_damage_roll` is `SELF` on
    the attacker (`hooks/definitions.py`): a rider applied to the defender has
    nothing to contribute with there, which is what makes the attacker the only
    end this can be asked from.
    """

    __slots__ = ()

    ID = "test.brands_on_hit"

    @hook("on_hit")
    def brand(self, ctx: HitContext) -> R:
        ctx.apply_effect(ctx.attacker, Brand(ctx.attacker))
        return R.APPLIED


def test_the_queue_is_drained_per_dispatch_and_not_per_attack() -> None:
    """**The scope of the drain, stated rather than left to be discovered.**
    `hooks/contexts.py` says the engine "drains the queue after the dispatch",
    and that is per dispatch: an effect applied at one stage of an attack is
    attached for the stages after it. So a brand applied from `on_hit` is on the
    swinger when `on_damage_roll` runs, and its die lands on the hit that
    applied it.

    The snapshot rule the same paragraph states is what this preserves — a
    handler list fixed per *dispatch*, so an effect applied during a hook never
    runs in that hook. Reckless Attack still has to call `grant_advantage()`
    beside applying its effect, because the dispatch its effect would have
    answered is the one already running.

    Deferring to the end of the attack was the alternative and is refused: it
    contradicts the sentence above, and it would give `on_incoming_damage` two
    different meanings depending on whether `resolve_damage()` was reached
    through an attack or called directly (`docs/decisions.md` § w1c).

    2d6 rolled 4 + 3 = 7, plus the +4 Strength modifier, plus the brand's 1d6
    rolled 5 = 16.
    """
    swinger, target = attacker(), defender()
    swinger.attach(BrandsOnHit(swinger))

    outcome, damage = resolve_attack(swinger, target, rng=ScriptedRng([_HIT, 4, 3, 5]))

    assert outcome is Outcome.HIT
    assert damage == 16
    assert [held.name for held in swinger.effects] == ["test.brand"]


def test_an_effect_queued_on_a_hit_lands_through_seal() -> None:
    """The hook that wants it most — a rider applying a condition on a hit —
    and the second call site, `_seal()`, which runs at five further stages of
    the attack."""
    swinger, target = attacker(), defender()
    swinger.attach(QueuesOnHit(swinger))

    resolve_attack(swinger, target, rng=ScriptedRng([_HIT, 3, 3]))

    assert [held.name for held in target.effects] == ["effect"]


# --- Which ability the attack is made with ----------------------------------


def test_strength_is_the_ability_a_weapon_attack_uses() -> None:
    """XPHB, "Attack Rolls": a melee weapon attack uses Strength unless the
    weapon says otherwise. The greatsword does not."""
    assert attack_ability(attacker(), GREATSWORD) is Ability.STRENGTH


def test_a_finesse_weapon_takes_dexterity_when_it_is_the_better_modifier() -> None:
    """Finesse: "you can use your Dexterity ... for the attack and damage
    rolls". The wielder here is +4 Dexterity against +2 Strength, so the choice
    the rules leave open has one sensible answer."""
    nimble = attacker(scores=_NIMBLE, weapon=RAPIER)
    assert attack_ability(nimble, RAPIER) is Ability.DEXTERITY


def test_a_finesse_weapon_keeps_strength_when_strength_is_the_better() -> None:
    """Finesse is a permission, not an obligation: the same rapier in the hands
    of a +4 Strength, +2 Dexterity fighter is a Strength weapon."""
    assert attack_ability(attacker(weapon=RAPIER), RAPIER) is Ability.STRENGTH


def test_the_chosen_ability_is_added_to_both_the_roll_and_the_damage() -> None:
    """One ability, two places — Finesse names both rolls in the same sentence.

    The +4 Dexterity wielder rolls 10 + 4 + 3 = 17 against AC 15 and deals the
    rapier's 1d8 rolled 6 plus 4 = 10. Under Strength the same attack would be
    15 to hit and 8 damage, so this number distinguishes them.
    """
    nimble, target = attacker(scores=_NIMBLE, weapon=RAPIER), defender()
    rng = ScriptedRng([_HIT, 6])

    outcome, damage = resolve_attack(nimble, target, rng=rng)

    assert outcome is Outcome.HIT
    assert damage == 10
    assert rng.exhausted


def test_an_explicit_ability_overrides_the_derivation() -> None:
    """A caller with a reason to swing a greatsword off Dexterity gets exactly
    that, on both rolls: 10 + 2 + 3 = 15 meets AC 15, and 2d6 (5 + 4) + 2 = 11.
    Under the derived Strength it would be 17 to hit and 13 damage."""
    swinger, target = attacker(), defender()
    rng = ScriptedRng([_HIT, 5, 4])

    outcome, damage = resolve_attack(
        swinger, target, rng=rng, ability=Ability.DEXTERITY
    )

    assert outcome is Outcome.HIT
    assert damage == 11
    assert rng.exhausted


# --- Reach, and the two attacks that cannot be described --------------------


def test_within_reach_measures_the_weapon_being_swung() -> None:
    """Reach is inclusive and belongs to the weapon (XPHB, "Weapons": the Reach
    property adds 5 feet). Two squares east is 10 feet: past a greatsword,
    exactly at a glaive's limit, and three squares is past both."""
    swinger = attacker()
    ten_feet, fifteen_feet = defender(cells=2), defender(cells=3)

    assert not within_reach(swinger, ten_feet, GREATSWORD)
    assert within_reach(swinger, ten_feet, GLAIVE)
    assert not within_reach(swinger, fifteen_feet, GLAIVE)


def test_an_attack_on_a_target_out_of_reach_is_refused() -> None:
    """Not a miss — an attack that cannot be described. Fifteen feet with five
    feet of greatsword."""
    swinger, target = attacker(), defender(cells=3)

    with pytest.raises(ValueError, match="cannot reach"):
        resolve_attack(swinger, target, rng=no_dice())


def test_the_reach_is_the_passed_weapons_and_not_the_equipped_ones() -> None:
    """A creature holding a greatsword can still be handed a glaive for this
    attack, and the ten feet that decide reach are the glaive's.

    10 + 4 + 3 = 17 hits AC 15 for 1d10 rolled 7 plus the +4 modifier = 11.
    """
    swinger, target = attacker(), defender(cells=2)
    rng = ScriptedRng([_HIT, 7])

    outcome, damage = resolve_attack(swinger, target, rng=rng, weapon=GLAIVE)

    assert outcome is Outcome.HIT
    assert damage == 11
    assert target.hp == _DEFENDER_HP - 11
    assert rng.exhausted


def test_the_equipped_weapon_does_not_lend_its_reach_to_another() -> None:
    """The same statement from the other side: a glaive on the attacker's belt
    does not let the greatsword in its hands reach ten feet."""
    swinger, target = attacker(weapon=GLAIVE), defender(cells=2)

    with pytest.raises(ValueError, match="cannot reach"):
        resolve_attack(swinger, target, rng=no_dice(), weapon=GREATSWORD)


def test_an_attacker_with_no_weapon_is_refused() -> None:
    """Unarmed strikes are their own attack (`w2b`), so an attack with no weapon
    at all is an assembly mistake rather than a 1-damage punch."""
    swinger, target = attacker(weapon=None), defender()

    with pytest.raises(ValueError, match="no weapon"):
        resolve_attack(swinger, target, rng=no_dice())


# --- Where the contexts come from -------------------------------------------


def test_a_pool_resolves_attack_after_attack_without_inheriting_the_first() -> None:
    """The rebinding source, twice over: a critical hit and then an ordinary
    one, on the same six contexts and the same bag.

    First: natural 20, 4d6 rolled 6 + 6 + 6 + 6 = 24, plus 4 = 28.
    Second: natural 10, 2d6 rolled 1 + 1 = 2, plus 4 = 6.

    40 - 28 - 6 = 6 hit points left. A second attack inheriting the first's
    `is_critical` would roll four dice, and one inheriting its bag would roll
    the greatsword twice — either way the script would run dry.
    """
    swinger, target = attacker(), defender()
    pool = PooledContexts(swinger, target, GREATSWORD)
    rng = ScriptedRng([20, 6, 6, 6, 6, _HIT, 1, 1])

    first = resolve_attack(swinger, target, rng=rng, contexts=pool)
    second = resolve_attack(swinger, target, rng=rng, contexts=pool)

    assert first == AttackResult(Outcome.CRITICAL_HIT, 28)
    assert second == AttackResult(Outcome.HIT, 6)
    assert target.hp == _DEFENDER_HP - 28 - 6
    assert rng.exhausted


def test_a_pool_refuses_an_attack_begun_inside_another() -> None:
    """One pool resolves one attack at a time. The inner attack would be handed
    the outer's live contexts and overwrite the event in flight, so it is
    refused loudly — `w2c`'s answer is a second pool, not a deeper one."""
    swinger, target = attacker(), defender()
    pool = PooledContexts(swinger, target, GREATSWORD)
    swinger.attach(Interrupts(swinger, defender=target, rng=no_dice(), contexts=pool))

    with pytest.raises(EngineAssemblyError, match="sharing this context pool"):
        resolve_attack(swinger, target, rng=no_dice(), contexts=pool)


def test_fresh_contexts_allow_a_nested_attack() -> None:
    """The allocating source has nothing to collide: the inner attack gets its
    own six contexts and both resolve.

    The inner one runs to completion first, inside the outer's declaration hook:
    natural 10 hits for 2d6 (1 + 1) + 4 = 6, and the outer then rolls its own
    natural 10 for 2d6 (2 + 2) + 4 = 8. 40 - 6 - 8 = 26.
    """
    swinger, target = attacker(), defender()
    rng = ScriptedRng([_HIT, 1, 1, _HIT, 2, 2])
    interrupts = Interrupts(swinger, defender=target, rng=rng, contexts=FRESH)
    swinger.attach(interrupts)

    outer = resolve_attack(swinger, target, rng=rng, contexts=FRESH)

    assert interrupts.result == AttackResult(Outcome.HIT, 6)
    assert outer == AttackResult(Outcome.HIT, 8)
    assert target.hp == _DEFENDER_HP - 6 - 8
    assert rng.exhausted


# --- The APL's trigger ------------------------------------------------------


def test_a_hit_offers_the_trigger_three_moments_and_the_last_carries_both() -> None:
    """**`bind()` states the whole offer, so every call says everything true of
    the attack at that moment.** A critical hit for 22, against a victim whose
    resistance leaves 11:

        bind()                              the declaration — nothing is known
                                            yet: no d20 has been thrown and no
                                            damage exists
        bind(crit=True)                     `on_hit` — the roll has resolved, so
                                            whether to spend a slot on Divine
                                            Smite is answerable; the damage has
                                            not been rolled
        bind(crit=True, damage_estimate=22) the victim's reactions — Uncanny
                                            Dodge is being offered *this* hit,
                                            and both facts are true of it

    The third moment is the decision `w2e` inherits. `crit` is re-bound there
    because leaving it at its default would be a **wrong** value rather than an
    absent one: a policy reading `trigger.crit` while deciding what to do about
    a critical hit's damage would be told the hit was ordinary, and the protocol
    has no third state to distinguish "not a crit" from "not yet known".
    `damage_estimate` at the second moment is the same shape of default and is
    not pinned as meaning anything — what a handler at `on_hit` may ask is
    `trigger.crit`.

    The estimate is what is on the table if nobody does anything about it —
    4d6 (6 + 5 + 4 + 3 = 18) + 4 = 22 — and not the 11 the resistance leaves,
    which is decided afterwards.
    """
    swinger, target = attacker(), defender()
    target.attach(SlashResistant(target))
    trigger = RecordingTrigger()
    rng = ScriptedRng([20, 6, 5, 4, 3])

    outcome, damage = resolve_attack(swinger, target, rng=rng, trigger=trigger)

    assert outcome is Outcome.CRITICAL_HIT
    assert damage == 11
    assert trigger.calls == [(False, 0), (True, 0), (True, 22)]


def test_a_miss_that_deals_damage_offers_two_moments_and_no_crit() -> None:
    """The asymmetry the hit branch's re-bind makes deliberate: a miss has no
    `on_hit` moment to offer, and `crit=False` at the damage moment is a true
    statement rather than a default standing in for an unknown — a miss is never
    a Critical Hit.

    Graze's flat 4 is the whole estimate, and the unresisted victim loses it.
    """
    swinger, target = attacker(), defender()
    swinger.attach(Graze(swinger))
    trigger = RecordingTrigger()
    rng = ScriptedRng([_MISS])

    outcome, damage = resolve_attack(swinger, target, rng=rng, trigger=trigger)

    assert outcome is Outcome.MISS
    assert damage == 4
    assert trigger.calls == [(False, 0), (False, 4)]


# --- Determinism ------------------------------------------------------------


def _fight(seed: int, attacks: int = 25) -> Sequence[AttackResult]:
    """`attacks` swings of one fresh pair, driven by one seeded generator."""
    swinger, target = attacker(), defender()
    rng = MersenneRng(seed)
    return [resolve_attack(swinger, target, rng=rng) for _ in range(attacks)]


def test_the_same_seed_gives_the_same_fight() -> None:
    """The backbone: seed plus build reproduces a run byte for byte, which is
    what makes `trace`, goldens and regression diffs possible."""
    first = _fight(20240915)

    assert first == _fight(20240915)
    assert any(result.damage_dealt for result in first)


def test_a_different_seed_gives_a_different_fight() -> None:
    """The control for the case above, which an engine that ignored its seed
    would also pass. Both sequences are fixed, so this is a statement about two
    particular streams rather than a probabilistic one."""
    assert _fight(20240915) != _fight(1)
