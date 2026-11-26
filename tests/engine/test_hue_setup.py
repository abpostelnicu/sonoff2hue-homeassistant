"""Finding bridges and pairing: the bridge's identity and certificate, one pairing attempt, the pairing loop and
discovery, over real sockets on 127.0.0.1 where they matter."""

import asyncio
from collections.abc import AsyncIterator, Callable
import json
import logging
import socket
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from custom_components.sonoff2hue.engine import hue_client, hue_setup
from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.dns import DnsMessage, DnsRecordType, build_query
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
        (404, "", "No Hue bridge answered at {}: HTTP 404 Not Found"),
        (503, "busy", "No Hue bridge answered at {}: HTTP 503 Service Unavailable"),
        (599, "", "No Hue bridge answered at {}: HTTP 599"),
        (200, "<html>busy</html>", "No Hue bridge answered at {}: Expecting value: line 1 column 1 (char 0)"),
        (200, '{"name":"Not a bridge"}', "The device at {} does not look like a Hue bridge."),
        (200, '{"bridgeid":42}', "The device at {} does not look like a Hue bridge."),
        (200, '{"BridgeID":"001788FFFE000001"}', "The device at {} does not look like a Hue bridge."),
        (200, "null", "The device at {} does not look like a Hue bridge."),
        (200, "[]", "The device at {} does not look like a Hue bridge."),
    ],
)
async def test_bridge_info_fails_for_a_device_that_is_not_a_bridge(
    fake_hue: FakeHueHost, status: int, body: str, message: str
) -> None:
    fake_hue.fail(Fault("/api/0/config", status=status, body=body))

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(fake_hue.address)

    assert str(failure.value) == message.format(fake_hue.address)


async def test_bridge_info_takes_only_texts_for_the_bridges_name_model_and_firmware(fake_hue: FakeHueHost) -> None:
    fake_hue.fail(
        Fault(
            "/api/0/config",
            status=200,
            body='{"bridgeid":"001788FFFE000001","name":"Hue","modelid":7,"swversion":null}',
        )
    )

    info = await hue_setup.get_bridge_info(fake_hue.address)

    assert info == HueBridgeInfo(fake_hue.address, "001788FFFE000001", "Hue", None, None, fake_hue.fingerprint)


@pytest.mark.usefixtures("socket_enabled")
async def test_bridge_info_fails_when_nothing_answers() -> None:
    port = closed_port()

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(f"127.0.0.1:{port}")

    assert str(failure.value) == f"No Hue bridge answered at 127.0.0.1:{port}: Connection refused (127.0.0.1:{port})"


async def test_bridge_info_times_out(fake_hue: FakeHueHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hue_client, "REQUEST_TIMEOUT", 0.1)
    fake_hue.fail(Fault("/api/0/config", status=200, body="{}", delay=0.5))

    with pytest.raises(HueApiError) as failure:
        await hue_setup.get_bridge_info(fake_hue.address)

    assert str(failure.value) == f"No Hue bridge answered at {fake_hue.address}: The request timed out."


@pytest.mark.usefixtures("socket_enabled")
async def test_bridge_info_fails_for_a_device_without_tls() -> None:
    async def answer_in_plain_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(1)
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer_in_plain_http, "127.0.0.1", 0)
    address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        with pytest.raises(HueApiError) as failure:
            await hue_setup.get_bridge_info(address)
    finally:
        server.close()
        await server.wait_closed()

    assert str(failure.value) == f"No Hue bridge answered at {address}: The TLS handshake failed ({address})"


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
            sent.append((method, str(request_url(self.url, path)), body))
            return HttpAnswer(200, None, b'[{"success":{"username":"new-app-key"}}]')

        async def close(self) -> None:
            pass

    result = await hue_setup.try_create_app_key(
        "[FE80::1]:8443", "00" * 32, "sonoff2hue#test", lambda url, pin, report: Recorder(url)
    )

    assert result == PAIRED
    assert [(method, url) for method, url, _ in sent] == [("POST", "https://[fe80::1]:8443/api")]
    assert json.loads(sent[0][2] or b"") == {"devicetype": "sonoff2hue#test", "generateclientkey": True}


@pytest.mark.parametrize(
    ("status", "body", "result"),
    [
        (200, '[{"success":{"username":"new-app-key","clientkey":"0123ABCD"}}]', PAIRED),
        (200, '[{"success":{"username":"new-app-key"}},{"error":{"type":101}}]', PAIRED),
        (200, '[{"error":{"type":101,"address":"","description":"link button not pressed"}}]', NOT_PRESSED),
        (200, '[{"success":{"username":""}}]', 'unexpected response (HTTP 200): [{"success":{"username":""}}]'),
        (200, "[]", "unexpected response (HTTP 200): []"),
        (
            200,
            '{"success":{"username":"new-app-key"}}',
            'unexpected response (HTTP 200): {"success":{"username":"new-app-key"}}',
        ),
        (
            503,
            "<html>\n  <body>busy</body>\n</html>\n",
            "unexpected response (HTTP 503): <html> <body>busy</body> </html>",
        ),
        (502, "", "unexpected response (HTTP 502): (empty body)"),
        (200, "x" * 121, f"unexpected response (HTTP 200): {'x' * 120}..."),
    ],
)
def test_a_pairing_answer_pairs_waits_or_is_worth_another_attempt(
    status: int, body: str, result: PairingResult | str
) -> None:
    expected = (
        result if isinstance(result, PairingResult) else PairingResult(PairingStatus.REQUEST_FAILED, None, result)
    )

    assert hue_setup.parse_pairing_response(status, body) == expected


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            '[{"error":{"type":7,"address":"/devicetype","description":"invalid value, x, for parameter devicetype"}}]',
            "invalid value, x, for parameter devicetype (error 7).",
        ),
        ('[{"error":{"type":1}}]', "no description (error 1)."),
        ('[{"error":{"description":" "}}]', "no description (error unknown)."),
        ('[{"error":{"type":"101","description":"busy"}}]', "busy (error unknown)."),
        ('[{"error":{"type":true}}]', "no description (error unknown)."),
    ],
)
def test_any_other_pairing_error_is_a_refusal(body: str, message: str) -> None:
    with pytest.raises(HueApiError) as failure:
        hue_setup.parse_pairing_response(200, body)

    assert str(failure.value) == f"The Hue bridge refused pairing: {message}"
    assert failure.value.status == 200


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
        ("host\U00010400x", "sonoff2hue#host\U00010400x"),
        ("\u0661\u0662", "sonoff2hue#\u0661\u0662"),
        ("my_host 1", "sonoff2hue#myhost1"),
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
                {"id": "001788fffe000004", "internalipaddress": "192.0.2.4"},
                {"id": "001788fffe000005", "internalipaddress": "bridge.local"},
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

        # A delay of 0 isn't awaited, so the first query went out before the started callback first waited.
        assert transport.sent == [(build_query("_hue._tcp.local", DnsRecordType.PTR), "192.0.2.1")]
        assert await task == []


