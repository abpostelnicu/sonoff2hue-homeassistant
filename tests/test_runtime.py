"""The runtime of the config entry (decision 23), in Home Assistant: the LAN on a fake network, the lights in Home
Assistant's state machine with ``light`` actions of the tests' own (decision 40), and time moved by ``freezer``."""

import asyncio
import logging
import string
from types import MappingProxyType
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry, ConfigSubentryData
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, async_mock_signal

from custom_components.sonoff2hue import runtime
from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    CONF_UNMIGRATED,
    DOMAIN,
    EVENT_PRESS,
    EVENT_TOGGLE,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine import mdns
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.runner import NO_TARGETS
from custom_components.sonoff2hue.engine.status import PRESS_DROPPED, ServiceState
from custom_components.sonoff2hue.runtime import (
    INTEGRATION_LOGGER,
    SIGNAL_CONFIG_CHANGED,
    SIGNAL_STATUS,
    SIGNAL_SUBENTRY_ADDED,
    SIGNAL_SUBENTRY_REMOVED,
    SIGNAL_SUBENTRY_UPDATED,
    Sonoff2HueData,
    SwitchTestError,
    SwitchTestResult,
    config_from_entry,
)

from .fakes import CEILING, DEVICE_ADDRESS, DEVICE_ID, DEVICE_KEY, OTHER_DEVICE_ID, Lan, Lights, entry, relay, switch
from .platforms import PRESS, RUNNING, settle, start, state_of, tick

HALL = f"Hall ({DEVICE_ID})"
RESTARTING = "Configuration changed; restarting the switches."
LISTENING = "Listening for wall switch presses."
SUPERVISOR = "custom_components.sonoff2hue.engine.supervisor"
RUNNER = "custom_components.sonoff2hue.engine.runner"
UNKNOWN_TARGET = "0123456789abcdef0123456789abcdef"
"""A made-up entity registry id, which the registry has never had."""


def subentry_of(config_entry: MockConfigEntry, device_id: str = DEVICE_ID) -> ConfigSubentry:
    return next(subentry for subentry in config_entry.subentries.values() if subentry.unique_id == device_id)


def new_subentry(device_id: str, name: str, targets: list[str]) -> ConfigSubentry:
    data = {CONF_DEVICE_ID: device_id, CONF_NAME: name, CONF_DEVICE_KEY: None, CONF_TARGETS: targets}
    return ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_SWITCH, title=name, unique_id=device_id)


def logged(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == logger]


def issues(hass: HomeAssistant) -> dict[str, ir.IssueEntry]:
    return {issue_id: issue for (domain, issue_id), issue in ir.async_get(hass).issues.items() if domain == DOMAIN}


async def check_texts(hass: HomeAssistant, issue: ir.IssueEntry) -> None:
    """The issue's texts are in ``translations/en.json``, with the placeholders they use: a fixable one's are those of
    its repair flow."""
    texts = await async_get_translations(hass, "en", "issues", [DOMAIN])
    prefix = f"component.{DOMAIN}.issues.{issue.translation_key}"
    keys = [f"{prefix}.title"]
    if issue.is_fixable:
        keys += [f"{prefix}.fix_flow.step.confirm.{key}" for key in ("title", "description")]
        keys += [f"{prefix}.fix_flow.abort.{reason}" for reason in ("edit_switch", "not_found", "not_loaded")]
    else:
        keys.append(f"{prefix}.description")
    given = set(issue.translation_placeholders or {})
    for key in keys:
        assert key in texts, f"{key} is missing from translations/en.json"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the issue doesn't give"


def switch_device(hass: HomeAssistant, config_entry: MockConfigEntry) -> dict[str, Any]:
    """How the bus events name the switch Hall: by the device that the platforms made for its subentry."""
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), config_entry.entry_id)
    assert device is not None
    assert device.config_subentry_id == subentry_of(config_entry).subentry_id
    return {"device_id": device.id, "switch_id": DEVICE_ID, "name": "Hall"}


def rename(hass: HomeAssistant, entity_id: str, new_entity_id: str) -> None:
    """Gives a light another entity id in the entity registry, as its settings do; its integration then shows its state
    under the new one."""
    state = hass.states.get(entity_id)
    assert state is not None
    er.async_get(hass).async_update_entity(entity_id, new_entity_id=new_entity_id)
    hass.states.async_remove(entity_id)
    hass.states.async_set(new_entity_id, state.state, state.attributes)


