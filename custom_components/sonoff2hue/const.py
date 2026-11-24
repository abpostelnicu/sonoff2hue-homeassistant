"""Constants of the integration."""

from typing import Final

DOMAIN: Final = "sonoff2hue"

ENTRY_TITLE: Final = "sonoff2hue"
"""The title of the config entry, whichever bridge it is paired with (decision 22)."""

# The config entry's data: the paired Hue bridge, under the field names of the engine's HueSettings, with the values
# that pairing gave.
CONF_BRIDGE_IP: Final = "bridge_ip"
"""The bridge's address as it was entered or found, trimmed: a host name or IP address with an optional port."""
CONF_BRIDGE_ID: Final = "bridge_id"
"""The bridge id from the bridge's public config; also the config entry's unique id."""
CONF_APP_KEY: Final = "app_key"
CONF_CERTIFICATE_SHA256: Final = "certificate_sha256"
"""The SHA-256 of the bridge's certificate, pinned when pairing."""

SUBENTRY_SWITCH: Final = "switch"
"""The type of a switch's config subentry, one per MINIR4M. Its unique id is the normalized device id
(``SwitchSettings.normalized_device_id``), and its title the switch's name, or that device id without one."""

# A switch subentry's data, under the field names of the engine's SwitchSettings, with the values as they were given.
CONF_DEVICE_ID: Final = "device_id"
CONF_NAME: Final = "name"
CONF_DEVICE_KEY: Final = "device_key"
CONF_LIGHTS: Final = "lights"
CONF_GROUPS: Final = "groups"
