"""Helpers for the tests of the devices and entities (decision 25): the entity ids, and starting the integration with
the LAN on a fake network and the lights in Home Assistant's state machine, the ``lan`` and ``lights`` fixtures of
``conftest.py``."""

import asyncio
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.runtime import Sonoff2HueData

# The entities of the service device, "sonoff2hue", and of the switch "Hall" (tests.fakes.switch()).
STATUS = "sensor.sonoff2hue_status"
DEVICES_SEEN = "sensor.sonoff2hue_ewelink_devices_seen"
PRESS = "event.hall_press"
LAST_ACTION = "sensor.hall_last_action"
ONLINE = "binary_sensor.hall_online"
TEST_TOGGLE = "button.hall_test_toggle"

RUNNING = "1 wall switch toggles 1 light."


async def settle() -> None:
    """Lets the event loop run what is ready: with the LAN and the lights in memory, nothing waits for I/O."""
    for _ in range(20):
        await asyncio.sleep(0)


async def tick(freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Moves the frozen time, which the event loop's clock follows, a microsecond more than ``seconds``, as a timer due
    exactly at the new time doesn't run yet, and runs the timers that are due."""
    freezer.tick(timedelta(seconds=seconds, microseconds=1))
    await settle()


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> Sonoff2HueData:
    """Sets the entry up, with its platforms, and lets the switches start."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle()
    return entry.runtime_data


def state_of(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state
