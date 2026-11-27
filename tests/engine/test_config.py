"""The switches' settings (engine/config.py): device ids stripped and lowercased, compared without regard to case,
blank values by Python's white space, and the checks before the switches run."""

import pytest

from custom_components.sonoff2hue.engine.config import ConfigError, EngineConfig, SwitchSettings

CEILING = "light.ceiling"
CEILING_ID = "0123456789abcdef0123456789abcdef"
"""How a switch's settings keep a light that the entity registry has: by its registry id."""


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
    config = EngineConfig([hall, street])

    assert config.find_switch("1000ABCDEF") is hall
    assert config.find_switch("1000STRASSE") is street
    assert config.find_switch("1000999999") is None
    assert hall.has_device_id("1000abcdef")
    assert not hall.has_device_id("1000abcde")


def test_switches_that_each_have_their_device_can_run_also_one_without_lights() -> None:
    """A switch without any light, as one migrated from version 1 can be, runs too: each of its presses fails, while
    the other switches toggle theirs (decision 32)."""
    EngineConfig(
        [
            SwitchSettings("1000abcdef", "Hall", None, [CEILING_ID]),
            SwitchSettings("1000999999", None, "a-device-key", [CEILING_ID, "light.porch"]),
            SwitchSettings("1000555555", "Attic", None, []),
        ]
    ).validate_for_run()


def test_without_switches_there_is_nothing_to_run() -> None:
    with pytest.raises(ConfigError) as err:
        EngineConfig().validate_for_run()

    assert str(err.value) == "Invalid configuration:\n  - No switches configured. Add a switch."


def test_a_device_id_is_configured_once_without_regard_to_case() -> None:
    config = EngineConfig(
        [
            SwitchSettings("1000abcdef", targets=[CEILING]),
            SwitchSettings(" 1000ABCDEF ", "Hall", targets=["light.desk"]),
        ]
    )

    with pytest.raises(ConfigError) as err:
        config.validate_for_run()

    assert str(err.value) == (
        "Invalid configuration:\n"
        "  - switches[1] ('Hall'): deviceId ' 1000ABCDEF ' is configured more than once; put all its lights in one "
        "switch."
    )


def test_every_switch_needs_a_device_id_and_no_blank_light() -> None:
    """Blank is empty or Python's white space, for a name too; every problem is listed, switch by switch."""
    config = EngineConfig(
        [
            SwitchSettings(" ", "\x1e", targets=["\x1f", ""]),
            SwitchSettings("1000999999", "Porch", targets=[CEILING, "\u3000"]),
        ]
    )

    with pytest.raises(ConfigError) as err:
        config.validate_for_run()

    assert str(err.value).splitlines() == [
        "Invalid configuration:",
        "  - switches[0]: deviceId is missing.",
        "  - switches[0]: empty light.",
        "  - switches[1] ('Porch'): empty light.",
    ]
