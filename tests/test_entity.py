"""The devices of the integration and the entities on them (decision 25): the service device and one per switch, their
unique ids across restarts, switches added, changed and removed while the entry is loaded, also with *Add switch* and
*Edit switch*, the device ids that the bus events carry, the entities' texts and icons, and the devices kept when a
switch gets another name or another MINIR4M. The LAN is on a fake network and the lights in Home Assistant's state
machine."""

import json
from pathlib import Path
from types import MappingProxyType

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentry, ConfigSubentryData
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.translation import async_get_translations
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from custom_components.sonoff2hue import entity
from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    DOMAIN,
    EVENT_PRESS,
    SUBENTRY_SWITCH,
)

from .fakes import CEILING, DEVICE_ID, DEVICE_KEY, OTHER_DEVICE_ID, Lan, Lights, entry, switch
from .platforms import (
    DEVICES_SEEN,
    LAST_ACTION,
    ONLINE,
    PRESS,
    STATUS,
    TEST_TOGGLE,
    settle,
    start,
    state_of,
)

NEW_DEVICE_ID = "1000777777"
BRIDGE_ID = "001788FFFE000001"
"""A made-up Hue bridge id: version 1 identified the bridge's device by it."""
DESK = "light.desk"
DESK_ENTITIES = {
    "event.desk_press": f"{OTHER_DEVICE_ID}_press",
    "sensor.desk_last_action": f"{OTHER_DEVICE_ID}_last_action",
    "binary_sensor.desk_online": f"{OTHER_DEVICE_ID}_online",
    "button.desk_test_toggle": f"{OTHER_DEVICE_ID}_test_toggle",
}
"""The entities of the switch "Desk", by entity id: their unique ids."""
STANDARD_ATTRIBUTES = {"device_class", "event_type", "event_types", "friendly_name", "options", "unit_of_measurement"}
"""The attributes that Home Assistant names itself."""


def new_switch(device_id: str, name: str) -> ConfigSubentry:
    data = {CONF_DEVICE_ID: device_id, CONF_NAME: name, CONF_DEVICE_KEY: None, CONF_TARGETS: [DESK]}
    return ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_SWITCH, title=name, unique_id=device_id)


def subentry_of(config_entry: MockConfigEntry, device_id: str = DEVICE_ID) -> ConfigSubentry:
    return next(subentry for subentry in config_entry.subentries.values() if subentry.unique_id == device_id)


def device_of(hass: HomeAssistant, config_entry: MockConfigEntry, identifier: str) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, identifier), config_entry.entry_id)
    assert device is not None, f"no device {identifier}"
    return device


def entities_of(hass: HomeAssistant, device: dr.DeviceEntry) -> dict[str, str]:
    """The device's entities: unique id by entity id."""
    entries = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    return {registry_entry.entity_id: registry_entry.unique_id for registry_entry in entries}


def service_entities(config_entry: MockConfigEntry) -> dict[str, str]:
    """The service device's entities, as the platforms make them: unique id by entity id."""
    return {STATUS: f"{config_entry.entry_id}_status", DEVICES_SEEN: f"{config_entry.entry_id}_devices_seen"}


def device_ids(hass: HomeAssistant, config_entry: MockConfigEntry) -> set[str]:
    return {device.id for device in dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)}


def entry_entities(hass: HomeAssistant, config_entry: MockConfigEntry) -> dict[str, er.RegistryEntry]:
    entries = er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    return {registry_entry.entity_id: registry_entry for registry_entry in entries}


