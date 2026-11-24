"""The .NET string and number rules that the engine's behavior depends on.

The .NET app compares names with ``StringComparison.OrdinalIgnoreCase``, trims with ``string.Trim()``, lowercases with
``ToLowerInvariant()`` and parses numbers with ``int.TryParse``/``long.TryParse``. Python's ``casefold()``,
``strip()``, ``lower()`` and ``int()`` differ in details (which characters count as white space, Unicode digits,
underscores in numbers, context-dependent case mappings), so the port uses these instead.
"""

import re

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


def _simple_upper(char: str) -> str:
    upper = char.upper()
    # .NET uses simple (one-to-one) case mappings; Python's full mappings turn 'ß' into 'SS'.
    return upper if len(upper) == 1 else char


def fold(value: str) -> str:
    """A key that makes ``OrdinalIgnoreCase`` comparisons plain comparisons."""
    if value.isascii():
        return value.upper()
    return "".join(_simple_upper(char) for char in value)


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
    """``string.ToLowerInvariant()``: per character, without Python's final-sigma rule or 'İ' → 'i̇'."""
    if value.isascii():
        return value.lower()
    return "".join("i" if char == "\u0130" else char.lower() for char in value)


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
