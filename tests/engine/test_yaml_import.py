"""Reading a pasted sonoff2hue.yaml (decisions 22 and 30): the settings it gives, the nulls it normalizes, and the line
and column of each problem it refuses. Every test runs with PyYAML's C loader and again with its pure-Python one, which
the import falls back to when PyYAML was built without libyaml. Device ids and keys are made up."""

from collections.abc import Iterator
import importlib

import pytest
import yaml

from custom_components.sonoff2hue.engine import yaml_import
from custom_components.sonoff2hue.engine.config import ConfigError, EngineConfig, HueSettings, SwitchSettings


@pytest.fixture(autouse=True, params=["libyaml", "pure Python"])
def loader(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Loads the import again with PyYAML's C loader, or, with that hidden, with the one it falls back to."""
    if request.param == "libyaml" and not yaml.__with_libyaml__:
        pytest.skip("PyYAML was built without libyaml.")
    if request.param == "pure Python":
        monkeypatch.delattr(yaml, "CSafeLoader")
    importlib.reload(yaml_import)
    yield request.param
    monkeypatch.undo()
    importlib.reload(yaml_import)


def parse(text: str) -> EngineConfig:
    return yaml_import.parse(text)


def error_of(text: str) -> str:
    with pytest.raises(ConfigError) as error:
        yaml_import.parse(text)
    return str(error.value)


EXAMPLE = """\
# The Hue bridge, as pairing filled it in.
hue:
  bridgeIp: 192.0.2.20
  bridgeId: 001788FFFE000001
  appKey: made-up-app-key
  certificateSha256: made-up-pin

# One entry per MINIR4M.
switches:
  - name: Living room wall switch
    deviceId: 1000abcdef
    deviceKey: null   # optional
    lights:
      - Ceiling
      - Floor lamp
    groups: []

  - name: "Spot #2"
    deviceId: '1000999999'
    deviceKey: made-up-device-key
    groups: [room:Hallway, "zone:Upstairs"]
"""


def test_the_c_loader_reads_the_text_when_pyyaml_has_libyaml(loader: str) -> None:
    assert issubclass(yaml_import._Loader, yaml.CSafeLoader if loader == "libyaml" else yaml.SafeLoader)


def test_a_whole_file_with_comments_quotes_and_both_styles() -> None:
    config = parse(EXAMPLE)

    assert config == EngineConfig(
        hue=HueSettings(
            bridge_ip="192.0.2.20",
            bridge_id="001788FFFE000001",
            app_key="made-up-app-key",
            certificate_sha256="made-up-pin",
        ),
        switches=[
            SwitchSettings(
                device_id="1000abcdef",
                name="Living room wall switch",
                device_key=None,
                lights=["Ceiling", "Floor lamp"],
                groups=[],
            ),
            SwitchSettings(
                device_id="1000999999",
                name="Spot #2",
                device_key="made-up-device-key",
                lights=[],
                groups=["room:Hallway", "zone:Upstairs"],
            ),
        ],
    )
    config.validate_for_run()


def test_values_are_kept_as_written() -> None:
    config = parse(
        "hue:\n"
        "  bridgeIp: ' bridge.test '\n"
        "switches:\n"
        "  - name: |\n"
        "      Hall\n"
        '    deviceId: " 1000ABCDEF "\n'
        '    lights: [" ceiling ", Desk]\n'
    )

    assert config.hue.bridge_ip == " bridge.test "
    (switch,) = config.switches
    assert (switch.name, switch.device_id, switch.lights) == ("Hall\n", " 1000ABCDEF ", [" ceiling ", "Desk"])


@pytest.mark.parametrize(
    "value",
    [
        "yes",
        "no",
        "on",
        "off",
        "y",
        "True",
        "false",
        "1",
        "-1",
        "0x10",
        "010",
        "0o17",
        "0b101",
        "1_000",
        "1e3",
        "1.5",
        ".inf",
        ".NaN",
        "12:30",
        "190:20:30",
        "2001-12-14",
        "2001-12-14t21:59:43.10-05:00",
        "2001-12-14 21:59:43.10 -5",
        "<<",
        "=",
    ],
)
def test_plain_values_other_than_null_stay_the_text_written(value: str) -> None:
    config = parse(f"hue:\n  appKey: {value}\nswitches:\n  - deviceId: {value}\n    lights: [{value}]\n")

    assert config.hue.app_key == value
    (switch,) = config.switches
    assert (switch.device_id, switch.lights) == (value, [value])


@pytest.mark.parametrize("value", ["", "~", "null", "Null", "NULL"])
def test_plain_nulls_are_none(value: str) -> None:
    config = parse(f"hue:\n  appKey: {value}\nswitches:\n  - name: {value}\n    deviceKey: {value}\n")

    assert config.hue.app_key is None
    (switch,) = config.switches
    assert (switch.name, switch.device_key) == (None, None)


def test_quoted_and_tagged_nulls_stay_text() -> None:
    config = parse(
        "switches:\n  - name: '~'\n    deviceId: \"null\"\n    deviceKey: !!str\n    lights: [!!str ~, nULL]\n"
    )

    (switch,) = config.switches
    assert (switch.name, switch.device_id, switch.device_key, switch.lights) == ("~", "null", "", ["~", "nULL"])


@pytest.mark.parametrize("text", ["", "# only a comment\n", "~\n", "null\n", "---\n", "--- # nothing\n...\n"])
def test_a_text_without_settings_is_empty(text: str) -> None:
    assert parse(text) == EngineConfig()


@pytest.mark.parametrize("text", ["hue:\nswitches:\n", "hue: ~\nswitches: null\n", "hue: {}\nswitches: []\n"])
def test_null_sections_are_empty(text: str) -> None:
    assert parse(text) == EngineConfig()


def test_null_values_of_a_switch_are_empty() -> None:
    config = parse("switches:\n  - name:\n    deviceId:\n    deviceKey: ~\n    lights:\n    groups: null\n  - {}\n")

    assert config.switches == [SwitchSettings(device_id="", lights=[], groups=[]), SwitchSettings()]


def test_a_null_light_or_group_stays_and_the_run_refuses_it() -> None:
    config = parse(
        "switches:\n  - name: Hall\n    deviceId: 1000abcdef\n    lights: [Ceiling, ~]\n    groups: [null]\n"
    )

    (switch,) = config.switches
    assert (switch.lights, switch.groups) == (["Ceiling", None], [None])
    with pytest.raises(ConfigError) as error:
        config.validate_for_run()
    assert "switches[0] ('Hall'): empty light/group name." in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "switches:\n  - deviceId: 1000abcdef\n    lights: [Ceiling]\n  -\n",
        "switches:\n  - deviceId: 1000abcdef\n    lights: [Ceiling]\n  - ~\n",
        "switches: [{deviceId: 1000abcdef, lights: [Ceiling]}, null]\n",
    ],
)
def test_an_empty_switch_entry_is_refused(text: str) -> None:
    assert error_of(text) == "Config: switches[1] is empty. Remove the stray '-' or fill in the entry."


