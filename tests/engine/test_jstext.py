"""Tests of JavaScript's string rules, against Node's own results (tools/js_oracle)."""

import json
from pathlib import Path
import unicodedata

import pytest

from custom_components.sonoff2hue.engine import jstext, text

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "js_picker.json").read_text(encoding="utf-8"))


def test_fixture_comes_from_the_app_with_pythons_unicode_version() -> None:
    assert _FIXTURE["app"].startswith("sonoff2hue v")
    # str.lower() stands in for toLowerCase(), which is only the same with the same Unicode version.
    assert _FIXTURE["unicode"] == ".".join(unicodedata.unidata_version.split(".")[:2])


def test_white_space_is_javascripts() -> None:
    actual = {c for c in range(0x110000) if jstext.is_white_space(chr(c))}

    assert actual == set(_FIXTURE["text"]["whiteSpace"])


def test_lower_is_javascripts_for_every_code_point() -> None:
    mappings = dict(_FIXTURE["text"]["lower"])

    for c in range(0x110000):
        if 0xD800 <= c <= 0xDFFF:
            continue
        char = chr(c)
        assert jstext.lower(char) == mappings.get(c, char), hex(c)


@pytest.mark.parametrize(("value", "expected"), _FIXTURE["trim"])
def test_trim_is_javascripts(value: str, expected: str) -> None:
    assert jstext.trim(value) == expected


@pytest.mark.parametrize(("value", "expected"), _FIXTURE["lower"])
def test_lower_is_javascripts_in_context(value: str, expected: str) -> None:
    assert jstext.lower(value) == expected


def test_javascript_trims_other_characters_than_dotnet_and_python() -> None:
    assert jstext.trim("\ufeffKitchen\ufeff") == "Kitchen"
    assert text.trim("\ufeffKitchen\ufeff") == "\ufeffKitchen\ufeff"
    assert "\ufeffKitchen\ufeff".strip() == "\ufeffKitchen\ufeff"

    assert jstext.trim("\x85Kitchen\x85") == "\x85Kitchen\x85"
    assert text.trim("\x85Kitchen\x85") == "Kitchen"

    assert jstext.trim("\x1cKitchen\x1f") == "\x1cKitchen\x1f"
    assert "\x1cKitchen\x1f".strip() == "Kitchen"


def test_javascript_lowercases_unlike_dotnet() -> None:
    assert jstext.lower("\u0130") == "i\u0307"
    assert text.lower_invariant("\u0130") == "\u0130"
    assert jstext.lower("\u039f\u0394\u039f\u03a3") == "\u03bf\u03b4\u03bf\u03c2"
    assert text.lower_invariant("\u039f\u0394\u039f\u03a3") == "\u03bf\u03b4\u03bf\u03c3"
    assert jstext.lower("\u1e9e") == jstext.lower("\u00df")
    assert not text.equals_ignore_case("\u1e9e", "\u00df")
    assert jstext.lower("\u212a") == "k"
    assert not text.equals_ignore_case("\u212a", "k")
    assert jstext.lower("\u00b5") != jstext.lower("\u03bc")
    assert text.equals_ignore_case("\u00b5", "\u03bc")


def test_string_and_length() -> None:
    assert jstext.string(None) == ""
    assert jstext.string("x") == "x"
    assert jstext.length("Lamp \U0001f600") == 7
