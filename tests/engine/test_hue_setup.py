"""Finding bridges and pairing: the bridge's identity and certificate, one pairing attempt, the pairing loop and
discovery, over real sockets on 127.0.0.1 where they matter."""

import asyncio
from collections.abc import AsyncIterator, Callable
import json
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from custom_components.sonoff2hue.engine import hue_client, hue_setup
from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.dns import DnsMessage, DnsRecordType, build_query
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.hue_address import BridgeUrl
from custom_components.sonoff2hue.engine.hue_client import HttpAnswer, HueApiError, HueBridgeInfo, request_url
from custom_components.sonoff2hue.engine.hue_setup import (
    DiscoveredBridge,
    PairingResult,
    PairingStatus,
    PairingTimeoutError,
)
from custom_components.sonoff2hue.engine.mdns import MdnsListener
from tools.fake_hue.bridge import BRIDGE_ID, NAME, Fault

from ..hue import FakeHueHost
from .fakes import FakeNetwork
from .packets import DnsPacketBuilder, a, srv, txt
from .test_hue_client import closed_port

PAIRED = PairingResult(PairingStatus.PAIRED, "new-app-key")
NOT_PRESSED = PairingResult(PairingStatus.LINK_BUTTON_NOT_PRESSED, None)


# The bridge's identity and certificate.


async def test_bridge_info_names_the_bridge_and_its_certificate(fake_hue: FakeHueHost) -> None:
    info = await hue_setup.get_bridge_info(fake_hue.address)

    assert info == HueBridgeInfo(fake_hue.address, BRIDGE_ID, NAME, "BSB002", "1972004020", fake_hue.fingerprint)


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (404, "", "No Hue bridge answered at {}: Response status code does not indicate success: 404 (Not Found)."),
        (200, '{"name":"Not a bridge"}', "The device at {} does not look like a Hue bridge."),
        (200, '{"bridgeid":42}', "The device at {} does not look like a Hue bridge."),
        (200, "null", "The device at {} does not look like a Hue bridge."),
        (
            200,
            "[]",
            "No Hue bridge answered at {}: The JSON value could not be converted to System.Text.Json.Nodes.JsonObject.",
        ),
    ],
)
async def test_bridge_info_fails_for_a_device_that_is_not_a_bridge(
    fake_hue: FakeHueHost, status: int, body: str, message: str
) -> None:
    fake_hue.fail(Fault("/api/0/config", status=status, body=body))

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(fake_hue.address)

    assert str(failure.value) == message.format(fake_hue.address)


async def test_bridge_info_fails_for_an_answer_that_is_not_json(fake_hue: FakeHueHost) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body="<html>busy</html>", content_type="text/html"))

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(fake_hue.address)

    # .NET's JsonException names the position differently; the start is the app's.
    assert str(failure.value).startswith(f"No Hue bridge answered at {fake_hue.address}: ")


async def test_bridge_info_reads_the_config_without_regard_to_case(fake_hue: FakeHueHost) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body='{"BridgeID":"001788FFFE000001","NAME":"Hue"}'))
    fake_hue.fail(Fault("/api/0/config", status=200, body='{"bridgeid":"a","BRIDGEID":"b"}'))

    info = await hue_setup.get_bridge_info(fake_hue.address)
    with pytest.raises(UnexpectedError) as duplicate:
        await hue_setup.get_bridge_info(fake_hue.address)

    assert (info.bridge_id, info.name) == ("001788FFFE000001", "Hue")
    assert duplicate.value.dotnet_type == "ArgumentException"


@pytest.mark.usefixtures("socket_enabled")
async def test_bridge_info_fails_when_nothing_answers() -> None:
    port = closed_port()

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(f"127.0.0.1:{port}")

    assert str(failure.value) == f"No Hue bridge answered at 127.0.0.1:{port}: Connection refused (127.0.0.1:{port})"


async def test_bridge_info_times_out_as_dotnet_does(fake_hue: FakeHueHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hue_client, "REQUEST_TIMEOUT", 0.1)
    fake_hue.fail(Fault("/api/0/config", status=200, body="{}", delay=0.5))

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(fake_hue.address)

    assert str(failure.value) == (
        f"No Hue bridge answered at {fake_hue.address}: The request was canceled due to the configured "
        "HttpClient.Timeout of 5 seconds elapsing."
    )


