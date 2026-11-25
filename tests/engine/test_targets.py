"""Resolving a switch's lights, rooms and zones against the bridge's resources (engine/targets.py): by name or id,
with an optional ``room:``/``zone:`` prefix, without regard to case or to white space around them."""

import pytest

from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueGroup, HueLight, HueTarget, HueTargetKind
from custom_components.sonoff2hue.engine.targets import refers_to, resolve, split_kind

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
    lights: list[str | None] | None = None,
    groups: list[str | None] | None = None,
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

    assert targets == [light(lights[1])]
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


def test_a_light_without_a_name_resolves_by_its_id() -> None:
    nameless = HueLight("l9", None, False)

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


def test_a_room_without_a_name_resolves_by_its_id() -> None:
    nameless = HueGroup("r9", None, "room", "g9")

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


def test_empty_and_null_entries_match_nothing() -> None:
    """``validate_for_run`` refuses them first; here they are empty names, which match nothing, not even a light with an
    empty name."""
    nameless = HueLight("l9", "", False)

    targets, errors = run([None, " "], [None], bridge_lights=[nameless])

    assert targets == []
    assert errors == [
        f"{HALL}: no Hue light named ''. Known lights: ''",
        f"{HALL}: no Hue light named ''. Known lights: ''",
        f"{HALL}: no Hue room or zone named ''. Known: {KNOWN_GROUPS}",
    ]


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
        (None, (None, "")),
    ],
)
def test_split_kind_takes_a_room_or_zone_prefix_in_any_case(
    entry: str | None, expected: tuple[str | None, str]
) -> None:
    assert split_kind(entry) == expected


@pytest.mark.parametrize(
    ("entry", "resource_id", "name", "expected"),
    [
        ("Ceiling", "l1", "Ceiling", True),
        ("\tceiling ", "l1", " CEILING", True),
        ("L1", "l1", "Ceiling", True),
        ("Desk", "l1", "Ceiling", False),
        ("l1", "l1", None, True),
        ("Ceiling", None, "Ceiling", True),
        ("", "", "", False),
        (" ", "l1", " ", False),
        (None, None, None, False),
    ],
)
def test_an_entry_refers_to_a_resource_by_its_id_or_its_name(
    entry: str | None, resource_id: str | None, name: str | None, expected: bool
) -> None:
    assert refers_to(entry, resource_id, name) is expected
