"""The config entry's runtime: the engine's parts that run as long as the entry, and the supervisor, which starts again
for every change of the configuration (decision 23).

The parts that run as long as the entry are the LAN monitor, the device registry, the status tracker and the activity
buffer. When the entry is set up, :meth:`Sonoff2HueData.async_start` adds the activity buffer to the integration's
logger and starts the LAN monitor, which runs the entry's mDNS listener, and a supervisor. When one of the entry's
``switch`` subentries changes, the entry's update listener calls :meth:`Sonoff2HueData.async_config_changed`, which
restarts only the supervisor: the listener's socket, the LAN registry, the status and the activity stay. Unloading the
entry stops them all, and so does Home Assistant's stop.

What the runtime adds to the engine: Home Assistant's lights, which the switches toggle with the ``light`` actions and
find by their entity registry ids, resolved again when the registry changes (decision 40); the runner's presses and
their outcomes as bus events; repair issues for the problems that the status shows; and the switch test.

The API for the platforms and the flows, on ``entry.runtime_data``:

- ``config``: the :class:`EngineConfig` that the supervisor runs: one :class:`SwitchSettings` per ``switch`` subentry,
  in the subentries' order, with the values as stored.
- ``status``: the :class:`StatusTracker`. ``status.snapshot()`` is the state that the entities show, and each snapshot
  it publishes, 500 ms after the first of the changes it holds, goes out on :data:`SIGNAL_STATUS`.
- ``registry``: the LAN's :class:`DeviceRegistry`. ``add_changed_callback`` calls back with the :class:`LanDevice`
  after every broadcast that isn't a repeat, of any eWeLink device, mapped or not, and
  ``add_listening_changed_callback`` when the listener starts or stops; both return a function that removes the
  callback. They run on the event loop as packets arrive, in the order they were added, and one that raises stops the
  others for that packet, so a callback must not raise.
- ``activity``: the :class:`ActivityBuffer`, the integration's latest log entries, for diagnostics.
- :meth:`Sonoff2HueData.async_test_toggle`: the switch test, for a switch's Test toggle button.
- The dispatcher signals, each formatted with the entry's id, such as ``SIGNAL_STATUS.format(entry.entry_id)``:
  :data:`SIGNAL_STATUS`, :data:`SIGNAL_CONFIG_CHANGED`, :data:`SIGNAL_SUBENTRY_ADDED`, :data:`SIGNAL_SUBENTRY_UPDATED`
  and :data:`SIGNAL_SUBENTRY_REMOVED`. A platform adds the entities of the ``switch`` subentries that exist in its
  ``async_setup_entry``, and those of a later one when :data:`SIGNAL_SUBENTRY_ADDED` brings it, with its
  ``config_subentry_id``. Home Assistant removes a removed subentry's devices and entities itself.
- The bus events ``sonoff2hue_press`` and ``sonoff2hue_toggle``: ``EVENT_PRESS`` and ``EVENT_TOGGLE`` in ``const.py``,
  which describes their data.

Identify by press, for the switch subentry flow: add a callback with ``registry.add_changed_callback`` and take a device
whose ``last_event`` is :attr:`SwitchEventKind.PRESS` with a new ``last_press_at``, as the switch flow takes it
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
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
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
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    CONF_UNMIGRATED,
    DOMAIN,
    EVENT_PRESS,
    EVENT_TOGGLE,
    RESULT_DROPPED,
    RESULT_FAILED,
    RESULT_TOGGLED,
    SUBENTRY_SWITCH,
)
from .engine.activity import ActivityBuffer
from .engine.config import EngineConfig, SwitchSettings
from .engine.lan_monitor import LanMonitor
from .engine.lights import LightControl, Toggler, ToggleResult
from .engine.mdns import MdnsListener
from .engine.registry import DeviceRegistry
from .engine.runner import NO_TARGETS, RunnerObserver, SwitchBinding, labels
from .engine.status import PRESS_DROPPED, ServiceState, StatusSnapshot, StatusTracker
from .engine.supervisor import Supervisor
from .lights import PRESS_CONTEXT, HassLights, async_track_targets, resolve, resolve_switch

_LOGGER = logging.getLogger(__name__)

INTEGRATION_LOGGER: Final = logging.getLogger(__name__.rpartition(".")[0])
"""``custom_components.sonoff2hue``, which every logger of the integration is under. The activity buffer is its handler,
and the switch test logs on it, so its lines have the category ``sonoff2hue``."""

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
ISSUE_INVALID_CONFIG: Final = "invalid_config"
ISSUE_NOT_DETACHED: Final = "not_detached"
ISSUE_DECODE_ERROR: Final = "decode_error"
ISSUE_MISSING_TARGETS: Final = "missing_targets"
ISSUE_UNMIGRATED: Final = "unmigrated"


def new_listener(clock: Callable[[], datetime]) -> MdnsListener:
    """The config entry's mDNS listener, on the network; tests make one on a fake network."""
    return MdnsListener(clock=clock)


