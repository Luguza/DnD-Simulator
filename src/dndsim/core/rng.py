"""Randomness — the one place in the project allowed to be random.

Determinism is the backbone of the whole design (DESIGN.md § The five
load-bearing ideas, 5): seed + build hash + engine version + content version
reproduces any run byte for byte, which is what makes `trace`, golden tests and
regression diffs possible. Three decisions here are what buy that:

**One protocol, injected, never ambient.** Everything that rolls takes an `Rng`.
There is no module-level generator and no global seed, so two simulations in one
process cannot interleave their streams.

**A word from the standard library, a die face of our own.** `MersenneRng`
draws 32-bit words from `random.Random.getrandbits(32)` — CPython's MT19937,
seeded and documented — and maps them to die faces itself, by rejection
sampling, in the eight lines below. The split is the whole design: what an
archived seed has to keep meaning is *this sequence of die faces*, and the face
mapping is the half of that this project owns and pins.

This module previously implemented PCG32 (XSH RR 64/32, O'Neill 2014) for the
stronger version of the same promise — an algorithm specified outside CPython,
checkable against a reference implementation. `w0i` measured the difference:
PCG32's 64-bit arithmetic in Python big-ints costs about eight times what
`getrandbits(32)` does, and at eight draws per attack resolution that is ~7% of
the entire simulation budget (`docs/decisions.md` § w0i). The trade was made
deliberately and it is not free — the residual dependency is CPython's MT
seeding routine (`init_by_array`), which is stable and documented but is not
ours. If the reproducibility of an archived run ever matters more than the
throughput, the PCG implementation is in this file's history and the `Rng`
protocol is what makes swapping it back a one-class change.

**Nothing but `getrandbits` is used**, deliberately. `randrange` and `randint`
map bits to a range through logic that *is* a CPython implementation detail, and
`random()` goes through floating point; the rejection sampling in `roll()`
exists so that neither is in the path between a seed and a die face.

**Seeding is per iteration, never per worker** (DESIGN.md § Performance). Callers
build `MersenneRng(mix(master_seed, iteration_index))`, so iteration 4,271 draws
the same stream whether it ran on worker 0 of 1 or worker 6 of 8 — which is why
`--jobs` is absent from the reproducibility contract.

`import random` is banned everywhere else in the tree and the linter (`dndsim
check`, w3a) enforces it; this module is the exception the rule is written
around. It imports `random.Random` — the class, instantiated per generator —
and never the module's global functions, so there is still no ambient generator
and no global seed, and an unseeded run gets its master seed from the caller
(the CLI), where "no seed given" is a policy decision rather than a hidden
source of entropy.
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

    Consecutive iterations must not draw correlated streams, and no generator's
    seeding routine is owed to us — `master_seed + i` hands neighbouring
    iterations neighbouring seeds and trusts the generator to scatter them. Two
    SplitMix64 rounds decorrelate the pair first: flipping one bit of either
    input changes about half the bits of the result. This is the separation
    mechanism between iterations, which is why the generator below needs no
    stream selector of its own.

    The mapping is part of the reproducibility contract. Changing it changes every
    archived run's meaning, so it changes only with the engine version.
    """
    if master_seed < 0:
        raise ValueError(f"master_seed must be non-negative, got {master_seed}")
    if iteration_index < 0:
        raise ValueError(f"iteration_index must be non-negative, got {iteration_index}")
    return _splitmix64((_splitmix64(master_seed) + iteration_index) & _MASK64)


_THRESHOLDS: dict[int, int] = {}
"""Rejection thresholds by die size, filled on first use.

`0x1_0000_0000 % sides` is a fixed function of the die and a big-integer
modulo; a project that rolls d20s several times per attack resolution should
compute each of the seven or eight distinct answers once.
"""


class MersenneRng:
    """The production generator: MT19937 words, our own die faces.

    Two `MersenneRng`s built from the same seed yield identical streams for
    ever; that is the entire promise. Die faces are drawn by rejection sampling
    rather than a bare modulo, so every face of every die is exactly equally
    likely. At d20 scale that correction is invisible — a bare modulo would
    over-represent 16 of the 2^32 words' faces by under 1 part in 10^8 — but it
    is a systematic error in the one primitive every number in the project is
    built from, and it costs 16 rejected words in 2^32 to not have it.

    **There is no stream selector.** PCG32 had one and this does not, because
    `random.Random` has no notion of a sequence and because the parameter was
    already vestigial: separation between iterations comes from `mix()`, which
    is what DESIGN.md § Performance specifies and what the reproducibility
    contract is stated in terms of.
    """

    __slots__ = ("_words",)

    def __init__(self, seed: int) -> None:
        if seed < 0:
            raise ValueError(f"seed must be non-negative, got {seed}")
        self._words = Random(seed)

    def roll(self, sides: int) -> int:
        """One face of a fair `sides`-sided die, in `1..sides`.

        The rejection threshold is memoised and the word is drawn through a
        bound method held on the instance: this is the primitive every number in
        the project is built from, and `w0i` measured eight draws per attack
        resolution.
        """
        if sides < 1:
            raise ValueError(f"a die needs at least one side, got {sides}")
        # 2**32 is not a multiple of `sides`, so the last partial block of the
        # word range is discarded instead of being folded onto the low faces.
        # For a d20 that rejects 16 words in 2**32 — an unmeasurable cost for an
        # exactly uniform die.
        threshold = _THRESHOLDS.get(sides)
        if threshold is None:
            threshold = _THRESHOLDS[sides] = _WORD_RANGE % sides
        word_of = self._words.getrandbits
        while True:
            word = word_of(32)
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
