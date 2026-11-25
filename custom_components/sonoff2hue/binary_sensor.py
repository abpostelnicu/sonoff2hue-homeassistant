"""The binary sensors (decision 25): Reachable on the bridge's device, and each switch's Online."""

from datetime import datetime, timedelta
from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time

from .engine import text
from .engine.registry import ONLINE_WINDOW, LanDevice
from .engine.status import StatusSnapshot
from .entity import BridgeEntity, WallSwitchEntity, async_add_switch_entities
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData

PARALLEL_UPDATES = 0

OFFLINE_RECHECK: Final = timedelta(seconds=30)
"""How often an offline switch looks at the LAN registry again, as the dashboard read the status every 30 seconds: a
switch heard again only with a repeated broadcast changes nothing that the registry reports."""


async def async_setup_entry(
    hass: HomeAssistant, entry: Sonoff2HueConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    async_add_entities([ReachableSensor(data)])

    def create(data: Sonoff2HueData, subentry: ConfigSubentry) -> list[BinarySensorEntity]:
        return [OnlineSensor(data, subentry)]

    async_add_switch_entities(hass, entry, async_add_entities, create)


class ReachableSensor(BridgeEntity, BinarySensorEntity):
    """The dashboard's Hue card: whether the bridge was usable when last checked, unknown until then and while it isn't
    paired, with its address, the message while it isn't usable, and the time of the check."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"checked_at"})

    def __init__(self, data: Sonoff2HueData) -> None:
        super().__init__(data, "reachable")

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        hue = snapshot.hue
        self._attr_is_on = hue.reachable
        self._attr_extra_state_attributes = {
            "address": hue.bridge_ip,
            "message": hue.message,
            "checked_at": hue.checked_at,
        }


class OnlineSensor(WallSwitchEntity, BinarySensorEntity):
    """Whether the switch's MINIR4M was heard within the last 5 minutes, with what the LAN registry knows about it.
    It looks again at each status snapshot, when the 5 minutes since it was last heard have passed, and every 30
    seconds while it is offline."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"last_seen_at"})

    def __init__(self, data: Sonoff2HueData, subentry: ConfigSubentry) -> None:
        super().__init__(data, subentry, "online")
        self._next_look: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_next_look)
        self._follow_status()

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        self._look()

    @callback
    def _refresh(self) -> None:
        self._look()
        self.async_write_ha_state()

    @callback
    def _on_time(self, now: datetime) -> None:
        self._next_look = None
        self._refresh()

    @callback
    def _look(self) -> None:
        """Takes the switch's device from the LAN registry, and plans the next look."""
        self._cancel_next_look()
        registry = self._data.registry
        device = registry.find(self._switch.normalized_device_id)
        online = device is not None and registry.is_online(device)
        self._attr_is_on = online
        self._attr_extra_state_attributes = _attributes(device, self._switch.device_key)
        if device is None:
            return
        if online:
            # Online for exactly 5 minutes after it was last heard, then offline.
            when = device.last_seen_at + ONLINE_WINDOW + timedelta(microseconds=1)
            self._next_look = async_track_point_in_utc_time(self.hass, self._on_time, when)
        else:
            self._next_look = async_call_later(self.hass, OFFLINE_RECHECK, self._on_time)

    @callback
    def _cancel_next_look(self) -> None:
        if self._next_look is not None:
            self._next_look()
            self._next_look = None


def _attributes(device: LanDevice | None, device_key: str | None) -> dict[str, object]:
    return {
        "address": None if device is None else device.address,
        "last_seen_at": None if device is None else device.last_seen_at,
        "last_press_at": None if device is None else device.last_press_at,
        "seq": None if device is None else device.seq,
        "encrypted": None if device is None else device.encrypted,
        "has_device_key": not text.is_null_or_white_space(device_key),
        "detached": None if device is None else device.detached,
        "decode_error": None if device is None else device.decode_error,
    }