async def test_a_malformed_address_fails_before_any_request() -> None:
    def no_transport(*_: Any) -> Any:
        raise AssertionError("no request expected")

    with pytest.raises(ConfigError):
        await hue_setup.get_bridge_info("not a host", no_transport)
    with pytest.raises(ConfigError):
        await hue_setup.try_create_app_key("not a host", "00" * 32, "sonoff2hue#test", no_transport)


# One pairing attempt.


async def test_pairing_waits_for_the_link_button(fake_hue: FakeHueHost) -> None:
    refused = await hue_setup.try_create_app_key(fake_hue.address, fake_hue.fingerprint, "sonoff2hue#test")
    await fake_hue.request("POST", "_fake/link-button")
    paired = await hue_setup.try_create_app_key(fake_hue.address, fake_hue.fingerprint, "sonoff2hue#test")

    assert refused == NOT_PRESSED
    assert paired.status == PairingStatus.PAIRED
    assert paired.app_key is not None
    assert fake_hue.bridge.is_authorized(paired.app_key)


async def test_pairing_with_a_changed_certificate_fails(fake_hue_pressed: FakeHueHost) -> None:
    with pytest.raises(HueApiError) as failure:
        await hue_setup.try_create_app_key(fake_hue_pressed.address, "00" * 32, "sonoff2hue#test")

    assert str(failure.value) == (
        f"The TLS certificate of {fake_hue_pressed.address} changed during pairing. Make sure this address belongs to "
        "your Hue bridge and pair it again."
    )


@pytest.mark.usefixtures("socket_enabled")
async def test_pairing_reports_a_refused_connection_as_worth_another_attempt() -> None:
    port = closed_port()

    result = await hue_setup.try_create_app_key(f"127.0.0.1:{port}", "00" * 32, "sonoff2hue#test")

    assert result == PairingResult(
        PairingStatus.REQUEST_FAILED, None, f"bridge not reachable: Connection refused (127.0.0.1:{port})"
    )


async def test_pairing_reports_a_timeout_as_worth_another_attempt(
    fake_hue_pressed: FakeHueHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hue_client, "REQUEST_TIMEOUT", 0.1)
    fake_hue_pressed.fail(Fault("/api", method="POST", status=200, body="[]", delay=0.5))

    result = await hue_setup.try_create_app_key(
        fake_hue_pressed.address, fake_hue_pressed.fingerprint, "sonoff2hue#test"
    )

    assert result == PairingResult(PairingStatus.REQUEST_FAILED, None, "the request timed out")


async def test_pairing_posts_the_device_type_to_the_normalized_address() -> None:
    sent: list[tuple[str, str, bytes | None]] = []

    class Recorder:
        def __init__(self, url: BridgeUrl) -> None:
            self.url = url

        async def send(
            self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
        ) -> HttpAnswer:
            sent.append((method, request_url(self.url, path), body))
            return HttpAnswer(200, None, b'[{"success":{"username":"new-app-key"}}]')

        async def close(self) -> None:
            pass

    result = await hue_setup.try_create_app_key(
        "[FE80::1]:8443", "00" * 32, "sonoff2hue#test", lambda url, pin, report: Recorder(url)
    )

    assert result == PAIRED
    assert [(method, url) for method, url, _ in sent] == [("POST", "https://[fe80::1]:8443/api")]
    assert json.loads(sent[0][2] or b"") == {"devicetype": "sonoff2hue#test", "generateclientkey": True}


# The pairing loop.

INFO = HueBridgeInfo("192.0.2.10", BRIDGE_ID, NAME, "BSB002", "1972004020", "00" * 32)


def attempts(*results: PairingResult | Exception) -> Callable[[str, str, str], Any]:
    """A pairing attempt that gives these results in turn, the last one again and again."""
    remaining = list(results)

    async def attempt(ip: str, certificate: str, device_type: str) -> PairingResult:
        assert (ip, certificate, device_type) == (INFO.ip, INFO.certificate_sha256, "sonoff2hue#test")
        result = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(result, Exception):
            raise result
        return result

    return attempt


