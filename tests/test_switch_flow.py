"""The switch subentry flow (decision 24): every step, error, warning and abort, through Home Assistant's flow manager
for subentries, with the integration set up and its runtime running (decision 23).

The runtime listens on the fake network of ``tests/fakes.py``. The devices seen on the LAN are its registry, fed with
synthetic tracker results as its LAN monitor feeds it, or with broadcasts on that network. The lights are Home
Assistant's, in its state machine and entity registry (``Lights``), as the Hue integration's lights, rooms and zones
are (decision 40). Every result is checked against ``translations/en.json``: the texts it shows must exist, with the
placeholders they use.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from ipaddress import IPv4Address
import json
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
from homeassistant.data_entry_flow import EVENT_DATA_ENTRY_FLOW_PROGRESSED, FlowResultType, section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectSelector
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, async_fire_time_changed
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import switch_flow
from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    CONF_UNMIGRATED,
    DOMAIN,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine import switch_rules
from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
from custom_components.sonoff2hue.engine.press import SwitchEvent, SwitchEventKind, TrackedEvent, TrackerUpdate
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.switch_rules import SwitchInput, SwitchValidationError, SwitchWrite
from custom_components.sonoff2hue.switch_flow import IDENTIFY_TIMEOUT

from . import platforms
from .engine.fakes import until
from .fakes import CEILING, DEVICE_ADDRESS, DEVICE_ID, DEVICE_KEY, OTHER_DEVICE_ID, Lan, Lights, entry, switch

pytestmark = pytest.mark.usefixtures("lan")

Kind = SwitchEventKind
DEVICE = DEVICE_ID
OTHER = OTHER_DEVICE_ID
FREE = "1000aaaaaa"
SECOND = "1000bbbbbb"
SELECTED = f"Selected {DEVICE}: it reported a press."
USED = f"{OTHER} reported a press, but it is already used by Hallway switch. Operate the switch you want to add."
KEY_WARNING = (
    "The current key belongs to the previous device. Choose \u201cReplace it\u201d or \u201cRemove it\u201d when you "
    "change the device."
)
HALLWAY_SWITCH = switch(OTHER, "Hallway switch")


# The config entry and its switches.


async def loaded(hass: HomeAssistant, *switches: ConfigSubentryData) -> MockConfigEntry:
    """The loaded config entry, with these switches."""
    config_entry = entry(*switches)
    await platforms.start(hass, config_entry)
    return config_entry


def add_switch(hass: HomeAssistant, config_entry: MockConfigEntry, data: ConfigSubentryData) -> None:
    """Adds a switch as another dialog would, while the one under test is open."""
    hass.config_entries.async_add_subentry(
        config_entry,
        ConfigSubentry(
            data=MappingProxyType(data["data"]),
            subentry_type=data["subentry_type"],
            title=data["title"],
            unique_id=data["unique_id"],
        ),
    )


def subentry(config_entry: MockConfigEntry, device_id: str) -> ConfigSubentry:
    [found] = [item for item in config_entry.subentries.values() if item.unique_id == device_id]
    return found


def switches_of(config_entry: MockConfigEntry) -> list[tuple[str, str | None, dict[str, Any]]]:
    """The switch subentries: title, unique id and data."""
    return [(item.title, item.unique_id, dict(item.data)) for item in config_entry.subentries.values()]


def data_of(config_entry: MockConfigEntry) -> dict[str, Any]:
    """The data of the entry's only switch."""
    [(_, _, data)] = switches_of(config_entry)
    return data


def hue_group(lights: Lights, object_id: str, name: str, kind: str, *members: str) -> str:
    """The light of a Hue room or zone, as the Hue integration has it: marked as a group, of the type ``room`` or
    ``zone``, with the entity ids of its lights."""
    return lights.add(
        object_id, name=name, attributes={"is_hue_group": True, "hue_type": kind, "entity_id": list(members)}
    )


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
        [config_entry] = self._hass.config_entries.async_entries(DOMAIN)
        registry: DeviceRegistry = config_entry.runtime_data.registry
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
        # The frontend formats texts as ICU messages, where an apostrophe right before a brace starts quoted text, so
        # "Use room '{group}'" would show "Use room {group}".
        assert "'{" not in texts[key], f"{key} puts an apostrophe before a placeholder, which the frontend shows as is"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the flow doesn't give"
    return result


