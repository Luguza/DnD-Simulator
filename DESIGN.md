# D&D 5e (2024) Combat Simulator — "SimC for D&D"

Founding design document. At the time of writing the repository contained no
code. [ROADMAP.md](ROADMAP.md) turns this into a dependency-ordered build plan.

## Context

A Monte Carlo combat simulator for D&D 5e 2024 rules (XPHB) — the D&D
equivalent of SimulationCraft: define a party in config files, simulate a full
adventuring day thousands of times, and get statistics about how a build
actually performs instead of back-of-envelope DPR math.

It is a **private personal repo, not for distribution**, so it may read the
5e.tools dataset freely. Bulk rules text still stays out of git (fetched on
demand into a gitignored `data/`) — to keep the repo small and diffs readable,
not for licensing reasons.

**The question v1 answers:** given a party and a sequence of encounters with
rests between them, *how far into the adventuring day does the party get before
resources run dry?* Attrition, not single-fight DPR.

**What "runs dry" means, precisely.** A metric this vague is a metric that
cannot be implemented, so the stop condition is settled here rather than
discovered in W6:

- A PC reduced to 0 HP falls unconscious and **rolls death saves** under the
  normal rules. Death saves are a hook (`on_death_save`), so Relentless
  Endurance and similar features attach to them like anything else.
- **Healing is an APL action, not engine special-casing.** Healing Word,
  Cure Wounds, potions, Second Wind and the Medicine-check stabilise are
  ordinary actions the APL may take, gated by ordinary conditions
  (`ally.down`, `ally.hp_pct < 0.3`). The healing-word yo-yo therefore falls
  out of policy rather than being modelled — and is directly comparable
  between builds, which is the point.
- The day ends on a **TPK**, or when the party's declared **retreat
  threshold** is met (`party.retreat_when: "allies.down >= 2"` in the party
  YAML). The threshold is a build-file knob, so `--sweep` over it is a
  legitimate experiment rather than a hardcoded assumption.

Everything in the v1 headline number is downstream of these three choices;
changing any of them changes every reported result, which is why they are
settled decisions and not implementation detail.

## Settled decisions

| Decision | Choice |
|---|---|
| Language | Python 3.11+, OOP, library-first with a CLI adapter |
| Ruleset | XPHB (2024), **levels 1–12** |
| Positioning | Square grid, 5 ft cells, real distance, opportunity attacks, true AoE templates |
| Movement | **An APL decision, not an engine heuristic** — a `move:` clause, same grammar as `if:` |
| Targeting | **An APL decision** — a `target:` selector expression per entry |
| Policy | SimC-style Action Priority List in build YAML |
| v1 output | Adventuring-day attrition |
| Death & stop condition | Death saves rolled; healing is an APL action; day ends on TPK or a declared retreat threshold |
| Content order | **Martials first, full vertical slice** (Barbarian/Fighter/Rogue/Monk, 16 subclasses, levels 1–12, masteries, martial feats) |
| Species | **In scope** — the ~18 combat-relevant 2024 species traits |
| Spells | **Engine built up front, individual spells added on demand** |
| **Out of v1 scope** | **Multiclassing, magic items and attunement** — see below |

Levels 1–12 removes 7th–9th level spells and tier-4 features — the
hardest-to-model content — from scope. It also caps prepared spell levels at
6th, which is why `spells/` only needs `level0..level6`.

**The two explicit exclusions.** Both are cheap to state now and expensive to
argue about mid-grind, which is the only reason they appear in a settled-decisions
table at all:

- **Multiclassing is out.** The build loader accepts a single class with levels,
  not a list. The multiclass spell-slot table, non-stacking Extra Attack and
  competing Unarmored Defense variants all complicate `builds/loader.py` and
  `content/tables.py` for a v1 question — attrition — that a single-classed party
  answers perfectly well. A batch that meets a multiclass-only interaction
  declares `NEEDS_HOOK` rather than approximating it.
- **Magic items and attunement are out.** `HookSource` is already the right shape
  for them, so this is a scope decision rather than an architectural one, and v2
  adds them without an engine change. Until then a build cannot express a `+1`
  weapon, and comparisons are between unequipped builds only. This is worth
  re-examining at M4, because it is the most common question a sim gets asked.

**Species are in.** Relentless Endurance, Breath Weapon, Savage Attacks and
Large Form are combat-relevant, fit the existing feature API with no new
machinery, and are roughly 18 implementations — small enough to batch alongside
the martial grind. They live in `features/species/` and arrive through the same
content ACL as everything else, off `species.json`.

## Reconnaissance (done, against live data)

`raw.githubusercontent.com/5etools-mirror-3/5etools-src/main/data` is reachable
through the proxy; `5e.tools` direct returns 403.

| Data | State | Verdict |
|---|---|---|
| Weapons & armour (`items-base.json`) | Structured: `dmg1`, `dmg2`, `dmgType`, `property`, `mastery`, `range`; armour carries `ac`, `armor`, `strength`, `stealth` | **Import directly** — AC is derived from equipment, never written into a build |
| Masteries / item properties | `itemMastery`, `itemProperty`; 8 masteries | Numbers import, behaviour hand-coded |
| Class scaling (`classTableGroups`) | Machine-readable per-level rows (Rages, Rage Damage, Sneak dice…) | **Import directly** |
| Class/subclass features | Prose in `entries[]`, `refSubclassFeature` indirection | Hand-coded |
| Spells (391 XPHB) | Skeleton structured (`level`, `savingThrow`, `spellAttack`, `scalingLevelDice`); mechanic is prose | Skeleton imports, behaviour hand-coded |
| Monsters (503 XMM) | Prose, but a **closed tag vocabulary**: `{@atkr}`, `{@hit}`, `{@h}`, `{@damage}`, `{@actSave}`, `{@dc}`, `{@recharge}` | Parseable — deferred to W7 |
| Feats (77 XPHB) | 2024 fighting styles are feats | Hand-coded |

### Content inventory — the real cost centre

