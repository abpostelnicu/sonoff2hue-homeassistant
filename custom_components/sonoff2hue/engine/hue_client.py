"""The Hue bridge's local CLIP API v2 over HTTPS, with the bridge's certificate pinned.

A port of the .NET app's ``Hue/HueClient.cs``, ``Hue/HueTls.cs`` and ``Hue/HueModels.cs``. The bridge serves a
certificate that is self-signed or signed by Signify's private CA, issued to the bridge id rather than its address, so
normal validation can never succeed. The certificate is pinned when pairing (trust on first use) and checked by its
SHA-256 afterwards. The client keeps its TLS connection open for 5 minutes, so that a press doesn't wait for a handshake.

The answers are read as .NET reads them: ``HttpContent.ReadAsStringAsync`` decodes the body, and ``JsonSerializer`` with
``JsonSerializerDefaults.Web`` the CLIP v2 envelope. Where the app's code throws something other than
``HueApiException`` for a malformed answer, the port raises :class:`UnexpectedError` with .NET's exception and message.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
import socket
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp
from yarl import URL

from . import errors, text
from .config import ConfigError
from .errors import UnexpectedError
from .hue_address import BridgeUrl, create_base_url
from .jsonnode import MAX_DEPTH, nesting_depth, reject_constant

PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"
"""What the .NET app's messages said with "run 'sonoff2hue hue-pair' again"."""
CERTIFICATE_MISMATCH = (
    "The Hue bridge TLS certificate does not match the pinned certificateSha256. If the bridge was replaced or reset, "
    f"{PAIR_AGAIN}."
)
"""Why a request failed when the bridge's certificate isn't the pinned one, or the TLS handshake failed."""

REQUEST_TIMEOUT = 5.0
CONNECT_TIMEOUT = 4.0
IDLE_TIMEOUT = 300.0


class HueTargetKind(Enum):
    LIGHT = "Light"
    GROUPED_LIGHT = "GroupedLight"


@dataclass(frozen=True, slots=True)
class HueTarget:
    """Something that can be switched on and off: a light, or the grouped_light of a room or zone. The id is ``None``
    only for a light that a malformed answer gave a null id, as in .NET."""

    kind: HueTargetKind
    id: str | None
    name: str | None

    def __str__(self) -> str:
        name = self.name or ""
        return f"light '{name}'" if self.kind == HueTargetKind.LIGHT else f"group '{name}'"


@dataclass(frozen=True, slots=True)
class HueLight:
    """A light. Its id and name are ``None`` only where a malformed answer has a null id, as in .NET."""

    id: str | None
    name: str | None
    on: bool


@dataclass(frozen=True, slots=True)
class HueGroup:
    """A room or zone. ``grouped_light_id`` is the resource that switches all its lights."""

    id: str | None
    name: str | None
    kind: str
    grouped_light_id: str | None


@dataclass(frozen=True, slots=True)
class HueBridgeInfo:
    ip: str
    bridge_id: str | None
    name: str | None
    model_id: str | None
    software_version: str | None
    certificate_sha256: str


