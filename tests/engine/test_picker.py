"""The switch form's picker of Hue lights, rooms and zones (engine/picker.py): resolving a switch's entries, encoding
the resources chosen, and the entries it saves."""

from dataclasses import dataclass

import pytest

from custom_components.sonoff2hue.engine import picker
from custom_components.sonoff2hue.engine.picker import Resolution, Resources, Selection


@dataclass(frozen=True, slots=True)
class Item:
    """A light, room or zone; equal items are still different resources."""

    id: str
    name: str


HALLWAY = Item("l1", "Hallway")
DESK = Item("l2", "Desk")
KITCHEN_ROOM = Item("r1", "Kitchen")
STUDY = Item("r2", "Study")
KITCHEN_ZONE = Item("z1", "Kitchen")
UPSTAIRS = Item("z2", "Upstairs")
RESOURCES = Resources([HALLWAY, DESK], [KITCHEN_ROOM, STUDY], [KITCHEN_ZONE, UPSTAIRS])


def _config(selection: Selection) -> tuple[list[str], list[str]]:
    targets = picker.selection_to_config(selection, RESOURCES)
    return targets.lights, targets.groups


# Resolving entries


@pytest.mark.parametrize(
    ("entry", "item"), [("Hallway", HALLWAY), (" HALLWAY\u3000", HALLWAY), ("l2", DESK), ("\tL2 ", DESK)]
)
def test_a_light_resolves_by_name_or_id_without_regard_to_case(entry: str, item: Item) -> None:
    resolved = picker.resolve_light(entry, [HALLWAY, DESK])

    assert resolved.status == "ok"
    assert resolved.item is item


@pytest.mark.parametrize("entry", ["Garage", "Hall", "", " "])
def test_an_entry_that_names_no_light_is_missing(entry: str) -> None:
    assert picker.resolve_light(entry, [HALLWAY, DESK, Item("l3", "")]) == Resolution("missing", count=0)


def test_names_are_casefolded() -> None:
    strasse = Item("a", "Straße")

    assert picker.resolve_light("STRASSE", [strasse]).item is strasse


def test_a_name_that_two_lights_have_is_ambiguous() -> None:
    lights = [Item("a", "Lamp"), Item("b", " lamp "), DESK]

    assert picker.resolve_light("LAMP", lights) == Resolution("ambiguous", count=2)
    assert picker.resolve_light("b", lights).item is lights[1]


def test_a_name_that_is_another_lights_id_is_ambiguous() -> None:
    lights = [Item("a1", "b2"), Item("b2", "Porch")]

    assert picker.resolve_light("b2", lights) == Resolution("ambiguous", count=2)
    assert picker.encode_light(lights[0], lights) == "b2"


def test_a_light_with_an_empty_name_resolves_by_its_id() -> None:
    nameless = Item("l9", "")

    assert picker.resolve_light("L9", [nameless]).item is nameless


def test_a_room_and_a_zone_that_share_a_name_need_a_prefix() -> None:
    def resolve(entry: str) -> Resolution:
        return picker.resolve_group(entry, RESOURCES.rooms, RESOURCES.zones)

    assert resolve("Kitchen") == Resolution("ambiguous", count=2)
    room, zone = resolve(" room: kitchen"), resolve("ZONE:Kitchen")
    assert (room.status, room.kind, zone.status, zone.kind) == ("ok", "room", "ok", "zone")
    assert room.item is KITCHEN_ROOM
    assert zone.item is KITCHEN_ZONE


def test_a_group_resolves_by_name_or_id_within_its_prefix() -> None:
    def resolve(entry: str) -> Resolution:
        return picker.resolve_group(entry, RESOURCES.rooms, RESOURCES.zones)

    assert (resolve("study").item, resolve("study").kind) == (STUDY, "room")
    assert (resolve("Z2").item, resolve("Z2").kind) == (UPSTAIRS, "zone")
    assert resolve("room:z2").item is None
    assert resolve("zone:Study") == Resolution("missing", count=0)
    assert resolve("room:") == Resolution("missing", count=0)
    assert resolve("") == Resolution("missing", count=0)


# Encoding the resources chosen


def test_a_light_is_saved_by_its_name_unless_another_light_has_it() -> None:
    lamp, other_lamp, desk = Item("a", "Lamp"), Item("b", " lamp\u3000"), Item("c", "  Desk\u2028")
    lights = [lamp, other_lamp, desk]

    assert picker.encode_light(lamp, lights) == "a"
    assert picker.encode_light(other_lamp, lights) == "b"
    assert picker.encode_light(desk, lights) == "Desk"


@pytest.mark.parametrize("item", [Item("a", ""), Item("b", " \u2028")])
def test_a_light_without_a_usable_name_is_saved_by_its_id(item: Item) -> None:
    assert picker.encode_light(item, [item]) == item.id


