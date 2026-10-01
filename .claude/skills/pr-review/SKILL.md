---
name: pr-review
description: Review a change to the D&D simulator on two axes — Standards (CODING_STANDARDS.md — the crit dice/flat split, reset scopes, the frozen hook registry, the content anti-corruption layer, determinism, citations and goldens) and Spec (the ROADMAP task row it implements). Use when reviewing a PR, a diff, or a batch of authored features.
---

# Reviewing a change to dndsim

Two axes, reported separately, after
[mattpocock/skills `code-review`](https://github.com/mattpocock/skills/blob/main/skills/engineering/code-review/SKILL.md):

- **Standards** — does the diff follow [`CODING_STANDARDS.md`](../../../CODING_STANDARDS.md)?
- **Spec** — does the diff do what its task row asked, and only that?

A change can pass one and fail the other: code that honours every invariant but
implements the wrong row, or the right row broken at the crit split. Never merge
or rerank findings across the axes; that is how one masks the other.

## 1. Pin the diff

`git diff origin/<base>...HEAD` (three dots: against the merge base), plus
`git log origin/<base>..HEAD --oneline`, where `<base>` is the PR's base — the
milestone branch for anything the loop opened. An empty diff stops here.

## 2. Find the spec

The task id is in the branch name, `claude/task-<id>` or
`claude/task-review-<id>`.

- A task: `grep -n "^| <id> " ROADMAP.md` — scope, depends, produces,
  done-when — plus the debt-register rows naming `<id>`
  (`ROADMAP.md` § The W0 debt register).
- A `⏸` gate: `.claude/commands/plan-review.md` is the spec.
- A spec PR: the gap the PR body states is the spec.
- Anything else: say "no spec" and review the Standards axis only.

## 3. Review each axis

Where you can spawn subagents, run the two axes as two parallel subagents, each
given the diff command, the commit list and only its own source. Where you
cannot (CI denies `Agent`), do Standards, then Spec, and keep the notes apart.

- **Standards:** every place the diff breaks a rule in `CODING_STANDARDS.md`,
  quoting the rule. Skip anything ruff, mypy or a test already enforces.
- **Spec:** (a) a done-when or debt obligation missing or partial; (b) behaviour
  no row asked for (scope creep); (c) a requirement that looks implemented but
  wrongly. Quote the row for each.

## 4. Report

```markdown
## Standards
<findings, or "No findings.">

## Spec
<findings, or "No findings.", or "No spec.">
```

If both axes are clean, reply with exactly: `No findings.`
