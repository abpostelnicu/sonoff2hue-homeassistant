"""The mDNS layer's string and number rules (engine/text.py): its white space and its case mappings, checked for every
code point against the tables in mdns_text.json, its case-insensitive comparisons and its integer parsing
(decision 30)."""

import json
from pathlib import Path

import pytest

from custom_components.sonoff2hue.engine import text

_TABLES = json.loads((Path(__file__).parent / "mdns_text.json").read_text(encoding="utf-8"))


def _code_points() -> range:
    return range(0x110000)


def test_white_space_set() -> None:
    expected = set(_TABLES["whiteSpace"])

    actual = {c for c in range(0x10000) if text.is_white_space(chr(c))}

    assert actual == expected
    assert text.trim(" \u3000a b\t\x85") == "a b"
    assert text.trim("\x1ca\x1c") == "\x1ca\x1c"
    assert text.is_null_or_white_space(None)
    assert text.is_null_or_white_space(" \u2028")
    assert not text.is_null_or_white_space("\u200b")


def test_case_mappings_for_every_code_point() -> None:
    upper = dict(_TABLES["upper"])
    lower = dict(_TABLES["lower"])

    for c in _code_points():
        if 0xD800 <= c <= 0xDFFF:
            continue
        char = chr(c)
        assert text.fold(char) == chr(upper.get(c, c)), hex(c)
        assert text.lower_invariant(char) == chr(lower.get(c, c)), hex(c)


def test_case_insensitive_comparisons() -> None:
    assert text.equals_ignore_case("eWeLink_1", "EWELINK_1")
    assert text.equals_ignore_case("Σ", "ς")
    assert not text.equals_ignore_case("\u0131", "i")  # dotless i
    assert not text.equals_ignore_case("ß", "SS")
    assert text.starts_with_ignore_case("eWelink_1000", "ewelink")
    assert not text.starts_with_ignore_case("ewe", "ewelink")
    assert text.ends_with_ignore_case("x._EWELINK._tcp.LOCAL", "._ewelink._tcp.local")
    assert text.lower_invariant("\u0130\u03a3A\u03c2") == "\u0130\u03c3a\u03c2"  # İΣAς: no final sigma rule


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
    ],
)
def test_try_parse_int_64_bits(value: str | None, expected: int | None) -> None:
    assert text.try_parse_int(value, 64) == expected


def test_try_parse_int_has_the_32_bit_range() -> None:
    assert text.try_parse_int("2147483647", 32) == 2147483647
    assert text.try_parse_int("2147483648", 32) is None
    assert text.try_parse_int("-2147483648", 32) == -2147483648


def test_utf16_length_counts_surrogate_pairs() -> None:
    assert text.utf16_length("a😀") == 3
