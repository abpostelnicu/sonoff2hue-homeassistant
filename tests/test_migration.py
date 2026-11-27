"""The migration of a config entry of sonoff2hue 1.x, version 1, to version 2 (decision 40): each switch's Hue lights,
rooms and zones become the Hue integration's lights, the entry's bridge settings go, and the bridge's device becomes the
service device."""

from typing import Any

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.helpers.storage import Store
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    CONF_UNMIGRATED,
    DOMAIN,
    ENTRY_TITLE,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.entity import SERVICE_NAME

from .fakes import CEILING, DEVICE_ID, DEVICE_KEY, OTHER_DEVICE_ID, Lan, Lights, switch
from .platforms import settle

BRIDGE_ID = "001788FFFE000001"
"""A made-up Hue bridge id."""
V1_DATA = {"bridge_ip": "192.0.2.10", "bridge_id": BRIDGE_ID, "app_key": "app-key", "certificate_sha256": "AB" * 32}


class Hue:
    """The Hue integration's lights, rooms and zones in Home Assistant's registries, as it registers them: a device per
    light with the light on it, uniquely identified by the Hue light id, and a service device per room or zone, with
    the Hue id of the room or zone as its identifier and its grouped light on it."""

    def __init__(self, hass: HomeAssistant, lights: Lights) -> None:
        self.hass = hass
        self.lights = lights
        self.entry = MockConfigEntry(domain="hue", title="Hue Bridge", unique_id="001788fffe000001")
        self.entry.add_to_hass(hass)

    def light(self, light_id: str, name: str, *, object_id: str | None = None, device: str | None = None) -> str:
        """A light named ``name``, on a device of that name, or on the device ``device`` that another light has; its
        entity id."""
        device_entry = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={("hue", device or f"device-{light_id}")},
            name=name,
            manufacturer="Signify Netherlands B.V.",
        )
        entity_id = (
            er.async_get(self.hass)
            .async_get_or_create(
                "light",
                "hue",
                light_id,
                config_entry=self.entry,
                device_id=device_entry.id,
                suggested_object_id=object_id or name.casefold().replace(" ", "_"),
                translation_key="hue_light",
            )
            .entity_id
        )
        self.hass.states.async_set(entity_id, "off", {"friendly_name": name})
        return entity_id

    def group(self, group_id: str, kind: str, name: str, *, object_id: str | None = None) -> str:
        """A room or zone named ``name``, with its grouped light; its entity id."""
        device_entry = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={("hue", group_id)},
            name=name,
            model=kind.title(),
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        entity_id = (
            er.async_get(self.hass)
            .async_get_or_create(
                "light",
                "hue",
                f"grouped-{group_id}",
                config_entry=self.entry,
                device_id=device_entry.id,
                suggested_object_id=object_id or name.casefold().replace(" ", "_"),
                translation_key="hue_grouped_light",
            )
            .entity_id
        )
        self.hass.states.async_set(entity_id, "off", {"friendly_name": name, "is_hue_group": True, "hue_type": kind})
        return entity_id

    def target(self, entity_id: str) -> str:
        return self.lights.target(entity_id)


@pytest.fixture
def hue(hass: HomeAssistant, lights: Lights) -> Hue:
    return Hue(hass, lights)


def v1_switch(
    device_id: str = DEVICE_ID,
    name: str | None = "Hall",
    *,
    lights: list[Any] | None = None,
    groups: list[Any] | None = None,
    key: str | None = None,
) -> ConfigSubentryData:
    """A ``switch`` subentry of version 1: its Hue lights, rooms and zones by name or id."""
    return ConfigSubentryData(
        data={
            CONF_DEVICE_ID: device_id,
            CONF_NAME: name,
            CONF_DEVICE_KEY: key,
            "lights": lights or [],
            "groups": groups or [],
        },
        subentry_type=SUBENTRY_SWITCH,
        title=name or device_id,
        unique_id=device_id,
    )


