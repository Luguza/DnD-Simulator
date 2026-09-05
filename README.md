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
