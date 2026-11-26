"""The Hue bridge's local CLIP API v2 over HTTPS, with the bridge's certificate pinned.

The bridge serves a certificate that is self-signed or signed by Signify's private CA, issued to the bridge id rather
than its address, so normal validation can never succeed. The certificate is pinned when pairing (trust on first use)
and checked by its SHA-256 afterwards. The client keeps its TLS connection open for 5 minutes, so that a press doesn't
wait for a handshake.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
from http import HTTPStatus
import json
import os
import re
import socket
from typing import Any, Protocol, Self
from urllib.parse import quote

import aiohttp
from yarl import URL

from .config import ConfigError
from .hue_address import BridgeUrl, create_base_url

PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"
"""What to do when the bridge no longer accepts the application key or presents another certificate."""
CERTIFICATE_MISMATCH = (
    "The Hue bridge TLS certificate does not match the pinned certificateSha256. If the bridge was replaced or reset, "
    f"{PAIR_AGAIN}."
)
"""Why a request failed when the bridge's certificate isn't the pinned one, or the TLS handshake failed."""

REQUEST_TIMEOUT = 5.0
CONNECT_TIMEOUT = 4.0
IDLE_TIMEOUT = 300.0
"""Seconds that an idle connection to the bridge stays open, much longer than the keep-alive's interval."""

ZONE_ARCHETYPE = "other"
"""The archetype of the zones that sonoff2hue creates: of the archetypes of rooms and zones, the one for any use."""
ZONE_READS = 5
ZONE_READ_DELAY = 0.5
"""A new zone is read up to 5 times, 0.5 s apart, until it has its grouped light, which the bridge may add a moment
after the zone: about 2 s in all, at most two requests a second."""

_TRANSIENT = frozenset(
    {
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
    }
)


class HueTargetKind(Enum):
    LIGHT = "Light"
    GROUPED_LIGHT = "GroupedLight"


@dataclass(frozen=True, slots=True)
class HueTarget:
    """Something that can be switched on and off: a light, or the grouped_light of a room or zone. Its label, such as
    ``light 'Ceiling'``, names it in the log, the status and the events, followed by ``label_id`` when that is set:
    the resolver sets it when another resource of its kind has the same name."""

    kind: HueTargetKind
    id: str
    name: str
    label_id: str | None = None
    """The id that the label shows: the light's own, or the room's or zone's, not its grouped_light's."""

    def __str__(self) -> str:
        label = f"light '{self.name}'" if self.kind == HueTargetKind.LIGHT else f"group '{self.name}'"
        return label if self.label_id is None else f"{label} ({self.label_id})"


@dataclass(frozen=True, slots=True)
class HueLight:
    """A light; its name is its id when the bridge gives it none."""

    id: str
    name: str
    on: bool


@dataclass(frozen=True, slots=True)
class HueGroup:
    """A room or zone; its name is its id when the bridge gives it none. ``grouped_light_id`` is the resource that
    switches all its lights, ``None`` for a room or zone without lights. ``light_ids`` are the ids of the lights it
    holds (:meth:`HueClient.get_lights_and_groups`)."""

    id: str
    name: str
    kind: str
    grouped_light_id: str | None
    light_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class HueBridgeInfo:
    ip: str
    bridge_id: str
    name: str | None
    model_id: str | None
    software_version: str | None
    certificate_sha256: str


class HueApiError(Exception):
    """A request to the bridge failed. ``status`` is the HTTP status of the bridge's answer, ``None`` without one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def is_authentication_error(self) -> bool:
        return self.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)

    @property
    def is_transient(self) -> bool:
        """Whether trying again later may help: the bridge didn't answer, or answered that it is busy or failing."""
        return self.status is None or self.status in _TRANSIENT


class HueApi(Protocol):
    """What the toggle logic needs; :class:`HueClient` has it."""

    async def get_light_states(self) -> dict[str, bool]: ...

    async def get_grouped_light_states(self) -> dict[str, bool]: ...

    async def set_on(self, target: HueTarget, on: bool) -> None: ...


# HTTP.


