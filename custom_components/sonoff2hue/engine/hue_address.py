"""The Hue bridge's address: a host name or IP address with an optional port.

A port of the .NET app's ``Hue/HueBridgeAddress.cs``, which accepts an address when .NET's ``Uri`` reads
``https://<address>/`` as a URL with nothing but a host and a port, and connects to the ``Uri.IdnHost`` that results. So
this module reproduces the parts of ``System.Private.Uri`` (release/10.0) that decide that, function by function:
``IPAddress.TryParse`` for the IPv6 test, the user info, the IPv6, IPv4, DNS (with IRI) and UNC host types tried in that
order, the port, dot segments in the path, the canonical host, and ``IdnMapping`` as it works with invariant
globalization, as the app runs: plain Punycode without IDNA mapping or normalization. Like .NET, it works on UTF-16 code
units.
"""

from dataclasses import dataclass
import unicodedata

from . import text
from .config import ConfigError

_DEFAULT_PORT = 443
_MAX_UNC_NAME = 256

# DomainNameHelper.s_iriInvalidChars: anything non-ASCII is valid in an IRI host except U+0080-U+009F.
_IRI_INVALID = frozenset(
    [chr(c) for c in range(0x20)] + list(" !\"#$%&'()*+,/:;<=>?@[\\]^`{|}~\x7f") + [chr(c) for c in range(0x80, 0xA0)]
)
_IRI_DOTS = ".\u3002\uff0e\uff61"
_HEX = "0123456789abcdefABCDEF"
_UNSAFE_FOR_NORMALIZED_HOST = frozenset("\\/?@#:[]")


@dataclass(frozen=True, slots=True)
class BridgeUrl:
    """The bridge's base URL, as .NET's ``Uri`` reads it."""

    uri: str
    """``Uri.AbsoluteUri``, such as ``https://192.168.1.20/``."""
    host: str
    """``Uri.Host``: IPv6 addresses in brackets and without a zone, host names lowercased where .NET does."""
    idn_host: str | None
    """``Uri.IdnHost``, the host that requests connect to: no brackets, the zone of an IPv6 address, host names in
    Punycode. ``None`` where .NET throws a ``UriFormatException`` for it, so that requests fail."""
    port: int
    ipv6: bool

    def __str__(self) -> str:
        return self.uri


def try_create_base_url(address: str | None) -> tuple[BridgeUrl, None] | tuple[None, str]:
    """The bridge's base URL, or ``None`` and the reason the address can't be used (``TryCreateBaseUri``)."""
    value = text.trim(address or "")
    if not value:
        return None, "The Hue bridge address is empty."

    units = _units(value)
    # A URI needs brackets around an IPv6 address; "[fe80::1]:8443" already has them.
    if not units.startswith("[") and is_ip_address(value) and ":" in units:
        units = f"[{units}]"
    url = _parse(units)
    if url is None:
        return None, (
            f"'{value}' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, "
            "for example 192.168.1.20 or bridge.local:8443."
        )
    return url, None


def create_base_url(address: str | None) -> BridgeUrl:
    """Like :func:`try_create_base_url`, but raises :class:`ConfigError` for an address that can't be used."""
    url, error = try_create_base_url(address)
    if url is None:
        raise ConfigError(error)
    return url


def is_ip_address(value: str) -> bool:
    """``IPAddress.TryParse``: an IPv4 address in any form ``Uri`` reads, or an IPv6 address, with or without a zone,
    brackets and a port."""
    units = _units(value)
    if ":" in units:
        return _ipv6_is_valid_strict(units)
    address, end = _ipv4_parse_non_canonical(units, 0, len(units)) if units else (None, 0)
    return address is not None and end == len(units)


def _units(value: str) -> str:
    """``value`` with characters outside the Basic Multilingual Plane as surrogate pairs, as in a .NET string."""
    if all(ord(char) <= 0xFFFF for char in value):
        return value
    return "".join(
        char
        if ord(char) <= 0xFFFF
        else chr(0xD800 + ((ord(char) - 0x10000) >> 10)) + chr(0xDC00 + ((ord(char) - 0x10000) & 0x3FF))
        for char in value
    )


