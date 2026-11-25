"""The text renderer: read off the JSON form, never off `SimResult`, so the
two cannot disagree (DESIGN.md § What is a file, and what is not)."""

import json
from collections.abc import Callable, Mapping
from typing import Any, Final

from dndsim.reporting.render_json import render_json
from dndsim.reporting.result import SimResult

__all__ = ["RENDERERS", "render_text"]


def render_text(result: SimResult) -> str:
    """`result` for a terminal, from its JSON form."""
    document = json.loads(render_json(result))
    provenance = document["provenance"]
    seed = provenance["seed"]
    lines = [
        f"iterations  {len(document['iterations'])}"
        + ("" if seed is None else f"  (seed {seed})"),
        _estimate("DPR", document["dpr"]),
        _estimate("damage", document["damage"]),
        _estimate("rounds", document["rounds"]),
        f"party wins  {document['win_rate']:.1%}",
        f"actions per round  {document['actions_per_round']:.2f}",
        f"attacks per round  {document['attacks_per_round']:.2f}",
        f"engine {provenance['engine_version']}  "
        f"content {provenance['content_version']}  "
        f"build {provenance['build_hash'][:12]}",
    ]
    return "\n".join(lines) + "\n"


def _estimate(label: str, value: Mapping[str, Any]) -> str:
    return (
        f"{label:<6} {value['mean']:.2f}  (sd {value['stdev']:.2f}, "
        f"95% CI {value['ci_low']:.2f}-{value['ci_high']:.2f})"
    )


RENDERERS: Final[Mapping[str, Callable[[SimResult], str]]] = {
    "text": render_text,
    "json": render_json,
}
"""Each `--format` by its name."""
