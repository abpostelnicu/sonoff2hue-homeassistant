"""The Hue client over real HTTPS, against the fake bridge: what the .NET oracle's canned answers can't show, namely the
pinned certificate and failed connections. How answers are read is in test_dotnet_parity.py."""

import asyncio
from collections.abc import Callable
import hashlib
import socket

import pytest

from custom_components.sonoff2hue.engine import hue_client
from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.hue_address import create_base_url
from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueClient, HueTarget, HueTargetKind
from tools.fake_hue.bridge import FakeIds, Fault

from ..hue import FakeHueHost

PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"


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


async def test_a_host_without_an_idn_form_fails_as_in_dotnet() -> None:
    client = HueClient("xn--b\u00fccher.local", "key", None)

    with pytest.raises(UnexpectedError) as failure:
        await ping(client)

    assert failure.value.dotnet_type == "UriFormatException"


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


@pytest.mark.usefixtures("socket_enabled")
async def test_a_connection_closed_during_the_handshake_is_reported_as_dotnet_reports_it() -> None:
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

    assert str(failure.value) == (
        "Hue bridge not reachable: The SSL connection could not be established, see inner exception."
    )


def test_a_name_that_does_not_resolve_is_reported_with_the_resolvers_reason() -> None:
    url = create_base_url("bridge.invalid")

    message = hue_client._connect_failure(socket.gaierror(socket.EAI_NONAME, "Name or service not known"), url)

    assert message == "Name or service not known (bridge.invalid:443)"
