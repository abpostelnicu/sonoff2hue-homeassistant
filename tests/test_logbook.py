"""The logbook's entries for presses and their outcomes (decision 26): the runner's log lines, in Home Assistant's logbook
and on the switch's device."""

from collections.abc import Callable
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.logbook.models import LazyEventPartialState, async_event_to_row
from homeassistant.components.recorder import Recorder
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import mock_component
from pytest_homeassistant.components.recorder.common import async_wait_recording_done
from pytest_homeassistant.typing import WebSocketGenerator

from custom_components.sonoff2hue import logbook
from custom_components.sonoff2hue.const import DOMAIN, EVENT_PRESS, EVENT_TOGGLE
from custom_components.sonoff2hue.engine import toggler
from custom_components.sonoff2hue.engine.status import PRESS_DROPPED
from custom_components.sonoff2hue.runtime import INTEGRATION_LOGGER

from .fakes import BRIDGE_ID, DEVICE_ID, OTHER_DEVICE_ID, Bridge, Lan, paired, switch
from .test_runtime import HALL, settle, start, subentry_of, tick

LIGHTS_DOWN = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"
NOT_SWITCHED = (
    '{"errors":[{"description":"device (grouped_light) has communication issues, command (.on.on) may not have '
    'effect"}],"data":[]}'
)
HALL_SWITCH = {"device_id": "a1b2c3d4", "switch_id": DEVICE_ID, "name": "Hall"}

