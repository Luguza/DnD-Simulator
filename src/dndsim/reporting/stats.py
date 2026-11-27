"""The summary statistics a `SimResult` reports for each per-seed quantity."""

import math
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

__all__ = ["Z_95", "Stat", "summarise"]

Z_95: Final = 1.959963984540054
"""The two-sided 95% quantile of the standard normal."""


class Stat(BaseModel):
    """A sample's size, mean, sample standard deviation, and the normal
    approximation's 95% confidence interval for the mean."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    mean: float
    stdev: float
    ci_low: float
    ci_high: float


def summarise(values: Sequence[float]) -> Stat:
    """`values` summarised. Raises `ValueError` for an empty sample; one value
    has a standard deviation of 0."""
    n = len(values)
    if n == 0:
        raise ValueError("a statistic needs at least one value")
    mean = math.fsum(values) / n
    stdev = (
        math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (n - 1))
        if n > 1
        else 0.0
    )
    half = Z_95 * stdev / math.sqrt(n)
    return Stat(n=n, mean=mean, stdev=stdev, ci_low=mean - half, ci_high=mean + half)