class HueApiError(Exception):
    """A request to the bridge failed (``HueApiException``)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_authentication_error(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_transient(self) -> bool:
        return self.status is None or self.status in (429, 503, 502, 504, 500)


class HueApi(Protocol):
    """What the toggle logic needs; :class:`HueClient` has it."""

    async def get_light_states(self) -> dict[str, bool]: ...

    async def get_grouped_light_states(self) -> dict[str, bool]: ...

    async def set_on(self, target: HueTarget, on: bool) -> None: ...


# HTTP answers, read as .NET reads them.

# HttpResponseMessage.ReasonPhrase when the status line has none (HttpStatusDescription), measured on .NET 10.
# fmt: off
_REASON_PHRASES = {
    100: "Continue", 101: "Switching Protocols", 102: "Processing", 103: "Early Hints", 200: "OK", 201: "Created",
    202: "Accepted", 203: "Non-Authoritative Information", 204: "No Content", 205: "Reset Content",
    206: "Partial Content", 207: "Multi-Status", 208: "Already Reported", 226: "IM Used", 300: "Multiple Choices",
    301: "Moved Permanently", 302: "Found", 303: "See Other", 304: "Not Modified", 305: "Use Proxy",
    307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request", 401: "Unauthorized",
    402: "Payment Required", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 406: "Not Acceptable",
    407: "Proxy Authentication Required", 408: "Request Timeout", 409: "Conflict", 410: "Gone",
    411: "Length Required", 412: "Precondition Failed", 413: "Request Entity Too Large", 414: "Request-Uri Too Long",
    415: "Unsupported Media Type", 416: "Requested Range Not Satisfiable", 417: "Expectation Failed",
    421: "Misdirected Request", 422: "Unprocessable Entity", 423: "Locked", 424: "Failed Dependency",
    426: "Upgrade Required", 428: "Precondition Required", 429: "Too Many Requests",
    431: "Request Header Fields Too Large", 451: "Unavailable For Legal Reasons", 500: "Internal Server Error",
    501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout",
    505: "Http Version Not Supported", 506: "Variant Also Negotiates", 507: "Insufficient Storage",
    508: "Loop Detected", 510: "Not Extended", 511: "Network Authentication Required",
}

# Encoding.GetEncoding's names without a code pages provider, lowercase, by code page; measured on .NET 10.
_UTF7 = 65000
_ENCODING_NAMES = {
    65001: ("utf-8", "unicode-1-1-utf-8", "unicode-2-0-utf-8", "x-unicode-1-1-utf-8", "x-unicode-2-0-utf-8"),
    28591: ("latin1", "l1", "iso-8859-1", "iso_8859-1:1987", "cp819", "ibm819", "csisolatin1", "iso-ir-100",
            "iso8859-1", "iso_8859-1"),
    20127: ("us-ascii", "ascii", "us", "cp367", "ibm367", "csascii", "iso-ir-6", "iso646-us", "iso_646.irv:1991",
            "ansi_x3.4-1968", "ansi_x3.4-1986"),
    1200: ("utf-16", "utf-16le", "unicode", "ucs-2", "iso-10646-ucs-2"),
    1201: ("utf-16be", "unicodefffe"),
    12000: ("utf-32", "utf-32le"),
    12001: ("utf-32be",),
    _UTF7: ("utf-7", "csunicode11utf7", "unicode-1-1-utf-7", "unicode-2-0-utf-7", "x-unicode-1-1-utf-7",
            "x-unicode-2-0-utf-7"),
}
# fmt: on
_CODE_PAGES = {name: code_page for code_page, names in _ENCODING_NAMES.items() for name in names}
_PREAMBLES = {65001: b"\xef\xbb\xbf", 1200: b"\xff\xfe", 1201: b"\xfe\xff", 12000: b"\xff\xfe\x00\x00"}
_CODECS = {65001: "utf-8", 1200: "utf-16-le", 1201: "utf-16-be", 12000: "utf-32-le", 12001: "utf-32-be"}
_DETECTED = (65001, 12000, 1200, 1201)
"""The byte order marks that ``ReadAsStringAsync`` looks for without a charset, in its order."""


@dataclass(frozen=True, slots=True)
class HttpAnswer:
    """An HTTP answer as it came: its status, the reason phrase of its status line (``None`` if it had none), its body
    and the charset of its Content-Type."""

    status: int
    reason: str | None
    content: bytes
    charset: str | None = None

    @property
    def reason_phrase(self) -> str | None:
        """``HttpResponseMessage.ReasonPhrase``: the status line's, else .NET's default for the status."""
        return self.reason if self.reason is not None else _REASON_PHRASES.get(self.status)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status <= 299

    def read_as_string(self) -> str:
        """``HttpContent.ReadAsStringAsync``: the charset's encoding, or one detected from a byte order mark, else
        UTF-8; a byte order mark is dropped."""
        if self.charset is not None:
            code_page = _code_page(self.charset)
            if code_page is None:
                raise UnexpectedError(
                    "InvalidOperationException",
                    "The character set provided in ContentType is invalid. Cannot read content as string using an "
                    "invalid character set.",
                )
            preamble = _PREAMBLES.get(code_page, b"")
            start = len(preamble) if preamble and self.content.startswith(preamble) else 0
        else:
            code_page, start = 65001, 0
            for detected in _DETECTED:
                if self.content.startswith(_PREAMBLES[detected]):
                    code_page, start = detected, len(_PREAMBLES[detected])
                    break
        return _decode(self.content[start:], code_page)

    def read_json_text(self) -> str:
        """The text that ``HttpContent.ReadFromJsonAsync`` parses: transcoded from the charset's encoding, else UTF-8,
        without a byte order mark."""
        code_page = 65001
        if self.charset is not None:
            found = _code_page(self.charset)
            if found is None:
                raise UnexpectedError(
                    "InvalidOperationException", "The character set provided in ContentType is invalid."
                )
            code_page = found
        decoded = _decode(self.content, code_page)
        return decoded.removeprefix("\ufeff")


def _code_page(charset: str) -> int | None:
    """``Encoding.GetEncoding`` for a charset, at most one pair of quotes removed; ``None`` for a name .NET doesn't
    know, and an :class:`UnexpectedError` for UTF-7, which .NET no longer supports."""
    if len(charset) > 2 and charset[0] == '"' and charset[-1] == '"':
        charset = charset[1:-1]
    code_page = _CODE_PAGES.get(text.lower_invariant(charset))
    if code_page == _UTF7:
        raise UnexpectedError(
            "NotSupportedException",
            "Support for UTF-7 is disabled. See https://aka.ms/dotnet-warnings/SYSLIB0001 for more information.",
        )
    return code_page


def _decode(content: bytes, code_page: int) -> str:
    if code_page == 20127:
        return "".join(chr(byte) if byte < 0x80 else "?" for byte in content)
    if code_page == 28591:
        return content.decode("latin-1")
    return content.decode(_CODECS[code_page], "replace")


# Transport.


class TransportError(Exception):
    """A request that got no answer. ``kind`` is "certificate" (the TLS handshake failed, or the pin didn't match),
    "timeout" or "network"; ``detail`` is .NET's message for a network failure, such as
    "Connection refused (192.0.2.1:443)"."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class HueTransport(Protocol):
    """Sends one request to the bridge; raises :class:`TransportError` when no answer arrives."""

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> HttpAnswer: ...

    async def close(self) -> None: ...


type TransportFactory = Callable[[BridgeUrl, str | None, Callable[[str], None] | None], HueTransport]
"""Makes a transport for a bridge URL, a pinned SHA-256 (or none) and a callback for each certificate it sees."""

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
"""The Content-Type of a request with a body, as .NET's ``JsonContent`` writes it."""
_SSL_FAILED = "The SSL connection could not be established, see inner exception."
_SEND_FAILED = "An error occurred while sending the request."


def normalize_fingerprint(fingerprint: str) -> str:
    """``HueTls.Normalize``: without colons and spaces, trimmed, upper case."""
    return text.fold(text.trim(fingerprint.replace(":", "").replace(" ", "")))


class AiohttpTransport:
    """HTTPS to the bridge with its own connection pool, whose idle connections stay 5 minutes. When
    ``certificate_sha256`` is set, a connection is only used if the bridge's certificate has that SHA-256; otherwise any
    certificate is accepted, as in .NET."""

    def __init__(
        self,
        url: BridgeUrl,
        certificate_sha256: str | None = None,
        on_certificate: Callable[[str], None] | None = None,
    ) -> None:
        self._url = url
        pinned = (
            None if text.is_null_or_white_space(certificate_sha256) else normalize_fingerprint(certificate_sha256 or "")
        )
        if pinned is not None and (len(pinned) != 64 or not all(c in "0123456789ABCDEF" for c in pinned)):
            pinned = "0" * 64  # a pin that no certificate has, as a malformed one never matches in .NET
        self._check = _CertificateCheck(pinned, on_certificate)
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            connector = aiohttp.TCPConnector(ssl=self._check, keepalive_timeout=IDLE_TIMEOUT)
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
            # .NET's HttpClient sends none of these headers and doesn't decompress.
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                auto_decompress=False,
                skip_auto_headers=("Accept", "Accept-Encoding", "User-Agent"),
            )
        return self._session

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> HttpAnswer:
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = JSON_CONTENT_TYPE
        try:
            url = request_url(self._url, path)
            async with self._client().request(method, url, data=body, headers=request_headers) as response:
                content = await response.read()
                return HttpAnswer(response.status, response.reason or None, content, response.charset)
        except (aiohttp.ServerFingerprintMismatch, aiohttp.ClientSSLError) as err:
            raise TransportError("certificate", _SSL_FAILED) from err
        except TimeoutError as err:  # aiohttp's connect and read timeouts are TimeoutErrors too
            raise TransportError("timeout", "The request timed out.") from err
        except aiohttp.ClientConnectorError as err:
            raise TransportError("network", _connect_failure(err.os_error, self._url)) from err
        except (aiohttp.ClientError, ValueError) as err:
            raise TransportError("network", _SEND_FAILED) from err

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class _CertificateCheck(aiohttp.Fingerprint):
    """What ``HueTls.CreateHandler``'s validation callback does for every new TLS connection: report the SHA-256 of the
    bridge's certificate, then accept it only if it is the pinned one, when there is a pin. aiohttp calls ``check``
    right after each handshake."""

    def __init__(self, pinned: str | None, on_certificate: Callable[[str], None] | None) -> None:
        super().__init__(bytes.fromhex(pinned) if pinned is not None else bytes(32))
        self._pinned = pinned is not None
        self._on_certificate = on_certificate

    def check(self, transport: asyncio.Transport) -> None:
        if self._on_certificate is not None:
            ssl_object = transport.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
            if der:
                self._on_certificate(hashlib.sha256(der).hexdigest().upper())
        if self._pinned:
            super().check(transport)


