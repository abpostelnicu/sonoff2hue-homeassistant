"""Setting up and unloading the config entry in Home Assistant."""

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import pytest

from custom_components.sonoff2hue import Sonoff2HueData

from .fakes import CEILING, Lan, Lights, entry, switch
from .platforms import settle, start


@pytest.mark.usefixtures("lan", "lights")
async def test_the_entry_sets_up_and_unloads(hass: HomeAssistant) -> None:
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.LOADED
    assert isinstance(config_entry.runtime_data, Sonoff2HueData)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("lan", "lights")
async def test_a_platform_that_fails_to_unload_fails_the_unload_with_its_reason(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unload raises Home Assistant's own error for a platform that didn't unload (decision 36), which the entry
    shows as its reason."""
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    unload_platform = hass.config_entries.async_forward_entry_unload

    async def fail_for_the_buttons(config_entry: ConfigEntry, domain: Platform | str) -> bool:
        return False if domain == Platform.BUTTON else await unload_platform(config_entry, domain)

    monkeypatch.setattr(hass.config_entries, "async_forward_entry_unload", fail_for_the_buttons)

    assert not await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.FAILED_UNLOAD
    assert (config_entry.error_reason_translation_domain, config_entry.error_reason_translation_key) == (
        "homeassistant",
        "unload_platforms_failed",
    )


async def test_presses_toggle_home_assistants_lights(hass: HomeAssistant, lan: Lan, lights: Lights) -> None:
    """The wall switch's presses turn its lights on, then off, with Home Assistant's light actions (decision 40); the
    first broadcast is the switch's state, not a press."""
    desk = lights.add("desk", on=False)
    lights.add("ceiling")
    await start(hass, entry(switch(targets=[CEILING, lights.target(desk)])))

    lan.broadcast(1)
    await settle()
    assert lights.turned() == []

    lan.broadcast(2)
    await settle()
    assert sorted(lights.turned()) == [("turn_on", CEILING), ("turn_on", desk)]
    assert lights.is_on(CEILING)
    assert lights.is_on(desk)

    lan.broadcast(3)
    await settle()
    assert sorted(lights.turned()) == [("turn_off", CEILING), ("turn_off", desk)]
    assert not lights.is_on(CEILING)
