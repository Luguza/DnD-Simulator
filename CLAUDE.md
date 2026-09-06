# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

A Monte Carlo D&D 5e (2024 / XPHB) combat simulator — "SimC for D&D". **Almost
no simulator code exists yet**: the repo holds the design (`DESIGN.md`), the
dependency-ordered build plan (`ROADMAP.md`), the uv/pre-commit toolchain, CI,
`src/dndsim/rules/` — the vocabulary every later package imports — and
`src/dndsim/core/` randomness, dice and the clock. Work is at wave **W0**
(foundations); `w0a` (scaffold), `w0b` (rules vocabulary), `w0c` (RNG and dice)
and `w0d` (timeline) are done, `w0e` (the hook registry) is next.

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
into a closed registry of 39 hooks. Features never drive the engine; the engine
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

`ROADMAP.md` has 60 tasks across waves W0–W7 with explicit dependency edges.
Two constraints dominate:

- **Nothing in W4 or later may start before M2** (API frozen, authoring skill
  written, paper pass done). The W3 freeze gate is what prevents 400 subtly
  wrong features.
- W0's performance spike (w0i) is a stop-and-revise gate: missing the budget
  means changing the dispatch design then, not later. It runs against the `w0g2`
  substrate — a first cut of `Creature`, `engine/damage.py` and
  `core/geometry.py` that W1 completes rather than rewrites — because a spike
  against stubs measures nothing.

Tasks are tagged 🔒 Solo (high blast radius, serial), 👥 Delegable, or
🚀 Batch (parallel agents on disjoint files). The task-packet template at the
end of `ROADMAP.md` is what a delegated agent receives.

## Conventions

- Ruff: line length 88, rules `E,F,I,UP,B,SIM`, target py310.
- mypy strict across the whole tree.
- `LATE`/`FINAL` phase handlers require a `# PHASE:` justification comment
  (linter-enforced); `rg "PHASE:"` should stay under ~30 hits.
- Every feature needs an XPHB `source_ref` carrying the quoted rules sentence,
  one golden test with exact-integer assertions, and a declared analytic
  `expectation` that never imports `dndsim`.
- `data/` is gitignored — fetched 5etools JSON, reproducible via
  `dndsim content fetch`. Hand-authored `builds/*.yaml` are in git.

## Rules vocabulary facts (w0b)

- `rules/` is the bottom of the stack and imports nothing else from `dndsim`.
  Names are not re-exported from `rules/__init__.py`: one name, one import path.
- Enums are plain `Enum`, **never `StrEnum`**, and that is load-bearing. Under
  mypy strict, `damage_type == "fire"` is then a `comparison-overlap` error
  instead of a comparison that works until someone misspells it — which is what
  makes "no string literals downstream" structural rather than aspirational.
  Members still carry lowercase string values for JSON, the log, and parsing at
  the `content/` boundary (`DamageType("holy")` raises).
- `rules.enums.Condition` is the **name** of a condition; the *behaviour* is a
  `HookSource` subclass in `conditions/` (w1h). Two different things, one word —
  import the enum qualified when both are in scope.
- `Outcome` is the attack-roll outcome only. 2024 calls attack rolls, checks and
  saves all "D20 Tests", but a natural 20 only crits on an attack roll, so saves
  get their own result type in `engine/saves.py` (w1g).
- The spell-slot tables in `rules/constants.py` overlap what w2a imports from
  `classTableGroups.rowsSpellProgression`. That is deliberate: w2a **checks the
  import against them** rather than picking a winner, so a dataset change is
  caught at the ACL. Per-class scaling (Rage Damage, Sneak Attack dice) is not
  in `constants.py` and never should be — that is `table_value()`.

## RNG and dice facts (w0c)

- `core/rng.py` is the only module allowed to be random, and it does not import
  `random` either: `PcgRng` is PCG32 (XSH RR 64/32) written out, because the
  requirement is not "random numbers" but *this* sequence of die faces, still
  reproducible from an archived `SimResult` years from now. `random.Random`'s
  bit-stream-to-die-face mapping is a CPython implementation detail and
  `numpy.random.Generator` costs microseconds per call. A contract test pins the
  stream against O'Neill's published `pcg32-demo` output — that is the one oracle
  here the project cannot fudge, and any change to the permutation invalidates
  every archived run.
