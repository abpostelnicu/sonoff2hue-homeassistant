"""The devices of the integration and what their entities share (decision 25).

The Hue bridge has a device, identified by the bridge id, with the service's state, the bridge's reachability and the
eWeLink devices seen on the LAN. Each ``switch`` subentry has a device, identified by the switch's normalized eWeLink
device id and named by the subentry's title, with that switch's presses, last action, presence on the LAN and test. An
entity's unique id is its device's identifier, an underscore and its translation key.

The entities show the runtime's state: the status tracker's snapshots, which come on ``SIGNAL_STATUS``, and the LAN
registry. A platform adds the entities of the switches that exist when it is set up, and those of a switch added later
when ``SIGNAL_SUBENTRY_ADDED`` brings it; Home Assistant removes a removed switch's device and entities itself.
:func:`async_track_devices` keeps the devices and the unique ids in step with the bridge id and the switches' device ids
and titles, which reconfiguring can change.
"""

from collections.abc import Callable, Iterable
from typing import Any, Final

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_BRIDGE_ID, CONF_DEVICE_ID, CONF_DEVICE_KEY, CONF_NAME, DOMAIN, SUBENTRY_SWITCH
from .engine.config import SwitchSettings
from .engine.status import StatusSnapshot
from .runtime import (
    SIGNAL_CONFIG_CHANGED,
    SIGNAL_STATUS,
    SIGNAL_SUBENTRY_ADDED,
    SIGNAL_SUBENTRY_UPDATED,
    Sonoff2HueConfigEntry,
    Sonoff2HueData,
)

BRIDGE_NAME: Final = "Hue bridge"
BRIDGE_MANUFACTURER: Final = "Signify Netherlands B.V."
"""The maker of Hue bridges, as their CLIP v2 device resource and Home Assistant's Hue integration name it."""
SWITCH_MANUFACTURER: Final = "SONOFF"
SWITCH_MODEL: Final = "MINIR4M"


def bridge_identifier(entry: Sonoff2HueConfigEntry) -> str:
    """The bridge device's identifier: the paired bridge's id, which pairing always stores, else the entry's id."""
    bridge_id: str | None = entry.data.get(CONF_BRIDGE_ID)
    return bridge_id or entry.entry_id


def switch_of(subentry: ConfigSubentry) -> SwitchSettings:
    """The parts of a switch subentry's settings that its device and entities use, with the values as stored."""
    data = subentry.data
    return SwitchSettings(
        device_id=data.get(CONF_DEVICE_ID) or "", name=data.get(CONF_NAME), device_key=data.get(CONF_DEVICE_KEY)
    )