def test_a_group_is_saved_by_its_name_if_it_is_unique_among_its_kind() -> None:
    assert picker.encode_group("room", KITCHEN_ROOM, RESOURCES.rooms) == "room:Kitchen"
    assert picker.encode_group("zone", KITCHEN_ZONE, RESOURCES.zones) == "zone:Kitchen"
    assert picker.encode_group("room", Item("r3", " Porch "), [Item("r3", " Porch ")]) == "room:Porch"
    twins = [Item("r1", "Office"), Item("r2", "OFFICE")]
    assert [picker.encode_group("room", twin, twins) for twin in twins] == ["r1", "r2"]
    assert picker.encode_group("zone", Item("z9", ""), [Item("z9", "")]) == "z9"


# The selection


def test_create_selection_resolves_the_current_entries() -> None:
    selection = picker.create_selection(RESOURCES, ["hallway", "Garage"], ["room:kitchen", "Kitchen", "z2"])

    assert selection.light == {"l1"}
    assert selection.room == {"r1"}
    assert selection.zone == {"z2"}
    assert [(e.list_name, e.value, e.status, e.kind, e.id, e.count) for e in selection.entries] == [
        ("lights", "hallway", "ok", "light", "l1", None),
        ("lights", "Garage", "missing", None, None, 0),
        ("groups", "room:kitchen", "ok", "room", "r1", None),
        ("groups", "Kitchen", "ambiguous", None, None, 2),
        ("groups", "z2", "ok", "zone", "z2", None),
    ]


def test_create_selection_without_entries() -> None:
    assert picker.create_selection(RESOURCES).entries == []
    assert picker.create_selection(RESOURCES, [], []).light == set()


def test_entries_keep_their_spelling_and_new_choices_are_encoded() -> None:
    selection = picker.create_selection(RESOURCES, [" hallway "], ["z2"])
    selection.light.add("l2")
    selection.room.add("r2")
    selection.zone.add("z1")

    assert _config(selection) == (["hallway", "Desk"], ["z2", "room:Study", "zone:Kitchen"])


def test_a_resource_is_saved_once() -> None:
    selection = picker.create_selection(RESOURCES, ["Hallway", "l1", "HALLWAY"], ["room:Kitchen", "r1"])

    assert _config(selection) == (["Hallway"], ["room:Kitchen"])


def test_deselected_entries_are_dropped() -> None:
    selection = picker.create_selection(RESOURCES, ["Hallway", "Desk"], ["z2"])
    selection.light.discard("l1")
    selection.zone.clear()

    assert _config(selection) == (["Desk"], [])


def test_unresolved_entries_are_kept_unless_removed() -> None:
    selection = picker.create_selection(RESOURCES, ["Garage", "Hallway"], [" Kitchen "])
    unresolved = picker.unresolved_entries(selection)

    assert [entry.value for entry in unresolved] == ["Garage", " Kitchen "]
    assert unresolved[0] is selection.entries[0]
    assert _config(selection) == (["Garage", "Hallway"], ["Kitchen"])

    unresolved[0].removed = True

    assert picker.unresolved_entries(selection) == [selection.entries[2]]
    assert _config(selection) == (["Hallway"], ["Kitchen"])
    selection.entries[2].removed = True
    assert _config(selection) == (["Hallway"], [])


def test_an_entry_that_resolves_again_once_chosen_keeps_its_spelling() -> None:
    selection = picker.create_selection(RESOURCES, ["l2"], ["upstairs"])
    selection.light.clear()
    selection.zone.clear()
    assert _config(selection) == ([], [])

    selection.light.add("l2")
    selection.zone.add("z2")

    assert _config(selection) == (["l2"], ["upstairs"])


def test_new_choices_follow_the_order_of_the_resources() -> None:
    selection = picker.create_selection(RESOURCES)
    selection.zone.update({"z2", "z1"})
    selection.light.update({"l2", "l1"})
    selection.room.add("r2")

    assert _config(selection) == (["Hallway", "Desk"], ["room:Study", "zone:Kitchen", "zone:Upstairs"])


# Typed entries


@pytest.mark.parametrize(
    ("text", "values"),
    [
        (" Hallway ,Desk lamp,, ", ["Hallway", "Desk lamp"]),
        ("\u3000Hallway\u2028, room:Kitchen", ["Hallway", "room:Kitchen"]),
        ("a\x85,\x1cb", ["a", "b"]),
        ("\ufeff", ["\ufeff"]),
        (",, ,", []),
        ("", []),
    ],
)
def test_split_list(text: str, values: list[str]) -> None:
    assert picker.split_list(text) == values
