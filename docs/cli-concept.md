# CLI concept — `dndsim` on rich-click

**Status: a concept for `w2h`, not yet a contract.** `DESIGN.md` § CLI surface
fixes *which* commands exist and what they mean; that is settled and this
document does not reopen it. What is not settled anywhere is *how* the surface
is presented — how help is grouped, which stream carries what, how a refusal
looks, and which exit code a caller reads. This is the answer to those, derived
against `DESIGN.md`'s own constraints rather than against CLI fashion, and
prototyped rather than sketched: every rendering below was produced by running
the prototype, and every claim marked *(measured)* was checked at **rich-click
1.9.9 / click 8.5.0 / typer 0.27.2 / Python 3.11**.

§ 11 lists what adopting it changes elsewhere. The two open questions are in
§ 12 — the first of them is whether to adopt it at all, because it replaces a
library named in `DESIGN.md` § Libraries before reimplementing.

---

## 1. rich-click rather than Typer

`DESIGN.md` says `typer` + `rich`. Typer's pitch is that a type-annotated
signature *is* the parameter declaration; its rich output is a by-product.
rich-click's pitch is the inverse: ordinary click, with help, error and usage
rendering treated as a configurable surface. For this CLI the second is the
better trade, on four differences that are about this project rather than about
taste.

| | Typer 0.27 | rich-click 1.9 |
|---|---|---|
| Group an **argument with its options** | No — arguments get their own panel | **Yes** — `BUILD` and `--dummy` share a *Target* panel *(measured)* |
| Panel order | Custom panels render after the default ones | `default_panels_first=False` puts the meaningful ones first *(measured)* |
| One config for the whole tree | Per-parameter kwargs only | `RichHelpConfiguration` on the group, **inherited by every subcommand** *(measured)* |
| Pinned help width | Follows `COLUMNS` | `width=88` pins it — help is byte-stable, so it can be a golden *(measured)* |
| Error panel text, suggestion line, epilogue | Fixed | Configurable (`errors_suggestion`, `errors_epilogue`) |
| Render the tree to `text` / `html` / `svg` | No | `rich-click --output text dndsim.cli:cli` — generated docs *(measured)* |
| Type-hint-driven signatures | **Yes** | No — explicit decorators |
| `--install-completion` out of the box | **Yes** | No (click has completion, without the installer command) |

The first four carry the decision. Panels that can mix an argument with the
options qualifying it are the difference between a help screen organised by *the
question being asked* and one organised by *click's parameter kinds*; a pinned
width is the difference between help output that can be a checked-in golden and
help output that renders differently on every terminal; and a tree that renders
itself to text is the difference between `docs/cli.md` being generated — as
`docs/hooks.md` and `docs/apl.md` already are — and being hand-maintained, which
this project has already decided is how docs drift.

What is given up is Typer's signature-as-declaration and its completion
installer. The first matters less here than it looks: three of the nine commands
take a value click has no type for anyway (`--dummy ac=16`,
`--sweep dummy.ac=10:22`), and those need a `ParamType` under either library
(§ 6). The second is one command's worth of work if it is ever wanted.

**The combination is not available.** rich-click ships
`rich_click.patch(patch_typer=True)` to put its renderer under a Typer app; at
the versions above it raises `TypeError: metaclass conflict` on import
*(measured)*. Patching click alone and then importing Typer runs, but Typer's
`TyperCommand` keeps Typer's own renderer *(measured)* — so the patch buys
nothing. This is a fork in the road, not a preference: pick one library.

---

## 2. The command tree is read through three panels

```
 Usage: dndsim [OPTIONS] COMMAND [ARGS]...

 A Monte Carlo D&D 5e (2024) combat simulator.
 Every run is reproducible: seed + build hash + engine version + content version replay
 it byte for byte, and every result carries all four.

╭─ Simulate ───────────────────────────────────────────────────────────────────────────╮
│ Answer a question about a build. Each writes a SimResult.                            │
│ sim      Simulate BUILD against a target dummy and report the distribution.          │
│ day      Run PARTY through an adventuring day and report attrition.                  │
│ compare  Simulate two or more BUILDS under identical conditions and diff them.       │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Examine ────────────────────────────────────────────────────────────────────────────╮
│ Ask what a build is, and why a number came out the way it did.                       │
│ inspect   Print the resolved BUILD: every feature, resource and APL entry.           │
│ validate  Check BUILDS against the schema and the APL without simulating.            │
│ trace     Replay one fight blow by blow, each line attributed to its handler.        │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Project ────────────────────────────────────────────────────────────────────────────╮
│ Keep the content set honest and the engine fast.                                     │
│ check    Lint the content set: citations, phases, no-ops, frozen boundaries.         │
│ bench    Measure the hot path and assert the M0 budget.                              │
│ content  Fetch and inspect the gitignored 5etools dataset.                           │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────────────╮
│ --version  -V  Show the version and exit.                                            │
│ --help     -h  Show this message and exit.                                           │
╰──────────────────────────────────────────────────────────────────────────────────────╯
```

