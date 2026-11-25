"""The YAML import: the reading parts of the .NET app's ConfigurationTests and the YAML of its QaRegressionTests, with
made-up ids and documentation addresses, and the port's own decisions (decision 15)."""

import pytest

from custom_components.sonoff2hue.engine import targets
from custom_components.sonoff2hue.engine.config import ConfigError, EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.hue_client import HueLight
from custom_components.sonoff2hue.engine.yaml_import import parse

# config/sonoff2hue.example.yaml of the .NET app, with made-up ids and a documentation address.
EXAMPLE = """# sonoff2hue configuration example.
#
# Keys are case-sensitive. Quote names that contain '#' or ': ' or start with a special character,
# e.g. "Spot #2".

# Filled in by `sonoff2hue hue-pair`.
hue:
  bridgeIp: 192.0.2.20
  bridgeId: 001788FFFE000001
  appKey: created-by-hue-pair
  certificateSha256: pinned-by-hue-pair

# One entry per MINIR4M. Find the deviceId with `sonoff2hue monitor`.
switches:
  - name: Living room wall switch
    deviceId: 1000abcdef
    # Optional eWeLink device key: lets sonoff2hue ignore relay and setting changes made in the eWeLink app.
    deviceKey: null
    # Hue light names (or ids) from `sonoff2hue hue-list`.
    lights:
      - Ceiling
      - Floor lamp
    groups: []

  - name: Hallway wall switch
    deviceId: "1000999999"
    lights: []
    # Rooms or zones; prefix with "room:" or "zone:" when a room and a zone share a name.
    groups:
      - room:Hallway
"""


def test_parse_reads_yaml_with_comments() -> None:
    config = parse(
        "# comments are fine\n"
        "hue:\n"
        "  bridgeIp: 192.0.2.20   # so are trailing ones\n"
        "  appKey: key\n"
        "switches:\n"
        "  - name: Hall\n"
        "    deviceId: 1000ABCDEF\n"
        "    lights: [Ceiling]\n"
        "    groups:\n"
        "      - room:Kitchen\n"
    )

    assert config.hue.bridge_ip == "192.0.2.20"
    (switch,) = config.switches
    assert switch.normalized_device_id == "1000abcdef"
    assert switch.groups == ["room:Kitchen"]
    assert config.find_switch("1000abcdef") is switch
    config.validate_for_run()


def test_example_config_is_valid() -> None:
    config = parse(EXAMPLE)

    config.validate_for_run()
    assert len(config.switches) == 2
    assert config.switches[0].device_key is None
    assert config.switches[1].device_id == "1000999999"


@pytest.mark.parametrize("yaml", ["", "# only a comment", "hue:\nswitches:"])
def test_parse_treats_empty_sections_as_empty(yaml: str) -> None:
    config = parse(yaml)

    assert config.hue.bridge_ip is None
    assert config.switches == []
    assert config.find_switch("1000abcdef") is None


def test_parse_treats_keys_without_value_as_empty() -> None:
    config = parse("switches:\n  - name: Hall\n    deviceId:\n    lights:\n    groups: [~]\n")

    (switch,) = config.switches
    assert switch.device_id == ""
    assert switch.lights == []
    assert switch.groups == [None]
    assert config.find_switch("1000abcdef") is None
    with pytest.raises(ConfigError) as err:
        config.validate_for_run()
    assert "('Hall'): deviceId is missing" in str(err.value)
    assert "('Hall'): empty light/group name" in str(err.value)


def test_parse_rejects_empty_switch_entry() -> None:
    with pytest.raises(ConfigError) as err:
        parse("switches:\n  - deviceId: a\n    lights: [x]\n  -\n")

    assert str(err.value) == "Config: switches[1] is empty. Remove the stray '-' or fill in the entry."