def config_from_entry(entry: ConfigEntry) -> EngineConfig:
    """The configuration of the entry: its ``switch`` subentries in their order, with the values as stored."""
    return EngineConfig(
        [_switch(subentry.data) for subentry in entry.subentries.values() if subentry.subentry_type == SUBENTRY_SWITCH]
    )


def _switch(data: Mapping[str, Any]) -> SwitchSettings:
    return SwitchSettings(
        device_id=data.get(CONF_DEVICE_ID) or "",
        name=data.get(CONF_NAME),
        device_key=data.get(CONF_DEVICE_KEY),
        targets=list(data.get(CONF_TARGETS) or ()),
    )


@dataclass(frozen=True, slots=True)
class SwitchTestResult:
    """What the switch test's toggle did, how long it took, the lights it toggled, and why it left out the others."""

    turned_on: bool
    changed: int
    unchanged: int
    failed: int
    elapsed_ms: int
    targets: tuple[str, ...]
    problems: tuple[str, ...] = ()
    """Why each of the switch's lights that Home Assistant doesn't have is left out, as the runner leaves it out."""


class SwitchTestError(Exception):
    """The switch test couldn't toggle; the message says why. When none of the switch's lights is in Home Assistant,
    ``problems`` lists why, and the message joins them with spaces."""

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
    subentry_id: str | None = None
    """The switch whose settings fix the issue: its repair flow leads to the switch's *Edit switch* dialog."""


