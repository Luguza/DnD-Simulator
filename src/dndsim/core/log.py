"""The structured event log: records as tuples, formatting left to the reporter.

A record is a plain tuple whose first element is its kind, written by the code
that knows what happened and read by whatever renders it — `trace`, the
`SimResult` breakdowns, a golden. Nothing here formats a string, because a
record is written on the hot path and read, if at all, long after
(DESIGN.md § Levers). A record holds names and values, never a live object,
since a creature goes on changing after the record that mentions it.

Two levels, the CLI's `--log-level=summary|full` (DESIGN.md § CLI surface).
`summary` keeps what an event *came to* and skips the per-roll records under
it. The writer asks `log.full` before it builds a per-roll record, so a
skipped record costs a slot read and not a tuple (`docs/decisions.md` § w1i).
"""

from collections.abc import Callable, Iterator
from enum import Enum
from typing import TypeAlias

__all__ = ["EventLog", "LogLevel", "Record"]

Record: TypeAlias = tuple[object, ...]
"""One record: `(kind, *fields)`, with `kind` a lowercase string."""


class LogLevel(Enum):
    """How much an `EventLog` keeps. Values are the CLI's spellings."""

    SUMMARY = "summary"
    FULL = "full"


class EventLog:
    """An append-only sequence of records, in the order they were written.

    `write` is the list's own `append`, bound once, so a write is one C call
    rather than a Python frame. `full` is `level is LogLevel.FULL`, kept as a
    plain slot because a writer reads it on every attack.
    """

    __slots__ = ("_records", "full", "level", "write")

    level: LogLevel
    full: bool
    write: Callable[[Record], None]

    def __init__(self, level: LogLevel = LogLevel.FULL) -> None:
        self.level = level
        self.full = level is LogLevel.FULL
        self._records: list[Record] = []
        self.write = self._records.append

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self._records)

    @property
    def records(self) -> tuple[Record, ...]:
        """Every record so far, as a snapshot later writes do not reach."""
        return tuple(self._records)

    def clear(self) -> None:
        """Drop every record, keeping the level."""
        self._records.clear()