| Bucket | XPHB count |
|---|---|
| Class features | 283 |
| Subclass features | 309 (48 subclasses) |
| Feats | 77 |
| Optional features | 58 (28 invocations, 20 maneuvers, 10 metamagic) |
| Weapon masteries | 8 |
| **Non-spell entries** | **735** |
| Combat spells | 212 (103 concentration) |

Not all 735 non-spell entries need code — Ability Score Improvement, Expertise,
ritual casting, pure exploration utility and the reference-list wrappers (Battle
Master's "Maneuver Options" is a pointer, not content) drop out. Four numbers
are used consistently throughout both documents:

- **~400 combat-relevant features** — the non-spell grind, and the number the
  paper pass classifies.
- **~18 species traits** — not part of the 735 (they live in `species.json`),
  small enough to ride along with the martial batches.
- **212 combat spells** — a separate grind, pulled on demand.
- **~630 implementations** at full coverage.

This drives every architectural decision below: the feature API must be
learnable from a short document, uniform across wildly different mechanics, and
**hard to get subtly wrong**, because most of these will be written by subagents
in parallel batches with limited context.

## Libraries before reimplementing

`pydantic` (build/APL schema validation — the main fail-loudly lever),
`numpy` (statistics, confidence intervals), `typer` + `rich` (CLI, reports),
`pytest` + `hypothesis`, `PyYAML`. Dice notation gets a small hand-rolled
tokeniser (the grammar is tiny and it sits on the hottest path); APL conditions
get a hand-written AST whitelist rather than `simpleeval`, because load-time
name resolution against a declared schema is the whole point and no library
does that part.

---

## The five load-bearing ideas

Everything else in this document is consequence. If you read only one section,
read this one — and if a later decision contradicts one of these five, the later
decision is wrong.

1. **The engine calls features; features never drive the engine.** Every
   extension point is a named hook the dispatcher invokes with one context
   object. ~630 independently-authored units can coexist only because none of
   them is in control.
2. **Contexts are attribute-locked; features call mutators, never assign
   fields.** Two features cannot clobber each other because nobody writes a
   field. This is encapsulation enforced at runtime rather than by convention,
   and it is what makes parallel low-context authoring survivable.
3. **Make illegal states unrepresentable rather than tested.** `DamagePart`
   splits `dice` from `flat` so crits cannot be got wrong; advantage is a
   collection of sources so stacking cannot be got wrong; `_resist` is a set so
   double resistance cannot happen. Every rule made structural is a rule that
   never needs a test.
4. **Fail at the earliest moment the information exists.** Unknown hook name at
   import; unknown feature id or unparseable APL path at load; contract
   violations at runtime. Silence is the enemy, not verbosity.
5. **Determinism is structural, not intended.** One RNG protocol, a linter ban
   on `import random`, and per-iteration seeding. Seed plus build reproduces a
   run byte for byte, which is what makes every bug reproducible and every
   regression diff readable.

The full pattern-name catalogue — which GoF pattern each module corresponds to,
and why — lives in [docs/patterns.md](docs/patterns.md). It is a vocabulary for
maintainers arguing about the design, deliberately kept **out of this document
and out of every batch agent's context**: an author needs "append a source,
don't set a flag", not "this is a Parameter Object", and pattern names in a
batch prompt produce code that is consistent with the vibe and wrong about the
rule.

Two patterns are **deliberately rejected**, and the reasons are the design's
load-bearing arguments: **Observer / Event Aggregator** (the pub-sub bus) and
**Template Method via mixin inheritance** for feature composition. Both are
argued immediately below.

## Hook system: typed pipelines over attribute-locked contexts

Rejected the **Observer / Event Aggregator** pattern (a pub-sub bus) —
`bus.subscribe("on_hitt", fn)` is a silent no-op, and with ~400 features written
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
`ctx.veto(...)`, which is the Chain of Responsibility element — the only place a
handler halts the chain rather than contributing to it.

The whole feature API is **Inversion of Control** (the Hollywood Principle): the
engine calls features at defined points; features never drive the engine. That
is what allows ~400 independently-authored units to coexist.

Four properties make it safe for parallel low-context authors:

1. **Hook names validated at import time** against a closed **Registry** — typos
   are `ImportError`, not silence.
2. **Handler signatures validated at class creation** via `__init_subclass__` —
   a **Template Method** used the way it works well here: a fixed validation
   skeleton subclasses cannot skip or forget to call. Wrong context annotation
   or missing return annotation is `ImportError`.
3. **Contexts are attribute-locked.** Features never assign fields; they call
   mutator methods (`ctx.grant_advantage()`, `ctx.damage.add(...)`). This is
   **encapsulation enforced at runtime** rather than by convention — two
   features cannot clobber each other because nobody writes a field.
4. **Provenance is automatic.** The dispatcher stamps the current source before
   each call — an **Ambient Context** scoped to one handler invocation — so
   every modifier is attributed with no author effort, giving the per-feature
   damage breakdown for free.

### Hook scope — the single biggest learnability lever

Every hook declares `Scope`: `SELF` (owner is the actor), `TARGET` (owner is the
one being hit), `OBSERVER` (owner merely perceives it), `GLOBAL`. In an
`on_incoming_damage` handler `self.owner` is *the victim*, with no prose needed.
No feature ever writes `if ctx.attacker is self.owner`.

### The closed hook list — 39 points

| Group | Hooks | Count |
|---|---|---|
| Timeline | `on_encounter_start` / `_end`, `on_initiative_roll`, `on_round_start` / `_end`, `on_turn_start` / `_end`, `on_short_rest`, `on_long_rest` | 9 |
| Attack | `on_declare_attack`, `on_incoming_attack_declared`, `on_attack_rolled`, `on_hit`, `on_hit_taken`, `on_miss`, `on_attack_resolved` | 7 |
| Damage | `on_damage_roll`, `on_incoming_damage`, `on_damage_taken`, `on_reduced_to_zero`, `on_kill` | 5 |
| Healing & dying | `on_heal`, `on_temp_hp_gained`, `on_death_save`, `on_stabilize` | 4 |
| Checks & saves | `on_ability_check`, `on_saving_throw`, `on_save_resolved`, `on_forced_save` | 4 |
| Casting | `on_cast_spell`, `on_concentration_start`, `on_concentration_check` | 3 |
| Effects | `on_condition_gained` / `_lost`, `on_effect_applied` / `_expired` | 4 |
| Movement & resources | `on_move`, `on_leave_reach`, `on_resource_spent` | 3 |

The **Healing & dying** group exists because the v1 metric is attrition, and
attrition is HP in versus HP out. Without it Second Wind, hit dice on a short
rest, Cure Wounds, potions, Song of Rest and Relentless Endurance have nowhere
to attach, and `on_temp_hp_gained` is needed because temp HP appears in the
damage resolution order below with nothing able to observe it. Cheap now,
impossible after the W3 freeze.

`on_initiative_roll` is in the list for the same reason and was found the same
way. Barbarian Feral Instinct (level 7) and Monk Uncanny Metabolism (level 2) are
both inside the levels 1–12 martial slice and both fire when initiative is
rolled, as does the Alert origin feat. Without the hook they are three
`NEEDS_HOOK` rows in W4 against a registry frozen in W3 — the exact failure the
freeze is supposed to prevent, arriving through the freeze itself.

The registry is **frozen** to feature-batch authors; CI fails any batch PR
touching `hooks/`, `engine/` or `core/`. A closed, frozen extension point is the
**Open/Closed Principle** applied literally: features extend behaviour without
modifying the engine, and the CI rule makes that structural rather than
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
item here is a **Value Object** — frozen, self-describing, safely shared — doing
work that would otherwise be procedural code an author could misplace:

- **Crit doubling.** `DamagePart` carries `dice` and `flat` as separate fields;
  `ctx.damage.add(dice="2d6")` doubles on a crit, `add(flat=3)` never does.
  Sneak Attack and Rage damage cannot be mis-crit because the author never
  touches the crit path.
- **Advantage.** Not a boolean — `grant_advantage()` appends a source to a
  collection. D&D's rule (any adv + any disadv = flat, multiples don't stack)
  falls out of the derived `had_advantage`. Two features granting advantage is
  idempotent, so ordering cannot matter.
