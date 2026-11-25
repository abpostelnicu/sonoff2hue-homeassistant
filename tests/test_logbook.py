"""The logbook (decision 26): Home Assistant's own entries for each press, the switch's Press and its Last action, and
nothing for the runtime's bus events, in the real logbook on the recorder and on the switch's device page."""

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import async_capture_events, mock_component
from pytest_homeassistant.components.recorder.common import async_wait_recording_done
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.sonoff2hue.const import DOMAIN, EVENT_PRESS, EVENT_TOGGLE
from custom_components.sonoff2hue.engine import toggler

from .fakes import DEVICE_ID, Bridge, Lan, paired, switch
from .platforms import LAST_ACTION, ONLINE, PRESS, REACHABLE, settle, start, tick

type Change = tuple[str, str]
"""A logbook entry: the entity whose state changed, and the new state, or for an event entity its event type."""


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggler retries a failed request at once, instead of after 300 ms."""
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


@pytest.fixture
async def logbook(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """The real logbook, on the recorder; its dependency, the frontend, counts as set up, as the tests don't have it."""
    mock_component(hass, "frontend")
    assert await async_setup_component(hass, "logbook", {})


async def press(lan: Lan, freezer: FrozenDateTimeFactory, seq: int) -> None:
    """A press a second after what came before, and the status that the entities show 500 ms later. The logbook lists
    what happened after the time it is asked from, so the press comes after it."""
    await tick(freezer, 1)
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
    assert all("entity_id" in entry for entry in entries), entries
    return [(entry["entity_id"], entry.get("attributes", {}).get("event_type", entry["state"])) for entry in entries]


@pytest.mark.usefixtures("logbook", "socket_enabled")
async def test_each_press_gives_the_logbook_press_and_last_action_and_nothing_else(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """The user's choice: Home Assistant's own entries, two for each press, also on the switch's device page; the
    runtime's bus events are there for automations and the entities, and the logbook has nothing for them."""
    entry = paired(switch())
    await start(hass, entry)
    logbook = Logbook(hass, hass_ws_client, freezer)
    presses = async_capture_events(hass, EVENT_PRESS)
    toggles = async_capture_events(hass, EVENT_TOGGLE)
    started = dt_util.utcnow().isoformat()
    await press(lan, freezer, 1)  # The switch's first broadcast, its baseline: no press, but now it is online.
    since = dt_util.utcnow().isoformat()

    await press(lan, freezer, 2)
    await press(lan, freezer, 3)

    each_press = [(PRESS, "press"), (LAST_ACTION, "turned_on"), (PRESS, "press"), (LAST_ACTION, "turned_off")]
    assert changes(await logbook.everything(since)) == each_press
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)
    assert device is not None
    assert changes(await logbook.device_page(since, device.id)) == each_press
    assert changes(await logbook.everything(started)) == [(ONLINE, "on"), *each_press]
    # The bus events were fired, and named the switch's device.
    assert [event.data["seq"] for event in presses] == [2, 3]
    assert [event.data["result"] for event in toggles] == ["toggled", "toggled"]
    assert {event.data["device_id"] for event in [*presses, *toggles]} == {device.id}


@pytest.mark.usefixtures("logbook", "socket_enabled")
async def test_last_action_shows_only_when_its_state_changes(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """The logbook lists changes of a state, not of its attributes alone, and the entities show the status 500 ms after
    a change: an outcome like the one before, or one that another replaces within the 500 ms, adds only the press."""
    entry = paired(switch())
    await start(hass, entry)
    logbook = Logbook(hass, hass_ws_client, freezer)
    await press(lan, freezer, 1)
    since = dt_util.utcnow().isoformat()

    # The bridge can't be read twice: the second outcome is the first one again. The first one also shows on the
    # bridge's device, as Reachable goes off.
    bridge.fail("/clip/v2/resource/light", 503)
    await press(lan, freezer, 2)
    await press(lan, freezer, 3)
    # A press that the bridge takes 6 s to toggle, and the next, dropped as it waited more than 5 s: the toggle and the
    # dropped press come together, and Last action shows the newer.
    bridge.heal()
    held = bridge.hold("/clip/v2/resource/light")
    await tick(freezer, 1)
    lan.broadcast(4)
    lan.broadcast(5)
    await settle()
    await tick(freezer, 6)
    held.set()
    await settle()
    await tick(freezer, 0.5)

    assert changes(await logbook.everything(since)) == [
        (PRESS, "press"),
        (REACHABLE, "off"),
        (LAST_ACTION, "failed"),
        (PRESS, "press"),
        (PRESS, "press"),
        (PRESS, "press"),
        (REACHABLE, "on"),
        (LAST_ACTION, "dropped"),
    ]
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id)
    assert device is not None
    assert changes(await logbook.device_page(since, device.id)) == [
        (PRESS, "press"),
        (LAST_ACTION, "failed"),
        (PRESS, "press"),
        (PRESS, "press"),
        (PRESS, "press"),
        (LAST_ACTION, "dropped"),
    ]
