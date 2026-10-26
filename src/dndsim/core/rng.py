"""Randomness — the one module allowed to be random.

Everything that rolls takes an injected `Rng`; there is no module-level
generator and no global seed. `MersenneRng` draws 32-bit words from
`random.Random.getrandbits(32)` only, never `randrange`, `randint` or `random()`,
and maps them to die faces itself by rejection sampling. Callers seed it per
iteration with `mix(master_seed, iteration_index)` (DESIGN.md § Performance).
Why MT19937 replaced PCG32 is `docs/decisions.md` § w0i; the rest is § w0c.
"""

from collections.abc import Iterable
from random import Random
from typing import Final, Protocol

__all__ = [
    "MersenneRng",
    "Rng",
    "ScriptedRng",
    "ScriptedRngExhausted",
    "mix",
]

_MASK64: Final = 0xFFFF_FFFF_FFFF_FFFF

_WORD_RANGE: Final = 0x1_0000_0000
"""The number of distinct values `getrandbits(32)` returns."""

_SPLITMIX_GAMMA: Final = 0x9E37_79B9_7F4A_7C15
_SPLITMIX_A: Final = 0xBF58_476D_1CE4_E5B9
_SPLITMIX_B: Final = 0x94D0_49BB_1331_11EB


class Rng(Protocol):
    """What the engine is allowed to ask of randomness: one die face at a time.

    `docs/decisions.md` § w0c.
    """

    def roll(self, sides: int) -> int:
        """Return a face of a fair `sides`-sided die, in `1..sides`."""
        ...


def _splitmix64(x: int) -> int:
    """The SplitMix64 finaliser — an avalanche mixer for 64-bit words."""
    z = (x + _SPLITMIX_GAMMA) & _MASK64
    z = ((z ^ (z >> 30)) * _SPLITMIX_A) & _MASK64
    z = ((z ^ (z >> 27)) * _SPLITMIX_B) & _MASK64
    return z ^ (z >> 31)


def mix(master_seed: int, iteration_index: int) -> int:
    """Derive iteration `iteration_index`'s seed from the run's master seed.

    Two SplitMix64 rounds, so neighbouring inputs give uncorrelated seeds. Raises
    `ValueError` on a negative input. The mapping is part of the reproducibility
    contract and changes only with the engine version (`docs/decisions.md`
    § w0c).
    """
    if master_seed < 0:
        raise ValueError(f"master_seed must be non-negative, got {master_seed}")
    if iteration_index < 0:
        raise ValueError(f"iteration_index must be non-negative, got {iteration_index}")
    return _splitmix64((_splitmix64(master_seed) + iteration_index) & _MASK64)


_THRESHOLDS: dict[int, int] = {}
"""Rejection thresholds (`0x1_0000_0000 % sides`) by die size, filled on first use."""


class MersenneRng:
    """The production generator: MT19937 words, our own die faces.

    Two built from the same seed yield identical streams. Faces are exactly
    uniform (rejection sampling, not a bare modulo). Raises `ValueError` on a
    negative seed. There is no stream selector (`docs/decisions.md` § w0i).
    """

    __slots__ = ("_words",)

    def __init__(self, seed: int) -> None:
        if seed < 0:
            raise ValueError(f"seed must be non-negative, got {seed}")
        self._words = Random(seed)

    def roll(self, sides: int) -> int:
        """One face of a fair `sides`-sided die, in `1..sides`.

        Threshold memoised and the word source bound locally for the hot path
        (`docs/decisions.md` § w0i).
        """
        if sides < 1:
            raise ValueError(f"a die needs at least one side, got {sides}")
        # 2**32 is not a multiple of `sides`: the last partial block of the word
        # range is discarded rather than folded onto the low faces.
        threshold = _THRESHOLDS.get(sides)
        if threshold is None:
            threshold = _THRESHOLDS[sides] = _WORD_RANGE % sides
        word_of = self._words.getrandbits
        while True:
            word = word_of(32)
            if word >= threshold:
                return word % sides + 1


class ScriptedRngExhausted(RuntimeError):
    """A `ScriptedRng` was asked for more dice than the test scripted."""


class ScriptedRng:
    """The golden-test generator: die faces are supplied, not drawn.

    Faces are consumed left to right, one per `roll`:

        rng = ScriptedRng([18, 11, 3, 4, 5, 6])   # 2d20 adv, d4 dagger, 3d6 sneak

    `roll` raises `ScriptedRngExhausted` past the last face and `ValueError` on
    a scripted face outside the die's range.
    """

    __slots__ = ("_faces", "_position")

    def __init__(self, faces: Iterable[int]) -> None:
        self._faces = tuple(faces)
        self._position = 0

    def roll(self, sides: int) -> int:
        if sides < 1:
            raise ValueError(f"a die needs at least one side, got {sides}")
        if self._position >= len(self._faces):
            raise ScriptedRngExhausted(
                f"scripted {len(self._faces)} dice, asked for another (a d{sides})"
            )
        face = self._faces[self._position]
        self._position += 1
        if not 1 <= face <= sides:
            raise ValueError(
                f"scripted face {face} at index {self._position - 1} "
                f"is not a possible d{sides} roll"
            )
        return face

    @property
    def exhausted(self) -> bool:
        """Whether every scripted face has been consumed."""
        return self._position >= len(self._faces)

    @property
    def remaining(self) -> int:
        """How many scripted faces are still unconsumed."""
        return len(self._faces) - self._position