- **Resistance.** `_resist` is a `set`, so a type can't be resisted twice.
- **Damage resolution order** lives in exactly one file, `engine/damage.py` — a
  **Policy object owned by the engine, not by features**: immunity →
  vulnerability → resistance → flat reductions → halvings (Uncanny Dodge) →
  temp HP → HP. Documented verbatim in the skill doc. Two of those steps are
  *rulings* rather than quotations — flat reductions applying after resistance,
  and Uncanny Dodge's halving applying after the flat reduction — so
  `engine/damage.py` carries the citation for each inline. Both change every
  reported number, and neither should be re-argued in a code review three years
  from now with no record of what was decided.

This is *"make illegal states unrepresentable"* rather than *"test for illegal
states"*. Every rule made structural is one that never needs a test — the
highest-leverage move available when ~630 units are written by different hands.

## Durations: one mechanism, three shapes

A totally ordered clock, `TimeKey(round, initiative_index, phase)` where phase is
`START`(0) or `END`(1) — a **Value Object** whose natural ordering *is* the
timeline, so expiry is a comparison rather than bespoke arithmetic. `Duration` is
a declarative **Value Object** too, with named constructors
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
| **Reckless Attack** | `until_start_of_next_turn_of(self)` |
| **Vex** | `until_end_of_next_turn_of(self)` **plus `charges=1`** keyed to a target id — whichever runs out first removes it |

`Effect`, `Condition`, `Feature` and magic items are all `HookSource` subclasses
carrying hooks identically. **One API shape for all four** — an agent who has
read Rage can write Frightened without reading anything else.

`Effect` and `Condition` are the **State** pattern: Raging, Prone and Frightened
change a creature's behaviour by being *attached objects with their own
handlers*, not by flags the engine branches on. There is no
`if creature.is_raging` anywhere in the engine — which is precisely why adding a
new condition never requires touching engine code. Stacking policy
(`REFRESH | STACK | UNIQUE_PER_SOURCE | REJECT`) is a **Strategy** on the
effect, so "does a second casting stack?" is declared, not re-implemented.

## Resources: one model, no special cases

`ResourceSpec(key, label, maximum, recovery, die=None, slot_level=None)` is an
immutable **Value Object / Flyweight** — one spec instance is shared by every
creature that has the resource, while the mutable `ResourcePool` holds only the
current count. `maximum` may be a literal, a class-table column, or a callable,
and `recovery` is a tuple of `Recovery(trigger, amount)`.

Seven shapes cover every resource in the game:

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

Every spend is appended to the structured event log, so the resource-spend report
is a group-by over that log rather than per-feature reporting code — the log is
the single **audit record**, which is also what makes seeded runs diffable.

## APL and mid-attack decisions

The APL as a whole is a **Strategy**: the creature's decision-making is a
pluggable policy object. That is what makes build-vs-build comparison possible at
all — swap the policy, hold everything else fixed. A built-in scoring heuristic
can later be dropped in as an alternative `Policy` implementation without the
engine noticing.

**The unifying idea:** a turn action, a reaction, and a mid-attack rider are all
"the engine has an offer; does the APL take it?" One flat ordered list; the
engine filters to currently-offered ids and takes the first passing entry.
Individual actions are **Command** objects (`is_legal()` / `execute()`), which is
what lets the turn loop re-poll after every action — and is why Action Surge
needs no special case: it simply grants another action slot and the loop asks
again.

```yaml
apl:
  - { action: rage,            if: "not self.raging and enemies.within(30) >= 1" }
  - { action: reckless_attack, if: "self.hp_pct > 0.4 or target.ac >= 17" }
  - action: divine_smite
    if:     "trigger.crit or (self.slots.highest >= 3 and target.hp > 25)"
    with:   { slot_level: "3 if trigger.crit else self.slots.highest" }
  - { action: uncanny_dodge,   if: "trigger.damage_estimate >= self.hp * 0.25" }
  - action: healing_word
    if:     "allies.down >= 1"
    target: "first(allies.down)"
  - action: attack
    target: "lowest_hp of enemies.in_reach"
    move:   "toward lowest_hp of enemies until in_reach"
```