async def start(hass: HomeAssistant, config_entry: MockConfigEntry, edit: str | None = None) -> SubentryFlowResult:
    """Adds a switch, or edits the one with the device id ``edit``, as the integration's page starts it."""
    context: dict[str, Any] = {"source": SOURCE_USER}
    if edit is not None:
        context = {"source": SOURCE_RECONFIGURE, "subentry_id": subentry(config_entry, edit).subentry_id}
    result = await hass.config_entries.subentries.async_init((config_entry.entry_id, SUBENTRY_SWITCH), context=context)
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


def field_names(result: SubentryFlowResult) -> list[str]:
    return [str(marker) for marker in result["data_schema"].schema]


def collapsed(result: SubentryFlowResult) -> bool:
    advanced = next(value for marker, value in result["data_schema"].schema.items() if marker == "advanced")
    folded: bool = advanced.options["collapsed"]
    return folded


def form(**values: Any) -> dict[str, Any]:
    """The settings form's input: no name and an empty key, unless given, and the lights only when given, as the
    frontend leaves out an empty choice."""
    advanced = values.pop("advanced", {"device_key": ""})
    return {"name": "", **values, "advanced": advanced}


async def settings_for(
    hass: HomeAssistant, config_entry: MockConfigEntry, device_id: str = DEVICE, edit: str | None = None
) -> SubentryFlowResult:
    """The settings form, for a device entered by its id, or for the edited switch's own device."""
    result = await start(hass, config_entry, edit)
    if edit is not None and device_id == edit:
        result = await choose(hass, result, "keep")
    else:
        result = await manual(hass, result, device_id)
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "settings")
    return result


# The ways in.


async def test_adding_a_switch_offers_three_ways_to_its_device(hass: HomeAssistant) -> None:
    result = await start(hass, await loaded(hass))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "user",
        ["identify", "pick", "manual"],
    )


async def test_editing_a_switch_offers_to_keep_its_device_first(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, HALLWAY_SWITCH, switch(" 1000ABCDEF ", None))

    result = await start(hass, config_entry, OTHER)

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "reconfigure",
        ["keep", "identify", "pick", "manual"],
    )
    assert result["description_placeholders"] == {"switch": "Hallway switch", "device_id": OTHER}
    result = await start(hass, config_entry, DEVICE)

    assert result["description_placeholders"] == {"switch": DEVICE, "device_id": DEVICE}


@pytest.mark.parametrize("state", [ConfigEntryState.NOT_LOADED, ConfigEntryState.SETUP_RETRY])
@pytest.mark.parametrize("edit", [None, OTHER])
async def test_switches_wait_for_the_integration_to_load(
    hass: HomeAssistant, state: ConfigEntryState, edit: str | None
) -> None:
    config_entry = entry(HALLWAY_SWITCH)
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, state)

    result = await start(hass, config_entry, edit)

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "entry_not_loaded")


# Identify by press.


async def test_identify_selects_the_device_that_reports_a_press(
    hass: HomeAssistant, lan: Lan, devices: Devices
) -> None:
    """A MINIR4M that no switch maps, over the fake network: its first broadcast is its baseline, and without its
    device key every new ``seq`` is a press."""
    config_entry = await loaded(hass)
    lan.broadcast(1)
    result = await identify(hass, await start(hass, config_entry), devices)

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
    config_entry = await loaded(hass)
    pressed_at = dt_util.utcnow() - timedelta(seconds=5)
    devices.press(DEVICE, at=pressed_at)
    result = await identify(hass, await start(hass, config_entry), devices)

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
    config_entry = await loaded(hass)
    result = await identify(hass, await start(hass, config_entry), devices)
    stopped = devices.registry

    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert not watching(stopped)
    report(stopped, DEVICE, Kind.PRESS)
    await platforms.start(hass, config_entry)

    assert devices.registry is not stopped
    assert devices.watched
    devices.press(FREE)
    result = await done(hass, result)

    assert result["description_placeholders"]["device"] == f"Selected {FREE}: it reported a press."
    assert not devices.watched