def v1_entry(*switches: ConfigSubentryData, data: dict[str, Any] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=ENTRY_TITLE,
        version=1,
        unique_id=BRIDGE_ID,
        data=V1_DATA if data is None else data,
        subentries_data=list(switches),
    )


def remembered(entry: MockConfigEntry, *records: tuple[str, str, str, str, str]) -> dict[str, Any]:
    """Version 1's store of remembered resources for ``entry``: each record a switch's device id, the setting, the
    entry as compared, and the resource's kind and id."""
    return {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {
            "bridge_id": BRIDGE_ID,
            "resources": [
                {"switch": switch, "field": field, "entry": text, "kind": kind, "id": resource_id, "name": "Old name"}
                for switch, field, text, kind, resource_id in records
            ],
        },
    }


def switches(entry: MockConfigEntry) -> dict[str, dict[str, Any]]:
    """The entry's switches' data, by their device ids."""
    return {
        str(subentry.data[CONF_DEVICE_ID]): dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_SWITCH
    }


async def migrate(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle()


@pytest.mark.usefixtures("lan")
async def test_lights_rooms_and_zones_become_the_hue_integrations_lights(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any]
) -> None:
    """By the id that each entry last found, by the id that it is, and by name; in the order of the switch's lights,
    then its rooms and zones, each light once. The rest of the switch stays, its device key too."""
    ceiling = hue.light("l1", "Ceiling")
    desk = hue.light("l2", "Desk")
    lamp = hue.light("l3", "Reading lamp")
    hall = hue.group("r1", "room", "Hall")
    upstairs = hue.group("z1", "zone", "Upstairs")
    entry = v1_entry(
        v1_switch(
            lights=["Ceiling", "l2", "Lamp", "ceiling "], groups=["room:Hall", "zone:upstairs", "r1"], key=DEVICE_KEY
        )
    )
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = remembered(entry, (DEVICE_ID, "lights", "lamp", "light", "l3"))

    await migrate(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert switches(entry) == {
        DEVICE_ID: {
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_NAME: "Hall",
            CONF_DEVICE_KEY: DEVICE_KEY,
            CONF_TARGETS: [hue.target(light) for light in (ceiling, desk, lamp, hall, upstairs)],
        }
    }
    assert entry.runtime_data.config.switches[0].targets == [
        hue.target(light) for light in (ceiling, desk, lamp, hall, upstairs)
    ]


@pytest.mark.usefixtures("lan")
async def test_the_entry_keeps_no_bridge_and_its_store_goes(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any]
) -> None:
    hue.light("l1", "Ceiling")
    entry = v1_entry(v1_switch(lights=["Ceiling"]))
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = remembered(entry)

    await migrate(hass, entry)

    assert (entry.version, entry.minor_version) == (2, 1)
    assert dict(entry.data) == {}
    assert entry.unique_id is None
    assert entry.title == ENTRY_TITLE
    assert f"{DOMAIN}.{entry.entry_id}" not in hass_storage