HUE_KEYS = "Valid keys are bridgeIp, bridgeId, appKey, certificateSha256 (case-sensitive)."
TOP_KEYS = "Valid keys are hue, switches (case-sensitive)."
SWITCH_KEYS = "Valid keys are name, deviceId, deviceKey, lights, groups (case-sensitive)."


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("hue:\n  bridgeIP: 192.0.2.20\n", f"line 2, column 3: unknown key 'bridgeIP' under 'hue'. {HUE_KEYS}"),
        ("hue: {}\nswitchs: []\n", f"line 2, column 1: unknown key 'switchs' at the top level. {TOP_KEYS}"),
        ("Hue: {}\n", f"line 1, column 1: unknown key 'Hue' at the top level. {TOP_KEYS}"),
        ("'hue ': {}\n", f"line 1, column 1: unknown key 'hue ' at the top level. {TOP_KEYS}"),
        ("~: {}\n", f"line 1, column 1: unknown key '~' at the top level. {TOP_KEYS}"),
        ("1: {}\n", f"line 1, column 1: unknown key '1' at the top level. {TOP_KEYS}"),
        (
            "switches:\n  - deviceID: 1000abcdef\n",
            f"line 2, column 5: unknown key 'deviceID' in a 'switches' entry. {SWITCH_KEYS}",
        ),
        (
            "switches:\n  - deviceId: 1000abcdef\n    normalizedDeviceId: 1000abcdef\n",
            f"line 3, column 5: unknown key 'normalizedDeviceId' in a 'switches' entry. {SWITCH_KEYS}",
        ),
        (
            "hue:\n  switches: []\n",
            f"line 2, column 3: unknown key 'switches' under 'hue'. {HUE_KEYS}",
        ),
        (
            "hue: {bridgeIp: \U0001f600\U0001f600, x: 1}\n",
            f"line 1, column 21: unknown key 'x' under 'hue'. {HUE_KEYS}",
        ),
    ],
    ids=[
        "hue",
        "top level",
        "letter case",
        "a space",
        "a null key",
        "a number",
        "a switch",
        "a switch's derived value",
        "a section under another",
        "columns count characters",
    ],
)
def test_an_unknown_key_names_its_section_and_the_valid_keys(text: str, message: str) -> None:
    assert error_of(text) == f"Config, {message}"


