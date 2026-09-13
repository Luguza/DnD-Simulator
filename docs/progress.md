# Task queue

The serial work queue. **Exactly one row is `next`.** A task's own pull request
flips its row to `done` and the row below to `next`, so merging is what advances
the queue — the ledger cannot desync from git because advancing it *is* a
merged diff.

A `⏸` row halts the chain: it is a plan review, and the session that reaches it
stops and hands back to the human (`docs/workflow.md` § Plan reviews).

Statuses: `done` · `next` · `queued` · `held` (blocked, see the PR).

| # | Task | Status | PR |
|---|---|---|---|
| 1 | w1a — `Creature`, `StatBlock`, `ActionEconomy` | done | #21 |
| 2 | w1e — damage resolution order completed 🔒 | done | #24 |
| 3 | w1f — attack pipeline 🔒 | next | |
| — | ⏸ plan review — the critical-path pair | queued | |
| 4 | w1c — `Effect`, `EffectStore`, stacking | queued | |
| 5 | w1b — resources, `SpellSlots`, `transaction()` | queued | |
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