def _from_units(units: str) -> str:
    """The reverse of :func:`_units`: surrogate pairs become the characters they encode."""
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _code_point(units: str, index: int) -> int:
    """``char.ConvertToUtf32``-like: the code point at ``index``, a surrogate pair read as one."""
    high = ord(units[index])
    if 0xD800 <= high <= 0xDBFF and index + 1 < len(units) and 0xDC00 <= ord(units[index + 1]) <= 0xDFFF:
        return 0x10000 + ((high - 0xD800) << 10) + (ord(units[index + 1]) - 0xDC00)
    return high


# Uri.TryCreate("https://<authority>/") and the checks of HueBridgeAddress.


def _parse(authority: str) -> BridgeUrl | None:
    s = authority + "/"
    length = len(s)
    if s[0] in "/#?":
        return None  # an empty host

    # CheckAuthorityHelper: user info ends at the first '@' before the path, query or fragment.
    start = 0
    user_info: str | None = None
    for index in range(length):
        char = s[index]
        if index == length - 1 or char in "?#\\/":
            break
        if char == "@":
            user_info = s[:index]
            start = index + 1
            break
    if user_info:
        return None  # HueBridgeAddress: uri.UserInfo.Length == 0

    first = s[start]
    end = length
    kind: str | None = None
    if first == "[" and (ipv6_end := _ipv6_is_valid(s, start + 1, length)) is not None:
        kind, end = "ipv6", ipv6_end
    elif "0" <= first <= "9" and (ipv4 := _ipv4_parse_non_canonical(s, start, length))[0] is not None:
        kind, end = "ipv4", ipv4[1]
    else:
        if "0" <= first <= "9":
            end = ipv4[1]  # ParseNonCanonical moves the end to a terminator even when it fails
        if (dns_length := _dns_is_valid(s[start:end])) is not None:
            kind, end = "dns", start + dns_length
        elif (unc_end := _unc_is_valid(s, start, end)) is not None and unc_end - start <= _MAX_UNC_NAME:
            kind, end = "unc", unc_end

    port = _DEFAULT_PORT
    after = end
    if kind is not None and end < length and s[end] == "\\":
        kind = None
    elif end < length and s[end] == ":":
        digits = 0
        number = 0
        after = end + 1
        while after < length:
            char = s[after]
            if "0" <= char <= "9":
                number = number * 10 + ord(char) - ord("0")
                digits += 1
                if number > 0xFFFF:
                    break
            elif char in "/?#":
                break
            else:
                return None  # a bad port
            after += 1
        if number > 0xFFFF:
            return None
        if digits:
            port = number
    if kind is None:
        return None

    # HueBridgeAddress: AbsolutePath == "/", no query and no fragment. http(s) URLs convert backslashes and remove dot
    # segments, "%2e" included.
    rest = s[after:].replace("\\", "/")
    if not rest.startswith("/") or "?" in rest or "#" in rest or remove_dot_segments(rest) != "/":
        return None

    host_units = s[start:end]
    match kind:
        case "ipv6":
            numbers, scope = _ipv6_parse(host_units)
            host = _ipv6_canonical(numbers)
            idn_host: str | None = host[1:-1] + scope
        case "ipv4":
            host = idn_host = _ipv4_canonical(_ipv4_parse_non_canonical(host_units, 0, len(host_units))[0] or 0)
        case "dns":
            host = _canonical_name(_strip_bidi_controls(host_units))
            idn_host = _idn_equivalent(host)
        case _:
            host = idn_host = _canonical_name(host_units)

    host, idn_host = _from_units(host), None if idn_host is None else _from_units(idn_host)
    user = "@" if user_info is not None else ""
    uri = f"https://{user}{host}{'' if port == _DEFAULT_PORT else f':{port}'}/"
    return BridgeUrl(uri, host, idn_host, port, kind == "ipv6")


def remove_dot_segments(path: str) -> str:
    """RFC 3986's remove_dot_segments for an absolute path, with "%2e" read as "." as .NET's ``Uri`` does."""
    segments = path.split("/")
    output: list[str] = []
    for index, segment in enumerate(segments[1:], start=1):
        last = index == len(segments) - 1
        name = segment.replace("%2e", ".").replace("%2E", ".")
        if name == ".":
            if last:
                output.append("")
        elif name == "..":
            if output:
                output.pop()
            if last:
                output.append("")
        else:
            output.append(segment)
    return "/" + "/".join(output)


