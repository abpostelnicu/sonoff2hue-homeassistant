"""The config entry's runtime: the engine's parts that run as long as the entry, and the bridge supervisor, which starts
again for every change of the configuration (decision 23).

The .NET app ran its LAN monitor, device registry, status tracker and log buffer for as long as its process, and its
bridge supervisor restarted the bridge after every change of the config file. Here the config entry stands for the
process. When the entry is set up, :meth:`Sonoff2HueData.async_start` adds the activity buffer to the integration's
logger and starts the LAN monitor, which runs the entry's mDNS listener, and a bridge supervisor. When the entry's data
or one of its ``switch`` subentries changes, the entry's update listener calls
:meth:`Sonoff2HueData.async_config_changed`, which restarts only the supervisor: the listener's socket, the LAN registry,
the status and the activity stay. Unloading the entry stops them all, and so does Home Assistant's stop.

What the runtime adds for Home Assistant: the runner's presses and their outcomes as bus events, Home Assistant's
reauthentication when the bridge rejects the application key, repair issues for the problems that the status shows,
and the web UI's switch test.

The API for the platforms and the flows, on ``entry.runtime_data``:

- ``config``: the :class:`EngineConfig` that the supervisor runs: the entry's data, and one :class:`SwitchSettings` per
  ``switch`` subentry, in the subentries' order, with the values as stored.
- ``status``: the :class:`StatusTracker`. ``status.snapshot()`` is the state that the entities show, and each snapshot
  it publishes, 500 ms after the first of the changes it holds, goes out on :data:`SIGNAL_STATUS`.
- ``registry``: the LAN's :class:`DeviceRegistry`. ``add_changed_callback`` calls back with the :class:`LanDevice`
  after every broadcast that isn't a repeat, of any eWeLink device, mapped or not, and
  ``add_listening_changed_callback`` when the listener starts or stops; both return a function that removes the
  callback. They run on the event loop as packets arrive, in the order they were added, and one that raises stops the
  others for that packet, so a callback must not raise.
- ``activity``: the :class:`ActivityBuffer`, the integration's latest log entries, for diagnostics.
- :meth:`Sonoff2HueData.async_test_toggle`: the web UI's switch test, for a switch's Test toggle button.
- The dispatcher signals, each formatted with the entry's id, such as ``SIGNAL_STATUS.format(entry.entry_id)``:
  :data:`SIGNAL_STATUS`, :data:`SIGNAL_CONFIG_CHANGED`, :data:`SIGNAL_SUBENTRY_ADDED`, :data:`SIGNAL_SUBENTRY_UPDATED`
  and :data:`SIGNAL_SUBENTRY_REMOVED`. A platform adds the entities of the ``switch`` subentries that exist in its
  ``async_setup_entry``, and those of a later one when :data:`SIGNAL_SUBENTRY_ADDED` brings it, with its
  ``config_subentry_id``. Home Assistant removes a removed subentry's devices and entities itself.
- The bus events ``sonoff2hue_press`` and ``sonoff2hue_toggle``: ``EVENT_PRESS`` and ``EVENT_TOGGLE`` in ``const.py``,
  which describes their data.

Identify by press, for the switch subentry flow: add a callback with ``registry.add_changed_callback`` and take a device
whose ``last_event`` is :attr:`SwitchEventKind.PRESS` with a new ``last_press_at``, as the web UI's dialog took it
(``engine/switch_form.py``). The LAN monitor's tracker sees every eWeLink device, so a MINIR4M that no switch maps yet
reports its presses too: without its device key, every new ``seq`` is one. Remove the callback once a press came, and
when the flow is removed.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import ATTR_DEVICE_ID, ATTR_NAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from homeassistant.util.signal_type import SignalTypeFormat

from .const import (
    ATTR_CHANGED,
    ATTR_ELAPSED_MS,
    ATTR_FAILED,
    ATTR_MESSAGE,
    ATTR_RESULT,
    ATTR_SEQ,
    ATTR_SWITCH_ID,
    ATTR_TARGETS,
    ATTR_TURNED_ON,
    ATTR_UNCHANGED,
    CONF_APP_KEY,
    CONF_BRIDGE_ID,
    CONF_BRIDGE_IP,
    CONF_CERTIFICATE_SHA256,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
    EVENT_PRESS,
    EVENT_TOGGLE,
    RESULT_DROPPED,
    RESULT_FAILED,
    RESULT_TOGGLED,
    SUBENTRY_SWITCH,
)
from .engine import text
from .engine.activity import ActivityBuffer
from .engine.config import ConfigError, EngineConfig, HueSettings, SwitchSettings
from .engine.hue_client import CERTIFICATE_MISMATCH, HueApiError, HueClient
from .engine.lan_monitor import LanMonitor
from .engine.mdns import MdnsListener
from .engine.registry import DeviceRegistry
from .engine.runner import RunnerObserver, SwitchBinding
from .engine.status import PRESS_DROPPED, ServiceState, StatusSnapshot, StatusTracker
from .engine.supervisor import BridgeSupervisor, HueClientFactory, create_hue_client
from .engine.switch_rules import is_paired
from .engine.targets import resolve
from .engine.toggler import HueToggler, ToggleResult

INTEGRATION_LOGGER: Final = logging.getLogger(__name__.rpartition(".")[0])
"""``custom_components.sonoff2hue``, which every logger of the integration is under. The activity buffer is its handler,
and the switch test logs on it, as the .NET app's test logged in its ``sonoff2hue`` category."""

