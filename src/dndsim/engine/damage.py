"""The damage resolution order, in the one file it is ever written in.

**immunity -> flat reductions -> halvings -> resistance -> vulnerability ->
temporary hit points -> hit points.** DESIGN.md § Structural prevention names
this order and names this file, and the reason it is one file rather than a rule
in a skill document is that every feature which touches damage — Rage, Uncanny
Dodge, Heavy Armor Master, a resistance from a spell — contributes to the ledger
and none of them applies anything. `hooks/contexts.py`'s `IncomingDamageContext`
*records*; this module is the only code that acts on the record.

## Where each step comes from

The middle of the sequence is a **quotation**, one step is a **ruling**, and
DESIGN.md asks for the citation of each to be here rather than re-argued in a
review three years from now.

- **Immunity** is the XPHB Rules Glossary entry: damage of a type you are Immune
  to is not taken at all. It runs first, and that is load-bearing rather than
  tidy — the adjustments below subtract from *the damage you take*, and damage
  you are immune to is not damage you take. Immunity placed any later would let
  Heavy Armor Master spend its reduction on the half of a hit that was never
  going to land.
- **Adjustments, then Resistance, then Vulnerability** is quoted: *"Modifiers to
  damage are applied in the following order: adjustments such as bonuses,
  penalties, or multipliers are applied first; Resistance is applied second; and
  Vulnerability is applied third"* (XPHB, "Damage and Healing"). The book's own
  worked example is `tests/contract/test_damage.py`'s first case — 28 fire
  damage, reduced by 5 to 23, halved by Resistance to 11, doubled by
  Vulnerability to 22. A flat reduction is a penalty and a halving is a
  multiplier, so both are adjustments and both land in that first step.
- **Flat reductions before halvings** is the one surviving **ruling**, and it is
  a ruling *inside* the quoted first step rather than against Resistance: the
  text bundles penalties and multipliers into one band without sequencing them
  against each other, so the order of the two is the project's to fix. It is
  fixed in favour of subtracting first — `(20 - 4) // 2 = 8`, not
  `20 // 2 - 4 = 6` — because halving last is what stops two mitigation features
  from being worth more together than the sum of their parts, which is the shape
  of the stacking the 2024 rules went out of their way to close everywhere else.
  The other reading is not absurd: Uncanny Dodge halves "the damage you take",
  and one can argue that is the amount before anyone subtracted from it. Nothing
  in the text prefers it, and it compounds.
- **Halving rounds down**, like every division in the game (XPHB, "Round Down").
- **Two resistances count once; two halvings do not.** The "counts once" clause
  names Resistance — `MitigationLedger` keeps the three type collections as
  *sets*, so that rule is structural rather than checked — and a halving is not
  Resistance. So `_halvings` is a list and each one halves. That is a reading of
  the clause, not an argument from silence, and it is why a raging barbarian's
  Uncanny Dodge quarters a hit.

Two consequences of this order read as bugs and are not:

- **Resistance and Vulnerability to the same type no longer cancel exactly.**
  7 damage halves to 3 and doubles to 6. `w0g2` ran vulnerability first for
  precisely this reason — `(2n) // 2 == n` on every integer — and the missing
  point is the book's answer rather than a rounding slip.
- **A flat reduction is worth half to a creature with resistance** what it is
  worth to one without, because it is subtracted from the full amount and then
  halved along with it. `w0g2` saw that coming and ruled against it; the book
  orders it that way regardless, and a feature reading how it ought to work is
  not a rule.

## How an untyped adjustment is spent across a mixed-type hit

The reorder creates a question `w0g2`'s order did not have. Adjustments act on
"the damage", which is one number; Resistance and Vulnerability act per type.
With the adjustments first, the order needs a per-type picture on the far side of
a step that has none — and the rules contemplate neither a mixed-type instance of
damage nor an untyped reduction of one, so this is a ruling with no sentence
behind it. Two properties decide it:

- **The spend may not consult the steps that follow it.** Spending the reduction
  on whatever is *not* resisted is what a player would choose and is exactly what
  the quotation forbids: an adjustment applied first cannot know what Resistance
  will do with what is left. A rule that looked ahead would keep the sequencing
  in form and invert it in fact.
- **It may not depend on the order the damage parts were added.** That order is
  an authoring artefact — Sneak Attack's d6s are appended after the dagger's, and
  nothing about the hit turns on it — and a reported number that moved when a
  feature was registered earlier would be a reproducibility break with no error
  anywhere.

What survives both is proportion: **an adjustment changes the size of the hit,
not its composition.** Each type keeps its share of what is left, and the points
that do not divide evenly go to the largest fractional remainders — the standard
largest-remainder apportionment — with ties broken by the larger component and
then by the damage type's name, so the result is determined by the numbers alone
and never by a dict's or a set's iteration history.

The alternative considered and rejected was spending the whole adjustment against
one component, largest first. One case kills it: 5 fire and 5 slashing under
Uncanny Dodge would delete the fire outright and leave 5 slashing, which is not
what happened to the creature and flips to the other type on a one-point change
in either.

## What the two numbers on `Mitigated` mean now

`w0g2` had `total` and `sum(amounts.values())` deliberately disagree, because the
untyped steps ran last and there was no honest way to spread a subtraction back
over the types. Under this order the typed steps run last instead, the
apportionment above *is* that honest way, and the two agree: `amounts` is the
final per-type picture and `total` is its sum, floored at zero. What inverts with
them is `types` — a reduction or a halving can now take a type out of the report,
because half of one point of fire is no fire.

## What this module deliberately does not do

- **It does not roll the attack** — `engine/attack.py` (w1f) does, and it calls
  `roll_damage()` here for the one part of an attack that is damage.
- **It does not decide who is dying.** Hit points stop at zero and `w1j` owns
  what happens then: death saves, stabilising, and the `on_reduced_to_zero` hook
  that lets Relentless Endurance keep a creature up. Until that task lands a
  creature at 0 is simply out of the fight (`Creature.is_alive`). Note what the
  clamp discards: the damage left over past zero, which is what the instant-death
  rule reads and what `ReducedToZeroContext.overflow` carries. `w1j` computes it
  where it dispatches that hook, from the hit points the creature had — it is not
  recoverable afterwards, which is why this sentence is here rather than in a
  bug report.
- **It does not apply the effects a handler queued.** `ctx.apply_effect()` is
  drained by whoever owns the event once every dispatch of it is done
  (`hooks/dispatch.py`), and the thing that applies an effect to a creature is
  `EffectStore` (w1c). Until that exists, a queued effect is refused loudly
  rather than dropped silently.
- **It does not type a reduction.** `ctx.reduce()` takes no damage type, so a
  reduction is spent across every type that survived immunity — and 2024 Heavy
  Armor Master reduces Bludgeoning, Piercing and Slashing only. Exact while a hit
  carries one type, wrong the first time a flame tongue makes it two (ROADMAP §
  The W0 debt register #23, `w3f` via `w3e`).
- **It does not log.** `w1i` owns the event log and reads these numbers; nothing
  here formats or records them.

Sources: DESIGN.md § Structural prevention over testing, § Hook system; XPHB
(2024) "Damage and Healing", Rules Glossary "Immunity", "Vulnerability",
"Resistance", "Round Down", "Temporary Hit Points"; ROADMAP w0g2, w1e, w1j.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from dndsim.core.errors import EngineAssemblyError
from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.hooks.contexts import (
    Context,
    DamagePart,
    DamageTakenContext,
    IncomingDamageContext,
    MitigationLedger,
)
from dndsim.hooks.definitions import ON_DAMAGE_TAKEN, ON_INCOMING_DAMAGE
from dndsim.rules.enums import DamageType

__all__ = [
    "DamageContexts",
    "FreshDamageContexts",
    "Mitigated",
    "mitigate",
    "resolve_damage",
    "roll_damage",
]


class DamageContexts(Protocol):
    """Where the two contexts of a damage event come from.

    The same **Strategy** the attack sequence uses for its six (`w0i`), and for
    the same reason: DESIGN.md § Levers asks for contexts reused per attack
    rather than allocated, and a hit allocates two here. Pooling them is worth
    about a tenth of an attack resolution, which at 4M of those is minutes.
    """

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext: ...

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext: ...


class FreshDamageContexts:
    """Allocate both, every hit. The default, and what `w0h` uses."""

    __slots__ = ()

    def incoming(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amounts: Mapping[DamageType, int],
    ) -> IncomingDamageContext:
        return IncomingDamageContext(
            defender=defender, attacker=attacker, amounts=amounts
        )

    def taken(
        self,
        *,
        defender: Creature,
        attacker: Creature | None,
        amount: int,
        damage_types: frozenset[DamageType],
    ) -> DamageTakenContext:
        return DamageTakenContext(
            defender=defender,
            attacker=attacker,
            amount=amount,
            damage_types=damage_types,
        )


FRESH_DAMAGE: Final[DamageContexts] = FreshDamageContexts()
"""The default source. Stateless, so one instance serves every caller."""


def roll_damage(
    parts: Iterable[DamagePart],
    *,
    rng: Rng,
    base_type: DamageType,
    critical: bool,
) -> dict[DamageType, int]:
    """Roll the contributions a `DamageBag` collected into an amount per type.

    Two rules live in these few lines and neither is the caller's to remember:

    - **A critical hit doubles the dice and never the flat part**, through
      `Dice.doubled()` — the crit rolls a doubled pool rather than multiplying a
      total, so 2d6 that rolled 7 is not 14 (DESIGN.md § Structural
      prevention). The doubling is spelled out in the loop rather than routed
      through `DamagePart.on_crit()`, which would allocate a second part per
      contribution per critical hit purely to hold the doubled pool; that
      method stays as the readable statement of the rule and the one a test
      pins it by, and this loop is the only caller that has a reason not to use
      it.
    - **`damage_type=None` means "the type of the attack this rides on"**, so
      Sneak Attack's d6s are the dagger's piercing. It is resolved here, at the
      last moment before the amounts stop being the attacker's and become the
      victim's, because every step downstream is keyed by type.
    """
    amounts: dict[DamageType, int] = {}
    get = amounts.get
    for part in parts:
        dice = part.dice
        if dice is None:
            amount = part.flat
        else:
            if critical:
                dice = dice.doubled()
            amount = dice.roll(rng) + part.flat
        damage_type = part.damage_type or base_type
        amounts[damage_type] = get(damage_type, 0) + amount
    return amounts


@dataclass(frozen=True, slots=True)
class Mitigated:
    """What the resolution order made of an instance of damage.

    `amounts` is the per-type picture once every step has run, and `total` is
    what the creature actually takes. They agree: `total` is
    `sum(amounts.values())`, floored at zero, and the floor is the only thing
    that can separate them — a negative contribution is malformed input the
    clamp exists to make safe rather than to give meaning to.

    **That agreement is `w1e`'s, and it is the reverse of `w0g2`'s.** Under the
    old order the untyped steps ran last, so a reduction left `total` smaller
    than the per-type picture with no honest way to reconcile them. The 2024
    order puts the typed steps last instead, which forces an answer to how an
    untyped adjustment is spent across a mixed-type hit — proportionally, per
    the module docstring — and the answer is what keeps one coherent set of
    numbers all the way to the end.
    """

    amounts: Mapping[DamageType, int]
    total: int

    @property
    def types(self) -> frozenset[DamageType]:
        """The types that got through, for the log and the hooks.

        Immunity takes a type out of this set, and so — since `w1e` — can a
        reduction or a halving, because both are now spent over the types
        rather than over a total the types never see again: half of one point of
        fire is no fire, and a reduction that ate the whole hit ate every type
        in it. A type reports here when the creature took damage of it, which is
        the question `on_damage_taken` and the log are both asking.
        """
        return frozenset(t for t, amount in self.amounts.items() if amount > 0)


def _apportion(
    amounts: Mapping[DamageType, int], target: int, total: int
) -> dict[DamageType, int]:
    """Spread `target` back over the types in proportion to `amounts`.

    The mechanism behind the module docstring's ruling on untyped adjustments,
    and it is only ever called when one of them changed the total. Largest
    remainder: every type takes its floored share, and the points left over go
    one each to the largest fractional remainders. The comparison is on
    `share * total` rather than on a float, so the ordering is exact; the
    tie-breaks after it are the larger component and then the type's name, which
    are the only two keys available that do not encode the order the damage
    parts happened to be added in.
    """
    if target <= 0 or total <= 0:
        return dict.fromkeys(amounts, 0)
    if len(amounts) == 1:
        return {damage_type: target for damage_type in amounts}
    shares: dict[DamageType, int] = {}
    remainders: list[tuple[int, int, str, DamageType]] = []
    assigned = 0
    for damage_type, amount in amounts.items():
        scaled = amount * target
        share = scaled // total
        shares[damage_type] = share
        assigned += share
        remainders.append(
            (scaled - share * total, amount, damage_type.value, damage_type)
        )
    remainders.sort(key=lambda row: (-row[0], -row[1], row[2]))
    for _, _, _, damage_type in remainders[: target - assigned]:
        shares[damage_type] += 1
    return shares


def mitigate(
    amounts: Mapping[DamageType, int],
    ledger: MitigationLedger,
    *,
    rng: Rng,
) -> Mitigated:
    """Run the resolution order over what the victim's handlers recorded.

    Pure: it reads a ledger and returns numbers, touching no creature. That is
    what lets every step of the order be pinned by a table of cases, and what
    keeps the arithmetic testable without a fight around it.

    One thing about it is a contract rather than a detail: **a reduction's dice
    are drawn from the `rng` passed here**, after the attacker's damage dice, so
    the attack pipeline (w1f) must hand this function the generator the attack
    is using — a second generator would reproduce a different fight from the
    same archived seed.
    """
    if ledger.untouched:
        # The ordinary hit: nothing was recorded, so every step below is the
        # identity — every step but the clamp, which is not about the ledger
        # and so has to survive the shortcut. Spelled as one question rather
        # than five empty loops because it is the common case on the hottest
        # path (w0i) — and it is the *order* that lives here, not an
        # optimisation of it.
        total = sum(amounts.values())
        return Mitigated(amounts=amounts, total=total if total > 0 else 0)

    # Immunity: the type leaves the event, before the adjustments can be spent
    # on damage that was never going to be taken.
    surviving = {
        damage_type: amount
        for damage_type, amount in amounts.items()
        if damage_type not in ledger.immunities
    }
    total = sum(surviving.values())

    # The adjustments, on the one number they are written against. The clamp
    # sits between the reductions and the halvings because `-7 // 2` is -4 and
    # a hit that dealt nothing must not come back as healing.
    adjusted = total
    for reduction in ledger.reductions:
        rolled = 0 if reduction.dice is None else reduction.dice.roll(rng)
        adjusted -= rolled + reduction.flat
    if adjusted < 0:
        adjusted = 0
    for _ in ledger.halvings:
        adjusted //= 2
    if adjusted != total:
        surviving = _apportion(surviving, adjusted, total)

    # Resistance second and Vulnerability third, per type, per the quotation.
    landed: dict[DamageType, int] = {}
    for damage_type, amount in surviving.items():
        if damage_type in ledger.resistances:
            amount //= 2
        if damage_type in ledger.vulnerabilities:
            amount *= 2
        landed[damage_type] = amount
    total = sum(landed.values())
    return Mitigated(amounts=landed, total=total if total > 0 else 0)


def resolve_damage(
    defender: Creature,
    amounts: Mapping[DamageType, int],
    *,
    rng: Rng,
    attacker: Creature | None = None,
    contexts: DamageContexts = FRESH_DAMAGE,
) -> int:
    """Deal damage to `defender` and return what it took.

    The whole event, in the order DESIGN.md fixes: the victim's handlers get
    their one chance to change it (`on_incoming_damage`), the order above runs
    over what they recorded, temporary hit points absorb what they can and hit
    points take the rest, and `on_damage_taken` reports what landed.

    An `EARLY` or `FINAL` handler that vetoes stops the whole thing: the damage
    does not happen, nothing is spent, and the return is zero. That is the Chain
    of Responsibility element of the pipeline, and it is why a veto is checked
    here rather than treated as one more kind of mitigation.

    **`on_damage_taken` does not fire when nothing landed.** A creature immune to
    the only type in the hit, or one whose reductions ate all of it, has taken no
    damage, and the rules read "when you take damage" — the concentration check
    is the case that matters, and a creature that takes none makes none. Firing
    the hook with a zero would put that condition inside every handler ever
    written for it, which is the sort of thing this design makes structural
    instead. A hit absorbed entirely by temporary hit points *did* land and does
    fire, because those hit points are gone.
    """
    incoming = contexts.incoming(defender=defender, attacker=attacker, amounts=amounts)
    defender.dispatch(ON_INCOMING_DAMAGE, incoming)
    _refuse_pending(incoming)
    if incoming.vetoed:
        incoming.close()
        return 0
    mitigated = mitigate(amounts, incoming.mitigation, rng=rng)
    incoming.close()

    if not mitigated.total:
        return 0
    absorbed = min(defender.temp_hp, mitigated.total)
    defender.temp_hp -= absorbed
    defender.hp = max(defender.hp - (mitigated.total - absorbed), 0)

    taken = contexts.taken(
        defender=defender,
        attacker=attacker,
        amount=mitigated.total,
        damage_types=mitigated.types,
    )
    defender.dispatch(ON_DAMAGE_TAKEN, taken)
    _refuse_pending(taken)
    taken.close()
    return mitigated.total


def _refuse_pending(ctx: Context) -> None:
    """Refuse an effect queued where there is nothing yet to apply it.

    `EffectStore` (w1c) is what drains `ctx.pending_effects`, and until it
    exists a handler that queues an effect during a damage event would have it
    silently discarded — a feature that did nothing and said nothing, which is
    the failure mode this design refuses everywhere else. Deleted by `w1c`, not
    by whoever trips over it.
    """
    pending = ctx.pending_effects
    if pending:
        raise EngineAssemblyError(
            f"{type(ctx).__name__} has {len(pending)} queued effect(s) and there "
            "is nothing to apply them yet (w1c). A queued effect nobody drains "
            "is a feature whose whole point was silently dropped."
        )
