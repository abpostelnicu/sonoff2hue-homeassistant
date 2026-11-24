"""JSON as the .NET app reads it, with System.Text.Json's ``JsonNode``.

``json.loads`` differs from ``JsonNode.Parse``: it accepts ``NaN`` and ``Infinity``, nests deeper than 64 levels,
keeps the last of duplicate keys and turns ``1.0`` into a float. ``JsonNode.Parse`` rejects the first two, parses an
object with a duplicate key or a string with a lone surrogate but fails as soon as the code reads them, and keeps a
number's text. This module reproduces that. A parsed value is a ``dict``, ``list``, ``str``, ``JsonNumber``, ``bool`` or
``None``; ``BrokenObject`` and ``BrokenString`` stand for the values that fail when read.
"""

from collections.abc import Iterator, Mapping
from decimal import Decimal
from enum import Enum
import json
import re
from typing import NoReturn

MAX_DEPTH = 64  # JsonDocumentOptions.MaxDepth's default

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_INTEGER_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)")


class JsonError(ValueError):
    """The text isn't valid JSON (``JsonException`` in .NET)."""


class JsonAccessError(Exception):
    """Reading a value that .NET can parse but not read (``ArgumentException`` or ``InvalidOperationException``)."""


class JsonNumber:
    """A number with the text it was written as. Numbers are equal when their values are, as in
    ``JsonNode.DeepEquals``: ``1``, ``1.0`` and ``1e0`` are, ``1`` and ``1.0000000000000001`` aren't."""

    __slots__ = ("text", "value")

    def __init__(self, raw: str) -> None:
        self.text = raw
        self.value = Decimal(raw)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JsonNumber) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"JsonNumber({self.text})"


class BrokenObject(Mapping[str, "JsonValue"]):
    """An object with a duplicate key: .NET parses it but throws on the first access to any of its properties."""

    __slots__ = ("message",)

    def __init__(self, message: str) -> None:
        self.message = message

    def error(self) -> JsonAccessError:
        return JsonAccessError(self.message)

    def __getitem__(self, key: str) -> JsonValue:
        raise self.error()

    def __iter__(self) -> Iterator[str]:
        raise self.error()

    def __len__(self) -> int:
        raise self.error()

    def __contains__(self, key: object) -> bool:
        raise self.error()

    def __repr__(self) -> str:
        return f"BrokenObject({self.message!r})"


class BrokenString:
    """A string with a lone surrogate (written as an escape such as ``\\ud800``): .NET parses it as a string but throws
    when the code reads its value."""

    __slots__ = ("message", "raw")

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.message = _surrogate_error(raw)

    def error(self) -> JsonAccessError:
        return JsonAccessError(self.message)

    def __repr__(self) -> str:
        return f"BrokenString({self.raw!r})"


type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | JsonNumber | bool | BrokenObject | BrokenString | None


class JsonKind(Enum):
    """``JsonValueKind``."""

    OBJECT = "Object"
    ARRAY = "Array"
    STRING = "String"
    NUMBER = "Number"
    TRUE = "True"
    FALSE = "False"
    NULL = "Null"


def parse(text: str) -> JsonValue:
    """``JsonNode.Parse``; raises :class:`JsonError` for text that isn't valid JSON."""
    if _depth(text) > MAX_DEPTH:
        raise JsonError(f"The maximum configured depth of {MAX_DEPTH} has been exceeded.")
    try:
        value = json.loads(
            text,
            parse_float=JsonNumber,
            parse_int=JsonNumber,
            parse_constant=_reject_constant,
            object_pairs_hook=_object,
        )
    except ValueError as err:  # JSONDecodeError, or a rejected constant
        raise JsonError(str(err)) from err
    return _mark_broken_strings(value)


def kind(value: JsonValue) -> JsonKind:
    """``GetValueKind()``."""
    match value:
        case dict() | BrokenObject():
            return JsonKind.OBJECT
        case list():
            return JsonKind.ARRAY
        case str() | BrokenString():
            return JsonKind.STRING
        case JsonNumber():
            return JsonKind.NUMBER
        case True:
            return JsonKind.TRUE
        case False:
            return JsonKind.FALSE
        case _:
            return JsonKind.NULL