async def test_pairing_asks_until_the_link_button_is_pressed() -> None:
    failed = PairingResult(PairingStatus.REQUEST_FAILED, None, "the request timed out")
    seen: list[PairingResult] = []

    app_key = await hue_setup.pair(
        INFO, "sonoff2hue#test", on_attempt=seen.append, attempt=attempts(NOT_PRESSED, failed, PAIRED), interval=0
    )

    assert app_key == "new-app-key"
    assert seen == [NOT_PRESSED, failed]


@pytest.mark.parametrize(
    ("last", "last_error"),
    [(NOT_PRESSED, None), (PairingResult(PairingStatus.REQUEST_FAILED, None, "busy"), "busy")],
)
async def test_pairing_gives_up_after_its_window(last: PairingResult, last_error: str | None) -> None:
    with pytest.raises(PairingTimeoutError) as failure:
        await hue_setup.pair(INFO, "sonoff2hue#test", attempt=attempts(last), window=0.05, interval=0.01)

    assert failure.value.last_error == last_error
    assert str(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None)) == (
        "The link button was not pressed within 60 seconds. Try again."
    )


async def test_pairing_stops_when_the_bridge_refuses() -> None:
    refusal = HueApiError("The Hue bridge refused pairing: invalid value for parameter, devicetype (error 7).", 200)

    with pytest.raises(HueApiError) as failure:
        await hue_setup.pair(INFO, "sonoff2hue#test", attempt=attempts(NOT_PRESSED, refusal), interval=0)

    assert failure.value is refusal


@pytest.mark.parametrize(
    ("host_name", "device_type"),
    [
        ("dev-vm.example.com", "sonoff2hue#devvm"),
        ("homeassistant", "sonoff2hue#homeassistant"),
        ("", "sonoff2hue#host"),
        ("---", "sonoff2hue#host"),
        ("a" * 30, "sonoff2hue#" + "a" * 19),
        ("K\u00fcche-Pi", "sonoff2hue#K\u00fcchePi"),
        ("host\U00010400x", "sonoff2hue#hostx"),
        ("\u0661\u0662", "sonoff2hue#\u0661\u0662"),
    ],
)
def test_device_type_takes_the_letters_and_digits_of_the_host_name(host_name: str, device_type: str) -> None:
    assert hue_setup.build_device_type(host_name) == device_type


def test_device_type_uses_this_host_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hue_setup.socket, "gethostname", lambda: "hass.local")

    assert hue_setup.build_device_type() == "sonoff2hue#hass"


# Discovery.


@pytest.fixture
async def cloud(socket_enabled: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[web.Response]]:
    """Signify's discovery endpoint on 127.0.0.1; a test puts the answer it gives into the list."""
    answers: list[web.Response] = []

    async def handler(_: web.Request) -> web.Response:
        return answers[0]

    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    monkeypatch.setattr(hue_setup, "CLOUD_DISCOVERY_URL", f"http://{host}:{port}/")
    yield answers
    await runner.cleanup()


async def discover(network: FakeNetwork, packets: list[tuple[bytes, str]], duration: float = 0.2) -> list[Any]:
    """Runs discovery for ``duration`` seconds while the fake network delivers ``packets``."""
    async with aiohttp.ClientSession() as session:
        task = asyncio.create_task(
            hue_setup.discover(session, duration, lambda: MdnsListener(network.open, network.watch))
        )
        await network.next_open()
        for data, sender in packets:
            network.deliver(data, (sender, 5353))
        return await task


def hue_announcement(instance: str, bridge_id: str, *, ttl: int = 4500, host: str | None = None) -> bytes:
    name = f"{instance}._hue._tcp.local"
    builder = DnsPacketBuilder().answer(name, DnsRecordType.TXT, txt(f"bridgeid={bridge_id}", "modelid=BSB002"), ttl)
    if host is not None:
        builder.additional(name.upper(), DnsRecordType.SRV, srv(443, f"{instance}.local"))
        builder.additional(f"{instance}.LOCAL", DnsRecordType.A, a(host))
    return builder.build()