def aiohttp_transport(
    url: BridgeUrl, certificate_sha256: str | None, on_certificate: Callable[[str], None] | None
) -> HueTransport:
    return AiohttpTransport(url, certificate_sha256, on_certificate)


def request_url(url: BridgeUrl, path: str) -> URL:
    """The URL of a request for ``path``, a path below the bridge's base URL whose escapes are kept as they are."""
    return url.url.with_path(f"/{path}", encoded=True)


def _connect_failure(error: OSError, url: BridgeUrl) -> str:
    """.NET's message for a connection that failed, such as "Connection refused (192.0.2.1:443)"."""
    if isinstance(error, ConnectionResetError):
        # aiohttp connects and shakes hands in one step; .NET reports a connection closed during the handshake so.
        return _SSL_FAILED
    if isinstance(error, socket.gaierror) and error.strerror:
        reason = error.strerror
    elif error.errno is not None and error.errno > 0:
        reason = os.strerror(error.errno)
    else:
        reason = str(error) or _SEND_FAILED
    return f"{reason} ({url.host}:{url.port})"


# The client.


class HueClient:
    """A minimal CLIP API v2 client for the local bridge."""

    def __init__(
        self,
        bridge_ip: str,
        app_key: str,
        certificate_sha256: str | None,
        transport_factory: TransportFactory = aiohttp_transport,
    ) -> None:
        """Raises :class:`ConfigError` for a malformed bridge address or application key."""
        url = create_base_url(bridge_ip)
        self.app_key = _check_app_key(app_key)
        self._headers = {"hue-application-key": self.app_key}
        self._transport = transport_factory(url, certificate_sha256, None)

    async def get_lights(self) -> list[HueLight]:
        lights = await self._resources("light")
        return [
            HueLight(light.id, light.name if light.name is not None else light.id, bool(light.on))
            for light in _items(lights)
        ]

    async def get_groups(self) -> list[HueGroup]:
        rooms = await self._resources("room")
        zones = await self._resources("zone")
        return [_group(resource, "room") for resource in _items(rooms)] + [
            _group(resource, "zone") for resource in _items(zones)
        ]

    async def get_light_states(self) -> dict[str, bool]:
        return _states(await self._resources("light"))

    async def get_grouped_light_states(self) -> dict[str, bool]:
        return _states(await self._resources("grouped_light"))

    async def set_on(self, target: HueTarget, on: bool) -> None:
        resource_type = "light" if target.kind == HueTargetKind.LIGHT else "grouped_light"
        body = json.dumps({"on": {"on": on}}, separators=(",", ":")).encode()
        await self._send("PUT", f"clip/v2/resource/{resource_type}/{_escape(target.id)}", body)

    async def ping(self) -> None:
        """A cheap authenticated request; it keeps the TLS connection warm and tells whether the bridge is usable."""
        await self._resources("bridge")

    async def close(self) -> None:
        await self._transport.close()

    async def _resources(self, resource_type: str) -> list[_Resource | None]:
        body = await self._send("GET", f"clip/v2/resource/{resource_type}", None)
        try:
            envelope = _envelope(body)
        except ValueError as err:
            raise HueApiError(f"Unexpected response from the Hue bridge for '{resource_type}'.") from err
        return [] if envelope is None or envelope.data is None else envelope.data

    async def _send(self, method: str, path: str, body: bytes | None) -> str:
        try:
            answer = await self._transport.send(method, path, body, self._headers)
        except TransportError as err:
            if err.kind == "certificate":
                raise HueApiError(CERTIFICATE_MISMATCH) from err
            if err.kind == "timeout":
                raise HueApiError("Hue bridge request timed out.") from err
            raise HueApiError(f"Hue bridge not reachable: {err.detail}") from err

        content = answer.read_as_string()
        if answer.is_success:
            # CLIP v2 can answer 2xx and still report errors, e.g. 207 for an unreachable light: "device (light) has
            # communication issues, command (.on.on) may not have effect". Those requests did not succeed.
            errors_found = _try_read_errors(content)
            if errors_found is not None:
                raise HueApiError(f"The Hue bridge reported errors for {method} /{path}: {errors_found}", answer.status)
            return content

        description = _try_read_errors(content)
        if answer.status in (401, 403):
            detail = "" if description is None else f" ({description})"
            message = f"The Hue bridge rejected the application key{detail}; {PAIR_AGAIN}."
        else:
            detail = "" if description is None else f": {description}"
            message = f"Hue bridge returned {answer.status} {answer.reason_phrase or ''} for {method} /{path}{detail}"
        raise HueApiError(message, answer.status)