def is_object(value: JsonValue) -> bool:
    """``value is JsonObject``."""
    return isinstance(value, (dict, BrokenObject))


def is_value(value: JsonValue) -> bool:
    """``value is JsonValue``: a string, number or boolean. JSON null reads as no node at all in .NET."""
    return isinstance(value, (str, BrokenString, JsonNumber, bool))


def get(obj: Mapping[str, JsonValue], key: str) -> JsonValue:
    """``obj[key]``: the value, or ``None`` when the key is missing or the value is JSON null."""
    return obj.get(key)


def get_string(value: str | BrokenString) -> str:
    """``GetValue<string>()`` on a string value."""
    if isinstance(value, BrokenString):
        raise value.error()
    return value


def try_get_int32(value: JsonValue) -> int | None:
    """``TryGetValue<int>()``: only a number written as an integer within the 32-bit range."""
    if not isinstance(value, JsonNumber) or not _INTEGER_TEXT.fullmatch(value.text):
        return None
    number = int(value.text)
    return number if -(1 << 31) <= number < (1 << 31) else None


def deep_equals(left: JsonValue, right: JsonValue) -> bool:
    """``JsonNode.DeepEquals``: numbers by value, objects without regard to the order of their properties. Like .NET,
    it fails on a broken value, the left one first."""
    if isinstance(left, (BrokenObject, BrokenString)):
        raise left.error()
    if isinstance(right, (BrokenObject, BrokenString)):
        raise right.error()
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(deep_equals(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(deep_equals(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return left is right
    if isinstance(left, (str, JsonNumber)) and type(left) is type(right):
        return left == right
    return False


def to_json_string(value: str | BrokenString | JsonNumber | bool) -> str:
    """``ToJsonString()`` of a value: numbers as written, strings escaped as System.Text.Json's default encoder does."""
    match value:
        case bool():
            return "true" if value else "false"
        case JsonNumber():
            return value.text
        case BrokenString():
            raise value.error()
        case _:
            return '"' + _escape(value) + '"'


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"'{name[0]}' is an invalid start of a value.")


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue] | BrokenObject:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            return BrokenObject(f"An item with the same key has already been added. Key: {key} (Parameter 'key')")
        if _LONE_SURROGATE.search(key):
            return BrokenObject(_surrogate_error(key))
        result[key] = value
    return result


def _surrogate_error(value: str) -> str:
    """.NET's message for the first lone surrogate in a string (Python combines the valid pairs when parsing)."""
    match = _LONE_SURROGATE.search(value)
    if match is not None and match.group() >= "\udc00":
        return f"Cannot read invalid UTF-16 JSON text as string. Invalid surrogate value: '0x{ord(match.group()):04X}'."
    return "Cannot read incomplete UTF-16 JSON text as string with missing low surrogate."


def _mark_broken_strings(value: JsonValue) -> JsonValue:
    match value:
        case str() if _LONE_SURROGATE.search(value):
            return BrokenString(value)
        case dict():
            return {key: _mark_broken_strings(item) for key, item in value.items()}
        case list():
            return [_mark_broken_strings(item) for item in value]
        case _:
            return value


def _depth(text: str) -> int:
    """The deepest nesting of objects and arrays, outside strings."""
    depth = deepest = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif char in "]}":
            depth -= 1
    return deepest


# JavaScriptEncoder.Default, which System.Text.Json uses: printable ASCII stays, except the characters that are
# sensitive in HTML; everything else is written as \uXXXX, with short escapes for a few control characters.
_SHORT_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", "\\": "\\\\"}
_ESCAPED_ASCII = frozenset("\"&'+<>`")


def _escape(value: str) -> str:
    out: list[str] = []
    for char in value:
        if (short := _SHORT_ESCAPES.get(char)) is not None:
            out.append(short)
        elif " " <= char <= "~" and char not in _ESCAPED_ASCII:
            out.append(char)
        elif ord(char) > 0xFFFF:
            code = ord(char) - 0x10000
            out.append(f"\\u{0xD800 + (code >> 10):04X}\\u{0xDC00 + (code & 0x3FF):04X}")
        else:
            out.append(f"\\u{ord(char):04X}")
    return "".join(out)
