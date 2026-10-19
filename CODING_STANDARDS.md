# Coding standards

The standards axis of every review (`.claude/skills/pr-review/SKILL.md`). The
reviewer reads this file and the implementer does not have to: implementation
carries the most context pressure and review the least, so the rules a
reviewer can enforce from a diff live here instead of in `CLAUDE.md`.

`CLAUDE.md` and `DESIGN.md` are the contract; this is the checklist. Report only
findings you can tie to a **specific line** and a **specific rule below**. A
review that always finds something is a review nobody reads — "no findings" is a
valid and useful outcome.

Rank findings: **correctness bugs → boundary violations → everything else.**

**What belongs here.** A judgement call only. A violation with a fixed shape — a
banned import, a file-location rule, a naming pattern — gets a ruff rule, a
pre-commit hook or a test instead, and its line here is deleted when that check
lands. `/retro` applies that split.

## Check first, on every diff that touches damage or timing

1. **`dice` doubles on a crit; `flat` never does.** A static bonus (Rage,
   Dueling, an ability modifier) folded into `dice` — or dice written as a flat
   average — is a silent wrong number on every critical hit. Look for any
   arithmetic that merges the two fields.
2. **`Reset.TURN` is *any* turn boundary; `Reset.OWN_TURN` is yours.** A
   once-per-turn rider that recharges on `TURN` fires up to N times per round.
   "Once on each of your turns" is `OWN_TURN`; "once per turn" (Sneak Attack) is
   `TURN`. Quote the rules sentence and match it to the enum.

## Boundaries

- **Content never touches the engine.** A change under `features/`, `spells/`,
  `conditions/` or `content/` that also edits `hooks/`, `engine/` or `core/` is
  wrong by construction — the feature declares
  `NEEDS_HOOK = "on_X: <description>"` and returns `SKIPPED` instead.
- **The hook registry is frozen.** A new `define_hook()` belongs to a batched
  core change (`w3f`), never to a feature PR.
- **Damage resolution order lives only in `engine/damage.py`.** A resistance
  check, halving or temp-HP subtraction anywhere else is a second ordering that
  will disagree with the canonical one.
- **5etools vocabulary stops at `content/`.** `dmg1`, `atkr`,
  `classTableGroups`, `refSubclassFeature`, `refOptionalfeature` outside that
  package is a finding, as is a hardcoded per-level number that should come from
  `table_value()` — even when it is currently correct.
- **`cli.py` holds no simulation logic**; it renders the `SimResult` JSON
  `api.py` returns.

## Structural correctness

- **Advantage is a collection, not a boolean.** Any `advantage = True`,
  `not advantage`, or boolean flip is a finding; it is appended via
  `grant_advantage()`.
- **Contexts are attribute-locked.** A diff that assigns a field directly
  instead of calling a mutator is reaching around a guard.
- **Scope decides `self.owner`.** `if ctx.attacker is self.owner` in a feature
  means the wrong scope was chosen — fix the scope, not the body.
- **Contexts are pooled.** A feature holding a context past its handler, or
  storing mutable state, is a hot-path aliasing bug.
- **`LATE`/`FINAL` handlers need a `# PHASE:` justification comment.**
  Unjustified late phases are how ordering becomes load-bearing by accident;
  `rg "PHASE:"` should stay under ~30 hits repo-wide.

## Determinism

- **`import random` outside `core/rng.py`** is banned (the linter enforces it —
  check anyway when the linter is touched).
- Output must not depend on worker count, dict iteration order or wall-clock
  time: a run must reproduce byte-for-byte **at any `--jobs` value**, or
  `trace`, the goldens and every regression diff break at once.

## Citations and comments, on every diff

- **A docstring or comment covers only its own code.** A docstring says what
  its function, class or module takes, does and returns; a comment names the
  trap in the line beneath it. A design argument, a list of what is
  deliberately absent, a claim about another module, a caller or a future task
  is a finding: each is a claim that can drift, and every later review has to
  re-check it. It belongs in `docs/decisions.md`, and the code points there in
  one line. Flag it on lines the diff adds or changes only — the docstrings
  already in `src/` are a cleanup task's, not every PR's. Most review rounds so
  far were spent on prose rather than code (`docs/decisions.md` § w1e, #25,
  #42).
- **A citation points; it does not restate.** A paraphrase or a re-derivation
  beside the code is a second copy of the rule that can drift from it — flag it.
  Where a citation does assert a consequence, work the consequence out rather
  than reading it: `w1e` shipped three that were false (`docs/decisions.md`
  § w1e).
- **Deleting prose is a citation change.** A diff that removes an argument,
  a quotation or a worked example has to move every pointer aimed at it;
  `grep` the deleted phrase across `DESIGN.md`, `ROADMAP.md`, `CLAUDE.md`,
  `docs/` **and `src/`** — a docstring in another module cites its neighbours as
  readily as a plan does. Four rounds of #25's own review were dangling inbound
  pointers, one of them in `src/` because this list first omitted it.

## Evidence a feature PR must carry

- An **XPHB `source_ref` with the quoted rules sentence** — not just a page
  reference. The quote is what the code is checked against.
- **One golden test with exact-integer assertions**, its arithmetic written out
  in a comment. Distributions prove nothing; a hand-computed number does.
- **The author did not write the tests.** One agent producing both the feature
  and its golden only proves the feature does what the author thought — say so
  and ask for an independent test.
- Feature files stay **under 60 lines**; no `try`/`except`, no `hasattr`, no
  private access in `features/`.

## Repo hygiene

- A `pyproject.toml` dependency edit needs `uv lock` **in the same commit**.
- `data/` is gitignored and refetchable; a committed 5etools dump is a finding.
  Hand-authored `builds/*.yaml` belong in git.
- `docs/hooks.md` and `docs/apl.md` are **generated** — hand edits get
  overwritten and CI fails them as stale.

## Smell baseline

Fowler's code smells (_Refactoring_, ch. 3), after
[mattpocock/skills `code-review`](https://github.com/mattpocock/skills/blob/main/skills/engineering/code-review/SKILL.md).
Each is a **judgement call**, labelled as a heuristic ("possible Feature Envy"),
never CONFIRMED on its own. Any rule above wins where it endorses what a smell
would flag: a `HookSource` handler reaching into its context is the design, not
Feature Envy, and a pooled context's many fields are not a Data Clump.

- **Mysterious Name** — the name does not say what it holds. Rename it.
- **Duplicated Code** — one logic shape in two hunks or files. Extract it.
- **Feature Envy** — a method using another object's data more than its own.
  Move it.
- **Data Clumps** — the same few values keep travelling together. Give them a
  type.
- **Primitive Obsession** — a string or int standing in for a domain concept
  `rules/` should name.
- **Repeated Switches** — the same `if`-cascade on one type in several places.
- **Shotgun Surgery** — one logical change scattered across many files.
- **Divergent Change** — one file edited for several unrelated reasons.
- **Speculative Generality** — a parameter, hook or abstraction the task row
  does not ask for. YAGNI (`CLAUDE.md` § Working rules).
- **Message Chains** — `a.b().c().d()` the caller should not depend on.
- **Middle Man** — a class that mostly delegates.
- **Refused Bequest** — a subclass that ignores most of what it inherits.

## Scope note

Sections referencing `features/`, `spells/` and the golden harness describe
rules that only start biting once that code exists (W3–W4). Until then they are
the standard a new file is held to, not a claim that the tree contains one. The
dedicated feature-review harness is `w3k`; this checklist is its seed and should
be folded into it rather than maintained twice.