@pytest.mark.usefixtures("lan")
async def test_entries_without_a_light_are_kept_for_a_repair_that_edits_the_switch(
    hass: HomeAssistant, hue: Hue
) -> None:
    """An entry is left over when no Hue light, room or zone has its id or its name, or when more than one has its name:
    two lights of one device share its name, and so may a room and a zone. The switch then has the other lights, and a
    repair names the entries and leads to the switch's dialog."""
    hue.light("l1", "Pendant", object_id="pendant_up")
    hue.light("l2", "Pendant", object_id="pendant_down", device="device-l1")
    hue.group("r1", "room", "Kitchen")
    hue.group("z1", "zone", "Kitchen", object_id="kitchen_zone")
    desk = hue.light("l3", "Desk")
    entry = v1_entry(
        v1_switch(lights=["Pendant", "Gone", "Desk"], groups=["Kitchen", "room:Nowhere"]),
        v1_switch(OTHER_DEVICE_ID, "Porch", lights=["Desk"]),
    )

    await migrate(hass, entry)

    hall = switches(entry)[DEVICE_ID]
    assert hall[CONF_TARGETS] == [hue.target(desk)]
    assert hall[CONF_UNMIGRATED] == ["Pendant", "Gone", "Kitchen", "room:Nowhere"]
    assert CONF_UNMIGRATED not in switches(entry)[OTHER_DEVICE_ID]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"unmigrated_{DEVICE_ID}")
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "name": f"Hall ({DEVICE_ID})",
        "entries": "- `Pendant`\n- `Gone`\n- `Kitchen`\n- `room:Nowhere`",
    }
    hall_subentry = next(s for s in entry.subentries.values() if s.data[CONF_DEVICE_ID] == DEVICE_ID)
    assert issue.data == {"entry_id": entry.entry_id, "subentry_id": hall_subentry.subentry_id}
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"unmigrated_{OTHER_DEVICE_ID}") is None


@pytest.mark.usefixtures("lan")
async def test_lights_of_one_name_migrate_by_their_ids(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any]
) -> None:
    """Version 1 saved a light by its id when another light had its name, so each is found by its id, also with the
    store gone."""
    up = hue.light("l1", "Pendant", object_id="pendant_up")
    down = hue.light("l2", "Pendant", object_id="pendant_down", device="device-l1")
    entry = v1_entry(v1_switch(lights=["l1", "l2"]))

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [hue.target(up), hue.target(down)]


@pytest.mark.usefixtures("lan")
async def test_a_room_and_a_zone_of_one_name_are_told_apart_by_their_prefixes(hass: HomeAssistant, hue: Hue) -> None:
    room = hue.group("r1", "room", "Kitchen")
    zone = hue.group("z1", "zone", "Kitchen", object_id="kitchen_zone")
    entry = v1_entry(v1_switch(groups=["ROOM: kitchen", "zone:Kitchen"]))

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [hue.target(room), hue.target(zone)]


@pytest.mark.usefixtures("lan")
async def test_a_light_the_user_renamed_is_found_by_its_new_name(hass: HomeAssistant, hue: Hue) -> None:
    """A name compares with the light's name as Home Assistant shows it: the user's for the entity, else its device's."""
    renamed = hue.light("l1", "Ceiling")
    er.async_get(hass).async_update_entity(renamed, name="Hall ceiling")
    entry = v1_entry(v1_switch(lights=["hall ceiling", "Ceiling"]))

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [hue.target(renamed)]
    assert switches(entry)[DEVICE_ID][CONF_UNMIGRATED] == ["Ceiling"]


@pytest.mark.usefixtures("lan")
async def test_without_the_hue_integration_every_entry_is_left_over(hass: HomeAssistant, lights: Lights) -> None:
    """Lights of other integrations aren't Hue's, even with the same name. The switch is then left without a light,
    which is no configuration problem: the switches run."""
    lights.add("ceiling", name="Ceiling")
    entry = v1_entry(v1_switch(lights=["Ceiling"], groups=["room:Hall"]))

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == []
    assert switches(entry)[DEVICE_ID][CONF_UNMIGRATED] == ["Ceiling", "room:Hall"]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.status.snapshot().service.state == "running"


async def test_a_switch_left_without_a_light_stops_no_other_switch(hass: HomeAssistant, hue: Hue, lan: Lan) -> None:
    """Its presses fail until its lights are chosen again, while the other switches toggle theirs."""
    desk = hue.light("l1", "Desk")
    entry = v1_entry(v1_switch(lights=["Gone"]), v1_switch(OTHER_DEVICE_ID, "Porch", lights=["Desk"]))

    await migrate(hass, entry)
    assert entry.runtime_data.status.snapshot().service.message == "2 wall switches toggle 1 light."

    lan.broadcast(1, OTHER_DEVICE_ID)
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()

    assert hue.lights.turned() == [("turn_on", desk)]