def test_keys_match_whatever_their_style() -> None:
    config = parse("'hue': {\"bridgeIp\": 192.0.2.20}\n!!str switches:\n  - ? deviceId\n    : 1000abcdef\n")

    assert config.hue.bridge_ip == "192.0.2.20"
    assert config.switches == [SwitchSettings(device_id="1000abcdef")]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("hue:\n  appKey: a\n  appKey: b\n", "line 3, column 3: duplicate key 'appKey'."),
        ("hue: {}\nswitches: []\nhue: {}\n", "line 3, column 1: duplicate key 'hue'."),
        ("'hue': {}\nhue: {}\n", "line 2, column 1: duplicate key 'hue'."),
        ("switches:\n  - {deviceId: a, lights: [x], deviceId: b}\n", "line 2, column 32: duplicate key 'deviceId'."),
        ("switches:\n  - lights: [{a: 1, a: 2}]\n", "line 2, column 21: duplicate key 'a'."),
        ("~: a\nnull: b\n", "line 2, column 1: duplicate key 'null'."),
    ],
    ids=["hue", "top level", "quoted and plain", "a switch", "a mapping in the wrong place", "two nulls"],
)
def test_a_duplicate_key_is_refused_where_it_repeats(text: str, message: str) -> None:
    assert error_of(text) == f"Config, {message}"


def test_a_duplicate_key_comes_before_a_problem_of_the_shape() -> None:
    text = "switchs: []\nhue:\n  appKey: a\n  appKey: b\n"

    assert error_of(text) == "Config, line 4, column 3: duplicate key 'appKey'."


SINGLE = "expected a single value here, not a list or 'key: value' lines."
LIST = "expected a list here: '- item' lines, or [a, b]."
MAPPING = "expected 'key: value' lines here."


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("switches: Hall\n", f"line 1, column 11: {LIST}"),
        ("switches:\n  name: Hall\n", f"line 2, column 3: {LIST}"),
        ("switches:\n  - deviceId: a\n    lights: Ceiling\n", f"line 3, column 13: {LIST}"),
        ("switches:\n  - groups: {room: Hall}\n", f"line 2, column 13: {LIST}"),
        ("hue: [192.0.2.20]\n", f"line 1, column 6: {MAPPING}"),
        ("hue: 192.0.2.20\n", f"line 1, column 6: {MAPPING}"),
        ("switches: [Hall]\n", f"line 1, column 12: {MAPPING}"),
        ("switches:\n  - - Hall\n", f"line 2, column 5: {MAPPING}"),
        ("hue:\n  bridgeIp: [1, 2]\n", f"line 2, column 13: {SINGLE}"),
        ("switches:\n  - name:\n      first: Hall\n", f"line 3, column 7: {SINGLE}"),
        ("switches:\n  - lights: [Ceiling, [Floor lamp]]\n", f"line 2, column 23: {SINGLE}"),
        ("? [hue]\n: {}\n", f"line 1, column 3: {SINGLE}"),
    ],
    ids=[
        "a value for switches",
        "key-value lines for switches",
        "a value for lights",
        "key-value lines for groups",
        "a list for hue",
        "a value for hue",
        "a value for a switch",
        "a list for a switch",
        "a list for a value",
        "key-value lines for a value",
        "a list for a light",
        "a list for a key",
    ],
)
def test_a_value_of_the_wrong_kind_is_refused_where_it_is(text: str, message: str) -> None:
    assert error_of(text) == f"Config, {message}"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "switches:\n  - deviceId: a\n    lights: Ceiling\nhue: [192.0.2.20]\nswitchs: []\n",
            f"line 3, column 13: {LIST}",
        ),
        (
            "hue: [192.0.2.20]\nswitches:\n  - deviceId: a\n    lights: Ceiling\nswitchs: []\n",
            f"line 1, column 6: {MAPPING}",
        ),
        (
            "switchs: []\nhue: [192.0.2.20]\nswitches:\n  - deviceId: a\n    lights: Ceiling\n",
            f"line 1, column 1: unknown key 'switchs' at the top level. {TOP_KEYS}",
        ),
    ],
    ids=["in a switch", "in hue", "at the top level"],
)
def test_of_several_problems_of_the_shape_the_first_in_the_text_is_reported(text: str, message: str) -> None:
    assert error_of(text) == f"Config, {message}"


