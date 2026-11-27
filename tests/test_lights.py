"""Home Assistant's lights as the switches toggle them (decision 40): their states and actions, the entity registry ids
by which a switch keeps them, what they resolve to, the registry changes that may change that, and the Hue room or zone
that holds exactly a switch's lights."""

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.lights import LightError
from custom_components.sonoff2hue.lights import (
    GroupOffer,
    HassLights,
    async_track_targets,
    entity_ids_of,
    find_group,
    resolve,
    resolve_switch,
    target_of,
)

from .fakes import CEILING, DEVICE_ID, OTHER_DEVICE_ID, Lights

HALL = f"Hall ({DEVICE_ID})"
UNKNOWN_TARGET = "0123456789abcdef0123456789abcdef"
"""A made-up entity registry id, which the registry has never had."""


def hall(*targets: str) -> SwitchSettings:
    return SwitchSettings(DEVICE_ID, "Hall", targets=list(targets))


# The states and the actions.


@pytest.mark.parametrize(
    ("state", "available", "on"),
    [
        (None, False, None),
        ("unavailable", False, None),
        ("unknown", True, None),
        ("off", True, False),
        ("on", True, True),
    ],
)
async def test_a_light_is_available_and_on_as_its_state_says(
    hass: HomeAssistant, state: str | None, available: bool, on: bool | None
) -> None:
    if state is not None:
        hass.states.async_set(CEILING, state)
    lights = HassLights(hass)

    assert (lights.is_available(CEILING), lights.is_on(CEILING)) == (available, on)


async def test_a_light_is_switched_with_the_light_actions_which_are_waited_for(
    hass: HomeAssistant, lights: Lights
) -> None:
    ceiling = lights.add("ceiling")
    control = HassLights(hass)
    held = lights.hold(ceiling)

    turning = asyncio.create_task(control.turn(ceiling, True))
    await asyncio.sleep(0)

    assert lights.turned() == [("turn_on", ceiling)]
    assert not turning.done()

    held.set()
    await turning

    assert lights.is_on(ceiling)

    await control.turn(ceiling, False)

    assert lights.turned() == [("turn_off", ceiling)]
    assert not lights.is_on(ceiling)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Request failed: the light didn't answer", "Request failed: the light didn't answer"),
        ("", "HomeAssistantError"),
    ],
    ids=["its message", "without a message"],
)
async def test_an_error_of_home_assistant_is_a_light_error(
    hass: HomeAssistant, lights: Lights, message: str, expected: str
) -> None:
    ceiling = lights.add("ceiling")
    lights.fail(ceiling, message)

    with pytest.raises(LightError) as caught:
        await HassLights(hass).turn(ceiling, True)

    assert str(caught.value) == expected
    assert isinstance(caught.value.__cause__, HomeAssistantError)


async def test_without_the_light_actions_a_light_is_not_switched(hass: HomeAssistant) -> None:
    hass.states.async_set(CEILING, "off")

    with pytest.raises(LightError, match=r"^Action light\.turn_off not found$"):
        await HassLights(hass).turn(CEILING, False)


# The lights of a switch's settings.