def _canonical_name(host: str) -> str:
    """``DomainNameHelper.ParseCanonicalName``: lowercased only if it has an ASCII capital letter; "loopback" and
    "localhost" become "localhost"."""
    if any("A" <= char <= "Z" for char in host):
        if text.equals_ignore_case(host, "localhost") or text.equals_ignore_case(host, "loopback"):
            return "localhost"
        return _units(text.lower_invariant(_from_units(host)))
    if host in ("localhost", "loopback"):
        return "localhost"
    return host


def _strip_bidi_controls(host: str) -> str:
    """``UriHelper.StripBidiControlCharacters``: U+200E, U+200F and U+202A-U+202E are removed."""
    return "".join(char for char in host if not ("\u200e" <= char <= "\u202e" and not "\u2010" <= char <= "\u2029"))


# DomainNameHelper.IsValid(iri: true) and UncNameHelper.IsValid.


def _dns_is_valid(hostname: str) -> int | None:
    """The length of the DNS host at the start of ``hostname``, or ``None`` if it isn't one."""
    invalid = next((index for index, char in enumerate(hostname) if char in _IRI_INVALID), -1)
    if invalid >= 0:
        if hostname[invalid] in "/\\:?#":
            hostname = hostname[:invalid]
        else:
            return None
    length = len(hostname)
    if length == 0:
        return None
    while True:
        first = hostname[0]
        if first < "\xa0" and not (first.isascii() and first.isalnum()):
            return None
        dot = next((index for index, char in enumerate(hostname) if char in _IRI_DOTS), -1)
        label_length = len(hostname) if dot < 0 else dot
        label = hostname[:label_length]
        if not label.isascii():
            # Room for the ACE prefix "xn--", and characters above U+00FF count twice.
            label_length += 4 + sum(1 for char in label if char > "\xff")
        if not 1 <= label_length <= 63:
            return None
        if dot < 0:
            return length
        hostname = hostname[dot + 1 :]
        if not hostname:
            return length  # the host name ends with a dot


def _unc_is_valid(name: str, start: int, end: int) -> int | None:
    """``UncNameHelper.IsValid``: the end of the UNC host name that starts at ``start``, or ``None``."""
    if start == end:
        return None
    valid_short_name = False
    index = start
    while index < end:
        char = name[index]
        if char in "/\\:?#":
            end = index
            break
        if char == ".":
            index += 1
            break
        if _is_letter(char) or char in "-_":
            valid_short_name = True
        elif not "0" <= char <= "9":
            return None
        index += 1
    if not valid_short_name:
        return None

    while index < end:
        char = name[index]
        if char in "/\\:?#":
            end = index
            break
        if char == ".":
            if not valid_short_name or (index - 1 >= start and name[index - 1] == "."):
                return None
            valid_short_name = False
        elif char in "-_":
            if not valid_short_name:
                return None
        elif _is_letter(char) or "0" <= char <= "9":
            valid_short_name = True
        else:
            return None
        index += 1
    if index - 1 >= start and name[index - 1] == ".":
        valid_short_name = True
    return end if valid_short_name else None


def _is_letter(char: str) -> bool:
    """``char.IsLetter`` for one UTF-16 code unit."""
    return unicodedata.category(char) in ("Lu", "Ll", "Lt", "Lm", "Lo")


# IPv4AddressHelper.


def _ipv4_parse_non_canonical(name: str, start: int, end: int) -> tuple[int | None, int]:
    """``ParseNonCanonical``: the address (one to four parts, each decimal, octal with a leading 0 or hex with 0x, the
    last filling the remaining bytes) and the possibly moved end, or ``None`` and the end."""
    parts: list[int] = []
    value = 0
    at_least_one_char = False
    current = start
    char = ""
    while current < end:
        char = name[current]
        value = 0
        base = 10
        if char == "0":
            current += 1
            at_least_one_char = True
            if current < end:
                char = name[current]
                if char in "xX":
                    base = 16
                    current += 1
                    at_least_one_char = False
                else:
                    base = 8
        while current < end:
            char = name[current]
            digit = int(char, 16) if char in _HEX else 0xFF
            if digit >= base:
                break
            value = value * base + digit
            if value > 0xFFFFFFFF:
                return None, end
            at_least_one_char = True
            current += 1
        if current < end and char == ".":
            if len(parts) >= 3 or not at_least_one_char or value > 0xFF:
                return None, end
            parts.append(value)
            at_least_one_char = False
            current += 1
            continue
        break

    if not at_least_one_char:
        return None, end
    if current < end:
        if char in "/\\:?#":
            end = current
        else:
            return None, end
    limits = (0xFFFFFFFF, 0xFFFFFF, 0xFFFF, 0xFF)
    if value > limits[len(parts)]:
        return None, end
    for index, part in enumerate(parts):
        value |= part << (24 - 8 * index)
    return value, end