def load_json(text: str) -> Any:
    """``json.loads``; raises ``ValueError`` for anything that isn't JSON, also for JSON nested too deeply to read."""
    try:
        return json.loads(text)
    except RecursionError as err:
        raise ValueError("The JSON is nested too deeply.") from err


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
        """The status line's reason phrase, else the standard one of the status, if it has one."""
        if self.reason:
            return self.reason
        try:
            return HTTPStatus(self.status).phrase
        except ValueError:
            return None

    @property
    def is_success(self) -> bool:
        return 200 <= self.status <= 299

    def text(self) -> str:
        """The body, decoded by its charset, or as UTF-8 without one or for a charset that Python has no text codec
        for; bytes that don't decode become U+FFFD, and a byte order mark is dropped."""
        try:
            text = self.content.decode(self.charset.strip('"') if self.charset else "utf-8", "replace")
        except LookupError:
            text = self.content.decode("utf-8", "replace")
        return text.removeprefix("\ufeff")

    def json(self) -> Any:
        """The body as JSON; raises ``ValueError`` when it isn't."""
        return load_json(self.text())


# Transport.


class TransportError(Exception):
    """A request that got no answer. ``kind`` is "certificate" (the TLS handshake failed, or the certificate isn't the
    pinned one), "timeout" or "network"; ``detail`` says what happened, such as "Connection refused (192.0.2.1:443)"."""

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

JSON_CONTENT_TYPE = "application/json"
"""The Content-Type of a request with a body."""

_SHA256 = re.compile(r"[0-9A-F]{64}")
_NO_CERTIFICATE = bytes(32)
"""The pin of a malformed fingerprint, which no certificate has."""


def normalize_fingerprint(fingerprint: str) -> str:
    """A SHA-256 fingerprint as upper-case hex, without the colons and white space it may be written with."""
    return re.sub(r"[\s:]", "", fingerprint).upper()


def matches_pin(certificate_sha256: str, pin: str) -> bool:
    """Whether a certificate with this SHA-256 is the pinned one, as the client's connections check it: both
    normalized, and never for a malformed pin."""
    pinned = normalize_fingerprint(pin)
    return _SHA256.fullmatch(pinned) is not None and normalize_fingerprint(certificate_sha256) == pinned


class AiohttpTransport:
    """HTTPS to the bridge with its own connection pool, whose idle connections stay open for 5 minutes. When
    ``certificate_sha256`` is set, a connection is used only if the bridge's certificate has that SHA-256; otherwise
    any certificate is accepted."""

    def __init__(
        self,
        url: BridgeUrl,
        certificate_sha256: str | None = None,
        on_certificate: Callable[[str], None] | None = None,
    ) -> None:
        self._url = url
        self._where = f"{url.host}:{url.port}"
        pinned: bytes | None = None
        if certificate_sha256 and not certificate_sha256.isspace():
            fingerprint = normalize_fingerprint(certificate_sha256)
            pinned = bytes.fromhex(fingerprint) if _SHA256.fullmatch(fingerprint) else _NO_CERTIFICATE
        self._check = _CertificateCheck(pinned, on_certificate)
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            # The system's resolver, which also answers .local names where the system can: with aiodns installed,
            # aiohttp's default resolver would be c-ares.
            connector = aiohttp.TCPConnector(
                ssl=self._check, keepalive_timeout=IDLE_TIMEOUT, resolver=aiohttp.ThreadedResolver()
            )
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> HttpAnswer:
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = JSON_CONTENT_TYPE
        try:
            async with self._client().request(
                method, request_url(self._url, path), data=body, headers=request_headers
            ) as response:
                content = await response.read()
                return HttpAnswer(response.status, response.reason or None, content, response.charset)
        except aiohttp.ServerFingerprintMismatch as err:
            raise TransportError("certificate", f"The certificate isn't the pinned one ({self._where})") from err
        except aiohttp.ClientSSLError as err:
            raise TransportError("certificate", f"The TLS handshake failed ({self._where})") from err
        except TimeoutError as err:  # aiohttp's connect and read timeouts are TimeoutErrors too
            raise TransportError("timeout", "The request timed out.") from err
        except aiohttp.ClientConnectorError as err:
            raise TransportError("network", f"{_reason(err.os_error)} ({self._where})") from err
        except aiohttp.ClientError as err:
            raise TransportError("network", f"{str(err) or 'The request failed'} ({self._where})") from err

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class _CertificateCheck(aiohttp.Fingerprint):
    """Reports the SHA-256 of the bridge's certificate for every new TLS connection, then accepts the connection only if
    the certificate is the pinned one, when there is a pin. aiohttp calls ``check`` right after each handshake."""

    def __init__(self, pinned: bytes | None, on_certificate: Callable[[str], None] | None) -> None:
        super().__init__(_NO_CERTIFICATE if pinned is None else pinned)
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


