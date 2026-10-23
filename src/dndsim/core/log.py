"""The structured event log: records as tuples, formatting left to the reporter.

A record is `(kind, *fields)`, written by the code that knows what happened.
`LogLevel.SUMMARY` skips the per-roll records a writer marks as such; the
writer asks `log.full` before building one. Why records hold names rather than
objects, and why `write` and `full` are shaped for the hot path, is in
`docs/decisions.md` § w1i.
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

    `write(record)` appends one. `full` is `level is LogLevel.FULL`. Nothing on
    a log can be assigned after construction, so the two cannot disagree.
    """

    __slots__ = ("_level", "_records", "full", "write")

    _level: LogLevel
    _records: list[Record]
    full: bool
    write: Callable[[Record], None]

    def __init__(self, level: LogLevel = LogLevel.FULL) -> None:
        records: list[Record] = []
        object.__setattr__(self, "_level", level)
        object.__setattr__(self, "_records", records)
        object.__setattr__(self, "full", level is LogLevel.FULL)
        object.__setattr__(self, "write", records.append)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"EventLog.{name} is fixed at construction")

    @property
    def level(self) -> LogLevel:
        return self._level

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