def _ipv4_canonical(value: int) -> str:
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _ipv4_is_valid_canonical(name: str, start: int, end: int) -> int | None:
    """``IsValidCanonical(allowIPv6: true)``, for an IPv4 address in an IPv6 one: its end, or ``None``."""
    dots = 0
    number = 0
    have_number = False
    first_char_is_zero = False
    while start < end:
        char = name[start]
        if char in "]/%":
            break
        if "0" <= char <= "9":
            digit = ord(char) - ord("0")
            if not have_number and digit == 0:
                if start + 1 < end and name[start + 1] == "0":
                    return None
                first_char_is_zero = True
            have_number = True
            number = number * 10 + digit
            if number > 0xFF:
                return None
        elif char == ".":
            if not have_number or (number > 0 and first_char_is_zero):
                return None
            dots += 1
            have_number = False
            number = 0
            first_char_is_zero = False
        else:
            return None
        start += 1
    return start if dots == 3 and have_number else None


# IPv6AddressHelper.


def _ipv6_is_valid(name: str, start: int, end: int) -> int | None:
    """``InternalIsValid`` as ``Uri`` calls it, from after the '[': the end after the ']', or ``None``. A zone may hold
    anything but ']' and '/', and a prefix length of one or two digits may follow a '/'."""
    sequence_count = 0
    sequence_length = 0
    have_compressor = False
    have_ipv4 = False
    have_prefix = False
    expecting_number = True
    last_sequence = 1
    if name[start] == ":" and (start + 1 >= end or name[start + 1] != ":"):
        return None
    index = start
    while index < end:
        char = name[index]
        if ("0" <= char <= "9") if have_prefix else (char in _HEX):
            sequence_length += 1
            expecting_number = False
            index += 1
            continue
        if sequence_length > 4:
            return None
        if sequence_length != 0:
            sequence_count += 1
            last_sequence = index - sequence_length
        if char == "%":
            while True:
                index += 1
                if index == end:
                    return None
                if name[index] in "]/":
                    break
            char = name[index]
        if char == "]":
            # The length of the last sequence stays, for the prefix check below.
            start = index
            index = end + 1
            continue
        if char == ":":
            if index > 0 and name[index - 1] == ":":
                if have_compressor:
                    return None
                have_compressor = True
                expecting_number = False
            else:
                expecting_number = True
        elif char == "/":
            if sequence_count == 0 or have_prefix:
                return None
            have_prefix = True
            expecting_number = True
        elif char == ".":
            if have_ipv4:
                return None
            ipv4_end = _ipv4_is_valid_canonical(name, last_sequence, end)
            if ipv4_end is None:
                return None
            sequence_count += 1
            have_ipv4 = True
            index = ipv4_end - 1
        else:
            return None
        sequence_length = 0
        index += 1

    if have_prefix and not 1 <= sequence_length <= 2:
        return None
    expected = 8 + (1 if have_prefix else 0)
    if (
        not expecting_number
        and sequence_length <= 4
        and (sequence_count < expected if have_compressor else sequence_count == expected)
        and index == end + 1
    ):
        return start + 1
    return None


