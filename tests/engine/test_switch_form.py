"""The switch form's own checks (engine/switch_form.py), before the switch rules check a switch again."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import picker, switch_form, switch_rules
from custom_components.sonoff2hue.engine.config import HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueGroup, HueLight
from custom_components.sonoff2hue.engine.switch_form import Problem
from custom_components.sonoff2hue.engine.switch_rules import HueResources, SwitchInput

_DEVICE = "1000abcdef"
_OTHER = "1000999999"
_FREE = "1000aaaaaa"
SWITCHES = [SwitchSettings(" 1000ABCDEF ", "Hall"), SwitchSettings(_OTHER, "  ")]


def _body(**kwargs: Any) -> SwitchInput:
    return SwitchInput(**({"name": None, "device_id": _FREE, "lights": ["Ceiling"], "groups": []} | kwargs))


def _problems(body: SwitchInput, original_id: str | None = None, **kwargs: Any) -> list[Problem]:
    options: dict[str, Any] = {"manual_device": False, "manual_targets": False, "key_mode": "set"} | kwargs
    return switch_form.form_problems(body, SWITCHES, original_id, **options)


# The device


def test_a_used_device_is_named_by_its_switch() -> None:
    assert switch_form.used_by(SWITCHES, "1000ABCDEF", None) == "Hall"
    assert switch_form.used_by(SWITCHES, _OTHER, None) == _OTHER
    assert switch_form.used_by(SWITCHES, _DEVICE, _DEVICE) is None
    assert switch_form.used_by(SWITCHES, "1000aaaaaa", None) is None
    assert switch_form.used_by([], _DEVICE, None) is None


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("1000abcdef", "1000abcdef", True),
        ("1000ABCDEF", "1000abcdef", True),
        ("1000STRASSE", "1000straße", True),
        (None, "", True),
        (None, None, True),
        (" 1000abcdef", "1000abcdef", False),
        ("1000abcdef", "1000abcdee", False),
        (None, "1000abcdef", False),
    ],
)
def test_device_ids_are_the_same_without_regard_to_case(a: str | None, b: str | None, same: bool) -> None:
    assert switch_form.same_id(a, b) is same
    assert switch_form.same_id(b, a) is same


@pytest.mark.parametrize(
    ("name", "device_id", "label"),
    [
        ("Hall", _DEVICE, "Hall"),
        ("\u3000 Hall lights\n", _DEVICE, "Hall lights"),
        (" \x1c", _DEVICE, _DEVICE),
        ("", _DEVICE, _DEVICE),
        (None, _DEVICE, _DEVICE),
        (None, "", "Switch"),
        (None, None, "Switch"),
    ],
)
def test_a_switch_is_labelled_by_its_name_else_its_device_id(
    name: str | None, device_id: str | None, label: str
) -> None:
    assert switch_form.switch_label(name, device_id) == label


@pytest.mark.parametrize(
    ("device_id", "manual", "message"),
    [
        ("", True, "Enter the device id."),
        ("", False, "Choose the device, or enter its id manually."),
        ("abc", True, "A device id has 4 to 40 letters and digits."),
        ("1000abcdef\n", False, "A device id has 4 to 40 letters and digits."),
        ("1000\u212aabc", True, "A device id has 4 to 40 letters and digits."),
        ("x" * 41, True, "A device id has 4 to 40 letters and digits."),
        ("1000ABCDEF", False, "This device is already used by Hall."),
        (_OTHER, False, "This device is already used by 1000999999."),
    ],
)
def test_device_problems(device_id: str, manual: bool, message: str) -> None:
    assert _problems(_body(device_id=device_id), manual_device=manual) == [Problem("deviceId", message)]


def test_the_edited_switch_may_keep_its_device() -> None:
    assert _problems(_body(device_id="1000ABCDEF"), original_id=_DEVICE) == []
    assert _problems(_body(device_id=_OTHER), original_id=_DEVICE) == [
        Problem("deviceId", "This device is already used by 1000999999.")
    ]


def test_current_device_id() -> None:
    assert switch_form.current_device_id(True, " 1000abcdef\u3000", "1000999999") == _DEVICE
    assert switch_form.current_device_id(False, "typed", " 1000999999 ") == " 1000999999 "
    assert switch_form.current_device_id(False, "typed", None) == ""


def test_identify_by_press_skips_devices_of_other_switches() -> None:
    assert switch_form.identify(SWITCHES, None, "1000aaaaaa") == switch_form.Identified(
        True, "Selected 1000aaaaaa: it reported a press."
    )
    assert switch_form.identify(SWITCHES, None, _DEVICE) == switch_form.Identified(
        False,
        "1000abcdef reported a press, but it is already used by Hall. Operate the switch you want to add.",
    )
    assert switch_form.identify(SWITCHES, _DEVICE, _DEVICE).selected


# The name, the targets and the key


def test_the_name_is_stripped_and_counted_in_characters() -> None:
    emoji = "\U0001f600"
    fits = switch_form.form_input("\u3000" + "x" * 63 + emoji + "\x85", _FREE, "set", "", ["Ceiling"], [])
    too_long = switch_form.form_input("x" * 64 + emoji, _FREE, "set", "", ["Ceiling"], [])

    assert fits.name == "x" * 63 + emoji
    assert _problems(fits) == []
    assert _problems(too_long) == [Problem("name", "Use at most 64 characters.")]
    assert switch_form.form_input(" \u3000\x1c", _FREE, "set", "", ["Ceiling"], []).name is None
    assert switch_form.form_input("\ufeffHall", _FREE, "set", "", ["Ceiling"], []).name == "\ufeffHall"


def test_at_least_one_target() -> None:
    assert _problems(_body(lights=[], groups=[])) == [Problem("targets", "Choose at least one light, room or zone.")]
    assert _problems(_body(lights=[], groups=[]), manual_targets=True) == [
        Problem("lights", "Choose at least one light, room or zone.")
    ]
    assert _problems(_body(lights=[], groups=["room:Hallway"])) == []


@pytest.mark.parametrize(
    ("mode", "typed", "expected"),
    [("keep", "ignored", None), ("remove", "ignored", ""), ("replace", " new-key\u3000", "new-key"), ("set", " ", "")],
)
def test_device_key_value(mode: switch_form.KeyMode, typed: str, expected: str | None) -> None:
    assert switch_form.device_key_value(mode, typed) == expected


def test_replacing_the_key_needs_a_new_one() -> None:
    body = switch_form.form_input("Hall", _FREE, "replace", " \u3000", ["Ceiling"], [])

    assert _problems(body, key_mode="replace") == [
        Problem("deviceKey", "Enter the new device key, or keep or remove the current one.")
    ]


def test_the_key_warning_shows_when_a_kept_key_meets_another_device() -> None:
    assert switch_form.key_warning("keep", True, "1000aaaaaa", _DEVICE) == switch_form.KEY_WARNING
    assert switch_form.key_warning("keep", True, "1000ABCDEF", _DEVICE) is None
    assert switch_form.key_warning("keep", True, "", _DEVICE) is None
    assert switch_form.key_warning("replace", True, "1000aaaaaa", _DEVICE) is None
    assert switch_form.key_warning("set", False, "1000aaaaaa", _DEVICE) is None


def test_all_problems_in_the_forms_order() -> None:
    body = switch_form.form_input("x" * 65, "", "replace", "", [], [])

    assert [problem.field for problem in _problems(body, key_mode="replace")] == [
        "deviceId",
        "name",
        "targets",
        "deviceKey",
    ]


def test_typed_entries_round_trip() -> None:
    text = switch_form.join_list(["Ceiling", "room:Hallway"])

    assert text == "Ceiling, room:Hallway"
    assert picker.split_list(text) == ["Ceiling", "room:Hallway"]
    assert switch_form.join_list([]) == ""


# The switch rules' errors on the form's fields


def test_field_errors_in_the_picker_join_the_target_lists() -> None:
    errors = {
        "deviceId": ["A device id has 4 to 40 letters and digits, e.g. 1000abcdef."],
        "lights": ["Use at most 50 entries."],
        "groups": ["Each entry needs 1 to 200 characters."],
        "Other": ["Something else.", ""],
    }

    assert switch_form.field_errors(errors, manual_targets=False) == (
        {
            "deviceId": "A device id has 4 to 40 letters and digits, e.g. 1000abcdef.",
            "targets": "Use at most 50 entries. Each entry needs 1 to 200 characters.",
        },
        ["Something else."],
    )
    assert switch_form.field_errors(errors, manual_targets=True)[0] == {
        "deviceId": "A device id has 4 to 40 letters and digits, e.g. 1000abcdef.",
        "lights": "Use at most 50 entries.",
        "groups": "Each entry needs 1 to 200 characters.",
    }
    assert switch_form.field_errors({"DEVICEKEY": ["x"]}, manual_targets=False) == ({"deviceKey": "x"}, [])


# The whole form


@dataclass
class Bridge:
    lights: list[HueLight]
    groups: list[HueGroup]

    async def get_lights(self) -> list[HueLight]:
        return self.lights

    async def get_groups(self) -> list[HueGroup]:
        return self.groups

    def connect(self, hue: HueSettings) -> AbstractAsyncContextManager[HueResources]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[HueResources]:
            yield self

        return opened()


async def test_a_switch_added_with_the_picker_is_saved_with_the_encoded_entries() -> None:
    bridge = Bridge(
        [HueLight("l1", "Ceiling", True), HueLight("l2", "Lamp", False), HueLight("l3", "lamp", False)],
        [HueGroup("r1", "Kitchen", "room", "g1"), HueGroup("z1", "Kitchen", "zone", "g2")],
    )
    hue = HueSettings(bridge_ip="192.0.2.20", app_key="test-app-key")
    resources = switch_rules.hue_resources(bridge.lights, bridge.groups)
    selection = picker.create_selection(resources)
    selection.light.update({"l1", "l3"})
    selection.zone.add("z1")
    targets = picker.selection_to_config(selection, resources)

    device_id = switch_form.current_device_id(True, " 1000ABCDEF ", None)
    body = switch_form.form_input(" Kitchen ", device_id, "set", "", targets.lights, targets.groups)
    assert switch_form.form_problems(body, [], None, manual_device=True, manual_targets=False, key_mode="set") == []
    write = await switch_rules.create_switch(list, body, hue, bridge.connect)

    assert write.switch == SwitchSettings(_DEVICE, "Kitchen", None, ["Ceiling", "l3"], ["zone:Kitchen"])
    assert write.warnings == []
