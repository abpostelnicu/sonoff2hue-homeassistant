"""The Hue bridge's address: a host name or IP address, optionally with a port.

An address is an IPv4 address, an IPv6 address (in brackets when a port follows, and with a zone if it is link-local),
or a host name of letters, digits and hyphens, internationalized names included. Requests go to HTTPS on port 443
unless the address names another port.
"""

from dataclasses import dataclass
import ipaddress
import re

from yarl import URL

from .config import ConfigError

DEFAULT_PORT = 443

_BRACKETED = re.compile(r"\[(?P<host>[^\[\]]+)\](?::(?P<port>[0-9]{1,5}))?")
_HOST_AND_PORT = re.compile(r"(?P<host>[^:\[\]]+)(?::(?P<port>[0-9]{1,5}))?")
_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
"""A label of a host name in its ASCII form: letters, digits and hyphens, neither first nor last a hyphen."""
_ZONE = re.compile(r"[A-Za-z0-9._~-]+")
"""An IPv6 zone, such as ``eth0`` or ``3``: the characters a URL may hold there unescaped."""
_MAX_NAME = 253


@dataclass(frozen=True, slots=True)
class BridgeUrl:
    """The base URL of the bridge's HTTPS API."""

    url: URL
    """Where requests go: an internationalized name in its ASCII form, an IPv6 address with its zone."""
    host: str
    """The host as shown: a lowercased host name, an IPv4 address, or an IPv6 address in brackets."""
    port: int

    @property
    def uri(self) -> str:
        """The URL as shown, such as ``https://192.168.1.20/`` or ``https://bridge.local:8443/``."""
        port = "" if self.port == DEFAULT_PORT else f":{self.port}"
        return f"https://{self.host}{port}/"

    def __str__(self) -> str:
        return self.uri


def create_base_url(address: str) -> BridgeUrl:
    """The bridge's base URL; raises :class:`ConfigError` with the reason when the address can't be used."""
    value = address.strip()
    if not value:
        raise ConfigError("The Hue bridge address is empty.")
    url = _parse(value)
    if url is None:
        raise ConfigError(
            f"'{value}' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, "
            "for example 192.168.1.20 or bridge.local:8443."
        )
    return url


def _parse(value: str) -> BridgeUrl | None:
    if match := _BRACKETED.fullmatch(value):
        host, port = match["host"], match["port"]
        parsed = _ipv6(host)
    elif value.count(":") > 1:
        # An IPv6 address without brackets can't be followed by a port.
        host, port = value, None
        parsed = _ipv6(host)
    elif match := _HOST_AND_PORT.fullmatch(value):
        host, port = match["host"], match["port"]
        parsed = _ipv4(host) or _host_name(host)
    else:
        return None
    if parsed is None:
        return None

    number = DEFAULT_PORT if port is None else int(port)
    if not 1 <= number <= 65535:
        return None
    url_host, shown = parsed
    url = URL.build(scheme="https", host=url_host, port=None if number == DEFAULT_PORT else number)
    return BridgeUrl(url, shown, number)


def _ipv6(text: str) -> tuple[str, str] | None:
    """The host of the URL and the host as shown, for an IPv6 address."""
    try:
        address = ipaddress.IPv6Address(text)
    except ValueError:
        return None
    if address.scope_id is not None and not _ZONE.fullmatch(address.scope_id):
        return None
    return address.compressed, f"[{address.compressed}]"


def _ipv4(text: str) -> tuple[str, str] | None:
    try:
        address = ipaddress.IPv4Address(text)
    except ValueError:
        return None
    return str(address), str(address)


def _host_name(text: str) -> tuple[str, str] | None:
    """A host name in its ASCII form, which an internationalized name gets through IDNA, and as shown, both lowercased
    by yarl."""
    try:
        url = URL.build(scheme="https", host=text)
        ascii_name, shown = url.raw_host or "", url.host or ""
    except ValueError:  # also an IDNA failure, such as a label that is too long or an "xn--" label that doesn't decode
        return None
    labels = ascii_name.split(".")
    if len(ascii_name) > _MAX_NAME or not all(_LABEL.fullmatch(label) for label in labels) or labels[-1].isdigit():
        return None
    return ascii_name, shown
