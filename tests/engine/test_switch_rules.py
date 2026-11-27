"""The rules a switch meets before it is saved (engine/switch_rules.py): validating the input, and adding and changing a
switch."""

from typing import Any

import pytest

from custom_components.sonoff2hue.engine import switch_rules
from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.switch_rules import (
    SwitchConflictError,
    SwitchInput,
    SwitchNotFoundError,
    SwitchValidationError,
    SwitchWrite,
)

_DEVICE = "1000abcdef"
_OTHER = "1000999999"
CEILING = "light.ceiling"
DESK = "light.desk"
CEILING_ID = "0123456789abcdef0123456789abcdef"
"""How a switch's settings keep a light that the entity registry has: by its registry id."""


def create(switches: list[SwitchSettings], switch: SwitchInput) -> SwitchWrite:
    write = switch_rules.create_switch(switches, switch)
    switches.append(write.switch)
    return write


def update(switches: list[SwitchSettings], device_id: str, switch: SwitchInput) -> SwitchWrite:
    write = switch_rules.update_switch(switches, device_id, switch)
    assert write.index is not None
    switches[write.index] = write.switch
    return write


# Adding and changing a switch


def test_a_new_switch_is_saved_with_its_device_key() -> None:
    switches: list[SwitchSettings] = []

    write = create(switches, SwitchInput("Hall", "1000ABCDEF", "secret-device-key", [CEILING_ID, "light.porch"]))

    assert write.index is None
    assert switches == [SwitchSettings(_DEVICE, "Hall", "secret-device-key", [CEILING_ID, "light.porch"])]


def test_a_new_switch_is_validated_first() -> None:
    switches: list[SwitchSettings] = []

    with pytest.raises(SwitchValidationError) as invalid:
        create(switches, SwitchInput("x", "not valid!", targets=[]))

    assert invalid.value.errors == {
        "deviceId": ["A device id has 4 to 40 letters and digits, e.g. 1000abcdef."],
        "targets": ["Choose at least one light."],
    }
    assert switches == []


def test_a_changed_switch_keeps_or_removes_its_device_key() -> None:
    switches: list[SwitchSettings] = []
    create(switches, SwitchInput(device_id=_DEVICE, device_key="k1", targets=[CEILING]))

    update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, None, [DESK]))
    after_keep = switches[0]
    update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, "", [DESK]))

    assert after_keep == SwitchSettings(_DEVICE, "Hall", "k1", [DESK])
    assert switches[0].device_key is None


def test_a_device_id_is_configured_once() -> None:
    switches = [SwitchSettings(_DEVICE, targets=[CEILING]), SwitchSettings(_OTHER, targets=[CEILING])]

    with pytest.raises(SwitchConflictError) as again:
        create(switches, SwitchInput(device_id=" 1000ABCDEF ", targets=[CEILING]))
    with pytest.raises(SwitchConflictError) as taken:
        update(switches, _DEVICE, SwitchInput(device_id=_OTHER, targets=[CEILING]))
    with pytest.raises(SwitchNotFoundError) as missing:
        update(switches, "1000BBBBBB ", SwitchInput(device_id=_DEVICE, targets=[CEILING]))

    assert str(again.value) == "Device 1000abcdef is already configured. Edit that switch instead."
    assert str(taken.value) == "Device 1000999999 is already configured by another switch."
    assert str(missing.value) == "No switch with device id 1000bbbbbb is configured."


def test_a_changed_device_id_drops_a_kept_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", targets=[CEILING])]

    update(switches, _DEVICE, SwitchInput(device_id=" 1000ABCDEF", targets=[CEILING]))
    assert switches[0].device_key == "k1"

    update(switches, _DEVICE, SwitchInput(device_id="1000aaaaaa", targets=[CEILING]))
    assert switches[0] == SwitchSettings("1000aaaaaa", targets=[CEILING])


def test_a_blank_device_key_removes_the_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", targets=[CEILING])]

    update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, device_key=" \u3000", targets=[CEILING]))

    assert switches[0].device_key is None


def test_a_new_key_replaces_the_current_one_also_with_another_device() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", targets=[CEILING])]

    update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, device_key=" k2 ", targets=[CEILING]))
    assert switches[0].device_key == "k2"

    update(switches, _DEVICE, SwitchInput(device_id="1000aaaaaa", device_key="k3", targets=[CEILING]))
    assert switches[0] == SwitchSettings("1000aaaaaa", device_key="k3", targets=[CEILING])


