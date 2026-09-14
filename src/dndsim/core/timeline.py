"""The clock: a totally ordered timeline, the durations expressed against it,
and the once-per budgets that clear at its boundaries.

Four things live here because they are the same idea seen from four sides.

**`TimeKey` is a position, not a duration.** `(round, initiative, phase)`
compares in exactly the order the encounter runs, so "has this expired?" is
`now >= end` rather than bespoke arithmetic in every effect. Getting the *clock*
right once is what lets ~630 features never compute a deadline.

**`InitiativeKey` is a place in the order, not a seat number.** The population
of an encounter changes while it runs — a summoned creature enters, a dead one
leaves — and a dense index renumbers everyone below the change, so every
deadline already resolved against the old numbering silently starts naming a
different creature's turn. The key is derived from the roll and the roller and
never from the roster, so adding or removing a creature changes no other
creature's key, and a key stays a valid position after its creature is gone.

**`Duration` is declarative and relative; `Expiry` is absolute.** A feature says
`until_end_of_next_turn_of(...)` and never names a round number; the duration
resolves against the clock at application time (DESIGN.md § Durations). They are
two types on purpose: after resolution the deadline is a `TimeKey` *plus* the
`through_phase` flag that says whether the boundary's own hooks still see the
effect, and separating them would let a caller carry one without the other.

**`Reset` and `ResetManager` are the same boundaries seen as budgets.** The
`once_per=` argument on a hook is a promise about a boundary, and `TURN` vs
`OWN_TURN` is the likeliest subtle bug in the project (CLAUDE.md): "once per
turn" in D&D means once per *any* turn — which is why Sneak Attack lands on an
opportunity attack during an enemy's turn and again on your own — while "once on
your turn" means only yours.

What is deliberately absent:

- **No `Clock` object.** The engine's turn loop (`engine/turn.py`, w1f) owns the
  current `TimeKey` and passes it in. A second mutable clock here would be a
  second source of truth for the one fact the whole simulation is ordered by.
- **No roster.** Which creature holds which `InitiativeKey`, which of them are
  still standing, and the roll that produced a key are the encounter's business
  (`engine/turn.py`, w2c). This module knows only that the keys are totally
  ordered, which is all a deadline needs to know.
- **No multi-round anchor.** Every duration below ends on a turn boundary one
  turn ahead, because every duration W1 needs does (Rage, Reckless Attack, Vex).
  A "1 minute" spell wants a `turns: int` field; it is an additive field with a
  default when W4 needs it, and speculating on it now would ship untested
  arithmetic.
- **No "permanent" duration.** Prone lasts until you stand up, which is not a
  deadline at all — such an effect carries no `Duration` rather than one that
  never fires.

Sources: XPHB (2024) "Playing the Game" — combat is a sequence of rounds, each
round giving every combatant one turn in initiative order; the Rules Glossary
duration phrasings "until the start of your next turn" and "until the end of
your next turn".
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

    The values are **ordinals rather than the lowercase strings the other
    enumerations carry** (`rules/enums.py`), because this one is a position on a
    clock and `"end" < "start"` alphabetically. It is still a plain `Enum` and
    not an `IntEnum`, so `phase == 0` remains a mypy `comparison-overlap` error;
    the log and the `SimResult` JSON serialise `phase.name.lower()`.

    Only the boundaries are named. There is no `MIDDLE`: everything a creature
    does on its turn happens while the clock reads `START`, and an effect that
    ends "at the end of your turn" is ordered against `END` by the flag on
    `Expiry`, not by a third phase.
    """

    START = 0
    END = 1


_Order = tuple[int, int, int, float, tuple[int, ...]]
"""The comparison form of an `InitiativeKey`: its tie-break rule, as a tuple."""


