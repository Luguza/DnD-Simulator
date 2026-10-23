"""Render the progress dashboard as one static HTML page.

Reads the live queue (`docs/progress.md` on the milestone branch that `main`'s
copy names), the task tables and debt register in that branch's `ROADMAP.md`,
and, when `GITHUB_TOKEN` is set, the open and recently merged pull requests.

    python dashboard/build.py --out _site

Run from a clone whose `origin/*` refs are fetched (`fetch-depth: 0`).
"""

import argparse
import html
import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

TEMPLATE = Template((Path(__file__).parent / "template.html").read_text("utf-8"))
TASK_ID = re.compile(r"\b(w(\d)[a-z]\d*)\b")


@dataclass
class Task:
    id: str
    title: str
    mode: str
    done: bool = False


@dataclass
class Wave:
    number: int
    title: str
    tasks: list[Task] = field(default_factory=list)


@dataclass
class QueueRow:
    number: str
    task: str
    status: str
    pr: str


@dataclass
class Debt:
    number: int
    title: str
    where: str
    owner: str


def git_show(ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"], check=True, capture_output=True, text=True
    ).stdout


def table_rows(text: str) -> list[list[str]]:
    """Every markdown table row in `text`, split into stripped cells."""
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s|:-]+\|$", line):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def section(text: str, heading: str) -> str:
    """The body of the `## ` section whose heading starts with `heading`."""
    match = re.search(
        rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", text, re.S | re.M
    )
    if match is None:
        raise SystemExit(f"ROADMAP.md has no section starting '## {heading}'")
    return match.group(1)