def test_a_switch_is_changed_in_its_place_found_without_regard_to_case() -> None:
    switches = [SwitchSettings(_OTHER, targets=[CEILING]), SwitchSettings(" 1000AbCdEf ", "Hall", "k1", [CEILING])]

    write = update(switches, "1000ABCDEF", SwitchInput("Hall", "1000ABCDEF", None, [DESK]))

    assert write.index == 1
    assert switches == [SwitchSettings(_OTHER, targets=[CEILING]), SwitchSettings(_DEVICE, "Hall", "k1", [DESK])]


def test_validation_comes_before_the_switch_is_looked_up() -> None:
    with pytest.raises(SwitchValidationError) as err:
        update([], "1000bbbbbb", SwitchInput(device_id=""))

    assert err.value.errors == {
        "deviceId": ["Choose a device or enter its device id."],
        "targets": ["Choose at least one light."],
    }
    assert str(err.value) == "Choose a device or enter its device id. Choose at least one light."


@pytest.mark.parametrize(("key", "has_key"), [("k1", True), (" ", False), ("", False), (None, False)])
def test_the_form_offers_to_keep_a_key_that_is_not_blank(key: str | None, has_key: bool) -> None:
    assert switch_rules.has_device_key(SwitchSettings(_DEVICE, device_key=key)) is has_key


# Validating the input

TOO_LONG_NAME = {"name": ["Use at most 64 characters."]}
CONTROL_NAME = {"name": ["The name contains control characters."]}
NO_DEVICE = {"deviceId": ["Choose a device or enter its device id."]}
BAD_DEVICE = {"deviceId": ["A device id has 4 to 40 letters and digits, e.g. 1000abcdef."]}
BAD_KEY = {"deviceKey": ["A device key has at most 128 characters and no spaces."]}
BAD_LIGHT = {"targets": ["Each light needs 1 to 255 characters without spaces."]}
NO_LIGHT = {"targets": ["Choose at least one light."]}
TOO_MANY_LIGHTS = {"targets": ["Choose at most 50 lights."]}
EMOJI = "\U0001f600"


def validate(**fields: Any) -> tuple[switch_rules.Errors, SwitchSettings]:
    """``fields`` of a valid switch input, unless given."""
    return switch_rules.validate(SwitchInput(**({"device_id": _DEVICE, "targets": [CEILING]} | fields)))


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
    assert validate(name=name) == (errors, SwitchSettings(_DEVICE, saved, targets=[CEILING]))


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
    ("light", "valid"),
    [
        (CEILING, True),
        (CEILING_ID, True),
        ("x" * 255, True),
        (" " + "x" * 255 + "\u3000", True),
        ("x" * 254 + EMOJI, True),
        ("light.ceiling\x85", True),
        ("light.ceil\u200bing", True),
        ("x" * 256, False),
        ("x" * 255 + EMOJI, False),
        ("light.ceiling lamp", False),
        ("light.ceiling\u3000lamp", False),
        ("light.ceil\x07ing", False),
        ("light.ceil\x85ing", False),
        ("", False),
        (" \u2028", False),
    ],
)
def test_a_light_has_1_to_255_characters_without_white_space_or_control_characters(light: str, valid: bool) -> None:
    assert validate(targets=[DESK, light])[0] == ({} if valid else BAD_LIGHT)


def test_a_switch_has_1_to_50_lights_each_kept_once_in_their_order() -> None:
    assert validate(targets=[])[0] == NO_LIGHT
    assert validate(targets=[f"light.l{i}" for i in range(50)])[0] == {}
    assert validate(targets=[f"light.l{i}" for i in range(51)])[0] == TOO_MANY_LIGHTS

    # Duplicates, once stripped, are kept once and don't count.
    errors, settings = validate(targets=[DESK, f" {CEILING}", *[CEILING] * 50, f"{DESK}\t"])

    assert errors == {}
    assert settings.targets == [DESK, CEILING]


def test_the_errors_come_in_the_order_of_the_fields() -> None:
    errors, _ = switch_rules.validate(SwitchInput("x" * 65, "abc", "a key", ["x" * 256]))
    assert list(errors) == ["name", "deviceId", "deviceKey", "targets"]

    errors, _ = switch_rules.validate(SwitchInput("Hall\x07", "", "a key", []))
    assert list(errors) == ["name", "deviceId", "deviceKey", "targets"]


def test_the_switch_to_save_is_stripped_and_normalized() -> None:
    errors, settings = switch_rules.validate(
        SwitchInput(" Hall ", " 1000ABCDEF ", " key ", [f" {CEILING}\u3000", f"\t{CEILING_ID} "])
    )

    assert errors == {}
    assert settings == SwitchSettings(_DEVICE, "Hall", "key", [CEILING, CEILING_ID])