@pytest.mark.usefixtures("lan", "lights")
async def test_the_service_and_each_switch_have_a_device_with_their_entities(hass: HomeAssistant) -> None:
    other = ConfigSubentryData(data={}, subentry_type="other", title="Other", unique_id=None)
    config_entry = entry(switch(), switch(OTHER_DEVICE_ID, None, targets=[DESK]), other)
    await start(hass, config_entry)

    # A subentry that isn't a switch has no device.
    assert len(device_ids(hass, config_entry)) == 3
    service = device_of(hass, config_entry, config_entry.entry_id)
    assert (
        service.name,
        service.entry_type,
        service.manufacturer,
        service.model,
        service.sw_version,
        service.serial_number,
        service.config_subentry_id,
    ) == ("sonoff2hue", DeviceEntryType.SERVICE, None, None, None, None, None)
    assert entities_of(hass, service) == service_entities(config_entry)

    hall = device_of(hass, config_entry, DEVICE_ID)
    assert (
        hall.name,
        hall.entry_type,
        hall.manufacturer,
        hall.model,
        hall.serial_number,
        hall.config_subentry_id,
    ) == ("Hall", None, "SONOFF", "MINIR4M", DEVICE_ID, subentry_of(config_entry).subentry_id)
    assert entities_of(hass, hall) == {
        PRESS: f"{DEVICE_ID}_press",
        LAST_ACTION: f"{DEVICE_ID}_last_action",
        ONLINE: f"{DEVICE_ID}_online",
        TEST_TOGGLE: f"{DEVICE_ID}_test_toggle",
    }
    # A switch without a name is named by its device id, as its subentry's title is.
    unnamed = device_of(hass, config_entry, OTHER_DEVICE_ID)
    assert unnamed.name == OTHER_DEVICE_ID
    assert set(entities_of(hass, unnamed)) == {
        f"event.{OTHER_DEVICE_ID}_press",
        f"sensor.{OTHER_DEVICE_ID}_last_action",
        f"binary_sensor.{OTHER_DEVICE_ID}_online",
        f"button.{OTHER_DEVICE_ID}_test_toggle",
    }
    assert {
        entity_id: registry_entry.entity_category
        for entity_id, registry_entry in entry_entities(hass, config_entry).items()
        if registry_entry.entity_category is not None
    } == {
        DEVICES_SEEN: EntityCategory.DIAGNOSTIC,
        ONLINE: EntityCategory.DIAGNOSTIC,
        f"binary_sensor.{OTHER_DEVICE_ID}_online": EntityCategory.DIAGNOSTIC,
        TEST_TOGGLE: EntityCategory.CONFIG,
        f"button.{OTHER_DEVICE_ID}_test_toggle": EntityCategory.CONFIG,
    }
    assert state_of(hass, PRESS).attributes["friendly_name"] == "Hall Press"
    assert state_of(hass, STATUS).attributes["friendly_name"] == "sonoff2hue Status"


@pytest.mark.usefixtures("lan", "lights")
async def test_the_devices_and_entities_stay_the_same_across_restarts(hass: HomeAssistant) -> None:
    config_entry = entry(switch())
    await start(hass, config_entry)
    devices = device_ids(hass, config_entry)
    entities = entry_entities(hass, config_entry)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await settle()

    assert device_ids(hass, config_entry) == devices
    assert entry_entities(hass, config_entry) == entities
    assert len(devices) == 2
    assert len(entities) == 6
    for entity_id in entities:
        assert state_of(hass, entity_id).state != "unavailable"


async def test_a_switch_added_changed_and_removed_while_loaded_gets_and_loses_its_device(
    hass: HomeAssistant, lan: Lan, lights: Lights
) -> None:
    lights.add("desk")
    config_entry = entry(switch())
    data = await start(hass, config_entry)
    presses = async_capture_events(hass, EVENT_PRESS)

    # Added: its device and entities, without a reload.
    desk = new_switch(OTHER_DEVICE_ID, "Desk")
    hass.config_entries.async_add_subentry(config_entry, desk)
    await settle()

    assert config_entry.runtime_data is data
    device = device_of(hass, config_entry, OTHER_DEVICE_ID)
    assert (device.name, device.config_subentry_id) == ("Desk", desk.subentry_id)
    assert entities_of(hass, device) == DESK_ENTITIES
    for entity_id in DESK_ENTITIES:
        state_of(hass, entity_id)

    # Its presses name its device, and toggle its light.
    lan.broadcast(1, OTHER_DEVICE_ID)
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()

    assert [(event.data["device_id"], event.data["switch_id"]) for event in presses] == [(device.id, OTHER_DEVICE_ID)]
    assert state_of(hass, "event.desk_press").attributes["seq"] == 2
    assert lights.turned() == [("turn_on", DESK)]

    # Changed: its entities show the new settings, and the other switch's stay as they were.
    hass.config_entries.async_update_subentry(config_entry, desk, data={**desk.data, CONF_DEVICE_KEY: DEVICE_KEY})
    await settle()

    assert state_of(hass, "binary_sensor.desk_online").attributes["has_device_key"] is True
    assert state_of(hass, ONLINE).attributes["has_device_key"] is False

    # Removed: its device and entities go.
    hass.config_entries.async_remove_subentry(config_entry, desk.subentry_id)
    await settle()

    assert dr.async_get(hass).async_get(device.id) is None
    for entity_id in DESK_ENTITIES:
        assert er.async_get(hass).async_get(entity_id) is None
        assert hass.states.get(entity_id) is None
    assert set(entities_of(hass, device_of(hass, config_entry, DEVICE_ID))) == {PRESS, LAST_ACTION, ONLINE, TEST_TOGGLE}


