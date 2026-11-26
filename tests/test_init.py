"""Setting up and unloading the config entry in Home Assistant."""

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
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


@pytest.mark.usefixtures("lan", "bridge")
async def test_a_platform_that_fails_to_unload_fails_the_unload_with_its_reason(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unload raises Home Assistant's own error for a platform that didn't unload (decision 36), which the entry
    shows as its reason."""
    entry = paired(switch())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    unload_platform = hass.config_entries.async_forward_entry_unload

    async def fail_for_the_buttons(config_entry: ConfigEntry, domain: Platform | str) -> bool:
        return False if domain == Platform.BUTTON else await unload_platform(config_entry, domain)

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_unload", fail_for_the_buttons)

    assert not await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    assert (entry.error_reason_translation_domain, entry.error_reason_translation_key) == (
        "homeassistant",
        "unload_platforms_failed",
    )
