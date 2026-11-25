"""The JSON renderer: `SimResult.to_json()`, the form every renderer reads."""

from dndsim.reporting.result import SimResult

__all__ = ["render_json"]


def render_json(result: SimResult) -> str:
    """`result` as JSON."""
    return result.to_json()
