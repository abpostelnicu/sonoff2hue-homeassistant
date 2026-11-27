"""Constants of the integration."""

from typing import Final

DOMAIN: Final = "sonoff2hue"

ENTRY_TITLE: Final = "sonoff2hue"
"""The title of the config entry, which holds no data of its own: the switches are its subentries (decision 40)."""

ENTRY_VERSION: Final = 2
"""The config entry's version: 1 held the paired Hue bridge and switches of Hue lights, rooms and zones by name; 2 holds
switches of Home Assistant's lights (decision 40), which ``migration.py`` makes of version 1's."""

SUBENTRY_SWITCH: Final = "switch"
"""The type of a switch's config subentry, one per MINIR4M. Its unique id is the normalized device id
(``SwitchSettings.normalized_device_id``), and its title the switch's name, or that device id without one."""

# A switch subentry's data, under the field names of the engine's SwitchSettings, with the values as they were given.
CONF_DEVICE_ID: Final = "device_id"
CONF_NAME: Final = "name"
CONF_DEVICE_KEY: Final = "device_key"
CONF_TARGETS: Final = "targets"
"""The lights that the switch toggles: entity registry ids, or entity ids for lights that the registry doesn't have."""
CONF_UNMIGRATED: Final = "unmigrated"
"""The lights, rooms and zones of a switch of version 1 that the migration found no light for, as version 1 named them;
the runtime shows them in a repair until the switch is saved again (decision 40)."""

# The bus events of the runtime (decision 23), for the entities and automations. Each names its switch:
# ``device_id``, the Home Assistant device of the switch's subentry, or None while it has none; ``switch_id``; and
# ``name``, the switch's name as configured, or None.
EVENT_PRESS: Final = "sonoff2hue_press"
"""A wall switch was operated: its switch, and ``seq``, the broadcast's sequence number. A toggle follows."""
EVENT_TOGGLE: Final = "sonoff2hue_toggle"
"""What came of a press: its switch, ``targets`` (its lights, such as ``light.ceiling``) and ``result``. With
``toggled``, also ``turned_on``, ``changed``, ``unchanged``, ``failed`` and ``elapsed_ms``; with ``failed`` (the toggle
couldn't run) or ``dropped`` (the press waited more than 5 seconds for the lights), ``message``. A toggle stopped by a
change of the configuration fires nothing, as the runner reports nothing for it."""
ATTR_SWITCH_ID: Final = "switch_id"
"""The switch's eWeLink device id, normalized: its subentry's unique id."""
ATTR_SEQ: Final = "seq"
ATTR_TARGETS: Final = "targets"
"""The lights of the switch, by their entity ids."""
ATTR_RESULT: Final = "result"
ATTR_TURNED_ON: Final = "turned_on"
ATTR_CHANGED: Final = "changed"
ATTR_UNCHANGED: Final = "unchanged"
ATTR_FAILED: Final = "failed"
"""How many lights weren't switched."""
ATTR_ELAPSED_MS: Final = "elapsed_ms"
ATTR_MESSAGE: Final = "message"
RESULT_TOGGLED: Final = "toggled"
RESULT_FAILED: Final = "failed"
RESULT_DROPPED: Final = "dropped"