def _reason(error: OSError) -> str:
    """Why a connection failed, in the system's words where it has them, such as "Connection refused"."""
    if error.errno is not None and not isinstance(error, socket.gaierror):
        return os.strerror(error.errno)
    if reason := error.strerror or str(error):
        return reason
    return "The connection was closed" if isinstance(error, ConnectionError) else "The connection failed"


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
        return [_light(light) for light in await self._resources("light")]

    async def get_lights_and_groups(self) -> tuple[list[HueLight], list[HueGroup]]:
        """The lights, then the rooms and zones, each with the lights it holds (``HueGroup.light_ids``): the lights
        among its children, those of the devices among them, which each light's owner names, and those of the rooms and
        zones among them. A room's children are devices, and a zone's are lights."""
        lights = await self._related("light")
        rooms = await self._related("room")
        zones = await self._related("zone")
        return [_light(light.resource) for light in lights], _groups(lights, rooms, zones)

    async def get_light_states(self) -> dict[str, bool]:
        return _states(await self._resources("light"))

    async def get_grouped_light_states(self) -> dict[str, bool]:
        return _states(await self._resources("grouped_light"))

    async def set_on(self, target: HueTarget, on: bool) -> None:
        if not target.id:
            raise ValueError(f"The Hue {target} has no id.")
        resource_type = "light" if target.kind == HueTargetKind.LIGHT else "grouped_light"
        body = json.dumps({"on": {"on": on}}, separators=(",", ":")).encode()
        await self._send("PUT", f"clip/v2/resource/{resource_type}/{_escape(target.id)}", resource_type, body)

    async def create_zone(self, name: str, light_ids: Sequence[str]) -> HueGroup:
        """Creates a zone of these lights, in this order, and returns it as the bridge then lists it, with the grouped
        light that switches them all. The bridge may add the grouped light a moment after the zone, so the zone is read
        up to 5 times, 0.5 s apart, until it has one, and returned without one after that. Raises
        :class:`HueApiError`; an answer that doesn't name the new zone, or a reading without it, is unexpected."""
        children = [{"rid": light_id, "rtype": "light"} for light_id in light_ids]
        body = {"children": children, "metadata": {"name": name, "archetype": ZONE_ARCHETYPE}}
        data = await self._send(
            "POST", "clip/v2/resource/zone", "zone", json.dumps(body, separators=(",", ":")).encode()
        )
        created = [reference.rid for reference in _parse(data, _Reference.parse, "zone") if reference.rtype == "zone"]
        if not created:
            raise _unexpected("zone")
        zone = await self._zone(created[0])
        for _ in range(ZONE_READS - 1):
            if zone.grouped_light_id is not None:
                break
            await asyncio.sleep(ZONE_READ_DELAY)
            zone = await self._zone(zone.id)
        return zone

    async def ping(self) -> None:
        """A cheap authenticated request; it keeps the TLS connection warm and tells whether the bridge is usable."""
        await self._resources("bridge")

    async def close(self) -> None:
        await self._transport.close()

    async def _resources(self, resource_type: str) -> list[_Resource]:
        return _parse(await self._get(resource_type), _Resource.parse, resource_type)

    async def _related(self, resource_type: str) -> list[_Related]:
        return _parse(await self._get(resource_type), _Related.parse, resource_type)

    async def _get(self, resource_type: str) -> list[dict[str, Any]]:
        return await self._send("GET", f"clip/v2/resource/{resource_type}", resource_type)

    async def _zone(self, zone_id: str) -> HueGroup:
        """The zone with this id, with the lights among its children."""
        data = await self._send("GET", f"clip/v2/resource/zone/{_escape(zone_id)}", "zone")
        zones = [zone for zone in _parse(data, _Related.parse, "zone") if zone.resource.id == zone_id]
        if not zones:
            raise _unexpected("zone")
        return _groups([], [], zones[:1])[0]

    async def _send(self, method: str, path: str, resource_type: str, body: bytes | None = None) -> list[Any]:
        """Sends a request and returns the data of the bridge's answer. Raises :class:`HueApiError` when there is no
        answer, when the answer is a failure or reports errors, and when it isn't a CLIP v2 answer."""
        try:
            answer = await self._transport.send(method, path, body, self._headers)
        except TransportError as err:
            match err.kind:
                case "certificate":
                    raise HueApiError(CERTIFICATE_MISMATCH) from err
                case "timeout":
                    raise HueApiError("Hue bridge request timed out.") from err
                case _:
                    raise HueApiError(f"Hue bridge not reachable: {err.detail}") from err

        content = answer.text()
        if answer.is_success:
            try:
                envelope = _Envelope.parse(content)
            except ValueError as err:
                raise _unexpected(resource_type) from err
            # CLIP v2 can answer 2xx and still report errors, e.g. 207 for an unreachable light: "device (light) has
            # communication issues, command (.on.on) may not have effect". Those requests did not succeed.
            if envelope.errors:
                raise HueApiError(
                    f"The Hue bridge reported errors for {method} /{path}: {'; '.join(envelope.errors)}", answer.status
                )
            return envelope.data

        try:
            description = "; ".join(_Envelope.parse(content).errors)
        except ValueError:
            description = ""
        if answer.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            detail = f" ({description})" if description else ""
            message = f"The Hue bridge rejected the application key{detail}; {PAIR_AGAIN}."
        else:
            phrase = answer.reason_phrase
            status = f"{answer.status} {phrase}" if phrase else str(answer.status)
            detail = f": {description}" if description else ""
            message = f"Hue bridge returned {status} for {method} /{path}{detail}"
        raise HueApiError(message, answer.status)


