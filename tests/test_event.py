"""The event entities (decision 25): each switch's Press, from the runtime's press events, with the LAN on a fake
network and the lights in Home Assistant's state machine."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.event import ATTR_EVENT_TYPE, ATTR_EVENT_TYPES, EventDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from .fakes import OTHER_DEVICE_ID, Lan, entry, switch
from .platforms import PRESS, STATUS, settle, start, state_of, tick

DESK_PRESS = "event.desk_press"


def fired(at: datetime) -> str:
    """An event entity's state: the time of its last event, to the millisecond."""
    return at.isoformat(timespec="milliseconds")


@pytest.mark.usefixtures("lights")
async def test_press_fires_for_each_press_of_its_switch(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, entry(switch(), switch(OTHER_DEVICE_ID, "Desk", targets=["light.desk"])))

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


@pytest.mark.usefixtures("lights")
async def test_press_fires_only_while_the_switches_run(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    """Its events are the presses that the runner acts on, as the toggle events have them: none while the configuration
    has errors."""
    await start(hass, entry(switch(targets=[" "])))
    await tick(freezer, 0.5)
    assert state_of(hass, STATUS).state == "invalid_config"

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert state_of(hass, PRESS).state == STATE_UNKNOWN