SIGNAL_STATUS: SignalTypeFormat[StatusSnapshot] = SignalTypeFormat(f"{DOMAIN}_{{}}_status")
"""A snapshot of the status, published 500 ms after the first of the changes it holds."""
SIGNAL_CONFIG_CHANGED: SignalTypeFormat[EngineConfig] = SignalTypeFormat(f"{DOMAIN}_{{}}_config_changed")
"""The configuration changed, and the supervisor started again with it."""
SIGNAL_SUBENTRY_ADDED: SignalTypeFormat[ConfigSubentry] = SignalTypeFormat(f"{DOMAIN}_{{}}_subentry_added")
"""A ``switch`` subentry was added."""
SIGNAL_SUBENTRY_UPDATED: SignalTypeFormat[ConfigSubentry] = SignalTypeFormat(f"{DOMAIN}_{{}}_subentry_updated")
"""A ``switch`` subentry's data, title or unique id changed."""
SIGNAL_SUBENTRY_REMOVED: SignalTypeFormat[str] = SignalTypeFormat(f"{DOMAIN}_{{}}_subentry_removed")
"""A ``switch`` subentry was removed: its id. Home Assistant has removed its devices and entities by then."""

# The repair issues, by translation key; the issue ids of the per-switch ones end with "_<device id>".
ISSUE_CERTIFICATE_NOT_PINNED: Final = "certificate_not_pinned"
ISSUE_CERTIFICATE_CHANGED: Final = "certificate_changed"
ISSUE_INVALID_CONFIG: Final = "invalid_config"
ISSUE_NOT_DETACHED: Final = "not_detached"
ISSUE_DECODE_ERROR: Final = "decode_error"

TEST_NOT_PAIRED: Final = "Pair the Hue bridge first: reconfigure the sonoff2hue integration."
""".NET's "Pair the Hue bridge first.", with how to pair in Home Assistant."""


def new_listener(clock: Callable[[], datetime]) -> MdnsListener:
    """The config entry's mDNS listener, on the network; tests make one on a fake network."""
    return MdnsListener(clock=clock)


def config_from_entry(entry: ConfigEntry) -> EngineConfig:
    """The configuration of the entry: its data, and its ``switch`` subentries in their order, with the values as
    stored."""
    data = entry.data
    hue = HueSettings(
        bridge_ip=data.get(CONF_BRIDGE_IP),
        bridge_id=data.get(CONF_BRIDGE_ID),
        app_key=data.get(CONF_APP_KEY),
        certificate_sha256=data.get(CONF_CERTIFICATE_SHA256),
    )
    switches = [
        _switch(subentry.data) for subentry in entry.subentries.values() if subentry.subentry_type == SUBENTRY_SWITCH
    ]
    return EngineConfig(hue, switches)