@dataclass(eq=False, repr=False)
class Sonoff2HueData:
    """What the loaded config entry holds (``entry.runtime_data``): the engine's parts that live as long as the entry,
    and the supervisor of the current configuration. The module's documentation describes its API."""

    hass: HomeAssistant
    entry: Sonoff2HueConfigEntry
    config: EngineConfig
    """The configuration that the supervisor runs, which follows the entry's subentries."""
    registry: DeviceRegistry
    status: StatusTracker
    activity: ActivityBuffer
    listener: MdnsListener
    lan_monitor: LanMonitor
    lights: LightControl
    """Home Assistant's lights, which the supervisors and the switch test switch."""
    clock: Callable[[], datetime]
    _supervisor: Supervisor = field(init=False)
    """The supervisor of the current configuration, from the start on: the entry's changes and the entity registry's,
    which reach it, come only from then on."""
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
            HassLights(hass),
            clock,
        )

    @callback
    def async_start(self) -> None:
        """Starts the LAN monitor and the supervisor as background tasks, after adding the activity buffer to the
        integration's logger, so that it has their first lines, and follows the entity registry, whose changes the
        supervisor resolves the lights again for. The tasks aren't the entry's background tasks: those are cancelled
        again when the entry unloads, and a second cancellation would cut a task's cleanup short. :meth:`async_stop`
        cancels each once."""
        INTEGRATION_LOGGER.addHandler(self.activity)
        self._undo.append(lambda: INTEGRATION_LOGGER.removeHandler(self.activity))
        self._undo.append(self.status.add_listener(self._on_status))
        self._undo.append(async_track_targets(self.hass, self._on_targets_changed))
        self._remember_subentries()
        self._lan_task = self.hass.async_create_background_task(self.lan_monitor.run(), f"{DOMAIN} LAN monitor")
        self._start_supervisor()
        self._update_issues(self.status.snapshot())
        self._stop_listener = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop)

    @callback
    def async_config_changed(self) -> None:
        """The entry's update listener, after any change of the entry. A change of the configuration restarts the
        supervisor: the old one logs "Configuration changed; restarting the switches." and is cancelled, the LAN
        monitor and the status take the new configuration, and a new supervisor starts once the old one has stopped.
        Then the signals go out."""
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
        if not changed and (updated or removed):
            # A changed title or a migrated switch's leftovers don't change the configuration, but may the issues.
            self._update_issues(self.status.snapshot())

    async def async_test_toggle(self, device_id: str, context: Context | None = None) -> SwitchTestResult:
        """The switch test: toggles the switch's lights once, resolved as the runner resolves them, and logs it. A light
        that Home Assistant doesn't have is left out, and the result and the log line name it. The light actions carry
        ``context``, such as that of the Test toggle button's press. Raises :class:`SwitchTestError` when no switch has
        ``device_id``, and when none of the switch's lights is in Home Assistant."""
        settings = self.config.find_switch(device_id.strip())
        if settings is None:
            raise SwitchTestError(f"No switch with device id {device_id} is configured.")
        binding = resolve_switch(er.async_get(self.hass), settings)
        targets, problems = binding.targets, binding.problems
        if not targets:
            raise SwitchTestError(" ".join(problems) or NO_TARGETS, problems)

        started = time.perf_counter()
        token = PRESS_CONTEXT.set(context)
        try:
            result = await Toggler(self.lights, INTEGRATION_LOGGER).toggle(targets)
        finally:
            PRESS_CONTEXT.reset(token)
        elapsed = timedelta(seconds=time.perf_counter() - started)
        name = settings.display_name
        # The resolver's messages name the switch, which the line names first.
        missing = "; ".join(problem.removeprefix(f"{name}: ") for problem in problems)
        INTEGRATION_LOGGER.info(
            "%s: test from Home Assistant turned %s %s%s",
            name,
            "ON" if result.turned_on else "OFF",
            labels(targets),
            f"; {len(problems)} {'is' if len(problems) == 1 else 'are'} missing: {missing}" if problems else ".",
        )
        return SwitchTestResult(
            result.turned_on,
            result.changed,
            result.unchanged,
            result.failed,
            elapsed // timedelta(milliseconds=1),
            tuple(targets),
            tuple(problems),
        )

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
        """No snapshots, issues or signals from now on."""
        self._stopped = True
        self.status.close()
        while self._undo:
            self._undo.pop()()

    @callback
    def _on_targets_changed(self) -> None:
        """The entity registry changed in a way that may change the switches' lights."""
        self._supervisor.targets_changed()

    def _start_supervisor(self) -> None:
        """Runs a supervisor for the current configuration as a background task, once the cancelled ones have
        ended."""
        hass = self.hass
        self._supervisor = supervisor = Supervisor(
            self.config,
            self.listener,
            self.status,
            control=self.lights,
            resolve=lambda config: resolve(hass, config),
            observer=_BusEvents(hass, self._switch_device_id),
            clock=self.clock,
        )
        previous = [task for task in self._supervisor_tasks if not task.done()]
        task = hass.async_create_background_task(_run_after(previous, supervisor), f"{DOMAIN} supervisor")
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

    def _switch_subentry(self, device_id: str) -> ConfigSubentry | None:
        """The ``switch`` subentry of the switch with ``device_id``, without regard to case, if there is one."""
        key = device_id.strip().casefold()
        return next(
            (
                subentry
                for subentry in self.entry.subentries.values()
                if subentry.subentry_type == SUBENTRY_SWITCH
                and _switch(subentry.data).normalized_device_id.casefold() == key
            ),
            None,
        )

    def _switch_device_id(self, settings: SwitchSettings) -> str | None:
        """The Home Assistant device of the switch's subentry, which the platforms make, if there is one."""
        subentry = self._switch_subentry(settings.normalized_device_id)
        if subentry is None:
            return None
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry.entry_id)
        return next((device.id for device in devices if device.config_subentry_id == subentry.subentry_id), None)

    @callback
    def _on_status(self, snapshot: StatusSnapshot) -> None:
        self._update_issues(snapshot)
        async_dispatcher_send(self.hass, SIGNAL_STATUS.format(self.entry.entry_id), snapshot)

    @callback
    def _update_issues(self, snapshot: StatusSnapshot) -> None:
        """Creates the issues that ``snapshot`` and the switches' settings show and deletes the ones they no longer
        show."""
        wanted = _issues_of(snapshot, self._switch_subentry)
        wanted |= _unmigrated_issues(self.entry)
        for issue_id in self._issues - wanted.keys():
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        entry_id = self.entry.entry_id
        for issue_id, issue in wanted.items():
            fixable = issue.subentry_id is not None
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                config_entry_id=entry_id,
                data={"entry_id": entry_id, "subentry_id": issue.subentry_id} if fixable else None,
                is_fixable=fixable,
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