async def test_identify_gives_up_while_the_entry_is_not_loaded(hass: HomeAssistant, devices: Devices) -> None:
    config_entry = await loaded(hass)
    result = await identify(hass, await start(hass, config_entry), devices)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=IDENTIFY_TIMEOUT))
    result = await done(hass, result)

    assert result["step_id"] == "identify_timeout"


async def test_closing_the_dialog_stops_identify(hass: HomeAssistant, devices: Devices) -> None:
    config_entry = await loaded(hass)
    result = await identify(hass, await start(hass, config_entry), devices)
    [flow] = hass.config_entries.subentries._progress.values()
    task = flow._identify
    assert task is not None

    hass.config_entries.subentries.async_abort(result["flow_id"])
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not devices.watched
    assert not config_entry._on_state_change


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
    config_entry = await loaded(hass, HALLWAY_SWITCH)
    now = dt_util.utcnow()
    devices.press(OTHER, at=now - timedelta(seconds=1))
    devices.see(SECOND, at=now - timedelta(minutes=6), address=None, device_type=None)
    devices.press(FREE, at=now - timedelta(seconds=12))
    devices.press(DEVICE, at=now - timedelta(minutes=3))
    devices.see("1000cccccc")

    result = await choose(hass, await start(hass, config_entry), "pick")

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
    config_entry = await loaded(hass, HALLWAY_SWITCH)
    devices.see(OTHER)
    devices.see(DEVICE)
    result = await choose(hass, await start(hass, config_entry), "pick")

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
    config_entry = await loaded(hass, HALLWAY_SWITCH)
    devices.see(OTHER)
    result = await choose(hass, await start(hass, config_entry, OTHER), "pick")

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
    config_entry = await loaded(hass)
    devices.see(DEVICE)
    result = await start(hass, config_entry)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
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
    """A switch of sonoff2hue 1.x, imported from a file that the form's checks never saw, can have an id that the form
    refuses, which then has to be entered anew."""
    config_entry = await loaded(hass, switch("abc", "Imported"))

    result = await choose(hass, await start(hass, config_entry, "abc"), "keep")

    assert (result["step_id"], result["errors"], result["description_placeholders"]) == (
        "manual",
        {"device_id": "invalid_device"},
        {"error": "A device id has 4 to 40 letters and digits."},
    )


# The lights.


async def test_a_switch_added_from_the_lan_toggles_its_lights(hass: HomeAssistant, lan: Lan, lights: Lights) -> None:
    """From a broadcast on the fake network to the switch that the runtime then runs, as a user adds one: its lights are
    kept by their entity registry ids, and its presses toggle them."""
    lights.add("ceiling", name="Ceiling")
    desk = lights.add("desk", name="Desk")
    config_entry = await loaded(hass)
    lan.broadcast(1)
    result = await choose(hass, await start(hass, config_entry), "pick")

    result = await go(hass, result, {"device": DEVICE})

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "settings", {})
    assert field_names(result) == ["name", "lights", "advanced"]
    assert [suggested(result, field) for field in ("name", "lights")] == ["", []]
    assert suggested(result, "advanced", "device_key") == ""
    assert collapsed(result)
    assert result["description_placeholders"] == {
        "device": f"Device {DEVICE}.",
        "missing": "",
        "key_warning": "",
        "base_error": "",
        "name_error": "",
        "key_error": "",
        "lights_error": "",
    }
    result = await go(hass, result, form(name=" Hall ", lights=[CEILING, desk]))

    assert (result["type"], result["title"], result["description"], result["description_placeholders"]) == (
        FlowResultType.CREATE_ENTRY,
        "Hall",
        "added",
        {"switch": "Hall"},
    )
    assert switches_of(config_entry) == [
        (
            "Hall",
            DEVICE,
            {
                CONF_DEVICE_ID: DEVICE,
                CONF_NAME: "Hall",
                CONF_DEVICE_KEY: None,
                CONF_TARGETS: [lights.target(CEILING), lights.target(desk)],
            },
        )
    ]
    await platforms.settle()
    # The switch's runner starts with the switch: its first broadcast is the switch's state, the next one a press.
    lan.broadcast(2)
    lan.broadcast(3)
    await platforms.settle()

    assert sorted(lights.turned()) == [("turn_on", CEILING), ("turn_on", desk)]


