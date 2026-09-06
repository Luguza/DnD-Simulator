"""The w0c contract: randomness is reproducible, injected, and honest.

Determinism is the property every other guarantee in the project rests on — the
`trace` command, every golden test, every regression diff and every archived
`SimResult` (DESIGN.md § What is a file). None of those fails loudly when the
generator drifts; they just quietly stop meaning what they meant. So the stream
itself is pinned here, against an oracle outside this repository.
"""

import pytest

from dndsim.core.rng import PcgRng, Rng, ScriptedRng, ScriptedRngExhausted, mix

# The first six 32-bit outputs of the PCG32 reference implementation seeded
# `pcg32_srandom_r(&rng, 42u, 54u)`, as printed by O'Neill's `pcg32-demo.c`:
#
#   0xa15c02b7 0x7b47f409 0xba1d3330 0x83d2f293 0xbfa4784b 0xcbed606e
#
# `roll` maps a word to a face with `word % sides + 1` (rejecting the words in
# the final partial block, of which there are none this early in the stream), so
# those words are the d20 faces below — 0xa15c02b7 % 20 + 1 = 4, and so on.
REFERENCE_SEED = 42
REFERENCE_STREAM = 54
REFERENCE_D20_FACES = [4, 18, 5, 16, 16, 7]
REFERENCE_D6_FACES = [4, 4, 3, 2, 2, 5]


def test_pcg_matches_the_reference_implementation() -> None:
    """The one assertion here with an oracle the project cannot fudge.

    Every other test in this file would still pass if `PcgRng` generated a
    different sequence, as long as it did so repeatably. This one says *which*
    sequence, so an "innocent" refactor of the permutation is a failure rather
    than a silent invalidation of every result ever archived.
    """
    rng = PcgRng(REFERENCE_SEED, REFERENCE_STREAM)
    assert [rng.roll(20) for _ in range(6)] == REFERENCE_D20_FACES


def test_the_face_mapping_is_pinned_too() -> None:
    rng = PcgRng(REFERENCE_SEED, REFERENCE_STREAM)
    assert [rng.roll(6) for _ in range(6)] == REFERENCE_D6_FACES


def test_the_same_seed_gives_the_same_stream() -> None:
    a, b = PcgRng(7), PcgRng(7)
    assert [a.roll(20) for _ in range(200)] == [b.roll(20) for _ in range(200)]


def test_different_seeds_give_different_streams() -> None:
    a, b = PcgRng(7), PcgRng(8)
    assert [a.roll(20) for _ in range(200)] != [b.roll(20) for _ in range(200)]


def test_a_generator_does_not_repeat_itself_immediately() -> None:
    rng = PcgRng(1)
    draws = [rng.roll(1_000_000) for _ in range(64)]
    assert len(set(draws)) == 64


@pytest.mark.parametrize("sides", [2, 3, 4, 6, 8, 10, 12, 20, 100])
def test_every_roll_is_a_possible_face(sides: int) -> None:
    rng = PcgRng(mix(99, sides))
    faces = {rng.roll(sides) for _ in range(2000)}
    assert faces <= set(range(1, sides + 1))
    # 2000 draws of a d100 leaving a face unseen has probability ~1e-7.
    assert faces == set(range(1, sides + 1))


def test_a_one_sided_die_is_legal_and_boring() -> None:
    assert PcgRng(3).roll(1) == 1


@pytest.mark.parametrize("sides", [0, -1])
def test_a_die_needs_sides(sides: int) -> None:
    with pytest.raises(ValueError, match="at least one side"):
        PcgRng(3).roll(sides)


def test_a_seed_is_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        PcgRng(-1)


def test_a_stream_is_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        PcgRng(1, -1)


def test_a_huge_seed_is_accepted() -> None:
    """Seeds arrive from `mix`, which returns a full 64-bit word."""
    assert 1 <= PcgRng(2**64 - 1).roll(20) <= 20


class TestMix:
    """Per-iteration seeding — the reason `--jobs` is absent from the
    reproducibility contract (DESIGN.md § Performance)."""

    def test_it_is_a_pure_function_of_its_inputs(self) -> None:
        assert mix(42, 4271) == mix(42, 4271)

    def test_neighbouring_iterations_get_unrelated_seeds(self) -> None:
        # `PcgRng` seeding advances its LCG only twice, so seeds that differ by
        # one would hand neighbouring iterations neighbouring streams. Half the
        # bits should move; anything above a third is comfortably decorrelated.
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
    rng = PcgRng(mix(master_seed, index))
    return [rng.roll(20) for _ in range(8)]


def test_a_run_reproduces_across_worker_counts() -> None:
    """The `--jobs` guarantee, expressed at the only level w0c can express it.

    A worker is handed a contiguous block of iteration indices. Because the seed
    is derived from the index and never from the worker, every partition of the
    same run produces the same per-iteration streams — which is what makes
    `--jobs` absent from the provenance recorded in a `SimResult`.
    """
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


@pytest.mark.parametrize("rng", [PcgRng(1), ScriptedRng([1, 2, 3])])
def test_both_generators_satisfy_the_protocol(rng: Rng) -> None:
    """`Rng` is what the engine is typed against; both implementations are
    substitutable for it, which is the whole point of the protocol."""
    assert 1 <= rng.roll(20) <= 20
