"""Tests of the switch editor's picker (engine/picker.py), the port of the .NET app's web UI module targets.js."""

from dataclasses import dataclass

import pytest

from custom_components.sonoff2hue.engine import picker
from custom_components.sonoff2hue.engine.picker import Resources, Selection


@dataclass(frozen=True, slots=True)
class Item:
    """A light, room or zone; equal items are still different objects, as in JavaScript."""

    id: str | None
    name: str | None


HALLWAY = Item("l1", "Hallway")
DESK = Item("l2", "Desk")
KITCHEN_ROOM = Item("r1", "Kitchen")
STUDY = Item("r2", "Study")
KITCHEN_ZONE = Item("z1", "Kitchen")
UPSTAIRS = Item("z2", "Upstairs")
RESOURCES = Resources([HALLWAY, DESK], [KITCHEN_ROOM, STUDY], [KITCHEN_ZONE, UPSTAIRS])


def _config(selection: Selection, resources: Resources = RESOURCES) -> tuple[list[str | None], list[str | None]]:
    targets = picker.selection_to_config(selection, resources)
    return targets.lights, targets.groups


# splitKind and resolving entries


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("room:Kitchen", ("room", "Kitchen")),
        ("ROOM:Kitchen", ("room", "Kitchen")),
        ("Zone: Upstairs\u3000", ("zone", "Upstairs")),
        ("zone:", ("zone", "")),
        ("\ufeffroom:x", ("room", "x")),
        ("\x85room:x", (None, "\x85room:x")),
        ("room :x", (None, "room :x")),
        ("light:x", (None, "light:x")),
        ("Kitchen", (None, "Kitchen")),
        ("", (None, "")),
        (None, (None, "")),
    ],
)
def test_split_kind_takes_room_and_zone_prefixes_in_any_case(
    entry: str | None, expected: tuple[str | None, str]
) -> None:
    assert picker.split_kind(entry) == expected


def test_resolve_light_by_name_or_id_without_regard_to_case() -> None:
    assert picker.resolve_light(" HALLWAY\u3000", [HALLWAY, DESK]).item is HALLWAY
    assert picker.resolve_light("L2", [HALLWAY, DESK]).item is DESK
    assert picker.resolve_light("Garage", [HALLWAY, DESK]) == picker.Resolution("missing", count=0)
    assert picker.resolve_light("", [HALLWAY, Item("l3", "")]) == picker.Resolution("missing", count=0)
    assert picker.resolve_light(None, [HALLWAY]) == picker.Resolution("missing", count=0)


def test_resolve_light_with_javascripts_case_mappings() -> None:
    iris, strasse, odos = Item("a", "\u0130ris"), Item("b", "Stra\u00dfe"), Item("c", "\u039f\u0394\u039f\u03a3")
    lights = [iris, strasse, odos]

    # 'İ' lowers to 'i' and a combining dot above; 'ẞ' to 'ß'; a final 'Σ' to 'ς'.
    assert picker.resolve_light("i\u0307ris", lights).item is iris
    assert picker.resolve_light("iris", lights).status == "missing"
    assert picker.resolve_light("STRA\u1e9eE", lights).item is strasse
    assert picker.resolve_light("strasse", lights).status == "missing"
    assert picker.resolve_light("\u03bf\u03b4\u03bf\u03c2", lights).item is odos
    assert picker.resolve_light("\u03bf\u03b4\u03bf\u03c3", lights).status == "missing"


def test_resolve_light_trims_as_javascript_does() -> None:
    porch, nel = Item("a", "\ufeffPorch"), Item("b", "Desk\x85")

    assert picker.resolve_light("porch", [porch, nel]).item is porch
    assert picker.resolve_light("desk", [porch, nel]).status == "missing"
    assert picker.resolve_light("desk\x85", [porch, nel]).item is nel


