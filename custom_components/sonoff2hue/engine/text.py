"""The .NET string and number rules that the engine's behavior depends on.

The .NET app compares names with ``StringComparison.OrdinalIgnoreCase``, trims with ``string.Trim()``, lowercases with
``ToLowerInvariant()`` and parses numbers with ``int.TryParse``/``long.TryParse``. Python's ``casefold()``,
``strip()``, ``lower()`` and ``int()`` differ in details (which characters count as white space, Unicode digits,
underscores in numbers, context-dependent case mappings), so the port uses these instead.
"""

import re
import struct

# char.IsWhiteSpace: Unicode categories Zs, Zl and Zp, plus U+0009-U+000D and U+0085. Python's str.isspace() also
# counts U+001C-U+001F, which .NET doesn't trim.
_WHITE_SPACE = frozenset(
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

# NumberStyles.Integer with the invariant culture: optional leading and trailing white space (U+0009-U+000D and
# U+0020 only), an optional sign and ASCII digits. .NET also ignores trailing NUL characters.
_INTEGER = re.compile(r"[\t\n\v\f\r ]*([+-]?[0-9]+)[\t\n\v\f\r ]*\x00*")


def is_white_space(char: str) -> bool:
    """``char.IsWhiteSpace``."""
    return char in _WHITE_SPACE


def trim(value: str) -> str:
    """``string.Trim()``."""
    start, end = 0, len(value)
    while start < end and value[start] in _WHITE_SPACE:
        start += 1
    while end > start and value[end - 1] in _WHITE_SPACE:
        end -= 1
    return value[start:end]


def is_null_or_white_space(value: str | None) -> bool:
    """``string.IsNullOrWhiteSpace``."""
    return value is None or all(char in _WHITE_SPACE for char in value)


# Where .NET's invariant uppercase mapping differs from Python's one-to-one mappings, measured on .NET 10 with invariant
# globalization for every code point: .NET keeps U+0131 (dotless i) and U+017F (long s), which Python maps to ASCII, and
# maps 27 Greek letters with ypogegrammeni, which Python only maps to several characters.
_UPPER_EXCEPTIONS = {
    "\u0131": "\u0131",
    "\u017f": "\u017f",
    **{chr(c): chr(c + 8) for start in (0x1F80, 0x1F90, 0x1FA0) for c in range(start, start + 8)},
    **{chr(c): chr(c + 9) for c in (0x1FB3, 0x1FC3, 0x1FF3)},
}


def _upper_invariant(char: str) -> str:
    if (exception := _UPPER_EXCEPTIONS.get(char)) is not None:
        return exception
    upper = char.upper()
    # .NET maps one character to one character; Python's full mappings turn 'ß' into 'SS'.
    return upper if len(upper) == 1 else char


def fold(value: str) -> str:
    """``ToUpperInvariant()``, the key that makes ``OrdinalIgnoreCase`` comparisons plain comparisons."""
    if value.isascii():
        return value.upper()
    return "".join(_upper_invariant(char) for char in value)


def equals_ignore_case(left: str, right: str) -> bool:
    """``string.Equals(left, right, StringComparison.OrdinalIgnoreCase)``."""
    return len(left) == len(right) and fold(left) == fold(right)


def starts_with_ignore_case(value: str, prefix: str) -> bool:
    """``value.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)``."""
    return len(value) >= len(prefix) and fold(value[: len(prefix)]) == fold(prefix)


def ends_with_ignore_case(value: str, suffix: str) -> bool:
    """``value.EndsWith(suffix, StringComparison.OrdinalIgnoreCase)``."""
    return len(value) >= len(suffix) and fold(value[len(value) - len(suffix) :]) == fold(suffix)


def lower_invariant(value: str) -> str:
    """``string.ToLowerInvariant()``: per character, without Python's final-sigma rule, and 'İ' stays 'İ'."""
    if value.isascii():
        return value.lower()
    return "".join(lower if len(lower := char.lower()) == 1 else char for char in value)


def compare_ignore_case(left: str | None, right: str | None) -> int:
    """``StringComparer.OrdinalIgnoreCase.Compare``: negative, zero or positive, with null first. With invariant
    globalization .NET compares code points, each mapped as ``ToUpperInvariant`` maps it, then the UTF-16 lengths; a
    lone surrogate counts as itself. So a character outside the Basic Multilingual Plane sorts after U+FFFF, not between
    U+D7FF and U+E000 as its UTF-16 code units would."""
    if left is None or right is None:
        return (left is not None) - (right is not None)
    units_left, units_right = _utf16_units(left), _utf16_units(right)
    index = 0
    while index < min(len(units_left), len(units_right)):
        code_left, size = _scalar(units_left, index)
        code_right, _ = _scalar(units_right, index)
        if code_left != code_right:
            upper_left, upper_right = ord(fold(chr(code_left))), ord(fold(chr(code_right)))
            if upper_left != upper_right:
                return upper_left - upper_right
        index += size
    return len(units_left) - len(units_right)


def _utf16_units(value: str) -> tuple[int, ...]:
    data = value.encode("utf-16-le", "surrogatepass")
    return struct.unpack(f"<{len(data) // 2}H", data)


def _scalar(units: tuple[int, ...], index: int) -> tuple[int, int]:
    """The code point at ``index`` and its number of code units: two for a surrogate pair."""
    unit = units[index]
    if 0xD800 <= unit <= 0xDBFF and index < len(units) - 1 and 0xDC00 <= units[index + 1] <= 0xDFFF:
        return 0x10000 + ((unit - 0xD800) << 10) + (units[index + 1] - 0xDC00), 2
    return unit, 1


def utf16_length(value: str) -> int:
    """``string.Length``: characters outside the Basic Multilingual Plane count twice."""
    return len(value) + sum(1 for char in value if ord(char) > 0xFFFF)


def try_parse_int(value: str | None, bits: int) -> int | None:
    """``int.TryParse`` (32 bits) or ``long.TryParse`` (64 bits) with the invariant culture; ``None`` on failure."""
    if value is None:
        return None
    match = _INTEGER.fullmatch(value)
    if match is None:
        return None
    number = int(match.group(1))
    limit = 1 << (bits - 1)
    return number if -limit <= number < limit else None
