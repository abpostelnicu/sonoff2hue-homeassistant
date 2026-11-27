"""The migration of a config entry of sonoff2hue 1.x, version 1, to version 2 (decision 40).

Version 1 paired a Hue bridge itself, and kept its address, id, application key and certificate pin in the entry's
data, and each switch's lights, rooms and zones by name or Hue resource id, in ``lights`` and ``groups``. Version 2
toggles Home Assistant's lights, which the Hue integration provides, so the migration:

- finds the light of each entry of a switch: by the Hue resource id that the entry last found, which the store of
  remembered resources keeps (``.storage/sonoff2hue.<entry id>``) for the bridge that the entry is paired with, or that
  the entry is; a light's id gives the Hue integration's light with that unique id, and a room's or zone's id the light
  of its Hue device. Without an id that Home Assistant has, by its name, when exactly one Hue light, or room or zone,
  has it, without regard to case;
- keeps each switch's lights as ``targets``, by their entity registry ids, and the entries that it found no light for
  as ``unmigrated``, which a repair shows until the switch is saved again;
- clears the entry's data and unique id, and deletes the store;
- turns the bridge's device into the service device "sonoff2hue", identified by the entry's id, with its entities'
  unique ids, and removes its Reachable entity, as nothing provides it any more.

The Hue integration's own data isn't touched. Home Assistant itself refuses an entry of a version newer than this
code's, so that an older sonoff2hue can't misread a migrated entry.
"""

from collections.abc import Mapping
import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    CONF_UNMIGRATED,
    DOMAIN,
    ENTRY_VERSION,
    SUBENTRY_SWITCH,
)
from .engine.config import SwitchSettings
from .entity import SERVICE_NAME

_LOGGER = logging.getLogger(__name__)

HUE: Final = "hue"
"""The domain of Home Assistant's Hue integration, the platform of its lights."""
HUE_GROUP_TRANSLATION_KEY: Final = "hue_grouped_light"
"""The translation key of the Hue integration's light of a room or zone."""
HUE_GROUP_MODELS: Final = {"room": "Room", "zone": "Zone"}
"""The models of the Hue integration's devices of rooms and zones."""

# Version 1's data.
V1_LIGHTS: Final = "lights"
V1_GROUPS: Final = "groups"
V1_BRIDGE_ID: Final = "bridge_id"
"""The key of the bridge id in the entry's data, and of the tag of its store of remembered resources."""
V1_KINDS: Final = {(V1_LIGHTS, "light"), *((V1_GROUPS, kind) for kind in HUE_GROUP_MODELS)}
"""The kinds of resource that version 1 remembered for the entries of each setting."""
STORE_VERSION: Final = 1
REACHABLE: Final = "reachable"
"""The translation key of the bridge's Reachable entity, which version 2 doesn't have."""

type Remembered = Mapping[tuple[str, str, str], tuple[str, str]]
"""The Hue resource that each entry last found, by the switch's device id, the setting and the entry as compared: its
kind (``light``, ``room`` or ``zone``) and id."""