def _check_app_key(app_key: str) -> str:
    # The key goes into an HTTP header; a newline or other control character would break the request. Real keys are
    # plain ASCII.
    if any(not char.isascii() or ord(char) < 0x20 or ord(char) == 0x7F for char in app_key):
        raise ConfigError(
            f"The Hue application key contains characters that can't be sent to the bridge; {PAIR_AGAIN}."
        )
    return app_key


def _escape(resource_id: str | None) -> str:
    """A resource id as one segment of a path: percent-encoded, dots too, so that ids such as ``..`` stay in it."""
    if resource_id is None:
        raise errors.argument_null("stringToEscape")
    return quote(resource_id, safe="", errors="replace").replace(".", "%2E")


# The CLIP v2 envelope, read as .NET's JsonSerializer reads HueEnvelope with JsonSerializerDefaults.Web: property names
# match without regard to case, the last of duplicate properties wins, unknown properties are skipped, and a value of
# the wrong JSON type, or a string with a lone surrogate, is an error. A null in a list stays, as in .NET, where reading
# it throws a NullReferenceException.


@dataclass(frozen=True, slots=True)
class _Service:
    rid: str | None
    rtype: str | None


@dataclass(frozen=True, slots=True)
class _Resource:
    id: str | None
    name: str | None
    on: bool | None
    services: list[_Service | None] | None