def _ipv6_is_valid_strict(name: str) -> bool:
    """``IsValidStrict``, behind ``IPAddress.TryParse``: no prefix length; a zone runs to the end or to a ']'; brackets
    may hold the address, followed by a port that is ignored but must be decimal, or hexadecimal after "0x"."""
    end = len(name)
    sequence_count = 0
    sequence_length = 0
    have_compressor = False
    have_ipv4 = False
    expecting_number = True
    last_sequence = 1
    needs_closing_bracket = False
    start = 0
    if start < end and name[start] == "[":
        start += 1
        needs_closing_bracket = True
    if start >= end or (name[start] == ":" and (start + 1 >= end or name[start + 1] != ":")):
        return False
    index = start
    while index < end:
        char = name[index]
        if char in _HEX:
            sequence_length += 1
            expecting_number = False
            index += 1
            continue
        if sequence_length > 4:
            return False
        if sequence_length != 0:
            sequence_count += 1
            last_sequence = index - sequence_length
            sequence_length = 0
        if char == "%":
            while index + 1 < end:
                index += 1
                if name[index] in "]/":
                    break
            else:
                index += 1
                continue
            char = name[index]
        if char == "]":
            if not needs_closing_bracket:
                return False
            needs_closing_bracket = False
            if index + 1 < end and name[index + 1] != ":":
                return False
            if index + 3 < end and name[index + 2] == "0" and name[index + 3] == "x":
                if not all(c in _HEX for c in name[index + 4 :]):
                    return False
            elif not all("0" <= c <= "9" for c in name[index + 2 :]):
                return False
            break
        if char == ":":
            if index > 0 and name[index - 1] == ":":
                if have_compressor:
                    return False
                have_compressor = True
                expecting_number = False
            else:
                expecting_number = True
        elif char == ".":
            if have_ipv4:
                return False
            ipv4_end = _ipv4_is_valid_canonical(name, last_sequence, end)
            if ipv4_end is None:
                return False
            sequence_count += 1
            have_ipv4 = True
            index = ipv4_end - 1
        else:
            return False  # '/' included
        index += 1

    if sequence_length != 0:
        if sequence_length > 4:
            return False
        sequence_count += 1
    return (
        not expecting_number
        and (sequence_count < 8 if have_compressor else sequence_count == 8)
        and not needs_closing_bracket
    )


def _ipv6_parse(address: str) -> tuple[list[int], str]:
    """``IPv6AddressHelper.Parse`` of a valid address: its eight numbers and its zone, with the '%'."""
    numbers = [0] * 8
    number = 0
    index = 0
    compressor_index = -1
    number_is_valid = True
    scope = ""
    i = 1 if address[0] == "[" else 0
    while i < len(address) and address[i] != "]":
        char = address[i]
        if char == "%":
            if number_is_valid:
                numbers[index] = number
                index += 1
                number_is_valid = False
            scope_start = i
            i += 1
            while i < len(address) and address[i] not in "]/":
                i += 1
            scope = address[scope_start:i]
            while i < len(address) and address[i] != "]":
                i += 1
        elif char == ":":
            numbers[index] = number
            index += 1
            number = 0
            i += 1
            if address[i] == ":":
                compressor_index = index
                i += 1
            elif compressor_index < 0 and index < 6:
                continue
            j = i
            while j < len(address) and address[j] not in "]:%/" and j < i + 4:
                if address[j] == ".":
                    while j < len(address) and address[j] not in "]/%":
                        j += 1
                    octets = [int(part or "0") for part in address[i:j].replace(":", ".").split(".")[:4]]
                    octets += [0] * (4 - len(octets))
                    numbers[index] = ((octets[0] & 0xFF) << 8) | (octets[1] & 0xFF)
                    numbers[index + 1] = ((octets[2] & 0xFF) << 8) | (octets[3] & 0xFF)
                    index += 2
                    i = j
                    number = 0
                    number_is_valid = False
                    break
                j += 1
        elif char == "/":
            if number_is_valid:
                numbers[index] = number
                index += 1
                number_is_valid = False
            i += 1
            while i < len(address) and address[i] != "]":
                i += 1
        else:
            number = number * 16 + int(char, 16)
            i += 1
    if number_is_valid:
        numbers[index] = number
        index += 1
    if compressor_index > 0:
        to_index = 7
        from_index = index - 1
        if from_index != to_index:
            for _ in range(index - compressor_index):
                numbers[to_index] = numbers[from_index]
                numbers[from_index] = 0
                to_index -= 1
                from_index -= 1
    return [n & 0xFFFF for n in numbers], scope


