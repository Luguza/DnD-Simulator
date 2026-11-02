# Questions for the human

Decisions the task loop took instead of stopping to ask
(`docs/workflow.md` § Questions for the human). Nothing waits on these; they are
answered when the human reviews the finished product. An answer that overturns
a choice becomes ordinary work for the queue.

Each row is written for a D&D expert who knows nothing of the code: the table
situation, the reading taken, and what the other reading would change in play.
The reasoning behind a choice is in `docs/decisions.md`, under the task named
in *Asked by*.

| # | Asked by | Question | Choice taken | If answered otherwise |
|---|---|---|---|---|
| 15 | `w2b` | Unconscious says you drop whatever you're holding. Does a creature that falls unconscious drop its **shield** too? | **No: the weapon drops, the shield stays on** and keeps its +2 AC. A shield takes an action to put on or take off, like armour, so it reads as worn rather than held. | The shield drops as well: an unconscious creature loses the +2 AC, and has to pick the shield up again after it wakes. |

## Answered

Answered by the human on 2026-11-02. The rows keep their numbers because code
and records point at them; the original wording is in this file's history.

| # | Asked by | Question | Answer |
|---|---|---|---|
| 4 | the W1 mid-wave gate | How the automated review loop ends a stalled review. | **Returned to the loop**: a process choice, not a D&D one. The choice stands. |
| 5 | `w1h` | The plan said three conditions make Strength and Dexterity saves fail automatically; the rules list four (Paralyzed, Petrified, Stunned, Unconscious). Build the rules' four? | **Yes. What the plan says is irrelevant if the features are correct**: the rules text wins, and the plan is corrected alongside (`docs/workflow.md` § When the spec is wrong). |
| 6 | `w1i` | How much of the combat log to wire up early. | **Returned to the loop**: a process choice, not a D&D one. The choice stands. |
| 7 | `w1j` | Does a Critical Hit on a creature at 0 HP count as two failed death saves? | **Yes.** |
| 8 | `w1j` | A creature already unconscious from a spell drops to 0 HP and is later healed. Does it stay asleep from the spell? | **Conditions never stack**, so the second Unconscious changes nothing while one holds; the creature stays Unconscious until every reason for it has ended (`docs/decisions.md` § The human's answers, 2026-11-02). |
| 9 | `w1j` | Reaching Exhaustion level 6 should kill; nothing can inflict Exhaustion until much later. Is it fine to connect that rule then? | **Yes.** |
| 10 | `w1k` | A build comparison takes ~3 minutes on one core (~25 s on eight) against a 2-minute target. Acceptable, or must it be made faster first? | **Accepted.** The speed target comes down to the measured rate at the next `⏸` gate. |
| 11 | the M0 / W1 milestone review | Should new work wait until the speed target is met? | **Moot**: the speed fixes ran, and with #10 accepted nothing waits. |
| 12 | the M0 / W1 milestone review | Should fights have cover and terrain, or take place on an open field? | **An open field** in the first version: no walls, no cover. |
| 13 | the M0 / W1 milestone review | Is a plain level 5 Fighter, one attack per turn with no class features, an acceptable first end-to-end test? | **Yes.** |
| 14 | the M0 / W1 milestone review | Which branch a review session pushes to. | **Returned to the loop**: a process choice, not a D&D one. The choice stands. |
| 16 | `w2c` | Whether a large task may be split in two. | **Returned to the loop**: a process choice, not a D&D one. The choice stands. |