def _switch(data: Mapping[str, Any]) -> SwitchSettings:
    return SwitchSettings(
        device_id=data.get(CONF_DEVICE_ID) or "",
        name=data.get(CONF_NAME),
        device_key=data.get(CONF_DEVICE_KEY),
        lights=list(data.get(CONF_LIGHTS) or ()),
        groups=list(data.get(CONF_GROUPS) or ()),
    )


@dataclass(frozen=True, slots=True)
class SwitchTestResult:
    """The .NET app's ``TestResult``: what the test's toggle did, how long it took, and the targets it toggled."""

    turned_on: bool
    changed: int
    unchanged: int
    failed: int
    elapsed_ms: int
    targets: tuple[str, ...]


class SwitchTestError(Exception):
    """The switch test couldn't toggle, with the .NET app's message. For lights, rooms or zones that aren't on the
    bridge, ``problems`` lists them, and the message joins them as the web UI showed them."""

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class _Subentry:
    """What the runtime last saw of a ``switch`` subentry, to tell which ones a change added, changed or removed."""

    title: str
    unique_id: str | None
    data: Mapping[str, Any]

    @classmethod
    def of(cls, subentry: ConfigSubentry) -> _Subentry:
        # Home Assistant replaces a subentry's data when it changes, so the old mapping keeps the old values.
        return cls(subentry.title, subentry.unique_id, subentry.data)


@dataclass(frozen=True, slots=True)
class _Issue:
    translation_key: str
    severity: ir.IssueSeverity
    placeholders: dict[str, str]
    fixable: bool = False
    """Fixed by pairing the bridge again: the issue's repair flow leads to the integration's reconfigure flow."""


