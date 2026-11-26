"""The switch subentry flow (decision 24): every step, error, warning and abort, through Home Assistant's flow manager
for subentries, with the integration set up and its runtime running (decision 23).

The runtime listens on the fake network of ``tests/fakes.py``. The devices seen on the LAN are its registry, fed with
synthetic tracker results as its LAN monitor feeds it, or with broadcasts on that network. Its Hue clients, and so the
flow's, reach a bridge in memory with the fake Hue bridge's lights, rooms and zones, which a test can change, or the
fake Hue bridge over HTTPS on 127.0.0.1. Every result is checked against ``translations/en.json``: the texts it shows
must exist, with the placeholders they use.
"""

import asyncio
from datetime import datetime, timedelta
from ipaddress import IPv4Address
import json
import logging
import string
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
    ConfigSubentry,
    ConfigSubentryData,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import (
    EVENT_DATA_ENTRY_FLOW_PROGRESSED,
    FlowResultType,
    InvalidData,
    UnknownFlow,
    section,
)
from homeassistant.helpers.selector import SelectSelector
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, async_fire_time_changed
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import runtime, switch_flow
from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
from custom_components.sonoff2hue.engine.hue_client import HueClient, HueGroup, HueLight
from custom_components.sonoff2hue.engine.press import SwitchEvent, SwitchEventKind, TrackedEvent, TrackerUpdate
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.supervisor import create_hue_client
from custom_components.sonoff2hue.engine.switch_rules import NOT_PAIRED
from custom_components.sonoff2hue.switch_flow import IDENTIFY_TIMEOUT
from tools.fake_hue.bridge import FakeIds

from .engine.fakes import until
from .fakes import DEVICE_ADDRESS, Bridge, Lan, paired
from .hue import FakeHueHost, RecordedRequest

Kind = SwitchEventKind
DEVICE = "1000abcdef"
OTHER = "1000999999"
FREE = "1000aaaaaa"
SECOND = "1000bbbbbb"
KEY = "secret-device-key"
SELECTED = f"Selected {DEVICE}: it reported a press."
USED = f"{OTHER} reported a press, but it is already used by Hallway switch. Operate the switch you want to add."
KEY_WARNING = (
    "The current key belongs to the previous device. Choose \u201cReplace it\u201d or \u201cRemove it\u201d when you "
    "change the device."
)


# The bridge.


def light(resource_id: str, name: str | None, *, on: bool = False) -> dict[str, Any]:
    return {"id": resource_id, "type": "light", "metadata": {"name": name}, "on": {"on": on}}


def group(kind: str, resource_id: str, name: str, *, grouped: bool = True) -> dict[str, Any]:
    services = [{"rid": f"grouped-{resource_id}", "rtype": "grouped_light"}] if grouped else []
    return {"id": resource_id, "type": kind, "metadata": {"name": name}, "services": services}


class HueBridge(Bridge):
    """The runtime's bridge in memory (``tests/fakes.py``), with the fake Hue bridge's lights, rooms and zones in
    ``resources``, which a test may change; ``fail`` and ``hold`` work on their lists too."""

    def __init__(self) -> None:
        super().__init__()
        self.resources: dict[str, list[Any]] = {
            "light": [light("l1", "Ceiling", on=True), light("l2", "Floor lamp"), light("l3", "Hallway")],
            "room": [group("room", "r1", "Living room")],
            "zone": [group("zone", "z1", "Downstairs")],
        }

    def _now(self, request: RecordedRequest) -> tuple[int, str]:
        status, body = super()._now(request)
        kind = request.path.removeprefix("/clip/v2/resource/")
        if request.method == "GET" and status == 200 and kind in self.resources:
            body = json.dumps({"errors": [], "data": self.resources[kind]})
        return status, body


@pytest.fixture(autouse=True)
def hue(lan: Lan, monkeypatch: pytest.MonkeyPatch) -> HueBridge:
    """Every test's runtime listens on the fake network, and its Hue clients, which the flow takes, reach the bridge in
    memory, unless the test uses the fake Hue bridge over HTTPS (``https_hue``)."""
    fake = HueBridge()
    monkeypatch.setattr(runtime, "create_hue_client", fake.clients())
    return fake


@pytest.fixture
def https_hue(fake_hue: FakeHueHost, monkeypatch: pytest.MonkeyPatch) -> FakeHueHost:
    """The fake Hue bridge over HTTPS on 127.0.0.1, which the runtime's own clients reach."""
    monkeypatch.setattr(runtime, "create_hue_client", create_hue_client)
    return fake_hue


# The config entry and its switches.


def switch(device_id: str, name: str | None, *, key: str | None = None, **lists: list[str]) -> ConfigSubentryData:
    """A switch subentry as the import of a ``sonoff2hue.yaml`` writes it: the values as given, the normalized device
    id as its unique id, and its name, or that id, as its title."""
    normalized = SwitchSettings(device_id).normalized_device_id
    return ConfigSubentryData(
        data={
            CONF_DEVICE_ID: device_id,
            CONF_NAME: name,
            CONF_DEVICE_KEY: key,
            CONF_LIGHTS: lists.get("lights", ["Hallway"]),
            CONF_GROUPS: lists.get("groups", []),
        },
        subentry_type=SUBENTRY_SWITCH,
        title=name or normalized,
        unique_id=normalized,
    )


HALLWAY_SWITCH = switch(OTHER, "Hallway switch")


def add_switch(hass: HomeAssistant, entry: MockConfigEntry, data: ConfigSubentryData) -> None:
    """Adds a switch as another dialog would, while the one under test is open."""
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(data["data"]),
            subentry_type=data["subentry_type"],
            title=data["title"],
            unique_id=data["unique_id"],
        ),
    )


async def settle() -> None:
    """Lets the event loop run what is ready: with the LAN and the bridge in memory, nothing waits for I/O."""
    for _ in range(20):
        await asyncio.sleep(0)


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """Sets the entry up, and lets its runtime start."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle()
    return entry


async def loaded(hass: HomeAssistant, *switches: ConfigSubentryData, **bridge: Any) -> MockConfigEntry:
    """The loaded config entry of a paired bridge, ``tests/fakes.py``'s, with these switches."""
    return await setup(hass, paired(*switches, **bridge))


def subentry(entry: MockConfigEntry, device_id: str) -> ConfigSubentry:
    [found] = [item for item in entry.subentries.values() if item.unique_id == device_id]
    return found


# The devices seen on the LAN.


def report(
    registry: DeviceRegistry,
    device_id: str,
    kind: Kind,
    *,
    at: datetime | None = None,
    address: str | None = "192.0.2.10",
    device_type: str | None = "plug",
    seq: int = 1,
) -> None:
    """A tracker result for one broadcast of the device, as the LAN monitor gives it to the registry."""
    received_at = dt_util.utcnow() if at is None else at
    announcement = EwelinkAnnouncement(
        device_id,
        f"eWeLink_{device_id}",
        seq,
        device_type,
        True,
        None,
        "",
        None if address is None else IPv4Address(address),
        received_at,
    )
    event = TrackedEvent(announcement, SwitchEvent(kind, seq), None, None, None, None)
    registry.apply(TrackerUpdate((), (event,)), received_at)