def inline(text: str) -> str:
    """Escape `text` and render the markdown the planning tables use."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    return out


def plain(text: str) -> str:
    return re.sub(r"[`*]", "", text)


def short(text: str, limit: int = 110) -> str:
    text = plain(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_queue(progress: str) -> list[QueueRow]:
    rows = [r for r in table_rows(progress) if len(r) == 4 and r[0] != "#"]
    if not rows:
        raise SystemExit("docs/progress.md has no queue table")
    return [QueueRow(*row) for row in rows]


def parse_waves(roadmap: str) -> list[Wave]:
    waves = []
    for match in re.finditer(r"^## W(\d) — (.+)$", roadmap, re.M):
        wave = Wave(int(match.group(1)), match.group(2).strip())
        for row in table_rows(section(roadmap, f"W{wave.number} — ")):
            if TASK_ID.fullmatch(row[0]):
                wave.tasks.append(Task(row[0], row[1], row[2] if len(row) > 2 else ""))
        waves.append(wave)
    if not waves:
        raise SystemExit("ROADMAP.md has no '## Wn — ' sections")
    return waves


def parse_debt(roadmap: str) -> list[Debt]:
    debts = []
    for row in table_rows(section(roadmap, "The W0 debt register")):
        if row[0].isdigit() and len(row) >= 4:
            debts.append(Debt(int(row[0]), row[1], row[2], row[3]))
    return debts


def mark_done(waves: list[Wave], queue: list[QueueRow]) -> None:
    """A task is done if the live queue says so, or if its wave is earlier than
    every task the queue holds — the queue only carries the current wave."""
    done = {
        m.group(1)
        for r in queue
        if r.status == "done"
        for m in [TASK_ID.search(r.task)]
        if m
    }
    queued = [int(m.group(2)) for r in queue for m in [TASK_ID.search(r.task)] if m]
    first_live = min(queued, default=0)
    for wave in waves:
        for task in wave.tasks:
            task.done = wave.number < first_live or task.id in done


def github(path: str) -> Any:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return []
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def chip(status: str) -> str:
    return f'<span class="chip {html.escape(status)}">{html.escape(status)}</span>'


def bar(done: int, total: int) -> str:
    pct = 100 * done / total if total else 0
    return f'<div class="bar"><span style="width:{pct:.1f}%"></span></div>'


def render(
    milestone: str,
    queue: list[QueueRow],
    waves: list[Wave],
    debts: list[Debt],
    open_prs: list[dict[str, Any]],
    merged_prs: list[dict[str, Any]],
    repo_url: str,
) -> str:
    all_tasks = [t for w in waves for t in w.tasks]
    done_total = sum(t.done for t in all_tasks)
    current = next((w for w in waves if not all(t.done for t in w.tasks)), waves[-1])
    next_row = next((r for r in queue if r.status in ("next", "held")), None)
    queue_tasks = [r for r in queue if TASK_ID.search(r.task)]
    queue_done = sum(r.status == "done" for r in queue_tasks)

    def pr_link(ref: str) -> str:
        num = ref.lstrip("#")
        if not num.isdigit():
            return ""
        return f'<a href="{repo_url}/pull/{num}">#{num}</a>'

    stats = [
        (
            "Overall",
            f"{done_total} / {len(all_tasks)}",
            "tasks done",
            bar(done_total, len(all_tasks)),
        ),
        (
            f"Wave W{current.number}",
            f"{sum(t.done for t in current.tasks)} / {len(current.tasks)}",
            html.escape(plain(current.title)),
            bar(sum(t.done for t in current.tasks), len(current.tasks)),
        ),
        (
            "Up next",
            inline(short(next_row.task, 40)) if next_row else "—",
            chip(next_row.status) if next_row else "queue is empty",
            "",
        ),
        ("Open debt", str(len(debts)), "rows in the register", ""),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'<div class="sub">{sub}</div>{extra}</div>'
        for label, value, sub, extra in stats
    )

    queue_html = "".join(
        f'<li class="{html.escape(r.status)}">'
        f'<span class="num">{html.escape(r.number)}</span>'
        f'<span class="task">{inline(r.task)}</span>{chip(r.status)}'
        f'<span class="pr">{pr_link(r.pr)}</span></li>'
        for r in queue
    )

    wave_html = ""
    for w in waves:
        done = sum(t.done for t in w.tasks)
        state = "complete" if done == len(w.tasks) else "active" if w is current else ""
        items = "".join(
            f'<li class="{"done" if t.done else ""}"><code>{t.id}</code> '
            f"{inline(short(t.title))}"
            f' <span class="mode">{html.escape(t.mode)}</span></li>'
            for t in w.tasks
        )
        wave_html += (
            f'<details class="wave {state}"{" open" if w is current else ""}><summary>'
            f'<span class="wname">W{w.number}</span>'
            f'<span class="wtitle">{inline(w.title)}</span>'
            f'<span class="wcount">{done}/{len(w.tasks)}</span>'
            f"{bar(done, len(w.tasks))}</summary>"
            f'<ul class="tasks">{items}</ul></details>'
        )

    def pr_item(pr: dict[str, Any], when: str) -> str:
        base = html.escape(pr["base"]["ref"])
        draft = ' <span class="chip queued">draft</span>' if pr.get("draft") else ""
        stamp = (pr.get(when) or "")[:10]
        return (
            f'<li><a href="{html.escape(pr["html_url"])}">#{pr["number"]}</a> '
            f"{inline(pr['title'])}{draft}"
            f'<span class="meta">→ {base} · {stamp}</span></li>'
        )

    open_html = (
        "".join(pr_item(p, "created_at") for p in open_prs)
        or "<li class='empty'>None</li>"
    )
    merged_html = (
        "".join(pr_item(p, "merged_at") for p in merged_prs)
        or "<li class='empty'>None</li>"
    )

    owners: dict[str, list[Debt]] = {}
    for d in debts:
        m = TASK_ID.search(d.owner)
        key = m.group(1) if m else plain(d.owner)[:40]
        owners.setdefault(key, []).append(d)

    def owner_key(item: tuple[str, list[Debt]]) -> tuple[int, str]:
        m = TASK_ID.fullmatch(item[0])
        return (int(m.group(2)) if m else 99, item[0])

    debt_html = "".join(
        f'<details class="owner"><summary><code>{html.escape(k)}</code>'
        f'<span class="wcount">{len(v)}</span></summary><ul>'
        + "".join(
            f'<li><span class="num">#{d.number}</span> '
            f"{inline(short(d.title, 140))}</li>"
            for d in v
        )
        + "</ul></details>"
        for k, v in sorted(owners.items(), key=owner_key)
    )

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return TEMPLATE.substitute(
        milestone=html.escape(milestone),
        repo_url=html.escape(repo_url),
        generated=generated,
        stats=stat_html,
        queue=queue_html,
        queue_done=queue_done,
        queue_total=len(queue_tasks),
        waves=wave_html,
        open_prs=open_html,
        merged_prs=merged_html,
        debt=debt_html,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("_site"))
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()

    main_progress = git_show(f"{args.remote}/main", "docs/progress.md")
    found = re.search(r"Milestone branch: `([^`]+)`", main_progress)
    milestone = found.group(1) if found else "main"
    ref = f"{args.remote}/{milestone}"
    progress, roadmap = git_show(ref, "docs/progress.md"), git_show(ref, "ROADMAP.md")

    queue, waves, debts = (
        parse_queue(progress),
        parse_waves(roadmap),
        parse_debt(roadmap),
    )
    mark_done(waves, queue)

    open_prs = github("pulls?state=open&per_page=20")
    closed = github("pulls?state=closed&sort=updated&direction=desc&per_page=30")
    merged = sorted(
        (p for p in closed if p.get("merged_at")),
        key=lambda p: p["merged_at"],
        reverse=True,
    )[:8]

    repo = os.environ.get("GITHUB_REPOSITORY", "Luguza/DnD-Simulator")
    page = render(
        milestone, queue, waves, debts, open_prs, merged, f"https://github.com/{repo}"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(page, encoding="utf-8")
    print(f"wrote {args.out / 'index.html'}")


if __name__ == "__main__":
    main()