@pytest.mark.parametrize(
    ("text", "position"),
    [
        ("a\n", "line 1, column 1"),
        ("- hue\n", "line 1, column 1"),
        ("'hue: {bridgeIp: 192.0.2.20}'\n", "line 1, column 1"),
        ("1\n", "line 1, column 1"),
        ("# a comment\n|\n  hue:\n", "line 2, column 1"),
        ("--- [hue, switches]\n", "line 1, column 5"),
    ],
    ids=["a word", "a list", "a quoted mapping", "a number", "a block of text", "a flow list"],
)
def test_a_document_that_is_not_key_value_lines_is_refused(text: str, position: str) -> None:
    assert error_of(text) == f"Config, {position}: {MAPPING}"


ALIASES = """\
hue:
  bridgeIp: &address 192.0.2.20
switches:
  - &hall
    name: Hall
    deviceId: 1000aaaaaa
    lights: &downstairs [Ceiling, Floor lamp]
  - name: Stairs
    deviceId: 1000bbbbbb
    lights: *downstairs
    groups: [*address]
  - *hall
"""


def test_anchors_and_aliases_repeat_what_they_name() -> None:
    config = parse(ALIASES)

    hall = SwitchSettings(device_id="1000aaaaaa", name="Hall", lights=["Ceiling", "Floor lamp"])
    stairs = SwitchSettings(
        device_id="1000bbbbbb", name="Stairs", lights=["Ceiling", "Floor lamp"], groups=["192.0.2.20"]
    )
    assert config == EngineConfig(hue=HueSettings(bridge_ip="192.0.2.20"), switches=[hall, stairs, hall])


def test_a_merge_key_is_an_unknown_key() -> None:
    text = "switches:\n  - &hall\n    deviceId: 1000aaaaaa\n    lights: [Ceiling]\n  - <<: *hall\n    deviceId: 1000bbbbbb\n"

    assert error_of(text) == f"Config, line 5, column 5: unknown key '<<' in a 'switches' entry. {SWITCH_KEYS}"


def test_an_alias_without_its_anchor_is_refused() -> None:
    assert error_of("switches:\n  - lights: *nowhere\n").startswith("Config, line 2, column 13: found undefined alias")


def test_a_list_that_holds_itself_is_refused_at_its_anchor() -> None:
    text = "switches:\n  - deviceId: a\n    lights: &l [Ceiling, *l]\n"

    assert error_of(text) == f"Config, line 3, column 13: {SINGLE}"


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("hue: {}\n---\nswitches: []\n", 2),
        ("--- \nhue: {}\n...\n---\nswitches: []\n", 4),
        ("hue: {}\n--- [unclosed\n", 2),
    ],
    ids=["after an implicit document", "after an explicit end", "before a syntax error in it"],
)
def test_a_second_document_is_refused(text: str, line: int) -> None:
    message = "only one document is allowed, but another one starts here."

    assert error_of(text) == f"Config, line {line}, column 1: {message}"


@pytest.mark.parametrize("text", ["--- \nhue: {}\n", "hue: {}\n...\n", "%YAML 1.1\n---\nhue: {}\n...\n"])
def test_one_document_with_markers_is_read(text: str) -> None:
    assert parse(text) == EngineConfig()


@pytest.mark.parametrize(
    ("text", "position"),
    [
        ("hue:\n  appKey: 'made-up\n", "line 3, column 1"),
        ('hue:\n  appKey: "made-up\n', "line 3, column 1"),
        ("hue:\n  bridgeIp: 192.0.2.20\n appKey: made-up\n", "line 3, column 2"),
        ("hue:\n  appKey: a: b\n", "line 2, column 12"),
        ("switches: [Hall,\n", "line 2, column 1"),
        ("hue: {}}\n", "line 1, column 8"),
        ("switches:\n  - lights: [a]\n  groups: [b]\n", "line 3, column 3"),
    ],
    ids=[
        "an unclosed single quote",
        "an unclosed double quote",
        "a key indented less",
        "a colon in a plain value",
        "an unclosed list",
        "a stray brace",
        "a key outside its entry",
    ],
)
def test_a_syntax_error_names_its_line_and_column(text: str, position: str) -> None:
    prefix = f"Config, {position}: "

    message = error_of(text)

    assert message.startswith(prefix)
    assert message.removeprefix(prefix)  # what PyYAML found there