def watching(registry: DeviceRegistry) -> bool:
    """Whether the flow follows the registry's changes, as the runtime's status always does."""
    return any(callback.__module__ == switch_flow.__name__ for callback in registry._changed)


class Devices:
    """The devices seen on the LAN: the registry of the runtime of the loaded entry, whichever runtime that is."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    @property
    def registry(self) -> DeviceRegistry:
        [entry] = self._hass.config_entries.async_entries(DOMAIN)
        registry: DeviceRegistry = entry.runtime_data.registry
        return registry

    def report(self, device_id: str, kind: Kind, **kwargs: Any) -> None:
        report(self.registry, device_id, kind, **kwargs)

    def see(self, device_id: str, **kwargs: Any) -> None:
        """The device's first broadcast, its baseline."""
        self.report(device_id, Kind.BASELINE, **kwargs)

    def press(self, device_id: str, **kwargs: Any) -> None:
        self.report(device_id, Kind.PRESS, **kwargs)

    @property
    def watched(self) -> bool:
        return watching(self.registry)


@pytest.fixture
def devices(hass: HomeAssistant) -> Devices:
    return Devices(hass)


# Driving the flow, and checking its texts.


async def shown(hass: HomeAssistant, result: SubentryFlowResult) -> SubentryFlowResult:
    """``result``, once Home Assistant has found every text it shows, with the placeholders those use."""
    texts = await async_get_translations(hass, "en", "config_subentries", [DOMAIN])
    texts |= await async_get_translations(hass, "en", "selector", [DOMAIN])
    prefix = f"component.{DOMAIN}.config_subentries.{SUBENTRY_SWITCH}"
    step = f"{prefix}.step.{result.get('step_id')}"
    keys: list[str] = []
    optional = [f"{step}.description"]

    def fields(schema: Any, path: str) -> None:
        for marker, value in schema.schema.items():
            if isinstance(value, section):
                keys.append(f"{step}.sections.{marker}.name")
                optional.append(f"{step}.sections.{marker}.description")
                fields(value.schema, f"{step}.sections.{marker}")
                continue
            keys.append(f"{path}.data.{marker}")
            optional.append(f"{path}.data_description.{marker}")
            if isinstance(value, SelectSelector) and (key := value.config.get("translation_key")):
                keys.extend(f"component.{DOMAIN}.selector.{key}.options.{option}" for option in value.config["options"])

    match result["type"]:
        case FlowResultType.FORM:
            keys.append(f"{step}.title")
            fields(result["data_schema"], step)
            keys += [f"{prefix}.error.{error}" for error in (result["errors"] or {}).values()]
        case FlowResultType.MENU:
            keys += [f"{step}.title", *(f"{step}.menu_options.{option}" for option in result["menu_options"])]
        case FlowResultType.SHOW_PROGRESS:
            keys += [f"{step}.title", f"{prefix}.progress.{result['progress_action']}"]
        case FlowResultType.ABORT:
            keys.append(f"{prefix}.abort.{result['reason']}")
        case FlowResultType.CREATE_ENTRY:
            keys.append(f"{prefix}.create_entry.{result['description']}")
        case other:
            pytest.fail(f"The flow showed a {other}")
    given = set(result.get("description_placeholders") or {})
    for key in [*keys, *(key for key in optional if key in texts)]:
        assert key in texts, f"{key} is missing from translations/en.json"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the flow doesn't give"
    return result


async def start(hass: HomeAssistant, entry: MockConfigEntry, edit: str | None = None) -> SubentryFlowResult:
    """Adds a switch, or edits the one with the device id ``edit``, as the integration's page starts it."""
    context: dict[str, Any] = {"source": SOURCE_USER}
    if edit is not None:
        context = {"source": SOURCE_RECONFIGURE, "subentry_id": subentry(entry, edit).subentry_id}
    result = await hass.config_entries.subentries.async_init((entry.entry_id, SUBENTRY_SWITCH), context=context)
    return await shown(hass, result)


async def go(
    hass: HomeAssistant, result: SubentryFlowResult, user_input: dict[str, Any] | None = None
) -> SubentryFlowResult:
    """Submits ``user_input`` to the step; without it, shows the step again, as the frontend does when the flow
    changed."""
    return await shown(hass, await hass.config_entries.subentries.async_configure(result["flow_id"], user_input))


async def choose(hass: HomeAssistant, result: SubentryFlowResult, option: str) -> SubentryFlowResult:
    assert result["type"] is FlowResultType.MENU
    assert option in result["menu_options"]
    return await go(hass, result, {"next_step_id": option})


async def identify(hass: HomeAssistant, result: SubentryFlowResult, devices: Devices) -> SubentryFlowResult:
    """Chooses identify-by-press, once it follows the devices seen on the LAN."""
    result = await choose(hass, result, "identify")
    assert (result["type"], result["step_id"], result["progress_action"]) == (
        FlowResultType.SHOW_PROGRESS,
        "identify",
        "identify",
    )
    await until(lambda: devices.watched)
    return result


async def done(hass: HomeAssistant, result: SubentryFlowResult) -> SubentryFlowResult:
    """The step after a progress step, once its task has finished."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await go(hass, result)


async def manual(hass: HomeAssistant, result: SubentryFlowResult, device_id: str) -> SubentryFlowResult:
    """Enters the device id from the device menu."""
    return await go(hass, await choose(hass, result, "manual"), {"device_id": device_id})


def suggested(result: SubentryFlowResult, *path: str) -> Any:
    """The value the form suggests for the field at ``path``, a section's name first for a field in it."""
    schema = result["data_schema"]
    for name in path[:-1]:
        schema = next(value for marker, value in schema.schema.items() if marker == name).schema
    for marker in schema.schema:
        if marker == path[-1]:
            return (marker.description or {}).get("suggested_value")
    return pytest.fail(f"The form has no field {path}")


def options(result: SubentryFlowResult, field: str) -> list[tuple[str, str]]:
    """The values and labels of a select field."""
    selector = next(value for marker, value in result["data_schema"].schema.items() if marker == field)
    return [(option["value"], option["label"]) for option in selector.config["options"]]


def mode(result: SubentryFlowResult, field: str) -> str | None:
    """How a select field is shown: Home Assistant's choice by the number of options when ``None``."""
    selector = next(value for marker, value in result["data_schema"].schema.items() if marker == field)
    shown_as: str | None = selector.config.get("mode")
    return shown_as


def field_names(result: SubentryFlowResult) -> list[str]:
    return [str(marker) for marker in result["data_schema"].schema]


def collapsed(result: SubentryFlowResult) -> bool:
    advanced = next(value for marker, value in result["data_schema"].schema.items() if marker == "advanced")
    folded: bool = advanced.options["collapsed"]
    return folded


