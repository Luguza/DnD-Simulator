# D&D 5e (2024) Combat Simulator — "SimC for D&D"

## Context

This is the founding design document for the project; at the time of writing
the repository contained no code. The goal is a Monte Carlo combat simulator
for D&D 5e 2024 rules (XPHB) — the D&D equivalent of SimulationCraft: define a
party in config files, simulate a full adventuring day thousands of times, and
get statistics about how a build actually performs instead of back-of-envelope
DPR math.

It is a **private personal repo, not for distribution**, so it may read the
5e.tools dataset freely. Bulk rules text still stays out of git (fetched on
demand into a gitignored `data/`) — to keep the repo small and diffs readable,
not for licensing reasons.

**The question v1 answers:** given a party and a sequence of encounters with
rests between them, *how far into the adventuring day does the party get before
resources run dry?* Attrition, not single-fight DPR.

## Reconnaissance (done, against live data)

`raw.githubusercontent.com/5etools-mirror-3/5etools-src/main/data` is reachable
through the proxy; `5e.tools` direct returns 403.

| Data | State | Verdict |
|---|---|---|
| Weapons (`items-base.json`) | Structured: `dmg1`, `dmg2`, `dmgType`, `property`, `mastery`, `range` | **Import directly** |
| Masteries / item properties | `itemMastery`, `itemProperty`; 8 masteries | Numbers import, behaviour hand-coded |
| Class scaling (`classTableGroups`) | Machine-readable per-level rows (Rages, Rage Damage, Sneak dice…) | **Import directly** |
| Class/subclass features | Prose in `entries[]`, `refSubclassFeature` indirection | Hand-coded |
| Spells (391 XPHB) | Skeleton structured (`level`, `savingThrow`, `spellAttack`, `scalingLevelDice`); mechanic is prose | Skeleton imports, behaviour hand-coded |
| Monsters (503 XMM) | Prose, but a **closed tag vocabulary**: `{@atkr}`, `{@hit}`, `{@h}`, `{@damage}`, `{@actSave}`, `{@dc}`, `{@recharge}` | Parseable — deferred to Phase 5 |
| Feats (77 XPHB) | 2024 fighting styles are feats | Hand-coded |

### Content inventory — the real cost centre

| Bucket | XPHB count |
|---|---|
| Class features | 283 |
| Subclass features | 309 (48 subclasses) |
| Feats | 77 |
| Optional features | 58 (28 invocations, 20 maneuvers, 10 metamagic) |
| Combat spells | 212 (103 concentration) |
| Weapon masteries | 8 |

≈ **670 hand-written implementations** at full coverage. This number drives
every architectural decision below: the feature API must be learnable from a
short document, uniform across wildly different mechanics, and **hard to get
subtly wrong**, because most of these will be written by subagents in parallel
batches with limited context.

## Settled decisions

| | |
|---|---|
| Language | Python 3.11+, OOP, CLI only |
| Ruleset | XPHB (2024), **levels 1–12** |
| Positioning | Square grid, 5 ft cells, real distance, OA, true AoE templates |
| Policy | SimC-style Action Priority List in build YAML |
| v1 output | Adventuring-day attrition |
| Phase 2 order | **Martials first, full vertical slice** (Barbarian/Fighter/Rogue/Monk, 16 subclasses, 1–12, masteries, martial feats) |
| Spells | **Engine built up front, individual spells added on demand** |

Levels 1–12 removes 7th–9th level spells and tier-4 features — the
hardest-to-model content — from scope.

## Libraries before reimplementing

`pydantic` (build/APL schema validation — the main fail-loudly lever),
`numpy` (statistics, confidence intervals), `typer` + `rich` (CLI, reports),
`pytest` + `hypothesis`, `PyYAML`. Dice notation gets a small hand-rolled
tokeniser (the grammar is tiny and it sits on the hottest path); APL conditions
get a hand-written AST whitelist rather than `simpleeval`, because load-time
name resolution against a declared schema is the whole point and no library
does that part.

---

# Architecture

## Design patterns at a glance

