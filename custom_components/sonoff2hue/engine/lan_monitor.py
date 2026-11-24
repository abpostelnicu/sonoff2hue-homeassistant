"""The always-on, passive view of the eWeLink devices on the LAN, independent of the bridge.

A port of the .NET app's ``Web/Monitoring/LanMonitor.cs``. It also runs before any switch is mapped, so the user can
find and identify switches, and feeds the :class:`DeviceRegistry`. The .NET LAN monitor ran an mDNS listener of its
own; this one runs the config entry's listener, which the runner shares (decision 8 of this repository): each adds a
packet handler and a started callback, and the runner comes and goes while the LAN monitor and the listener go on.
"""

import asyncio
import logging

from .config import EngineConfig
from .dns import DnsRecordType
from .ewelink import SERVICE_TYPE
from .mdns import MdnsListener, MdnsPacket
from .press import SwitchTracker
from .registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

QUERY_DELAYS = (0.0, 1.0, 2.0)
"""Seconds before each of the first queries after the socket (re)starts, so queries at 0, 1 and 3 s."""
QUERY_INTERVAL = 120.0
RETRY_DELAY = 10.0


class LanMonitor:
    """Feeds a :class:`DeviceRegistry` from a :class:`SwitchTracker` of its own, which sees every packet of the
    listener it runs."""

    def __init__(self, listener: MdnsListener, registry: DeviceRegistry, config: EngineConfig) -> None:
        self._listener = listener
        self._registry = registry
        self._tracker = SwitchTracker(config)

    def config_changed(self, config: EngineConfig) -> None:
        """A fresh tracker picks up new device keys; its first broadcast per device is a baseline, never a press."""
        self._tracker = SwitchTracker(config)

    async def run(self) -> None:
        """Runs the listener and feeds the registry until cancelled. The registry is ``listening`` while the listener
        runs, including while it retries its socket. If the listener stops unexpectedly, it starts again 10 s later.
        When this ends, the LAN monitor's packet handler and started callback are gone from the listener."""
        remove_handler = self._listener.add_packet_handler(self._on_packet)
        remove_started = self._listener.add_started(self._query)
        try:
            while True:
                try:
                    self._registry.listening = True
                    await self._listener.run()
                except Exception:
                    self._registry.listening = False
                    _LOGGER.exception("The LAN monitor stopped; restarting it in %g s.", RETRY_DELAY)
                    await asyncio.sleep(RETRY_DELAY)
        finally:
            remove_handler()
            await remove_started()
            self._registry.listening = False

    def _on_packet(self, packet: MdnsPacket) -> None:
        self._registry.apply(self._tracker.process(packet), packet.received_at)

    async def _query(self, listener: MdnsListener) -> None:
        self._tracker.reset_all()

        # A few quick queries make every device answer with its current state; later ones keep "last seen" fresh.
        for delay in QUERY_DELAYS:
            await asyncio.sleep(delay)
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)

        while True:
            await asyncio.sleep(QUERY_INTERVAL)
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)
