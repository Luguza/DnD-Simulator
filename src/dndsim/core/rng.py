"""Randomness — the one place in the project allowed to be random.

Determinism is the backbone of the whole design (DESIGN.md § The five
load-bearing ideas, 5): seed + build hash + engine version + content version
reproduces any run byte for byte, which is what makes `trace`, golden tests and
regression diffs possible. Three decisions here are what buy that:

**One protocol, injected, never ambient.** Everything that rolls takes an `Rng`.
There is no module-level generator and no global seed, so two simulations in one
process cannot interleave their streams.

**A generator we own the exact output of.** `PcgRng` implements PCG32 (XSH RR
64/32, O'Neill 2014) rather than delegating to `random` or `numpy`. That is a
deliberate exception to "libraries before reimplementing": the requirement is not
"random numbers", it is *this* sequence of die faces, still reproducible years
from now from an archived `SimResult`. `random.Random` is seed-stable but the
mapping from its bit stream to a die face is a CPython implementation detail, and
`numpy.random.Generator` costs microseconds per call against a budget of ~50k
attack resolutions/sec/core. PCG32 is ~15 lines of specified arithmetic, so we
pin it here and the archived seed keeps meaning what it meant.

**Seeding is per iteration, never per worker** (DESIGN.md § Performance). Callers
build `PcgRng(mix(master_seed, iteration_index))`, so iteration 4,271 draws the
same stream whether it ran on worker 0 of 1 or worker 6 of 8 — which is why
`--jobs` is absent from the reproducibility contract.

`import random` is banned everywhere else in the tree and the linter (`dndsim
check`, w3a) enforces it. This module does not import it either: an unseeded run
gets its master seed from the caller (the CLI), which is where "no seed given"
is a policy decision rather than a hidden source of entropy.
"""

from collections.abc import Iterable
from typing import Final, Protocol

__all__ = [
    "PcgRng",
    "Rng",
    "ScriptedRng",
    "ScriptedRngExhausted",
    "mix",
]

_MASK64: Final = 0xFFFF_FFFF_FFFF_FFFF
_MASK32: Final = 0xFFFF_FFFF

_PCG_MULTIPLIER: Final = 6364136223846793005
"""The PCG32 LCG multiplier, from the reference implementation."""

_PCG_DEFAULT_STREAM: Final = 1442695040888963407
"""The reference implementation's default stream selector. One stream is all a
run needs — separation between iterations comes from `mix`, not from streams."""

_SPLITMIX_GAMMA: Final = 0x9E37_79B9_7F4A_7C15
_SPLITMIX_A: Final = 0xBF58_476D_1CE4_E5B9
_SPLITMIX_B: Final = 0x94D0_49BB_1331_11EB


class Rng(Protocol):
    """What the engine is allowed to ask of randomness: one die at a time.

    The primitive is a die face rather than a raw word because that is the unit a
    golden test scripts (DESIGN.md § Golden tests) — `ScriptedRng([18, 11, 3])`
    reads as "18, 11, 3" in the test, not as three 32-bit integers nobody can
    check. Multi-die expressions loop over this; see `core.dice`.
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

    Consecutive iterations must not draw correlated streams, and `PcgRng`'s
    seeding advances the LCG only twice — so `master_seed + i` would hand
    neighbouring iterations neighbouring states. Two SplitMix64 rounds decorrelate
    the pair first: flipping one bit of either input changes about half the bits
    of the result.

    The mapping is part of the reproducibility contract. Changing it changes every
    archived run's meaning, so it changes only with the engine version.
    """
    if master_seed < 0:
        raise ValueError(f"master_seed must be non-negative, got {master_seed}")
    if iteration_index < 0:
        raise ValueError(f"iteration_index must be non-negative, got {iteration_index}")
    return _splitmix64((_splitmix64(master_seed) + iteration_index) & _MASK64)


class PcgRng:
    """PCG32 (XSH RR 64/32) — the production generator.

    Two `PcgRng`s built from the same seed yield identical streams for ever; that
    is the entire promise. Die faces are drawn by rejection sampling rather than a
    bare modulo, so every face of every die is exactly equally likely — a 2.4%
    bias on a d20 would move a DPR number by more than most of the features the
    simulator exists to compare.
    """

    __slots__ = ("_inc", "_state")

    def __init__(self, seed: int, stream: int = _PCG_DEFAULT_STREAM) -> None:
        if seed < 0:
            raise ValueError(f"seed must be non-negative, got {seed}")
        if stream < 0:
            raise ValueError(f"stream must be non-negative, got {stream}")
        # The reference seeding routine: state 0, step, add the seed, step.
        self._inc = ((stream << 1) | 1) & _MASK64
        self._state = 0
        self._next_word()
        self._state = (self._state + (seed & _MASK64)) & _MASK64
        self._next_word()

    def _next_word(self) -> int:
        """Advance the LCG and return the permuted 32-bit output."""
        state = self._state
        self._state = (state * _PCG_MULTIPLIER + self._inc) & _MASK64
        xorshifted = (((state >> 18) ^ state) >> 27) & _MASK32
        rot = state >> 59
        return ((xorshifted >> rot) | (xorshifted << (-rot & 31))) & _MASK32

    def roll(self, sides: int) -> int:
        if sides < 1:
            raise ValueError(f"a die needs at least one side, got {sides}")
        # 2**32 is not a multiple of `sides`, so the last partial block of the
        # word range is discarded instead of being folded onto the low faces.
        # For a d20 that rejects 16 words in 2**32 — an unmeasurable cost for an
        # exactly uniform die.
        threshold = 0x1_0000_0000 % sides
        while True:
            word = self._next_word()
            if word >= threshold:
                return word % sides + 1


class ScriptedRngExhausted(RuntimeError):
    """A `ScriptedRng` was asked for more dice than the test scripted.

    Raised rather than falling back to real randomness: a golden test that draws
    an unscripted die is asserting on a number nobody chose, and the failure that
    would eventually surface says nothing about where the extra roll came from.
    """


class ScriptedRng:
    """The golden-test generator: die faces are supplied, not drawn.

    Faces are consumed left to right, one per `roll`, so a test reads as the
    sequence of dice the engine is expected to roll — and a change in *how many*
    dice a feature rolls fails as loudly as a change in the total:

        rng = ScriptedRng([18, 11, 3, 4, 5, 6])   # 2d20 adv, d4 dagger, 3d6 sneak

    A scripted face outside the die's range is a broken test rather than an
    unusual roll, so it raises too.
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
        """Whether every scripted face has been consumed.

        A golden asserting this has pinned the exact number of dice rolled, not
        just the total — which is what catches a feature that stops rolling one.
        """
        return self._position >= len(self._faces)

    @property
    def remaining(self) -> int:
        """How many scripted faces are still unconsumed."""
        return len(self._faces) - self._position
