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
  reports every failure rather than the first. Later gates — frozen-registry
  diff (`w3b`), benchmark regression (`w3c`), stale generated docs (`w3i`) —
  join this workflow as their tasks land.
- Ruff excludes `*.md` and `.claude/skills`, so `ruff format` never rewrites
  `DESIGN.md`'s code samples or vendored skills; the hook only sees `.py`/`.pyi`,
  so this keeps hook and CI in agreement.
- `cli.py` is a placeholder that exits non-zero pointing at `w2h`, so the
  `dndsim` entry point gives a message rather than an ImportError.

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

## w0d — timeline

- There is no clock *object*. `TimeKey(round, initiative_index, phase)` is a
  position and the turn loop (`engine/turn.py`, w1f) owns the current one; a
  second mutable clock would be a second source of truth for the one fact the
  simulation is ordered by. Rounds are 1-based, an initiative index is a seat
  (0 acts first), both validated.
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
  (`anchor > now.initiative_index`).
- `Duration` anchors on an initiative **index**, not a creature, because `core/`
  sits below `creatures/`. `creatures/effects.py` (w1c) passes
  `creature.initiative_index` — `DESIGN.md`'s `until_end_of_next_turn_of(c)` one
  layer up.
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
  settled (`Duration` anchors on `initiative_index`; the log needs a name); one
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
- **Vulnerability before resistance is load-bearing arithmetic.** `(2n) // 2 ==
  n` for every integer, so that order *is* the rule that resistance and
  vulnerability to the same type cancel, with no special case written. The
  reverse order loses a point on every odd number.
- **Two steps are rulings, not quotations, and `damage.py` says so inline**
  (DESIGN.md asks for exactly this): flat reductions apply *after* resistance,
  and halvings apply *after* the flat reductions. Each changes every number a
  build with Heavy Armor Master or Uncanny Dodge reports. `w1e` owns confirming
  them against the 2024 text; nobody re-litigates them in a review without
  reading the argument that is written there.
- **Reductions and halvings act on the total, not per type** — Heavy Armor
  Master reduces *the damage*, not the fire part of it — which is why
  `Mitigated` carries both the per-type picture after the typed steps and the
  one total after the untyped ones. `on_damage_taken` gets that total plus the
  surviving types, exactly as its context is shaped.
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
  top of Rage quartering a hit is the known-correct answer. `w1e` owns
  confirming that against the 2024 text.
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

## w0h — walking skeleton

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
  `initiative_index`. The fixtures work around it and say so in place. `w1a`
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

Sources: DESIGN.md § Performance, § The spike must be pessimistic, § Levers,
§ The benchmark gate, § Condition language; ROADMAP w0i, w1a, w1f, w2e, w3i.