async def test_a_light_outside_the_entity_registry_is_kept_by_its_entity_id(
    hass: HomeAssistant, lights: Lights
) -> None:
    lights.add("ceiling")
    lamp = lights.add("lamp", registered=False)
    config_entry = await loaded(hass)

    result = await go(hass, await settings_for(hass, config_entry), form(lights=[lamp, CEILING]))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data_of(config_entry)[CONF_TARGETS] == [lamp, lights.target(CEILING)]


async def test_a_light_chosen_twice_is_kept_once(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass)
    result = await settings_for(hass, config_entry)

    result = await go(hass, result, form(name="x" * 65, lights=[CEILING, "light.desk", CEILING]))

    assert suggested(result, "lights") == [CEILING, "light.desk"]
    result = await go(hass, result, form(name="Hall", lights=[CEILING, "light.desk", CEILING]))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data_of(config_entry)[CONF_TARGETS] == [CEILING, "light.desk"]


async def test_editing_a_switch_shows_its_lights_by_their_entity_ids_now(hass: HomeAssistant, lights: Lights) -> None:
    """A light keeps its place in the switch when its entity id is renamed."""
    lights.add("ceiling")
    desk = lights.add("desk")
    desk_target = lights.target(desk)
    config_entry = await loaded(hass, switch(targets=[lights.target(CEILING), desk_target, "light.porch"]))
    er.async_get(hass).async_update_entity(desk, new_entity_id="light.reading")

    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    assert [suggested(result, field) for field in ("name", "lights")] == [
        "Hall",
        [CEILING, "light.reading", "light.porch"],
    ]
    assert result["description_placeholders"]["missing"] == ""
    result = await go(hass, result, form(name="Reading", lights=["light.reading", "light.porch"]))

    assert (result["type"], result["reason"], result["description_placeholders"]) == (
        FlowResultType.ABORT,
        "saved",
        {"switch": "Reading"},
    )
    assert switches_of(config_entry) == [
        (
            "Reading",
            DEVICE,
            {
                CONF_DEVICE_ID: DEVICE,
                CONF_NAME: "Reading",
                CONF_DEVICE_KEY: None,
                CONF_TARGETS: [desk_target, "light.porch"],
            },
        )
    ]


async def test_editing_a_switch_names_the_lights_that_home_assistant_no_longer_has(
    hass: HomeAssistant, lights: Lights
) -> None:
    """A removed light can't be chosen, so the form names it and saves the switch without it; a disabled light stays
    chosen."""
    lights.add("ceiling")
    desk = lights.add("desk")
    lamp = lights.add("lamp")
    registry = er.async_get(hass)
    forgotten = "0123456789abcdef0123456789abcdef"
    targets = [lights.target(CEILING), lights.target(desk), forgotten, lights.target(lamp)]
    config_entry = await loaded(hass, switch(targets=targets))
    registry.async_remove(desk)
    registry.async_update_entity(lamp, disabled_by=er.RegistryEntryDisabler.USER)

    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    assert suggested(result, "lights") == [CEILING, lamp]
    assert result["description_placeholders"]["missing"] == (
        "\n\nNot chosen, as Home Assistant no longer has them:\n"
        "- light.desk was removed from Home Assistant.\n"
        "- a light was removed from Home Assistant."
    )
    result = await go(hass, result, form(name="Hall", lights=[CEILING, lamp]))

    assert result["reason"] == "saved"
    assert data_of(config_entry)[CONF_TARGETS] == [lights.target(CEILING), lights.target(lamp)]


async def test_a_migrated_switch_asks_for_the_lights_that_it_lost_until_it_is_saved(
    hass: HomeAssistant, lights: Lights
) -> None:
    """The lights, rooms and zones of sonoff2hue 1.x that the migration found no light for are named on the form, and
    saving the switch forgets them, as its lights have been chosen again."""
    lights.add("ceiling")
    lost = {CONF_UNMIGRATED: ["Kitchen", "room:Attic"]}
    config_entry = await loaded(hass, switch(targets=[lights.target(CEILING)], key=DEVICE_KEY, extra=lost))

    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    assert suggested(result, "lights") == [CEILING]
    assert result["description_placeholders"]["missing"] == (
        "\n\nFrom sonoff2hue 1.x, without a light in Home Assistant: `Kitchen`, `room:Attic`. Choose their lights again."
    )
    result = await go(hass, result, form(name="Hall", lights=[CEILING], advanced={"key_mode": "keep"}))

    assert result["reason"] == "saved"
    assert data_of(config_entry) == {
        CONF_DEVICE_ID: DEVICE,
        CONF_NAME: "Hall",
        CONF_DEVICE_KEY: DEVICE_KEY,
        CONF_TARGETS: [lights.target(CEILING)],
    }