- The `Rng` protocol's primitive is **one die face**, not a raw word, because a
  die face is what a golden test scripts. `ScriptedRng` consumes faces left to
  right, raises `ScriptedRngExhausted` on over-draw and `ValueError` on a face
  the die cannot show; `exhausted` lets a golden pin how many dice were rolled,
  not just the total.
- `mix(master_seed, iteration_index)` is two SplitMix64 rounds, not addition:
  `PcgRng` seeding advances its LCG twice, so `master_seed + i` would hand
  neighbouring iterations neighbouring streams. It is part of the reproducibility
  contract — changing it changes what every archived seed means.
- `Dice.parse` **refuses a flat modifier**: `"1d8+3"` raises, naming where the
  `+3` belongs. Accepting it would let a string bypass the `DamagePart`
  dice/flat split, which is what makes the crit rule structural rather than
  conventional. `Dice.doubled()` is the crit primitive — it doubles the pool, so
  a crit re-rolls dice instead of multiplying a total.
- Measured throughput on the CI-class runner is ~1.8M `roll(20)`/sec/core
  (~560 ns). That is the number `w0i` is spending against its 50k attack
  resolutions/sec budget; the inlined variant measures the same, so the cost is
  Python call overhead rather than the arithmetic.

## Timeline facts (w0d)

- `core/timeline.py` holds no clock *object*. `TimeKey(round, initiative_index,
  phase)` is a position and the engine's turn loop (`engine/turn.py`, w1f) owns
  the current one; a second mutable clock would be a second source of truth for
  the one fact the simulation is ordered by. Rounds are 1-based and an
  initiative index is a seat in the order (0 acts first), both validated.
- `TurnPhase` is the one enum whose values are **ordinals, not lowercase
  strings** — it is a position on a clock and `"end" < "start"` alphabetically.
  It is still a plain `Enum`, so `phase == 0` stays a mypy error; the log
  serialises `phase.name.lower()`.
- `Duration` (relative, declarative) and `Expiry` (absolute: a `TimeKey` plus
  `through_phase`) are two types so that a resolved deadline cannot be carried
  without the flag that decides it. The flag is read through two named sweeps
  rather than a boolean argument: `expires_before(now)` runs at a boundary
  before its hooks, `expires_after(now)` once they are done. Rage survives the
  first and not the second; Reckless Attack fails both.
- **"Your next turn" is the next turn, not the next boundary of that phase.**
  Said on your own turn it means a round from now, so `resolve()` counts the
  anchor's turn in the current round only when it has not begun
  (`anchor > now.initiative_index`).
- `Duration` anchors on an initiative **index**, not a creature: `core/` sits
  below `creatures/` and may not import it. `creatures/effects.py` (w1c) passes
  `creature.initiative_index` — that is what `DESIGN.md`'s
  `until_end_of_next_turn_of(c)` spells one layer up.
- Two deliberate omissions. There is no multi-round anchor (every W1 duration
  ends one turn ahead; a "1 minute" spell wants an additive `turns` field in
  W4), and no "permanent" duration — Prone lasts until you stand up, which is
  no deadline at all, so such an effect carries no `Duration`.
- `ResetManager` is **one ledger per creature**, which is why `Reset.TURN` vs
  `Reset.OWN_TURN` needs no creature identity inside it: the turn loop calls
  `turn_started(own=...)` and already knows whose turn it is. A global ledger
  keyed by owner would put an encounter-sized lookup on the path every handler
  of every attack walks. Each boundary clears exactly the scopes it names —
  a rest does not touch turn budgets, because the next turn boundary hands
  those back before anything in initiative can ask.
- Recharge on a die roll (a monster's `{@recharge}`) is not a `Reset` member:
  it is a roll, not a boundary, and belongs to `Recovery` with the resources
  (w1b).

## Working rules

- Follow the YAGNI principle.
- Use skills with care: only when you make large changes or when they
  specifically apply.
- The agent that writes a feature writes its golden, but is never the only party
  that has computed the number: a separate read-only agent re-derives it from the
  quoted `source_ref` alone before the batch is accepted (`w3k`).
- Never let the agent that writes the feature write the tests.
- Critically review every task specification before implementing it — the task
  packets in `ROADMAP.md`, the contracts in `DESIGN.md`, and any task given in
  chat. Check them against the rest of the design for gaps, contradictions and
  unstated assumptions.
- Ask the user for clarification when something is ambiguous or looks wrong
  instead of guessing.

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