The grouping is the one `DESIGN.md` § CLI surface already argues for in prose —
`inspect`, `trace` and `validate` exist *because of* decisions made elsewhere in
that document, and they are a different kind of thing from `sim` and `day`. A
nine-command alphabetical list hides that; three panels with a sentence each put
a new reader in the right third immediately. Panel help text is the place to say
what the panel is *for*, which is the one thing a flat command list cannot
express.

`content` is a group (`fetch`, `status`) rather than a bare command, because the
pinned upstream commit is something you ask about at least as often as you
refetch — every `SimResult` cites it.

---

## 3. There are no global options — there are three shared option sets

`DESIGN.md` calls `--seed`, `--log-level` and `--jobs` "global options". Taken
literally in click, a group-level option must be typed *before* the subcommand:

```sh
dndsim --seed 42 sim builds/fighter.yaml     # what group-level options force
dndsim sim builds/fighter.yaml --seed 42     # what everyone actually types
```

So they are declared as **reusable decorators applied per command**, not on the
group. This also lets each command take only the ones that mean something: a
seed on `dndsim check` is noise, and `-n/--iterations` on `trace` is a
contradiction — a trace is one fight.

| Set | Options | Applied to |
|---|---|---|
| Sampling | `-n/--iterations`, `--seed`, `-j/--jobs` | `sim`, `day`, `compare` |
| Output | `-f/--format`, `-o/--output`, `--log-level` | every reporting command |
| Diagnostics | `--color`, `--traceback` | every reporting command |

`--seed` and `--jobs` also read `DNDSIM_SEED` and `DNDSIM_JOBS`, so CI pins them
once instead of threading them through every invocation.

```
 Usage: dndsim sim [OPTIONS] BUILD

 Simulate BUILD against a target dummy and report the distribution.
 The single-encounter question: damage per round, hit and crit rates, actions and
 attack resolutions per round.

╭─ Target ─────────────────────────────────────────────────────────────────────────────╮
│ *  BUILD    FILE       Path to the build YAML.                                       │
│                        [required]                                                    │
│    --dummy  KEY=VALUE  Target dummy knobs, repeatable: --dummy ac=16 --dummy hp=200. │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Sampling ───────────────────────────────────────────────────────────────────────────╮
│ --iterations  -n  INTEGER RANGE [x>=1]  Monte Carlo iterations.                      │
│                                         [default: 10000]                             │
│ --seed            INTEGER               Master seed. Omitted, one is drawn and       │
│                                         reported.                                    │
│ --jobs        -j  INTEGER RANGE [x>=1]  Worker processes. Results are seed-identical │
│                                         at any value.                                │
│                                         [default: 1]                                 │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Output ─────────────────────────────────────────────────────────────────────────────╮
│ --format     -f  [text|json]     json is the machine contract; text is rendered from │
│                                  it.                                                 │
│                                  [default: text]                                     │
│ --output     -o  FILE            Write the result here instead of stdout.            │
│ --log-level      [summary|full]  full records every roll; summary skips per-roll     │
│                                  records.                                            │
│                                  [default: summary]                                  │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Diagnostics ────────────────────────────────────────────────────────────────────────╮
│ --color      [auto|always|never]  auto colours a terminal and nothing else. NO_COLOR │
│                                   wins over all three.                               │
│                                   [default: auto]                                    │
│ --traceback                       Show the traceback for errors the CLI would        │
│                                   otherwise summarise.                               │
╰──────────────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────────────╮
│ --help  -h  Show this message and exit.                                              │
╰──────────────────────────────────────────────────────────────────────────────────────╯
```