@pytest.mark.parametrize(
    ("yaml", "expected"),
    [
        (
            "hue:\n  bridgeIP: 1.2.3.4\n",
            "line 2, column 3: unknown key 'bridgeIP' under 'hue'. Valid keys are bridgeIp, bridgeId, appKey, "
            "certificateSha256",
        ),
        (
            "hue: {}\nswitchs: []\n",
            "line 2, column 1: unknown key 'switchs' at the top level. Valid keys are hue, switches",
        ),
        (
            "switches:\n  - deviceID: a\n",
            "line 2, column 5: unknown key 'deviceID' in a 'switches' entry. Valid keys are name, deviceId, deviceKey, "
            "lights, groups",
        ),
        (
            "switches:\n  - deviceId: a\n    normalizedDeviceId: b\n",
            "line 3, column 5: unknown key 'normalizedDeviceId'",
        ),
        ("hue:\n  appKey: a\n  appKey: b\n", "line 3, column 3: duplicate key 'appKey'"),
        ("switches: Hall\n", "line 1, column 11: expected a list here"),
        ("switches:\n  - deviceId: a\n    lights: Ceiling\n", "line 3, column 13: expected a list here"),
        ("hue:\n  bridgeIp: [1, 2]\n", "line 2, column 13: expected a single value here"),
        ("hue: [1, 2]\n", "line 1, column 6: expected 'key: value' lines here"),
        ("hue:\n  bridgeIp: 1\n appKey: 2\n", "line 3, column 2: "),
    ],
)
def test_parse_reports_yaml_errors_with_position(yaml: str, expected: str) -> None:
    """The .NET test reads a file, whose name the message gives; a pasted text is "Config", as ``AppConfig.Parse``
    calls a text that doesn't come from a file."""
    with pytest.raises(ConfigError) as err:
        parse(yaml)

    assert str(err.value).startswith("Config, ")
    assert expected in str(err.value)


def test_parse_reads_what_save_quoted() -> None:
    """The reading half of ``Save_QuotesValuesThatYamlWouldReadAsOtherTypes``: what ``AppConfig.Save`` writes."""
    names = ["true", "null", "Spot #2", "Desk: left", "1000e12345", "[x]"]
    lights = "".join(f'      - "{name}"\n' for name in names)

    config = parse(
        f'switches:\n  - name: yes\n    deviceId: "1000123456"\n    deviceKey: "0123"\n    lights:\n{lights}'
        "    groups: []\n"
    )

    (switch,) = config.switches
    assert (switch.name, switch.device_id, switch.device_key) == ("yes", "1000123456", "0123")
    assert switch.lights == names


def test_parse_reads_the_qa_regression_config() -> None:
    """The config of ``EncryptedPresses_ResolveAndToggleMixedTargetsWithoutFalsePresses``, which test_runner.py builds
    directly."""
    config = parse(
        "hue:\n"
        "  bridgeIp: bridge.test\n"
        "  appKey: qa-only-app-key\n"
        "switches:\n"
        "  - name: Hall\n"
        '    deviceId: " 1000ABCDEF "\n'
        "    deviceKey: qa-only-device-key\n"
        '    lights: [" ceiling ", l1, Desk]\n'
        '    groups: [" room:Hall ", r1]\n'
    )

    config.validate_for_run()
    assert config == EngineConfig(
        HueSettings("bridge.test", app_key="qa-only-app-key"),
        [
            SwitchSettings(
                " 1000ABCDEF ", "Hall", "qa-only-device-key", [" ceiling ", "l1", "Desk"], [" room:Hall ", "r1"]
            )
        ],
    )
    assert config.switches[0].normalized_device_id == "1000abcdef"


def test_scalars_stay_the_text_written() -> None:
    config = parse(
        "switches:\n  - deviceId: 1000e12345\n    name: yes\n    deviceKey: 0x10\n"
        "    lights: [true, 1.5, 0o17, .inf, 1_000, 12:30, 2001-12-14, ~, null, '~', \"null\"]\n"
    )

    (switch,) = config.switches
    assert (switch.device_id, switch.name, switch.device_key) == ("1000e12345", "yes", "0x10")
    assert switch.lights == ["true", "1.5", "0o17", ".inf", "1_000", "12:30", "2001-12-14", None, None, "~", "null"]


def test_columns_count_utf16_code_units() -> None:
    """A character outside the Basic Multilingual Plane counts as two columns, as in .NET's strings."""
    with pytest.raises(ConfigError) as err:
        parse("hue: {bridgeIp: \U0001f600\U0001f600, x: 1}\n")

    assert str(err.value).startswith("Config, line 1, column 23: unknown key 'x' under 'hue'.")


