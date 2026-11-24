"""Setting up and unloading the config entry in Home Assistant."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue import Sonoff2HueData
from custom_components.sonoff2hue.const import DOMAIN


async def test_the_entry_sets_up_and_unloads(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="sonoff2hue")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Sonoff2HueData)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
