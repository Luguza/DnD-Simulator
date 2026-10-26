"""The clock: a totally ordered timeline, the durations expressed against it,
and the once-per budgets that clear at its boundaries.

`TimeKey` is a position, `InitiativeKey` a place in the order, `Duration` a
relative deadline that `resolve()` turns into an absolute `Expiry`, and
`ResetManager` the once-per ledger. `docs/decisions.md` § w0d; DESIGN.md
§ Durations, § Initiative is a key, not an index.
"""

from collections.abc import Hashable
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Final

__all__ = [
    "Duration",
    "Expiry",
    "InitiativeKey",
    "Reset",
    "ResetManager",
    "TimeKey",
    "TurnPhase",
]


@unique
class TurnPhase(Enum):
    """The two hook boundaries of a turn, in the order they happen.

    Values are ordinals, not lowercase strings: `"end" < "start"`
    alphabetically (`docs/decisions.md` § w0d). Everything a creature does on
    its turn happens while the clock reads `START`.
    """

    START = 0
    END = 1


_Order = tuple[int, int, int, float, tuple[int, ...]]
"""The comparison form of an `InitiativeKey`: its tie-break rule, as a tuple."""


@dataclass(frozen=True, slots=True)
class InitiativeKey:
    """A creature's place in the initiative order. Earlier in the order is
    *less than*.

    Not a seat index: derived from the roll and the roller, never the roster,
    so an arrival or a death moves no other key (DESIGN.md § Initiative is a
    key, not an index). Raises `ValueError` on a Dexterity score below 1, a
    `tiebreak` outside `[0, 1)` (NaN included) or a negative `after` step.

    The comparison *is* the tie-break rule, read top to bottom:

    1. **higher `initiative`** acts first. It may be negative.
    2. **higher `dexterity`** — the ability *score*, not the modifier.
    3. **a player character before a monster.**
    4. **lowest `tiebreak`** — one uniform draw from `[0, 1)`, per creature.
    5. **lowest `after`** — the summon chain, below.

    Steps 2 to 4 are a house rule (`docs/decisions.md` § w0d).

    `after` is empty for a creature that rolled its own initiative. It carries
    XPHB's summon rule instead — see `sharing_turn()`.
    """

    initiative: int
    dexterity: int
    player_character: bool
    tiebreak: float
    after: tuple[int, ...] = ()
    _order: _Order = field(init=False, repr=False, compare=False)
    """Precomputed comparison form, built once rather than per comparison."""

    def __post_init__(self) -> None:
        if self.dexterity < 1:
            raise ValueError(
                "initiative ties break on the Dexterity *score*, and the lowest "
                f"score a creature can have is 1, got {self.dexterity} — this is "
                "where passing the modifier instead of the score shows up"
            )
        # A NaN fails this too, which is the point: one NaN and the order stops
        # being an order.
        if not 0.0 <= self.tiebreak < 1.0:
            raise ValueError(
                f"a tiebreak is one uniform draw from [0, 1), got {self.tiebreak!r}"
            )
        if any(step < 0 for step in self.after):
            raise ValueError(f"a shared-turn step is counted from 0, got {self.after}")
        object.__setattr__(
            self,
            "_order",
            (
                -self.initiative,
                -self.dexterity,
                0 if self.player_character else 1,
                self.tiebreak,
                self.after,
            ),
        )

    def sharing_turn(self, order: int = 0) -> "InitiativeKey":
        """The place of a creature that shares this one's initiative count and
        takes its turn immediately afterwards.

        Appends `order` to `after`, which moves nobody: for a shared draw `t`,
        `(t, ()) < (t, (0,)) < (t, (0, 0)) < (t, (1,))`. `order` separates
        several creatures sharing one count, lowest first; the chain nests.
        `docs/decisions.md` § w0d.
        """
        return InitiativeKey(
            self.initiative,
            self.dexterity,
            self.player_character,
            self.tiebreak,
            (*self.after, order),
        )

    def __lt__(self, other: "InitiativeKey") -> bool:
        return self._order < other._order

    def __le__(self, other: "InitiativeKey") -> bool:
        return self._order <= other._order

    def __gt__(self, other: "InitiativeKey") -> bool:
        return self._order > other._order

    def __ge__(self, other: "InitiativeKey") -> bool:
        return self._order >= other._order

    def __str__(self) -> str:
        chain = "".join(f"+{step}" for step in self.after)
        return f"i{self.initiative}#{self.tiebreak:.6f}{chain}"


@dataclass(frozen=True, slots=True)
class TimeKey:
    """A position on the encounter timeline: whose turn, in which round, at
    which boundary.

    Ordered as play runs: round, then place in the order, then phase. Raises
    `ValueError` on a round below 1.
    """

    round: int
    initiative: InitiativeKey
    phase: TurnPhase

    def __post_init__(self) -> None:
        if self.round < 1:
            raise ValueError(
                f"the first round of an encounter is round 1, got {self.round}"
            )

    def _ordinal(self) -> tuple[int, _Order, int]:
        return (self.round, self.initiative._order, self.phase.value)

    def __lt__(self, other: "TimeKey") -> bool:
        return self._ordinal() < other._ordinal()

    def __le__(self, other: "TimeKey") -> bool:
        return self._ordinal() <= other._ordinal()

    def __gt__(self, other: "TimeKey") -> bool:
        return self._ordinal() > other._ordinal()

    def __ge__(self, other: "TimeKey") -> bool:
        return self._ordinal() >= other._ordinal()

    def __str__(self) -> str:
        return f"r{self.round}:{self.initiative}:{self.phase.name.lower()}"


