"""The binary sensors (decision 25): Reachable on the bridge's device, and each switch's Online, with the LAN on a fake
network and the Hue bridge in memory."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    EVENT_STATE_CHANGED,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import async_capture_events

from .fakes import BRIDGE_ADDRESS, DEVICE_ADDRESS, DEVICE_KEY, Bridge, Lan, paired, relay, switch
from .platforms import BRIDGE_DOWN, ONLINE, REACHABLE, settle, start, state_of, tick

NOT_HEARD = {
    "address": None,
    "last_seen_at": None,
    "last_press_at": None,
    "seq": None,
    "encrypted": None,
    "has_device_key": False,
    "detached": None,
    "decode_error": None,
}


# Reachable.


async def test_reachable_shows_the_bridges_last_check(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    checked = dt_util.utcnow()
    await start(hass, paired(switch()))
    await tick(freezer, 0.5)

    reachable = state_of(hass, REACHABLE)
    assert reachable.state == STATE_ON
    assert reachable.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.CONNECTIVITY
    assert {key: reachable.attributes[key] for key in ("address", "message", "checked_at")} == {
        "address": BRIDGE_ADDRESS,
        "message": None,
        "checked_at": checked,
    }
    registry_entry = er.async_get(hass).async_get(REACHABLE)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    # The runner's keep-alive, every 45 seconds, finds the bridge down, then up again.
    bridge.fail("/clip/v2/resource/bridge", 503)
    await tick(freezer, 45)
    checked = dt_util.utcnow()
    await tick(freezer, 0.5)

    reachable = state_of(hass, REACHABLE)
    assert reachable.state == STATE_OFF
    assert (reachable.attributes["message"], reachable.attributes["checked_at"]) == (BRIDGE_DOWN, checked)

    bridge.heal()
    await tick(freezer, 45)
    await tick(freezer, 0.5)

    reachable = state_of(hass, REACHABLE)
    assert (reachable.state, reachable.attributes["message"]) == (STATE_ON, None)


async def test_reachable_is_unknown_while_the_bridge_is_not_paired(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    await start(hass, paired(switch(), app_key=None))

    reachable = state_of(hass, REACHABLE)
    assert reachable.state == STATE_UNKNOWN
    assert {key: reachable.attributes[key] for key in ("address", "message", "checked_at")} == {
        "address": BRIDGE_ADDRESS,
        "message": None,
        "checked_at": None,
    }


# Online.


def online(hass: HomeAssistant) -> tuple[str, dict[str, object]]:
    state = state_of(hass, ONLINE)
    return state.state, {key: state.attributes[key] for key in NOT_HEARD}


async def test_online_means_heard_within_the_last_5_minutes(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, paired(switch()))

    # Never heard: offline.
    assert online(hass) == (STATE_OFF, NOT_HEARD)
    assert state_of(hass, ONLINE).attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.CONNECTIVITY
    registry_entry = er.async_get(hass).async_get(ONLINE)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    heard = dt_util.utcnow()
    lan.broadcast(1)
    await tick(freezer, 0.5)
    pressed = dt_util.utcnow()
    lan.broadcast(2)
    await tick(freezer, 0.5)

    assert online(hass) == (
        STATE_ON,
        NOT_HEARD
        | {"address": DEVICE_ADDRESS, "last_seen_at": pressed, "last_press_at": pressed, "seq": 2, "encrypted": True},
    )
    assert heard < pressed

    # Online until 5 minutes have passed since the last broadcast, then offline.
    await tick(freezer, (pressed + timedelta(minutes=5) - dt_util.utcnow()).total_seconds() - 1)

    assert online(hass)[0] == STATE_ON

    await tick(freezer, 2)

    assert online(hass) == (
        STATE_OFF,
        NOT_HEARD
        | {"address": DEVICE_ADDRESS, "last_seen_at": pressed, "last_press_at": pressed, "seq": 2, "encrypted": True},
    )

    # Heard again with a repeated broadcast, which the registry doesn't report: online within 30 seconds.
    repeated = dt_util.utcnow()
    lan.broadcast(2)
    await settle()

    assert online(hass)[0] == STATE_OFF

    await tick(freezer, 30)

    assert online(hass) == (
        STATE_ON,
        NOT_HEARD
        | {"address": DEVICE_ADDRESS, "last_seen_at": repeated, "last_press_at": pressed, "seq": 2, "encrypted": True},
    )

    # The 5 minutes start again from each broadcast.
    await tick(freezer, 200)
    lan.broadcast(2)
    await settle()
    await tick(freezer, 200)

    assert online(hass)[0] == STATE_ON

    await tick(freezer, 100)

    assert online(hass)[0] == STATE_OFF


async def test_online_shows_the_relay_mode_the_key_and_a_payload_it_cannot_decode(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, paired(switch(key=DEVICE_KEY)))

    assert online(hass) == (STATE_OFF, NOT_HEARD | {"has_device_key": True})

    lan.broadcast(1, params=relay(detached=False), key=DEVICE_KEY)
    await tick(freezer, 0.5)

    assert online(hass)[1] | {"last_seen_at": None} == NOT_HEARD | {
        "address": DEVICE_ADDRESS,
        "seq": 1,
        "encrypted": True,
        "has_device_key": True,
        "detached": False,
    }

    lan.broadcast(2)  # an IV that isn't base64
    await tick(freezer, 0.5)

    attributes = online(hass)[1]
    assert (attributes["detached"], attributes["decode_error"]) == (False, "iv is not valid base64")


@pytest.mark.parametrize("key", ["", " \t"])
async def test_online_counts_a_blank_key_as_none(hass: HomeAssistant, lan: Lan, bridge: Bridge, key: str) -> None:
    """A key that is empty or only white space is no key."""
    await start(hass, paired(switch(key=key)))

    assert online(hass)[1]["has_device_key"] is False


async def test_online_stays_on_when_two_answers_in_a_row_are_lost(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """The switch answers each query, as a MINIR4M does, until two answers in a row are lost. With a query every 2
    minutes, its last answer was then 6 minutes old, and Online was off for a minute; once the switch is late, the
    LAN monitor asks every 30 seconds (decision 36)."""
    await start(hass, paired(switch()))
    transport = lan.network.transports[-1]
    send = transport.send
    lost = 0

    def answer(data: bytes, interface: str) -> None:
        nonlocal lost
        send(data, interface)
        if lost:
            lost -= 1
        else:
            lan.broadcast(1)

    transport.send = answer  # type: ignore[method-assign]
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    lan.broadcast(1)  # its answer to the query that went out as the socket opened
    for _ in range(10):  # the queries at 1 and 3 seconds, answered
        await tick(freezer, 1)

    lost = 2
    for _ in range(600):
        await tick(freezer, 1)

    assert lost == 0
    states = [event.data["new_state"].state for event in changes if event.data["entity_id"] == ONLINE]
    assert states[0] == STATE_ON
    assert STATE_OFF not in states
    assert online(hass)[0] == STATE_ON
