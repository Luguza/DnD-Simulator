# The task loop

One task, one session, one branch, one pull request, reviewed until clean.
`docs/progress.md` is the queue; this file is the procedure.

## The cycle

1. A session starts with `/next-task`. It reads the queue, takes the `next` row,
   and criticises the ROADMAP spec **before** implementing it.
2. It branches `claude/task-<id>`, implements, and proves it locally:
   `uv run pre-commit run --all-files` and `uv run pytest`.
3. It opens the PR **as a draft**, pushes everything, then marks it ready. That
   is what makes `claude-code-review.yml` fire once against a complete diff
   instead of once per push — drafts are skipped and reviewed on
   `ready_for_review`.
4. It subscribes to PR activity and ends its turn. Review and CI wake it.
5. Each verdict comment is answered (§ The review loop) until the reviewer says
   `✅ Looks good` with CI green.
6. It merges if the PR is a **code** PR, then starts the next session and
   archives itself. Otherwise it stops and hands back (§ Merge authority).

## The review loop

The reviewer posts one sticky comment per PR, rewritten on each push, tagging
every finding **CONFIRMED** or **PLAUSIBLE**.

- **CONFIRMED** — fix it and push.
- **PLAUSIBLE** — fix it, *or* reply on the PR saying why it is not a defect.
  Never silently skipped. The split exists so that a weak finding costs a reply
  rather than a speculative commit.
- **Done** = `✅ Looks good` **and** CI green **and** no merge conflict.
- **Round cap: three.** On a fourth non-clean verdict the session stops, posts
  one comment naming what is still contested, sets the row to `held`, and
  notifies the human. PR #19 took 27 pushes; the cap is what catches that.

A verdict that says *the review did not run* or *did not finish* is not a pass
and never merges.

## Merge authority

The `human-merge-required` CI job classifies every PR by the paths it touches.

- **Contract PR** — touches `DESIGN.md`, `ROADMAP.md`, `CLAUDE.md`,
  `pyproject.toml`, `uv.lock`, `.github/` or `.claude/`: the plans, the agent
  configuration, the CI that gates both, and the dependency set. The job fails
  by design; the human merges. The name is the message — it is the only red
  check, and it means *your turn*.
- **Code PR** — everything else, `src/`, `tests/` and `docs/` included. The
  session merges it on a clean review.

Two things follow from that line. The plan cannot be rewritten by the loop that
executes it. And a task PR cannot edit its own reviewer — which matters more
than it looks, because `claude-code-action` skips itself when the workflow file
differs from the default branch **and exits successfully**, so an implementer
editing `claude-code-review.yml` would silently disable the only gate.

## When the spec is wrong

A session that finds a real gap in the task spec does not guess. It opens a
**spec PR** — contract class, so the human merges it — describing the gap and
its proposed resolution, leaves the queue row at `next`, and stops. The W0 debt
register is what this project gets when specs are read critically; an
implementer that can only ever implement is worse than one that can stop.

## Plan reviews

A `⏸` row runs `/plan-review` instead: re-read `ROADMAP.md` and `DESIGN.md`
against what the last few tasks actually measured, in the shape of
`docs/decisions.md` § The W0 plan review. Its deliverable is a contract PR, so
it ends with the human. Gates sit every two or three tasks and at every
milestone.

## The chain

- **Fast path.** On merging, the session creates the next session with the same
  `/next-task` prompt, tagged `dnd-task`, then archives itself.
- **Watchdog.** An hourly Routine that starts a task session only when three
  things hold at once: the queue has a `next` row, no `claude/task-*` PR is
  open, and nothing is `held`. It is what makes the chain survive a session
  dying mid-task; the three conditions are what stop it double-starting one.

  **Set it up from the claude.ai Routines UI**, not from a session — a Routine
  created through the MCP tool stores no connector grants, and its fired
  sessions would come up without the GitHub and session tools all three checks
  need. Hourly, new session per firing, push notifications on, this repository
  as the source, with this prompt:

  ```text
  Watchdog for the DnD-Simulator task loop. Be brief and cheap: make the three
  checks below and nothing else. Do not read DESIGN.md, ROADMAP.md or
  docs/decisions.md.

  1. Read docs/progress.md on main. If it does not exist there, stop silently.
  2. Find the row whose status is `next`. If there is none, or it is a ⏸ row,
     or any row is `held`: stop, notifying the user in one sentence only for a
     ⏸ or `held` row.
  3. If a pull request with a `claude/task-*` head branch is open, stop
     silently — a task is in flight. Exception: if its last activity is over
     two hours old, say so in one sentence naming the PR and what it waits on.

  Only if all three pass: create a session with the prompt `/next-task`, tagged
  ["dnd-task"], titled with the task id, and stop. Never do the task yourself.
  ```

Neither path ever passes a `⏸` row or a `held` row.

## Token cost

The loop is designed to be cheap, and the design is load-bearing, not incidental:
the reviewer cancels superseded runs and routes prose-only diffs to the cheaper
pass; sessions grep `DESIGN.md`, `ROADMAP.md` and `docs/decisions.md` for the one
claim they need and never read them end to end; the round cap bounds the worst
case at four reviews per task; and the watchdog's prompt is three checks long.
