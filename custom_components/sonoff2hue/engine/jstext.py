"""JavaScript's string rules, which the .NET app's web UI applied in the browser.

The switch editor (``targets.js``, ``views/switcheditor.js`` and ``format.js``) compares names with
``String(value ?? '').trim().toLowerCase()``. Those rules differ from .NET's (``text.py``) and from Python's own:

- ``trim()`` removes U+FEFF, which .NET's ``Trim()`` keeps, and keeps U+0085, which .NET removes. Python's ``strip()``
  also removes U+001C-U+001F and U+0085, and keeps U+FEFF.
- ``toLowerCase()`` uses Unicode's full mappings, so U+0130 'İ' becomes 'i' and U+0307, two characters, and a capital
  sigma at the end of a word becomes the final sigma U+03C2. .NET's ``ToLowerInvariant`` maps one character to one
  character, without the final-sigma rule, and ``OrdinalIgnoreCase`` compares uppercase forms. So U+1E9E and 'ß', the
  Kelvin sign U+212A and 'k', or U+0130 and 'i' with U+0307 are equal in JavaScript but not in .NET, while the final
  and the other small sigma, or the micro sign U+00B5 and the Greek small mu, are equal in .NET but not in JavaScript.
  Python's ``lower()`` is the same algorithm as JavaScript's, and Python 3.14 and Node 20.19 both have Unicode 16.0.
- ``length`` counts UTF-16 code units, as .NET's ``string.Length`` does (``text.utf16_length``).
"""

from . import text

# String.prototype.trim: WhiteSpace (U+0009, U+000B, U+000C, U+FEFF and the category Zs) and LineTerminator (U+000A,
# U+000D, U+2028 and U+2029).
_WHITE_SPACE = frozenset(
    "\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def string(value: str | None) -> str:
    """``String(value ?? '')`` for the values the port passes: a string, or ``None`` for null and undefined."""
    return "" if value is None else value


def is_white_space(char: str) -> bool:
    """Whether ``trim()`` removes the character."""
    return char in _WHITE_SPACE


def trim(value: str) -> str:
    """``String.prototype.trim``."""
    start, end = 0, len(value)
    while start < end and value[start] in _WHITE_SPACE:
        start += 1
    while end > start and value[end - 1] in _WHITE_SPACE:
        end -= 1
    return value[start:end]


def lower(value: str) -> str:
    """``String.prototype.toLowerCase``: Unicode's full lowercase mappings and the final-sigma rule, as ``str.lower``."""
    return value.lower()


def length(value: str) -> int:
    """``String.prototype.length``: UTF-16 code units."""
    return text.utf16_length(value)