**Three clauses, one grammar.** `if:` decides *whether*, `target:` decides *on
whom*, `move:` decides *from where* — all three compile through the same AST
whitelist against the same namespace schema, so an author learns one language
and a typo in any of them fails at load.

- **`target:`** is a selector expression returning one creature or an ordered
  list: `lowest_hp of enemies.in_reach`, `nearest of enemies`,
  `first(allies.down)`. Omitted, it defaults to the engine's current offer —
  which for a rider or reaction is the creature that triggered it, and for an
  attack is the nearest enemy in reach. Without this clause the APL cannot
  express focus-fire versus spread damage, and those differ by more than most
  feats do.
- **`move:`** is why the grid is not decorative. The turn loop offers movement
  the same way it offers actions, and the APL takes it or does not:
  `toward <selector> until in_reach`, `away_from <selector> to_max_range`,
  `hold`. Opportunity attacks, Cleave adjacency, Monk mobility, ranged kiting
  and every AoE template only mean something once somebody decides where to
  stand, and that decision belongs in the policy rather than in an engine
  heuristic nobody can compare against.

Movement is a **decision, not a pathfinding problem**: the selector picks a
destination and the engine walks the shortest legal route to it, provoking on
the way out of reach as normal. There is no tactical pathing, and none is
planned — an APL that wants a specific route says so with a sequence of `move:`
entries.

**Default policy: riders and reactions are OFF when unmentioned** — a **Null
Object** standing in for the absent entry, so adding a feature never silently
changes every existing build's numbers. If a turn falls through with no legal
entry the creature ends its turn and an `APL_FALLTHROUGH` counter is surfaced in
the report — a build falling through 40% of turns is misconfigured and you want
to see it. Movement is the one exception to OFF-by-default: a build with no
`move:` clause anywhere closes to reach of its nearest enemy, because the
alternative is a melee build that stands still and reports zero damage.

**Condition language: the Interpreter pattern, over an AST whitelist.** The `if:`
clauses are a small expression grammar with its own compile step, not `eval` of
source: parse, walk, reject any node outside an allowlist (no comprehensions,
lambdas, imports, dunder access, assignment), reject private attributes, then
resolve every dotted path against a declared `NamespaceSchema` at **load time**
with `difflib` suggestions. `self.ragging` fails when the party loads — not
silently as `False` at simulation-minute-nine.

Each compiled condition is a **Specification**: a reusable boolean predicate over
domain objects, composable with `and`/`or`, and — because the compiler records
which paths each one reads — introspectable, which is what lets the loader prove
an APL never references state a build doesn't have.

The namespace roots (`self`, `target`, `trigger`, `enemies`, …) are **Proxy**
objects built once per creature holding weak references to live state, so
evaluation is attribute access rather than object construction.

## Failing loudly — layered

This is **Design by Contract** with **Fail-Fast** escalation: each layer states a
precondition and refuses to continue when it is violated, as early as the
information exists to check it. `FeatureContractError` is named for exactly that
— a handler that returns the wrong type has broken its contract, and the run
stops rather than absorbing it.

**Import time:** unknown hook name; bad handler signature or context annotation;
missing return annotation; duplicate/malformed feature id; missing `source_ref`
rulebook citation.

**Load time:** unknown feature id (with suggestions); APL action id no attached
feature provides; unparseable condition or unknown namespace path; `with:` params
not matching the feature's declared `params`; declared features cross-checked
against the class progression table, warning on omissions.

**Runtime invariants (cheap, always on):** non-`HookResult` return; mutator
called in the wrong context phase (`grant_advantage` after the roll); direct
field assignment on a context; resource going negative or spent during a legality
query; **a second reaction spent before the creature's own turn has begun**;
attack chain depth > 3.

> The reaction invariant is stated on the **turn** boundary, not the round
> boundary, because 2024's rule is that you regain your reaction *at the start of
> each of your turns*. A creature high in initiative may legitimately react on an
> enemy's turn, take its own turn, and react again later in the same round.
> Accounting per round raises on that legal sequence and misses a genuinely
> illegal one — it is the kind of invariant that would halt correct simulations.

**Golden tests — necessary, and not sufficient.** Every feature ships one,
enforced by a collection test that diffs `FEATURE_REGISTRY` against tests found
on disk. Goldens run on a `ScriptedRng` returning a fixed sequence, so
assertions are exact integers rather than distributions:

```python
# level-5 rogue, +3 DEX, dagger, attacking with Advantage
# XPHB Sneak Attack, level 5: 3d6. Expected damage:
#   dagger 1d4 -> 3  +  DEX 3  +  sneak 3d6 (4+5+6) -> 15  =  21
with scripted_rng([18, 11, 3, 4, 5, 6]):      # 2d20 adv, d4 dagger, 3d6 sneak
    result = rogue.attack(dummy, advantage_from=["test.setup"])
assert result.total_damage == 21
assert result.parts_by_source["rogue.sneak_attack"] == 15
```

**Be honest about what a golden proves.** The agent that writes the feature
writes its test, so a green golden suite is evidence that the code is
self-consistent, not that it matches the rulebook. Three rules turn goldens back
into a real check, and all three are enforced rather than encouraged:

1. **The expected number is written out as arithmetic in a comment**, derived
   from the rules text and not from the implementation — as above. An author who
   cannot produce that line does not understand the feature, and the comment is
   what makes review a five-second read instead of a re-derivation.
2. **`source_ref` carries the quoted rules sentence**, not just a citation (see
   § The feature-authoring skill). A reviewer compares the quote to the
   assertion with no book open.
3. **A read-only reviewer pass per batch** — an agent that sees the quoted rules
   text and the code but not the author's reasoning, **re-derives every expected
   number from the rules text alone**, and reports discrepancies only. The author
   still writes the golden, because the author is the one who has to understand
   the feature; what the author must not be is the only party who has ever
   computed the number. This is the check goldens structurally cannot be, and it
   is cheap precisely because rules 1 and 2 put everything it needs in the file.

