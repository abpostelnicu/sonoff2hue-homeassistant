"""The integration's recent activity: its latest log entries, which the diagnostics show.

:class:`ActivityBuffer` is a ``logging.Handler`` that keeps the newest entries of the loggers it is added to, at the
levels those loggers let through (decision 23). An entry has the exception's type and message only; the traceback stays
in Home Assistant's log.
"""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import itertools
import logging
import threading

CAPACITY = 1000
"""How many entries the buffer keeps."""


class LogLevel(StrEnum):
    """An entry's level. A record's level maps to the highest of these at or below it; anything below debug, such as
    level 5, is trace."""

    TRACE = "trace"
    DEBUG = "debug"
    INFORMATION = "information"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


_LEVELS = (
    (logging.CRITICAL, LogLevel.CRITICAL),
    (logging.ERROR, LogLevel.ERROR),
    (logging.WARNING, LogLevel.WARNING),
    (logging.INFO, LogLevel.INFORMATION),
    (logging.DEBUG, LogLevel.DEBUG),
)


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    id: int
    at: datetime
    level: LogLevel
    category: str
    """The logger's name after its last dot, such as ``lan_monitor``."""
    message: str
    exception: str | None
    """``Type: message`` of the exception logged with the entry."""


class ActivityBuffer(logging.Handler):
    """Keeps the newest entries of the loggers it is added to, at the levels they let through."""

    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__()
        self._sync = threading.Lock()
        self._entries: deque[ActivityEntry] = deque(maxlen=capacity)
        self._ids = itertools.count(1)

    def get(self, after: int, limit: int) -> list[ActivityEntry]:
        """The newest ``limit`` entries with an id above ``after``, oldest first; a limit of 0 gives none."""
        with self._sync:
            return list(deque((entry for entry in self._entries if entry.id > after), maxlen=limit))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            at = datetime.fromtimestamp(record.created, UTC)
            level = next((level for threshold, level in _LEVELS if record.levelno >= threshold), LogLevel.TRACE)
            message = record.getMessage()
            exception = _describe(record)
            with self._sync:
                self._entries.append(
                    ActivityEntry(next(self._ids), at, level, _category(record.name), message, exception)
                )
        except Exception:  # noqa: BLE001 - logging must never fail the code that logs
            self.handleError(record)


def _category(logger_name: str) -> str:
    """The logger's name after its last dot; the whole name when it has no dot or ends with one."""
    return logger_name.rpartition(".")[2] or logger_name


def _describe(record: logging.LogRecord) -> str | None:
    """``Type: message`` of the exception logged with ``record``, if any."""
    if record.exc_info is None or (error := record.exc_info[1]) is None:
        return None
    return f"{type(error).__name__}: {error}"
