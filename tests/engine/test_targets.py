"""Resolving a switch's lights, rooms and zones against the bridge's resources (engine/targets.py): by name or id,
with an optional ``room:``/``zone:`` prefix, without regard to case or to white space around them."""

import pytest

from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueGroup, HueLight, HueTarget, HueTargetKind
from custom_components.sonoff2hue.engine.targets import (
    EntryKey,
    Field,
    Remembered,
    RememberedResource,
    Renamed,
    SwitchResolution,
    refers_to,
    resolve,
    resolve_switch,
    split_kind,
)

DEVICE = "1000abcdef"
HALL = "Hall (1000abcdef)"

CEILING = HueLight("l1", "Ceiling", True)
FLOOR_LAMP = HueLight("l2", "Floor lamp", False)
DESK = HueLight("l3", "Desk", False)
LIGHTS = [CEILING, FLOOR_LAMP, DESK]

LIVING_ROOM = HueGroup("r1", "Living room", "room", "g1")
KITCHEN_ROOM = HueGroup("r2", "Kitchen", "room", "g2")
ATTIC = HueGroup("r3", "Attic", "room", None)
DOWNSTAIRS = HueGroup("z1", "Downstairs", "zone", "g3")
KITCHEN_ZONE = HueGroup("z2", "Kitchen", "zone", "g4")
GROUPS = [LIVING_ROOM, KITCHEN_ROOM, ATTIC, DOWNSTAIRS, KITCHEN_ZONE]
KNOWN_GROUPS = "'room:Attic', 'room:Kitchen', 'room:Living room', 'zone:Downstairs', 'zone:Kitchen'"


def light(resource: HueLight) -> HueTarget:
    return HueTarget(HueTargetKind.LIGHT, resource.id, resource.name)


def grouped(resource: HueGroup) -> HueTarget:
    return HueTarget(HueTargetKind.GROUPED_LIGHT, resource.grouped_light_id, resource.name)


def run(
    lights: list[str] | None = None,
    groups: list[str] | None = None,
    *,
    name: str | None = "Hall",
    bridge_lights: list[HueLight] = LIGHTS,
    bridge_groups: list[HueGroup] = GROUPS,
) -> tuple[list[HueTarget], list[str]]:
    errors: list[str] = []
    settings = SwitchSettings(DEVICE, name, lights=lights or [], groups=groups or [])
    return resolve(settings, bridge_lights, bridge_groups, errors), errors


# Lights


def test_lights_resolve_by_name_or_id_without_regard_to_case_or_surrounding_white_space() -> None:
    assert run([" ceiling ", "L2", "DESK\u3000"]) == ([light(CEILING), light(FLOOR_LAMP), light(DESK)], [])


def test_names_compare_casefolded() -> None:
    strasse, odos = HueLight("a", "Straße", False), HueLight("b", "ΟΔΟΣ", False)

    assert run(["STRASSE", "οδος"], bridge_lights=[strasse, odos]) == ([light(strasse), light(odos)], [])


def test_an_unknown_light_lists_the_known_names_in_order_without_regard_to_case() -> None:
    lights = [
        HueLight("a", "desk", False),
        HueLight("b", "ceiling", False),
        HueLight("c", "_Spare", False),
        HueLight("d", "Ceiling", False),
        HueLight("e", "desk", False),
        HueLight("f", "Arc lamp", False),
    ]

    assert run([" Garage "], bridge_lights=lights) == (
        [],
        [f"{HALL}: no Hue light named 'Garage'. Known lights: '_Spare', 'Arc lamp', 'ceiling', 'Ceiling', 'desk'"],
    )


def test_a_name_that_two_lights_have_needs_the_light_id() -> None:
    lights = [HueLight("a2", "lamp", False), HueLight("a1", "Lamp", False), DESK]

    targets, errors = run(["LAMP", "a1"], bridge_lights=lights)

    assert targets == [HueTarget(HueTargetKind.LIGHT, "a1", "Lamp", "a1")]
    assert errors == [f"{HALL}: 2 lights are named 'LAMP'; use the light id instead ('a1', 'a2')."]