def form(**values: Any) -> dict[str, Any]:
    """The settings form's input: no name, nothing chosen, an empty key, unless given."""
    advanced = values.pop("advanced", {"device_key": ""})
    return {"name": "", **values, "advanced": advanced}


def switches_of(entry: MockConfigEntry) -> list[tuple[str, str | None, dict[str, Any]]]:
    """The switch subentries: title, unique id and data."""
    return [(item.title, item.unique_id, dict(item.data)) for item in entry.subentries.values()]


async def settings_for(
    hass: HomeAssistant, entry: MockConfigEntry, device_id: str = DEVICE, edit: str | None = None
) -> SubentryFlowResult:
    """The settings form, for a device entered by its id, or for the edited switch's own device."""
    result = await start(hass, entry, edit)
    if edit is not None and device_id == edit:
        result = await choose(hass, result, "keep")
    else:
        result = await manual(hass, result, device_id)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "settings")
    return result


# The type and the ways in.


async def test_the_entry_offers_to_add_and_edit_switches(hass: HomeAssistant) -> None:
    entry = await loaded(hass)
    await (await async_get_integration(hass, DOMAIN)).async_get_platform("config_flow")

    assert entry.supported_subentry_types == {SUBENTRY_SWITCH: {"supports_reconfigure": True}}
    texts = await async_get_translations(hass, "en", "config_subentries", [DOMAIN])
    prefix = f"component.{DOMAIN}.config_subentries.{SUBENTRY_SWITCH}"
    assert texts[f"{prefix}.initiate_flow.user"] == "Add switch"
    assert texts[f"{prefix}.initiate_flow.reconfigure"] == "Edit switch"
    assert texts[f"{prefix}.entry_type"] == "Switch"


async def test_adding_a_switch_offers_three_ways_to_its_device(hass: HomeAssistant) -> None:
    result = await start(hass, await loaded(hass))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "user",
        ["identify", "pick", "manual"],
    )


async def test_editing_a_switch_offers_to_keep_its_device_first(hass: HomeAssistant) -> None:
    entry = await loaded(hass, HALLWAY_SWITCH, switch(" 1000ABCDEF ", None))

    result = await start(hass, entry, OTHER)

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "reconfigure",
        ["keep", "identify", "pick", "manual"],
    )
    assert result["description_placeholders"] == {"switch": "Hallway switch", "device_id": OTHER}
    result = await start(hass, entry, DEVICE)

    assert result["description_placeholders"] == {"switch": DEVICE, "device_id": DEVICE}


@pytest.mark.parametrize("state", [ConfigEntryState.NOT_LOADED, ConfigEntryState.SETUP_RETRY])
@pytest.mark.parametrize("edit", [None, OTHER])
async def test_switches_wait_for_the_integration_to_load(
    hass: HomeAssistant, state: ConfigEntryState, edit: str | None
) -> None:
    entry = paired(HALLWAY_SWITCH)
    entry.add_to_hass(hass)
    entry.mock_state(hass, state)

    result = await start(hass, entry, edit)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entry_not_loaded")


# Identify by press.


async def test_identify_selects_the_device_that_reports_a_press(
    hass: HomeAssistant, lan: Lan, devices: Devices
) -> None:
    """A MINIR4M that no switch maps, over the fake network: its first broadcast is its baseline, and without its
    device key every new ``seq`` is a press."""
    entry = await loaded(hass)
    lan.broadcast(1)
    result = await identify(hass, await start(hass, entry), devices)

    assert result["description_placeholders"] == {"message": ""}
    lan.broadcast(1)
    lan.broadcast(2)
    result = await done(hass, result)

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "settings")
    assert result["description_placeholders"]["device"] == SELECTED
    assert not devices.watched
    seen = devices.registry.find(DEVICE)
    assert seen is not None
    assert (seen.address, seen.seq) == (DEVICE_ADDRESS, 2)


async def test_identify_names_the_switch_that_uses_a_pressed_device(hass: HomeAssistant, devices: Devices) -> None:
    progressed = async_capture_events(hass, EVENT_DATA_ENTRY_FLOW_PROGRESSED)
    result = await identify(hass, await start(hass, await loaded(hass, HALLWAY_SWITCH)), devices)

    devices.press(OTHER, seq=2)

    assert len(progressed) == 1
    result = await go(hass, result)

    assert (result["type"], result["progress_action"], result["description_placeholders"]) == (
        FlowResultType.SHOW_PROGRESS,
        "identify_used",
        {"message": USED},
    )
    fetched = len(progressed)
    devices.press(OTHER, seq=3)

    assert len(progressed) == fetched
    devices.press(DEVICE)
    result = await done(hass, result)

    assert (result["step_id"], result["description_placeholders"]["device"]) == ("settings", SELECTED)


async def test_identify_selects_the_edited_switchs_own_device(hass: HomeAssistant, devices: Devices) -> None:
    result = await identify(hass, await start(hass, await loaded(hass, HALLWAY_SWITCH), OTHER), devices)

    devices.press(OTHER)
    result = await done(hass, result)

    assert result["description_placeholders"]["device"] == f"Selected {OTHER}: it reported a press."


async def test_identify_counts_only_new_presses(hass: HomeAssistant, devices: Devices) -> None:
    """A baseline, a repeat, another event, and a press whose time the device already had don't select a
    device."""
    entry = await loaded(hass)
    pressed_at = dt_util.utcnow() - timedelta(seconds=5)
    devices.press(DEVICE, at=pressed_at)
    result = await identify(hass, await start(hass, entry), devices)

    devices.see(FREE)
    devices.report(FREE, Kind.DUPLICATE)
    devices.report(DEVICE, Kind.SEQUENCE_RESET)
    devices.report(DEVICE, Kind.RELAY_CHANGED, seq=2)
    devices.press(DEVICE, at=pressed_at, seq=3)
    await asyncio.sleep(0)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=IDENTIFY_TIMEOUT))
    result = await done(hass, result)

    assert (result["type"], result["step_id"]) == (FlowResultType.MENU, "identify_timeout")


async def test_the_first_press_wins(hass: HomeAssistant, devices: Devices) -> None:
    result = await identify(hass, await start(hass, await loaded(hass)), devices)

    devices.press(DEVICE)
    devices.press(FREE)
    result = await done(hass, result)

    assert result["description_placeholders"]["device"] == SELECTED


async def test_identify_gives_up_after_60_seconds(hass: HomeAssistant, devices: Devices) -> None:
    result = await identify(hass, await start(hass, await loaded(hass)), devices)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=59))
    await asyncio.sleep(0)
    assert devices.watched
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await asyncio.sleep(0)
    assert not devices.watched
    result = await done(hass, result)

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "identify_timeout",
        ["identify", "pick", "manual"],
    )
    result = await identify(hass, result, devices)
    devices.press(DEVICE)
    result = await done(hass, result)

    assert result["step_id"] == "settings"


