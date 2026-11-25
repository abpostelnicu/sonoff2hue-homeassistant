"""The switch settings' rules (engine/config.py): device ids stripped and lowercased, compared without regard to case,
and blank values by Python's white space."""

import pytest

from custom_components.sonoff2hue.engine.config import ConfigError, EngineConfig, HueSettings, SwitchSettings

PAIRED = HueSettings(bridge_ip="192.0.2.20", app_key="test-app-key")


@pytest.mark.parametrize(
    ("device_id", "normalized"),
    [
        ("1000abcdef", "1000abcdef"),
        (" 1000ABCDEF\u3000", "1000abcdef"),
        ("\x1c1000abcdef\x85", "1000abcdef"),
        ("\ufeff1000abcdef", "\ufeff1000abcdef"),
        ("1000\u212aabc", "1000kabc"),
    ],
)
def test_the_device_id_is_stripped_and_lowercased(device_id: str, normalized: str) -> None:
    assert SwitchSettings(device_id).normalized_device_id == normalized


@pytest.mark.parametrize(
    ("name", "display"), [("Hall", "Hall (1000abcdef)"), (None, "1000abcdef"), (" \x1f", "1000abcdef")]
)
def test_the_display_name_is_the_name_and_the_device_id(name: str | None, display: str) -> None:
    assert SwitchSettings(" 1000ABCDEF ", name).display_name == display


def test_switches_are_found_by_device_id_without_regard_to_case() -> None:
    hall, street = SwitchSettings(" 1000AbCdEf ", "Hall"), SwitchSettings("1000Straße", "Street")
    config = EngineConfig(PAIRED, [hall, street])

    assert config.find_switch("1000ABCDEF") is hall
    assert config.find_switch("1000STRASSE") is street
    assert config.find_switch("1000999999") is None
    assert hall.has_device_id("1000abcdef")
    assert not hall.has_device_id("1000abcde")


def test_a_device_id_is_configured_once_without_regard_to_case() -> None:
    config = EngineConfig(
        PAIRED,
        [
            SwitchSettings("1000abcdef", lights=["Ceiling"]),
            SwitchSettings(" 1000ABCDEF ", "Hall", lights=["Desk"]),
        ],
    )

    with pytest.raises(ConfigError) as err:
        config.validate_for_run()

    assert str(err.value) == (
        "Invalid configuration:\n"
        "  - switches[1] ('Hall'): deviceId ' 1000ABCDEF ' is configured more than once; put all its lights in one entry."
    )


def test_blank_values_are_empty_or_white_space() -> None:
    config = EngineConfig(
        HueSettings(bridge_ip="\x1c", app_key="\u3000"),
        [SwitchSettings(" ", lights=["\x1f", None]), SwitchSettings("1000abcdef", "\x1e", groups=["\ufeff"])],
    )

    with pytest.raises(ConfigError) as err:
        config.validate_for_run()

    assert str(err.value).splitlines() == [
        "Invalid configuration:",
        "  - hue.bridgeIp is missing (pair the bridge).",
        "  - hue.appKey is missing (pair the bridge).",
        "  - switches[0]: deviceId is missing.",
        "  - switches[0]: empty light/group name.",
    ]
