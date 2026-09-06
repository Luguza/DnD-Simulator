"""`PcgRng` rolls fair dice.

Statistical, but not flaky: every draw below comes from a fixed seed, so these
either pass for ever or fail for everyone. The thresholds are chosen so that a
*correct* generator would have to be extraordinarily unlucky to trip them, which
is the point — a biased d20 shifts every DPR number in the project by more than
most of the features it exists to compare, and it does so silently.
"""

from collections import Counter

import pytest

from dndsim.core.rng import PcgRng

# chi-square critical values at p = 0.001, from a standard table. A fair die
# exceeds these once in a thousand runs; ours is seeded, so it never will.
CHI2_CRITICAL = {5: 20.515, 19: 43.820}


@pytest.mark.parametrize(("sides", "draws"), [(6, 60_000), (20, 200_000)])
def test_faces_are_uniform(sides: int, draws: int) -> None:
    rng = PcgRng(20_240_101)
    counts = Counter(rng.roll(sides) for _ in range(draws))

    assert set(counts) == set(range(1, sides + 1))
    expected = draws / sides
    chi2 = sum((count - expected) ** 2 / expected for count in counts.values())
    assert chi2 < CHI2_CRITICAL[sides - 1]


def test_rejection_sampling_removes_the_modulo_bias() -> None:
    """The reason `roll` rejects the final partial block instead of taking the
    modulo of every word.

    At die sizes the rules use, the bias a bare `word % sides` would introduce is
    under 1 part in 10^8 and no test could see it. So this uses a die whose size
    makes it enormous: 3 * 2^30 faces over 2^32 words leaves a remainder of 2^30,
    so the bottom third of the faces gets two source words each and the rest one.
    A naive mapping therefore sends 2^31 of the 2^32 words — half the draws — into
    that bottom third, where a fair die sends a third. The same rejection is what
    keeps the d20 clean.
    """
    sides = 3 * 2**30
    rng = PcgRng(4)
    draws = [rng.roll(sides) for _ in range(4000)]

    in_doubled_region = sum(1 for face in draws if face <= sides // 3)
    assert 0.30 < in_doubled_region / len(draws) < 0.37
