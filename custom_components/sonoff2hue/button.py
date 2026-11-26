"""The buttons (decision 25): each switch's Test toggle, which runs the switch test."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import WallSwitchEntity, async_add_switch_entities
from .runtime import Sonoff2HueConfigEntry, Sonoff2HueData, SwitchTestError

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: Sonoff2HueConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    def create(data: Sonoff2HueData, subentry: ConfigSubentry) -> list[ButtonEntity]:
        return [SwitchTestButton(data, subentry)]

    async_add_switch_entities(hass, entry, async_add_entities, create)


class SwitchTestButton(WallSwitchEntity, ButtonEntity):
    """Toggles the switch's lights, rooms and zones once. The runtime logs the result; the reason it couldn't toggle is
    the action's error, translated as ``test_toggle_failed`` with the switch test's message (decision 36)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: Sonoff2HueData, subentry: ConfigSubentry) -> None:
        super().__init__(data, subentry, "test_toggle")

    async def async_press(self) -> None:
        try:
            await self._data.async_test_toggle(self._switch.normalized_device_id)
        except SwitchTestError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="test_toggle_failed",
                translation_placeholders={"message": str(err)},
            ) from err
