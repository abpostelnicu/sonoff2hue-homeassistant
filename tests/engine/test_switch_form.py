"""The switch form's own checks (engine/switch_form.py), before the switch rules check a switch again."""

from typing import Any

import pytest

from custom_components.sonoff2hue.engine import switch_form, switch_rules
from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.switch_form import Problem
from custom_components.sonoff2hue.engine.switch_rules import SwitchInput

_DEVICE = "1000abcdef"
_OTHER = "1000999999"
_FREE = "1000aaaaaa"
CEILING = "light.ceiling"
CEILING_ID = "0123456789abcdef0123456789abcdef"
"""How a switch's settings keep a light that the entity registry has: by its registry id."""
SWITCHES = [SwitchSettings(" 1000ABCDEF ", "Hall"), SwitchSettings(_OTHER, "  ")]


def _body(**kwargs: Any) -> SwitchInput:
    return SwitchInput(**({"name": None, "device_id": _FREE, "targets": [CEILING]} | kwargs))


def _problems(body: SwitchInput, original_id: str | None = None, **kwargs: Any) -> list[Problem]:
    options: dict[str, Any] = {"manual_device": False, "key_mode": "set"} | kwargs
    return switch_form.form_problems(body, SWITCHES, original_id, **options)


# The device


def test_a_used_device_is_named_by_its_switch() -> None:
    assert switch_form.used_by(SWITCHES, "1000ABCDEF", None) == "Hall"
    assert switch_form.used_by(SWITCHES, _OTHER, None) == _OTHER
    assert switch_form.used_by(SWITCHES, _DEVICE, _DEVICE) is None
    assert switch_form.used_by(SWITCHES, _FREE, None) is None
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


def test_identify_by_press_skips_devices_of_other_switches() -> None:
    assert switch_form.identify(SWITCHES, None, _FREE) == switch_form.Identified(
        True, "Selected 1000aaaaaa: it reported a press."
    )
    assert switch_form.identify(SWITCHES, None, _DEVICE) == switch_form.Identified(
        False,
        "1000abcdef reported a press, but it is already used by Hall. Operate the switch you want to add.",
    )
    assert switch_form.identify(SWITCHES, _DEVICE, _DEVICE).selected


# The name, the lights and the key


def test_the_name_is_stripped_and_counted_in_characters() -> None:
    emoji = "\U0001f600"
    fits = switch_form.form_input("\u3000" + "x" * 63 + emoji + "\x85", _FREE, "set", "", [CEILING])
    too_long = switch_form.form_input("x" * 64 + emoji, _FREE, "set", "", [CEILING])

    assert fits.name == "x" * 63 + emoji
    assert _problems(fits) == []
    assert _problems(too_long) == [Problem("name", "Use at most 64 characters.")]
    assert switch_form.form_input(" \u3000\x1c", _FREE, "set", "", [CEILING]).name is None
    assert switch_form.form_input("\ufeffHall", _FREE, "set", "", [CEILING]).name == "\ufeffHall"


def test_at_least_one_light() -> None:
    assert _problems(_body(targets=[])) == [Problem("targets", "Choose at least one light.")]
    assert _problems(_body(targets=[CEILING_ID])) == []


@pytest.mark.parametrize(
    ("mode", "typed", "expected"),
    [("keep", "ignored", None), ("remove", "ignored", ""), ("replace", " new-key\u3000", "new-key"), ("set", " ", "")],
)
def test_device_key_value(mode: switch_form.KeyMode, typed: str, expected: str | None) -> None:
    assert switch_form.device_key_value(mode, typed) == expected


def test_replacing_the_key_needs_a_new_one() -> None:
    body = switch_form.form_input("Hall", _FREE, "replace", " \u3000", [CEILING])

    assert _problems(body, key_mode="replace") == [
        Problem("deviceKey", "Enter the new device key, or keep or remove the current one.")
    ]


def test_the_key_warning_shows_when_a_kept_key_meets_another_device() -> None:
    assert switch_form.key_warning("keep", True, _FREE, _DEVICE) == switch_form.KEY_WARNING
    assert switch_form.key_warning("keep", True, "1000ABCDEF", _DEVICE) is None
    assert switch_form.key_warning("keep", True, "", _DEVICE) is None
    assert switch_form.key_warning("replace", True, _FREE, _DEVICE) is None
    assert switch_form.key_warning("set", False, _FREE, _DEVICE) is None


def test_all_problems_in_the_forms_order() -> None:
    body = switch_form.form_input("x" * 65, "", "replace", "", [])

    assert [problem.field for problem in _problems(body, key_mode="replace")] == [
        "deviceId",
        "name",
        "targets",
        "deviceKey",
    ]


# The switch rules' errors on the form's fields


def test_the_rules_messages_go_to_their_fields_and_the_others_to_the_form() -> None:
    """Joined by field, in order, with the keys matched without regard to case; empty messages are left out."""
    errors = {
        "deviceId": ["A device id has 4 to 40 letters and digits, e.g. 1000abcdef."],
        "TARGETS": ["Choose at most 50 lights.", "Each light needs 1 to 255 characters without spaces."],
        "Other": ["Something else.", ""],
        "devicekey": ["A device key has at most 128 characters and no spaces."],
        "name": ["Use at most 64 characters."],
    }

    assert switch_form.field_errors(errors) == (
        {
            "deviceId": "A device id has 4 to 40 letters and digits, e.g. 1000abcdef.",
            "targets": "Choose at most 50 lights. Each light needs 1 to 255 characters without spaces.",
            "deviceKey": "A device key has at most 128 characters and no spaces.",
            "name": "Use at most 64 characters.",
        },
        ["Something else."],
    )


# The whole form


def test_a_switch_from_the_form_is_saved_by_the_rules() -> None:
    body = switch_form.form_input(" Kitchen ", "1000ABCDEF", "set", " a-device-key ", [CEILING_ID, "light.counter"])

    assert switch_form.form_problems(body, [], None, manual_device=True, key_mode="set") == []
    write = switch_rules.create_switch([], body)

    assert write.switch == SwitchSettings(_DEVICE, "Kitchen", "a-device-key", [CEILING_ID, "light.counter"])