async def test_editing_a_switch_after_no_press_can_keep_its_device(hass: HomeAssistant, devices: Devices) -> None:
    result = await identify(hass, await start(hass, await loaded(hass, HALLWAY_SWITCH), OTHER), devices)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=IDENTIFY_TIMEOUT))
    result = await done(hass, result)

    assert (result["step_id"], result["menu_options"], result["description_placeholders"]) == (
        "identify_timeout",
        ["keep", "identify", "pick", "manual"],
        {"switch": "Hallway switch", "device_id": OTHER},
    )


async def test_identify_follows_the_entry_when_it_is_reloaded(hass: HomeAssistant, devices: Devices) -> None:
    """A reload stops the runtime and starts another, with a registry of its own."""
    entry = await loaded(hass)
    result = await identify(hass, await start(hass, entry), devices)
    stopped = devices.registry

    assert await hass.config_entries.async_unload(entry.entry_id)

    assert not watching(stopped)
    report(stopped, DEVICE, Kind.PRESS)
    await setup(hass, entry)

    assert devices.registry is not stopped
    assert devices.watched
    devices.press(FREE)
    result = await done(hass, result)

    assert result["description_placeholders"]["device"] == f"Selected {FREE}: it reported a press."
    assert not devices.watched


async def test_identify_gives_up_while_the_entry_is_not_loaded(hass: HomeAssistant, devices: Devices) -> None:
    entry = await loaded(hass)
    result = await identify(hass, await start(hass, entry), devices)

    assert await hass.config_entries.async_unload(entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=IDENTIFY_TIMEOUT))
    result = await done(hass, result)

    assert result["step_id"] == "identify_timeout"


async def test_closing_the_dialog_stops_identify(hass: HomeAssistant, devices: Devices) -> None:
    entry = await loaded(hass)
    result = await identify(hass, await start(hass, entry), devices)
    [flow] = hass.config_entries.subentries._progress.values()
    task = flow._identify
    assert task is not None

    hass.config_entries.subentries.async_abort(result["flow_id"])
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not devices.watched
    assert not entry._on_state_change


async def test_a_pressed_device_with_an_id_the_form_refuses_can_be_entered(
    hass: HomeAssistant, devices: Devices
) -> None:
    result = await identify(hass, await start(hass, await loaded(hass)), devices)

    devices.press("abc")
    result = await done(hass, result)

    assert (result["type"], result["step_id"], result["errors"]) == (
        FlowResultType.FORM,
        "manual",
        {"device_id": "invalid_device"},
    )
    assert result["description_placeholders"] == {"error": "A device id has 4 to 40 letters and digits."}
    assert suggested(result, "device_id") == "abc"
    result = await go(hass, result, {"device_id": "abc1"})

    assert (result["step_id"], result["description_placeholders"]["device"]) == ("settings", "Device abc1.")


# The devices seen on the LAN.


async def test_pick_lists_unused_devices_first_and_the_latest_press_first(
    hass: HomeAssistant, devices: Devices
) -> None:
    entry = await loaded(hass, HALLWAY_SWITCH)
    now = dt_util.utcnow()
    devices.press(OTHER, at=now - timedelta(seconds=1))
    devices.see(SECOND, at=now - timedelta(minutes=6), address=None, device_type=None)
    devices.press(FREE, at=now - timedelta(seconds=12))
    devices.press(DEVICE, at=now - timedelta(minutes=3))
    devices.see("1000cccccc")

    result = await choose(hass, await start(hass, entry), "pick")

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "pick", {})
    assert suggested(result, "device") is None
    assert options(result, "device") == [
        (FREE, f"{FREE} — 192.0.2.10 — plug · Online · Last press 12 s ago"),
        (DEVICE, f"{DEVICE} — 192.0.2.10 — plug · Online · Last press 3 min ago"),
        (SECOND, f"{SECOND} — no address — unknown type · Offline · Last press none yet"),
        ("1000cccccc", "1000cccccc — 192.0.2.10 — plug · Online · Last press none yet"),
        (OTHER, f"{OTHER} — 192.0.2.10 — plug · Online · Last press just now · used by Hallway switch"),
    ]


async def test_picking_a_device_of_another_switch_says_which_switch_uses_it(
    hass: HomeAssistant, devices: Devices
) -> None:
    entry = await loaded(hass, HALLWAY_SWITCH)
    devices.see(OTHER)
    devices.see(DEVICE)
    result = await choose(hass, await start(hass, entry), "pick")

    result = await go(hass, result, {"device": OTHER})

    assert (result["step_id"], result["errors"], result["description_placeholders"]) == (
        "pick",
        {"device": "invalid_device"},
        {"error": "This device is already used by Hallway switch."},
    )
    assert suggested(result, "device") == OTHER
    result = await go(hass, result, {"device": DEVICE})

    assert (result["step_id"], result["description_placeholders"]["device"]) == ("settings", f"Device {DEVICE}.")


async def test_editing_a_switch_picks_its_own_device_first(hass: HomeAssistant, devices: Devices) -> None:
    entry = await loaded(hass, HALLWAY_SWITCH)
    devices.see(OTHER)
    result = await choose(hass, await start(hass, entry, OTHER), "pick")

    assert suggested(result, "device") == OTHER
    assert options(result, "device") == [(OTHER, f"{OTHER} — 192.0.2.10 — plug · Online · Last press none yet")]


async def test_pick_without_devices_offers_the_other_ways(hass: HomeAssistant, devices: Devices) -> None:
    result = await choose(hass, await start(hass, await loaded(hass)), "pick")

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "no_devices",
        ["identify", "pick", "manual"],
    )
    devices.see(DEVICE)
    result = await choose(hass, result, "pick")

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "pick")


async def test_pick_while_the_entry_is_not_loaded(hass: HomeAssistant, devices: Devices) -> None:
    entry = await loaded(hass)
    devices.see(DEVICE)
    result = await start(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    result = await choose(hass, result, "pick")

    assert result["step_id"] == "no_devices"


# The device id.


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ("", "Enter the device id."),
        ("  ", "Enter the device id."),
        ("abc", "A device id has 4 to 40 letters and digits."),
        ("1000\u212aabc", "A device id has 4 to 40 letters and digits."),
        ("x" * 41, "A device id has 4 to 40 letters and digits."),
        (" 1000999999\t", "This device is already used by Hallway switch."),
    ],
)
async def test_the_device_id_is_checked_by_the_form(hass: HomeAssistant, typed: str, message: str) -> None:
    result = await manual(hass, await start(hass, await loaded(hass, HALLWAY_SWITCH)), typed)

    assert (result["type"], result["step_id"], result["errors"]) == (
        FlowResultType.FORM,
        "manual",
        {"device_id": "invalid_device"},
    )
    assert result["description_placeholders"] == {"error": message}
    assert suggested(result, "device_id") == typed


async def test_an_empty_device_id_may_be_left_out(hass: HomeAssistant) -> None:
    result = await go(hass, await choose(hass, await start(hass, await loaded(hass)), "manual"), {})

    assert result["description_placeholders"] == {"error": "Enter the device id."}


