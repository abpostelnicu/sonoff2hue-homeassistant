"""The sonoff2hue integration: Philips Hue lights toggled by Sonoff MINIR4M wall switches."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData

__all__ = ["Sonoff2HueConfigEntry", "Sonoff2HueData"]

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR]
"""The entity platforms (decision 25). They are set up once the runtime runs, and unloaded before it stops."""


async def async_setup_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> bool:
    """Starts the runtime of the one bridge (decision 23), its update listener and the platforms."""
    runtime = Sonoff2HueData.create(hass, entry)
    entry.runtime_data = runtime
    # Also when a platform fails to set up, which unloads the entry without async_unload_entry.
    entry.async_on_unload(runtime.async_stop)
    runtime.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> bool:
    """Unloads the platforms; the runtime stops after them."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    """After every change of the entry, its data, its title or its subentries: a change of the configuration restarts
    only the bridge supervisor, without reloading the entry."""
    entry.runtime_data.async_config_changed()