@pytest.mark.usefixtures("lan", "lights")
async def test_the_bridges_device_becomes_the_service_device_and_keeps_its_entity_ids(hass: HomeAssistant) -> None:
    """Status and eWeLink devices seen keep their entity ids, with the unique ids of the service device; Reachable goes,
    as nothing provides it any more. The switches' devices stay as they were."""
    entry = v1_entry(v1_switch(lights=["Ceiling"]))
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    bridge = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, BRIDGE_ID)},
        name="Hue bridge",
        manufacturer="Signify Netherlands B.V.",
    )
    for domain, key, object_id in (
        ("sensor", "status", "hue_bridge_status"),
        ("sensor", "devices_seen", "hue_bridge_ewelink_devices_seen"),
        ("binary_sensor", "reachable", "hue_bridge_reachable"),
    ):
        entities.async_get_or_create(
            domain,
            DOMAIN,
            f"{BRIDGE_ID}_{key}",
            config_entry=entry,
            device_id=bridge.id,
            suggested_object_id=object_id,
            translation_key=key,
        )
    hall = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=next(iter(entry.subentries)),
        identifiers={(DOMAIN, DEVICE_ID)},
        name="Hall",
    )
    last_action = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DEVICE_ID}_last_action",
        config_entry=entry,
        device_id=hall.id,
        suggested_object_id="hall_last_action",
        translation_key="last_action",
    )

    await migrate(hass, entry)

    service = devices.async_get(bridge.id)
    assert service is not None
    assert service.identifiers == {(DOMAIN, entry.entry_id)}
    assert (service.name, service.manufacturer, service.entry_type) == (
        SERVICE_NAME,
        None,
        dr.DeviceEntryType.SERVICE,
    )
    assert devices.async_get_device_by_identifier((DOMAIN, BRIDGE_ID), entry.entry_id) is None
    status = entities.async_get("sensor.hue_bridge_status")
    seen = entities.async_get("sensor.hue_bridge_ewelink_devices_seen")
    assert status is not None
    assert seen is not None
    assert (status.unique_id, seen.unique_id) == (f"{entry.entry_id}_status", f"{entry.entry_id}_devices_seen")
    assert entities.async_get("binary_sensor.hue_bridge_reachable") is None
    assert hass.states.get("sensor.hue_bridge_status") is not None
    assert hass.states.get("sensor.sonoff2hue_status") is None
    assert devices.async_get_device_by_identifier((DOMAIN, DEVICE_ID), entry.entry_id) is not None
    kept = entities.async_get(last_action.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{DEVICE_ID}_last_action"


@pytest.mark.usefixtures("lan", "lights")
async def test_an_entry_that_was_never_paired_migrates_too(hass: HomeAssistant) -> None:
    """Version 1 named the bridge's device by the entry's id until the bridge was paired."""
    entry = v1_entry(v1_switch(lights=["Ceiling"]), data={})
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    bridge = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="Hue bridge"
    )

    await migrate(hass, entry)

    service = devices.async_get(bridge.id)
    assert service is not None
    assert service.identifiers == {(DOMAIN, entry.entry_id)}
    assert service.name == SERVICE_NAME
    assert entry.version == 2


@pytest.mark.usefixtures("lan", "lights")
async def test_a_version_newer_than_this_one_is_refused(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title=ENTRY_TITLE, version=3, data={"future": True})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert dict(entry.data) == {"future": True}