class Stubborn:
    """``light`` actions that never answer, and that end only when let go once they are cancelled, as an integration
    may take its time to stop a request."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.let_go = asyncio.Event()
        self.ended = False
        for service in ("turn_on", "turn_off"):
            hass.services.async_register("light", service, self._handle)

    async def _handle(self, call: ServiceCall) -> None:
        try:
            await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            await self.let_go.wait()
            self.ended = True
            raise


# The configuration.


def test_the_configuration_is_the_entrys_switches_as_stored() -> None:
    """In the subentries' order; a subentry of another type isn't a switch."""
    config_entry = entry(
        switch(" 1000ABCDEF ", None, targets=[" light.ceiling ", UNKNOWN_TARGET], key=DEVICE_KEY),
        ConfigSubentryData(data={}, subentry_type="other", title="Other", unique_id=None),
        switch(OTHER_DEVICE_ID, "Desk", targets=["light.desk"]),
        ConfigSubentryData(
            data={CONF_DEVICE_ID: None, CONF_TARGETS: None}, subentry_type=SUBENTRY_SWITCH, title="", unique_id=None
        ),
    )

    assert config_from_entry(config_entry) == EngineConfig(
        [
            SwitchSettings(" 1000ABCDEF ", None, DEVICE_KEY, [" light.ceiling ", UNKNOWN_TARGET]),
            SwitchSettings(OTHER_DEVICE_ID, "Desk", None, ["light.desk"]),
            SwitchSettings("", None, None, []),
        ]
    )
    assert config_from_entry(entry()) == EngineConfig()


# Starting and stopping.


def test_the_runtimes_listener_is_on_the_network() -> None:
    """Unless a test gives it the fake LAN."""
    listener = runtime.new_listener(dt_util.utcnow)

    assert listener._transport_factory is mdns.open_multicast_transport
    assert listener._network_watcher is mdns.watch_network_changes


async def test_a_runtime_that_never_started_stops_at_once(hass: HomeAssistant, lan: Lan) -> None:
    """As when the entry's setup fails before the runtime starts."""
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)
    runtime_data = Sonoff2HueData.create(hass, config_entry)

    await runtime_data.async_stop()

    assert lan.network.transports == []
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers


async def test_the_runtime_runs_the_lan_monitor_and_the_switches_until_it_unloads(
    hass: HomeAssistant, lan: Lan
) -> None:
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)

    (transport,) = lan.network.transports
    assert not transport.closed
    assert lan.network.watching
    assert runtime_data.registry.listening
    service = runtime_data.status.service
    assert (service.state, service.message) == (ServiceState.RUNNING, RUNNING)
    assert runtime_data.config == config_from_entry(config_entry)
    assert runtime_data.activity in INTEGRATION_LOGGER.handlers

    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert transport.closed
    assert not lan.network.watching
    assert not runtime_data.registry.listening
    assert all(task.done() for task in runtime_data._supervisor_tasks)
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_a_reload_starts_a_new_runtime_after_the_old_one_stopped(hass: HomeAssistant, lan: Lan) -> None:
    config_entry = entry(switch())
    first = await start(hass, config_entry)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await settle()

    second = config_entry.runtime_data
    assert second is not first
    assert [transport.closed for transport in lan.network.transports] == [True, False]
    assert second.status.service.state is ServiceState.RUNNING
    assert INTEGRATION_LOGGER.handlers.count(second.activity) == 1
    assert first.activity not in INTEGRATION_LOGGER.handlers


async def test_home_assistant_stopping_ends_the_runtime(hass: HomeAssistant, lan: Lan) -> None:
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)

    await hass.async_stop()
    # Home Assistant doesn't wait for the tasks it cancels when it stops.
    await settle()

    assert lan.network.transports[0].closed
    assert all(task.done() for task in runtime_data._supervisor_tasks)
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers
    # Stopped: a change is no longer applied.
    subentry = subentry_of(config_entry)
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_TARGETS: ["light.desk"]}
    )
    await settle()

    assert runtime_data.config.switches[0].targets == [CEILING]
    assert len(runtime_data._supervisor_tasks) == 1


async def test_removing_the_entry_deletes_what_version_1_kept_for_it(
    hass: HomeAssistant, lan: Lan, hass_storage: dict[str, Any]
) -> None:
    """Version 1's store of the lights, rooms and zones that each switch found, if it was never migrated."""
    config_entry = entry(switch())
    key = f"{DOMAIN}.{config_entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"resources": []}}
    await start(hass, config_entry)

    assert await hass.config_entries.async_remove(config_entry.entry_id)

    assert key not in hass_storage


