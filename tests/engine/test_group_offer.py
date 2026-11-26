"""The room or zone that the switch dialog offers for a switch's single lights (engine/group_offer.py, decision 34): the
lights the switch names, the room or zone that holds exactly them, the name of a zone to create, and the switch with
the room or zone in their place."""

import pytest

from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.group_offer import (
    CreateZone,
    UseGroup,
    find_offer,
    single_lights,
    with_group,
    zone_name,
)
from custom_components.sonoff2hue.engine.hue_client import HueGroup, HueLight
from custom_components.sonoff2hue.engine.switch_rules import BridgeLists, SwitchInput

DEVICE = "1000abcdef"
CEILING = HueLight("l1", "Ceiling", True)
FLOOR_LAMP = HueLight("l2", "Floor lamp", False)
HALLWAY = HueLight("l3", "Hallway", False)
DESK = HueLight("l4", "Desk", False)
LIGHTS = [CEILING, FLOOR_LAMP, HALLWAY, DESK]
LIVING_ROOM = HueGroup("r1", "Living room", "room", "g1", frozenset({"l1", "l2"}))
STUDY = HueGroup("r2", "Study", "room", "g2", frozenset({"l4"}))
DOWNSTAIRS = HueGroup("z1", "Downstairs", "zone", "g3", frozenset({"l1", "l2", "l3"}))
LISTS = BridgeLists(LIGHTS, [LIVING_ROOM, STUDY, DOWNSTAIRS])


def switch(*lights: str, groups: tuple[str, ...] = (), name: str | None = "Hall") -> SwitchSettings:
    return SwitchSettings(DEVICE, name, None, list(lights), list(groups))


def group(kind: str, group_id: str, name: str, *light_ids: str) -> HueGroup:
    return HueGroup(group_id, name, kind, f"grouped-{group_id}", frozenset(light_ids))


# The single lights.


def test_the_single_lights_are_those_that_the_entries_name_each_once_in_their_order() -> None:
    lamps = [*LIGHTS, HueLight("l5", "Lamp", False), HueLight("l6", "lamp", False)]

    found = single_lights(switch("hallway", " l1 ", "CEILING", "Kitchen", "Lamp", "l6"), lamps)

    assert found == (HALLWAY, CEILING, lamps[5])


@pytest.mark.parametrize(
    "settings",
    [
        switch(),
        switch("Ceiling"),
        switch("Ceiling", "l1", " ceiling "),
        switch("Ceiling", groups=("room:Living room",)),
        switch(groups=("room:Living room", "zone:Downstairs")),
        switch("Ceiling", "Kitchen"),
    ],
)
def test_fewer_than_two_single_lights_are_offered_nothing(settings: SwitchSettings) -> None:
    assert find_offer(settings, LISTS) is None


# A room or zone of exactly those lights.


def test_the_room_that_holds_exactly_the_lights_is_offered() -> None:
    assert find_offer(switch("Ceiling", "Floor lamp"), LISTS) == UseGroup(LIVING_ROOM, (CEILING, FLOOR_LAMP))


def test_the_zone_that_holds_exactly_the_lights_is_offered() -> None:
    offer = find_offer(switch("Hallway", "Ceiling", "l2"), LISTS)

    assert offer == UseGroup(DOWNSTAIRS, (HALLWAY, CEILING, FLOOR_LAMP))


def test_rooms_come_before_zones_and_each_kind_by_name() -> None:
    zone = group("zone", "z9", "A zone", "l1", "l2")
    zeta = group("room", "r8", "Zeta", "l1", "l2")
    alpha = group("room", "r9", "alpha", "l1", "l2")

    offer = find_offer(switch("Ceiling", "Floor lamp"), BridgeLists(LIGHTS, [zone, zeta, alpha]))

    assert offer == UseGroup(alpha, (CEILING, FLOOR_LAMP))


@pytest.mark.parametrize("entry", ["room:Living room", " living ROOM ", "r1"])
def test_nothing_is_offered_when_the_switch_has_that_room_already(entry: str) -> None:
    assert find_offer(switch("Ceiling", "Floor lamp", groups=(entry,)), LISTS) is None


def test_nothing_is_offered_when_the_switch_has_another_room_or_zone_of_exactly_those_lights() -> None:
    lounge = group("zone", "z9", "Lounge", "l1", "l2")

    offer = find_offer(
        switch("Ceiling", "Floor lamp", groups=("zone:Lounge",)), BridgeLists(LIGHTS, [LIVING_ROOM, lounge])
    )

    assert offer is None