class _NullItem:
    """A null in the list of errors, unlike an error without a description: .NET throws when it reads it."""


_NULL_ITEM = _NullItem()


@dataclass(frozen=True, slots=True)
class _Envelope:
    errors: list[str | _NullItem | None] | None
    data: list[_Resource | None] | None


class _MismatchError(ValueError):
    """The JSON doesn't fit the type .NET deserializes it into (a ``JsonException`` there)."""


class _Pairs(list[tuple[str, Any]]):
    """A JSON object as its properties, in order."""

    def get(self, name: str) -> Any:
        found: Any = _MISSING
        for key, value in self:
            if text.equals_ignore_case(key, name):
                found = value
        return found


_MISSING = object()


def _parse(body: str) -> Any:
    if nesting_depth(body) > MAX_DEPTH:
        raise _MismatchError("The maximum configured depth of 64 has been exceeded.")
    return json.loads(body, object_pairs_hook=_Pairs, parse_constant=reject_constant)


def _envelope(body: str) -> _Envelope | None:
    """``JsonSerializer.Deserialize<HueEnvelope>``; raises ``ValueError`` where .NET throws ``JsonException``."""
    root = _parse(body)
    if root is None:
        return None
    root = _require_object(root)
    return _Envelope(_list(root.get("errors"), _error, _NULL_ITEM), _list(root.get("data"), _resource))


def _list(value: Any, item: Callable[[Any], Any], null: Any = None) -> list[Any] | None:
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, list) or isinstance(value, _Pairs):
        raise _MismatchError("The JSON value could not be converted to a list.")
    return [null if element is None else item(element) for element in value]


