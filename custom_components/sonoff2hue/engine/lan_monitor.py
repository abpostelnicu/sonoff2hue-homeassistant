"""The always-on, passive view of the eWeLink devices on the LAN, independent of the bridge.

It also runs before any switch is mapped, so the user can find and identify switches, and feeds the
:class:`DeviceRegistry`. It runs the config entry's mDNS listener, which the runner shares (decision 8): each adds a
packet handler and a started callback, and the runner comes and goes while the LAN monitor and the listener go on.

Its queries make the devices answer, which keeps what the registry last heard of them fresh, and with it each switch's
Online (decision 36). It asks at 0, 1 and 3 s after each (re)start of the socket, then looks every 30 s: it asks when
2 minutes have passed since its last query, and at each look while a configured switch that it heard before is late,
not heard for more than 2 min 30 s but still online. Each query starts the 2 minutes again, so a look sends one query
at most. While every switch answers, that is a query every 2 minutes; after a lost answer, one every 30 s until the
switch is heard again, or offline.
"""

import asyncio
from datetime import timedelta
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
"""Seconds from a query to the next one while no switch is late: at every fourth look."""
CATCH_UP_INTERVAL = 30.0
"""Seconds between the looks after the first queries, and so between the catch-up queries: each look asks again while
a switch is late."""
CATCH_UP_AFTER = timedelta(seconds=150)
"""A configured switch that was heard before is late once it hasn't been heard for longer than this, 2 min 30 s: 30 s
more than the 2 minutes between the queries, so its answer to the last one is missing. The catch-up queries give it
more chances to answer within the 5 minutes that it stays online."""
RETRY_DELAY = 10.0


class LanMonitor:
    """Feeds a :class:`DeviceRegistry` from a :class:`SwitchTracker` of its own, which sees every packet of the
    listener it runs."""

    def __init__(self, listener: MdnsListener, registry: DeviceRegistry, config: EngineConfig) -> None:
        self._listener = listener
        self._registry = registry
        self._tracker = SwitchTracker(config)
        self._switch_ids = _switch_ids(config)

    def config_changed(self, config: EngineConfig) -> None:
        """A fresh tracker picks up new device keys; its first broadcast per device is a baseline, never a press. From
        the next look on, the new configuration's switches are those that can be late."""
        self._tracker = SwitchTracker(config)
        self._switch_ids = _switch_ids(config)

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

        # A few quick queries make every device answer with its current state; later ones keep "last seen" fresh. A
        # delay of 0 isn't awaited: the first query goes out at once, before this callback first waits.
        for delay in QUERY_DELAYS:
            if delay > 0:
                await asyncio.sleep(delay)
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)

        # Then a look every 30 s, which asks once 2 minutes have passed since the last query, or while a switch is late.
        # Each query starts the 2 minutes again, so no look sends two.
        waited = 0.0
        while True:
            await asyncio.sleep(CATCH_UP_INTERVAL)
            waited += CATCH_UP_INTERVAL
            if waited >= QUERY_INTERVAL or self._is_a_switch_late():
                listener.query(SERVICE_TYPE, DnsRecordType.PTR)
                waited = 0.0

    def _is_a_switch_late(self) -> bool:
        """Whether a configured switch that was heard before is late: not heard for more than 2 min 30 s, but still
        online."""
        registry = self._registry
        for device_id in self._switch_ids:
            device = registry.find(device_id)
            if device is not None and registry.is_online(device) and not registry.heard_within(device, CATCH_UP_AFTER):
                return True
        return False


def _switch_ids(config: EngineConfig) -> tuple[str, ...]:
    return tuple(switch.normalized_device_id for switch in config.switches)
