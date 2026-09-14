---
name: pr-review
description: Review a change to the D&D simulator against the project's load-bearing invariants — the crit dice/flat split, reset scopes, the frozen hook registry, the content anti-corruption layer, determinism, and the citation/golden requirement. Use when reviewing a PR, a diff, or a batch of authored features.
---

# Reviewing a change to dndsim

`CLAUDE.md` and `DESIGN.md` are the contract; this is the checklist. Report only
findings you can tie to a **specific line** and a **specific rule below**. A
review that always finds something is a review nobody reads — "no findings" is a
valid and useful outcome.

Rank findings: **correctness bugs → boundary violations → everything else.**

## Check first, on every diff that touches damage or timing

1. **`dice` doubles on a crit; `flat` never does.** A static bonus (Rage,
   Dueling, an ability modifier) folded into `dice` — or dice written as a flat
   average — is a silent wrong number on every critical hit. Look for any
   arithmetic that merges the two fields.
2. **`Reset.TURN` is *any* turn boundary; `Reset.OWN_TURN` is yours.** A
   once-per-turn rider that recharges on `TURN` fires up to N times per round.
   "Once on each of your turns" is `OWN_TURN`; "once per turn" (Sneak Attack) is
   `TURN`. Quote the rules sentence and match it to the enum.

## Boundaries

- **Content never touches the engine.** A change under `features/`, `spells/`,
  `conditions/` or `content/` that also edits `hooks/`, `engine/` or `core/` is
  wrong by construction — the feature declares
  `NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED` instead.
- **The hook registry is frozen.** A new `define_hook()` belongs to a batched
  core change (`w3f`), never to a feature PR.
- **Damage resolution order lives only in `engine/damage.py`.** A resistance
  check, halving or temp-HP subtraction anywhere else is a second ordering that
  will disagree with the canonical one.
- **5etools vocabulary stops at `content/`.** `dmg1`, `atkr`,
  `classTableGroups`, `refSubclassFeature`, `refOptionalfeature` outside that
  package is a finding, as is a hardcoded per-level number that should come from
  `table_value()` — even when it is currently correct.
- **`cli.py` holds no simulation logic**; it renders the `SimResult` JSON
  `api.py` returns.

## Structural correctness

- **Advantage is a collection, not a boolean.** Any `advantage = True`,
  `not advantage`, or boolean flip is a finding; it is appended via
  `grant_advantage()`.
- **Contexts are attribute-locked.** A diff that assigns a field directly
  instead of calling a mutator is reaching around a guard.
- **Scope decides `self.owner`.** `if ctx.attacker is self.owner` in a feature
  means the wrong scope was chosen — fix the scope, not the body.
- **Contexts are pooled.** A feature holding a context past its handler, or
  storing mutable state, is a hot-path aliasing bug.
- **`LATE`/`FINAL` handlers need a `# PHASE:` justification comment.**
  Unjustified late phases are how ordering becomes load-bearing by accident;
  `rg "PHASE:"` should stay under ~30 hits repo-wide.

## Determinism

- **`import random` outside `core/rng.py`** is banned (the linter enforces it —
  check anyway when the linter is touched).
- Output must not depend on worker count, dict iteration order or wall-clock
  time: a run must reproduce byte-for-byte **at any `--jobs` value**, or
  `trace`, the goldens and every regression diff break at once.

## Citations and comments, on every diff

- **A citation points; it does not restate.** A paraphrase or a re-derivation
  beside the code is a second copy of the rule that can drift from it — flag it.
  Where a citation does assert a consequence, work the consequence out rather
  than reading it: `w1e` shipped three that were false (`docs/decisions.md`
  § w1e).
- **Deleting prose is a citation change.** A diff that removes an argument,
  a quotation or a worked example has to move every pointer aimed at it;
  `grep` the deleted phrase across `DESIGN.md`, `ROADMAP.md`, `CLAUDE.md`,
  `docs/` **and `src/`** — a docstring in another module cites its neighbours as
  readily as a plan does. Four rounds of #25's own review were dangling inbound
  pointers, one of them in `src/` because this list first omitted it.

## Evidence a feature PR must carry

- An **XPHB `source_ref` with the quoted rules sentence** — not just a page
  reference. The quote is what the code is checked against.
- **One golden test with exact-integer assertions**, its arithmetic written out
  in a comment. Distributions prove nothing; a hand-computed number does.
- **The author did not write the tests.** One agent producing both the feature
  and its golden only proves the feature does what the author thought — say so
  and ask for an independent test.
- Feature files stay **under 60 lines**; no `try`/`except`, no `hasattr`, no
  private access in `features/`.

## Repo hygiene

- A `pyproject.toml` dependency edit needs `uv lock` **in the same commit**.
- `data/` is gitignored and refetchable; a committed 5etools dump is a finding.
  Hand-authored `builds/*.yaml` belong in git.
- `docs/hooks.md` and `docs/apl.md` are **generated** — hand edits get
  overwritten and CI fails them as stale.

## Scope note

Sections referencing `features/`, `spells/` and the golden harness describe
rules that only start biting once that code exists (W3–W4). Until then they are
the standard a new file is held to, not a claim that the tree contains one. The
dedicated feature-review harness is `w3k`; this checklist is its seed and should
be folded into it rather than maintained twice.