async def test_discovery_finds_bridges_by_mdns_and_in_the_cloud(cloud: list[web.Response]) -> None:
    cloud.append(
        web.json_response(
            [
                {"id": "001788fffe000003", "internalipaddress": "192.0.2.30", "port": 443},
                {"ID": "001788fffe000004", "InternalIpAddress": "192.0.2.4"},
                {"id": "001788fffe000005", "internalipaddress": "bridge.local"},
                None,
            ]
        )
    )
    network = FakeNetwork()
    packets = [
        (hue_announcement("Hue Bridge - 1", "001788fffe000001", host="192.0.2.10"), "192.0.2.99"),
        (hue_announcement("Hue Bridge - 2", "001788fffe000002"), "192.0.2.20"),
        (hue_announcement("Hue Bridge - 6", "001788fffe000006", ttl=0), "192.0.2.60"),
    ]

    bridges = await discover(network, packets)

    assert bridges == [
        DiscoveredBridge("192.0.2.10", "001788fffe000001", "mDNS"),
        DiscoveredBridge("192.0.2.20", "001788fffe000002", "mDNS"),
        DiscoveredBridge("192.0.2.30", "001788fffe000003", "discovery.meethue.com"),
        DiscoveredBridge("192.0.2.4", "001788fffe000004", "discovery.meethue.com"),
    ]
    sent = [DnsMessage.parse(data) for data, _ in network.transports[0].sent]
    assert [(q.name, q.type) for q in sent[0].questions] == [("_hue._tcp.local", DnsRecordType.PTR)]


async def test_discovery_takes_no_address_from_a_copy_that_this_host_relays(cloud: list[web.Response]) -> None:
    # Home Assistant OS's multicast plugin repeats each answer from its `hassio` bridge, one of this host's own
    # addresses, as the fake network's 192.0.2.1 and 198.51.100.1 are (decision 19). Without an A record, such a copy
    # doesn't say where the bridge is.
    cloud.append(web.json_response([]))
    packets = [
        (hue_announcement("Hue Bridge - 2", "001788fffe000002"), "192.0.2.20"),
        (hue_announcement("Hue Bridge - 2", "001788fffe000002"), "192.0.2.1"),
        (hue_announcement("Hue Bridge - 7", "001788fffe000007", host="192.0.2.70"), "198.51.100.1"),
    ]

    assert await discover(FakeNetwork(), packets) == [
        DiscoveredBridge("192.0.2.20", "001788fffe000002", "mDNS"),
        DiscoveredBridge("192.0.2.70", "001788fffe000007", "mDNS"),
    ]


async def test_discovery_asks_for_bridges_as_the_socket_opens(cloud: list[web.Response]) -> None:
    cloud.append(web.json_response([]))
    network = FakeNetwork()
    async with aiohttp.ClientSession() as session:
        task = asyncio.create_task(hue_setup.discover(session, 0.2, lambda: MdnsListener(network.open, network.watch)))
        transport = await network.next_open()

        # As .NET's Task.Delay(0) doesn't wait, the first query went out before the started callback first waited.
        assert transport.sent == [(build_query("_hue._tcp.local", DnsRecordType.PTR), "192.0.2.1")]
        assert await task == []


@pytest.mark.parametrize(
    "answer",
    [
        web.Response(status=429, text="Too many requests"),
        web.Response(text="<html>"),
        web.json_response({"error": "not a list"}),
        web.json_response([1, {"internalipaddress": "192.0.2.30"}]),
        web.json_response([{"internalipaddress": 42}]),
        web.Response(text="[]", content_type="application/json", charset="windows-1252"),
    ],
)
async def test_discovery_ignores_what_dotnet_ignores_from_the_cloud(
    cloud: list[web.Response], answer: web.Response
) -> None:
    cloud.append(answer)

    assert await discover(FakeNetwork(), []) == []


async def test_discovery_keeps_the_bridges_before_an_unreadable_one(cloud: list[web.Response]) -> None:
    cloud.append(web.json_response([{"internalipaddress": "192.0.2.30"}, {"internalipaddress": {"a": 1}}]))

    assert await discover(FakeNetwork(), []) == [DiscoveredBridge("192.0.2.30", None, "discovery.meethue.com")]


async def test_discovery_fails_where_dotnet_fails(cloud: list[web.Response]) -> None:
    cloud.append(web.Response(text='[{"internalipaddress":"192.0.2.30","INTERNALIPADDRESS":"192.0.2.31"}]'))

    with pytest.raises(UnexpectedError) as failure:
        await discover(FakeNetwork(), [])

    assert failure.value.dotnet_type == "ArgumentException"
