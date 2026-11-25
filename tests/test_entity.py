"""The devices of the integration and the entities on them (decision 25): the bridge's device and one per switch, their
unique ids across restarts, switches added, changed and removed while the entry is loaded, also with *Add switch*, the
device ids that the bus events give the logbook, and the entities' texts. The LAN is on a fake network and the Hue bridge
in memory."""

from types import MappingProxyType

from homeassistant.config_entries import SOURCE_USER, ConfigSubentry, ConfigSubentryData
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import MockConfigEntry, async_capture_events

from custom_components.sonoff2hue.const import (
    CONF_BRIDGE_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
    EVENT_PRESS,
    SUBENTRY_SWITCH,
)

from .fakes import BRIDGE_ID, DEVICE_ID, DEVICE_KEY, OTHER_DEVICE_ID, Bridge, Lan, paired, switch
from .platforms import (
    DEVICES_SEEN,
    LAST_ACTION,
    ONLINE,
    PRESS,
    REACHABLE,
    STATUS,
    TEST_TOGGLE,
    settle,
    start,
    state_of,
)

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
    data = {CONF_DEVICE_ID: device_id, CONF_NAME: name, CONF_DEVICE_KEY: None, CONF_LIGHTS: ["Desk"], CONF_GROUPS: []}
    return ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_SWITCH, title=name, unique_id=device_id)


def subentry_of(entry: MockConfigEntry, device_id: str = DEVICE_ID) -> ConfigSubentry:
    return next(subentry for subentry in entry.subentries.values() if subentry.unique_id == device_id)


def device_of(hass: HomeAssistant, entry: MockConfigEntry, identifier: str) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, identifier), entry.entry_id)
    assert device is not None, f"no device {identifier}"
    return device


def entities_of(hass: HomeAssistant, device: dr.DeviceEntry) -> dict[str, str]:
    """The device's entities: unique id by entity id."""
    entries = er.async_entries_for_device(er.async_get(hass), device.id, include_disabled_entities=True)
    return {entry.entity_id: entry.unique_id for entry in entries}


