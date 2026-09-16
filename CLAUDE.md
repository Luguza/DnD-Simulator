# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## Project state

A Monte Carlo D&D 5e (2024 / XPHB) combat simulator — "SimC for D&D". **Almost
no simulator code exists yet**: the repo holds the design (`DESIGN.md`), the
dependency-ordered build plan (`ROADMAP.md`), the uv/pre-commit toolchain, CI,
and five packages — `rules/` (the vocabulary every later package imports),
`core/` (randomness, dice, the clock, the grid, the error hierarchy), `hooks/`
(the closed registry of 39 extension points, the 31 contexts they hand out, and
the decorator, source base class and dispatcher that run them), `creatures/`
(`Creature`, `StatBlock`, `Armour` and the action economy) and `engine/` (the
damage resolution order and the movement observer).

Work is at wave **W1**; **W0 is complete and M0 was met on measurement**:
`w0a`–`w0i` are done and the pessimistic performance spike measured **~31.5k
attack resolutions/sec/core** against a budget of 30k. **That margin is spent
and the budget is now projected to be missed** — `w1f`'s completed attack
pipeline cost 5.5% of the rate, which puts the hostile configuration at ~29.8k,
under the ≥30k floor. It is a projection across machines rather than a
measurement; `w1k` is the re-measure that settles it, and nothing revises the
budget on a projection (§ Performance below, `docs/decisions.md`
§ The W1 critical-path review).
The gate fired on the way — the spike came in at 17k
against the 50k the design had assumed, a revision pass across the hot path
took it to ~31.5k, and the budget was revised to 30k because 50k turned out to
sit at the theoretical floor of this object model in CPython.
`docs/decisions.md` § w0i has the measurements, what each change bought, and
the two levers deliberately not taken. **W1 is under way.**

The plans were then re-read against what the wave measured
(`docs/decisions.md` § The W0 plan review). Two things came out of it that
matter before writing W1 code: **`ROADMAP.md` § The W0 debt register** lists the
first cuts and deferred obligations W0 left behind — twenty-one rows open, of
which fifteen are W0's, one is `w1e`'s, two are `w1f`'s and three were found by
the W1 critical-path gate — and the task that
retires each;
check it for the file you are about to touch. And **`w1k` is a
new task**, an M0 re-measure at the end of W1, because the measured ~31.5k had
5% of headroom and W1 adds the per-round cost the spike does not model.

Two tasks have landed since. `w0g3` cut `engine/triggers.py`, holding
the **movement observer** — who is offered a reaction to a move, on every
five-foot step, and in what order. It is the audience for an opportunity attack
and not the attack itself, which is still `w2b`'s Command and `w2c`'s turn loop.
Read `docs/decisions.md` § w0g3 before touching movement, reactions or the
`Gate` path.

And `w1a`, the first task of W1, **completed `Creature` and `StatBlock`** and
added `creatures/economy.py`. Three things changed that every later task builds
on: **AC is derived from an `Armour` value object** and `StatBlock.armour_class`
is gone rather than defaulted, so a build can no longer type a number in; a
creature **owns an `ActionEconomy` and hands it to its own dispatcher**, so
`Dispatcher.bind()` is now the gate's alone; and the `Combatant` protocol
**carries the three hit-point numbers**, so a handler can finally ask whether
its target is bloodied. Derived stats (`modifiers`, `proficiency_bonus`,
`crit_threshold`) are settled at construction and `Creature.stats` is read-only
so they cannot go stale. `docs/decisions.md` § w1a has the reasoning; it retires
debt-register rows #1, #2, #4 and #10.

Then `w1e` **completed `engine/damage.py`**, and it moves numbers rather than
files: the resolution order is now the 2024 text's — immunity → flat reductions
→ halvings → resistance → vulnerability → temp HP → HP — which overturns
`w0g2`'s flat-after-resistance ruling, keeps its flat-before-halving one inside
the quoted adjustment step, and answers the open question about two halvings
from the "counts once" clause. The reorder's own question is the one to know
before touching this file: **the untyped steps now run before the typed ones, so
an untyped adjustment is spent across a mixed-type hit in proportion** — never
by looking ahead to which types are resisted, never in the order the damage
parts arrived, and by a method that cannot let a larger reduction land more
damage. `Mitigated.total` is therefore the sum of `Mitigated.amounts` again,
where `w0g2` had the two deliberately disagree. `docs/decisions.md` § w1e has
the arguments; it retires debt-register row #7 and opens #23, and its review is
the case behind the citation rule in § Working rules.