Named so that batch authors and reviewers share vocabulary. Each is expanded
at its point of use below.

| Pattern | Where | Why it earns its place |
|---|---|---|
| **Intercepting Filter / Pipeline** | `hooks/dispatch.py` | Ordered handlers over one shared context — the core mechanism |
| **Chain of Responsibility** (veto variant) | `Phase.EARLY` / `FINAL` handlers | Any handler may veto an attack or a cast |
| **Parameter Object** | `hooks/contexts.py` | Contexts bundle the in-flight state features are allowed to touch |
| **Registry + Factory Method** | `HOOKS`, `FEATURE_REGISTRY` | id → class resolution; closed registries make typos import errors |
| **Command** | `actions/base.py` | `is_legal()` / `execute()`; lets the economy queue and re-poll actions |
| **Strategy** | `apl/`, `core/rng.py` | Swappable decision policy; `PcgRng` vs `ScriptedRng` |
| **Interpreter** | `apl/expr.py` | APL conditions are a small expression grammar compiled to an AST |
| **Specification** | APL `if:` clauses | Composable boolean predicates over domain objects |
| **State** | `conditions/`, `effects.py` | Prone/Frightened/Raging alter behaviour by being attached |
| **Mediator** | `engine/world.py` | Creatures never talk to each other directly |
| **Adapter / Anti-Corruption Layer** | `content/loader.py` | 5etools' schema never leaks into the domain model |
| **Visitor** | Phase 5 stat-block parser | Walks 5etools entry trees by node type |
| **Builder** | `builds/loader.py` | Assembles a `Character` from YAML in validated steps |
| **Unit of Work** | `resources.transaction()` | Multi-resource costs commit or roll back atomically |
| **Facade** | `SpellSlots`, `world.query` | Ergonomic surface over nine pools / spatial queries |
| **Value Object** | `DamagePart`, `Modifier`, `TimeKey`, `Duration`, `ResourceSpec` | Frozen, comparable, safely shared |
| **Object Pool + Flyweight** | context reuse, shared specs | The performance budget depends on both |
| **Null Object** | APL default policy, empty dispatch list | Absent config behaves predictably, never crashes |
| **Template Method** | `HookSource.__init_subclass__` | Fixed validation skeleton subclasses cannot skip |
| **Inversion of Control** | the whole feature API | The engine calls features; features never drive the engine |

Two patterns are **deliberately rejected**, and the reasons are the design's
load-bearing arguments — see below: **Observer / Event Aggregator** (the
pub-sub bus) and **Template Method via mixin inheritance** for feature
composition.

## Hook system: typed pipelines over attribute-locked contexts

Rejected the **Observer / Event Aggregator** pattern (a pub-sub bus) —
`bus.subscribe("on_hitt", fn)` is a silent no-op, and with 400 features written
by agents that can't read each other's code, an invisible typo is the likeliest
failure. Observer also has no natural ordering story, and "publish returns
nothing" gives an author no channel for *changing* an attack in flight, so they
reach into `attacker._advantage` instead.

Rejected **Template Method via mixin inheritance** — a level-12 Barbarian has
~15 objects wanting the same `on_hit`; the MRO cannot express that, and it
forces every author to remember `super().on_hit(...)`, where one omission
silently deletes every other feature's contribution.

**Chosen: Intercepting Filter (a Pipeline of handlers), with a Chain of
Responsibility veto.** Each hook is a named pipeline stage with exactly one
**Parameter Object** context type. Features declare handlers via decorator
registration; the framework builds a per-creature dispatch table and runs
handlers in phase order over one shared mutable context. It reads like a method
override and composes like a pipeline. `EARLY` and `FINAL` handlers may call
`ctx.veto(...)`, which is the Chain of Responsibility element — the only place
a handler halts the chain rather than contributing to it.

The whole feature API is **Inversion of Control** (the Hollywood Principle):
the engine calls features at defined points; features never drive the engine.
That is what allows 400 independently-authored units to coexist.

Four properties make it safe for parallel low-context authors:

1. **Hook names validated at import time** against a closed **Registry** —
   typos are `ImportError`, not silence.
