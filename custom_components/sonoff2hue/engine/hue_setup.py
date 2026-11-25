"""Finding Hue bridges and pairing with one: a port of the .NET app's ``Hue/HueBridgeSetup.cs``, with the pairing loop of
``Web/Management/HuePairing.cs``.

Pairing pins the certificate the bridge presents when it is asked for its public config (trust on first use), then asks
it for an application key every 2 seconds for 60 seconds, while the user presses its link button.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import logging
import socket
import unicodedata

import aiohttp

from . import jsonnode, text
from .dns import DnsRecordType, ends_with_ignoring_case, equals_ignoring_case
from .hue_address import create_base_url
from .hue_client import HttpAnswer, HueApiError, HueBridgeInfo, TransportError, TransportFactory, aiohttp_transport
from .mdns import MdnsListener, MdnsPacket

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE = "_hue._tcp.local"
CLOUD_DISCOVERY_URL = "https://discovery.meethue.com/"
DISCOVERY_TIMEOUT = 5.0
PAIRING_WINDOW = 60.0
PAIRING_INTERVAL = 2.0


@dataclass(frozen=True, slots=True)
class DiscoveredBridge:
    ip: str
    bridge_id: str | None
    source: str


class PairingStatus(Enum):
    PAIRED = "Paired"
    LINK_BUTTON_NOT_PRESSED = "LinkButtonNotPressed"
    REQUEST_FAILED = "RequestFailed"
    """The attempt failed in a way that may go away (timeout, connection reset, busy bridge): try again."""


@dataclass(frozen=True, slots=True)
class PairingResult:
    """The outcome of one pairing attempt; ``error`` explains a :attr:`PairingStatus.REQUEST_FAILED`."""

    status: PairingStatus
    app_key: str | None
    error: str | None = None


class PairingTimeoutError(Exception):
    """The link button wasn't pressed within the pairing window. ``last_error`` is the failure of the last attempt, if
    it failed."""

    def __init__(self, window: float, last_error: str | None) -> None:
        super().__init__(f"The link button was not pressed within {window:g} seconds. Try again.")
        self.last_error = last_error


# Discovery.


async def discover(
    session: aiohttp.ClientSession,
    duration: float = DISCOVERY_TIMEOUT,
    listener_factory: Callable[[], MdnsListener] = MdnsListener,
) -> list[DiscoveredBridge]:
    """Bridges found with mDNS and with Signify's discovery endpoint within ``duration`` seconds: the first answer for
    each address, sorted by address."""
    found: dict[str, DiscoveredBridge] = {}
    # Both run to the end; then the first failure, if any, is raised.
    outcomes = await asyncio.gather(
        _discover_mdns(found, duration, listener_factory),
        _discover_cloud(found, duration, session),
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return sorted(found.values(), key=lambda bridge: bridge.ip)


async def _discover_mdns(
    found: dict[str, DiscoveredBridge], duration: float, listener_factory: Callable[[], MdnsListener]
) -> None:
    listener = listener_factory()

    async def query(listener: MdnsListener) -> None:
        for delay in (0, 1, 2):
            if delay > 0:  # a delay of 0 isn't awaited: the first query goes out at once
                await asyncio.sleep(delay)
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)

    def on_packet(packet: MdnsPacket) -> None:
        if not packet.message.is_response:
            return
        records = packet.message.all_records
        for record in records:
            if (
                record.type != DnsRecordType.TXT
                or record.ttl == 0
                or not ends_with_ignoring_case(record.name, "." + SERVICE_TYPE)
            ):
                continue
            target = next(
                (
                    r.srv_target
                    for r in records
                    if r.type == DnsRecordType.SRV and equals_ignoring_case(r.name, record.name)
                ),
                None,
            )
            address = next(
                (
                    r.address
                    for r in records
                    if r.type == DnsRecordType.A and target is not None and equals_ignoring_case(r.name, target)
                ),
                None,
            )
            if address is not None:
                ip = str(address)
            elif packet.from_this_host:
                # A relay's copy, such as the one that Home Assistant OS's multicast plugin repeats from its `hassio`
                # bridge, doesn't say where the bridge is (decision 19).
                continue
            else:
                ip = packet.remote[0]
            bridge_id = record.txt.get("bridgeid") if record.txt is not None else None
            found.setdefault(ip, DiscoveredBridge(ip, bridge_id, "mDNS"))

    listener.add_started(query)
    listener.add_packet_handler(on_packet)
    task = asyncio.ensure_future(listener.run())
    try:
        await asyncio.wait({task}, timeout=duration)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as err:  # noqa: BLE001 - discovery is best effort
            _LOGGER.debug("mDNS bridge discovery failed: %s", err)


async def _discover_cloud(found: dict[str, DiscoveredBridge], duration: float, session: aiohttp.ClientSession) -> None:
    """Asks Signify's discovery endpoint, which lists the bridges that reach the internet from this network. It is rate
    limited and needs the internet, and mDNS usually suffices, so a failure is only logged at debug level."""
    try:
        async with session.get(CLOUD_DISCOVERY_URL, timeout=aiohttp.ClientTimeout(total=duration)) as response:
            response.raise_for_status()
            answer = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError, RecursionError) as err:  # RecursionError: nested too deeply
        _LOGGER.debug("Cloud bridge discovery failed: %s", str(err) or type(err).__name__)
        return
    if not isinstance(answer, list) or not all(isinstance(bridge, dict) for bridge in answer):
        _LOGGER.debug("Cloud bridge discovery failed: the answer isn't a list of bridges.")
        return
    _add_cloud_bridges(found, answer)


def _add_cloud_bridges(found: dict[str, DiscoveredBridge], bridges: list[dict[str, object]]) -> None:
    """Adds each bridge whose ``internalipaddress`` is an IP address, with its ``id`` if it is a text, unless an earlier
    answer named that address."""
    for bridge in bridges:
        if (ip := _ip_address(bridge.get("internalipaddress"))) is not None:
            bridge_id = bridge.get("id")
            found.setdefault(
                ip, DiscoveredBridge(ip, bridge_id if isinstance(bridge_id, str) else None, "discovery.meethue.com")
            )


def _ip_address(value: object) -> str | None:
    """``value`` as ``ipaddress`` writes the address, or ``None`` when it isn't an IP address."""
    if not isinstance(value, str):
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