# The device key.


@pytest.mark.parametrize(
    ("mode", "new_key", "saved"),
    [("keep", None, DEVICE_KEY), ("replace", " new-key ", "new-key"), ("remove", None, None)],
)
async def test_the_device_key_can_be_kept_replaced_or_removed(
    hass: HomeAssistant, mode: str, new_key: str | None, saved: str | None
) -> None:
    config_entry = await loaded(hass, switch(key=DEVICE_KEY))
    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    assert field_names(result) == ["name", "lights", "advanced"]
    assert (suggested(result, "advanced", "key_mode"), suggested(result, "advanced", "new_device_key")) == ("keep", "")
    assert collapsed(result)
    keys = {"key_mode": mode} | ({} if new_key is None else {"new_device_key": new_key})
    result = await go(hass, result, form(name="Hall", lights=[CEILING], advanced=keys))

    assert result["reason"] == "saved"
    assert data_of(config_entry)[CONF_DEVICE_KEY] == saved


async def test_a_new_key_is_taken_only_to_replace_the_current_one(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch(key=DEVICE_KEY))
    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    await go(hass, result, form(lights=[CEILING], advanced={"key_mode": "keep", "new_device_key": "other"}))

    assert data_of(config_entry)[CONF_DEVICE_KEY] == DEVICE_KEY


async def test_changing_the_device_warns_about_a_kept_key(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch(key=DEVICE_KEY))
    subentry_id = subentry(config_entry, DEVICE).subentry_id
    result = await manual(hass, await start(hass, config_entry, DEVICE), FREE)

    assert result["description_placeholders"]["key_warning"] == KEY_WARNING
    assert not collapsed(result)
    result = await go(hass, result, form(lights=[CEILING], advanced={"key_mode": "remove"}))

    assert result["reason"] == "saved"
    assert config_entry.subentries[subentry_id].unique_id == FREE
    assert switches_of(config_entry) == [
        (FREE, FREE, {CONF_DEVICE_ID: FREE, CONF_NAME: None, CONF_DEVICE_KEY: None, CONF_TARGETS: [CEILING]})
    ]


async def test_a_changed_device_drops_a_kept_key(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch(key=DEVICE_KEY))
    result = await manual(hass, await start(hass, config_entry, DEVICE), " 1000AAAAAA ")

    assert result["description_placeholders"]["device"] == "Device 1000AAAAAA."
    result = await go(hass, result, form(name="Hall", lights=[CEILING], advanced={"key_mode": "keep"}))

    assert (result["reason"], result["description_placeholders"]) == ("saved", {"switch": "Hall"})
    assert switches_of(config_entry) == [
        ("Hall", FREE, {CONF_DEVICE_ID: FREE, CONF_NAME: "Hall", CONF_DEVICE_KEY: None, CONF_TARGETS: [CEILING]})
    ]


async def test_a_new_switch_may_have_a_key(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass)
    result = await manual(hass, await start(hass, config_entry), " 1000ABCDEF ")

    result = await go(hass, result, form(lights=[CEILING], advanced={"device_key": f" {DEVICE_KEY} "}))

    assert (result["title"], result["description_placeholders"]) == (DEVICE, {"switch": "1000ABCDEF"})
    assert data_of(config_entry)[CONF_DEVICE_KEY] == DEVICE_KEY


# The form's checks, then the switch rules.


async def test_the_form_checks_everything_it_can_at_once(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch(key=DEVICE_KEY))
    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    result = await go(hass, result, form(name="x" * 65, advanced={"key_mode": "replace", "new_device_key": " "}))

    assert result["errors"] == {"name": "invalid_name", "lights": "invalid_lights", "advanced": "invalid_device_key"}
    placeholders = result["description_placeholders"]
    assert [placeholders[key] for key in ("name_error", "lights_error", "key_error", "base_error")] == [
        "Use at most 64 characters.",
        "Choose at least one light.",
        "Enter the new device key, or keep or remove the current one.",
        "",
    ]
    assert not collapsed(result)
    assert [suggested(result, field) for field in ("name", "lights")] == ["x" * 65, []]
    assert (suggested(result, "advanced", "key_mode"), suggested(result, "advanced", "new_device_key")) == (
        "replace",
        " ",
    )
    assert data_of(config_entry)[CONF_NAME] == "Hall"


