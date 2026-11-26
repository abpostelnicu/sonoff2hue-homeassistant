"""The rules a switch meets before it is saved (engine/switch_rules.py): validating the input, adding and changing a
switch, and checking its lights, rooms and zones against the bridge."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import switch_rules
from custom_components.sonoff2hue.engine.config import ConfigError, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueGroup, HueLight
from custom_components.sonoff2hue.engine.switch_rules import (
    HueResources,
    SwitchConflictError,
    SwitchInput,
    SwitchNotFoundError,
    SwitchValidationError,
)

_DEVICE = "1000abcdef"
_OTHER = "1000999999"
PAIRED = HueSettings(
    bridge_ip="192.0.2.20", bridge_id="001788fffe000001", app_key="test-app-key", certificate_sha256="AA"
)
UNPAIRED = HueSettings()

CEILING = HueLight("l1", "Ceiling", True)
FLOOR_LAMP = HueLight("l2", "Floor lamp", False)
HALLWAY = HueGroup("r1", "Hallway", "room", "g1")


class FakeBridge:
    """The lights and groups a bridge answers with, or the error it fails with."""

    def __init__(
        self,
        lights: Sequence[HueLight] = (CEILING, FLOOR_LAMP),
        groups: Sequence[HueGroup] = (HALLWAY,),
        error: Exception | None = None,
    ) -> None:
        self.lights, self.groups, self.error = list(lights), list(groups), error
        self.opened = 0
        self.closed = 0

    async def get_lights_and_groups(self) -> tuple[list[HueLight], list[HueGroup]]:
        if self.error is not None:
            raise self.error
        return self.lights, self.groups

    def connect(self, hue: HueSettings) -> AbstractAsyncContextManager[HueResources]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[HueResources]:
            self.opened += 1
            try:
                yield self
            finally:
                self.closed += 1

        return opened()


async def create(
    switches: list[SwitchSettings],
    switch: SwitchInput,
    hue: HueSettings = PAIRED,
    bridge: FakeBridge | None = None,
) -> switch_rules.SwitchWrite:
    write = await switch_rules.create_switch(lambda: switches, switch, hue, (bridge or FakeBridge()).connect)
    switches.append(write.switch)
    return write


async def update(
    switches: list[SwitchSettings],
    device_id: str,
    switch: SwitchInput,
    hue: HueSettings = PAIRED,
    bridge: FakeBridge | None = None,
) -> switch_rules.SwitchWrite:
    write = await switch_rules.update_switch(lambda: switches, device_id, switch, hue, (bridge or FakeBridge()).connect)
    assert write.index is not None
    switches[write.index] = write.switch
    return write


# Adding and changing a switch


async def test_create_switch_saves_it_with_its_device_key() -> None:
    switches: list[SwitchSettings] = []

    write = await create(
        switches,
        SwitchInput("Hall", "1000ABCDEF", "secret-device-key", ["Ceiling"], ["room:Hallway"]),
    )

    assert write.warnings == []
    assert switches == [
        SwitchSettings(_DEVICE, "Hall", "secret-device-key", ["Ceiling"], ["room:Hallway"]),
    ]
    assert switch_rules.has_device_key(switches[0])


async def test_create_switch_validates_the_input() -> None:
    switches: list[SwitchSettings] = []

    with pytest.raises(SwitchValidationError) as invalid:
        await create(switches, SwitchInput("x", "not valid!", lights=[]))
    with pytest.raises(SwitchValidationError) as unknown_light:
        await create(switches, SwitchInput(device_id=_DEVICE, lights=["Kitchen"]))

    assert list(invalid.value.errors) == ["deviceId", "targets"]
    assert unknown_light.value.errors == {
        "targets": [f"{_DEVICE}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Floor lamp'"]
    }
    assert switches == []


async def test_create_switch_without_a_paired_bridge_is_saved_with_a_warning() -> None:
    switches: list[SwitchSettings] = []
    bridge = FakeBridge()

    write = await create(switches, SwitchInput(device_id=_DEVICE, lights=["Kitchen"]), UNPAIRED, bridge)

    assert write.warnings == ["The Hue bridge is not paired yet, so the light names were not checked."]
    assert len(switches) == 1
    assert bridge.opened == 0


async def test_update_switch_keeps_or_removes_the_device_key() -> None:
    switches: list[SwitchSettings] = []
    await create(switches, SwitchInput(device_id=_DEVICE, device_key="k1", lights=["Ceiling"]))

    await update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, None, ["Floor lamp"]))
    after_keep = switches[0]
    await update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, "", ["Floor lamp"]))

    assert (after_keep.name, after_keep.device_key, after_keep.lights) == ("Hall", "k1", ["Floor lamp"])
    assert switches[0].device_key is None


async def test_an_unusable_bridge_address_is_a_warning() -> None:
    switches = [SwitchSettings(_DEVICE, lights=["Ceiling"])]
    hue = HueSettings(bridge_ip="bad host", app_key="test-app-key")
    bridge = FakeBridge(error=ConfigError("'bad host' is not a valid Hue bridge address."))

    write = await update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, lights=["Ceiling"]), hue, bridge)

    assert write.warnings == [
        "The Hue settings in the config can't be used, so the light names were not checked ('bad host' is not a valid "
        "Hue bridge address.)."
    ]


# The device id, the key and the second check


async def test_a_device_id_is_configured_once() -> None:
    switches = [SwitchSettings(_DEVICE, lights=["Ceiling"]), SwitchSettings(_OTHER, lights=["Ceiling"])]

    with pytest.raises(SwitchConflictError) as again:
        await create(switches, SwitchInput(device_id=" 1000ABCDEF ", lights=["Ceiling"]))
    with pytest.raises(SwitchConflictError) as taken:
        await update(switches, _DEVICE, SwitchInput(device_id=_OTHER, lights=["Ceiling"]))
    with pytest.raises(SwitchNotFoundError) as missing:
        await update(switches, "1000BBBBBB ", SwitchInput(device_id=_DEVICE, lights=["Ceiling"]))

    assert str(again.value) == "Device 1000abcdef is already configured. Edit that switch instead."
    assert str(taken.value) == "Device 1000999999 is already configured by another switch."
    assert str(missing.value) == "No switch with device id 1000bbbbbb is configured."


async def test_a_changed_device_id_drops_a_kept_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", lights=["Ceiling"])]

    await update(switches, _DEVICE, SwitchInput(device_id=" 1000ABCDEF", lights=["Ceiling"]))
    assert switches[0].device_key == "k1"

    await update(switches, _DEVICE, SwitchInput(device_id="1000aaaaaa", lights=["Ceiling"]))
    assert switches[0] == SwitchSettings("1000aaaaaa", lights=["Ceiling"])


async def test_a_blank_device_key_removes_the_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", lights=["Ceiling"])]

    await update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, device_key=" \u3000", lights=["Ceiling"]))

    assert switches[0].device_key is None


async def test_the_device_id_is_checked_again_after_the_bridge() -> None:
    switches: list[SwitchSettings] = []
    calls: list[int] = []

    def current() -> list[SwitchSettings]:
        # Another flow adds the same device while the bridge is asked.
        calls.append(len(calls))
        return switches if len(calls) == 1 else [SwitchSettings(_DEVICE, lights=["Desk"])]

    with pytest.raises(SwitchConflictError):
        await switch_rules.create_switch(
            current, SwitchInput(device_id=_DEVICE, lights=["Ceiling"]), PAIRED, FakeBridge().connect
        )
    assert calls == [0, 1]


async def test_a_switch_removed_while_the_bridge_is_asked_is_not_found() -> None:
    before = [SwitchSettings(_DEVICE, lights=["Ceiling"])]
    calls: list[int] = []

    def current() -> list[SwitchSettings]:
        calls.append(len(calls))
        return before if len(calls) == 1 else []

    with pytest.raises(SwitchNotFoundError) as err:
        await switch_rules.update_switch(
            current,
            _DEVICE,
            SwitchInput(device_id=_DEVICE, lights=["Ceiling"]),
            PAIRED,
            FakeBridge().connect,
        )
    assert str(err.value) == "No switch with device id 1000abcdef is configured."


async def test_validation_comes_before_the_switch_is_looked_up() -> None:
    with pytest.raises(SwitchValidationError) as err:
        await update([], "1000bbbbbb", SwitchInput(device_id=""))

    assert err.value.errors == {
        "deviceId": ["Choose a device or enter its device id."],
        "targets": ["Choose at least one light, room or zone."],
    }
    assert str(err.value) == "Choose a device or enter its device id. Choose at least one light, room or zone."


# The bridge


async def test_check_targets_resolves_against_a_reachable_bridge() -> None:
    bridge = FakeBridge()
    candidate = SwitchSettings(_DEVICE, "Hall", lights=["Ceiling", "Garage"], groups=["room:Hallway"])

    check = await switch_rules.check_targets(candidate, PAIRED, bridge.connect)

    assert check == switch_rules.TargetCheck(
        ["Hall (1000abcdef): no Hue light named 'Garage'. Known lights: 'Ceiling', 'Floor lamp'"], []
    )
    assert (bridge.opened, bridge.closed) == (1, 1)


async def test_check_targets_can_take_another_resolver() -> None:
    calls: list[tuple[SwitchSettings, list[HueLight], list[HueGroup]]] = []

    def resolve(settings: SwitchSettings, lights: list[HueLight], groups: list[HueGroup], errors: list[str]) -> None:
        calls.append((settings, lights, groups))
        errors.append("refused")

    candidate = SwitchSettings(_DEVICE, lights=["Ceiling"])
    check = await switch_rules.check_targets(candidate, PAIRED, FakeBridge().connect, resolve)

    assert check.errors == ["refused"]
    assert calls == [(candidate, [CEILING, FLOOR_LAMP], [HALLWAY])]


async def test_check_targets_only_warns_when_the_bridge_cant_be_reached() -> None:
    bridge = FakeBridge(error=HueApiError("Hue bridge not reachable: Connection refused (192.0.2.20:443)"))

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), PAIRED, bridge.connect)

    assert check.errors == []
    assert check.warnings == [
        "The Hue bridge could not be reached, so the light names were not checked (Hue bridge not reachable: "
        "Connection refused (192.0.2.20:443))."
    ]
    assert bridge.closed == 1


@pytest.mark.parametrize(
    "hue",
    [
        HueSettings(),
        HueSettings(bridge_ip="192.0.2.20"),
        HueSettings(app_key="test-app-key"),
        HueSettings(bridge_ip=" \u3000", app_key="test-app-key"),
        HueSettings(bridge_ip="192.0.2.20", app_key="\u2028"),
    ],
)
async def test_without_an_address_and_a_key_the_bridge_is_not_paired(hue: HueSettings) -> None:
    bridge = FakeBridge()

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), hue, bridge.connect)

    assert check.warnings == [switch_rules.NOT_PAIRED]
    assert bridge.opened == 0
    assert not switch_rules.is_paired(hue)


async def test_settings_that_cant_be_used_are_a_warning_before_the_bridge_is_asked() -> None:
    def connect(hue: HueSettings) -> AbstractAsyncContextManager[HueResources]:
        raise ConfigError("The Hue application key contains characters that can't be sent to the bridge.")

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), PAIRED, connect)

    assert check.warnings == [
        "The Hue settings in the config can't be used, so the light names were not checked (The Hue application key "
        "contains characters that can't be sent to the bridge.)."
    ]


def test_hue_resources_are_sorted_by_name_without_regard_to_case() -> None:
    lights = [
        HueLight("a", "lamp", True),
        HueLight("b", "Desk", False),
        HueLight("c", "LAMP", True),
        HueLight("d", "", False),
    ]
    groups = [
        HueGroup("z1", "Upstairs", "zone", "g1"),
        HueGroup("r1", "study", "room", None),
        HueGroup("r2", "Kitchen", "room", "g2"),
        HueGroup("z2", "attic", "zone", "g3"),
        HueGroup("x1", "Other", "entertainment", "g4"),
    ]

    resources = switch_rules.hue_resources(lights, groups)

    assert [light.id for light in resources.lights] == ["d", "b", "a", "c"]
    assert [room.id for room in resources.rooms] == ["r2", "r1"]
    assert [zone.id for zone in resources.zones] == ["z2", "z1"]


async def test_load_resources() -> None:
    resources = await switch_rules.load_resources(PAIRED, FakeBridge().connect)
    assert [light.name for light in resources.lights] == ["Ceiling", "Floor lamp"]

    with pytest.raises(switch_rules.ResourcesError) as not_paired:
        await switch_rules.load_resources(UNPAIRED, FakeBridge().connect)
    with pytest.raises(switch_rules.ResourcesError) as unreachable:
        await switch_rules.load_resources(
            PAIRED, FakeBridge(error=HueApiError("Hue bridge request timed out.")).connect
        )
    with pytest.raises(switch_rules.ResourcesError) as unusable:
        await switch_rules.load_resources(PAIRED, FakeBridge(error=ConfigError("Bad settings.")).connect)

    assert (not_paired.value.status, str(not_paired.value)) == (409, "Pair the Hue bridge first.")
    assert (unreachable.value.status, str(unreachable.value)) == (502, "Hue bridge request timed out.")
    assert (unusable.value.status, str(unusable.value)) == (409, "Bad settings.")


# Validating the input

TOO_LONG_NAME = {"name": ["Use at most 64 characters."]}
CONTROL_NAME = {"name": ["The name contains control characters."]}
NO_DEVICE = {"deviceId": ["Choose a device or enter its device id."]}
BAD_DEVICE = {"deviceId": ["A device id has 4 to 40 letters and digits, e.g. 1000abcdef."]}
BAD_KEY = {"deviceKey": ["A device key has at most 128 characters and no spaces."]}
BAD_ENTRY = ["Each entry needs 1 to 200 characters."]
NO_TARGET = {"targets": ["Choose at least one light, room or zone."]}
EMOJI = "\U0001f600"


def validate(**fields: Any) -> tuple[switch_rules.Errors, SwitchSettings]:
    """``fields`` of a valid switch input, unless given."""
    return switch_rules.validate(SwitchInput(**({"device_id": _DEVICE, "lights": ["Ceiling"]} | fields)))


@pytest.mark.parametrize(
    ("name", "saved", "errors"),
    [
        ("x" * 64, "x" * 64, {}),
        ("\u3000" + "x" * 64 + "\n", "x" * 64, {}),
        ("x" * 63 + EMOJI, "x" * 63 + EMOJI, {}),
        ("x" * 65, "x" * 65, TOO_LONG_NAME),
        ("x" * 64 + EMOJI, "x" * 64 + EMOJI, TOO_LONG_NAME),
        ("x" * 65 + "\x07", "x" * 65 + "\x07", TOO_LONG_NAME),
        ("Hall\x07", "Hall\x07", CONTROL_NAME),
        ("Hall\x7f", "Hall\x7f", CONTROL_NAME),
        ("Ha\x85ll", "Ha\x85ll", CONTROL_NAME),
        ("Ha\tll", "Ha\tll", CONTROL_NAME),
        ("Hall\x85", "Hall", {}),
        ("Hall\u200b", "Hall\u200b", {}),
        (" \x1c", None, {}),
        ("", None, {}),
        (None, None, {}),
    ],
)
def test_a_name_has_at_most_64_characters_without_control_characters(
    name: str | None, saved: str | None, errors: switch_rules.Errors
) -> None:
    assert validate(name=name) == (errors, SwitchSettings(_DEVICE, saved, lights=["Ceiling"]))


@pytest.mark.parametrize(
    ("device_id", "normalized", "errors"),
    [
        ("1000abcdef", "1000abcdef", {}),
        (" 1000ABCDEF\u3000", "1000abcdef", {}),
        ("\x1c1000abcdef\x85", "1000abcdef", {}),
        ("abcd", "abcd", {}),
        ("x" * 40, "x" * 40, {}),
        ("1000\u212aabc", "1000kabc", {}),
        ("abc", "abc", BAD_DEVICE),
        ("x" * 41, "x" * 41, BAD_DEVICE),
        ("1000-abcde", "1000-abcde", BAD_DEVICE),
        ("1000 abcde", "1000 abcde", BAD_DEVICE),
        ("1000abcde\x00", "1000abcde\x00", BAD_DEVICE),
        ("\uff11000abc", "\uff11000abc", BAD_DEVICE),
        ("1000\u0130abc", "1000i\u0307abc", BAD_DEVICE),
        ("\ufeff1000abcdef", "\ufeff1000abcdef", BAD_DEVICE),
        ("", "", NO_DEVICE),
        (" \t", "", NO_DEVICE),
        (None, "", NO_DEVICE),
    ],
)
def test_a_device_id_has_4_to_40_ascii_letters_and_digits_once_stripped_and_lowercased(
    device_id: str | None, normalized: str, errors: switch_rules.Errors
) -> None:
    found, settings = validate(device_id=device_id)

    assert (found, settings.device_id) == (errors, normalized)


@pytest.mark.parametrize(
    ("key", "saved", "errors"),
    [
        ("k" * 128, "k" * 128, {}),
        (" " + "k" * 128 + "\t", "k" * 128, {}),
        ("k" * 127 + EMOJI, "k" * 127 + EMOJI, {}),
        ("key\u200b", "key\u200b", {}),
        ("k" * 129, "k" * 129, BAD_KEY),
        ("two words", "two words", BAD_KEY),
        ("two\u3000words", "two\u3000words", BAD_KEY),
        ("two\x1cwords", "two\x1cwords", BAD_KEY),
        ("key\x00", "key\x00", BAD_KEY),
        ("", None, {}),
        (" \u3000", None, {}),
        (None, None, {}),
    ],
)
def test_a_device_key_has_at_most_128_characters_without_white_space_or_control_characters(
    key: str | None, saved: str | None, errors: switch_rules.Errors
) -> None:
    found, settings = validate(device_key=key)

    assert (found, settings.device_key) == (errors, saved)


@pytest.mark.parametrize(
    ("entry", "valid"),
    [
        ("x", True),
        ("x" * 200, True),
        (" " + "x" * 200 + "\u3000", True),
        ("x" * 199 + EMOJI, True),
        ("Ceiling\x85", True),
        ("Ceil\u200bing", True),
        ("x" * 201, False),
        ("x" * 200 + EMOJI, False),
        ("Ceil\x07ing", False),
        ("Ceil\x85ing", False),
        ("", False),
        (" \u2028", False),
    ],
)
def test_an_entry_has_1_to_200_characters_without_control_characters(entry: str, valid: bool) -> None:
    assert validate(lights=["Desk", entry])[0] == ({} if valid else {"lights": BAD_ENTRY})
    assert validate(lights=[], groups=[entry])[0] == ({} if valid else {"groups": BAD_ENTRY})


def test_a_list_has_at_most_50_entries_counted_before_duplicates_are_removed() -> None:
    assert validate(lights=[f"Light {i}" for i in range(50)])[0] == {}
    assert validate(groups=[f"Room {i}" for i in range(51)])[0] == {"groups": ["Use at most 50 entries."]}

    errors, settings = validate(lights=["Ceiling"] * 51)

    assert errors == {"lights": ["Use at most 50 entries."]}
    assert settings.lights == ["Ceiling"]
    assert validate(lights=["", *["Ceiling"] * 50])[0] == {"lights": ["Use at most 50 entries."]}


def test_a_switch_needs_at_least_one_light_room_or_zone() -> None:
    assert validate(lights=[], groups=[])[0] == NO_TARGET
    assert validate(lights=[], groups=["room:Hallway"])[0] == {}
    assert validate(lights=[""], groups=[" "])[0] == {"lights": BAD_ENTRY, "groups": BAD_ENTRY}


def test_the_errors_come_in_the_order_of_the_fields() -> None:
    errors, _ = switch_rules.validate(SwitchInput("x" * 65, "abc", "a key", ["x" * 201], [""]))
    assert list(errors) == ["name", "deviceId", "deviceKey", "lights", "groups"]

    errors, _ = switch_rules.validate(SwitchInput("Hall\x07", "", "a key", [], []))
    assert list(errors) == ["name", "deviceId", "deviceKey", "targets"]


def test_duplicates_are_removed_without_regard_to_case() -> None:
    lights = [
        "Ceiling",
        " ceiling ",
        "Straße",
        "STRASSE",
        "\u03c3",
        "\u03c2",
        "\u00b5",
        "\u039c",
        "\u212a",
        "k",
        "Desk",
    ]

    errors, settings = validate(lights=lights, groups=["room:Hall", " ROOM:hall", "Hall"])

    assert errors == {}
    assert settings.lights == ["Ceiling", "Straße", "\u03c3", "\u00b5", "\u212a", "Desk"]
    assert settings.groups == ["room:Hall", "Hall"]


def test_the_switch_to_save_is_stripped_and_normalized() -> None:
    errors, settings = switch_rules.validate(
        SwitchInput(" Hall ", " 1000ABCDEF ", " key ", [" Ceiling\u3000"], ["\troom:Hallway "])
    )

    assert errors == {}
    assert settings == SwitchSettings(_DEVICE, "Hall", "key", ["Ceiling"], ["room:Hallway"])


# Saving a changed switch


async def test_a_new_key_replaces_the_current_one_also_with_another_device() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", lights=["Ceiling"])]

    await update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, device_key=" k2 ", lights=["Ceiling"]))
    assert switches[0].device_key == "k2"

    await update(switches, _DEVICE, SwitchInput(device_id="1000aaaaaa", device_key="k3", lights=["Ceiling"]))
    assert switches[0] == SwitchSettings("1000aaaaaa", device_key="k3", lights=["Ceiling"])


async def test_a_switch_is_changed_in_its_place_found_without_regard_to_case() -> None:
    switches = [
        SwitchSettings(_OTHER, lights=["Ceiling"]),
        SwitchSettings(" 1000AbCdEf ", "Hall", "k1", ["Ceiling"]),
    ]

    write = await update(switches, "1000ABCDEF", SwitchInput("Hall", "1000ABCDEF", None, ["Floor lamp"]))

    assert write.index == 1
    assert switches[1] == SwitchSettings(_DEVICE, "Hall", "k1", ["Floor lamp"])
