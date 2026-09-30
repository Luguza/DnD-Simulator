# The task loop

One task, one session, one branch, one pull request, reviewed until clean — into
the **milestone branch**, never into `main`. The only pull request into `main`
is the milestone branch itself, and that one the human reviews and merges.
`docs/progress.md` is the queue; this file is the procedure.

## The milestone branch

Each ROADMAP milestone (M0–M5) has one branch, `milestone/<name>`, cut from
`main`, and one pull request from it into `main`: the **milestone PR**. Every
task, spec and plan-review PR in that milestone targets the milestone branch and
is merged there by the loop. So when the milestone closes, the milestone PR's
diff is everything the milestone did, in one place, and merging it is the
human's review.

- **Which branch.** The `Milestone branch:` line at the top of
  `docs/progress.md` **on `main`** names it. `main` only moves when a milestone
  PR merges, so that line changes exactly then — the milestone review writes the
  next branch's name onto the milestone branch, and merging the milestone PR is
  what publishes it.
- **Draft means the loop's, ready means the human's.** The milestone PR is
  opened as a draft once the branch has its first merge — GitHub refuses a pull
  request with no commits between the two branches, so it cannot be opened when
  the branch is created. The milestone review marks it
  ready; from then until the human merges it, nothing is started.
- **The queue lives on the milestone branch.** Task PRs flip rows there, so the
  loop reads `docs/progress.md` from the milestone branch; the copy on `main` is
  the queue as of the last milestone.
- **`main` flows in, never out.** A change the human merges straight to `main`
  (a workflow edit, a tooling fix) is merged into the milestone branch before
  the next task starts, so the two never diverge and the milestone PR never
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
   Red on a task PR means a workflow edit or a wrong base: it stops and hands
   back (§ Merge authority).

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
  in `ROADMAP.md` § The W0 debt register, owned by the next milestone review, so
  the human reads them there. Otherwise it sets the row to `held` and notifies
  the human. PR #19 took 27 pushes; the cap is what catches that.

A verdict that says *the review did not run* or *did not finish* is not a pass
and never merges.

## Merge authority

The human reads the code at ROADMAP milestones (M0–M5) and nowhere else, so the
loop merges everything in between into the milestone branch — task PRs, spec
PRs and plan-review PRs, including edits to `DESIGN.md`, `ROADMAP.md`,
`CLAUDE.md`, the dependency set and `.claude/`. The `human-merge-required` CI
job fails, and the loop stops, in exactly two cases:

- **Into `main`** — any PR whose base is `main`. The loop only ever targets the
  milestone branch, so this is the milestone PR, whose merge is the human's
  review of the whole milestone, or a change the human opened themselves.
- **Workflow edit** — anything under `.github/workflows/`. Not for the
  contract's sake: `claude-code-action` skips itself when the workflow file
  differs from the default branch **and exits successfully**, so such a PR can
  never get a real verdict, and the loop merging it would be merging unreviewed.
  For the same reason a workflow edit never goes into the milestone branch
  mid-milestone: every task PR cut after it would carry a workflow that differs
  from `main`, and the reviewer would pass them all without reading them. A
  workflow edit the loop wants is proposed at a gate and committed by the
  milestone review, after the last task (§ Plan reviews); one the human wants
  goes to `main` in its own PR and flows into the milestone branch from there.

The reviewer skips the milestone PR itself: every change in it has already been
reviewed on its way into the milestone branch. The cost is that the loop can
rewrite the plan it executes; the milestone PR is where the human catches
that.

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
a `.github/workflows/` edit waits for the next milestone review. Gates sit every two or three tasks
and at every milestone. Every gate's PR is merged into the milestone branch by
the loop. An ordinary gate then carries on; a **milestone review** goes on to
close the milestone: it commits the workflow edits earlier gates proposed
straight onto the milestone branch, sets the `Milestone branch:` line to the
next milestone's branch, writes the milestone PR's body for the human and marks
it ready. The chain waits for the human's merge.

## The chain

**A Routine session cannot start another session.** Routines run with no
permission mode ([Routines](https://code.claude.com/docs/en/routines): "there is
no permission-mode picker"), and `create_session` refuses a parent whose mode it
cannot read. There is no setting that changes this. So the Routine *is* the
worker, and both paths below drive the Routine rather than spawn around it.

- **Watchdog.** The Routine finds the milestone branch (creating it when `main`
  names one that does not exist yet, merging `main` into it when `main` is
  ahead, and opening the draft milestone PR once it has commits of its own), then runs its checks and, only if all of
  them hold, works the task itself in that same session: the milestone PR is
  still a draft, the queue has a `next` row, no `claude/task-*` PR is open, and
  nothing is `held`. The checks are what stop it double-starting a task or
  starting one the human has not released; firing no more often than the
  slowest task reaches its PR is the other half of that guard.
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
     silently — the human is reviewing the milestone.
  4. Read docs/progress.md on M. Find the row whose status is `next`. If there
     is none, or any row is `held`: stop, notifying the user in one sentence
     for a `held` row.
  5. If a pull request with a `claude/task-*` head branch is open, stop
     silently — a task is in flight. Exception: if its last activity is over
     two hours old, say so in one sentence naming the PR and what it waits on.

  Only if all of these pass: run `/next-task` yourself, in this session, on M,
  and drive it to a PR merged into M. Never call `create_session` — a Routine
  session has no permission mode, so that call is always refused.
  ```

Neither path ever passes a `held` row, or a milestone PR that is ready for the
human.

## Token cost

The loop is designed to be cheap, and the design is load-bearing, not incidental:
the reviewer cancels superseded runs and routes prose-only diffs to the cheaper
pass; sessions grep `DESIGN.md`, `ROADMAP.md` and `docs/decisions.md` for the one
claim they need and never read them end to end; the round cap bounds the worst
case at four reviews per task; and the watchdog's prompt is three checks long.
