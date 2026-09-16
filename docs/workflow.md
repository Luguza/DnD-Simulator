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
6. It merges unless `human-merge-required` is red, then fires the Routine for
   the next task (§ The chain) and archives itself. Red means a milestone
   review or a workflow edit: it stops and hands back (§ Merge authority).

## The review loop

The reviewer posts one sticky comment per PR, rewritten on each push, tagging
every finding **CONFIRMED** or **PLAUSIBLE**.

- **CONFIRMED** — fix it and push.
- **PLAUSIBLE** — fix it, *or* reply on the PR saying why it is not a defect.
  Never silently skipped. The split exists so that a weak finding costs a reply
  rather than a speculative commit.
- **Done** = `✅ Looks good` **and** CI green **and** no merge conflict.
- **Round cap: three.** On a fourth non-clean verdict the session stops pushing
  and posts one comment naming what is still contested. If CI is green and every
  **CONFIRMED** finding is fixed, it merges and records the contested findings
  in `ROADMAP.md` § The W0 debt register, owned by the next milestone review, so
  the human reads them there. Otherwise it sets the row to `held` and notifies
  the human. PR #19 took 27 pushes; the cap is what catches that.

A verdict that says *the review did not run* or *did not finish* is not a pass
and never merges.

## Merge authority

The human reads the code at ROADMAP milestones (M0–M5) and nowhere else, so the
loop merges everything in between — task PRs, spec PRs and plan-review PRs,
including edits to `DESIGN.md`, `ROADMAP.md`, `CLAUDE.md`, the dependency set
and `.claude/`. The `human-merge-required` CI job fails, and the loop stops, in
exactly two cases:

- **Milestone review** — the PR flips a `docs/progress.md` row whose name
  contains `milestone review` to `done`. The human merges it, and that merge is
  their review of everything since the previous milestone.
- **Workflow edit** — anything under `.github/workflows/`. Not for the
  contract's sake: `claude-code-action` skips itself when the workflow file
  differs from the default branch **and exits successfully**, so such a PR can
  never get a real verdict, and the loop merging it would be merging unreviewed.

The cost is that the loop can now rewrite the plan it executes; the milestone
review is where the human catches that. The classifier matches the row's name,
so **every milestone row must say `milestone review`** — one that does not would
be merged by the loop.

## When the spec is wrong

A session that finds a real gap in the task spec does not guess. It opens a
**spec PR** describing the gap and its resolution, leaves the queue row at
`next`, and drives the PR through § The review loop like any other. On merge it
starts a fresh `/next-task` session, which re-reads the corrected row. The W0 debt
register is what this project gets when specs are read critically; an
implementer that can only ever implement is worse than one that can stop.

## Plan reviews

A `⏸` row runs `/plan-review` instead: re-read `ROADMAP.md` and `DESIGN.md`
against what the last few tasks actually measured, in the shape of
`docs/decisions.md` § The W0 plan review. Gates sit every two or three tasks
and at every milestone. An ordinary gate's PR is merged by the loop, which then
carries on; a **milestone review**'s PR is merged by the human, and the chain
waits for that merge.

## The chain

**A Routine session cannot start another session.** Routines run with no
permission mode ([Routines](https://code.claude.com/docs/en/routines): "there is
no permission-mode picker"), and `create_session` refuses a parent whose mode it
cannot read. There is no setting that changes this. So the Routine *is* the
worker, and both paths below drive the Routine rather than spawn around it.

- **Watchdog.** The Routine runs three checks and, only if all three hold, works
  the task itself in that same session: the queue has a `next` row, no
  `claude/task-*` PR is open, and nothing is `held`. The three conditions are
  what stop it double-starting a task; firing no more often than the slowest
  task reaches its PR is the other half of that guard.
- **Fast path.** On merging, the session fires the Routine (`fire_trigger`)
  instead of waiting for the schedule, so the next task starts in a fresh
  session with a clean context. The checks run again on that firing, which is
  what keeps the fast path from passing a `held` row.

  **Set the Routine up from the claude.ai Routines UI**, not from a session — a
  Routine created through the MCP tool stores no connector grants, and its fired
  sessions would come up without the GitHub tools the checks need. New session
  per firing, push notifications on, this repository as the source, with this
  prompt:

  ```text
  Watchdog for the DnD-Simulator task loop. Be brief and cheap in the three
  checks below: do not read DESIGN.md, ROADMAP.md or docs/decisions.md for them.

  1. Read docs/progress.md on main. If it does not exist there, stop silently.
  2. Find the row whose status is `next`. If there is none, or any row is
     `held`: stop, notifying the user in one sentence for a `held` row.
  3. If a pull request with a `claude/task-*` head branch is open, stop
     silently — a task is in flight. Exception: if its last activity is over
     two hours old, say so in one sentence naming the PR and what it waits on,
     unless it is a milestone review waiting for the human to merge it.

  Only if all three pass: run `/next-task` yourself, in this session, and drive
  it to a merged PR. Never call `create_session` — a Routine session has no
  permission mode, so that call is always refused.
  ```

Neither path ever passes a `held` row, or a milestone review whose PR is still
open.

## Token cost

The loop is designed to be cheap, and the design is load-bearing, not incidental:
the reviewer cancels superseded runs and routes prose-only diffs to the cheaper
pass; sessions grep `DESIGN.md`, `ROADMAP.md` and `docs/decisions.md` for the one
claim they need and never read them end to end; the round cap bounds the worst
case at four reviews per task; and the watchdog's prompt is three checks long.
