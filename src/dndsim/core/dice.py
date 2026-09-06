"""Dice notation — `2d6` in, a value object out.

A hand-rolled tokeniser rather than a library (DESIGN.md § Libraries before
reimplementing): the grammar is one production, it sits on the hottest path in
the simulator, and the *rejections* below are the point as much as the parse is.

**A dice expression carries no flat modifier, and `+3` is an error rather than a
term.** `DamagePart` splits `dice` from `flat` precisely so that a crit doubles
the first and never the second (DESIGN.md § Structural prevention over testing);
if `Dice.parse` accepted `"1d8+3"` the split would be bypassable by a string, and
the single most-cited invariant in the project would become a convention. So the
parser refuses, and says where the `+3` belongs.

Sources: XPHB (2024) "Playing the Game" — "dice notation ... The number of dice
comes first, then the die's letter", and the crit rule "roll the attack's damage
dice twice".
"""

import re
from dataclasses import dataclass
from typing import Final

from dndsim.core.rng import Rng

__all__ = ["Dice"]

_NOTATION: Final = re.compile(r"(?P<count>\d*)[dD](?P<sides>\d+)")
"""`[count]d<sides>`, count defaulting to 1 so that both `d20` and `1d20` parse.
Deliberately not anchored with `\\s`-tolerance or sign handling: the caller's
string is stripped first, and anything left over is a rejection, not a term."""


@dataclass(frozen=True, slots=True)
class Dice:
    """A pool of identical dice: `count` of them, each with `sides` faces.

    Frozen and shareable — one `Dice` per weapon or spell definition is read by
    every creature that uses it, on every attack, for the life of the run.
    """

    count: int
    sides: int

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"a dice pool needs at least one die, got {self.count}")
        if self.sides < 1:
            raise ValueError(f"a die needs at least one side, got {self.sides}")

    @classmethod
    def parse(cls, notation: str) -> "Dice":
        """Parse `2d6`, `d20`, `1D8`. Anything else raises `ValueError`.

        Failing here is failing at the earliest moment the information exists
        (DESIGN.md § Fail at the earliest moment): weapon and spell notation is
        read once at content load, so a typo in the dataset stops the run before
        the first iteration rather than mis-scoring every one of them.
        """
        text = notation.strip()
        match = _NOTATION.fullmatch(text)
        if match is None:
            raise ValueError(_rejection(notation, text))
        return cls(count=int(match["count"] or 1), sides=int(match["sides"]))

    def doubled(self) -> "Dice":
        """Twice the dice — a critical hit (XPHB: "roll the damage dice twice").

        Doubling the *pool* rather than the rolled total is what makes a crit
        re-roll dice instead of multiplying a number, and it is only ever reached
        through `DamagePart.dice`, so a feature's `flat` bonus cannot arrive here.
        """
        return Dice(count=self.count * 2, sides=self.sides)

    def roll(self, rng: Rng) -> int:
        """Roll the pool and return the total."""
        roll = rng.roll
        sides = self.sides
        return sum(roll(sides) for _ in range(self.count))

    def __str__(self) -> str:
        return f"{self.count}d{self.sides}"


def _rejection(notation: str, text: str) -> str:
    """Explain a rejected expression — pointedly, for the one common mistake."""
    if "+" in text or "-" in text:
        return (
            f"dice notation carries no flat modifier: {notation!r}. "
            "Dice double on a critical hit and flat bonuses never do, so they "
            "are separate fields — pass the dice here and the modifier as flat."
        )
    return f"not dice notation: {notation!r} (expected e.g. '2d6' or 'd20')"
