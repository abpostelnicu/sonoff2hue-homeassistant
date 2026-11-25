"""The mDNS layer's own string rules (decision 30): how dns.py compares names and TXT keys without regard to case and
counts a name's length, and how ewelink.py reads white space, device ids and ``seq``. The white space and the case
mappings are checked for every code point against the tables in mdns_text.json."""

import json
from pathlib import Path

import pytest

from custom_components.sonoff2hue.engine import dns, ewelink

_TABLES = json.loads((Path(__file__).parent / "mdns_text.json").read_text(encoding="utf-8"))


def _code_points() -> range:
    return range(0x110000)


def test_white_space_set() -> None:
    expected = set(_TABLES["whiteSpace"])

    actual = {c for c in range(0x10000) if ewelink._is_blank(chr(c))}

    assert actual == expected
    assert ewelink._trim(" \u3000a b\t\x85") == "a b"
    assert ewelink._trim("\x1ca\x1c") == "\x1ca\x1c"
    assert ewelink._is_blank(None)
    assert ewelink._is_blank("")
    assert ewelink._is_blank(" \u2028")
    assert not ewelink._is_blank("\u200b")


def test_case_mappings_for_every_code_point() -> None:
    upper = dict(_TABLES["upper"])
    lower = dict(_TABLES["lower"])

    for c in _code_points():
        if 0xD800 <= c <= 0xDFFF:
            continue
        char = chr(c)
        assert dns.case_key(char) == chr(upper.get(c, c)), hex(c)
        assert ewelink._lower(char) == chr(lower.get(c, c)), hex(c)
        # So against an ASCII pattern, such as "ewelink", only ASCII letters match without regard to case.
        assert c < 0x80 or not dns.case_key(char).isascii(), hex(c)


def test_case_insensitive_comparisons() -> None:
    assert dns.equals_ignoring_case("eWeLink_1", "EWELINK_1")
    assert dns.equals_ignoring_case("Σ", "ς")
    assert dns.equals_ignoring_case("\u00fc", "\u00dc")  # ü, Ü
    assert not dns.equals_ignoring_case("\u0131", "i")  # dotless i
    assert not dns.equals_ignoring_case("\u017f", "s")  # long s
    assert not dns.equals_ignoring_case("\u212a", "k")  # Kelvin sign
    assert not dns.equals_ignoring_case("ß", "SS")
    assert dns.equals_ignoring_case("ß", "ß")
    assert dns.starts_with_ignoring_case("eWelink_1000", "ewelink")
    assert not dns.starts_with_ignoring_case("ewe", "ewelink")
    assert dns.ends_with_ignoring_case("x._EWELINK._tcp.LOCAL", "._ewelink._tcp.local")
    assert not dns.ends_with_ignoring_case("local", "._ewelink._tcp.local")
    assert ewelink._lower("\u0130\u03a3A\u03c2") == "\u0130\u03c3a\u03c2"  # İΣAς: no final sigma rule


def test_txt_keys_compare_by_the_same_rule() -> None:
    record = dns.TxtRecord([("ID", "1"), ("\u00fcber", "2"), ("\u0131d", "3")])

    assert record["id"] == "1"
    assert record["\u00dcBER"] == "2"
    assert record["\u0131D"] == "3"
    assert "Id" in record
    assert list(record) == ["ID", "\u00fcber", "\u0131d"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12\0", 12),
        ("12 \0", 12),
        ("12\0 ", None),
        (" +5 ", 5),
        ("\t7\n", 7),
        ("+0", 0),
        ("1_0", None),
        ("٣", None),
        ("\u00a05", None),
        ("0x10", None),
        ("", None),
        ("-", None),
        (None, None),
        ("9223372036854775807", 9223372036854775807),
        ("9223372036854775808", None),
        ("-9223372036854775808", -9223372036854775808),
        ("-9223372036854775809", None),
    ],
)
def test_seq_is_a_signed_64_bit_decimal(value: str | None, expected: int | None) -> None:
    assert ewelink._parse_seq(value) == expected


def test_a_names_length_counts_utf16_code_units() -> None:
    assert dns._utf16_length("a😀") == 3
    assert dns._utf16_length("a.b") == 3