async def test_a_switch_added_with_its_dialog_gets_its_device(hass: HomeAssistant, lan: Lan, lights: Lights) -> None:
    """*Add switch*: the switch subentry flow (decision 24), with the device id entered and the light Ceiling chosen."""
    lights.add("ceiling")
    config_entry = entry()
    await start(hass, config_entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init((config_entry.entry_id, SUBENTRY_SWITCH), context={"source": SOURCE_USER})
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await flows.async_configure(result["flow_id"], {"device_id": DEVICE_ID})
    result = await flows.async_configure(
        result["flow_id"], {"name": "Hall", "lights": [CEILING], "advanced": {"device_key": ""}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await settle()

    device = device_of(hass, config_entry, DEVICE_ID)
    assert (device.name, device.config_subentry_id) == ("Hall", subentry_of(config_entry).subentry_id)
    assert set(entities_of(hass, device)) == {PRESS, LAST_ACTION, ONLINE, TEST_TOGGLE}

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert lights.turned() == [("turn_on", CEILING)]
    assert state_of(hass, PRESS).attributes["seq"] == 2


async def test_a_switch_renamed_or_given_another_minir4m_keeps_its_device_and_entities(
    hass: HomeAssistant, lan: Lan, lights: Lights
) -> None:
    lights.add("desk")
    config_entry = entry(switch(), switch(OTHER_DEVICE_ID, "Desk", targets=[DESK]))
    await start(hass, config_entry)
    desk = subentry_of(config_entry, OTHER_DEVICE_ID)
    device = device_of(hass, config_entry, OTHER_DEVICE_ID)
    service = device_of(hass, config_entry, config_entry.entry_id)
    presses = async_capture_events(hass, EVENT_PRESS)

    # A new name: the device's name; the entity ids stay.
    hass.config_entries.async_update_subentry(config_entry, desk, title="Desk lamp")
    await settle()

    assert device_of(hass, config_entry, OTHER_DEVICE_ID).name == "Desk lamp"
    assert entities_of(hass, device) == DESK_ENTITIES
    assert state_of(hass, "event.desk_press").attributes["friendly_name"] == "Desk lamp Press"

    # Another MINIR4M: the same device and entities, identified by the new device id.
    hass.config_entries.async_update_subentry(
        config_entry, desk, data={**desk.data, CONF_DEVICE_ID: NEW_DEVICE_ID}, unique_id=NEW_DEVICE_ID
    )
    await settle()

    moved = device_of(hass, config_entry, NEW_DEVICE_ID)
    assert (moved.id, moved.serial_number) == (device.id, NEW_DEVICE_ID)
    assert entities_of(hass, moved) == {
        entity_id: unique_id.replace(OTHER_DEVICE_ID, NEW_DEVICE_ID) for entity_id, unique_id in DESK_ENTITIES.items()
    }
    lan.broadcast(11, NEW_DEVICE_ID)
    lan.broadcast(12, NEW_DEVICE_ID)
    await settle()

    assert state_of(hass, "event.desk_press").attributes["seq"] == 12
    assert [(event.data["device_id"], event.data["seq"]) for event in presses] == [(device.id, 12)]
    assert lights.turned() == [("turn_on", DESK)]

    # A switch with the device id that the other one had gets a device of its own.
    shelf = new_switch(OTHER_DEVICE_ID, "Shelf")
    hass.config_entries.async_add_subentry(config_entry, shelf)
    await settle()

    assert device_of(hass, config_entry, OTHER_DEVICE_ID).id != device.id
    assert device_of(hass, config_entry, OTHER_DEVICE_ID).config_subentry_id == shelf.subentry_id
    assert device_of(hass, config_entry, NEW_DEVICE_ID).config_subentry_id == desk.subentry_id
    # The service device is the entry's through all of it.
    assert device_of(hass, config_entry, config_entry.entry_id).id == service.id
    assert entities_of(hass, service) == service_entities(config_entry)


async def test_a_switch_edited_with_its_dialog_keeps_its_device(hass: HomeAssistant, lan: Lan, lights: Lights) -> None:
    """*Edit switch* (decision 24), with another MINIR4M's device id and another name."""
    lights.add("ceiling")
    config_entry = entry(switch())
    await start(hass, config_entry)
    device = device_of(hass, config_entry, DEVICE_ID)
    flows = hass.config_entries.subentries
    context = {"source": SOURCE_RECONFIGURE, "subentry_id": subentry_of(config_entry).subentry_id}
    result = await flows.async_init((config_entry.entry_id, SUBENTRY_SWITCH), context=context)
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await flows.async_configure(result["flow_id"], {"device_id": NEW_DEVICE_ID})
    result = await flows.async_configure(
        result["flow_id"], {"name": "Hallway", "lights": [CEILING], "advanced": {"device_key": ""}}
    )
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "saved")
    await settle()

    moved = device_of(hass, config_entry, NEW_DEVICE_ID)
    assert (moved.id, moved.name, moved.serial_number) == (device.id, "Hallway", NEW_DEVICE_ID)
    assert entities_of(hass, moved) == {
        PRESS: f"{NEW_DEVICE_ID}_press",
        LAST_ACTION: f"{NEW_DEVICE_ID}_last_action",
        ONLINE: f"{NEW_DEVICE_ID}_online",
        TEST_TOGGLE: f"{NEW_DEVICE_ID}_test_toggle",
    }


@pytest.mark.usefixtures("lan", "lights")
async def test_the_changes_made_while_the_entry_was_not_loaded_apply_at_the_start(hass: HomeAssistant) -> None:
    config_entry = entry(switch())
    await start(hass, config_entry)
    service, hall = device_of(hass, config_entry, config_entry.entry_id), device_of(hass, config_entry, DEVICE_ID)
    entity_ids = set(entry_entities(hass, config_entry))
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    subentry = subentry_of(config_entry)
    hass.config_entries.async_update_subentry(
        config_entry,
        subentry,
        data={**subentry.data, CONF_DEVICE_ID: NEW_DEVICE_ID},
        title="Hallway",
        unique_id=NEW_DEVICE_ID,
    )
    await start(hass, config_entry)

    moved = device_of(hass, config_entry, NEW_DEVICE_ID)
    assert (moved.id, moved.name, moved.serial_number) == (hall.id, "Hallway", NEW_DEVICE_ID)
    assert device_of(hass, config_entry, config_entry.entry_id).id == service.id
    assert device_ids(hass, config_entry) == {service.id, hall.id}
    assert set(entry_entities(hass, config_entry)) == entity_ids
    assert entities_of(hass, moved)[PRESS] == f"{NEW_DEVICE_ID}_press"


@pytest.mark.usefixtures("lan", "lights")
async def test_a_device_of_the_entry_without_a_switch_becomes_the_service_device(hass: HomeAssistant) -> None:
    """Such as version 1's Hue bridge's device, when the migration didn't find it by the bridge id that the entry kept:
    the service device's entities find theirs on it, and keep their entity ids."""
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    bridge = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, BRIDGE_ID)}, name="Hue bridge"
    )
    entities.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{BRIDGE_ID}_status",
        config_entry=config_entry,
        device_id=bridge.id,
        suggested_object_id="hue_bridge_status",
    )
    await start(hass, config_entry)

    service = device_of(hass, config_entry, config_entry.entry_id)
    assert (service.id, service.name, service.entry_type) == (bridge.id, "sonoff2hue", DeviceEntryType.SERVICE)
    assert entities_of(hass, service) == {
        "sensor.hue_bridge_status": f"{config_entry.entry_id}_status",
        DEVICES_SEEN: f"{config_entry.entry_id}_devices_seen",
    }
    assert state_of(hass, "sensor.hue_bridge_status").state == "running"
    assert hass.states.get(STATUS) is None