async def test_the_switch_rules_check_the_rest(hass: HomeAssistant) -> None:
    result = await settings_for(hass, await loaded(hass))

    result = await go(hass, result, form(name="Hall\x85x\x07", lights=[CEILING], advanced={"device_key": "a key"}))

    assert result["errors"] == {"name": "invalid_name", "advanced": "invalid_device_key"}
    placeholders = result["description_placeholders"]
    assert (placeholders["name_error"], placeholders["key_error"]) == (
        "The name contains control characters.",
        "A device key has at most 128 characters and no spaces.",
    )
    assert suggested(result, "lights") == [CEILING]
    assert suggested(result, "advanced", "device_key") == "a key"
    assert not collapsed(result)


async def test_the_switch_rules_take_at_most_50_lights(hass: HomeAssistant) -> None:
    chosen = [f"light.lamp_{number}" for number in range(51)]

    result = await go(hass, await settings_for(hass, await loaded(hass)), form(lights=chosen))

    assert (result["errors"], result["description_placeholders"]["lights_error"]) == (
        {"lights": "invalid_lights"},
        "Choose at most 50 lights.",
    )
    assert suggested(result, "lights") == chosen


async def test_rules_messages_without_a_field_of_their_own_go_on_the_form(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device has no field on the settings form, and the rules' messages about it, or about keys that no field
    has, show on the form as a whole. The form checks the device as it is chosen, so here the rules have a stand-in."""

    def refuse(switches: Sequence[SwitchSettings], body: SwitchInput) -> SwitchWrite:
        raise SwitchValidationError({"deviceId": ["The device id changed."], "other": ["Something else."]})

    monkeypatch.setattr(switch_rules, "create_switch", refuse)

    result = await go(hass, await settings_for(hass, await loaded(hass)), form(name="Hall", lights=[CEILING]))

    assert (result["step_id"], result["errors"]) == ("settings", {"base": "not_saved"})
    assert result["description_placeholders"]["base_error"] == "The device id changed. Something else."


# A room or zone in place of many lights (decision 34).


async def test_the_room_of_exactly_the_chosen_lights_is_offered_in_their_place(
    hass: HomeAssistant, lights: Lights
) -> None:
    ceiling = lights.add("ceiling", name="Ceiling")
    floor = lights.add("floor_lamp", name="Floor lamp")
    room = hue_group(lights, "living_room", "Living room", "room", ceiling, floor)
    config_entry = await loaded(hass)

    result = await go(hass, await settings_for(hass, config_entry), form(name="Hall", lights=[floor, ceiling]))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "group_offer",
        ["use_room", "keep_lights"],
    )
    assert result["description_placeholders"] == {"group": "Living room", "lights": "Floor lamp, Ceiling"}
    assert switches_of(config_entry) == []
    result = await choose(hass, result, "use_room")

    assert (result["type"], result["title"], result["description_placeholders"]) == (
        FlowResultType.CREATE_ENTRY,
        "Hall",
        {"switch": "Hall"},
    )
    assert data_of(config_entry)[CONF_TARGETS] == [lights.target(room)]


async def test_the_zone_of_exactly_the_chosen_lights_is_offered_in_their_place(
    hass: HomeAssistant, lights: Lights
) -> None:
    members = [lights.add(object_id, name=name) for object_id, name in [("ceiling", "Ceiling"), ("hallway", "Hallway")]]
    zone = hue_group(lights, "downstairs", "Downstairs", "zone", *members)
    config_entry = await loaded(hass)

    result = await go(hass, await settings_for(hass, config_entry), form(lights=members))

    assert (result["step_id"], result["menu_options"], result["description_placeholders"]) == (
        "group_offer",
        ["use_zone", "keep_lights"],
        {"group": "Downstairs", "lights": "Ceiling, Hallway"},
    )
    result = await choose(hass, result, "use_zone")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data_of(config_entry)[CONF_TARGETS] == [lights.target(zone)]


async def test_keeping_the_lights_saves_them_as_they_were_chosen(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling", name="Ceiling")
    floor = lights.add("floor_lamp", name="Floor lamp")
    hue_group(lights, "living_room", "Living room", "room", ceiling, floor)
    config_entry = await loaded(hass)
    result = await go(hass, await settings_for(hass, config_entry), form(name="Hall", lights=[floor, ceiling]))

    result = await choose(hass, result, "keep_lights")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data_of(config_entry)[CONF_TARGETS] == [lights.target(floor), lights.target(ceiling)]


async def test_editing_a_switch_offers_the_room_of_its_lights(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling", name="Ceiling")
    floor = lights.add("floor_lamp", name="Floor lamp")
    room = hue_group(lights, "living_room", "Living room", "room", ceiling, floor)
    hall = switch(key=DEVICE_KEY, targets=[lights.target(ceiling), lights.target(floor)])
    config_entry = await loaded(hass, hall)
    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)

    result = await go(
        hass, result, form(name="Hall", lights=suggested(result, "lights"), advanced={"key_mode": "keep"})
    )

    assert (result["step_id"], result["description_placeholders"]) == (
        "group_offer",
        {"group": "Living room", "lights": "Ceiling, Floor lamp"},
    )
    result = await choose(hass, result, "use_room")

    assert (result["type"], result["reason"], result["description_placeholders"]) == (
        FlowResultType.ABORT,
        "saved",
        {"switch": "Hall"},
    )
    assert switches_of(config_entry) == [
        (
            "Hall",
            DEVICE,
            {
                CONF_DEVICE_ID: DEVICE,
                CONF_NAME: "Hall",
                CONF_DEVICE_KEY: DEVICE_KEY,
                CONF_TARGETS: [lights.target(room)],
            },
        )
    ]


async def test_the_switch_rules_check_a_switch_after_its_room_is_chosen(hass: HomeAssistant, lights: Lights) -> None:
    """The room is offered once the form's own checks pass; the switch rules then check the switch with the room."""
    ceiling = lights.add("ceiling", name="Ceiling")
    floor = lights.add("floor_lamp", name="Floor lamp")
    room = hue_group(lights, "living_room", "Living room", "room", ceiling, floor)
    config_entry = await loaded(hass)
    result = await go(hass, await settings_for(hass, config_entry), form(name="Hall\x07", lights=[ceiling, floor]))

    result = await choose(hass, result, "use_room")

    assert (result["step_id"], result["errors"]) == ("settings", {"name": "invalid_name"})
    assert result["description_placeholders"]["name_error"] == "The name contains control characters."
    assert suggested(result, "lights") == [room]
    assert switches_of(config_entry) == []


# Another switch, or the edited one, changed meanwhile.


async def test_a_device_that_another_switch_took_meanwhile_ends_the_flow(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass)
    result = await settings_for(hass, config_entry)
    add_switch(hass, config_entry, switch(DEVICE, "Other"))

    result = await go(hass, result, form(lights=[CEILING]))

    assert (result["type"], result["reason"], result["description_placeholders"]) == (
        FlowResultType.ABORT,
        "conflict",
        {"error": f"Device {DEVICE} is already configured. Edit that switch instead."},
    )


async def test_changing_to_a_device_that_another_switch_took_ends_the_flow(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch())
    result = await manual(hass, await start(hass, config_entry, DEVICE), FREE)
    add_switch(hass, config_entry, switch(FREE, "Other"))

    result = await go(hass, result, form(lights=[CEILING]))

    assert (result["reason"], result["description_placeholders"]) == (
        "conflict",
        {"error": f"Device {FREE} is already configured by another switch."},
    )


async def test_a_switch_deleted_meanwhile_ends_the_flow(hass: HomeAssistant) -> None:
    config_entry = await loaded(hass, switch())
    result = await settings_for(hass, config_entry, DEVICE, edit=DEVICE)
    hass.config_entries.async_remove_subentry(config_entry, subentry(config_entry, DEVICE).subentry_id)

    result = await go(hass, result, form(lights=[CEILING]))

    assert (result["reason"], result["description_placeholders"]) == (
        "not_found",
        {"error": f"No switch with device id {DEVICE} is configured."},
    )


# What the frontend gets.

FLOWS = "/api/config/config_entries/subentries/flow"


async def test_the_frontend_gets_each_step_over_http(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, devices: Devices, lights: Lights
) -> None:
    """The steps as Home Assistant's flow API serializes them for the frontend, which fetches a progress step again
    when told that it changed: the progress, the lights' entity picker, the section with the device key, and its field
    shown only to replace the key. No answer holds the key."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    lights.add("ceiling")
    config_entry = await loaded(hass, switch(OTHER, "Hallway switch", key=DEVICE_KEY, targets=[lights.target(CEILING)]))
    subentry_id = subentry(config_entry, OTHER).subentry_id

    async def send(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{FLOWS}/{flow_id}"
        response = await (client.get(url) if user_input is None else client.post(url, json=user_input))
        assert response.status == 200, await response.text()
        step: dict[str, Any] = await response.json()
        assert DEVICE_KEY not in json.dumps(step)
        return step

    response = await client.post(
        FLOWS, json={"handler": [config_entry.entry_id, SUBENTRY_SWITCH], "subentry_id": subentry_id}
    )
    step = await response.json()
    assert DEVICE_KEY not in json.dumps(step)
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
    assert [field["name"] for field in step["data_schema"]] == ["name", "lights", "advanced"]
    [chosen] = [field for field in step["data_schema"] if field["name"] == "lights"]
    assert (chosen["selector"], chosen["description"]) == (
        {"entity": {"domain": ["light"], "multiple": True, "reorder": False}},
        {"suggested_value": [CEILING]},
    )
    [advanced] = [field for field in step["data_schema"] if field["name"] == "advanced"]
    assert (advanced["type"], advanced["expanded"]) == ("expandable", True)
    assert [(field["name"], field.get("visible")) for field in advanced["schema"]] == [
        ("key_mode", None),
        ("new_device_key", {"field": "key_mode", "value": "replace"}),
    ]
    assert step["description_placeholders"]["key_warning"] == KEY_WARNING

    step = await send(form(name="Hall", lights=[CEILING], advanced={"key_mode": "keep", "new_device_key": "hidden"}))
    assert (step["type"], step["reason"]) == ("abort", "saved")
    assert data_of(config_entry)[CONF_DEVICE_KEY] is None


async def test_a_new_switch_is_answered_over_http_without_its_key(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, lights: Lights
) -> None:
    """Through the room offered for its lights, which a fetch of the flow shows again, to the new switch, which the
    answer holds without its data."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    ceiling = lights.add("ceiling", name="Ceiling")
    floor = lights.add("floor_lamp", name="Floor lamp")
    room = hue_group(lights, "living_room", "Living room", "room", ceiling, floor)
    config_entry = await loaded(hass)
    response = await client.post(FLOWS, json={"handler": [config_entry.entry_id, SUBENTRY_SWITCH]})
    flow_id = (await response.json())["flow_id"]

    async def send(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{FLOWS}/{flow_id}"
        response = await (client.get(url) if user_input is None else client.post(url, json=user_input))
        assert response.status == 200, await response.text()
        step: dict[str, Any] = await response.json()
        return step

    steps = [
        await send({"next_step_id": "manual"}),
        await send({"device_id": DEVICE}),
        await send(form(lights=[ceiling, floor], advanced={"device_key": DEVICE_KEY})),
        await send(),
        await send({"next_step_id": "use_room"}),
    ]

    assert [(step["type"], step.get("step_id")) for step in steps] == [
        ("form", "manual"),
        ("form", "settings"),
        ("menu", "group_offer"),
        ("menu", "group_offer"),
        ("create_entry", None),
    ]
    for offer in steps[2:4]:
        assert (offer["menu_options"], offer["description_placeholders"]) == (
            ["use_room", "keep_lights"],
            {"group": "Living room", "lights": "Ceiling, Floor lamp"},
        )
    assert (steps[4]["title"], "data" in steps[4]) == (DEVICE, False)
    assert DEVICE_KEY not in json.dumps(steps)
    assert data_of(config_entry) == {
        CONF_DEVICE_ID: DEVICE,
        CONF_NAME: None,
        CONF_DEVICE_KEY: DEVICE_KEY,
        CONF_TARGETS: [lights.target(room)],
    }


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