def store_key(entry_id: str) -> str:
    """The key of version 1's store of remembered resources, its file name in ``.storage``."""
    return f"{DOMAIN}.{entry_id}"


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    """Deletes version 1's store of remembered resources, if it is still there."""
    await Store[dict[str, Any]](hass, STORE_VERSION, store_key(entry_id)).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrates ``entry`` from version 1 to version 2. Home Assistant calls it for an older version, and for another
    minor version of 2, which needs nothing; it refuses a newer version itself."""
    if entry.version == 1:
        await _async_migrate_from_1(hass, entry)
    return True


async def _async_migrate_from_1(hass: HomeAssistant, entry: ConfigEntry) -> None:
    store = Store[dict[str, Any]](hass, STORE_VERSION, store_key(entry.entry_id))
    try:
        remembered = _remembered(await store.async_load(), str(entry.data.get(V1_BRIDGE_ID) or ""))
    except HomeAssistantError as err:
        _LOGGER.warning("Can't read the remembered lights, rooms and zones (%s); migrating without them.", err)
        remembered = {}

    hue = _HueLights(hass)
    for subentry in list(entry.subentries.values()):
        if subentry.subentry_type != SUBENTRY_SWITCH:
            continue
        data = subentry.data
        switch = SwitchSettings(device_id=str(data.get(CONF_DEVICE_ID) or ""), name=data.get(CONF_NAME))
        targets: list[str] = []
        unmigrated: list[str] = []
        for setting in (V1_LIGHTS, V1_GROUPS):
            for value in data.get(setting) or ():
                text = str(value)
                found = remembered.get((switch.normalized_device_id, setting, _name_key(text)))
                target = hue.find(setting, text, found)
                if target is None:
                    unmigrated.append(text)
                elif target not in targets:
                    targets.append(target)
        new_data: dict[str, Any] = {
            CONF_DEVICE_ID: data.get(CONF_DEVICE_ID),
            CONF_NAME: data.get(CONF_NAME),
            CONF_DEVICE_KEY: data.get(CONF_DEVICE_KEY),
            CONF_TARGETS: targets,
        }
        if unmigrated:
            new_data[CONF_UNMIGRATED] = unmigrated
            _LOGGER.warning(
                "%s: found no light in Home Assistant for %s; edit the switch to choose them.",
                switch.display_name,
                ", ".join(f"'{text}'" for text in unmigrated),
            )
        _LOGGER.info("%s: migrated to %s of Home Assistant's lights.", switch.display_name, len(targets))
        hass.config_entries.async_update_subentry(entry, subentry, data=new_data)

    _migrate_devices(hass, entry, str(entry.data.get(V1_BRIDGE_ID) or "") or entry.entry_id)
    hass.config_entries.async_update_entry(entry, data={}, unique_id=None, version=ENTRY_VERSION, minor_version=1)
    await store.async_remove()


def _name_key(value: str) -> str:
    """A name or id as version 1 compared them: stripped and casefolded."""
    return value.strip().casefold()


def _split_kind(value: str) -> tuple[str | None, str]:
    """A ``groups`` entry of version 1 as its ``room:`` or ``zone:`` prefix, in any case, or ``None``, and its name."""
    text = value.strip()
    for kind in HUE_GROUP_MODELS:
        prefix = f"{kind}:"
        if text[: len(prefix)].casefold() == prefix:
            return kind, text[len(prefix) :].strip()
    return None, text


def _remembered(data: object, bridge_id: str) -> dict[tuple[str, str, str], tuple[str, str]]:
    """The resources in the store's data, read as version 1 read them: only when the store belongs to ``bridge_id``,
    the bridge that the entry is paired with, as its tag says without regard to case, and only for the kinds of
    resource that each setting's entries found; what can't be read is left out."""
    if not isinstance(data, dict) or not _same_bridge(data.get(V1_BRIDGE_ID), bridge_id):
        return {}
    records = data.get("resources")
    remembered: dict[tuple[str, str, str], tuple[str, str]] = {}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        switch, setting, entry, kind, resource_id = (
            record.get(key) for key in ("switch", "field", "entry", "kind", "id")
        )
        if (
            isinstance(switch, str)
            and (setting, kind) in V1_KINDS
            and isinstance(entry, str)
            and isinstance(resource_id, str)
            and resource_id
        ):
            remembered[(switch.strip().lower(), str(setting), _name_key(entry))] = (str(kind), resource_id)
    return remembered


def _same_bridge(tag: object, bridge_id: str) -> bool:
    """Whether the store's tag is the bridge id, stripped and without regard to case; no bridge id matches none."""
    return isinstance(tag, str) and bool(bridge_id.strip()) and tag.strip().casefold() == bridge_id.strip().casefold()