def test_a_name_that_is_another_lights_id_is_ambiguous() -> None:
    lights = [HueLight("a1", "b2", False), HueLight("b2", "Porch", False)]

    assert run(["B2"], bridge_lights=lights)[1] == [
        f"{HALL}: 2 lights are named 'B2'; use the light id instead ('a1', 'b2')."
    ]


def test_without_lights_on_the_bridge_none_are_known() -> None:
    assert run(["Ceiling"], name=None, bridge_lights=[])[1] == [
        f"{DEVICE}: no Hue light named 'Ceiling'. Known lights: (none)"
    ]


def test_a_light_with_an_empty_name_resolves_by_its_id() -> None:
    nameless = HueLight("l9", "", False)

    assert run(["L9"], bridge_lights=[nameless]) == ([light(nameless)], [])
    assert run(["Porch"], bridge_lights=[nameless, CEILING])[1] == [
        f"{HALL}: no Hue light named 'Porch'. Known lights: '', 'Ceiling'"
    ]


# Rooms and zones


def test_groups_resolve_by_name_id_or_prefixed_name() -> None:
    targets, errors = run(groups=["living ROOM", "Z1", "ROOM: kitchen", "zone:Kitchen "])

    assert targets == [grouped(LIVING_ROOM), grouped(DOWNSTAIRS), grouped(KITCHEN_ROOM), grouped(KITCHEN_ZONE)]
    assert errors == []


def test_a_name_that_a_room_and_a_zone_share_needs_a_prefix() -> None:
    assert run(groups=[" kitchen "]) == (
        [],
        [f"{HALL}: 'kitchen' matches 'room:Kitchen', 'zone:Kitchen'; prefix it with 'room:' or 'zone:'."],
    )


@pytest.mark.parametrize("entry", ["Garden", "zone:Living room", "room:Downstairs", "room:"])
def test_an_unknown_room_or_zone_lists_the_known_ones(entry: str) -> None:
    assert run(groups=[f" {entry} "]) == ([], [f"{HALL}: no Hue room or zone named '{entry}'. Known: {KNOWN_GROUPS}"])


def test_without_rooms_and_zones_on_the_bridge_none_are_known() -> None:
    assert run(groups=["Garden"], bridge_groups=[])[1] == [f"{HALL}: no Hue room or zone named 'Garden'. Known: (none)"]


def test_a_room_without_lights_cannot_be_switched() -> None:
    assert run(groups=["room:attic"]) == ([], [f"{HALL}: room 'Attic' has no lights."])


def test_a_room_with_an_empty_name_resolves_by_its_id() -> None:
    nameless = HueGroup("r9", "", "room", "g9")

    assert run(groups=["R9"], bridge_groups=[nameless]) == ([HueTarget(HueTargetKind.GROUPED_LIGHT, "g9", "")], [])
    assert run(groups=["Garden"], bridge_groups=[nameless])[1] == [
        f"{HALL}: no Hue room or zone named 'Garden'. Known: 'room:'"
    ]


# The whole switch


def test_each_target_is_resolved_once_in_the_order_of_its_first_entry() -> None:
    targets, errors = run(["Ceiling", "l2", "l1", "CEILING"], ["Living room", "room:living room", "r1", "z1"])

    assert targets == [light(CEILING), light(FLOOR_LAMP), grouped(LIVING_ROOM), grouped(DOWNSTAIRS)]
    assert errors == []


def test_a_light_and_a_grouped_light_with_the_same_id_are_both_targets() -> None:
    twin = HueLight("g1", "Twin", False)

    assert run(["Twin"], ["Living room"], bridge_lights=[twin])[0] == [light(twin), grouped(LIVING_ROOM)]


