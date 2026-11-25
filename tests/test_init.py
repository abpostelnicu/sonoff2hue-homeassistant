"""Setting up and unloading the config entry in Home Assistant."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest

from custom_components.sonoff2hue import Sonoff2HueData

from .fakes import paired, switch


@pytest.mark.usefixtures("lan", "bridge")
async def test_the_entry_sets_up_and_unloads(hass: HomeAssistant) -> None:
    entry = paired(switch())
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, Sonoff2HueData)

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