# The bridge's identity and certificate.


async def get_bridge_info(ip: str, transport_factory: TransportFactory = aiohttp_transport) -> HueBridgeInfo:
    """The bridge's public config and the SHA-256 of the certificate it presents (``GetBridgeInfoAsync``). Raises
    :class:`HueApiError` when no Hue bridge answers, and ``ConfigError`` for a malformed address, before any request."""
    url = create_base_url(ip)
    fingerprints: list[str] = []
    transport = transport_factory(url, None, fingerprints.append)
    try:
        answer = await transport.send("GET", "api/0/config", None)
    except TransportError as err:
        detail = (
            "The request was canceled due to the configured HttpClient.Timeout of 5 seconds elapsing."
            if err.kind == "timeout"
            else err.detail
        )
        raise HueApiError(f"No Hue bridge answered at {ip}: {detail}") from err
    finally:
        await transport.close()

    if not answer.is_success:
        raise HueApiError(f"No Hue bridge answered at {ip}: {not_success_message(answer)}")
    try:
        config = jsonnode.parse(answer.read_json_text(), case_insensitive=True)
    except jsonnode.JsonError as err:
        raise HueApiError(f"No Hue bridge answered at {ip}: {err}") from err
    if config is not None and not isinstance(config, (dict, jsonnode.BrokenObject)):
        raise HueApiError(
            f"No Hue bridge answered at {ip}: The JSON value could not be converted to System.Text.Json.Nodes.JsonObject."
        )

    bridge_id = None if config is None else _text(config, "bridgeid")
    if config is None or bridge_id is None or not fingerprints:
        raise HueApiError(f"The device at {ip} does not look like a Hue bridge.")
    return HueBridgeInfo(
        ip, bridge_id, _text(config, "name"), _text(config, "modelid"), _text(config, "swversion"), fingerprints[-1]
    )


def not_success_message(answer: HttpAnswer) -> str:
    """``EnsureSuccessStatusCode``'s message."""
    reason = answer.reason_phrase
    if text.is_null_or_white_space(reason):
        return f"Response status code does not indicate success: {answer.status}."
    return f"Response status code does not indicate success: {answer.status} ({reason})."


def _text(obj: Mapping[str, jsonnode.JsonValue], key: str) -> str | None:
    """``json[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null``; reading a duplicate
    key or a lone surrogate throws, as in .NET."""
    value = obj.get(key)
    if isinstance(value, jsonnode.BrokenString):
        raise value.error()
    return value if isinstance(value, str) else None


# Pairing.


