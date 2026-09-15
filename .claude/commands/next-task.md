---
description: Take the next task off docs/progress.md and drive it to a merged PR.
---

Work the queue. One task, one branch, one PR, reviewed until clean.
`docs/workflow.md` is the procedure; this is the checklist.

**Read only what you need.** `DESIGN.md`, `ROADMAP.md` and `docs/decisions.md`
are 82KB, 17KB and 72KB — grep them for the claim you need, never read them end
to end. Budget your reading to: the queue row, the ROADMAP row for your id, the
`docs/decisions.md` section CLAUDE.md's table names for the files you will
touch, and the files themselves.

## 1. Take the task

- `docs/progress.md` → the `next` row. A `⏸` row means run `/plan-review`
  instead, in this session. A `held` row means stop and report.
- `grep -n "^| <id> " ROADMAP.md` → the task row: scope, depends, produces,
  done-when. Read `ROADMAP.md` § Task packet template and § Standing rules once.
- Confirm every dependency is `done` in the queue.

## 2. Criticise the spec before writing code

CLAUDE.md requires this and it is the highest-value step in the loop. Check the
row against the rest of the design for gaps, contradictions and unstated
assumptions. If you find a real one, do **not** guess: open a spec PR per
`docs/workflow.md` § When the spec is wrong, leave the row at `next`, and take
it through steps 5 and 6 like a task PR.

## 3. Implement

- `git checkout -b claude/task-<id>`
- Write the code and the tests. The done-when column is the acceptance test.
- `uv run pre-commit run --all-files` and `uv run pytest` — both green before
  you push, every time. A push that turns CI red costs a review round.
- Add the `docs/decisions.md` § `<id>` section: the *why*, not the what.
- Retire the `ROADMAP.md` § The W0 debt register rows this task owns.
- Flip your queue row to `done` and the row below to `next`.

## 4. Open the PR

Open it as a **draft**, push everything, then mark it ready for review. One
review against the finished diff, not one per push.

Body: what changed, the done-when evidence, the debt rows retired, and anything
you deliberately did not do. Then `subscribe_pr_activity` and end your turn.

## 5. Answer the review

Each wake: read the sticky verdict comment and CI.

- **CONFIRMED** → fix, validate locally, push.
- **PLAUSIBLE** → fix, or reply on the thread saying why it is not a defect.
- A verdict saying the review did not run or did not finish is not a pass.
- CI red or a merge conflict is work now, whatever the review says.
- **Fourth non-clean verdict:** stop pushing and comment naming what is
  contested. If CI is green and every CONFIRMED finding is fixed, record the
  contested findings in `ROADMAP.md` § The W0 debt register, owned by the next
  milestone review, and go to step 6. Otherwise set the row to `held` and tell
  the human.

## 6. Merge and hand off

When the verdict is `✅ Looks good`, CI is green and there is no conflict:

- If `human-merge-required` is red, a human merges this one — a milestone
  review or a workflow edit (`docs/workflow.md` § Merge authority). Notify the
  human, say which, and stop.
- Otherwise merge — contract files included — then start the next session —
  `create_session` with this same `/next-task` prompt, tagged `dnd-task`,
  titled with the next id (or `review` for a `⏸` row) — and archive yourself.
  If the next row is `held`, notify the human instead of starting anything.