def test_every_entry_that_does_not_resolve_is_reported() -> None:
    targets, errors = run(["Ceiling", "Garage"], ["Kitchen", "Garden", "Downstairs"])

    assert targets == [light(CEILING), grouped(DOWNSTAIRS)]
    assert errors == [
        f"{HALL}: no Hue light named 'Garage'. Known lights: 'Ceiling', 'Desk', 'Floor lamp'",
        f"{HALL}: 'Kitchen' matches 'room:Kitchen', 'zone:Kitchen'; prefix it with 'room:' or 'zone:'.",
        f"{HALL}: no Hue room or zone named 'Garden'. Known: {KNOWN_GROUPS}",
    ]


def test_empty_entries_match_nothing() -> None:
    """``validate_for_run`` refuses them first; here they match nothing, not even a light with an empty name."""
    nameless = HueLight("l9", "", False)

    targets, errors = run(["", " "], ["\u3000"], bridge_lights=[nameless])

    assert targets == []
    assert errors == [
        f"{HALL}: no Hue light named ''. Known lights: ''",
        f"{HALL}: no Hue light named ''. Known lights: ''",
        f"{HALL}: no Hue room or zone named ''. Known: {KNOWN_GROUPS}",
    ]


# Labels


def test_a_target_whose_name_another_light_has_is_labelled_with_its_id() -> None:
    """As the switch dialog lists them: names compare as entries do, and only a repeated name gets the id."""
    lamps = [HueLight("a1", "Lamp", False), HueLight("a2", " LAMP ", False), CEILING]

    targets = run(["a1", "a2", "Ceiling"], bridge_lights=lamps)[0]

    assert targets == [
        HueTarget(HueTargetKind.LIGHT, "a1", "Lamp", "a1"),
        HueTarget(HueTargetKind.LIGHT, "a2", " LAMP ", "a2"),
        light(CEILING),
    ]
    assert [str(target) for target in targets] == ["light 'Lamp' (a1)", "light ' LAMP ' (a2)", "light 'Ceiling'"]


def test_a_room_or_zone_is_labelled_with_its_own_id_only_when_one_of_its_kind_has_its_name() -> None:
    """The grouped light switches it, but the label shows the room's or zone's id, which the switch dialog shows. A
    light, a room and a zone of one name are of three kinds, so none of them gets an id."""
    study, other_study = HueGroup("r4", "Study", "room", "g5"), HueGroup("r5", "study", "room", "g6")
    groups = [*GROUPS, study, other_study]
    kitchen_light = HueLight("l7", "Kitchen", False)

    targets = run(
        ["Kitchen"], ["r4", "room:kitchen", "zone:Kitchen"], bridge_lights=[kitchen_light], bridge_groups=groups
    )[0]

    assert targets == [
        light(kitchen_light),
        HueTarget(HueTargetKind.GROUPED_LIGHT, "g5", "Study", "r4"),
        grouped(KITCHEN_ROOM),
        grouped(KITCHEN_ZONE),
    ]
    assert [str(target) for target in targets] == [
        "light 'Kitchen'",
        "group 'Study' (r4)",
        "group 'Kitchen'",
        "group 'Kitchen'",
    ]


# Renames, with the remembered resources (decision 32)


def key(field: Field, entry: str) -> EntryKey:
    return EntryKey(DEVICE, field, entry.strip().casefold())


def follow(
    lights: list[str] | None = None,
    groups: list[str] | None = None,
    *,
    remembered: Remembered,
    bridge_lights: list[HueLight] = LIGHTS,
    bridge_groups: list[HueGroup] = GROUPS,
) -> SwitchResolution:
    settings = SwitchSettings(DEVICE, "Hall", lights=lights or [], groups=groups or [])
    return resolve_switch(settings, bridge_lights, bridge_groups, remembered)


def test_an_entry_that_matches_no_light_resolves_to_its_remembered_light_which_was_renamed() -> None:
    renamed = HueLight("l1", "Ceiling lamp", True)
    memory = RememberedResource("light", "l1", "Ceiling")

    resolution = follow(["Ceiling"], remembered={key("lights", "Ceiling"): memory}, bridge_lights=[renamed, DESK])

    now = RememberedResource("light", "l1", "Ceiling lamp")
    assert resolution == SwitchResolution(
        [light(renamed)],
        [],
        {key("lights", "Ceiling"): now},
        [Renamed(key("lights", "Ceiling"), "Ceiling", "Ceiling", now, "Ceiling lamp")],
    )


