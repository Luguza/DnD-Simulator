"""The w0c contract: randomness is reproducible, injected, and honest.

The stream is pinned against `random.Random.getrandbits` and the face mapping
`core/rng.py` puts on top of it (`docs/decisions.md` § w0i).
"""

import pytest

from dndsim.core.rng import MersenneRng, Rng, ScriptedRng, ScriptedRngExhausted, mix

# The first six words of CPython's MT19937 seeded with 42 — the sequence
# `random.Random(42).getrandbits(32)` yields, which is the standard library's
# documented output:
#
#   0xa3b1799d 0x1c80317f 0x06671ad1 0xbdd640fb 0x46685257 0x3eb13b90
#
# `roll` maps a word to a face with `word % sides + 1`, rejecting the words in
# the final partial block (of which there are none this early in the stream), so
# those words are the d20 faces below — 0xa3b1799d % 20 + 1 = 14, and so on.
REFERENCE_SEED = 42
REFERENCE_WORDS = [
    0xA3B1799D,
    0x1C80317F,
    0x06671AD1,
    0xBDD640FB,
    0x46685257,
    0x3EB13B90,
]
REFERENCE_D20_FACES = [14, 8, 10, 4, 4, 13]
REFERENCE_D6_FACES = [4, 6, 6, 2, 2, 5]


def test_the_stream_is_pinned_to_the_word_source() -> None:
    """Seed 42 yields exactly the faces the standard library's words map to.

    Pins *which* sequence, not only that it repeats (`docs/decisions.md` § w0i).
    """
    rng = MersenneRng(REFERENCE_SEED)
    assert [rng.roll(20) for _ in range(6)] == REFERENCE_D20_FACES
    assert [word % 20 + 1 for word in REFERENCE_WORDS] == REFERENCE_D20_FACES


def test_the_face_mapping_is_pinned_too() -> None:
    rng = MersenneRng(REFERENCE_SEED)
    assert [rng.roll(6) for _ in range(6)] == REFERENCE_D6_FACES
    assert [word % 6 + 1 for word in REFERENCE_WORDS] == REFERENCE_D6_FACES


def test_the_same_seed_gives_the_same_stream() -> None:
    a, b = MersenneRng(7), MersenneRng(7)
    assert [a.roll(20) for _ in range(200)] == [b.roll(20) for _ in range(200)]


def test_different_seeds_give_different_streams() -> None:
    a, b = MersenneRng(7), MersenneRng(8)
    assert [a.roll(20) for _ in range(200)] != [b.roll(20) for _ in range(200)]


def test_a_generator_does_not_repeat_itself_immediately() -> None:
    rng = MersenneRng(1)
    draws = [rng.roll(1_000_000) for _ in range(64)]
    assert len(set(draws)) == 64


@pytest.mark.parametrize("sides", [2, 3, 4, 6, 8, 10, 12, 20, 100])
def test_every_roll_is_a_possible_face(sides: int) -> None:
    rng = MersenneRng(mix(99, sides))
    faces = {rng.roll(sides) for _ in range(2000)}
    assert faces <= set(range(1, sides + 1))
    # 2000 draws of a d100 leaving a face unseen has probability ~1e-7.
    assert faces == set(range(1, sides + 1))


def test_a_one_sided_die_is_legal_and_boring() -> None:
    assert MersenneRng(3).roll(1) == 1


@pytest.mark.parametrize("sides", [0, -1])
def test_a_die_needs_sides(sides: int) -> None:
    with pytest.raises(ValueError, match="at least one side"):
        MersenneRng(3).roll(sides)


def test_a_seed_is_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        MersenneRng(-1)


def test_a_huge_seed_is_accepted() -> None:
    """Seeds arrive from `mix`, which returns a full 64-bit word."""
    assert 1 <= MersenneRng(2**64 - 1).roll(20) <= 20