@pytest.mark.usefixtures("lan", "lights")
async def test_another_device_without_a_switch_keeps_its_identifier(hass: HomeAssistant) -> None:
    """Only one device is the service device: another device of the entry without a switch keeps its identifier, which
    the service device's can't be too, and the entry loads."""
    config_entry = entry(switch())
    config_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    service = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, config_entry.entry_id)}, name="sonoff2hue"
    )
    stray = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, BRIDGE_ID)}, name="Hue bridge"
    )

    await start(hass, config_entry)

    assert device_of(hass, config_entry, config_entry.entry_id).id == service.id
    kept = devices.async_get(stray.id)
    assert kept is not None
    assert kept.identifiers == {(DOMAIN, BRIDGE_ID)}


@pytest.mark.usefixtures("lan", "lights")
async def test_devices_and_entities_that_the_platforms_did_not_make_are_left_alone(hass: HomeAssistant) -> None:
    """A helper linked to a switch's device belongs to another entry, a device of a subentry that isn't a switch is no
    switch's, and a device without the integration's identifier isn't the service device."""
    other_subentry = ConfigSubentryData(data={}, subentry_type="other", title="Other", unique_id=None)
    config_entry = entry(switch(), other_subentry)
    config_entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    other_id = next(
        subentry.subentry_id for subentry in config_entry.subentries.values() if subentry.subentry_type == "other"
    )
    other = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        config_subentry_id=other_id,
        identifiers={(DOMAIN, "other")},
        name="Other",
    )
    foreign = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("template", "foreign")}, name="Foreign"
    )
    await start(hass, config_entry)
    helper_entry = MockConfigEntry(domain="template")
    helper_entry.add_to_hass(hass)
    helper = entities.async_get_or_create(
        "sensor",
        "template",
        "helper",
        config_entry=helper_entry,
        device_id=device_of(hass, config_entry, DEVICE_ID).id,
    )

    subentry = subentry_of(config_entry)
    hass.config_entries.async_update_subentry(
        config_entry, subentry, data={**subentry.data, CONF_DEVICE_ID: NEW_DEVICE_ID}, unique_id=NEW_DEVICE_ID
    )
    await settle()

    assert entities_of(hass, device_of(hass, config_entry, NEW_DEVICE_ID))[PRESS] == f"{NEW_DEVICE_ID}_press"
    helper_now = entities.async_get(helper.entity_id)
    assert helper_now is not None
    assert helper_now.unique_id == "helper"
    assert devices.async_get(other.id) == other
    assert devices.async_get(foreign.id) == foreign
    assert entities_of(hass, device_of(hass, config_entry, config_entry.entry_id)) == service_entities(config_entry)