2. **Handler signatures validated at class creation** via `__init_subclass__` —
   a **Template Method** used the way it works well here: a fixed validation
   skeleton subclasses cannot skip or forget to call. Wrong context annotation
   or missing return annotation is `ImportError`.
3. **Contexts are attribute-locked.** Features never assign fields; they call
   mutator methods (`ctx.grant_advantage()`, `ctx.damage.add(...)`). This is
   **Encapsulation enforced at runtime** rather than by convention — two
   features cannot clobber each other because nobody writes a field.
4. **Provenance is automatic.** The dispatcher stamps the current source before
   each call — an **Ambient Context** scoped to one handler invocation — so
   every modifier is attributed with no author effort, giving the per-feature
   damage breakdown for free.

### Scope — the single biggest learnability lever

Every hook declares `Scope`: `SELF` (owner is the actor), `TARGET` (owner is
the one being hit), `OBSERVER` (owner merely perceives it), `GLOBAL`. In an
`on_incoming_damage` handler `self.owner` is *the victim*, with no prose
needed. No feature ever writes `if ctx.attacker is self.owner`.

### The closed hook list (~30 points)

Timeline: `on_encounter_start/end`, `on_round_start/end`, `on_turn_start/end`,
`on_short_rest`, `on_long_rest`.
Attack: `on_declare_attack`, `on_incoming_attack_declared`, `on_attack_rolled`,
`on_hit`, `on_hit_taken`, `on_miss`, `on_attack_resolved`.
Damage: `on_damage_roll`, `on_incoming_damage`, `on_damage_taken`,
`on_reduced_to_zero`, `on_kill`.
Other: `on_ability_check`, `on_saving_throw`, `on_save_resolved`,
`on_forced_save`, `on_cast_spell`, `on_concentration_start/check`,
`on_condition_gained/lost`, `on_effect_applied/expired`, `on_move`,
`on_leave_reach`, `on_resource_spent`.

The registry is **frozen** to feature-batch authors; CI fails any batch PR
touching `hooks/`, `engine/` or `core/`. A closed, frozen extension point is
the **Open/Closed Principle** applied literally: features extend behaviour
without modifying the engine, and the CI rule makes that structural rather than
aspirational.

An unregistered hook returns an empty handler list rather than raising — the
**Null Object** pattern, so `dispatch()` on a hook nothing listens to is one
dict lookup and a return.

### The uniform handler shape

```python
@hook("on_hit", once_per=Reset.TURN)
def sneak(self, ctx: HitContext) -> R:
    ...
    return R.APPLIED   # or R.SKIPPED
```

- `once_per` budget and `costs` (reaction / bonus action) are consumed **only
  when the handler returns `APPLIED`** — so a skipped opportunity attack does
  not burn the turn's Sneak Attack.
- `gate="<apl_id>"` wires an APL decision in with zero code in the feature.
- Returning anything but a `HookResult` raises `FeatureContractError`.

### Ordering: phase bands, not free integers

`EARLY` (declare/gate/veto) → `NORMAL` (add modifiers, the 95% case) → `LATE`
(replace/multiply/halve) → `FINAL` (clamp/veto). Within a band, order is
`(order, attachment_sequence)`, and attachment sequence is the YAML feature
order, so runs are deterministic.

A `LATE`/`FINAL` handler **requires a `# PHASE:` justification comment**,
enforced by the linter. `rg "PHASE:"` then yields the complete list of
order-sensitive handlers in the codebase — it should stay under ~30.

## Structural prevention over testing

The rules an author *cannot* get wrong, because the data model decides. Every
item here is a **Value Object** — frozen, self-describing, safely shared —
doing work that would otherwise be procedural code an author could misplace:

- **Crit doubling.** `DamagePart` carries `dice` and `flat` as separate fields;
  `ctx.damage.add(dice="2d6")` doubles on a crit, `add(flat=3)` never does.
  Sneak Attack and Rage damage cannot be mis-crit because the author never
  touches the crit path.
