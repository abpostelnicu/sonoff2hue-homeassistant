"""The event entities (decision 25): each switch's Press, from the runtime's press events, with the LAN on a fake
network and the Hue bridge in memory."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.event import ATTR_EVENT_TYPE, ATTR_EVENT_TYPES, EventDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .fakes import OTHER_DEVICE_ID, Bridge, Lan, paired, switch
from .platforms import PRESS, settle, start, state_of, tick

DESK_PRESS = "event.desk_press"


def fired(at: datetime) -> str:
    """An event entity's state: the time of its last event, to the millisecond."""
    return at.isoformat(timespec="milliseconds")


async def test_press_fires_for_each_press_of_its_switch(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, paired(switch(), switch(OTHER_DEVICE_ID, "Desk", lights=["Desk"])))

    press = state_of(hass, PRESS)
    assert press.state == STATE_UNKNOWN
    assert press.attributes[ATTR_DEVICE_CLASS] == EventDeviceClass.BUTTON
    assert press.attributes[ATTR_EVENT_TYPES] == ["press"]

    # The first broadcast is the baseline; the next one is a press.
    lan.broadcast(1)
    await settle()

    assert state_of(hass, PRESS).state == STATE_UNKNOWN

    pressed = dt_util.utcnow()
    lan.broadcast(2)
    await settle()

    press = state_of(hass, PRESS)
    assert press.state == fired(pressed)
    assert (press.attributes[ATTR_EVENT_TYPE], press.attributes["seq"]) == ("press", 2)

    # Another switch's press is its own.
    await tick(freezer, 1)
    lan.broadcast(7, OTHER_DEVICE_ID)
    lan.broadcast(8, OTHER_DEVICE_ID)
    await settle()

    assert state_of(hass, PRESS).state == fired(pressed)
    assert state_of(hass, DESK_PRESS).attributes["seq"] == 8

    # A repeated broadcast is no press.
    lan.broadcast(2)
    await settle()

    assert state_of(hass, PRESS).state == fired(pressed)

    await tick(freezer, 1)
    pressed = dt_util.utcnow()
    lan.broadcast(3)
    await settle()

    press = state_of(hass, PRESS)
    assert press.state == fired(pressed)
    assert press.attributes["seq"] == 3


async def test_press_fires_only_while_the_bridge_runs(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, freezer: FrozenDateTimeFactory
) -> None:
    """Its events are the presses that the runner acts on, as the toggle events and the logbook have them."""
    bridge.fail("/clip/v2/resource/bridge", 503)
    await start(hass, paired(switch()))

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert state_of(hass, PRESS).state == STATE_UNKNOWN