def test_the_switchs_other_rooms_and_zones_dont_matter() -> None:
    offer = find_offer(switch("Ceiling", "Floor lamp", groups=("zone:Downstairs", "Study")), LISTS)

    assert offer == UseGroup(LIVING_ROOM, (CEILING, FLOOR_LAMP))


# A zone to create.


def test_a_zone_is_offered_when_no_room_or_zone_holds_exactly_the_lights() -> None:
    """The living room holds fewer of them, and the zone downstairs more."""
    assert find_offer(switch("Ceiling", "Hallway"), LISTS) == CreateZone("Hall", (CEILING, HALLWAY))
    assert find_offer(switch("Ceiling", "Floor lamp", "Desk"), LISTS) == CreateZone("Hall", (CEILING, FLOOR_LAMP, DESK))


def test_a_zone_is_named_after_the_switch_or_its_device_id() -> None:
    assert find_offer(switch("Ceiling", "Hallway", name=None), LISTS) == CreateZone(DEVICE, (CEILING, HALLWAY))


@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("Hall", "Hall"),
        (" Hall ", "Hall"),
        ("Living room ceiling and floor lamps", "Living room ceiling and floor la"),
        ("x" * 31 + " y", "x" * 31),
        ("1000abcdef0123456789abcdef0123456789abcd", "1000abcdef0123456789abcdef012345"),
    ],
)
def test_a_zones_name_is_cut_to_the_bridges_32_characters(label: str, name: str) -> None:
    assert zone_name(label, []) == name


@pytest.mark.parametrize(
    ("taken", "name"),
    [
        (["Hall"], "Hall 2"),
        ([" HALL "], "Hall 2"),
        (["Hall", "hall 2"], "Hall 3"),
        (["Hall 2"], "Hall"),
        (["Hall", *(f"Hall {number}" for number in range(2, 10))], "Hall 10"),
    ],
)
def test_a_zone_gets_a_number_when_a_room_or_zone_has_its_name(taken: list[str], name: str) -> None:
    groups = [group("room" if index % 2 else "zone", f"g{index}", text) for index, text in enumerate(taken)]

    assert zone_name("Hall", groups) == name


def test_a_number_keeps_the_name_within_32_characters() -> None:
    label = "C" * 32
    groups = [group("zone", "z1", label), group("room", "r1", "C" * 30 + " 2"), group("zone", "z3", "C" * 30 + " 3")]

    assert zone_name(label, groups) == "C" * 30 + " 4"
    groups += [group("zone", f"z{number}", "C" * 30 + f" {number}") for number in range(4, 10)]
    assert zone_name(label, groups) == "C" * 29 + " 10"


def test_a_zone_gets_a_number_when_its_name_is_the_id_of_a_room_or_zone() -> None:
    assert zone_name("hall", [group("zone", "Hall", "Upstairs")]) == "hall 2"


# The room or zone in place of the lights.


def test_the_room_takes_the_place_of_exactly_its_lights() -> None:
    body = SwitchInput(" Hall ", DEVICE, None, [" ceiling ", "l2", "Hallway"], ["zone:Downstairs"])

    assert with_group(body, LIVING_ROOM, LISTS) == SwitchInput(
        " Hall ", DEVICE, None, ["Hallway"], ["zone:Downstairs", "room:Living room"]
    )


def test_a_room_whose_name_another_room_has_too_is_saved_by_its_id() -> None:
    twin = group("room", "r9", " living room ", "l3")
    body = SwitchInput("Hall", DEVICE, "", ["Ceiling", "Floor lamp"])

    assert with_group(body, LIVING_ROOM, BridgeLists(LIGHTS, [LIVING_ROOM, twin])) == SwitchInput(
        "Hall", DEVICE, "", [], ["r1"]
    )


def test_a_room_that_an_entry_names_already_isnt_added_again() -> None:
    body = SwitchInput("Hall", DEVICE, None, ["Ceiling", "Floor lamp"], ["living room"])

    assert with_group(body, LIVING_ROOM, LISTS) == SwitchInput("Hall", DEVICE, None, [], ["living room"])


def test_a_new_zone_takes_the_place_of_its_lights_though_the_lists_dont_have_it() -> None:
    zone = group("zone", "z9", "Hall", "l1", "l3")
    body = SwitchInput("Hall", DEVICE, "new-key", ["Ceiling", "Hallway", "Desk"], ["room:Study"])

    assert with_group(body, zone, LISTS) == SwitchInput(
        "Hall", DEVICE, "new-key", ["Desk"], ["room:Study", "zone:Hall"]
    )