- **Advantage.** Not a boolean — `grant_advantage()` appends a source to a
  collection. D&D's rule (any adv + any disadv = flat, multiples don't stack)
  falls out of the derived `had_advantage`. Two features granting advantage is
  idempotent, so ordering cannot matter.
- **Resistance.** `_resist` is a `set`, so a type can't be resisted twice.
- **Damage resolution order** lives in exactly one file, `engine/damage.py` —
  a **Policy/Strategy object owned by the engine, not by features**:
  immunity → vulnerability → resistance → flat reductions → halvings (Uncanny
  Dodge) → temp HP → HP. Documented verbatim in the skill doc.

This is *"make illegal states unrepresentable"* rather than *"test for illegal
states"*. Every rule made structural is one that never needs a test — the
highest-leverage move available when 670 units are written by different hands.

## Durations: one mechanism, three shapes

A totally ordered clock, `TimeKey(round, initiative_index, phase)` where phase
is `START`(0) or `END`(1) — a **Value Object** whose natural ordering *is* the
timeline, so expiry is a comparison rather than bespoke arithmetic. `Duration`
is a declarative **Value Object** too, with named constructors
(`until_end_of_next_turn_of(c)`) acting as **Factory Methods** that keep D&D's
timing vocabulary out of feature code. It resolves to an absolute `TimeKey` at
application time. The load-bearing field is `through_phase`:

- **"until the end of your next turn"** (`through_phase=True`) — still active
  while that turn's `on_turn_end` hooks run, removed immediately after. Rage
  still grants resistance to damage taken during your own end of turn.
- **"until the start of your next turn"** (`through_phase=False`) — removed
  *before* that turn's `on_turn_start` hooks run.

That one flag covers all three hard cases:

| Effect | Expression |
|---|---|
| **Rage** | `until_end_of_next_turn_of(self)`, plus `refresh()` from `on_declare_attack` / `on_damage_taken` / `on_forced_save` — recomputes against the current clock, so "next turn" slides forward |
| **Reckless** | `until_start_of_next_turn_of(self)` |
| **Vex** | `until_end_of_next_turn_of(self)` **plus `charges=1`** keyed to a target id — whichever runs out first removes it |

`Effect`, `Condition`, `Feature` and magic items are all `HookSource`
subclasses carrying hooks identically. **One API shape for all four** — an
agent who has read Rage can write Frightened without reading anything else.

`Effect` and `Condition` are the **State** pattern: Raging, Prone and
Frightened change a creature's behaviour by being *attached objects with their
own handlers*, not by flags the engine branches on. There is no
`if creature.is_raging` anywhere in the engine — which is precisely why adding
a new condition never requires touching engine code. Stacking policy
(`REFRESH | STACK | UNIQUE_PER_SOURCE | REJECT`) is a **Strategy** on the
effect, so "does a second casting stack?" is declared, not re-implemented.

## Resources: one model, no special cases

`ResourceSpec(key, label, maximum, recovery, die=None, slot_level=None)` is an
immutable **Value Object / Flyweight** — one spec instance is shared by every
creature that has the resource, while the mutable `ResourcePool` holds only the
current count. `maximum` may be a literal, a class-table column, or a callable,
and `recovery` is a tuple of `Recovery(trigger, amount)`.

| Thing | Spec |
|---|---|
| Rage | `(SHORT_REST, 1), (LONG_REST, ALL)` |
| Spell slots | nine pools `slot:1`…`slot:9`, `(LONG_REST, ALL)` |
| Pact magic | `slot_level=N`, `(SHORT_REST, ALL)` |
| Ki / Focus, superiority dice | `(SHORT_REST, ALL)`, `die="d8"` |
| Channel Divinity | `(SHORT_REST, 1), (LONG_REST, ALL)` |
| Sorcery points | `(LONG_REST, ALL)` |
| Monster recharge | `(RECHARGE_5_6, ALL)` |

`SpellSlots` is a **Facade** over the nine level-tagged pools, exposing the
queries features and the APL actually want — `highest()`, `count(n)`,
`total_above(n)`, `spend(level, allow_upcast=)` — instead of nine pools to
iterate.

