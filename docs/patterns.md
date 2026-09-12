# Pattern catalogue

Vocabulary for maintainers arguing about the design. Moved out of
[DESIGN.md](../DESIGN.md) deliberately: pattern names help two people disagree
precisely about architecture, and actively harm a batch author, who needs the
five mutator shapes rather than the nomenclature behind them.

**Do not put this file in a batch agent's context.** See DESIGN.md § The five
load-bearing ideas for the rule and the reason.

| Pattern | Where | Why it earns its place |
|---|---|---|
| **Intercepting Filter / Pipeline** | `hooks/dispatch.py` | Ordered handlers over one shared context — the core mechanism |
| **Chain of Responsibility** (veto variant) | `Phase.EARLY` / `FINAL` handlers | Any handler may veto an attack or a cast |
| **Parameter Object** | `hooks/contexts.py` | Contexts bundle the in-flight state features are allowed to touch |
| **Registry + Factory Method** | `HOOKS`, `FEATURE_REGISTRY` | id → class resolution; closed registries make typos import errors |
| **Command** | `actions/base.py` | `is_legal()` / `execute()`; lets the economy queue and re-poll actions |
| **Strategy** | `apl/`, `core/rng.py` | Swappable decision policy; `MersenneRng` vs `ScriptedRng` |
| **Interpreter** | `apl/expr.py` | APL conditions are a small expression grammar compiled to an AST |
| **Specification** | APL `if:` clauses | Composable boolean predicates over domain objects |
| **State** | `conditions/`, `effects.py` | Prone/Frightened/Raging alter behaviour by being attached |
| **Mediator** | `engine/world.py` | Creatures never talk to each other directly |
| **Adapter / Anti-Corruption Layer** | `content/loader.py` | 5etools' schema never leaks into the domain model |
| **Visitor** | W7 stat-block parser | Walks 5etools entry trees by node type |
| **Builder** | `builds/loader.py` | Assembles a `Character` from YAML in validated steps |
| **Unit of Work** | `resources.transaction()` | Multi-resource costs commit or roll back atomically |
| **Facade** | `SpellSlots`, `world.query` | Ergonomic surface over nine pools / spatial queries |
| **Value Object** | `DamagePart`, `Modifier`, `TimeKey`, `Duration`, `ResourceSpec` | Frozen, comparable, safely shared |
| **Object Pool + Flyweight** | context reuse, shared specs | The performance budget depends on both |
| **Null Object** | APL default policy, empty dispatch list | Absent config behaves predictably, never crashes |
| **Template Method** | `HookSource.__init_subclass__` | Fixed validation skeleton subclasses cannot skip |
| **Ports & Adapters** | `api.py` vs `cli.py` | A UI added later is another adapter, not a rewrite |
| **Inversion of Control** | the whole feature API | The engine calls features; features never drive the engine |
| **Abstract Factory** | `search/candidates.py` | Generates the policy candidate pool from the schema, so no per-class table exists |
| **Memento**-adjacent | `search/encoding.py` | The random-key vector is a flat, samplable representation a policy is restored from |
| **Client, not layer** | `search/` over `api.py` | Policy search consumes the simulator; nothing in the simulator knows it exists |
| **Layer, not alternative** | `apl/score/` | The value model the founding design anticipated. It is not a second policy Strategy: `best_scored` is one action id *inside* an `Apl`, so the scorer is reached through the ordered list rather than in place of it |
| **Decorator** (conceptual) | the APL overlay over `best_scored` | The per-build search wraps the global scorer with its departures, rather than replacing it |
| **Registry + Composite** | `apl/score/registry.py` | The score is a sum of independently declared terms; adding one is a file, not a formula edit |
| **Strategy** | `apl/score/horizon.py` | `H` was the most-revised quantity in the design; this is where a learned value function would land |

## Patterns by package

- `api.py` vs `cli.py` — **Ports & Adapters**. The boundary that makes a future
  UI a client rather than a rewrite.
- `engine/world.py` — **Mediator**. Creatures never reference each other
  directly; targeting, line of sight and reaction audiences all go through the
  world. This keeps `Creature` from accumulating knowledge of the whole
  battlefield. `world.query` is a **Facade** over the spatial index.
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
  fail loudly with a useful message, rather than via a constructor with twenty
  arguments.
- `search/` — a **client** of `api.py`, deliberately not a layer of it. It
  generates `Apl` **Strategy** objects and uses the simulator as a noisy oracle,
  which is why W8 needs no engine change at all. The candidate pool is an
  **Abstract Factory** over the `NamespaceSchema` rather than a hand-written
  list: a hand-written pool would launder the author's priors through a search
  and defeat the point. Import direction is enforced — `search/` may import
  `api.py` and `apl/`, and nothing may import `search/` except `cli.py`.
- `apl/score/` — the **Strategy** slot § APL of DESIGN.md reserved from the
  start ("a built-in scoring heuristic can later be dropped in as an alternative
  Policy implementation"). It is not an alternative in the end but a *layer*: the
  ordered list keeps its place and gains one entry, `best_scored`, so the two
  compose rather than compete. `engine/estimate.py` is the expected-damage
  estimator it reads, and it is engine code precisely because it must share the
  crit, advantage and defence rules with `engine/damage.py` by construction —
  and is pointedly *not* behind a Strategy of its own, since that would be a way
  to bypass the property test binding it to the resolver. Inside `apl/score/`,
  the registry is the hook registry's shape minus the phase bands: terms are
  summed, addition commutes, so nothing can run at the wrong time.
- `core/rng.py` — **Strategy**. `MersenneRng` in production, `ScriptedRng` in golden
  tests. The linter bans `import random` everywhere else, so determinism is
  structurally guaranteed rather than merely intended.
- W7 stat-block parser — **Visitor** over 5etools entry trees, dispatching on
  node type (`{@atkr}`, `{@actSave}`, `{@recharge}`). The closed tag vocabulary
  is exactly the "stable element hierarchy, growing set of operations"
  situation Visitor is for.

## The tension worth naming

**Object Pool** and mutable shared contexts are in direct conflict with the
**Value Object** discipline. The split is deliberate — contexts are pooled and
mutable (hot path, engine-owned), everything a feature stores is frozen (cold
path, author-owned). The attribute lock is what keeps authors on the safe side
of that line.