async def try_create_app_key(
    ip: str, certificate_sha256: str, device_type: str, transport_factory: TransportFactory = aiohttp_transport
) -> PairingResult:
    """One attempt to create an application key; it succeeds only within 30 seconds of a press of the link button.
    Failures worth another attempt come back as :attr:`PairingStatus.REQUEST_FAILED`; a changed certificate or a
    refusal by the bridge raises :class:`HueApiError`, and a malformed address ``ConfigError``, before any request."""
    url = create_base_url(ip)
    transport = transport_factory(url, certificate_sha256, None)
    body = json.dumps({"devicetype": device_type, "generateclientkey": True}, separators=(",", ":")).encode()
    try:
        answer = await transport.send("POST", "api", body)
    except TransportError as err:
        if err.kind == "certificate":
            raise HueApiError(
                f"The TLS certificate of {ip} changed during pairing. Make sure this address belongs to your Hue bridge "
                "and pair it again."
            ) from err
        if err.kind == "timeout":
            return PairingResult(PairingStatus.REQUEST_FAILED, None, "the request timed out")
        return PairingResult(PairingStatus.REQUEST_FAILED, None, f"bridge not reachable: {err.detail}")
    finally:
        await transport.close()
    return parse_pairing_response(answer.status, answer.read_as_string())


def parse_pairing_response(status: int, body: str) -> PairingResult:
    """Reads the answer to ``POST /api``, e.g. ``[{"success":{"username":"..."}}]``."""
    first: jsonnode.JsonValue = None
    try:
        parsed = jsonnode.parse(body)
        if isinstance(parsed, list) and parsed:
            first = parsed[0]
    except jsonnode.JsonError:
        pass

    if isinstance(first, (dict, jsonnode.BrokenObject)):
        success = first.get("success")
        if isinstance(success, (dict, jsonnode.BrokenObject)):
            app_key = _text(success, "username")
            if app_key:
                return PairingResult(PairingStatus.PAIRED, app_key)

        error = first.get("error")
        if isinstance(error, (dict, jsonnode.BrokenObject)):
            error_type = jsonnode.try_get_int32(error.get("type"))
            if error_type == 101:
                return PairingResult(PairingStatus.LINK_BUTTON_NOT_PRESSED, None)
            description = _text(error, "description")
            raise HueApiError(
                f"The Hue bridge refused pairing: {'no description' if description is None else description} "
                f"(error {'unknown' if error_type is None else error_type}).",
                status,
            )

    # Not the documented shape, e.g. an HTML error page from a busy bridge: worth another attempt.
    return PairingResult(PairingStatus.REQUEST_FAILED, None, f"unexpected response (HTTP {status}): {_shorten(body)}")


def _shorten(value: str) -> str:
    """The body on one line, cut after 120 UTF-16 code units; a character cut in half shows as U+FFFD, as .NET's lone
    surrogate does when it is written out."""
    single_line = " ".join(part for part in _split_white_space(value) if part)
    if not single_line:
        return "(empty body)"
    if text.utf16_length(single_line) <= 120:
        return single_line
    kept: list[str] = []
    length = 0
    for char in single_line:
        width = 2 if ord(char) > 0xFFFF else 1
        if length + width > 120:
            if length < 120:
                kept.append("\ufffd")
            break
        kept.append(char)
        length += width
    return "".join(kept) + "..."


def _split_white_space(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for char in value:
        if text.is_white_space(char):
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def build_device_type(host_name: str | None = None) -> str:
    """The ``devicetype`` to pair with, ``sonoff2hue#`` and the letters and digits of the host name: Hue limits it to
    ``<app, max 20>#<device, max 19>``. Like ``Environment.MachineName``, the host name ends at its first dot, and like
    ``char.IsLetterOrDigit``, characters outside the Basic Multilingual Plane don't count."""
    machine = (host_name if host_name is not None else socket.gethostname()).split(".", 1)[0]
    host = "".join(
        char
        for char in machine
        if ord(char) <= 0xFFFF and unicodedata.category(char) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nd")
    )
    return "sonoff2hue#" + (host or "host")[:19]


async def pair(
    info: HueBridgeInfo,
    device_type: str,
    *,
    on_attempt: Callable[[PairingResult], None] | None = None,
    attempt: Callable[[str, str, str], Awaitable[PairingResult]] = try_create_app_key,
    window: float = PAIRING_WINDOW,
    interval: float = PAIRING_INTERVAL,
) -> str:
    """Asks the bridge for an application key until its link button is pressed, as ``HuePairing`` does; returns the
    key. ``on_attempt`` hears of each attempt that didn't pair. Raises :class:`PairingTimeoutError` once ``window``
    seconds have passed, and :class:`HueApiError` when the bridge refuses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    last_error: str | None = None
    while loop.time() < deadline:
        result = await attempt(info.ip, info.certificate_sha256, device_type)
        if result.status == PairingStatus.PAIRED and result.app_key is not None:
            return result.app_key
        last_error = result.error if result.status == PairingStatus.REQUEST_FAILED else None
        if on_attempt is not None:
            on_attempt(result)
        await asyncio.sleep(interval)
    raise PairingTimeoutError(window, last_error)
