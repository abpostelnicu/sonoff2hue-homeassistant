"""Every eWeLink device seen on the LAN, as the LAN monitor's broadcasts show it.

A port of the .NET app's ``Web/Monitoring/DeviceRegistry.cs``. An injected clock replaces .NET's ``TimeProvider``, and
the ``Changed`` and ``ListeningChanged`` events are callbacks.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import cmp_to_key

from . import text
from .press import SwitchEventKind, TrackerUpdate, summarize

ONLINE_WINDOW = timedelta(minutes=5)
"""A device counts as online when it was heard within this window (the LAN monitor queries every 2 min)."""

_ORDER = cmp_to_key(text.compare_ignore_case)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LanDevice:
    """What is known about one eWeLink device from its LAN broadcasts."""

    device_id: str
    address: str | None
    device_type: str | None
    encrypted: bool
    seq: int | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_event: SwitchEventKind | None
    last_event_at: datetime | None
    last_press_at: datetime | None
    detached: bool | None
    summary: str | None
    decode_error: str | None


class DeviceRegistry:
    """Every eWeLink device seen on the LAN, updated from :class:`SwitchTracker` results."""

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock
        self._devices: dict[str, LanDevice] = {}  # by text.fold(device_id), as .NET's OrdinalIgnoreCase dictionary
        self._listening = False
        self._last_packet_at: datetime | None = None
        self._changed: list[Callable[[LanDevice], None]] = []
        self._listening_changed: list[Callable[[], None]] = []

    def add_changed_callback(self, callback: Callable[[LanDevice], None]) -> Callable[[], None]:
        """Calls ``callback`` for each broadcast that is not a repeat, and for the first broadcast of a device; returns
        a function that removes it."""
        self._changed.append(callback)
        return lambda: self._changed.remove(callback)

    def add_listening_changed_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Calls ``callback`` when :attr:`listening` changes; returns a function that removes it."""
        self._listening_changed.append(callback)
        return lambda: self._listening_changed.remove(callback)

    @property
    def listening(self) -> bool:
        return self._listening

    @listening.setter
    def listening(self, value: bool) -> None:
        if self._listening == value:
            return
        self._listening = value
        for callback in list(self._listening_changed):
            callback()

    @property
    def last_packet_at(self) -> datetime | None:
        return self._last_packet_at

    def is_online(self, device: LanDevice) -> bool:
        return self._clock() - device.last_seen_at <= ONLINE_WINDOW

    def snapshot(self) -> list[LanDevice]:
        """The devices in the order of their ids, as ``StringComparer.OrdinalIgnoreCase`` sorts them."""
        return sorted(self._devices.values(), key=lambda device: _ORDER(device.device_id))

    def find(self, device_id: str) -> LanDevice | None:
        return self._devices.get(text.fold(device_id))

    def apply(self, update: TrackerUpdate, received_at: datetime) -> None:
        changed: list[LanDevice] = []
        self._last_packet_at = received_at
        for tracked in update.events:
            announcement = tracked.announcement
            kind = tracked.event.kind
            key = text.fold(announcement.device_id)
            previous = self._devices.get(key)
            # What was known before: for a new device, only that it was first seen now.
            known = previous if previous is not None else _first_seen(announcement.device_id, announcement.received_at)
            device = LanDevice(
                device_id=announcement.device_id,
                # Always IPv4: an A record, or the sender on the IPv4 socket.
                address=_or(None if announcement.address is None else str(announcement.address), known.address),
                device_type=_or(announcement.device_type, known.device_type),
                encrypted=announcement.encrypted,
                seq=_or(announcement.seq, known.seq),
                first_seen_at=known.first_seen_at,
                last_seen_at=announcement.received_at,
                last_event=known.last_event,
                last_event_at=known.last_event_at,
                last_press_at=known.last_press_at,
                detached=_or(tracked.is_detached, known.detached),
                summary=known.summary if tracked.params is None else summarize(tracked.params),
                decode_error=tracked.decode_error,
            )

            if kind != SwitchEventKind.DUPLICATE:
                device = replace(
                    device,
                    last_event=kind,
                    last_event_at=announcement.received_at,
                    last_press_at=announcement.received_at if kind == SwitchEventKind.PRESS else device.last_press_at,
                )

            self._devices[key] = device
            if kind != SwitchEventKind.DUPLICATE or previous is None:
                changed.append(device)

        for device in changed:
            for callback in list(self._changed):
                callback(device)


def _first_seen(device_id: str, at: datetime) -> LanDevice:
    return LanDevice(device_id, None, None, False, None, at, at, None, None, None, None, None, None)


def _or[T](value: T | None, fallback: T | None) -> T | None:
    """``value ?? fallback``."""
    return fallback if value is None else value