def _check_app_key(app_key: str) -> str:
    # The key goes into an HTTP header; a newline or other control character would break the request. Real keys are
    # plain ASCII.
    if not (app_key.isascii() and app_key.isprintable()):
        raise ConfigError(
            f"The Hue application key contains characters that can't be sent to the bridge; {PAIR_AGAIN}."
        )
    return app_key


def _escape(resource_id: str) -> str:
    """A resource id as one segment of a path: percent-encoded, dots too, so that ids such as ``..`` stay in it."""
    return quote(resource_id, safe="", errors="replace").replace(".", "%2E")


def _unexpected(resource_type: str) -> HueApiError:
    return HueApiError(f"Unexpected response from the Hue bridge for '{resource_type}'.")


# CLIP v2 answers. An answer is a JSON object with the lists "errors" and "data", both lists of objects. A key whose
# value is null counts as missing; any other value has to be of its kind. A null in a list of objects makes the answer
# malformed, as does a value of another kind or a missing key that is needed, such as a resource's id.


class _MalformedError(ValueError):
    """An answer that isn't a CLIP v2 answer."""


def _field[T](obj: dict[str, Any], key: str, kind: type[T]) -> T | None:
    """``obj[key]``, or ``None`` when it is missing or null; raises :class:`_MalformedError` when it isn't a
    ``kind``."""
    value = obj.get(key)
    if value is not None and not isinstance(value, kind):
        raise _MalformedError(f"'{key}' is not a {kind.__name__}: {value!r}")
    return value