Multi-resource costs go through `resources.transaction()`, a **Unit of Work**:
Quickened Fireball spends 2 sorcery points *and* a 3rd-level slot, committing
both or rolling back both, so a half-paid ability can never exist even if a
later step raises.

Every spend is appended to the structured event log, so the resource-spend
report is a group-by over that log rather than per-feature reporting code — the
log is the single **audit record**, which is also what makes seeded runs
diffable.

## APL and mid-attack decisions

The APL as a whole is a **Strategy**: the creature's decision-making is a
pluggable policy object. That is what makes build-vs-build comparison possible
at all — swap the policy, hold everything else fixed. A built-in scoring
heuristic can later be dropped in as an alternative `Policy` implementation
without the engine noticing.

**The unifying idea:** a turn action, a reaction, and a mid-attack rider are
all "the engine has an offer; does the APL take it?" One flat ordered list; the
engine filters to currently-offered ids and takes the first passing entry.
Individual actions are **Command** objects (`is_legal()` / `execute()`), which
is what lets the turn loop re-poll after every action — and is why Action Surge
needs no special case: it simply grants another action slot and the loop asks
again.

```yaml
apl:
  - { action: rage,            if: "not self.raging and enemies.within(30) >= 1" }
  - { action: reckless_attack, if: "self.hp_pct > 0.4 or target.ac >= 17" }
  - action: divine_smite
    if:   "trigger.crit or (self.slots.highest >= 3 and target.hp > 25)"
    with: { slot_level: "3 if trigger.crit else self.slots.highest" }
  - { action: uncanny_dodge,   if: "trigger.damage_estimate >= self.hp * 0.25" }
  - { action: attack }
```

**Default policy: riders and reactions are OFF when unmentioned** — a
**Null Object** standing in for the absent entry, so adding a feature never
silently changes every existing build's numbers. If a turn falls through with
no legal entry the creature ends its turn and an `APL_FALLTHROUGH` counter is
surfaced in the report — a build falling through 40% of turns is misconfigured
and you want to see it.

**Condition language: the Interpreter pattern, over an AST whitelist.** The
`if:` clauses are a small expression grammar with its own compile step, not
`eval` of source: parse, walk, reject any node outside an allowlist (no
comprehensions, lambdas, imports, dunder access, assignment), reject private
attributes, then resolve every dotted path against a declared
`NamespaceSchema` at **load time** with `difflib` suggestions. `self.ragging`
fails when the party loads — not silently as `False` at simulation-minute-nine.

Each compiled condition is a **Specification**: a reusable boolean predicate
over domain objects, composable with `and`/`or`, and — because the compiler
records which paths each one reads — introspectable, which is what lets the
loader prove an APL never references state a build doesn't have.

The namespace roots (`self`, `target`, `trigger`, `enemies`, …) are **Proxy**
objects built once per creature holding weak references to live state, so
evaluation is attribute access rather than object construction.

## Failing loudly — layered

This is **Design by Contract** with **Fail-Fast** escalation: each layer states
a precondition and refuses to continue when it is violated, as early as the
information exists to check it. `FeatureContractError` is named for exactly
that — a handler that returns the wrong type has broken its contract, and the
run stops rather than absorbing it.

**Import time:** unknown hook name; bad handler signature or context
annotation; missing return annotation; duplicate/malformed feature id; missing
`source_ref` rulebook citation.

**Load time:** unknown feature id (with suggestions); APL action id no attached
feature provides; unparseable condition or unknown namespace path; `with:`
params not matching the feature's declared `params`; declared features
cross-checked against the class progression table, warning on omissions.

**Runtime invariants (cheap, always on):** non-`HookResult` return; mutator
called in the wrong context phase (`grant_advantage` after the roll);
direct field assignment on a context; resource going negative or spent during a
legality query; two reactions in a round; attack chain depth > 3.

**Golden tests — the real net.** Every feature ships one, enforced by a
collection test that diffs `FEATURE_REGISTRY` against tests found on disk.
Goldens run on a `ScriptedRng` returning a fixed sequence, so assertions are
exact integers rather than distributions:

```python
with scripted_rng([18, 3, 4, 5, 6]):          # d20, d4 dagger, 3d6 sneak
    result = rogue.attack(dummy, advantage=True)
assert result.total_damage == 3 + 3 + 15
assert result.parts_by_source["rogue.sneak_attack"] == 15
```

**No-op detector.** After the golden suite, any feature whose hooks never once
returned `APPLIED` is reported. This catches the most common agent bug by far:
a condition that can never be true (wrong enum member, inverted check).

**Interaction matrix.** ~50 hand-computed combos maintained by a human, not by
batch agents — Rage + Uncanny Dodge + vulnerability, Sneak Attack + crit + GWM,
Cleave + Vex, Smite on a crit. This is the regression net for ordering changes,
and the defence against "every feature is individually right, the total is 8%
off".

**Statistical harness (nightly).** Features declare an analytic expectation;
200k simulated attacks are compared inside a Wilson interval. Catches the
"compiles, tests pass, numbers wrong" class.

**Linter** (`tools/check_features.py`): `LATE`/`FINAL` without a `# PHASE:`
comment, access to `_`-prefixed attributes, `import random` outside
`core/rng.py`, missing XPHB citation.

## Performance

~10k iterations × 5 encounters × 5 rounds × 5 creatures × 3 attacks ≈ **4M
attack resolutions** per build comparison. Budget: **≥50k attack
resolutions/sec/core**, measured on a stub in week one — before 400 features
are written against the architecture.

Levers: per-creature precomputed dispatch tables (never scan a global bus);
`__slots__` on every hot object; **Object Pool** for contexts — `AttackContext`
instances are reset and reused per attack rather than allocated, which matters
at 4M resolutions; **Flyweight** for the immutable specs (`ResourceSpec`,
`Duration`, weapon definitions) shared across every creature; APL compiled once
per build; structured tuple logging with string formatting deferred to the
reporter — a **Lazy Evaluation** boundary worth ~2–3× on its own
(`--log-level=summary`); multiprocessing across iterations with one seeded
stream per worker; **adaptive stopping** on confidence-interval width rather
than a fixed count.

Note the tension worth naming: Object Pool and mutable shared contexts are in
direct conflict with the Value Object discipline above. The split is
deliberate — **contexts are pooled and mutable** (hot path, engine-owned),
**everything a feature stores is frozen** (cold path, author-owned). The
attribute lock is what keeps authors on the safe side of that line.
A CI benchmark gate fails the build on a >10% sims/sec regression — performance
decays one feature at a time.

---

# Module layout

```
src/dndsim/
  core/       rng.py dice.py geometry.py log.py errors.py timeline.py
  rules/      enums.py constants.py
  hooks/      registry.py definitions.py decorator.py source.py
              dispatch.py contexts.py
  creatures/  creature.py character.py monster.py dummy.py stats.py
              resources.py economy.py effects.py
  conditions/
  features/   base.py params.py TEMPLATE.py
              classes/<class>/[subclasses/<subclass>/] feats/ masteries/
              fighting_styles/
  actions/    base.py attack.py dash.py dodge.py disengage.py cast_spell.py
  spells/     base.py concentration.py templates.py level0..level6/
  apl/        expr.py schema.py namespace.py apl.py
  builds/     schema.py loader.py
  engine/     world.py query.py attack.py damage.py saves.py turn.py
              encounter.py adventuring_day.py reactions.py
  content/    fetch.py loader.py refs.py tables.py weapons.py
  reporting/  collect.py stats.py report.py
  tools/      check_features.py noop_report.py
  cli.py
data/                      # gitignored: fetched 5etools JSON + builds/encounters
tests/  contract/ golden/ interaction/ statistical/ bench/
docs/   hooks.md apl.md    # GENERATED from the registries
.claude/skills/feature-authoring/  statblock-import/
```

Patterns by package, so the layout is self-explaining:

- `engine/world.py` — **Mediator**. Creatures never reference each other
  directly; targeting, line of sight and reaction audiences all go through the
  world. This is what keeps `Creature` from accumulating knowledge of the
  whole battlefield. `world.query` is a **Facade** over the spatial index.
