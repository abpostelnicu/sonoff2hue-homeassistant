"""The sonoff2hue integration: Philips Hue lights toggled by Sonoff MINIR4M wall switches."""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


@dataclass(slots=True)
class Sonoff2HueData:
    """What the loaded config entry holds. A placeholder until the runtime is ported."""


type Sonoff2HueConfigEntry = ConfigEntry[Sonoff2HueData]


async def async_setup_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> bool:
    """Set up the config entry of the one bridge."""
    entry.runtime_data = Sonoff2HueData()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> bool:
    """Unload the config entry."""
    return True