@dataclass(frozen=True, slots=True)
class InitiativeKey:
    """A creature's place in the initiative order. Earlier in the order is
    *less than*.

    Not an index. An index into the living combatants is wrong the first time
    the population changes: a summon entering at the top of the order, or a dead
    creature leaving the middle of it, renumbers every seat below it, and every
    `Expiry` already resolved quietly starts naming a different creature's turn.
    This key is derived from the roll and the roller, never from the roster,
    which buys three properties the clock depends on:

    - a creature entering or leaving changes **no other creature's key**, so
      deadlines resolved before the change stay correct;
    - a key stays a **valid position** once its creature is gone — the expiry
      sweep compares against it and never looks it up, so an effect anchored to
      a creature that has died still ends when its turn would have come;
    - the order is **total**, so `sorted()` over these keys is the order of play.

    The comparison *is* the tie-break rule, read top to bottom:

    1. **higher `initiative`** acts first — the d20 + Dexterity modifier (plus
       Alert and the rest) rolled at the top of the encounter. It may be
       negative: a natural 1 at Dexterity 1 is -4.
    2. **higher `dexterity`** — the ability *score*, not the modifier.
       `dexterity=3` is a creature that can barely move, not a +3 bonus.
    3. **a player character before a monster.**
    4. **lowest `tiebreak`** — one uniform draw from `[0, 1)`, per creature.
    5. **lowest `after`** — the summon chain, below.

    Steps 2 to 4 are a stated house rule, not a quotation. XPHB leaves a tie to
    the table ("the DM decides the order"), which is not a decision a simulator
    can make ten thousand times a second; what it needs instead is a rule that
    is total, deterministic and reproducible from a seed. Step 3 hands ties to
    the party, a small systematic bias in the PCs' favour — constant across the
    builds being compared, and the opposite convention is a bias too.

    `tiebreak` is **one independent draw per creature**, not a position in a
    shuffle of the entrants, and that is the point: a creature joining
    mid-encounter draws its own and lands in the order without anybody else
    being redrawn or renumbered. A shuffle has no honest answer for a joiner —
    it can only append it last among the tied, which is not random. Two equal
    draws are possible in principle and are what a shuffle buys protection
    against; at the 2**-32 granularity below that is one encounter in ~2*10**7
    with twenty combatants, against a consequence (two tied creatures ordered
    arbitrarily but still deterministically) that a shuffle's cost does not
    justify.

    `engine/turn.py` (w2c) draws it as `(rng.roll(2**32) - 1) / 2**32`, which
    keeps `Rng`'s primitive a die face — a golden test scripts the face, and the
    protocol gains no float method it cannot script. The quotient is a dyadic
    rational, so it is exact in binary and round-trips through the event log.
    `2**32` is also the widest die the production generator can serve: `roll()`
    draws one 32-bit word per face (`MersenneRng`, as `PcgRng` did before w0i).

    `after` is empty for a creature that rolled its own initiative. It carries
    XPHB's summon rule instead — see `sharing_turn()`.
    """

    initiative: int
    dexterity: int
    player_character: bool
    tiebreak: float
    after: tuple[int, ...] = ()
    _order: _Order = field(init=False, repr=False, compare=False)
    """Precomputed comparison form. A key is built once per creature per
    encounter and compared once per attached effect per boundary, so the tuple
    is assembled here rather than on every comparison."""

    def __post_init__(self) -> None:
        if self.dexterity < 1:
            raise ValueError(
                "initiative ties break on the Dexterity *score*, and the lowest "
                f"score a creature can have is 1, got {self.dexterity} — this is "
                "where passing the modifier instead of the score shows up"
            )
        # A NaN fails this too, which is the point: one NaN and the order below
        # stops being an order at all.
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

        This is XPHB's summon rule — the summoned creature "shares your
        Initiative count, but it takes its turn immediately after yours" — and
        it is the one case a single number cannot express, because there is no
        value guaranteed to sit between two adjacent draws. Appending a step to
        `after` always gives one, and moves nobody: for a shared draw `t`,
        `(t, ()) < (t, (0,)) < (t, (0, 0)) < (t, (1,))`, and all of them still
        sit inside the count `t` won.

        `order` separates several creatures sharing one count, lowest first; the
        chain nests, so a summon's own summon lands behind it. A summon that
        rolls its own initiative instead gets an ordinary key with no chain.
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

    `initiative` is the acting creature's place in the order — an
    `InitiativeKey`, not a seat number and not the bare roll. Places ascend as
    the round advances, which is what makes the natural ordering of this tuple
    the ordering of play.

    Frozen, `slots`, and compared through one tuple, because comparison is the
    entire mechanism: the effect-expiry sweep runs at every boundary and asks
    nothing else.
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

    `through_phase` is the load-bearing half (DESIGN.md § Durations), and the two
    predicates below are what the engine's sweep calls — once before a
    boundary's hooks run and once after, which is precisely the difference the
    flag encodes:

    - **Rage**, "until the end of your next turn", `through_phase=True`. It is
      still attached while that turn's `on_turn_end` handlers run, so damage
      taken during your own end of turn is still resisted, and it is gone
      immediately afterwards.
    - **Reckless Attack**, "until the start of your next turn",
      `through_phase=False`. It is gone *before* that turn's `on_turn_start`
      handlers run, so nothing on your own turn sees the advantage you granted
      the enemies a round ago.

    Naming the two sweeps rather than passing a boolean keeps the distinction at
    the call site in `engine/turn.py`, where a reviewer can see which one runs
    on which side of the dispatch.

    Both predicates compare, and neither looks the anchor up. That is what makes
    an effect anchored to a creature that has since died expire on schedule
    rather than outlive the encounter: a deadline is a position, and the clock
    passes positions whether or not anyone is still standing on them.
    """

    end: TimeKey
    through_phase: bool

    def expires_before(self, now: TimeKey) -> bool:
        """True when the effect must be gone before `now`'s hooks run."""
        return now > self.end or (now == self.end and not self.through_phase)

    def expires_after(self, now: TimeKey) -> bool:
        """True when the effect must be gone once `now`'s hooks have run.

        `through_phase` does not appear: by the time a boundary's handlers have
        finished, everything due to end at or before it is over either way.
        """
        return now >= self.end