# Presses, toggles and their events.


async def test_a_press_toggles_the_lights_and_fires_the_events(
    hass: HomeAssistant, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    lights.add("ceiling")
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    hall = switch_device(hass, config_entry)

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert lights.turned() == [("turn_on", CEILING)]
    assert [event.data for event in presses] == [{**hall, "seq": 2}]
    assert [event.data for event in toggles] == [
        {
            **hall,
            "result": "toggled",
            "targets": [CEILING],
            "turned_on": True,
            "changed": 1,
            "unchanged": 0,
            "failed": 0,
            "elapsed_ms": 0,
        }
    ]
    action = runtime_data.status.snapshot().switches[0].last_action
    assert action is not None
    assert (action.turned_on, action.changed) == (True, 1)

    # A light that Home Assistant can't switch counts as failed.
    lights.fail(CEILING)
    lan.broadcast(3)
    await settle()

    assert presses[-1].data == {**hall, "seq": 3}
    assert toggles[-1].data == {
        **hall,
        "result": "toggled",
        "targets": [CEILING],
        "turned_on": False,
        "changed": 0,
        "unchanged": 0,
        "failed": 1,
        "elapsed_ms": 0,
    }

    # A press that waits more than 5 seconds for the lights is dropped: the press before it waits for a light that
    # doesn't answer within 5 seconds.
    lights.heal()
    held = lights.hold(CEILING)
    lan.broadcast(4)
    lan.broadcast(5)
    await settle()
    await tick(freezer, 6)
    held.set()
    await settle()

    assert [event.data["seq"] for event in presses] == [2, 3, 4, 5]
    assert [event.data["result"] for event in toggles] == ["toggled", "toggled", "toggled", "dropped"]
    assert (toggles[2].data["failed"], toggles[2].data["elapsed_ms"]) == (1, 6000)
    assert toggles[-1].data == {**hall, "result": "dropped", "targets": [CEILING], "message": PRESS_DROPPED}
    assert lights.turned() == [("turn_off", CEILING), ("turn_off", CEILING)]


async def test_a_press_its_events_and_its_lights_changes_share_a_context_of_their_own(
    hass: HomeAssistant, lan: Lan, lights: Lights
) -> None:
    """Each press has a context of its own: its events, its Press and its toggle's light actions carry it, so the
    lights' new states do too, and the logbook shows their changes as the press's. The switch test's actions carry the
    context that it is given."""
    lights.add("ceiling")
    desk = lights.add("desk")
    runtime_data = await start(hass, entry(switch(targets=[CEILING, lights.target(desk)])))
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)

    def contexts() -> set[str]:
        return {state_of(hass, entity_id).context.id for entity_id in (PRESS, CEILING, desk)}

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    first = presses[0].context
    assert contexts() == {first.id}

    lan.broadcast(3)
    await settle()
    second = presses[1].context
    assert second.id != first.id
    assert contexts() == {second.id}
    assert [event.context.id for event in toggles] == [first.id, second.id]

    tested = Context()
    await runtime_data.async_test_toggle(DEVICE_ID, tested)
    assert {state_of(hass, entity_id).context.id for entity_id in (CEILING, desk)} == {tested.id}


async def test_a_switch_with_nothing_left_to_toggle_fires_its_presses_and_fails_each_toggle(
    hass: HomeAssistant, lan: Lan
) -> None:
    """Its presses reach automations as before, and each toggle event says why nothing was toggled."""
    config_entry = entry(switch(targets=[UNKNOWN_TARGET]))
    runtime_data = await start(hass, config_entry)
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    hall = switch_device(hass, config_entry)

    assert runtime_data.status.service.message == "1 wall switch toggles 0 lights; 1 is missing."

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert [event.data for event in presses] == [{**hall, "seq": 2}]
    assert [event.data for event in toggles] == [{**hall, "result": "failed", "targets": [], "message": NO_TARGETS}]


async def test_a_switch_without_a_subentry_names_no_device(hass: HomeAssistant, lan: Lan) -> None:
    """A switch that the configuration has but whose subentry is gone, as while a change is applied."""
    runtime_data = await start(hass, entry(switch()))

    assert runtime_data._switch_device_id(SwitchSettings(OTHER_DEVICE_ID)) is None