async def test_editing_a_switch_may_enter_its_own_device(hass: HomeAssistant) -> None:
    result = await start(hass, await loaded(hass, HALLWAY_SWITCH), OTHER)
    result = await choose(hass, result, "manual")

    assert (result["errors"], suggested(result, "device_id")) == ({}, "")
    result = await go(hass, result, {"device_id": " 1000999999 "})

    assert (result["step_id"], result["description_placeholders"]["device"]) == ("settings", f"Device {OTHER}.")


async def test_keeping_a_device_id_that_the_form_refuses(hass: HomeAssistant) -> None:
    """An imported switch can have an id that the form refuses, which then has to be entered anew."""
    entry = await loaded(hass, switch("abc", "Imported"))

    result = await choose(hass, await start(hass, entry, "abc"), "keep")

    assert (result["step_id"], result["errors"], result["description_placeholders"]) == (
        "manual",
        {"device_id": "invalid_device"},
        {"error": "A device id has 4 to 40 letters and digits."},
    )


# Adding and editing a switch against the fake Hue bridge.


async def paired_with(hass: HomeAssistant, host: FakeHueHost, *switches: ConfigSubentryData) -> MockConfigEntry:
    """The loaded config entry of the fake Hue bridge, paired, with these switches."""
    return await loaded(hass, *switches, address=host.address, app_key=await host.pair(), pin=host.fingerprint)


async def test_adding_a_switch_with_the_fake_bridge(hass: HomeAssistant, lan: Lan, https_hue: FakeHueHost) -> None:
    """From a broadcast on the fake network to the switch that the runtime then runs, as a user adds one."""
    https_hue.bridge.try_set_on("light", FakeIds.FLOOR_LAMP, True)
    entry = await paired_with(hass, https_hue)
    lan.broadcast(1)
    result = await choose(hass, await start(hass, entry), "pick")

    result = await go(hass, result, {"device": DEVICE})

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "settings", {})
    assert field_names(result) == ["name", "lights", "rooms", "zones", "advanced"]
    assert options(result, "lights") == [("0", "Ceiling — off"), ("1", "Floor lamp — on"), ("2", "Hallway — off")]
    assert (options(result, "rooms"), options(result, "zones")) == ([("0", "Living room")], [("0", "Downstairs")])
    assert [suggested(result, field) for field in ("name", "lights", "rooms", "zones")] == ["", [], [], []]
    assert suggested(result, "advanced", "device_key") == ""
    assert collapsed(result)
    assert result["description_placeholders"] == {
        "device": f"Device {DEVICE}.",
        "key_warning": "",
        "base_error": "",
        "name_error": "",
        "key_error": "",
        "light_names_error": "",
        "group_names_error": "",
    }

    result = await go(hass, result, form(name=" Hall ", lights=["0", "2"], rooms=["0"]))

    assert (result["type"], result["title"], result["description"]) == (FlowResultType.CREATE_ENTRY, "Hall", "added")
    assert result["description_placeholders"] == {"switch": "Hall"}
    assert switches_of(entry) == [
        (
            "Hall",
            DEVICE,
            {
                CONF_DEVICE_ID: DEVICE,
                CONF_NAME: "Hall",
                CONF_DEVICE_KEY: None,
                CONF_LIGHTS: ["Ceiling", "Hallway"],
                CONF_GROUPS: ["room:Living room"],
            },
        )
    ]
    await until(lambda: entry.runtime_data.config.find_switch(DEVICE) is not None)


async def test_editing_a_switch_with_the_fake_bridge(hass: HomeAssistant, https_hue: FakeHueHost) -> None:
    hall = switch(DEVICE, "Hall", key=KEY, lights=["hallway", FakeIds.CEILING], groups=["zone:downstairs"])
    entry = await paired_with(hass, https_hue, hall, HALLWAY_SWITCH)
    subentry_id = subentry(entry, DEVICE).subentry_id

    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    assert field_names(result) == ["name", "lights", "rooms", "zones", "advanced"]
    assert [suggested(result, field) for field in ("name", "lights", "rooms", "zones")] == [
        "Hall",
        ["0", "2"],
        [],
        ["0"],
    ]
    assert (suggested(result, "advanced", "key_mode"), suggested(result, "advanced", "new_device_key")) == ("keep", "")
    result = await go(
        hass, result, form(name="Hall lights", lights=["1", "2"], zones=["0"], advanced={"key_mode": "keep"})
    )

    assert (result["type"], result["reason"], result["description_placeholders"]) == (
        FlowResultType.ABORT,
        "saved",
        {"switch": "Hall lights"},
    )
    assert entry.subentries[subentry_id].title == "Hall lights"
    assert switches_of(entry)[0] == (
        "Hall lights",
        DEVICE,
        {
            CONF_DEVICE_ID: DEVICE,
            CONF_NAME: "Hall lights",
            CONF_DEVICE_KEY: KEY,
            CONF_LIGHTS: ["hallway", "Floor lamp"],
            CONF_GROUPS: ["zone:downstairs"],
        },
    )


# The lights, rooms and zones.


async def test_the_picker_lists_names_states_and_rooms_without_lights(hass: HomeAssistant, hue: HueBridge) -> None:
    hue.resources = {
        "light": [light("l1", "Lamp", on=True), light("l2", " lamp "), light("l3", None), light("l4", "Desk")],
        "room": [group("room", "r1", "Kitchen", grouped=False), group("room", "r2", "Kitchen")],
        "zone": [group("zone", "z1", "Kitchen")],
    }

    result = await settings_for(hass, await loaded(hass))

    assert options(result, "lights") == [
        ("0", " lamp  (l2) — off"),
        ("1", "Desk — off"),
        ("2", "l3 — off"),
        ("3", "Lamp (l1) — on"),
    ]
    assert options(result, "rooms") == [("0", "Kitchen (r1) — no lights"), ("1", "Kitchen (r2)")]
    assert options(result, "zones") == [("0", "Kitchen")]
    result = await go(hass, result, form(lights=["1", "2", "3"], rooms=["1"], zones=["0"]))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert switches_of(entry)[0][2][CONF_LIGHTS] == ["Desk", "l3", "l1"]
    assert switches_of(entry)[0][2][CONF_GROUPS] == ["r2", "zone:Kitchen"]


