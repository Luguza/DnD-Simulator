# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

A Monte Carlo D&D 5e (2024 / XPHB) combat simulator — "SimC for D&D". **Almost
no simulator code exists yet**: the repo holds the design (`DESIGN.md`), the
dependency-ordered build plan (`ROADMAP.md`), the uv/pre-commit toolchain, CI,
`src/dndsim/rules/` — the vocabulary every later package imports —
`src/dndsim/core/` randomness, dice, the clock and the error hierarchy, and
`src/dndsim/hooks/` the closed registry of the 39 extension points, the 31
contexts they hand out, and the decorator, source base class and dispatcher that
run them. Work is at wave **W0** (foundations); `w0a` (scaffold), `w0b` (rules
vocabulary), `w0c` (RNG and dice), `w0d` (timeline), `w0e` (the hook registry),
`w0f` (the contexts) and `w0g` (the dispatch path) are done, `w0g2` (the spike
substrate: a minimal `Creature`, the first cut of `engine/damage.py` and of
`core/geometry.py`) is next.

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

## Hook registry facts (w0e)

- `hooks/registry.py` is the machinery (`Scope`, `Phase`, `HookGroup`,
  `HookSpec`, `HookRegistry`, `define_hook`, `hook_named`) and
  `hooks/definitions.py` is the 39 `define_hook()` calls. Nothing else defines a
  hook, and no hook is defined anywhere else.
- **Importing anything under `dndsim.hooks` defines the hooks**, because
  `hooks/__init__.py` imports `definitions` for its side effect — the one
  import of its kind in the project. Without it `hook_named()` would raise or
  not depending on which module a test happened to import first, which is the
  opposite of the property the registry exists for.
- DESIGN.md's "an unregistered hook returns an empty handler list rather than
  raising" is about *dispatch* — a hook with no handlers attached costs one dict
  lookup — and does not contradict "unknown hook name raises". Names are
  validated once, at import, by `hook_named()`; the hot path never sees a
  string. The error carries `difflib` suggestions, as the APL compiler does for
  a namespace path.
- `HookSpec` is `eq=False`: specs are singletons owned by the registry, so
  identity *is* equality. That makes a dispatch table keyed by spec hash one
  pointer rather than five fields, and it means a hand-built look-alike is not
  the hook. Engine code dispatches on the constant (`ON_HIT`), feature authors
  write the string (`@hook("on_hit")`) — a typo is an `AttributeError` on one
  side and an `UnknownHookError` on the other, both at import.
- A spec names its context type as a **string** (`"HitContext"`), not the class:
  `hooks/contexts.py` (w0f) sits above the registry and imports it, so w0f owns
  the classes and a contract test there asserts the two sides agree.
- `Phase` values are ordinals, for the same reason `TurnPhase`'s are — a band is
  a position, and `"early"` sorts after `"before"` only by luck. Which bands
  need a `# PHASE:` comment lives on `Phase.requires_justification`, next to the
  bands, not as a literal inside the w3b linter.
- **`on_heal` is `TARGET`** — the owner is the creature whose hit points go up,
  matching `on_temp_hp_gained`. The healer's side (Disciple of Life) has nowhere
  to attach; that is outside the W1–W3 slice, so it is a known gap for the `w3e`
  paper pass to confirm and `w3f` to add, not something invented on
  speculation. Four hook *pairs* describe one moment from both ends
  (declare/incoming attack, hit/hit_taken, forced_save/saving_throw,
  damage_roll/incoming_damage); `on_leave_reach` is the project's one
  `OBSERVER`.
- The registry is **not sealed here**. It is closed in the sense that content
  authors may not add to it, but the CI freeze guard is `w3f`'s, after the paper
  pass has added what the pass proves missing (ROADMAP w0e).

## Context facts (w0f)

