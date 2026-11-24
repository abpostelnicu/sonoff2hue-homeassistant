"""Tests of the activity buffer; a port of the .NET app's ``LogBuffer`` test, plus its entries' format."""

from collections.abc import Iterator
from datetime import UTC, datetime
import logging
import threading

import pytest

from custom_components.sonoff2hue.engine import errors
from custom_components.sonoff2hue.engine.activity import ActivityBuffer, ActivityEntry, LogLevel
from custom_components.sonoff2hue.engine.mdns import TRACE


@pytest.fixture
def logger() -> Iterator[logging.Logger]:
    """A logger of its own whose lines go to no other handler, at every level."""
    logger = logging.getLogger("tests.engine.Sonoff2Hue.Web.Monitoring.Something")
    logger.setLevel(TRACE)
    logger.propagate = False
    yield logger
    logger.handlers.clear()


def test_keeps_the_newest_entries(logger: logging.Logger) -> None:
    buffer = ActivityBuffer(capacity=3)
    logger.addHandler(buffer)
    for number in range(1, 6):
        logger.info("entry %s", number)

    entries = buffer.get(after=0, limit=10)
    assert [entry.message for entry in entries] == ["entry 3", "entry 4", "entry 5"]
    assert entries[0].category == "Something"
    assert [entry.message for entry in buffer.get(after=entries[1].id, limit=10)] == ["entry 5"]


def test_get_returns_the_newest_entries_after_an_id_oldest_first(logger: logging.Logger) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)
    for number in range(1, 6):
        logger.warning("entry %d", number)

    assert [entry.id for entry in buffer.get(after=0, limit=2)] == [4, 5]
    assert [entry.id for entry in buffer.get(after=2, limit=10)] == [3, 4, 5]
    assert buffer.get(after=1, limit=0) == []
    assert buffer.get(after=5, limit=10) == []


def test_entries_have_dotnets_levels_and_short_categories(logger: logging.Logger) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)
    others = [logging.getLogger(name) for name in ("tests_engine_no_dot", "tests.engine.trailing.")]
    for other in others:
        other.propagate = False
        other.addHandler(buffer)

    for level in (TRACE, logging.DEBUG, logging.INFO, 25, logging.WARNING, logging.ERROR, logging.CRITICAL, 60):
        logger.log(level, "level %d", level)
    for other in others:
        other.warning("hello")
        other.removeHandler(buffer)

    entries = buffer.get(after=0, limit=100)
    assert [entry.level for entry in entries[:8]] == [
        LogLevel.TRACE,
        LogLevel.DEBUG,
        LogLevel.INFORMATION,
        LogLevel.INFORMATION,
        LogLevel.WARNING,
        LogLevel.ERROR,
        LogLevel.CRITICAL,
        LogLevel.CRITICAL,
    ]
    assert [str(entry.level) for entry in entries[:3]] == ["trace", "debug", "information"]
    assert entries[2].message == "level 20"
    assert [entry.category for entry in entries[-2:]] == ["tests_engine_no_dot", "tests.engine.trailing."]


def test_an_exception_is_its_type_and_message(logger: logging.Logger) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)

    def fail(error: Exception) -> None:
        raise error

    try:
        fail(ValueError("bad value"))
    except ValueError:
        logger.exception("first")
    try:
        fail(errors.null_reference())
    except errors.UnexpectedError:
        logger.exception("second")
    logger.error("third", exc_info=True)  # noqa: LOG014 - with no exception being handled, on purpose
    logger.warning("fourth", exc_info=OSError(1, "Operation not permitted"))

    assert [(entry.message, entry.exception) for entry in buffer.get(after=0, limit=10)] == [
        ("first", "ValueError: bad value"),
        ("second", "NullReferenceException: Object reference not set to an instance of an object."),
        ("third", None),
        ("fourth", "PermissionError: [Errno 1] Operation not permitted"),
    ]


def test_an_entry_has_the_time_it_was_logged(logger: logging.Logger) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)
    record = logger.makeRecord(logger.name, logging.INFO, __file__, 1, "at %s", ("noon",), None)
    record.created = datetime(2026, 11, 24, 12, 0, tzinfo=UTC).timestamp()

    logger.handle(record)

    (entry,) = buffer.get(after=0, limit=1)
    assert entry == ActivityEntry(
        1, datetime(2026, 11, 24, 12, 0, tzinfo=UTC), LogLevel.INFORMATION, "Something", "at noon", None
    )


def test_added_callbacks_get_every_entry_outside_the_buffers_lock(logger: logging.Logger) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)
    added: list[str] = []
    read: list[list[str]] = []
    readers: list[threading.Thread] = []
    in_time: list[bool] = []

    def reader(entry: ActivityEntry) -> None:
        # Another thread can read the buffer while the callback runs.
        thread = threading.Thread(target=lambda: read.append([e.message for e in buffer.get(after=0, limit=10)]))
        readers.append(thread)
        thread.start()
        thread.join(1)
        in_time.append(not thread.is_alive())

    remove = buffer.add_added_callback(lambda entry: added.append(entry.message))
    buffer.add_added_callback(reader)
    logger.info("one")
    remove()
    logger.info("two")
    for thread in readers:
        thread.join()

    assert added == ["one"]
    assert in_time == [True, True]
    assert read == [["one"], ["one", "two"]]


def test_a_failing_callback_never_fails_the_code_that_logs(
    logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = ActivityBuffer()
    logger.addHandler(buffer)
    failed: list[logging.LogRecord] = []
    monkeypatch.setattr(buffer, "handleError", failed.append)
    later: list[str] = []

    def failing(entry: ActivityEntry) -> None:
        raise RuntimeError("boom")

    buffer.add_added_callback(failing)
    buffer.add_added_callback(lambda entry: later.append(entry.message))
    logger.info("kept")

    assert [entry.message for entry in buffer.get(after=0, limit=10)] == ["kept"]
    assert [record.getMessage() for record in failed] == ["kept"]
    assert later == []