@dataclass(eq=False, repr=False)
class Sonoff2HueData:
    """What the loaded config entry holds (``entry.runtime_data``): the engine's parts that live as long as the entry,
    and the bridge supervisor of the current configuration. The module's documentation describes its API."""

    hass: HomeAssistant
    entry: Sonoff2HueConfigEntry
    config: EngineConfig
    """The configuration that the supervisor runs, which follows the entry's data and subentries."""
    registry: DeviceRegistry
    status: StatusTracker
    activity: ActivityBuffer
    listener: MdnsListener
    lan_monitor: LanMonitor
    hue_clients: HueClientFactory
    clock: Callable[[], datetime]
    _supervisor: BridgeSupervisor = field(init=False)
    _supervisor_tasks: list[asyncio.Task[None]] = field(default_factory=list, init=False)
    """The supervisor's task, after the cancelled ones of earlier configurations that haven't ended yet."""
    _lan_task: asyncio.Task[None] | None = field(default=None, init=False)
    _subentries: dict[str, _Subentry] = field(default_factory=dict, init=False)
    _issues: set[str] = field(default_factory=set, init=False)
    _undo: list[Callable[[], None]] = field(default_factory=list, init=False)
    _stop_listener: Callable[[], None] | None = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)

    @classmethod
    def create(cls, hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> Sonoff2HueData:
        """The runtime of ``entry``, on Home Assistant's clock, not started yet."""
        clock = dt_util.utcnow
        config = config_from_entry(entry)
        registry = DeviceRegistry(clock)
        listener = new_listener(clock)
        return cls(
            hass,
            entry,
            config,
            registry,
            StatusTracker(config, registry, clock),
            ActivityBuffer(),
            listener,
            LanMonitor(listener, registry, config),
            create_hue_client,
            clock,
        )

    def __post_init__(self) -> None:
        self._supervisor = self._new_supervisor()

    @callback
    def async_start(self) -> None:
        """Starts the LAN monitor and the supervisor as background tasks, after adding the activity buffer to the
        integration's logger, so that it has their first lines. They aren't the entry's background tasks: those are
        cancelled again when the entry unloads, and a second cancellation would cut short a supervisor that is closing
        its Hue client. :meth:`async_stop` cancels each once."""
        INTEGRATION_LOGGER.addHandler(self.activity)
        self._undo.append(lambda: INTEGRATION_LOGGER.removeHandler(self.activity))
        self._undo.append(self.status.add_listener(self._on_status))
        self._remember_subentries()
        self._lan_task = self.hass.async_create_background_task(self.lan_monitor.run(), f"{DOMAIN} LAN monitor")
        self._start_supervisor()
        self._update_issues(self.status.snapshot())
        self._stop_listener = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop)

    @callback
    def async_config_changed(self) -> None:
        """The entry's update listener, after any change of the entry. A change of the configuration restarts the
        supervisor, as the .NET app's ``ConfigStore`` did: the old one logs "Configuration changed; restarting the
        bridge." and is cancelled, the LAN monitor and the status take the new configuration, and a new supervisor
        starts once the old one has stopped. Then the signals go out."""
        if self._stopped:
            return
        config = config_from_entry(self.entry)
        changed = config != self.config
        if changed:
            self._supervisor.config_changed()
            _cancel_once(self._supervisor_tasks)
            self.config = config
            self.lan_monitor.config_changed(config)
            self.status.config_changed(config)
            self._supervisor = self._new_supervisor()
            self._start_supervisor()
        added, updated, removed = self._remember_subentries()
        entry_id = self.entry.entry_id
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_CONFIG_CHANGED.format(entry_id), config)
        for subentry_id in removed:
            async_dispatcher_send(self.hass, SIGNAL_SUBENTRY_REMOVED.format(entry_id), subentry_id)
        for subentry in updated:
            async_dispatcher_send(self.hass, SIGNAL_SUBENTRY_UPDATED.format(entry_id), subentry)
        for subentry in added:
            async_dispatcher_send(self.hass, SIGNAL_SUBENTRY_ADDED.format(entry_id), subentry)

    async def async_test_toggle(self, device_id: str) -> SwitchTestResult:
        """The .NET app's switch test (``ConfigEndpoints.TestAsync``): toggles the switch's lights, rooms and zones once,
        resolved with a client of its own from the bridge's lights and groups as they are now, and logs it. Raises
        :class:`SwitchTestError` with .NET's message when no switch has ``device_id``, when the bridge isn't paired,
        when its settings can't be used, when some targets aren't on the bridge, and with the bridge's error."""
        config = self.config
        settings = config.find_switch(text.trim(device_id))
        if settings is None:
            raise SwitchTestError(f"No switch with device id {device_id} is configured.")
        if not is_paired(config.hue):
            raise SwitchTestError(TEST_NOT_PAIRED)
        try:
            hue = self.hue_clients(config.hue)
        except ConfigError as err:
            raise SwitchTestError(str(err)) from err
        try:
            return await _test(settings, hue)
        except HueApiError as err:
            raise SwitchTestError(str(err)) from err
        finally:
            await hue.close()

    async def async_stop(self) -> None:
        """Closes the status and takes the activity buffer off the logger, stops the supervisors, then the LAN monitor,
        whose listener closes its socket, and deletes the repair issues. The entry calls it when it unloads, and when
        its setup fails."""
        if self._stop_listener is not None:
            self._stop_listener()
            self._stop_listener = None
        self._close()
        _cancel_once(self._supervisor_tasks)
        if self._supervisor_tasks:
            await asyncio.wait(self._supervisor_tasks)
        if self._lan_task is not None:
            _cancel_once([self._lan_task])
            await asyncio.wait([self._lan_task])
        for issue_id in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._issues.clear()

    @callback
    def _on_hass_stop(self, event: Event) -> None:
        """Home Assistant cancels the background tasks when it stops, without unloading the entry."""
        self._stop_listener = None
        self._close()

    def _close(self) -> None:
        """No snapshots, issues, signals or reauthentication from now on."""
        self._stopped = True
        self.status.close()
        while self._undo:
            self._undo.pop()()

    def _new_supervisor(self) -> BridgeSupervisor:
        return BridgeSupervisor(
            self.config,
            self.listener,
            self.status,
            hue_clients=self.hue_clients,
            on_key_rejected=self._on_key_rejected,
            observer=_BusEvents(self.hass, self._switch_device_id),
            clock=self.clock,
        )

    def _start_supervisor(self) -> None:
        """Runs the current supervisor as a background task, once the cancelled ones have ended."""
        previous = [task for task in self._supervisor_tasks if not task.done()]
        task = self.hass.async_create_background_task(
            _run_after(previous, self._supervisor), f"{DOMAIN} bridge supervisor"
        )
        self._supervisor_tasks = [*previous, task]

    def _remember_subentries(self) -> tuple[list[ConfigSubentry], list[ConfigSubentry], list[str]]:
        """The ``switch`` subentries added, changed and removed since the last call."""
        current = {
            subentry_id: subentry
            for subentry_id, subentry in self.entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_SWITCH
        }
        known = self._subentries
        added = [subentry for subentry_id, subentry in current.items() if subentry_id not in known]
        updated = [
            subentry
            for subentry_id, subentry in current.items()
            if subentry_id in known and known[subentry_id] != _Subentry.of(subentry)
        ]
        removed = [subentry_id for subentry_id in known if subentry_id not in current]
        self._subentries = {subentry_id: _Subentry.of(subentry) for subentry_id, subentry in current.items()}
        return added, updated, removed

    def _switch_device_id(self, settings: SwitchSettings) -> str | None:
        """The Home Assistant device of the switch's subentry, which the platforms make, if there is one."""
        key = text.fold(settings.normalized_device_id)
        subentry_id = next(
            (
                subentry_id
                for subentry_id, subentry in self.entry.subentries.items()
                if subentry.subentry_type == SUBENTRY_SWITCH
                and text.fold(_switch(subentry.data).normalized_device_id) == key
            ),
            None,
        )
        if subentry_id is None:
            return None
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)
        return next((device.id for device in devices if device.config_subentry_id == subentry_id), None)

    @callback
    def _on_key_rejected(self) -> None:
        """Home Assistant starts no second reauthentication while one, or a reconfigure flow, is in progress."""
        if not self._stopped:
            self.entry.async_start_reauth(self.hass)

    @callback
    def _on_status(self, snapshot: StatusSnapshot) -> None:
        self._update_issues(snapshot)
        async_dispatcher_send(self.hass, SIGNAL_STATUS.format(self.entry.entry_id), snapshot)

    @callback
    def _update_issues(self, snapshot: StatusSnapshot) -> None:
        """Creates the issues that ``snapshot`` shows and deletes the ones it no longer shows."""
        wanted = _issues_of(snapshot)
        for issue_id in self._issues - wanted.keys():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        entry_id = self.entry.entry_id
        for issue_id, issue in wanted.items():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                config_entry_id=entry_id,
                data={"entry_id": entry_id} if issue.fixable else None,
                is_fixable=issue.fixable,
                severity=issue.severity,
                translation_key=issue.translation_key,
                translation_placeholders=issue.placeholders,
            )
        self._issues = set(wanted)