def test_a_syntax_error_says_what_pyyaml_found() -> None:
    assert error_of("hue:\n  appKey: 'made-up\n") == "Config, line 3, column 1: found unexpected end of stream"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("!!python/object/apply:os.system ['true']", "!!python/object/apply:os.system"),
        ("!!python/name:os.system", "!!python/name:os.system"),
        ("!!python/str a", "!!python/str"),
        ("!!int 1", "!!int"),
        ("!!float 1", "!!float"),
        ("!!bool yes", "!!bool"),
        ("!!timestamp 2001-12-14", "!!timestamp"),
        ("!!binary aGk=", "!!binary"),
        ("!!null ~", "!!null"),
        ("!!seq [a]", "!!seq"),
        ("!!map {a: b}", "!!map"),
        ("!!set {a}", "!!set"),
        ("!!str [a]", "!!str"),
        ("!custom a", "!custom"),
        ("! a", "!"),
        ("!<tag:example.com,2000:key> a", "tag:example.com,2000:key"),
    ],
)
def test_a_tag_other_than_str_on_a_value_is_refused(value: str, shown: str) -> None:
    assert error_of(f"hue:\n  appKey: {value}\n") == (
        f"Config, line 2, column 11: the tag {shown} isn't supported here. Write the value without the tag."
    )


def test_a_tag_through_a_tag_directive_is_refused() -> None:
    text = "%TAG !e! tag:example.com,2000:\n---\nhue:\n  appKey: !e!key a\n"

    assert error_of(text) == (
        "Config, line 4, column 11: the tag tag:example.com,2000:key isn't supported here. Write the value without "
        "the tag."
    )


def test_a_tag_on_a_key_or_a_section_is_refused() -> None:
    message = "isn't supported here. Write the value without the tag."
    assert error_of("!!int 1: a\n") == f"Config, line 1, column 1: the tag !!int {message}"
    assert error_of("hue: !!map\n  appKey: a\n") == f"Config, line 1, column 6: the tag !!map {message}"


def test_the_str_tag_keeps_the_text() -> None:
    config = parse("!!str hue:\n  appKey: !!str 0x10\nswitches:\n  - deviceId: !!str 1000abcdef\n")

    assert config.hue.app_key == "0x10"
    assert config.switches == [SwitchSettings(device_id="1000abcdef")]


def test_a_tab_cannot_indent() -> None:
    assert error_of("hue:\n\tappKey: made-up\n").startswith("Config, line 2, column 1: ")


def test_a_quoted_value_keeps_its_tab() -> None:
    config = parse("switches:\n  - name: 'Hall\tway'\n    deviceKey: \"made\\tup\"\n")

    (switch,) = config.switches
    assert (switch.name, switch.device_key) == ("Hall\tway", "made\tup")


def test_crlf_line_breaks_read_as_line_feeds() -> None:
    assert parse(EXAMPLE.replace("\n", "\r\n")) == parse(EXAMPLE)
    assert error_of("hue:\r\n  appKey: a\r\n  appKey: b\r\n") == "Config, line 3, column 3: duplicate key 'appKey'."


def test_a_byte_order_mark_at_the_start_is_skipped() -> None:
    assert parse("\ufeff" + EXAMPLE) == parse(EXAMPLE)
    assert error_of("\ufeffhu: {}\n") == f"Config, line 1, column 1: unknown key 'hu' at the top level. {TOP_KEYS}"


@pytest.mark.parametrize(
    ("text", "start"),
    [
        ("hue:\n  appKey: made\x07up\n", "Config, line 2, column 15: unacceptable character #x0007: "),
        ("hue:\n  bridgeId: caf\u00e9\x1b\n", "Config, line 2, column 17: unacceptable character #x001b: "),
        ("hue:\r\n  appKey: \u2028\x00\n", "Config, line 3, column 1: unacceptable character #x0000: "),
        ("hue:\n  appKey: made\ud800up\n", "Config, line 2, column 15: unacceptable character #xd800: "),
    ],
    ids=["a control character", "after a character of two bytes", "after a line separator", "a lone surrogate"],
)
def test_a_character_that_yaml_does_not_allow_is_refused_where_it_is(text: str, start: str) -> None:
    assert error_of(text).startswith(start)


def test_nesting_deeper_than_32_levels_is_refused() -> None:
    within = "x: " + "[" * 31 + "]" * 31 + "\n"
    deeper = "x: " + "[" * 32 + "]" * 32 + "\n"

    assert error_of(within) == f"Config, line 1, column 1: unknown key 'x' at the top level. {TOP_KEYS}"
    assert error_of(deeper) == "Config, line 1, column 35: the text nests more than 32 levels deep here."