class TestMix:
    """`mix` derives a per-iteration seed (DESIGN.md § Performance)."""

    def test_it_is_a_pure_function_of_its_inputs(self) -> None:
        assert mix(42, 4271) == mix(42, 4271)

    def test_neighbouring_iterations_get_unrelated_seeds(self) -> None:
        # MT19937's `init_by_array` is not a hash, so `mix` must decorrelate
        # neighbouring seeds. Half the bits should move; above a third is clear.
        a, b = mix(42, 0), mix(42, 1)
        assert bin(a ^ b).count("1") > 21

    def test_neighbouring_master_seeds_get_unrelated_seeds(self) -> None:
        a, b = mix(0, 17), mix(1, 17)
        assert bin(a ^ b).count("1") > 21

    def test_ten_thousand_iterations_collide_with_nothing(self) -> None:
        assert len({mix(42, i) for i in range(10_000)}) == 10_000

    def test_the_master_seed_actually_matters(self) -> None:
        assert mix(1, 0) != mix(2, 0)

    @pytest.mark.parametrize(("seed", "index"), [(-1, 0), (0, -1)])
    def test_negative_inputs_are_rejected(self, seed: int, index: int) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            mix(seed, index)


def _iteration_stream(master_seed: int, index: int) -> list[int]:
    """What one simulation iteration would draw, given its index in the run."""
    rng = MersenneRng(mix(master_seed, index))
    return [rng.roll(20) for _ in range(8)]


def test_a_run_reproduces_across_worker_counts() -> None:
    """Every partition of a run into contiguous blocks of iteration indices
    yields the same per-iteration streams."""
    iterations = 40
    serial = [_iteration_stream(2024, i) for i in range(iterations)]

    for jobs in (1, 2, 3, 8):
        block = -(-iterations // jobs)
        by_worker = [
            [
                _iteration_stream(2024, i)
                for i in range(start, min(start + block, iterations))
            ]
            for start in range(0, iterations, block)
        ]
        merged = [stream for worker in by_worker for stream in worker]
        assert merged == serial, f"stream differs at --jobs={jobs}"


class TestScriptedRng:
    def test_it_returns_the_scripted_faces_in_order(self) -> None:
        rng = ScriptedRng([18, 11, 3, 4, 5, 6])
        assert [rng.roll(20), rng.roll(20)] == [18, 11]
        assert rng.roll(4) == 3
        assert [rng.roll(6) for _ in range(3)] == [4, 5, 6]

    def test_over_drawing_raises(self) -> None:
        rng = ScriptedRng([1])
        rng.roll(20)
        with pytest.raises(ScriptedRngExhausted, match="asked for another"):
            rng.roll(20)

    def test_an_empty_script_raises_on_the_first_roll(self) -> None:
        with pytest.raises(ScriptedRngExhausted):
            ScriptedRng([]).roll(20)

    def test_a_face_the_die_cannot_show_is_a_broken_test(self) -> None:
        with pytest.raises(ValueError, match="not a possible d6 roll"):
            ScriptedRng([18]).roll(6)

    def test_a_face_below_one_is_rejected_too(self) -> None:
        with pytest.raises(ValueError, match="not a possible d20 roll"):
            ScriptedRng([0]).roll(20)

    def test_exhaustion_is_observable(self) -> None:
        # Asserted as pairs rather than one property at a time: mypy narrows a
        # property access to the literal it was last compared against, and does
        # not widen it again across the `roll` that changes it.
        rng = ScriptedRng([1, 2])
        assert (rng.exhausted, rng.remaining) == (False, 2)
        rng.roll(20)
        assert (rng.exhausted, rng.remaining) == (False, 1)
        rng.roll(20)
        assert (rng.exhausted, rng.remaining) == (True, 0)

    def test_it_takes_any_iterable(self) -> None:
        rng = ScriptedRng(face for face in (5, 6))
        assert [rng.roll(6), rng.roll(6)] == [5, 6]

    def test_a_die_needs_sides(self) -> None:
        with pytest.raises(ValueError, match="at least one side"):
            ScriptedRng([1]).roll(0)


@pytest.mark.parametrize("rng", [MersenneRng(1), ScriptedRng([1, 2, 3])])
def test_both_generators_satisfy_the_protocol(rng: Rng) -> None:
    """Both implementations satisfy the `Rng` protocol."""
    assert 1 <= rng.roll(20) <= 20
