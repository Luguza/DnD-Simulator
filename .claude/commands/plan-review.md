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

## Deliver

A contract PR against `ROADMAP.md`, `DESIGN.md`, `CLAUDE.md` and
`docs/decisions.md` — new or changed rows, new debt entries, a decision record
for the review itself. Add the next wave's queue rows if this gate is what
unblocks them. Flip the `⏸` row to `done` and the row below to `next`.

It is a contract PR, so `human-merge-required` is red by design: answer the
review, then hand it to the human and stop. Do not start the next task.

Finding nothing is a valid outcome. Say so in one paragraph in
`docs/decisions.md` and flip the rows; do not manufacture changes to justify
the gate.
