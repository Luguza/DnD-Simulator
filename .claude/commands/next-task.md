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

- The milestone branch M is named by `docs/progress.md`'s `Milestone branch:`
  line on `main`; everything below happens on M (`docs/workflow.md` § The
  milestone branch). If the milestone PR (M into `main`) is ready rather than a
  draft, the milestone is closing and its merge is waiting: stop.
- Never stop to ask the user. Where CLAUDE.md says to ask, take the most
  conservative reading, proceed, and record it in `docs/questions.md` in this
  PR (`docs/workflow.md` § Questions for the human).
- `docs/progress.md` on M → the `next` row. A `⏸` row means run `/plan-review`
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

- `git checkout -b claude/task-<id> origin/<M>`
- Write the code and the tests. The done-when column is the acceptance test.
- `uv run pre-commit run --all-files` and `uv run pytest` — both green before
  you push, every time. A push that turns CI red costs a review round.
- Add the `docs/decisions.md` § `<id>` section: the *why*, not the what.
- Retire the `ROADMAP.md` § The W0 debt register rows this task owns.
- Flip your queue row to `done` and the row below to `next`.

## 4. Open the PR

Open it as a **draft** with base M — never `main` — push everything, then mark it ready for review. One
review against the finished diff, not one per push.

Write the body with the `pr` skill: Summary, Evidence, Merge Danger, then the
done-when, the debt rows retired and anything you deliberately did not do. Then
`subscribe_pr_activity` and end your turn.

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
  the human. Count the PR's verdicts, not this session's
  (`docs/workflow.md` § The review loop).

## 6. Merge and hand off

When the verdict is `✅ Looks good`, CI is green and there is no conflict:

- If `human-merge-required` is red, the PR has a base that is `main` by
  mistake — retarget it to M and wait for CI again — or edits
  `.github/workflows/` — take the edit out, add it to `docs/questions.md` as a
  proposal for the human, and wait for the new verdict (`docs/workflow.md` § Merge
  authority).
- Otherwise merge into M — contract files included — then hand off by firing the
  watchdog Routine (`fire_trigger`, `trig_01P3i9g4LRN6i4nakMEH32hq`), which
  re-runs its checks (`docs/workflow.md` § The chain) and takes the next row in a fresh session, and
  archive yourself. Never `create_session`: a Routine session has no
  permission mode, so the call is always refused
  (`docs/workflow.md` § The chain). If the fire is refused too, stop and say
  so — the schedule picks the queue up on its next firing.
  If the next row is `held`, notify the human instead of starting anything.