@pytest.mark.usefixtures("lan", "lights")
async def test_another_minor_version_of_2_loads_as_it_is(hass: HomeAssistant) -> None:
    """A later release may add to version 2 with a minor version, which this one runs without a change."""
    entry = MockConfigEntry(
        domain=DOMAIN, title=ENTRY_TITLE, version=2, minor_version=2, data={}, subentries_data=[switch()]
    )

    await migrate(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (2, 2)
    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [CEILING]


@pytest.mark.usefixtures("lan", "lights")
async def test_a_subentry_of_another_type_is_left_as_it_is(hass: HomeAssistant) -> None:
    other = ConfigSubentryData(data={"lights": ["Ceiling"]}, subentry_type="other", title="Other", unique_id="other")
    entry = v1_entry(v1_switch(lights=["Ceiling"]), other)

    await migrate(hass, entry)

    kept = next(subentry for subentry in entry.subentries.values() if subentry.subentry_type == "other")
    assert dict(kept.data) == {"lights": ["Ceiling"]}


@pytest.mark.usefixtures("lan")
async def test_records_of_the_store_that_cant_be_read_are_left_out(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any]
) -> None:
    """Such as a kind of resource that version 1 never remembered for the setting, a light's entry found as a room. The
    store's other records still give their lights."""
    lamp = hue.light("l3", "Reading lamp")
    hue.group("r1", "room", "Hall")
    entry = v1_entry(v1_switch(lights=["Lamp", "Spot"]))
    store = remembered(
        entry,
        (DEVICE_ID, "lights", "lamp", "light", "l3"),
        (DEVICE_ID, "lights", "lamp", "room", "r1"),
        (DEVICE_ID, "lights", "spot", "bulb", "l9"),
    )
    store["data"]["resources"].insert(0, "not a record")
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = store

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [hue.target(lamp)]
    assert switches(entry)[DEVICE_ID][CONF_UNMIGRATED] == ["Spot"]


@pytest.mark.usefixtures("lan")
@pytest.mark.parametrize(("tag", "mapped"), [(BRIDGE_ID.lower(), True), ("001788FFFE000002", False), (None, False)])
async def test_the_store_counts_only_for_the_entrys_bridge(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any], tag: str | None, mapped: bool
) -> None:
    """Version 1 tagged its store with the id of the bridge that it was paired with, and read it for that bridge only,
    without regard to case."""
    lamp = hue.light("l3", "Reading lamp")
    entry = v1_entry(v1_switch(lights=["Lamp"]))
    store = remembered(entry, (DEVICE_ID, "lights", "lamp", "light", "l3"))
    store["data"]["bridge_id"] = tag
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = store

    await migrate(hass, entry)

    hall = switches(entry)[DEVICE_ID]
    assert (hall[CONF_TARGETS], hall.get(CONF_UNMIGRATED)) == (([hue.target(lamp)], None) if mapped else ([], ["Lamp"]))


@pytest.mark.usefixtures("lan")
async def test_only_the_lights_of_rooms_and_zones_are_theirs(hass: HomeAssistant, hue: Hue) -> None:
    """A grouped light on a device that is neither a room nor a zone, or on no device, is neither."""
    hue.group("h1", "bridge", "Home")
    er.async_get(hass).async_get_or_create(
        "light",
        "hue",
        "grouped-orphan",
        config_entry=hue.entry,
        suggested_object_id="orphan",
        translation_key="hue_grouped_light",
    )
    entry = v1_entry(v1_switch(groups=["Home", "orphan"]))

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == []
    assert switches(entry)[DEVICE_ID][CONF_UNMIGRATED] == ["Home", "orphan"]


@pytest.mark.usefixtures("lan")
async def test_an_unreadable_store_migrates_by_ids_and_names(
    hass: HomeAssistant, hue: Hue, hass_storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ceiling = hue.light("l1", "Ceiling")
    entry = v1_entry(v1_switch(lights=["Ceiling", "Lamp"]))

    async def unreadable(self: Store[Any]) -> None:
        raise HomeAssistantError("the file is damaged")

    monkeypatch.setattr(Store, "async_load", unreadable)

    await migrate(hass, entry)

    assert switches(entry)[DEVICE_ID][CONF_TARGETS] == [hue.target(ceiling)]
    assert switches(entry)[DEVICE_ID][CONF_UNMIGRATED] == ["Lamp"]
