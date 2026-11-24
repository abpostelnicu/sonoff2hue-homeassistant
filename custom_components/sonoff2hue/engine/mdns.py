"""Passive mDNS: the packets the listener receives.

A port of the .NET app's ``Mdns/MdnsListener.cs``.
"""

from dataclasses import dataclass
from datetime import datetime

from .dns import DnsMessage


@dataclass(frozen=True, slots=True)
class MdnsPacket:
    """A parsed mDNS packet with its sender (address and port) and the time it was received."""

    message: DnsMessage
    remote: tuple[str, int]
    received_at: datetime
