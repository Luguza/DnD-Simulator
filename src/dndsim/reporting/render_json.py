"""The JSON renderer: a `SimResult` as the document the text renderer reads."""

from dndsim.reporting.result import SimResult

__all__ = ["render_json"]


def render_json(result: SimResult) -> str:
    """`result` as indented JSON, fields in the model's order."""
    return result.model_dump_json(indent=2)