# The status.


async def test_each_published_snapshot_goes_out_on_the_signal(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)
    snapshots = async_mock_signal(hass, SIGNAL_STATUS.format(config_entry.entry_id))
    await start(hass, config_entry)

    assert snapshots == []

    await tick(freezer, 0.5)

    ((snapshot,),) = snapshots
    assert (snapshot.service.state, snapshot.service.message) == (ServiceState.RUNNING, RUNNING)
    assert snapshot.lan.listening

    lan.broadcast(1)
    await tick(freezer, 0.5)

    assert len(snapshots) == 2
    assert snapshots[-1][0].switches[0].last_seen_at is not None


# Changes of the configuration.


async def test_a_switch_added_changed_and_removed_restarts_only_the_supervisor(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    desk = lights.add("desk")
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)
    entry_id = config_entry.entry_id
    configs = async_mock_signal(hass, SIGNAL_CONFIG_CHANGED.format(entry_id))
    added = async_mock_signal(hass, SIGNAL_SUBENTRY_ADDED.format(entry_id))
    updated = async_mock_signal(hass, SIGNAL_SUBENTRY_UPDATED.format(entry_id))
    removed = async_mock_signal(hass, SIGNAL_SUBENTRY_REMOVED.format(entry_id))
    lan.broadcast(1)
    lan.broadcast(2)
    lan.broadcast(7, OTHER_DEVICE_ID)
    await settle()
    listener, lan_task = runtime_data.listener, runtime_data._lan_task

    def kept(restarts: int) -> None:
        """The listener's socket and the LAN registry stay, and the supervisor restarted ``restarts`` times."""
        assert config_entry.state is ConfigEntryState.LOADED
        assert config_entry.runtime_data is runtime_data
        assert (runtime_data.listener, runtime_data._lan_task) == (listener, lan_task)
        assert [transport.closed for transport in lan.network.transports] == [False]
        assert [(device.device_id, device.seq) for device in runtime_data.registry.snapshot()] == [
            (OTHER_DEVICE_ID, 7),
            (DEVICE_ID, 2),
        ]
        assert logged(caplog, SUPERVISOR).count(RESTARTING) == restarts
        assert logged(caplog, RUNNER).count(LISTENING) == 1 + restarts

    # Added.
    desk_switch = new_subentry(OTHER_DEVICE_ID, "Desk", [lights.target(desk)])
    hass.config_entries.async_add_subentry(config_entry, desk_switch)
    await settle()

    kept(restarts=1)
    assert runtime_data.config == config_from_entry(config_entry)
    assert [switch.device_id for switch in runtime_data.config.switches] == [DEVICE_ID, OTHER_DEVICE_ID]
    snapshot = runtime_data.status.snapshot()
    assert snapshot.service.message == "2 wall switches toggle 2 lights."
    # The switch's last action stays.
    assert snapshot.switches[0].last_action is not None
    assert configs == [(runtime_data.config,)]
    assert added == [(desk_switch,)]

    # Changed.
    hass.config_entries.async_update_subentry(
        config_entry, desk_switch, data={**desk_switch.data, CONF_TARGETS: [CEILING, lights.target(desk)]}
    )
    await settle()

    kept(restarts=2)
    assert runtime_data.status.snapshot().service.message == "2 wall switches toggle 3 lights."
    assert updated == [(desk_switch,)]

    # A new title changes nothing that the switches run.
    hass.config_entries.async_update_subentry(config_entry, desk_switch, title="Desk switch")
    hass.config_entries.async_update_entry(config_entry, title="Lights")
    await settle()

    kept(restarts=2)
    assert len(configs) == 2
    assert updated == [(desk_switch,), (desk_switch,)]

    # Removed.
    hall = subentry_of(config_entry)
    hass.config_entries.async_remove_subentry(config_entry, hall.subentry_id)
    await settle()

    kept(restarts=3)
    assert [switch.device_id for switch in runtime_data.status.snapshot().switches] == [OTHER_DEVICE_ID]
    assert removed == [(hall.subentry_id,)]
    assert len(configs) == 3
    assert len(added) == 1