Every choice list on that screen is built from an enum that already exists —
`click.Choice([m.value for m in LogLevel])` — so a new log level cannot appear in
the engine and be missing from the help. The same single-source rule as the
generated docs, one level down.

**`--jobs` earns its help string.** "Results are seed-identical at any value" is
the one sentence that stops a user believing a parallel run is an approximation,
and `DESIGN.md` § What is a file is explicit that this holds only while seeding
stays per-iteration and merging stays in iteration order.

---

## 4. Two streams, one contract

| Stream | Carries |
|---|---|
| **stdout** | the payload, and nothing else: `SimResult` JSON, or the text rendered from it |
| **stderr** | help, usage, errors, warnings, progress, the `-o` confirmation |

`dndsim sim … --format json | jq` has to work with no extra flags, and a
progress bar that lands in the pipe breaks it. rich's `Console(stderr=True)` is
the whole implementation; the rule is that `cli.py` constructs exactly one stdout
writer and one stderr console, and nothing else prints.

Progress is a rich `Progress` on stderr, `transient=True`, and **disabled when
stderr is not a terminal** — a CI log does not want 10,000 redraws. It never
touches the RNG or the merge order, so it cannot affect reproducibility; the only
discipline it imposes is that the runner can report completed iterations without
reordering them, which `w2g` already owes for a different reason.

With `-o result.json`, stdout stays empty and a one-line confirmation goes to
stderr: `dndsim sim … -o a.json > log.txt` should not produce two copies.

---

## 5. The error hierarchy decides the exit code *and* whether you see a traceback

`core/errors.py` already splits the world correctly; the CLI only has to respect
the split. Everything under `DndSimError` today — `PhaseError`,
`FeatureContractError`, `FeatureDeclarationError`, `EngineAssemblyError` —
describes **a bug in code**, either the engine's or a feature author's. None of
them means "your YAML is wrong". A traceback is the *right* output for all four,
and folding them into a tidy red panel would hide the one thing § Failing loudly
exists to show.

| Situation | Rendering | Exit |
|---|---|---|
| Success | payload on stdout | 0 |
| Bad flags, bad option value, unknown command | rich-click error panel | 2 |
| Input refused at load time (`BuildError`, `ContentError`) | error panel, title names the file | 3 |
| Inputs fine, the command's own check failed — `check` findings, `bench` under budget, `validate` rejecting a build | the command's report, then a one-line verdict | 4 |
| Any other `DndSimError`, or an unexpected exception | **full traceback** | 1 |

**This surfaces a gap in the current hierarchy, and it is a real one.** There is
no `DndSimError` subclass meaning "the file the user wrote is wrong" — every
existing member means "the code is wrong". `w2f` (build schema, unknown feature
id with difflib suggestions) and `w2a` (content fetch and load) are the tasks
that will need to raise it, so the branch belongs with them:

```python
class InputError(DndSimError):
    """A file the user wrote was refused. Not a bug — a rejection."""


class BuildError(InputError): ...      # w2f: schema, APL, unknown feature id


class ContentError(InputError): ...    # w2a: missing or stale data/
```

The CLI then catches `InputError` and **nothing else**. That is a one-line rule a
reviewer can check, and it keeps the table above true by construction rather than
by a growing `except` tuple.

```
╭─ Build rejected ─────────────────────────────────────────────────────────────────────╮
│ builds/fighter.yaml: unknown feature id 'fighter.action_surg'.                       │
│ Did you mean 'fighter.action_surge'?                                                 │
╰──────────────────────────────────────────────────────────────────────────────────────╯
```

Exit 3. `--traceback` (or `DNDSIM_TRACEBACK=1`) prints the stack for these too,
which is what a bug report against the loader needs.

**One mechanical trap, measured.** The obvious way to wrap a click app —
`cli.main(standalone_mode=False)` inside a `try` — **loses rich-click's error
rendering**: usage errors come back as plain `Error: …` text with the configured
suggestion and epilogue dropped *(measured)*. Standalone mode has to stay on, and
the domain handler goes in a `RichGroup` subclass instead:

```python
class DndsimGroup(click.RichGroup):
    def main(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().main(*args, **kwargs)
        except InputError as exc:
            _render_rejection(exc)          # rich panel on stderr
            sys.exit(Exit.REJECTED)
```

---

## 6. Free-form values get a `ParamType`, not a regex in the command body

