"""The damage resolution order, in the one file it is ever written in.

**immunity -> flat reductions -> halvings -> resistance -> vulnerability ->
temporary hit points -> hit points.** Every feature that touches damage — Rage,
Uncanny Dodge, Heavy Armor Master, a resistance from a spell — contributes to a
`MitigationLedger` and none of them applies anything. This module is the only
code that acts on the record.

## The citation for each step

- **Immunity**: damage of a type you are Immune to is not taken at all (XPHB
  Rules Glossary). It runs first because the adjustments below subtract from
  *the damage you take*, and damage you are immune to is not that.
- **Adjustments, then Resistance, then Vulnerability**: *"Modifiers to damage
  are applied in the following order: adjustments such as bonuses, penalties, or
  multipliers are applied first; Resistance is applied second; and Vulnerability
  is applied third"* (XPHB, "Damage and Healing"). A flat reduction is a penalty
  and a halving is a multiplier, so both are adjustments.
- **Flat reductions before halvings** is the one **ruling**, and it sits inside
  that quoted first step, which bundles penalties and multipliers together
  without sequencing them against each other. Subtract first: the one thing the
  text does settle about a reduction is that Resistance halves it along with
  everything else, and a halving is the same ×½ operation left unsequenced, so a
  reduction meets Uncanny Dodge exactly as the book has it meet Rage. Halving
  first would make those two exactly additive while a reduction stays
  sub-additive with Resistance — one feature combining differently depending on
  where the ×½ came from, which the text gives no reason for.
- **Halving rounds down** (XPHB, "Round Down").
- **Two resistances count once; two halvings do not.** The "counts once" clause
  names Resistance and a halving is not Resistance, so the three type
  collections are sets and `_halvings` is a list.

Two consequences read as bugs and are not: resistance and vulnerability to the
same type no longer cancel exactly (7 halves to 3 and doubles to 6), and a flat
reduction is worth half to a creature that also resists, because it is
subtracted before the halving that follows it.

## Spending an untyped adjustment across a mixed-type hit

Adjustments act on one number and the typed steps act per type, so with the
adjustments first the order needs a per-type picture on the far side of a step
that has none. No sentence in the book covers it. Two properties fix the ruling:
the spend may not consult the resistances that follow it, because an adjustment
applied *first* cannot know what Resistance will do with what it leaves; and it
may not depend on the order the damage parts arrived, which is an authoring
artefact. What survives both is proportion — an adjustment changes the size of
the hit, not its composition. `_apportion()` does it.

`Mitigated.total` is therefore `sum(amounts.values())` floored at zero, where
`w0g2` had the two deliberately disagree, and an untyped step can now take a
type out of `Mitigated.types`: half of one point of fire is no fire.

## What this module does not do

- **Roll the attack.** `engine/attack.py` (w1f) does, and calls `roll_damage()`.
- **Decide who is dying.** `w1j` owns death saves and `on_reduced_to_zero`. The
  clamp at zero discards the overflow the instant-death rule reads, which `w1j`
  computes where it dispatches that hook (debt #8).
- **Apply queued effects.** `EffectStore` (w1c) drains `ctx.pending_effects`;
  until it exists `_refuse_pending()` raises (debt #5).
- **Type a reduction.** `ctx.reduce()` takes no damage type, so a reduction is
  spent across every type that survived immunity — and 2024 Heavy Armor Master
  reduces Bludgeoning, Piercing and Slashing only (debt #23, `w3f` via `w3e`).
- **Log.** `w1i` owns the event log and reads these numbers.

Sources: XPHB (2024) "Damage and Healing", Rules Glossary "Immunity",
"Vulnerability", "Resistance", "Round Down", "Temporary Hit Points"; DESIGN.md
§ Structural prevention over testing; ROADMAP w0g2, w1e.
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

    The same **Strategy** the attack sequence uses for its six (`w0i`): pooling
    them instead of allocating two per hit is worth about a tenth of an attack
    resolution.
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

    Two rules live here and neither is the caller's to remember:

    - **A critical hit doubles the dice and never the flat part**, by rolling a
      doubled pool rather than multiplying a total, so 2d6 that rolled 7 is not
      14 (DESIGN.md § Structural prevention). It is inlined rather than routed
      through `DamagePart.on_crit()`, which would allocate a part per
      contribution per crit; that method stays as the readable statement of the
      rule and the one a test pins it by.
    - **`damage_type=None` means "the type of the attack this rides on"**, so
      Sneak Attack's d6s are the dagger's piercing. Resolved here, at the last
      moment before the amounts stop being the attacker's, because every step
      downstream is keyed by type.
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

    `amounts` is the per-type picture once every step has run and `total` is its
    sum, floored at zero — the floor is the only thing that can separate them,
    and only for a malformed negative contribution.
    """

    amounts: Mapping[DamageType, int]
    total: int

    @property
    def types(self) -> frozenset[DamageType]:
        """The types that got through, for the log and the hooks.

        Immunity takes a type out of this set, and since `w1e` so can a reduction
        or a halving: both are spent over the types, and half of one point of
        fire is no fire.
        """
        return frozenset(t for t, amount in self.amounts.items() if amount > 0)