def test_a_name_that_is_another_lights_id_is_ambiguous() -> None:
    lights = [Item("a1", "b2"), Item("b2", "Porch")]

    assert picker.resolve_light("b2", lights) == picker.Resolution("ambiguous", count=2)
    assert picker.encode_light(lights[0], lights) == "b2"


def test_resolve_group_with_room_and_zone_name_clashes() -> None:
    assert picker.resolve_group("Kitchen", RESOURCES.rooms, RESOURCES.zones) == picker.Resolution("ambiguous", count=2)
    room = picker.resolve_group("room:kitchen", RESOURCES.rooms, RESOURCES.zones)
    zone = picker.resolve_group("ZONE:Kitchen", RESOURCES.rooms, RESOURCES.zones)
    assert (room.status, room.kind, room.item) == ("ok", "room", KITCHEN_ROOM)
    assert room.item is KITCHEN_ROOM
    assert (zone.status, zone.kind) == ("ok", "zone")
    assert zone.item is KITCHEN_ZONE
    assert picker.resolve_group("zone:Study", RESOURCES.rooms, RESOURCES.zones).status == "missing"
    assert picker.resolve_group("z2", RESOURCES.rooms, RESOURCES.zones).item is UPSTAIRS
    assert picker.resolve_group("room:", RESOURCES.rooms, RESOURCES.zones).status == "missing"


# Encoding


def test_encode_light_uses_the_name_unless_another_light_has_it() -> None:
    lamp, other_lamp, desk = Item("a", "Lamp"), Item("b", " lamp\u3000"), Item("c", "  Desk\ufeff")
    lights = [lamp, other_lamp, desk]

    assert picker.encode_light(lamp, lights) == "a"
    assert picker.encode_light(other_lamp, lights) == "b"
    assert picker.encode_light(desk, lights) == "Desk"
    assert picker.encode_light(Item("d", "Desk\x85"), [Item("d", "Desk\x85")]) == "Desk\x85"


def test_encode_light_without_a_usable_name_uses_the_id() -> None:
    for item in (Item("a", ""), Item("b", " \u2028"), Item("c", None), Item(None, None)):
        assert picker.encode_light(item, [item]) == item.id


def test_encode_group_needs_a_unique_name_among_its_kind_only() -> None:
    assert picker.encode_group("room", KITCHEN_ROOM, RESOURCES.rooms) == "room:Kitchen"
    assert picker.encode_group("zone", KITCHEN_ZONE, RESOURCES.zones) == "zone:Kitchen"
    twins = [Item("r1", "Office"), Item("r2", "OFFICE")]
    assert [picker.encode_group("room", twin, twins) for twin in twins] == ["r1", "r2"]


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
    assert picker.create_selection(RESOURCES, [], None).light == set()


def test_entries_keep_their_spelling_and_new_selections_are_encoded() -> None:
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
    selection = picker.create_selection(RESOURCES, ["Garage", "Hallway"], ["Kitchen"])
    unresolved = picker.unresolved_entries(selection)

    assert [entry.value for entry in unresolved] == ["Garage", "Kitchen"]
    assert unresolved[0] is selection.entries[0]
    assert _config(selection) == (["Garage", "Hallway"], ["Kitchen"])

    unresolved[0].removed = True

    assert picker.unresolved_entries(selection) == [selection.entries[2]]
    assert _config(selection) == (["Hallway"], ["Kitchen"])


def test_a_null_id_is_saved_as_none_and_collides_with_the_id_null() -> None:
    nameless, null_id = Item(None, None), Item("null", "Null id")
    resources = Resources([nameless, null_id], [], [])
    selection = picker.create_selection(resources)
    selection.light.update({None, "null"})

    # The key `${kind}:${id}` is "light:null" for both, so only the first is saved.
    assert _config(selection, resources) == ([None], [])


def test_split_list() -> None:
    assert picker.split_list(" Hallway ,Desk lamp,, \ufeff") == ["Hallway", "Desk lamp"]
    assert picker.split_list("a\x85, b") == ["a\x85", "b"]
    assert picker.split_list(None) == []
    assert picker.split_list("") == []
