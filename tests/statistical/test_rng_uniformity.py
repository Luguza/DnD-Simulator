"""`MersenneRng` rolls fair dice.

Every draw comes from a fixed seed, so these tests are deterministic.
"""

from collections import Counter

import pytest

from dndsim.core.rng import MersenneRng

# chi-square critical values at p = 0.001, from a standard table. A fair die
# exceeds these once in a thousand runs; ours is seeded, so it never will.
CHI2_CRITICAL = {5: 20.515, 19: 43.820}


@pytest.mark.parametrize(("sides", "draws"), [(6, 60_000), (20, 200_000)])
def test_faces_are_uniform(sides: int, draws: int) -> None:
    rng = MersenneRng(20_240_101)
    counts = Counter(rng.roll(sides) for _ in range(draws))

    assert set(counts) == set(range(1, sides + 1))
    expected = draws / sides
    chi2 = sum((count - expected) ** 2 / expected for count in counts.values())
    assert chi2 < CHI2_CRITICAL[sides - 1]


def test_rejection_sampling_removes_the_modulo_bias() -> None:
    """`roll` shows no modulo bias on a die whose size makes it enormous.

    3 * 2^30 faces over 2^32 words leaves a remainder of 2^30, so under a bare
    `word % sides` the bottom third of the faces gets two source words each and
    the rest one: half the draws land in that third, where a fair die sends a
    third (`docs/decisions.md` § w0c).
    """
    sides = 3 * 2**30
    rng = MersenneRng(4)
    draws = [rng.roll(sides) for _ in range(4000)]

    in_doubled_region = sum(1 for face in draws if face <= sides // 3)
    assert 0.30 < in_doubled_region / len(draws) < 0.37
