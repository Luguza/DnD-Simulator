"""Render the progress dashboard as one static HTML page.

Reads the live queue (`docs/progress.md` on the milestone branch that `main`'s
copy names), the task tables and debt register in that branch's `ROADMAP.md`,
its `docs/questions.md`, and, when `GITHUB_TOKEN` is set, the pull requests
that date each finished task.

    python dashboard/build.py --out _site

Run from a clone whose `origin/*` refs are fetched (`fetch-depth: 0`).
"""

import argparse
import html
import json
import os
import re
import statistics
import subprocess
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template
from typing import Any

TEMPLATE = Template((Path(__file__).parent / "template.html").read_text("utf-8"))
TASK_ID = re.compile(r"\b(w(\d)[a-z]\d*)\b")
DAY_PX = 6
PHONE_DAY_PX = 2.5
PACE_WINDOW = 10


@dataclass
class Item:
    """One row of the chart: a ROADMAP task or a queue-only row (a review)."""

    id: str
    title: str
    mode: str = ""
    status: str = "queued"
    pr: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    kind: str = "planned"
    review: bool = False


@dataclass
class Wave:
    number: int
    title: str
    items: list[Item] = field(default_factory=list)


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
    cost: str

    @property
    def owner_keys(self) -> list[str]:
        """The tasks the cell opens with, or the cell itself."""
        ids = []
        for token in re.findall(r"w\d[a-z]\d*|[A-Za-z]+", plain(self.owner)):
            if TASK_ID.fullmatch(token):
                ids.append(token)
            elif token != "and":
                break  # "needs `w1a`", "via `w3e`" name prerequisites
        return ids or [plain(self.owner)]


@dataclass
class Question:
    number: str
    asked_by: str
    question: str
    choice: str
    otherwise: str


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
    pattern = rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, re.S | re.M)
    if match is None:
        raise SystemExit(f"no section starting '## {heading}'")
    return match.group(1)


