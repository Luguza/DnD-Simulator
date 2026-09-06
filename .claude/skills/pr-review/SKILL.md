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

## The two likeliest subtle bugs in this project

Check these first, on every diff that touches damage or timing.

1. **`dice` doubles on a crit; `flat` never does.** A `DamagePart` splits the
   two so the author never touches the crit path. A static bonus (Rage, Dueling,
   an ability modifier) folded into `dice` — or dice written as a flat average —
   is a silent wrong number on every critical hit. Look for any arithmetic that
   merges the two fields.
2. **`Reset.TURN` is *any* turn boundary in initiative; `Reset.OWN_TURN` is
   yours.** A once-per-turn rider that recharges on `TURN` fires up to N times
   per round. When a feature says "once on each of your turns", it is
   `OWN_TURN`. When it says "once per turn" (Sneak Attack), it is `TURN`.
   Quote the rules sentence and match it to the enum.

## Boundaries that must not leak

- **Content never touches the engine.** A change under `features/`, `spells/`,
  `conditions/` or `content/` that also edits `hooks/`, `engine/` or `core/` is
  wrong by construction. A feature that cannot be expressed declares
  `NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED`. Blocked and
  visible beats improvised and wrong.
- **The hook registry is frozen.** New `define_hook()` calls belong to a
  deliberate, batched core change (roadmap `w3f`), never to a feature PR.
- **Damage resolution order lives in exactly one file**, `engine/damage.py`:
  immunity → vulnerability → resistance → flat reductions → halvings → temp HP →
  HP. A resistance check, a halving, or a temp-HP subtraction anywhere else is a
  duplicate ordering that will disagree with the canonical one.
- **5etools vocabulary stops at `content/`.** `dmg1`, `atkr`, `classTableGroups`,
  `refSubclassFeature`, `refOptionalfeature` must not appear outside that
  package. Scaling numbers reach features via `table_value()` — a hardcoded
  per-level number is a finding even when it is currently correct.
- **`cli.py` holds no simulation logic.** It is a thin adapter over `api.py`, and
  text output is rendered from the same `SimResult` JSON the API returns.

## Structural correctness

- **Advantage is a collection, not a boolean.** It is appended via
  `grant_advantage()` so it stays idempotent and order-independent. Any
  `advantage = True`, `not advantage`, or boolean flip is a finding.
- **Contexts are attribute-locked Parameter Objects.** Features call mutators
  (`ctx.damage.add(...)`, `ctx.apply_effect(...)`); direct field assignment
  raises. A diff that reaches around a mutator is reaching around a guard.
- **Hook `Scope` decides who `self.owner` is.** `if ctx.attacker is self.owner`
  in a feature means the wrong scope was chosen — fix the scope, not the body.
- **Engine-owned contexts are pooled and mutable; anything a feature stores is
  frozen.** A feature holding a reference to a context past its handler, or
  storing mutable state, is a hot-path aliasing bug.
- **`LATE` / `FINAL` phase handlers require a `# PHASE:` justification comment.**
  Unjustified late phases are how ordering becomes load-bearing by accident.
  `rg "PHASE:"` should stay under ~30 hits repo-wide.

## Determinism

- **`import random` is banned outside `core/rng.py`** (the linter enforces it;
  check anyway when the linter is touched). Use the `Rng` strategy — `PcgRng` in
  production, `ScriptedRng` in golden tests.
- Seed + build hash + engine version + content version must reproduce a run
  **byte-for-byte, at any `--jobs` value**. Anything that makes output depend on
  worker count, dict iteration order, or wall-clock time breaks `trace`, the
  goldens and every regression diff at once.

## Evidence a feature PR must carry

- An **XPHB `source_ref`** with the quoted rules sentence — not just a page
  reference. The quote is what a reviewer checks the code against.
- **One golden test with exact-integer assertions.** Distributions prove
  nothing; a hand-computed number does. The arithmetic should be written out in
  a comment so it is reviewable without running anything.
- **The author did not write the tests.** If one agent produced both the feature
  and its golden, the golden only proves the feature does what the author
  thought — say so and ask for an independent test.
- Feature files stay **under 60 lines**; no `try`/`except`, no `hasattr`, no
  private access in `features/`.

## Repo hygiene

- A `pyproject.toml` dependency edit needs `uv lock` **in the same commit**
  (`uv lock --check` is a hook and a CI step).
- `data/` is gitignored and reproducible via `dndsim content fetch`; hand-authored
  `builds/*.yaml` belong in git. A committed 5etools dump is a finding.
- `docs/hooks.md` and `docs/apl.md` are **generated**. Hand edits to them get
  overwritten and CI fails them as stale.

## Scope note

Sections above that reference `features/`, `spells/` and the golden harness
describe rules that only start biting once that code exists (roadmap W3–W4).
Until then they are the standard a new file is held to, not a claim that the
tree already contains one. The dedicated feature-review harness is roadmap task
`w3k`; this checklist is its seed, and should be folded into it rather than
maintained twice.
