"""The buttons (decision 25): each switch's Test toggle, which runs the switch test, with the LAN on a fake network and
the lights in Home Assistant's state machine."""

import logging

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant.common import MockUser

from custom_components.sonoff2hue.const import DOMAIN
from custom_components.sonoff2hue.engine.runner import NO_TARGETS
from custom_components.sonoff2hue.runtime import INTEGRATION_LOGGER

from .fakes import CEILING, Lights, entry, switch
from .platforms import TEST_TOGGLE, start, state_of

HALL = "Hall (1000abcdef)"
DESK = "light.desk"
REMOVED = "0123456789abcdef0123456789abcdef"
"""A light's entity registry id that the registry doesn't have."""


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: TEST_TOGGLE}, blocking=True)


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What the switch test logged."""
    return [record.getMessage() for record in caplog.records if record.name == INTEGRATION_LOGGER.name]


@pytest.mark.usefixtures("lan")
async def test_test_toggle_toggles_the_switchs_lights(
    hass: HomeAssistant, lights: Lights, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    lights.add("desk")
    await start(hass, entry(switch(targets=[lights.target(CEILING), lights.target(DESK)])))
    registry_entry = er.async_get(hass).async_get(TEST_TOGGLE)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.CONFIG

    await press(hass)

    assert sorted(lights.turned()) == [("turn_on", CEILING), ("turn_on", DESK)]

    await press(hass)

    assert sorted(lights.turned()) == [("turn_off", CEILING), ("turn_off", DESK)]
    assert logged(caplog) == [
        f"{HALL}: test from Home Assistant turned ON {CEILING}, {DESK}.",
        f"{HALL}: test from Home Assistant turned OFF {CEILING}, {DESK}.",
    ]


@pytest.mark.usefixtures("lan")
async def test_the_lights_changes_are_the_button_presss(
    hass: HomeAssistant, lights: Lights, hass_admin_user: MockUser
) -> None:
    """The light actions carry the press's context, with its user, as the logbook then shows the lights' changes."""
    lights.add("ceiling")
    await start(hass, entry(switch()))
    context = Context(user_id=hass_admin_user.id)

    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: TEST_TOGGLE}, blocking=True, context=context
    )

    changed = state_of(hass, CEILING).context
    assert (changed.id, changed.user_id) == (context.id, hass_admin_user.id)


@pytest.mark.usefixtures("lan")
async def test_test_toggle_switches_what_it_can_and_logs_the_rest(
    hass: HomeAssistant, lights: Lights, caplog: pytest.LogCaptureFixture
) -> None:
    """A light that Home Assistant no longer has is left out, as a press leaves it out, and the log line names it
    (decision 32); a light that isn't switched is logged. Neither fails the test."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    lights.add("ceiling")
    lights.add("desk")
    kitchen = lights.target(lights.add("kitchen"))
    er.async_get(hass).async_remove("light.kitchen")
    lights.fail(DESK)
    await start(hass, entry(switch(targets=[CEILING, DESK, kitchen])))

    await press(hass)

    assert sorted(lights.turned()) == [("turn_on", CEILING), ("turn_on", DESK)]
    assert lights.is_on(CEILING)
    assert logged(caplog) == [
        f"Could not switch {DESK} on: Request failed: the light didn't answer",
        f"{HALL}: test from Home Assistant turned ON {CEILING}, {DESK}; 1 is missing: light.kitchen was removed from "
        "Home Assistant.",
    ]


@pytest.mark.parametrize(
    ("targets", "message"),
    [
        ([REMOVED], f"{HALL}: a light was removed from Home Assistant."),
        (
            [REMOVED, DESK],
            f"{HALL}: a light was removed from Home Assistant. {HALL}: {DESK} is disabled.",
        ),
        ([], NO_TARGETS),
    ],
    ids=["removed", "removed and disabled", "no lights"],
)
@pytest.mark.usefixtures("lan")
async def test_test_toggle_fails_with_the_reason_when_none_of_its_lights_is_in_home_assistant(
    hass: HomeAssistant, lights: Lights, targets: list[str], message: str
) -> None:
    lights.add("desk")
    er.async_get(hass).async_update_entity(DESK, disabled_by=er.RegistryEntryDisabler.USER)
    await start(hass, entry(switch(targets=targets)))

    with pytest.raises(HomeAssistantError) as caught:
        await press(hass)

    # Translated, and its text is the switch test's message as it was, which the log and the frontend show (decision 36).
    error = caught.value
    assert (error.translation_domain, error.translation_key, error.translation_placeholders) == (
        DOMAIN,
        "test_toggle_failed",
        {"message": message},
    )
    assert str(error) == message
    assert lights.turned() == []