async def test_entries_that_dont_match_the_bridge_are_kept_until_removed(hass: HomeAssistant, hue: HueBridge) -> None:
    hue.resources["light"].append(light("l4", "Ceiling"))
    hall = switch(DEVICE, "Hall", lights=["Kitchen", "Hallway", "ceiling"], groups=["room:Attic", "Living room"])
    entry = await loaded(hass, hall)

    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    assert field_names(result) == ["name", "unresolved", "lights", "rooms", "zones", "advanced"]
    assert options(result, "unresolved") == [
        ("0", "Kitchen — not found on the bridge"),
        ("2", "ceiling — matches 2 lights"),
        ("3", "room:Attic — not found on the bridge"),
    ]
    assert suggested(result, "unresolved") == ["0", "2", "3"]
    assert (mode(result, "unresolved"), mode(result, "lights")) == ("dropdown", None)
    assert suggested(result, "lights") == ["3"]
    result = await go(hass, result, form(unresolved=["0", "3"], lights=["3"], rooms=["0"]))

    assert (result["step_id"], result["errors"]) == ("settings", {"base": "not_saved"})
    assert result["description_placeholders"]["base_error"] == (
        f"{DEVICE}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Floor lamp', 'Hallway' "
        f"{DEVICE}: no Hue room or zone named 'room:Attic'. Known: 'room:Living room', 'zone:Downstairs'"
    )
    assert options(result, "unresolved") == [
        ("0", "Kitchen — not found on the bridge"),
        ("3", "room:Attic — not found on the bridge"),
    ]
    result = await go(hass, result, form(lights=["3"], rooms=["0"]))

    assert result["reason"] == "saved"
    assert switches_of(entry) == [
        (
            DEVICE,
            DEVICE,
            {
                CONF_DEVICE_ID: DEVICE,
                CONF_NAME: None,
                CONF_DEVICE_KEY: None,
                CONF_LIGHTS: ["Hallway"],
                CONF_GROUPS: ["Living room"],
            },
        )
    ]


async def test_an_ambiguous_group_says_how_many_it_matches(hass: HomeAssistant, hue: HueBridge) -> None:
    hue.resources["zone"].append(group("zone", "z2", "Living room"))
    entry = await loaded(hass, switch(DEVICE, "Hall", groups=["Living room"]))

    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    assert options(result, "unresolved") == [("1", "Living room — matches 2 rooms/zones")]


# The form's checks, then the switch rules.


async def test_the_form_checks_everything_it_can_at_once(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", key=KEY))
    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    result = await go(hass, result, form(name="x" * 65, advanced={"key_mode": "replace", "new_device_key": " "}))

    assert result["errors"] == {"name": "invalid_name", "base": "not_saved", "advanced": "invalid_device_key"}
    placeholders = result["description_placeholders"]
    assert (placeholders["name_error"], placeholders["base_error"], placeholders["key_error"]) == (
        "Use at most 64 characters.",
        "Choose at least one light, room or zone.",
        "Enter the new device key, or keep or remove the current one.",
    )
    assert not collapsed(result)
    assert suggested(result, "name") == "x" * 65
    assert suggested(result, "advanced", "key_mode") == "replace"
    assert suggested(result, "advanced", "new_device_key") == " "


async def test_the_switch_rules_check_the_rest(hass: HomeAssistant) -> None:
    result = await settings_for(hass, await loaded(hass))

    result = await go(hass, result, form(name="Hall\x85x\x07", lights=["0"], advanced={"device_key": "a key"}))

    assert result["errors"] == {"name": "invalid_name", "advanced": "invalid_device_key"}
    placeholders = result["description_placeholders"]
    assert (placeholders["name_error"], placeholders["key_error"]) == (
        "The name contains control characters.",
        "A device key has at most 128 characters and no spaces.",
    )
    assert suggested(result, "advanced", "device_key") == "a key"
    assert not collapsed(result)


@pytest.mark.parametrize(
    ("mode", "new_key", "saved"),
    [("keep", None, KEY), ("replace", " new-key ", "new-key"), ("remove", None, None)],
)
async def test_the_device_key_can_be_kept_replaced_or_removed(
    hass: HomeAssistant, mode: str, new_key: str | None, saved: str | None
) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", key=KEY))
    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    assert collapsed(result)
    keys = {"key_mode": mode} | ({} if new_key is None else {"new_device_key": new_key})
    result = await go(hass, result, form(name="Hall", lights=["0"], advanced=keys))

    assert result["reason"] == "saved"
    assert switches_of(entry)[0][2][CONF_DEVICE_KEY] == saved


async def test_a_new_key_is_taken_only_to_replace_the_current_one(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", key=KEY))
    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)

    result = await go(hass, result, form(lights=["0"], advanced={"key_mode": "keep", "new_device_key": "other"}))

    assert switches_of(entry)[0][2][CONF_DEVICE_KEY] == KEY


async def test_changing_the_device_warns_about_a_kept_key(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", key=KEY))
    subentry_id = subentry(entry, DEVICE).subentry_id
    result = await manual(hass, await start(hass, entry, DEVICE), FREE)

    assert result["description_placeholders"]["key_warning"] == KEY_WARNING
    assert not collapsed(result)
    result = await go(hass, result, form(lights=["0"], advanced={"key_mode": "remove"}))

    assert result["reason"] == "saved"
    assert entry.subentries[subentry_id].unique_id == FREE
    assert switches_of(entry) == [
        (
            FREE,
            FREE,
            {CONF_DEVICE_ID: FREE, CONF_NAME: None, CONF_DEVICE_KEY: None, CONF_LIGHTS: ["Ceiling"], CONF_GROUPS: []},
        )
    ]


async def test_a_changed_device_drops_a_kept_key(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", key=KEY))
    result = await manual(hass, await start(hass, entry, DEVICE), " 1000AAAAAA ")

    assert result["description_placeholders"]["device"] == "Device 1000AAAAAA."
    result = await go(hass, result, form(name="Hall", lights=["0"], advanced={"key_mode": "keep"}))

    assert (result["reason"], result["description_placeholders"]) == ("saved", {"switch": "Hall"})
    assert switches_of(entry)[0][1:] == (
        FREE,
        {CONF_DEVICE_ID: FREE, CONF_NAME: "Hall", CONF_DEVICE_KEY: None, CONF_LIGHTS: ["Ceiling"], CONF_GROUPS: []},
    )


async def test_a_new_switch_may_have_a_key(hass: HomeAssistant) -> None:
    entry = await loaded(hass)
    result = await manual(hass, await start(hass, entry), " 1000ABCDEF ")

    result = await go(hass, result, form(lights=["0"], advanced={"device_key": f" {KEY} "}))

    assert (result["title"], result["description_placeholders"]) == (DEVICE, {"switch": "1000ABCDEF"})
    assert switches_of(entry)[0][2][CONF_DEVICE_KEY] == KEY


# When the bridge's lights can't be listed.


async def test_lights_can_be_typed_and_saved_without_checking_while_the_bridge_is_down(
    hass: HomeAssistant, hue: HueBridge
) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall", lights=["Ceiling"], groups=["room:Living room"]))
    hue.fail("/clip/v2/resource/light", 503)
    result = await choose(hass, await start(hass, entry, DEVICE), "keep")

    unreachable = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"
    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "lights_unavailable",
        ["settings", "typed"],
    )
    assert result["description_placeholders"] == {"error": unreachable}
    result = await choose(hass, result, "typed")

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "settings")
    assert field_names(result) == ["name", "light_names", "group_names", "advanced"]
    assert (suggested(result, "light_names"), suggested(result, "group_names")) == ("Ceiling", "room:Living room")
    result = await go(hass, result, form(light_names=" Ceiling ,, Kitchen", group_names="zone:Downstairs"))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "unchecked",
        ["save", "settings"],
    )
    assert result["description_placeholders"] == {
        "warning": f"The Hue bridge could not be reached, so the light names were not checked ({unreachable})."
    }
    # Saved unchecked, although the bridge answers again and has no light named Kitchen.
    hue.heal()
    result = await choose(hass, result, "save")

    assert (result["reason"], result["description_placeholders"]) == ("saved", {"switch": DEVICE})
    assert switches_of(entry)[0][2][CONF_LIGHTS] == ["Ceiling", "Kitchen"]
    assert switches_of(entry)[0][2][CONF_GROUPS] == ["zone:Downstairs"]