type Sonoff2HueConfigEntry = ConfigEntry[Sonoff2HueData]


def _cancel_once(tasks: list[asyncio.Task[None]]) -> None:
    """Cancels the tasks that aren't being cancelled yet: cancelling one again would interrupt its cleanup."""
    for task in tasks:
        if not task.cancelling():
            task.cancel()


async def _run_after(previous: list[asyncio.Task[None]], supervisor: BridgeSupervisor) -> None:
    """Runs ``supervisor`` once the cancelled ones have stopped and closed their clients, as the .NET app's supervisor
    started a run after the last one had ended."""
    if previous:
        await asyncio.wait(previous)
    await supervisor.run()


async def _test(settings: SwitchSettings, hue: HueClient) -> SwitchTestResult:
    errors: list[str] = []
    targets = resolve(settings, await hue.get_lights(), await hue.get_groups(), errors)
    if errors:
        raise SwitchTestError(" ".join(errors), errors)

    started = time.perf_counter()
    result = await HueToggler(hue, INTEGRATION_LOGGER).toggle(targets)
    elapsed = timedelta(seconds=time.perf_counter() - started)
    INTEGRATION_LOGGER.info(
        "%s: test from Home Assistant turned %s %s.",
        settings.display_name,
        "ON" if result.turned_on else "OFF",
        ", ".join(str(target) for target in targets),
    )
    return SwitchTestResult(
        result.turned_on,
        result.changed,
        result.unchanged,
        result.failed,
        elapsed // timedelta(milliseconds=1),
        tuple(str(target) for target in targets),
    )


