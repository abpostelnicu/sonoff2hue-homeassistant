"""The integration's state for its entities and diagnostics: what the supervisor and the runner report, combined with
the configuration and the LAN devices (decision 21).

Changes reach the listeners as one snapshot, 500 ms after the first of them, so that the reports of one event arrive
together and the entities write their states at most twice a second. The tracker is a :class:`RunnerObserver`, which
the supervisor gives the runner, and it follows the :class:`DeviceRegistry` for the switches' devices. It is used on the
event loop only. Its clock returns an aware UTC datetime, so that tests can set the time.

Use, for the Home Assistant runtime: make one for a configuration and its registry, add a listener with
:meth:`StatusTracker.add_listener`, pass the tracker to the supervisor, read :meth:`StatusTracker.snapshot` whenever
needed, and :meth:`StatusTracker.close` it when the config entry unloads. The runtime keeps the tracker and its registry
while the configuration changes, and calls :meth:`StatusTracker.config_changed` for each change (decision 23).
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .config import EngineConfig
from .lights import ToggleResult
from .registry import DeviceRegistry, LanDevice
from .runner import RunnerObserver, SwitchBinding, bindings_by_device_id

PUBLISH_DELAY = 0.5
"""Seconds that changes are collected before the listeners get a snapshot with all of them."""
PRESS_DROPPED = "A press was dropped because it waited more than 5 seconds for the lights."
"""A switch's last error after the runner dropped a press."""
STARTING = "Starting."
"""The service's message before the supervisor's first report, which comes as the runtime starts."""


class ServiceState(StrEnum):
    """The state of the switches; the values are the Status sensor's options."""

    NOT_CONFIGURED = "not_configured"
    """No switch is mapped yet."""
    INVALID_CONFIG = "invalid_config"
    """The configuration has errors."""
    RUNNING = "running"
    ERROR = "error"
    """Something unexpected failed."""


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    state: ServiceState
    message: str | None
    problems: tuple[str, ...]
    since: datetime
    """When the state, the message or the problems last changed."""


@dataclass(frozen=True, slots=True)
class LanStatus:
    listening: bool
    devices_seen: int
    last_packet_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToggleAction:
    """The last toggle of a switch's lights."""

    at: datetime
    turned_on: bool
    changed: int
    unchanged: int
    failed: int
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class SwitchError:
    """The last thing that went wrong for a switch; not an exception."""

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
    """The lights that the switch toggles, by their entity ids, while the switches run."""
    problems: tuple[str, ...]
    """While the switches run, why each of the switch's lights that Home Assistant doesn't have is left out: the switch
    toggles the others (decision 32)."""
    last_action: ToggleAction | None
    last_error: SwitchError | None


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    started_at: datetime
    """When the tracker was made: the config entry's setup."""
    service: ServiceStatus
    lan: LanStatus
    switches: tuple[SwitchStatus, ...]
    """In the order of the configuration."""


type StatusListener = Callable[[StatusSnapshot], None]


def _now() -> datetime:
    return datetime.now(UTC)


class StatusTracker(RunnerObserver):
    """The switches' state, combined with the configuration and the LAN devices. The supervisor sets the service's
    state; as the runner's observer, it records each switch's last action and error."""

    def __init__(self, config: EngineConfig, registry: DeviceRegistry, clock: Callable[[], datetime] = _now) -> None:
        self._config = config
        self._registry = registry
        self._clock = clock
        self._started_at = clock()
        self._service = ServiceStatus(ServiceState.NOT_CONFIGURED, STARTING, (), self._started_at)
        # By the switches' device ids, casefolded.
        self._targets: dict[str, tuple[str, ...]] = {}
        self._problems: dict[str, tuple[str, ...]] = {}
        self._last_actions: dict[str, ToggleAction] = {}
        self._last_errors: dict[str, SwitchError] = {}
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
        is published only with the next other change: follow the registry for every device."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def set_service(self, state: ServiceState, message: str | None, problems: Sequence[str] | None = None) -> None:
        """Sets the service's state; ``since`` moves only when the state, the message or the problems change. Any state
        but running forgets the switches' lights and problems."""
        problems = () if problems is None else tuple(problems)
        service = self._service
        if service.state == state and service.message == message and service.problems == problems:
            return

        self._service = ServiceStatus(state, message, problems, self._clock())
        if state != ServiceState.RUNNING:
            self._targets = {}
            self._problems = {}
        self._schedule_publish()

    def set_running(self, bindings: Sequence[SwitchBinding]) -> None:
        """The runner runs these resolved switches: each switch's lights and problems, and the service's message and
        problems, all of the switches' in their order. A snapshot follows when any of them changed. Raises
        :class:`ValueError` when two of them have the same device id, and changes nothing then."""
        by_device_id = bindings_by_device_id(bindings)
        targets = {key: tuple(binding.targets) for key, binding in by_device_id.items()}
        problems = {key: tuple(binding.problems) for key, binding in by_device_id.items() if binding.problems}
        if (targets, problems) != (self._targets, self._problems):
            self._targets, self._problems = targets, problems
            self._schedule_publish()

        count = sum(len(binding.targets) for binding in bindings)
        missing = [problem for binding in bindings for problem in binding.problems]
        message = (
            f"{len(bindings)} wall {'switch toggles' if len(bindings) == 1 else 'switches toggle'} {count} "
            f"{'light' if count == 1 else 'lights'}"
        )
        if missing:
            message += f"; {len(missing)} {'is' if len(missing) == 1 else 'are'} missing"
        self.set_service(ServiceState.RUNNING, f"{message}.", missing)

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        now = self._clock()
        key = binding.settings.normalized_device_id.casefold()
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
        self._record_error(binding, PRESS_DROPPED)

    def snapshot(self) -> StatusSnapshot:
        config = self._config
        devices = self._registry.snapshot()
        switches: list[SwitchStatus] = []
        for switch in config.switches:
            device_id = switch.normalized_device_id
            key = device_id.casefold()
            device = next((d for d in devices if d.device_id.casefold() == key), None)
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
                    self._problems.get(key, ()),
                    self._last_actions.get(key),
                    self._last_errors.get(key),
                )
            )

        return StatusSnapshot(
            self._started_at,
            self._service,
            LanStatus(self._registry.listening, len(devices), self._registry.last_packet_at),
            tuple(switches),
        )

    def config_changed(self, config: EngineConfig) -> None:
        """For each change of the configuration, which the runtime applies without reloading the config entry:
        snapshots show the new configuration."""
        self._config = config
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
        self._last_errors[binding.settings.normalized_device_id.casefold()] = SwitchError(self._clock(), message)
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