async def test_typed_entries_are_checked_by_the_form_and_the_switch_rules(hass: HomeAssistant, hue: HueBridge) -> None:
    entry = await loaded(hass)
    hue.fail("/clip/v2/resource/light", 503)
    result = await choose(hass, await manual(hass, await start(hass, entry), DEVICE), "typed")

    result = await go(hass, result, form(light_names=" , "))

    assert (result["errors"], result["description_placeholders"]["light_names_error"]) == (
        {"light_names": "invalid_light_names"},
        "Choose at least one light, room or zone.",
    )
    result = await go(hass, result, form(light_names=",".join(["x"] * 51), group_names="y" * 201))

    assert result["errors"] == {"light_names": "invalid_light_names", "group_names": "invalid_group_names"}
    placeholders = result["description_placeholders"]
    assert (placeholders["light_names_error"], placeholders["group_names_error"]) == (
        "Use at most 50 entries.",
        "Each entry needs 1 to 200 characters.",
    )
    assert suggested(result, "group_names") == "y" * 201


async def test_typed_entries_are_checked_by_the_bridge_when_it_answers_again(
    hass: HomeAssistant, hue: HueBridge
) -> None:
    entry = await loaded(hass)
    hue.fail("/clip/v2/resource/light", 503)
    result = await choose(hass, await manual(hass, await start(hass, entry), DEVICE), "typed")
    hue.heal()

    result = await go(hass, result, form(light_names="Kitchen"))

    assert (result["errors"], result["description_placeholders"]["base_error"]) == (
        {"base": "not_saved"},
        f"{DEVICE}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Floor lamp', 'Hallway'",
    )


async def test_trying_again_lists_the_lights_once_the_bridge_answers(hass: HomeAssistant, hue: HueBridge) -> None:
    entry = await loaded(hass)
    hue.fail("/clip/v2/resource/light", 503)
    result = await choose(hass, await manual(hass, await start(hass, entry), DEVICE), "typed")
    result = await go(hass, result, form(light_names="Ceiling, Kitchen", group_names="room:Living room"))
    assert result["step_id"] == "unchecked"

    result = await choose(hass, result, "settings")

    assert result["step_id"] == "lights_unavailable"
    hue.heal()
    result = await choose(hass, result, "settings")

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "settings")
    assert options(result, "unresolved") == [("1", "Kitchen — not found on the bridge")]
    assert [suggested(result, field) for field in ("lights", "rooms")] == [["0"], ["0"]]
    result = await go(hass, result, form(lights=["0", "1"], rooms=["0"]))

    assert switches_of(entry)[0][2][CONF_LIGHTS] == ["Ceiling", "Floor lamp"]


async def test_an_unpaired_bridge_asks_to_pair_it_first(hass: HomeAssistant) -> None:
    entry = await loaded(hass, app_key=None)
    result = await manual(hass, await start(hass, entry), DEVICE)

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "pair_first",
        ["settings", "typed"],
    )
    assert not result["description_placeholders"]
    result = await go(hass, await choose(hass, result, "typed"), form(light_names="Ceiling"))

    assert result["description_placeholders"] == {"warning": NOT_PAIRED}
    result = await choose(hass, result, "save")

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_bridge_settings_that_cannot_be_used_ask_to_pair_it(hass: HomeAssistant) -> None:
    result = await manual(hass, await start(hass, await loaded(hass, address="not a host")), DEVICE)

    assert result["step_id"] == "pair_first"
    result = await go(hass, await choose(hass, result, "typed"), form(light_names="Ceiling"))

    assert result["description_placeholders"]["warning"] == (
        "The Hue settings in the config can't be used, so the light names were not checked ('not a host' is not a "
        "valid Hue bridge address. Use a host name or IP address, optionally with a port, for example 192.168.1.20 or "
        "bridge.local:8443.)."
    )


UNEXPECTED = "The client failed unexpectedly."


async def broken(self: HueClient) -> tuple[list[HueLight], list[HueGroup]]:
    raise RuntimeError(UNEXPECTED)


async def test_an_unexpected_failure_while_listing_the_lights(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await loaded(hass)
    monkeypatch.setattr(HueClient, "get_lights_and_groups", broken)

    result = await manual(hass, await start(hass, entry), DEVICE)

    assert (result["step_id"], result["description_placeholders"]) == ("lights_unavailable", {"error": UNEXPECTED})
    [record] = [record for record in caplog.records if record.name == switch_flow.__name__]
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        "Listing the lights, rooms and zones of the Hue bridge failed.",
    )
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


async def test_an_unexpected_failure_while_checking_the_switch(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await loaded(hass)
    result = await settings_for(hass, entry)
    monkeypatch.setattr(HueClient, "get_lights_and_groups", broken)

    result = await go(hass, result, form(lights=["0"]))

    assert (result["step_id"], result["errors"]) == ("settings", {"base": "unexpected"})
    assert result["description_placeholders"]["base_error"] == UNEXPECTED
    [record] = [record for record in caplog.records if record.name == switch_flow.__name__]
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        f"Checking the switch {DEVICE} against the Hue bridge failed.",
    )
    assert record.exc_info is not None
    assert switches_of(entry) == []


# Another switch, or the edited one, changed meanwhile.


async def test_a_device_that_another_switch_took_meanwhile_ends_the_flow(hass: HomeAssistant) -> None:
    entry = await loaded(hass)
    result = await settings_for(hass, entry)
    add_switch(hass, entry, switch(DEVICE, "Other"))

    result = await go(hass, result, form(lights=["0"]))

    assert (result["type"], result["reason"], result["description_placeholders"]) == (
        FlowResultType.ABORT,
        "conflict",
        {"error": f"Device {DEVICE} is already configured. Edit that switch instead."},
    )


