# The task loop

One task, one session, one branch, one pull request, reviewed until clean — into
the **milestone branch**, never into `main`. The only pull request into `main`
is the milestone branch itself, and the loop merges that one too.
`docs/progress.md` is the queue; this file is the procedure.

**The human does not review code along the way.** They review the finished
product once, after the last ROADMAP milestone (§ The final product). Nothing in
the loop waits for them: a decision that would have been put to them is taken by
the loop and recorded in `docs/questions.md` (§ Questions for the human). Only
two things still reach them: a `held` row, and a milestone PR the loop could not
merge (§ Plan reviews).

## The milestone branch

Each ROADMAP milestone (M0–M5) has one branch, `milestone/<name>`, cut from
`main`, and one pull request from it into `main`: the **milestone PR**. Every
task, spec and plan-review PR in that milestone targets the milestone branch and
is merged there by the loop. So when the milestone closes, the milestone PR's
diff is everything the milestone did, in one place — the record the human reads
at the end, not a gate they pass on the way.

- **Which branch.** The `Milestone branch:` line at the top of
  `docs/progress.md` **on `main`** names it. `main` only moves when a milestone
  PR merges, so that line changes exactly then — the milestone review writes the
  next branch's name onto the milestone branch, and merging the milestone PR is
  what publishes it.
- **The milestone PR is a draft until the milestone review.** It is opened as a
  draft once the branch has its first merge — GitHub refuses a pull request with
  no commits between the two branches. The milestone review marks it ready and
  merges it (§ Plan reviews).
- **The queue lives on the milestone branch.** Task PRs flip rows there, so the
  loop reads `docs/progress.md` from the milestone branch; the copy on `main` is
  the queue as of the last milestone.
- **`main` flows in, never out.** A change merged straight to `main` (a
  workflow edit, a tooling fix) is merged into the milestone branch before the
  next task starts, so the two never diverge and the milestone PR never
  conflicts.

## The cycle

1. A session starts with `/next-task`. It reads the queue, takes the `next` row,
   and criticises the ROADMAP spec **before** implementing it.
2. It branches `claude/task-<id>` off the milestone branch, implements, and proves it locally:
   `uv run pre-commit run --all-files` and `uv run pytest`.
3. It opens the PR **as a draft** against the milestone branch, body per `.claude/skills/pr/SKILL.md`, pushes everything, then marks it ready. That
   is what makes `claude-code-review.yml` fire once against a complete diff
   instead of once per push — drafts are skipped and reviewed on
   `ready_for_review`.
4. It subscribes to PR activity and ends its turn. Review and CI wake it.
5. Each verdict comment is answered (§ The review loop) until the reviewer says
   `✅ Looks good` with CI green.
6. It merges into the milestone branch unless `human-merge-required` is red,
   then fires the Routine for the next task (§ The chain) and archives itself.
   Red on a task PR means a wrong base or a workflow edit: a wrong base is
   retargeted to the milestone branch; a workflow edit comes out of the PR and
   becomes a proposal in `docs/questions.md` instead (§ Merge authority).

## The review loop

The reviewer posts one sticky comment per PR, rewritten on each push, tagging
every finding **CONFIRMED** or **PLAUSIBLE**. It reviews on two axes
(`.claude/skills/pr-review/SKILL.md`): **Standards** against
`CODING_STANDARDS.md`, and **Spec** against the task's ROADMAP row.

- **CONFIRMED** — fix it and push.
- **PLAUSIBLE** — fix it, *or* reply on the PR saying why it is not a defect.
  Never silently skipped. The split exists so that a weak finding costs a reply
  rather than a speculative commit.
- **Done** = `✅ Looks good` **and** CI green **and** no merge conflict.
- **Round cap: three.** On a fourth non-clean verdict the session stops pushing
  and posts one comment naming what is still contested. If CI is green and every
  **CONFIRMED** finding is fixed, it merges and records the contested findings
  in `ROADMAP.md` § The W0 debt register, owned by the next milestone review.
  Otherwise it sets the row to `held` and notifies the human. PR #19 took 27
  pushes; the cap is what catches that.

A verdict that says *the review did not run* or *did not finish* is not a pass
and never merges.

## Merge authority

The loop merges everything: task PRs, spec PRs and plan-review PRs into the
milestone branch — edits to `DESIGN.md`, `ROADMAP.md`, `CLAUDE.md`, the
dependency set and `.claude/` included — and the milestone PR into `main`. The
`human-merge-required` CI job fails, and the loop does not merge, only for a PR
that either:

- **targets `main` from anything but a `milestone/*` branch.** The loop only
  ever targets the milestone branch, so this is a wrong base or a change the
  human opened themselves; or
- **edits `.github/workflows/`.** Not for the contract's sake:
  `claude-code-action` skips itself when the workflow file differs from the
  default branch **and exits successfully**, so such a PR can never get a real
  verdict, and the loop merging it would be merging unreviewed. For the same
  reason a workflow edit never goes into the milestone branch: every task PR
  cut after it would carry a workflow that differs from `main`, and the
  reviewer would pass them all without reading them.

**The loop never commits a workflow edit.** One it wants is a proposal: a row
in `docs/questions.md`, which the human applies when they review the finished
product. The check that holds this is the milestone review's, not this job's:
`ci.yml` runs on `pull_request`, which runs the PR's own copy of the workflow,
so a PR that edits `human-merge-required` is judged by the edited job. A
milestone PR whose diff touches `.github/workflows/` is therefore never merged
by the loop, whatever its CI says (§ Plan reviews).