async def _run_after(previous: list[asyncio.Task[None]], supervisor: Supervisor) -> None:
    """Runs ``supervisor`` once the cancelled ones have stopped, so that no two runs overlap."""
    if previous:
        await asyncio.wait(previous)
    await supervisor.run()


def _issues_of(snapshot: StatusSnapshot, subentry_of: Callable[[str], ConfigSubentry | None]) -> dict[str, _Issue]:
    """The repair issues for what ``snapshot`` shows, by issue id."""
    issues: dict[str, _Issue] = {}
    service = snapshot.service
    if service.state is ServiceState.INVALID_CONFIG:
        issues[ISSUE_INVALID_CONFIG] = _Issue(
            ISSUE_INVALID_CONFIG,
            ir.IssueSeverity.ERROR,
            {"message": service.message or "", "problems": _bullets(service.problems)},
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
        if switch.problems:
            subentry = subentry_of(switch.device_id)
            issues[f"{ISSUE_MISSING_TARGETS}_{switch.device_id}"] = _Issue(
                ISSUE_MISSING_TARGETS,
                ir.IssueSeverity.WARNING,
                {"name": name, "problems": _bullets(switch.problems)},
                None if subentry is None else subentry.subentry_id,
            )
    return issues


def _unmigrated_issues(entry: ConfigEntry) -> dict[str, _Issue]:
    """An issue for each switch with lights, rooms or zones of version 1 that the migration found no light for, until
    the switch is saved again (decision 40)."""
    issues: dict[str, _Issue] = {}
    for subentry in entry.subentries.values():
        unmigrated = subentry.data.get(CONF_UNMIGRATED) if subentry.subentry_type == SUBENTRY_SWITCH else None
        if unmigrated:
            switch = _switch(subentry.data)
            issues[f"{ISSUE_UNMIGRATED}_{switch.normalized_device_id}"] = _Issue(
                ISSUE_UNMIGRATED,
                ir.IssueSeverity.WARNING,
                {"name": switch.display_name, "entries": _bullets([f"`{entry}`" for entry in unmigrated])},
                subentry.subentry_id,
            )
    return issues


def _bullets(lines: Sequence[str]) -> str:
    """A markdown list, as an issue's description shows it."""
    return "\n".join(f"- {line}" for line in lines)


class _BusEvents(RunnerObserver):
    """Fires the runner's presses and their outcomes on Home Assistant's bus, right after the status tracker has them."""

    def __init__(self, hass: HomeAssistant, device_id: Callable[[SwitchSettings], str | None]) -> None:
        self._hass = hass
        self._device_id = device_id

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        # A new context for the press, its event and its toggle, whose task copies the context variables as it starts.
        PRESS_CONTEXT.set(Context())
        self._fire(EVENT_PRESS, binding, {ATTR_SEQ: seq})

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        self._fire(
            EVENT_TOGGLE,
            binding,
            {
                ATTR_RESULT: RESULT_TOGGLED,
                ATTR_TARGETS: list(binding.targets),
                ATTR_TURNED_ON: result.turned_on,
                ATTR_CHANGED: result.changed,
                ATTR_UNCHANGED: result.unchanged,
                ATTR_FAILED: result.failed,
                ATTR_ELAPSED_MS: elapsed // timedelta(milliseconds=1),
            },
        )

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self._fire(
            EVENT_TOGGLE,
            binding,
            {ATTR_RESULT: RESULT_FAILED, ATTR_TARGETS: list(binding.targets), ATTR_MESSAGE: message},
        )

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self._fire(
            EVENT_TOGGLE,
            binding,
            {ATTR_RESULT: RESULT_DROPPED, ATTR_TARGETS: list(binding.targets), ATTR_MESSAGE: PRESS_DROPPED},
        )

    def _fire(self, event_type: str, binding: SwitchBinding, data: dict[str, Any]) -> None:
        """Fires the event with the press's context: the one that ``on_press`` made, which a toggle took from its
        press."""
        settings = binding.settings
        self._hass.bus.async_fire(
            event_type,
            {
                ATTR_DEVICE_ID: self._device_id(settings),
                ATTR_SWITCH_ID: settings.normalized_device_id,
                ATTR_NAME: settings.name,
                **data,
            },
            context=PRESS_CONTEXT.get(),
        )
