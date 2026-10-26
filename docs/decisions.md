# Decision records

The reasoning behind choices you cannot infer from any single file, one section
per completed roadmap task. `CLAUDE.md` holds the invariants that apply
everywhere; this file holds the ones local to a module.

**Read the section for an area before writing code in it.** `DESIGN.md` says
what the architecture is and `ROADMAP.md` what may be built when — these are the
notes on *why the code came out this way*, so a settled decision is not
re-litigated by the next agent through the area.

| Touching | Read |
|---|---|
| `pyproject.toml`, CI, tooling | [w0a](#w0a--scaffold) |
| `rules/` | [w0b](#w0b--rules-vocabulary) |
| `core/rng.py`, `core/dice.py` | [w0c](#w0c--rng-and-dice) |
| `core/timeline.py` | [w0d](#w0d--timeline) |
| `hooks/registry.py`, `hooks/definitions.py` | [w0e](#w0e--hook-registry) |
| `hooks/contexts.py`, `core/errors.py` | [w0f](#w0f--contexts) |
| `hooks/decorator.py`, `hooks/source.py`, `hooks/dispatch.py` | [w0g](#w0g--dispatch) |
| `creatures/`, `engine/damage.py`, `core/geometry.py` | [w0g2](#w0g2--spike-substrate) |
| `tools/skeleton.py`, `tests/bench/test_skeleton.py` | [w0h](#w0h--walking-skeleton) |
| `tools/spike*.py`, `tests/bench/test_spike.py`, the hot path | [w0i](#w0i--the-pessimistic-performance-spike-m0) |
| `ROADMAP.md`, `DESIGN.md`, planning a wave | [the W0 plan review](#the-w0-plan-review--what-m0-changed-in-the-plan) |
| `.claude/skills/`, `CODING_STANDARDS.md`, the review loop | [the PR, review and retro skills](#the-pr-review-and-retro-skills) |

## w0a — scaffold

- Python **3.11+** everywhere: `.python-version`, `requires-python`, the
  mypy/ruff targets. `DESIGN.md` § Settled decisions is the source of that
  number.
- src layout, hatchling, package root `src/dndsim/`; `uv sync` installs it
  editable so `import dndsim` works from tests. `__version__` is read from
  installed metadata, so the version lives in `pyproject.toml` only.
- `tests/` mirrors `DESIGN.md` § Module layout: `contract/ golden/ interaction/
  statistical/ bench/`, pytest rooted at `tests`.
- `.github/workflows/ci.yml` runs the same five checks as the pre-commit hook
  (`uv lock --check`, ruff check, ruff format, mypy, pytest) through
  `uv run --frozen`, each guarded by `if: ${{ !cancelled() }}` so one push
  reports every failure rather than the first. Later gates — the feature linter
  (`w3b`), stale generated docs (`w3c`), the benchmark regression gate (`w3i`)
  and the frozen-registry diff (`w3f`, after the paper pass has added what it
  proves missing) — join this workflow as their tasks land.
- Ruff excludes `*.md` and `.claude/skills`, so `ruff format` never rewrites
  `DESIGN.md`'s code samples or vendored skills; the hook only sees `.py`/`.pyi`,
  so this keeps hook and CI in agreement.
- `cli.py` is a placeholder that exits non-zero pointing at `w2h`, so the
  `dndsim` entry point gives a message rather than an ImportError.

### The review workflow's two reviews

`claude-code-review.yml` ran one configuration — the general-purpose
`code-review` plugin at `high` effort on Opus — against every pull request, on
every event, with no concurrency group. Measured against what this repo actually
opens, that was wrong in three ways at once, and #19 showed all three: 27
pushes, every file in the diff markdown, and a full code review of prose on each
one.

- **Two reviews, chosen by the diff.** A `.md`-only diff goes to a short prompt
  on Sonnet that checks four things — self-contradiction, contradiction against
  CLAUDE.md/DESIGN.md/ROADMAP.md, arithmetic and counts, and obligations with no
  owning task. Everything else goes to the code review. The split is not a
  cost dodge: the #19 findings were almost all of the first kind, and a reviewer
  hunting for crit-split bugs in a document cannot find them.
- **The checklist is this repo's, not a plugin's.** `.claude/skills/pr-review/SKILL.md`
  is 93 lines and already names the failures that matter here; the plugin's
  prompt is much larger and knows none of them. What the plugin was worth
  keeping for — a verification pass and a fixed finding shape — is asked for in
  the prompt instead: pass 1 collects candidates, pass 2 re-reads the cited
  lines in the file and drops anything whose concrete failure cannot be
  constructed, and each survivor is reported as CONFIRMED or PLAUSIBLE against a
  named rule, two sentences, at most eight. The output shape is load-bearing in
  both directions — a review nobody reads is a review that does not run.
- **Do not review the same PR twice.** A concurrency group keyed on the PR
  number with `cancel-in-progress`, matching `ci.yml`, so a burst of pushes does
  not run a burst of reviews to completion on diffs already superseded. Drafts
  are skipped; `ready_for_review` stays in the trigger list, so a draft is
  reviewed once, when it stops being one.

`--disallowedTools Agent,Task` survives unchanged and for the original reason:
in headless CI there is no next turn in which a subagent's completion
notification can be delivered, so a fan-out ends the run with the findings
uncollected. The `high` effort level does not survive, because it was the
plugin's knob and there is no plugin.

Two silent-failure guards, because a review that did not run and a review that
found nothing look identical from the outside — the reason the post step exists
at all. The classifier runs under `set -e` and fetches the base ref explicitly:
a `git diff` that errors yields no filenames, an empty list is indistinguishable
from "markdown only", and the failure would route a code PR to the prose
reviewer with nothing to show for it. And the "did not run" comment now names
both causes, the workflow-file mismatch and a failed classification, rather than
asserting the first.


## w0b — rules vocabulary

- `rules/` is the bottom of the stack and imports nothing else from `dndsim`.
  Nothing is re-exported from `rules/__init__.py`: one name, one import path.
- Enums are plain `Enum`, **never `StrEnum`**, and that is load-bearing: under
  mypy strict `damage_type == "fire"` is then a `comparison-overlap` error
  rather than a comparison that works until someone misspells it. Members still
  carry lowercase string values for JSON, the log and parsing at the `content/`
  boundary (`DamageType("holy")` raises).
- `rules.enums.Condition` is the **name** of a condition; the behaviour is a
  `HookSource` subclass in `conditions/` (w1h). Import the enum qualified when
  both are in scope.
- `Outcome` is the attack-roll outcome only. 2024 calls attack rolls, checks and
  saves all "D20 Tests", but only an attack roll crits on a natural 20, so saves
  get their own result type in `engine/saves.py` (w1g).
- The spell-slot tables in `rules/constants.py` overlap what w2a imports from
  `classTableGroups.rowsSpellProgression`. Deliberate: w2a **checks the import
  against them** rather than picking a winner, so a dataset change is caught at
  the ACL. Per-class scaling (Rage Damage, Sneak Attack dice) is never in
  `constants.py` — that is `table_value()`.

### What the docstrings argued, recorded here by `w1l`

- **`Condition` encodes two 2024 changes by what it lacks.** Surprised is no
  longer a condition (it is Disadvantage on Initiative), and Bloodied and
  Concentration are statuses, not conditions; none of the three is a member.
- **`WeaponProperty` has no Special member**: 2014's Special property is gone in
  2024.
- **`MAX_CHARACTER_LEVEL` is 20, not v1's 12.** Simulating levels 1–12 is a
  build-schema limit (`w2f`), not a fact about the rules, so the tables run to
  20.

## w0c — RNG and dice

> **Superseded in part by [w0i](#w0i--the-pessimistic-performance-spike-m0).**
> The production generator is now `MersenneRng` — MT19937 words from
> `random.Random.getrandbits(32)`, with this section's rejection-sampling
> face mapping kept on top of them — and there is no `stream` parameter.
> The argument below for owning the exact output is the thing M0 traded
> away, knowingly, for 10% of the simulation budget; read it as the case
> for putting PCG32 back if an archived seed ever matters more than the
> throughput. Everything here about `mix()`, injection, and the dice
> tokeniser still stands.

- `core/rng.py` is the only module allowed to be random, and it does not import
  `random` either: `PcgRng` wrote out PCG32 (XSH RR 64/32), because the
  requirement is not "random numbers" but *this* sequence of die faces,
  reproducible from an archived `SimResult` years from now.
  `random.Random`'s bit-stream-to-die-face mapping is a CPython implementation
  detail; `numpy.random.Generator` costs microseconds per call. A contract test
  pins the stream against O'Neill's published `pcg32-demo` output — the one
  oracle the project cannot fudge, and any change to the permutation
  invalidates every archived run.
- The `Rng` protocol's primitive is **one die face**, not a raw word, because a
  die face is what a golden scripts. `ScriptedRng` consumes faces left to right,
  raises `ScriptedRngExhausted` on over-draw and `ValueError` on a face the die
  cannot show; `exhausted` lets a golden pin how many dice were rolled.
- `mix(master_seed, iteration_index)` is two SplitMix64 rounds, not addition:
  `PcgRng` seeding advanced its LCG twice, so `master_seed + i` would hand
  neighbouring iterations neighbouring streams. Changing it changes what every
  archived seed means.
- `Dice.parse` **refuses a flat modifier** (`"1d8+3"` raises, naming where the
  `+3` belongs): accepting it would let a string bypass the `DamagePart`
  dice/flat split. `Dice.doubled()` is the crit primitive — it doubles the pool,
  so a crit re-rolls dice instead of multiplying a total.
- Measured on the CI-class runner: ~1.8M `roll(20)`/sec/core (~560 ns). Inlining
  measures the same, so the cost is Python call overhead, not the arithmetic.

### What the docstrings argued, recorded here by `w1l`

- **An unseeded run gets its master seed from the caller** (the CLI), where "no
  seed given" is a policy decision rather than a hidden source of entropy.
  `core/rng.py` imports `random.Random` — the class, instantiated per generator
  — and never the module's functions, so there is no ambient generator and no
  global seed.

## w0d — timeline

- There is no clock *object*. `TimeKey(round, initiative, phase)` is a position
  and the turn loop (`engine/turn.py`, w1f) owns the current one; a second
  mutable clock would be a second source of truth for the one fact the
  simulation is ordered by. Rounds are 1-based and validated.
- **`InitiativeKey` is a place in the order, never a seat index**, because the
  population changes mid-encounter: a summon arrives, a corpse leaves, and a
  dense index renumbers everyone below the change, so every already-resolved
  `Expiry` starts naming a different creature's turn. The key is derived from
  the roll and the roller, never from the roster — an arrival or a death moves
  nobody, and a key stays a valid position after its creature is gone (the sweep
  compares, it never looks the anchor up).
- The **comparison is the tie-break rule**: higher initiative, then higher
  Dexterity *score* (not modifier — a score below 1 raises, which is where
  passing the modifier shows up), then PC over monster, then lowest `tiebreak`,
  then shortest `after` chain. Only the first is a quotation; XPHB leaves ties
  to the table, so the rest are a stated house rule chosen for being total,
  deterministic and seed-reproducible. `tiebreak` is **one uniform draw from
  `[0, 1)` per creature**, not a position in a shuffle of the entrants: a
  shuffle has no honest place for a latecomer, and redrawing it to seat one
  fairly moves everybody already in the order. w2c draws it as
  `(rng.roll(2**32) - 1) / 2**32` — `Rng`'s primitive stays a scriptable die
  face, `2**32` is the widest die `PcgRng.roll` can serve (one 32-bit word per
  face), and the quotient is dyadic so it is exact in binary. A draw outside
  `[0, 1)` raises, which is also what rejects NaN — one NaN and the order stops
  being an order.
- **`after: tuple[int, ...]` is the summon chain**, empty for anything that
  rolled for itself, appended to by `key.sharing_turn()`. It is a separate field
  because no single number can express XPHB's "shares your Initiative count …
  takes its turn immediately after yours" — no draw is guaranteed to fall
  between two others, but a chain step always does, and it nests, so a summon's
  own summon lands behind it.
- `TurnPhase` values are **ordinals, not lowercase strings** — a phase is a
  position on a clock and `end` sorts before `start` alphabetically. Still a
  plain `Enum`, so `phase == 0` stays a mypy error; the log serialises
  `phase.name.lower()`.
- `Duration` (relative, declarative) and `Expiry` (absolute: a `TimeKey` plus
  `through_phase`) are two types so a resolved deadline cannot be carried
  without the flag that decides it. That flag is read through two named sweeps
  rather than a boolean argument: `expires_before(now)` at a boundary before its
  hooks, `expires_after(now)` once they are done. Rage survives the first and
  not the second; Reckless Attack fails both.
- **"Your next turn" is the next turn, not the next boundary of that phase.**
  Said on your own turn it means a round from now, so `resolve()` counts the
  anchor's turn in the current round only when it has not begun
  (`anchor > now.initiative`). The same comparison handles a mid-encounter
  arrival with no special case: seated after the current actor it acts this
  round, seated before it has already missed its turn.
- `Duration` anchors on an `InitiativeKey`, not a creature, because `core/` sits
  below `creatures/`. `creatures/effects.py` (w1c) passes `creature.initiative`
  — `DESIGN.md`'s `until_end_of_next_turn_of(c)` one layer up. Anchoring on the
  key is also what lets a deadline outlive the creature it named.
- Two deliberate omissions: no multi-round anchor (every W1 duration ends one
  turn ahead; a "1 minute" spell wants an additive `turns` field in W4), and no
  "permanent" duration — Prone lasts until you stand up, which is no deadline,
  so such an effect carries no `Duration`.
- `ResetManager` is **one ledger per creature**, which is why `Reset.TURN` vs
  `Reset.OWN_TURN` needs no creature identity inside it: the turn loop calls
  `turn_started(own=...)` and already knows whose turn it is. A global ledger
  keyed by owner would put an encounter-sized lookup on the path every handler
  of every attack walks. Each boundary clears exactly the scopes it names — a
  rest does not touch turn budgets, since the next turn boundary hands those
  back before anything in initiative can ask.
- Recharge on a die roll (`{@recharge}`) is not a `Reset` member: it is a roll,
  not a boundary, and belongs to `Recovery` with the resources (w1b).

## w0e — hook registry

- `hooks/registry.py` is the machinery (`Scope`, `Phase`, `HookGroup`,
  `HookSpec`, `HookRegistry`, `define_hook`, `hook_named`); `hooks/definitions.py`
  is the 39 `define_hook()` calls. No hook is defined anywhere else.
- **Importing anything under `dndsim.hooks` defines the hooks** —
  `hooks/__init__.py` imports `definitions` for its side effect, the one import
  of its kind in the project. Without it, `hook_named()` would raise or not
  depending on which module a test imported first.
- DESIGN.md's "an unregistered hook returns an empty handler list rather than
  raising" is about *dispatch* (a hook with no handlers costs one dict lookup)
  and does not contradict "unknown hook name raises". Names are validated once,
  at import, by `hook_named()`, with `difflib` suggestions; the hot path never
  sees a string.
- `HookSpec` is `eq=False`: specs are registry-owned singletons, so identity
  *is* equality — a dispatch table keyed by spec hash is one pointer rather than
  five fields, and a hand-built look-alike is not the hook. Engine code
  dispatches on the constant (`ON_HIT`), authors write the string
  (`@hook("on_hit")`); a typo is an `AttributeError` on one side and an
  `UnknownHookError` on the other, both at import.
- A spec names its context type as a **string** (`"HitContext"`): `contexts.py`
  sits above the registry, so w0f owns the classes and a contract test there
  asserts the two sides agree.
- `Phase` values are ordinals, for the same reason `TurnPhase`'s are. Which
  bands need a `# PHASE:` comment lives on `Phase.requires_justification`, next
  to the bands, not as a literal in the `w3b` linter.
- **`on_heal` is `TARGET`** — the owner is the creature whose hit points go up,
  matching `on_temp_hp_gained`. The healer's side (Disciple of Life) has nowhere
  to attach: a known gap for the `w3e` paper pass to confirm and `w3f` to add,
  not something to invent on speculation. Four hook *pairs* describe one moment
  from both ends (declare/incoming attack, hit/hit_taken,
  forced_save/saving_throw, damage_roll/incoming_damage); `on_leave_reach` is
  the project's one `OBSERVER`.
- The registry is **not sealed here**: content authors may not add to it, but
  the CI freeze guard is `w3f`'s, after the paper pass has added what it proves
  missing.

### What the docstrings argued, recorded here by `w1l`

- **`UnknownHookError` is a `LookupError`**, because it is a failed lookup.
  `KeyError` was rejected: its `str()` re-quotes the message, which buries the
  "did you mean" suggestion the caller needs.
- **`HookRegistry` is a class although there is one registry**, so that its
  validation can be tested against a throwaway instance rather than by adding
  a fortieth hook to the real one. `define()` raises plain `ValueError`s because
  only an edit to `definitions.py` can reach them.
- **All four Effects-group hooks are `SELF`**: the owner is the creature the
  effect is on, and an effect ending has no actor to be the other side of.

## w0f — contexts

- **31 context types for 39 hooks**: eight are named by two hooks each and 23 by
  one. Six of the eight are the two boundaries of one thing (encounter, round,
  turn, rest, condition, effect); the other two are a moment seen from both
  ends, where the *scope* tells the ends apart — `on_hit`/`on_hit_taken` and
  `on_declare_attack`/`on_incoming_attack_declared`. **The other two
  `SELF`/`TARGET` pairs deliberately do not share**: a save's DC and its roll
  are different objects, and `on_damage_roll`/`on_incoming_damage` is the
  sharing this task rejected outright (the victim gets rolled amounts, not the
  attacker's bag). A contract test pins the eight by name and checks the
  registry's `context=` strings against this module in **both** directions.
- A context is a **frozen, slotted, keyword-only dataclass whose `__setattr__`
  is replaced** after the decorator runs: `frozen=True` makes `ctx.damage = x` a
  mypy error and gives the generated `__init__` a path around the lock; the
  replacement makes the runtime message name the mutators. `@_context` is a
  `dataclass_transform`, so mypy still sees a dataclass.
- **The lock is for features, not the engine.** `reopen(**fields)` writes
  through `object.__setattr__` and is the pooling primitive; it names every
  field it is given (a typo is an `AttributeError`, not a field still holding
  the previous attack's value), **demands every field the context does not
  default**, and resets the defaulted ones — so no part of the previous event
  survives by omission.
- **`PhaseError` is two mistakes, one type**: a mutator called out of band
  (`ctx.veto()` from `NORMAL` — only `EARLY` and `FINAL` may halt the chain) and
  a mutator called after `close()`. The second is what makes pooling survivable,
  turning a stashed-and-later-written context into a raise the first time. It
  does *not* catch a context already recycled and re-opened; nothing cheap does.
- **Provenance is stamped, never passed.** `stamp(source, phase)` is the
  dispatcher's ambient context for one handler call, so `ctx.damage.add(...)` is
  attributed with no author effort — there is deliberately no `source=` argument
  to get wrong, and a mutation with nothing stamped is a `PhaseError`.
- **Two contribution types, because two crit rules.** `DamagePart` is the only
  thing that can reach `Dice.doubled()`, through `on_crit()`, and the predicate
  stays on `Outcome.is_critical` rather than being re-spelled by a context.
  `Modifier` is the same dice/flat split for everything whose dice never double
  (a D20 Test bonus, healing, temp HP, a save DC); one class with a
  `doubles: bool` flag would hand the crit rule back to every author who sets
  it. `damage_type=None` means "the type of the attack this rides on" — a rule,
  not a missing value.
- **Five append-only ledgers** carry contributions: `DamageBag`, `ModifierBag`,
  `AdvantageLedger` (two lists, so cancellation and idempotence are derived),
  `MitigationLedger` (three *sets*, so a type cannot be resisted twice) and
  `OverrideLedger` (Legendary Resistance, Relentless Endurance). Each is a
  `_Bag`, which is how `reopen()` knows what to bind and what to clear.
  **The declaration is the ownership**: a bag declared with a `default_factory`
  was created by the context, so `reopen()` clears it in place; a bag declared
  without one is handed in, so it is required, rebound and never cleared. The
  damage bag belongs to the attack — built at `on_hit`, rolled at
  `on_damage_roll` — so `HitContext`, `MissContext` and `DamageRollContext` all
  declare `damage: DamageBag` bare, and a contract test guards it: a
  `default_factory` there would make the pool clear the attack's own damage.
- **Participants are `Protocol`s, not imports**, because `hooks/` sits below
  `creatures/` and w0f ran before the first `Creature` existed. `Combatant`,
  `Weapon`, `Spell`, `Effect` and `Cell` declare only what the design had
  settled (`Duration` anchors on `creature.initiative`; the log needs a name); one
  that grows to mirror `Creature` is a second domain model.
  **w0g2 owes a contract test that its real classes satisfy them.**
- Nothing here rolls or applies anything — `engine/damage.py` (w1e) owns the
  resolution order, `engine/attack.py` (w1f) the arithmetic. So
  `IncomingDamageContext` *records* resistance, reduction and halving rather
  than applying any, and is handed the damage **rolled**, as an amount per type:
  by then `damage_type=None` has resolved, which is what the type-keyed steps of
  the order need.
- `core/errors.py` was created here rather than in `w0g` as ROADMAP's table
  says, because w0f's done-when names `PhaseError`; `w0g` completes it with
  `FeatureContractError`.

### What the docstrings argued, recorded here by `w1l`

- **`kw_only`**: a Parameter Object read at the call site is the point, and it
  frees base and subclass fields from declaration order. **`eq=False`**: a
  context is the event, not a value — two attacks that look alike are not the
  same attack — and identity hashing keeps a pooled object cheap.
- **Everything that changes between handler calls lives on one `_Frame`** — the
  stamp, the band, the open flag, the veto — so the context itself stays frozen
  (`ctx.attacker = x` is a static error as well as a runtime one) and the
  per-handler stamp is a plain slot store on the hot path, which
  `object.__setattr__` on a frozen dataclass would not be.
- **`DndSimError` exists** so a caller embedding the simulator can tell "the
  simulator refused" from "Python broke".

## w0g — dispatch

- Three modules for three moments: `decorator.py` marks a function at
  **decoration**, `source.py` validates and collects at **class creation**,
  `dispatch.py` binds and runs at the **run**. `@hook` returns the function
  unchanged with a `Handler` attached, so a handler is still an ordinary method
  — callable in a test, its own name in a traceback.
- **`HookResult` has two members and no third.** `APPLIED` spends the handler's
  `once_per` budget and pays its `costs`; `SKIPPED` spends nothing. Halting the
  chain is `ctx.veto()`, a mutation like any other, and a vetoing handler has
  `APPLIED`. `R` is a `TypeAlias` for the same enum, not a second one.
- `FeatureDeclarationError` **is an `ImportError`** as well as a
  `FeatureContractError`, so a batch loader can distinguish "would not import"
  from "misbehaved". `EngineAssemblyError` is deliberately *not* a feature's
  fault: `costs=` with no economy wired in, or `gate=` with no APL, raises
  rather than running free — a reaction nobody paid for and a gate treated as
  open are silent corruptions of every number.
- Four declaration mistakes are refused at class creation; the fourth is the one
  invisible in a diff: **an override that dropped its `@hook`**, where the
  base's body keeps running while the author reads their own.
- The context annotation is checked by **identity** against the class the
  registry names, so a same-named look-alike is not the context.
  `get_type_hints` resolves it — which is why a context imported only under
  `TYPE_CHECKING` is an error with a sentence saying so.
- **`ActionCost` lives in `rules/enums.py`**, not `creatures/economy.py`: the
  decorator declares it and w1a's economy consumes it, and neither package may
  import the other. There is no `FREE` member — that is `costs=None`.
- `Economy` and `Gate` are **`Protocol`s** (the device w0f used for
  `Combatant`), so `hooks/` still sits below `creatures/` and `apl/`. w1a and
  w2f owe the contract tests that their real classes satisfy them.
- **The dispatch table's tuples are the snapshot.** `attach()`/`detach()` build
  new tuples rather than mutating those in the table, so an effect applied
  mid-hook cannot join the event that applied it — the authoring skill's
  snapshot rule made structural rather than documented.
- A `BoundHandler` is created once per (source, declaration) and **survives
  table rebuilds**, because it is the key its `once_per` budget is filed under
  in the creature's `ResetManager`. Rebuilding on every attach would hand a
  spent Sneak Attack back whenever a condition landed mid-turn.
- `ctx.stamp(source, phase)` before each call is the whole of provenance, and
  `dispatch()` restores the stamp it found (`Context.stamped()`), so a dispatch
  leaves attribution as it was. `stamp(None, ...)` releases it, which makes an
  engine mutation *outside* a handler a `PhaseError` instead of a wrong line in
  the damage breakdown.
- Only `EARLY` and `FINAL` can veto, so the loop checks `ctx.vetoed` only after
  those — the 95% `NORMAL` case pays one slot load, not a property call. A veto
  also short-circuits the *second* half of a paired dispatch: an `on_hit` veto
  means `on_hit_taken` does not run.
- The dispatcher deliberately does **not** drain `ctx.pending_effects` or
  `close()` the context: one context is dispatched to two creatures, so the
  engine that owns the event does both, once, afterwards.
- **Measured on the CI-class runner**: ~0.3 µs per handler for the dispatch loop
  itself, but ~1.25 µs for one `ctx.damage.add(...)`, ~0.9 µs of which is
  `DamagePart`'s frozen-dataclass `__init__`. Fifteen on-hit handlers each
  adding one part is ~19 µs against a ~20 µs budget for a whole attack
  resolution. `w0i` owns that number; the cheap lever if it misses is the shape
  of `DamagePart` and `Modifier` (a `NamedTuple` constructs in ~0.38 µs, a plain
  slotted class in ~0.28 µs), not the dispatch design.

### What the docstrings argued, recorded here by `w1l`

- **`dispatch()` does not check that its context is the one the hook names.**
  The pairing is fixed at import by `source.py` and pinned by `w0f`'s contract
  test; re-checking it per dispatch costs something on every attack for a
  mistake that cannot survive one test run.

## w0g2 — spike substrate

- **Everything here is a first cut that a W1 task completes, never rewrites**
  (ROADMAP w0g2): `w1a` finishes `creature.py`/`stats.py`, `w1d` finishes
  `geometry.py`, `w1e` finishes `damage.py`. The reason they exist this early is
  that `w0i` is only a hostile spike if the machinery it stresses is real — a
  stub that answers `5` to every distance query measures nothing.
- **Distance is Chebyshev × 5 ft.** Each square is 5 feet and a diagonal step
  costs the same as an orthogonal one (XPHB battle-grid rules), so (0,0)→(3,3)
  is 15 ft. The DMG's 5-10-5 alternating diagonal is a *variant* and is
  deliberately not implemented: it is not the default, it makes distance depend
  on the order the steps were taken in, and every reported number would then be
  conditional on a rule nobody asked for.
- **A creature occupies one cell.** `Size` is on the stat block and geometry
  reads it nowhere; a Large creature's 2×2 square, and what that does to reach
  and to templates, is `w1d`'s. Exact for the Small/Medium combatants of W1–W3.
- **`route()` is greedy first, with a bounded search behind it.** DESIGN.md §
  APL settles the shape: movement is a decision, not a pathfinding problem — the
  APL picks a destination and the engine walks the shortest legal route, with no
  tactical pathing now or planned. The greedy diagonal walk *is* that shortest
  route in the open field and costs one step per cell, which is what keeps a
  per-turn movement query off `w0i`'s budget. It is not enough on its own: the
  first cut stopped the moment every axis-closing step was blocked, so a
  three-square wall of creatures left a melee build standing still — the "melee
  build that stands still and reports zero damage" DESIGN.md warns about,
  arriving through the pathing rather than through the APL. So a breadth-first
  search over the eight neighbours runs behind it, in a fixed neighbour order
  (ties between equal-length routes must not depend on a set's iteration
  history, or a seeded run stops reproducing), capped at `SEARCH_CELLS` because
  the grid is unbounded and an enclosed destination would otherwise search
  forever.
- **The greedy walk is trusted only when its length proves it optimal**, not
  whenever it arrives. A creature standing diagonally adjacent sends the
  sidesteps the long way round: the walk reaches the destination in three
  squares where two exist, which is a square of movement lost and a melee
  creature left one short of the target it chose. `len(walked)` against the
  Chebyshev distance is the test — one subtraction on a path that was already
  returning — and it took a randomised sweep against an independent BFS to find,
  because every hand-built fixture either arrives optimally or does not arrive.
- **A route that does not arrive is the partial walk, not an exception**, which
  is what a creature with movement and nowhere to go does. Callers tell the two
  apart with `walked and walked[-1] == destination`; a destination that is
  itself blocked *does* raise, because that is a selector that has already gone
  wrong rather than a fight condition. The route is the whole path regardless of
  movement budget: the turn loop truncates with `steps_within()`, because a
  creature that runs out of movement halfway has still crossed the cells it
  crossed and provoked on the way out of everyone's reach.
- **The greedy walk needs no visited set.** Every candidate step closes the axis
  it was chosen for, so that axis' distance strictly decreases until it is zero
  and the other takes over: the walk cannot cycle by construction, and a `seen`
  set would be untested defensive code standing where the real guarantee is.
- **`Creature.position` has no default.** Two creatures defaulted onto the same
  cell is a fight where every distance is zero and everything is in everyone's
  reach — the silence this design refuses, in exchange for one keyword at every
  construction site.
- **`StatBlock` is frozen and shared; `Creature` holds what changes.** One stat
  block serves the four identical dummies in an encounter. The three numbers
  nobody can guess — all six ability scores, hit points, AC — are **required**,
  because a defaulted 10 is a number nobody chose that silently changes every
  save, attack and hit made with it. Speed, size and level keep defaults, since
  30 feet, Medium and level 1 are what the rules themselves default to. The
  first cut refused a *partial* ability map while defaulting a missing one to
  six 10s, which is the same mistake wearing the argument against itself.
- **`level` is validated at construction**, not at the first read of
  `proficiency_bonus`: failing at the earliest moment the information exists is
  the whole of DESIGN.md's fourth load-bearing idea, and a level-21 stat block
  that raises a fight later blames whoever read it.
- **`armour_class` is a literal, and only until `w1a`.** DESIGN.md is emphatic
  that AC is derived from equipment and never typed into a build; there is
  nothing to derive it from before `Armour` exists, so the number sits on the
  stat block with the paragraph explaining why.
- **`Weapon.damage` is a `Dice` pool with no flat part.** The wielder's ability
  modifier reaches the damage as a `flat` `DamagePart` added by the attack, so
  the crit rule stays structural: whatever is on the weapon doubles, and the
  modifier cannot be smuggled into it. `GREATSWORD` is the one hardcoded weapon
  the project will ever have — `w2a` imports the 38 XPHB weapons as instances of
  the same class and the constant goes with the spike that needed it.
- **No `Creature.take_damage()`.** Hit points and temp HP are plain attributes
  and the arithmetic lives in `engine/damage.py`, because a method here would be
  a second place the resolution order could be written — and the second place is
  the one that gets it wrong.
- **`attach()` refuses a source owned by another creature.** `self.owner` inside
  every one of its handlers would be the wrong creature, which is the single
  thing hook `Scope` exists to make impossible.
- **`damage.py` is three functions for three different things**: `roll_damage()`
  turns a `DamageBag` into an amount per type (this is where a crit doubles dice
  and never flat, and where `damage_type=None` resolves to the attack's type —
  the last moment before the amounts stop being the attacker's); `mitigate()` is
  the *pure* resolution order over a `MitigationLedger`, so `w1e` can pin every
  step with a table of cases and no fight around it; `resolve_damage()` is the
  event — dispatch, apply, hit points.
- **Vulnerability before resistance is load-bearing arithmetic** — *overturned
  by `w1e`; see § w1e.* `(2n) // 2 == n` for every integer, so that order *is*
  the rule that resistance and vulnerability to the same type cancel, with no
  special case written. The reverse order loses a point on every odd number. The
  2024 text orders resistance first regardless, and losing that point is the
  book's answer.
- **Two steps are rulings, not quotations** — argued inline in `damage.py` at
  the time, and since PR #25 argued in § w1e with `damage.py` pointing at it:
  flat reductions apply *after* resistance,
  and halvings apply *after* the flat reductions. Each changes every number a
  build with Heavy Armor Master or Uncanny Dodge reports. `w1e` owned confirming
  them against the 2024 text and **found the first one wrong** — adjustments
  precede Resistance — while the second survives inside the adjustment step it
  moved into. § w1e has both arguments in their final form.
- **Reductions and halvings act on the total, not per type** — Heavy Armor
  Master reduces *the damage*, not the fire part of it — which is why
  `Mitigated` carries both the per-type picture after the typed steps and the
  one total after the untyped ones. *`w1e` inverted this*: with the untyped
  steps first, the total has to be spread back over the types in proportion, and
  the two numbers agree from there on.
- **A veto on `on_incoming_damage` means zero damage**, checked in
  `resolve_damage()` rather than modelled as one more kind of mitigation: the
  Chain of Responsibility element is about whether the event happens at all.
- **`on_damage_taken` does not fire when nothing landed.** A creature immune to
  the only type in the hit, or whose reductions ate all of it, has taken no
  damage, and the rules read "when you take damage" — the concentration check is
  the case that decides it. Firing with a zero would put that condition inside
  every handler ever written for the hook. A hit absorbed entirely by temporary
  hit points *does* fire: those hit points are gone.
- **Halvings stack where resistances do not.** `_resist` is a set because the
  rules say multiple instances of resistance count once; `_halvings` is a list
  because no rule says the same of two features that halve, and Uncanny Dodge on
  top of Rage quartering a hit is the known-correct answer. *`w1e` confirmed it*:
  the "counts once" clause names Resistance, and a halving is not Resistance.
- **A reduction's dice come out of the caller's `rng`**, after the attacker's
  damage dice in the same stream, so `w1f` must hand `mitigate()` the generator
  the attack is using. A second generator reproduces a different fight from the
  same archived seed, which is a reproducibility break with no error anywhere.
- **A queued effect raises rather than being dropped.** Nothing drains
  `ctx.pending_effects` until `EffectStore` (`w1c`), and a handler whose whole
  point was silently discarded is the failure mode this design refuses
  everywhere else. `_refuse_pending()` is `w1c`'s to delete, not the next
  agent's to trip over.
- **`on_reduced_to_zero` does not fire here.** Dying — death saves, stabilising,
  and the hook Relentless Endurance attaches to — is `w1j`'s whole task. Until
  then hit points clamp at 0 and `Creature.is_alive` is hit points only.
- **`w0f`'s protocol debt is paid here**: the contract tests assert that the real
  `Creature`, `Weapon` and `Cell` satisfy the narrow `Combatant` / `Weapon` /
  `Cell` protocols `hooks/contexts.py` declares, which is what lets those
  protocols stay narrow instead of growing into a second domain model. `w1a`
  still owes the same for `Economy`, `w2f` for `Gate`.

### What the docstrings argued, recorded here by `w1l`

- **`route()`'s search is breadth-first, not A\***: the frontier is capped at a
  few hundred cells, where the heuristic buys less than the branch it costs, and
  a queue with no priority is one fewer thing to get subtly wrong on a path a
  fight walks every round.
- **`route()`'s blocked cells may be a plain `set`**: the turn loop builds one per
  move out of who is standing where, and copying it into a `frozenset` per query
  would be an allocation on the measured path.

## w0h — walking skeleton

> **Superseded in part by `w1f`.** The attack sequence described below moved to
> `engine/attack.py` and `attack_once()` is gone (debt #6); what is left in
> `tools/skeleton.py` is the two builds and the fight loop. The bullets are
> w0h's reasoning as it stood, and three of them `w1f` re-decided: the crit
> rule now asks the AC before calling a 19 a critical hit, unconditional
> proficiency is debt #25 rather than a settled choice, and the reach check
> reads the weapon the attack is made with. Read § w1f for what the pipeline
> does now.

- **The attack sequence in `tools/skeleton.py` is not `engine/attack.py`.**
  `w1f` has not landed, so the seven attack hooks and the three damage ones are
  sequenced by hand here, over the real `Dispatcher` and the real
  `engine/damage.py` — nothing about *how* an attack is put together is meant
  to survive `w1f`; what is meant to survive is the proof that the substrate
  those hooks run on works end to end before 25 more W0/W1 tasks are built on
  top of it unexercised (ROADMAP § The critical path).
- **Featureless means zero `HookSource`s, not a stub creature.** Both
  combatants are the real `Creature`/`StatBlock` from `w0g2`; what is absent is
  anything attached to their dispatchers. That is what makes this a proof that
  the Null Object ending of the hook system is cheap (one failed dict lookup
  per hook, ten times an attack) rather than a proof about a mock.
- **A natural 1 misses and a natural 20 crits, unconditionally, ahead of any
  AC comparison** — the ordinary 5e rule, checked in that order in
  `attack_once()` because a natural 1 that also fails the AC check is not
  evidence the unconditional rule fired at all. The regression test for it
  therefore builds a defender whose AC the total would otherwise beat.
- **Proficiency is unconditional.** There is no proficiency system yet (that is
  `w1a`'s), and a level-5 fighter swinging its own weapon is proficient with it
  in every build this project will ever run, so `attack_once()` simply adds
  `attacker.proficiency_bonus` rather than inventing a flag with one caller.
- **`in_reach()` gates the attack.** Not asked for by ROADMAP w0h in so many
  words, but the two creatures the skeleton builds have real positions on a
  real grid, and an attack that never checked them would be exercising
  `core/geometry.py`'s type signatures without exercising the query itself —
  the same gap w0g2's own docstring warns a stub distance query leaves. `w1f`
  owns the real version of this check, alongside reach from a Reach weapon and
  from a Large creature's square.
- **`run_fight()` takes a plain callback, not a formatter.** `main()` passes one
  that prints; the contract tests pass none. Splitting it this way keeps the
  reproducibility test (`PcgRng(seed)` twice, same round count) free of
  `capsys`, and keeps the print format free to change without touching the one
  thing about a fight this file promises: that it is deterministic.
- **The safety cap is 50 rounds, not a loop-detection heuristic.** A level-5
  fighter vs. an AC-15, 40-hp dummy dies in single digits on realistic dice; 50
  rounds without a kill is this script's own bug, not an unlucky streak worth
  simulating further. `w1f`'s real turn loop owns actual termination
  conditions (both sides down, a round limit from the build config).

Sources: DESIGN.md § The closed hook list, § Structural prevention over
testing; XPHB (2024) "Making an Attack" (natural 1/20); ROADMAP § The critical
path, w0g2, w0h, w1f.

## w0i — the pessimistic performance spike (M0)

**Verdict: the gate fired, the architecture was revised, and the budget was
revised with it — 50,000 attack resolutions/sec/core became 30,000, against a
measured ~31,500.** The spike first ran at 17,231. Measured on Windows 11,
CPython 3.11.14, one core, median of five runs of a thousand encounters after a
warmup. `w0i` is a stop-and-revise gate, so both revisions happened here rather
than in W4; what follows is what the spike is, what each change bought, and why
the last 18k was not worth what it would have cost.

### What the spike is, and why each part of it

`tools/spike.py` assembles it, `tools/spike_features.py` holds the twenty-five
sources, `tools/spike_apl.py` the hand-compiled policy, and
`tests/bench/test_spike.py` is the contract that keeps all three hostile. The
shape is DESIGN.md § The spike must be pessimistic, item by item: 25 attached
`HookSource`s with 15 on `on_hit`; an 8-condition APL with a `target:` selector
and a `move:` clause, three of whose entries are `gate:` answers given *inside*
an attack; real routes over a real blocked-cell set; both directions of the
attack, because four of the twenty-five listen on the victim's side.

- **The rate is total encounter time divided by attack resolutions**, so the
  turn loop, the APL, the movement and the per-encounter reset are all loaded
  onto the attack count. That is the same denominator DESIGN.md § Performance
  uses to reach 4M resolutions per build comparison, and the alternative —
  timing `attack_once()` alone — is the inner-loop benchmark that section warns
  about.
- **The one stand-in is the APL compiler**, as ROADMAP w0i allows. Its
  conditions are the callables `w2e` will emit over the same namespace-proxy
  shape, including the weak references DESIGN.md § Condition language
  specifies; `w2e` re-runs this benchmark and must come in at or under the
  number above.
- **`spawn` worker startup is measured apart: 0.167 s per worker** — a fresh
  interpreter, a fresh import of the package, and every character rebuilt.
  `compare --sweep dummy.ac=10:22` pays that thirteen times, which is two
  seconds of pure startup before any dice are rolled.
- **The startup measurement launches a subprocess rather than a
  `multiprocessing.Pool`.** `spawn` brings a worker up by re-importing the
  parent's `__main__`, so a pool started from a script without a main guard —
  or from inside a test runner, whose `__main__` is the runner — re-runs that
  module in every worker. This was not theoretical: the first version of the
  measurement fork-bombed the machine it was measuring.
- **What the spike understates**: effects (`w1c`) and resources (`w1b`) do not
  exist, so there is no per-boundary expiry sweep and no pool arithmetic. The
  per-round cost will grow in W1. What it does *not* understate is the
  per-attack cost, which is where the budget is spent.
- **What it would flatter, and how that is guarded**: a benchmark whose riders
  all skip never builds a damage bag or spends a budget.
  `test_the_riders_that_should_land_do_land` names eleven handlers that must
  have applied at least once, read off the no-op detector's own counter, and
  the fixture set was corrected twice when that test found conditions which
  could never hold in this build.
- **The turn loop charges the XPHB action costs, and review found out why that
  matters.** `_TURN_COSTS` gives Rage, Second Wind and Healing Word a Bonus
  Action and the Attack action an Action, and `_offered()` filters the list to
  what the economy can still pay. An earlier version charged all four an Action,
  which interacted badly with the entry order: `second_wind` sits above `attack`
  and its `if:` is `self.hp_pct < 0.5`, so the first turn the hero dropped under
  half it spent its Action on Second Wind and — since nothing in the spike heals
  — stayed under half and never swung again for the rest of that encounter.
  Measured before the fix: it fired in about 0.4% of encounters, almost always
  on the last round, costing **21 of 30,000 resolutions a run (0.07%)**. That is
  an order of magnitude under the 0.2% run-to-run spread, so the *rate* was
  never in question. What was in question is the encounter's shape: 1 seed in 60
  resolved something other than 30 attacks, and 3 in 60 made `measure(5, 1)`
  return something other than 150 — so the exact-integer assertions in
  `tests/bench/test_spike.py` were facts about seed 0 rather than about the
  configuration. They are now invariants: 12,000 encounters across 60 seeds all
  resolve exactly 30, `test_every_encounter_resolves_the_same_number_of_attacks`
  holds the line on six of them, and
  `test_a_wounded_hero_takes_second_wind_and_still_swings` checks the mechanism
  directly without waiting on the dice. **A benchmark whose unit of work depends
  on the dice cannot have exact-integer assertions written against it**, which
  is the general lesson and the reason this was worth fixing for 0.07%.

### The revision, and what each step bought

Measured on the pooled configuration, in order. Nothing here changed a result:
`test_pooling_contexts_does_not_change_the_fight` passes, as do the 400 tests
that existed before. The only two that changed are the `reopen()` contract
tests, which now get their guarantee from the interpreter and assert
`TypeError` where they asserted `AttributeError`.

| Step | rate | why it was there |
|---|---|---|
| as designed | 17,231 | pooling was *slower* than allocating |
| `Dice.parse` memoised | ~18,200 | a feature writes `dice="6d6"` in a handler body, so the regex ran 2.5x per resolution |
| dispatch loop: `bound.guarded`, stamp written into the frame | ~19,200 | three tests and a method call per handler, twenty-odd handlers per attack |
| `DamageBag.add` / `ModifierBag.add` fast paths | ~20,300 | six contributions per hit, each paying a call to resolve provenance |
| `PcgRng.roll` inlined, thresholds memoised | ~20,600 | eight draws per resolution |
| **`reopen()` compiled per context class** | 26,145 | the pooling lever, finally positive |
| the two damage contexts pooled too | 27,977 | a hit allocates two inside `resolve_damage` |
| dispatch tables hoisted, `roll_damage` tightened | 28,918 | nine dispatches per resolution went through a delegating method |
| **`PcgRng` replaced by `MersenneRng`** | ~31,500 | PCG32's 64-bit arithmetic in Python big-ints, eight draws per resolution |

The load-bearing one is the sixth. **`reopen()`'s validation cost more per call
than constructing the context from scratch** — a set difference and a
comprehension over the field list — so DESIGN.md § Levers' "contexts reset and
reused per attack" was a lever that did not work. Compiling `reopen()` per
context class, with the fields as keyword-only parameters, gets the same two
guarantees (every field named, no field invented) from Python's own argument
binding for nothing, and turns a 1.8 µs rebind into a 0.45 µs one. It is the
technique `dataclasses` uses for `__init__`, for the same reason and in the
same place: once, at class creation.

### Why 50k is not reachable by tuning

The remaining time is spread thin: ~15% handler bodies, ~11% the attack
sequence's own glue, ~10% the dispatch loop, ~7% the RNG, ~7% the damage
pipeline, the rest in ones and twos. There is no hotspot left worth 1.7x. A
floor calculation agrees: at the measured cost of a Python call in this
interpreter, one resolution's *irreducible* work — nine dispatches, ~12 handler
calls, six context rebinds, six `DamagePart`s, eight die rolls and the damage
arithmetic — comes to roughly 20 µs, which is the budget exactly. **50k was set
at the theoretical limit of this object model in CPython, with no headroom**,
and the design's own richness — a Parameter Object per hook, automatic
provenance per contribution, a frozen value per damage part — is what fills it.

### The generator, and what it cost to swap

The last row of that table is the one that gave something up. `core/rng.py`
argued PCG32 by name and § w0c pinned it against O'Neill's reference
implementation — "a generator we own the exact output of", checkable against an
oracle outside this repository. `MersenneRng` draws its words from
`random.Random.getrandbits(32)` instead, and keeps the rejection-sampling face
mapping, so what the project still owns is the half of the stream between a
32-bit word and a die face.

What that buys is 10%. What it costs is precise and worth stating plainly: the
reproducibility of an archived seed now rests on CPython's MT seeding routine
(`init_by_array`) rather than on fifteen lines of arithmetic in this repository.
That is a documented, stable interface and not a wild bet — but it is not ours,
and `test_the_stream_is_pinned_to_the_word_source` says so where a future reader
will find it. The `Rng` protocol is what keeps the door open: putting PCG32 back
is a one-class change, and this file's history has it.

`stream` went with it. `random.Random` has no sequence selector, and the
parameter was already vestigial — separation between iterations comes from
`mix()`, which is what DESIGN.md § Performance specifies and what the
reproducibility contract is stated in terms of.

### The lever that was not taken

**The object model.** Collapsing an attack's six contexts into one, or dropping
per-part provenance, would buy real time and would cost the authoring
properties the whole project leans on — one hook, one Parameter Object;
`parts_by_source` in a golden; the no-op detector; the trace. Those are what
make ~630 agent-written features safe to accept, and they are not a trade worth
making for a factor the hardware gives back by running eight iterations at
once.

**What the gap actually costs**: 4M resolutions is 143 s/core rather than 80,
and the simulator is embarrassingly parallel across iterations, so a build
comparison on eight cores is ~18 seconds instead of ~10. That is the whole
practical consequence, and it is why the open question below is a question
about the budget rather than about the architecture.

### The budget, revised once

**30,000**, in `BUDGET_PER_SECOND`, DESIGN.md § Performance, ROADMAP w0i and the
M0 gate. It is the measured ~31,500 with the run-to-run spread taken off, and
the revision is recorded in all four places rather than quietly applied in one.

Lowering it a second time is the edit that must not be made quietly: a budget
that follows the measurement down is not a budget, and DESIGN.md § The benchmark
gate is explicit that performance decays one feature at a time. `w2e` re-runs
this benchmark when the real APL compiler lands and must come in at or under
this number; `w3i` turns it into a CI gate against a same-run baseline, which is
the form that survives a shared runner.

**How much work a timed run must do.** At 200 encounters the same machine
returned 29.4k, 30.6k and 32.1k on three consecutive runs — a 9% spread, wide
enough to hide the regression a gate exists to catch. At 1,000 (about a second
per repeat) the spread is 0.2%. That is why `measure()` defaults to a thousand,
and `w3i` should treat it as a floor rather than a number to trim when the suite
feels slow.

### Findings for later waves

Four things the spike found that are somebody else's to fix, recorded so they
are not rediscovered:

- **`Creature` needed `__weakref__`.** A slotted class cannot be the referent
  of a weak reference, and DESIGN.md § Condition language builds the APL's
  proxies over weak references to live state. One pointer per creature, nothing
  on the hot path.
- **`Dispatcher.bind()` exists now**, because the constructor could not do it:
  the APL's namespace is built over the creature, the creature owns the
  dispatcher, so the gate does not exist when the dispatcher is made. The build
  loader (w2f) calls it once; rebinding raises.
- **The `Combatant` and `Weapon` protocols are too narrow for real features.**
  No `on_hit` handler can read the defender's hit points or ask whether the
  weapon is Heavy — `hooks/contexts.py` gives them `name` and
  `initiative`. The fixtures work around it and say so in place. `w1a`
  and `w1f` own widening them, and the W3 paper pass (`w3e`) should confirm the
  shape before ~400 features are written against it.
- **Derived stats are recomputed per attack.** `proficiency_bonus` walks the
  XPHB table and `modifier()` divides, three times per resolution, because
  `w1a`'s derived-stat cache does not exist yet. Worth ~1.5%, and already on
  `w1a`'s list — but it means the number above is measured against a creature
  doing slightly more work than the finished one will.

### What the review of the revision caught

Three things, all of them consequences of the revision above rather than of the
spike, fixed in the same branch:

- **Compiling `reopen()` per class silently widened the blast radius of
  forgetting `@_context`.** `Context.reopen`'s raising body was written as the
  guard for a class that escaped the decorator, but `Context` is itself
  decorated, so that body was overwritten and never ran: an undecorated
  subclass inherited the base's *compiled* zero-parameter `reopen()`, which
  resets the frame and the pending queue and leaves the subclass's own fields
  holding the previous event's values — a pooled attack on the previous target,
  with no error anywhere. `Context.__init_subclass__` now installs the raising
  body on every subclass before the decorator runs, so the class that skipped
  the decorator is the one left holding it. Class-creation time only; nothing
  on the hot path.
- **`DamagePart`'s docstring claimed `on_crit()` was the only place the
  doubling happens**, which stopped being true when `roll_damage` inlined
  `dice.doubled()` to avoid allocating a doubled part per contribution per
  crit. The flagship structural invariant is not one to document by a sentence
  that is no longer true; both sides now say which caller is which and why.
- **`mitigate()`'s `untouched` fast path skipped the floor-at-zero clamp.** The
  shortcut is the identity for every step the ledger drives, but the clamp is
  not one of those — `DamagePart.flat` is a signed `int` and nothing rejects a
  negative one, so a contribution that subtracts more than the hit deals would
  arrive at `resolve_damage` as negative damage, which is temporary hit points
  granted by a sword. One comparison, no measurable cost (31,452/sec after).

### What the review of the spike caught

Both findings are about the same handler, and together they are the failure M0
exists to catch, caught late: the one fixture that pays for the dispatcher's
two expensive escape valves was never actually running.

- **The victim's side of an attack never bound `trigger`.** `_enemy_turns()`
  called `attack_once()` without one, and `attack_once()` guards every bind
  with `if trigger is not None` — so the hero's only gate on that side, Uncanny
  Dodge's `trigger.damage_estimate >= self.hp * 0.25`, was answered from the
  estimate left over from the hero's own last swing. The two are not close: the
  hero's bag carries Sneak Attack and the smite and offered 63 on the first
  round of a fresh run, while the most an enemy can put on the table is 25. The
  hero's trigger now travels with the enemies' attacks, and
  `test_the_victims_gate_is_asked_about_the_swing_that_is_landing` pins it on
  exactly that size difference rather than on a recorded number.
- **With the hero at 400 hp the gate could not answer yes at all**, so the
  reaction was never spent, `can_spend()` never denied, and `ctx.halve()` was
  never on the measured path — while three docstrings claimed otherwise. The
  tension is structural and worth stating so it is not undone: **a
  fraction-of-current-hp reaction gate and a pool sized so the hero cannot drop
  are the same knob pulled in opposite directions.** Losing a quarter of your
  hit points to one hit and taking six hits an encounter cannot both be true of
  a creature that lives, unless the hits are spread — the gate has to ride the
  tail of the damage distribution, not its mean.

  Of the two, the pool is the benchmark artifact and the gate is quoted
  verbatim from DESIGN.md § APL, so the pool gave way: **70 hp against a 2d6
  maul** rather than 400 against a 1d6 scimitar. The entry then fires about
  0.4 times an encounter, and across 2,060 encounters on six seeds the hero
  never fell below a third of its maximum. Re-measured after the change:
  **31,674/sec**, inside the 0.2% run-to-run spread of the number the revision
  landed on, so the budget is untouched.

Beside those, five pieces of stale prose: the `once_per` count (three handlers,
not two), `UncannyDodge` claiming all three valves when it carries no ledger,
`SneakAttack` documented as reading advantage when it reads the rage flag,
`BrutalStrike` testing for a `MISS` that `on_hit` cannot deliver, and a comment
in `test_rng.py` still describing PCG32's seeding. Fixture prose drifting from
fixture behaviour is how a benchmark ends up trusted for something it does not
measure, which is the whole subject of this section.

Sources: DESIGN.md § Performance, § The spike must be pessimistic, § Levers,
§ The benchmark gate, § Condition language; ROADMAP w0i, w1a, w1f, w2e, w3i.

### What the docstrings argued, recorded here by `w1l`

- **`DamagePart.on_crit()` constructs a new part** rather than calling
  `dataclasses.replace()`: it runs once per contribution on every critical hit,
  and `replace` reads the field list and builds a keyword dict to do what four
  positional arguments do.
- **`tools/spike_features.py`'s mix: most handlers skip and a few apply**, as a
  real martial's `on_hit` list is mostly riders whose conditions are unmet. A
  benchmark where every handler applies measures `DamageBag.add()` and calls it
  dispatch; one where none applies never pays for `once_per` or the economy —
  both wrong in the flattering direction. Each valve-carrying handler must
  *apply* at least once, because a gate that always answers no buys the cheap
  half of the valve and none of what it guards.
- **`tools/spike_apl.py`** resolves `self.slots.highest` to one attribute rather
  than a `slots` sub-proxy, which would construct an object per evaluation; and
  `trigger.*` is bound by the engine rather than scraped off the context, which
  would be an `isinstance` chain on the hottest path.

## The W0 plan review — what M0 changed in the plan

Not a task. W0 is complete and M0 is met, so `ROADMAP.md` and `DESIGN.md` were
re-read against what the wave measured rather than against what it assumed. The
findings are recorded here so the *reasoning* survives even though the documents
now simply state the conclusions.

**The wave's own record was in good shape and this pass changed very little of
it.** Every finding below was already written down somewhere in the code or in
this file — usually in the module docstring of the file that owes the debt. What
none of them had was a home in the plan. That distinction is the whole content
of this review: a paragraph explaining a stand-in ages into a paragraph
describing the code, and the reader who needed to act on it is the one who never
opens that file.

### The one structural change: `w1k`

`w0i` measured ~31,500 against a budget of 30,000 and states in its own record
that what the spike understates is the per-round cost, "because effects (`w1c`)
and resources (`w1b`) do not exist, so there is no per-boundary expiry sweep and
no pool arithmetic". W1 is the wave that builds both. The next task in the plan
that re-measured anything was `w2e`, twenty tasks downstream.

Five percent of headroom, twenty tasks, and a known-missing cost that lands in
the very next wave is not a gap the M0 gate can be said to have covered. So
`w1k` re-runs the spike at the end of W1 with the fixtures' hand-built effect
and resource stand-ins replaced by the real ones. It is the same argument `w0i`
made for the APL compiler, applied to the two stand-ins it named itself: **a
stand-in a later task has to beat is a budget; a stand-in nobody re-measures is
a stub.** That sentence is now sequencing principle 7 in `ROADMAP.md`, because
W0 produced sixteen more stand-ins after writing it.

### The debt register, and why a table rather than more prose

Seventeen deliberate first cuts and deferred obligations came out of W0. Every one
is explained where it lives, and the explanations are good — `_refuse_pending()`
raises with a sentence naming `w1c`, `stats.py`'s docstring says why
`armour_class` is a literal and until when. The problem is discovery, not
documentation: an agent picking up `w1c` reads the `w1c` row of `ROADMAP.md`,
not the docstring of a function in a file it may have no reason to open.

So each debt now appears twice: once in `ROADMAP.md` § The W0 debt register, as
an index with the task that retires it and what it costs if nobody does, and
once in that task's **Done when** cell, which is the sentence the agent actually
reads. The register is the map; the done-when is the instruction.

Two of the seventeen are worth naming here because they are the ones that would
have cost the most:

- **`skeleton.attack_once()` becomes a second attack order the moment
  `engine/attack.py` exists** — and `tools/spike.py` imports it. Left alone,
  the benchmark that defends the performance budget, that `w2e` must beat and
  that `w3i` turns into a CI gate would go on measuring a hand-sequenced attack
  the project does not ship. The project's hardest structural rule is that the
  damage order is written in exactly one place; the attack order has the same
  property and had no rule protecting it. `w1f` retires it.
- **The `Combatant` and `Weapon` protocols are too narrow to write a real
  feature against.** `w0i` found this and worked around it in two fixtures with
  a paragraph each. No `on_hit` handler can read the defender's hit points or
  ask whether the weapon is Heavy — which is Great Weapon Master, every
  mastery, and every bloodied-target rider in the game. `w1a` and `w1f` widen
  them, `w3e` confirms the shape before ~400 features are written through it,
  and `w3f` fixes a structural gap if the pass finds one. Those two sub-items
  are the only W1 work that touches `hooks/contexts.py`, so they are solo inside
  otherwise delegable tasks.

### Three lessons that generalise past their tasks

Each was learned once in W0 at some cost, and each has an obvious later victim
that the plan did not previously warn:

1. **A benchmark must witness its own fixtures** (`w3i`). The spike ran for a
   while with its only gated reaction never firing, because the hero's hit point
   pool was sized so the gate's condition could not hold. The rate was fine
   throughout. The cheapest way to speed up a benchmark is to stop a rider
   firing, so a gate watching only the rate cannot tell a fix from a fixture
   that broke.
2. **A harness with exact-integer assertions must fix its unit of work**
   (`w3a`). The spike's counts were facts about seed 0. The fix was an invariant
   — a configuration resolving the same number of attacks whatever the dice do —
   not a bigger sample, and it is the harness's job rather than each author's.
3. **An optimisation that skips a step must prove every skipped step was the
   identity** (DESIGN.md § Structural prevention). Two of `w0i`'s three review
   findings had exactly that shape. A structural guarantee is only as strong as
   the fastest path around it.

### The turn-loop finding, which is a design note and not a bug

`w0i` charged an Action for what XPHB gives a Bonus Action, and the entry sat
above `attack` with an `if:` that its own effect did not clear — so the creature
bought the buff once and stopped attacking for the rest of the encounter. It
cost 0.07% of the rate. **That is why it is worth recording: nothing watching
the rate could ever have found it**, and it was caught only because the
exact-integer assertions moved when the seeds changed.

Two consequences are now in the plan. `w2c` charges real action costs and
filters offers to what the economy can pay, which is correctness rather than
policy. And `w2g`'s `SimResult` reports actions and attack resolutions per
round, so a policy that starves itself shows up as a count rather than as a
disappointing average — the same failure DESIGN.md § APL already warns about for
a melee build that stands still, arriving through the economy instead of the
grid.

### Stale statements corrected

- `ROADMAP.md` w0c named `PcgRng` as the production generator; `w0i` replaced it
  with `MersenneRng`. The row now names both and which superseded which.
- This file's § w0a listed the later CI gates against the wrong task ids
  (frozen-registry diff as `w3b`, benchmark regression as `w3c`, stale docs as
  `w3i`). The registry freeze is `w3f`'s, after the paper pass; the benchmark
  gate is `w3i`'s; stale generated docs are `w3c`'s.
- `core/timeline.py`'s `tiebreak` docstring justified `2**32` as the widest die
  `PcgRng.roll` can serve. Still true of `MersenneRng` — one 32-bit word per
  face — but by the old generator's name.
- DESIGN.md § Levers listed "contexts reset and reused per attack" without the
  finding that makes it true: the general `reopen()` was *slower* than
  allocating, and only the per-class compiled one is a win. A later reader
  simplifying that generated code back to a general implementation would re-take
  a lever that does not work, which is precisely the re-litigation this file
  exists to prevent.
- DESIGN.md § Biggest risks still listed "the performance spike passes on a
  stub" as risk 1. It fired, the mitigation worked, and it is closed. Its
  successor — the headroom eroding between M0 and the gate that guards it — is
  a different risk with different mitigations, and pretending it is the same one
  would leave the register describing a danger that is already behind us.

Sources: `docs/decisions.md` § w0c, § w0d, § w0f, § w0g, § w0g2, § w0h, § w0i;
DESIGN.md § Performance, § The spike must be pessimistic, § Levers, § The
benchmark gate, § APL, § Structural prevention over testing, § Biggest risks;
ROADMAP § Sequencing principles, § The W0 debt register, w1a–w1k, w2c, w2e–w2g,
w3a, w3e, w3f, w3i, w5a.
## w0g3 — the movement observer

`engine/triggers.py`, the audience half of the opportunity attack, and
`Dispatcher.responsive()` underneath it. The task was set in chat: *"make sure
the opportunity attacks are handled efficiently with an observer strategy where
all enemies in the room are notified on every 5 ft move action what the position
is and where the designated square is of that move. they then decide if they
want to use their reaction. we should also have a reaction apl."*

### Why this is in W0 at all

`w2c` owns the turn loop, opportunity attacks and the trigger audience, and W2
has not started. What moved forward is the audience and nothing else, on the
`w0g2` precedent: a spike that walks a grid but fires no trigger is a stub of
the most flattering kind, because the distance queries are paid for and the
provocation they exist to produce costs nothing. The same sentence that pulled
`core/geometry.py` into W0 pulls this in behind it. What deliberately did **not**
move is the opportunity attack itself — it is a Command, Commands are `w2b`'s,
and DESIGN.md § Module layout draws that line for the reason it is worth
keeping: *what can be done* versus *when you are asked*.

### An observer, but not a broadcast

The request as put is O(combatants x squares): everybody hears about every step.
That is the pub-sub bus DESIGN.md rejects, priced per square. What is
implemented has the same semantics and a different cost, and the reason it is
allowed to is that every filter is on the *offer* rather than on the answer — a
bystander is dropped only where it provably could not have acted:

1. **Nothing attached to `on_leave_reach`.** One dict lookup on the creature's
   own dispatch table. In a fight where one creature owns an opportunity attack,
   this alone takes an audience of twenty down to one.
2. **Every handler it has costs a reaction it has already spent.** Also asked of
   the dispatcher, because the dispatcher is what owns the economy.
3. **Further from the mover than its own reach plus the length of the route.**
   A step is one square in Chebyshev whatever its direction, so no square of an
   n-square route is more than 5n feet from where it started; a creature beyond
   `reach + 5n` can never have the mover in reach and so can never be left.

Filters 1 and 2 are `Dispatcher.responsive(spec)`, added for this. It is
deliberately a **conservative over-approximation**: it may answer True where a
dispatch would run nothing (one wasted dispatch) and never False where one would
run something (a silently skipped reaction). That is why it asks about `costs`
and not about `gate` or `once_per` — the gate is the policy question, needs a
context that does not exist yet, and is the expensive one of the dispatcher's
three; a spent `once_per` would be exact but buys a ledger lookup per handler
for a case the reaction cost already covers.

What falls out is broad phase once per move, narrow phase once per square per
survivor. When the broad phase comes back empty — most moves in most fights —
`watch_movement()` returns `None` rather than an empty watch, which is the
caller's licence to assign the destination in one statement and pay nothing per
square. An empty **Null Object** would have cost a call and a loop over nothing
on the one path whose whole claim is that it is free.

### Two orderings that are not free to get wrong

**The offer is made from the square being left.** XPHB puts the reaction right
before the creature leaves your reach, so `walk_provoking()` offers, then
assigns the position — and `LeaveReachContext.origin` is where the mover is
standing while the attack resolves. Resolve it a square late and a 5-foot reach
finds the mover out of range about half the time, on a route that provoked
correctly. This is why the stepping loop lives in `engine/triggers.py` next to
the offer rather than at each call site: it is one rule, and a second copy of it
is an off-by-one square nothing would catch.

**Two watchers left by the same step are asked in initiative order**, sorted
once per move rather than per square. Any order produces legal D&D; only a
stated one produces the same fight twice from the same seed, and determinism is
what `trace`, the goldens and every regression diff stand on.

### Measured, not asserted

The efficiency claim is checked as a **count**, in `tests/contract/test_triggers.py`:
twenty combatants, one of them owning a movement observer, a six-square move —
eight grid queries, being two broad-phase questions for the creature that got
past the dict lookup plus one per square for the same creature. A broadcast is
120. The other nineteen never reach the geometry. A wall-clock assertion of the
same claim on a shared runner fires on noise and is deleted within a month
(DESIGN.md § The benchmark gate), so there isn't one.

The spike's `_step()` now moves creatures through `walk_provoking()`, so the
audience computation is inside the M0 measurement. Nothing in the spike owns an
`on_leave_reach` handler, so what is measured there is the case the design
claims is free. Measured **A/B in one session**, which is the only comparison
this container supports: three baseline runs returned 26.5k / 26.6k / 26.6k and
three with the observer wired in returned 26.6k / 25.8k / 26.6k — the same
number. The fast path costs, per move, one dict lookup per combatant and a
`None`.

**That container is not the one M0 was measured on**, and the gap is not small:
it misses the 30k budget with the observer removed entirely, and the same
baseline measured 22.0k earlier the same day under different load. So these are
a *delta* and nothing else — an absolute figure from here is worthless, and
re-running the gate for real belongs on the machine § w0i quotes. Two sessions'
numbers are not comparable; only two runs in one session are.
`BUDGET_PER_SECOND` was not touched, and the one edit `tools/spike.py` warns
must never be made quietly was not made.

A configuration in which the offers actually fire is `w2c`'s, and its acceptance
criterion says so — not out of caution but because an opportunity attack made by
the spike's featureless enemies would be a *cheap* attack resolution added to the
numerator of a rate quoted in attack resolutions per second. It would raise the
reported number by making the average resolution less like a real one, which is
the exact failure DESIGN.md § The spike must be pessimistic exists to prevent.

### The reaction APL

There is no second list, and the chat request for one is answered by pointing at
what was already there: DESIGN.md § APL's unifying idea is that a turn action, a
reaction and a mid-attack rider are all the same question, and the dispatcher
already asks it — a handler declaring
`@hook("on_leave_reach", costs=ActionCost.REACTION, gate="attack.opportunity_attack")`
gets its economy checked and then its owner's action list asked, and the
reaction is taken only if it answered `APPLIED`. So the opportunity attack needs
no engine support to be a policy decision; it needs an audience, which is what
this task built. What was added is the contract: DESIGN.md § Reactions in the
APL, and `w2e`'s acceptance criteria.

One consequence is worth naming because it will look like a bug in a report
before it looks like a decision: **unmentioned is OFF applies to opportunity
attacks**, so a melee build whose list never claims one never swings at a
runner. That is the existing Null Object default and it was not carved out for
this case. The alternative — a free attack taken by default — silently spends a
reaction slot contested by Shield, Counterspell and Uncanny Dodge, and would
change the numbers of every build that already had a reaction entry on the day
opportunity attacks landed. Movement stays the one exception to OFF-by-default,
because a melee build that stands still reports *zero* damage, which is a
different order of wrong from one that declines a free swing.

### Two things left loud rather than guessed

`hooks/dispatch.py` deliberately does not drain `ctx.pending_effects` — the
engine that owns the event must, and for this one that is `w2c`'s turn loop.
There are no effects to queue until `w1c`, so rather than write a drain against
a class that does not exist, `_offer()` raises `EngineAssemblyError` if a handler
queues one. A silently dropped 2024 Sentinel is a wrong number nobody would ever
chase to this file.

**Entering** reach — which is what 2024 Polearm Master reacts to — has no hook.
The registry is closed at 39 and this was not the task to open it: a hook
addition is a deliberate batched core change, and one added on the way past
while implementing something else is exactly the drift the freeze exists to
stop. It is recorded here as a genuine gap for `w3e`'s paper pass to find and
`w3f`'s batch to close.

### And one left quiet, which is worse

The quoted rule is "a creature **you can see** leaves your reach", and there is
no visibility gate anywhere in this file. That was exact when this was
written — nothing in the project was Blinded or Invisible — and it is the debt
most likely to be forgotten precisely because it cost nothing yet. It has since
stopped being exact: `w1h` landed both conditions, and the gate now has
something to be wrong about (§ w1h, debt #39).
The review of this change caught it, and the answer was not to invent a
visibility model against conditions that do not exist but to put it where the
other four debts live: row 21 of ROADMAP.md § The W0 debt register, retired by
`w2c` once `w1h` gives it something to ask.

All five of this task's debts are rows 18-22 of that register, which post-dates
the first draft of this section. The register is the record; the module docstring
points at it.

### Reach is asked through `in_reach()`, not inlined

The first draft wrote `distance_ft(a, b) <= reach` at both query sites, on the
grounds that it is `in_reach`'s own body and the narrow phase runs per square.
The review of it pointed at `core/geometry.py`, which claims `in_reach` is the
one query "the attack pipeline, **the opportunity-attack trigger** and the APL's
`enemies.in_reach` selector all ask, in one place so that 'reach' means the same
thing to all three" — and called the mismatch doc drift.

It is more than that, which is why the code moved rather than the docstring.
Debt 9 is `Size` reaching geometry nowhere, retired by `w1d`, and what `w1d`
does is give a Large creature a reach its square does not describe. On that day
`in_reach` learns something and an inlined Chebyshev does not, and the failure
is an opportunity attack that a Large creature silently does not get — the exact
class of bug the sentence in `core/geometry.py` was written to prevent (`w1l`
cut that sentence to a pointer here). Both
sites now ask `in_reach`. The broad phase keeps a raw `distance_ft` alongside it
for the horizon cut, because that is a question about the length of the route
rather than about reach; `w1d` owes that bound the same second look, since a
footprint wider than one square widens it too.

The cost is one function call per candidate per move and one per watcher per
square — paid only when somebody in the room actually owns a movement reaction,
which is the case that was going to dispatch an attack anyway. The spike's
measurement is unchanged, because the filter that empties the audience runs
before either call.

### Hostility is not asked about here

The request says "all enemies in the room". `watch_movement()` takes the roster
minus the mover and does not filter by side, which is both closer to the rules —
an opportunity attack against an ally is legal and merely pointless — and
cheaper to keep honest: the two places that already encode "don't swing at your
own side" are the `opportunity_attack` Command's legality check (`w2b`) and the
APL entry's `if:`. A third copy, in the file least able to see what a build
means, is the copy that would go stale.

### What the docstrings argued, recorded here by `w1l`

- **A watcher is flattened at watch time**, for the reason `BoundHandler` is
  flattened at attach time: the per-step loop reads `reach_ft` and `threatening`
  once per watcher per square. `reach_ft` is safe to cache for one move — the
  watcher is not the one taking a turn.
- **A `MovementWatch` is not reusable across moves**: who threatens whom is a
  fact about the positions at the top of a move. Its `LeaveReachContext` is
  allocated on the first offer and rebound after, because most moves make no
  offer at all — pooling applied to a cold path, where the cost worth avoiding is
  the first allocation rather than the hundredth.

## w1a — the creature completed: equipment, a derived AC, and the economy

The first task of W1, and the one four of W0's twenty-two debts were filed
against: `creatures/creature.py` and `creatures/stats.py` finished rather than
rewritten, a new `creatures/economy.py`, and the widening of `Combatant` that
`hooks/contexts.py` has been waiting for since `w0f`. Retires debt-register rows
**#1** (the AC literal), **#2** (`Combatant` carries a name and nothing else),
**#4** (derived stats recomputed per attack) and **#10** (the `Economy` protocol
with no implementer and no contract test).

### AC is derived, and `Armour` is the one shape that derives it

DESIGN.md is emphatic that AC comes from equipment and never from a number in a
build, and `StatBlock.armour_class` is now **gone rather than defaulted** — a
default is precisely how a number nobody chose gets into a build and stays
there. `StatBlock(..., armour_class=15)` is a `TypeError`, which is the same
failure as writing it in the YAML.

What replaced it is one frozen value object answering one question — *how much
does wearing this raise the wearer's AC* — with two fields: `base_ac`, and the
ability modifiers it adds with the cap on each. That covers the four shapes the
rules have without a branch anywhere:

| Shape | `base_ac` | `abilities` |
|---|---|---|
| Unarmoured (and Unarmored Defense) | 10 | DEX, uncapped (+ CON or WIS) |
| Light | the table's | DEX, uncapped |
| Medium | the table's | DEX, capped at 2 |
| Heavy, and a monster's printed AC | the table's | none |

Three things about that table are load-bearing:

- **Heavy armour has no Dexterity entry; it does not cap Dexterity at zero.**
  The two differ only on a *negative* modifier, which is exactly the build that
  wears plate: a Strength fighter with a Dexterity of 8 wears it for 18, not 17.
  A cap of zero is the natural way to write it and is wrong for every heavy
  build in the game, so the field is a list of contributions rather than a cap
  and the named constructors are what a caller reaches for.
- **Unarmored Defense is a value, not a special case.** Barbarian is the
  unarmoured shape plus Constitution, Monk plus Wisdom; the class feature (W4)
  equips one of these rather than reaching into an AC calculation that would
  then have to know about classes. This is the sub-item ROADMAP `w1a` names and
  the reason the field is a tuple rather than one optional ability.
- **`Armour.natural(15)` is the only legitimate way an AC arrives as a typed
  number**, and it is legitimate because for a monster the number is what the
  rules publish and for the target dummy (`w2d`) it is the knob being swept. It
  is the heavy shape, since a printed AC already includes whatever Dexterity the
  creature has. A *character* build reaching for it is what `w2f`'s schema
  refuses.

A shield is `+2` and a `bool` on the creature. It is not an item because v1 has
no magic items (DESIGN.md § Settled decisions), so the number the rules state
once is the number every shield gives; `rules/constants.py` holds it and `w2a`'s
import checks its entry against it the way the slot tables check theirs.

**What does not go through `Armour` is a bonus that lasts one attack.** Shield,
cover and Shield of Faith ride on `AttackDeclaredContext.ac_bonus`, which is the
defender's one lever in the attack pipeline and the reason the closed registry
has no hook that recomputes an AC. So `Creature.armour_class` is derived once,
cached in a slot, and recomputed only when something is equipped — which matters
because every attack made against a creature reads it.

### The derived-stat cache, and the two ways it could have gone stale

`proficiency_bonus` walked the XPHB table and `modifier()` divided on every
read, three times per resolution — ~1.5% of the budget (§ w0i). Both are pure
functions of a frozen stat block, so both are settled in `StatBlock.__post_init__`
and read off the creature as plain slots.

Both are private and read back through properties with no setter, for the same
reason `stats` is: a cache anybody can assign to is a cache that says what the
creature used to be.

A cache is only worth having if it cannot lie, and there were two further ways
for this one to:

- **The caller keeps the ability dict.** `StatBlock` now copies the scores
  behind a `MappingProxyType` before deriving anything from them, so a caller
  raising a Strength score after construction cannot leave the modifiers
  describing a creature that no longer exists.
- **Somebody swaps the stat block.** `Creature.stats` is a read-only property
  over `_stats`. Three numbers are read off it at construction — the modifiers,
  the proficiency bonus and the derived AC — and a creature whose build changed
  is a new creature, not a mutated one.

`crit_threshold` joins them as a derived stat, which `hooks/contexts.py` and
`tools/skeleton.py` both said in as many words was this task's: Improved
Critical lowers the *character's* threshold, so `AttackRollContext` deliberately
has no mutator for it and the engine reads it off the creature when it builds
the context.

### `ActionEconomy`: four things, and the refusal

Counts, not flags, because Action Surge is a second Action and a creature
granted two and having spent one has one left — which no boolean can say. That
is the whole of Action Surge from this side: `grant()`, and the turn loop
re-polls (DESIGN.md § APL).

Three decisions worth keeping:

- **Overdrawing raises `EconomyError` rather than clamping.** Asking is free —
  `can_spend()` is what the dispatcher calls for every costed handler and what
  `w2c` filters its offers through — so reaching the refusal means a caller
  decided not to ask. The bug this catches is M0's own: an entry charging an
  Action for what XPHB gives a Bonus Action, at 0.07% of the rate (§ The W0 plan
  review). A ledger that clamped would have hidden it completely.
- **The reaction comes back in `turn_started()` and nowhere else**, which is
  DESIGN.md § Failing loudly's invariant stated on the turn boundary rather than
  the round: a creature high in initiative may react on an enemy's turn, take
  its own turn, and react again in the same round. A fresh economy starts with
  its reaction in hand and nothing else — a creature may take an opportunity
  attack in round one before its own turn, and may not take an Action until it.
- **`turn_started()` is given the speed rather than storing one.** A Rage, a
  Longstrider or a set of heavy armour changes a creature's speed between turns,
  and a ledger holding its own copy would be a second place a speed is written.

**A creature owns its economy and hands it to its own dispatcher** at
construction. That makes "a creature spending out of somebody else's economy"
the same kind of impossible as "a handler owned by somebody else", and it leaves
`Dispatcher.bind()` for the gate alone — which is the one of the two that
genuinely has a cycle, since the APL's namespace is built over the creature that
owns the dispatcher. `tools/spike.py`'s `SpikeEconomy` went with it: the
protocol now has a real implementer, and a benchmark measuring a fake economy
would be a stand-in nobody re-measures.

### Widening `Combatant`: three numbers, and what stayed out

`hp`, `max_hp` and `temp_hp` — one promise rather than three, since they are
what `engine/damage.py` spends and the order it spends them in. Without them no
`on_hit` handler could ask whether its target is bloodied, and "below half its
hit points" is a condition dozens of the ~630 features are written around;
`w0i`'s own bloodied rider had to interrogate the damage bag and said so in a
paragraph. That rider now reads the pool, and the property its paragraph was
defending is unchanged: the condition is still sometimes true, which is what
keeps the benchmark's mix honest.

All three are read-only on the protocol. A handler that could set hit points
would be a second place the damage resolution order lives, and the engine
mutates them through the concrete `Creature` instead.

What stayed out, deliberately: **Armor Class**, because reading a target's AC is
a policy question and the APL asks the live creature through its namespace
rather than through an event's view of it; and **`is_alive`**, because dying is
`w1j`'s and a protocol promising it now would be promising a definition that
does not exist. `w3e`'s paper pass confirms the shape before ~400 features are
written against it.

### The bloodied rider, which was never the rider it said it was

Widening the protocol let `w0i`'s fixture ask the question it was named for, and
asking it turned up two things worth recording.

**Bloodied is hit points alone.** The first cut of the rewritten rider asked
`(hp + temp_hp) * 2 > max_hp`, which is wrong: temporary hit points are a buffer
in front of hit points and are not hit points (XPHB, Rules Glossary), so a
creature at 30 of 100 behind 21 temporary ones is Bloodied and that predicate
says it is not. It changed no number here — nothing in the spike has temporary
hit points — which is exactly why it was worth catching now rather than in W4,
when it would have been copied into every feature written against a target's
pool. Both numbers stay on the protocol because they answer different questions:
`hp` against `max_hp` is Bloodied, and `hp + temp_hp` is the pool the next hit
comes out of.

**The old fixture applied on every hit.** Counted over twenty encounters from
seed 0: the four riders ahead of it in the order each apply on all 287 hits, so
the bag was never shorter than four and `len(ctx.damage) < 3` was never true.
The rider that the file's own docstring called "the sometimes-true rider" was a
constant, and nothing was watching. It now applies on 104 of those 287 — the
first version of it that is what it claims. That is `w3i`'s lesson arriving from
the other direction: a benchmark must witness its own fixtures, and "sometimes
true" is a claim to be counted rather than asserted in prose.

### What this cost the benchmark

Nothing measurable, and that was checked rather than assumed — because a rider
that stopped applying on 64% of hits is precisely the shape of a benchmark
getting faster by doing less work.

- **The rider's applies-rate is worth nothing at this resolution.** Seven
  measurements of each, interleaved in one process, honest predicate against the
  same fixture forced back to always-applying: medians **37.7k** and **37.3k** —
  a difference in the wrong direction and an order of magnitude inside the
  noise. One `damage.add(flat=2)` out of ~20 handler calls and nine dispatches
  per resolution does not show up.
- **The wave's own change is the same story.** Interleaved runs of the W0 tree
  against this one put the medians at ~33.0k and ~34.5k. Run-to-run spread on
  this container is ±10%, wider than the gap, so the honest claim is **no
  regression** rather than a gain.

`w1k` is the task that measures W1's per-round cost properly, and it inherits a
baseline whose one fixture change has been priced.

### What w1a deliberately did not do

- **No proficiency system.** `tools/skeleton.py` adds a proficiency bonus
  unconditionally and says why (§ w0h); weapon proficiency belongs to the
  attack (`w1f`) and save proficiency to saving throws (`w1g`), and a flag with
  one caller invented here would be in both their way. Both landed and neither
  added the flag: `engine/attack.py` adds the bonus to every attack (debt #25,
  `w2f`'s) and `engine/saves.py` adds none to a save (debt #36, `w2f`'s too),
  because the field is a build fact and `w2f` is what assembles one.
- **No armour catalogue.** `Armour` is the class; the 5etools entries arrive
  through `content/` in `w2a`, exactly as the 38 weapons do. The two armours
  written by hand are in `tools/spike.py` and `tools/skeleton.py`, where they
  hold the AC those benchmarks have always run against — fixtures, not domain
  constants, and neither is a second `GREATSWORD`.
- **No movement accounting in the spike.** `turn_started()` now hands the hero
  30 feet it never spends: the benchmark's `_step` walks the grid without asking
  the economy, because charging movement per square is the turn loop's job and
  `w2c` owes that measurement. Handing out a budget nobody draws on costs one
  store per turn and measures nothing — which is worth saying plainly, since the
  per-step economy call `w2c` will add is a cost this number still does not
  include.
- **No speed modifiers.** Heavy armour's Strength requirement costs 10 feet of
  speed, and nothing here reads it: `Armour` carries neither the requirement nor
  the stealth penalty, because a combat simulator has no stealth check and the
  speed penalty needs the effect machinery `w1c` builds. `w2a` imports both
  fields and the first feature that needs them is where they land.

### What the docstrings argued, recorded here by `w1l`

- **`Armour.unarmoured()` does not carry who may also hold a Shield** (the
  Barbarian's Unarmored Defense works with one, the Monk's does not). That is a
  legality rule about a build, `builds/schema.py`'s (`w2f`), not arithmetic
  about an AC.

## w1e — the damage resolution order, against the 2024 text

The spec review that produced `w1e`'s row (PR #23) found the XPHB sentence that
settles the order and overturned one of `w0g2`'s two rulings with it. This is
that finding turned into arithmetic, plus the decisions the reorder forced.

### One quotation, one surviving ruling, one question answered

- **Adjustments, then Resistance, then Vulnerability is quoted, not ruled**
  (XPHB, "Damage and Healing"). A flat reduction is a penalty and a halving is a
  multiplier, so Heavy Armor Master and Uncanny Dodge both land in the first
  band. The book ships its own worked example — 28 fire, reduced by 5 to 23,
  halved to 11, doubled to 22 — and it is the first contract case.
- **`w0g2`'s flat-after-resistance ruling is gone.** It reasoned that "the damage
  you take" is what arrives past your resistances, and noticed correctly that the
  consequence of the other order is a reduction worth half to the creature that
  also invested in resistance. The consequence is real and the book orders it
  that way anyway.
- **Flat-before-halving survives**, and its argument had to be replaced twice
  before it was right. What it is now: the one thing the text *does* settle about
  a reduction is that Resistance halves it along with everything else, and a
  halving is the same ×½ operation the text simply declined to sequence — so
  subtracting first makes a reduction meet Uncanny Dodge exactly as the book has
  it meet Rage. Halving first would make those two exactly additive while a
  reduction stays sub-additive with Resistance.
- **Immunity leads, and that is load-bearing.** Adjustments subtract from *the
  damage you take*; damage you are immune to is not that. Placed later, a -4
  against 10 fire and 10 poison on a poison-immune creature would leak 8 fire
  instead of 6.
- **The open question is answered from the text.** The "counts once" clause names
  *Resistance*, a halving is not Resistance, so two halvings halve twice. The
  case that turns on it is two halvings (9 → 4 → 2), **not** Rage plus Uncanny
  Dodge quartering a hit — one resistance and one halving come out the same
  however halvings are counted.

### How an untyped adjustment is spent, which is the reorder's own question

Adjustments act on one number and the typed steps act per type, so putting the
untyped steps first leaves the order needing a per-type picture on the far side
of a step that has none. No sentence covers it. Two properties decided it:

- **The spend may not consult the steps that follow it.** Spending the reduction
  on whatever is not resisted is the better play and is what the quotation
  forbids: an adjustment applied *first* cannot know what Resistance will do with
  what it leaves.
- **It may not depend on the order the damage parts arrived** — an authoring
  artefact, and a number that moved when a feature registered earlier is a
  reproducibility break with no error anywhere.

What survives both is proportion: an adjustment changes the size of the hit, not
its composition. A third property decided *which* proportional method, and the
review found it rather than the design: **more mitigation must never mean more
damage taken.** `_apportion()` was first written as largest remainder, which is
not house-monotone — the Alabama paradox — and it showed in the fight: 1 fire
beside 5 slashing and 5 cold, resisting the latter two, took **2** damage through
a 5-point reduction and **3** through a 6-point one. Jefferson's method has the
property by construction, since the allocation of `n` points is the allocation of
`n - 1` plus one more. Three types are needed to see the paradox, which is why
every two-type case passed under both methods.

### What inverted on `Mitigated`

`w0g2` had `total` and `sum(amounts.values())` deliberately disagree, because the
untyped steps ran last and there was then no honest way to spread a subtraction
back over the types. The apportionment is that way, so the two now agree, and
`types` can lose a type to an untyped step: half of one point of fire is no fire.
The `untouched` fast path still skips every step and still clamps, the clamp
being the one step that is not the identity on an empty ledger.

### What the review cost, and what that is worth recording

Five rounds. **The first found a real bug** — `_apportion()` written as largest
remainder, so a larger reduction could leave the creature taking more damage —
**and a test gap**, the tie-break no case pinned. **The other four were prose**:
three arguments that defended a correct decision with a consequence it did not
have — flat-before-halving justified by a stacking that does not occur (the
rejected order is exactly additive, never more), `_halvings` justified by a case
that works either way, a worked comment that derived the apportionment by the
method the code had just abandoned — and one queue row left in a state that
would have started `w1f` on an unmerged dependency. Each of the four was found
by checking a stated consequence and finding it false.

The lesson is not "write less down" — the citations are contract. It is that an
argument in a decision record is load-bearing exactly like code, and the ones
that failed here were the decorative extensions past the citation: the flourish
about what the rules "went out of their way to close", the vivid case reached for
instead of the case that applies. A citation carries the quotation and the one
consequence that actually follows. Everything past that is surface for a later
agent to disprove, and disproving it is how a settled decision gets reopened for
the wrong reason.

### What this task deliberately did not do

- **Reductions are still untyped.** 2024 Heavy Armor Master reduces Bludgeoning,
  Piercing and Slashing only, and `ctx.reduce()` takes no damage type. `w0g2`
  spent the reduction over the total too, but the reorder makes the spend decide
  *which* types shrink. Debt register #23, retired by `w3f` via `w3e`.
- **No log records, no dying, no effects.** `w0g2`'s module docstring claimed
  `w1e` would bring all three, before those tasks had rows: the log is `w1i`,
  dying is `w1j`, draining a queued effect is `w1c`.
- **The benchmark was measured, not assumed.** The spike ran both ways on the
  same container — 26,267/sec before, 26,649/sec after, inside a ±10% spread.
  Both are under the 30k budget because this container is slower than the one
  `w0i` measured ~31.5k on, which is what `w3i`'s same-run baseline is for. The
  apportionment runs only when a ledger has an untyped step *and* more than one
  type survived immunity.

## w1f — the attack pipeline, and the rulings it needed

`engine/attack.py` is the second of the two resolution orders everything
downstream trusts, and the first that had a hand-written copy to displace:
`w0h` sequenced the attack by hand in `tools/skeleton.py` so that `w0i` had
something integrated to measure. That copy is gone (debt #6). What follows is
what the real one had to decide that the copy never did.

### A Critical Hit below 20 still has to hit

The skeleton took any natural roll at or above the threshold as a Critical Hit
without ever consulting AC, which is exact while the threshold is 20 — a natural
20 hits whatever the AC (XPHB, "D20 Tests" -> Attack Rolls, "Rolling 20 or 1") —
and wrong the first time a Champion lowers it. Nothing makes a 19 hit; Improved
Critical says only that an attack roll *can score a Critical Hit* on one. So the
pipeline asks the two questions separately: `natural == NATURAL_TWENTY or total
>= AC` decides whether it hit, and the threshold decides only whether a hit is a
critical one.

`AttackRollContext.is_critical` was already built for this — it is the natural
roll against the threshold, at a hook that fires before AC is consulted — so the
engine combines it with the hit test and no context changed.

### A veto is honoured at the declaration and refused after it

`ctx.veto()` is on every context by inheritance, and the attack has seven hooks.
Only one moment can honour it: before the roll, where "this attack does not
happen" is a thing that can be true. `resolve_attack()` returns `PREVENTED`
there — `outcome is None`, no d20 drawn — because `Outcome` is scoped to attack
rolls on purpose (§ w0b) and an attack nobody rolled has no member
that describes it. That is the whole reason `AttackResult.outcome` is optional.

At every later stage the attack has been made, and the honest answer is neither
to invent an outcome nor to continue: `_seal()` raises. A veto silently ignored
would make the design's one Chain of Responsibility element a no-op at five of
seven attack hooks, which is the class of silence DESIGN.md § Failing loudly
exists to refuse. `_seal()` carries the queued-effect refusal (debt #5) in the
same breath and closes the context, so both checks ride the `close()` the
sequence was making anyway.

**The two refusals raise different types on purpose.** A queued effect is an
`EngineAssemblyError` — the engine really is incomplete, and `w1c` completes it.
A late veto is a `FeatureContractError`: the feature used a mutator where it has
no meaning, and no later task gives it one. `core/errors.py` draws exactly that
line, and without it `w1c`'s sweep would have to tell the raise it must delete
from the one it must keep by matching on the message.

### Damage on a miss, and why `on_damage_roll` does not fire for it

`MissContext` has carried a damage bag since `w0f` because the Graze mastery
deals the ability modifier on a miss, and nothing ever rolled it. It is rolled
here and resolved through `engine/damage.py` like any other damage — a target's
resistance does not care which branch the damage came down.

`on_damage_roll` does **not** fire on that path, and this is the branch's one
real decision. Graze's own sentence is that its damage "can be increased only by
increasing the ability modifier" (XPHB, "Equipment" -> Mastery Properties), and
`on_damage_roll` is where Rage damage and brutal dice attach. Firing it would
put every such feature on a miss, and the alternative — firing it and asking
each feature to know about Graze — is the engine branching on content. Damage
that belongs to a miss is added at `on_miss`, by the feature that owns the rule.

### Finesse picks the better modifier

`attack_ability()` returns Strength, or Dexterity for a Finesse weapon when
Dexterity is the higher modifier (XPHB, "Equipment" -> Weapon Properties). The
rules leave the choice to the wielder, and taking the better of the two is a
policy rather than arithmetic — so it is a default the caller can override with
`resolve_attack(..., ability=...)` rather than a rule written into the roll.
Nothing in the design wants the worse modifier today; `w8`'s scorer is the first
thing that could, and it has the argument to say so.

### The `Weapon` protocol, widened to six members

Debt #3: through W0 a context knew a weapon by its name alone, so Great Weapon
Master could not ask whether it was holding a Heavy weapon and no mastery could
recognise its own. It now declares what a handler has to ask — the dice and type
it contributes to, the mastery, the reach, and `has()` for one property at a
time. `has()` rather than the property set: a `frozenset` on the protocol would
be a second way to ask the same question that `w2a`'s importer would then have
to keep equal to the first.

Range stayed out, because nothing can supply it: a ranged attack needs the
normal/long pair the Weapon table prints, `w2a` imports it, and until then every
attack resolves at the weapon's reach. That is debt #24, and it is why reach is
taken from **the weapon this attack is made with** rather than from
`Creature.reach_ft`, which is about what the creature has equipped. The query
itself is still `in_reach()`, for the reason § w0g3 gives.

### What the `trigger.*` offer says at each moment

`Trigger.bind()` replaces the whole offer rather than adding to it, which `w0i`
wrote and this task kept: the APL is being told what is on the table *now*.
What changed is the hit branch's last bind, which carries `crit` alongside
`damage_estimate`. Both facts are true of the same attack, and the alternative
was a victim's reaction being offered a critical hit's damage while
`trigger.crit` said the hit was ordinary — a wrong value rather than an absent
one. DESIGN.md § APL's example reads `trigger.crit` for Divine Smite at
`on_hit` and `trigger.damage_estimate` for Uncanny Dodge at the damage moment;
the first is untouched and the second can now ask both. A miss binds the
estimate alone, because a miss is never critical.

The contract test pins all three offers of one critical hit in order, so `w2e`
inherits a decision instead of an observation.

### One pool, one attack

`PooledContexts` rebinds one context of each kind per attack, so an attack begun
from inside a handler of another attack on the same pool would overwrite the
event in flight — the hazard `w0i` noted and left to this task. `declared()`
now refuses re-entry and `resolve_attack()` calls `release()` on every path it
returns by. `w2c` is the wave that can produce a nested attack, and its answer is
a second pool rather than a deeper one. An exception escaping the sequence
deliberately does not release the pool: a pool that reopened over a half-finished
attack would turn one loud failure into a quiet wrong fight.

### What it cost the benchmark

The spike was run on the same container before and after, because this container
reads ~27.5k/sec on `main` against the ~31.5k `w0i` recorded on its own — the
absolute number cannot be compared across machines, which is what `w3i`'s
same-run baseline is for.

**The completed pipeline costs 5.5% of the rate**: 27,574 -> 26,063/sec,
medians of four paired runs (base 27,542/27,571/27,577/27,608; w1f
25,882/25,921/26,205/26,245).

**`_seal()`'s two refusals are 1.8% of it**, measured in a second paired run
against a build with `_seal()` stubbed to a bare `close()`: 26,194 -> 26,675/sec,
two pairs each. That is a separate experiment on a container whose own spread is
around 1%, so it is an attribution and not a subtraction — the two runs do not
decompose each other exactly, and the points it leaves over are the rest of the
pipeline's new work: deriving the attack ability, rolling modifier dice rather
than reading flat totals, the reach query, `release()`, and the result tuple.
The queued-effect refusal is the transitional half and goes with `w1c`;
`_seal()` is the one place to inline if `w1k` needs the rest back.

Both runs predate one change to the spike itself: its Great Weapon Master
fixture matched the weapon's *name* because the `Weapon` protocol had nothing
else to ask, and now asks `has(HEAVY)`. The re-measure is 26,104/sec — inside
the spread, as that fixture's own note predicted when it called the identity
check a stand-in for the property lookup — and `test_spike.py`'s work witness
still shows the rider applying, which is what separates a cheap change from a
handler that quietly stopped firing.

None of it is waste to cut; it is the pipeline being complete. It is also most
of M0's 5% of headroom, spent in the wave `ROADMAP.md` § What else the review
changed predicted would spend it — which is what `w1k` was added to measure.

### What this task deliberately did not do

- **No proficiency question.** The proficiency bonus is added to every attack.
  Exact for a character swinging their own weapon and for a monster, whose
  attack bonus is printed; wrong for a build holding something it is not
  proficient with, which `w2f`'s schema is the first thing that can express.
  Debt #25.
- **No ranged branch**, per above — debt #24.
- **No legality and no cost.** Whether a creature may attack, and what the swing
  spends, is `w2b`'s Command over `w1a`'s economy. What `resolve_attack()`
  refuses is only an attack that cannot be described: no weapon, or a target
  outside the weapon's reach.
- **No opportunity attack.** `engine/triggers.py` makes the offer and `w2c`
  turns it into an attack; this task gives it the sequence to call.

### What the docstrings argued, recorded here by `w1l`

- **`AttackResult` is a `NamedTuple`**, not a frozen dataclass: it is allocated on
  every resolution, and a two-field tuple is built by the interpreter where a
  frozen dataclass runs `object.__setattr__` twice.
- **`Trigger` is a `Protocol`**, because what defines it (`apl/namespace.py`,
  `w2e`) does not exist yet; the attack sequence's whole knowledge of the APL is
  that something wants to be told what it is being offered before a dispatch
  that carries gated handlers. `None` — nobody asking — is the ordinary case.
- **`AttackContexts` is a Strategy** rather than an `if pool is None` at eight
  call sites, which keeps the sequence readable as one thing and the two answers
  — allocate or rebind — honest about what each costs.

## The W1 critical-path review — the headroom, and three debts with no row

Not a task. `w1a`, `w1e` and `w1f` have landed since the last gate, and the
plans were re-read against what those three measured rather than against what
they assumed. The model is § The W0 plan review, and so is the result: most of
what follows was already written down in this file by the task that found it,
and what none of it had was a home in the plan.

### The finding: M0's margin is spent, and the plan still said it wasn't

`w0i` cleared the budget at ~31.5k against 30k. Five percent. § The W0 plan
review added `w1k` on the argument that five percent would not survive twenty
tasks unwatched with the per-round cost still unmodelled.

It did not survive three. `w1f` priced the completed attack pipeline at **5.5%
of the rate** — 27,574 → 26,063/sec, medians of four paired runs on one
container — and `w1a` and `w1e` each measured no change outside the spread.
Projected onto the machine `w0i` measured on, the hostile configuration now sits
at roughly **29.8k — under the ≥30k floor, not at it**. None of
`w1c`'s expiry sweep, `w1b`'s pool arithmetic, `w1d`'s route resolution, `w1i`'s
per-roll records or `w1j`'s zero-hit-point hook has landed yet, and those are
exactly the per-round costs `w0i` said its number understates.

**Say plainly what kind of claim that is**, because the two get conflated and
the conflation is what a stop-and-revise gate cannot afford. *Measured*: 5.5%,
four paired runs, one container. *Projected*: ~29.8k on a machine nobody has
re-run the hostile configuration on since `w0i` — and § w1f is explicit that an
absolute rate does not survive a change of machine, which is exactly what
`w3i`'s same-run baseline is for. So **M0 was met on measurement and is
projected to be missed on arithmetic.** `w1k` is the measurement that settles
which, and this gate does not halt W1 on a projection. What it refuses is to
let the plan go on describing a margin the arithmetic has already eaten, in
every place it said so (ROADMAP § What the W1 critical-path gate changed lists
them).

**Nothing here is wrong and nothing needs undoing.** `w1f` spent the margin on
the pipeline being complete, in the wave the last review predicted would spend
it, and said so. The budget is not revised — DESIGN.md § Biggest risks already
names a second revision as the failure mode to watch for, and the argument that
would produce one is a shortfall nobody can attribute.

### So the change is attribution, not a number

That is everything this gate changes about *how a W1 task is executed*, and it
is sequencing principle 10: **a cost is actionable only if the task that added it attributes
it.** Principle 7 makes a stand-in a budget by naming who re-measures it; this
is the same move pointed the other way.

The case for it is `w1f` itself. It did not report 5.5%; it reported 5.5% of
which 1.8% is `_seal()`, measured against a build with `_seal()` stubbed. That
second number is what makes the first one usable — it names the one thing worth
inlining if `w1k` needs points back, and it establishes that the remainder is
the pipeline doing work it has to do. Had `w1b`, `w1c`, `w1d`, `w1i` and `w1j`
each quietly taken 2% and only `w1k` measured, the gate would have reported
~26.9k against a 30k budget — `w1f`'s 5.5% and then five 2% cuts off ~31.5k —
with five candidate causes, no paired run left to separate them, and one cheap
way out.

So each of those five rows now carries a **priced** clause: run the spike paired
on your own container, record what it cost. `w1k` totals attributions. The cost
is an afternoon per task, which is what `w1f` shows.

**`w1d` is on that list and the first draft of this review left it off**, which
is worth recording because of how it was missed: the four obvious rows are the
ones whose work happens *per attack*, and `core/geometry.py` is the one whose
work happens per *move*. `tools/spike.py`'s `_step` calls `route()` once per
move, so line of sight, a size-aware occupancy and the shortest-legal-route
resolution all land on a path the benchmark already walks. "Hot path" is what
the benchmark executes, not what the attack sequence calls.

### `w1c`'s instruction was stale within one task of being written

The most concrete finding, and the one worth the gate on its own. `w1c`'s
**Done when** told its agent to delete `engine/damage.py`'s `refuse_pending()`
and its two contract tests. `w1f` then called the same function from two more
places in `engine/attack.py` — at the declaration, and from `_seal()`, which
runs at five of the attack's stages — and pinned them with two contract tests of
its own. Debt row #5 was updated to say so; the **Done when** was not, and
neither the second pair of call sites nor the second pair of tests had anybody.

§ The W0 plan review's own sentence is that the register is the map and the
done-when is the instruction, *because the done-when is what the agent reads*.
An agent following it would have swept one file, left the attack pipeline's two
raise sites standing — six moments, since `_seal()` runs at five stages — and
shipped a `w1c` in which no `on_hit` handler can apply an effect, which is most
of what an effect is for.

**The enumeration took three review rounds to finish, and that is the finding
underneath the finding.** Round 1 corrected the call sites and left the contract
tests stale in the same cell. Round 2 corrected the tests as "all four" and
missed a fifth — `test_the_two_refusals_a_sealed_context_makes_are_different_kinds`,
which raises on both refusals and is an **amend, not a delete**: its
queued-effect half goes, its veto half and its two `issubclass` assertions stay,
because those pin `EngineAssemblyError` and `FeatureContractError` as siblings,
which is the property that makes sweeping by type possible at all. A sweep that
deleted it would remove the guard on the distinction it was following. So the
row names the files, both pairs of deletable tests, the fifth test and what
happens to it, and the rule for telling the two refusals
apart:
**sweep by exception type, never by message**, which is the distinction `w1f`
built `core/errors.py`'s two classes to carry and which would otherwise have
been discovered by deleting a `FeatureContractError` that is permanent.

### Three deferred obligations that had a record and no row

Each was written down by the task that made it, in this file, and named the
task that would answer it. None was in the register, so none is in a
**Done when** cell, so the agent who owes it has not been told.

- **#26 — a nested attack raises.** `PooledContexts` serves one attack at a
  time and `declared()` refuses re-entry, because rebinding a live context would
  resolve the outer attack against the inner one's numbers (§ w1f). The refusal
  is right and its message names the fix — a second pool, never a deeper one.
  What was missing is that `w2c`, the wave whose reaction attacks first produce
  one, had no row saying it owes that pool.
- **#27 — heavy armour never costs its 10 feet.** § w1a left the Strength
  requirement and the stealth penalty to "`w2a` imports both fields and the
  first feature that needs them is where they land". Wearing armour is not a
  feature, so no later task's row ever named it, and a Strength-13 character in
  plate moves 30 feet forever. `w2a` carries the field; `w2f` assembles a
  creature from its equipment and is the first thing that can apply it, exactly
  as it is the first thing that derives AC from it.
  **The row tracks the speed penalty only**, though § w1a names the stealth
  penalty in the same breath: this simulator rolls no stealth check, so that
  half is out of scope rather than owed, and a register row with no task that
  can retire it is worse than no row — it reads as work outstanding forever.
- **#28 — movement is never charged.** § w1a: `turn_started()` hands the hero
  30 feet the spike's `_step` walks without ever drawing on, and says plainly
  that the per-step economy call `w2c` will add is a cost the number does not
  include. That is a debt against the *budget* as much as against the turn loop,
  which is why it belongs in the register beside #15 rather than only in a
  paragraph — it is one of the reasons `w1k`'s number will still be optimistic.

### The seam: two cases `w8a`'s property test would not have thought to include

`engine/estimate.py` must never disagree with `engine/damage.py`, and the
binding that enforces it is `w8a`'s property test over 10⁵ real resolutions. Its
matrix was written from the closed form — crit, advantage, resistance,
save-for-half, a small pool, temp HP. `w1f` settled two things the resolver now
does that a matrix written that way covers by accident or not at all:

- **A Critical Hit below 20 still has to hit.** The pipeline asks the two
  questions separately, so `P(crit)` is `P(natural ≥ threshold and hit)`. At a
  threshold of 20 that equals `(21 − threshold)/20`, and a threshold of 20 is
  every case in the project until a Champion is written in W4. An estimator
  using the simple form passes the whole matrix and diverges the first time it
  scores the one subclass the rule exists for — at which point the fit absorbs
  it into a coefficient and nothing goes red, which is the failure this test is
  the only defence against. The matrix needs a lowered threshold against an AC a
  19 does not meet.
- **Damage on a miss.** Graze is rolled and resolved down the miss branch, so
  `Ê_raw = E[X]` carries a miss term. An estimator summing the hit branch alone
  under-reads every mastery weapon in W4.

Cutting this in `w8a`'s row costs two sentences. Discovered in W8 it is a fitted
coefficient set to throw away.

### What six review rounds cost, and what that measures

This gate changed no code and took six rounds. Every finding across all six was
confirmed against the source and none was contested — and after round 1, most of
them were drift the *previous round's fix* introduced. Round 1 corrected `w1c`'s
call sites and left its contract tests stale. Round 3 corrected the tests and
left a sentence unterminated. Round 4 added `w1d` to the priced set and left
`w1c`'s new dependency edge (round 5) and `w1k`'s missing `w1i` edge (round 6)
behind it, along with a "the only structural change" claim that three later
bullets falsified.

**The thing being measured is the plan's cross-reference density, not the
reviewer's patience.** One fact — "`w1d` is on the hot path" — has to be true in
a **Done when** cell, a wave preamble, a sequencing principle, a dependency
edge, `w1k`'s totalling clause, CLAUDE.md's file list and this file. Seven
places, each individually cheap, and no tooling checks any of them against the
others. That is the cost of the register-plus-done-when duplication § The W0
plan review chose deliberately, and it was still the right choice: the
duplication is what got `w1c`'s instruction corrected at all, since the register
row was right and the instruction was the thing being read.

It is recorded rather than fixed because the fix is a generated document or a
consistency linter, and neither is worth building for a plan this size. What is
worth knowing is the shape: **after changing a fact in the plan, grep the plan
for the fact — not for the sentence.** The four rounds that found bookkeeping
would each have been caught by one grep.

### What this review deliberately did not change

- **The budget.** Per above.
- **`w1k`'s Done when**, which anticipated this exactly — "≥30k, or the
  shortfall is measured, attributed and either accepted or revised away". The
  row was right; what changed is which of its two branches it will take.
- **`trigger.damage_estimate`.** It is the rolled, pre-mitigation total rather
  than a closed-form estimate (§ w1f), so it is not a second estimator beside
  `engine/estimate.py` and there is no divergence to police. Whether a reaction
  should see an exact number at all is a question DESIGN.md § APL settled when
  it wrote the Uncanny Dodge example, and re-opening it is not this gate's.
- **The register's name.** It is § The W0 debt register and it now carries six
  rows that are not W0's. Every row is cited by number from code and from this
  file; a rename is churn against every one of those citations for a word.

Sources: `docs/decisions.md` § w0i, § The W0 plan review, § w1a, § w1e, § w1f;
`ROADMAP.md` § Sequencing principles, § The W0 debt register, w1b, w1c, w1i,
w1j, w1k, w2a, w2c, w2f, w8a; DESIGN.md § Performance, § Biggest risks.

## w1c — effects, and the two places a queued one had nowhere to go

`Effect`, `EffectStore` and `Stacking` are the objects DESIGN.md § Durations
describes; what needed deciding was everything around them, because the row's
own deliverable — "`ctx.pending_effects` is drained at every one of its
callers" — turned out to name two questions the plans do not answer. Both are
about what the engine can reach from where.

### A queued effect names a `Combatant`, which has no store behind it

`ctx.apply_effect(target, effect)` takes the narrow read-only protocol
`hooks/contexts.py` declares, because that is all a feature has: `HookSource.owner`
is a `Combatant` and every participant field on every context is one. An
`EffectStore` hangs off a `Creature`. So the drain has to cross from the view to
the object, and there are only four ways across.

- **Widen `Combatant` with the store.** Refused, and not only because
  `ROADMAP.md` § W1 reserves the protocol widenings for `w1a` and `w1f`: it does
  not typecheck. `EffectStore.apply()` takes this module's `Effect`, the
  protocol version carries a name and nothing else, and a parameter type is
  contravariant — so a store accepting the narrower class does not satisfy a
  protocol member declaring the wider one. Making it satisfy the protocol means
  moving `duration`, `charges` and `STACKING` into `hooks/contexts.py`, which is
  the whole effect model in the package content batches may never touch.
- **A registry keyed by combatant.** A global bus, which DESIGN.md § Levers
  refuses for dispatch and `core/timeline.py` refuses again for the reset
  ledger, and for the reason that applies here too: every application would pay
  a lookup the size of the encounter.
- **A `cast`.** An unchecked assertion in the one place the checker could have
  proved something, which is what this project's contract tests exist to avoid.
- **Identity against the event's participants.** Taken. `apply_pending()` is
  handed the creatures the caller is resolving between and matches each queued
  target against them.

It is complete for the two files that call it, and the reason is structural
rather than lucky: `engine/damage.py` dispatches to the defender only, and
`engine/attack.py` to the attacker and the defender, so every handler that can
queue during either event is owned by one of the two and can name no third
creature except through state of its own. An offer is the case where that stops
being true — its audience is the room — which is why `engine/triggers.py` keeps
its refusal and debt #18 stays `w2c`'s. The general answer is `w2c`'s roster and
is debt #30; until then a target that is neither participant is refused loudly
rather than dropped, which is the same bargain the refusal this task deleted was
making.

The `isinstance` in the drain is the second half of the same gap. The protocol
admits anything with a name — `tests/contract/test_triggers.py` queues a bare
`object()` through it — so what a store is handed is checked once, at the
boundary, and a `FeatureContractError` says which of the two `Effect`s the
caller meant. That is the narrowing position `ROADMAP.md` § Standing rules
allows, not a branch on what kind of effect something is.

### The store keeps the clock, because nothing hands it one

A `Duration` is relative and resolves at application time (`core/timeline.py`),
and the moment an effect is applied is almost always inside an attack: Reckless
Attack applies from `on_declare_attack`, Vex from `on_hit`. No attack context
carries a `TimeKey`, `resolve_attack()` is handed none, and DESIGN.md § Durations
nonetheless has Rage calling `refresh()` from three of those hooks and
"recomput[ing] against the current clock". So the clock has to be reachable from
the creature.

Passing `now` down through `resolve_attack()` and `resolve_damage()` was the
alternative, and it is a signature change to the pipeline `w1f` has just frozen,
on a path with no honest default — a `TimeKey` invented at the call site is a
wrong fight rather than a missing one. What the store holds instead is the last
clock it was *told*: `expire_before()` and `expire_after()` take `now` and record
it, and nothing else writes it. That is a copy of the one fact, written only by
the turn loop that owns it, and not the second authority `core/timeline.py`
refuses; `Duration.resolve()` reads only `now.round` and `now.initiative`, so a
stale phase between two boundaries cannot change an answer.

Until `w2c` calls the sweeps, nothing moves it: every store reads the top of
round 1 and every duration resolves against it. That is debt #29, and it is
stated in a test rather than left as a default nobody chose.

### Ending is the store's, never the effect's

`hooks/dispatch.py` rebuilds a creature's dispatch table on every attach and
detach, and attachment order is the last tiebreak in dispatch order (DESIGN.md
§ Ordering). An effect that attached or removed itself would be a second hand on
that table. So `EffectStore` owns both halves and goes through `Creature.attach()`
rather than the dispatcher, which is what makes the ownership check — the one
that stops a handler running for the wrong creature — cover an effect too.

`Effect.consume()` and `Effect.refresh()` therefore raise when the effect is
attached to nothing: both need the store, one for the creature and one for the
clock, and an effect holding a deadline it computed against nothing is the
silent half of the bug.

### The two hooks only an `EffectStore` can fire

`on_effect_applied` and `on_effect_expired` were in the closed registry from
`w0e` with no possible caller, and this is the task that builds the only object
that can call them — `EffectContext` was written for them and
`on_effect_applied`'s summary names Rage extending rather than stacking. A
registry that is closed and CI-diffed makes a hook nothing fires worse than a
missing one: the feature that declares a handler for it runs nothing and says
nothing, which is the failure the closure exists to prevent. The first cut of
this task shipped without them and the review caught it.

Both fire between the store's bookkeeping and the dispatcher's: **applied before
the attach, expired after the store has let go and before the detach**. Stated
the other way round, that is one rule — *an effect's own handlers run for
exactly the span it is attached* — and each half is independently right. It
cannot answer its own arrival, which is the snapshot rule `hooks/contexts.py`
states for queued effects and the reason Reckless Attack grants advantage
separately from applying its effect. It can answer its own ending, which is the
only moment it has left.

**What is already held is asked on both sides of the dispatch**, for the same
reason: a handler may dispel the incumbent, leaving a remembered one attached to
nothing that `refresh()` would raise on, or apply one of its own, leaving a
remembered `None` that would attach a second `REFRESH` effect of a class that
may hold only one. Asking again costs a scan of a handful of effects on a path
that is not the attack's.

**Letting go first is what makes a removal happen once**, and the review found
it the hard way. An `on_effect_expired` handler may remove something — a chain
ending with whatever anchored it, a dispel — and what it removes can be an
effect the same expiry sweep already has in its snapshot. Reaching that handler
with the effect still held fires its hook a second time and then dies inside
`list.remove`. So `remove()` settles the store's side before dispatching and
refuses an effect it does not hold with a sentence, and the sweep skips what a
handler took while it was running. The state a handler sees is therefore: the
creature no longer holds the effect, and the effect still answers.

**A veto is honoured on the application and refused on the expiry**, which is
the split `engine/attack.py` makes at `_seal()` and for the same reason. An
effect is "on its way onto a creature" until this hook has run — the phrase is
`hooks/contexts.py`'s own — so refusing one is what a feature immune to it does,
and `apply()` already had a return that says "nothing landed". An ending is a
position on the clock that has been passed; a feature meaning to keep an effect
alive refreshes it, and a veto there would be a mutator quietly doing nothing.
A `REJECT`ed application fires neither hook, because nothing happened.

Each dispatch is drained like any other (`apply_pending(ctx, holder)`), so a
handler that applies an effect on hearing about one is not the silent drop that
debt #5 was about.

**`ctx.effect` is the application, not what ends up in force**, and on a
`REFRESH` re-application those differ: the handler is handed the incoming object,
which is attached to nothing and discarded once the incumbent has absorbed it.
The fourth review round raised this and it is recorded rather than patched
(debt #31, `docs/workflow.md` § The review loop), because the two decisions above
make it structural — the hook fires before the state change so a veto can refuse
an effect, and the incumbent cannot be known before the dispatch because the
dispatch can dispel or create one. Firing after the change instead, and losing
the veto, is the alternative; it is a design choice for the milestone review, and
nothing declares a handler on this hook until W3 authoring does.

### `apply()` returns what is in force, rather than a boolean

`REJECT`'s entire content is the answer to "did anything happen", and `REFRESH`'s
is "the one already there". Returning the incumbent, or `None` when the
application was refused, says both without a second call — which is what lets
DESIGN.md § The scorer price a re-application of an active effect at zero with no
condition written anywhere. `STACK` is the one policy under which a creature
holds two of something, which is why it is the member an author picks
deliberately and `REFRESH` is the default.

`UNIQUE_PER_SOURCE` keys on the caster object rather than on a name, because
"one per ranger" is about a creature and two creatures can share a name. An
effect declaring that policy with no caster is refused at construction: the
alternative is a policy that silently behaves as `REFRESH` and merges two
rangers' marks in some later fight.

### The drain is per dispatch, not per attack

`hooks/contexts.py` says the engine "drains the queue after the dispatch", and
that is what this is: an effect applied at one stage of an attack is attached
for the stages after it, so a rider applied from `on_hit` contributes at
`on_damage_roll` of the hit that applied it. The review read the same paragraph
as scoping the drain to the whole attack, which is worth answering rather than
leaving to be rediscovered.

What the paragraph protects is the snapshot rule, and it is a rule about one
dispatch: the handler list is fixed per dispatch, so an effect applied during a
hook never runs in that hook. Reckless Attack still has to call
`grant_advantage()` beside applying its effect, because the dispatch its effect
would have answered is the one already running — the example holds under this
reading and does not distinguish it from the other.

Deferring to the end of the attack is refused on two counts beyond the sentence
itself. It would give `on_incoming_damage` two different meanings depending on
whether `resolve_damage()` was reached through an attack or called directly,
since the queue would be the attack's rather than the event's. And the pending
list lives on a context `_seal()` closes and `reopen()` clears, so accumulating
across an attack means a second queue outside the contexts — machinery for a
question the plans have already answered in one sentence.
`test_the_queue_is_drained_per_dispatch_and_not_per_attack` pins it.

One detail the review's own example got the wrong way round, and it is worth
recording because the authoring skill will want it: `on_damage_roll` is `SELF`
on the attacker, so a rider applied to the *defender* from `on_hit` has nothing
to contribute with there. The attacker is the only end this can be asked from.

### What it cost the benchmark

Eight paired runs on one container, alternating so drift cancels: **22.5k–22.9k
attack resolutions/sec/core before, 22.3k–22.8k after**, ratios 1.012, 0.986,
0.990, 0.995, 1.003, 0.999, 1.004, 0.975 — a median of **0.997, which is −0.3%,
against pair-to-pair ratios spanning −2.5% to +1.2%**. The cost is not distinguishable from zero, and
that is what the diff predicts: on the hot path the only change is
`refuse_pending(ctx)` becoming `apply_pending(ctx, attacker, defender)`, the same
empty-list early return with two more arguments pushed, plus one `EffectStore`
per creature at construction. The two effect hooks cost nothing here for the
same reason the sweep does — the spike applies no effect, so neither dispatches.
Startup is 0.132–0.139 s per worker on both sides, one module import apart.

The absolute number is well under the 30k budget on this container and says
nothing about it: `w0i` measured ~31.5k on a different machine, which is why
principle 10 asks for a paired attribution rather than a number.

**What is not in that measurement is the half the row expected to be larger.**
The per-boundary expiry sweep does not run, because there is no turn loop to
call it — `tools/spike.py` resolves attacks and walks, and nothing in it crosses
a turn boundary. So `w1c`'s attribution covers the drain and not the sweep, the
spike still answers `self.raging` from a stand-in flag (debt #15), and `w1k`
inherits an expiry sweep with no measured cost. Saying so is the point of
principle 10: an unattributed shortfall is the one nobody can act on.

### What this task deliberately did not do

- **Touch `engine/triggers.py`.** Its raise is debt #18 and `w2c`'s, and it is
  not the same debt one hook along after all — an offer has an audience rather
  than two participants, so it needs the roster this task did without.
- **Sweep anything.** `expire_before()` and `expire_after()` exist and are
  tested; the turn loop that calls them at every boundary is `w2c`'s, and a
  sweep called from somewhere else would be a second place the clock advances.
- **Model Vex's target key.** DESIGN.md § Durations describes Vex's charge as
  "keyed to a target id"; which target a charge is spendable against is the
  feature's state, and `w4` is where the feature exists to hold it.
- **Give an `Effect` a display name beside its id.** `HookSource.ID` is already
  what the trace, the damage breakdown and the no-op report call it, and the
  `Effect` protocol asks for one string. Two would be one more pair that can
  disagree.
- **Fire `on_effect_applied` for an effect the store already holds under
  `REJECT`.** Nothing happened, and a handler told about an event that did not
  take place is the same wrong number as one not told about an event that did.
- **Decide whether a veto unwinds what earlier handlers queued.** It does not,
  and it needed no ruling: `ctx.veto()` halts the chain, so anything queued was
  queued by a handler that had already run, and the dispatcher does not unwind
  that handler's other mutations or its spent `once_per` budget either.

Sources: DESIGN.md § Durations, § Structural prevention over testing, § Levers,
§ The scorer; `core/timeline.py`; `docs/decisions.md` § w0d, § w0g2, § w0g3,
§ w1a, § w1f, § The W1 critical-path review; `ROADMAP.md` § Sequencing
principles 10, § The W0 debt register rows #5, #15, #18, w1c, w1k, w2c.

### What the docstrings argued, recorded here by `w1l`

- **`UNSEATED` is one key shared by every creature not yet in initiative**: the
  roster that draws real keys is `w2c`'s, and until then a shared key says
  honestly that the order is undecided. `position` has no default at all, because
  a default there would make a silently wrong fight rather than an undecided one.
- **`apply_pending()` reads the private `ctx._pending`** rather than
  `ctx.pending_effects`, which builds a tuple: `resolve_damage()` calls it on
  every instance of damage, where the queue is almost always empty. The attack's
  call sites test the list and call only when there is something to apply,
  which is why the callers do not read alike.

## w1b — resources, and the three words DESIGN.md left to be read carefully

`ResourceSpec`, `Recovery`, `ResourcePool`, `SpellSlots` and `transaction()` are
the objects DESIGN.md § Resources names, and the seven shapes it tabulates all
fell out of one spec with no branch anywhere. What needed deciding was smaller
than that and in three places: what a *pool* is, which exception an overdrawn
one raises, and where the trigger half of a `Recovery` lives.

### A pool is one resource, so the ledger needed a name

DESIGN.md § Resources reads two ways in isolation — "the mutable `ResourcePool`
holds only the current count" could be the container — and the section settles
it two sentences later: spell slots are "nine pools `slot:1`…`slot:9`" and
`SpellSlots` is "a **Facade** over the nine level-tagged pools". Nine pools for
nine slot levels is a pool per resource, which leaves the per-creature container
unnamed in both documents. It is `Resources`, and `resources.transaction()` in
the same paragraph is the attribute rather than a class: `creature.resources` is
what § The authoring skill's fifth mutator family, `resources.spend`, is written
against.

That split decides where `on_resource_spent` is fired. A pool cannot know it is
inside a transaction, so it is a counter and `Resources` is the only thing that
announces — which is also the only arrangement in which the rule below is
expressible at all.

### The announcement belongs to the commit, not to the take

A transaction takes what it is asked for as it is asked for, because the second
`spend()` must see what the first one left — three 3rd-level slots asked of a
pool holding two is a refusal, and a ledger that only added up at the commit
would let it through. But it *announces* nothing until the `with` block exits
cleanly, and then once per pool carrying the committed total rather than once
per call. A spend that rolled back did not happen, and § w1c already records
what a handler told about an event that did not take place is worth.

The same sentence answers the veto. `on_resource_spent` is past tense — the
registry summary is "The owner spends a resource" — so by the time a handler
sees it the resource is gone, and a veto has no meaning and never will have one.
It is refused with `FeatureContractError`, which is `EffectStore._announce()`'s
split for `on_effect_expired` and `_seal()`'s for a late veto, reached the same
way (§ w1c, § w1f).

Nesting is refused rather than supported: an outer rollback would un-spend what
an inner commit had already announced, and the handler that read the count in
between has no way to find out. The refusal is the same first cut
`engine/attack.py` makes for a nested attack (debt #26) and names the same fix.

**While a transaction is open, `Resources.spend()` is refused too**, and the
first review round is what found it: guarding the nested transaction and the
un-entered one had left the third way round open. That method announces at once
and the rollback can only give back what the transaction took, so a direct spend
beside an open one survives the exception that un-spends everything else — the
half-paid ability, arrived at through the door rather than the window.
`SpellSlots.spend()` routes through the same refusal because it *is* that method,
which is the second reason `select()` is split out: it is how a slot reaches
`txn.spend()`.

The veto's refusal says where a free use *does* go, and the third review round is
why it has to. It had ended "a feature that means to make a spend free hands it
straight back", which is advice this task ships no way to take: there is no
public mutator that gives a resource back, by the choice recorded below, so the
only route the sentence left open was `pool.current += 1` — an unguarded slot
that would also push a pool above its maximum. A use that costs nothing is a
bigger `maximum` or another `Recovery`, declared with the rest of the resource,
and the message now says so.

### `EconomyError`, not a type of its own

`core/errors.py` called the action economy "the first thing in the project that
could be *overdrawn*" until `w1l` cut its docstring to its own code, and DESIGN.md § Failing loudly lists "resource going
negative" beside the reaction invariant in the same sentence of runtime
invariants. An overdrawn pool and an overdrawn economy are one mistake, so they
are one exception, widened to cover both rather than a sibling class added.

What is *not* an error is the distinction that made this cheap: `core/errors.py`
says in as many words that "a spell slot that is not available" is a fight
outcome. So `can_spend()` and `SpellSlots.count()` answer for nothing and
reaching the refusal means a caller decided not to ask — `creatures/economy.py`'s
argument exactly, and the reason the two refusals read alike.

The two refusals that do *not* read alike are deliberate. A pool that is empty
and a pool the build never granted are different mistakes: the first comes back
on a rest and the second is a feature whose resource nobody gave the creature,
which is a build error. `Resources.__getitem__` says which.

### `RecoveryTrigger` is rules vocabulary, so it lives in `rules/enums.py`

§ w0g's `ActionCost` bullet gives the rule: a word that `hooks/` and `creatures/`
both need, with no import between them, lives at the bottom of the stack. A rest
is an XPHB word, and `engine/adventuring_day.py` (w6a) and the stat-block parser
(w7a) will both speak it. Membership is DESIGN.md § Resources' seven shapes and
nothing more — the printed recharge ranges other than 5–6 arrive with `w7a`,
which is the first task that has the data to read them.

Nothing in `creatures/resources.py` rolls anything, and `RECHARGE_5_6` is why
that is worth saying: the d6 is rolled by whoever fires the trigger, so a pool
never reaches for a generator and `core/rng.py`'s single stream stays single.

### A slot level is a spec field, which is what lets Pact Magic be ordinary

`spell_slot_specs()` returns nine shared specs per progression whose `maximum` is
a function of the stat block, so a level-3 warlock and a level-20 one read their
different numbers off the same `pact:2` spec — the Flyweight claim made real
rather than asserted. Levels a progression never reaches are pools of 0 rather
than absent pools, so levelling up needs no pool added.

Pact slots are keyed apart (`pact:N`, not `slot:N`) because the key is all
`on_resource_spent` hands a handler, and a Pact slot is a different resource
from a spell slot — it comes back on a Short Rest — while `SpellSlots` sums
them, because "how many 3rd-level slots have I" is one
question. That is the whole of what the facade buys, and it is why
`rules/constants.py`'s promise that callers "never branch on the progression"
survives contact with Pact Magic.

`select()` is split from `spend()` for the transaction's sake: a slot is half of
Quickened Fireball's cost, and only the transaction may take it if the two are
to commit together.

### What it cost the benchmark

Eight paired runs on one container, alternating so drift cancels: **27.0k–29.7k
attack resolutions/sec/core before, 27.6k–30.6k after**, ratios 1.058, 1.030,
1.024, 0.965, 0.977, 0.982, 1.015, 0.938 — a median of **0.998, which is −0.2%,
against pair-to-pair ratios spanning −6.2% to +5.8%**. The cost is not
distinguishable from zero on a container this noisy, and it is what the diff
predicts: on the spike's path the only change is one empty `Resources` per
creature at construction, measured at **0.092 µs**, and nothing per attack.

**The pool arithmetic is not in that measurement, and this row is the one that
asked for it.** `tools/spike.py` answers `self.slots.highest` from a stand-in
flag — debt #15, `w1k`'s — so a paired run cannot reach the arithmetic however
it is run. Pricing it per operation instead is what lets `w1k` multiply by a
spend count rather than inherit an unmeasured cost. On the same container, median
of seven runs of 100k iterations each:

| Operation | Cost | As a share of one attack resolution (~35 µs here) |
|---|---|---|
| `can_spend()` | 0.095 µs | 0.3% |
| `pool._take()` alone | 0.146 µs | 0.4% |
| `spend()` — take and announce, no handler attached | 1.528 µs | 4.3% |
| `spells.count(3)` | 1.184 µs | 3.4% |
| `spells.highest()` | 1.378 µs | 3.9% |
| `spells.spend(3, allow_upcast=True)` | 3.077 µs | 8.8% |
| `transaction()` of two pools, committed | 3.836 µs | 10.9% |
| `recover()` across 11 pools | 2.202 µs | 6.3% |

The attribution inside `spend()` is the part worth keeping: **0.15 µs of it is
the ledger and 0.83 µs is constructing `ResourceSpentContext`**, with the rest
in the dispatch, the drain and `close()`. So the announcement costs ten times the
arithmetic, and the lever if this ever matters is the one `w0i` already pulled
for the attack — pool the context — rather than anything in the pools. Nobody
should pull it now: a creature spends a resource about once a round against
several attack resolutions, so at one slot per round this is ~4% of one
resolution added to a round that contains several, and `w1k` is where that
multiplication is done against a real scenario.

`recover()` is per rest rather than per round and is on nobody's hot path; it is
priced here only so that `w6a` does not have to.

### What this task deliberately did not do

- **Wire the spike's stand-ins to real pools.** Debt #15 names `w1k` for it, and
  a task that retired half of it would leave `w1k` totalling attributions against
  a spike that had already changed underneath them.
- **Fire both rest triggers on a long rest.** A feature recharging on a Short
  Rest recharges on a Long Rest too, and DESIGN.md § Resources' table writes
  Rage's two entries out rather than relying on an implication — so `recover()`
  matches the trigger it is given and nothing more. Who fires both is the day
  loop's, and saying so is debt **#32** rather than a rule invented here from a
  sentence nobody has quoted.
- **Charge the action economy inside a transaction.** Quickened Fireball costs a
  Bonus Action as well as its two resources, and the dispatcher is what spends
  that (`hooks/dispatch.py`). A unit of work spanning both would put the economy
  behind a second gate; DESIGN.md § Resources scopes `transaction()` to resources
  and the two ledgers stay separate.
- **Catch "spent during a legality query".** It is a named runtime invariant
  (DESIGN.md § Failing loudly) and there is no legality query yet — the APL that
  asks one is `w2e`'s, and the flag belongs to whatever asks rather than to the
  pool that answers.
- **Give `ResourcePool` a `give()` beside `_take()`.** Nothing hands a resource
  back outside a rollback and a recovery, both of which are in this file. A
  public mutator with no caller is a second way to write a count.
- **Read `maximum` from a class table.** DESIGN.md § Resources allows "a
  class-table column"; `table_value()` is `w3b`'s and the callable is the seam it
  will arrive through, already exercised by every level-scaled spec here.

Sources: DESIGN.md § Resources: one model, no special cases, § Failing loudly —
layered, § The authoring skill; XPHB (2024) "Spells" (Spell Slots, Pact Magic);
`docs/decisions.md` § w0i, § w1a, § w1c, § w1f, § The W1 critical-path review;
`ROADMAP.md` § Sequencing principles 10, § The W0 debt register rows #15, #26,
#32, w1b, w1k, w2e, w6a, w7a.

## w1d — the grid completed: a footprint, a line of sight, three templates

`w0g2` left `core/geometry.py` exact for a fight of Small and Medium
combatants on an open field and said so in as many words. What `w1d` owed it
was the three things that sentence excluded: a creature whose square is not one
square, an area of effect, and the rule that decides what an area reaches.
Nothing `w0g2` wrote was rewritten; `distance_ft`, `in_reach` and `route` grew a
size and kept their answers.

### A size is a footprint, and the anchor is its low corner

`Creature.position` was one cell and had to keep meaning something once a
creature covers four. The choice is between anchoring the footprint at a corner
and centring it, and centring is only defined for the odd widths: Large is 2x2
and Gargantuan 4x4, so "the centre square" does not exist for two of the four
sizes that need one. The anchor is therefore the low corner — cells run `+x` and
`+y` from `position` — which is defined for every size, keeps `position` the one
square a caller has to supply, and leaves "which corner" to whoever places the
creature.

`SIZE_WIDTH_CELLS` is in `rules/constants.py` rather than beside the code that
reads it, for the reason that file's own opener gives: the XPHB "Creature Size"
table is universal rules data, not per-class scaling, and it belongs with
`SIZE_ORDER` which is the same table read a different way. Tiny's 2.5 feet and
Medium's 5 both come back as one square, because two Tiny creatures sharing a
square is a placement rule and this is a distance table.

### Reach is measured between footprints, so nothing is added to a reach

The tempting shape is to let a big creature carry a bigger reach —
`Creature.reach_ft` returning the weapon's reach plus something for size. It is
wrong twice over: the rules give an ogre a 5-foot reach and a 10-foot *space*,
and a creature that added its size to its reach would then be compared against a
distance that had already accounted for the same squares. So `reach_ft` is still
the weapon's, and the arithmetic moved into `distance_ft`, where the gap on an
axis is zero wherever two footprints touch or overlap. An ogre anchored at
(0, 0) is 5 feet from a goblin at (2, 0), and the opportunity attack debt 9
named — the one a Large creature silently did not get — is now offered because
the one query everybody asks learned something an inlined Chebyshev could not
(`docs/decisions.md` § w0g3 argued exactly this would happen).

`engine/attack.py`'s `within_reach()` and `engine/triggers.py`'s audience both
pass the two sizes, because a valve nothing sets is debt 20's shape and this
task is not here to add another one. The broad-phase horizon in
`watch_movement()` did **not** need widening, which is the second look § w0g3
asked for: a footprint translates with its anchor, so a route of `n` squares
still moves every square of it by at most `5n` feet, and a bound computed from
footprint distances stays exact.

### One metric answers a radius as well as a reach

The question this task could have got wrong quietly: by what distance is a
Fireball's 20-foot radius measured. Chebyshev — the metric `w0g2` settled and
every other query here uses — makes a Sphere a 9x9 block of squares rather than
a disc, because a square four diagonal steps out *is* 20 feet away under the
rule that a diagonal step costs 5 feet. A Euclidean radius would draw a prettier
circle and would mean that "20 feet" named one set of squares to the attack
pipeline and a different one to the spell. That is the drift `in_reach` exists
to prevent, one subsystem along, so the template asks `distance_ft` like
everything else. The human confirmed it (§ w1d › The three questions,
closed). Overturning it would change the number of targets an area spell
catches, which lands on `w5a` and on the placement search in DESIGN.md § AoE.

The same argument settles the origin: a Sphere is centred on a *square*, not on
a grid intersection. Intersections are the DMG's optional template rule, the
same book and the same status as the 5-10-5 diagonal this project already
refuses, and a point of origin that is not a square cannot be the square a
creature is standing in.

### Line of sight: exact integers, and a graze does not block

An area does not reach a location no unblocked straight line from its point of
origin reaches (XPHB, Rules Glossary "Areas of Effect"), so a template cannot be
written without deciding what blocks a line. Three decisions:

- **Centre to centre, and the segment blocks on a square it passes through.**
  Grazing the corner of a *lone* obstruction does not block, which matters
  immediately: the diagonal from (0, 0) to (2, 2) passes through the corner of
  (1, 0), and the opposite rule would have every pillar cast a shadow along the
  diagonal beside it. A corner-to-corner visibility test would be the *Dungeon
  Master's Guide*'s cover variant, which is out for the reason the 5-10-5
  diagonal is out.
- **Two opaque squares meeting at that corner are a wall with no gap**, and the
  first cut of the graze rule got this wrong — it tested each opaque square on
  its own, so a diagonal wall leaked sight at every joint and a Fireball reached
  the far side of it. Raised by the review as a CONFIRMED finding and worth the
  code it costs even though nothing supplies `opaque` yet (debt #33): a diagonal
  wall is the first thing anyone builds out of walls, and "an obstruction must
  provide Total Cover to block the line" does not read as "unless you lay it out
  at 45 degrees". The rule is local — a square the segment touches at exactly
  one corner blocks when the square diagonally across that corner is opaque too
  — which is also what keeps the case apart from a segment running *along* a
  face, where the segment meets two of each square's corners rather than one and
  neither may claim the other.
- **The arithmetic is integer.** Squares are measured in half-cells, which puts
  every centre on an odd coordinate, and the two tests — the square's bands
  against the segment's, then the four corners against the segment's line — are
  multiplications of small integers. A float comparison would decide a diagonal
  graze by rounding, and a seeded run that reproduces byte for byte cannot have
  a Fireball's target list depend on that.
- **`opaque` is not `route()`'s `blocked`.** A creature's square may not be
  walked into and does not stop a line; a creature in the way is Cover, a
  different rule with a different answer, and `w2a` is where it arrives. Keeping
  them separate is what stops a wall of allies from blocking a spell the rules
  let it be cast through.

Nothing in the tree produces terrain, so `opaque` is empty at every call site
today — debt **#33**, owned by the milestone review, which is the gate that can
either put cover in the scenario spec or decide the parameter is not earning its
keep.

### A cone on a grid, and the width that can only be odd

A Cone's width at any point equals that point's distance from the point of
origin. A grid cannot draw that: a cone laid along a row of squares is symmetric
about that row, so its width in squares is always odd and the rule's even widths
round. **Rounding up** — `2 * lateral <= along`, one square at 5 feet, three at
10 and 15, five at 20 — makes the cone wider than the rule at every second step;
rounding down makes it narrower at every second step, and keeps a 10-foot
Burning Hands one square wide for its whole length. Up is the lesser error and
the one a grid cone is usually drawn with, and because it *is* a choice rather
than something the quoted sentence implies, it was a question beside the
sphere's metric until the human confirmed it. Its second review round is where that distinction was
caught: the first draft of the docstring presented the formula as following from
the rule. A diagonal cone is
generated in its own rotated frame rather than by filtering a box, so a cone
pointed at a corner is the same seven squares as one pointed along a row instead
of a staircase of single steps.

`toward` is a square rather than an angle. A creature chooses a direction by
pointing at something, every selector the APL can write returns a creature or a
square, and an arbitrary angle would be a second geometry that nothing on this
grid can act on. A `width_ft` on `line()` was left out for the same YAGNI
reason: every Line in the spell list this project imports is 5 feet wide, and
`w5a` is where one that is not would arrive.

### What it cost the benchmark

Priced as sequencing principle 10 asks, paired on one container, and the first
cut **failed** that pricing: eight pairs gave a median of **−3.5%** of the rate
(ratios −9.5% to +2.3%). The profile said why, deterministically and without the
noise: `distance_ft`'s cumulative time went from 21 ms to 74 ms over a
200-encounter run, and the run gained 133k Python calls (1.678M to 1.811M). Two
causes, both avoidable — the axis gap was a helper called twice per query, and
an `Enum` is hashed by a Python-level `__hash__`, so the two `SIZE_WIDTH_CELLS`
lookups cost more than the arithmetic they fed.

The fix is the one-square fast path at the top of `distance_ft`, taken on two
identity comparisons, plus folding the two axes into a single `max`. Fourteen
fresh pairs then gave a median of **+0.6%** — nothing, on a container whose
pair-to-pair ratios spanned −15.1% to +17.4% and whose baseline alone wandered
between 21.5k and 26.7k in one session. The number worth quoting to `w1k` is
therefore the deterministic one beside it: the same profiled run went from
1.678M calls to **1.632M**, 2.8% *fewer* than before this task, because
`max(across, -across, down, -down, 0)` replaced `max(abs(...), abs(...))` and
removed two builtin calls from the hottest query in the engine.
`distance_ft` 21 ms to 20 ms, `in_reach` 22 ms to 22 ms, `route` 17 ms to 20 ms
— the footprint clearance check, which is one call per candidate square and the
only part of the module that got dearer.

**So `w1d` adds nothing to the shortfall `w1k` is totalling**, and the lesson
generalises past this file: a default argument that reaches a dict keyed by an
`Enum` is not free on a path measured in tens of microseconds, and the fast path
that avoids it has to be pinned by a test against the general form — which is
`test_a_size_that_fits_in_one_square_takes_the_same_distance`, written against
Small precisely because Small takes the general path and must agree.

### What this task deliberately did not do

- **The APL's destination grammar is still computed from anchors.**
  `tools/spike_apl.py`'s `toward … until in_reach` picks the square to stand in
  by arithmetic on two positions, which is exact while both creatures are one
  square and off by one for a Large mover or target. It is a stand-in `w2e`
  replaces wholesale (debt 14), and the gap is debt **#34** rather than a patch
  to a file that is going away. Its selectors stay cell-based for the same
  reason.
- **No cover, and no Cylinder, Cube or Emanation.** Cover is a modifier on a
  roll rather than a question about the grid, and the three remaining 2024 area
  shapes have no caller until `w5a` lands the spells that use them. The row
  asked for a cone, a sphere and a line.
- **`route()` does not know a creature's speed, still.** Truncation is
  `steps_within()`'s and the turn loop's, unchanged by anything here.
- **Nothing charges movement against the economy** — debt 28, still `w2c`'s.

### The three questions, closed

`w1d` filed the sphere's metric, the footprint's anchor and the cone's rounding
as `docs/questions.md` #1–#3. The human closed all three on 2026-10-23,
confirming every choice, and judged them trivial: each was already settled by a
convention the project had (the one metric), by the only option defined for
every size (the low corner), or by being plainly the lesser error (rounding
up). They were never questions in the sense `docs/workflow.md` § Questions for
the human now gives the word, and the rows are gone. The reasoning above stands
as the record.

## The W1 mid-wave review

`w1c`, `w1b` and `w1d` landed between this gate and the critical-path one. The
model is § The W0 plan review: compare what the plan assumes against what the
wave measured. The headline is that **nothing measured moved a number the plan
assumes** — all three tasks priced themselves at or inside their container's
noise — so the projection over the budget stands untouched (§ The W1
critical-path review owns that number) and `w1k` is still the measurement that
settles it. What did move is what three completed tasks oblige
of the four rows behind them, and in one case the obligation was that the next
row could not be built from what it said.

### `w1g` was six words written before three of its dependencies existed

The row read "Saving throws, DC calculation, save-for-half" with a done-when of
"legendary resistance expressible". That was accurate when W1 was planned.
`w1b`, `w1c` and `w1e` have since landed, and between them they make three of
those deliverables concrete and one of them impossible with the API that exists.

**Save-for-half is the impossible one, and the failure mode is the one the
standards forbid by name.** A halving belongs in `w1e`'s `MitigationLedger`,
in the halvings step between the flat reductions and resistance. `ctx.halve()`
is the ledger's only writer, and it goes through `_acting_source()`, which
raises `PhaseError` with "a mutation with nobody to attribute it to is the
engine reaching into a context it has not entered" unless a handler is
mid-dispatch. `resolve_damage()` takes `amounts` and a `DamageContexts`, and
nothing else: there is no parameter through which a caller passes mitigation in.
So `engine/saves.py` has three routes and only one of them is open without a
decision:

- **Halve at the call site.** Open, wrong, and the route an agent working from
  the old row reaches for. `CODING_STANDARDS.md` § Boundaries names it — "a
  resistance check, halving or temp-HP subtraction anywhere else is a second
  ordering that will disagree with the canonical one" — and here it would
  disagree immediately rather than eventually: halving before the call puts
  save-for-half *ahead* of immunity and the flat reductions instead of in the
  halvings step, so a creature immune to the damage type takes half of nothing
  correctly and a Heavy Armor Master reduction applies to an already-halved
  number. `w1e` reordered this file precisely to stop that.
- **Have the spell attach a handler.** Expressible today and still wrong: a
  handler on `on_incoming_damage` fires for every instance of damage the target
  takes, so the halving has to carry a condition identifying its own event, and
  that is machinery for what is a property of one resolution.
- **Widen `resolve_damage()` to take caller-supplied mitigation.** The answer.
  It is the only route that leaves the resolution order in one file, and it
  composes exactly as `w1e` ruled: a save-for-half halving and a resistance are
  two steps rather than one, and two halvings stack because the "counts once"
  clause names Resistance.

The gate names the routes and the choice; it does not make the change. The
signature is `engine/damage.py`'s and `w1e` froze that file, so `w1g` settles it
and records it in a section of its own — which is the same split the
critical-path gate used when it corrected `w1c`'s done-when rather than
rewriting `refuse_pending()` itself.

The other three are cheaper and are the reason the row grew rather than
splitting:

- **Legendary Resistance is now an acceptance test rather than an aspiration.**
  `w1b` landed `ResourceSpec` and `Recovery`, so it is a `LONG_REST` pool plus one
  handler on `on_save_resolved` that spends a charge and flips the failure. The
  test worth writing is that `engine/saves.py` contains nothing about it.
- **The save result type is owed to `rules/enums.py`**, which already promises it
  here: `Outcome`'s docstring said it was scoped to attack rolls on purpose (§ w0b),
  because 2024 unifies the three D20 Tests but a natural 20 is a critical hit
  only for an attack roll, and "saving throws get their own result type with
  `engine/saves.py` (w1g)". A row that did not say so invites a fourth member on
  `Outcome`.
- **The registry needs nothing.** `on_forced_save` (the DC's side),
  `on_saving_throw` (the saver's) and `on_save_resolved` have been in the closed
  39 since `w0e`; `on_death_save` is `w1j`'s. Saying so is cheaper than an agent
  discovering it, and a `define_hook()` in this task would be a finding.

This is the second time a gate has found a stale done-when, and the diagnosis is
the same both times: the register is the index and the done-when is the
instruction, and three tasks landing between a row's writing and its execution
is enough to falsify it. The difference is that `w1c`'s row was falsified by one
task adding call sites, and this one by three tasks adding capability — which is
the ordinary case, not the exceptional one, for any row whose dependencies land
in the same wave.

### Nothing rolls a save-ends effect's repeat save

DESIGN.md § Buffs and debuffs writes out both series and says the vocabulary
carries the timing rather than assuming one, because `1/(1−p_fail)` for a save
at the end of each of the target's turns and `p_fail/(1−p_fail)` for one at the
start differ by a whole round, and getting the exponent wrong scores every
control spell wrong by a factor of `p`. `w3f`'s `Perturbation` declares it —
`repeat="turn_end"` or `"turn_start"` — for the **scorer**.

Nothing resolves it. `w1c`'s `Effect` carries `duration`, `charges` and
`caster`; its two turn-boundary sweeps, `expire_before()` and `expire_after()`,
read deadlines and nothing else; and no row in W1, W2 or W5 asks for a save at a
boundary. So the repeat save is the third missing piece, not a replacement for a
working one: no duration can last a minute yet (debt **#16**), and nothing calls
the sweeps that would end one (debt **#29**).

It is debt **#35**, and it is split the way sequencing principle 8 splits a
seam. The signature is cheap and lands now, in `w1g`, which is already adding a
save vocabulary: one enum for the timing, in `rules/enums.py`, read by both the
engine that rolls the repeat save and `w3f`'s profile key. Two string literals
in two packages that must agree is the estimator-and-resolver divergence
DESIGN.md says may never happen, one level up — and this is the level where it
is invisible, because the scorer's fit absorbs a timing error into a coefficient
and nothing goes red. The roll itself is `w2c`'s, which owns the boundaries that
call the sweeps; `w5a` is the first task that produces an effect needing one.

Found by asking question 5 rather than question 2: the obligation is not in any
decision record, because no completed task deferred it. It is a gap between
three documents that each assume another closes it.

### `w1k` was told to total numbers that cannot be added

Its row said it "totals attributions". Three of the five are now written and
they are not the same kind of thing:

Each figure below stays in the section named beside it; what this table records
is the *kind* of number, which is what decides whether it can be added to
another.

| Task | What kind of number it recorded | Commensurable? |
|---|---|---|
| `w1f` (§ w1f) | a share of the rate, paired, with part of it attributed to `_seal()` | yes — on the path the spike walks |
| `w1d` (§ w1d) | a Python call count over a fixed run, before and after | yes, and machine-independent |
| `w1b` (§ w1b) | a table of per-operation microsecond prices | no — the spike cannot reach the pools |
| `w1c` (§ w1c) | a share of the rate that expressly excludes the expiry sweep | no — nothing calls the sweep |

The two "no" rows are unreachable for **different** reasons, and conflating them
is the trap. `w1b`'s is debt #15: `tools/spike.py` answers `self.slots.highest`
from a stand-in flag, so wiring real pools in is what puts the arithmetic on the
path. That makes #15 a precondition of `w1k`'s measurement rather than a chore
beside it, and the row now says to retire it first.

`w1c`'s is not #15 and retiring #15 does not touch it. **The sweep has no caller
anywhere in `tools/`** — `expire_before()` and `expire_after()` are never
invoked — so wiring real pools and effects into the fixtures leaves it still
unrun. It is unmeasured for want of a call, not for want of somewhere to put
one: the spike already runs four turns a round, each calling
`resets.turn_started()`, and `_hero_turn` builds a real `TimeKey`. So wiring the
two sweeps at those boundaries is `w1k`'s, and the sweep's per-call cost is
`w1k`'s number.

What `w1k` cannot supply is the per-boundary *count*, because the spike's four
turns stand in for a roster `w2c` orders: the cost per call is priced here, the
multiplier belongs to the loop that knows how many boundaries a round has. That
split is the whole of it, and this gate's first two drafts of this paragraph both
got it wrong in the same direction — first by folding the sweep into debt #15,
then by declaring it unmeasurable before `w2c`. `tools/spike.py`'s own docstring
said "there is no turn loop here", which is true of an initiative-ordered loop
and was read as true of the boundaries; the boundaries exist, and only the calls
are missing. Both corrections came from the review.

That is also why the arithmetic is a multiplication rather than a sum: a
per-operation price times how often a real scenario performs it, which is what
`w1b`'s record already said and the row did not. Summing the four numbers above
gives a figure about nothing. And it is why the cross-check is worth stating,
since it is the only one available: the multiplication and the re-run should
agree, and where they do not, a stand-in has been wired wrong. A re-run that
merely produces a number has no way to know that.

### DESIGN.md over-promises the effect model by one field

§ Durations' Vex row describes the charge as "keyed to a target id". `w1c`
shipped `Effect.charges` as an `int | None` and ruled the key the feature's own
state — correctly, and argued in § w1c — but left the table saying otherwise. A
W4 author writing Vex reads the table, looks for the key on `Effect`, and either
re-litigates a settled decision or edits `creatures/` from a content batch,
which is the one thing a content batch may never do. The cell now points at the
ruling. This is question 4's shape with a small answer: the document was wrong,
not the code.

### The docstring cleanup is not queued here

A `w1l` row — cutting `src/` and `tests/` docstrings back to their own code —
was added to the queue in the commit that cleared the hold, and the sixth
verdict found it twice over. It is removed, and the reason is the gate's own
headline finding turned on the gate.

The row was not one of this review's findings. This section lists what came out
of re-reading the plan against `w1c`, `w1b` and `w1d`, and the cleanup is not
among them: it came from a standards change made on `main` in #43, which is
ordinary work rather than something the wave measured. `/plan-review` says in as
many words that **finding nothing is a valid outcome and that changes are not to
be manufactured to justify the gate**; writing a post-hoc argument into this
record for a row the gate did not derive is exactly that, and it would also be
the second copy of #43's reasoning rather than a pointer to it.

And the row could not be built from what it said, which is what `w1g`'s row was
held up for three rounds to fix. Its **Done when** cited
`CODING_STANDARDS.md` § Citations and comments "added on `main` by #43", and
that rule is on neither branch: #43 is open, and it targets `main` from a branch
that is not `milestone/*`, so `human-merge-required` fails on it by design and
**the loop may never merge it** (`docs/workflow.md` § Merge authority). Its
**Depends** cell was `—`. An agent taking the row would meet a done-when it
cannot check, against a section name that already exists on this branch and
means something narrower, and would have to guess which rule was meant.

None of that says the cleanup is wrong — #43's own "deliberately not done" asks
for it, and the measurement behind it is real — #43 counted the docstring lines
against the code lines in `src/` and in `core/timeline.py`, and the figures are
its, not this gate's, so they are not copied here. It says the
row belongs to whoever queues it **after** a human has merged #43 and the rule
has reached the milestone branch, because only then is its done-when checkable
and its **Depends** nameable. Queued before that it is a row that reads correct
and cannot be done, which is the failure mode this whole gate is about.

### What this gate did not change

- **The budget, and the projection over it.** Three tasks measured at or inside
  the noise, so nothing revises the projection (§ The W1 critical-path review)
  and nothing revises the 30k budget. The bar for revising the budget is a
  measurement with an attribution (DESIGN.md § Biggest risks), and this gate has
  neither.
- **The round cap.** Two of the three tasks reached it — `w1b` was held at the
  fourth non-clean verdict and resolved in a later session, `w1c` recorded debt
  #31 — and in both cases the reviewer was still finding real defects at the
  cap: `w1b`'s negative-pool bug and `w1c`'s `apply()` re-entrancy were CONFIRMED
  at rounds 4 and 3. That reads at first like a cap set too low. It is not what
  it is: `w1b`'s hold was cleared by a **fresh session** repeating the same loop
  with an empty context, which is the cap working as designed — what degrades
  across five rounds is the implementer's remaining context, not the reviewer's
  accuracy. Raising the cap would buy more rounds of a worse implementer. Left
  alone, and recorded so the next gate can see whether the rate holds.
  **This gate then hit the cap itself and did not converge**, which makes it
  three of the last four PRs. Every verdict it drew found something real: a
  drifted measurement and a `±` over an asymmetric range, a miscount, a
  misordered debt row, one ruling standing in three places, a retelling that had
  drifted from the record, a row that contradicted itself, a factual error in a
  debt row this gate added, an unargued task row that could not be built, and
  this bullet and `docs/questions.md` #4 both describing a PR that had moved on
  without them. An earlier draft of this bullet called that a converging review
  because the findings got smaller up to round four; rounds five onward falsified
  it, and the claim is withdrawn rather than re-asserted at a higher number —
  which is itself the lesson, since a snapshot of "how many rounds it took"
  written *during* the PR is a hostage to the rounds after it.
  Two things the next gate should take from it, neither of them about the cap's
  number. **A gate PR that edits the standards is judged by them**, so each new
  rule wants applying to the gate's own diff before the first push; four of the
  early findings were exactly that. And **narrative prose about a PR's own
  progress is a liability inside that PR** — "four verdicts", "no further round
  will be taken", "fixed and merged" were each true when written and each became
  a finding, because the document and the thing it describes were being edited in
  the same branch. Write the record about the *work*, and let the round count and
  the outcome be read off git and the PR. `docs/questions.md` #4 records the one
  judgement the cap's own text does not cover.
- **`w1j`'s and `w1i`'s rows.** Both were re-read. `w1j` says "healing
  application and temp HP", which `w1e`'s landing the temp-HP *spend* does not
  falsify — `on_temp_hp_gained` in the same cell makes clear it is the grant —
  and `w1i`'s pricing instruction is already the shape principle 10 now asks
  for.

### Retro

The environment axis, over the same three tasks (`.claude/skills/retro`). Eleven
findings closed across thirteen review rounds, three of them real bugs. Six
edits: one to the plan, four to the standards, and one pointer in `src/`.

- **Sequencing principle 10 now asks for a deterministic witness beside the
  paired rate** (`ROADMAP.md`). The protocol as written bought "not
  distinguishable from zero" three times out of three, against pair-to-pair
  ratios spanning −6.2% to +5.8% (§ w1b), −2.5% to +1.2% (§ w1c) and −15.1% to
  +17.4% (§ w1d) — eight to fourteen paired runs each. Every one of those ranges
  is asymmetric, which is why a `±` summary of them is wrong as well as
  unpointed: the review of this gate caught exactly that in its first draft. That is the right conclusion and it is not an attribution, which is what
  the principle exists to produce. What attributed anything was deterministic:
  `w1d`'s first cut cost 3.5% of the rate, and the profile named the cause
  without the noise (`distance_ft` 21 ms to 74 ms cumulative, 133k calls added —
  all four figures are § w1d's, quoted here and owned there),
  while the fix is quoted to `w1k` as a call count rather than a percentage
  because that number means the same thing on another machine. The principle now
  asks for the call count over a fixed run and the cProfile cumulative time of
  the functions the diff touched, before and after, with the rate kept as the
  check that the deterministic story is not missing off-path interpreter work.
  Where they disagree the deterministic one is the finding. This lands before
  `w1i` and `w1j`, the two remaining priced rows.
- **"Adding code is a citation change too"** (`CODING_STANDARDS.md`). The
  existing line covers deleting prose, which `w1e`'s review earned. Its
  complement was missing and cost three findings here: `creatures/__init__.py`
  still said the resources were unwritten and `tools/spike.py` said they did not
  exist (`w1b`, round 1, both CONFIRMED), and a comment in `engine/triggers.py`
  said two queries "are the same arithmetic today" four lines above a diff that
  had made it false (`w1d`, CONFIRMED). All three are prose about what does not
  exist, falsified by the task that makes it exist, in a file the author had no
  other reason to open.
- **"What a handler can change is re-read after the dispatch"**
  (`CODING_STANDARDS.md`). `w1c` shipped the same read-dispatch-use shape twice
  and the review confirmed both: `apply()` asked what was held before firing
  `on_effect_applied` and used the answer after, and `remove()` dispatched
  `on_effect_expired` before letting go. The second was a crash in
  `list.remove` and a doubled hook. `w1g`, `w1h`, `w1j` and `w2c` all dispatch
  and then act, so the rule is due before them rather than after.
- **"A count with an invariant has named writers"** (`CODING_STANDARDS.md`).
  `ResourcePool.current` shipped as a plain attribute, so `pool.current -= 5`
  left a pool at −2 past every guard on the spend path. It is the same argument
  that attribute-locks a context, applied to engine-owned state, and the
  standards said it only for contexts.
- **The citation rule extends to measurements** (`CODING_STANDARDS.md`). `w1c`'s
  median was −0.5% in the commit message, −0.3% in the record and a third figure
  in `CLAUDE.md`; reconciling the three was a review round. A number lives in the
  record that measured it and a copy carries the pointer, and a diff that changes
  one greps for the figure rather than the prose around it.

- **`tools/spike.py`'s docstring named `w2e`'s re-run and not `w1k`'s**
  (navigation). It is the file a task reads to learn what the benchmark leaves
  out, and the paragraph saying the spike "understates the eventual per-round
  cost" pointed only at the APL compiler's re-run. `w1k` is the task that totals
  what W1 spent and the one that retires debt #15, so it is named there now, and
  in the module's Sources line.

Nothing here is a check rather than a standard, which is worth saying since the
retro's split prefers checks. All four of the standards edits are judgement
calls over shapes a grep cannot distinguish from a correct use: prose about
another module, a read before a dispatch, a writable count, a quoted number. The
one mechanical candidate — grepping for a module name near "not written" —
would fire on every stand-in's own honest docstring, which is most of `tools/`.

No CI run went red on any of the three PRs, and no task reported difficulty
locating a ruling, so `CLAUDE.md`'s decision-record table gains no row — this
section is appended to the two that already exist, for planning a wave and for
pricing a change — and the hooks are left alone.

### Filing a seam is not cutting it, and "signature only" was unbuildable

Two spec findings after the pricing fix, both of this gate's own making and both
the same mistake in different clothes: a decision recorded in one place that the
places which have to honour it were never told about.

**Debt #35 obliged an enum and nothing else was told.** The row says `w1g`'s
timing enum is the one member read by both the engine that rolls the repeat save
and `w3f`'s `Perturbation.repeat`, "rather than two string literals that can
disagree" — and DESIGN.md § Buffs and debuffs and `w3f`'s row both still spelled
the field as `"turn_end"` / `"turn_start"`. A `w3f` agent closing the vocabulary
from its own row would have closed it on string literals, which is exactly the
divergence #35 exists to prevent, landing in the one row that owns the field.
Filing a debt is not cutting a seam: the row records the obligation, and the
rows that must honour it have to say so too. Both now point at #35, and #35
points back at them.

**And `w1g`'s scope cell was unbuildable a second time.** It read
"`engine/damage.py` for `resolve_damage()`'s signature only", which cannot do
what the row asks: `resolve_damage()` hands `mitigate()` nothing but
`incoming.mitigation`, and `MitigationLedger.halve()` raises outside a
dispatching handler, so a caller's halving has to change how the **body**
assembles what it passes on. The cell now says signature *and* that body, keeps
`mitigate()`'s order and `ctx.halve()`'s handler-only rule out of scope — which
is what keeps the row 👥 — and says that if seeding the ledger from the engine
side needs `hooks/contexts.py`, that sub-item is solo within the task, as
`w1a`'s `Combatant` and `w1f`'s `Weapon` widenings were.

That is the third time this row has been found unbuildable, each time by the
reviewer and each time after a fix of this gate's own. The pattern is worth more
than the three corrections: **a scope cell is a claim about what the task can
reach, and it is checkable against the code the task must change** — so it wants
reading against that code, not against the sentence above it. Nothing about
`w1g`'s intent moved across the three rounds; only the row's account of what it
may write.

### Widening `resolve_damage()` put `w1g` on the hot path, which the gate missed

Both holds were cleared by a fresh session, and the verdict after the second one
found the consequence of this gate's own `w1g` fix. Adding `engine/damage.py` to
that row's scope — so save-for-half can reach `w1e`'s ledger — means `w1g` now
changes a function `engine/attack.py` calls on **every** attack it resolves
(`:821` and `:845`). Sequencing principle 10, which this same gate amended two
sections above, says a task that touches the hot path prices itself; `w1g` was
not marked **Priced**, and `w1k`'s list of the tasks whose attributions it totals
still named five. So the gate created exactly the residual that its own `w1k`
correction is about: a cost on the measured path with nobody told to measure it.

`w1g` is **Priced** now, it is in the list and in `w1k`'s **Depends**, and the W1
section says why it joined them. The lesson is narrower than "re-read the
row": **a scope change is a pricing question**, because the hot path is a
property of the files a task may write and not of what the task is called. Three
of the five tasks the critical-path gate listed are on it for exactly that
reason.

Two citation findings came with it, both this gate's own rule turned on it a
fifth and sixth time, and both fixed by deleting rather than adding: `w1k`'s row
still carried a bare `5.5%` in the same row that says the figures live in § w1f
and § w1d and are "not restated here" — one copy was removed at `5339388` and
this one was read as the critical-path gate's and left — and debt #35 had copied
DESIGN.md § Buffs and debuffs' two geometric series and the factor-of-`p`
argument into the register, where a pointer does the work. The register tells a
task what it owes; it is not where the design's formulas live.

### Held a second time, on a two-word finding, deliberately

The eighth verdict came back clean on the standards axis — the first time in
this PR — with one PLAUSIBLE on spec, and it is correct. `w1g`'s row builds its
Legendary Resistance acceptance test on "a `ResourceSpec` with a **per-day**
`Recovery`", and there is no such trigger: `RecoveryTrigger` has `SHORT_REST`,
`LONG_REST` and `RECHARGE_5_6`, and its docstring closes membership to
"DESIGN.md § Resources' seven shapes and nothing beyond them". Legendary
Resistance's 3/Day recharges on a Long Rest, so the row should say `LONG_REST`.

**The fix is two words and the row is held anyway.** The hazard is worth naming
first, because it is the one thing here that could do damage: the commit before
this one added `rules/enums.py` to `w1g`'s packet allowlist, so a `w1g` agent
reading "per-day" has both the instruction to want a member that does not exist
and the write access to add one — to a closed rules vocabulary, which is the
boundary `CODING_STANDARDS.md` guards. The hold contains it, since nothing
advances off a held row.

The reason for holding rather than spending one more commit is the record above,
applied to this session. Eight verdicts, and every one found something; the
three before this were each created or revealed by the push before them. At
rounds four, five, six and seven the remaining work looked trivial each time and
each time it was not, so the honest conclusion is not that the work is nearly
done but that **this session's judgement of "nearly done" on this PR is
miscalibrated**, and a two-word fix is exactly the shape that judgement keeps
getting wrong. The round cap exists for that, `w1b`'s hold was cleared by a
fresh session in one commit, and this gate's own § What this gate did not change
argues that is the cap working. A session that argues that and then takes a
ninth round because the finding is small has not understood its own record.

So: the fix is stated here and in a comment on #42, the row is `held`, `w1g` is
back to `queued`, and the two words belong to whoever picks it up. Nothing else
on the PR is open — the standards axis is clean and the three CONFIRMED findings
of round seven are closed.

Closed in the commit after this hold: `w1g`'s row and the paragraph in
§ `w1g` was six words written before three of its dependencies existed now
say `LONG_REST`.

### Held at the round cap, with one CONFIRMED finding open

The fifth verdict found a factual error in debt row #35 itself, and it is
right: the row says that today "a save-ends effect ends on its duration … and a
Hypnotic Pattern holds for a minute", and neither half is true. `core/timeline.py`
offers three durations and all of them end one turn ahead — a minute is not
expressible at all, which is debt **#16** and `w5a`'s — and nothing calls the
expiry sweeps, which is debt **#29** and `w2c`'s, so an applied effect does not
expire on any deadline yet. The row therefore misstates the baseline the repeat
save is meant to fix, in the one cell a `w2c` or `w5a` agent would read to learn
what the current state is.

`docs/workflow.md` § The review loop sets the cap at three rounds and merges on
the fourth **only if every CONFIRMED finding is fixed**. One is not, so this is
the other branch: the `⏸` row goes to `held`, `w1g` goes back to `queued`
because no row is `next` while a hold stands, and the human is told. The fix is
one cell and is named here so that a fresh session closes it in a single commit
rather than re-deriving it: #35's "what it costs" column should state the
baseline **through #16 and #29** — a save-ends effect has no minute-long
duration to end on and nothing to expire it even if it had — so the repeat save
is the third missing piece rather than a replacement for a working one. Nothing
else in this PR is contested; the four earlier rounds are closed.

That the error sat in a row this gate added, and was found only at the fifth
look, is itself the retro's point about a gate PR being judged by the standards
it writes — recorded above rather than argued again here.

Closed in the commit after the hold: #35 and the paragraph above now state the
baseline through #16 and #29.

Sources: `docs/decisions.md` § w1b, § w1c, § w1d, § w1e, § w1f, § The W0 plan
review, § The W1 critical-path review; DESIGN.md § Durations, § Buffs and
debuffs, § Biggest risks; `ROADMAP.md` § Sequencing principles 8 and 10,
§ The W0 debt register rows #15, #31, #35; `CODING_STANDARDS.md` § Boundaries,
§ Citations and comments; PRs #31, #32, #41.

## w1g — saving throws, and the signature save-for-half needed

The row the mid-wave gate rewrote asked for four things: the DC, the roll, the
save result type, and save-for-half — which it had established could not be
written with the API that existed (§ The W1 mid-wave review names the three
routes and why two of them are wrong; they are not rebuilt here). The sequence
is `engine/saves.py`, the vocabulary is two enums in `rules/enums.py`, and the
fourth is one keyword on `resolve_damage()`.

### The signature, and where the halving is seeded

`resolve_damage()` takes `halved_by: str | None = None` — the *reason* a caller
halves, not a number and not a ledger. The body stamps the incoming context as
`engine.damage`, calls `incoming.halve(halved_by)` and releases the stamp, all
before `on_incoming_damage` is dispatched.

Three things decided that shape.

**A reason rather than a `Mitigation` object.** The only caller-side mitigation
the rules have is save-for-half; a reduction, a resistance or an immunity
arrives from a handler and already has a door. A parameter object would be the
Speculative Generality `CODING_STANDARDS.md` § Smell baseline names, and the
widening it would make cheap is the one nothing is asking for. The reason string
is not decoration: `MitigationLedger.halve()` stores `f"{source}: {reason}"`,
so what the ledger carries is `engine.damage: a successful saving throw halves
it` and `w1i`'s log gets the attribution without a second field.

**Seeded before the dispatch, not after.** The victim's handlers run against the
ledger the engine will actually apply, so Uncanny Dodge on a save-for-half hit
reads one halving already present and adds the second — which is what
`test_the_reason_reaches_the_ledger_before_the_victims_handlers_run` pins. After
the dispatch would be the same arithmetic and a lie to every handler that looks.

**Stamped because the ledger refuses an unattributed change.** `ctx.halve()`
goes through `_acting_source()`, which raises `PhaseError` unless a source is
stamped; `engine/attack.py` already seeds a context this way (`hit.stamp(
"engine.attack", None)`), so the engine naming itself is the established move
rather than a new one. The stamp is released immediately, because a stamp left
standing attributes whatever the dispatcher does next to the engine
(`hooks/contexts.py` on `stamp()`).

What this does *not* do is touch `mitigate()` or the order it runs: a halving
from the caller and a halving from a handler are the same entry in the same
list, and two of them stack for the reason `w1e` gives. `ctx.halve()` stays
handler-only.

`engine/saves.py`'s `save_for_half()` is the one place the condition is
written — half on a success, full on a failure — so no spell in `w5a` writes
`if saved` beside a damage call, which is how a second halving rule would get
in.

**And the case that catches a call-site halving is narrower than it looks.** The
independent re-derivation CLAUDE.md § Working rules asks for built the
counterfactual and ran it through the real engine: halving `amounts` per type at
the call site gives the *same* number as the ledger on an immunity, on a
resistance, on two halvings and on an evenly-split mixed hit. It differs on a
flat reduction (9 against 4), on the ledger the victim's handlers read, and on an
odd mixed hit, where one halving of the total is not two halvings of the parts —
7 fire and 7 cold against fire resistance is 5 through the ledger and 4 at the
call site. Those are the tests that defend the signature; the others defend the
steps around it.

### Two enums, and why the result type is not a boolean

`SaveOutcome` has two members and no critical one. § w0b's `Outcome` bullet has
promised this from the start and gives the reason: 2024 unifies the three D20 Tests,
and a natural 20 is a Critical Hit on an attack roll only. The corollary is the
trap in this file and the two tests written against it — **a natural 20 does not
succeed on a save and a natural 1 does not fail one.** XPHB's "Rolling 20 or 1"
says the special effects are the ones the rules for that test specify, and the
Saving Throws entry specifies none; `rules/constants.py`'s `NATURAL_TWENTY`
already said the two rolls it names are the attack's.

An enum rather than `succeeded: bool` because the vocabulary is enums for the
reason `rules/enums.py` opens with — the member serialises into the log and the
`SimResult` JSON, and `content/` parses it back — and because `SaveResult` is
what `w5a`'s spells and `w8a`'s estimator will pass around. (This named `w1h`'s
conditions as a third reader and they turned out not to be one: the four that
touch a save do it by failing one automatically, which is debt #37 and cannot be
written at all — § w1h.) `SaveResolvedContext.succeeded` stays a `bool`: it is one question asked
of one live event inside a frozen package, and widening it would be a
`hooks/contexts.py` change with nothing to buy.

`SaveRepeat` is debt **#35**'s declared half — `TURN_END` and `TURN_START`, the
two members `w2c`'s roll and `w3f`'s `Perturbation.repeat` have to agree on.
Nothing reads it yet, which is the point: declared after W3 it is a retrofit
across every control spell in `spells/` (`ROADMAP.md` § Sequencing principles,
8). The two `E[N]` formulas it selects between are DESIGN.md § Buffs and
debuffs' and are not copied into the enum.

### The DC, and the bonus a save adds

`save_dc()` is `SPELL_SAVE_DC_BASE + proficiency + modifier`, and the 8 is in
`rules/constants.py` rather than beside the function that uses it — a fourth
file, named here because the row's allowlist named three. The argument is
`RecoveryTrigger`'s: a W4 class feature that sets a DC and `w7a`'s stat-block
parser need the same number and neither may import `engine/`. The ability is the
imposer's and is passed in, because nothing on a `Creature` yet says which
ability it casts with, and a monster's DC is printed rather than derived.

`save_bonus()` is a function for one reason and it is not line count: DESIGN.md
§ The estimator and the resolver may never diverge means `w8a` has to compute
`p_fail` from the number this module actually adds to the d20. One function is
what makes that checkable; the expression inlined into `saving_throw()` would
invite the estimator to re-derive it, which is the divergence a fitted
coefficient hides.

It returns the bare ability modifier. **No creature can declare proficiency in a
save yet** — `StatBlock` has no field for one, a PC's comes from a class (W4) and
a monster's is printed (`w7a`) — so the proficiency bonus the rules add for a
proficient save is debt **#36**, owned by `w2f`, which is where equipment and
build facts are assembled into a creature. It is exact for every creature that
exists today and wrong for the first one that is proficient.

### A veto is refused at all three hooks

`_settle()` raises `FeatureContractError` if any of the three dispatches vetoed,
for the reason `engine/attack.py` refuses one past the declaration: `SaveOutcome`
has no member for a save that did not happen, so honouring a veto would leave
`saving_throw()` returning something arbitrary. The message names the three
things a feature that wants to change a save does instead.

It runs **before any die is drawn**, which is the order `engine/attack.py`
settles its declaration in and not the order this file first had: a save that is
about to be refused should not have rolled a handler's `1d4` first. Nothing
observable turned on it — the raise abandons the run — but the asymmetry with the
one precedent in the tree was the kind that gets copied.

`_settle()` and `_modifier_total()` are each a second copy of a private helper in
`engine/attack.py`, deliberately. Extracting either means editing a file this
task's scope excludes, and on the attack's side it would put a cross-module call
on the hot path that `w1f` spent 5.5% of the rate completing. What is duplicated
is also not the rule: advantage cancellation lives once, in `AdvantageLedger`,
and both files read `had_advantage` off it — the copies are `max`/`min` of two
rolls and a loop over a bag. If `w1j` writes a third, that is the third occurrence
and the moment to extract one.

### Legendary Resistance, which this file has never heard of

The row's acceptance test. The feature is a `ResourceSpec` with a `LONG_REST`
`Recovery` and one handler on `on_save_resolved` that spends a charge and calls
`ctx.succeed()`; `engine/saves.py` has no branch for it, which
`test_the_saving_throw_sequence_knows_nothing_about_legendary_resistance` asserts
by reading the module's own source for the words. The roll still happened and
`SaveResult.total` still says what it was, so the log can show a 5 that became a
success and `forced_by` says who insisted. `forced_by` carries every claim a
handler made, including one made on a save the dice had already won: the ledger
behind it logs claims rather than what changed the outcome, which is the
property `OverrideLedger` was written for — two features surviving the same hit
leave two citations instead of racing (`hooks/contexts.py`).

That it works is a statement about `w1b` and `w1c` as much as this task: the pool
is `w1b`'s, the drain that lands an effect queued from a save hook is `w1c`'s,
and this file supplied neither.

Writing it turned up one shape worth passing to `w3e`'s paper pass: the feature
captures its owner's `Resources` at construction, because `self.owner` is a
`Combatant` and `w1a` deliberately kept the protocol to a name, a seat and the
three hit-point numbers. Every W4 feature that spends a resource will do the same
or want the protocol widened, and the pass is where that is decided rather than
here.

### What it cost the benchmark

Priced as sequencing principle 10 asks, with the deterministic witness first.

**The witness says the diff adds no call.** Over the fixed 200-encounter profiled
run, `resolve_damage()` is entered 3,934 times before and after, `mitigate()`
3,934 times, and the whole run's Python call count is drawn from the same two
values in both arms (1,483,283 and 1,505,799). What the diff adds to the hot path
is one `is not None` test per instance of damage, on a path that was already
there — there is no new call to count, which principle 10 names as itself the
answer.

**That the count has two values at all is the finding worth passing to `w1k`.**
The 22,516-call difference is one row of the profile, an `__init__` compiled to
`<string>` — a dataclass-generated constructor — while every named function's
count, every rolled die and the fight's outcome are identical across the two
modes and across three `PYTHONHASHSEED`s. It is a cProfile keying artefact rather
than work, and what it costs is resolution: a call-count delta smaller than
~22.5k on this harness needs the arms separated by a fixed-seed run each, not one
pair.

**The rate found the harness before it found the diff.** Eight pairs run
baseline-then-change gave a median of **−3.0%** (ratios −9.3% to +4.3%) — close
enough to the −3.5% that failed `w1d`'s first cut to be worth a second
experiment. Eight more with the order *reversed* gave **+1.5%** (−7.8% to +5.6%).
The within-pair order is worth about 3% on this container, so a protocol that
always runs one arm first measures the order; pooled over all sixteen pairs the
median is **−1.5%** with a span of −9.3% to +5.6%, on a baseline that wandered
between 24.9k and 28.6k in one session. Which is "not distinguishable from zero"
for the fourth time running, and the reason principle 10 asks for the witness.

**So `w1g` adds nothing to the shortfall `w1k` is totalling**, and it hands `w1k`
one amendment to the method rather than a number: alternate the arms within the
sixteen pairs, because four tasks have now paid for paired runs and the one that
reversed the order found ±3% of pure harness.

### What this task deliberately did not do

- **No auto-failed save.** Paralyzed, Stunned and Unconscious all fail Strength
  and Dexterity saves automatically, and `SaveResolvedContext` has `succeed()`
  with no counterpart — so a condition cannot express one. It is debt **#37**,
  `w3f`'s via `w3e`, and `w1h` is the first task that meets it. Adding `fail()`
  here would be a `hooks/contexts.py` change that also has to rule on what a
  forced success and a forced failure on one save mean, and that ruling has no
  quoted sentence behind it: Legendary Resistance says the creature may succeed
  instead of failing, which reads as beating an auto-fail, but reading it is not
  the same as the rules saying so. Blocked and visible beats a ruling invented in
  the wrong task.
- **No pooled or Strategy-supplied save contexts.** `engine/damage.py` and
  `engine/attack.py` both take a context source because the spike resolves
  millions of attacks; nothing saves on the hot path, and `w0i`'s own argument
  for pooling was a measurement rather than a principle.
- **No ability checks.** `on_ability_check` still has no caller. Nothing before
  `w2b`'s actions rolls one — a combat simulator rolls no skill check — so a
  caller invented here would be a third hook fired by nobody rather than a debt.
- **Nothing rolls the repeat save.** The enum is declared and `w2c` owns the
  boundaries that call it (debt #35, now half discharged).
- **Nothing was added to `hooks/contexts.py` but two sentences.** The seeding the
  row warned might need that package did not: the engine stamps itself and calls
  the mutator that already exists. Two docstrings there were corrected because
  the diff falsified them (`CODING_STANDARDS.md` § Citations and comments), and
  one shape is left alone — `SaveResolvedContext` carries no `imposed_by`, so an
  `on_save_resolved` handler cannot tell who forced the save where an
  `on_saving_throw` one can. Nothing needs it yet; `w3e` is where a feature that
  does would surface it.
- **No concentration check and no death save.** The first is `w5a`'s, the second
  `w1j`'s, and both are D20 Tests this module could plausibly have grown a
  function for; neither has a caller yet.

### What the review's first round found

Four citation findings and no spec findings, all on prose this diff added, and
one of them is worth recording rather than just fixing: **the "Not here:"
paragraph that `engine/damage.py` and `engine/attack.py` both open with is not a
style a new module may copy.** `CODING_STANDARDS.md` § Citations and comments
bans a list of what is deliberately absent and a claim about another task in a
docstring, and exempts only the docstrings already in `src/` — so the two
sibling files keep theirs as a cleanup task's and this one points here instead.
The other three were the same rule in smaller places: a design argument in
`save_bonus()`, future consumers in `SPELL_SAVE_DC_BASE`, and a `SaveResult`
docstring that described `forced_by` as holding only successes the dice did not
produce, which it does not. `SaveRepeat`'s docstring was trimmed on the same
standard before a later round reached it.

Sources: XPHB (2024) "D20 Tests" -> Saving Throws and "Rolling 20 or 1",
"Spells" -> Spell Save DC, "Damage and Healing", Rules Glossary "Round Down";
DESIGN.md § Buffs and debuffs, § The estimator and the resolver may never
diverge, § Structural prevention; `docs/decisions.md` § w1e, § w1f, § w1b,
§ w1c, § w1d, § The W1 mid-wave review; `ROADMAP.md` § Sequencing principles 8
and 10, § The W0 debt register rows #35, #36, #37; `CODING_STANDARDS.md`
§ Boundaries, § Smell baseline.

### What the docstrings argued, recorded here by `w1l`

- **`on_forced_save` fires once for the whole set of targets**, not once per
  target, because the DC is one number for the area and a bonus to it applies to
  every save rolled against it.
- **Evasion is not on `on_save_resolved`**: turning a failed save into half
  damage is a damage-side change and belongs to `on_incoming_damage`.
  `SaveResolvedContext.succeed()` is Legendary Resistance — the roll failed and
  the creature declares a success anyway.

## w1h — the 15 conditions, and the clauses that have nowhere to go

The row asked for the 15 combat-relevant conditions as `Effect` subclasses,
with "no `if creature.is_prone` anywhere in the engine" as the acceptance test.
The engine side of that was already won: `w0g2` through `w1g` built the
pipeline without a single condition branch, so this task adds `conditions/` and
changes nothing above it. What it actually decided is how a condition composes,
where a clause that cannot be written goes, and which clauses those are.

### A condition is an `Effect`, and the composed four are subclasses

Four of the fifteen say "You have the Incapacitated condition" in their own
rules text, and Unconscious says "You have the Incapacitated and Prone
conditions". Inheritance expresses that with machinery that already exists:
`HookSource._collect()` walks the MRO base-first, so `Stunned(Incapacitated)`
carries Incapacitated's handlers at Incapacitated's position in dispatch order
and adds its own after them, and `HookSource.__init_subclass__` re-validates
every inherited declaration in the subclass that uses it (`hooks/source.py`).

The alternative — applying Incapacitated as a second effect when Paralyzed
lands — cannot be undone: an effect may queue an application through
`ctx.apply_effect()` but nothing can *remove* another effect, so Paralyzed
expiring would leave an Incapacitated behind that the rules do not grant. The
one place the rules *do* ask for that shape is Unconscious's "When this
condition ends, you remain Prone", and there it is written as what it is, an
`on_effect_expired` handler applying a real `Prone`.

Two consequences of inheritance are worth knowing before touching this package:

- **Handler names must not collide inside one MRO.** `_collect()` keys on the
  method name, so a subclass method sharing a base method's name either
  replaces it (with `@hook`) or raises (without). That is why Prone's two
  handlers are `prone_own_attacks` and `prone_incoming_attacks` and
  Unconscious's is `unconscious_incoming_attacks`: a plain `incoming_attacks`
  on both would have dropped one of them silently in the class that has both.
- **Unconscious declares its own Advantage grant beside Prone's.** The two read
  alike today because Prone's incoming clause is approximated as unconditional
  Advantage (below). They stop reading alike the moment that approximation is
  fixed, and then the rules answer is both at once: Advantage from Unconscious
  and Disadvantage from Prone against a shooter beyond five feet, which the
  `AdvantageLedger` cancels to a straight roll. Inheriting Prone's grant alone
  would have made an unconscious creature *harder* to shoot than a standing
  one.

### The two escape valves are tuples, and they accumulate

`ROADMAP.md` § Standing rules, 2 gives a feature one `NEEDS_HOOK` string and
one `APPROXIMATED` string. A condition is a list of clauses — Petrified has
seven — and one of them failing says nothing about the other six, so both are
`tuple[str, ...]` here and each entry names one clause.
`Condition.__init_subclass__` unions them down the MRO for the same reason
`HANDLERS` is unioned: a composed condition inherits its base's handlers, so it
inherits its base's gaps, and a subclass declaring its own would otherwise
report itself whole.

Nothing in `conditions/` reports a gap in prose. Every entry is data a
collection test reads, which is what `CLAUDE.md` § Architecture promises ("a
collection test lists them") and what keeps the count honest: sixteen
`NEEDS_HOOK` entries, each quoting the clause it could not write, and four
`APPROXIMATED` ones. The sixteen reduce to **eleven** distinct structural gaps
once the clauses that want the same missing thing are merged — six conditions
want a Speed that can be changed and four a save that can be failed — and
Petrified's `APPROXIMATED` entry is a twelfth. Every
entry names the register row that owns it, so the register is the record and
the tuple is the pointer: rows #28 and #37 were already open, #38, #39, #40,
#41, #44, #45, #46, #47, #48 and #49 are new, and two further gaps that are not
clause-level — #42 and #43 below — make twelve new rows in all. (#21 and #27
are each the same gap seen from another file and are cited from the new row
rather than duplicated into it.)

### Petrified is the fourth condition that cannot save, not the third

The row and debt #37 both name three — Paralyzed, Stunned and Unconscious.
XPHB, Rules Glossary "Petrified" carries the same sentence: "You automatically
fail Strength and Dexterity saving throws". So four of the fifteen declare #37,
and the row's count and the register's are corrected in this PR rather than
through a spec PR (`docs/workflow.md` § When the spec is wrong): what the row
*asked* for is implementable exactly as written — declare the gap, do not
approximate it — and only the number of conditions doing so was wrong. A spec
PR would have spent a loop cycle to change a numeral that the quoted sentence
settles.

### Exhaustion: the level is the charge count

Exhaustion is the glossary's own exception to "A condition doesn't stack with
itself", and neither `Stacking` member expresses it. `REFRESH` overwrites
`charges` from the arriving effect, so a second application would set the level
back to 1; `STACK` holds two independent effects, so one Long Rest would remove
every level at once because each instance would consume its own. So the
incumbent counts the arrival itself: an `on_effect_applied` handler at
`Phase.EARLY` increments its own level and vetoes the newcomer, which is the
hook's stated purpose ("Rage extending rather than stacking",
`hooks/definitions.py`) rather than a trick played on it.

The level lives in `Effect.charges`. A count that ends the effect when it runs
out is what `charges` *is*, and `Effect.consume()` is the only public way for
something to end itself — "Finishing a Long Rest removes 1 of your Exhaustion
levels. When your Exhaustion level reaches 0, the condition ends" is then one
call, with the store doing the removal as `creatures/effects.py` insists. A
`level` field of its own would have needed `self._store.remove(self)`, which is
private access from outside the package.

`-2` per level is `PER_LEVEL_D20_PENALTY`, contributed through `ctx.bonus` on
all six D20 Test hooks. Three of those six have no engine caller yet
(`on_ability_check`, `on_death_save`, `on_concentration_check`), and the
handlers are declared anyway: the rule is written about the category, the
registry's six `D20TestContext` hooks *are* the category, and a handler added
when the caller lands is a retrofit that only the author of this file would
know was owed (sequencing principle 8's shape). `exhaustion.py` is the one file
in the package over 60 lines for that reason — eight handlers, none of which is
more than three lines.

### Prone is approximated; the four save conditions are not

The two calls look alike and went opposite ways, so the difference is the thing
to record. Prone's "An attack roll against you has Advantage if the attacker is
within 5 feet of you. Otherwise, that attack roll has Disadvantage" needs the
attacker's distance, which no context carries and `Combatant` cannot be asked
for. Both arms of that clause are *expressible*; what is missing is the datum
that chooses between them. So it is `APPROXIMATED` on the melee arm, because
Prone exists in this simulator to be applied by the Topple mastery and by a
shove, and a Prone that did nothing to incoming attacks would score Topple as a
pure cost — silently, inside a fitted coefficient, which is the failure mode
DESIGN.md § The estimator and the resolver may never diverge is about.

The automatically failed save has no arm to pick: `SaveResolvedContext` has
`succeed()` and no counterpart, so the outcome cannot be written at all, and
`w1g` has already established that adding one is a ruling rather than a mutator
(§ w1g). `NEEDS_HOOK`, as the row asked. The same test separates Frightened's
line-of-sight gate (approximated — the clause has one arm and the gate only
narrows when it applies) from its "You can't willingly move closer to the
source of fear" (declared — nothing refuses a route).

### A second source of a sourced condition is lost

Charmed, Frightened and Grappled are written about the creature that imposed
them, which is `Effect.caster`; `SourcedCondition` requires it, because a
`None` there makes the clause skip silently rather than fail. Under `REFRESH` a
second Frightened — a second dragon — slides the incumbent's deadline and keeps
the *first* dragon as the source, since `EffectStore.apply()` copies `duration`
and `charges` onto the incumbent and not `caster`. That is the glossary's "a
recipient either has a condition or doesn't" taken at its word, and the loss is
that one of the two sources is forgotten. `UNIQUE_PER_SOURCE` would hold two
Frightened conditions at once, which the same sentence forbids. Debt #43.

### Petrified's immunity is approximated, not written

"You have Immunity to the Poisoned condition" is a veto on Poisoned's arrival,
which is exact only when Petrified comes first. A creature Poisoned *before* it
is Petrified keeps Poisoned's handlers running, where XPHB, Rules Glossary
"Immunity" says an immune condition doesn't affect you. The review of this PR
found it, and it is declared rather than closed for two reasons.

- **Removing the held Poisoned would be wrong in the other direction.** The
  immunity lasts as long as Petrified does. A Poisoned with an hour left that
  outlasts a short petrification should affect the creature again afterwards,
  and removing it would cure it instead.
- **Suppressing it needs something the store does not have.** Petrified cannot
  answer its own arrival (`EffectStore._announce()`'s snapshot rule), so the
  only code that sees both effects is Poisoned's own `on_effect_applied` and
  `on_effect_expired`. Writing it there would make Poisoned keep a list of the
  conditions that grant immunity to it. The general fix is to switch a held
  effect's handlers off and back on, and that belongs in `creatures/effects.py`,
  outside this task's file set.

Nothing applies a condition through the engine yet (#42), so the loss is
invisible until something does. Debt #49.

### The two condition hooks still have no caller

`on_condition_gained` and `on_condition_lost` have been in the closed registry
since `w0e` and nothing fires them, which this task does not change. The
announcement belongs beside `on_effect_applied`, in `EffectStore`, and the
store holds `Effect`s: telling a condition from an effect there means reading a
field `Effect` does not have, which is a `creatures/effects.py` change this row
does not authorise and `conditions/` may not make. It is the same shape as
`w1c` wiring the two effect hooks that `w0e` had declared with no possible
caller — one package along. Debt #42.

### What the independent test pass found

`CLAUDE.md` § Working rules keeps the author of a change away from its tests,
and on this task that bought two findings and no behavioural disagreement —
every clause's arithmetic matched what the glossary gives, on the first run.

- **Unconscious's "you drop whatever you're holding" was silently absent.** It
  had been filed with the out-of-combat clauses below on the grounds that
  nothing carries an inventory, which is the wrong reading: the clause has a
  combat consequence and `Creature.weapon` exists, so it is *nearly*
  expressible and the honest answer is a declaration. It is now `NEEDS_HOOK`
  and debt #48. The three clauses still undeclared are the ones below, and the
  review named each of them, which is what makes leaving them out a decision
  rather than an oversight.
- **`Exhaustion.level` answered 0 for a state that cannot happen.**
  `Effect.charges` is `int | None` and Exhaustion's `__init__` always sets it,
  so the narrowing was unreachable — and answering 0 would have reached
  `ModifierBag.add(flat=0)`, which raises two frames away about an empty
  contribution. It raises `EngineAssemblyError` here instead, naming the real
  fault (DESIGN.md § Failing loudly).

Two of the pass's own derivations are worth keeping rather than re-deriving.
The breadth of Exhaustion's D20 Test coverage is asserted **off the hook
registry** — every spec whose context is a `D20TestContext`, less the
attacker's side of an incoming attack — so a sixth D20 Test hook cannot land
uncovered, which is the only way `on_death_save` and `on_concentration_check`
are reachable at all before `w1j` and `w5a`. And the row's acceptance test
**tokenises** the engine's sources rather than scanning them for substrings:
five files in `engine/`, `hooks/` and `core/` discuss conditions in prose, so a
substring scan fails on the documentation it is supposed to be ignoring.

The second review found what the first had not: **Initiative is an ability
check** (XPHB, Rules Glossary "Initiative": a Dexterity check), and
`on_initiative_roll` is dispatched instead of `on_ability_check`, not as well
as it. So Poisoned and Frightened, whose Disadvantage covers "ability checks",
hook both, the same way Exhaustion does.

### What it cost the benchmark

Nothing, and it was not priced. `ROADMAP.md` § Sequencing principles, 10 names
`w1b`, `w1c`, `w1d`, `w1i` and `w1j` as the W1 rows that run the spike paired;
`w1h` is not one of them and the reason holds on inspection: no file on the hot
path is touched, and `tools/spike_features.py` is a fixed configuration of
twenty-five fixtures that this package does not join. A condition attached to a
creature costs exactly what any other `HookSource` costs, which `w0i` measured.

### What this task deliberately did not do

- **Nothing outside `conditions/`.** Every clause that wants a mutator, a field
  or a caller in `hooks/` or `creatures/` is declared instead (#28, #37–#47).
  The one that was tempting is `SaveResolvedContext.fail()`, which four
  conditions need and `w1g` had already refused for the right reason.
- **No condition queries anything about another creature.** Grappled's "the
  condition ends if the grappler has the Incapacitated condition" (XPHB,
  "Grappling") and Invisible's "unless the effect's creator can somehow see
  you" both need to ask a second creature what it is carrying, and `Combatant`
  is a read-only view with no store behind it — the same wall `apply_pending()`
  hit in `w1c` and the roster `w2c` owns answers.
- **No out-of-combat clause.** Charmed's social Advantage, Incapacitated's "You
  can't speak", Petrified's weight and ageing, and Unconscious's "You're
  unaware of your surroundings" are not modelled and are not debt: there is no
  task that would retire a row for them, which is #27's own argument — a debt
  row nobody can retire is worse than no row. Blinded's and Deafened's ability-
  check clauses *are* declared, because `on_ability_check` exists and what is
  missing is a mutator (#38), and so is Grappled's drag-and-carry, because
  movement is already owed (#28).
- **No `source_ref` field.** `features/base.py` is `w3a`'s and that is where
  the field is designed; the quoted rules sentence is in each handler's
  docstring beside the code it governs until then.
- **No `Perturbation` profile.** `w3f` closes that vocabulary and `DESIGN.md` §
  Buffs and debuffs names the scorer as its only reader; declaring keys for
  fifteen conditions against a vocabulary that does not exist is the retrofit
  that sequencing principle 8 warns about, pointed the other way.


## The PR, review and retro skills

Three skills from Matt Pocock's *Fixing the PR Bottleneck* (AI Engineer, 2026;
[mattpocock/skills](https://github.com/mattpocock/skills), MIT), adapted rather
than vendored. The talk's argument: implementation is overloaded and review is
underloaded, so standards belong to the reviewer, and a retro turns each round
of review into a check or a rule so the next task does not repeat it.

- **The checklist moved to `CODING_STANDARDS.md`.** It was
  `.claude/skills/pr-review/SKILL.md` (§ w0a, The review workflow's two
  reviews); that file is now the procedure and the root file is the standard, so
  the retro edits a document and not a skill. `CLAUDE.md` was left whole: moving
  review-only rules out of it is the retro's "CLAUDE.md" candidate, and the
  first gate that runs it decides what goes.
- **Two axes, one pass in CI.** Upstream `code-review` runs Standards and Spec as
  parallel subagents. CI keeps `--disallowedTools Agent,Task` for the reason in
  § w0a, so there the axes run in sequence and are reported under separate
  headings; the skill fans out only where subagents can return. Spec is the
  ROADMAP row named by the branch, which is what `/next-task` step 2 already
  criticises — the reviewer now checks the implementation against it too.
- **The retro runs at every `⏸` gate**, inside `/plan-review`, not after every
  task — `docs/workflow.md` § Token cost. Its edits ride the gate's PR, except a
  `.github/workflows/` edit: a gate that carried one would stop the loop and go
  unreviewed. (Superseded by #40: the loop never commits a workflow edit and
  proposes it in `docs/questions.md` instead, `docs/workflow.md` § Merge
  authority.)
- **The PR body is `pr`'s**, with a Bookkeeping section appended for what
  `/next-task` already required. Merge Danger's one-way doors are named for this
  repo: the frozen registry, seeded output, closed vocabularies, the contract.

Upstream's `GLOSSARY.md` and `writing-for-agents` were not brought in: `DESIGN.md`
is the vocabulary and `CLAUDE.md` § Working rules is the writing guide.

## w1i — the event log, and what a record per roll turned out to cost

`core/log.py` is a list of tuples and a level. What needed deciding was the
shape of a record, how a writer skips one, and how far to wire the writers.

### A record is a tuple of names and values

A record is `(kind, *fields)` with a lowercase string kind, which is the
row's "tuples; formatting deferred to the reporter" taken literally. It holds a
creature's *name* and never the creature: a record is read after the fight has
moved on, and a reference to a mutable `Creature` would report its hit points at
read time rather than at write time. Enum members (`Outcome`, `DamageType`) are
kept as members, because they are immutable and their serialised spelling is
the reporter's decision (`rules/enums.py` gives them lowercase values for it).

The kinds live with their writer — `ATTACK`, `ATTACK_ROLL` and `DAMAGE_ROLL` in
`engine/attack.py`, each documented with its field order — rather than in a
closed enum in `core/`, which sits below every package that would write one and
cannot know what an attack is. That is a first cut: when `w2g`'s reporter reads
records from five writers, a vocabulary in one place may be worth its import,
and nothing here prevents moving the constants.

### The writer asks before it builds

`--log-level=summary` keeps what an attack came to and skips the d20 and the
damage dice under it. The skip is the writer's: it reads `log.full` before it
builds the tuple, so a skipped record costs a slot read rather than an
allocation thrown away inside a method. `write` is the list's own bound
`append`, so a kept record costs one C call and no Python frame. Both are why
`full` is a slot and not a property, and why it is kept from disagreeing with
`level` by refusing every assignment after construction rather than by a
property the writer would pay a frame for on each read. The first review
round found the two writable and able to drift.

### How far the writers go

Only the attack pipeline writes, and that is debt #50 (`docs/questions.md` #6
has the choice). `resolve_attack()` takes `log` as it takes `trigger`: optional,
and `None` costs one identity test per record site. The `ATTACK` record is
written after `on_attack_resolved` is sealed, so an attack that raised part-way
leaves no result in the log; a vetoed attack writes one with `outcome` `None`,
at both levels, because "prevented" is a result and not a roll.

### What it cost the benchmark

Priced as sequencing principle 10 asks, with the deterministic witness first.

**The witness.** Over the fixed 200-encounter profiled run (6,000 attacks, 3,934
of them dealing damage), the call count moves from 1,483,283 to **1,489,683 at
`summary`** and **1,503,551 at `full`**, comparing like with like across
§ w1g's two-valued keying artefact. `summary`'s 6,400 are one `append` per attack
plus the per-encounter `clear()`; `full` adds one `append` per attack roll and an
`append` and a `dict.items()` per damage roll, 6,000 + 2 × 3,934. Every added
call is a C builtin except the 200 `EventLog.clear()` frames, one per encounter
reset. `resolve_attack()`'s cumulative time read 0.67–0.71 s before, 0.61–0.67 s
at `full` and 0.67–0.69 s at `summary` over three runs each: inside its own noise.

**The rate agrees.** Eight interleaved triples on one container, unlogged
baseline then `full` then `summary`: medians of **+0.1% at `full`** (ratios −7.3%
to +7.1%) and **+0.2% at `summary`** (−3.8% to +9.6%) against the baseline, and
−0.6% between the two levels. None of it is distinguishable from zero.

**So the row's question has an answer, and it is neither.** It asked whether
the skip is the thing that has to be cheap or the record is. Both are: a record
is at most three C calls and the skip is one attribute read, in a run that
makes ~250 calls per attack. DESIGN.md § Levers credited `--log-level=summary`
with "~2–3× on its own", and that figure is the cost of formatting a string per
roll, which tuple records never pay; the third review round caught the sentence
still standing, and § Levers now says what is measured here. What `summary`
buys now is memory: `full` holds
about three records per attack. That is `w2g`'s concern, since the runner decides
whether a log outlives its iteration.

### What this task deliberately did not do

- **No writer outside `engine/attack.py`** — debt #50, `w2c`.
- **No time on a record.** No attack context carries a `TimeKey` (§ w1c), so
  order in the log is the only clock; the round and turn records that give a
  reporter boundaries are the turn loop's to write.
- **No serialisation.** JSONL for `trace` and the provenance a `SimResult`
  carries are `w2g`'s; a tuple of names, numbers and enum members is what they
  will be read from.

## The W1 late-wave review

`w1g`, `w1h` and `w1i` landed between this gate and the mid-wave one. The model
is § The W0 plan review. **Nothing measured moved a number the plan assumes**:
`w1g`'s witness added no call, `w1h` is off the hot path, and `w1i`'s records
cost nothing the rate could see (§ w1g, § w1h, § w1i). The projection over the
budget stands where § The W1 critical-path review put it, and `w1k` still
settles it. What moved is the row behind them: `w1j` was written before the
three tasks it now has to build on, and could not be done from what it said.

### `w1j`'s file cell was narrower than its done-when

The row produced `engine/dying.py` and `engine/healing.py` and asked, in the
same row, for `resolve_damage()` to fire `on_reduced_to_zero` — which is
`engine/damage.py`, debt #8's own file. That is the third row in a row whose
Produces cell omitted a file its done-when had to change: § The W1 mid-wave
review found it in `w1g`'s, and `w1i` wrote to `engine/attack.py` and
`tools/spike.py` past a cell that named `core/log.py` alone and recorded the
judgement as `docs/questions.md` #6. Reading the cell against the code is now
part of `/next-task` step 2 and of this gate's own third question, and `w1k`'s
cell, which named a measurement and not the `tools/` files the re-measure
wires, is corrected on the same reading.

Three more things the landed tasks oblige of `w1j`, each now in its row:

- **Consciousness is a condition now.** `w1h` landed `Unconscious`, including
  the "you remain Prone" handler, and a row that says "a heal at 0 HP restores
  consciousness" would otherwise be built beside it rather than on it. `w1j`
  depends on `w1h` and applies the real condition. That makes it the first
  engine code to apply a condition, which debt #42 had said was `w2b`; the row
  stays `w2b`'s, since retiring it means telling a condition from an effect in
  `creatures/effects.py`, and the gap is exact until a feature hooks
  `on_condition_gained`.
- **`Creature.is_alive` answers from hit points and names `w1j` in its
  docstring**, and `engine/triggers.py` and the spike read it. Once a creature
  at 0 can be dying rather than dead, what that property answers is a decision
  with readers, and which creatures make death saves at all is a fact nothing
  carries: DESIGN.md's stop condition says a PC, and a `StatBlock` cannot say
  which creatures are PCs. Both are `w1j`'s to settle, and the change to fight
  length is a pricing question.
- **The third D20 roll.** § w1g kept its two helpers as private copies of
  `engine/attack.py`'s and named the third copy as the moment to extract them.
  A death save is that copy, so extraction into a new `engine/d20.py` is in
  scope rather than a choice `w1j` would have to make outside its files.

### Two hooks nothing fires, and nobody owned

`on_kill` fires at the moment `on_reduced_to_zero` does and was named by no row,
so it rides `w1j` beside debt #8. `on_ability_check` has been hooked by three of
`w1h`'s conditions and fired by nothing; initiative fires its own hook and the
2024 Grapple and Shove are saves, so the first check the plan rolls is
stabilising a dying creature — a Wisdom (Medicine) check. That overturns
§ w1g's "nothing before `w2b`'s actions rolls one — a combat simulator rolls no
skill check" on both counts: `w1j` comes before `w2b`, and stabilising is a
skill check. It is debt **#51**: `w1j` builds the roll and fires the hook, and
`w2b`, the row that builds actions, wraps it in the Action an APL takes, so the
stabilise has one roll and one action rather than two owners of either.
The other uncalled hooks — turn, round and encounter boundaries, movement,
rests, concentration and casting — are each inside a row that names the thing
they announce.

### The arm-order finding lived only in its record

`w1g` found the within-pair order worth about 3% of the rate and handed `w1k`
"alternate the arms". It said so in § w1g alone, and `w1i`, the next priced
task, ran its triples in one fixed order. `w1i`'s conclusion survives, because
its witness carried it, but a method that reaches the plan only through one
record reaches the task after it by luck. Sequencing principle 10 now says it,
together with the keying artefact that sets the witness's resolution.

### Retro

Three PRs and six review rounds between them — one for `w1g`, two for `w1h`,
three for `w1i` — with no red CI run and nothing sent to the register by the
round cap. Most of what the rounds found was prose held to `CODING_STANDARDS.md`
§ Citations and comments (`w1g`'s four findings, `w1i`'s first two rounds); the
rest was one rules reading (`w1h`: Initiative is an ability check), one
writable field (`w1i`: `EventLog`'s level), and one DESIGN.md sentence a
measurement had falsified (`w1i`: § Levers).

- **`/next-task` step 2 reads the Produces cell against the code**
  (`.claude/commands/next-task.md`), and `/plan-review`'s questions 2 and 3 say
  what this gate looked for (`.claude/commands/plan-review.md`). Both are the
  finding above, made where the next reader of a row looks.
- **Sequencing principle 10 carries `w1g`'s method** (`ROADMAP.md`), for the
  reason in the section above.
- The prose findings are not a new standard: they are
  `CODING_STANDARDS.md` § Citations and comments applied as written, and the
  file they keep being found in — a new module copying a sibling's "Not here:"
  paragraph — is exactly what `w1l` cuts. No check is proposed: the mechanical
  candidate is `w1l`'s own AST test, which lands with it.

Sources: `docs/decisions.md` § w1g, § w1h, § w1i, § The W1 mid-wave review;
`ROADMAP.md` § Sequencing principles 10, § The W0 debt register rows #8, #42,
#51; `docs/questions.md` #6; PRs #45, #46, #50.

## w1j — dying, death saves, healing, and the third D20 roll

Three new engine files — `engine/dying.py`, `engine/healing.py`, `engine/d20.py`
— and `resolve_damage()` handing them a creature at 0. What needed deciding was
who dies at 0, what "alive" answers once dying is a state, where the drop fires
relative to the damage hooks, and how much of the third D20 roll to extract.

### Who rolls death saves is a constructor fact, named as the initiative names it

`Creature(player_character=...)`, default `False`. DESIGN.md § Context says a
PC rolls death saves; nothing on a `StatBlock` says which creatures are PCs, and
a stat block is what a creature *is*, where being a player character is what a
build loader *says* about one — so the fact is the creature's, beside
`initiative`, not the stat block's. It carries the name `InitiativeKey` already
uses for the same fact (`core/timeline.py`), so `w2c`, which builds the key,
reads one field rather than reconciling two. The default is the monster's
because the cost of a wrong default is asymmetric: a PC built without the flag
dies at 0 and the fight ends visibly early, where a monster built with it would
lie on the floor making death saves while the party's damage went into it.

Anything that is not a PC **dies at 0**: `dropped_to_zero()` calls `Creature.die()`,
and `damaged_at_zero()` does the same for one somehow left at 0 alive. DESIGN.md
settles that in the same sentence that settles the PC's.

### `is_alive` is not hit points, and its readers wanted `is_down`

`is_alive` is now `not dead`: a creature at 0 making death saves is alive, by
the rules' own wording (it dies on the third failure). Every reader was re-read
against that and **none of them meant it**. The two movement checks in
`engine/triggers.py` ask whether an opportunity attack ended the move, which a
drop to 0 does whether or not the mover is dying; the watcher filter asks
whether a creature can take a reaction, which an Unconscious one cannot; the
spike's `enemies.*` selectors and the skeleton's loop ask whether there is still
something to hit. All of those are `is_down` — hit points at or below 0 —
which is the answer `is_alive` gave before, so **the spike fights the same
fights**: the witness below shows identical outcomes. The one reader that wants
both is `allies.down`, the APL's Healing Word target, which is alive *and* down:
a dead ally is not a heal target.

`dead`, `stable` and the two tallies are **read-only on `Creature`, moved by six
named methods** (`die`, `revive`, `reset_death_saves`, `stabilize`,
`succeed_death_save`, `fail_death_save`), because they carry two invariants — three failures is
dead, three successes is Stable with both tallies reset — and
`CODING_STANDARDS.md` asks for a count with an invariant to have named writers.
The first cut had them as plain slots, and the review showed that writing three
failures by hand left a creature dying with nothing to notice. The creature
keeps the invariants; `engine/dying.py` decides when a rule moves them and fires
the hooks.

### The drop fires between the hit points and `on_damage_taken`

`resolve_damage()` sets hit points to 0, then `dropped_to_zero()` fires
`on_reduced_to_zero`, settles the victim (Unconscious, or `Creature.die()`), fires
`on_kill` for the attacker, and only then does `on_damage_taken` fire. So a
`prevent()` handler (Relentless Endurance) reads a creature at 0 and leaves it
at 1, and an `on_damage_taken` handler reads the hit points the event ended on
rather than an intermediate 0. `overflow` is the damage left over after hit
points ran out, and temporary hit points have already been spent by then — it
is what the Instant Death sentence compares against the maximum.

`on_kill` fires when the creature *dropped*, not when it died: "reduce a
creature to 0 Hit Points" is what Great Weapon Master's Hew says, and a PC that
drops and starts dying is reduced to 0 all the same. It does not fire for a
dying creature finished off by damage at 0, which was not reduced to anything.

**Vetoes are refused everywhere but `on_heal`**, for `engine/attack.py`'s and
`engine/saves.py`'s reason: none of the other five moments can be called off,
and each refusal names what a feature does instead. A heal can be: "can't
regain Hit Points" is a rules effect (2024 Chill Touch), so a veto on `on_heal`
returns zero.

### Damage at 0 counts what got past temporary hit points

`damaged_at_zero()` is handed what reached hit points, and is not called when
temporary hit points absorbed the whole hit. The glossary settles it: at 0 hit
points temporary hit points "can still absorb damage directed at you while
you're in that state" (XPHB, Rules Glossary, "Temporary Hit Points"), so damage
they absorbed is not damage that fails a death save or kills outright. The
first cut passed the whole mitigated amount, on the analogy of `on_damage_taken`
firing for an absorbed hit (§ w0g2); the independent test pass found the
sentence and the two tests that pin it.

`critical` is a new keyword on `resolve_damage()`, because "If the damage is
from a Critical Hit, you suffer two failures" needs to know, and nothing else in
the damage pipeline does. `engine/attack.py` passes it on a hit — one argument
beyond the row's "edited only to import", recorded as `docs/questions.md` #7.

### The dying Unconscious is its own class, `Downed`

The row asks for the real condition, and `w1h`'s acceptance test forbids any
condition's name in `engine/`. The test's claim is "no `if creature.is_prone`":
a condition is behaviour the engine dispatches to and never a flag it branches
on. Subclassing one is neither, so the test exempts exactly that identifier in
exactly `engine/healing.py` and stays as strict everywhere else.

**`Downed` subclasses `Unconscious` rather than applying it**, because
`Unconscious` stacks `REFRESH`: applied to a creature that already holds one
— from a spell, say — the dying condition merged into the spell's effect, so
the spell ending left a dying creature conscious and a heal stripped the
spell's condition. A different class is a different stacking key
(`creatures/effects.py`), so the two are held apart and each ends on its own
terms; holding two is harmless because everything `Unconscious` grants is
idempotent (DESIGN.md § Structural prevention). The fourth review round found
the merge. `Downed` lives in `engine/healing.py`, beside the removal, because
the death save's natural 20 regains a hit point through `heal()` and
`engine/dying.py` importing `engine/healing.py` is the only direction the two
can import in.

**A dead creature regains nothing and `on_heal` does not fire for it.** Death
in the rules is undone by magic that revives, which is not healing, so a heal
aimed at a corpse is a wasted action and returning zero is how the APL sees
that. Nothing revives a creature yet; whatever does will clear `dead` itself.

A natural 20 goes through `heal()` rather than setting `hp = 1`: it is
regaining hit points, so `on_heal` fires for it and waking up happens in the one
place it is written.

### The D20 helpers, extracted at the third copy

`engine/d20.py` holds `modifier_total()`, `roll_d20()` and `settle()`, which is
§ w1g's prediction taken up. `engine/saves.py` now uses all three and
`engine/dying.py` uses all three twice (the death save and the Medicine check).
**`engine/attack.py` imports `modifier_total()` and keeps its inlined roll and
its own `_seal()`**: the roll is three lines on the hottest path in the project,
and `_seal()` is not the same helper — it closes the context and reads the
frame directly, which is what `w1f` priced (§ w1f). An import of a module-level
function costs what calling the private copy did, which is why the witness
shows the 12,000 calls moving name and not count.

### What it cost the benchmark

Priced as sequencing principle 10 asks, with the deterministic witness first.

**The witness.** Over the fixed 200-encounter profiled run at seed 0, the whole
run's call count went from **1,504,052 to 1,502,340**, the same under three
`PYTHONHASHSEED`s, with an identical fingerprint of every encounter's closing
hit points and an identical next draw from the generator afterwards — **the same
fights**. The per-function diff accounts for every call: −3,941 `max()` (the
clamp `resolve_damage()` no longer makes on every instance of damage), +1,600
`is_down` reads (`allies.down` now asks two questions), +1,600 for the spike's
reset clearing death on the hero and its three enemies (`revive()` and the
`reset_death_saves()` it calls, 200 times each), +29 for the one drop to 0 the run contains (one
`on_reduced_to_zero`, one `on_kill`, their contexts and settles), and −999 in
the generated `__init__` row that § w1g identified as cProfile's keying
artefact. `resolve_damage()`'s cumulative time read 0.133 s before and 0.128 s
after: inside noise. So the drop is off the hot path in this configuration —
one per 200 encounters — and what is on it got one call cheaper.

**The rate agrees, after the harness was found first.** Sixteen pairs with
alternating order, the baseline in a worktree and the change in the main
checkout, gave a median of −3.6% — against a witness with fewer calls. Sixteen
more with **both arms in sibling worktrees**, same venv layout, gave **+2.3%**
(ratios −6.5% to +4.2%; base-first +1.9%, change-first +2.3%; baseline 21.9k–
24.7k). The checkout an arm runs from was worth about 6% on this container,
which is a fourth harness artefact for `w1k` to control beside § w1g's arm
order: **run both arms from the same kind of directory**. Not distinguishable
from zero, and the witness says why.

**So `w1j` adds nothing to the shortfall `w1k` is totalling.** If the real
encounter loop drops creatures far more often than the spike's one per 200
encounters, each drop costs two dispatches and two context allocations, which
`w1k` can price per call against `w2c`'s count.

### What this task deliberately did not do

- **Nothing calls `death_save()` at the start of a turn.** "Whenever you start
  your turn with 0 Hit Points" is a turn boundary, and the boundaries are
  `w2c`'s; this file supplies the roll it calls.
- **A dead creature keeps its effects**, its Unconscious included. Nothing reads
  them once `dead` is set, and what ends at death is a question about
  concentration and summons that `w5a` and `w6a` meet first.
- **No "regains 1 hit point after 1d4 hours"** for a Stable creature: hours are
  `w6a`'s rests.
- **`on_condition_gained` still does not fire** for the Unconscious applied here
  — debt #42, `w2b`'s, as its row says.
- **No log records** for drops, death saves or heals — debt #50, `w2c`.
- **The Medicine check adds the bare Wisdom modifier** — debt #36, widened to
  skills, `w2f`.
- **Exhaustion's sixth level still cannot kill** — debt #44, narrowed to the
  missing mutator and moved to `w3f` via `w3e`.

Sources: XPHB (2024) "Dropping to 0 Hit Points" (Instant Death, Falling
Unconscious, Death Saving Throws, Rolling 1 or 20, Damage at 0 Hit Points,
Stabilizing a Character), "Damage and Healing" -> Healing, Rules Glossary
"Temporary Hit Points"; DESIGN.md § Context; `docs/decisions.md` § w0g2, § w1f,
§ w1g, § w1h, § The W1 late-wave review; `ROADMAP.md` § Sequencing principles
10, § The W0 debt register rows #8, #36, #42, #44, #50, #51.

## w1l — docstrings cut back to their own code

`CODING_STANDARDS.md` § Citations and comments' first rule applied to every
docstring and comment in `src/` and `tests/`, which the rule had left to this
task rather than to every PR that touched a file.

**Where an argument went.** Almost everything cut was already recorded in the
section the module's row in `CLAUDE.md`'s table names, and the code now points
there. What was argued only in the code — twenty-odd items, none of them a new
decision — is written into the section it belongs to, under each section's
"What the docstrings argued, recorded here by `w1l`" heading, so a pointer to
§ w0e from `hooks/registry.py` still finds `UnknownHookError`'s reason in § w0e.
Inbound pointers aimed at removed prose were moved with it, in
`docs/decisions.md`, `ROADMAP.md` and `CLAUDE.md`. Passages that record what a
docstring *said* at review time — § The W0 plan review, § w0g3, § The W1
mid-wave review — stay history, put in the past tense where they had been
present.

**"No code changes" was checked mechanically, not read.** Every module under
`src/` and `tests/` was parsed at the milestone branch's head and at this
branch's, with every bare string statement removed and a body left holding
only `pass` or `...` treated as empty, and the two `ast.dump`s compared: 80
modules, 0 differ. It is not committed as a standing test, because it compares
a tree against a fixed earlier commit, and once this diff merges there is no
"before" for it to read; the standing guards remain `CODING_STANDARDS.md`
§ Citations and comments on lines a diff adds, and review.

**What was kept** — what a function takes, does, returns and raises; the trap in
the line beneath a comment; one-line pointers; XPHB citations of the rule the
code implements; golden arithmetic in tests (standing rule 4); and phrases other
files quote word for word, such as `Context`'s drain sentence and the snapshot
rule. Error-message strings that say "see the module docstring" are values, not
prose, and were left; `engine/triggers.py`'s module docstring still names the
refusal they point at.
