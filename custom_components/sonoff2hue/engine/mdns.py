"""Passive mDNS: an IPv4 listener on 224.0.0.251:5353 that shares the port with the host's own responder.

A port of the .NET app's ``Mdns/MdnsListener.cs`` and of the parts of .NET's ``NetworkInterface`` and ``NetworkChange``
it relies on. It shares UDP 5353 through SO_REUSEADDR and SO_REUSEPORT, so it sees every multicast announcement on the
LAN, including the unsolicited TXT updates of eWeLink devices. On Linux it is bound to the group, so that it receives
multicast only (decision 19). The only packets it sends are the queries its users ask for. One listener can serve
several users: each adds a packet handler and a callback that runs whenever the socket was (re)created, as each had a
listener of its own in the .NET app.
"""

import array
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import logging
import socket
import struct
import sys
from typing import Protocol

import ifaddr

from .dns import DnsMessage, build_query

_LOGGER = logging.getLogger(__name__)

TRACE = 5
"""The level of the .NET app's trace lines, below DEBUG."""

MULTICAST_ADDRESS = "224.0.0.251"
PORT = 5353
RETRY_DELAY = 5.0
NETWORK_CHANGE_DEBOUNCE = 3.0

BIND_ADDRESS = MULTICAST_ADDRESS if sys.platform == "linux" else "0.0.0.0"  # noqa: S104 - see below
"""The socket's own address. On Linux, the group: the socket then receives multicast only, so it never takes a unicast
reply meant for another responder that shares the port, such as Home Assistant's zeroconf, and its queries still go out
from the address of the interface they are sent on. macOS refuses to send from a socket bound to a multicast address
(EADDRNOTAVAIL), so elsewhere it is every address, as in the .NET app (decision 19)."""


@dataclass(frozen=True, slots=True)
class MdnsPacket:
    """A parsed mDNS packet with its sender (address and port) and the time it was received."""

    message: DnsMessage
    remote: tuple[str, int]
    received_at: datetime
    from_this_host: bool = False
    """Whether the sender is one of this host's own addresses: a packet this host sent, or a copy that a relay on it
    repeats, such as Home Assistant OS's multicast plugin from its ``hassio`` bridge (decision 19)."""


class MulticastTransport(Protocol):
    """An open mDNS socket: the interfaces it joined, this host's own addresses, and sending on one of them."""

    @property
    def interfaces(self) -> list[str]: ...

    @property
    def local_addresses(self) -> frozenset[str]:
        """The IPv4 addresses of all of this host's interfaces when the socket opened."""

    def send(self, data: bytes, interface: str) -> None:
        """Sends a multicast packet out of ``interface``; raises ``OSError``."""

    def close(self) -> None: ...


type DatagramHandler = Callable[[bytes, tuple[str, int]], None]
type ErrorHandler = Callable[[OSError], None]
type TransportFactory = Callable[[DatagramHandler, ErrorHandler], Awaitable[MulticastTransport]]
type NetworkWatcher = Callable[[Callable[[], None]], Callable[[], None]]
type StartedCallback = Callable[[MdnsListener], Awaitable[None]]


