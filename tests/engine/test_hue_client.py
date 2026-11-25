"""The Hue client: the pinned certificate, connections that fail, the requests it sends and how it reads the bridge's
answers, over real HTTPS against the fake bridge where the connection matters, and in memory otherwise."""

import asyncio
from collections.abc import Callable
import errno
import hashlib
import socket
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import hue_client
from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.hue_address import BridgeUrl, create_base_url
from custom_components.sonoff2hue.engine.hue_client import (
    AiohttpTransport,
    HttpAnswer,
    HueApiError,
    HueClient,
    HueGroup,
    HueLight,
    HueTarget,
    HueTargetKind,
    request_url,
)
from tools.fake_hue.bridge import FakeIds, Fault
from tools.fake_hue.server import create_certificate, ssl_context

from ..hue import FakeHueHost, InMemoryBridge, RecordedRequest

PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"
UNEXPECTED = "Unexpected response from the Hue bridge for 'light'."
LAMP = HueTarget(HueTargetKind.LIGHT, "l1", "Lamp")


def closed_port() -> int:
    """A local port that nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def ping(client: HueClient) -> None:
    try:
        await client.ping()
    finally:
        await client.close()


def answering(status: int, body: str) -> InMemoryBridge:
    """A bridge in memory that gives every request this answer."""
    return InMemoryBridge(lambda request: (status, body))


class Answering:
    """A transport that gives every request this answer, with a reason phrase and a charset."""

    def __init__(self, answer: HttpAnswer) -> None:
        self.answer = answer

    def __call__(self, url: BridgeUrl, pin: str | None, report: Any) -> Answering:
        return self

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> HttpAnswer:
        return self.answer

    async def close(self) -> None:
        pass


# The pinned certificate.


@pytest.mark.parametrize(
    "notation", [str.upper, str.lower, lambda pin: ":".join(pin[i : i + 2] for i in range(0, 64, 2))]
)
async def test_the_pinned_certificate_is_accepted_in_any_notation(
    fake_hue: FakeHueHost, notation: Callable[[str], str]
) -> None:
    app_key = await fake_hue.pair()

    await ping(HueClient(fake_hue.address, app_key, f" {notation(fake_hue.fingerprint)} "))


@pytest.mark.parametrize("pin", [hashlib.sha256(b"another bridge").hexdigest(), "not a fingerprint"])
async def test_another_or_a_malformed_pin_refuses_the_bridge(fake_hue: FakeHueHost, pin: str) -> None:
    app_key = await fake_hue.pair()

    with pytest.raises(HueApiError) as failure:
        await ping(HueClient(fake_hue.address, app_key, pin))

    assert str(failure.value) == (
        f"The Hue bridge TLS certificate does not match the pinned certificateSha256. If the bridge was replaced or "
        f"reset, {PAIR_AGAIN}."
    )
    assert failure.value.status is None
    assert failure.value.is_transient


@pytest.mark.parametrize("pin", [None, "", " "])
async def test_without_a_pin_any_certificate_is_accepted(fake_hue: FakeHueHost, pin: str | None) -> None:
    await ping(HueClient(fake_hue.address, await fake_hue.pair(), pin))


# Connections.


@pytest.mark.usefixtures("socket_enabled")
async def test_a_refused_connection_names_the_address() -> None:
    port = closed_port()

    with pytest.raises(HueApiError) as failure:
        await ping(HueClient(f"127.0.0.1:{port}", "key", None))

    assert str(failure.value) == f"Hue bridge not reachable: Connection refused (127.0.0.1:{port})"
    assert failure.value.is_transient


async def test_a_request_that_takes_too_long_times_out(fake_hue: FakeHueHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hue_client, "REQUEST_TIMEOUT", 0.1)
    app_key = await fake_hue.pair()
    fake_hue.fail(Fault("/clip/v2/resource/bridge", status=200, body='{"data":[]}', delay=0.5))

    with pytest.raises(HueApiError) as failure:
        await ping(HueClient(fake_hue.address, app_key, fake_hue.fingerprint))

    assert str(failure.value) == "Hue bridge request timed out."
    assert failure.value.is_transient


@pytest.mark.usefixtures("socket_enabled")
async def test_a_connection_closed_during_the_handshake_is_reported_with_the_systems_reason() -> None:
    async def close_at_once(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(close_at_once, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(HueApiError) as failure:
            await ping(HueClient(f"127.0.0.1:{port}", "key", None))
    finally:
        server.close()
        await server.wait_closed()

    assert str(failure.value) == f"Hue bridge not reachable: Connection reset by peer (127.0.0.1:{port})"
    assert failure.value.is_transient


@pytest.mark.usefixtures("socket_enabled")
async def test_a_bridge_that_hangs_up_without_answering_is_not_reachable() -> None:
    async def hang_up_after_the_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.close()

    server = await asyncio.start_server(
        hang_up_after_the_request, "127.0.0.1", 0, ssl=ssl_context(*create_certificate())
    )
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(HueApiError) as failure:
            await ping(HueClient(f"127.0.0.1:{port}", "key", None))
    finally:
        server.close()
        await server.wait_closed()

    assert str(failure.value) == f"Hue bridge not reachable: Server disconnected (127.0.0.1:{port})"
    assert failure.value.is_transient


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (socket.gaierror(socket.EAI_NONAME, "Name or service not known"), "Name or service not known"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "Connect call failed ('192.0.2.10', 443)"), "Connection refused"),
        (OSError(None, "Domain name not found"), "Domain name not found"),
        (ConnectionResetError(), "The connection was closed"),
        (OSError(), "The connection failed"),
    ],
)
def test_a_connection_that_fails_is_explained_in_the_systems_words(error: OSError, reason: str) -> None:
    assert hue_client._reason(error) == reason


async def test_the_connection_stays_open_from_one_request_to_the_next(fake_hue: FakeHueHost) -> None:
    certificates: list[str] = []
    transport = AiohttpTransport(create_base_url(fake_hue.address), None, certificates.append)

    try:
        answers = [await transport.send("GET", "api/0/config", None) for _ in range(3)]
    finally:
        await transport.close()

    assert [answer.status for answer in answers] == [200, 200, 200]
    assert certificates == [fake_hue.fingerprint]  # one TLS handshake


async def test_a_host_name_is_resolved_by_the_systems_resolver(
    fake_hue: FakeHueHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_key = await fake_hue.pair()
    port = int(fake_hue.address.rsplit(":", 1)[1])
    looked_up: list[str] = []

    async def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        looked_up.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)

    await ping(HueClient(f"hue-bridge.local:{port}", app_key, fake_hue.fingerprint))

    assert looked_up == ["hue-bridge.local"]


# Requests.


@pytest.mark.parametrize(
    ("address", "path", "url"),
    [
        ("b\u00fccher.local", "api/0/config", "https://xn--bcher-kva.local/api/0/config"),
        ("[fe80::1%eth0]:8443", "clip/v2/resource/light", "https://[fe80::1%eth0]:8443/clip/v2/resource/light"),
        ("192.0.2.10", "clip/v2/resource/light/%2E%2E", "https://192.0.2.10/clip/v2/resource/light/%2E%2E"),
    ],
)
def test_requests_go_to_the_bridges_url_with_the_path_as_escaped(address: str, path: str, url: str) -> None:
    assert str(request_url(create_base_url(address), path)) == url


@pytest.mark.parametrize(
    ("resource_id", "segment"),
    [
        ("11111111-0000-4000-8000-000000000001", "11111111-0000-4000-8000-000000000001"),
        ("..", "%2E%2E"),
        (".", "%2E"),
        ("a/../b", "a%2F%2E%2E%2Fb"),
        ("l?1#x", "l%3F1%23x"),
        ("a b", "a%20b"),
        ("b\u00fcro", "b%C3%BCro"),
    ],
)
async def test_a_resource_id_stays_one_segment_of_the_path(resource_id: str, segment: str) -> None:
    bridge = answering(200, '{"errors":[],"data":[]}')

    await bridge.client().set_on(HueTarget(HueTargetKind.LIGHT, resource_id, "Lamp"), True)

    assert [request.path for request in bridge.requests] == [f"/clip/v2/resource/light/{segment}"]


async def test_an_id_of_dots_reaches_the_bridge_as_an_id(fake_hue: FakeHueHost) -> None:
    client = fake_hue.hue_client(await fake_hue.pair())

    try:
        with pytest.raises(HueApiError) as failure:
            await client.set_on(HueTarget(HueTargetKind.LIGHT, "..", "Lamp"), True)
    finally:
        await client.close()

    assert failure.value.status == 404
    assert (
        await fake_hue.get_text("_fake/puts")
        == "rejected PUT /clip/v2/resource/light/..: 404 no light with the id '..'\n"
    )


async def test_switching_sends_the_application_key_and_the_state() -> None:
    bridge = answering(200, '{"errors":[],"data":[{"rid":"g1","rtype":"grouped_light"}]}')

    await bridge.client("the-key").set_on(HueTarget(HueTargetKind.GROUPED_LIGHT, "g1", "Hall"), False)

    [request] = bridge.requests
    assert (request.method, request.path, request.body) == (
        "PUT",
        "/clip/v2/resource/grouped_light/g1",
        '{"on":{"on":false}}',
    )
    assert request.headers == {"hue-application-key": "the-key"}


@pytest.mark.parametrize("resource_id", [None, ""])
async def test_a_target_without_an_id_is_refused_before_any_request(resource_id: str | None) -> None:
    bridge = answering(200, '{"errors":[],"data":[]}')

    with pytest.raises(ValueError, match="has no id"):
        await bridge.client().set_on(HueTarget(HueTargetKind.LIGHT, resource_id, "Lamp"), True)

    assert bridge.requests == []


def test_a_malformed_address_is_reported_before_a_malformed_app_key() -> None:
    with pytest.raises(ConfigError) as failure:
        HueClient("not a host", "key\n", None)

    assert str(failure.value).startswith("'not a host' is not a valid Hue bridge address.")


@pytest.mark.parametrize("app_key", ["app-key\r\nInjected: header", "cl\u00e9", "key\x7f"])
def test_an_app_key_that_cannot_be_sent_is_a_config_error(app_key: str) -> None:
    with pytest.raises(ConfigError) as failure:
        HueClient("192.168.1.20", app_key, None)

    assert (
        str(failure.value)
        == f"The Hue application key contains characters that can't be sent to the bridge; {PAIR_AGAIN}."
    )


# The bridge's answers.


async def test_a_write_answered_with_errors_and_207_fails(fake_hue: FakeHueHost) -> None:
    client = fake_hue.hue_client(await fake_hue.pair())
    fake_hue.fail(
        Fault(
            "/clip/v2/resource/light/",
            method="PUT",
            status=207,
            body='{"data":[{"rid":"l1","rtype":"light"}],"errors":[{"description":"device (light) has communication '
            'issues, command (.on.on) may not have effect"}]}',
        )
    )

    try:
        with pytest.raises(HueApiError) as failure:
            await client.set_on(HueTarget(HueTargetKind.LIGHT, FakeIds.HALLWAY, "Hallway"), True)
    finally:
        await client.close()

    assert str(failure.value) == (
        f"The Hue bridge reported errors for PUT /clip/v2/resource/light/{FakeIds.HALLWAY}: device (light) has "
        "communication issues, command (.on.on) may not have effect"
    )
    assert failure.value.status == 207
    assert not failure.value.is_transient


@pytest.mark.parametrize(
    ("status", "body", "errors"),
    [
        (200, '{"errors":[{"description":"a"},{"description":" "},{}]}', "a; no description; no description"),
        (207, '{"errors":[{"description":"busy"}],"data":[]}', "busy"),
    ],
)
async def test_a_success_that_reports_errors_fails(status: int, body: str, errors: str) -> None:
    with pytest.raises(HueApiError) as failure:
        await answering(status, body).client().get_lights()

    assert str(failure.value) == f"The Hue bridge reported errors for GET /clip/v2/resource/light: {errors}"
    assert (failure.value.status, failure.value.is_transient) == (status, False)


@pytest.mark.parametrize(
    ("status", "body", "returned", "transient"),
    [
        (400, '{"errors":[{"description":"invalid body"}]}', "400 Bad Request for {}: invalid body", False),
        (404, "", "404 Not Found for {}", False),
        (409, '{"errors":[null]}', "409 Conflict for {}", False),
        (599, "", "599 for {}", False),
        (429, "", "429 Too Many Requests for {}", True),
        (500, "<html>error</html>", "500 Internal Server Error for {}", True),
        (502, "", "502 Bad Gateway for {}", True),
        (503, '{"errors":[{"description":"busy"}]}', "503 Service Unavailable for {}: busy", True),
        (504, "", "504 Gateway Timeout for {}", True),
    ],
)
async def test_a_failure_names_its_status_and_the_bridges_errors(
    status: int, body: str, returned: str, transient: bool
) -> None:
    with pytest.raises(HueApiError) as failure:
        await answering(status, body).client().get_lights()

    assert str(failure.value) == f"Hue bridge returned {returned.format('GET /clip/v2/resource/light')}"
    assert (failure.value.status, failure.value.is_transient) == (status, transient)
    assert not failure.value.is_authentication_error


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, '{"errors":[{"description":"unauthorized user"}]}', f"key (unauthorized user); {PAIR_AGAIN}."),
        (403, "", f"key; {PAIR_AGAIN}."),
    ],
)
async def test_a_rejected_key_says_to_pair_again(status: int, body: str, message: str) -> None:
    with pytest.raises(HueApiError) as failure:
        await answering(status, body).client().ping()

    assert str(failure.value) == f"The Hue bridge rejected the application {message}"
    assert failure.value.status == status
    assert failure.value.is_authentication_error
    assert not failure.value.is_transient


@pytest.mark.parametrize(
    "body",
    [
        "",
        "<html>busy</html>",
        "[]",
        "null",
        "[" * 100_000,
        '{"data":{}}',
        '{"data":[null]}',
        '{"errors":[null],"data":[]}',
        '{"errors":[{"description":42}]}',
        '{"data":[{"metadata":{"name":"Lamp"}}]}',
        '{"data":[{"id":""}]}',
        '{"data":[{"id":7}]}',
        '{"data":[{"ID":"l1"}]}',
        '{"data":[{"id":"l1","metadata":[]}]}',
        '{"data":[{"id":"l1","metadata":{"name":7}}]}',
        '{"data":[{"id":"l1","on":true}]}',
        '{"data":[{"id":"l1","on":{}}]}',
        '{"data":[{"id":"l1","on":{"on":"yes"}}]}',
        '{"data":[{"id":"l1","on":{"on":1}}]}',
        '{"data":[{"id":"r1","services":[null]}]}',
        '{"data":[{"id":"r1","services":[{"rtype":"grouped_light"}]}]}',
    ],
)
async def test_an_answer_that_is_not_a_clip_answer_is_unexpected(body: str) -> None:
    with pytest.raises(HueApiError) as failure:
        await answering(200, body).client().get_lights()

    assert str(failure.value) == UNEXPECTED
    assert failure.value.status is None
    assert failure.value.is_transient


async def test_a_write_answered_with_something_else_is_unexpected() -> None:
    with pytest.raises(HueApiError) as failure:
        await answering(200, "").client().set_on(LAMP, True)

    assert str(failure.value) == UNEXPECTED


async def test_a_null_counts_as_missing() -> None:
    body = (
        '{"errors":null,"data":[{"id":"l1","metadata":null,"on":{"on":true}},'
        '{"id":"l2","metadata":{"name":null},"on":null,"services":null}]}'
    )
    client = answering(200, body).client()

    assert await client.get_lights() == [HueLight("l1", "l1", True), HueLight("l2", "l2", False)]
    assert await client.get_light_states() == {"l1": True}


async def test_keys_are_read_as_the_bridge_writes_them() -> None:
    client = answering(200, '{"Errors":[{"description":"ignored"}],"DATA":[{"id":"l1"}]}').client()

    assert await client.get_lights() == []


async def test_the_lights_rooms_and_zones() -> None:
    answers = {
        "/clip/v2/resource/light": '{"data":[{"id":"l1","metadata":{"name":"Lamp"},"on":{"on":true}},{"id":"l2"}]}',
        "/clip/v2/resource/room": '{"data":[{"id":"r1","metadata":{"name":"Hall"},"services":['
        '{"rid":"d1","rtype":"device"},{"rid":"g1","rtype":"grouped_light"}]},{"id":"r2","services":[]}]}',
        "/clip/v2/resource/zone": '{"data":[{"id":"z1","metadata":{"name":"Upstairs"},"services":['
        '{"rid":"g2","rtype":"grouped_light"}]}]}',
        "/clip/v2/resource/grouped_light": '{"data":[{"id":"g1","on":{"on":false}},{"id":"g2","on":{"on":true}}]}',
    }

    def respond(request: RecordedRequest) -> tuple[int, str]:
        return 200, answers[request.path]

    client = InMemoryBridge(respond).client()

    assert await client.get_lights() == [HueLight("l1", "Lamp", True), HueLight("l2", "l2", False)]
    assert await client.get_groups() == [
        HueGroup("r1", "Hall", "room", "g1"),
        HueGroup("r2", "r2", "room", None),
        HueGroup("z1", "Upstairs", "zone", "g2"),
    ]
    assert await client.get_light_states() == {"l1": True}
    assert await client.get_grouped_light_states() == {"g1": False, "g2": True}


@pytest.mark.parametrize(
    ("content", "charset", "text"),
    [
        (b'{"name":"K\xc3\xbcche"}', None, '{"name":"K\u00fcche"}'),
        (b"\xef\xbb\xbfok", None, "ok"),
        (b"\xef\xbb\xbfok", "utf-8", "ok"),
        ("K\u00fcche".encode("utf-16"), "utf-16", "K\u00fcche"),
        ("\ufeffK\u00fcche".encode("utf-16-le"), "UTF-16LE", "K\u00fcche"),
        ("K\u00fcche".encode("latin-1"), '"ISO-8859-1"', "K\u00fcche"),
        ("K\u00fcche".encode("cp1252"), "windows-1252", "K\u00fcche"),
        (b"K\xfcche", None, "K\ufffdche"),
        (b"ok", "no-such-charset", "ok"),
        (b"ok", "base64", "ok"),
    ],
)
def test_an_answer_is_decoded_by_its_charset(content: bytes, charset: str | None, text: str) -> None:
    assert HttpAnswer(200, None, content, charset).text() == text


async def test_a_light_named_in_the_answers_charset() -> None:
    body = '{"data":[{"id":"l1","metadata":{"name":"K\u00fcche"},"on":{"on":false}}]}'
    client = HueClient("bridge.test", "key", None, Answering(HttpAnswer(200, None, body.encode("utf-16"), "utf-16")))

    assert await client.get_lights() == [HueLight("l1", "K\u00fcche", False)]


@pytest.mark.parametrize(
    ("reason", "status", "phrase"),
    [
        ("Bridge busy", 503, "Bridge busy"),
        (None, 503, "Service Unavailable"),
        ("", 207, "Multi-Status"),
        (None, 599, None),
    ],
)
def test_the_reason_phrase_is_the_status_lines_else_the_statuss_own(
    reason: str | None, status: int, phrase: str | None
) -> None:
    assert HttpAnswer(status, reason, b"").reason_phrase == phrase


async def test_a_failure_names_the_reason_of_the_status_line() -> None:
    client = HueClient("bridge.test", "key", None, Answering(HttpAnswer(503, "Bridge busy", b"")))

    with pytest.raises(HueApiError) as failure:
        await client.ping()

    assert str(failure.value) == "Hue bridge returned 503 Bridge busy for GET /clip/v2/resource/bridge"
