"""The text renderer. It reads the JSON document, never the `SimResult`
object, so it cannot report what the JSON does not hold (DESIGN.md § What is a
file, and what is not)."""

import json
from typing import Any

__all__ = ["render_text"]


def render_text(document: str) -> str:
    """The run `document`, a `render_json()` output, as a few lines of text."""
    result = json.loads(document)
    provenance = result["provenance"]
    builds = ", ".join(build["name"] for build in provenance["builds"])
    seed = provenance["seed"]
    lines = [
        f"{builds} vs {provenance['scenario']['dummy']['name']}",
        f"{result['iterations']} iterations"
        + ("" if seed is None else f", seed {seed}")
        + f"; engine {provenance['engine_version']}"
        + f", content {provenance['content_version'] or 'unversioned'}",
        _stat("DPR", result["dpr"]),
        _stat("damage", result["damage"]),
        _stat("rounds", result["rounds"]),
        _stat("actions/round", result["actions_per_round"]),
        _stat("attacks/round", result["attacks_per_round"]),
        f"{'win rate':>14}  {result['win_rate']:.1%}",
    ]
    return "\n".join(lines) + "\n"


def _stat(label: str, stat: dict[str, Any]) -> str:
    return (
        f"{label:>14}  {stat['mean']:.2f}"
        f"  (95% CI {stat['ci_low']:.2f}-{stat['ci_high']:.2f}, sd {stat['stdev']:.2f})"
    )