class MdnsListener:
    """Receives mDNS packets (responses and queries) while :meth:`run` runs, and sends queries."""

    def __init__(
        self,
        transport_factory: TransportFactory | None = None,
        network_watcher: NetworkWatcher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport_factory = transport_factory or open_multicast_transport
        self._network_watcher = network_watcher or watch_network_changes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._handlers: list[Callable[[MdnsPacket], None]] = []
        self._started: list[StartedCallback] = []
        self._transport: MulticastTransport | None = None
        self._started_tasks: dict[asyncio.Task[None], StartedCallback] = {}  # in the order they started
        self._restart: asyncio.Future[None] | None = None
        self._debounce: asyncio.TimerHandle | None = None

    @property
    def is_open(self) -> bool:
        return self._transport is not None

    def add_packet_handler(self, handler: Callable[[MdnsPacket], None]) -> Callable[[], None]:
        """Calls ``handler`` for every packet; returns a function that removes it."""
        self._handlers.append(handler)
        return lambda: self._handlers.remove(handler)

    def add_started(self, callback: StartedCallback) -> Callable[[], Awaitable[None]]:
        """Runs ``callback`` every time the socket was (re)created and joined the multicast group, and right away if it
        is open. Users reset what they derived from earlier packets and send their queries again there. It runs at once
        until it first waits, as .NET raises ``Started`` right after it opens the socket, so the reset comes before the
        socket's first packet. The callback is cancelled when its socket closes. Returns a coroutine function that
        removes it, cancels it if it is running, and waits for that to end, so that a user can stop while the listener
        goes on."""
        self._started.append(callback)
        if self._transport is not None:
            self._start(callback)

        async def remove() -> None:
            self._started.remove(callback)
            tasks = [task for task, owner in self._started_tasks.items() if owner is callback]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return remove

    async def run(self) -> None:
        """Receives packets until cancelled, and re-creates the socket after network changes and errors."""
        loop = asyncio.get_running_loop()
        stop_watching = self._network_watcher(self._on_network_change)
        try:
            while True:
                retry_later = False
                # A network change while the socket opens restarts it right away; one between sockets is absorbed.
                restart = self._restart = loop.create_future()
                errors: asyncio.Future[OSError] = loop.create_future()

                def on_error(err: OSError, errors: asyncio.Future[OSError] = errors) -> None:
                    if not errors.done():
                        errors.set_result(err)

                try:
                    self._transport = await self._transport_factory(self._on_datagram, on_error)
                    for callback in list(self._started):  # a callback runs at once and may add or remove some
                        self._start(callback)
                    await asyncio.wait((restart, errors), return_when=asyncio.FIRST_COMPLETED)
                    if errors.done():
                        raise errors.result()
                    _LOGGER.info("Network configuration changed; rejoining the mDNS multicast group.")
                except OSError as err:
                    _LOGGER.warning("mDNS socket error (%s); retrying in %gs.", _message(err), RETRY_DELAY)
                    retry_later = True
                finally:
                    self._restart = None
                    await self._close()

                if retry_later:
                    await asyncio.sleep(RETRY_DELAY)
        finally:
            stop_watching()
            if self._debounce is not None:
                self._debounce.cancel()

    def query(self, name: str, record_type: int) -> None:
        """Sends a multicast query on every joined interface."""
        transport = self._transport
        if transport is None:
            return
        packet = build_query(name, record_type)
        for interface in transport.interfaces:
            try:
                transport.send(packet, interface)
            except OSError as err:
                _LOGGER.debug("mDNS query for %s on %s failed: %s", name, interface, _message(err))

    def _start(self, callback: StartedCallback) -> None:
        task = asyncio.get_running_loop().create_task(self._run_started(callback), eager_start=True)
        if not task.done():
            self._started_tasks[task] = callback
            task.add_done_callback(lambda done: self._started_tasks.pop(done, None))

    async def _run_started(self, callback: StartedCallback) -> None:
        try:
            await callback(self)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("mDNS start handler failed.")

    async def _close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        tasks = list(self._started_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _on_datagram(self, data: bytes, remote: tuple[str, int]) -> None:
        try:
            message = DnsMessage.parse(data)
        except Exception as err:  # noqa: BLE001 - untrusted input must never stop the listener
            _LOGGER.log(TRACE, "Ignoring malformed mDNS packet from %s:%s: %s", remote[0], remote[1], err)
            return

        transport = self._transport
        from_this_host = transport is not None and remote[0] in transport.local_addresses
        packet = MdnsPacket(message, remote, self._clock(), from_this_host)
        for handler in list(self._handlers):
            try:
                handler(packet)
            except Exception:
                _LOGGER.exception("mDNS packet handler failed.")

    def _on_network_change(self) -> None:
        # Address changes usually arrive in bursts; restart once things settle.
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = asyncio.get_running_loop().call_later(NETWORK_CHANGE_DEBOUNCE, self._request_restart)

    def _request_restart(self) -> None:
        if self._restart is not None and not self._restart.done():
            self._restart.set_result(None)


def _message(err: OSError) -> str:
    return err.strerror or str(err)


# The socket.


class _DatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: DatagramHandler, on_error: ErrorHandler) -> None:
        self._on_datagram = on_datagram
        self._on_error = on_error

    def datagram_received(self, data: bytes, addr: tuple[str | bytes, int]) -> None:
        self._on_datagram(data, (str(addr[0]), int(addr[1])))

    def error_received(self, exc: Exception) -> None:
        self._on_error(exc if isinstance(exc, OSError) else OSError(str(exc)))

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            self.error_received(exc)


class _SocketTransport:
    def __init__(
        self,
        sock: socket.socket,
        transport: asyncio.BaseTransport,
        interfaces: list[str],
        local_addresses: frozenset[str],
    ) -> None:
        self._sock = sock
        self._transport = transport
        self._interfaces = interfaces
        self._local_addresses = local_addresses

    @property
    def interfaces(self) -> list[str]:
        return self._interfaces

    @property
    def local_addresses(self) -> frozenset[str]:
        return self._local_addresses

    def send(self, data: bytes, interface: str) -> None:
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        self._sock.sendto(data, (MULTICAST_ADDRESS, PORT))

    def close(self) -> None:
        self._transport.close()


async def open_multicast_transport(on_datagram: DatagramHandler, on_error: ErrorHandler) -> MulticastTransport:
    """Opens the mDNS socket and joins the group on every interface that :func:`multicast_interfaces` lists. The same
    look at the interfaces gives this host's own addresses."""
    loop = asyncio.get_running_loop()
    states = await loop.run_in_executor(None, interface_states)
    sock, joined = open_multicast_socket(multicast_interfaces(states), PORT)
    try:
        transport, _ = await loop.create_datagram_endpoint(lambda: _DatagramProtocol(on_datagram, on_error), sock=sock)
    except BaseException:
        sock.close()
        raise
    return _SocketTransport(sock, transport, joined, local_addresses(states))


def open_multicast_socket(interfaces: list[str], port: int = PORT) -> tuple[socket.socket, list[str]]:
    """A non-blocking UDP socket on ``port`` that shares it (SO_REUSEADDR and SO_REUSEPORT, which .NET's ReuseAddress
    sets on Unix), bound to :data:`BIND_ADDRESS`, and joined 224.0.0.251 on as many of ``interfaces`` as possible, with
    the ones it joined. Raises ``OSError`` "Network is down" when it joined none."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        joined = _configure(sock, interfaces, port)
    except BaseException:
        sock.close()
        raise
    if not joined:
        sock.close()
        raise OSError(errno.ENETDOWN, "Network is down")
    _LOGGER.debug("Listening for mDNS on %s", ", ".join(joined))
    return sock, joined


def _configure(sock: socket.socket, interfaces: list[str], port: int) -> list[str]:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((BIND_ADDRESS, port))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.setblocking(False)

    joined: list[str] = []
    for address in interfaces:
        try:
            membership = socket.inet_aton(MULTICAST_ADDRESS) + socket.inet_aton(address)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            joined.append(address)
        except OSError as err:
            _LOGGER.debug("Could not join mDNS group on %s: %s", address, _message(err))
    return joined


# The interfaces, as .NET's NetworkInterface sees them on Linux (pal_interfaceaddresses.c, pal_maphardwaretype.c).

_IFF_UP = 0x1
_IFF_LOOPBACK = 0x8
_IFF_RUNNING = 0x40
_IFF_ALLMULTI = 0x200
_IFF_MULTICAST = 0x1000
_SIOCGIFFLAGS = 0x8913
_SIOCGIFHWADDR = 0x8927
_SIOCETHTOOL = 0x8946
_ETHTOOL_GLINK = 0x0000000A
_ARPHRD_LOOPBACK = 772
# ARPHRD types that .NET maps to an interface type other than Unknown, Tunnel and Loopback. Only those get the ethtool
# link check.
_ARPHRD_CHECKED = frozenset({1, 2, 4, 19, 256, 257, 258, 259, 512, 774, 801, 802, 803})


@dataclass(frozen=True, slots=True)
class InterfaceState:
    name: str
    up: bool
    """.NET's ``OperationalStatus.Up``: IFF_UP and IFF_RUNNING, and a link where ethtool can tell."""
    multicast: bool
    """IFF_MULTICAST or IFF_ALLMULTI."""
    loopback: bool
    ipv4: tuple[str, ...]


def multicast_interfaces(states: list[InterfaceState] | None = None) -> list[str]:
    """The IPv4 addresses of the interfaces that are up, multicast-capable and not loopback, in interface order
    (``GetMulticastInterfaces``). Blocking: run it in an executor."""
    addresses: list[str] = []
    for state in interface_states() if states is None else states:
        if state.up and state.multicast and not state.loopback:
            for address in state.ipv4:
                if not address.startswith("127.") and address not in addresses:
                    addresses.append(address)
    return addresses


def local_addresses(states: list[InterfaceState]) -> frozenset[str]:
    """The IPv4 addresses of all of the interfaces, whatever their state: this host's own addresses."""
    return frozenset(address for state in states for address in state.ipv4)


def interface_states() -> list[InterfaceState]:
    """Every interface with its state and IPv4 addresses, in index order. Blocking."""
    ipv4: dict[str, list[str]] = {}
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if isinstance(ip.ip, str):
                # An alias such as eth0:1 belongs to eth0, as if_nametoindex says.
                ipv4.setdefault(adapter.name.split(":", 1)[0], []).append(ip.ip)

    if sys.platform != "linux":
        # Development on other systems: without Linux's ioctls, every interface with an address counts.
        return [
            InterfaceState(
                name, up=True, multicast=True, loopback=all(ip.startswith("127.") for ip in ips), ipv4=tuple(ips)
            )
            for name, ips in ipv4.items()
        ]

    import fcntl  # noqa: PLC0415 - Unix only

    def ioctl_short(sock: socket.socket, request: int, name: str) -> int:
        return int(struct.unpack_from("H", fcntl.ioctl(sock, request, _ifreq(name)), 16)[0])

    def link_detected(sock: socket.socket, name: str) -> bool | None:
        """ETHTOOL_GLINK; ``None`` when the driver can't tell."""
        command = array.array("I", (_ETHTOOL_GLINK, 0))
        address, _ = command.buffer_info()
        try:
            fcntl.ioctl(sock, _SIOCETHTOOL, _ifreq(name, struct.pack("P", address)))
        except OSError:
            return None
        return command[1] != 0

    states: list[InterfaceState] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex():
            try:
                flags = ioctl_short(probe, _SIOCGIFFLAGS, name)
            except OSError:
                continue
            try:
                hardware_type: int | None = ioctl_short(probe, _SIOCGIFHWADDR, name)
            except OSError:
                hardware_type = None
            loopback = hardware_type == _ARPHRD_LOOPBACK if hardware_type is not None else bool(flags & _IFF_LOOPBACK)
            up = (flags & (_IFF_UP | _IFF_RUNNING)) == (_IFF_UP | _IFF_RUNNING)
            if up and hardware_type in _ARPHRD_CHECKED and link_detected(probe, name) is False:
                up = False
            states.append(
                InterfaceState(
                    name=name,
                    up=up,
                    multicast=bool(flags & (_IFF_MULTICAST | _IFF_ALLMULTI)),
                    loopback=loopback,
                    ipv4=tuple(ipv4.get(name, ())),
                )
            )
    return states


def _ifreq(name: str, payload: bytes = b"") -> bytes:
    """A ``struct ifreq``: the interface name, then the 24-byte union."""
    return struct.pack("16s", name.encode()[:15]) + payload.ljust(24, b"\0")


# Network changes, as .NET's NetworkChange.NetworkAddressChanged reports them on Linux (pal_networkchange.c): an
# RTM_NEWADDR or RTM_DELADDR message from the IPv4 address group of a netlink route socket.

_RTMGRP_IPV4_IFADDR = 0x10
_NLMSG_ERROR = 2
_NLMSG_DONE = 3
_RTM_NEWADDR = 20
_RTM_DELADDR = 21
_POLL_INTERVAL = 5.0


def address_changes(data: bytes) -> int:
    """How many address changes a netlink datagram reports. Like .NET, it stops at NLMSG_DONE and NLMSG_ERROR."""
    changes = 0
    offset = 0
    while offset + 16 <= len(data):
        length, message_type = struct.unpack_from("=IH", data, offset)
        if length < 16 or offset + length > len(data) or message_type in (_NLMSG_DONE, _NLMSG_ERROR):
            break
        if message_type in (_RTM_NEWADDR, _RTM_DELADDR):
            changes += 1
        offset += (length + 3) & ~3
    return changes


def watch_network_changes(on_change: Callable[[], None]) -> Callable[[], None]:
    """Calls ``on_change`` on every change of an IPv4 address; returns a function that stops watching."""
    loop = asyncio.get_running_loop()
    netlink = getattr(socket, "AF_NETLINK", None)
    if netlink is None:
        return _poll_network_changes(loop, on_change)

    sock = socket.socket(netlink, socket.SOCK_RAW, socket.NETLINK_ROUTE)
    try:
        sock.bind((0, _RTMGRP_IPV4_IFADDR))
        sock.setblocking(False)
    except OSError as err:
        sock.close()
        _LOGGER.debug("Can't watch the network with netlink (%s); polling the interfaces instead.", _message(err))
        return _poll_network_changes(loop, on_change)

    def read() -> None:
        while True:
            try:
                data = sock.recv(65536)
            except BlockingIOError:
                return
            except OSError as err:
                if err.errno == errno.ENOBUFS:
                    # The kernel dropped messages, so something changed.
                    on_change()
                    continue
                return
            for _ in range(address_changes(data)):
                on_change()

    loop.add_reader(sock.fileno(), read)

    def stop() -> None:
        loop.remove_reader(sock.fileno())
        sock.close()

    return stop


def _poll_network_changes(loop: asyncio.AbstractEventLoop, on_change: Callable[[], None]) -> Callable[[], None]:
    async def poll() -> None:
        previous = await loop.run_in_executor(None, _ipv4_addresses)
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            current = await loop.run_in_executor(None, _ipv4_addresses)
            if current != previous:
                previous = current
                on_change()

    task = loop.create_task(poll())

    def stop() -> None:
        task.cancel()

    return stop


def _ipv4_addresses() -> set[tuple[str, str]]:
    return {(adapter.name, ip.ip) for adapter in ifaddr.get_adapters() for ip in adapter.ips if isinstance(ip.ip, str)}