async def test_changing_to_a_device_that_another_switch_took_ends_the_flow(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall"))
    result = await manual(hass, await start(hass, entry, DEVICE), FREE)
    add_switch(hass, entry, switch(FREE, "Other"))

    result = await go(hass, result, form(lights=["0"]))

    assert (result["reason"], result["description_placeholders"]) == (
        "conflict",
        {"error": f"Device {FREE} is already configured by another switch."},
    )


async def test_a_switch_deleted_meanwhile_ends_the_flow(hass: HomeAssistant) -> None:
    entry = await loaded(hass, switch(DEVICE, "Hall"))
    result = await settings_for(hass, entry, DEVICE, edit=DEVICE)
    hass.config_entries.async_remove_subentry(entry, subentry(entry, DEVICE).subentry_id)

    result = await go(hass, result, form(lights=["0"]))

    assert (result["reason"], result["description_placeholders"]) == (
        "not_found",
        {"error": f"No switch with device id {DEVICE} is configured."},
    )


async def test_saving_without_checking_checks_the_device_again(hass: HomeAssistant, hue: HueBridge) -> None:
    entry = await loaded(hass)
    hue.fail("/clip/v2/resource/light", 503)
    result = await choose(hass, await manual(hass, await start(hass, entry), DEVICE), "typed")
    result = await go(hass, result, form(light_names="Ceiling"))
    add_switch(hass, entry, switch(DEVICE, "Other"))

    result = await choose(hass, result, "save")

    assert (result["reason"], result["description_placeholders"]) == (
        "conflict",
        {"error": f"Device {DEVICE} is already configured. Edit that switch instead."},
    )


@pytest.mark.parametrize("edit", [None, DEVICE])
async def test_closing_the_dialog_while_the_bridge_is_asked_saves_nothing(
    hass: HomeAssistant, hue: HueBridge, edit: str | None
) -> None:
    entry = await loaded(hass, *([switch(DEVICE, "Hall")] if edit else []))
    result = await settings_for(hass, entry, DEVICE, edit=edit)
    before = switches_of(entry)
    asked = len(hue.requests)
    held = hue.hold("/clip/v2/resource/light")

    saving = asyncio.create_task(
        hass.config_entries.subentries.async_configure(result["flow_id"], form(name="New", lights=["0"]))
    )
    await until(lambda: len(hue.requests) > asked)
    hass.config_entries.subentries.async_abort(result["flow_id"])
    held.set()

    with pytest.raises(UnknownFlow):
        await saving
    assert switches_of(entry) == before


# What the frontend gets.


async def test_the_frontend_gets_each_step_over_http(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, devices: Devices
) -> None:
    """The steps as Home Assistant's flow API serializes them for the frontend, which fetches a progress step again
    when told that it changed: the progress, the section with the device key, and its field shown only to replace
    the key."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    entry = await loaded(hass, switch(OTHER, "Hallway switch", key=KEY))
    subentry_id = subentry(entry, OTHER).subentry_id
    path = "/api/config/config_entries/subentries/flow"

    async def send(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{path}/{flow_id}"
        response = await (client.get(url) if user_input is None else client.post(url, json=user_input))
        assert response.status == 200, await response.text()
        step: dict[str, Any] = await response.json()
        assert KEY not in json.dumps(step)
        return step

    response = await client.post(path, json={"handler": [entry.entry_id, SUBENTRY_SWITCH], "subentry_id": subentry_id})
    step = await response.json()
    assert KEY not in json.dumps(step)
    flow_id = step["flow_id"]
    assert (step["type"], step["step_id"], step["menu_options"]) == (
        "menu",
        "reconfigure",
        ["keep", "identify", "pick", "manual"],
    )

    step = await send({"next_step_id": "identify"})
    assert (step["type"], step["step_id"], step["progress_action"]) == ("progress", "identify", "identify")
    await until(lambda: devices.watched)
    devices.press(DEVICE)
    await hass.async_block_till_done()

    step = await send()
    assert (step["type"], step["step_id"]) == ("form", "settings")
    assert [field["name"] for field in step["data_schema"]] == ["name", "lights", "rooms", "zones", "advanced"]
    [advanced] = [field for field in step["data_schema"] if field["name"] == "advanced"]
    assert (advanced["type"], advanced["expanded"]) == ("expandable", True)
    assert [(field["name"], field.get("visible")) for field in advanced["schema"]] == [
        ("key_mode", None),
        ("new_device_key", {"field": "key_mode", "value": "replace"}),
    ]
    assert step["description_placeholders"]["key_warning"] == KEY_WARNING

    step = await send(form(name="Hall", lights=["0"], advanced={"key_mode": "keep", "new_device_key": "hidden"}))
    assert (step["type"], step["reason"]) == ("abort", "saved")
    assert switches_of(entry)[0][2][CONF_DEVICE_KEY] is None


async def test_a_new_switch_is_answered_over_http_without_its_key(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    entry = await loaded(hass)
    path = "/api/config/config_entries/subentries/flow"
    response = await client.post(path, json={"handler": [entry.entry_id, SUBENTRY_SWITCH]})
    flow_id = (await response.json())["flow_id"]
    await client.post(f"{path}/{flow_id}", json={"next_step_id": "manual"})
    await client.post(f"{path}/{flow_id}", json={"device_id": DEVICE})

    response = await client.post(f"{path}/{flow_id}", json=form(lights=["0"], advanced={"device_key": KEY}))

    answer = await response.json()
    assert (answer["type"], answer["title"]) == ("create_entry", DEVICE)
    assert KEY not in json.dumps(answer)
    assert switches_of(entry)[0][2][CONF_DEVICE_KEY] == KEY


async def test_a_choice_that_was_not_offered_is_refused(hass: HomeAssistant) -> None:
    result = await settings_for(hass, await loaded(hass))

    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(result["flow_id"], form(lights=["3"]))


# The texts.


@pytest.mark.parametrize(
    ("ago", "text"),
    [
        (timedelta(seconds=-61), "in 1 min"),
        (timedelta(seconds=-59.5), "in 59 s"),
        (timedelta(minutes=-30), "in 30 min"),
        (timedelta(hours=-2), "Nov 25 06:00"),
        (timedelta(seconds=-5), "just now"),
        (timedelta(seconds=4.4), "just now"),
        (timedelta(seconds=4.5), "5 s ago"),
        (timedelta(seconds=59), "59 s ago"),
        (timedelta(seconds=60), "1 min ago"),
        (timedelta(minutes=59, seconds=59), "59 min ago"),
        (timedelta(hours=3), "3 h ago"),
        (timedelta(hours=5, minutes=59), "5 h ago"),
        (timedelta(hours=7), "yesterday 21:00"),
        (timedelta(hours=29), "Nov 23 23:00"),
        (timedelta(days=330), "Dec 30, 2025 04:00"),
    ],
)
async def test_times_read_relative_to_now(hass: HomeAssistant, ago: timedelta, text: str) -> None:
    """In Home Assistant's time zone, which the tests set to US/Pacific: 'now' is 04:00 on 25 November there."""
    now = datetime.fromisoformat("2026-11-25T12:00:00+00:00")

    assert switch_flow.since(now - ago, now) == text


async def test_the_registry_is_the_loaded_entrys(hass: HomeAssistant) -> None:
    entry = await loaded(hass)

    assert switch_flow.lan_registry(entry) is entry.runtime_data.registry
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert switch_flow.lan_registry(entry) is None
