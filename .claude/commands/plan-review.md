---
description: The ⏸ gate — re-read the plans against what the last few tasks measured.
---

A `⏸` row in `docs/progress.md`. You are not writing simulator code.

The model is `docs/decisions.md` § The W0 plan review: that pass produced the
debt register and the `w1k` task, and both came from comparing what the plan
assumed against what the wave actually measured. Do that again, over the tasks
completed since the last gate.

## Read

The `docs/decisions.md` sections for the tasks since the last `⏸`, their merged
PRs, and the ROADMAP rows for what comes next. Grep `DESIGN.md` for the specific
claims those tasks touched — never read it end to end.

## Ask

1. **Did a measurement move a number the plan assumes?** `w0i` revised the
   budget from 50k to 30k; that is the shape of finding worth looking for.
2. **Did a task leave a first cut or a deferred obligation** that no later task
   owns? It belongs in § The W0 debt register with the task that retires it.
3. **Does an upcoming row still say the right thing** given what has landed, or
   does it restate something now settled elsewhere?
4. **Did anything land that DESIGN.md contradicts?** One of the two is wrong.
5. **Is a seam still uncut?** W8's seams are cut in W2 and W3 or they are
   retrofits across every build file.

## Retro

Then run the `retro` skill (`.claude/skills/retro/SKILL.md`) over the same
tasks: the plan questions above ask whether the plan still holds, the retro asks
whether the environment — checks, `CODING_STANDARDS.md`, `CLAUDE.md`, the
skills — made those tasks cost what they did. Its edits ride this PR.

## Deliver

A contract PR against `ROADMAP.md`, `DESIGN.md`, `CLAUDE.md` and
`docs/decisions.md`, plus whatever the retro changed — new or changed rows, new debt entries, a decision record
for the review itself. Add the next wave's queue rows if this gate is what
unblocks them. Flip the `⏸` row to `done` and the row below to `next`.

Branch `claude/task-review-<next id>` so the watchdog sees it in flight, and
answer the review per `/next-task` step 5.

The PR's base is the milestone branch M, like any task's. Merge on a clean
verdict.

- **An ordinary gate:** continue per `/next-task` step 6.
- **A milestone review** (the row says `milestone review`): the gate's PR also
  sets the `Milestone branch:` line in `docs/progress.md` to the next
  milestone's branch. **Mark the milestone PR (M into `main`) ready before
  merging the gate's PR**: from that merge on, M's queue names the next
  milestone's first task, and the open `claude/task-review-*` PR is what holds
  the watchdog until the ready PR can (`docs/workflow.md` § The chain, checks 3
  and 5). Then merge it and close the milestone on M itself
  (`docs/workflow.md` § Plan reviews):
  1. Rewrite the milestone PR's body with the `pr` skill: what
     the milestone delivered, what the loop merged into the plans and the
     environment since the last milestone (every gate's `### Retro`), the
     debt rows the round cap left, and the open rows of `docs/questions.md`,
     workflow-edit proposals among them. It is the record the human reads at
     the end, not a request for review.
  2. If `git diff --name-only origin/main...M`
     lists anything under `.github/workflows/`, do not merge, whatever CI
     says — `human-merge-required` runs the PR's own copy of `ci.yml`
     (`docs/workflow.md` § Merge authority). Otherwise wait for its CI, fix
     any red check or conflict as on any PR, and merge it into `main`. Then
     fire the Routine as in `/next-task` step 6; it cuts the next milestone's
     branch. If the diff touched workflows or the merge is refused for lack of
     permission, notify the human once naming the PR and which it is, and
     stop; the PR stays ready, which is what keeps the watchdog from starting
     the next milestone (`docs/workflow.md` § Plan reviews).
  3. **The last milestone (M5)** names no next branch and leaves no row
     `next`. After the merge, notify the human once that the product is ready
     for their review, linking the milestone PRs and `docs/questions.md`
     (`docs/workflow.md` § The final product).

Finding nothing, in either half, is a valid outcome. Say so in one paragraph in
`docs/decisions.md` and flip the rows; do not manufacture changes to justify
the gate.