- `content/loader.py` — **Adapter**, and more precisely an **Anti-Corruption
  Layer** (DDD). 5etools' vocabulary (`dmg1`, `atkr`, `classTableGroups`,
  `refSubclassFeature`) stops at this boundary and never reaches the domain
  model. Since the dataset is a third-party artefact that changes without
  notice, this is the difference between "a schema change breaks the importer"
  and "a schema change breaks everything".
- `content/tables.py` — **Repository**, the single read interface for imported
  reference data, so `table_value("Rage Damage")` is the only way features see
  scaling numbers.
- `builds/loader.py` — **Builder**. A `Character` is assembled in validated
  steps (abilities → class levels → features → equipment → APL), each able to
  fail loudly with a useful message, rather than via a constructor with
  twenty arguments.
- `core/rng.py` — **Strategy**. `PcgRng` in production, `ScriptedRng` in golden
  tests. The linter bans `import random` everywhere else, so determinism is
  structurally guaranteed rather than merely intended.
- Phase 5 stat-block parser — **Visitor** over 5etools entry trees, dispatching
  on node type (`{@atkr}`, `{@actSave}`, `{@recharge}`). The closed tag
  vocabulary is exactly the "stable element hierarchy, growing set of
  operations" situation Visitor is for.

`docs/hooks.md` and `docs/apl.md` are **generated** from the registries —
**Single Source of Truth** applied to documentation. Hand-maintained docs
drift, and a drifted doc is how you get 40 wrong features in one batch.

# The feature-authoring skill

The user's explicit direction — *"we design skills for subagents to use in
crawling 5e.tools and parsing the prose into code"* — and the leverage that
makes 670 implementations tractable. It is a first-class deliverable, and if it
needs more than ~3 pages the API is too complicated and gets simplified rather
than documented harder.

`.claude/skills/feature-authoring/SKILL.md`:

1. The uniform shape (10 lines).
2. The generated hook table, **with the scope column**.
3. The five mutator families: `grant_advantage`, `damage.add`,
   `add_resistance`, `apply_effect`, `resources.spend`.
4. `once_per` vs `costs` vs `gate` — a paragraph each.
5. **The three gotchas, stated as rules:**
   - `Reset.TURN` (any turn boundary in initiative — real D&D "once per turn",
     which is why Sneak Attack works on an OA *and* again on your turn) vs
     `Reset.OWN_TURN` ("once on your turn"). The likeliest subtle bug; two
     distinct enum members, covered by a contract test.
   - `dice` doubles on crits, `flat` does not.
   - **The snapshot rule:** the handler list is snapshotted per event, so an
     effect applied during a hook does not fire for that same event — Reckless
     Attack must both apply its effect *and* call `ctx.grant_advantage()` for
     the triggering attack.
6. Rage, Sneak Attack and Uncanny Dodge verbatim as the three exemplars —
   passive+duration+resource, on-hit rider with once-per-turn, reaction with an
   APL gate.
7. Where numbers come from: `table_value(...)` reading imported
   `classTableGroups`. **Hardcoding a scaling number is a review failure.**
8. How to resolve `refSubclassFeature` / `refOptionalfeature` indirection in
   the source data.
9. **The escape valve:** a feature that cannot be expressed declares
   `NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED`; a collection
   test fails listing all of them. Hook additions are batched into a deliberate
   core change. Blocked-and-visible beats improvised-and-wrong.
10. Checklist: `source_ref`, golden test, run the linter.

The skill deliberately teaches **the five shapes, not the pattern names** —
an author needs "append a source, don't set a flag", not "this is a Parameter
Object". The pattern vocabulary belongs in `docs/` and in review, where it
helps maintainers argue about the design; putting it in the skill would spend
scarce context on nomenclature instead of mechanics.

`features/TEMPLATE.py` is a **Prototype** to copy — the fastest possible way to
get an author onto the happy path without them internalising the whole API.

`.claude/skills/statblock-import/` covers the Phase 5 monster parser against
the closed tag vocabulary.