Then `w1f` **cut `engine/attack.py`**, the second resolution order and the one
~400 features plug into: declare -> roll -> hit/crit/miss -> damage -> resolved,
in the file that owns it. `tools/skeleton.py`'s hand-sequenced `attack_once()`
is gone with it, so the benchmark now measures the sequence the project ships.
Four things every later task inherits: a **Critical Hit below 20 still has to
hit**, so the threshold decides only whether a hit is critical; a **veto is
honoured at the declaration and raises after it**, and a prevented attack has
`outcome is None` because `Outcome` describes rolls; **damage on a miss is
rolled and resolved, without firing `on_damage_roll`** (the Graze mastery's own
sentence); and the **`Weapon` protocol is widened to six members**, which is the
shape a feature reads a weapon through. It costs 5.5% of the spike's rate,
measured and attributed — more than all of M0's 5% headroom, in the wave the
plan review said would spend it. `docs/decisions.md` § w1f has the arguments; it
retires debt-register rows #3 and #6 and opens #24 and #25.

The W1 critical-path gate then re-read the plans against those three tasks
(`docs/decisions.md` § The W1 critical-path review). One thing from it changes
how a W1 task is done: **the margin over the 30k budget is gone**, so `w1b`,
`w1c`, `w1d`, `w1i` and `w1j` each run the spike paired and record what they cost
(`ROADMAP.md` § Sequencing principles, 10) and `w1k` totals attributions instead
of discovering a number. Two are worth knowing before you open a file: **`w1c`
sweeps the pending-effect refusal in `engine/attack.py` as well as
`engine/damage.py`**, four call sites and four contract tests across two files
rather than the one file its row used to name; and three unowned obligations the completed tasks had recorded only in
their own decision records are now debt rows **#26** (a nested attack raises on a
shared context pool), **#27** (heavy armour never costs its 10 feet of speed) and
**#28** (movement is never charged against the economy).

`DESIGN.md` § APL derivation and § The scorer were added after M0 and are the
largest change to the project's goal since the founding document: the APL is
**derived by search per build, not authored**, over a global fitted value model
shared by every build. That adds wave W8 and milestone M5, three signatures to
W2, and one authoring declaration to W3 (the `Perturbation` profile on a buff or
debuff) that must exist before the W4 grind. Nothing in W0's code is affected.

Both documents are load-bearing: `DESIGN.md` fixes the architecture and the
vocabulary, `ROADMAP.md` fixes what may be built when and by whom. Read the
relevant sections before writing code in an area — they are the contract, not
background reading.

## Commands

The toolchain is **uv**, not pip.