def test_a_document_of_a_scalar_is_read_again() -> None:
    """``AppConfig.Parse`` reads a top-level scalar's value as YAML again, through its own static ``Parse``."""
    assert parse('"hue: {bridgeIp: inner}"\n').hue.bridge_ip == "inner"
    with pytest.raises(ConfigError) as err:
        parse('"hue: [1]"\n')
    assert str(err.value) == "Config, line 1, column 1: Exception during deserialization"


@pytest.mark.parametrize("yaml", ["a\n", "'a'\n", "1", "|\n  |\n    x\n", "!!str a\n"])
def test_a_scalar_that_reads_as_itself_overflows_the_stack(yaml: str) -> None:
    """In .NET the recursion ends the process; the port reports the ``StackOverflowException``."""
    with pytest.raises(UnexpectedError) as err:
        parse(yaml)

    assert err.value.dotnet_type == "StackOverflowException"


@pytest.mark.parametrize(("tag", "column"), [("float", 15), ("timestamp", 15)])
def test_float_and_timestamp_tags_are_refused(tag: str, column: int) -> None:
    """.NET reads them with ``double.Parse`` and ``DateTime.Parse``, which the port doesn't reproduce."""
    with pytest.raises(ConfigError) as err:
        parse(f"switches:\n  - deviceId: !!{tag} 1\n")

    assert str(err.value) == (
        f"Config, line 2, column {column}: Values tagged !!{tag} are not supported. Write the value without the tag."
    )


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("!!caf%C3%A9", "line 2, column 15: Encountered an unresolved tag 'tag:yaml.org,2002:caf\u00e9'"),
        ("!<a+b>", "line 2, column 5: Exception during deserialization"),
        ("!!%F0%9F%98%80", "line 2, column 5: Exception during deserialization"),
    ],
)
def test_a_global_tag_has_to_be_a_well_formed_uri(tag: str, message: str) -> None:
    """YamlDotNet refuses a tag that doesn't start with '!' unless ``Uri.IsWellFormedUriString`` accepts it, after
    decoding its escapes and a '+' as a space. .NET's IRI rules take 'é' but not a character outside the Basic
    Multilingual Plane."""
    with pytest.raises(ConfigError) as err:
        parse(f"switches:\n  - deviceId: {tag} x\n")

    assert str(err.value) == f"Config, {message}"


@pytest.mark.parametrize(
    ("tag", "value"),
    [("!<ftp://host:80W/a>", "ftp://host:80W/a"), ("!<xy:c%7C/%C3%A9>", "xy:c|/é"), ("!<mailto:$>", "mailto:$")],
)
def test_tags_that_need_dotnets_host_parsing_are_approximated(tag: str, value: str) -> None:
    """Decision 15: for an authority, a DOS path after a scheme and mailto, the port checks what follows the scheme as
    a path, query and fragment. .NET 10's ``Uri.IsWellFormedUriString`` refuses these three values: a port that isn't
    a number, 'c|' as a drive with a non-ASCII character in the path, and '$' as a mail address. The port takes them."""
    with pytest.raises(ConfigError) as err:
        parse(f"switches:\n  - deviceId: {tag} x\n")

    assert str(err.value) == f"Config, line 2, column 15: Encountered an unresolved tag '{value}'"


def test_a_scanner_exception_outside_the_deserializer_is_unexpected() -> None:
    """YamlDotNet's scanner throws an ``InvalidOperationException`` for a flow scalar left of its indentation. Inside
    a node's deserialization YamlDotNet reports it as an error of that node; before it, it escapes."""
    with pytest.raises(ConfigError) as err:
        parse("switches:\n  - deviceId: a\n    lights: [Ceiling,\n    Floor]\n")
    assert str(err.value) == "Config, line 3, column 14: Exception during deserialization"

    with pytest.raises(UnexpectedError) as unexpected:
        parse("%TAG !e! \n---\nhue: {}\n")
    assert unexpected.value.dotnet_type == "ArgumentNullException"


def test_a_null_light_name_fails_target_resolution_as_in_dotnet() -> None:
    """A null entry reaches the resolver only without ``validate_for_run``; .NET's ``Trim()`` throws for it."""
    config = parse("switches:\n  - deviceId: 1000abcdef\n    lights: [Ceiling, ~]\n")

    with pytest.raises(UnexpectedError) as err:
        targets.resolve(config.switches[0], [HueLight("l1", "Ceiling", False)], [], [])
    assert err.value.dotnet_type == "NullReferenceException"
