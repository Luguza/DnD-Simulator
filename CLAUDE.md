# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

A Monte Carlo D&D 5e (2024 / XPHB) combat simulator — "SimC for D&D". **No
simulator code exists yet** beyond the scaffold: the repo holds the design
(`DESIGN.md`), the dependency-ordered build plan (`ROADMAP.md`), the
uv/pre-commit toolchain, CI, and an empty `src/dndsim/` package. Work is at wave
**W0** (foundations); `w0a` (scaffold) is done, `w0b` (rules vocabulary) is
next.

Both documents are load-bearing: `DESIGN.md` fixes the architecture and the
vocabulary, `ROADMAP.md` fixes what may be built when and by whom. Read the
relevant sections before writing code in an area — they are the contract, not
background reading.

## Commands

The toolchain is **uv**, not pip.

```sh
uv sync                              # create .venv with the dev toolchain
uv run pre-commit install            # install the git pre-commit hook
uv run pytest                        # tests
uv run pytest tests/path/test_x.py::test_name   # single test
uv run pre-commit run --all-files    # every hook over the whole repo
uv run --frozen ruff check --fix     # lint (same invocation the hook uses)
uv run --frozen ruff format
uv run --frozen mypy .               # strict mode, warn_unreachable
```

Hooks call ruff/mypy through `uv run --frozen`, so hook, CI and local runs use
exactly the lockfile-pinned versions. `uv lock --check` is a hook — a
`pyproject.toml` dependency edit needs `uv lock` in the same commit.

The `dndsim` CLI described in `DESIGN.md` (`sim`, `day`, `compare`, `inspect`,
`validate`, `trace`, `check`, `bench`, `content fetch`) is planned for W2, not
yet implemented.

## Architecture: the load-bearing decisions

Full rationale in `DESIGN.md`; what follows is what you cannot infer from any
single file.

**Everything hangs off the hook pipeline.** ~630 content implementations (~400
class/subclass/feat features + ~18 species traits + 212 combat spells) plug
into a closed registry of 34 hooks. Features never drive the engine; the engine
dispatches to them. `Effect`, `Condition`, `Feature` and magic items are all
`HookSource` subclasses with one identical API shape, so learning one teaches
all four.

**The registry is frozen and CI-guarded.** Content work must never touch
`hooks/`, `engine/` or `core/`. A feature that cannot be expressed declares
`NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED`; a collection test
lists them, and hook additions are batched into a deliberate core change.
Blocked-and-visible beats improvised-and-wrong.

**Correctness is structural, not tested-for.** The data model is shaped so
authors cannot make the common mistakes:

- `DamagePart` splits `dice` from `flat`; **`dice` doubles on a crit, `flat`
  never does**, and the author never touches the crit path.
- Advantage is a collection of sources appended via `grant_advantage()`, never a
  boolean — so it is idempotent and order-independent.
- Damage resolution order lives in exactly one file, `engine/damage.py`:
  immunity → vulnerability → resistance → flat reductions → halvings → temp HP
  → HP.
- Contexts are attribute-locked Parameter Objects. Features call mutators
  (`ctx.damage.add(...)`, `ctx.apply_effect(...)`); direct field assignment
  raises.
- Hook `Scope` (`SELF` / `TARGET` / `OBSERVER` / `GLOBAL`) decides who
  `self.owner` is, so no feature writes `if ctx.attacker is self.owner`.
- `Reset.TURN` is *any* turn boundary in initiative; `Reset.OWN_TURN` is yours.
  This and the dice/flat split are the two likeliest subtle bugs in the project.

**Determinism is the backbone.** Seed + build hash + engine version + content
version reproduces any run byte-for-byte, which is what makes `trace`, golden
tests and regression diffs possible. `import random` is banned outside
`core/rng.py` and the linter enforces it; use the `Rng` strategy (`PcgRng` in
production, `ScriptedRng` in golden tests).

**Boundaries that must not leak.** `content/` is an anti-corruption layer —
5etools vocabulary (`dmg1`, `atkr`, `classTableGroups`, `refSubclassFeature`)
stops there and never reaches the domain model; scaling numbers reach features
only via `table_value()`, never hardcoded. `cli.py` is a thin adapter over
`api.py` and holds no simulation logic; text output is rendered from the same
`SimResult` JSON the API returns, so the two cannot disagree.

**Performance is a design constraint, not a later concern.** Budget is ≥50k
attack resolutions/sec/core, proven on a stub in W0 before any content is
written. Contexts are pooled and mutable (engine-owned, hot path); everything a
feature stores is frozen (author-owned, cold path).

## Sequencing rules

`ROADMAP.md` has 51 tasks across waves W0–W7 with explicit dependency edges.
Two constraints dominate:

- **Nothing in W4 or later may start before M2** (API frozen, authoring skill
  written, paper pass done). The W3 freeze gate is what prevents 400 subtly
  wrong features.
- W0's performance spike (w0h) is a stop-and-revise gate: missing the budget
  means changing the dispatch design then, not later.

Tasks are tagged 🔒 Solo (high blast radius, serial), 👥 Delegable, or
🚀 Batch (parallel agents on disjoint files). The task-packet template at the
end of `ROADMAP.md` is what a delegated agent receives.

## Conventions

- Ruff: line length 88, rules `E,F,I,UP,B,SIM`, target py310.
- mypy strict across the whole tree.
- `LATE`/`FINAL` phase handlers require a `# PHASE:` justification comment
  (linter-enforced); `rg "PHASE:"` should stay under ~30 hits.
- Every feature needs an XPHB `source_ref` citation and one golden test with
  exact-integer assertions.
- `data/` is gitignored — fetched 5etools JSON, reproducible via
  `dndsim content fetch`. Hand-authored `builds/*.yaml` are in git.

## Working rules

- Follow the YAGNI principle.
- Use skills with care: only when you make large changes or when they
  specifically apply.
- Never let the agent that writes the feature write the tests.

## Scaffold facts (w0a)

- Python **3.11+** everywhere: `.python-version`, `requires-python`, and the
  mypy/ruff targets. `DESIGN.md` § Settled decisions is the source of that
  number.
- src layout, hatchling build backend, package root `src/dndsim/`. `uv sync`
  installs it editable, so `import dndsim` works from tests.
- `tests/` mirrors `DESIGN.md` § Module layout: `contract/ golden/ interaction/
  statistical/ bench/`, with pytest rooted at `tests` via `pyproject.toml`.
- `.github/workflows/ci.yml` runs the same five checks as the pre-commit hook
  (`uv lock --check`, ruff check, ruff format, mypy, pytest) through
  `uv run --frozen`. The later roadmap gates — frozen-registry diff, benchmark
  regression, stale generated docs — get added to this workflow as their tasks
  land (`w3b`, `w3c`, `w3i`).
- Ruff excludes `*.md`, so `ruff format` never rewrites the code samples in
  `DESIGN.md`; that keeps CI and the hook (which only sees `.py`/`.pyi`)
  in agreement.
