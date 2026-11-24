"""The integration's recent activity: the last log entries, which Home Assistant shows in diagnostics.

A port of the .NET app's ``Web/Monitoring/LogBuffer.cs``, as a ``logging.Handler``. It keeps the most recent entries
in memory, as the .NET app did for its web interface's activity and log views. Stack traces stay in the log; an entry
has the exception's type and message only.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import logging
import threading

from .errors import UnexpectedError

CAPACITY = 1000


class LogLevel(StrEnum):
    """.NET's ``LogLevel``, as the web interface's API wrote it."""

    TRACE = "trace"
    DEBUG = "debug"
    INFORMATION = "information"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


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
        self._last_id = 0
        self._added: list[Callable[[ActivityEntry], None]] = []

    def add_added_callback(self, callback: Callable[[ActivityEntry], None]) -> Callable[[], None]:
        """Calls ``callback`` for every new entry, outside the buffer's lock and on the thread that logged it; returns a
        function that removes it. A callback that raises is reported as ``logging`` reports a failing handler."""
        self._added.append(callback)
        return lambda: self._added.remove(callback)

    def get(self, after: int, limit: int) -> list[ActivityEntry]:
        """The newest ``limit`` entries with an id above ``after``, oldest first."""
        with self._sync:
            newer = [entry for entry in self._entries if entry.id > after]
        return newer[len(newer) - limit :] if len(newer) > limit else newer

    def handle(self, record: logging.LogRecord) -> bool:
        # logging.Handler.handle holds the handler's I/O lock while it emits, which the buffer doesn't need: it has a
        # lock of its own, and the callbacks run outside it, as in .NET.
        result = self.filter(record)
        if isinstance(result, logging.LogRecord):
            record = result
        if result:
            self.emit(record)
        return bool(result)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            exception = _describe(record)
            with self._sync:
                self._last_id += 1
                entry = ActivityEntry(
                    self._last_id,
                    datetime.fromtimestamp(record.created, UTC),
                    _level(record.levelno),
                    _short_category(record.name),
                    message,
                    exception,
                )
                self._entries.append(entry)
            for callback in list(self._added):
                callback(entry)
        except Exception:  # noqa: BLE001 - logging must never fail the code that logs
            self.handleError(record)


def _level(levelno: int) -> LogLevel:
    if levelno >= logging.CRITICAL:
        return LogLevel.CRITICAL
    if levelno >= logging.ERROR:
        return LogLevel.ERROR
    if levelno >= logging.WARNING:
        return LogLevel.WARNING
    if levelno >= logging.INFO:
        return LogLevel.INFORMATION
    if levelno >= logging.DEBUG:
        return LogLevel.DEBUG
    return LogLevel.TRACE


def _short_category(category: str) -> str:
    dot = category.rfind(".")
    return category if dot < 0 or dot == len(category) - 1 else category[dot + 1 :]


def _describe(record: logging.LogRecord) -> str | None:
    """``{exception.GetType().Name}: {exception.Message}``. An :class:`UnexpectedError` stands for the .NET exception it
    names."""
    if record.exc_info is None or (error := record.exc_info[1]) is None:
        return None
    name = error.dotnet_type if isinstance(error, UnexpectedError) else type(error).__name__
    return f"{name}: {error}"
