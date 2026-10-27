"""The event log contract: records as tuples, two levels, nothing formatted.

Sources: DESIGN.md § Levers, § CLI surface; `docs/decisions.md` § w1i; ROADMAP
w1i.
"""

import pytest

from dndsim.core.log import EventLog, LogLevel


def test_the_levels_are_spelt_as_the_cli_spells_them() -> None:
    assert [level.value for level in LogLevel] == ["summary", "full"]


def test_a_log_keeps_every_record_by_default() -> None:
    log = EventLog()

    assert log.level is LogLevel.FULL
    assert log.full


def test_a_summary_log_says_so_to_its_writers() -> None:
    assert not EventLog(LogLevel.SUMMARY).full


def test_records_come_back_in_the_order_they_were_written() -> None:
    log = EventLog()
    log.write(("first", 1))
    log.write(("second", 2))

    assert len(log) == 2
    assert list(log) == [("first", 1), ("second", 2)]
    assert log.records == (("first", 1), ("second", 2))


def test_a_snapshot_is_not_reached_by_later_writes() -> None:
    log = EventLog()
    log.write(("first",))
    snapshot = log.records
    log.write(("second",))

    assert snapshot == (("first",),)


def test_clearing_drops_the_records_and_keeps_the_level() -> None:
    log = EventLog(LogLevel.SUMMARY)
    log.write(("first",))
    log.clear()
    log.write(("second",))

    assert log.records == (("second",),)
    assert log.level is LogLevel.SUMMARY
    assert not log.full


def test_the_level_cannot_be_changed_under_a_writer() -> None:
    """Neither `level` nor `full` can be assigned (`docs/decisions.md` § w1i)."""
    log = EventLog()

    with pytest.raises(AttributeError):
        log.level = LogLevel.SUMMARY  # type: ignore[misc]
    with pytest.raises(AttributeError):
        log.full = False

    assert log.full
    assert log.level is LogLevel.FULL
