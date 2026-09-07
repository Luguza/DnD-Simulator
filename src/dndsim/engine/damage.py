"""The damage resolution order, in the one file it is ever written in.

**immunity -> vulnerability -> resistance -> flat reductions -> halvings ->
temporary hit points -> hit points.** DESIGN.md § Structural prevention names
this order and names this file, and the reason it is one file rather than a rule
in a skill document is that every feature which touches damage — Rage, Uncanny
Dodge, Heavy Armor Master, a resistance from a spell — contributes to the ledger
and none of them applies anything. `hooks/contexts.py`'s `IncomingDamageContext`
*records*; this module is the only code that acts on the record.

A first cut (ROADMAP w0g2), so that `w0h`'s walking skeleton and `w0i`'s
benchmark run against real arithmetic. `w1e` completes it — effects, the
interaction with dying (w1j), and the log records — and the order below is not
written a second time when it does.

## Where each step comes from

Three steps are quotations and two are rulings, and DESIGN.md asks for the
citation of each to be here rather than re-argued in a review three years from
now.

- **Immunity, vulnerability, resistance** are the XPHB Rules Glossary entries:
  damage of a type you are Immune to is not taken at all, damage you are
  Vulnerable to is doubled, damage you are Resistant to is halved, and multiple
  instances of the same one count once. The last of those is structural rather
  than checked — `MitigationLedger` keeps three *sets*, so a type cannot be
  resisted twice (`hooks/contexts.py`).
- **Vulnerability before resistance** is not arbitrary. Doubling and then
  halving is exactly the identity on integers — `(2n) // 2 == n` for every n —
  so this order *is* the rule that resistance and vulnerability to the same type
  cancel out, with no special case to write. The other order would lose a point
  on every odd number and quietly disagree with the book.
- **Halving rounds down**, like every division in the game (XPHB, "Round Down").
- **Flat reductions apply after resistance** — a *ruling*. No XPHB sentence
  settles the interaction between a subtraction (Heavy Armor Master) and a
  halving, and the two orders disagree by half the reduction on every hit. The
  project applies the subtraction to what resistance let through, because a
  reduction is written as "reduce the damage you take" — the damage you take is
  what arrives after your resistances — and because the alternative makes a
  defensive feature worth *less* to the creature that invested in resistance,
  which is the opposite of how both features read.
- **Halvings apply after the flat reductions** — the second *ruling*, and the
  same argument in the other direction: Uncanny Dodge halves "the damage you
  take", and taking it after the subtraction is what stops two mitigation
  features from being worth more together than the sum of their parts.

Both rulings change every number the simulator reports for a build that has
either feature. They are `w1e`'s to confirm against the 2024 text and the
project's to own.

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

Sources: DESIGN.md § Structural prevention over testing, § Hook system; XPHB
(2024) Rules Glossary "Immunity", "Vulnerability", "Resistance", "Round Down",
"Temporary Hit Points"; ROADMAP w0g2, w1e, w1j.
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

    Two numbers rather than one because the steps split in two: immunity,
    vulnerability and resistance are per damage type, while a flat reduction and
    a halving are subtractions from what arrives — Heavy Armor Master reduces
    *the damage*, not the fire part of it. `amounts` is therefore the per-type
    picture after the typed steps, and `total` is what the creature actually
    takes.

    **`total` is not `sum(amounts.values())` and the two are not interchangeable.**
    Anything that wants to know how much damage happened reads `total`; `amounts`
    and `types` answer *what kind* it was, which is a question the untyped steps
    cannot change — a 3-point hit against a reduction of 10 is still slashing and
    is still zero.
    """

    amounts: Mapping[DamageType, int]
    total: int

    @property
    def types(self) -> frozenset[DamageType]:
        """The types that survived the typed steps, for the log and the hooks.

        Immunity removes a type from this set because immunity is about the
        type; a reduction or a halving never does, because they are about the
        amount. So this set can name a type on a hit that dealt nothing, and
        that is the honest report of what was thrown rather than a bug.
        """
        return frozenset(t for t, amount in self.amounts.items() if amount > 0)


def mitigate(
    amounts: Mapping[DamageType, int],
    ledger: MitigationLedger,
    *,
    rng: Rng,
) -> Mitigated:
    """Run the resolution order over what the victim's handlers recorded.

    Pure: it reads a ledger and returns numbers, touching no creature. That is
    what lets `w1e` pin every step of the order with a table of cases, and what
    keeps the arithmetic testable without a fight around it.

    Two things about it are contracts rather than details. **A reduction's dice
    are drawn from the `rng` passed here**, after the attacker's damage dice, so
    the attack pipeline (w1f) must hand this function the generator the attack
    is using — a second generator would reproduce a different fight from the
    same archived seed. And **halvings are a list where the resistances are a
    set**: the rules say multiple instances of resistance count once and say
    nothing of the kind about two features that halve, so each halving halves.
    That is an argument from silence about a rare pair — Uncanny Dodge plus a
    second halving — and `w1e` owns confirming it against the 2024 text. What is
    *not* in doubt is the common case the sets and the list are both needed for:
    a resistance and a halving quarter the hit.
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
    resisted: dict[DamageType, int] = {}
    for damage_type, amount in amounts.items():
        if damage_type in ledger.immunities:
            continue
        landed = amount
        if damage_type in ledger.vulnerabilities:
            landed *= 2
        if damage_type in ledger.resistances:
            landed //= 2
        resisted[damage_type] = landed
    total = sum(resisted.values())
    for reduction in ledger.reductions:
        rolled = 0 if reduction.dice is None else reduction.dice.roll(rng)
        total -= rolled + reduction.flat
    total = max(total, 0)
    for _ in ledger.halvings:
        total //= 2
    return Mitigated(amounts=resisted, total=total)


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
