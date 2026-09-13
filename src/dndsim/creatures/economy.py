"""`ActionEconomy` — what a creature still has to spend this turn.

A ledger of four things and nothing else: an Action, a Bonus Action, a Reaction
and a number of feet. It holds no rules about *what* may be bought with them —
that is `actions/` (w2b) — and no rules about *when* a turn begins — that is
`engine/turn.py` (w2c). What it owns is the one question the dispatcher and the
turn loop both ask, "can this still be paid for", and the refusal when something
is spent that was never there.

**Action Surge needs no special case, and that is the point** (DESIGN.md § APL).
A feature that grants another Action calls `grant()`; the turn loop re-polls the
APL after every action and finds an Action on the table again. Nothing in the
loop knows why, nothing in the feature knows about the loop, and the same
sentence covers a Thief's Fast Hands, a Haste action and a Legendary Action.

**Spending what you do not have raises** (`EconomyError`). Asking is free —
`can_spend()` is what `hooks/dispatch.py` calls for every costed handler and
what w2c filters its offers through — so reaching the refusal means a caller
decided not to ask. That is the shape of the bug M0 found by accident: an entry
charged an Action for a Bonus Action self-buff, and because it cost 0.07% of the
rate, nothing watching the rate could have found it (docs/decisions.md § The W0
plan review). A ledger that clamped at zero instead would have hidden it
completely.

**The reaction comes back at the start of your own turn**, which is why there is
one method for the boundary and not two. DESIGN.md § Failing loudly states the
invariant on the *turn* rather than the round: a creature high in initiative may
legitimately react on an enemy's turn, take its own turn, and react again in the
same round. `turn_started()` is the only thing that hands a reaction back, so the
legal sequence works and a second reaction before the creature's own turn has
begun is the refusal above.

**Movement is feet, not slots.** `ActionCost` deliberately has no member for it
(`rules/enums.py`), so it is a separate pair of methods here; a Dash grants more
of it through `grant_movement()`, exactly as Action Surge grants an Action.

Sources: XPHB (2024) "Combat" (Your Turn, Reactions, Movement); DESIGN.md § APL
and mid-attack decisions, § Failing loudly — layered; ROADMAP w1a, w2b, w2c.
"""

from types import MappingProxyType
from typing import Final

from dndsim.core.errors import EconomyError
from dndsim.rules.enums import ActionCost

__all__ = ["ActionEconomy"]

_ACTION: Final = ActionCost.ACTION
_BONUS_ACTION: Final = ActionCost.BONUS_ACTION

_NAMES: Final = MappingProxyType(
    {
        ActionCost.ACTION: "Action",
        ActionCost.BONUS_ACTION: "Bonus Action",
        ActionCost.REACTION: "Reaction",
    }
)
"""What the rules call each slot, for the errors a caller reads. `ActionCost`'s
own values are identifiers — `bonus_action` — because they are what an APL entry
and a `costs=` declaration are written in."""


class ActionEconomy:
    """One creature's unspent Actions, Bonus Actions, Reactions and movement.

    Satisfies the `Economy` protocol `hooks/dispatch.py` declares — `can_spend`
    and `spend` — which is the contract `w0g` deferred to this task.

    Counts rather than flags: Action Surge is a second Action, not a flag saying
    the first one is back, and a creature that has been granted two and spent
    one has one left in a way no boolean can say.
    """

    __slots__ = ("actions", "bonus_actions", "movement_ft", "reactions")

    def __init__(self) -> None:
        # A creature starts an encounter with its reaction in hand and nothing
        # else: it may take an opportunity attack in the first round before its
        # own turn has come round, and it may not take an Action, a Bonus
        # Action or a step until that turn begins. `turn_started()` is what
        # hands it the other three.
        self.actions = 0
        self.bonus_actions = 0
        self.reactions = 1
        self.movement_ft = 0

    # -- the `Economy` protocol ------------------------------------------------

    def can_spend(self, cost: ActionCost) -> bool:
        """Whether one of `cost` is still in hand. Asking costs nothing and
        changes nothing, which is what makes the refusal in `spend()` fair."""
        if cost is _ACTION:
            return self.actions > 0
        if cost is _BONUS_ACTION:
            return self.bonus_actions > 0
        return self.reactions > 0

    def spend(self, cost: ActionCost) -> None:
        """Spend one of `cost`, or raise if there was none to spend."""
        if cost is _ACTION:
            if self.actions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.actions -= 1
        elif cost is _BONUS_ACTION:
            if self.bonus_actions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.bonus_actions -= 1
        else:
            if self.reactions < 1:
                raise EconomyError(self._nothing_to_spend(cost))
            self.reactions -= 1

    # -- what features and the turn loop do to it ------------------------------

    def grant(self, cost: ActionCost, count: int = 1) -> None:
        """Hand the creature another Action, Bonus Action or Reaction.

        Action Surge, and every feature shaped like it. `count` is how many.
        """
        if count < 1:
            raise EconomyError(f"a grant of {count} {_NAMES[cost]}s grants nothing")
        if cost is _ACTION:
            self.actions += count
        elif cost is _BONUS_ACTION:
            self.bonus_actions += count
        else:
            self.reactions += count

    def can_move(self, distance_ft: int) -> bool:
        """Whether the creature still has that many feet of movement."""
        return distance_ft <= self.movement_ft

    def move(self, distance_ft: int) -> None:
        """Spend feet of movement, or raise if the creature has not got them.

        The five-foot step is the unit the mover walks in (`engine/triggers.py`),
        so this is called once per square rather than once per move — a route
        that runs out halfway is a real thing a creature does, and the turn loop
        finds out by asking `can_move()` before each step.
        """
        if distance_ft < 0:
            raise EconomyError(f"{distance_ft} ft is not a distance to move")
        if distance_ft > self.movement_ft:
            raise EconomyError(
                f"moving {distance_ft} ft costs more than the "
                f"{self.movement_ft} ft this creature has left this turn"
            )
        self.movement_ft -= distance_ft

    def grant_movement(self, distance_ft: int) -> None:
        """Add feet of movement: a Dash, and everything shaped like one."""
        if distance_ft < 1:
            raise EconomyError(f"a grant of {distance_ft} ft grants nothing")
        self.movement_ft += distance_ft

    def turn_started(self, movement_ft: int) -> None:
        """The creature's own turn has begun: everything back.

        `movement_ft` is the creature's speed *now* rather than a copy this
        object took when it was built, because a Rage, a Longstrider or a set
        of heavy armour changes it between turns and a ledger holding its own
        stale copy would be the second place a speed could be written.
        """
        if movement_ft < 0:
            raise EconomyError(f"a speed of {movement_ft} ft is not a speed")
        self.actions = 1
        self.bonus_actions = 1
        self.reactions = 1
        self.movement_ft = movement_ft

    def _nothing_to_spend(self, cost: ActionCost) -> str:
        if cost is ActionCost.REACTION:
            return (
                "this creature has already spent its reaction and has not "
                "started a turn since; a reaction comes back at the start of "
                "each of your own turns, never at the top of a round "
                "(DESIGN.md § Failing loudly). Ask `can_spend()` first."
            )
        return (
            f"this creature has no {_NAMES[cost]} left to spend. Asking is free: "
            "`can_spend()` answers before anything is spent, and the turn loop "
            "offers only what the economy can still pay for."
        )

    def __repr__(self) -> str:
        return (
            f"<economy {self.actions}a {self.bonus_actions}b "
            f"{self.reactions}r {self.movement_ft}ft>"
        )