async def test_a_light_is_kept_by_its_registry_id_else_by_its_entity_id(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    porch = lights.add("porch", registered=False)
    registry = er.async_get(hass)

    assert target_of(registry, ceiling) == lights.target(ceiling)
    assert target_of(registry, porch) == porch


async def test_the_entity_ids_of_a_switchs_lights_are_those_they_have_now(hass: HomeAssistant, lights: Lights) -> None:
    """In the switch's order, once each, without the lights that Home Assistant no longer has; a disabled light is still
    there."""
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    porch = lights.add("porch", registered=False)
    registry = er.async_get(hass)
    targets = [lights.target(desk), porch, UNKNOWN_TARGET, ceiling, lights.target(ceiling)]
    registry.async_update_entity(desk, new_entity_id="light.desk_lamp")
    registry.async_update_entity(ceiling, disabled_by=er.RegistryEntryDisabler.USER)

    assert entity_ids_of(registry, targets) == ["light.desk_lamp", porch, ceiling]


async def test_a_switch_toggles_the_entity_ids_that_its_lights_have_now(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    switch = hall(lights.target(ceiling), lights.target(desk))
    er.async_get(hass).async_update_entity(desk, new_entity_id="light.desk_lamp")

    binding = resolve_switch(er.async_get(hass), switch)

    assert binding.settings is switch
    assert (binding.targets, binding.problems) == ([ceiling, "light.desk_lamp"], [])


async def test_a_light_is_toggled_once_however_often_the_switch_has_it(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")

    binding = resolve_switch(er.async_get(hass), hall(lights.target(ceiling), ceiling, lights.target(ceiling)))

    assert (binding.targets, binding.problems) == ([ceiling], [])


async def test_an_entity_id_that_the_registry_doesnt_have_is_kept(hass: HomeAssistant) -> None:
    """Whether Home Assistant has the light shows when it is toggled."""
    binding = resolve_switch(er.async_get(hass), hall("light.porch"))

    assert (binding.targets, binding.problems) == (["light.porch"], [])


async def test_a_removed_light_is_left_out_and_named_while_the_registry_remembers_it(
    hass: HomeAssistant, lights: Lights
) -> None:
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    switch = hall(lights.target(ceiling), lights.target(desk), UNKNOWN_TARGET)
    er.async_get(hass).async_remove(desk)

    binding = resolve_switch(er.async_get(hass), switch)

    assert (binding.targets, binding.problems) == (
        [ceiling],
        [f"{HALL}: {desk} was removed from Home Assistant.", f"{HALL}: a light was removed from Home Assistant."],
    )


async def test_a_disabled_light_is_left_out(hass: HomeAssistant, lights: Lights) -> None:
    """By its registry id or by its entity id."""
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    registry = er.async_get(hass)
    for light in (ceiling, desk):
        registry.async_update_entity(light, disabled_by=er.RegistryEntryDisabler.USER)

    binding = resolve_switch(registry, hall(lights.target(ceiling), desk))

    assert (binding.targets, binding.problems) == (
        [],
        [f"{HALL}: {ceiling} is disabled.", f"{HALL}: {desk} is disabled."],
    )


async def test_each_switch_is_resolved_in_the_configurations_order(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    porch = SwitchSettings(OTHER_DEVICE_ID, targets=[UNKNOWN_TARGET])
    hall_switch = hall(lights.target(ceiling))

    bindings = resolve(hass, EngineConfig([porch, hall_switch]))

    assert [(binding.settings, binding.targets, binding.problems) for binding in bindings] == [
        (porch, [], [f"{OTHER_DEVICE_ID}: a light was removed from Home Assistant."]),
        (hall_switch, [ceiling], []),
    ]


# The registry changes that may change what the lights resolve to.


async def test_the_registry_changes_of_lights_are_followed_until_stopped(hass: HomeAssistant) -> None:
    """A light added, removed, given another entity id, or disabled or enabled; nothing else."""
    registry = er.async_get(hass)
    changes = 0

    def changed() -> None:
        nonlocal changes
        changes += 1

    stop = async_track_targets(hass, changed)
    sensor = registry.async_get_or_create("sensor", "test", "temperature").entity_id
    registry.async_update_entity(sensor, new_entity_id="sensor.outside")
    registry.async_update_entity("sensor.outside", disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_remove("sensor.outside")

    assert changes == 0

    lamp = registry.async_get_or_create("light", "test", "lamp").entity_id

    assert changes == 1

    registry.async_update_entity(lamp, name="Desk lamp", icon="mdi:lamp")

    assert changes == 1

    registry.async_update_entity(lamp, new_entity_id="light.desk_lamp")

    assert changes == 2

    registry.async_update_entity("light.desk_lamp", disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_update_entity("light.desk_lamp", disabled_by=None)

    assert changes == 4

    registry.async_remove("light.desk_lamp")

    assert changes == 5

    stop()
    registry.async_get_or_create("light", "test", "porch")

    assert changes == 5


# The Hue room or zone that holds exactly a switch's lights (decision 34).


def hue_group(lights: Lights, object_id: str, name: str, members: list[str], kind: str = "room") -> str:
    """The light of a Hue room or zone, as the Hue integration shows it."""
    return lights.add(object_id, name=name, attributes={"is_hue_group": True, "hue_type": kind, "entity_id": members})


async def test_the_room_whose_light_switches_exactly_the_lights_is_offered(hass: HomeAssistant, lights: Lights) -> None:
    """With the names of the lights that it would take the place of, in the switch's order and once each, whatever the
    order of the room's lights."""
    ceiling = lights.add("ceiling", name="Ceiling")
    desk = lights.add("desk", name="Desk")
    room = hue_group(lights, "hall", "Hall", [desk, ceiling])

    assert find_group(hass, [ceiling, desk, ceiling]) == GroupOffer(room, "Hall", "room", ("Ceiling", "Desk"))


async def test_a_room_comes_before_a_zone_then_the_first_name(hass: HomeAssistant, lights: Lights) -> None:
    """Names compare without regard to case."""
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    hue_group(lights, "downstairs", "Downstairs", [ceiling, desk], "zone")
    hue_group(lights, "hall", "Hall", [ceiling, desk])
    entrance = hue_group(lights, "entrance", "entrance", [ceiling, desk])

    assert find_group(hass, [ceiling, desk]) == GroupOffer(entrance, "entrance", "room", ("ceiling", "desk"))


async def test_a_zone_is_offered_as_a_zone(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    zone = hue_group(lights, "upstairs", "Upstairs", [ceiling, desk], "zone")

    assert find_group(hass, [ceiling, desk]) == GroupOffer(zone, "Upstairs", "zone", ("ceiling", "desk"))


async def test_no_group_is_offered_for_fewer_than_two_lights(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    hue_group(lights, "hall", "Hall", [ceiling])

    assert find_group(hass, [ceiling, ceiling]) is None


@pytest.mark.parametrize(
    "attributes",
    [
        {"is_hue_group": True, "entity_id": ["light.ceiling", "light.desk", "light.lamp"]},
        {"is_hue_group": True, "entity_id": ["light.ceiling"]},
        {"is_hue_group": True},
        {"entity_id": ["light.ceiling", "light.desk"]},
        {"is_hue_group": False, "entity_id": ["light.ceiling", "light.desk"]},
    ],
    ids=["more lights", "fewer lights", "no lights", "not a hue group", "marked as no group"],
)
async def test_only_a_hue_group_of_exactly_the_lights_is_offered(
    hass: HomeAssistant, lights: Lights, attributes: dict[str, Any]
) -> None:
    """The Hue integration's, with exactly the chosen lights: a light group of Home Assistant's isn't one."""
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    lights.add("lamp")
    lights.add("hall", name="Hall", attributes=attributes)

    assert find_group(hass, [ceiling, desk]) is None


async def test_no_group_is_offered_for_a_group_or_a_light_without_a_state(hass: HomeAssistant, lights: Lights) -> None:
    ceiling = lights.add("ceiling")
    desk = lights.add("desk")
    hall_room = hue_group(lights, "hall", "Hall", [ceiling, desk])
    hue_group(lights, "upstairs", "Upstairs", [hall_room, desk], "zone")
    hue_group(lights, "porch", "Porch", [ceiling, "light.porch"])

    assert find_group(hass, [hall_room, desk]) is None
    assert find_group(hass, [ceiling, "light.porch"]) is None