async def test_every_entity_has_its_texts_in_translations_and_its_icons(
    hass: HomeAssistant, lan: Lan, lights: Lights
) -> None:
    """Its name, the names of its states and of its attributes, and nothing more, are in ``translations/en.json``; the
    icons in ``icons.json`` are for the entities and the states that they have."""
    lights.add("ceiling")
    config_entry = entry(switch())
    await start(hass, config_entry)
    # A press, after which the event has its attributes.
    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    texts = await async_get_translations(hass, "en", "entity", [DOMAIN])
    icons = json.loads((Path(entity.__file__).parent / "icons.json").read_text(encoding="utf-8"))["entity"]

    wanted = set()
    states: dict[tuple[str, str], set[str]] = {}
    for registry_entry in entry_entities(hass, config_entry).values():
        domain, key = registry_entry.domain, registry_entry.translation_key
        assert key is not None
        prefix = f"component.{DOMAIN}.entity.{domain}.{key}"
        state = state_of(hass, registry_entry.entity_id)
        states[domain, key] = {"on", "off"} if domain == "binary_sensor" else set(state.attributes.get("options", ()))
        wanted.add(f"{prefix}.name")
        wanted.update(f"{prefix}.state.{option}" for option in state.attributes.get("options", ()))
        wanted.update(
            f"{prefix}.state_attributes.event_type.state.{event_type}"
            for event_type in state.attributes.get("event_types", ())
        )
        if domain == "binary_sensor":
            wanted.update({f"{prefix}.state.on", f"{prefix}.state.off"})
        if "unit_of_measurement" in state.attributes:
            wanted.add(f"{prefix}.unit_of_measurement")
        wanted.update(
            f"{prefix}.state_attributes.{attribute}.name"
            for attribute in state.attributes
            if attribute not in STANDARD_ATTRIBUTES
        )

    assert set(texts) == wanted
    for domain, by_key in icons.items():
        for key, icon in by_key.items():
            assert (domain, key) in states, f"an icon for no entity: {domain}.{key}"
            assert set(icon.get("state", {})) <= states[domain, key], f"an icon for no state of {domain}.{key}"