async def test_a_new_device_key_reaches_the_lan_monitor(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    """The LAN monitor takes a new tracker with the new configuration's keys."""
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)
    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    await settle()

    device = runtime_data.registry.find(DEVICE_ID)
    assert device is not None
    assert (device.detached, device.summary) == (None, None)

    subentry = subentry_of(config_entry)
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_DEVICE_KEY: DEVICE_KEY}
    )
    await settle()
    lan.broadcast(2, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    device = runtime_data.registry.find(DEVICE_ID)
    assert device is not None
    assert device.detached is False
    assert f"not_detached_{DEVICE_ID}" in issues(hass)


async def test_the_new_supervisor_starts_once_the_old_one_has_stopped(
    hass: HomeAssistant, lan: Lan, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    """Two changes while the first supervisor waits for the toggle that it stopped to end: the last configuration runs
    after both supervisors stopped, and the stopped toggle fires nothing."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    stubborn = Stubborn(hass)
    hass.states.async_set(CEILING, "off")
    config_entry = entry(switch())
    runtime_data = await start(hass, config_entry)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    subentry = subentry_of(config_entry)

    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_TARGETS: ["light.desk"]}
    )
    await settle()
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_TARGETS: ["light.lamp"]}
    )
    await settle()

    assert [task.done() for task in runtime_data._supervisor_tasks] == [False, True, False]
    assert runtime_data.status.snapshot().switches[0].targets == (CEILING,)
    assert logged(caplog, RUNNER).count(LISTENING) == 1

    stubborn.let_go.set()
    await settle()

    assert stubborn.ended
    assert [task.done() for task in runtime_data._supervisor_tasks] == [True, True, False]
    assert runtime_data.status.snapshot().switches[0].targets == ("light.lamp",)
    assert logged(caplog, RUNNER).count(LISTENING) == 2
    # Only the supervisor that ran says that it restarts.
    assert logged(caplog, SUPERVISOR).count(RESTARTING) == 1
    assert toggles == []


async def test_unloading_lets_a_supervisor_that_a_change_stopped_end(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    """It is cancelled once: unloading the entry waits for the toggle that it stopped to end."""
    stubborn = Stubborn(hass)
    hass.states.async_set(CEILING, "off")
    config_entry = entry(switch())
    await start(hass, config_entry)
    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    subentry = subentry_of(config_entry)
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_TARGETS: ["light.desk"]}
    )
    await settle()

    unloading = hass.async_create_task(hass.config_entries.async_unload(config_entry.entry_id))
    await settle()

    assert not unloading.done()

    stubborn.let_go.set()

    assert await unloading
    assert stubborn.ended
    assert lan.network.transports[0].closed


# The lights followed in the entity registry (decision 40).


async def test_a_light_given_another_entity_id_is_toggled_under_it_without_a_restart(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture
) -> None:
    """The switch keeps the light by its registry id: the switches are resolved again, and the runner goes on with its
    baselines."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    ceiling = lights.add("ceiling")
    config_entry = entry(switch(targets=[lights.target(ceiling)]))
    runtime_data = await start(hass, config_entry)
    lan.broadcast(1)
    await settle()

    rename(hass, ceiling, "light.hall_ceiling")
    await settle()
    lan.broadcast(2)
    await settle()

    assert lights.turned() == [("turn_on", "light.hall_ceiling")]
    assert runtime_data.status.snapshot().switches[0].targets == ("light.hall_ceiling",)
    assert f"{HALL} -> light.hall_ceiling" in logged(caplog, RUNNER)
    assert logged(caplog, RUNNER).count(LISTENING) == 1
    assert RESTARTING not in logged(caplog, SUPERVISOR)


async def test_a_removed_light_raises_a_repair_and_the_others_still_toggle_until_it_is_back(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    """Decision 32: the switches run on, the switch toggles the lights that Home Assistant has, and its issue, a
    warning, leads to its settings. A light added again has its registry id again."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    config_entry = entry(switch(targets=[lights.target(ceiling), lights.target(desk)]))
    runtime_data = await start(hass, config_entry)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    lan.broadcast(1)

    er.async_get(hass).async_remove(desk)
    hass.states.async_remove(desk)
    await tick(freezer, 0.5)

    removed = f"{HALL}: {desk} was removed from Home Assistant."
    service = runtime_data.status.service
    assert (service.state, service.message, service.problems) == (
        ServiceState.RUNNING,
        "1 wall switch toggles 1 light; 1 is missing.",
        (removed,),
    )
    issue = issues(hass)[f"missing_targets_{DEVICE_ID}"]
    assert (issue.translation_key, issue.severity, issue.is_fixable, issue.config_entry_id) == (
        "missing_targets",
        ir.IssueSeverity.WARNING,
        True,
        config_entry.entry_id,
    )
    assert issue.data == {"entry_id": config_entry.entry_id, "subentry_id": subentry_of(config_entry).subentry_id}
    assert issue.translation_placeholders == {"name": HALL, "problems": f"- {removed}"}
    await check_texts(hass, issue)

    lan.broadcast(2)
    await settle()

    assert lights.turned() == [("turn_on", ceiling)]

    lights.add("desk", on=True)
    await tick(freezer, 0.5)

    assert issues(hass) == {}
    assert runtime_data.status.service.message == "1 wall switch toggles 2 lights."

    lan.broadcast(3)
    await settle()

    assert sorted(lights.turned()) == [("turn_off", ceiling), ("turn_off", desk)]
    assert [event.data["targets"] for event in toggles] == [[ceiling], [ceiling, desk]]
    assert logged(caplog, SUPERVISOR) == [removed]
    assert logged(caplog, RUNNER).count(LISTENING) == 1


async def test_a_disabled_light_is_left_out_until_it_is_enabled(
    hass: HomeAssistant, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    config_entry = entry(switch(targets=[lights.target(ceiling), lights.target(desk)]))
    runtime_data = await start(hass, config_entry)
    registry = er.async_get(hass)
    lan.broadcast(1)

    registry.async_update_entity(desk, disabled_by=er.RegistryEntryDisabler.USER)
    await tick(freezer, 0.5)

    disabled = f"{HALL}: {desk} is disabled."
    assert runtime_data.status.service.problems == (disabled,)
    assert issues(hass)[f"missing_targets_{DEVICE_ID}"].translation_placeholders == {
        "name": HALL,
        "problems": f"- {disabled}",
    }

    lan.broadcast(2)
    await settle()

    assert lights.turned() == [("turn_on", ceiling)]

    registry.async_update_entity(desk, disabled_by=None)
    await tick(freezer, 0.5)

    assert issues(hass) == {}
    assert runtime_data.status.snapshot().switches[0].targets == (ceiling, desk)


# Repairs.


async def test_a_switch_not_in_detached_mode_and_one_that_cannot_be_decoded_raise_issues(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    config_entry = entry(switch(key=DEVICE_KEY))
    await start(hass, config_entry)

    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    issue = issues(hass)[f"not_detached_{DEVICE_ID}"]
    assert (issue.translation_key, issue.severity, issue.is_fixable, issue.data) == (
        "not_detached",
        ir.IssueSeverity.WARNING,
        False,
        None,
    )
    assert (issue.translation_placeholders, issue.config_entry_id) == ({"name": HALL}, config_entry.entry_id)
    await check_texts(hass, issue)

    lan.broadcast(2, params=relay(detached=True), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    assert issues(hass) == {}

    lan.broadcast(3)  # an IV that isn't base64
    await tick(freezer, 0.5)

    issue = issues(hass)[f"decode_error_{DEVICE_ID}"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "decode_error",
        ir.IssueSeverity.WARNING,
        False,
    )
    assert issue.translation_placeholders == {"name": HALL, "error": "iv is not valid base64"}
    await check_texts(hass, issue)

    lan.broadcast(4, params=relay(detached=True), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    assert issues(hass) == {}


async def test_the_issues_of_a_removed_switch_go_with_it_and_all_go_at_unload(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    config_entry = entry(switch(key=DEVICE_KEY), switch(OTHER_DEVICE_ID, None, key=DEVICE_KEY))
    await start(hass, config_entry)
    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    lan.broadcast(1, OTHER_DEVICE_ID, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    assert set(issues(hass)) == {f"not_detached_{DEVICE_ID}", f"not_detached_{OTHER_DEVICE_ID}"}
    assert issues(hass)[f"not_detached_{OTHER_DEVICE_ID}"].translation_placeholders == {"name": OTHER_DEVICE_ID}

    hass.config_entries.async_remove_subentry(config_entry, subentry_of(config_entry).subentry_id)
    await settle()
    await tick(freezer, 0.5)

    assert set(issues(hass)) == {f"not_detached_{OTHER_DEVICE_ID}"}

    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert issues(hass) == {}


async def test_a_configuration_with_errors_raises_an_issue_until_it_is_fixed(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    config_entry = entry(switch(targets=[" "]), switch(OTHER_DEVICE_ID, None, targets=[CEILING, ""]))
    await start(hass, config_entry)

    issue = issues(hass)["invalid_config"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "invalid_config",
        ir.IssueSeverity.ERROR,
        False,
    )
    assert issue.translation_placeholders == {
        "message": "The configuration has errors.",
        "problems": "- switches[0] ('Hall'): empty light.\n- switches[1]: empty light.",
    }
    await check_texts(hass, issue)

    for subentry in config_entry.subentries.values():
        hass.config_entries.async_update_subentry(
            config_entry, subentry, data={**subentry.data, CONF_TARGETS: [CEILING]}
        )
    # The switches start again, then the status shows them.
    await settle()
    await tick(freezer, 0.5)

    assert issues(hass) == {}


async def test_a_migrated_switchs_lights_without_a_light_raise_its_issue_until_it_is_saved(
    hass: HomeAssistant, lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """The lights, rooms and zones of version 1 that the migration found no light for (decision 40). Saving the switch
    drops them, which changes nothing that the switches run."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    config_entry = entry(switch(extra={CONF_UNMIGRATED: ["Pendant", "room:Hall"]}), switch(OTHER_DEVICE_ID, "Porch"))
    await start(hass, config_entry)

    assert set(issues(hass)) == {f"unmigrated_{DEVICE_ID}"}
    issue = issues(hass)[f"unmigrated_{DEVICE_ID}"]
    assert (issue.translation_key, issue.severity, issue.is_fixable, issue.config_entry_id) == (
        "unmigrated",
        ir.IssueSeverity.WARNING,
        True,
        config_entry.entry_id,
    )
    assert issue.data == {"entry_id": config_entry.entry_id, "subentry_id": subentry_of(config_entry).subentry_id}
    assert issue.translation_placeholders == {"name": HALL, "entries": "- `Pendant`\n- `room:Hall`"}
    await check_texts(hass, issue)

    subentry = subentry_of(config_entry)
    data = {key: value for key, value in subentry.data.items() if key != CONF_UNMIGRATED}
    hass.config_entries.async_update_subentry(config_entry, subentry, data=data)
    await settle()

    assert issues(hass) == {}
    assert RESTARTING not in logged(caplog, SUPERVISOR)


# The switch test.


async def test_the_switch_test_toggles_and_logs(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    desk = lights.add("desk")
    runtime_data = await start(hass, entry(switch(targets=[CEILING, lights.target(desk)])))
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)

    result = await runtime_data.async_test_toggle(f" {DEVICE_ID.upper()} ")

    assert result == SwitchTestResult(True, 2, 0, 0, 0, (CEILING, desk))
    assert sorted(lights.turned()) == [("turn_on", CEILING), ("turn_on", desk)]
    assert logged(caplog, INTEGRATION_LOGGER.name) == [f"{HALL}: test from Home Assistant turned ON {CEILING}, {desk}."]

    # It takes as long as its lights take.
    held = lights.hold(desk)
    testing = hass.async_create_task(runtime_data.async_test_toggle(DEVICE_ID))
    await tick(freezer, 0.25)
    held.set()
    result = await testing

    assert result == SwitchTestResult(False, 2, 0, 0, 250, (CEILING, desk))
    # The test isn't a press: no events, and no last action.
    await settle()
    assert (presses, toggles) == ([], [])
    assert runtime_data.status.snapshot().switches[0].last_action is None


async def test_the_switch_test_counts_and_logs_a_light_that_fails(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    desk = lights.add("desk")
    runtime_data = await start(hass, entry(switch(targets=[CEILING, desk])))
    lights.fail(CEILING, "Request failed: the light didn't answer")

    result = await runtime_data.async_test_toggle(DEVICE_ID)

    assert result == SwitchTestResult(True, 1, 0, 1, 0, (CEILING, desk))
    assert logged(caplog, INTEGRATION_LOGGER.name) == [
        f"Could not switch {CEILING} on: Request failed: the light didn't answer",
        f"{HALL}: test from Home Assistant turned ON {CEILING}, {desk}.",
    ]


@pytest.mark.parametrize(
    ("device_id", "message"),
    [
        (OTHER_DEVICE_ID, f"No switch with device id {OTHER_DEVICE_ID} is configured."),
        (" ", "No switch with device id   is configured."),
    ],
    ids=["unknown switch", "blank id"],
)
async def test_the_switch_test_refuses_a_switch_that_isnt_configured(
    hass: HomeAssistant, lan: Lan, lights: Lights, device_id: str, message: str
) -> None:
    lights.add("ceiling")
    runtime_data = await start(hass, entry(switch()))

    with pytest.raises(SwitchTestError) as caught:
        await runtime_data.async_test_toggle(device_id)

    assert (str(caught.value), caught.value.problems) == (message, ())
    assert lights.turned() == []


async def test_the_switch_test_names_the_lights_that_home_assistant_doesnt_have(
    hass: HomeAssistant, lan: Lan, lights: Lights
) -> None:
    """When it has none of them: the runner would toggle nothing either."""
    desk = lights.add("desk")
    er.async_get(hass).async_update_entity(desk, disabled_by=er.RegistryEntryDisabler.USER)
    runtime_data = await start(hass, entry(switch(targets=[UNKNOWN_TARGET, lights.target(desk)])))

    with pytest.raises(SwitchTestError) as caught:
        await runtime_data.async_test_toggle(DEVICE_ID)

    problems = (f"{HALL}: a light was removed from Home Assistant.", f"{HALL}: {desk} is disabled.")
    assert caught.value.problems == problems
    assert str(caught.value) == " ".join(problems)
    assert lights.turned() == []


async def test_the_switch_test_of_a_switch_without_lights_says_so(hass: HomeAssistant, lan: Lan) -> None:
    runtime_data = await start(hass, entry(switch(targets=[])))

    with pytest.raises(SwitchTestError) as caught:
        await runtime_data.async_test_toggle(DEVICE_ID)

    assert (str(caught.value), caught.value.problems) == (NO_TARGETS, ())


LIGHT_REMOVED = "a light was removed from Home Assistant."
DESK_REMOVED = "light.desk was removed from Home Assistant."


@pytest.mark.parametrize(
    ("unknown", "missing", "line"),
    [
        ([], [DESK_REMOVED], f"1 is missing: {DESK_REMOVED}"),
        ([UNKNOWN_TARGET], [LIGHT_REMOVED, DESK_REMOVED], f"2 are missing: {LIGHT_REMOVED}; {DESK_REMOVED}"),
    ],
    ids=["one", "two"],
)
async def test_the_switch_test_toggles_the_lights_that_home_assistant_has_and_names_the_others(
    hass: HomeAssistant,
    lan: Lan,
    lights: Lights,
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
    unknown: list[str],
    missing: list[str],
    line: str,
) -> None:
    """As the runner does (decision 32): the result and the log line name what is missing."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    desk = lights.add("desk")
    desk_target = lights.target(desk)
    er.async_get(hass).async_remove(desk)
    runtime_data = await start(hass, entry(switch(targets=[CEILING, *unknown, desk_target])))
    caplog.clear()

    result = await runtime_data.async_test_toggle(DEVICE_ID)

    problems = tuple(f"{HALL}: {problem}" for problem in missing)
    assert result == SwitchTestResult(True, 1, 0, 0, 0, (CEILING,), problems)
    assert lights.turned() == [("turn_on", CEILING)]
    assert logged(caplog, INTEGRATION_LOGGER.name) == [f"{HALL}: test from Home Assistant turned ON {CEILING}; {line}"]


# The activity.


async def test_the_activity_has_what_the_integration_logs_at_its_level(
    hass: HomeAssistant, lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """Home Assistant's level decides, as for its own log: warnings by default, info and debug lines when asked."""
    caplog.set_level(logging.WARNING, logger=INTEGRATION_LOGGER.name)
    runtime_data = await start(hass, entry(switch(targets=[UNKNOWN_TARGET])))

    entries = runtime_data.activity.get(0, 1000)
    assert [(entry.level, entry.category, entry.message) for entry in entries] == [
        ("warning", "supervisor", f"{HALL}: a light was removed from Home Assistant.")
    ]

    caplog.set_level(logging.DEBUG, logger=INTEGRATION_LOGGER.name)
    subentry = subentry_of(runtime_data.entry)
    hass.config_entries.async_update_subentry(
        runtime_data.entry, subentry, data={**subentry.data, CONF_TARGETS: [CEILING]}
    )
    await settle()
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()

    later = [(entry.level, entry.category, entry.message) for entry in runtime_data.activity.get(entries[-1].id, 1000)]
    assert ("information", "supervisor", RESTARTING) in later
    assert ("information", "runner", LISTENING) in later
    assert later[-1] == (
        "debug",
        "runner",
        f"Ignoring unconfigured eWeLink device {OTHER_DEVICE_ID} at {DEVICE_ADDRESS}.",
    )
