"""The sonoff2hue integration: Home Assistant's lights, such as Philips Hue's, toggled by Sonoff MINIR4M wall
switches."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import migration
from .entity import async_track_devices
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData

__all__ = ["Sonoff2HueConfigEntry", "Sonoff2HueData"]

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.SENSOR]
"""The entity platforms (decision 25). They are set up once the runtime runs, and unloaded before it stops."""


async def async_migrate_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> bool:
    """Migrates an entry of sonoff2hue 1.x, which paired a Hue bridge itself, to Home Assistant's lights (decision
    40)."""
    return await migration.async_migrate_entry(hass, entry)


async def async_setup_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    """Starts the runtime (decision 23), its update listener and the platforms."""
    runtime = Sonoff2HueData.create(hass, entry)
    entry.runtime_data = runtime
    # Also when a platform fails to set up, which unloads the entry without async_unload_entry.
    entry.async_on_unload(runtime.async_stop)
    runtime.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(async_track_devices(hass, entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    """Unloads the platforms; the runtime stops after them. A platform that fails to unload raises Home Assistant's
    translated ``ConfigEntryError`` (decision 36)."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    """Deletes what sonoff2hue 1.x kept for the entry outside it, in case it was never migrated (decision 40)."""
    await migration.async_remove_store(hass, entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    """After every change of the entry, its data, its title or its subentries: a change of the configuration restarts
    only the supervisor, without reloading the entry."""
    entry.runtime_data.async_config_changed()