**No-op detector.** After the golden suite — and on **every batch PR**, not only
nightly — any feature whose hooks never once returned `APPLIED` is reported.
This catches the most common agent bug by far: a condition that can never be
true (wrong enum member, inverted check).

**Interaction matrix.** ~50 hand-computed combos maintained by a human, not by
batch agents — Rage + Uncanny Dodge + vulnerability, Sneak Attack + crit + GWM,
Cleave + Vex, Smite on a crit. This is the regression net for ordering changes,
and the defence against "every feature is individually right, the total is 8%
off".

**Statistical harness — per batch, not nightly.** Features declare an analytic
expectation and 200k simulated attacks are compared against it — a Wilson
interval for rates (hit chance, crit rate, save failure) and a Student-t
interval for means (damage per round). Catches the "compiles, tests pass,
numbers wrong" class, and it is the **only oracle in the project an author
cannot fake**, which is why it runs against each batch rather than overnight.

That property has to be defended: an expectation computed by calling the engine
passes forever and means nothing. So an expectation **must be a literal number
or a closed-form expression of declared inputs** (AC, ability modifier,
proficiency bonus, level) — any import from `dndsim` inside an expectation is a
linter error, not a review comment.

**Linter** (`dndsim check`), enforcing the rules above plus the rules that keep
agent-authored content from drifting:

| Rule | Why |
|---|---|
| `LATE`/`FINAL` without a `# PHASE:` comment | Keeps the order-sensitive set enumerable by `rg` |
| Access to `_`-prefixed attributes | The attribute lock is worthless if features reach around it |
| `import random` outside `core/rng.py` | Determinism is structural |
| Missing `source_ref`, or one without a quoted rules sentence | Makes review mechanical |
| `dndsim` imports inside an analytic expectation | Stops the oracle from becoming a tautology |
| `try`/`except` anywhere in `features/` or `spells/` | Defensive blocks swallow the fail-loud design |
| `hasattr` / `getattr` / `isinstance` chains on domain objects | The same routing-around, differently spelled |
| A feature file over **60 lines** | A feature needing more is a design smell; it goes back to the architect |
| A batch PR touching `hooks/`, `engine/`, `core/`, or any file outside its packet's allowlist | The freeze is structural or it is nothing |
| A new third-party dependency in a content batch | — |

The file-size cap is the least conventional rule here and the most useful. It
costs an occasional argument about a genuinely large feature, and in exchange it
kills speculative generality before it is written — which is the single most
common shape of agent-authored slop.

## Performance

~10k iterations × 5 encounters × 5 rounds × 5 creatures × 3 attacks ≈ **4M
attack resolutions** per build comparison. Budget: **≥30k attack
resolutions/sec/core**, measured in W0 — before any content is written against
the architecture. About two minutes a core for one comparison, or under twenty
seconds across eight.

> **That number was 50k until M0, and the spike is what changed it.** The
> original was set before anything had been measured. `w0i` measured 17k on the
> hostile configuration, a revision pass took it to ~31.5k, and a floor
> calculation put 50k at the theoretical limit of this object model in CPython —
> reachable only by giving up the Parameter Object per hook, the automatic
> provenance per contribution, or the frozen value per damage part, which are
> the things that make ~630 agent-written features safe. The full argument, the
> per-step measurements and the two levers taken (a compiled `reopen()`, and
> MT19937 words in place of PCG32) are in `docs/decisions.md` § w0i.

### The spike must be pessimistic, or M0 proves nothing

The obvious way to run `w0h` is against a stub creature and an attack loop. That
measurement is worthless: a stub has ~0 attached handlers, no APL evaluation, no
effect-expiry scan and no geometry query. It will clear the budget comfortably
whatever the budget is, M0 gets declared, and the real number arrives in W4 with 400 features already
written against a dispatch design nobody can now afford to change. **M0 exists
to be the cheap moment to revise; a stub spends that moment on nothing.**

So the spike benchmarks a deliberately hostile synthetic creature:

- **~25 attached `HookSource`s** spread across the hot hooks — at least 15 of
  them on `on_hit`, which is what a level-12 Barbarian actually looks like.
- **An 8-condition APL** re-evaluated per action, plus a `target:` selector and
  a `move:` clause, since all three run every turn.
- **Real movement on a real grid**, so distance and reach queries are in the
  measurement rather than optimised away.
- **Worker startup measured separately.** Windows uses `spawn`, not `fork`:
  every worker re-imports the package and rebuilds every character from YAML.
  At 10k iterations that amortises to nothing; at `compare --sweep dummy.ac=10:22`
  it is paid 13 times over, and it is the cost most likely to be missed by a
  benchmark that only times the inner loop.

If that configuration misses the budget, the architecture is revised in W0. That
is the whole point of the milestone — and it is what happened: see the note
above and `docs/decisions.md` § w0i.

### Levers

Per-creature precomputed dispatch tables (never scan a global bus); `__slots__`
on every hot object; contexts reset and reused per attack rather than allocated,
which matters at 4M resolutions; immutable specs (`ResourceSpec`, `Duration`,
weapon definitions) shared across every creature; APL compiled once per build;
structured tuple logging with string formatting deferred to the reporter — worth
~2–3× on its own (`--log-level=summary`); multiprocessing across iterations;
**adaptive stopping** on confidence-interval width rather than a fixed count.

**Seeding is per iteration, never per worker.** `rng = MersenneRng(mix(master_seed,
iteration_index))`, so iteration 4,271 draws the same stream whether it ran on
worker 0 of 1 or worker 6 of 8. Seeding per worker would make every result
depend on `--jobs` and on scheduling, quietly breaking the reproducibility
guarantee below — the cost is one RNG init per iteration, which is nothing
against a whole encounter.

Note the tension worth naming: pooled mutable contexts are in direct conflict
with the frozen-value discipline above. The split is deliberate — **contexts are
pooled and mutable** (hot path, engine-owned), **everything a feature stores is
frozen** (cold path, author-owned). The attribute lock is what keeps authors on
the safe side of that line.

### The benchmark gate

A CI benchmark gate fails the build on a >10% sims/sec regression — performance
decays one feature at a time. Two details decide whether that gate survives
contact with a shared CI runner:

- **Median of N runs with a warmup**, never a single timing.
- **Compared against a same-run microbenchmark baseline**, not an absolute
  number, so a slow runner scales both sides.

A gate that fires on noise gets disabled within a month, at which point
performance decays exactly as predicted. Run it tracked-but-non-blocking until
its variance is known, then turn on the failure.

---

## Interfaces: library, CLI, and files

**Ports & Adapters (Hexagonal Architecture).** The simulator is a *library* with
a stable programmatic API; the CLI is a thin adapter over it. A web UI added
later is another adapter against the same API — never a scraper of CLI output,
and never a reimplementation.

```python
from dndsim import run_sim, run_day, SimConfig, SimResult

result: SimResult = run_sim(SimConfig(build=..., dummy=..., iterations=10_000, seed=42))
```

`SimResult` is a frozen, serialisable dataclass. Everything the CLI prints is
derived from it, so a renderer cannot report something the API doesn't expose.

### What is a file, and what is not

| Artefact | Location & format | Why |
|---|---|---|
| Builds, parties, encounters | `builds/*.yaml`, **in git** | You author and edit these. Diffable, reviewable. |
| Fetched rules data | `data/`, **gitignored** | Third-party, bulky, reproducible from `dndsim content fetch`. |
| Results | JSON (`SimResult`) | The machine contract a UI consumes. Schema-versioned. |
| Terminal output | rendered from that JSON | A **Strategy** (`--format text\|json`), so text and JSON cannot disagree. |
| Event trace | JSONL, opt-in | Huge — one run's blow-by-blow, for debugging, never part of a report. |
| Runtime objects | **never serialised** | See below. |

**Runtime objects are deliberately not persisted.** A mid-simulation `Creature`
is not a document: dumping and reloading it would create a second source of truth
that drifts from the YAML that produced it, and invites "resume from a saved
state" semantics the engine does not support. Determinism removes the need
entirely — **seed + build hash + engine version + content version reproduces any
run exactly**, so a result file carries *provenance*, not state. Every
`SimResult` embeds those four fields; without them, comparing two runs months
apart is meaningless. The content version is the **upstream 5etools commit SHA**
that `dndsim content fetch` pinned, not a local timestamp — the mirror changes
without notice, and a date does not identify what was read.

Note what is *not* in that list: `--jobs`. Reproduction is independent of worker
count only because seeding is per iteration rather than per worker (§
Performance), *and* because worker records are merged back in iteration order
rather than completion order — the seeding rule alone does not make a log
byte-identical if the merge is racy. If either ever changes, this guarantee
silently becomes false, and every archived result becomes incomparable with no
error anywhere.

### CLI surface

Installed as a console script. `python -m dndsim <cmd>` is equivalent.

```bash
dndsim content fetch                                  # populate gitignored data/
dndsim sim BUILD --dummy ac=16 -n 10000 [--format text|json] [-o result.json]
dndsim day PARTY --encounters 6 --short-rests 2
dndsim compare A B --sweep dummy.ac=10:22
dndsim inspect BUILD                                  # resolved character, readable
dndsim validate BUILD                                 # schema + APL check; exit code
dndsim trace BUILD --seed 42                          # replay one fight, blow by blow
dndsim check                                          # feature linter + no-op detector
dndsim bench                                          # asserts the sims/sec budget
```

Global options: `--seed`, `--log-level=summary|full`, `--jobs`.

Three of these exist because of decisions made elsewhere in this document:

- **`inspect`** dumps the *resolved* build — every feature actually attached,
  every resource with its recovery rule, the APL as parsed, and which entries are
  unreachable. This is how you debug a build without reading engine code, and it
  is a report *about* objects rather than a serialisation *of* them.
- **`trace`** replays a single deterministic fight in readable text, each line
  attributed to the handler that produced it (provenance is stamped automatically
  by the dispatcher). It is how a wrong number gets chased down to the feature
  responsible. Only possible because runs are reproducible.
- **`validate`** applies the load-time failures above as a fast exit code, so CI
  and batch authors can check a build without simulating it.

---

## Module layout

```
src/dndsim/
  api.py            # public surface: run_sim, run_day, SimConfig, SimResult
  cli.py            # thin Typer adapter over api.py — no simulation logic
  core/       rng.py dice.py geometry.py log.py errors.py timeline.py
  rules/      enums.py constants.py
  hooks/      registry.py definitions.py decorator.py source.py
              dispatch.py contexts.py
  creatures/  creature.py character.py monster.py dummy.py stats.py
              resources.py economy.py effects.py
  conditions/
  features/   base.py params.py TEMPLATE.py
              classes/<class>/[subclasses/<subclass>/] feats/ masteries/
              fighting_styles/ species/
  actions/    base.py attack.py opportunity_attack.py dash.py dodge.py
              disengage.py cast_spell.py
  spells/     base.py concentration.py templates.py level0..level6/
  apl/        expr.py schema.py namespace.py apl.py
  builds/     schema.py loader.py
  engine/     world.py query.py attack.py damage.py saves.py turn.py
              encounter.py adventuring_day.py triggers.py
  content/    fetch.py loader.py refs.py tables.py weapons.py
  sim/        runner.py day.py compare.py sweep.py
  reporting/  result.py collect.py stats.py render_text.py render_json.py
  tools/      check_features.py noop_report.py gen_docs.py skeleton.py

builds/     # hand-authored YAML: characters, parties, encounters — in git
data/       # gitignored: fetched 5etools JSON
docs/       # hooks.md + apl.md GENERATED; feature-hook-map.md + patterns.md authored
tests/      contract/ golden/ interaction/ statistical/ bench/
.claude/skills/  feature-authoring/  feature-review/  statblock-import/
```

The pattern-name rationale for each package lives in
[docs/patterns.md](docs/patterns.md), not here. Three boundaries are load-bearing
enough to restate in place:

