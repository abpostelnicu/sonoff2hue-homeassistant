"""Tests of the LAN monitor, with fake sockets and virtual time."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
import errno
import json
import logging
import math
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.lan_monitor import LanMonitor
from custom_components.sonoff2hue.engine.mdns import MdnsListener, MdnsPacket, NetworkWatcher
from custom_components.sonoff2hue.engine.press import SwitchEventKind
from custom_components.sonoff2hue.engine.registry import DeviceRegistry

from .fakes import FakeNetwork, FakeTransport, VirtualTime
from .packets import announcement, probe
from .test_mdns import QUERY
from .test_press import KEY, minir4m_json

DEVICE = "1000abcdef"
OTHER = "1000999999"
"""Another made-up MINIR4M."""
SENDER = ("10.0.0.5", 5353)
Kind = SwitchEventKind


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


def switches(*device_ids: str) -> EngineConfig:
    """A configuration with a switch for each of ``device_ids``."""
    return EngineConfig(switches=[SwitchSettings(device_id=device_id, lights=["Lamp"]) for device_id in device_ids])


class Lan:
    """A LAN monitor on a fake network, with its registry, all on virtual time."""

    def __init__(
        self, time: VirtualTime, watch: NetworkWatcher | None = None, config: EngineConfig | None = None
    ) -> None:
        self.time = time
        self.network = FakeNetwork()
        self.listener = MdnsListener(self.network.open, watch or self.network.watch, clock=time.utc)
        self.registry = DeviceRegistry(time.utc)
        self.monitor = LanMonitor(self.listener, self.registry, config or EngineConfig())
        self.events: list[Kind | None] = []
        self.registry.add_changed_callback(lambda device: self.events.append(device.last_event))

    def broadcast(self, seq: int, data: str = "abc", iv: str = "iv", device_id: str = DEVICE) -> None:
        self.network.deliver(announcement(device_id, seq, data, iv), SENDER)

    def broadcast_encrypted(self, seq: int, **params: Any) -> None:
        data, iv = ewelink.encrypt(json.dumps(minir4m_json(**params), separators=(",", ":")), KEY)
        self.broadcast(seq, data, iv)


class Answers:
    """The MINIR4Ms that answer each query as it goes out, with their state again, as they do. An answer can be lost:
    :meth:`lose` drops a device's next answers, and :meth:`stop` all of them."""

    def __init__(self, lan: Lan, *device_ids: str) -> None:
        self._lan = lan
        self._lost: dict[str, float] = dict.fromkeys(device_ids, 0)

    def lose(self, device_id: str, count: int) -> None:
        self._lost[device_id] = count

    def stop(self, device_id: str) -> None:
        self._lost[device_id] = math.inf

    def __call__(self) -> None:
        for device_id, lost in self._lost.items():
            if lost > 0:
                self._lost[device_id] = lost - 1
            else:
                self._lan.broadcast(1, device_id=device_id)


def query_times(transport: FakeTransport, time: VirtualTime, answer: Callable[[], None] = lambda: None) -> list[float]:
    """When each query went out on the transport's working interface, in seconds of virtual time; ``answer`` runs as
    each later one goes out. Call it as the socket opens: the queries sent so far went out then, before the started
    callbacks first waited."""
    assert all(data == QUERY for data, _ in transport.sent)
    times: list[float] = [time.elapsed for _ in transport.sent]
    send = transport.send

    def timed(data: bytes, interface: str) -> None:
        send(data, interface)
        assert data == QUERY
        times.append(time.elapsed)
        answer()

    transport.send = timed  # type: ignore[method-assign]
    return times


