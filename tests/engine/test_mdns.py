"""Tests of the mDNS listener: its packets, queries, socket, interfaces and network changes, with fakes, and with real
sockets where they matter."""

import asyncio
from collections.abc import Awaitable, Callable
import errno
import logging
import select
import socket
import struct
import sys
from time import monotonic

import ifaddr
import pytest

from custom_components.sonoff2hue.engine import mdns
from custom_components.sonoff2hue.engine.dns import DnsRecordType
from custom_components.sonoff2hue.engine.mdns import InterfaceState, MdnsListener, MdnsPacket

from .fakes import FakeNetwork, until
from .packets import announcement

QUERY = bytes((0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)) + b"\x08_ewelink\x04_tcp\x05local\x00" + bytes((0, 12, 0, 1))
ON_LINUX = pytest.mark.skipif(sys.platform != "linux", reason="the socket is bound to the group on Linux only")


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeNetwork:
    monkeypatch.setattr(mdns, "RETRY_DELAY", 0.01)
    monkeypatch.setattr(mdns, "NETWORK_CHANGE_DEBOUNCE", 0.05)
    return FakeNetwork()


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_delivers_packets_to_every_handler_and_survives_bad_ones(
    network: FakeNetwork, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(mdns.TRACE)
    listener = MdnsListener(network.open, network.watch)
    first: list[MdnsPacket] = []
    second: list[MdnsPacket] = []
    listener.add_packet_handler(first.append)

    def failing(packet: MdnsPacket) -> None:
        raise RuntimeError("boom")

    listener.add_packet_handler(failing)
    remove = listener.add_packet_handler(second.append)
    task = asyncio.create_task(listener.run())
    await network.next_open()

    network.deliver(announcement("1000abcdef", 5, "{}", None, encrypted=False), ("10.0.0.5", 5353))
    network.deliver(b"\x00\x01", ("10.0.0.6", 5353))
    remove()
    network.deliver(QUERY, ("10.0.0.7", 5353))

    assert [p.remote for p in first] == [("10.0.0.5", 5353), ("10.0.0.7", 5353)]
    assert [p.remote for p in second] == [("10.0.0.5", 5353)]
    assert first[0].received_at.tzinfo is not None
    assert "Ignoring malformed mDNS packet from 10.0.0.6:5353: DNS packet shorter than header." in caplog.text
    assert caplog.text.count("mDNS packet handler failed.") == 2
    await _stop(task)
    assert network.transports[0].closed
    assert not network.watching


async def test_queries_go_out_on_every_joined_interface(network: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    listener = MdnsListener(network.open, network.watch)
    listener.query("_ewelink._tcp.local", DnsRecordType.PTR)  # not open yet: nothing to send on
    task = asyncio.create_task(listener.run())
    transport = await network.next_open()

    listener.query("_ewelink._tcp.local", DnsRecordType.PTR)

    assert transport.sent == [(QUERY, "192.0.2.1")]
    assert "mDNS query for _ewelink._tcp.local on 198.51.100.1 failed: Network is unreachable" in caplog.text
    await _stop(task)


async def test_started_callbacks_run_for_every_socket_and_stop_with_it(network: FakeNetwork) -> None:
    listener = MdnsListener(network.open, network.watch)
    runs: list[str] = []
    cancelled: list[str] = []

    def started(label: str) -> Callable[[MdnsListener], asyncio.Future[None]]:
        async def callback(_: MdnsListener) -> None:
            runs.append(label)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(label)
                raise

        return callback  # type: ignore[return-value]

    listener.add_started(started("before"))
    task = asyncio.create_task(listener.run())
    await network.next_open()
    listener.add_started(started("while open"))
    await asyncio.sleep(0)
    assert runs == ["before", "while open"]

    network.network_changed()
    await network.next_open()
    await asyncio.sleep(0)

    assert cancelled == ["before", "while open"]
    assert runs == ["before", "while open", "before", "while open"]
    await _stop(task)


async def test_removing_a_started_callback_stops_it_while_the_listener_goes_on(network: FakeNetwork) -> None:
    listener = MdnsListener(network.open, network.watch)
    runs: list[str] = []
    cancelled: list[str] = []

    def started(label: str) -> Callable[[MdnsListener], Awaitable[None]]:
        async def callback(_: MdnsListener) -> None:
            runs.append(label)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(label)
                raise

        return callback

    listener.add_started(started("kept"))
    remove = listener.add_started(started("removed"))
    task = asyncio.create_task(listener.run())
    await network.next_open()
    await asyncio.sleep(0)

    await remove()
    assert cancelled == ["removed"]

    network.network_changed()
    await network.next_open()
    await asyncio.sleep(0)
    assert runs == ["kept", "removed", "kept"]
    assert cancelled == ["removed", "kept"]
    await _stop(task)


async def test_started_callbacks_run_before_the_first_packet_of_their_socket(network: FakeNetwork) -> None:
    listener = MdnsListener(network.open, network.watch)
    seen: list[str] = []

    def started(label: str) -> Callable[[MdnsListener], Awaitable[None]]:
        async def callback(_: MdnsListener) -> None:
            seen.append(label)
            await asyncio.sleep(3600)

        return callback

    listener.add_packet_handler(lambda packet: seen.append("packet"))
    listener.add_started(started("started"))
    task = asyncio.create_task(listener.run())
    await network.next_open()
    network.deliver(QUERY, ("192.0.2.7", 5353))
    listener.add_started(started("added while open"))
    assert seen == ["started", "packet", "added while open"]

    network.network_changed()
    await network.next_open()
    network.deliver(QUERY, ("192.0.2.7", 5353))

    # The started callbacks run as their socket opens, before it receives anything.
    assert seen == ["started", "packet", "added while open", "started", "added while open", "packet"]
    await _stop(task)


async def test_failing_started_callback_is_logged(network: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    listener = MdnsListener(network.open, network.watch)

    async def failing(_: MdnsListener) -> None:
        raise RuntimeError("boom")

    listener.add_started(failing)
    task = asyncio.create_task(listener.run())
    await network.next_open()
    await asyncio.sleep(0)

    assert "mDNS start handler failed." in caplog.text
    await _stop(task)


async def test_socket_errors_are_retried(network: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    network.failures.append(OSError(errno.ENETDOWN, "Network is down"))
    listener = MdnsListener(network.open, network.watch)
    task = asyncio.create_task(listener.run())

    first = await network.next_open()
    assert "mDNS socket error (Network is down); retrying in 0.01s." in caplog.text

    network.fail(OSError(errno.EBADF, "Bad file descriptor"))
    await network.next_open()

    assert first.closed
    assert "mDNS socket error (Bad file descriptor); retrying in 0.01s." in caplog.text
    assert listener.is_open
    await _stop(task)
    assert not listener.is_open


async def test_network_changes_rejoin_once_they_settle(network: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    listener = MdnsListener(network.open, network.watch)
    task = asyncio.create_task(listener.run())
    await network.next_open()

    for _ in range(3):
        network.network_changed()
        await asyncio.sleep(0.01)
    await network.next_open()

    assert len(network.transports) == 2
    assert caplog.text.count("Network configuration changed; rejoining the mDNS multicast group.") == 1
    await _stop(task)


async def test_packets_from_this_hosts_own_addresses_are_marked(network: FakeNetwork) -> None:
    listener = MdnsListener(network.open, network.watch)
    packets: list[MdnsPacket] = []
    listener.add_packet_handler(packets.append)
    task = asyncio.create_task(listener.run())
    await network.next_open()

    # A relay on this host, such as Home Assistant OS's multicast plugin, repeats packets from one of its addresses.
    for sender in ("192.0.2.7", "192.0.2.1", "198.51.100.1"):
        network.deliver(QUERY, (sender, 5353))
    network.local_addresses = frozenset({"192.0.2.7"})  # the host's address changed, and the socket rejoined
    network.network_changed()
    await network.next_open()
    for sender in ("192.0.2.7", "192.0.2.1"):
        network.deliver(QUERY, (sender, 5353))

    assert [(packet.remote[0], packet.from_this_host) for packet in packets] == [
        ("192.0.2.7", False),
        ("192.0.2.1", True),
        ("198.51.100.1", True),
        ("192.0.2.7", True),
        ("192.0.2.1", False),
    ]
    await _stop(task)


def test_multicast_interfaces_are_up_multicast_and_not_loopback() -> None:
    states = [
        InterfaceState("lo", up=True, multicast=False, loopback=True, ipv4=("127.0.0.1",)),
        InterfaceState("eth0", up=True, multicast=True, loopback=False, ipv4=("10.0.0.2", "10.0.0.3", "10.0.0.2")),
        InterfaceState("eth1", up=False, multicast=True, loopback=False, ipv4=("10.1.0.2",)),
        InterfaceState("tun0", up=True, multicast=False, loopback=False, ipv4=("10.2.0.2",)),
        InterfaceState("dummy", up=True, multicast=True, loopback=False, ipv4=("127.0.0.2", "10.0.0.3")),
    ]

    assert mdns.multicast_interfaces(states) == ["10.0.0.2", "10.0.0.3"]


@pytest.mark.usefixtures("socket_enabled")
def test_interface_states_read_the_flags_of_this_host(monkeypatch: pytest.MonkeyPatch) -> None:
    adapters = [ifaddr.Adapter("lo", "lo", [ifaddr.IP("127.0.0.1", 8, "lo")])]
    monkeypatch.setattr(mdns.ifaddr, "get_adapters", lambda: adapters)

    states = {state.name: state for state in mdns.interface_states()}

    assert states["lo"] == InterfaceState(
        "lo", up=True, multicast=states["lo"].multicast, loopback=True, ipv4=("127.0.0.1",)
    )
    assert mdns.multicast_interfaces(list(states.values())) == []


def _netlink(*messages: tuple[int, bytes]) -> bytes:
    data = b""
    for message_type, payload in messages:
        length = 16 + len(payload)
        data += struct.pack("=IHHII", length, message_type, 0, 0, 0) + payload + b"\0" * (-length % 4)
    return data


def test_address_changes_count_new_and_deleted_addresses() -> None:
    assert mdns.address_changes(_netlink((20, b"\x02" * 8), (16, b""), (21, b"\x01" * 5))) == 2
    assert mdns.address_changes(_netlink((20, b""), (3, b""), (21, b""))) == 1
    assert mdns.address_changes(_netlink((2, b""), (20, b""))) == 0
    assert mdns.address_changes(b"\x01\x02") == 0
    assert mdns.address_changes(struct.pack("=IHHII", 100, 20, 0, 0, 0)) == 0


@pytest.mark.usefixtures("socket_enabled")
def test_open_multicast_socket_shares_the_port_and_joins_what_it_can(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]

    first, joined = mdns.open_multicast_socket(["127.0.0.1", "192.0.2.123"], port)
    second, _ = mdns.open_multicast_socket(["127.0.0.1"], port)
    try:
        assert joined == ["127.0.0.1"]
        assert first.getsockname() == ("224.0.0.251" if sys.platform == "linux" else "0.0.0.0", port)  # noqa: S104
        assert first.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert first.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT)
        assert first.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL) == 255
        assert first.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP) == 1
        assert not first.getblocking()
        assert "Could not join mDNS group on 192.0.2.123" in caplog.text
        assert "Listening for mDNS on 127.0.0.1" in caplog.text
    finally:
        first.close()
        second.close()

    with pytest.raises(OSError, match="Network is down"):
        mdns.open_multicast_socket(["192.0.2.123"], port)


def _wildcard_socket() -> socket.socket:
    """A socket as python-zeroconf opens it when Home Assistant has only its default adapter and no IPv6: on every
    address, sharing the port (Research findings). Here on a free port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("0.0.0.0", 0))  # noqa: S104
    return sock


def _count_datagrams(sockets: dict[str, socket.socket], expected: int) -> dict[str, int]:
    """How many datagrams each socket got, once they got ``expected`` together, or after 5 s."""
    counts = dict.fromkeys(sockets, 0)
    names = {sock: name for name, sock in sockets.items()}
    deadline = monotonic() + 5
    while sum(counts.values()) < expected and monotonic() < deadline:
        readable, _, _ = select.select(list(sockets.values()), [], [], 0.1)
        for sock in readable:
            sock.recv(9000)
            counts[names[sock]] += 1
    return counts


@ON_LINUX
@pytest.mark.usefixtures("socket_enabled")
def test_unicast_to_the_port_reaches_a_wildcard_socket_and_never_the_engines() -> None:
    with _wildcard_socket() as wildcard:
        port = wildcard.getsockname()[1]
        engine, _ = mdns.open_multicast_socket(["127.0.0.1"], port)
        with engine:
            # From 64 ports: Linux spreads unicast over the SO_REUSEPORT sockets bound to the same address by the
            # sender's address and port, so a second socket on 0.0.0.0 would get about half of them.
            for _ in range(64):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b"a unicast reply", ("127.0.0.1", port))
            counts = _count_datagrams({"wildcard": wildcard, "engine": engine}, 64)

    assert counts == {"wildcard": 64, "engine": 0}


@ON_LINUX
@pytest.mark.usefixtures("socket_enabled")
async def test_the_group_reaches_the_socket_and_its_queries_go_out_from_the_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    # The loopback interface stands in for the LAN, which the engine never joins on its own; nothing sent on it leaves
    # this host.
    lo = InterfaceState("lo", up=True, multicast=True, loopback=True, ipv4=("127.0.0.1",))
    monkeypatch.setattr(mdns, "interface_states", lambda: [lo])
    monkeypatch.setattr(mdns, "multicast_interfaces", lambda states=None: ["127.0.0.1"])
    packets: list[MdnsPacket] = []
    with _wildcard_socket() as other:
        port = other.getsockname()[1]
        monkeypatch.setattr(mdns, "PORT", port)
        other.setblocking(False)
        other.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        listener = MdnsListener(network_watcher=lambda on_change: lambda: None)
        listener.add_packet_handler(packets.append)
        task = asyncio.create_task(listener.run())
        await until(lambda: listener.is_open)

        listener.query("_ewelink._tcp.local", DnsRecordType.PTR)
        query, sender = await asyncio.wait_for(loop.sock_recvfrom(other, 9000), 5)
        other.sendto(announcement("1000abcdef", 5, "{}", None, encrypted=False), (mdns.MULTICAST_ADDRESS, port))
        await until(lambda: len(packets) == 2)
        await _stop(task)

    # The query comes from the interface's address, not from the group's, and loops back to the engine's socket too.
    assert (query, sender) == (QUERY, ("127.0.0.1", port))
    assert [(packet.message.is_response, packet.remote) for packet in packets] == [
        (False, ("127.0.0.1", port)),
        (True, ("127.0.0.1", port)),
    ]


@pytest.mark.usefixtures("socket_enabled")
async def test_the_transport_knows_this_hosts_addresses_from_the_look_that_chose_its_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    looks: list[None] = []

    def states() -> list[InterfaceState]:
        looks.append(None)
        return [
            InterfaceState("lo", up=True, multicast=False, loopback=True, ipv4=("127.0.0.1",)),
            InterfaceState("eth0", up=True, multicast=True, loopback=False, ipv4=("192.0.2.1",)),
            InterfaceState("eth1", up=False, multicast=True, loopback=False, ipv4=("198.51.100.1",)),
        ]

    def open_socket(interfaces: list[str], port: int = mdns.PORT) -> tuple[socket.socket, list[str]]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        return sock, interfaces

    monkeypatch.setattr(mdns, "interface_states", states)
    monkeypatch.setattr(mdns, "open_multicast_socket", open_socket)

    transport = await mdns.open_multicast_transport(lambda data, remote: None, lambda err: None)
    transport.close()
    await asyncio.sleep(0)

    assert len(looks) == 1
    assert transport.interfaces == ["192.0.2.1"]
    assert transport.local_addresses == {"127.0.0.1", "192.0.2.1", "198.51.100.1"}
