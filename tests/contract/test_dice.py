"""The w0c contract: dice notation parses, and refuses what it must refuse.

The rejections include any flat modifier (DESIGN.md § Structural prevention over
testing).
"""

import pytest

from dndsim.core.dice import Dice
from dndsim.core.rng import MersenneRng, ScriptedRng


class TestParsing:
    @pytest.mark.parametrize(
        ("notation", "count", "sides"),
        [
            ("2d6", 2, 6),
            ("1d8", 1, 8),
            ("d20", 1, 20),
            ("8d6", 8, 6),
            ("10d10", 10, 10),
            ("1d100", 1, 100),
            ("1D8", 1, 8),  # 5etools capitalises inconsistently
            ("  3d6  ", 3, 6),
        ],
    )
    def test_it_parses_the_notation_the_rules_use(
        self, notation: str, count: int, sides: int
    ) -> None:
        assert Dice.parse(notation) == Dice(count=count, sides=sides)

    def test_a_bare_die_means_one_die(self) -> None:
        assert Dice.parse("d20") == Dice.parse("1d20")

    @pytest.mark.parametrize("notation", ["1d8+3", "2d6 + 1", "1d8-1", "-1d8"])
    def test_a_flat_modifier_is_refused_by_name(self, notation: str) -> None:
        with pytest.raises(ValueError, match="carries no flat modifier"):
            Dice.parse(notation)

    @pytest.mark.parametrize(
        "notation", ["", "d", "6", "2d", "dd6", "2d6d6", "2 d 6", "fireball", "2x6"]
    )
    def test_anything_else_is_refused(self, notation: str) -> None:
        with pytest.raises(ValueError, match="not dice notation"):
            Dice.parse(notation)

    @pytest.mark.parametrize("notation", ["0d6", "1d0"])
    def test_an_empty_pool_or_a_sideless_die_is_refused(self, notation: str) -> None:
        with pytest.raises(ValueError, match="at least one"):
            Dice.parse(notation)

    def test_a_rejection_quotes_what_it_was_given(self) -> None:
        with pytest.raises(ValueError, match="'1d8 plus 3'"):
            Dice.parse("1d8 plus 3")


class TestConstruction:
    def test_it_is_frozen(self) -> None:
        dice = Dice(count=2, sides=6)
        with pytest.raises(AttributeError):
            dice.count = 3  # type: ignore[misc]

    def test_it_is_hashable_and_compares_by_value(self) -> None:
        assert len({Dice(2, 6), Dice(count=2, sides=6), Dice(3, 6)}) == 2

    @pytest.mark.parametrize(("count", "sides"), [(0, 6), (-1, 6), (2, 0), (2, -6)])
    def test_direct_construction_validates_too(self, count: int, sides: int) -> None:
        with pytest.raises(ValueError, match="at least one"):
            Dice(count=count, sides=sides)

    def test_it_renders_back_to_notation(self) -> None:
        assert str(Dice.parse("d20")) == "1d20"
        assert str(Dice(8, 6)) == "8d6"


class TestRolling:
    def test_it_rolls_one_die_per_die_in_the_pool(self) -> None:
        # 3d6 showing 4, 5, 6.
        rng = ScriptedRng([4, 5, 6])
        assert Dice(3, 6).roll(rng) == 15
        assert rng.exhausted

    def test_a_single_die_is_its_own_total(self) -> None:
        assert Dice(1, 8).roll(ScriptedRng([7])) == 7

    def test_the_total_stays_within_the_pool(self) -> None:
        rng = MersenneRng(11)
        dice = Dice(8, 6)
        for _ in range(500):
            assert 8 <= dice.roll(rng) <= 48

    def test_rolling_does_not_mutate_the_pool(self) -> None:
        dice = Dice(2, 6)
        dice.roll(MersenneRng(1))
        assert dice == Dice(2, 6)


class TestCritDoubling:
    def test_a_crit_doubles_the_dice(self) -> None:
        assert Dice.parse("2d6").doubled() == Dice.parse("4d6")

    def test_it_doubles_the_pool_rather_than_the_total(self) -> None:
        """XPHB: "roll the attack's damage dice twice" — four separate d6 for a
        critical 2d6, not one 2d6 total multiplied by two."""
        rng = ScriptedRng([1, 1, 6, 6])
        assert Dice(2, 6).doubled().roll(rng) == 14
        assert rng.exhausted

    def test_doubling_leaves_the_original_alone(self) -> None:
        dice = Dice(1, 8)
        assert dice.doubled() == Dice(2, 8)
        assert dice == Dice(1, 8)
