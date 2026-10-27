"""Dice notation — `2d6` in, a `Dice` value object out.

A dice expression carries no flat modifier: `"1d8+3"` raises
(`docs/decisions.md` § w0c). XPHB "Playing the Game", dice notation.
"""

import re
from dataclasses import dataclass
from typing import Final

from dndsim.core.rng import Rng

__all__ = ["Dice"]

_PARSED: "dict[str, Dice]" = {}
"""Notation already parsed, keyed by the exact string the caller passed.

Keyed before `strip()`, so `"2d6 "` and `"2d6"` are two entries for one value.
"""

_NOTATION: Final = re.compile(r"(?P<count>\d*)[dD](?P<sides>\d+)")
"""`[count]d<sides>`, count defaulting to 1 so that both `d20` and `1d20` parse.
Matched against the stripped string; anything left over is a rejection."""


@dataclass(frozen=True, slots=True)
class Dice:
    """A pool of identical dice: `count` of them, each with `sides` faces.

    Frozen, so one instance may be shared by every reader.
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

        Memoised: repeated notation returns the same frozen instance
        (`docs/decisions.md` § w0i).
        """
        cached = _PARSED.get(notation)
        if cached is not None:
            return cached
        text = notation.strip()
        match = _NOTATION.fullmatch(text)
        if match is None:
            raise ValueError(_rejection(notation, text))
        parsed = cls(count=int(match["count"] or 1), sides=int(match["sides"]))
        _PARSED[notation] = parsed
        return parsed

    def doubled(self) -> "Dice":
        """Twice the dice — a Critical Hit's pool (XPHB "Damage and Healing").

        `docs/decisions.md` § w0c.
        """
        return Dice(count=self.count * 2, sides=self.sides)

    def roll(self, rng: Rng) -> int:
        """Roll the pool and return the total.

        A loop, not `sum(...)` over a generator: the generator's frame costs more
        than it saves at typical pool sizes, on the hot path.
        """
        roll = rng.roll
        sides = self.sides
        total = 0
        for _ in range(self.count):
            total += roll(sides)
        return total

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