@pytest.mark.parametrize(
    "answer",
    [
        web.Response(status=429, text="Too many requests"),
        web.Response(text="<html>"),
        web.Response(text=""),
        web.Response(body=b'[{"internalipaddress":"192.0.2.30","id":"\xff"}]', content_type="application/json"),
        web.Response(text="[" * 100_000, content_type="application/json"),
        web.json_response({"error": "not a list"}),
        web.json_response([1, {"internalipaddress": "192.0.2.30"}]),
        web.json_response([None, {"internalipaddress": "192.0.2.30"}]),
    ],
    ids=["status", "html", "empty", "utf8", "nested", "object", "number", "null"],
)
async def test_discovery_ignores_cloud_answers_it_cant_use(
    cloud: list[web.Response], answer: web.Response, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=hue_setup.__name__)
    cloud.append(answer)

    assert await discover(FakeNetwork(), []) == []
    assert "Cloud bridge discovery failed: " in caplog.text


@pytest.mark.usefixtures("socket_enabled")
async def test_discovery_gives_up_on_a_cloud_that_doesnt_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=hue_setup.__name__)
    with socket.socket() as silent:  # takes the connection, and never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        host, port = silent.getsockname()
        monkeypatch.setattr(hue_setup, "CLOUD_DISCOVERY_URL", f"http://{host}:{port}/")

        assert await discover(FakeNetwork(), []) == []

    assert "Cloud bridge discovery failed: " in caplog.text


async def test_discovery_skips_cloud_entries_without_an_ip_address(cloud: list[web.Response]) -> None:
    cloud.append(
        web.json_response(
            [
                {"internalipaddress": "192.0.2.30"},
                {"internalipaddress": {"a": 1}},
                {"internalipaddress": 42},
                {"internalipaddress": "192.168.1"},
                {"internalipaddress": " 192.0.2.31"},
                {"id": "001788fffe000004"},
            ]
        )
    )

    assert await discover(FakeNetwork(), []) == [DiscoveredBridge("192.0.2.30", None, "discovery.meethue.com")]


async def test_discovery_reads_the_clouds_answer_as_json(cloud: list[web.Response]) -> None:
    # Keys are as written, the last of duplicate keys counts, and an id that isn't a text is left out. Addresses are
    # written as ipaddress writes them, and the first entry for an address is kept.
    cloud.append(
        web.Response(
            text='[{"InternalIpAddress":"192.0.2.4"},'
            '{"internalipaddress":"192.0.2.30","internalipaddress":"192.0.2.31","id":42},'
            '{"internalipaddress":"2001:DB8::1","id":"001788fffe000001"},'
            '{"internalipaddress":"2001:db8::1","id":"001788fffe000002"}]'
        )
    )

    assert await discover(FakeNetwork(), []) == [
        DiscoveredBridge("192.0.2.31", None, "discovery.meethue.com"),
        DiscoveredBridge("2001:db8::1", "001788fffe000001", "discovery.meethue.com"),
    ]


class NoCloud:
    """A session for discovery that fails the test when Signify's endpoint is asked."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Signify's discovery endpoint was asked.")


async def test_discovery_can_leave_the_cloud_out() -> None:
    """As the bridge supervisor asks between two requests to Signify's endpoint (decision 33)."""
    network = FakeNetwork()
    task = asyncio.create_task(
        hue_setup.discover(NoCloud(), 0.2, lambda: MdnsListener(network.open, network.watch), cloud=False)  # type: ignore[arg-type]
    )
    await network.next_open()
    network.deliver(hue_announcement("Hue Bridge - 1", "001788fffe000001", host="192.0.2.10"), ("192.0.2.99", 5353))

    assert await task == [DiscoveredBridge("192.0.2.10", "001788fffe000001", "mDNS")]


async def test_discovery_can_leave_mdns_out(cloud: list[web.Response]) -> None:
    cloud.append(web.json_response([{"id": "001788fffe000003", "internalipaddress": "192.0.2.30"}]))

    def no_listener() -> MdnsListener:
        raise AssertionError("mDNS was asked.")

    async with aiohttp.ClientSession() as session:
        bridges = await hue_setup.discover(session, 0.2, no_listener, mdns=False)

    assert bridges == [DiscoveredBridge("192.0.2.30", "001788fffe000003", "discovery.meethue.com")]
