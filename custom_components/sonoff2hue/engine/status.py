"""The integration's state for its entities and diagnostics: what the bridge supervisor and the runner report, combined
with the configuration and the LAN devices.

A port of the .NET app's ``Web/Monitoring/StatusTracker.cs``, which fed the web interface's dashboard. Changes reach
the listeners as one snapshot at most every 500 ms, as .NET published its ``status`` event; Home Assistant's entities
are those listeners now. The tracker is a :class:`RunnerObserver`, which the supervisor gives the runner, and it
follows the :class:`DeviceRegistry` for the switches' devices.

Not ported: the ``EventHub`` (the listeners replace it), the config file's and the password file's paths, which belonged
to the web interface, and the version, which is the manifest's. The tracker is used on the event loop only, so .NET's
lock has no counterpart. An injected clock that returns an aware UTC datetime replaces ``TimeProvider``.

Use, for the Home Assistant runtime: make one for a configuration and its registry, add a listener with
:meth:`StatusTracker.add_listener`, pass the tracker to the bridge supervisor, read :meth:`StatusTracker.snapshot`
whenever needed, and :meth:`StatusTracker.close` it when the config entry unloads, after the supervisor stopped. A runtime
that keeps the tracker and its registry across a reload calls :meth:`StatusTracker.config_changed` instead, as .NET kept
them across configuration changes.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from . import text
from .config import EngineConfig, HueSettings
from .errors import UnexpectedError
from .registry import DeviceRegistry, LanDevice
from .runner import RunnerObserver, SwitchBinding
from .toggler import ToggleResult

PUBLISH_DELAY = 0.5
"""Seconds that changes are collected before the listeners get a snapshot with all of them."""


class ServiceState(StrEnum):
    """The state of the bridge, in the order of .NET's ``ServiceState``."""

    NOT_CONFIGURED = "not_configured"
    """The Hue bridge is not paired or no switch is mapped yet."""
    INVALID_CONFIG = "invalid_config"
    """The configuration has errors."""
    CONNECTING = "connecting"
    """Waiting for the Hue bridge."""
    RUNNING = "running"
    ERROR = "error"
    """The bridge refused sonoff2hue (for example, the application key was revoked), or something unexpected failed."""


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    state: ServiceState
    message: str | None
    problems: tuple[str, ...]
    since: datetime
    """When the state, the message or the problems last changed."""


@dataclass(frozen=True, slots=True)
class HueStatus:
    paired: bool
    """Both the bridge's address and the application key are set."""
    bridge_ip: str | None
    bridge_id: str | None
    certificate_pinned: bool
    reachable: bool | None
    """Whether the bridge was usable when last checked; ``None`` until then, and while it isn't paired."""
    message: str | None
    """Why the bridge isn't usable."""
    checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class LanStatus:
    listening: bool
    devices_seen: int
    last_packet_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToggleAction:
    """The last toggle of a switch's targets."""

    at: datetime
    turned_on: bool
    changed: int
    unchanged: int
    failed: int
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class SwitchError:
    """The last thing that went wrong for a switch (``SwitchErrorDto``); not an exception."""

    at: datetime
    message: str


@dataclass(frozen=True, slots=True)
class SwitchStatus:
    """A configured switch, with what the LAN and the runner said about it."""

    name: str | None
    device_id: str
    online: bool
    address: str | None
    last_seen_at: datetime | None
    last_press_at: datetime | None
    detached: bool | None
    decode_error: str | None
    targets: tuple[str, ...] | None
    """The resolved targets, such as ``light 'Ceiling'``, while the bridge is running."""
    last_action: ToggleAction | None
    last_error: SwitchError | None


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    started_at: datetime
    """When the tracker was made: the config entry's setup."""
    service: ServiceStatus
    hue: HueStatus
    lan: LanStatus
    switches: tuple[SwitchStatus, ...]
    """In the order of the configuration."""


type StatusListener = Callable[[StatusSnapshot], None]


def _now() -> datetime:
    return datetime.now(UTC)