@dataclass(frozen=True, slots=True)
class Duration:
    """How long an effect lasts, said the way the rules say it.

    An immutable value object with named constructors — the **Factory Method**
    that keeps D&D's timing vocabulary out of feature code. One instance is
    shared by every application of the effect that declares it; `resolve()`
    turns it into an absolute `Expiry` against the clock of the moment.

    `of_initiative` anchors the deadline to a creature's *next* turn. `None`
    means the turn already in progress, whoever's it is — "until the end of this
    turn" names no owner because only one turn is ever in progress.

    The anchor is an `InitiativeKey` rather than a creature: `core/` sits below
    `creatures/` and may not import it. `creatures/effects.py` (w1c) passes
    `creature.initiative`, which is what DESIGN.md's
    `until_end_of_next_turn_of(c)` spells at the layer above. Anchoring on the
    key rather than on a seat number is also what lets the deadline outlive the
    change: the anchor cannot be renumbered by an arrival or a death.
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

        "Your next turn" is the next *turn*, not the next boundary of this
        phase: said on your own turn it means a round from now, and said before
        your turn has come round it means later today. So the anchor's turn this
        round counts only if it has not begun — `anchor > now.initiative` — and
        otherwise the deadline lands in the round after.

        The same comparison gives a creature that joined mid-encounter the right
        answer with no special case: a summon placed later in the order than the
        creature currently acting takes its first turn this round, and one
        placed earlier has already missed it.
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

    `TURN` and `OWN_TURN` are two members rather than one flag because they are
    the likeliest subtle bug in the project (CLAUDE.md, and the feature-authoring
    skill states it as a rule):

    - `TURN` — **any** turn boundary in initiative. This is D&D's "once per
      turn", and the reason Sneak Attack can land on an opportunity attack
      during an enemy's turn and again on your own.
    - `OWN_TURN` — only when *your* turn begins. This is "once on your turn".

    Recharge on a die roll (a monster's `{@recharge}`) is not here: it is not a
    boundary but a roll, so it belongs to `Recovery` with the resources (w1b).
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

    One per creature, so that `TURN` and `OWN_TURN` need no creature identity
    here — the turn loop already knows whose turn it is and says so. The
    alternative, a global ledger keyed by owner, would have every `once_per`
    check pay for a lookup into a structure the size of the encounter, on a path
    that runs for every handler of every attack.

    The dispatcher (w0g) asks `is_spent()` before calling a handler and calls
    `spend()` only when that handler returns `APPLIED`, which is what makes a
    skipped opportunity attack cost nothing (DESIGN.md § The uniform handler
    shape).

    Each boundary clears exactly the scopes it names and nothing wider: a rest
    does not touch turn budgets, because the next turn boundary hands those back
    before anything in initiative can ask for them.
    """

    __slots__ = ("_spent",)

    def __init__(self) -> None:
        self._spent: dict[Hashable, Reset] = {}

    def is_spent(self, key: Hashable) -> bool:
        """True while `key`'s budget is used up."""
        return key in self._spent

    def spend(self, key: Hashable, scope: Reset) -> None:
        """Use up `key`'s budget until the next `scope` boundary.

        Spending a budget that is already spent raises: the dispatcher checks
        `is_spent()` first, so reaching here twice is a bug in the caller rather
        than a state the ledger should quietly absorb.
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