- **`actions/` vs `engine/triggers.py`.** `actions/` holds every **Command**,
  *regardless of which economy slot it costs* — action, bonus action or
  reaction. An opportunity attack is an `attack` Command answering a different
  offer, so its body lives in `actions/`, not in the engine. `engine/triggers.py`
  holds only the other half: which event fires an offer, who is in the audience,
  in what order, and the turn-boundary reaction invariant. The split is *what
  can be done* versus *when you are asked* — and it is why Action Surge needs no
  special case, since granting a slot and re-polling touches neither file.
  The name matches the APL's `trigger.*` namespace root, which this module
  populates.
- **`api.py` vs `cli.py`.** The CLI holds no simulation logic. A UI added later
  is another adapter against the same API, never a scraper of CLI output.
- **`content/loader.py`.** 5etools' vocabulary (`dmg1`, `atkr`,
  `classTableGroups`, `refSubclassFeature`) stops at this boundary and never
  reaches the domain model. The dataset is a third-party artefact that changes
  without notice, so this is the difference between "a schema change breaks the
  importer" and "a schema change breaks everything".

`docs/hooks.md` and `docs/apl.md` are **generated** from the registries —
**Single Source of Truth** applied to documentation. Hand-maintained docs drift,
and a drifted doc is how you get 40 wrong features in one batch.

## The feature-authoring skill

The user's explicit direction — *"we design skills for subagents to use in
crawling 5e.tools and parsing the prose into code"* — and the leverage that makes
~630 implementations tractable. It is a first-class deliverable, and if it needs
more than ~3 pages the API is too complicated and gets simplified rather than
documented harder.

`.claude/skills/feature-authoring/SKILL.md`:

1. The uniform shape (10 lines).
2. The generated hook table, **with the scope column**.
3. The five mutator families: `grant_advantage`, `damage.add`, `add_resistance`,
   `apply_effect`, `resources.spend`.
4. `once_per` vs `costs` vs `gate` — a paragraph each.
5. **The three gotchas, stated as rules:**
   - `Reset.TURN` (any turn boundary in initiative — real D&D "once per turn",
     which is why Sneak Attack works on an opportunity attack *and* again on your
     turn) vs `Reset.OWN_TURN` ("once on your turn"). The likeliest subtle bug;
     two distinct enum members, covered by a contract test.
   - `dice` doubles on crits, `flat` does not.
   - **The snapshot rule:** the handler list is snapshotted per event, so an
     effect applied during a hook does not fire for that same event — Reckless
     Attack must both apply its effect *and* call `ctx.grant_advantage()` for the
     triggering attack.
6. Rage, Sneak Attack and Uncanny Dodge verbatim as the three exemplars —
   passive+duration+resource, on-hit rider with once-per-turn, reaction with an
   APL gate.
7. Where numbers come from: `table_value(...)` reading imported
   `classTableGroups`. **Hardcoding a scaling number is a review failure.**
8. How to resolve `refSubclassFeature` / `refOptionalfeature` indirection in the
   source data.
9. **The two escape valves.** A feature that cannot be expressed at all
   declares `NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED`. A
   feature that *can* be expressed but only approximately declares
   `APPROXIMATED = "<what is lost, and why>"` and implements the approximation.
   A collection test fails listing both sets. Hook additions are batched into a
   deliberate core change.

   The second marker exists because the first one alone hides the more common
   failure. Most content that does not fit is not impossible — it is *lossy*:
   keyed on creature type, on illumination, on a condition not yet modelled, on
   a multiclass interaction that is out of scope. Without `APPROXIMATED` that
   lands as "implemented", passes its golden, and is quietly wrong. Neither
   marker is a failure: **blocked-and-visible beats improvised-and-wrong, and
   lossy-and-declared beats lossy-and-silent.**
10. **`source_ref` carries the quoted rules sentence**, not just a page
    citation. `source_ref = ("XPHB 51", "If you're able to make an Attack roll
    with Advantage and the attack uses Dexterity...")`. This is what makes
    review a side-by-side read rather than 630 trips to the book, and it is what
    the reviewer pass needs to work at all. A citation without a quote is a
    linter error.
11. **The analytic expectation.** Every feature declares `expectation` as a
    literal or a closed-form expression of declared inputs (AC, ability modifier,
    proficiency bonus, level). It is the one oracle in the project an author
    cannot fake, and it stays that way only because importing `dndsim` inside one
    is a linter error rather than a review comment.
12. Checklist: quoted `source_ref`, declared `expectation`, golden test with the
    arithmetic written out, `dndsim check`, disposition row.

The skill deliberately teaches **the five shapes, not the pattern names** — an
author needs "append a source, don't set a flag", not "this is a Parameter
Object". The pattern vocabulary lives in [docs/patterns.md](docs/patterns.md)
and in review, where it helps maintainers argue about the design; putting it in
the skill would spend scarce context on nomenclature instead of mechanics. For
the same reason, **DESIGN.md is not a batch agent's reading list** — the packet
points at the skill, the generated `docs/hooks.md` and `docs/apl.md`, and the
exemplars. Prose-heavy architecture in a batch context window produces features
that are consistent with the vibe and wrong about the rule.

`features/TEMPLATE.py` is a **Prototype** to copy — the fastest way to get an
author onto the happy path without them internalising the whole API.

`.claude/skills/statblock-import/` covers the W7 monster parser against the
closed tag vocabulary.

## The target dummy

The only "monster" until W7, fully configurable in YAML: `ac`, `hp`, `count`,
size, speed, grid placement; per-ability save bonuses and its own `save_dc`; a
scripted `damage_per_round` routine (to-hit, dice, targets, type) so the party
actually takes attrition damage; resistances / immunities / vulnerabilities;
`legendary_resistances`; regeneration; a `threat_profile` driving its target
selection; and a **`movement_profile`** — `static`, `closes_to_melee`, or
`kites_at(range)`.

The movement profile is not a detail. A dummy that never moves makes the grid
decorative: opportunity attacks never provoke, Cleave adjacency is whatever the
starting placement happened to be, and a Monk's mobility measures identically to
a Fighter standing still. `closes_to_melee` is the default because it is the
condition most martial content is balanced against; `kites_at` is what makes
ranged builds and Slow/Push masteries measurable.

