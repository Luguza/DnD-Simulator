# Task queue

Milestone branch: `milestone/m0-w1`

The serial work queue. **Exactly one row is `next`.** A task's own pull request
flips its row to `done` and the row below to `next`, so merging is what advances
the queue — the ledger cannot desync from git because advancing it *is* a
merged diff. Task PRs merge into the milestone branch named above, so the live
queue is the copy on that branch; the one on `main` is the queue as of the last
milestone (`docs/workflow.md` § The milestone branch).

A `⏸` row is a plan review, run by the loop (`docs/workflow.md` § Plan
reviews). A **milestone review** also merges the milestone PR into `main` and
carries on; its row must say `milestone review`, and its PR sets the line above
to the next milestone's branch. A milestone PR the loop could not merge stays
ready and halts the chain until it merges (`docs/workflow.md` § Plan reviews),
whatever row is `next`. A `held`
row halts it too, and while one stands **no row is `next`** — the row below a
held task advances only when that task's diff merges, and a `next` row sitting
under a hold is an invitation to start work on a dependency that never landed.

Statuses: `done` · `next` · `queued` · `held` (blocked, see the PR).

| # | Task | Status | PR |
|---|---|---|---|
| 1 | w1a — `Creature`, `StatBlock`, `ActionEconomy` | done | #21 |
| 2 | w1e — damage resolution order completed 🔒 | done | #24 |
| 3 | w1f — attack pipeline 🔒 | done | #27 |
| — | ⏸ plan review — the critical-path pair | done | #30 |
| 4 | w1c — `Effect`, `EffectStore`, stacking | done | #31 |
| 5 | w1b — resources, `SpellSlots`, `transaction()` | done | #32 |
| 6 | w1d — grid geometry completed | done | #41 |
| — | ⏸ plan review | done | #42 |
| 7 | w1g — saving throws, DCs, save-for-half | done | #45 |
| 8 | w1h — the 15 combat conditions | done | #46 |
| 9 | w1i — structured event log | done | #50 |
| — | ⏸ plan review | done | #51 |
| 10 | w1j — dying, death saves, healing, temp HP | next | |
| 11 | w1l — docstrings cut back to their own code | queued | |
| 12 | w1k — M0 re-measure 🔒 | queued | |
| — | ⏸ **M0 / W1 milestone review** — before any W2 task | queued | |

Order within W1 is the critical path first (`w1e → w1f`, the two 🔒 tasks
everything downstream trusts), then the three files W0 left as first cuts, then
the leaves. `w1k` depends on `w1a`–`w1g`, `w1i` and `w1j`, which is why it is
last: every row that prices itself has to have landed before the totalling.

W2 rows are added by the M0 / W1 milestone review, not before it — the review
is what decides whether they are still the right rows.