def _objects(obj: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The list of objects ``obj[key]``, empty when it is missing or null; raises :class:`_MalformedError` for anything
    else."""
    items = _field(obj, key, list) or []
    if not all(isinstance(item, dict) for item in items):
        raise _MalformedError(f"'{key}' is not a list of objects")
    return items


def _text(obj: dict[str, Any], key: str) -> str:
    """The text ``obj[key]``, such as an id, which has to be there and not be empty."""
    value = _field(obj, key, str)
    if not value:
        raise _MalformedError(f"'{key}' is missing")
    return value


@dataclass(frozen=True, slots=True)
class _Envelope:
    """A CLIP v2 answer: the description of each error it reports, or "no description", and its data."""

    errors: list[str]
    data: list[dict[str, Any]]

    @classmethod
    def parse(cls, content: str) -> Self:
        answer = load_json(content)
        if not isinstance(answer, dict):
            raise _MalformedError("The answer is not a JSON object.")
        descriptions = [_field(error, "description", str) for error in _objects(answer, "errors")]
        return cls(
            [text if text and not text.isspace() else "no description" for text in descriptions],
            _objects(answer, "data"),
        )


@dataclass(frozen=True, slots=True)
class _Reference:
    """A reference to another resource, such as a service, a child or an owner: its id and its type."""

    rid: str
    rtype: str

    @classmethod
    def parse(cls, reference: dict[str, Any]) -> Self:
        return cls(_text(reference, "rid"), _text(reference, "rtype"))


@dataclass(frozen=True, slots=True)
class _Resource:
    id: str
    name: str | None
    on: bool | None
    services: list[_Reference]

    @classmethod
    def parse(cls, resource: dict[str, Any]) -> Self:
        on = _field(resource, "on", dict)
        if on is not None and not isinstance(on.get("on"), bool):
            raise _MalformedError("'on' has no boolean 'on'")
        return cls(
            _text(resource, "id"),
            _field(_field(resource, "metadata", dict) or {}, "name", str),
            None if on is None else on["on"],
            [_Reference.parse(service) for service in _objects(resource, "services")],
        )


@dataclass(frozen=True, slots=True)
class _Related:
    """A light, room or zone with what relates it to other resources: its owner, such as a light's device, and its
    children, such as a room's devices or a zone's lights."""

    resource: _Resource
    owner: _Reference | None
    children: list[_Reference]

    @classmethod
    def parse(cls, resource: dict[str, Any]) -> Self:
        owner = _field(resource, "owner", dict)
        return cls(
            _Resource.parse(resource),
            None if owner is None else _Reference.parse(owner),
            [_Reference.parse(child) for child in _objects(resource, "children")],
        )


def _parse[T](items: list[dict[str, Any]], parse: Callable[[dict[str, Any]], T], resource_type: str) -> list[T]:
    """The resources of an answer's data, each read by ``parse``; a malformed one makes the answer unexpected."""
    try:
        return [parse(item) for item in items]
    except ValueError as err:
        raise _unexpected(resource_type) from err


def _states(resources: list[_Resource]) -> dict[str, bool]:
    """Whether each resource that can be switched is on, by its id."""
    return {resource.id: resource.on for resource in resources if resource.on is not None}


def _light(resource: _Resource) -> HueLight:
    return HueLight(resource.id, resource.name if resource.name is not None else resource.id, bool(resource.on))


def _groups(lights: list[_Related], rooms: list[_Related], zones: list[_Related]) -> list[HueGroup]:
    """The rooms, then the zones, each with the lights it holds: the lights among its children, the lights that a
    device among them owns, and the lights of a room or zone among them. A child of another kind holds no lights."""
    owned: dict[str, set[str]] = {}
    for light in lights:
        if light.owner is not None and light.owner.rtype == "device":
            owned.setdefault(light.owner.rid, set()).add(light.resource.id)
    groups = [("room", room) for room in rooms] + [("zone", zone) for zone in zones]
    children = {(kind, group.resource.id): group.children for kind, group in groups}

    def held(kind: str, group_id: str) -> frozenset[str]:
        found: set[str] = set()
        seen = {(kind, group_id)}
        pending = list(children[kind, group_id])
        while pending:
            child = pending.pop()
            key = (child.rtype, child.rid)
            if child.rtype == "light":
                found.add(child.rid)
            elif child.rtype == "device":
                found |= owned.get(child.rid, set())
            elif key in children and key not in seen:
                seen.add(key)
                pending += children[key]
        return frozenset(found)

    return [_group(group.resource, kind, held(kind, group.resource.id)) for kind, group in groups]


def _group(resource: _Resource, kind: str, light_ids: frozenset[str]) -> HueGroup:
    grouped_light = next((service.rid for service in resource.services if service.rtype == "grouped_light"), None)
    name = resource.name if resource.name is not None else resource.id
    return HueGroup(resource.id, name, kind, grouped_light, light_ids)