The reviewer skips the milestone PR itself: every change in it was reviewed on
its way into the milestone branch. The cost is that the loop can rewrite the
plan it executes; the human catches that at the end, in the milestone PRs and
`docs/questions.md`.

## Questions for the human

Where CLAUDE.md says to ask the user, the loop does not stop to ask. It takes
the most conservative reading the documents support — the one cheapest to
reverse — proceeds on it, and appends one row to `docs/questions.md` in the same
PR: the question, the choice taken, and what changes if the answer is
otherwise. A milestone review lists the open rows in the milestone PR's body.
The human answers them when they review the finished product; an answer that
overturns a choice becomes ordinary work for the queue.

A question is never a reason to skip a check, widen a task, or merge something a
review has not passed. If no reading is safe to proceed on, the row is `held`.

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
`docs/decisions.md` § The W0 plan review, then the `retro` skill over the same
tasks — what in the environment (checks, `CODING_STANDARDS.md`, `CLAUDE.md`,
the skills) made them cost what they did. The retro's edits ride the gate's PR;
a `.github/workflows/` edit becomes a proposal in `docs/questions.md` (§ Merge
authority). Gates sit every two or three tasks
and at every milestone. Every gate's PR is merged into the milestone branch by
the loop. An ordinary gate then carries on; a **milestone review** goes on to
close the milestone: its PR sets the `Milestone branch:` line to the next
milestone's branch, and it marks the milestone PR ready *before* merging that
PR — the merge is what puts the next milestone's first task at `next` on M, and
the gate's own open PR is what holds the watchdog until then. It then writes the
milestone PR's body, merges it into `main` once its CI is green, and fires the
Routine. That firing cuts the
next milestone's branch from `main`, and the chain carries on.

If the milestone PR's diff touches `.github/workflows/` — read from the diff,
never from `human-merge-required` (§ Merge authority) — or the merge into
`main` is refused for a permission the session does not have, the milestone
review notifies the human
once, naming the PR and which of the two it is, and stops. Any other red CI or
a conflict on the milestone PR is the milestone review's to fix before it
merges — red CI through a reviewed PR into M, never a commit pushed to M, since
the reviewer skips the milestone PR; a conflict by merging `main` into M; if the session dies first, the watchdog's two-hour
notice is what reports it. While the milestone PR is ready and unmerged the
watchdog starts nothing: the queue on M already names the next milestone's
first task, and running it on the closed milestone's branch is the thing that
check exists to stop. The next firing after the merge picks the chain up again.

## The final product

The last milestone review (M5) closes the queue instead of naming a next
milestone: it merges its milestone PR as above, leaves no row `next`, and
notifies the human once that the product is ready for review, linking the
milestone PRs and `docs/questions.md`. That review is the human's, and the only
one.

## The chain

**A Routine session cannot start another session.** Routines run with no
permission mode ([Routines](https://code.claude.com/docs/en/routines): "there is
no permission-mode picker"), and `create_session` refuses a parent whose mode it
cannot read. There is no setting that changes this. So the Routine *is* the
worker, and both paths below drive the Routine rather than spawn around it.

- **Watchdog.** The Routine finds the milestone branch (creating it when `main`
  names one that does not exist yet, merging `main` into it when `main` is
  ahead, and opening the draft milestone PR once it has commits of its own),
  then runs its checks and, only if all of them hold, works the task itself in
  that same session: the milestone PR is still a draft, the queue has a `next`
  row, no `claude/task-*` PR is open, and nothing is `held`. The checks are what
  stop it double-starting a task or starting the next milestone on a branch
  whose merge is waiting (§ Plan reviews); firing no more often than the slowest
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
  Watchdog for the DnD-Simulator task loop. Be brief and cheap in the
  checks below: do not read DESIGN.md, ROADMAP.md or docs/decisions.md for them.

  1. Read docs/progress.md on main. If it does not exist there, stop silently.
     Its `Milestone branch:` line names the milestone branch M.
  2. If M does not exist, create it from main and push it. If main has
     commits M lacks, merge main into M and push. If M has commits main lacks
     and no pull request from M into main exists, open one as a draft, titled
     after the milestone.
  3. If the pull request from M into main is open and not a draft, stop
     silently — the milestone is closing, and its merge is waiting.
     Exception: if its last activity is over two hours old, say so in one
     sentence naming the PR and what it waits on.
  4. Read docs/progress.md on M. Find the row whose status is `next`. If there
     is none, or any row is `held`: stop, notifying the user in one sentence
     for a `held` row.
  5. If a pull request with a `claude/task-*` head branch is open, stop
     silently — a task is in flight. Exception: if its last activity is over
     two hours old, say so in one sentence naming the PR and what it waits on.

  Only if all of these pass: run `/next-task` yourself, in this session, on M,
  and drive it to a PR merged into M. Never call `create_session` — a Routine
  session has no permission mode, so that call is always refused. Never stop
  to ask the user a question: docs/workflow.md § Questions for the human says
  what to do instead.
  ```

Neither path ever passes a `held` row, or a milestone PR that is ready and
unmerged.

## Token cost

The loop is designed to be cheap, and the design is load-bearing, not incidental:
the reviewer cancels superseded runs and routes prose-only diffs to the cheaper
pass; sessions grep `DESIGN.md`, `ROADMAP.md` and `docs/decisions.md` for the one
claim they need and never read them end to end; the round cap bounds the worst
case at four reviews per task; and the watchdog's prompt is five checks long.