class Sonoff2HueEntity(Entity):
    """An entity of the config entry's runtime, named by its translation key on its device, and updated by the
    runtime rather than polled."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: Sonoff2HueData, key: str) -> None:
        self._data = data
        self._attr_translation_key = key

    @callback
    def _follow_status(self) -> None:
        """Shows the status tracker's snapshot as it is now, then each one that the tracker publishes, until the entity
        is removed. For :meth:`async_added_to_hass`."""
        signal = SIGNAL_STATUS.format(self._data.entry.entry_id)
        self.async_on_remove(async_dispatcher_connect(self.hass, signal, self._on_status))
        self._show(self._data.status.snapshot())

    @callback
    def _on_status(self, snapshot: StatusSnapshot) -> None:
        self._show(snapshot)
        self.async_write_ha_state()

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        """Takes what the entity shows from ``snapshot``."""


class BridgeEntity(Sonoff2HueEntity):
    """An entity of the bridge's device, which follows the status tracker's snapshots."""

    def __init__(self, data: Sonoff2HueData, key: str) -> None:
        super().__init__(data, key)
        identifier = bridge_identifier(data.entry)
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)}, name=BRIDGE_NAME, manufacturer=BRIDGE_MANUFACTURER
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._follow_status()


class WallSwitchEntity(Sonoff2HueEntity):
    """An entity of a switch subentry's device. ``_switch`` follows the subentry's settings, and :meth:`_refresh` runs
    after each change of them."""

    def __init__(self, data: Sonoff2HueData, subentry: ConfigSubentry, key: str) -> None:
        super().__init__(data, key)
        self._subentry_id = subentry.subentry_id
        self._switch = switch_of(subentry)
        identifier = self._switch.normalized_device_id
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=subentry.title,
            manufacturer=SWITCH_MANUFACTURER,
            model=SWITCH_MODEL,
            serial_number=identifier,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        signal = SIGNAL_SUBENTRY_UPDATED.format(self._data.entry.entry_id)
        self.async_on_remove(async_dispatcher_connect(self.hass, signal, self._on_subentry_updated))

    @callback
    def _on_subentry_updated(self, subentry: ConfigSubentry) -> None:
        if subentry.subentry_id == self._subentry_id:
            self._switch = switch_of(subentry)
            self._refresh()

    @callback
    def _refresh(self) -> None:
        """Shows the switch again after its settings changed."""


@callback
def async_add_switch_entities(
    hass: HomeAssistant,
    entry: Sonoff2HueConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    create: Callable[[Sonoff2HueData, ConfigSubentry], Iterable[Entity]],
) -> None:
    """Adds the entities that ``create`` makes for each switch subentry, now and when one is added, to its subentry."""
    data = entry.runtime_data

    @callback
    def add(subentry: ConfigSubentry) -> None:
        async_add_entities(create(data, subentry), config_subentry_id=subentry.subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_SWITCH:
            add(subentry)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_SUBENTRY_ADDED.format(entry.entry_id), add))


@callback
def async_track_devices(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> CALLBACK_TYPE:
    """Keeps the devices in step with the entry: a new bridge id, or a switch's new device id, becomes its device's
    identifier and the start of its entities' unique ids, and a switch's new title its device's name. It looks now,
    for the changes made while the entry wasn't loaded, then after each change of the entry; returns a function that
    stops it. Without it, a switch added with a device id that another switch had before would take that switch's
    device."""

    @callback
    def update(*_: Any) -> None:
        _async_update_devices(hass, entry)

    update()
    entry_id = entry.entry_id
    stops = [
        async_dispatcher_connect(hass, SIGNAL_CONFIG_CHANGED.format(entry_id), update),
        async_dispatcher_connect(hass, SIGNAL_SUBENTRY_UPDATED.format(entry_id), update),
    ]

    @callback
    def stop() -> None:
        for stop_one in stops:
            stop_one()

    return stop


@callback
def _async_update_devices(hass: HomeAssistant, entry: Sonoff2HueConfigEntry) -> None:
    # The identifier and the name of each device, by its subentry: the bridge's device has none, and keeps its name.
    wanted: dict[str | None, tuple[str, str | None]] = {None: (bridge_identifier(entry), None)}
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_SWITCH:
            wanted[subentry.subentry_id] = (switch_of(subentry).normalized_device_id, subentry.title)

    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        target = wanted.get(device.config_subentry_id)
        old = next((value for domain, value in device.identifiers if domain == DOMAIN), None)
        if target is None or old is None:
            continue  # not a device that the platforms made
        identifier, name = target
        if name is not None and device.name != name:
            devices.async_update_device(device.id, name=name)
        if old == identifier:
            continue
        if name is None:
            devices.async_update_device(device.id, new_identifiers={(DOMAIN, identifier)})
        else:
            devices.async_update_device(device.id, new_identifiers={(DOMAIN, identifier)}, serial_number=identifier)
        # The device's own entities are "<identifier>_<key>"; a helper linked to the device belongs to another entry.
        for entity in er.async_entries_for_device(entities, device.id, include_disabled_entities=True):
            if entity.config_entry_id == entry.entry_id:
                key = entity.unique_id.removeprefix(f"{old}_")
                entities.async_update_entity(entity.entity_id, new_unique_id=f"{identifier}_{key}")
