---
name: retro
description: Retrospective over the tasks since the last ⏸ gate — improve the agent's environment (checks, standards, CLAUDE.md, tooling) and the plan. Run by /plan-review at every gate, or by hand.
disable-model-invocation: true
---

# Retrospective

After [mattpocock/skills `retro`](https://github.com/mattpocock/skills/blob/main/skills/engineering/retro/SKILL.md).
`/plan-review` asks whether the **plan** still matches what was measured; this
asks whether the **environment** made the tasks cost what they did. Both land in
the gate's one PR.

## 1. Read the primary sources

The tasks since the previous `⏸` row in `docs/progress.md`. For each merged PR
(GitHub MCP tools):

- the push count and the review rounds — every sticky verdict is rewritten, so
  count the commits and read the replies on the PR;
- each CONFIRMED and PLAUSIBLE finding, and what the fix was;
- every red CI run on the PR, and which check;
- anything the round cap sent to the debt register.

Then the tasks' `docs/decisions.md` sections. When run by hand in a session,
the current session's own transcript is a source too.

## 2. Look for candidates

- **Plan.** Run `/plan-review`'s five questions over the same tasks. A finding
  there is a `ROADMAP.md` or `DESIGN.md` edit, same as the gate always made.
- **Automated checks.** A mistake a check could have caught. Read `ci.yml`,
  `.pre-commit-config.yaml` and the ruff config first — a check that exists but
  is unwired is the finding, not a reinvention.
- **Coding standards.** A defect the reviewer missed, or a rule it keeps
  misapplying. Classify it first: a **mechanical** rule (a banned import, a
  file-location or naming pattern) gets a ruff rule, a pre-commit hook or a test,
  and any line for it in `CODING_STANDARDS.md` goes; a **judgement** rule gets
  added to, clarified in, or cut from `CODING_STANDARDS.md`.
- **CLAUDE.md.** It is in every agent's context and should hold only
  load-bearing invariants and navigation pointers. A rule the reviewer can
  enforce from a diff moves to `CODING_STANDARDS.md`; a paragraph a task summary
  has outgrown moves to `docs/decisions.md`.
- **Navigation.** A task that spent long finding a file or a ruling. Add a row
  to the table in `CLAUDE.md` § Per-module decision records, or a pointer
  where the task went looking.
- **Tool economy.** An expensive tool call, a whole-file read of `DESIGN.md`, a
  review round spent on something a local check would have shown.
- **No-ops.** An instruction in a steering file that changed no behaviour.
- **Information access.** A number the task needed and could not get, e.g. a
  spike rate from the base branch.

## 3. Apply, most severe first

Make the edits in the gate's PR — the checks, `CODING_STANDARDS.md`,
`CLAUDE.md`, the skills, the plan — and record them under a `### Retro`
heading in the gate's `docs/decisions.md` section, each with the PR or finding
that motivated it. Finding nothing is a valid outcome; say so in one line.

**Never edit `.github/workflows/` at an ordinary gate.** It turns
`human-merge-required` red and stops the loop, and the reviewer skips a PR that
edits its own workflow (`docs/workflow.md` § Merge authority). List the change
under `### Retro` as proposed; the next **milestone review** makes it, since a
human merges that PR anyway.