@dataclass(frozen=True, slots=True)
class Expiry:
    """A resolved deadline: the boundary an effect ends on, and whether that
    boundary's own hooks still see it.

    `expires_before(now)` is asked before a boundary's hooks run and
    `expires_after(now)` once they have; `through_phase` decides only the
    first. Neither looks the anchor up (`docs/decisions.md` § w0d).
    """

    end: TimeKey
    through_phase: bool

    def expires_before(self, now: TimeKey) -> bool:
        """True when the effect must be gone before `now`'s hooks run."""
        return now > self.end or (now == self.end and not self.through_phase)

    def expires_after(self, now: TimeKey) -> bool:
        """True when the effect must be gone once `now`'s hooks have run."""
        return now >= self.end


@dataclass(frozen=True, slots=True)
class Duration:
    """How long an effect lasts, said the way the rules say it.

    A frozen value built by the named constructors; `resolve()` turns it into
    an absolute `Expiry`. `of_initiative` anchors the deadline to that
    creature's *next* turn; `None` means the turn already in progress. The
    anchor is an `InitiativeKey`, not a creature (`docs/decisions.md` § w0d).
    """

    of_initiative: InitiativeKey | None
    phase: TurnPhase
    through_phase: bool

    @classmethod
    def until_start_of_next_turn_of(cls, initiative: InitiativeKey) -> "Duration":
        """Reckless Attack's "until the start of your next turn".

        Removed *before* that turn's `on_turn_start` handlers run.
        """
        return cls(initiative, TurnPhase.START, through_phase=False)

    @classmethod
    def until_end_of_next_turn_of(cls, initiative: InitiativeKey) -> "Duration":
        """Rage's and Vex's "until the end of your next turn".

        Still attached while that turn's `on_turn_end` handlers run.
        """
        return cls(initiative, TurnPhase.END, through_phase=True)

    @classmethod
    def until_end_of_current_turn(cls) -> "Duration":
        """A rider's "until the end of this turn" — it dies with the turn it
        was applied on, whoever's turn that is."""
        return cls(None, TurnPhase.END, through_phase=True)

    def resolve(self, now: TimeKey) -> Expiry:
        """Pin this duration to the clock, giving the absolute deadline.

        The anchor's turn this round counts only if it has not begun
        (`anchor > now.initiative`); otherwise the deadline lands in the round
        after (`docs/decisions.md` § w0d).
        """
        anchor = self.of_initiative
        if anchor is None:
            end = TimeKey(now.round, now.initiative, self.phase)
        else:
            begun = anchor <= now.initiative
            end = TimeKey(now.round + 1 if begun else now.round, anchor, self.phase)
        return Expiry(end, self.through_phase)


@unique
class Reset(Enum):
    """When a once-per budget refills.

    - `TURN` — **any** turn boundary in initiative: D&D's "once per turn", so
      Sneak Attack can land on an opportunity attack during an enemy's turn and
      again on your own.
    - `OWN_TURN` — only when *your* turn begins: "once on your turn".
    """

    TURN = "turn"
    OWN_TURN = "own_turn"
    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"


_ANY_TURN_CLEARS: Final = frozenset({Reset.TURN})
_OWN_TURN_CLEARS: Final = frozenset({Reset.TURN, Reset.OWN_TURN})
_SHORT_REST_CLEARS: Final = frozenset({Reset.SHORT_REST})
_LONG_REST_CLEARS: Final = frozenset({Reset.SHORT_REST, Reset.LONG_REST})


class ResetManager:
    """The once-per ledger for one creature: which budgets are spent, and the
    boundaries that hand them back.

    One per creature, so the caller says whether a turn is its own. Each
    boundary clears exactly the scopes it names (`docs/decisions.md` § w0d).
    """

    __slots__ = ("_spent",)

    def __init__(self) -> None:
        self._spent: dict[Hashable, Reset] = {}

    def is_spent(self, key: Hashable) -> bool:
        """True while `key`'s budget is used up."""
        return key in self._spent

    def spend(self, key: Hashable, scope: Reset) -> None:
        """Use up `key`'s budget until the next `scope` boundary.

        Raises `ValueError` if the budget is already spent: callers ask
        `is_spent()` first.
        """
        if key in self._spent:
            raise ValueError(
                f"the once-per budget {key!r} is already spent; it comes back "
                f"at the next {self._spent[key].value} boundary"
            )
        self._spent[key] = scope

    def turn_started(self, *, own: bool) -> None:
        """A turn has begun. Clears `TURN`, and `OWN_TURN` too when it is the
        turn of the creature this ledger belongs to."""
        self._clear(_OWN_TURN_CLEARS if own else _ANY_TURN_CLEARS)

    def short_rest(self) -> None:
        """Clears `SHORT_REST`."""
        self._clear(_SHORT_REST_CLEARS)

    def long_rest(self) -> None:
        """Clears `LONG_REST`, and `SHORT_REST` with it — anything the rules
        return on a short rest is returned by a long one."""
        self._clear(_LONG_REST_CLEARS)

    def _clear(self, scopes: frozenset[Reset]) -> None:
        self._spent = {
            key: scope for key, scope in self._spent.items() if scope not in scopes
        }