def test_an_entry_that_matches_several_lights_resolves_to_its_remembered_one() -> None:
    """A rename only when the entry no longer names it."""
    twin = HueLight("l9", "Ceiling", False)
    memory = {key("lights", "ceiling"): RememberedResource("light", "l1", "Ceiling")}

    same = follow(["ceiling"], remembered=memory, bridge_lights=[CEILING, twin])
    renamed = follow(
        ["ceiling"],
        remembered=memory,
        bridge_lights=[HueLight("l1", "Porch", False), twin, HueLight("l8", "Ceiling", False)],
    )

    assert (same.targets, same.problems, same.renamed) == (
        [HueTarget(HueTargetKind.LIGHT, "l1", "Ceiling", "l1")],
        [],
        [],
    )
    assert renamed.targets == [HueTarget(HueTargetKind.LIGHT, "l1", "Porch")]
    assert [(rename.old_name, rename.new_entry) for rename in renamed.renamed] == [("Ceiling", "Porch")]


def test_a_renamed_room_and_zone_are_followed_and_spelled_with_their_prefixes() -> None:
    lounge, ground_floor = HueGroup("r1", "Lounge", "room", "g1"), HueGroup("z1", "Ground floor", "zone", "g3")
    memory = {
        key("groups", "Living room"): RememberedResource("room", "r1", "Living room"),
        key("groups", "zone:Downstairs"): RememberedResource("zone", "z1", "Downstairs"),
    }

    resolution = follow(
        groups=["Living room", "zone:Downstairs"],
        remembered=memory,
        bridge_groups=[lounge, KITCHEN_ROOM, ground_floor, KITCHEN_ZONE],
    )

    assert resolution.targets == [grouped(lounge), grouped(ground_floor)]
    assert resolution.problems == []
    assert [(rename.entry, rename.old_name, rename.new_entry) for rename in resolution.renamed] == [
        ("Living room", "Living room", "room:Lounge"),
        ("zone:Downstairs", "Downstairs", "zone:Ground floor"),
    ]
    assert resolution.found == {
        key("groups", "Living room"): RememberedResource("room", "r1", "Lounge"),
        key("groups", "zone:Downstairs"): RememberedResource("zone", "z1", "Ground floor"),
    }


def test_a_rename_to_a_name_that_another_resource_of_its_kind_has_is_spelled_as_its_id() -> None:
    """As the picker saves it; a zone of the same name doesn't count for a room."""
    lamp = HueLight("l1", "Desk", True)
    study = HueGroup("r1", "Kitchen", "room", "g1")
    memory = {
        key("lights", "Ceiling"): RememberedResource("light", "l1", "Ceiling"),
        key("groups", "room:Living room"): RememberedResource("room", "r1", "Living room"),
        key("groups", "Downstairs"): RememberedResource("zone", "z1", "Downstairs"),
    }
    zones = [HueGroup("z1", "Hall", "zone", "g3"), HueGroup("r9", "Hall", "room", "g9")]

    resolution = follow(
        ["Ceiling"],
        ["room:Living room", "Downstairs"],
        remembered=memory,
        bridge_lights=[lamp, DESK],
        bridge_groups=[study, KITCHEN_ROOM, *zones],
    )

    assert [rename.new_entry for rename in resolution.renamed] == ["l1", "r1", "zone:Hall"]
    assert [str(target) for target in resolution.targets] == [
        "light 'Desk' (l1)",
        "group 'Kitchen' (r1)",
        "group 'Hall'",
    ]