def _issues_of(snapshot: StatusSnapshot) -> dict[str, _Issue]:
    """The repair issues for what ``snapshot`` shows, by issue id."""
    issues: dict[str, _Issue] = {}
    hue = snapshot.hue
    if hue.paired and not hue.certificate_pinned:
        issues[ISSUE_CERTIFICATE_NOT_PINNED] = _Issue(
            ISSUE_CERTIFICATE_NOT_PINNED, ir.IssueSeverity.WARNING, {}, fixable=True
        )
    if hue.message == CERTIFICATE_MISMATCH:
        issues[ISSUE_CERTIFICATE_CHANGED] = _Issue(ISSUE_CERTIFICATE_CHANGED, ir.IssueSeverity.ERROR, {}, fixable=True)
    service = snapshot.service
    if service.state is ServiceState.INVALID_CONFIG:
        problems = "\n".join(f"- {problem}" for problem in service.problems)
        issues[ISSUE_INVALID_CONFIG] = _Issue(
            ISSUE_INVALID_CONFIG, ir.IssueSeverity.ERROR, {"message": service.message or "", "problems": problems}
        )
    for switch in snapshot.switches:
        name = SwitchSettings(switch.device_id, switch.name).display_name
        if switch.detached is False:
            issues[f"{ISSUE_NOT_DETACHED}_{switch.device_id}"] = _Issue(
                ISSUE_NOT_DETACHED, ir.IssueSeverity.WARNING, {"name": name}
            )
        if switch.decode_error is not None:
            issues[f"{ISSUE_DECODE_ERROR}_{switch.device_id}"] = _Issue(
                ISSUE_DECODE_ERROR, ir.IssueSeverity.WARNING, {"name": name, "error": switch.decode_error}
            )
    return issues


class _BusEvents(RunnerObserver):
    """Fires the runner's presses and their outcomes on Home Assistant's bus, right after the status tracker has them."""

    def __init__(self, hass: HomeAssistant, device_id: Callable[[SwitchSettings], str | None]) -> None:
        self._hass = hass
        self._device_id = device_id

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        self._fire(EVENT_PRESS, binding, {ATTR_SEQ: seq})

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        self._fire(
            EVENT_TOGGLE,
            binding,
            {
                ATTR_RESULT: RESULT_TOGGLED,
                ATTR_TARGETS: _targets(binding),
                ATTR_TURNED_ON: result.turned_on,
                ATTR_CHANGED: result.changed,
                ATTR_UNCHANGED: result.unchanged,
                ATTR_FAILED: result.failed,
                ATTR_ELAPSED_MS: elapsed // timedelta(milliseconds=1),
            },
        )

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self._fire(
            EVENT_TOGGLE, binding, {ATTR_RESULT: RESULT_FAILED, ATTR_TARGETS: _targets(binding), ATTR_MESSAGE: message}
        )

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self._fire(
            EVENT_TOGGLE,
            binding,
            {ATTR_RESULT: RESULT_DROPPED, ATTR_TARGETS: _targets(binding), ATTR_MESSAGE: PRESS_DROPPED},
        )

    def _fire(self, event_type: str, binding: SwitchBinding, data: dict[str, Any]) -> None:
        settings = binding.settings
        self._hass.bus.async_fire(
            event_type,
            {
                ATTR_DEVICE_ID: self._device_id(settings),
                ATTR_SWITCH_ID: settings.normalized_device_id,
                ATTR_NAME: settings.name,
                **data,
            },
        )


def _targets(binding: SwitchBinding) -> list[str]:
    return [str(target) for target in binding.targets]