async def test_the_bridge_and_each_switch_have_a_device_with_their_entities(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    other = ConfigSubentryData(data={}, subentry_type="other", title="Other", unique_id=None)
    entry = paired(switch(), switch(OTHER_DEVICE_ID, None, lights=["Desk"]), other)
    await start(hass, entry)

    # A subentry that isn't a switch has no device.
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 3
    bridge_device = device_of(hass, entry, BRIDGE_ID)
    assert (
        bridge_device.name,
        bridge_device.manufacturer,
        bridge_device.model,
        bridge_device.sw_version,
        bridge_device.serial_number,
        bridge_device.config_subentry_id,
    ) == ("Hue bridge", "Signify Netherlands B.V.", None, None, None, None)
    assert entities_of(hass, bridge_device) == {
        STATUS: f"{BRIDGE_ID}_status",
        REACHABLE: f"{BRIDGE_ID}_reachable",
        DEVICES_SEEN: f"{BRIDGE_ID}_devices_seen",
    }

    hall = device_of(hass, entry, DEVICE_ID)
    assert (hall.name, hall.manufacturer, hall.model, hall.serial_number, hall.config_subentry_id) == (
        "Hall",
        "SONOFF",
        "MINIR4M",
        DEVICE_ID,
        subentry_of(entry).subentry_id,
    )
    assert entities_of(hass, hall) == {
        PRESS: f"{DEVICE_ID}_press",
        LAST_ACTION: f"{DEVICE_ID}_last_action",
        ONLINE: f"{DEVICE_ID}_online",
        TEST_TOGGLE: f"{DEVICE_ID}_test_toggle",
    }
    # A switch without a name is named by its device id, as its subentry's title is.
    unnamed = device_of(hass, entry, OTHER_DEVICE_ID)
    assert unnamed.name == OTHER_DEVICE_ID
    assert set(entities_of(hass, unnamed)) == {
        f"event.{OTHER_DEVICE_ID}_press",
        f"sensor.{OTHER_DEVICE_ID}_last_action",
        f"binary_sensor.{OTHER_DEVICE_ID}_online",
        f"button.{OTHER_DEVICE_ID}_test_toggle",
    }
    assert {
        entity_id: entity.entity_category
        for entity_id, entity in er.async_get(hass).entities.items()
        if entity.config_entry_id == entry.entry_id and entity.entity_category is not None
    } == {
        REACHABLE: EntityCategory.DIAGNOSTIC,
        DEVICES_SEEN: EntityCategory.DIAGNOSTIC,
        ONLINE: EntityCategory.DIAGNOSTIC,
        f"binary_sensor.{OTHER_DEVICE_ID}_online": EntityCategory.DIAGNOSTIC,
        TEST_TOGGLE: EntityCategory.CONFIG,
        f"button.{OTHER_DEVICE_ID}_test_toggle": EntityCategory.CONFIG,
    }
    assert state_of(hass, PRESS).attributes["friendly_name"] == "Hall Press"
    assert state_of(hass, STATUS).attributes["friendly_name"] == "Hue bridge Status"


async def test_the_devices_and_entities_stay_the_same_across_restarts(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    entry = paired(switch())
    await start(hass, entry)
    devices = {device.id for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    entities = dict(er.async_get(hass).entities.items())

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle()

    assert {device.id for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)} == devices
    assert dict(er.async_get(hass).entities.items()) == entities
    assert len(devices) == 2
    assert len(entities) == 7
    for entity_id in entities:
        assert state_of(hass, entity_id).state != "unavailable"


async def test_a_switch_added_changed_and_removed_while_loaded_gets_and_loses_its_device(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    entry = paired(switch())
    data = await start(hass, entry)
    presses = async_capture_events(hass, EVENT_PRESS)

    # Added: its device and entities, without a reload.
    desk = new_switch(OTHER_DEVICE_ID, "Desk")
    hass.config_entries.async_add_subentry(entry, desk)
    await settle()

    assert entry.runtime_data is data
    device = device_of(hass, entry, OTHER_DEVICE_ID)
    assert (device.name, device.config_subentry_id) == ("Desk", desk.subentry_id)
    assert entities_of(hass, device) == DESK_ENTITIES
    for entity_id in DESK_ENTITIES:
        state_of(hass, entity_id)

    # Its presses name its device, for the logbook.
    lan.broadcast(1, OTHER_DEVICE_ID)
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()

    assert [(event.data["device_id"], event.data["switch_id"]) for event in presses] == [(device.id, OTHER_DEVICE_ID)]
    assert state_of(hass, "event.desk_press").attributes["seq"] == 2

    # Changed: the entities show the new settings.
    hass.config_entries.async_update_subentry(entry, desk, data={**desk.data, CONF_DEVICE_KEY: DEVICE_KEY})
    await settle()

    assert state_of(hass, "binary_sensor.desk_online").attributes["has_device_key"] is True
    assert state_of(hass, ONLINE).attributes["has_device_key"] is False

    # Removed: its device and entities go.
    hass.config_entries.async_remove_subentry(entry, desk.subentry_id)
    await settle()

    assert dr.async_get(hass).async_get(device.id) is None
    for entity_id in DESK_ENTITIES:
        assert er.async_get(hass).async_get(entity_id) is None
        assert hass.states.get(entity_id) is None
    assert set(entities_of(hass, device_of(hass, entry, DEVICE_ID))) == {PRESS, LAST_ACTION, ONLINE, TEST_TOGGLE}


async def test_a_switch_added_with_its_dialog_gets_its_device(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    """*Add switch*: the switch subentry flow (decision 24), with the device id entered and the light Ceiling chosen."""
    entry = paired()
    await start(hass, entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, SUBENTRY_SWITCH), context={"source": SOURCE_USER})
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await flows.async_configure(result["flow_id"], {"device_id": DEVICE_ID})
    result = await flows.async_configure(
        result["flow_id"], {"name": "Hall", "lights": ["0"], "advanced": {"device_key": ""}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await settle()

    device = device_of(hass, entry, DEVICE_ID)
    assert (device.name, device.config_subentry_id) == ("Hall", subentry_of(entry).subentry_id)
    assert set(entities_of(hass, device)) == {PRESS, LAST_ACTION, ONLINE, TEST_TOGGLE}

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()

    assert bridge.puts() == [("l1", True)]
    assert state_of(hass, PRESS).attributes["seq"] == 2


async def test_an_entry_without_a_bridge_id_names_the_bridges_device_by_the_entry(
    hass: HomeAssistant, lan: Lan, bridge: Bridge
) -> None:
    """Pairing always stores the bridge id; without one, the entry's id keeps the device and its unique ids apart."""
    entry = paired(switch())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_BRIDGE_ID: None})
    await start(hass, entry)

    assert entities_of(hass, device_of(hass, entry, entry.entry_id))[STATUS] == f"{entry.entry_id}_status"


async def test_every_entity_has_its_texts_in_translations(hass: HomeAssistant, lan: Lan, bridge: Bridge) -> None:
    """Its name, the names of its states and of its attributes, and nothing more, are in ``translations/en.json``."""
    entry = paired(switch())
    await start(hass, entry)
    # A press, after which the event has its attributes.
    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    texts = await async_get_translations(hass, "en", "entity", [DOMAIN])

    wanted = set()
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        prefix = f"component.{DOMAIN}.entity.{entity.domain}.{entity.translation_key}"
        state = state_of(hass, entity.entity_id)
        wanted.add(f"{prefix}.name")
        wanted.update(f"{prefix}.state.{option}" for option in state.attributes.get("options", ()))
        wanted.update(
            f"{prefix}.state_attributes.event_type.state.{event_type}"
            for event_type in state.attributes.get("event_types", ())
        )
        if entity.domain == "binary_sensor":
            wanted.update({f"{prefix}.state.on", f"{prefix}.state.off"})
        if "unit_of_measurement" in state.attributes:
            wanted.add(f"{prefix}.unit_of_measurement")
        wanted.update(
            f"{prefix}.state_attributes.{attribute}.name"
            for attribute in state.attributes
            if attribute not in STANDARD_ATTRIBUTES
        )

    assert set(texts) == wanted
