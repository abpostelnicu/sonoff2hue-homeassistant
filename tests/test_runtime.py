"""The runtime of the config entry (decision 23), in Home Assistant: the LAN on a fake network and a Hue bridge in memory,
or the fake Hue bridge over HTTPS on 127.0.0.1, with time moved by ``freezer``."""

import asyncio
from datetime import timedelta
import logging
import string
from types import MappingProxyType
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.repairs import FlowType, repairs_flow_manager
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState, ConfigSubentry, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, async_mock_signal
from pytest_homeassistant.components.repairs import process_repair_fix_flow, start_repair_fix_flow
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import repairs, runtime
from custom_components.sonoff2hue.const import (
    CONF_APP_KEY,
    CONF_CERTIFICATE_SHA256,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
    EVENT_PRESS,
    EVENT_TOGGLE,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine import mdns, toggler
from custom_components.sonoff2hue.engine.config import EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import CERTIFICATE_MISMATCH
from custom_components.sonoff2hue.engine.status import PRESS_DROPPED, ServiceState
from custom_components.sonoff2hue.engine.supervisor import KEY_REJECTED
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
from tools.fake_hue.bridge import BRIDGE_ID as FAKE_BRIDGE_ID

from .engine.fakes import until
from .fakes import (
    APP_KEY,
    BRIDGE_ADDRESS,
    BRIDGE_ID,
    DEVICE_ID,
    DEVICE_KEY,
    OTHER_DEVICE_ID,
    PIN,
    Bridge,
    Lan,
    paired,
    relay,
    switch,
)
from .hue import FakeHueHost

RUNNING = "1 wall switch toggles 1 Hue light, room or zone."
BRIDGE_DOWN = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/bridge"
RESTARTING = "Configuration changed; restarting the bridge."
UNAUTHORIZED = '{"errors":[{"description":"unauthorized user"}]}'
HALL = "Hall (1000abcdef)"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggler retries a failed request at once, instead of after 300 ms."""
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


async def settle() -> None:
    """Lets the event loop run what is ready: with the LAN and the bridge in memory, nothing waits for I/O."""
    for _ in range(20):
        await asyncio.sleep(0)


async def tick(freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Moves the frozen time, which the event loop's clock follows, and runs the timers that are due. The loop runs a
    timer once its clock is past it by its resolution, 1 ns, which a monotonic clock of epoch seconds can't show, so
    time moves a microsecond more."""
    freezer.tick(timedelta(seconds=seconds, microseconds=1))
    await settle()


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> Sonoff2HueData:
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle()
    return entry.runtime_data


def subentry_of(entry: MockConfigEntry, device_id: str = DEVICE_ID) -> ConfigSubentry:
    return next(subentry for subentry in entry.subentries.values() if subentry.unique_id == device_id)


def new_subentry(device_id: str, name: str, lights: list[str]) -> ConfigSubentry:
    data = {CONF_DEVICE_ID: device_id, CONF_NAME: name, CONF_DEVICE_KEY: None, CONF_LIGHTS: lights, CONF_GROUPS: []}
    return ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_SWITCH, title=name, unique_id=device_id)


def logged(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == logger]


def reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [flow for flow in hass.config_entries.flow.async_progress() if flow["context"]["source"] == SOURCE_REAUTH]


def issues(hass: HomeAssistant) -> dict[str, ir.IssueEntry]:
    return {issue_id: issue for (domain, issue_id), issue in ir.async_get(hass).issues.items() if domain == DOMAIN}


async def check_texts(hass: HomeAssistant, issue: ir.IssueEntry) -> None:
    """The issue's texts are in ``translations/en.json``, with the placeholders they use."""
    texts = await async_get_translations(hass, "en", "issues", [DOMAIN])
    prefix = f"component.{DOMAIN}.issues.{issue.translation_key}"
    keys = [f"{prefix}.title"]
    if issue.is_fixable:
        keys += [f"{prefix}.fix_flow.step.confirm.title", f"{prefix}.fix_flow.step.confirm.description"]
        keys += [f"{prefix}.fix_flow.abort.reconfigure", f"{prefix}.fix_flow.abort.not_loaded"]
    else:
        keys.append(f"{prefix}.description")
    given = set(issue.translation_placeholders or {})
    for key in keys:
        assert key in texts, f"{key} is missing from translations/en.json"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the issue doesn't give"


