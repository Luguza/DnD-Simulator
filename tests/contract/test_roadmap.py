"""ROADMAP.md's Depends columns against its wave graph.

`docs/decisions.md` § The W2 late-wave review, Retro.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROADMAP = ROOT / "ROADMAP.md"

_COUNTS = (
    ("ROADMAP.md", r"^(\d+) tasks across"),
    ("DESIGN.md", r"^(\d+) tasks, their dependency edges"),
    ("CLAUDE.md", r"has (\d+) tasks across"),
)
_TOTALS = ("ROADMAP.md", "DESIGN.md")

_TASK_ID = re.compile(r"\bw\d[a-z]+\d*\b")
_SOLID = re.compile(r"\b([WM]\d)\s*-->\s*")
_DASHED = re.compile(r'\b(W\d)\s*-\.->\|"(w\d[a-z]+\d*) only"\|\s*(W\d)\b')


def _tasks(text: str) -> dict[str, tuple[str, ...]]:
    """Every task id in a task table, with the ids its Depends cell names."""
    tasks: dict[str, tuple[str, ...]] = {}
    in_table = False
    for line in text.splitlines():
        if line.startswith("| ID | Task | Mode | Depends |"):
            in_table = True
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if not in_table or len(cells) < 4 or not _TASK_ID.fullmatch(cells[0]):
            continue
        tasks[cells[0]] = tuple(_TASK_ID.findall(cells[3]))
    return tasks


def _graph(text: str) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    """The mermaid chart's solid edges, and its dashed ones as (from, task, to)."""
    chart = text.split("```mermaid", 1)[1].split("```", 1)[0]
    solid: set[tuple[str, str]] = set()
    for line in chart.splitlines():
        if "-.->" in line:
            continue
        nodes = re.findall(r"\b[WM]\d\b", line)
        if _SOLID.search(line):
            solid.update(zip(nodes, nodes[1:], strict=False))
    return solid, set(_DASHED.findall(chart))


def _reaches(solid: set[tuple[str, str]], start: str, end: str) -> bool:
    seen, frontier = {start}, [start]
    while frontier:
        node = frontier.pop()
        for a, b in solid:
            if a == node and b not in seen:
                seen.add(b)
                frontier.append(b)
    return end in seen


def test_every_dependency_is_a_task() -> None:
    tasks = _tasks(ROADMAP.read_text(encoding="utf-8"))
    assert len(tasks) > 70
    unknown = {
        (task, dep) for task, deps in tasks.items() for dep in deps if dep not in tasks
    }
    assert not unknown


def test_every_cross_wave_edge_is_drawn() -> None:
    text = ROADMAP.read_text(encoding="utf-8")
    solid, dashed = _graph(text)
    undrawn = []
    for task, deps in _tasks(text).items():
        to = f"W{task[1]}"
        for dep in deps:
            frm = f"W{dep[1]}"
            if frm == to or _reaches(solid, frm, to):
                continue
            if (frm, task, to) not in dashed:
                undrawn.append(f"{task} depends on {dep}: no {frm} -> {to} edge")
    assert not undrawn


def test_every_stated_task_count_is_the_tables() -> None:
    count = len(_tasks(ROADMAP.read_text(encoding="utf-8")))
    stated = {
        name: int(m.group(1))
        for name, pattern in _COUNTS
        if (m := re.search(pattern, (ROOT / name).read_text(encoding="utf-8"), re.M))
    }
    assert stated == dict.fromkeys(stated, count)
    assert len(stated) == len(_COUNTS)


def test_every_stated_day_total_is_the_graphs() -> None:
    text = ROADMAP.read_text(encoding="utf-8")
    chart = text.split("```mermaid", 1)[1].split("```", 1)[0]
    total = sum(int(days) for days in re.findall(r"~(\d+) nominal days", chart))
    stated = [
        int(m.group(1))
        for name in _TOTALS
        for m in re.finditer(
            r"Roughly (\d+)\s+nominal", (ROOT / name).read_text(encoding="utf-8")
        )
    ]
    assert stated == [total] * len(_TOTALS)