def _ipv6_canonical(numbers: list[int]) -> str:
    """``IPv6AddressHelper.ParseCanonicalName``: RFC 5952 form in brackets, with an embedded IPv4 address where .NET
    writes one."""
    longest_length, longest_start, current = 0, -1, 0
    for index, number in enumerate(numbers):
        if number == 0:
            current += 1
            if current > longest_length:
                longest_length, longest_start = current, index - current + 1
        else:
            current = 0
    range_start, range_end = (longest_start, longest_start + longest_length) if longest_length > 1 else (-1, 0)
    embedded = (
        numbers[0] == numbers[1] == numbers[2] == numbers[3] == 0
        and numbers[6] != 0
        and ((numbers[4] == 0 and numbers[5] in (0, 0xFFFF)) or (numbers[4] == 0xFFFF and numbers[5] == 0))
    ) or (numbers[4] == 0 and numbers[5] == 0x5EFE)

    out = "["
    for index in range(8):
        if embedded and index == 6:
            out += f":{numbers[6] >> 8}.{numbers[6] & 0xFF}.{numbers[7] >> 8}.{numbers[7] & 0xFF}"
            break
        if range_start == index:
            out += ":"
        if range_start <= index and range_end == 8:
            out += ":"
            break
        if range_start <= index < range_end:
            continue
        if index != 0:
            out += ":"
        out += f"{numbers[index]:x}"
    return out + "]"


# DomainNameHelper.IdnEquivalent and IdnMapping.GetAscii with invariant globalization.


class _IdnError(Exception):
    pass


def _idn_equivalent(host: str) -> str | None:
    """The Punycode form of a DNS host, or ``None`` where .NET's ``IdnHost`` throws."""
    if host.isascii():
        return text.lower_invariant(host)
    try:
        ascii_form = _get_ascii_invariant(_strip_bidi_controls(host))
    except _IdnError:
        return None
    if any(char in _UNSAFE_FOR_NORMALIZED_HOST for char in ascii_form):
        return None
    return ascii_form


def _get_ascii_invariant(unicode: str) -> str:
    if not unicode or unicode[-1] == "\0":
        raise _IdnError
    last_dot = -1
    for index, char in enumerate(unicode):
        if char <= "\x1f":
            raise _IdnError
        if char >= "\x7f":
            break
        if char == ".":
            if index == last_dot + 1 or index - last_dot > 64:
                raise _IdnError
            last_dot = index
    else:
        return unicode
    if unicode[-1] <= "\x1f":
        raise _IdnError
    return _punycode_encode(unicode)


def _punycode_encode(unicode: str) -> str:
    """``IdnMapping.PunycodeEncode``: each label that isn't plain ASCII becomes "xn--" and its Punycode, after the
    checks .NET makes: direction, an "xn--" label that isn't ASCII, and the lengths of labels and of the name."""
    output = ""
    after_last_dot = 0
    next_dot = 0
    while next_dot < len(unicode):
        relative = next((index for index, char in enumerate(unicode[after_last_dot:]) if char in _IRI_DOTS), -1)
        next_dot = len(unicode) if relative < 0 else relative + after_last_dot
        if next_dot == after_last_dot:
            if next_dot != len(unicode):
                raise _IdnError
            break

        right_to_left = _is_right_to_left(_code_point(unicode, after_last_dot))
        if right_to_left:
            last = next_dot - 1
            if 0xDC00 <= ord(unicode[last]) <= 0xDFFF:
                last -= 1
            if not _is_right_to_left(_code_point(unicode, last)):
                raise _IdnError
        basic = ""
        index = after_last_dot
        while index < next_dot:
            code_point = _code_point(unicode, index)
            direction = unicodedata.bidirectional(chr(code_point)) if code_point <= 0x10FFFF else ""
            if (right_to_left and direction == "L") or (not right_to_left and direction in ("R", "AL")):
                raise _IdnError
            if unicode[index] < "\x80":
                basic += unicode[index].lower() if "A" <= unicode[index] <= "Z" else unicode[index]
            elif code_point > 0xFFFF:
                index += 1
            index += 1

        label = unicode[after_last_dot:next_dot]
        if len(basic) == len(label):
            encoded = basic
        else:
            if text.starts_with_ignore_case(unicode[after_last_dot:], "xn--"):
                raise _IdnError
            encoded = "xn--" + _from_units(_lower_ascii(label)).encode("punycode").decode("ascii")
        if len(encoded) > 63:
            raise _IdnError
        output += encoded
        if next_dot != len(unicode):
            output += "."
        after_last_dot = next_dot + 1

    if len(output) > 255 - (0 if unicode[-1] in _IRI_DOTS else 1):
        raise _IdnError
    return output


def _lower_ascii(value: str) -> str:
    return "".join(char.lower() if "A" <= char <= "Z" else char for char in value)


def _is_right_to_left(code_point: int) -> bool:
    return code_point <= 0x10FFFF and unicodedata.bidirectional(chr(code_point)) in ("R", "AL")
