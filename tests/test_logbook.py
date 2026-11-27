"""The logbook (decision 26): Home Assistant's own entries for each press, the switch's Press and its Last action, and
nothing for the runtime's bus events, in the real logbook on the recorder and on the switch's device page. The lights
that a press switches are Home Assistant's (decision 40), with their own entries, as for whatever switches them."""

from collections import Counter
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events, mock_component
from pytest_homeassistant.components.recorder.common import async_wait_recording_done
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.sonoff2hue.const import DOMAIN, EVENT_PRESS, EVENT_TOGGLE

from .fakes import CEILING, DEVICE_ID, Lan, Lights, entry, switch
from .platforms import LAST_ACTION, ONLINE, PRESS, settle, start, tick

type Change = tuple[str, str]
"""A logbook entry: the entity whose state changed, and the new state, or for an event entity its event type."""


@pytest.fixture
async def logbook(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """The real logbook, on the recorder; its dependency, the frontend, counts as set up, as the tests don't have it."""
    mock_component(hass, "frontend")
    assert await async_setup_component(hass, "logbook", {})


async def press(lan: Lan, freezer: FrozenDateTimeFactory, *seqs: int) -> None:
    """Presses a second after what came before, and the status that the entities show 500 ms later. The logbook lists
    what happened after the time it is asked from, so the presses come after it."""
    await tick(freezer, 1)
    for seq in seqs:
        lan.broadcast(seq)
    await settle()
    await tick(freezer, 0.5)


class Logbook:
    """The logbook over the websocket API, as the frontend asks for it."""

    def __init__(self, hass: HomeAssistant, hass_ws_client: WebSocketGenerator, freezer: FrozenDateTimeFactory) -> None:
        self._hass = hass
        self._hass_ws_client = hass_ws_client
        self._freezer = freezer

    async def everything(self, since: str) -> list[dict[str, Any]]:
        return await self._get({"start_time": since})

    async def device_page(self, since: str, device_id: str) -> list[dict[str, Any]]:
        """A device's page asks for its entities and for the device."""
        entities = er.async_entries_for_device(er.async_get(self._hass), device_id)
        return await self._get(
            {"start_time": since, "entity_ids": [entity.entity_id for entity in entities], "device_ids": [device_id]}
        )

    async def _get(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """It lists what happened before the moment it is asked, so time goes on a second first."""
        await tick(self._freezer, 1)
        await async_wait_recording_done(self._hass)
        client = await self._hass_ws_client()
        await client.send_json_auto_id({"type": "logbook/get_events", **query})
        response = await client.receive_json()
        assert response["success"]
        entries: list[dict[str, Any]] = response["result"]
        return entries


def changes(entries: list[dict[str, Any]]) -> list[Change]:
    """The entries, each of them an entity's change; an event entity's shows its event type."""
    assert all("entity_id" in item for item in entries), entries
    return [(item["entity_id"], item.get("attributes", {}).get("event_type", item["state"])) for item in entries]


def moments(entries: list[dict[str, Any]]) -> list[Counter[Change]]:
    """The changes of each moment, in the order of the moments. The entities write what one status snapshot changes at
    the same moment, in the order in which they started following the status, which varies, as Home Assistant sets
    their platforms up side by side; and the logbook sorts its entries by their time alone. So the entries of one
    moment come in no fixed order."""
    grouped: list[Counter[Change]] = []
    when: object = None
    for item, change in zip(entries, changes(entries), strict=True):
        if not grouped or item["when"] != when:
            grouped.append(Counter())
            when = item["when"]
        grouped[-1][change] += 1
    return grouped


def together(*changes: Change) -> Counter[Change]:
    """Changes of the same moment, in any order."""
    return Counter(changes)


def switch_device(hass: HomeAssistant, config_entry: MockConfigEntry) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), config_entry.entry_id)
    assert device is not None
    return device


@pytest.mark.usefixtures("logbook")
async def test_each_press_gives_the_logbook_press_and_last_action_and_nothing_else(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    """The user's choice: Home Assistant's own entries, two for each press, also on the switch's device page, next to
    the light's own; the runtime's bus events are there for automations and the entities, and the logbook has nothing
    for them."""
    lights.add("ceiling")
    config_entry = entry(switch())
    await start(hass, config_entry)
    logbook = Logbook(hass, hass_ws_client, freezer)
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    started = dt_util.utcnow().isoformat()
    await press(lan, freezer, 1)  # The switch's first broadcast, its baseline: no press, but now it is online.
    since = dt_util.utcnow().isoformat()

    await press(lan, freezer, 2)
    await press(lan, freezer, 3)

    each_press = [
        together((PRESS, "press"), (CEILING, "on")),
        together((LAST_ACTION, "turned_on")),
        together((PRESS, "press"), (CEILING, "off")),
        together((LAST_ACTION, "turned_off")),
    ]
    assert moments(await logbook.everything(since)) == each_press
    assert moments(await logbook.everything(started)) == [together((ONLINE, "on")), *each_press]
    device = switch_device(hass, config_entry)
    assert changes(await logbook.device_page(since, device.id)) == [
        (PRESS, "press"),
        (LAST_ACTION, "turned_on"),
        (PRESS, "press"),
        (LAST_ACTION, "turned_off"),
    ]
    # The bus events were fired, and named the switch's device; the presses toggled the light.
    assert [event.data["seq"] for event in presses] == [2, 3]
    assert [event.data["result"] for event in toggles] == ["toggled", "toggled"]
    assert {event.data["device_id"] for event in [*presses, *toggles]} == {device.id}
    assert lights.turned() == [("turn_on", CEILING), ("turn_off", CEILING)]


@pytest.mark.usefixtures("logbook")
async def test_last_action_shows_only_when_its_state_changes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    """The logbook lists changes of a state, not of its attributes alone, and the entities show the status 500 ms after
    a change: an outcome like the one before, or one that another replaces within the 500 ms, adds only the press."""
    lights.add("ceiling")
    config_entry = entry(switch())
    await start(hass, config_entry)
    logbook = Logbook(hass, hass_ws_client, freezer)
    await press(lan, freezer, 1)
    since = dt_util.utcnow().isoformat()

    # The switch's light disabled: each press fails, and the second outcome is the first one again.
    registry = er.async_get(hass)
    registry.async_update_entity(CEILING, disabled_by=er.RegistryEntryDisabler.USER)
    await settle()
    await press(lan, freezer, 2)
    await press(lan, freezer, 3)
    # Enabled again, and two presses at once: the light turns on, then off, and Last action shows the newer.
    registry.async_update_entity(CEILING, disabled_by=None)
    await settle()
    await press(lan, freezer, 4, 5)

    assert lights.turned() == [("turn_on", CEILING), ("turn_off", CEILING)]
    assert moments(await logbook.everything(since)) == [
        together((PRESS, "press")),
        together((LAST_ACTION, "failed")),
        together((PRESS, "press")),
        together((PRESS, "press"), (PRESS, "press"), (CEILING, "on"), (CEILING, "off")),
        together((LAST_ACTION, "turned_off")),
    ]
    assert moments(await logbook.device_page(since, switch_device(hass, config_entry).id)) == [
        together((PRESS, "press")),
        together((LAST_ACTION, "failed")),
        together((PRESS, "press")),
        together((PRESS, "press"), (PRESS, "press")),
        together((LAST_ACTION, "turned_off")),
    ]
