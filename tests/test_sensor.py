"""The sensors (decision 25): Status and eWeLink devices seen on the bridge's device, and each switch's Last action,
with the LAN on a fake network and the Hue bridge in memory."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import ATTR_OPTIONS, SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.engine import toggler
from custom_components.sonoff2hue.engine.status import PRESS_DROPPED
from custom_components.sonoff2hue.engine.supervisor import KEY_REJECTED, NO_SWITCHES, NOT_PAIRED

from .fakes import BRIDGE_ADDRESS, OTHER_DEVICE_ID, Bridge, Lan, paired, switch
from .platforms import (
    BRIDGE_DOWN,
    DEVICES_SEEN,
    LAST_ACTION,
    RUNNING,
    STATUS,
    UNAUTHORIZED,
    settle,
    start,
    state_of,
    tick,
)

HALL = "Hall (1000abcdef)"
LIGHTS_DOWN = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggler retries a failed request at once, instead of after 300 ms."""
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


# Status.


@pytest.mark.parametrize(
    ("entry", "fault", "state", "message", "problems"),
    [
        (paired(switch()), None, "running", RUNNING, []),
        (
            paired(switch()),
            (503, "{}"),
            "connecting",
            f"Waiting for the Hue bridge at {BRIDGE_ADDRESS}: {BRIDGE_DOWN}",
            [],
        ),
        (paired(switch()), (403, UNAUTHORIZED), "error", KEY_REJECTED, []),
        (
            paired(switch(lights=["Kitchen"])),
            None,
            "invalid_config",
            "Some lights, rooms or zones were not found on the Hue bridge.",
            [f"{HALL}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Desk'"],
        ),
        (paired(), None, "not_configured", NO_SWITCHES, []),
        (paired(switch(), app_key=None), None, "not_configured", NOT_PAIRED, []),
    ],
    ids=["running", "connecting", "error", "invalid config", "no switch", "not paired"],
)
async def test_status_shows_the_service(
    hass: HomeAssistant,
    lan: Lan,
    bridge: Bridge,
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    fault: tuple[int, str] | None,
    state: str,
    message: str,
    problems: list[str],
) -> None:
    if fault is not None:
        bridge.fail("/clip/v2/resource/bridge", *fault)
    started = dt_util.utcnow()

    await start(hass, entry)
    await tick(freezer, 0.5)
    await hass.async_block_till_done()

    status = state_of(hass, STATUS)
    assert status.state == state
    assert status.attributes["message"] == message
    assert status.attributes["problems"] == problems
    assert status.attributes["since"] == started
    assert status.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.ENUM
    assert status.attributes[ATTR_OPTIONS] == ["not_configured", "invalid_config", "connecting", "running", "error"]