`--dummy ac=16` and `--sweep dummy.ac=10:22` are the two places a user types
structure into a flag. Both are parsed by a `click.ParamType`, which puts the
refusal at the parameter — where click already knows the option name, the
position and how to render it — instead of twenty lines into the command body:

```python
class KeyValue(click.ParamType):
    """`ac=16` — a key from a declared schema, not an arbitrary pair."""

    name = "KEY=VALUE"

    def convert(self, value, param, ctx):
        key, sep, raw = value.partition("=")
        if not sep:
            self.fail(f"{value!r} is not KEY=VALUE.", param, ctx)
        if key not in DUMMY_KEYS:
            near = difflib.get_close_matches(key, DUMMY_KEYS, n=1)
            hint = f" Did you mean {near[0]!r}?" if near else ""
            self.fail(f"unknown dummy key {key!r}.{hint}", param, ctx)
        return (key, raw)
```

```
 Usage: dndsim sim [OPTIONS] BUILD

 Try dndsim COMMAND --help for the options it takes.
╭─ Error ──────────────────────────────────────────────────────────────────────────────╮
│ Invalid value for '--dummy': unknown dummy key 'hpp'. Did you mean 'hp'?             │
╰──────────────────────────────────────────────────────────────────────────────────────╯

 Surface and semantics: DESIGN.md § CLI surface.
```

The difflib suggestion is the same courtesy `w2f` owes for an unknown feature id,
and the same one click already gives for an unknown subcommand (`No such command
'simm'. Did you mean 'sim'?` *(measured)*) — three refusals, one shape.

`DUMMY_KEYS` and the `--sweep` namespace paths must come from the schemas that
own them (the target dummy, the APL namespace), never a literal in `cli.py`. A
CLI that accepts a key the engine has never heard of is the anti-corruption
failure wearing a different hat.

---

## 7. Colour, width, and two gotchas

One `RichHelpConfiguration` on the group, inherited by every subcommand
*(measured)*:

```python
HELP = RichHelpConfiguration(
    width=88, max_width=88,          # = the ruff line length, and golden-stable
    show_arguments=True,
    group_arguments_options=False,   # arguments join the panel they belong to
    default_panels_first=False,      # …and that panel comes first
    commands_before_options=True,
    text_markup="markdown",
    errors_suggestion="Try [bold]dndsim COMMAND --help[/] for the options it takes.",
    errors_epilogue="Surface and semantics: DESIGN.md § CLI surface.",
)
```

- **Width is pinned at 88, not taken from `COLUMNS`** *(measured: identical
  output at `COLUMNS=40` and `COLUMNS=200`)*. That is what makes § 8's help
  goldens possible, and 88 is already the line length the repo formats to.
- **Piped output is uncoloured automatically** *(measured: zero ANSI escapes when
  the stream is not a terminal)*. No flag needed for the common case.
- **`force_terminal=True` overrides `NO_COLOR`** *(measured)*. So `--color
  always` must not be implemented as a bare `force_terminal=True`; the precedence
  is **`NO_COLOR` → `--color` → TTY detection**, with `NO_COLOR` winning
  outright, and the CLI checks the environment itself before setting the flag.
  This is the kind of thing that gets fixed in a terminal and never written down,
  so it is written down here.

---

## 8. Help text is generated documentation, and a golden

`docs/hooks.md` and `docs/apl.md` are generated from the registries because
hand-maintained docs drift. The command tree is a registry too, and rich-click
renders it without a bespoke walker:

```sh
rich-click --output text dndsim.cli:cli -- --help     # docs/cli.md
rich-click --output html dndsim.cli:cli -- --help     # if docs ever go to HTML
```

*(measured for both.)* `w3c` owns doc generation and gains this target; the
stale-docs CI check then covers the CLI at no extra cost.

Because the width is pinned, help output is also a **contract test**:

```python
def test_sim_help_matches_golden() -> None:
    result = CliRunner().invoke(cli, ["sim", "--help"])
    assert result.exit_code == 0
    assert result.output == SIM_HELP_GOLDEN.read_text()
```

*(measured: `CliRunner` captures the rendered panels, longest line exactly 88
characters, independent of the environment.)* This is worth having for one
specific reason — `DESIGN.md` § Verification lists nine invocations as the manual
sanity check of the whole project, and a golden per command is how those nine
stop being prose.

---

## 9. Determinism is visible, not just true