def test_an_entry_whose_remembered_resource_is_gone_is_missing() -> None:
    """Deleted on the bridge, or of another kind than the entry: the resolver's message says so, and nothing is found
    for it, while an entry whose remembered light is there resolves to it."""
    reading_lamp = HueLight("l2", "Reading lamp", False)
    memory = {
        key("lights", "Porch"): RememberedResource("light", "l7", "Porch"),
        key("lights", "Floor lamp"): RememberedResource("light", "l2", "Floor lamp"),
        key("groups", "zone:Garden"): RememberedResource("room", "r1", "Garden"),
        key("groups", "Patio"): RememberedResource("light", "l1", "Patio"),
    }

    resolution = follow(
        ["Porch", "Floor lamp"], ["zone:Garden", "Patio"], remembered=memory, bridge_lights=[CEILING, reading_lamp]
    )

    assert resolution.targets == [light(reading_lamp)]
    assert resolution.problems == [
        f"{HALL}: no Hue light named 'Porch'. Known lights: 'Ceiling', 'Reading lamp'",
        f"{HALL}: no Hue room or zone named 'zone:Garden'. Known: {KNOWN_GROUPS}",
        f"{HALL}: no Hue room or zone named 'Patio'. Known: {KNOWN_GROUPS}",
    ]
    assert resolution.found == {key("lights", "Floor lamp"): RememberedResource("light", "l2", "Reading lamp")}


def test_a_remembered_room_left_without_lights_is_found_and_has_no_lights() -> None:
    empty = HueGroup("r1", "Lounge", "room", None)
    memory = {key("groups", "Living room"): RememberedResource("room", "r1", "Living room")}

    resolution = follow(groups=["Living room"], remembered=memory, bridge_groups=[empty])

    assert resolution.targets == []
    assert resolution.problems == [f"{HALL}: room 'Lounge' has no lights."]
    assert resolution.found == {key("groups", "Living room"): RememberedResource("room", "r1", "Lounge")}
    assert [rename.new_entry for rename in resolution.renamed] == ["room:Lounge"]


def test_an_entry_that_names_its_resource_by_its_id_is_never_renamed() -> None:
    """Its id matches, and so does another light's name: the remembered light is still the one it names."""
    porch, other = HueLight("b2", "Porch light", False), HueLight("a1", "b2", False)
    memory = {key("lights", "b2"): RememberedResource("light", "b2", "Porch")}

    resolution = follow(["b2"], remembered=memory, bridge_lights=[porch, other])

    assert (resolution.targets, resolution.renamed) == ([light(porch)], [])
    assert resolution.found == {key("lights", "b2"): RememberedResource("light", "b2", "Porch light")}


# The rules for an entry


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("room:Kitchen", ("room", "Kitchen")),
        ("ROOM:Kitchen", ("room", "Kitchen")),
        ("rOoM:Kitchen", ("room", "Kitchen")),
        (" Zone: Upstairs\u3000", ("zone", "Upstairs")),
        ("zone:", ("zone", "")),
        ("room:zone:x", ("room", "zone:x")),
        ("room :x", (None, "room :x")),
        ("\uff52oom:x", (None, "\uff52oom:x")),
        ("light:x", (None, "light:x")),
        (" Kitchen ", (None, "Kitchen")),
        ("", (None, "")),
    ],
)
def test_split_kind_takes_a_room_or_zone_prefix_in_any_case(entry: str, expected: tuple[str | None, str]) -> None:
    assert split_kind(entry) == expected


@pytest.mark.parametrize(
    ("entry", "resource_id", "name", "expected"),
    [
        ("Ceiling", "l1", "Ceiling", True),
        ("\tceiling ", "l1", " CEILING", True),
        ("L1", "l1", "Ceiling", True),
        ("Desk", "l1", "Ceiling", False),
        ("l1", "l1", "", True),
        ("", "", "", False),
        (" ", "l1", " ", False),
    ],
)
def test_an_entry_refers_to_a_resource_by_its_id_or_its_name(
    entry: str, resource_id: str, name: str, expected: bool
) -> None:
    assert refers_to(entry, resource_id, name) is expected