def _apportion(amounts: Mapping[DamageType, int], target: int) -> dict[DamageType, int]:
    """Spread `target` over the types in proportion, by Jefferson's method.

    Each point goes to the type furthest from its share — the one maximising
    `amount / (points it already holds + 1)` — with ties broken by the larger
    component and then the type's name, so nothing depends on the order the
    damage parts arrived. The comparison cross-multiplies, so it is exact.

    The method is chosen for one property: the allocation of `target` points is
    the allocation of `target - 1` plus one more, so a larger reduction can never
    leave the creature taking *more* damage. Largest remainder fails that — the
    Alabama paradox — and `test_more_mitigation_never_means_more_damage` is the
    three-type case that caught it.
    """
    if target <= 0:
        return dict.fromkeys(amounts, 0)
    if len(amounts) == 1:
        return {damage_type: target for damage_type in amounts}
    order = sorted(amounts.items(), key=lambda item: (-item[1], item[0].value))
    shares = dict.fromkeys(amounts, 0)
    for _ in range(target):
        best_type, best_amount = order[0]
        best_divisor = shares[best_type] + 1
        for damage_type, amount in order[1:]:
            divisor = shares[damage_type] + 1
            if amount * best_divisor > best_amount * divisor:
                best_type, best_amount, best_divisor = damage_type, amount, divisor
        shares[best_type] += 1
    return shares


def mitigate(
    amounts: Mapping[DamageType, int],
    ledger: MitigationLedger,
    *,
    rng: Rng,
) -> Mitigated:
    """Run the resolution order over what the victim's handlers recorded.

    Pure: it reads a ledger and returns numbers, touching no creature.

    **A reduction's dice are drawn from the `rng` passed here**, after the
    attacker's damage dice, so the attack pipeline (w1f) must hand this function
    the generator the attack is using — a second generator would reproduce a
    different fight from the same archived seed.
    """
    if ledger.untouched:
        # The clamp is the one step of the order that is not the identity on an
        # empty ledger, so the shortcut past the rest keeps it (DESIGN.md).
        total = sum(amounts.values())
        return Mitigated(amounts=amounts, total=total if total > 0 else 0)

    surviving = {
        damage_type: amount
        for damage_type, amount in amounts.items()
        if damage_type not in ledger.immunities
    }
    total = sum(surviving.values())

    adjusted = total
    for reduction in ledger.reductions:
        rolled = 0 if reduction.dice is None else reduction.dice.roll(rng)
        adjusted -= rolled + reduction.flat
    if adjusted < 0:
        adjusted = 0  # before the halvings, because -7 // 2 is -4
    for _ in ledger.halvings:
        adjusted //= 2
    if adjusted != total:
        surviving = _apportion(surviving, adjusted)

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

    A veto from an `EARLY` or `FINAL` handler stops the whole event: nothing is
    spent and the return is zero.

    **`on_damage_taken` does not fire when nothing landed.** The rules read "when
    you take damage", and a creature immune to the only type in the hit, or whose
    reductions ate all of it, took none — so it makes no concentration check, and
    no handler ever written for the hook has to test for a zero. A hit absorbed
    entirely by temporary hit points *did* land and does fire: those hit points
    are gone.
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

    `EffectStore` (w1c) drains `ctx.pending_effects`; until it exists a queued
    effect would be silently discarded. Deleted by `w1c`, not by whoever trips
    over it.
    """
    pending = ctx.pending_effects
    if pending:
        raise EngineAssemblyError(
            f"{type(ctx).__name__} has {len(pending)} queued effect(s) and there "
            "is nothing to apply them yet (w1c). A queued effect nobody drains "
            "is a feature whose whole point was silently dropped."
        )