type Describe = Callable[[LazyEventPartialState], dict[str, str]]


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggler retries a failed request at once, instead of after 300 ms."""
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


def describers(hass: HomeAssistant) -> dict[str, Describe]:
    """What the integration tells the logbook: how to describe each of its events."""
    described: dict[str, Describe] = {}

    def describe_event(domain: str, event_type: str, describe: Describe) -> None:
        assert domain == DOMAIN
        described[event_type] = describe

    logbook.async_describe_events(hass, describe_event)
    return described


def describe(hass: HomeAssistant, event: Event[Any]) -> dict[str, str]:
    """The event's entry, as the logbook's live stream reads it."""
    return describers(hass)[event.event_type](LazyEventPartialState(async_event_to_row(event), {}))


@pytest.mark.parametrize(
    ("event_type", "data", "name", "message"),
    [
        (EVENT_PRESS, {**HALL_SWITCH, "seq": 12}, HALL, "wall switch operated (seq 12)."),
        (EVENT_PRESS, {**HALL_SWITCH, "seq": None}, HALL, "wall switch operated (seq (null))."),
        (
            EVENT_PRESS,
            {"device_id": None, "switch_id": DEVICE_ID, "name": None, "seq": 3},
            DEVICE_ID,
            "wall switch operated (seq 3).",
        ),
        (EVENT_PRESS, {**HALL_SWITCH, "name": " ", "seq": 3}, DEVICE_ID, "wall switch operated (seq 3)."),
        (
            EVENT_TOGGLE,
            {
                **HALL_SWITCH,
                "result": "toggled",
                "targets": ["light 'Ceiling'"],
                "turned_on": True,
                "changed": 1,
                "unchanged": 0,
                "failed": 0,
                "elapsed_ms": 85,
            },
            HALL,
            "turned ON light 'Ceiling' in 85 ms.",
        ),
        (
            EVENT_TOGGLE,
            {
                **HALL_SWITCH,
                "result": "toggled",
                "targets": ["light 'Ceiling'", "group 'Hall'", "light 'Desk'"],
                "turned_on": False,
                "changed": 1,
                "unchanged": 1,
                "failed": 1,
                "elapsed_ms": 120,
            },
            HALL,
            "turned OFF light 'Ceiling', group 'Hall', light 'Desk' in 120 ms (1 already off, 1 failed).",
        ),
        (
            EVENT_TOGGLE,
            {
                **HALL_SWITCH,
                "result": "toggled",
                "targets": ["light 'Ceiling'", "group 'Hall'"],
                "turned_on": True,
                "changed": 0,
                "unchanged": 2,
                "failed": 0,
                "elapsed_ms": 40,
            },
            HALL,
            "turned ON light 'Ceiling', group 'Hall' in 40 ms (2 already on).",
        ),
        (
            EVENT_TOGGLE,
            {
                **HALL_SWITCH,
                "result": "toggled",
                "targets": ["light 'Ceiling'", "group 'Hall'"],
                "turned_on": True,
                "changed": 0,
                "unchanged": 0,
                "failed": 2,
                "elapsed_ms": 4012,
            },
            HALL,
            "turned ON light 'Ceiling', group 'Hall' in 4012 ms (2 failed).",
        ),
        (
            EVENT_TOGGLE,
            {**HALL_SWITCH, "result": "failed", "targets": ["light 'Ceiling'"], "message": LIGHTS_DOWN},
            HALL,
            f"could not toggle the lights: {LIGHTS_DOWN}",
        ),
        (
            EVENT_TOGGLE,
            {**HALL_SWITCH, "result": "dropped", "targets": ["light 'Ceiling'"], "message": PRESS_DROPPED},
            HALL,
            "dropped a press that waited more than 5s for the bridge.",
        ),
        (
            EVENT_TOGGLE,
            {**HALL_SWITCH, "result": "stopped", "message": "Stopped."},
            HALL,
            "could not toggle the lights: Stopped.",
        ),
        (EVENT_PRESS, {}, "", "wall switch operated (seq (null))."),
        (EVENT_TOGGLE, {}, "", "could not toggle the lights: (null)"),
        (
            EVENT_TOGGLE,
            {"switch_id": 1000, "name": 7, "result": "toggled", "targets": "light 'Ceiling'", "unchanged": "1"},
            "7 (1000)",
            "turned OFF light 'Ceiling' in (null) ms.",
        ),
    ],
    ids=[
        "press",
        "press without a seq",
        "press of a switch without a name or device",
        "press of a switch with a blank name",
        "turned on",
        "turned off, one already off and one failed",
        "turned on, all already on",
        "turned on, all failed",
        "failed",
        "dropped",
        "another result",
        "press without data",
        "toggle without data",
        "values of other types",
    ],
)
async def test_each_event_is_described_as_the_runner_logs_it(
    hass: HomeAssistant, event_type: str, data: dict[str, Any], name: str, message: str
) -> None:
    """The switch's name is the entry's name, and the message is the rest of the line. The logbook also describes events
    that Home Assistant stored long ago, or that someone fired with other data, so nothing makes it fail."""
    assert describe(hass, Event(event_type, data)) == {"name": name, "message": message}


async def test_the_entries_are_the_runners_log_lines(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    """Every press, and every outcome of its toggle: "<name>: <message>" is the line that the runner logs."""
    caplog.set_level("INFO", logger=INTEGRATION_LOGGER.name)
    bridge.on["l2"] = True  # The room Hall is on, through its light Desk.
    await start(hass, paired(switch(lights=["Ceiling"], groups=["Hall"])))
    events: list[Event[Any]] = []

    @callback
    def capture(event: Event[Any]) -> None:
        events.append(event)

    for event_type in (EVENT_PRESS, EVENT_TOGGLE):
        hass.bus.async_listen(event_type, capture)
    lan.broadcast(1)
    await settle()
    caplog.clear()

    # The room goes off, its light Ceiling was off already, and the bridge doesn't switch the room.
    bridge.fail("/clip/v2/resource/grouped_light/g1", 207, NOT_SWITCHED)
    lan.broadcast(2)
    await settle()
    # Again, and the bridge switches the room off.
    bridge.heal()
    lan.broadcast(3)
    await settle()
    # The lights' state can't be read.
    bridge.fail("/clip/v2/resource/light", 503)
    lan.broadcast(4)
    await settle()
    # Both go on, slowly, while the next press waits more than 5 s.
    bridge.heal()
    held = bridge.hold("/clip/v2/resource/light")
    lan.broadcast(5)
    lan.broadcast(6)
    await settle()
    await tick(freezer, 6)
    held.set()
    await settle()

    entries = [describe(hass, event) for event in events]
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.sonoff2hue.engine.runner" and record.getMessage().startswith(f"{HALL}: ")
    ]
    assert [f"{entry['name']}: {entry['message']}" for entry in entries] == lines
    assert lines == [
        f"{HALL}: wall switch operated (seq 2).",
        f"{HALL}: turned OFF light 'Ceiling', group 'Hall' in 0 ms (1 already off, 1 failed).",
        f"{HALL}: wall switch operated (seq 3).",
        f"{HALL}: turned OFF light 'Ceiling', group 'Hall' in 0 ms (1 already off).",
        f"{HALL}: wall switch operated (seq 4).",
        f"{HALL}: could not toggle the lights: {LIGHTS_DOWN}",
        f"{HALL}: wall switch operated (seq 5).",
        f"{HALL}: wall switch operated (seq 6).",
        f"{HALL}: turned ON light 'Ceiling', group 'Hall' in 6000 ms.",
        f"{HALL}: dropped a press that waited more than 5s for the bridge.",
    ]


@pytest.mark.parametrize("logbook_first", [True, False], ids=["logbook first", "logbook after the integration"])
@pytest.mark.usefixtures("socket_enabled")
async def test_the_logbook_shows_the_entries_and_a_switchs_device_shows_its_own(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    lan: Lan,
    bridge: Bridge,
    logbook_first: bool,
) -> None:
    """The real logbook, with the events stored by the recorder: it finds the integration's descriptions whichever of
    the two Home Assistant sets up first, and matches an event to the switch's device by the ``device_id`` in its data.
    A switch whose subentry has no device yet is in the logbook, but on no device's page."""
    mock_component(hass, "frontend")
    if logbook_first:
        assert await async_setup_component(hass, "logbook", {})
    since = dt_util.utcnow()
    entry = paired(switch(), switch(OTHER_DEVICE_ID, "Desk", lights=["Desk"]))
    await start(hass, entry)
    if not logbook_first:
        assert await async_setup_component(hass, "logbook", {})
    devices = dr.async_get(hass)
    bridge_device = devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, BRIDGE_ID)})
    hall_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_of(entry).subentry_id,
        identifiers={(DOMAIN, DEVICE_ID)},
    )
    for device_id in (DEVICE_ID, OTHER_DEVICE_ID):
        lan.broadcast(1, device_id)
        lan.broadcast(2, device_id)
    await settle()
    await async_wait_recording_done(hass)
    client = await hass_ws_client()

    async def logbook_of(device_ids: list[str] | None) -> list[tuple[str, str, str]]:
        query: dict[str, Any] = {"type": "logbook/get_events", "start_time": since.isoformat()}
        if device_ids is not None:
            query["device_ids"] = device_ids
        await client.send_json_auto_id(query)
        response = await client.receive_json()
        assert response["success"]
        return [(entry["domain"], entry["name"], entry["message"]) for entry in response["result"]]

    hall = [
        (DOMAIN, HALL, "wall switch operated (seq 2)."),
        (DOMAIN, HALL, "turned ON light 'Ceiling' in 0 ms."),
    ]
    desk = [
        (DOMAIN, "Desk (1000999999)", "wall switch operated (seq 2)."),
        (DOMAIN, "Desk (1000999999)", "turned ON light 'Desk' in 0 ms."),
    ]
    assert await logbook_of(None) == hall + desk
    assert await logbook_of([hall_device.id]) == hall
    assert await logbook_of([bridge_device.id]) == []
