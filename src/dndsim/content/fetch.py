"""`content fetch`: download the 5etools data at one pinned upstream commit.

The commit SHA is the content version every result cites (DESIGN.md § What is
a file, and what is not), so the files are read from that commit's tree, never
from a moving branch, and `VERSION` is written only once all of them are.
`docs/decisions.md` § w2a.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Final

from dndsim.content.loader import DEFAULT_DATA_DIR, VERSION_FILE, ContentError
from dndsim.content.refs import CLASS_INDEX, OPTIONAL

__all__ = ["FILES", "fetch", "main", "upstream_head"]

REPOSITORY: Final = "https://github.com/5etools-mirror-3/5etools-src.git"
RAW: Final = "https://raw.githubusercontent.com/5etools-mirror-3/5etools-src"

FILES: Final = ("items-base.json", "races.json", OPTIONAL, CLASS_INDEX)
"""The files read directly; the class files are the ones `CLASS_INDEX` names."""

Download = Callable[[str], bytes]


def upstream_head() -> str:
    """The commit SHA the upstream default branch points at now."""
    out = subprocess.run(
        ["git", "ls-remote", REPOSITORY, "HEAD"],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    ).stdout.split()
    if not out:
        raise ContentError(f"`git ls-remote {REPOSITORY} HEAD` named no commit")
    return out[0]


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        body: bytes = response.read()
        return body


def fetch(
    dest: Path = DEFAULT_DATA_DIR,
    sha: str | None = None,
    download: Download = _download,
) -> str:
    """Replace `dest` with the data at commit `sha` (default: upstream HEAD).

    Returns the SHA. Nothing at `dest` changes unless every file arrived.
    """
    sha = sha or upstream_head()
    base = f"{RAW}/{sha}/data"
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dest.parent))
    try:

        def get(path: str) -> None:
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(download(f"{base}/{path}"))

        for path in FILES:
            get(path)
        for name in json.loads((staging / CLASS_INDEX).read_bytes()).values():
            get(f"class/{name}")
        (staging / VERSION_FILE).write_text(sha + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return sha


def main() -> int:
    """Fetch into the default directory and print the pinned SHA."""
    sha = fetch(sha=sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"fetched 5etools at {sha} into {DEFAULT_DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
