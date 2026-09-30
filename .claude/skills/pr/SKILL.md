---
name: pr
description: The pull-request body for this repo — Summary as a diagram, Evidence, Merge Danger. Use when opening or rewriting a PR.
---

# Writing a PR body

After [mattpocock/skills `pr`](https://github.com/mattpocock/skills/blob/main/skills/engineering/pr/SKILL.md),
itself after Humanlayer's
[`show-me`](https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md).
The body exists so the reviewer, and the human at a milestone, can see how
closely a PR needs reading without reading it. No preamble; keep prose brief;
use `DESIGN.md`'s vocabulary.

```markdown
## Summary
<the smallest diagram that makes the point>

## Evidence
- **Before:** <failing test / spike rate / output>
  **After:** <passing test / spike rate / output>

## Merge Danger
**Door:** <one-way or two-way> — <one line>
**Blast Radius:** <one word> — <what breaks if this is wrong>

## Bookkeeping
- Done-when: <the row's column, and where it is met>
- Debt rows retired / opened: <#n, …>
- Deliberately not done: <…>
```

## Summary

Pick one view, occasionally two:

- **Pseudocode** for an algorithm or an ordering (the damage resolution order,
  a dispatch loop).
- **A call tree** for runtime flow, e.g.
  `resolve_attack → dispatch(on_attack_rolled) → …`.
- **A shallow file tree** for new files or a move.
- **A `diff` block of the shape**, not the code, when the surrounding structure
  already exists:

  ```diff
   declare -> roll -> hit/crit/miss
  +  -> on_damage_roll (hit only)
     -> damage -> resolved
  ```

- **Mermaid** for interaction between creatures, the dispatcher and the store.

Keep only the calls, files and boundaries the reader needs.

## Evidence

A before and after. Execution is the evidence here: the test that failed and
now passes, named and in pseudocode, or the pytest/pre-commit summary. A change
to a hot-path file (the list under **Performance is a design constraint** in
`CLAUDE.md`) shows the paired spike rate and its ratio (`ROADMAP.md`
§ Sequencing principles, 10).

## Merge Danger

A **two-way door** can be walked back by a revert: most task PRs. A
**one-way door** cannot be, cheaply:

- a new hook in the frozen registry, or a changed hook signature;
- anything that changes a seeded run's output, which moves every golden and
  every regression baseline (**Determinism is the backbone**, `CLAUDE.md`);
- a closed vocabulary a later wave writes against (`rules/`, the APL action ids,
  the `Perturbation` profiles);
- a contract edit to `DESIGN.md` or `ROADMAP.md` that later rows are built on.

**Blast radius** names who breaks if this is wrong: the hot path, every
feature, the goldens, the next task, or nothing outside the file.