# The configuration.


def test_the_configuration_is_the_entrys_data_and_its_switches_as_stored() -> None:
    entry = paired(
        switch(" 1000ABCDEF ", None, lights=[" ceiling ", None], groups=["room:Hall"], key=DEVICE_KEY),
        ConfigSubentryData(data={}, subentry_type="other", title="Other", unique_id=None),
        switch(OTHER_DEVICE_ID, "Desk", lights=["Desk"]),
        ConfigSubentryData(data={CONF_DEVICE_ID: None}, subentry_type=SUBENTRY_SWITCH, title="", unique_id=None),
    )

    assert config_from_entry(entry) == EngineConfig(
        HueSettings(BRIDGE_ADDRESS, BRIDGE_ID, APP_KEY, PIN),
        [
            SwitchSettings(" 1000ABCDEF ", None, DEVICE_KEY, [" ceiling ", None], ["room:Hall"]),
            SwitchSettings(OTHER_DEVICE_ID, "Desk", None, ["Desk"], []),
            SwitchSettings("", None, None, [], []),
        ],
    )
    assert config_from_entry(MockConfigEntry(domain=DOMAIN)) == EngineConfig()


# Starting and stopping.


def test_the_runtimes_listener_is_on_the_network() -> None:
    """Unless a test gives it the fake LAN."""
    listener = runtime.new_listener(dt_util.utcnow)

    assert listener._transport_factory is mdns.open_multicast_transport
    assert listener._network_watcher is mdns.watch_network_changes