async def test_status_moves_since_with_each_change(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    bridge.fail("/clip/v2/resource/bridge", 503)
    started = dt_util.utcnow()
    await start(hass, paired(switch()))
    await tick(freezer, 0.5)

    assert (state_of(hass, STATUS).state, state_of(hass, STATUS).attributes["since"]) == ("connecting", started)

    # Waiting again 10 seconds later, with the same message: since stays.
    await tick(freezer, 10)
    await tick(freezer, 0.5)

    assert state_of(hass, STATUS).attributes["since"] == started

    bridge.heal()
    await tick(freezer, 10)
    running = dt_util.utcnow()
    await tick(freezer, 0.5)

    status = state_of(hass, STATUS)
    assert (status.state, status.attributes["message"]) == ("running", RUNNING)
    assert running - timedelta(microseconds=1) <= status.attributes["since"] <= running


# eWeLink devices seen.


async def test_ewelink_devices_seen_counts_every_device_at_once(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    data = await start(hass, paired(switch()))

    seen = state_of(hass, DEVICES_SEEN)
    assert (seen.state, seen.attributes["listening"], seen.attributes["last_packet_at"]) == ("0", True, None)
    assert seen.attributes[ATTR_UNIT_OF_MEASUREMENT] == "devices"
    registry_entry = er.async_get(hass).async_get(DEVICES_SEEN)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    # A device that no switch maps, which the status tracker doesn't publish, counts at once.
    heard = dt_util.utcnow()
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()

    seen = state_of(hass, DEVICES_SEEN)
    assert (seen.state, seen.attributes["last_packet_at"]) == ("1", heard)

    lan.broadcast(1)
    await settle()

    assert state_of(hass, DEVICES_SEEN).state == "2"

    # The listener stopped: the status tracker publishes it.
    data.registry.listening = False
    await tick(freezer, 0.5)

    assert state_of(hass, DEVICES_SEEN).attributes["listening"] is False


async def test_ewelink_devices_seen_shows_the_last_packet_every_30_seconds(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, paired(switch()))
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()
    first = dt_util.utcnow()

    # A repeat changes nothing that the registry reports, but it is the last packet.
    await tick(freezer, 10)
    repeat = dt_util.utcnow()
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()

    assert state_of(hass, DEVICES_SEEN).attributes["last_packet_at"] == first

    await tick(freezer, 20)

    assert state_of(hass, DEVICES_SEEN).attributes["last_packet_at"] == repeat
    assert state_of(hass, DEVICES_SEEN).state == "1"


# Last action.


def last_action(hass: HomeAssistant) -> tuple[str, dict[str, object]]:
    state = state_of(hass, LAST_ACTION)
    attributes = {key: state.attributes[key] for key in ("changed", "unchanged", "failed", "targets", "message")}
    return state.state, attributes


async def test_last_action_shows_each_toggle_and_what_went_wrong(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, paired(switch()))
    await tick(freezer, 0.5)
    targets = ["light 'Ceiling'"]

    action = state_of(hass, LAST_ACTION)
    assert action.state == STATE_UNKNOWN
    assert action.attributes[ATTR_OPTIONS] == ["turned_on", "turned_off", "failed", "dropped"]
    assert {key: action.attributes[key] for key in ("at", "elapsed_ms")} == {"at": None, "elapsed_ms": None}
    assert last_action(hass)[1] == {
        "changed": None,
        "unchanged": None,
        "failed": None,
        "targets": targets,
        "message": None,
    }

    # Turned on.
    lan.broadcast(1)
    pressed = dt_util.utcnow()
    lan.broadcast(2)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )
    assert state_of(hass, LAST_ACTION).attributes["at"] == pressed
    assert state_of(hass, LAST_ACTION).attributes["elapsed_ms"] == 0

    # Turned off.
    lan.broadcast(3)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_off",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )

    # Failed: the bridge couldn't say which lights are on.
    bridge.fail("/clip/v2/resource/light", 503)
    failed = dt_util.utcnow()
    lan.broadcast(4)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "failed",
        {"changed": None, "unchanged": None, "failed": None, "targets": targets, "message": LIGHTS_DOWN},
    )
    assert state_of(hass, LAST_ACTION).attributes["at"] == failed
    assert state_of(hass, LAST_ACTION).attributes["elapsed_ms"] is None

    # Dropped: the press waited more than 5 seconds for the bridge.
    bridge.heal()
    held = bridge.hold("/clip/v2/resource/light")
    lan.broadcast(5)
    lan.broadcast(6)
    await settle()
    await tick(freezer, 6)
    held.set()
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "dropped",
        {"changed": None, "unchanged": None, "failed": None, "targets": targets, "message": PRESS_DROPPED},
    )

    # Seq 5 turned the light on before seq 6 was dropped, so seq 7 turns it off; the older error isn't this toggle's.
    await tick(freezer, 1)
    lan.broadcast(7)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_off",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )


async def test_last_action_shows_the_lights_a_toggle_could_not_switch(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """The status tracker records the toggle and its error at the same time; the toggle shows, with the error."""
    await start(hass, paired(switch(lights=["Ceiling"], groups=["Hall"])))
    bridge.fail("/clip/v2/resource/grouped_light/g1", 503)

    lan.broadcast(1)
    lan.broadcast(2)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {
            "changed": 1,
            "unchanged": 0,
            "failed": 1,
            "targets": ["light 'Ceiling'", "group 'Hall'"],
            "message": "1 of 2 lights could not be switched.",
        },
    )


async def test_last_action_has_no_targets_while_the_bridge_does_not_run(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    bridge.fail("/clip/v2/resource/bridge", 503)
    await start(hass, paired(switch()))
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        STATE_UNKNOWN,
        {"changed": None, "unchanged": None, "failed": None, "targets": None, "message": None},
    )


async def test_last_action_labels_a_target_whose_name_another_light_has_with_its_id(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    bridge.names["l2"] = "Ceiling"
    await start(hass, paired(switch(lights=["l1"], groups=["Hall"])))

    lan.broadcast(1)
    lan.broadcast(2)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {
            "changed": 2,
            "unchanged": 0,
            "failed": 0,
            "targets": ["light 'Ceiling' (l1)", "group 'Hall'"],
            "message": None,
        },
    )
