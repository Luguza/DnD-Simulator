# Vendored skill

Source: https://github.com/wdm0006/python-skills (`skills/python/performance/`)
Licence: MIT — see [LICENSE](LICENSE), © 2025 Will McGinnis.
Vendored: 2026-09-05

Copied as a single folder rather than installed via
`/plugin install python-library-quality@dev-skills`, because that bundle pulls in
eight other skills this repo has no use for.

## Why this one

`w0h` (performance spike) gates milestone **M0**, and DESIGN.md sets a hard
budget of ≥50k attack resolutions/sec/core plus a CI gate that fails on a >10%
sims/sec regression. `BENCHMARKS.md` covers exactly that pytest-benchmark
baseline-and-compare workflow; `PROFILING.md` covers the tooling for finding the
bottleneck when the spike misses budget.

Not yet installed: pyinstrument, memray, pytest-benchmark. Add them to the `dev`
dependency group when `w0h` starts.
