"""The config entry's diagnostics: what the .NET app's web interface showed, in one download (decision 26).

The web interface read four views of the running service from its API: the configuration (``/api/config``), the status
(``/api/status``), every eWeLink device on the LAN (``/api/devices``) and the log (``/api/logs``). The diagnostics hold
the same, as the runtime has them (decision 23):

- ``config``: the configuration that the bridge supervisor runs, the entry's data and its switches as stored;
- ``status``: the status tracker's snapshot, the service, the Hue bridge, the LAN and each switch with its resolved
  targets;
- ``lan_devices``: the LAN registry's devices, each with ``online``;
- ``activity``: the activity buffer's entries, the last 1,000, as Home Assistant's log levels let them in.

An entry that isn't loaded has no runtime, so its diagnostics hold only the configuration.

Home Assistant's ``async_redact_data`` takes out the secrets by the keys that the entry and its subentries keep them
under, so the application key, the certificate pin and the device keys show only whether they are set. The texts hold
none of them: the engine sends the key only in the ``hue-application-key`` header, the pin only to aiohttp's check of the
certificate and the device key only to the payload's decryption, and its messages and log lines don't name them.
"""

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_APP_KEY, CONF_CERTIFICATE_SHA256, CONF_DEVICE_KEY
from .engine.activity import CAPACITY
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData, config_from_entry

TO_REDACT: Final = {CONF_APP_KEY, CONF_CERTIFICATE_SHA256, CONF_DEVICE_KEY}
"""The keys of the secrets in the entry's data and in its switches' subentries, which the engine's settings share."""


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> dict[str, Any]:
    """The configuration, the status, the LAN's devices and the activity, without secrets."""
    runtime: Sonoff2HueData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        diagnostics = {"config": _json(config_from_entry(entry)), "status": None, "lan_devices": None, "activity": None}
    else:
        registry = runtime.registry
        diagnostics = {
            "config": _json(runtime.config),
            "status": _json(runtime.status.snapshot()),
            "lan_devices": [{**_json(device), "online": registry.is_online(device)} for device in registry.snapshot()],
            "activity": [_json(activity) for activity in runtime.activity.get(0, CAPACITY)],
        }
    return async_redact_data(diagnostics, TO_REDACT)


def _json(value: object) -> Any:
    """``value`` for JSON: a dataclass as an object of its fields, a time in ISO 8601 and an enum as its value."""
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    return value
