"""The damage resolution order.

**immunity -> flat reductions -> halvings -> resistance -> vulnerability ->
temporary hit points -> hit points.** Features contribute to a
`MitigationLedger`; this module is the only code that acts on it.

Sources:

- immunity, resistance, vulnerability, temporary hit points, rounding down —
  XPHB Rules Glossary, under each of those names
- adjustments, then Resistance, then Vulnerability — XPHB, "Damage and Healing"
  (`DESIGN.md` § Structural prevention)
- two halvings stacking — XPHB Rules Glossary, "Resistance", the "counts once"
  clause (`docs/decisions.md` § w1e)
- immunity first, flat reductions before halvings, and the proportional spend
  of an untyped adjustment across a mixed-type hit — `docs/decisions.md` § w1e

A reduction carries no damage type and is spent across every surviving type:
debt #23.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from dndsim.core.rng import Rng
from dndsim.creatures.creature import Creature
from dndsim.creatures.effects import apply_pending
from dndsim.engine.dying import damaged_at_zero, dropped_to_zero
from dndsim.hooks.contexts import (
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
    """Where the two contexts of a damage event come from (`docs/decisions.md`
    § w0i)."""

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
    """Allocate both, every hit. The default."""

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

    Applies the crit dice/flat split (DESIGN.md § Structural prevention) and
    resolves `damage_type=None` to the attack's type (`docs/decisions.md`
    § w0f). The doubling is inlined rather than routed through
    `DamagePart.on_crit()` (`docs/decisions.md` § w0i).
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

    `amounts` is the per-type picture once every step has run; `total` is its
    sum, floored at zero.
    """

    amounts: Mapping[DamageType, int]
    total: int

    @property
    def types(self) -> frozenset[DamageType]:
        """The types that got through, for the log and the hooks.

        An untyped step can empty a type out of this set, not just immunity
        (`docs/decisions.md` § w1e).
        """
        return frozenset(t for t, amount in self.amounts.items() if amount > 0)


def _apportion(amounts: Mapping[DamageType, int], target: int) -> dict[DamageType, int]:
    """Spread `target` over the types in proportion, by Jefferson's method.

    Each point goes to the type maximising `amount / (points held + 1)`, ties by
    the larger component then the type's name (`docs/decisions.md` § w1e).
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

    **A reduction's dice are drawn from `rng`**, which must be the generator
    the damage itself was rolled from.
    """
    if ledger.untouched:
        # The clamp is not the identity on an empty ledger, so the shortcut
        # keeps it (`docs/decisions.md` § w0i).
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
    halved_by: str | None = None,
    critical: bool = False,
) -> int:
    """Deal damage to `defender` and return what it took.

    A veto from an `EARLY` or `FINAL` handler stops the whole event: nothing is
    spent and the return is zero. `on_damage_taken` does not fire when nothing
    landed, but does fire for a hit temporary hit points absorbed whole
    (`docs/decisions.md` § w0g2).

    `halved_by` is a halving the caller supplies, as the reason it is halved;
    it runs in the halvings step with every other halving (`docs/decisions.md`
    § w1g).

    `critical` is whether the damage is a Critical Hit's (XPHB, "Dropping to 0
    Hit Points" -> Damage at 0 Hit Points; `docs/decisions.md` § w1j).
    """
    incoming = contexts.incoming(defender=defender, attacker=attacker, amounts=amounts)
    if halved_by is not None:
        # Stamped, because a ledger mutator refuses an unattributed change;
        # seeded before the dispatch, so handlers read the ledger that is applied.
        incoming.stamp("engine.damage", None)
        incoming.halve(halved_by)
        incoming.stamp(None, None)
    defender.dispatch(ON_INCOMING_DAMAGE, incoming)
    apply_pending(incoming, defender, attacker)
    if incoming.vetoed:
        incoming.close()
        return 0
    mitigated = mitigate(amounts, incoming.mitigation, rng=rng)
    incoming.close()

    if not mitigated.total:
        return 0
    absorbed = min(defender.temp_hp, mitigated.total)
    defender.temp_hp -= absorbed
    landed = mitigated.total - absorbed
    hp = defender.hp
    if not hp:
        if landed:
            damaged_at_zero(defender, landed, critical=critical)
    elif landed:
        hp -= landed
        if hp > 0:
            defender.hp = hp
        else:
            defender.hp = 0
            dropped_to_zero(defender, attacker=attacker, overflow=-hp)

    taken = contexts.taken(
        defender=defender,
        attacker=attacker,
        amount=mitigated.total,
        damage_types=mitigated.types,
    )
    defender.dispatch(ON_DAMAGE_TAKEN, taken)
    apply_pending(taken, defender, attacker)
    taken.close()
    return mitigated.total