class _HueLights:
    """The Hue integration's lights, rooms and zones, as Home Assistant's registries have them: the entity registry ids
    of its lights by their Hue light ids, and of its rooms' and zones' lights by the Hue ids of their devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        entities = er.async_get(hass)
        devices = dr.async_get(hass)
        self._lights: dict[str, er.RegistryEntry] = {}
        group_lights: dict[str, er.RegistryEntry] = {}
        for entity in entities.entities.values():
            if entity.platform != HUE or entity.domain != "light":
                continue
            if entity.translation_key == HUE_GROUP_TRANSLATION_KEY:
                if entity.device_id is not None:
                    group_lights[entity.device_id] = entity
            else:
                self._lights[entity.unique_id] = entity
        self._light_names = {
            light.id: _name_key(_display_name(light, devices.async_get(light.device_id or "")))
            for light in self._lights.values()
        }
        self._groups: dict[str, tuple[str, str, str]] = {}
        """The rooms' and zones' lights by the Hue ids of their devices: the entity registry id, the kind and the name
        as compared."""
        for device in devices.devices:
            light = group_lights.get(device.id)
            if light is None:
                continue
            kind = next((kind for kind, model in HUE_GROUP_MODELS.items() if model == device.model), None)
            for domain, identifier in device.identifiers:
                if domain == HUE and kind is not None:
                    self._groups[identifier] = (light.id, kind, _name_key(device.name_by_user or device.name or ""))

    def find(self, setting: str, entry: str, found: tuple[str, str] | None) -> str | None:
        """The entity registry id of the light that a version 1 entry of ``setting`` names: the one that it last found,
        else the one whose Hue id it is, else the one with its name; ``None`` without one."""
        if found is not None and (target := self._by_id(found[0], found[1])) is not None:
            return target
        if setting == V1_LIGHTS:
            return self._by_id("light", entry.strip()) or self._light_named(entry)
        kind, name = _split_kind(entry)
        return (self._by_id("room", entry.strip()) if kind is None else None) or self._group_named(kind, name)

    def _by_id(self, kind: str, resource_id: str) -> str | None:
        if kind == "light":
            light = self._lights.get(resource_id)
            return None if light is None else light.id
        group = self._groups.get(resource_id)
        return None if group is None else group[0]

    def _light_named(self, name: str) -> str | None:
        key = _name_key(name)
        matches = [light_id for light_id, light_name in self._light_names.items() if key and light_name == key]
        return matches[0] if len(matches) == 1 else None

    def _group_named(self, kind: str | None, name: str) -> str | None:
        key = _name_key(name)
        matches = [
            target
            for target, group_kind, group_name in self._groups.values()
            if key and group_name == key and kind in (None, group_kind)
        ]
        return matches[0] if len(matches) == 1 else None


def _display_name(light: er.RegistryEntry, device: dr.BaseDeviceEntry | None) -> str:
    """A light's name as Home Assistant shows it: the user's, else its device's name with the light's own name after
    it, as for an entity named on its device."""
    if light.name:
        return light.name
    device_name = "" if device is None else device.name_by_user or device.name or ""
    own = light.original_name
    return f"{device_name} {own}".strip() if own else device_name


def _migrate_devices(hass: HomeAssistant, entry: ConfigEntry, bridge_identifier: str) -> None:
    """Turns the bridge's device into the service device, identified by the entry's id, keeps its entities, Status and
    eWeLink devices seen, with their entity ids, and removes its Reachable entity."""
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, bridge_identifier), entry.entry_id)
    if device is not None:
        devices.async_update_device(
            device.id,
            new_identifiers={(DOMAIN, entry.entry_id)},
            name=SERVICE_NAME,
            manufacturer=None,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
    entities = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        key = entity.unique_id.removeprefix(f"{bridge_identifier}_")
        if key == entity.unique_id:
            continue
        if key == REACHABLE:
            entities.async_remove(entity.entity_id)
        else:
            entities.async_update_entity(entity.entity_id, new_unique_id=f"{entry.entry_id}_{key}")