- `hooks/contexts.py` holds **31 context types for 39 hooks**: eight types are
  named by two hooks each and 23 by one, so 16 + 23 = 39. Six of the eight are
  the two boundaries of one thing (encounter, round, turn, rest, condition,
  effect). The other two are a moment seen from both ends, where the *scope*
  tells the ends apart and the context does not: `on_hit` / `on_hit_taken` and
  `on_declare_attack` / `on_incoming_attack_declared`. **The other two
  `SELF`/`TARGET` pairs deliberately do not share** — a save's DC and its roll
  are different objects, and `on_damage_roll` / `on_incoming_damage` is the
  sharing this task rejected outright (the victim gets rolled amounts, not the
  attacker's bag). A contract test pins the eight by name rather than counting
  them, and checks the registry's `context=` strings against this module in
  **both** directions — a hook naming a class nobody wrote, and a class no hook
  hands out, are both failures.
- A context is a **frozen, slotted, keyword-only dataclass whose `__setattr__`
  is replaced** after the decorator runs. `frozen=True` is what makes
  `ctx.damage = x` a mypy error *and* gives the generated `__init__` a path that
  bypasses the lock; the replacement is what makes the runtime message name the
  mutators instead of saying "cannot assign to field". `@_context` is a
  `dataclass_transform`, so mypy still sees a dataclass.
- **The lock is for features, not for the engine.** `reopen(**fields)` writes
  through `object.__setattr__` and is the pooling primitive DESIGN.md § Levers
  asks for; it names every field it is given, so a typo is an `AttributeError`
  rather than a field left holding the previous attack's value.
- **`PhaseError` is two mistakes, one type** (`core/errors.py`): a mutator
  called out of band (`ctx.veto()` from `NORMAL` — only `EARLY` and `FINAL` may
  halt the chain) and a mutator called after `close()`. The second is what makes
  pooling survivable: a handler that stashes a context and writes to it later is
  writing to whichever event owns that object now, and the seal turns that into
  a raise the first time. It does *not* catch a context already recycled and
  re-opened; nothing cheap does.
- **Provenance is stamped, never passed.** `stamp(source, phase)` is the
  dispatcher's Ambient Context for one handler call, so `ctx.damage.add(...)` is
  attributed with no author effort. There is deliberately no `source=` argument
  to get wrong, and a mutation with nothing stamped is a `PhaseError`.
- **Two contribution types, because two crit rules.** `DamagePart` (dice, flat,
  damage type) is the only thing that can reach `Dice.doubled()`, through
  `on_crit()`, and the *predicate* that decides a crit stays on `Outcome`
  (`Outcome.is_critical`, w0b) rather than being re-spelled by a context.
  `Modifier` is the same dice/flat split for everything whose dice
  never double — a D20 Test bonus, healing, temp HP, a save DC. One class with a
  `doubles: bool` flag would hand the crit rule back to every author who sets it.
  `damage_type=None` means "the type of the attack this rides on", which is a
  rule, not a missing value.
- **Five append-only ledgers** carry what handlers contribute: `DamageBag`,
  `ModifierBag`, `AdvantageLedger` (two lists, so cancellation and idempotence
  are derived), `MitigationLedger` (three *sets*, so a type cannot be resisted
  twice) and `OverrideLedger` (Legendary Resistance, Relentless Endurance). Each
  is a `_Bag`, which is how `reopen()` knows what to bind and what to clear
  without being told. **The declaration is the ownership**: a bag declared with
  a `default_factory` is one the context created, so `reopen()` clears it in
  place; a bag declared *without* one is a bag the context is handed, so it is
  a required field, rebound and never cleared. The damage bag belongs to the
  attack — the damage built at `on_hit` is the damage rolled at
  `on_damage_roll` — so `HitContext`, `MissContext` and `DamageRollContext` all
  declare `damage: DamageBag` bare. Giving any of them a `default_factory`
  would make the pool clear the attack's own damage; a contract test guards it.
  Rebinding is what keeps provenance and the seal with the context currently
  dispatching rather than with one that has closed.
- **`reopen()` demands every field the context does not default** and resets the
  defaulted ones, so no part of the previous event survives by omission — a
  pooled `HitContext` rebound with a new attacker and no new defender would
  otherwise be an attack on the previous target.
- **Participants are `Protocol`s, not imports.** `hooks/` sits below
  `creatures/` and w0f runs two tasks before the first `Creature` exists, so
  `Combatant`, `Weapon`, `Spell`, `Effect` and `Cell` declare only what the
  design has already settled (`Duration` anchors on `initiative_index`; the log
  needs a name). They are meant to stay narrow — one that grows to mirror
  `Creature` is a second domain model — and **w0g2 owes a contract test that its
  real classes satisfy them**.
- Nothing here rolls or applies anything: `engine/damage.py` (w1e) owns the
  resolution order and `engine/attack.py` (w1f) the arithmetic.
  `IncomingDamageContext` therefore *records* resistance, reduction and halving
  rather than applying any of it — and it is handed the damage **rolled**, as an
  amount per type, not as the attacker's `DamageBag`. By that point the dice are
  thrown and `damage_type=None` ("the type of the attack this rides on") has
  been resolved, which is exactly what the type-keyed steps of the order need.
- `core/errors.py` was created here rather than in `w0g` as ROADMAP's table
  says, because `w0f`'s own done-when names `PhaseError`. `w0g` completes it
  with `FeatureContractError` — the same "created early, completed later"
  arrangement the roadmap already uses for `w0g2`'s files.

## Dispatch facts (w0g)

- Three modules for three moments: `hooks/decorator.py` marks a function at
  **decoration**, `hooks/source.py` validates and collects at **class
  creation**, `hooks/dispatch.py` binds and runs at the **run**. `@hook` returns
  the function unchanged with a `Handler` attached, so a handler is still an
  ordinary method — callable in a test, its own name in a traceback.
- **`HookResult` has two members and no third.** `APPLIED` spends the handler's
  `once_per` budget and pays its `costs`; `SKIPPED` spends nothing. Halting the
  chain is `ctx.veto()`, a mutation like any other, and a handler that vetoes
  has `APPLIED`. `R` is a `TypeAlias` for the same enum, not a second one.
- `FeatureDeclarationError` **is an `ImportError`** as well as a
  `FeatureContractError`, which is how ROADMAP's "bad signature is an
  `ImportError`" is literally true: a batch loader already distinguishes "this
  module would not import" from "this module misbehaved". `EngineAssemblyError`
  is the third new error and is deliberately *not* a feature's fault — a
  handler declaring `costs=` with no economy wired in, or `gate=` with no APL,
  raises rather than running free, because a reaction nobody paid for and a gate
  treated as open are silent corruptions of every number.
- Four declaration mistakes are refused at class creation, and the fourth is the
  one invisible in a diff: **an override that dropped its `@hook`**, where the
  base's body would keep running while the author reads their own.
- The context annotation is checked by **identity** against the class the
  registry names, so a same-named look-alike in the author's module is not the
  context. `get_type_hints` resolves it, which is why a context imported only
  under `TYPE_CHECKING` is an error with a sentence saying so.
- **`ActionCost` lives in `rules/enums.py`**, not in `creatures/economy.py`: the
  decorator declares it and w1a's economy consumes it, and neither package may
  import the other. There is no `FREE` member — that is `costs=None`.
- The dispatcher takes `Economy` and `Gate` as **`Protocol`s** (the device w0f
  used for `Combatant`), so `hooks/` still sits below `creatures/` and `apl/`.
  w1a and w2f owe the contract tests that their real classes satisfy them.
- **The dispatch table's tuples are the snapshot.** `attach()`/`detach()` build
  new tuples rather than mutating the ones in the table, so an effect applied
  mid-hook cannot join the event that applied it — the authoring skill's
  snapshot rule made structural rather than documented.
- A `BoundHandler` is created once per (source, declaration) and **survives
  table rebuilds**, because it is the key its own `once_per` budget is filed
  under in the creature's `ResetManager`. Rebuilding those objects on every
  attach would hand a spent Sneak Attack back whenever a condition landed
  mid-turn.
- `ctx.stamp(source, phase)` before each call is the whole of provenance;
  `dispatch()` restores the stamp it found (`Context.stamped()`, added to w0f
  here) so a dispatch leaves the context's attribution as it was. `stamp(None,
  ...)` releases it, which is what makes an engine mutation *outside* a handler
  a `PhaseError` instead of a wrong line in the damage breakdown.
- Only `EARLY` and `FINAL` handlers can veto, so the loop checks `ctx.vetoed`
  only after those — the 95% `NORMAL` case pays one slot load, not a property
  call. A veto also short-circuits the *second* half of a paired dispatch: an
  `on_hit` veto means `on_hit_taken` does not run either.
- The dispatcher deliberately does **not** drain `ctx.pending_effects` or
  `close()` the context: one context is dispatched to two creatures, so the
  engine that owns the event does both, once, afterwards.
- **Measured on the CI-class runner**: ~0.3 µs per handler for the dispatch loop
  itself (call, stamp, budget checks), but ~1.25 µs for one
  `ctx.damage.add(...)`, of which ~0.9 µs is `DamagePart`'s frozen-dataclass
  `__init__`. Fifteen on-hit handlers each adding one part is ~19 µs, against a
  ~20 µs budget for a whole attack resolution. `w0i` is the task that owns that
  number; the cheap lever if it misses is the shape of `DamagePart` and
  `Modifier` (a `NamedTuple` constructs in ~0.38 µs, a plain slotted class in
  ~0.28 µs), not the dispatch design.

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
  installs it editable, so `import dndsim` works from tests. `__version__` is
  read from the installed metadata, so the version lives in `pyproject.toml`
  only.
- `tests/` mirrors `DESIGN.md` § Module layout: `contract/ golden/ interaction/
  statistical/ bench/`, with pytest rooted at `tests` via `pyproject.toml`.
- `.github/workflows/ci.yml` runs the same five checks as the pre-commit hook
  (`uv lock --check`, ruff check, ruff format, mypy, pytest) through
  `uv run --frozen`, each guarded by `if: ${{ !cancelled() }}` so one push
  reports every failure rather than the first. The later roadmap gates —
  frozen-registry diff, benchmark regression, stale generated docs — get added
  to this workflow as their tasks land (`w3b`, `w3c`, `w3i`).
- Ruff excludes `*.md` and `.claude/skills`, so `ruff format` never rewrites the
  code samples in `DESIGN.md` or touches vendored third-party skills; that keeps
  CI and the hook (which only sees `.py`/`.pyi`) in agreement.
- `cli.py` is a placeholder that exits non-zero with a pointer to `w2h`. The
  `dndsim` entry point resolves to a clear message instead of an ImportError;
  no simulation logic lives there, now or later.
