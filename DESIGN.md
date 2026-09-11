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

That question is only well posed once "played how?" is answered, and it must not
be answered by whoever wrote the build file. A party's attrition depends at least
as much on its action priorities as on its class features, so a hand-authored
policy makes every reported number a measurement of build *plus author*. **The
policy is therefore derived by search per build, not authored** — see § APL
derivation. An authored APL survives as the baseline the derived one is scored
against, and nothing else.

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
| Policy | SimC-style Action Priority List — the *language*; see the next row for where the list comes from |
| **Policy derivation** | **In scope for v1 — derived by statistical search per build, never authored.** A hand-written `apl:` is a baseline, not an input |
| Policy structure | Two layers: a **global fitted scorer** (`best_scored`) under a **per-build searched overlay** of departures. Score is in expected-damage points, gauged so a plain attack is 1 and doing nothing is 0 |
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

**The failure mode this creates, named by M0 so it is recognised next time: an
optimisation that skips a step it believed was the identity.** Two of the three
findings in `w0i`'s review had that exact shape — a `mitigate()` fast path that
skipped the floor-at-zero clamp along with the steps it was right to skip (so a
reduction larger than the hit arrived as negative damage, which is temporary hit
points granted by a sword), and a compiled `reopen()` that overwrote the raising
body meant to catch a context class which had escaped its decorator (so such a
class silently inherited a `reopen()` that left its own fields holding the
previous attack's values). A structural guarantee is only as strong as the
fastest path around it, and the hot path is where the paths around things get
written. **When a fast path skips a sequence, every step in that sequence needs
to be checked for being the identity — the clamp usually is not.**

## Durations: one mechanism, three shapes

A totally ordered clock, `TimeKey(round, initiative, phase)` where phase is
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

### Initiative is a key, not an index

The middle field of `TimeKey` is an `InitiativeKey` — a creature's *place* in the
order — because the population of an encounter changes while it runs. A summoned
creature enters; a dead one leaves. A dense seat index renumbers everyone below
the change, and every `Expiry` already resolved against the old numbering
silently starts naming a different creature's turn. The key is derived from the
roll and the roller and never from the roster, which gives three properties the
clock depends on: an arrival or a death changes **no other creature's key**; a
key stays a **valid position** after its creature is gone, so an effect anchored
to something that died still ends when its turn would have come (the sweep
compares, it never looks the anchor up); and the order stays **total**.

The comparison *is* the tie-break rule, in order: higher initiative total, then
higher Dexterity **score** (not modifier), then a PC before a monster, then the
lowest `tiebreak`, then the shortest `after` chain. Only the first is a
quotation. XPHB leaves a tie to the table
("the DM decides the order"), which is not a decision a simulator can make ten
thousand times a second; what it needs is a rule that is total, deterministic and
reproducible from a seed, so the remaining three are a **stated house rule**.
Handing ties to the party is a small systematic bias in the PCs' favour, constant
across the builds being compared — and the opposite convention is a bias too.

`tiebreak` is **one uniform draw from `[0, 1)` per creature**, not a position in
a shuffle of the entrants — because a shuffle has no honest answer for a
latecomer. It can only append the joiner last among the tied, and redrawing the
shuffle to place it fairly moves everybody already in the order, which is the
failure this whole type exists to prevent. An independent draw means a summon
arriving in round 3 lands in the order having cost nobody else a redraw. Two
equal draws are possible in principle — one encounter in ~2×10⁷ with twenty
combatants at 2⁻³² granularity — and the consequence is two tied creatures
ordered arbitrarily but still deterministically, which does not pay for a
shuffle. `engine/turn.py` (w2c) draws it as `(rng.roll(2**32) - 1) / 2**32`, so
`Rng`'s primitive stays a die face a golden test can script, and the quotient is
a dyadic rational — exact in binary, round-trips through the event log.

The one thing a single number cannot express is XPHB's summon rule: the spirit
"shares your Initiative count, but it takes its turn immediately after yours",
and no draw is guaranteed to fall between two others. So the chain is its own
field, `after: tuple[int, ...]`, empty for anything that rolled for itself and
appended to by `summoner.sharing_turn()`: for a shared draw `t`, `(t, ()) < (t,
(0,)) < (t, (0, 0)) < (t, (1,))`, all still inside the count `t` won, and moving
nobody.

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
engine noticing — and § The scorer is that heuristic arriving, as a *layer*
under this list rather than as a rival to it.

**The unifying idea:** a turn action, a reaction, and a mid-attack rider are all
"the engine has an offer; does the APL take it?" One flat ordered list; the
engine filters to currently-offered ids and takes the first passing entry.
**Filtering to what the action economy can still pay is part of that filter,
not an optimisation of it** — M0 found the alternative by accident: a self-buff
charged an Action instead of a Bonus Action, sitting above `attack` with an
`if:` its own effect did not clear, so the creature bought the buff once and
never swung again for the rest of the encounter. It cost 0.07% of the rate,
which is precisely why nothing watching the rate would ever have found it. The
sibling of "a melee build that stands still and reports zero damage", through
the economy rather than the grid, and the reason `SimResult` reports actions and
attack resolutions per round: a starved policy should be visible as a count and
not only as a disappointing average.
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

## APL derivation — the policy is a result, not an input

The section above describes how a policy is *expressed*. This one describes
where it comes from, and it is the more important half.

**The problem with an authored APL.** A hand-written `apl:` block makes every
number the sim reports a measurement of *build plus author*. `compare A B` then
answers "which build did I write a better APL for?", which is the one question a
simulator exists to stop people asking. There is no prior knowledge of correct
5e play at this granularity — nobody knows the HP threshold below which Reckless
Attack stops paying, and the honest answer is that it depends on the build, the
AC, the party and the encounter count in ways no author can hold in their head.

So the quantity that is actually a property of a build is not `E[objective |
apl]` but

```
value(build) = max over policies of  E[objective | build, policy, scenario]
```

and the argmax is unknown. **The policy is therefore derived by search, and the
derivation is part of the product rather than a spreadsheet exercise done once
by hand.** A hand-written APL keeps exactly one job: it is the **baseline** the
derived policy is scored against.

This is the reason for every statistical commitment the rest of this document
makes. Determinism was justified above by reproducibility and readable
regression diffs; its larger payoff is here, because it is what makes common
random numbers exact and therefore what makes the search affordable at all.

### What it changes architecturally: almost nothing

> Amended by § The scorer below, which adds two files and a third seam. The
> claim that *derivation* touches no engine file survives; the claim that the
> whole feature does not, does not.

The APL is already a **Strategy** — a pluggable policy object the engine knows
nothing about. Derivation is a *generator of Strategy objects* that uses the
simulator as a noisy oracle. It lives in `search/`, sits above `sim/` and
`api.py`, imports `apl/`, and **changes no engine file, no hook, and no
feature**. That property is the whole reason this is affordable to add, and it
is worth defending: anything in `search/` that wants an engine change is a
design error in `search/`, not a missing hook.

It costs three seams, and all must be cut in W2 — before a single build YAML
exists in git — because retrofitting them afterwards is a migration across every
file in `builds/`:

1. **`Apl` is constructible programmatically and serialisable back to YAML**,
   round-tripping exactly. The search builds millions of policies; not one of
   them may go through a text file.
2. **The API accepts an APL override and returns per-seed results.** `run_sim`
   and `run_day` take an explicit seed list and return the objective *vector*,
   not only its mean. Aggregates are useless to a paired comparison, and a
   paired comparison is the entire variance-reduction strategy below.
3. **`best_scored` is reserved in the action vocabulary.** The scorer below
   enters the APL as one action id, and that vocabulary is closed — reserving
   the id costs a line in W2 and is a schema migration across every build file
   afterwards, exactly as the three clauses were.

### The scorer: one global value model under the per-build search

The search above starts every build from nothing. That is wasteful and it is also
wrong about where the knowledge lives: *most* of what makes a turn good is not
build-specific. "Hit the thing you are most likely to hit, with the option that
deals the most damage, unless you are spending something scarce to do it" is true
for every class at every level, and re-searching it once per build spends the
budget proving the obvious.

So the policy is two layers:

1. **A scorer** — a global value model that scores every offered action at every
   decision point, with **coefficients fitted once across a whole corpus of
   builds** and reused by all of them.
2. **The per-build overlay** — the searched APL from the previous sections,
   which now carries only this build's *departures* from generic good play.

The layering is what makes the per-build search cheap and its output readable.
A derived APL stops being a policy and becomes a diff.

**The obvious objection first.** A fixed scoring formula with fitted coefficients
is a hardcoded functional form, which is exactly what the previous section said
not to do. That trade is deliberate and it is made falsifiable rather than
argued: the scorer competes for its place in the ordered list like any other
candidate entry (below), so if the model is wrong for a build the search routes
around it and the ablation table reports the scorer carrying no marginal value.
**A scorer that cannot be shown to beat its own absence is cut.** That is an
acceptance criterion of `w8h`, not a matter of taste.

#### The score function

Every offered action is scored in one currency — **points of expected damage** —
so that damage, prevented damage and spent resources are directly comparable:

```
score(a, T) =   w[a]     · Ê_dmg(a, T)                       # damage now
              + w_off[a] · ΔÊ_ours(a, T)                      # our output, later
              + w_def[a] · ΔÊ_theirs(a, T) · w_ally            # their output, later
              + w_ally   · Ê_ally_hp(a, T)                    # damage prevented now
              − Σ_r  c[r](day) · spend_r/pool_r               # what it costs
              + b[a]                                          # the residual, only that
```

Both `ΔÊ` terms carry the horizon and the landing probability **inside** their
per-target sums, because an area effect rolls a separate save against a separate
creature with its own remaining life; `p_land` is `1` for anything not gated on a
save, so only a debuff pays it. Both are signed so that positive is good — see
§ Buffs and debuffs, where getting that backwards is the one sign error the fit
cannot rescue.

Four families of fitted coefficients, and nothing else:

- **`w[a]`, a slope per action id**, multiplying the engine's expected-damage
  estimate. For a plain attack it is pinned (below); elsewhere it absorbs the
  residual the estimator cannot see — rider damage, follow-on attacks, the
  tempo the action costs.
- **`w_off[a]` and `w_def[a]`, two slopes on *rates*** rather than on damage,
  multiplying the change an action makes to expected damage per round over the
  rounds that change survives. Rage, Bless, Hunter's Mark, Faerie Fire,
  Frightened, Slow and Reckless Attack all deal zero damage on the turn you spend
  them and are among the strongest actions in the game; these terms are the whole
  reason they can be scored at all rather than left to a constant. The split is
  **offensive versus defensive — what changes, not whom it is cast on** — so a
  stun scores in both with no special case. Both are computed counterfactually,
  not declared as numbers, and `p_land` is the save-gating a debuff has and a
  buff does not. See § Buffs and debuffs below.
- **`b[a]`, an intercept per action id**, for whatever remains after all three.
  With the two rate terms present this is a genuine residual rather than the
  entire value of every non-damaging action, which is a large improvement in what
  the model can be trusted with — but it stays, because a closed perturbation
  vocabulary will never cover every feature, and positioning control is the
  standing example of one it does not.
- **`w_ally` and `c[r]`, the two exchange rates.** `w_ally` is what one point of
  ally HP is worth in enemy damage; `c[r]` is the scarcity price of one unit of
  resource `r`, modulated by how much of the day remains. These two numbers are
  not bookkeeping — **they are the attrition question in scalar form**, and
  discovering them is arguably the most interesting single output the project
  can produce. One fitted `w_ally` drives healing, Dodge, Uncanny Dodge, Shield
  and friendly-fire avoidance simultaneously, which is a very large compression
  of the policy space for one parameter.

**The gauge — two coefficients are pinned, not fitted.** A ranking is invariant
under any positive affine transform of the scores, so the model is
unidentifiable until two degrees of freedom are removed. Both choices are
meaningful rather than arbitrary:

- **`w[attack] = 1`, `b[attack] = 0`** fixes the scale. Every other coefficient
  is then denominated in *plain attacks*, which is the unit a reader already
  has intuition for: "Reckless Attack is worth 1.4 attacks against AC 17" is a
  sentence; "Reckless Attack scores 8.3" is not.
- **`score(null) = 0`** fixes the offset, where `null` is the explicit
  end-your-turn option. This one earns its place twice over: it makes every
  score a value *relative to doing nothing*, and it is what lets the scorer
  decide to **hold a resource** rather than spend it. A model in which "do
  nothing" is not on the ballot cannot answer the attrition question, because
  conserving is half of it.

#### The estimator is computed, never fitted

`Ê_dmg` is the part the user's question is really about — *number of targets,
and hit chance from our attack modifiers and our save DC* — and it is
**closed-form engine code, not a learned number and not per-feature**:

- **Attack rolls:** `Σ_t [ p_hit(t)·(dice_mean + flat) + p_crit·dice_mean ]`,
  with `p_hit` from the real attack bonus against that target's AC and
  advantage folded in as `1 − (1−p)²` (disadvantage `p²`), since advantage is
  already a collection of sources rather than a boolean.
- **Saving throws:** `Σ_t [ p_fail(t)·full + (1−p_fail(t))·half ]`, with
  `p_fail` from this creature's spell save DC against that target's save bonus.
- **Defences applied in the resolver's order**, so a Fireball against fire-immune
  enemies scores zero rather than full.

It lives in one file, `engine/estimate.py`, for the same reason the damage
resolution order does — and it is bound to the resolver by an invariant that is
the linchpin of this whole scheme:

> **The estimator and the resolver share the crit rule, the advantage rule and
> the defence order by construction, and a property test asserts that
> `Ê_dmg` matches the empirical mean of 10⁵ resolutions of the same action to
> within Monte Carlo error.**

If those two ever disagree about whether `flat` doubles on a crit, the scorer is
systematically wrong at every decision point and no amount of fitting repairs it
— the fit would simply absorb the bug into `w[a]` and hide it. This test is the
one that keeps the model honest, and it is cheap.

**One stated modelling assumption:** the estimator reads the target's true AC,
save bonuses, resistances and immunities. That is perfect information about
defences, which a real party does not have. Modelling belief and scouting is a
subsystem, not a parameter, and it is out of v1 — but it is written down here
because it is the kind of assumption that is invisible in results and
embarrassing when discovered later. It inflates AoE and damage-type selection
specifically.

#### Buffs and debuffs: one counterfactual, split by what changes

A buff's value is the damage it adds to future rounds; a debuff's is the damage
it adds *or removes* from them. Both are evaluated the same way — **perturb the
estimator and take the difference** — and the useful axis between them is not who
they target:

```
ΔÊ_ours(a, T)   = Σ_a∈allies  p_land(a)·H_a·[ Ê_dpr(a | perturbed) − Ê_dpr(a | stands) ]
ΔÊ_theirs(a, T) = Σ_e∈enemies p_land(e)·H_e·[ Ê_dpr(e | stands) − Ê_dpr(e | perturbed) ]
```

**`p_land` and the horizon live inside the sum, per target.** An area control
spell rolls a separate save for every creature it catches, so a single scalar
`p_land` multiplied over the whole term would price Hypnotic Pattern against a
goblin and an archmage as though they were the same creature. The horizon is
per-target for the same reason — one enemy may be about to die while another has
full hit points. Factoring either out of the sum is only correct for a
single-target effect, which is the case that makes the error easy to miss.

**Note the enemy term subtracts the other way round.** Both are written so that
*positive is good* — `ΔÊ_ours` is damage per round gained, `ΔÊ_theirs` is damage
per round denied — because a single sign convention across the two terms is what
lets one fitted exchange rate convert between them. Written the naive way, with
both differences taken in the same direction, every debuff would score negative
and the scorer would refuse to cast any of them; that is the sort of bug a fit
cannot rescue you from, because the coefficient would simply go negative and the
model would still be wrong about everything else it touches.

**The axis is what changes, not whom it is cast on.** Bless raises our output;
Faerie Fire raises our output too, by lowering an enemy's effective defence;
Frightened lowers theirs; Shield of Faith lowers theirs as well, by raising our
AC. A "buff versus debuff" split would cut across all four and need special cases
for each. The offensive/defensive split does not, and it pays for itself the
first time it meets a **stun**, which is both at once — the enemy loses its turn
*and* becomes far easier to hit — and therefore simply scores in both terms with
no special case anywhere.

`+2` to hit is `p_hit(atk + 2, AC)` instead of `p_hit(atk, AC)`. Advantage is
`1 − (1−p)²`. Lowering an enemy's AC is the same call from the other side.
Removing the enemy's action zeroes its whole contribution to `Ê_dpr(e)`. In every
case it is the same closed form run twice and subtracted — no dispatch, no
dry-run context, no hook change.

Three properties fall out of the counterfactual form, and they are the reason to
prefer it over a declared scalar:

- **Re-applying an active effect scores zero, structurally.** Perturbing a state
  that already carries it produces no difference. Nobody writes
  `if: "not self.raging"` or `if: "not target.frightened"` — the arithmetic
  refuses on its own.
- **Non-stacking is respected for free.** A second source of advantage against an
  already-advantaged target changes `p_hit` by nothing, so the second Faerie
  Fire scores ~0 without anyone encoding the rule that advantage does not stack.
  That is one of the classic policy errors, gone by construction.
- **It scales with who is actually there.** Bless on three allies sums three
  perturbations. Faerie Fire on one enemy sums over **every ally who will attack
  that enemy**, so it is worth roughly four times as much to a party of four as
  to a lone character — which no per-action constant can express.

Note what the second summation requires: **the estimator must run over enemies as
well as allies.** `Ê_dpr(e)` is the enemy's own expected output, computed from
its own attack bonuses against our ACs. This is free in the architecture — the
target dummy implements the full `Creature` interface and a monster is not
special-cased anywhere — but it is a real requirement on `engine/estimate.py`
that the offensive-only version would not have had, and it roughly doubles
estimator work on a turn where control is offered.

#### What debuffs need that buffs do not

Here the symmetry stops, and treating a debuff as "a buff with the sign flipped"
would be wrong in three ways that all matter:

**1. It has to land.** A buff on an ally always applies; a debuff is usually
gated on a saving throw, so its whole value is multiplied by
`p_land = p_fail_save(target)` — computed from this creature's spell save DC
against that target's save bonus, which is the same arithmetic `Ê_dmg` already
does for save-for-half damage. This is what makes Hold Person excellent against a
low-WIS brute and a wasted slot against a high-WIS caster, and the scorer gets
the distinction for free rather than needing a condition per spell.

> **Legendary resistance sets `p_land` to zero while any remain**, because the
> target will simply succeed. The value of *burning* one falls to the intercept,
> which is a known approximation rather than a modelled effect — worth stating
> because a scorer that valued legendary resistances correctly would be making a
> multi-turn plan, and § The known weakness says it cannot.

**2. Its duration is stochastic, not fixed.** "Save at the end of each of its
turns" is not a duration, it is a geometric process, and the expected number of
rounds it actually holds for is

```
E[rounds] = Σ_{k≥1} p_fail^k  =  p_fail / (1 − p_fail)
```

capped by the stated duration and by the horizon. A buff's `duration_remaining`
is read off the effect; a save-ends debuff's must be computed. The difference is
large and it is not a correction factor: against a target failing half the time
the effect is worth one round, not the ten the spell's duration claims.

**3. Its horizon is the target's, not the encounter's.** Debuffing an enemy about
to die is waste, so the horizon for a single-target debuff is bounded by how long
that enemy has left:

```
H_target = min( H_encounter,  hp_target / Ê_dpr_directed_at_it )
```

This is the per-target refinement of the encounter-wide horizon below, and it is
what stops the scorer from casting Hold Person on the enemy sitting at 3 hit
points — the other half of the same instinct that stops it buffing on the
killing round.

**4. Some of them are destroyed by your own follow-up.** Hypnotic Pattern is the
canonical case: *"The spell ends for an affected creature if it takes any
damage."* A scorer that only ever asked "what does this action change?" would
cast it on a cluster for a large `ΔÊ_theirs`, and then on the next turn happily
drop a Fireball on the same cluster — scoring the damage, ignoring the control it
just destroyed, and teaching the fit that Hypnotic Pattern is worthless. The
failure would be the policy's, and the coefficient would take the blame.

The fix is not lookahead. **Breaking an effect is itself a perturbation, so it
belongs in the breaking action's own counterfactual.** Attacking a creature whose
control `ends_on_damage` restores that creature's output, so `ΔÊ_theirs` for the
*attack* is negative by exactly the control it destroys, and the attack's score
drops by that much. The scorer then targets elsewhere because the arithmetic says
to — one step, no plan, no condition written by anyone. It flows into AoE
placement for free, since placement scores candidate origins with the same
estimator: an origin covering hypnotised enemies pays for each one it wakes, and
the coverage-maximising placement stops being the highest-scoring one.

This is why `ends_on_damage` is in the perturbation vocabulary rather than left
to the intercept, and it carries one rule with it: **an effect that ends on
damage does not take the per-target hit-point horizon.** That bound exists
because a dying enemy stops being worth debuffing, but it presumes the party
keeps attacking the target — and for this class of effect the correct policy
stops. Applying both would double-count the same consideration, once as a
shortened horizon and once as a penalty on the breaking action, and the penalty
is the one that is actually true.

Control that works through **positioning** — Push, Thorn Whip, Repelling Blast —
is deliberately outside this. Its value depends on where everyone ends up and on
the route back, which is a pathing question the design does not answer even for
the policy's own movement. Those features declare nothing and fall back to the
intercept, which is the honest outcome rather than a number invented to look
complete.

#### The perturbation vocabulary is closed and CI-guarded

A buff or debuff feature declares what it does to the attack math as data,
exactly like the hook registry and for the same reason:

```python
BUFF_PROFILE = Perturbation(
    to_hit=+Dice("1d4"),               # Bless
    targets="allies.within(30)",
    max_targets=3,
    duration=Duration.rounds(10),
    concentration=True,
)

DEBUFF_PROFILE = Perturbation(
    action_denied=True, speed=0,        # Hypnotic Pattern
    targets="enemies.in(template)",     # a 30-foot cube; one save each
    save=Save(Ability.WIS),             # no repeat save — it just holds
    ends_on_damage=True,                # so nobody in the party may hit them
    duration=Duration.minutes(1),
    concentration=True,
)
```

Offensive keys (`to_hit`, `advantage`, `damage_per_hit`, `extra_attacks`,
`crit_range`) and defensive keys (`target_ac`, `target_save`,
`advantage_against`, `their_disadvantage`, `damage_taken`, `action_denied`,
`speed`), plus the two that say how an effect *ends* — `save=` and
`ends_on_damage` — a closed list, appended to deliberately, never improvised. **An effect
that does not fit declares nothing and falls back to `b[a]`**, which is the
`NEEDS_HOOK` escape valve applied to the scorer: blocked and visible beats
approximated and wrong.

Only buff, debuff and control features need this — on the order of 30–50
implementations across the whole project, not 630. But **it is authoring surface,
so it is a W3 decision, not a W8 one.** The declaration must be in the authoring
skill and the linter before the grind writes 400 features, or it is a retrofit
across every one of them. That is the one place the seam rule lands outside W2.

**It can disagree with the implementation, so it is checked against it.** A
declared `+2` where the code grants `+1d4`, or a declared `save=WIS` where the
spell calls for CON, is a silent systematic scoring error of exactly the kind the
estimator/resolver property test exists to prevent — so the same statistical
harness that already validates a feature's `expectation` validates its profile,
by measuring the real DPR difference on both sides with and without the feature
attached and comparing it to the perturbation's prediction. A profile more than
10% off its own implementation fails the batch, on the same terms and in the same
run as everything else `w3l` checks.

#### The horizon is computed too, and it is what fixes myopia

`ΔÊ_ours` and `ΔÊ_theirs` are rates; the score needs quantities. The multiplier
is how many rounds the effect will actually be worth something for:

```
H = clamp( min( duration_or_E[rounds],  Σ_enemies hp_remaining / Ê_party_dpr ),  0,  h_max )
```

— computed per target, and narrowed to `H_target` above for an effect that
persists through damage. The second term is the estimated rounds left in the
fight, and **the scorer can compute it**: it knows the enemies' remaining HP, and
`Ê_dmg` over the party is precisely an estimate of how fast that HP disappears. A
fitted correction absorbs the rest.

Its weakest moment is the one Hypnotic Pattern creates. Neutralised enemies are
not being killed, so counting their hit points in the numerator says the fight
has longer to run than it does for the enemies that still matter — which
*under*-values the control that neutralised them, the wrong direction for a
heuristic to fail in. The numerator therefore runs over enemies the party is
actually still fighting. That is a refinement, not a repair: `H_encounter` stays
a heuristic with a fitted correction, and control-heavy states remain where it is
least trustworthy.

This is the part worth dwelling on, because it repairs the model's worst failure.
A myopic scorer buffs in the last round of a fight and stuns the enemy on 3 hit
points — the two most recognisable marks of a bad policy — and a fitted
per-action constant cannot avoid either, because the constant is the same on
round one and round six. With a computed horizon, `H → 0` as the last enemy
drops and every buff, debuff and control effect collapses to its cost,
automatically and in every build. **Rage is worth ten rounds of extra damage at
the start of a fight and nothing at the end of one, and the scorer now says so
without being told.**

The same `H` multiplies the defensive term, so Dodge and Shield of Faith get the
horizon treatment on exactly the same footing as Frightened and Slow.

**These coefficients are fitted with more freedom than anything else, and pay for
it with more scrutiny.** This is where the model's structural form is weakest —
the horizon is an estimate of an estimate, `p_land` compounds it, and the
perturbation vocabulary approximates a rules text — so `w_off[a]` and `w_def[a]`
are given **wider priors than `w[a]` in the hierarchical fit**, shrunk toward the
global mean far less aggressively. That is the principled version of "learn these
harder": not a separate procedure, just less pooling where the prior deserves
less trust. The cost is more effective parameters on the smallest subset of
actions, which is the classic way to overfit — so these coefficients carry their
own ablation rows, and the held-out *builds* check of § Fitting the priors is
what stops the extra freedom from being spent on noise.

One trap the exchange rates already handle: a concentration effect blocks every
other concentration option, and its value must be net of the best one it
displaces. No new machinery is needed — **concentration is modelled as a resource
with a pool of one**, so `c[concentration]` prices it through the term that is
already there, and the scorer declines Hunter's Mark when Faerie Fire wants the
slot more.

#### `best_scored` — how the two layers compose

The scorer does **not** replace the ordered list. It enters it as one new action
id in the closed vocabulary:

```yaml
apl:
  - { action: rage, if: "not self.raging and enemies.within(30) >= 1" }
  - { action: best_scored }
```

`best_scored` means *take the highest-scoring offered action*. The consequences
are worth being explicit about, because they are most of the value of this
design:

- **The default policy for any build is a single entry**, `[{action:
  best_scored}]` — a strong default rather than the Null Object of § APL. A
  build with no APL at all now plays competently instead of falling through.
- **A derived APL is a sparse diff.** The entries the search places *above*
  `best_scored` are precisely this build's departures from generic play, and
  "how many lines does this build need beyond the scorer?" becomes a reportable
  per-build number. This is the parsimony rule of the previous section getting a
  much better-conditioned problem to work on.
- **The search gets a warm start** instead of a cold one, which is the largest
  single cut to the derivation budget in this document.
- **`APL_FALLTHROUGH` sharpens.** With `best_scored` present, falling through is
  nearly impossible; a build that still does is one where the *search chose to
  exclude the scorer*, which is a far more informative signal than the old
  counter.
- **The grammar does not change.** `best_scored` is an action id, so the AST
  whitelist, the three clauses and the loader are all untouched — but the action
  vocabulary is closed, which is why `w2e` must reserve the id now. Adding it
  after builds exist in git is the same migration the three clauses were.

#### Fitting the priors: a declared corpus, and held-out *builds*

The coefficients are fitted on **`builds/corpus.yaml`** — a declared, versioned
list of builds spanning class family × level (1 / 5 / 11 at minimum) × role,
hashed into the derivation provenance and **refitted deliberately once per
content wave**, never continuously. Corpus membership is a review decision. The
alternative — fitting on whatever happens to be in `builds/` — makes the priors
drift silently and makes two derivations months apart incomparable, which is the
same failure the content-version pin exists to prevent.

**The fit is validated on builds that are not in the corpus.** This is the
build-level analogue of the holdout seed set, and without it "global priors help"
is unfalsifiable: coefficients fitted and evaluated on the same builds will
always look good. Held-out builds are also the only way to detect the risk that
one `w[attack]` shared between a Wizard and a Barbarian is a number that fits
neither, and the fit therefore reports **per-build residuals** so that failure is
visible as a row rather than as a slightly worse average. Splitting the priors by
class family is the remedy if the residuals demand it — but it is a response to a
measurement, not a precaution.

**Two tiers, gated by measurement** — the same shape as `w0i` and `w8g`:

1. **Pooled black-box fit.** Treat the coefficient vector as one policy and
   search it against the corpus's pooled holdout objective, restricted to the
   ~15 most common action ids. ~30 parameters, the existing machinery, no new
   concepts. Try this first.
2. **Decision-level regression**, only if tier 1 stalls on parameter count.
   Roll the corpus out under the current coefficients with ε-greedy exploration,
   log every decision point (the offered set, each option's feature vector, the
   choice, and the realised return-to-go), and regress return on features. **A
   day-run yields one number to a black-box search and ~200 labelled decisions
   to a regression**, which is the ~10²× sample-efficiency difference that makes
   a hundred shared coefficients affordable when twenty per-build ones were
   already the expensive part.

Tier 2 is approximate policy iteration and inherits its known failure: the
regression is fitted on the state distribution the *current* coefficients visit,
and the improved ones visit a different one. The mitigation is not clever, it is
mandatory — **every iterate is validated on the held-out builds and seeds before
it is accepted, and a step that does not improve is rejected rather than
compounded.** An unguarded policy-iteration loop that diverges looks exactly like
one that is still converging.

#### AoE: placement is searched, and it is part of the estimator

For an area spell, "number of targets" is not given — it is chosen, and choosing
it well is most of what the spell is worth. Placement is therefore **searched at
the decision point**, and the search is bounded by geometry rather than by the
grid: for a fixed-radius template the coverage-maximising origins are generated
by the enemy positions and the pairwise intersections of radius-circles around
them, so the candidate set is **O(n²) in enemies (≤ ~64 in practice), not O(grid
cells)**. Each candidate is scored by the same `Ê_dmg` summed over what it
covers.

Two points keep this consistent with everything above:

- **Placement is an estimator, not a policy.** It answers "what is the best this
  spell can do from here"; the *decision* — whether that best is worth the slot —
  stays with the scorer comparing that number against the alternatives. So a
  hand-written max-coverage enumeration is not a hardcoded tactic, any more than
  `p_hit` is.
- **The friendly-fire coefficient is fitted, not assumed.** Allies inside the
  template subtract `w_ally · Ê_dmg` — the same exchange rate as everywhere else,
  which is exactly the reuse that justified having one. Whether a point of ally
  HP is worth more or less than a point of enemy damage is a result, not an
  assumption, and asserting `1.0` would be the hardcoded heuristic this section
  exists to avoid.

The code lands with the spell engine (`w5a`), because templates do not exist
before it; the *decision* is settled here. M5 is reachable on martials, so this
sits off the critical path to it.

#### What it costs, and the correction it forces

The scorer runs per decision point over every offered action × its candidate
targets — on the order of 30 estimator calls per decision, against ~2 attack
resolutions per turn at ~32 µs each. Naively that is a **3–10× regression on the
turn**, which would be fatal. It is brought back by the fact that almost
everything in `Ê_dmg` is static: dice means are memoised per weapon and spell,
`p_hit` is a lookup on `(attack_bonus − AC, advantage_state)` over a small
integer table, and the score vector is cached within a turn until something it
reads changes. The target is **under 30% turn overhead**, and it is a measured
number, not an argued one — `w8g` measures it, and if it misses, the scorer is
revised there rather than after the grind.

This also forces a correction to the claim two sections above. **§ What it
changes architecturally is no longer strictly true:** derivation still changes no
engine file, but *the scorer does*. It adds `engine/estimate.py` and
`apl/scorer.py`, and it puts a third item on W2's seam list — the reserved
`best_scored` action id. The layering rule survives intact and is what matters:
`engine/estimate.py` is engine code, `apl/scorer.py` is policy code that runs in
the hot path, the fitted coefficients are **data** that `apl/` loads, and
`search/` only *produces* those coefficients. Nothing in `search/` imports
`engine/`.

#### The known weakness: one round of lookahead, and no more

The buff term buys exactly one step of foresight — *this action changes the rate
at which damage happens, for this many rounds* — and that covers the large,
recognisable failures. What it does not buy is planning.

The scorer still cannot represent a **sequence** whose value is not the sum of
its parts: holding Action Surge for the round the enemy clusters, opening with
the control spell so the damage spell lands better next turn, or declining a kill
now to set up two next round. Each step is scored against the state in front of
it, and a plan that is worth less at step one than its alternative is never
reached, however good step two would have been. Second-order interactions between
two buffs are the same gap seen from another angle: perturbations are evaluated
one at a time against the current state, so Bless plus Faerie Fire is scored as
the sum of two independent deltas when the truth is superadditive.

Three things contain what remains, in order of how much they are relied on. The
**per-build overlay** exists precisely to carry the entries the scorer gets
wrong, and a combination that needs an explicit opener gets an explicit entry —
this is the case the two-layer design was built for, and a build whose overlay is
mostly buff-sequencing entries is telling you something real about that build.
`rounds_elapsed` and `encounters_remaining` are available to the intercept and the
resource price, which recovers the first-order timing effect the horizon does not.
And the **ablation table reports the scorer's marginal value per build**, so a
class the model serves badly surfaces as a row rather than as a quietly mediocre
number.

What is explicitly *not* proposed is a learned state-value function or a search
over action sequences. Both are a different project; the overlay is the cheaper
answer to the same problem, and it is the answer that stays readable.

### The candidate space is generated, never enumerated by hand

This is what "derived, not hardcoded" has to mean in practice. If the search
space were a hand-written list of plausible entries, the search would be an
elaborate way to launder the author's priors — the exact failure it exists to
avoid. So all three clause types are generated mechanically from artefacts that
already exist:

- **Actions** come from the *resolved build*: every action id its attached
  features offer, plus every rider and every reaction. `dndsim inspect` already
  computes this list; the generator reads it.
- **Conditions** come from the `NamespaceSchema` — the same declared schema the
  APL compiler resolves paths against. A boolean path `p` yields `p` and
  `not p`; an enum path yields `p == v` for each declared `v`; a numeric path
  yields `p <cmp> θ`, where θ is a searched parameter over the grid the schema
  declares for that path (`hp_pct` steps of 0.1, counts 0–4, AC 12–20). The
  schema gains one field — a candidate grid per numeric path — and nothing else.
- **Targets and movement** come from the closed selector grammar's own cross
  product: `{lowest_hp, highest_hp, nearest, farthest} of enemies.{in_reach, all}`
  and the three `move:` forms.
- **`best_scored`** is always in the pool, and its *priority* is searched like
  any other entry's. Where it lands is the result that matters: entries the
  search places above it are this build's departures from generic play, and a
  build whose derived APL is `best_scored` alone is a build with nothing special
  to say — which is a finding, not a failure.

Nothing in `search/` knows what a Barbarian is. **The acceptance test for this
package is that adding a class, a subclass or a spell requires zero lines of
change in it** — a feature authored in W5 is searched the day it lands, because
it arrived in the resolved build and its namespace paths arrived in the schema.
A generator that needed a per-class table would reintroduce exactly the prior
knowledge the search is meant to replace.

The cross product is large, and the answer to that is staging (below), not
pruning it by taste.

### Encoding: random keys, so every point in the space is legal

A policy is a *subset* of the candidate pool, in an *order*, with *thresholds* —
a mixed combinatorial and numeric object that most samplers handle badly.
Searching permutations directly means every mutation and every crossover needs a
repair step, and the neighbourhood structure is poor.

Instead each candidate entry `i` is encoded as `(enabled_i: bool, priority_i:
float ∈ [0,1), θ_i...)`, and decoding is: keep the enabled entries, sort by
priority, emit. This is the **random-key** encoding (Bean, 1994). Every vector in
the box decodes to a legal APL, no repair operator exists, the dimension is
fixed, and any off-the-shelf sampler works on it unmodified. The decoder is
canonical, so two different vectors decoding to the same policy hash to the same
string — which is what makes the evaluation cache below actually hit.

### The oracle is noisy, so the statistics are the hard part

The search is the easy half. Every evaluation is a Monte Carlo estimate, the
differences being chased are a few percent, and a search that treats a lucky
sample as a discovery will confidently report a policy that is not better. Four
commitments, in descending order of how much they matter:

**1. Common random numbers, and paired comparisons.** Every candidate is
evaluated on the *same* seed set, and candidates are compared on the vector of
per-seed differences, never on two independent means. Two APLs differing in one
entry produce nearly identical runs on the same seed, so the difference has a far
smaller variance than either mean — the paired test resolves a gap that an
unpaired one would need roughly an order of magnitude more seeds to see. This is
free, and it is free *because* seed + build + engine + content already reproduces
a run byte for byte. Nothing else in the design pays off this hard here.

**2. Racing, not fixed budgets.** Successive halving: evaluate every candidate on
a small seed count, keep the top fraction, multiply the seed count, repeat. Most
candidates are obviously bad and should die at 32 seeds rather than consume 512.
This is the single largest cost lever, worth roughly 10× (see below).

**3. Held-out seeds, and a reported optimism gap.** The search runs on a
*derivation* seed set and the answer is reported on a **disjoint evaluation seed
set**. With thousands of candidates scored against one seed set, the winner is
partly noise-selected; the search-set number is therefore biased upward and must
never be the headline. Every derivation reports both, and their difference as
`optimism`. **A large optimism gap means the fix is more seeds, not more
candidates** — which is the sort of thing that is obvious in a report and
invisible in a single number.

**4. Parsimony — the one-standard-error rule.** Among policies within one
standard error of the best on the holdout, return the *simplest*: fewest entries,
then fewest conditions. Then ablate: drop each surviving entry, re-evaluate on
the holdout, and report that entry's marginal value with a confidence interval.
Entries whose ablation is inside noise are dropped from the emitted APL. The
output is a short readable policy in which every line has a measured reason to
exist, rather than a thirty-entry thicket nobody can check — and the ablation
table is the derivation's most useful artefact for a human, because it is the
part that says *why*.

### The party is a joint problem, and coordinate ascent is a compromise

Four PCs means the policy space is a product of four spaces, and a joint search
over it is hopeless. The derivation therefore does cyclic best response: fix
three members, derive the fourth, rotate, and repeat until a full sweep produces
no holdout improvement beyond noise.

This converges to a **mutual best response, not a global optimum**, and it can
cycle. Both facts are reported rather than hidden: the sweep count is capped, the
per-sweep trajectory is in the result, and the result names which member's policy
changed last. A coordinate-ascent answer presented as "optimal" is a false claim,
and the difference matters most exactly where party composition is interesting —
a healer's policy and a tank's policy are each other's inputs.

### The objective is a knob, and a scalar objective hides variance

For v1 the objective is the attrition metric: encounters cleared before the stop
condition. But `objective:` is declared and pluggable (`attrition`, `dpr`,
`survival`, `time_to_kill`), because a policy derived to maximise DPR and one
derived to maximise attrition are *different policies*, and the size of that
difference is itself a result worth reporting.

Any scalar objective can be raised by trading away something it does not measure.
So a derivation reports the objective's whole holdout distribution, not its mean
— a policy that gains 4% expected encounters while doubling the TPK rate is then
visible rather than silently preferred. Constrained objectives ("maximise
encounters subject to TPK rate ≤ 5%") are expressible for the same reason.

### Scenario dependence — the trap that invalidates the answer

**There is no "best Champion APL".** There is a best Champion APL against AC 16,
two enemies that close to melee, six encounters and two short rests. A derivation
therefore names its scenario set, the result is labelled with it, and comparing
two policies derived against different scenarios is a category error the tooling
refuses rather than performs.

Because deriving against one scenario overfits to it as surely as against one
seed set, the default derives against a *distribution* of scenarios — the same
sweep axes `compare --sweep` already uses — with the objective averaged over
them, and reports the per-scenario breakdown. A policy that is excellent at AC 12
and terrible at AC 20 is a finding, not a defect to average away.

### Cost, honestly

An adventuring-day run is on the order of 300 attack resolutions, so at the M0
budget of 30k/sec/core one core does roughly 100 day-runs per second. A staged
search of ~2,000 candidates with racing (most dying at 32 seeds, finalists at
512) is on the order of 10^5 day-runs — **about 15 core-minutes per character**,
and about 3 core-hours for a four-member party over three coordinate-ascent
sweeps. Without racing the same search is ~10^6 runs and roughly 34 core-hours;
a five-scenario distribution multiplies both by five.

Those numbers are an order-of-magnitude estimate to be replaced by a measurement
(`w8d`), and they carry one conclusion that must be stated plainly so it is not
re-litigated: **the answer to derivation cost is not a faster engine.** Derivation
multiplies sample count by two to three orders of magnitude, and no plausible
CPython speedup covers that. `w0i`'s budget is not reopened. The levers are all
on the sampling side:

- **the scorer's warm start** — searching departures from a fitted global policy
  rather than a policy from scratch, which is the largest single cut and the main
  reason § The scorer earns its complexity;
- common random numbers and racing, as above — together worth ~10×;
- **staged search**: first search structure (which entries, in what order) with
  default conditions, then search thresholds only on the entries that survived;
- **a cheap proxy objective in the early rungs** — a single encounter against a
  dummy, ~50× cheaper per sample than a full day — promoted to the true objective
  only for finalists, with the promotion validated by checking that proxy rank
  order correlates with true rank order on the finalists;
- **memoisation on the canonical policy hash**, since under CRN an identical
  decoded policy on an identical seed set is an identical result, and a search
  revisits policies constantly.

### A derived APL is an artefact with provenance

Derivation is reproducible or it is worthless: **seed + build hash + engine
version + content version + derivation-config hash + scenario-set hash** produces
the same APL byte for byte. All six go in the result, exactly as the four already
do for a simulation.

The output is written as **ordinary build YAML** — a normal `apl:` block under a
provenance comment header — so it can be pinned into the build file, reviewed in
a diff, and hand-edited. There is no second policy format and no binary policy
blob: a derived APL is indistinguishable from an authored one to the loader,
which is the Single Source of Truth rule applied to policy. Re-deriving a pinned
policy and diffing it is then an ordinary regression check, and one that catches
a content change that quietly invalidates a line of play.

### The optimizer is an adversary, and that is the point

A search that runs 10^5 evaluations against the engine will find every place the
rules are implemented too generously. This is not a hazard to be defended
against so much as the most valuable secondary output in the project: it is to
the content grind what the canary batch is to the API, an outsider that cannot
be argued with.

It does need a guard, because an exploit reported as a strategy is worse than no
report. Two are cheap:

- **Plausibility band.** A derived policy whose holdout objective exceeds a
  declared band — or that rests on an entry whose measured marginal value is
  implausible — is triaged as a suspected engine or feature bug *before* it is
  reported as a finding, and `dndsim derive` exits non-zero with the offending
  entries listed.
- **The baseline comparison.** Every build keeps its hand-written APL as
  `baseline_apl`, and the derivation reports Δ against it on the holdout. Two
  signals fall out for free: derived ≈ baseline across many builds means the
  search is not searching, and derived ≫ baseline by an implausible margin means
  either an exploit or a genuinely non-obvious line — which is precisely the
  output this project exists to produce. There will never be a ground truth to
  check the derivation against; this is the substitute, and it is self-checking
  in both directions.

### Libraries

`optuna` for the search itself — TPE over the define-by-run mixed space, its
successive-halving and Hyperband pruners for racing, seeded samplers, and
resumable study storage, none of which is worth hand-rolling. `scipy.stats` for
the paired tests and intervals. Hand-written: the candidate generator and the
decoder, because both are specific to this project's schema and neither is a
solved problem in a library.

One ladder rung is worth naming, because it is the common case and the fancy
answer is the wrong one: a level 1–3 martial often generates six or fewer
candidate entries, and there **exhaustive enumeration with racing dominates any
sampler**. `optuna.samplers.BruteForceSampler` covers it, so the ladder — brute
force when the decoded space is small, TPE when it is not — is one configuration
switch rather than a second implementation.

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
  benchmark that only times the inner loop. **Measured at M0: 0.167 s per
  worker**, so that sweep spends two seconds on startup before a die is rolled —
  which is an argument for reusing one pool across sweep points rather than
  building one per point. Measuring it is itself hazardous: `spawn` brings a
  worker up by re-importing the parent's `__main__`, so a pool started from a
  module without a main guard re-runs that module in every worker. The first
  version of this measurement fork-bombed the machine it was measuring.

If that configuration misses the budget, the architecture is revised in W0. That
is the whole point of the milestone — and it is what happened: see the note
above and `docs/decisions.md` § w0i.

**Two things the spike taught about benchmarks, which its own review found the
hard way and which every later harness inherits:**

- **A benchmark must witness its own fixtures.** The one handler that paid for
  the dispatcher's two expensive escape valves — a gate answered mid-attack, a
  reaction spent — ran for a while without ever firing, because the hero's hit
  point pool was sized so its condition could never hold. The rate looked fine
  the whole time. A benchmark that watches only the rate cannot tell an
  optimisation from a rider that quietly stopped working, so the fixtures now
  carry a test naming the handlers that must have applied at least once, read
  off the no-op detector's counters. The CI gate (`w3i`) inherits the
  requirement.
- **A benchmark whose unit of work depends on the dice cannot carry
  exact-integer assertions.** The spike's counts were true of the seed they were
  written on and false on 3 seeds in 60. The fix is an invariant — a
  configuration that resolves the same number of attacks whatever the dice do —
  not a larger sample, and the golden harness (`w3a`) inherits that too.

> **A third reader arrived with the scorer.** It runs per decision point over
> every offered action and its candidate targets, and on a turn where control is
> offered it estimates the *enemies'* output too — ~30 estimator calls against a
> turn's ~2 attack resolutions, roughly doubling where a debuff is on the ballot — so the naive implementation is a 3–10×
> regression on the turn rather than a rounding error. The budget stands because
> almost all of `Ê_dmg` is static (memoised dice means, a `p_hit` lookup table on
> `(attack_bonus − AC, advantage_state)`, a per-turn score cache), and the target
> is **under 30% turn overhead** — measured by `w8g`, not argued here. It is the
> one part of § APL derivation that touches the hot path at all.

> **A second reader of this budget arrived after M0.** § APL derivation turns the
> simulator into the inner loop of an optimiser, which multiplies the sample count
> by two to three orders of magnitude. That does *not* reopen this number: no
> plausible CPython speedup covers 10²–10³, so derivation buys its budget on the
> sampling side (common random numbers, racing, staged search, a cheap proxy
> objective, memoisation) rather than on the engine side. What it does change is
> the value of two levers already listed below — **adaptive stopping** and
> **per-iteration seeding** — from conveniences into load-bearing requirements,
> because paired evaluation on a fixed seed set is what makes the search affordable.

### Levers

Per-creature precomputed dispatch tables (never scan a global bus); `__slots__`
on every hot object; contexts reset and reused per attack rather than allocated,
which matters at 4M resolutions — **but only with `reopen()` compiled per
context class**: the general version, which validated its keyword arguments
against the field list at every call, cost more per call than constructing the
context from scratch, and pooling was a net *loss* until `w0i` moved that check
to class-creation time and let Python's own argument binding do it (that one
change is 26k against 20k). A later reader who simplifies the generated
`reopen()` back into a general one will re-take a lever that does not work;
immutable specs (`ResourceSpec`, `Duration`,
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
- **Enough work per timed run.** M0 measured a 9% spread across three
  consecutive 200-encounter runs on one machine and 0.2% at a thousand. A gate
  looking for a 10% regression inside a 9% spread is a coin toss, so a thousand
  encounters is a floor rather than the first number to trim when the suite
  feels slow.

A gate that fires on noise gets disabled within a month, at which point
performance decays exactly as predicted. Run it tracked-but-non-blocking until
its variance is known, then turn on the failure.

**When it does fire, the levers are ordered and the order is a decision, not a
preference.** M0 spent the cheap ones already (see `docs/decisions.md` § w0i);
what is left in reserve is the *shape* of `DamagePart` and `Modifier` — a
`NamedTuple` constructs in ~0.38 µs and a plain slotted class in ~0.28 µs
against a frozen dataclass's ~0.9 µs, and there are six of them per hit. What is
**not** in reserve is the object model: collapsing an attack's contexts into one
or dropping per-part provenance buys real time and costs `parts_by_source`, the
no-op detector and the trace, which are what make ~630 agent-written features
safe to accept. M0 declined that trade with the numbers in front of it; a later
agent under CI pressure should not re-open it without them.

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
dndsim derive BUILD --scenarios scenarios.yaml --budget 2000  # search the APL
dndsim derive PARTY --joint --sweeps 3                        # coordinate ascent
dndsim fit builds/corpus.yaml                                 # refit the scorer
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
- **`derive`** searches the APL rather than reading it, and emits ordinary build
  YAML plus the ablation and optimism report (§ APL derivation). It is the
  command that makes `compare` a comparison of builds rather than of authors, and
  it exits non-zero when the winning policy leaves the plausibility band — an
  optimiser that beats the baseline by 40% has usually found a bug, not a line.
- **`fit`** refits the scorer's coefficients across the declared corpus and
  reports them in attacks-worth units with per-build residuals on **held-out
  builds**. It is run once per content wave, deliberately, never continuously —
  a coefficient set that drifts silently makes every archived derivation
  incomparable, which is the failure the content-version pin already exists to
  prevent.

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
  features/   base.py params.py perturbation.py TEMPLATE.py
              classes/<class>/[subclasses/<subclass>/] feats/ masteries/
              fighting_styles/ species/
  actions/    base.py attack.py opportunity_attack.py dash.py dodge.py
              disengage.py cast_spell.py
  spells/     base.py concentration.py templates.py level0..level6/
  apl/        expr.py schema.py namespace.py apl.py scorer.py coefficients.py
  builds/     schema.py loader.py
  engine/     world.py query.py attack.py damage.py saves.py turn.py
              encounter.py adventuring_day.py triggers.py estimate.py
  content/    fetch.py loader.py refs.py tables.py weapons.py
  sim/        runner.py day.py compare.py sweep.py
  search/     candidates.py encoding.py evaluate.py driver.py
              ablate.py party.py emit.py objectives.py fit.py
  reporting/  result.py collect.py stats.py render_text.py render_json.py
  tools/      check_features.py noop_report.py gen_docs.py skeleton.py

builds/     # hand-authored YAML: characters, parties, encounters — in git
            # corpus.yaml declares the builds the scorer's coefficients fit on
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
- **`search/` imports downward and is imported by nobody.** It consumes `api.py`
  and `apl/` and is consumed only by `cli.py`. Nothing in `sim/`, `engine/`,
  `hooks/` or `features/` may import it, and it may not import `engine/` or
  `hooks/` directly. This is what keeps the claim in § APL derivation true — that
  policy search is a client of the simulator rather than a second engine — and it
  is a one-line import check in `dndsim check`.
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
61 tasks, their dependency edges, delegation modes and the task-packet template.
Its § The W0 debt register is the ledger of what W0 left for later waves to
retire, and which task retires each.

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
| **W8** | **Policy derivation** — the expected-damage estimator, buff evaluation by counterfactual, the global scorer and its corpus fit, candidate generation, paired evaluation, racing search, ablation, coordinate ascent, `dndsim derive` | **M5:** attrition under a *derived* policy — the number that is about the build |

W0–W4 are the commitment being made now; W5–W8 are sketched to prove the
architecture doesn't paint itself into a corner. **W6 depends only on W4**, not
on W5 — attrition against target dummies is reachable with martials alone, so
M4 does not wait for casters.

**W8 is last in sequence and first in importance**, and the tension is real
rather than a scheduling accident: derivation is a consumer of everything else,
so it cannot be built before there are features to search over and an objective
to search against. What that means for now is not "start it earlier" but "cut its
seams earlier" — the two W2 seams named in § APL derivation (programmatic `Apl`
construction, and an API that takes an explicit seed list and returns per-seed
results) are cheap in W2 and a migration across every file in `builds/` after
that. M4 remains the stated v1 goal; M5 is what makes M4's number a property of
the build rather than of its author.

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
dndsim derive builds/fighter_gwm_l5.yaml --scenarios builds/scenarios.yaml
```

Determinism is the backbone: identical seed + identical build ⇒ byte-identical
event log, so every bug is reproducible and every regression diff is readable.
`compare` and `--sweep` are what make this a sim rather than a calculator, and
are the primary manual check that results are sane. `derive` is what makes
`compare` honest: without it, a comparison of two builds is partly a comparison
of the two APLs somebody wrote for them.

## Biggest risks

Ordered by expected cost, which is not the same as by likelihood. The first four
share a shape worth naming: **each is a check that passes while proving nothing**,
and that is a far more dangerous failure than a check that fails.

1. ~~**The performance spike passes on a stub.**~~ **Closed at M0, and replaced
   by its successor: the headroom erodes between M0 and the gate that guards
   it.** The original risk was real and the mitigation worked — the hostile
   configuration measured 17k where a stub would have cleared any budget, the
   architecture was revised, and the budget was revised with it. What that
   leaves is a live risk of a different shape. The measured ~31.5k sits 5% over
   a 30k budget rather than 60% over a 50k one, `w0i` says plainly that what it
   understates is the per-round cost — effects and resources did not exist to be
   swept — and W1 is the wave that adds exactly that. Nothing in the plan
   re-measured until `w2e`, twenty tasks later. *Mitigated by:* `w1k`, the M0
   re-measure at the end of W1 against the real `EffectStore` and `ResourcePool`;
   `w2e`'s re-run when the real APL compiler replaces the hand-compiled one; and
   `w3i`'s CI gate, which must witness its own fixtures rather than watch the
   rate alone. *The failure mode to watch for is not a slow feature — it is the
   budget being lowered a second time.* A budget that follows the measurement
   down is not a budget.
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
4. **The derivation fits noise and reports a policy that is not better.** A
   search scoring thousands of candidates against one seed set will find a winner
   whether or not one exists; the search-set number is biased upward by
   construction, and a policy selected on noise looks exactly like a discovery.
   This is the most expensive version of the shape above, because the whole
   project's headline claim rests on it and there will never be a ground truth to
   check it against. Two sub-risks ride along: **scenario overfitting** (a policy
   derived against one AC and enemy count, reported as general) and
   **exploitation** — an optimiser with 10^5 evaluations finds every place the
   rules are implemented too generously, and reports the exploit as a strategy.
   The scorer adds two more of its own. **Estimator/resolver divergence** is the
   nastier: if `engine/estimate.py` and `engine/damage.py` ever disagree — about
   whether `flat` doubles on a crit, say — the fit absorbs the discrepancy into a
   coefficient and hides it, and every decision in the project is then made on a
   wrong number with nothing red anywhere. **Perturbation-profile drift** is the
   likelier: a buff declares `+2` where its code grants `+1d4`, or a debuff
   declares the wrong save ability, and every decision involving it is scored
   wrong with nothing red anywhere either. Debuffs carry a third of their own —
   their value is the product of three estimates (`p_land`, a geometric expected
   duration, and a per-target horizon), so an error in any one compounds, and
   control is where the scorer's confidence most exceeds its evidence.
   *Mitigated by:* the disjoint holdout seed set with a reported optimism gap, a
   scenario *distribution* with a per-scenario breakdown, common random numbers
   and paired tests so a real difference is resolvable at an affordable sample
   count, the one-standard-error parsimony rule with per-entry ablation
   intervals, the plausibility band that exits non-zero, and the Δ-vs-baseline
   comparison that catches both a search that is not searching and a win too
   large to be real. All six are in § APL derivation, and the exploitation
   sub-risk is also the project's best content-bug detector once triaged rather
   than trusted. Divergence is caught by the property test binding the estimator
   to 10^5 real resolutions; profile drift by `w3l` measuring each effect's real
   DPR difference **on both sides** against what its perturbation predicts, on
   the same terms as every `expectation`; the compounding on control by the wider
   priors on `w_off`/`w_def` being paid for with their own ablation rows; the planning gap by the per-build overlay that exists to
   carry what the scorer gets wrong, and by the ablation row that reports the
   scorer's marginal value per build.
5. **The paper pass degrades quietly.** Classifying ~400 prose features to hooks
   is the highest-leverage task in the project and the most tedious, and nothing
   downstream detects a sloppy classification until features are written against
   it. *Mitigated by:* batching the classification against a strict row schema
   with a confidence column, human triage of the low-confidence and gap rows
   only, and the additive-vs-structural split that makes the hook-gap budget
   honest.
6. **Review capacity, not agent throughput, is the constraint.** Roughly 150
   nominal agent-days funnel through one person; every parallelism gain lands on
   the same desk. *Mitigated by:* designing artefacts to be reviewable in minutes
   — disposition tables, quoted citations, arithmetic in goldens, the file-size
   cap — and by sizing batches for a review sitting rather than for agent
   capacity.
7. **Hook drift** — feature #340 needs a hook that doesn't exist, and an agent
   improvises by mutating private state or picking a wrong hook that happens to
   fire. Ranked below the others because it is the risk this design already
   defends best. *Mitigated by:* the paper pass in W3, the frozen registry with a
   CI diff check, both escape valves, and a linter banning private access and
   stray `random` imports.
8. **Interaction correctness** — features individually right, combinations wrong;
   grows quadratically while features grow linearly. *Mitigated by:* structural
   prevention first, one resolution order in one file, the human-maintained
   interaction matrix, and property tests (damage never negative, HP
   conservation, reaction economy per turn, effect count returns to baseline
   after every encounter).