```sh
uv sync                              # create .venv with the dev toolchain
uv run pre-commit install            # install the git pre-commit hook
uv run pytest                        # tests (wall-clock benchmarks deselected)
uv run pytest -m bench               # the M0 budget, asked deliberately
uv run pytest tests/path/test_x.py::test_name   # single test
uv run python -m dndsim.tools.spike --startup   # the spike, with worker cost
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
class/subclass/feat features + ~18 species traits + 212 combat spells) plug into
a closed registry of 39 hooks. Features never drive the engine; the engine
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
  immunity → flat reductions → halvings → resistance → vulnerability → temp HP →
  HP. The adjustments-then-resistance-then-vulnerability run is quoted from the
  XPHB, not a house ruling; resistance and vulnerability therefore do **not**
  cancel exactly on odd damage.
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
`core/rng.py` and the linter enforces it; use the `Rng` strategy (`MersenneRng`
in production, `ScriptedRng` in golden tests).

**Boundaries that must not leak.** `content/` is an anti-corruption layer —
5etools vocabulary (`dmg1`, `atkr`, `classTableGroups`, `refSubclassFeature`)
stops there and never reaches the domain model; scaling numbers reach features
only via `table_value()`, never hardcoded. `cli.py` is a thin adapter over
`api.py` and holds no simulation logic; text output is rendered from the same
`SimResult` JSON the API returns, so the two cannot disagree.

**The policy is derived, not authored, and it has two layers.** A build's APL is
produced by statistical search per build (`DESIGN.md` § APL derivation,
`ROADMAP.md` W8), because nobody knows the best line of play and a hand-written
APL makes every reported number a measurement of *build plus author*. A
hand-written `apl:` is the **baseline** the derived policy is scored against,
never the input. Underneath the search sits a **global scorer** (§ The scorer):
one fitted value model, shared by every build, scoring offered actions in
expected-damage points — so the searched APL carries only this build's
*departures* from generic good play. Buffs and debuffs are scored by **perturbing the
estimator and differencing** (§ Buffs and debuffs) rather than by a constant,
split **offensive/defensive by what changes, not by whom it targets** — so a stun
scores in both terms with no special case. A debuff additionally multiplies by
`p_land` from the save DC, uses a geometric `E[rounds]` for save-ends effects,
and takes a per-target horizon. **`p_land` and the horizon index the creature the
perturbation is applied to, never the creature whose output moves** — only a
cross-side effect separates the two, so the one-sum form looks right on every
same-side effect until it meets one, and getting it wrong scores Faerie Fire
as though it always lands and gives the two halves of one stun different landing
probabilities. That is why re-applying an active effect scores
zero with no condition written, why a second source of advantage scores ~0
without anyone encoding that advantage does not stack, and why nothing buffs on
the killing round or stuns the enemy on 3 hp.

**Party tactics are emergent, not a coordination protocol** (§ Party tactics).
A killing blow is the debuff that removes all of an enemy's remaining output, so
`p_kill · Ê_dpr(e) · H` rides `w_def` and **focus fire needs no shared focus
target** — a blackboard was considered and rejected. The ally-side re-estimate
includes **target availability**, so a control spell's `ΔÊ_ours` goes negative by
the damage it strands: the same spell scores well beside a sniper and badly
beside four engaged bruisers. **Composition enters through the inputs, never
through the coefficient vector** — every term is already computed over the actual
party, which is what keeps one global fit defensible. What must be *told* to the
derivation is that a derived APL is conditional on its party: the scenario spec
carries a party axis and the corpus spans compositions. Five consequences bite
before W8 exists:
`Apl` must be constructible programmatically and serialisable back (`w2e`), the
action id `best_scored` must be **reserved** in W2's closed vocabulary (`w2e`),
the API must accept a seed list and an APL override and return **per-seed**
results (`w2g`), a buff or debuff feature must declare a `Perturbation` profile from
`w3f`'s closed vocabulary **before the W4 grind writes them** or it is a
retrofit,
and `search/` is a client of `api.py` that may never import
`engine/` or `hooks/`. Anything in `search/` that wants an engine change is a bug
in `search/` — with the one stated exception of `engine/estimate.py`, which is
engine code the *scorer* needs and the search does not.

**The score is a registry, not a formula** (§ Extensibility). `apl/score/` is the
hook registry's shape on purpose — a closed CI-diffed registry, an
attribute-locked pooled `ScoreContext`, a uniform handler — so learning one
surface teaches the other; it is simpler in that terms are summed and addition
commutes, so there are no phase bands. Adding a term is one file, one
coefficient and one ablation row. **Extensibility without falsification is rot:
a term that cannot show marginal value on held-out builds is cut, not
deprecated**, and `docs/score.md` is generated from the registry. Provenance
carries a **score-model version** hashing the registered terms, and a coefficient
set that does not match it is refused at load rather than used with the missing
term treated as zero. Deliberately *not* extensible, and each for a reason:
`engine/estimate.py` (a Strategy there bypasses the property test that is the
scheme's linchpin), the damage resolution order, the gauge (identifiability needs
exactly two pins — `score(null) = 0` and `w[attack] = 1`; `b[attack] = 0` is a
relaxable *assumption*, not a third pin), and the single unit.

**The estimator and the resolver may never diverge.** `engine/estimate.py`
computes expected damage in closed form (hit chance from attack bonus vs AC, or
save DC vs save bonus, summed over targets); `engine/damage.py` resolves it. If
they disagree about the crit dice/flat split or the defence order, the scorer's
fit absorbs the discrepancy into a coefficient and hides it, and nothing goes
red. It computes **two** quantities: `Ê_raw = E[X]`, the damage dealt, and
`Ê_eff = E[min(X, pool)]`, the *pool* removed (temp HP, then hit points, in the
order `engine/damage.py` spends them) — the score's damage term uses `Ê_eff`,
which is what stops a 40-damage nova being scored as 40 against a 5-hp enemy.
The property test binds **both** to 10⁵ real resolutions, and that split is why:
capping a single estimate in place would put the estimator in deliberate
disagreement with the damage roll, which is the divergence the test exists to
catch. It is not optional.

**Performance is a design constraint, not a later concern.** Budget is ≥30k
attack resolutions/sec/core, revised down from 50k by the W0 spike itself,
which measured ~31.5k on the hostile configuration (`docs/decisions.md` § w0i)
and has since spent that margin on `w1f`'s completed attack pipeline — so the
budget is the number to hold, and it is not revised a second time by anyone who
cannot attribute the shortfall (DESIGN.md § Biggest risks). Contexts are pooled and mutable
(engine-owned, hot path); everything a feature stores is frozen (author-owned,
cold path). **The hot path is measured, not guessed** — `tools/spike.py` is the
harness, and a change to `hooks/dispatch.py`, `hooks/contexts.py`,
`engine/damage.py`, `core/rng.py` or `core/dice.py` should be run past it. Three
of those files carry optimisations whose reasons are only in the decision
record: `reopen()` is generated per context class, `Dice.parse` is memoised, and
the dispatch loop reaches into the context's frame on purpose.

## Per-module decision records

[`docs/decisions.md`](docs/decisions.md) holds the *why* behind each completed
task's code — the reasoning that keeps a settled decision from being
re-litigated by the next agent through the area. **Read the section for an area
before writing code in it**; everything above applies everywhere, everything
there is local to a module.

| Touching | Read |
|---|---|
| `pyproject.toml`, CI, tooling | § w0a |
| `rules/` | § w0b |
| `core/rng.py`, `core/dice.py` | § w0c |
| `core/timeline.py` | § w0d |
| `hooks/registry.py`, `hooks/definitions.py` | § w0e |
| `hooks/contexts.py`, `core/errors.py` | § w0f |
| `hooks/decorator.py`, `hooks/source.py`, `hooks/dispatch.py` | § w0g |
| `creatures/`, `core/geometry.py` | § w0g2 |
| `engine/damage.py`, the resolution order | § w0g2, § w1e |
| `engine/attack.py`, the attack sequence | § w1f |
| `creatures/creature.py`, `stats.py`, `economy.py`, AC, the action economy | § w1a |
| `tools/skeleton.py`, `tests/bench/test_skeleton.py` | § w0h, § w1f |
| `tools/spike*.py`, `tests/bench/test_spike.py`, the hot path | § w0i |
| `ROADMAP.md`, `DESIGN.md`, planning a wave | § The W0 plan review, § The W1 critical-path review |
| the performance budget, pricing a change | § w0i, § The W1 critical-path review |
| `engine/triggers.py`, `Dispatcher.responsive()` | § w0g3 |

Add a section there when a task lands, rather than growing this file.

## How work is scheduled

Tasks are taken one at a time off [`docs/progress.md`](docs/progress.md), the
serial queue, and driven to a merged pull request by the loop in
[`docs/workflow.md`](docs/workflow.md): `/next-task` implements, the
`claude-code-review.yml` reviewer answers, and the queue advances only by a
merged diff. A `⏸` row is a plan review (`/plan-review`) that the loop runs and
merges itself. The human reads the code only at ROADMAP milestones: CI's
`human-merge-required` job fails on purpose for a **milestone review** PR and
for an edit to `.github/workflows/`, and nothing else stops the chain but a
`held` row (`docs/workflow.md` § Merge authority).

## Sequencing rules

`ROADMAP.md` has 77 tasks across waves W0–W8 with explicit dependency edges.
Three constraints dominate:

- **Nothing in W4 or later may start before M2** (API frozen, authoring skill
  written, paper pass done). The W3 freeze gate is what prevents 400 subtly
  wrong features.
- **W8's seams are cut in W2 and W3, not in W8.** Policy derivation is the last
  wave and the most important one; it cannot start earlier (there is nothing to
  search over until W4 lands features and W6 lands the objective), so the
  compensation is the signatures named above — near-free in `w2e`/`w2g`, and in
  the reserved id's case a migration across every file in `builds/` afterwards.
  **One seam is authoring surface and lands in W3**: the closed `Perturbation`
  vocabulary the scorer reads off a buff or debuff feature (`w3f`, `w3g`, `w3b`,
  `w3l`). Declared
  after the grind it is a retrofit across every one of the ~30–50 effects in
  `features/`.
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
  `expectation` that never imports `dndsim`. The citation rule in § Working
  rules governs what may be written around that sentence.
- `data/` is gitignored — fetched 5etools JSON, reproducible via
  `dndsim content fetch`. Hand-authored `builds/*.yaml` are in git.

## Working rules

- Follow the YAGNI principle.
- Use skills with care: only when you make large changes or when they
  specifically apply.
- The agent that writes a feature writes its golden, but is never the only party
  that has computed the number: a separate read-only agent re-derives it from
  the quoted `source_ref` alone before the batch is accepted (`w3k`).
- Never let the agent that writes the feature write the tests.
- Critically review every task specification before implementing it — the task
  packets in `ROADMAP.md`, the contracts in `DESIGN.md`, and any task given in
  chat. Check them against the rest of the design for gaps, contradictions and
  unstated assumptions.
- **Cite where a rule is written; do not restate it.** A pointer — XPHB
  "Damage and Healing", `DESIGN.md` § Structural prevention, `docs/decisions.md`
  § w1e — is checkable and cannot drift from its source. A paraphrase, a
  re-derivation, or an argument rebuilt beside the code is a second copy that
  can, and it is what a later agent disproves before reopening a settled
  decision for the wrong reason. Where the thing cited is the project's own
  ruling, it lives in `docs/decisions.md` and the code points at it.
- **Do not comment code that does not need explaining**, and never derive a
  statement from the lines beneath it. Comment the trap and the ordering that is
  not obvious; nothing else. Four of `w1e`'s five review rounds found prose of
  that kind; the fifth found a real bug (`docs/decisions.md` § w1e).
- Ask the user for clarification when something is ambiguous or looks wrong
  instead of guessing.