Every run reports the four fields that reproduce it — seed, build hash, engine
version, content version — because a result without them cannot be compared to
one from three months ago. Under `--format json` they are `SimResult` fields and
already settled by `w2g`. In text they are a footer, and the footer carries the
command that replays the run:

```
 seed 4815162342 · build 9f3c1a · engine 0.1.0 · content 5etools@a3f91c2

 Replay:  dndsim trace builds/fighter_gwm_l5.yaml --seed 4815162342 --dummy ac=16
```

The replay line is this concept's one piece of genuine ergonomics: `trace` is how
a wrong number gets chased to the feature responsible, and the moment you want it
is the moment you are looking at the wrong number. Printing the exact command
removes the step where a user retypes the seed and gets a different fight.

When `--seed` is omitted, the drawn seed is reported in that same footer, so no
run is unreproducible after the fact.

---

## 10. What keeps `cli.py` thin

"No simulation logic in `cli.py`" is the rule; on its own it is unfalsifiable.
Two mechanical versions of it that a reviewer can check:

- **An import allowlist.** `cli.py` may import `dndsim.api`, `dndsim.reporting`,
  and enums from `dndsim.rules` — nothing else. This is one more rule in `dndsim
  check` (`w3b`), which already bans `import random` outside `core/rng.py` for
  the same structural reason. An import of `dndsim.engine` in `cli.py` is then a
  linter failure rather than a review argument.
- **rich-click renders the CLI; `reporting/` renders results.** rich-click's
  config governs help, usage and errors — the CLI's own chrome. A `SimResult` is
  rendered by `reporting/render_text.py`, from the same JSON `render_json.py`
  emits, and `cli.py` only decides where the bytes go. If a table of damage
  numbers is ever built inside `cli.py`, text and JSON have acquired two sources
  of truth, which is exactly what § What is a file forbids.

---

## 11. What adopting this changes

| Where | Change |
|---|---|
| `DESIGN.md` § Libraries before reimplementing | `typer` + `rich` → **`rich-click`** (which depends on `click` + `rich`) |
| `DESIGN.md` § Module layout | `cli.py` comment: "thin Typer adapter" → "thin rich-click adapter" |
| `DESIGN.md` § CLI surface | Add `content status`; note that `--seed`/`--log-level`/`--jobs` are shared option sets, not group-level options (§ 3) |
| `ROADMAP.md` `w2h` | "Typer CLI" → "rich-click CLI"; acceptance gains the help goldens |
| `ROADMAP.md` `w2f` / `w2a` | Add `InputError` → `BuildError` / `ContentError` (§ 5) — the CLI's exit-code contract depends on it |
| `ROADMAP.md` `w3b` | One linter rule: `cli.py`'s import allowlist (§ 10) |
| `ROADMAP.md` `w3c` | Generate `docs/cli.md` via `rich-click --output text` (§ 8) |
| `pyproject.toml` | `rich-click>=1.9` (panel decorators and config inheritance are 1.9 API), with `uv lock` in the same commit |
| `tests/contract/` | One help golden per command |

Nothing here touches `hooks/`, `engine/` or `core/`, and nothing here is
load-bearing before `w2g` exists to be adapted.

---

## 12. Open questions

1. **Does rich-click replace Typer, or is this concept re-cut for Typer?** § 1
   says they cannot be combined at current versions. Re-cutting for Typer costs
   the argument-in-panel grouping, the pinned width (and with it the help
   goldens), and the generated `docs/cli.md`; it keeps the library `DESIGN.md`
   already names. The recommendation is rich-click, and the call is the user's
   because it edits a settled-decisions line.
2. **Should a rejected build and a failed check share an exit code?** Both mean
   "your content is wrong, the tool is fine". This concept separates them — 3 for
   a *refused* input, 4 for a *failed* check — so CI can tell a lint regression
   from a broken build file without parsing output. Collapsing them to one code
   is simpler and is the alternative worth naming.

---

## Evidence

The prototype behind every rendering and every *(measured)* claim is a
single-file click tree with stubbed command bodies — the full nine-command
surface, the three option sets, both `ParamType`s and the `RichGroup.main`
wrapper. It is deliberately **not** committed: `cli.py` is `w2h`'s to write, it
must be an adapter over an `api.py` that does not exist yet, and a second CLI in
the tree would be exactly the speculative code the design's file-size and freeze
rules exist to prevent. Everything from it that matters is quoted above.
