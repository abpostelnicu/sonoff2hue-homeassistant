"""Whether a global YAML tag is a well-formed URI, as YamlDotNet's ``TagName`` requires: a port of
``Uri.IsWellFormedUriString(value, UriKind.RelativeOrAbsolute)`` (System.Private.Uri, release/10.0) on Linux, on UTF-16
code units like .NET.

It follows .NET's code for relative references, implicit file paths, and every ``scheme:`` without an authority, such
as the tags of the ``!!`` handle (``tag:yaml.org,2002:str``), with the IRI escaping and unescaping that a non-ASCII
character or a percent-encoded one starts. Three forms would need .NET's host parsing (DNS names, IDN, IPv4 and IPv6
addresses, ports, user info): an authority (``scheme://host``), a DOS path after a scheme (``xy:c:/``) and ``mailto:``.
For those it applies the check of a URI without an authority to everything after the scheme (decision 15).
"""

from dataclasses import dataclass
from enum import Flag, auto

from . import text

_SCHEME_LENGTH_LIMIT = 1024
_SCHEME_CHARS = frozenset("+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_UNRESERVED = frozenset("-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz~")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_HIGH_HEX_DIGITS = frozenset("89abcdefABCDEF")
_RESERVED_MARKS = frozenset(";/?:@&=+$,#[]!'()*")
_NOT_SAFE_FOR_UNESCAPE = _RESERVED_MARKS | frozenset("%\\#")
_EOL = "\ufffe"  # c_EOL, CheckCanonical's "no delimiter"
_DUMMY = "\uffff"  # c_DummyChar, also what DecodeHexChars returns for a non-hex digit


class _Syntax(Flag):
    """The ``UriSyntaxFlags`` that the check reads."""

    MUST_HAVE_AUTHORITY = auto()
    OPTIONAL_AUTHORITY = auto()
    MAY_HAVE_QUERY = auto()
    MAY_HAVE_FRAGMENT = auto()
    ALLOW_EMPTY_HOST = auto()
    ALLOW_DOS_PATH = auto()
    FILE_LIKE = auto()
    MAIL_TO_LIKE = auto()


_QUERY_AND_FRAGMENT = _Syntax.MAY_HAVE_QUERY | _Syntax.MAY_HAVE_FRAGMENT
_HTTP = _Syntax.MUST_HAVE_AUTHORITY | _QUERY_AND_FRAGMENT
_NEWS = _Syntax.MAY_HAVE_FRAGMENT
_FILE = _HTTP | _Syntax.ALLOW_EMPTY_HOST | _Syntax.ALLOW_DOS_PATH | _Syntax.FILE_LIKE
_UNKNOWN = _Syntax.OPTIONAL_AUTHORITY | _QUERY_AND_FRAGMENT | _Syntax.ALLOW_EMPTY_HOST | _Syntax.ALLOW_DOS_PATH
_KNOWN_SCHEMES = {
    "WS": _HTTP,
    "WSS": _HTTP,
    "FTP": _Syntax.MUST_HAVE_AUTHORITY | _Syntax.MAY_HAVE_FRAGMENT,
    "HTTP": _HTTP,
    "FILE": _FILE,
    "UUID": _NEWS,
    "NNTP": _Syntax.MUST_HAVE_AUTHORITY | _Syntax.MAY_HAVE_FRAGMENT,
    "LDAP": _HTTP | _Syntax.ALLOW_EMPTY_HOST,
    "NEWS": _NEWS,
    "HTTPS": _HTTP,
    "MAILTO": _QUERY_AND_FRAGMENT | _Syntax.ALLOW_EMPTY_HOST | _Syntax.MAIL_TO_LIKE,
    "GOPHER": _Syntax.MUST_HAVE_AUTHORITY | _Syntax.MAY_HAVE_FRAGMENT,
    "TELNET": _Syntax.MUST_HAVE_AUTHORITY | _Syntax.MAY_HAVE_FRAGMENT,
    "NET.TCP": _HTTP,
    "NET.PIPE": _HTTP,
}


class _Check(Flag):
    """``Uri.Check``: what ``CheckCanonical`` found."""

    NONE = 0
    ESCAPED_CANONICAL = auto()
    DISPLAY_CANONICAL = auto()
    BACKSLASH_IN_PATH = auto()
    RESERVED_FOUND = auto()
    NOT_IRI_CANONICAL = auto()
    FOUND_NON_ASCII = auto()


_IRI_CANONICAL = _Check.DISPLAY_CANONICAL | _Check.FOUND_NON_ASCII


class _Fatal(Exception):  # noqa: N818
    """A parsing error after which .NET makes no URI at all, not even a relative one."""


@dataclass(frozen=True)
class _Scheme:
    """What ``ParseScheme`` found: the syntax and the index after the scheme, or an implicit file path."""

    syntax: _Syntax
    index: int
    implicit_file: bool = False
    unix_path: bool = False
    dos_path: bool = False


def is_well_formed_uri_string(value: str) -> bool:
    """``Uri.IsWellFormedUriString(value, UriKind.RelativeOrAbsolute)`` for a text in UTF-16 code units."""
    try:
        scheme = _parse_scheme(value)
    except _Fatal:
        return False
    if scheme is None:
        return _relative_is_well_formed(value)
    if scheme.implicit_file:
        # InitializeUri: a relative URI wins over a Unix path, and over a UNC path unless it starts with "\\"; "c:\"
        # and "\\server" are absolute file URIs, never well-formed.
        relative = not scheme.dos_path and len(value) >= 2 and (value[0] != "\\" or value[1] != "\\")
        return _relative_is_well_formed(value) if relative or scheme.unix_path else False
    try:
        return _absolute_is_well_formed(value, scheme.syntax, scheme.index)
    except _Fatal:
        return False


def _parse_scheme(value: str) -> _Scheme | None:
    """``ParseScheme`` on Linux, or ``None`` for an error after which the text can still be a relative URI."""
    if not value:
        return None
    if text.starts_with_ignore_case(value, "https:"):
        return _Scheme(_KNOWN_SCHEMES["HTTPS"], 6)
    if text.starts_with_ignore_case(value, "http:"):
        return _Scheme(_KNOWN_SCHEMES["HTTP"], 5)

    i = 0
    while i < len(value) and _is_lws(value[i]):
        i += 1
    if i < len(value) and value[i] == "/" and (i + 1 >= len(value) or value[i + 1] not in "/\\"):
        return _Scheme(_FILE, i, implicit_file=True, unix_path=True)

    colon = value.find(":", i)
    if i + 2 >= len(value) or colon == i:
        return None  # BadFormat
    if value[i + 1] in ":|":
        if _is_ascii_letter(value[i]):
            if value[i + 2] in "\\/":
                return _Scheme(_FILE, i, implicit_file=True, dos_path=True)
            raise _Fatal  # MustRootedPath
        return None  # BadScheme or BadFormat
    if value[i] in "/\\":
        if value[i + 1] in "\\/":
            return _Scheme(_FILE, i, implicit_file=True)  # a UNC path
        return None  # BadFormat
    if colon < 0:
        return None  # BadFormat

    name = value[i:colon]
    syntax = _KNOWN_SCHEMES.get(text.fold(name))
    if syntax is not None:
        return _Scheme(syntax, colon + 1)
    if not _is_ascii_letter(name[0]) or any(char not in _SCHEME_CHARS for char in name):
        return None  # BadScheme
    if len(name) > _SCHEME_LENGTH_LIMIT:
        raise _Fatal  # SchemeLimit
    return _Scheme(_UNKNOWN, colon + 1)


def _absolute_is_well_formed(value: str, syntax: _Syntax, index: int) -> bool:
    """``PrivateParseMinimal`` up to the authority, then ``InternalIsWellFormedOriginalString``."""
    length = len(value)
    while length > index and _is_lws(value[length - 1]):
        length -= 1

    if (_Syntax.ALLOW_EMPTY_HOST | _Syntax.ALLOW_DOS_PATH) in syntax and index + 1 < length:
        i = index
        while i < length and value[i] in "\\/":
            i += 1
        if _Syntax.FILE_LIKE in syntax or i - index <= 3:
            if i + 1 < length and value[i + 1] in ":|" and _is_ascii_letter(value[i]):
                if i + 2 < length and value[i + 2] in "\\/":
                    return _approximate(value, index, length, syntax)  # a DOS path
                if _Syntax.FILE_LIKE in syntax:
                    raise _Fatal  # MustRootedPath
            if i - index >= 2:
                return _approximate(value, index, length, syntax)  # an authority

    if index + 2 <= length:
        first, second = value[index], value[index + 1]
        if _Syntax.MUST_HAVE_AUTHORITY in syntax:
            if first in "\\/" and second in "\\/":
                return _approximate(value, index, length, syntax)
            return _relative_is_well_formed(value)  # BadAuthority: a relative URI
        if _Syntax.OPTIONAL_AUTHORITY in syntax and first == "/" and second == "/":
            return _approximate(value, index, length, syntax)
    elif _Syntax.MUST_HAVE_AUTHORITY in syntax:
        return _relative_is_well_formed(value)
    if _Syntax.MAIL_TO_LIKE in syntax:
        return _approximate(value, index, length, syntax)
    return _components_are_well_formed(value, index, length, syntax)


def _approximate(value: str, index: int, length: int, syntax: _Syntax) -> bool:
    """For the forms that need .NET's host parsing: the check of a URI without an authority (module docstring)."""
    return _components_are_well_formed(value, index, length, syntax)


def _relative_is_well_formed(value: str) -> bool:
    """A relative URI: no colon in its first segment, and nothing that needs escaping."""
    first = next((i for i, char in enumerate(value) if char in ":\\/?#"), -1)
    if first >= 0 and value[first] == ":":
        return False
    result, _ = _check_canonical(value, 0, len(value), _EOL, None, has_unicode=False)
    return result & (_Check.BACKSLASH_IN_PATH | _Check.ESCAPED_CANONICAL) == _Check.ESCAPED_CANONICAL


def _components_are_well_formed(value: str, index: int, length: int, syntax: _Syntax) -> bool:
    """``ParseRemaining`` and ``InternalIsWellFormedOriginalString`` for a URI without an authority: the path, the
    query and the fragment each need nothing escaped, or are canonical IRIs."""
    has_unicode = _has_unicode_or_escaped_unreserved(value)
    string = value
    if has_unicode:
        string, length = _iri_string(value, index, syntax)

    if _QUERY_AND_FRAGMENT & syntax == _Syntax(0):
        delimiter = _DUMMY
    else:
        delimiter = "?" if _Syntax.MAY_HAVE_QUERY in syntax else "#"
    result, position = _check_canonical(string, index, length, delimiter, syntax, has_unicode)
    path_not_canonical = _Check.ESCAPED_CANONICAL not in result
    non_canonical = _Check.DISPLAY_CANONICAL not in result
    if _Check.BACKSLASH_IN_PATH in result:
        path_not_canonical = non_canonical = True
    mask = _IRI_CANONICAL | _Check.ESCAPED_CANONICAL | _Check.NOT_IRI_CANONICAL
    if path_not_canonical and (non_canonical or result & mask != _IRI_CANONICAL):
        return False

    if position < length and string[position] == "?":
        query_delimiter = "#" if _Syntax.MAY_HAVE_FRAGMENT in syntax else _EOL
        result, position = _check_canonical(string, position + 1, length, query_delimiter, syntax, has_unicode)
        if not _component_is_canonical(result):
            return False
    if position < length and string[position] == "#":
        result, position = _check_canonical(string, position + 1, length, _EOL, syntax, has_unicode)
        if not _component_is_canonical(result):
            return False
    return True


def _component_is_canonical(result: _Check) -> bool:
    """A query or fragment needs nothing escaped (no ``E_QueryNotCanonical``), or is a canonical IRI."""
    if result & (_Check.ESCAPED_CANONICAL | _Check.BACKSLASH_IN_PATH) == _Check.ESCAPED_CANONICAL:
        return True
    mask = _IRI_CANONICAL | _Check.ESCAPED_CANONICAL | _Check.BACKSLASH_IN_PATH | _Check.NOT_IRI_CANONICAL
    return result & mask == _IRI_CANONICAL


def _iri_string(value: str, index: int, syntax: _Syntax) -> tuple[str, int]:
    """The string and its length that ``ParseRemaining`` builds for a text with non-ASCII characters: the scheme, then
    the path, query and fragment through ``IriHelper.EscapeUnescapeIri``."""
    end = len(value)
    if _QUERY_AND_FRAGMENT & syntax == _Syntax(0):
        path_end = end
    else:
        stops = {_QUERY_AND_FRAGMENT: "?#", _Syntax.MAY_HAVE_QUERY: "?", _Syntax.MAY_HAVE_FRAGMENT: "#"}
        path_end = next((i for i in range(index, end) if value[i] in stops[_QUERY_AND_FRAGMENT & syntax]), end)
    string = value[:index] + _escape_unescape_iri(value, index, path_end, is_query=False)
    length = _length_without_trailing_spaces(string, index) if string == value else len(string)

    position = path_end
    if position < end and value[position] == "?":
        query_end = end
        if _Syntax.MAY_HAVE_FRAGMENT in syntax:
            query_end = next((i for i in range(position + 1, end) if value[i] == "#"), end)
        string += _escape_unescape_iri(value, position, query_end, is_query=True)
        length = _length_without_trailing_spaces(string, index) if string == value else len(string)
        position = query_end
    if position < end and value[position] == "#":
        string += _escape_unescape_iri(value, position, end, is_query=False)
        length = _length_without_trailing_spaces(string, index)
    return string, length


def _escape_unescape_iri(value: str, start: int, end: int, is_query: bool) -> str:
    """``IriHelper.EscapeUnescapeIri`` for a path, query or fragment: unescapes what is safe, escapes characters
    outside the IRI ranges."""
    out: list[str] = []
    i = start
    while i < end:
        char = value[i]
        if char == "%" and end - i > 2:
            decoded = _decode_hex(value[i + 1], value[i + 2])
            if decoded in (_DUMMY, "%") or decoded in _RESERVED_MARKS or _not_safe_for_unescape(decoded):
                out.append(value[i : i + 3])
                i += 3
            elif decoded <= "\x7f":
                out.append(decoded)
                i += 3
            else:
                i += _unescape_utf8(value, i, end, is_query, out)
            continue
        if char > "\x7f":
            high = 0xD800 <= ord(char) <= 0xDBFF
            if high and i + 1 < end and 0xDC00 <= ord(value[i + 1]) <= 0xDFFF:
                code = 0x10000 + ((ord(char) - 0xD800) << 10) + (ord(value[i + 1]) - 0xDC00)
                out.append(value[i : i + 2] if _in_iri_range(code, is_query) else _percent_encode(chr(code)))
                i += 2
                continue
            if _in_iri_range(ord(char), is_query):
                out.append(char)
            else:
                out.append(_percent_encode("\ufffd" if 0xD800 <= ord(char) <= 0xDFFF else char))
        else:
            out.append(char)
        i += 1
    return "".join(out)


def _unescape_utf8(value: str, start: int, end: int, is_query: bool, out: list[str]) -> int:
    """``PercentEncodingHelper.UnescapePercentEncodedUTF8Sequence``: decodes the run of escaped bytes from 0x80 up at
    ``start``, unescaping each character in the IRI ranges and leaving the other bytes escaped. Returns the length of
    the run."""
    data = bytearray()
    i = start
    while end - i > 2 and value[i] == "%" and value[i + 1] in _HIGH_HEX_DIGITS and value[i + 2] in _HEX_DIGITS:
        data.append(int(value[i + 1 : i + 3], 16))
        i += 3
    position = 0
    while position < len(data):
        code, consumed = _decode_utf8(data[position : position + 4])
        if code is not None and _in_iri_range(code, is_query):
            out.append(_utf16(code))
        else:
            out.append(value[start + 3 * position : start + 3 * (position + consumed)])
        position += consumed
    return i - start


def _decode_utf8(data: bytes | bytearray) -> tuple[int | None, int]:
    """``Rune.DecodeFromUtf8``: the code point and the bytes it took, or ``None`` and the length of the invalid or
    incomplete sequence (its maximal subpart)."""
    lead = data[0]
    if 0xC2 <= lead <= 0xDF:
        size, low, high = 2, 0x80, 0xBF
    elif 0xE0 <= lead <= 0xEF:
        size, low, high = 3, (0xA0 if lead == 0xE0 else 0x80), (0x9F if lead == 0xED else 0xBF)
    elif 0xF0 <= lead <= 0xF4:
        size, low, high = 4, (0x90 if lead == 0xF0 else 0x80), (0x8F if lead == 0xF4 else 0xBF)
    else:
        return None, 1
    for k in range(1, size):
        if k >= len(data) or not low <= data[k] <= high:
            return None, k
        low, high = 0x80, 0xBF
    return ord(data[:size].decode()), size


def _check_canonical(
    string: str, start: int, end: int, delimiter: str, syntax: _Syntax | None, has_unicode: bool
) -> tuple[_Check, int]:
    """``Uri.CheckCanonical``: what in ``string[start:end]``, up to the delimiter, would need escaping; and where it
    stopped."""
    result = _Check.NONE
    needs_escaping = False
    found_escaping = False
    # With '?' as the delimiter, a '#' also ends the path when there can be a fragment.
    hash_ends = delimiter == "?" and syntax is not None and _Syntax.MAY_HAVE_FRAGMENT in syntax
    i = start
    while i < end:
        char = string[i]
        if char <= "\x1f" or "\x7f" <= char <= "\x9f":
            needs_escaping = found_escaping = True
            result |= _Check.RESERVED_FOUND
        elif char > "~":
            result |= _Check.FOUND_NON_ASCII
            if 0xD800 <= ord(char) <= 0xDBFF:
                valid = i + 1 < end and _in_iri_pair_range(char, string[i + 1])
            else:
                valid = _in_iri_range(ord(char), is_query=True)
            if not valid:
                result |= _Check.NOT_IRI_CANONICAL
            needs_escaping = True
        elif char == delimiter or (char == "#" and hash_ends):
            break
        elif char == "?":
            if syntax is not None and _Syntax.MAY_HAVE_QUERY not in syntax and delimiter != _EOL:
                result |= _Check.RESERVED_FOUND
                found_escaping = needs_escaping = True
        elif char == "#":
            needs_escaping = True
            if syntax is not None and _Syntax.MAY_HAVE_FRAGMENT not in syntax:
                result |= _Check.RESERVED_FOUND
                found_escaping = True
        elif char == "\\":
            result |= _Check.BACKSLASH_IN_PATH
        elif (char <= '"' and char != "!") or "[" <= char <= "^" or char in "<>`":
            needs_escaping = True
            if has_unicode:
                result |= _Check.NOT_IRI_CANONICAL
        elif "{" <= char <= "}":
            needs_escaping = True
        elif char == "%":
            found_escaping = True
            if i + 2 < end and _decode_hex(string[i + 1], string[i + 2]) != _DUMMY:
                i += 3
                continue
            needs_escaping = True
        i += 1

    if not found_escaping:
        result |= _Check.DISPLAY_CANONICAL
    if not needs_escaping:
        result |= _Check.ESCAPED_CANONICAL
    return result, i


def _has_unicode_or_escaped_unreserved(value: str) -> bool:
    """``CheckForUnicodeOrEscapedUnreserved``: a non-ASCII character, or an escape of one, of an unreserved character
    or with a non-hex digit."""
    i = 0
    while i < len(value):
        char = value[i]
        if char == "%":
            if i + 2 < len(value):
                decoded = _decode_hex(value[i + 1], value[i + 2])
                if decoded > "\x7f" or decoded in _UNRESERVED:
                    return True
                i += 2
        elif char > "\x7f":
            return True
        i += 1
    return False


def _in_iri_range(code: int, is_query: bool) -> bool:
    """``IriHelper.CheckIriUnicodeRange(uint, bool)``."""
    if code <= 0xFFFF:
        return (
            0xA0 <= code <= 0xD7FF
            or 0xF900 <= code <= 0xFDCF
            or 0xFDF0 <= code <= 0xFFEF
            or (is_query and 0xE000 <= code <= 0xF8FF)
        )
    return (code & 0xFFFF) < 0xFFFE and not 0xE0000 <= code <= 0xE0FFF and (is_query or code < 0xF0000)


def _in_iri_pair_range(high: str, low: str) -> bool:
    """``IriHelper.CheckIriUnicodeRange(char, char, out bool, true)``, as ``CheckCanonical`` calls it."""
    if not 0xDC00 <= ord(low) <= 0xDFFF:
        return False
    return _in_iri_range(0x10000 + ((ord(high) - 0xD800) << 10) + (ord(low) - 0xDC00), is_query=True)


def _decode_hex(first: str, second: str) -> str:
    """``UriHelper.DecodeHexChars``: the character two hexadecimal digits stand for, or U+FFFF."""
    if first not in _HEX_DIGITS or second not in _HEX_DIGITS:
        return _DUMMY
    return chr(int(first + second, 16))


def _percent_encode(char: str) -> str:
    return "".join(f"%{byte:02X}" for byte in char.encode())


def _utf16(code: int) -> str:
    if code <= 0xFFFF:
        return chr(code)
    code -= 0x10000
    return chr(0xD800 + (code >> 10)) + chr(0xDC00 + (code & 0x3FF))


def _length_without_trailing_spaces(string: str, start: int) -> int:
    length = len(string)
    while length > start and _is_lws(string[length - 1]):
        length -= 1
    return length


def _not_safe_for_unescape(char: str) -> bool:
    return char <= "\x1f" or "\x7f" <= char <= "\x9f" or char in _NOT_SAFE_FOR_UNESCAPE


def _is_lws(char: str) -> bool:
    return char in " \n\r\t"


def _is_ascii_letter(char: str) -> bool:
    return "A" <= char <= "Z" or "a" <= char <= "z"
