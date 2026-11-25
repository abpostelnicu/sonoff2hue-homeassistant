"""The event entities (decision 25): each switch's Press, one event for each press that the bridge acts on."""

from collections.abc import Mapping
from typing import Any, Final

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_SEQ, ATTR_SWITCH_ID, EVENT_PRESS
from .engine import text
from .entity import WallSwitchEntity, async_add_switch_entities
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData

PARALLEL_UPDATES = 0

EVENT_TYPE_PRESS: Final = "press"


async def async_setup_entry(
    hass: HomeAssistant, entry: Sonoff2HueConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    def create(data: Sonoff2HueData, subentry: ConfigSubentry) -> list[EventEntity]:
        return [PressEvent(data, subentry)]

    async_add_switch_entities(hass, entry, async_add_entities, create)


class PressEvent(WallSwitchEntity, EventEntity):
    """A press of the wall switch, from the runtime's ``sonoff2hue_press`` event, with the broadcast's ``seq``. The
    toggle that follows is the switch's Last action."""

    _attr_device_class = EventDeviceClass.BUTTON

    def __init__(self, data: Sonoff2HueData, subentry: ConfigSubentry) -> None:
        super().__init__(data, subentry, "press")
        self._attr_event_types = [EVENT_TYPE_PRESS]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_PRESS, self._on_press, self._is_this_switch))

    @callback
    def _is_this_switch(self, data: Mapping[str, Any]) -> bool:
        return text.equals_ignore_case(data[ATTR_SWITCH_ID], self._switch.normalized_device_id)

    @callback
    def _on_press(self, event: Event[Mapping[str, Any]]) -> None:
        self._trigger_event(EVENT_TYPE_PRESS, {ATTR_SEQ: event.data[ATTR_SEQ]})
        self.async_write_ha_state()