def _object(value: Any) -> _Pairs | None:
    if value is _MISSING or value is None:
        return None
    return _require_object(value)


def _require_object(value: Any) -> _Pairs:
    if not isinstance(value, _Pairs):
        raise _MismatchError("The JSON value could not be converted to an object.")
    # The serializer reads every property name of a type it fills; a lone surrogate in one is an error.
    if any(_has_lone_surrogate(key) for key, _ in value):
        raise _MismatchError("Cannot read invalid UTF-16 JSON text as string.")
    return value


def _string(value: Any) -> str | None:
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str):
        raise _MismatchError("The JSON value could not be converted to System.String.")
    if _has_lone_surrogate(value):
        raise _MismatchError("Cannot read invalid UTF-16 JSON text as string.")
    return value


def _has_lone_surrogate(value: str) -> bool:
    return any("\ud800" <= char <= "\udfff" for char in value)


def _error(value: Any) -> str | None:
    error = _require_object(value)
    return _string(error.get("description"))


def _resource(value: Any) -> _Resource:
    resource = _require_object(value)
    metadata = _object(resource.get("metadata"))
    on = _object(resource.get("on"))
    on_value = None
    if on is not None:
        flag = on.get("on")
        if flag is _MISSING:
            on_value = False
        elif not isinstance(flag, bool):
            raise _MismatchError("The JSON value could not be converted to System.Boolean.")
        else:
            on_value = flag
    resource_id = resource.get("id")
    return _Resource(
        id="" if resource_id is _MISSING else _string(resource_id),
        name=None if metadata is None else _string(metadata.get("name")),
        on=on_value,
        services=_list(resource.get("services"), _service),
    )


def _service(value: Any) -> _Service:
    service = _require_object(value)
    rid, rtype = service.get("rid"), service.get("rtype")
    return _Service("" if rid is _MISSING else _string(rid), "" if rtype is _MISSING else _string(rtype))


def _try_read_errors(body: str) -> str | None:
    """The descriptions in a CLIP v2 ``errors`` array, or ``None`` if there are none or the body can't be read."""
    try:
        envelope = _envelope(body)
    except ValueError:
        return None
    if envelope is None or not envelope.errors:
        return None
    descriptions: list[str] = []
    for error in envelope.errors:
        if isinstance(error, _NullItem):
            raise errors.null_reference()
        descriptions.append("no description" if text.is_null_or_white_space(error) else (error or ""))
    return "; ".join(descriptions)


def _items(resources: list[_Resource | None]) -> list[_Resource]:
    """The resources, failing on a null among them as .NET's ``Select`` does."""
    if any(resource is None for resource in resources):
        raise errors.null_reference()
    return [resource for resource in resources if resource is not None]


def _states(resources: list[_Resource | None]) -> dict[str, bool]:
    """``Where(r => r.On is not null).ToDictionary(r => r.Id, r => r.On!.On, OrdinalIgnoreCase)``."""
    states: dict[str, bool] = {}
    folded: set[str] = set()
    for resource in resources:
        if resource is None:
            raise errors.null_reference()
        if resource.on is None:
            continue
        if resource.id is None:
            raise errors.argument_null("key")
        key = text.fold(resource.id)
        if key in folded:
            raise UnexpectedError(
                "ArgumentException", f"An item with the same key has already been added. Key: {resource.id}"
            )
        folded.add(key)
        states[resource.id] = resource.on
    return _CaseInsensitive(states)


class _CaseInsensitive(dict[str, bool]):
    """The states, keyed by resource id without regard to case, as the .NET dictionary is."""

    def __init__(self, states: dict[str, bool]) -> None:
        super().__init__(states)
        self._folded = {text.fold(key): value for key, value in states.items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self._folded.get(text.fold(key), default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and text.fold(key) in self._folded


def _group(resource: _Resource, kind: str) -> HueGroup:
    """``Services?.FirstOrDefault(s => s.Rtype == "grouped_light")?.Rid``, failing on a null service before a match."""
    grouped: str | None = None
    for service in resource.services or []:
        if service is None:
            raise errors.null_reference()
        if service.rtype == "grouped_light":
            grouped = service.rid
            break
    return HueGroup(resource.id, resource.name if resource.name is not None else resource.id, kind, grouped)