def inline(text: str) -> str:
    """Escape `text` and render the markdown the planning tables use."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\*([^*\s][^*]*)\*", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    return out


def plain(text: str) -> str:
    return re.sub(r"[`*]", "", text)


def short(text: str, limit: int) -> str:
    text = plain(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def current_wave(waves: list["Wave"]) -> "Wave":
    return next(
        (w for w in waves if any(i.status != "done" for i in w.items)), waves[-1]
    )


def progress(wave_items: list["Item"]) -> tuple[int, int]:
    """Tasks done and tasks in all, reviews left out."""
    tasks = [i for i in wave_items if not i.review]
    return sum(i.status == "done" for i in tasks), len(tasks)


def parse_date(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


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
                mode = row[2] if len(row) > 2 else ""
                wave.items.append(Item(row[0], row[1], mode))
        waves.append(wave)
    if not waves:
        raise SystemExit("ROADMAP.md has no '## Wn — ' sections")
    return waves


def parse_debt(roadmap: str) -> list[Debt]:
    return [
        Debt(int(row[0]), row[1], row[2], row[3], row[4])
        for row in table_rows(section(roadmap, "The W0 debt register"))
        if row[0].isdigit() and len(row) >= 5
    ]


def parse_questions(questions: str) -> list[Question]:
    return [
        Question(*row[:5])
        for row in table_rows(questions)
        if row[0].isdigit() and len(row) >= 5
    ]


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


def fetch_prs() -> list[dict[str, Any]]:
    prs: list[dict[str, Any]] = []
    for page in range(1, 6):
        batch = github(f"pulls?state=all&per_page=100&page={page}")
        prs.extend(batch)
        if len(batch) < 100:
            break
    return prs


def arrange(waves: list[Wave], queue: list[QueueRow]) -> None:
    """Put each queued wave's rows in queue order, reviews included, and carry
    the queue's status and PR onto each. Waves before the queue's are done."""
    by_id = {item.id: item for wave in waves for item in wave.items}
    rows: list[Item] = []
    for row in queue:
        # A task row opens with its id; a review row may name one later on.
        found = re.match(r"\s*(w\d[a-z]\d*)\b", row.task)
        if found is None:
            item = Item("⏸", row.task, review=True)
        else:
            item = by_id.get(found.group(1)) or Item(found.group(1), row.task)
        item.status = row.status
        item.pr = int(row.pr.lstrip("#")) if row.pr.lstrip("#").isdigit() else None
        rows.append(item)

    queued = {item.id for item in rows if not item.review}
    live = min((int(task_id[1]) for task_id in queued), default=0)
    # Each row stays in its own task's wave; a review joins the wave of the
    # task above it, or of the first task when it opens the queue.
    homes: dict[int, list[Item]] = {}
    home = live
    for item in rows:
        if not item.review:
            home = int(item.id[1])
        homes.setdefault(home, []).append(item)
    for wave in waves:
        if wave.number < live:
            for item in wave.items:
                item.status = "done"
        if wave.number in homes:
            rest = [i for i in wave.items if i.id not in queued]
            wave.items = homes[wave.number] + rest


def schedule(waves: list[Wave], prs: list[dict[str, Any]], now: datetime) -> float:
    """Date every row: finished ones from their pull request, the rest projected
    one after another at the recent pace. Returns that pace in days."""
    by_number = {pr["number"]: pr for pr in prs}
    titled: dict[str, dict[str, Any]] = {}
    merged_first = sorted(prs, key=lambda p: (not p.get("merged_at"), p["number"]))
    for candidate in merged_first:
        found = re.match(r"\W*(w\d[a-z]\d*)\b", candidate["title"])
        if found and found.group(1) not in titled:
            titled[found.group(1)] = candidate
    items = [item for wave in waves for item in wave.items]

    for item in items:
        pr: dict[str, Any] | None = (
            by_number.get(item.pr) if item.pr else titled.get(item.id)
        )
        if pr is None:
            continue
        item.pr = pr["number"]
        item.start = parse_date(pr["created_at"])
        if item.status == "done" and pr.get("merged_at"):
            item.end, item.kind = parse_date(pr["merged_at"]), "done"
        elif pr.get("state") == "open":
            item.end, item.kind = now, "active"

    done = [i for i in items if i.status == "done"]
    for index, item in enumerate(done):
        if item.end is None:
            before = [d.end for d in done[:index] if d.end]
            after = [d.start for d in done[index + 1 :] if d.start]
            item.start = before[-1] if before else (after[0] if after else None)
            item.end = after[0] if after else item.start
            item.kind = "approx" if item.start else "undated"

    ends = sorted(i.end for i in done if i.end)[-(PACE_WINDOW + 1) :]
    gaps = [
        (b - a).total_seconds() / 86400 for a, b in zip(ends, ends[1:], strict=False)
    ]
    pace = max(statistics.fmean(gaps), 0.25) if gaps else 1.0

    cursor = max([now, *(i.end for i in items if i.end)])
    for item in items:
        if item.status == "done" or item.kind == "active":
            continue
        item.start, item.end = cursor, cursor + timedelta(days=pace)
        cursor = item.end
    return pace


def day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def render_gantt(
    waves: list[Wave],
    debts: list[Debt],
    now: datetime,
    repo_url: str,
) -> str:
    dated = [i for w in waves for i in w.items if i.start and i.end]
    origin = min(i.start for i in dated if i.start).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    finish = max(i.end for i in dated if i.end) + timedelta(days=7)
    days = (finish - origin).days

    def x(moment: datetime) -> float:
        """Percent of the way along the track."""
        return (moment - origin).total_seconds() / 86400 / days * 100

    owed: dict[str, int] = {}
    for debt in debts:
        for key in debt.owner_keys:
            owed[key] = owed.get(key, 0) + 1

    months, ticks = [], []
    cursor = origin.replace(day=1)
    while cursor < finish:
        if cursor >= origin:
            ticks.append(f'<i style="left:{x(cursor):.2f}%"></i>')
            months.append(f'<span style="left:{x(cursor):.2f}%">{cursor:%b %Y}</span>')
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    today = f'<b class="today" style="left:{x(now):.2f}%"><em>today</em></b>'

    def bar(item: Item) -> str:
        if item.start is None or item.end is None:
            return ""
        left, span = x(item.start), x(item.end) - x(item.start)
        tip = f"{plain(item.title)}\n{day(item.start)} → {day(item.end)}"
        if item.kind == "planned":
            tip += " (projected)"
        if item.review:
            return (
                f'<span class="diamond {item.status}" style="left:{left:.2f}%" '
                f'title="{html.escape(tip)}"></span>'
            )
        return (
            f'<span class="bar {item.kind}" style="left:{left:.2f}%;'
            f'width:{span:.2f}%" title="{html.escape(tip)}"></span>'
        )

    rows = [
        f'<div class="row axis"><div class="lab"></div>'
        f'<div class="track">{"".join(months)}</div></div>'
    ]
    current = current_wave(waves)
    for wave in waves:
        done, total = progress(wave.items)
        spans = [i for i in wave.items if i.start and i.end]
        summary = ""
        if spans:
            left = x(min(i.start for i in spans if i.start))
            right = x(max(i.end for i in spans if i.end))
            pct = 100 * done / total if total else 0
            summary = (
                f'<span class="bar wave" style="left:{left:.2f}%;'
                f'width:{right - left:.2f}%"><span style="width:{pct:.0f}%">'
                "</span></span>"
            )
        body = []
        for item in wave.items:
            owes = owed.get(item.id, 0)
            chip = (
                f'<a class="debt-chip" href="#debt" data-owner="{item.id}" '
                f'title="{owes} open debt row(s) to retire">{owes}</a>'
                if owes
                else ""
            )
            pr = (
                f' <a class="pr" href="{repo_url}/pull/{item.pr}">#{item.pr}</a>'
                if item.pr
                else ""
            )
            name = "" if item.review else f"<code>{item.id}</code> "
            body.append(
                f'<div class="row {item.status} {item.kind}"><div class="lab">'
                f'{name}<span class="t">{inline(short(item.title, 60))}</span>'
                f"{chip}{pr}</div>"
                f'<div class="track">{bar(item)}</div></div>'
            )
        state = "complete" if done == total else "active" if wave is current else ""
        rows.append(
            f'<details class="wave {state}"{" open" if wave is current else ""}>'
            f'<summary class="row"><div class="lab"><strong>W{wave.number}</strong> '
            f'<span class="t">{inline(wave.title)}</span>'
            f'<span class="count">{done}/{total}</span></div>'
            f'<div class="track">{summary}</div></summary>{"".join(body)}</details>'
        )
    return (
        f'<div class="gantt" style="--wd:{days * DAY_PX:.0f}px;'
        f'--wp:{days * PHONE_DAY_PX:.0f}px">'
        f'<div class="overlay">{"".join(ticks)}{today}</div>{"".join(rows)}</div>'
    )


def render_debt(debts: list[Debt]) -> tuple[str, str]:
    def order(key: str) -> tuple[int, str]:
        found = TASK_ID.fullmatch(key)
        return (int(found.group(2)) if found else 99, key)

    owners = sorted({key for d in debts for key in d.owner_keys}, key=order)
    options = "".join(
        f'<option value="{html.escape(o)}">{html.escape(o)}</option>' for o in owners
    )
    items = "".join(
        f'<details class="entry" data-owner="{html.escape("|".join(d.owner_keys))}">'
        f'<summary><span class="num">#{d.number}</span>'
        f'<span class="t">{inline(d.title)}</span>'
        f'<code class="owner">{html.escape(", ".join(d.owner_keys))}</code></summary>'
        f"<dl><dt>Where</dt><dd>{inline(d.where)}</dd>"
        f"<dt>Retired by</dt><dd>{inline(d.owner)}</dd>"
        f"<dt>If nobody does</dt><dd>{inline(d.cost)}</dd></dl></details>"
        for d in sorted(debts, key=lambda d: d.number)
    )
    return options, items


def render_questions(questions: list[Question]) -> str:
    return (
        "".join(
            f'<details class="entry"><summary><span class="num">#{q.number}</span>'
            f'<span class="t">{inline(q.question)}</span>'
            f'<span class="owner">{inline(q.asked_by)}</span></summary>'
            f"<dl><dt>Choice taken</dt><dd>{inline(q.choice)}</dd>"
            f"<dt>If answered otherwise</dt><dd>{inline(q.otherwise)}</dd>"
            "</dl></details>"
            for q in sorted(questions, key=lambda q: int(q.number))
        )
        or '<p class="empty">No open questions.</p>'
    )


def render(
    milestone: str,
    waves: list[Wave],
    debts: list[Debt],
    questions: list[Question],
    prs: list[dict[str, Any]],
    pace: float,
    now: datetime,
    repo_url: str,
) -> str:
    tasks = [i for w in waves for i in w.items if not i.review]
    done, total = progress(tasks)
    current = current_wave(waves)
    wave_done, wave_total = progress(current.items)
    up_next = next(
        (i for w in waves for i in w.items if i.status in ("next", "held")), None
    )
    ends = [i.end for i in tasks if i.end]
    finish = max(ends) if ends else None

    def meter(part: int, whole: int) -> str:
        pct = 100 * part / whole if whole else 0
        return f'<div class="meter"><span style="width:{pct:.1f}%"></span></div>'

    stats = [
        ("Overall", f"{done} / {total}", "tasks done", meter(done, total)),
        (
            f"Wave W{current.number}",
            f"{wave_done} / {wave_total}",
            inline(current.title),
            meter(wave_done, wave_total),
        ),
        (
            "Up next",
            f"<code>{up_next.id}</code>" if up_next else "—",
            inline(short(up_next.title, 60)) if up_next else "queue is empty",
            "",
        ),
        (
            "Projected finish",
            f"{finish:%b %Y}" if finish else "—",
            f"at {pace:.1f} days per item, the last {PACE_WINDOW} merges",
            "",
        ),
        (
            "Open decisions",
            str(len(questions)),
            '<a href="#decisions">questions for the human</a>',
            "",
        ),
        ("Open debt", str(len(debts)), '<a href="#debt">register rows</a>', ""),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="label">{label}</div>'
        f'<div class="value">{value}</div><div class="sub">{sub}</div>{extra}</div>'
        for label, value, sub, extra in stats
    )

    def pr_item(pr: dict[str, Any], when: str) -> str:
        draft = ' <span class="tag">draft</span>' if pr.get("draft") else ""
        return (
            f'<li><a href="{html.escape(pr["html_url"])}">#{pr["number"]}</a> '
            f"{inline(pr['title'])}{draft}"
            f'<span class="meta">→ {html.escape(pr["base"]["ref"])} · '
            f"{(pr.get(when) or '')[:10]}</span></li>"
        )

    open_prs = [p for p in prs if p.get("state") == "open"]
    merged = sorted(
        (p for p in prs if p.get("merged_at")),
        key=lambda p: str(p["merged_at"]),
        reverse=True,
    )[:6]
    empty = '<li class="empty">None</li>'
    debt_options, debt_items = render_debt(debts)
    blob = f"{repo_url}/blob/{milestone}"
    return TEMPLATE.substitute(
        milestone=html.escape(milestone),
        repo_url=html.escape(repo_url),
        roadmap_url=html.escape(f"{blob}/ROADMAP.md"),
        questions_url=html.escape(f"{blob}/docs/questions.md"),
        generated=now.strftime("%Y-%m-%d %H:%M UTC"),
        stats=stat_html,
        gantt=render_gantt(waves, debts, now, repo_url),
        pace=f"{pace:.1f}",
        debt_count=len(debts),
        debt_options=debt_options,
        debt=debt_items,
        question_count=len(questions),
        questions=render_questions(questions),
        open_prs="".join(pr_item(p, "created_at") for p in open_prs) or empty,
        merged_prs="".join(pr_item(p, "merged_at") for p in merged) or empty,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the progress dashboard.")
    parser.add_argument("--out", type=Path, default=Path("_site"))
    parser.add_argument("--remote", default="origin")
    parser.add_argument(
        "--prs", type=Path, help="read pull requests from this JSON file instead"
    )
    args = parser.parse_args()

    main_progress = git_show(f"{args.remote}/main", "docs/progress.md")
    found = re.search(r"Milestone branch: `([^`]+)`", main_progress)
    milestone = found.group(1) if found else "main"
    ref = f"{args.remote}/{milestone}"
    roadmap = git_show(ref, "ROADMAP.md")

    waves = parse_waves(roadmap)
    arrange(waves, parse_queue(git_show(ref, "docs/progress.md")))
    prs = json.loads(args.prs.read_text()) if args.prs else fetch_prs()
    now = datetime.now(UTC)
    pace = schedule(waves, prs, now)

    repo = os.environ.get("GITHUB_REPOSITORY", "Luguza/DnD-Simulator")
    page = render(
        milestone,
        waves,
        parse_debt(roadmap),
        parse_questions(git_show(ref, "docs/questions.md")),
        prs,
        pace,
        now,
        f"https://github.com/{repo}",
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(page, encoding="utf-8")
    print(f"wrote {args.out / 'index.html'}")


if __name__ == "__main__":
    main()
