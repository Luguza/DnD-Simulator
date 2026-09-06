"""Typer CLI — a thin adapter over `api.py`. Built in W2 (roadmap task w2h).

Reserved now so the `dndsim` entry point resolves to a clear message instead of
an import error.
"""

import sys


def main() -> int:
    print(
        "dndsim: the CLI is not implemented yet (roadmap task w2h).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