It is a **Test Double** in the precise xUnit sense — a configurable stand-in
implementing the full `Creature` interface, so the engine cannot tell it from a
real monster and no code path is special-cased for testing. That is what lets
everything before W7 proceed with zero bestiary work while still exercising the
real turn loop.

This makes the dummy the **experimental control**: sweep AC 10–22 and see where
Great Weapon Master stops being worth it, exactly as a WoW sim would.

---

## Build order

Eight waves, `W0`–`W7`. This is the summary; [ROADMAP.md](ROADMAP.md) carries the
60 tasks, their dependency edges, delegation modes and the task-packet template.

| Wave | Content | Exit |
|---|---|---|
| **W0** | Scaffold, rules vocabulary, RNG/dice, timeline, hook registry, contexts, dispatch, **spike substrate + walking skeleton + pessimistic spike** | **M0:** ≥30k attack resolutions/sec/core on a 25-feature creature (revised down from 50k by the spike itself) |
| **W1** | `Creature` and AC derivation, resources, effects, grid, damage + attack resolution, saves, conditions, death saves, event log | interfaces importable |
| **W2** | Content importers, actions, turn/encounter loop, target dummy, APL, build loader, runner, API + CLI | **M1:** a level-5 Fighter kills a dummy, golden-verified |
| **W3** | Golden harness, linter, doc generators, reference exemplars, **the paper pass**, hook gap resolution, authoring skill, interaction matrix, statistical harness, **the canary batch** | **M2:** API frozen — *by a real batch, not by inspection* |
| **W4** | The martial grind — Barbarian/Fighter/Rogue/Monk 1–12, 16 subclasses, 8 masteries, martial feats, 20 maneuvers, ~18 species traits | **M3:** martial slice complete |
| **W5** | Spell engine, then Paladin/Ranger, then full casters; spells on demand | casters playable |
| **W6** | Rests, encounter chaining, attrition metrics, compare + sweep | **M4:** attrition v1 — the project's stated goal |
| **W7** | Stat-block parser, bestiary import | real monsters |

W0–W4 are the commitment being made now; W5–W7 are sketched to prove the
architecture doesn't paint itself into a corner. **W6 depends only on W4**, not
on W5 — attrition against target dummies is reachable with martials alone, so
M4 does not wait for casters.

Two of the exits above changed shape after review, and both changes are the same
argument: **a milestone that can be met by inspection has not been met.** M0 on
a stub proves the benchmark harness runs, not that the architecture is fast
enough. M2 on three hand-built exemplars proves the exemplars compile, not that
the API survives an author who has never seen it. Each therefore has a real
adversary attached — a hostile synthetic creature, and one disposable batch.

## Verification

```bash
uv sync
dndsim content fetch
uv run pytest tests/contract tests/golden tests/interaction
dndsim check
dndsim bench

dndsim validate builds/fighter_gwm_l5.yaml
dndsim sim builds/fighter_gwm_l5.yaml --dummy ac=16 -n 10000
dndsim trace builds/fighter_gwm_l5.yaml --seed 42
dndsim day builds/party.yaml --encounters 6 --short-rests 2
dndsim compare builds/a.yaml builds/b.yaml --sweep dummy.ac=10:22
```

Determinism is the backbone: identical seed + identical build ⇒ byte-identical
event log, so every bug is reproducible and every regression diff is readable.
`compare` and `--sweep` are what make this a sim rather than a calculator, and
are the primary manual check that results are sane.

## Biggest risks

Ordered by expected cost, which is not the same as by likelihood. The first three
share a shape worth naming: **each is a check that passes while proving nothing**,
and that is a far more dangerous failure than a check that fails.

1. **The performance spike passes on a stub.** M0 is the designated cheap moment
   to revise dispatch, and a stub benchmark spends it on nothing — the real
   number then arrives in W4 with 400 features already written against a design
   nobody can afford to change. *Mitigated by:* the hostile-configuration spike
   in § Performance, a benchmark gate built to survive CI noise, and the walking
   skeleton that makes W0's measurement an integrated one.
2. **Goldens grade their own homework.** The agent that writes the feature writes
   its test; a green suite is evidence of self-consistency, not of correctness.
   *Mitigated by:* hand-computed arithmetic in the golden, quoted `source_ref`, a
   read-only reviewer pass per batch, and — the only oracle an author cannot fake
   — the statistical harness, with expectations the linter forbids computing from
   engine code.
3. **The API freezes on an untested contract.** M2 rests on three hand-built
   exemplars and a paper exercise; a wrong API discovered in the sixth parallel
   batch costs six times the rework of one discovered in the first. *Mitigated
   by:* the canary batch in W3 — one small, semi-disposable slice authored
   against the frozen API before anything fans out.
4. **The paper pass degrades quietly.** Classifying ~400 prose features to hooks
   is the highest-leverage task in the project and the most tedious, and nothing
   downstream detects a sloppy classification until features are written against
   it. *Mitigated by:* batching the classification against a strict row schema
   with a confidence column, human triage of the low-confidence and gap rows
   only, and the additive-vs-structural split that makes the hook-gap budget
   honest.
5. **Review capacity, not agent throughput, is the constraint.** Roughly 150
   nominal agent-days funnel through one person; every parallelism gain lands on
   the same desk. *Mitigated by:* designing artefacts to be reviewable in minutes
   — disposition tables, quoted citations, arithmetic in goldens, the file-size
   cap — and by sizing batches for a review sitting rather than for agent
   capacity.
6. **Hook drift** — feature #340 needs a hook that doesn't exist, and an agent
   improvises by mutating private state or picking a wrong hook that happens to
   fire. Ranked below the others because it is the risk this design already
   defends best. *Mitigated by:* the paper pass in W3, the frozen registry with a
   CI diff check, both escape valves, and a linter banning private access and
   stray `random` imports.
7. **Interaction correctness** — features individually right, combinations wrong;
   grows quadratically while features grow linearly. *Mitigated by:* structural
   prevention first, one resolution order in one file, the human-maintained
   interaction matrix, and property tests (damage never negative, HP
   conservation, reaction economy per turn, effect count returns to baseline
   after every encounter).
