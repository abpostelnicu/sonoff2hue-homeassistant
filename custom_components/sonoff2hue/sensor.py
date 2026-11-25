"""The sensors (decision 25): Status and eWeLink devices seen on the bridge's device, and each switch's Last action."""

from datetime import datetime, timedelta
from typing import Any, Final

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .engine.registry import LanDevice
from .engine.status import PRESS_DROPPED, ServiceState, StatusSnapshot, SwitchStatus
from .entity import BridgeEntity, WallSwitchEntity, async_add_switch_entities
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData

PARALLEL_UPDATES = 0

LAN_REFRESH: Final = timedelta(seconds=30)
"""How often eWeLink devices seen shows the time of the last packet again: every packet moves it, and the status tracker
publishes it only with other changes."""

ACTION_TURNED_ON: Final = "turned_on"
ACTION_TURNED_OFF: Final = "turned_off"
ACTION_FAILED: Final = "failed"
ACTION_DROPPED: Final = "dropped"


async def async_setup_entry(
    hass: HomeAssistant, entry: Sonoff2HueConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data
    async_add_entities([StatusSensor(data), DevicesSeenSensor(data)])

    def create(data: Sonoff2HueData, subentry: ConfigSubentry) -> list[SensorEntity]:
        return [LastActionSensor(data, subentry)]

    async_add_switch_entities(hass, entry, async_add_entities, create)


class StatusSensor(BridgeEntity, SensorEntity):
    """The service's state, with its message, its problems, and since when."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, data: Sonoff2HueData) -> None:
        super().__init__(data, "status")
        self._attr_options = [state.value for state in ServiceState]

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        service = snapshot.service
        self._attr_native_value = service.state.value
        self._attr_extra_state_attributes = {
            "message": service.message,
            "problems": list(service.problems),
            "since": service.since,
        }


class DevicesSeenSensor(BridgeEntity, SensorEntity):
    """How many eWeLink devices the LAN has shown, whether the listener runs, and when the last packet came. It follows
    the LAN registry, which the status tracker publishes only for the configured switches' devices: a new device shows
    at once, and the last packet every 30 seconds."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"last_packet_at"})

    def __init__(self, data: Sonoff2HueData) -> None:
        super().__init__(data, "devices_seen")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._data.registry.add_changed_callback(self._on_device))
        self.async_on_remove(
            async_track_time_interval(self.hass, self._on_refresh, LAN_REFRESH, cancel_on_shutdown=True)
        )

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        self._show_registry()

    @callback
    def _on_device(self, device: LanDevice) -> None:
        if len(self._data.registry.snapshot()) != self._attr_native_value:
            self._show_registry()
            self.async_write_ha_state()

    @callback
    def _on_refresh(self, now: datetime) -> None:
        self._show_registry()
        self.async_write_ha_state()

    def _show_registry(self) -> None:
        registry = self._data.registry
        self._attr_native_value = len(registry.snapshot())
        self._attr_extra_state_attributes = {
            "listening": registry.listening,
            "last_packet_at": registry.last_packet_at,
        }


class LastActionSensor(WallSwitchEntity, SensorEntity):
    """A switch's last action, or its last error when that came later: turned on or off, with the toggle's counts and
    time, or failed or dropped, with the message. Its targets are the ones it toggles while the bridge runs."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, data: Sonoff2HueData, subentry: ConfigSubentry) -> None:
        super().__init__(data, subentry, "last_action")
        self._attr_options = [ACTION_TURNED_ON, ACTION_TURNED_OFF, ACTION_FAILED, ACTION_DROPPED]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._follow_status()

    @callback
    def _refresh(self) -> None:
        self._on_status(self._data.status.snapshot())

    @callback
    def _show(self, snapshot: StatusSnapshot) -> None:
        key = self._switch.normalized_device_id.casefold()
        status = next((switch for switch in snapshot.switches if switch.device_id.casefold() == key), None)
        self._attr_native_value, self._attr_extra_state_attributes = _last_action(status)


def _last_action(status: SwitchStatus | None) -> tuple[str | None, dict[str, Any]]:
    """The state and the attributes of a switch's Last action: the newer of its last action and its last error. The
    status tracker records a toggle that some lights failed with its error, at the same time; so an error at the time
    of a toggle without failures came after it, and one at the time of a toggle with failures is that toggle's, which
    shows with the error's message."""
    attributes: dict[str, Any] = dict.fromkeys(
        ("at", "changed", "unchanged", "failed", "elapsed_ms", "targets", "message")
    )
    targets = status.targets if status else None
    attributes["targets"] = None if targets is None else list(targets)
    action = status.last_action if status else None
    error = status.last_error if status else None
    if error is not None and (action is None or error.at > action.at or (error.at == action.at and action.failed == 0)):
        attributes["at"] = error.at
        attributes["message"] = error.message
        return ACTION_DROPPED if error.message == PRESS_DROPPED else ACTION_FAILED, attributes
    if action is None:
        return None, attributes
    attributes |= {
        "at": action.at,
        "changed": action.changed,
        "unchanged": action.unchanged,
        "failed": action.failed,
        "elapsed_ms": action.elapsed_ms,
        "message": error.message if error is not None and error.at == action.at else None,
    }
    return ACTION_TURNED_ON if action.turned_on else ACTION_TURNED_OFF, attributes