async def answered(lan: Lan, answers: Answers) -> tuple[asyncio.Task[None], list[float]]:
    """Runs the LAN monitor, whose queries ``answers`` answers, the first one too; gives its task and its queries'
    times."""
    task = asyncio.create_task(lan.monitor.run())
    sent = query_times(await lan.network.next_open(), lan.time, answers)
    answers()  # to the query that went out as the socket opened
    return task, sent


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_queries_at_once_after_one_and_three_seconds_then_every_two_minutes(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    transport = await lan.network.next_open()
    # A delay of 0 isn't awaited, so the first query went out before the started callback first waited.
    assert transport.sent == [(QUERY, "192.0.2.1")]
    sent = query_times(transport, time)

    await asyncio.sleep(250)

    assert sent == pytest.approx([0, 1, 3, 123, 243])
    await stop(task)


# The catch-up queries (decision 36). Each answer comes as its query goes out, so a switch heard at 243 s is late after
# 393 s, and online up to 543 s.


async def test_no_catch_up_queries_while_every_switch_answers(time: VirtualTime) -> None:
    lan = Lan(time, config=switches(DEVICE, OTHER))
    task, sent = await answered(lan, Answers(lan, DEVICE, OTHER))

    await asyncio.sleep(610)

    assert sent == pytest.approx([0, 1, 3, 123, 243, 363, 483, 603])
    await stop(task)


async def test_a_lost_answer_brings_a_query_every_30_s_until_the_switch_is_heard(time: VirtualTime) -> None:
    lan = Lan(time, config=switches(DEVICE))
    answers = Answers(lan, DEVICE)
    task, sent = await answered(lan, answers)
    await asyncio.sleep(300)
    answers.lose(DEVICE, 2)

    await asyncio.sleep(400)

    # The answers to the queries at 363 s and 423 s are lost; at 393 s the switch was heard 150 s before, not more. The
    # answer to the one at 453 s comes, and the 2 minutes start again from it.
    assert sent == pytest.approx([0, 1, 3, 123, 243, 363, 423, 453, 573, 693])
    device = lan.registry.find(DEVICE)
    assert device is not None
    assert device.last_seen_at == VirtualTime.START + timedelta(seconds=693)
    await stop(task)


async def test_a_switch_silent_for_more_than_5_minutes_is_asked_every_2_minutes_again(time: VirtualTime) -> None:
    lan = Lan(time, config=switches(DEVICE))
    answers = Answers(lan, DEVICE)
    task, sent = await answered(lan, answers)
    await asyncio.sleep(300)
    answers.stop(DEVICE)

    await asyncio.sleep(610)

    # A query every 30 s while it is late and online, from 423 s to 543 s; then every 2 minutes from the last one.
    assert sent == pytest.approx([0, 1, 3, 123, 243, 363, 423, 453, 483, 513, 543, 663, 783, 903])
    device = lan.registry.find(DEVICE)
    assert device is not None
    assert not lan.registry.is_online(device)
    await stop(task)


async def test_an_unconfigured_device_never_brings_catch_up_queries(time: VirtualTime) -> None:
    lan = Lan(time, config=switches(DEVICE))
    answers = Answers(lan, DEVICE, OTHER)
    task, sent = await answered(lan, answers)
    await asyncio.sleep(300)
    answers.stop(OTHER)  # no switch maps it

    await asyncio.sleep(400)

    assert sent == pytest.approx([0, 1, 3, 123, 243, 363, 483, 603])
    await stop(task)


async def test_a_config_change_changes_which_switches_can_be_late(time: VirtualTime) -> None:
    lan = Lan(time)
    answers = Answers(lan, DEVICE)
    task, sent = await answered(lan, answers)
    await asyncio.sleep(10)
    answers.stop(DEVICE)  # heard at 3 s, then silent: late after 153 s, and online up to 303 s

    await asyncio.sleep(190)
    lan.monitor.config_changed(switches(DEVICE))
    await asyncio.sleep(50)
    lan.monitor.config_changed(EngineConfig())
    await asyncio.sleep(120)

    # It counts at the looks at 213 s and 243 s, after the first change and before the second.
    assert sent == pytest.approx([0, 1, 3, 123, 213, 243, 363])
    await stop(task)


async def test_each_new_socket_rebaselines_and_queries_again(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    await lan.network.next_open()
    await asyncio.sleep(1)
    lan.broadcast(5)
    lan.broadcast(6)
    pressed_at = time.utc()

    lan.network.network_changed()
    sent = query_times(await lan.network.next_open(), time)
    restarted = time.elapsed
    await asyncio.sleep(10)
    lan.broadcast(7)

    # A new seq after the socket's restart is a baseline, not a press.
    assert restarted == pytest.approx(4)  # the listener lets the network settle for 3 s
    assert sent == pytest.approx([4, 5, 7])
    assert lan.events == [Kind.BASELINE, Kind.PRESS, Kind.BASELINE]
    device = lan.registry.find(DEVICE)
    assert device is not None
    assert (device.seq, device.last_event, device.last_press_at) == (7, Kind.BASELINE, pressed_at)
    await stop(task)


async def test_probes_and_every_other_packet_reach_the_tracker(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    await lan.network.next_open()
    await asyncio.sleep(1)

    lan.broadcast(5)
    lan.network.deliver(probe(DEVICE), SENDER)  # the device rejoined Wi-Fi
    await asyncio.sleep(1)
    lan.broadcast(9)
    await asyncio.sleep(1)
    lan.network.deliver(QUERY, ("10.0.0.6", 5353))  # another host's query

    assert lan.events == [Kind.BASELINE, Kind.BASELINE]
    assert lan.registry.last_packet_at == time.utc()
    await stop(task)


async def test_a_relays_copy_of_a_press_leaves_the_switchs_address_as_it_was(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    await lan.network.next_open()
    await asyncio.sleep(1)

    lan.network.deliver(announcement(DEVICE, 5, "abc", "iv"), SENDER)  # its answer to the query, with its A record
    # A press, without SRV and A records, then the copy that Home Assistant OS's multicast plugin repeats from its
    # bridge, one of this host's addresses (decision 19).
    lan.network.deliver(announcement(DEVICE, 6, "abc", "iv", ip=None), SENDER)
    lan.network.deliver(announcement(DEVICE, 6, "abc", "iv", ip=None), ("192.0.2.1", 5353))

    device = lan.registry.find(DEVICE)
    assert device is not None
    assert (device.address, device.seq, device.last_seen_at) == ("10.0.0.5", 6, time.utc())
    assert lan.events == [Kind.BASELINE, Kind.PRESS]
    await stop(task)


async def test_a_config_change_makes_a_fresh_tracker_with_the_new_device_keys(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    await lan.network.next_open()
    await asyncio.sleep(1)
    lan.broadcast_encrypted(1, relay="on")
    lan.broadcast_encrypted(2, relay="off")  # can't be told from a press without the key
    before = lan.registry.find(DEVICE)

    lan.monitor.config_changed(
        EngineConfig(switches=[SwitchSettings(device_id=DEVICE, device_key=KEY, lights=["Lamp"])])
    )
    lan.broadcast_encrypted(3, relay="off")
    lan.broadcast_encrypted(4, relay="on")
    after = lan.registry.find(DEVICE)

    assert lan.events == [Kind.BASELINE, Kind.PRESS, Kind.BASELINE, Kind.RELAY_CHANGED]
    assert before is not None
    assert (before.summary, before.detached) == (None, None)
    assert after is not None
    assert after.summary == "relay0=on relaySeparation=1 swMode=3 triggerType=17 rssi=-75 fwVersion=1.3.1"
    assert after.detached is True
    await stop(task)


async def test_listening_while_the_listener_runs_even_when_its_socket_fails(time: VirtualTime) -> None:
    lan = Lan(time)
    lan.network.failures.append(OSError(errno.ENETDOWN, "Network is down"))
    changes: list[bool] = []
    lan.registry.add_listening_changed_callback(lambda: changes.append(lan.registry.listening))
    task = asyncio.create_task(lan.monitor.run())

    await asyncio.sleep(1)
    assert lan.registry.listening
    assert not lan.listener.is_open
    await lan.network.next_open()  # the listener retries after 5 s

    await stop(task)
    assert changes == [True, False]


async def test_an_unexpected_stop_restarts_the_listener_ten_seconds_later(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    watches = 0

    def watch(on_change: Callable[[], None]) -> Callable[[], None]:
        nonlocal watches
        watches += 1
        if watches == 1:
            raise OSError(errno.EPERM, "Operation not permitted")
        return lan.network.watch(on_change)

    lan = Lan(time, watch)
    changes: list[bool] = []
    lan.registry.add_listening_changed_callback(lambda: changes.append(lan.registry.listening))
    task = asyncio.create_task(lan.monitor.run())

    await asyncio.sleep(9.9)
    assert changes == [True, False]
    assert not lan.network.transports
    transport = await lan.network.next_open()
    opened = time.elapsed
    sent = query_times(transport, time)
    await asyncio.sleep(5)

    assert opened == pytest.approx(10)
    assert sent == pytest.approx([10, 11, 13])
    assert changes == [True, False, True]
    (record,) = [r for r in caplog.records if r.name == "custom_components.sonoff2hue.engine.lan_monitor"]
    assert (record.levelno, record.getMessage()) == (logging.ERROR, "The LAN monitor stopped; restarting it in 10 s.")
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "[Errno 1] Operation not permitted"
    await stop(task)
    assert changes == [True, False, True, False]


async def test_stopping_detaches_it_from_the_listener(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    await lan.network.next_open()
    await stop(task)
    assert not lan.registry.listening

    # The listener can go on for others; the LAN monitor no longer sees its packets or sends queries.
    other = asyncio.create_task(lan.listener.run())
    transport = await lan.network.next_open()
    lan.broadcast(1)
    await asyncio.sleep(250)

    assert transport.sent == []
    assert lan.registry.snapshot() == []
    assert lan.registry.last_packet_at is None
    await stop(other)


async def test_another_user_of_the_listener_comes_and_goes_while_it_goes_on(time: VirtualTime) -> None:
    lan = Lan(time)
    task = asyncio.create_task(lan.monitor.run())
    sent = query_times(await lan.network.next_open(), time)
    packets: list[MdnsPacket] = []
    started: list[float] = []

    async def other_started(listener: MdnsListener) -> None:
        started.append(time.elapsed)
        await asyncio.sleep(3600)

    # As the runner attaches to the LAN monitor's listener and leaves it when the supervisor stops it.
    remove_handler = lan.listener.add_packet_handler(packets.append)
    remove_started = lan.listener.add_started(other_started)
    await asyncio.sleep(5)
    lan.broadcast(1)
    remove_handler()
    await remove_started()
    lan.broadcast(2)
    await asyncio.sleep(120)

    assert started == [0]
    assert len(packets) == 1
    assert lan.events == [Kind.BASELINE, Kind.PRESS]
    assert sent == pytest.approx([0, 1, 3, 123])
    assert lan.listener.is_open
    await stop(task)