class StatusTracker(RunnerObserver):
    """The bridge's state, combined with the configuration and the LAN devices. The supervisor sets the service's state
    and the bridge's reachability; as the runner's observer, it records each switch's last action and error."""

    def __init__(self, config: EngineConfig, registry: DeviceRegistry, clock: Callable[[], datetime] = _now) -> None:
        self._config = config
        self._registry = registry
        self._clock = clock
        self._started_at = clock()
        self._service = ServiceStatus(ServiceState.CONNECTING, "Starting.", (), self._started_at)
        self._bridge = _bridge_identity(config.hue)
        # By text.fold(device id), as .NET's OrdinalIgnoreCase dictionaries.
        self._targets: dict[str, tuple[str, ...]] = {}
        self._last_actions: dict[str, ToggleAction] = {}
        self._last_errors: dict[str, SwitchError] = {}
        self._reachable: bool | None = None
        self._reachability_message: str | None = None
        self._checked_at: datetime | None = None
        self._listeners: list[StatusListener] = []
        self._publish: asyncio.TimerHandle | None = None
        self._closed = False
        self._unsubscribe = (
            registry.add_changed_callback(self._on_device_changed),
            registry.add_listening_changed_callback(self._schedule_publish),
        )

    @property
    def service(self) -> ServiceStatus:
        return self._service

    def add_listener(self, listener: StatusListener) -> Callable[[], None]:
        """Calls ``listener`` with a snapshot 500 ms after a change, with every change made until then; returns a
        function that removes it. Listeners run in order on the event loop, and one that raises stops the rest and goes
        to the loop's exception handler. A device that isn't a configured switch, which only changes ``devices_seen``,
        is published only with the next other change, as in .NET: follow the registry for every device."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def set_service(self, state: ServiceState, message: str | None, problems: Sequence[str] | None = None) -> None:
        """Sets the service's state; ``since`` moves only when the state, the message or the problems change. Any state
        but running forgets the resolved targets."""
        problems = () if problems is None else tuple(problems)
        service = self._service
        if service.state == state and service.message == message and service.problems == problems:
            return

        self._service = ServiceStatus(state, message, problems, self._clock())
        if state != ServiceState.RUNNING:
            self._targets = {}
        self._schedule_publish()

    def set_running(self, bindings: Sequence[SwitchBinding]) -> None:
        """The runner started with these resolved switches."""
        targets: dict[str, tuple[str, ...]] = {}
        for binding in bindings:
            key = text.fold(binding.settings.normalized_device_id)
            if key in targets:
                raise UnexpectedError(
                    "ArgumentException",
                    f"An item with the same key has already been added. Key: {binding.settings.normalized_device_id}",
                )
            targets[key] = tuple(str(target) for target in binding.targets)
        self._targets = targets

        count = sum(len(binding.targets) for binding in bindings)
        self.set_service(
            ServiceState.RUNNING,
            f"{len(bindings)} wall {'switch toggles' if len(bindings) == 1 else 'switches toggle'} {count} Hue "
            f"{'light, room or zone' if count == 1 else 'lights, rooms and zones'}.",
        )

    def set_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        """Records a check of the bridge; the message is kept only while it isn't reachable."""
        self._reachable = reachable
        self._reachability_message = None if reachable else message
        self._checked_at = self._clock()
        self._schedule_publish()

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        now = self._clock()
        key = text.fold(binding.settings.normalized_device_id)
        self._last_actions[key] = ToggleAction(
            now, result.turned_on, result.changed, result.unchanged, result.failed, elapsed // timedelta(milliseconds=1)
        )
        if result.failed > 0:
            self._last_errors[key] = SwitchError(
                now, f"{result.failed} of {len(binding.targets)} lights could not be switched."
            )
        self._schedule_publish()

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self._record_error(binding, message)

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self._record_error(binding, "A press was dropped because it waited more than 5 seconds for the Hue bridge.")

    def on_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        self.set_bridge_reachability(reachable, message)

    def snapshot(self) -> StatusSnapshot:
        config = self._config
        devices = self._registry.snapshot()
        hue = config.hue
        paired = not text.is_null_or_white_space(hue.bridge_ip) and not text.is_null_or_white_space(hue.app_key)
        switches: list[SwitchStatus] = []
        for switch in config.switches:
            device_id = switch.normalized_device_id
            key = text.fold(device_id)
            device = next((d for d in devices if text.equals_ignore_case(d.device_id, device_id)), None)
            switches.append(
                SwitchStatus(
                    switch.name,
                    device_id,
                    device is not None and self._registry.is_online(device),
                    None if device is None else device.address,
                    None if device is None else device.last_seen_at,
                    None if device is None else device.last_press_at,
                    None if device is None else device.detached,
                    None if device is None else device.decode_error,
                    self._targets.get(key),
                    self._last_actions.get(key),
                    self._last_errors.get(key),
                )
            )

        return StatusSnapshot(
            self._started_at,
            self._service,
            HueStatus(
                paired,
                hue.bridge_ip,
                hue.bridge_id,
                not text.is_null_or_white_space(hue.certificate_sha256),
                self._reachable if paired else None,
                self._reachability_message if paired else None,
                self._checked_at if paired else None,
            ),
            LanStatus(self._registry.listening, len(devices), self._registry.last_packet_at),
            tuple(switches),
        )

    def config_changed(self, config: EngineConfig) -> None:
        """``OnConfigChanged``, for a runtime that keeps the tracker across a reload of the config entry: snapshots show
        the new configuration. Reachability belongs to one bridge: editing switches keeps it, pairing again or another
        bridge resets it."""
        self._config = config
        bridge = _bridge_identity(config.hue)
        if bridge != self._bridge:
            self._bridge = bridge
            self._reachable = None
            self._reachability_message = None
            self._checked_at = None
        self._schedule_publish()

    def close(self) -> None:
        """Stops following the registry and drops a pending snapshot; nothing is published afterwards. Closing again
        does nothing."""
        if self._closed:
            return
        self._closed = True
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._publish is not None:
            self._publish.cancel()
            self._publish = None

    def _record_error(self, binding: SwitchBinding, message: str) -> None:
        self._last_errors[text.fold(binding.settings.normalized_device_id)] = SwitchError(self._clock(), message)
        self._schedule_publish()

    def _on_device_changed(self, device: LanDevice) -> None:
        if self._config.find_switch(device.device_id) is not None:
            self._schedule_publish()

    def _schedule_publish(self) -> None:
        if self._publish is not None or self._closed:
            return
        self._publish = asyncio.get_running_loop().call_later(PUBLISH_DELAY, self._publish_now)

    def _publish_now(self) -> None:
        self._publish = None
        snapshot = self.snapshot()
        for listener in list(self._listeners):
            listener(snapshot)


def _bridge_identity(hue: HueSettings) -> str:
    """The bridge as .NET compares it: ``$"{BridgeIp}|{AppKey}|{CertificateSha256}"``, where null is empty."""
    return "|".join("" if value is None else value for value in (hue.bridge_ip, hue.app_key, hue.certificate_sha256))
