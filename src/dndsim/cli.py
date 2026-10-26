"""The `dndsim` entry point: `main` reports that the CLI is not implemented."""

import sys


def main() -> int:
    print(
        "dndsim: the CLI is not implemented yet (roadmap task w2h).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