async def test_a_runtime_that_never_started_stops_at_once(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    """As when the entry's setup fails before the runtime starts."""
    entry = paired(switch())
    entry.add_to_hass(hass)
    runtime_data = Sonoff2HueData.create(hass, entry)

    await runtime_data.async_stop()

    assert (lan.network.transports, bridge.created) == ([], 0)
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers


async def test_the_runtime_runs_the_lan_monitor_and_the_bridge_until_it_unloads(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    entry = paired(switch())
    runtime_data = await start(hass, entry)

    (transport,) = lan.network.transports
    assert not transport.closed
    assert lan.network.watching
    assert runtime_data.registry.listening
    snapshot = runtime_data.status.snapshot()
    assert (snapshot.service.state, snapshot.service.message) == (ServiceState.RUNNING, RUNNING)
    assert snapshot.hue.reachable is True
    assert runtime_data.config == config_from_entry(entry)
    assert runtime_data.activity in INTEGRATION_LOGGER.handlers
    assert (bridge.created, bridge.closed) == (1, 0)

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert transport.closed
    assert not lan.network.watching
    assert not runtime_data.registry.listening
    assert (bridge.created, bridge.closed) == (1, 1)
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_a_reload_starts_a_new_runtime_after_the_old_one_stopped(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    entry = paired(switch())
    first = await start(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle()

    second = entry.runtime_data
    assert second is not first
    assert [transport.closed for transport in lan.network.transports] == [True, False]
    assert (bridge.created, bridge.closed) == (2, 1)
    assert second.status.snapshot().service.state is ServiceState.RUNNING
    assert INTEGRATION_LOGGER.handlers.count(second.activity) == 1
    assert first.activity not in INTEGRATION_LOGGER.handlers


async def test_home_assistant_stopping_ends_the_runtime(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    runtime_data = await start(hass, paired(switch()))

    await hass.async_stop()
    # Home Assistant doesn't wait for the tasks it cancels when it stops.
    await settle()

    assert lan.network.transports[0].closed
    assert (bridge.created, bridge.closed) == (1, 1)
    assert runtime_data.activity not in INTEGRATION_LOGGER.handlers
    # Stopped: no reauthentication, and no change is applied.
    runtime_data._on_key_rejected()
    runtime_data.async_config_changed()
    assert bridge.created == 1


# Presses, toggles and their events.


async def test_a_press_toggles_the_lights_and_fires_the_events(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch())
    runtime_data = await start(hass, entry)
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    # The events name the switch's device, which the platforms made for its subentry, so the logbook finds them.
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)
    assert device is not None
    assert device.config_subentry_id == subentry_of(entry).subentry_id
    hall = {"device_id": device.id, "switch_id": DEVICE_ID, "name": "Hall"}

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert bridge.puts() == [("l1", True)]
    assert [event.data for event in presses] == [{**hall, "seq": 2}]
    assert [event.data for event in toggles] == [
        {
            **hall,
            "result": "toggled",
            "targets": ["light 'Ceiling'"],
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

    bridge.fail("/clip/v2/resource/light", 503)
    lan.broadcast(3)
    await settle()

    assert presses[-1].data == {**hall, "seq": 3}
    assert toggles[-1].data == {
        **hall,
        "result": "failed",
        "targets": ["light 'Ceiling'"],
        "message": "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light",
    }

    # A press that waits more than 5 seconds for the bridge is dropped.
    bridge.heal()
    held = bridge.hold("/clip/v2/resource/light")
    lan.broadcast(4)
    lan.broadcast(5)
    await settle()
    await tick(freezer, 6)
    held.set()
    await settle()

    assert [event.data["seq"] for event in presses] == [2, 3, 4, 5]
    assert [event.data["result"] for event in toggles] == ["toggled", "failed", "toggled", "dropped"]
    assert toggles[-1].data == {**hall, "result": "dropped", "targets": ["light 'Ceiling'"], "message": PRESS_DROPPED}
    assert bridge.puts() == [("l1", True), ("l1", False)]


async def test_a_switch_without_a_subentry_names_no_device(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    """A switch that the configuration has but whose subentry is gone, as while a change is applied."""
    runtime_data = await start(hass, paired(switch()))

    assert runtime_data._switch_device_id(SwitchSettings(OTHER_DEVICE_ID)) is None


# The status and the bridge across time.


async def test_each_published_snapshot_goes_out_on_the_signal(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch())
    entry.add_to_hass(hass)
    snapshots = async_mock_signal(hass, SIGNAL_STATUS.format(entry.entry_id))
    await start(hass, entry)

    assert snapshots == []

    await tick(freezer, 0.5)

    ((snapshot,),) = snapshots
    assert (snapshot.service.state, snapshot.service.message) == (ServiceState.RUNNING, RUNNING)
    assert snapshot.lan.listening

    lan.broadcast(1)
    await tick(freezer, 0.5)

    assert len(snapshots) == 2
    assert snapshots[-1][0].switches[0].last_seen_at is not None


async def test_the_bridge_is_waited_for_and_watched_across_time(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    bridge.fail("/clip/v2/resource/bridge", 503)
    runtime_data = await start(hass, paired(switch()))
    status = runtime_data.status

    waiting = f"Waiting for the Hue bridge at {BRIDGE_ADDRESS}: {BRIDGE_DOWN}"
    assert (status.service.state, status.service.message) == (ServiceState.CONNECTING, waiting)
    assert (status.snapshot().hue.reachable, status.snapshot().hue.message) == (False, BRIDGE_DOWN)

    await tick(freezer, 10)

    assert status.service.state is ServiceState.CONNECTING
    assert sum(request.path == "/clip/v2/resource/bridge" for request in bridge.requests) == 2

    bridge.heal()
    await tick(freezer, 10)

    assert (status.service.state, status.service.message) == (ServiceState.RUNNING, RUNNING)
    assert (status.snapshot().hue.reachable, status.snapshot().hue.message) == (True, None)

    # The runner's keep-alive, every 45 seconds, watches the bridge; the state stays running, as in .NET.
    bridge.fail("/clip/v2/resource/bridge", 503)
    await tick(freezer, 45)

    assert (status.snapshot().hue.reachable, status.snapshot().hue.message) == (False, BRIDGE_DOWN)
    assert status.service.state is ServiceState.RUNNING

    bridge.heal()
    await tick(freezer, 45)

    assert status.snapshot().hue.reachable is True


# Changes of the configuration.


async def test_a_switch_added_changed_and_removed_restarts_only_the_supervisor(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    entry = paired(switch())
    runtime_data = await start(hass, entry)
    entry_id = entry.entry_id
    configs = async_mock_signal(hass, SIGNAL_CONFIG_CHANGED.format(entry_id))
    added = async_mock_signal(hass, SIGNAL_SUBENTRY_ADDED.format(entry_id))
    updated = async_mock_signal(hass, SIGNAL_SUBENTRY_UPDATED.format(entry_id))
    removed = async_mock_signal(hass, SIGNAL_SUBENTRY_REMOVED.format(entry_id))
    lan.broadcast(1)
    lan.broadcast(2)
    lan.broadcast(7, OTHER_DEVICE_ID)
    await settle()
    listener, lan_task = runtime_data.listener, runtime_data._lan_task
    supervisor = "custom_components.sonoff2hue.engine.supervisor"

    def kept() -> None:
        """The listener's socket, the LAN registry and the switch's last action stay."""
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is runtime_data
        assert (runtime_data.listener, runtime_data._lan_task) == (listener, lan_task)
        assert [transport.closed for transport in lan.network.transports] == [False]
        assert [(device.device_id, device.seq) for device in runtime_data.registry.snapshot()] == [
            (OTHER_DEVICE_ID, 7),
            (DEVICE_ID, 2),
        ]

    # Added.
    desk = new_subentry(OTHER_DEVICE_ID, "Desk", ["Desk"])
    hass.config_entries.async_add_subentry(entry, desk)
    await settle()

    kept()
    assert logged(caplog, supervisor).count(RESTARTING) == 1
    assert (bridge.created, bridge.closed) == (2, 1)
    assert runtime_data.config == config_from_entry(entry)
    assert [switch.device_id for switch in runtime_data.config.switches] == [DEVICE_ID, OTHER_DEVICE_ID]
    snapshot = runtime_data.status.snapshot()
    assert snapshot.service.message == "2 wall switches toggle 2 Hue lights, rooms and zones."
    assert snapshot.switches[0].last_action is not None
    assert configs == [(runtime_data.config,)]
    assert added == [(desk,)]

    # Changed.
    hass.config_entries.async_update_subentry(entry, desk, data={**desk.data, CONF_LIGHTS: ["Ceiling", "Desk"]})
    await settle()

    kept()
    assert logged(caplog, supervisor).count(RESTARTING) == 2
    assert (bridge.created, bridge.closed) == (3, 2)
    assert runtime_data.status.snapshot().service.message == "2 wall switches toggle 3 Hue lights, rooms and zones."
    assert updated == [(desk,)]

    # A new title changes nothing that the supervisor runs.
    hass.config_entries.async_update_subentry(entry, desk, title="Desk switch")
    hass.config_entries.async_update_entry(entry, title="Hue")
    await settle()

    assert (bridge.created, bridge.closed) == (3, 2)
    assert len(configs) == 2
    assert updated == [(desk,), (desk,)]

    # Removed.
    hall = subentry_of(entry)
    hass.config_entries.async_remove_subentry(entry, hall.subentry_id)
    await settle()

    kept()
    assert logged(caplog, supervisor).count(RESTARTING) == 3
    assert (bridge.created, bridge.closed) == (4, 3)
    assert [switch.device_id for switch in runtime_data.status.snapshot().switches] == [OTHER_DEVICE_ID]
    assert removed == [(hall.subentry_id,)]
    assert len(configs) == 3
    assert len(added) == 1


async def test_a_new_device_key_reaches_the_lan_monitor(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """The LAN monitor takes a new tracker with the new configuration's keys, as .NET's ``OnConfigChanged`` did."""
    entry = paired(switch())
    runtime_data = await start(hass, entry)
    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    await settle()

    device = runtime_data.registry.find(DEVICE_ID)
    assert device is not None
    assert (device.detached, device.summary) == (None, None)

    subentry = subentry_of(entry)
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_DEVICE_KEY: DEVICE_KEY})
    await settle()
    lan.broadcast(2, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    device = runtime_data.registry.find(DEVICE_ID)
    assert device is not None
    assert device.detached is False
    assert f"not_detached_{DEVICE_ID}" in issues(hass)


async def test_the_new_supervisor_starts_once_the_old_one_has_closed_its_client(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    entry = paired(switch())
    runtime_data = await start(hass, entry)
    closing = asyncio.Event()
    close = bridge.close

    async def slow_close() -> None:
        await closing.wait()
        await close()

    bridge.close = slow_close  # type: ignore[method-assign]
    subentry = subentry_of(entry)

    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_LIGHTS: ["Desk"]})
    await settle()
    # Two changes while the first supervisor closes its client: the last configuration runs after both stopped.
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_LIGHTS: [], CONF_GROUPS: ["Hall"]}
    )
    await settle()

    assert (bridge.created, bridge.closed) == (1, 0)
    assert len(runtime_data._supervisor_tasks) == 3

    closing.set()
    await settle()

    assert (bridge.created, bridge.closed) == (2, 1)
    assert runtime_data.status.snapshot().switches[0].targets == ("group 'Hall'",)
    assert [task.done() for task in runtime_data._supervisor_tasks] == [True, True, False]


async def test_unloading_lets_a_stopping_supervisor_close_its_client(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    """A supervisor that a change stopped is cancelled once: unloading the entry waits for it to close its client."""
    entry = paired(switch())
    await start(hass, entry)
    closing = asyncio.Event()
    close = bridge.close

    async def slow_close() -> None:
        await closing.wait()
        await close()

    bridge.close = slow_close  # type: ignore[method-assign]
    subentry = subentry_of(entry)
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_LIGHTS: ["Desk"]})
    await settle()

    unloading = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    await settle()

    assert not unloading.done()

    closing.set()

    assert await unloading
    assert (bridge.created, bridge.closed) == (1, 1)
    assert lan.network.transports[0].closed


# Reauthentication.


async def test_a_key_rejected_at_the_start_starts_one_reauthentication(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    bridge.fail("/clip/v2/resource/bridge", 403, UNAUTHORIZED)
    entry = paired(switch())
    runtime_data = await start(hass, entry)
    await hass.async_block_till_done()

    assert (runtime_data.status.service.state, runtime_data.status.service.message) == (
        ServiceState.ERROR,
        KEY_REJECTED,
    )
    (flow,) = reauth_flows(hass)
    assert (flow["step_id"], flow["context"]["entry_id"]) == ("reauth_confirm", entry.entry_id)

    # The supervisor starts again after a change, and the bridge rejects the key again: still one.
    subentry = subentry_of(entry)
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_LIGHTS: ["Desk"]})
    await settle()
    await hass.async_block_till_done()

    assert bridge.created == 2
    assert len(reauth_flows(hass)) == 1


async def test_a_key_rejected_while_running_starts_one_reauthentication(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    runtime_data = await start(hass, paired(switch()))
    bridge.fail("/clip/v2/resource/bridge", 401, UNAUTHORIZED)

    await tick(freezer, 45)
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1

    await tick(freezer, 45)
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1
    # The bridge goes on running and is shown as unreachable, as in .NET.
    assert runtime_data.status.service.state is ServiceState.RUNNING
    assert runtime_data.status.snapshot().hue.reachable is False


@pytest.mark.usefixtures("lan")
async def test_reauthentication_restarts_the_bridge_with_the_new_key(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    """Over HTTPS with the fake bridge, which knows no key "revoked": no reload, the supervisor starts again."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    entry = paired(
        switch(lights=["Hallway"]),
        address=fake_hue_pressed.address,
        app_key="revoked",
        pin=fake_hue_pressed.fingerprint,
        bridge_id=FAKE_BRIDGE_ID,
    )
    runtime_data = await start(hass, entry)
    await until(lambda: runtime_data.status.service.state is ServiceState.ERROR)
    await hass.async_block_till_done()
    (flow,) = reauth_flows(hass)

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reauth_successful")
    await until(lambda: runtime_data.status.service.state is ServiceState.RUNNING)
    assert entry.runtime_data is runtime_data
    assert fake_hue_pressed.bridge.is_authorized(entry.data[CONF_APP_KEY])
    assert runtime_data.config.hue.app_key == entry.data[CONF_APP_KEY]
    # The supervisor that waited for a change after the rejected key restarted, as in .NET.
    assert logged(caplog, "custom_components.sonoff2hue.engine.supervisor").count(RESTARTING) == 1
    assert reauth_flows(hass) == []


@pytest.mark.usefixtures("lan")
async def test_reconfiguring_restarts_the_bridge_without_a_reload(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost
) -> None:
    app_key = await fake_hue_pressed.pair()
    entry = paired(
        switch(lights=["Hallway"]),
        address=fake_hue_pressed.address,
        app_key=app_key,
        pin=fake_hue_pressed.fingerprint,
        bridge_id=FAKE_BRIDGE_ID,
    )
    runtime_data = await start(hass, entry)
    await until(lambda: runtime_data.status.service.state is ServiceState.RUNNING)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "address"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": fake_hue_pressed.address})
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure_successful")
    assert entry.data[CONF_APP_KEY] != app_key
    assert runtime_data.config.hue.app_key == entry.data[CONF_APP_KEY]
    # A new key: the bridge's reachability starts over, and the new supervisor reaches the bridge with it.
    assert runtime_data.status.snapshot().hue.reachable is None
    await until(lambda: runtime_data.status.snapshot().hue.reachable is True)
    await until(lambda: runtime_data.status.service.state is ServiceState.RUNNING)
    assert entry.runtime_data is runtime_data
    assert [task.done() for task in runtime_data._supervisor_tasks] == [True, False]


# Repairs.


async def test_a_switch_not_in_detached_mode_and_one_that_cannot_be_decoded_raise_issues(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch(key=DEVICE_KEY))
    await start(hass, entry)

    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    issue = issues(hass)[f"not_detached_{DEVICE_ID}"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "not_detached",
        ir.IssueSeverity.WARNING,
        False,
    )
    assert (issue.translation_placeholders, issue.config_entry_id) == ({"name": HALL}, entry.entry_id)
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


async def test_the_issues_of_a_removed_switch_go_with_it(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch(key=DEVICE_KEY), switch(OTHER_DEVICE_ID, None, key=DEVICE_KEY))
    await start(hass, entry)
    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    lan.broadcast(1, OTHER_DEVICE_ID, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    assert set(issues(hass)) == {f"not_detached_{DEVICE_ID}", f"not_detached_{OTHER_DEVICE_ID}"}
    assert issues(hass)[f"not_detached_{OTHER_DEVICE_ID}"].translation_placeholders == {"name": OTHER_DEVICE_ID}

    hass.config_entries.async_remove_subentry(entry, subentry_of(entry).subentry_id)
    await settle()
    await tick(freezer, 0.5)

    assert set(issues(hass)) == {f"not_detached_{OTHER_DEVICE_ID}"}

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert issues(hass) == {}


async def test_lights_rooms_or_zones_not_on_the_bridge_raise_an_issue_until_they_are(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch(lights=["Kitchen"]))
    await start(hass, entry)
    await tick(freezer, 0.5)

    issue = issues(hass)["invalid_config"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "invalid_config",
        ir.IssueSeverity.ERROR,
        False,
    )
    assert issue.translation_placeholders == {
        "message": "Some lights, rooms or zones were not found on the Hue bridge.",
        "problems": f"- {HALL}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Desk'",
    }
    await check_texts(hass, issue)

    subentry = subentry_of(entry)
    hass.config_entries.async_update_subentry(entry, subentry, data={**subentry.data, CONF_LIGHTS: ["Ceiling"]})
    await settle()
    await tick(freezer, 0.5)

    assert issues(hass) == {}


async def test_an_unpinned_certificate_raises_an_issue_until_one_is_pinned(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch(), pin=None)
    await start(hass, entry)

    issue = issues(hass)["certificate_not_pinned"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "certificate_not_pinned",
        ir.IssueSeverity.WARNING,
        True,
    )
    assert (issue.data, issue.translation_placeholders) == ({"entry_id": entry.entry_id}, {})
    await check_texts(hass, issue)

    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CERTIFICATE_SHA256: PIN})
    await settle()
    await tick(freezer, 0.5)

    assert issues(hass) == {}


async def test_an_unpaired_bridge_raises_no_certificate_issue(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    await start(hass, paired(switch(), app_key=None, pin=None))

    assert issues(hass) == {}


async def test_a_changed_certificate_raises_an_issue_until_the_bridge_is_paired_again(
    hass: HomeAssistant, lan: Lan, fake_hue_pressed: FakeHueHost
) -> None:
    """Over HTTPS with the fake bridge, whose certificate isn't the pinned one."""
    app_key = await fake_hue_pressed.pair()
    entry = paired(
        switch(lights=["Hallway"]),
        address=fake_hue_pressed.address,
        app_key=app_key,
        pin="00" * 32,
        bridge_id=FAKE_BRIDGE_ID,
    )
    runtime_data = await start(hass, entry)
    await until(lambda: "certificate_changed" in issues(hass))

    assert runtime_data.status.snapshot().hue.message == CERTIFICATE_MISMATCH
    issue = issues(hass)["certificate_changed"]
    assert (issue.translation_key, issue.severity, issue.is_fixable) == (
        "certificate_changed",
        ir.IssueSeverity.ERROR,
        True,
    )
    await check_texts(hass, issue)

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CERTIFICATE_SHA256: fake_hue_pressed.fingerprint}
    )
    await until(lambda: issues(hass) == {})

    assert runtime_data.status.service.state is ServiceState.RUNNING


async def test_an_unexpected_configuration_problem_shows_its_message(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    entry = paired(switch(lights=[None]))
    await start(hass, entry)
    await tick(freezer, 0.5)

    assert issues(hass)["invalid_config"].translation_placeholders == {
        "message": "The configuration has errors.",
        "problems": "- switches[0] ('Hall'): empty light/group name.",
    }


# Repair flows.


@pytest.mark.usefixtures("lan", "bridge")
async def test_the_repair_leads_to_the_reconfigure_flow(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = paired(switch(), pin=None)
    await start(hass, entry)
    manager = repairs_flow_manager(hass)
    assert manager is not None

    result = await manager.async_init(DOMAIN, context={"issue_id": "certificate_not_pinned"})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "confirm")

    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "reconfigure")
    flow_type, flow_id = result["next_flow"]
    assert flow_type is FlowType.CONFIG_FLOW
    flow = hass.config_entries.flow.async_get(flow_id)
    assert (flow["context"]["source"], flow["context"]["entry_id"], flow["step_id"]) == (
        "reconfigure",
        entry.entry_id,
        "reconfigure",
    )
    # The issue stays until the bridge is paired again.
    assert "certificate_not_pinned" in issues(hass)


@pytest.mark.usefixtures("lan", "bridge", "socket_enabled")
async def test_the_frontend_gets_the_reconfigure_flow_as_the_next_flow(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    await start(hass, paired(switch(), pin=None))
    client = await hass_client()

    result = await start_repair_fix_flow(client, DOMAIN, "certificate_not_pinned")
    result = await process_repair_fix_flow(client, result["flow_id"], {})

    assert (result["type"], result["reason"]) == ("abort", "reconfigure")
    assert result["next_flow"][0] == "config_flow"
    assert hass.config_entries.flow.async_get(result["next_flow"][1])["step_id"] == "reconfigure"


@pytest.mark.usefixtures("lan", "bridge")
async def test_the_repair_of_a_removed_entry_ends(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = paired(switch(), pin=None)
    await start(hass, entry)
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, context={"issue_id": "certificate_not_pinned"})

    assert await hass.config_entries.async_remove(entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_loaded")
    assert hass.config_entries.flow.async_progress() == []


async def test_a_repair_without_its_entry_ends(hass: HomeAssistant) -> None:
    flow = await repairs.async_create_fix_flow(hass, "certificate_changed", None)
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_loaded")


# The switch test.


async def test_the_switch_test_toggles_and_logs(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    runtime_data = await start(hass, paired(switch(lights=["Ceiling"], groups=["Hall"])))
    created = bridge.created

    result = await runtime_data.async_test_toggle(f" {DEVICE_ID.upper()} ")

    assert result == SwitchTestResult(True, 2, 0, 0, 0, ("light 'Ceiling'", "group 'Hall'"))
    assert bridge.puts() == [("l1", True), ("g1", True)]
    assert logged(caplog, INTEGRATION_LOGGER.name) == [
        f"{HALL}: test from Home Assistant turned ON light 'Ceiling', group 'Hall'."
    ]
    # A client of its own, closed afterwards.
    assert (bridge.created - created, bridge.closed) == (1, 1)

    result = await runtime_data.async_test_toggle(DEVICE_ID)

    assert result == SwitchTestResult(False, 2, 0, 0, 0, ("light 'Ceiling'", "group 'Hall'"))
    # The test isn't a press: no events, and no last action.
    assert runtime_data.status.snapshot().switches[0].last_action is None


@pytest.mark.parametrize(
    ("entry", "device_id", "message"),
    [
        (paired(switch()), OTHER_DEVICE_ID, f"No switch with device id {OTHER_DEVICE_ID} is configured."),
        (paired(switch()), " ", "No switch with device id   is configured."),
        (
            paired(switch(), app_key=None),
            DEVICE_ID,
            "Pair the Hue bridge first: reconfigure the sonoff2hue integration.",
        ),
        (
            paired(switch(), address="not a host"),
            DEVICE_ID,
            "'not a host' is not a valid Hue bridge address.",
        ),
    ],
    ids=["unknown switch", "blank id", "not paired", "unusable settings"],
)
@pytest.mark.usefixtures("lan")
async def test_the_switch_test_refuses_with_dotnets_messages(
    hass: HomeAssistant, bridge: Bridge, entry: MockConfigEntry, device_id: str, message: str
) -> None:
    runtime_data = await start(hass, entry)
    created = bridge.created

    with pytest.raises(SwitchTestError) as caught:
        await runtime_data.async_test_toggle(device_id)

    assert str(caught.value).startswith(message)
    assert caught.value.problems == ()
    assert bridge.created == created


@pytest.mark.usefixtures("lan")
async def test_the_switch_test_lists_the_targets_not_on_the_bridge(hass: HomeAssistant, bridge: Bridge) -> None:
    runtime_data = await start(hass, paired(switch(lights=["Kitchen"], groups=["Garden"])))

    with pytest.raises(SwitchTestError) as caught:
        await runtime_data.async_test_toggle(DEVICE_ID)

    problems = (
        f"{HALL}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Desk'",
        f"{HALL}: no Hue room or zone named 'Garden'. Known: 'room:Hall'",
    )
    assert caught.value.problems == problems
    assert str(caught.value) == " ".join(problems)
    assert bridge.puts() == []
    assert bridge.closed == bridge.created - 1


@pytest.mark.usefixtures("lan")
async def test_the_switch_test_gives_the_bridges_error(hass: HomeAssistant, bridge: Bridge) -> None:
    runtime_data = await start(hass, paired(switch()))
    bridge.fail("/clip/v2/resource/light", 503)

    with pytest.raises(SwitchTestError, match=r"^Hue bridge returned 503 Service Unavailable for GET /clip/v2/"):
        await runtime_data.async_test_toggle(DEVICE_ID)

    assert bridge.closed == bridge.created - 1


# The activity.


async def test_the_activity_has_what_the_integration_logs_at_its_level(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture
) -> None:
    """Home Assistant's level decides, as for its own log: warnings by default, info and debug lines when asked."""
    caplog.set_level(logging.WARNING, logger=INTEGRATION_LOGGER.name)
    bridge.fail("/clip/v2/resource/bridge", 503)
    runtime_data = await start(hass, paired(switch()))

    entries = runtime_data.activity.get(0, 1000)
    assert [(entry.level, entry.category, entry.message) for entry in entries] == [
        (
            "warning",
            "supervisor",
            f"Hue bridge at {BRIDGE_ADDRESS} not reachable ({BRIDGE_DOWN}); retrying every 10 s.",
        )
    ]

    caplog.set_level(logging.DEBUG, logger=INTEGRATION_LOGGER.name)
    bridge.heal()
    lan.broadcast(1, OTHER_DEVICE_ID)
    subentry = subentry_of(runtime_data.entry)
    hass.config_entries.async_update_subentry(
        runtime_data.entry, subentry, data={**subentry.data, CONF_LIGHTS: ["Desk"]}
    )
    await settle()

    messages = [entry.message for entry in runtime_data.activity.get(entries[-1].id, 1000)]
    assert RESTARTING in messages
    assert "Listening for wall switch presses." in messages
    assert any(message.startswith("Ignoring unconfigured eWeLink device") for message in messages) is False
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()
    assert [entry.message for entry in runtime_data.activity.get(0, 1000)][-1] == (
        "Ignoring unconfigured eWeLink device 1000999999 at 192.0.2.5."
    )
