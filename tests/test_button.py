"""The buttons (decision 25): each switch's Test toggle, which runs the switch test, with the LAN on a fake network and
the Hue bridge in memory."""

import logging

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.runtime import INTEGRATION_LOGGER, TEST_NOT_PAIRED

from .fakes import Bridge, Lan, paired, switch
from .platforms import TEST_TOGGLE, start

HALL = "Hall (1000abcdef)"


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: TEST_TOGGLE}, blocking=True)


async def test_test_toggle_toggles_the_lights_rooms_and_zones(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    await start(hass, paired(switch(lights=["Ceiling"], groups=["Hall"])))
    registry_entry = er.async_get(hass).async_get(TEST_TOGGLE)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.CONFIG

    await press(hass)
    await press(hass)

    assert bridge.puts() == [("l1", True), ("g1", True), ("l1", False), ("g1", False)]
    lines = [record.getMessage() for record in caplog.records if record.name == INTEGRATION_LOGGER.name]
    assert lines == [
        f"{HALL}: test from Home Assistant turned ON light 'Ceiling', group 'Hall'.",
        f"{HALL}: test from Home Assistant turned OFF light 'Ceiling', group 'Hall'.",
    ]


@pytest.mark.parametrize(
    ("entry", "fault", "message"),
    [
        (paired(switch(), app_key=None), None, TEST_NOT_PAIRED),
        (
            paired(switch(lights=["Kitchen"])),
            None,
            f"{HALL}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Desk'",
        ),
        (
            paired(switch()),
            ("/clip/v2/resource/light", 503),
            "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light",
        ),
    ],
    ids=["not paired", "not on the bridge", "bridge error"],
)
async def test_test_toggle_fails_with_the_reason(
    hass: HomeAssistant,
    lan: Lan,
    bridge: Bridge,
    entry: MockConfigEntry,
    fault: tuple[str, int] | None,
    message: str,
) -> None:
    await start(hass, entry)
    if fault is not None:
        bridge.fail(*fault)

    with pytest.raises(HomeAssistantError) as caught:
        await press(hass)

    assert str(caught.value) == message
    assert bridge.puts() == []
