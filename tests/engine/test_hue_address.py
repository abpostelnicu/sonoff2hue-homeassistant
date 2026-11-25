"""The Hue bridge's address: which addresses are accepted, how they are written, and what requests connect to."""

import pytest

from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.hue_address import create_base_url, try_create_base_url


def invalid(address: str) -> str:
    return (
        f"'{address}' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, for "
        "example 192.168.1.20 or bridge.local:8443."
    )


@pytest.mark.parametrize(
    ("address", "url", "host", "port"),
    [
        # IPv4, as ipaddress writes it.
        ("192.168.1.20", "https://192.168.1.20", "192.168.1.20", 443),
        (" 192.168.1.20\t", "https://192.168.1.20", "192.168.1.20", 443),
        ("192.168.1.20:8443", "https://192.168.1.20:8443", "192.168.1.20", 8443),
        ("192.168.1.20:443", "https://192.168.1.20", "192.168.1.20", 443),
        ("10.0.0.1:1", "https://10.0.0.1:1", "10.0.0.1", 1),
        ("10.0.0.1:65535", "https://10.0.0.1:65535", "10.0.0.1", 65535),
        ("10.0.0.1:08443", "https://10.0.0.1:8443", "10.0.0.1", 8443),
        # IPv6, compressed and in brackets, with its zone; a port needs the brackets.
        ("fe80::1", "https://[fe80::1]", "[fe80::1]", 443),
        ("[FE80:0:0::0001]", "https://[fe80::1]", "[fe80::1]", 443),
        ("[2001:db8::1]:8443", "https://[2001:db8::1]:8443", "[2001:db8::1]", 8443),
        ("fe80::1%eth0", "https://[fe80::1%eth0]", "[fe80::1%eth0]", 443),
        ("[fe80::1%eth0.5]:8443", "https://[fe80::1%eth0.5]:8443", "[fe80::1%eth0.5]", 8443),
        ("fe80::1:8443", "https://[fe80::1:8443]", "[fe80::1:8443]", 443),
        ("::ffff:192.0.2.1", "https://[::ffff:192.0.2.1]", "[::ffff:192.0.2.1]", 443),
        # Host names, lowercased.
        ("Bridge.LOCAL", "https://bridge.local", "bridge.local", 443),
        ("hue-bridge", "https://hue-bridge", "hue-bridge", 443),
        ("localhost:8443", "https://localhost:8443", "localhost", 8443),
        ("bridge-1.example.com", "https://bridge-1.example.com", "bridge-1.example.com", 443),
        ("1.2.3.4a", "https://1.2.3.4a", "1.2.3.4a", 443),
        ("a" * 63 + ".local", f"https://{'a' * 63}.local", "a" * 63 + ".local", 443),
        # Internationalized names: requests go to the ASCII form.
        ("B\u00fccher.local:8443", "https://xn--bcher-kva.local:8443", "b\u00fccher.local", 8443),
        ("XN--BCHER-KVA.local", "https://xn--bcher-kva.local", "b\u00fccher.local", 443),
    ],
)
def test_an_accepted_address(address: str, url: str, host: str, port: int) -> None:
    base, error = try_create_base_url(address)

    assert error is None
    assert base is not None
    assert (str(base.url), base.host, base.port) == (url, host, port)


@pytest.mark.parametrize(
    "address",
    [
        # Anything but a host and a port.
        "https://192.168.1.20",
        "admin@192.168.1.20",
        "192.168.1.20/api",
        "192.168.1.20?x=1",
        "192.168.1.20#top",
        "hue bridge",
        "bridge\u00a0local",
        ":8443",
        # A port from 1 to 65535, in ASCII digits.
        "bridge.local:",
        "bridge.local:0",
        "bridge.local:65536",
        "bridge.local:123456",
        "bridge.local:https",
        "bridge.local:+80",
        "bridge.local:\u0668\u0660",
        # Not an IPv4 address, and a host name's last label can't be all digits.
        "192.168.1",
        "1.2.3.4.5",
        "999.1.1.1",
        "192.168.1.020",
        # Labels of letters, digits and hyphens, at most 63 of them each and 253 in all.
        "-bridge.local",
        "bridge-.local",
        "bridge..local",
        "bridge.local.",
        ".bridge",
        "bridge_1.local",
        "a" * 64 + ".local",
        ".".join(["a" * 63] * 4),
        "xn--b\u00fccher.local",
        "xn--zzzzzz.local",
        # Brackets hold an IPv6 address only.
        "[192.168.1.20]",
        "[bridge.local]",
        "[]",
        "[fe80::1",
        "fe80::1]",
        "[fe80::1]8443",
        "fe80::1::2",
        "fe80::1%",
        "fe80::1%eth0#x",
    ],
)
def test_a_refused_address(address: str) -> None:
    assert try_create_base_url(address) == (None, invalid(address))


@pytest.mark.parametrize("address", [None, "", " \t\n"])
def test_an_empty_address(address: str | None) -> None:
    assert try_create_base_url(address) == (None, "The Hue bridge address is empty.")


def test_create_base_url_raises_the_reason() -> None:
    with pytest.raises(ConfigError) as failure:
        create_base_url("hue bridge")

    assert str(failure.value) == invalid("hue bridge")


@pytest.mark.parametrize(
    ("address", "uri"),
    [
        ("192.168.1.20", "https://192.168.1.20/"),
        ("[fe80::1%eth0]:8443", "https://[fe80::1%eth0]:8443/"),
        ("xn--bcher-kva.local:8443", "https://b\u00fccher.local:8443/"),
    ],
)
def test_the_url_as_shown(address: str, uri: str) -> None:
    base = create_base_url(address)

    assert base.uri == str(base) == uri
