# Task queue

The serial work queue. **Exactly one row is `next`.** A task's own pull request
flips its row to `done` and the row below to `next`, so merging is what advances
the queue — the ledger cannot desync from git because advancing it *is* a
merged diff.

A `⏸` row is a plan review, run by the loop (`docs/workflow.md` § Plan
reviews). Only a **milestone review** halts the chain: its PR is merged by the
human, and its row must say `milestone review` or CI will not know to stop. A `held`
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
| 5 | w1b — resources, `SpellSlots`, `transaction()` | next | |
| 6 | w1d — grid geometry completed | queued | |
| — | ⏸ plan review | queued | |
| 7 | w1g — saving throws, DCs, save-for-half | queued | |
| 8 | w1h — the 15 combat conditions | queued | |
| 9 | w1i — structured event log | queued | |
| — | ⏸ plan review | queued | |
| 10 | w1j — dying, death saves, healing, temp HP | queued | |
| 11 | w1k — M0 re-measure 🔒 | queued | |
| — | ⏸ **M0 / W1 milestone review** — before any W2 task | queued | |

Order within W1 is the critical path first (`w1e → w1f`, the two 🔒 tasks
everything downstream trusts), then the three files W0 left as first cuts, then
the leaves. `w1k` depends on `w1a`–`w1f` and `w1j`, which is why it is last.

W2 rows are added by the M0 / W1 milestone review, not before it — the review
is what decides whether they are still the right rows.
