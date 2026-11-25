"""The statistics a result reports over its iterations."""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = ["Z_95", "Estimate", "estimate"]

Z_95: Final = 1.959963984540054
"""The standard normal quantile a 95% confidence interval spans."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """A sample's mean, its standard deviation, and a 95% normal-approximation
    confidence interval on the mean. The deviation and the interval's width
    are 0 for a sample of one."""

    mean: float
    stdev: float
    ci_low: float
    ci_high: float


def estimate(values: Sequence[float]) -> Estimate:
    """The `Estimate` of `values`. Raises `ValueError` for no values."""
    if not values:
        raise ValueError("an estimate needs at least one value")
    mean = math.fsum(values) / len(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    half = Z_95 * stdev / math.sqrt(len(values))
    return Estimate(mean, stdev, mean - half, mean + half)