# The target dummy

The only "monster" in Phases 1–3, fully configurable in YAML: `ac`, `hp`,
`count`, size, speed, grid placement; per-ability save bonuses and its own
`save_dc`; a scripted `damage_per_round` routine (to-hit, dice, targets, type)
so the party actually takes attrition damage; resistances / immunities /
vulnerabilities; `legendary_resistances`; regeneration; and a `threat_profile`
driving target selection.

It is a **Test Double** in the precise xUnit sense — a configurable stand-in
implementing the full `Creature` interface, so the engine cannot tell it from a
real monster and no code path is special-cased for testing. That is what lets
Phases 1–3 proceed with zero bestiary work and still exercise the real turn
loop.

This makes the dummy the **experimental control**: sweep AC 10–22 and see where
Great Weapon Master stops being worth it, exactly as a WoW sim would.

---

# Phasing

- **Phase 0 — Skeleton + performance proof.** Package, CI, `core/rng.py`,
  dice, the hook registry and dispatcher, a stub attack loop. *Exit:* the
  ≥50k attacks/sec/core budget is measured and met.
- **Phase 1 — Engine core.** `Creature`, grid, timeline, action economy,
  conditions, resources, effects, damage/attack resolution, target dummy, CLI,
  stats. *Exit:* a level-5 Fighter with a greatsword kills a dummy and every
  number is reproduced by a hand-computed golden test.
- **Phase 2 — Paper pass, then the martial slice.** First classify all ~400
  features into the hook each needs — a day of work that decides whether the
  other 400 succeed, and the only reliable way to avoid hook drift. Then write
  the authoring skill, then grind Barbarian/Fighter/Rogue/Monk 1–12 with 16
  subclasses, 8 masteries, martial feats and the 20 maneuvers. **The API is
  frozen only after this slice passes.**
- **Phase 3 — Spell engine + casters.** Concentration, slots, upcasting, AoE
  templates; then Paladin/Ranger, then full casters, spells added on demand.
- **Phase 4 — Adventuring day.** Encounter chaining, rests, attrition
  reporting, APL tuning, the build-comparison harness.
- **Phase 5 — Monsters.** The 5etools stat-block parser, then the bestiary.

Phases 0–2 are the commitment being made now; 3–5 are sketched to prove the
architecture doesn't paint itself into a corner.

# Verification

```bash
pip install -e ".[dev]"
python -m dndsim content fetch          # populate gitignored data/ from 5etools
pytest tests/contract tests/golden tests/interaction
python -m dndsim.tools.check_features   # linter + no-op detector + coverage
python -m dndsim bench                  # asserts the sims/sec budget

python -m dndsim sim builds/fighter_gwm_l5.yaml --dummy ac=16 --iterations 10000
python -m dndsim day builds/party.yaml --encounters 6 --short-rests 2
python -m dndsim compare builds/a.yaml builds/b.yaml --sweep dummy.ac=10:22
```

Determinism is the backbone: identical seed + identical build ⇒ byte-identical
event log, so every bug is reproducible and every regression diff is readable.
`compare` and `--sweep` are what make this a sim rather than a calculator, and
are the primary manual check that results are sane.

# Biggest risks

1. **Hook drift** — feature #340 needs a hook that doesn't exist, and an agent
   improvises by mutating private state or picking a wrong hook that happens to
   fire. *Mitigated by:* the paper pass in Phase 2, the frozen registry with a
   CI diff check, the loud `NEEDS_HOOK` escape valve, and a linter banning
   private access and stray `random` imports.
2. **Interaction correctness** — features individually right, combinations
   wrong; grows quadratically while features grow linearly. *Mitigated by:*
   structural prevention first, one resolution order in one file, the
   human-maintained interaction matrix, and property tests (damage never
   negative, HP conservation, one reaction per round, effect count returns to
   baseline after every encounter).
3. **Performance** — a simulator too slow to run is one nobody uses.
   *Mitigated by:* the Phase 0 budget proof before the content grind, and a CI
   benchmark gate per PR.
