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

# The bus events of the runtime (decision 23), for the entities and automations. Each names its switch:
# ``device_id``, the Home Assistant device of the switch's subentry, or None while it has none; ``switch_id``; and
# ``name``, the switch's name as configured, or None.
EVENT_PRESS: Final = "sonoff2hue_press"
"""A wall switch was operated: its switch, and ``seq``, the broadcast's sequence number. A toggle follows."""
EVENT_TOGGLE: Final = "sonoff2hue_toggle"
"""What came of a press: its switch, ``targets`` (such as ``light 'Ceiling'``) and ``result``. With ``toggled``, also
``turned_on``, ``changed``, ``unchanged``, ``failed`` and ``elapsed_ms``; with ``failed`` (the toggle couldn't run) or
``dropped`` (the press waited more than 5 seconds for the bridge), ``message``. A toggle stopped by a change of the
configuration fires nothing, as the runner reports nothing for it."""
ATTR_SWITCH_ID: Final = "switch_id"
"""The switch's eWeLink device id, normalized: its subentry's unique id."""
ATTR_SEQ: Final = "seq"
ATTR_TARGETS: Final = "targets"
ATTR_RESULT: Final = "result"
ATTR_TURNED_ON: Final = "turned_on"
ATTR_CHANGED: Final = "changed"
ATTR_UNCHANGED: Final = "unchanged"
ATTR_FAILED: Final = "failed"
"""How many targets the bridge didn't switch."""
ATTR_ELAPSED_MS: Final = "elapsed_ms"
ATTR_MESSAGE: Final = "message"
RESULT_TOGGLED: Final = "toggled"
RESULT_FAILED: Final = "failed"
RESULT_DROPPED: Final = "dropped"
