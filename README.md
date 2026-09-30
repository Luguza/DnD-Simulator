# D&D Combat Simulator

See [DESIGN.md](DESIGN.md) for the architecture and [ROADMAP.md](ROADMAP.md) for
the build order.

## Development setup

```sh
uv sync                          # create .venv with the dev toolchain
uv run pre-commit install        # install the git pre-commit hook
```

The hook runs whitespace/YAML/TOML hygiene checks, `uv lock --check`, `ruff
check --fix`, `ruff format` and `mypy` on every commit. Ruff and mypy are
invoked through `uv run --frozen`, so the versions used by the hook are exactly
the ones pinned in `uv.lock`.

Useful commands:

```sh
uv run pre-commit run --all-files   # run every hook over the whole repo
uv run pytest                       # tests
git commit --no-verify              # bypass the hook (avoid)
```

## CI and review

`.github/workflows/ci.yml` runs `uv lock --check`, `ruff check`, `ruff format
--check`, `mypy` and `pytest` on every push to `main` and every pull request —
the same lockfile-pinned tools the pre-commit hook uses. Each check runs even
after an earlier one fails, so a PR shows the whole list in one pass.

Two workflows put Claude on pull requests:

- `claude-code-review.yml` reviews every PR automatically with
  [`.claude/skills/pr-review/SKILL.md`](.claude/skills/pr-review/SKILL.md): the
  project's invariant checklist in [`CODING_STANDARDS.md`](CODING_STANDARDS.md),
  and the task's ROADMAP row.
- `claude.yml` responds to `@claude` mentions in PR and issue comments.

Both need the Claude GitHub App installed on the repo and a
`CLAUDE_CODE_OAUTH_TOKEN` repository secret. The token comes from a Claude Pro
or Max subscription — generate it with:

```sh
claude setup-token
```

then add it under *Settings → Secrets and variables → Actions → New repository
secret*. `/install-github-app` in an interactive Claude Code session does both
steps for you.

An Anthropic API key is the alternative, but it is a separate pay-as-you-go
product with its own billing — a Pro subscription does not include one. To use
that route instead, swap the input for
`anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}`.

Until a token is set the two Claude workflows fail; `ci.yml` needs no secret.
