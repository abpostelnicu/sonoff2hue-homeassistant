"""Finding Hue bridges and pairing with one.

Discovery runs for 5 seconds: it asks for ``_hue._tcp.local`` with an mDNS listener of its own, from the integration's
own mDNS layer, and asks Signify's discovery endpoint, which lists the bridges that reach the internet from this
network, or only one of the two. It lists the bridges by address, each as the first answer for that address gave it.

Pairing reads the bridge's public config and pins the certificate it presents (trust on first use), then asks the bridge
for an application key every 2 seconds for 60 seconds, while the user presses its link button.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import logging
import socket
from typing import Any

import aiohttp

from .dns import DnsRecordType, ends_with_ignoring_case, equals_ignoring_case
from .hue_address import create_base_url
from .hue_client import (
    HttpAnswer,
    HueApiError,
    HueBridgeInfo,
    TransportError,
    TransportFactory,
    aiohttp_transport,
    load_json,
)
from .mdns import MdnsListener, MdnsPacket

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE = "_hue._tcp.local"
CLOUD_DISCOVERY_URL = "https://discovery.meethue.com/"
DISCOVERY_TIMEOUT = 5.0
PAIRING_WINDOW = 60.0
PAIRING_INTERVAL = 2.0
LINK_BUTTON_NOT_PRESSED = 101
"""The error type of the bridge's answer to a pairing request while its link button isn't pressed."""


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
    *,
    mdns: bool = True,
    cloud: bool = True,
) -> list[DiscoveredBridge]:
    """Bridges found with mDNS and with Signify's discovery endpoint within ``duration`` seconds: the first answer for
    each address, sorted by address. ``mdns=False`` leaves mDNS out, and ``cloud=False`` Signify's endpoint."""
    found: dict[str, DiscoveredBridge] = {}
    searches: list[Awaitable[None]] = []
    if mdns:
        searches.append(_discover_mdns(found, duration, listener_factory))
    if cloud:
        searches.append(_discover_cloud(found, duration, session))
    # They run to the end; then the first failure, if any, is raised.
    outcomes = await asyncio.gather(*searches, return_exceptions=True)
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
    """The bridge's public config and the SHA-256 of the certificate it presents, read over the same connection. Raises
    :class:`HueApiError` when no Hue bridge answers, and ``ConfigError`` for a malformed address, before any request."""
    url = create_base_url(ip)
    fingerprints: list[str] = []
    transport = transport_factory(url, None, fingerprints.append)
    try:
        answer = await transport.send("GET", "api/0/config", None)
    except TransportError as err:
        raise HueApiError(f"No Hue bridge answered at {ip}: {err.detail}") from err
    finally:
        await transport.close()

    if not answer.is_success:
        raise HueApiError(f"No Hue bridge answered at {ip}: {not_success_message(answer)}")
    try:
        config = answer.json()
    except ValueError as err:
        raise HueApiError(f"No Hue bridge answered at {ip}: {err}") from err
    if not isinstance(config, dict) or not isinstance(bridge_id := config.get("bridgeid"), str) or not fingerprints:
        raise HueApiError(f"The device at {ip} does not look like a Hue bridge.")
    return HueBridgeInfo(
        ip,
        bridge_id,
        _optional_text(config, "name"),
        _optional_text(config, "modelid"),
        _optional_text(config, "swversion"),
        fingerprints[-1],
    )


def not_success_message(answer: HttpAnswer) -> str:
    """What an answer with a failure status says, such as "HTTP 404 Not Found"."""
    phrase = answer.reason_phrase
    return f"HTTP {answer.status} {phrase}" if phrase else f"HTTP {answer.status}"


def _optional_text(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
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
    return parse_pairing_response(answer.status, answer.text())


def parse_pairing_response(status: int, body: str) -> PairingResult:
    """Reads the answer to ``POST /api``, such as ``[{"success":{"username":"…","clientkey":"…"}}]``, whose username is
    the application key; the client key isn't used. An error of type 101 means that the link button wasn't pressed, and
    any other error is a refusal, raised as :class:`HueApiError`. Anything else is worth another attempt."""
    try:
        answer = load_json(body)
    except ValueError:
        answer = None
    first = answer[0] if isinstance(answer, list) and answer else None

    if isinstance(first, dict):
        success = first.get("success")
        if isinstance(success, dict) and isinstance(app_key := success.get("username"), str) and app_key:
            return PairingResult(PairingStatus.PAIRED, app_key)

        error = first.get("error")
        if isinstance(error, dict):
            error_type = error.get("type")
            if not isinstance(error_type, int) or isinstance(error_type, bool):
                error_type = None
            if error_type == LINK_BUTTON_NOT_PRESSED:
                return PairingResult(PairingStatus.LINK_BUTTON_NOT_PRESSED, None)
            description = error.get("description")
            if not isinstance(description, str) or not description or description.isspace():
                description = "no description"
            number = "unknown" if error_type is None else error_type
            raise HueApiError(f"The Hue bridge refused pairing: {description} (error {number}).", status)

    # Not the documented shape, e.g. an HTML error page from a busy bridge: worth another attempt.
    return PairingResult(PairingStatus.REQUEST_FAILED, None, f"unexpected response (HTTP {status}): {_shorten(body)}")


def _shorten(body: str, limit: int = 120) -> str:
    """The body on one line, cut after ``limit`` characters."""
    line = " ".join(body.split())
    if not line:
        return "(empty body)"
    return line if len(line) <= limit else f"{line[:limit]}..."


def build_device_type(host_name: str | None = None) -> str:
    """The ``devicetype`` to pair with: ``sonoff2hue#`` and the letters and digits of the host name up to its first dot,
    at most 19 of them, as Hue limits it to ``<app, at most 20>#<device, at most 19>``."""
    name = (socket.gethostname() if host_name is None else host_name).split(".", 1)[0]
    letters_and_digits = "".join(char for char in name if char.isalnum())
    return f"sonoff2hue#{(letters_and_digits or 'host')[:19]}"


async def pair(
    info: HueBridgeInfo,
    device_type: str,
    *,
    on_attempt: Callable[[PairingResult], None] | None = None,
    attempt: Callable[[str, str, str], Awaitable[PairingResult]] = try_create_app_key,
    window: float = PAIRING_WINDOW,
    interval: float = PAIRING_INTERVAL,
) -> str:
    """Asks the bridge for an application key every ``interval`` seconds until its link button is pressed; returns the
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
