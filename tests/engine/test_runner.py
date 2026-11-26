"""Tests of the runner: presses in, Hue toggles out, with what it reports and logs, how it resolves the names, and the
runner on a listener (``run``) on a fake network."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import json
import logging
import re
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink, mdns, runner, toggler
from custom_components.sonoff2hue.engine.config import EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.dns import DnsRecordType, build_query
from custom_components.sonoff2hue.engine.hue_client import (
    HueApiError,
    HueClient,
    HueGroup,
    HueLight,
    HueTarget,
    HueTargetKind,
)
from custom_components.sonoff2hue.engine.mdns import MdnsListener, MdnsPacket
from custom_components.sonoff2hue.engine.runner import (
    NO_TARGETS,
    Runner,
    RunnerObserver,
    SwitchBinding,
    resolve_bindings,
)
from custom_components.sonoff2hue.engine.targets import EntryKey, RememberedResource, Renamed
from custom_components.sonoff2hue.engine.toggler import ToggleResult

from ..hue import InMemoryBridge, RecordedRequest
from .fakes import FakeNetwork, until
from .packets import DnsPacketBuilder, a, announcement, name, probe, srv, to_packet, txt

DEVICE_ID = "1000abcdef"
OTHER_DEVICE_ID = "1000999999"
DEVICE_KEY = "qa-only-device-key"
ADDRESS = "192.0.2.5"
OTHER_ADDRESS = "192.0.2.6"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
BRIDGE_DOWN_503 = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"
PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"
SUCCESS = '{"errors":[],"data":[]}'
LIGHT_OFF = '{"errors":[],"data":[{"id":"l1","metadata":{"name":"Ceiling"},"on":{"on":false}}]}'
QUERY = build_query("_ewelink._tcp.local", DnsRecordType.PTR)
CEILING = HueTarget(HueTargetKind.LIGHT, "l1", "Ceiling")


class LightBridge(InMemoryBridge):
    """A bridge with one light, l1, that answers every request with ``status`` when it isn't 200."""

    def __init__(self) -> None:
        super().__init__(self._answer)
        self.lights = {"l1": False}
        self.status = 200

    @property
    def paths(self) -> list[str]:
        return [f"{request.method} {request.path}" for request in self.requests]

    def _answer(self, request: RecordedRequest) -> tuple[int, str]:
        if self.status != 200:
            return self.status, "{}"
        light_path = "/clip/v2/resource/light/"
        if request.method == "PUT" and request.path.startswith(light_path):
            self.lights[request.path[len(light_path) :]] = request.on
            return 200, SUCCESS
        if request.path == "/clip/v2/resource/light":
            data: list[dict[str, Any]] = [{"id": key, "on": {"on": on}} for key, on in self.lights.items()]
        elif request.path == "/clip/v2/resource/bridge":
            data = [{"id": "b1", "type": "bridge"}]
        else:
            raise AssertionError(f"Unexpected request {request.method} {request.path}")
        return 200, json.dumps({"errors": [], "data": data})


class RecordingObserver(RunnerObserver):
    def __init__(self) -> None:
        self.events: list[str] = []

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        self.events.append(f"press {binding.settings.name} seq={seq}")

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        state = "on" if result.turned_on else "off"
        self.events.append(
            f"toggled {binding.settings.name} {state} changed={result.changed} unchanged={result.unchanged} "
            f"failed={result.failed}"
        )

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self.events.append(f"toggle failed {binding.settings.name}: {message}")

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self.events.append(f"dropped {binding.settings.name}")

    def on_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        self.events.append("bridge up" if reachable else f"bridge down: {message}")


class KeyObserver(RecordingObserver):
    """Records each rejected key too."""

    def on_key_rejected(self) -> None:
        self.events.append("key rejected")


class ThrowingObserver(RunnerObserver):
    def __init__(self) -> None:
        self.calls = 0

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        self._fail()

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        self._fail()

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self._fail()

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self._fail()

    def on_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        self._fail()

    def on_key_rejected(self) -> None:
        self._fail()

    def _fail(self) -> None:
        self.calls += 1
        raise RuntimeError("observer failure")


class SilentObserver(RunnerObserver):
    pass


def hall(device_key: str | None = None) -> SwitchBinding:
    """The switch "Hall", mapped to the light l1."""
    return SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", device_key, ["Ceiling"]), [CEILING])


def create_runner(
    hue: HueClient,
    observer: RunnerObserver | None = None,
    binding: SwitchBinding | None = None,
    now: datetime = NOW,
) -> Runner:
    binding = binding or hall()
    return Runner(EngineConfig(switches=[binding.settings]), [binding], hue, observer, clock=lambda: now)


def plain(seq: int, device_id: str = DEVICE_ID, received_at: datetime = NOW) -> MdnsPacket:
    """Without a device key every new sequence number of a configured switch is a press."""
    data = announcement(device_id, seq, f"payload{seq}", "iv", ip=ADDRESS)
    return to_packet(data, ADDRESS, received_at)


def with_other_device(seq: int, other_seq: int) -> MdnsPacket:
    """One packet with the broadcast of the switch, then that of another device."""
    packet = DnsPacketBuilder()
    for device_id, number, ip in ((DEVICE_ID, seq, ADDRESS), (OTHER_DEVICE_ID, other_seq, OTHER_ADDRESS)):
        instance, host = f"eWeLink_{device_id}._ewelink._tcp.local", f"eWeLink_{device_id}.local"
        entries = [f"id={device_id}", "type=plug", f"seq={number}", "encrypt=true", "iv=iv", f"data1=payload{number}"]
        packet.answer("_ewelink._tcp.local", DnsRecordType.PTR, name(instance))
        packet.answer(instance, DnsRecordType.TXT, txt(*entries), cache_flush=True)
        packet.additional(instance, DnsRecordType.SRV, srv(8081, host))
        packet.additional(host, DnsRecordType.A, a(ip))
    return to_packet(packet.build(), ADDRESS, NOW)


def encrypted(
    seq: int,
    relay: str = "on",
    led: str = "on",
    rssi: int = -70,
    ttl: int = 4500,
    device_id: str = DEVICE_ID,
    received_at: datetime = NOW,
    separation: int = 1,
) -> MdnsPacket:
    params = {
        "switches": [{"outlet": 0, "switch": relay}],
        "relaySeparation": separation,
        "sledOnline": led,
        "rssi": rssi,
    }
    data, iv = ewelink.encrypt(json.dumps(params, separators=(",", ":")), DEVICE_KEY)
    return to_packet(announcement(device_id, seq, data, iv, ip=ADDRESS, ttl=ttl), ADDRESS, received_at)


def runner_log(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    """The runner's log lines with their levels; the time a toggle took is N."""
    return [
        (record.levelno, re.sub(r" in \d+ ms", " in N ms", record.getMessage()))
        for record in caplog.records
        if record.name == runner.__name__
    ]


@pytest.fixture(autouse=True)
def no_retry_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)
    monkeypatch.setattr(runner, "RESOLVE_RETRY_DELAY", 0)


# Presses, toggles and what the observer hears of them.


async def test_a_press_toggles_the_targets_and_reports_each_step() -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    switch = create_runner(bridge.client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(5, OTHER_DEVICE_ID))
    switch.handle_packet(plain(6, OTHER_DEVICE_ID))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    assert observer.events == ["press Hall seq=101", "bridge up", "toggled Hall on changed=1 unchanged=0 failed=0"]
    assert bridge.lights["l1"] is True
    assert bridge.paths == ["GET /clip/v2/resource/light", "PUT /clip/v2/resource/light/l1"]


async def test_a_press_while_the_bridge_is_down_reports_the_failure_and_the_recovery() -> None:
    bridge = LightBridge()
    bridge.status = 503
    observer = RecordingObserver()
    switch = create_runner(bridge.client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()
    bridge.status = 200
    switch.handle_packet(plain(102))
    await switch.when_toggles_complete()

    assert observer.events == [
        "press Hall seq=101",
        f"bridge down: {BRIDGE_DOWN_503}",
        f"toggle failed Hall: {BRIDGE_DOWN_503}",
        "press Hall seq=102",
        "bridge up",
        "toggled Hall on changed=1 unchanged=0 failed=0",
    ]


async def test_a_stale_press_is_dropped_without_calling_the_bridge() -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    switch = create_runner(bridge.client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101, received_at=NOW - timedelta(seconds=10)))
    await switch.when_toggles_complete()

    assert observer.events == ["press Hall seq=101", "dropped Hall"]
    assert bridge.requests == []


async def test_a_stale_press_is_dropped_before_handle_packet_returns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    bridge = LightBridge()
    observer = RecordingObserver()
    switch = create_runner(bridge.client(), observer, now=NOW + timedelta(milliseconds=5001))
    switch.handle_packet(plain(100))

    switch.handle_packet(plain(101))

    # Nothing awaited: the toggle's task started eagerly and ended before handle_packet returned.
    assert observer.events == ["press Hall seq=101", "dropped Hall"]
    assert runner_log(caplog)[-2:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 101)."),
        (logging.WARNING, "Hall (1000abcdef): dropped a press that waited more than 5s for the bridge."),
    ]
    assert bridge.requests == []


async def test_a_toggle_that_needs_no_io_ends_before_the_next_event_of_its_packet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    observer = RecordingObserver()
    switch = create_runner(LightBridge().client(), observer)
    switch.handle_packet(plain(100))

    switch.handle_packet(with_other_device(101, 5))

    # The bridge answers at once, so the toggle ends inside handle_packet, before the other device's broadcast.
    assert observer.events == ["press Hall seq=101", "bridge up", "toggled Hall on changed=1 unchanged=0 failed=0"]
    assert runner_log(caplog)[1:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 101)."),
        (logging.INFO, "Hall (1000abcdef): turned ON light 'Ceiling' in N ms."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at 192.0.2.6."),
    ]


async def test_pings_report_only_changes_of_the_bridge_state() -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    switch = create_runner(bridge.client(), observer)

    await switch.ping_bridge()
    await switch.ping_bridge()
    bridge.status = 503
    await switch.ping_bridge()
    await switch.ping_bridge()
    bridge.status = 403
    await switch.ping_bridge()
    bridge.status = 200
    await switch.ping_bridge()

    assert observer.events == [
        "bridge up",
        "bridge down: Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/bridge",
        f"bridge down: The Hue bridge rejected the application key; {PAIR_AGAIN}.",
        "bridge up",
    ]
    assert len(bridge.requests) == 6


async def test_stopping_ends_running_and_queued_toggles_without_reports() -> None:
    entered = asyncio.Event()

    async def hang(request: RecordedRequest) -> tuple[int, str]:
        entered.set()
        await asyncio.get_running_loop().create_future()  # never answers
        raise AssertionError

    observer = RecordingObserver()
    switch = create_runner(InMemoryBridge(hang).client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await asyncio.wait_for(entered.wait(), 10)
    switch.handle_packet(plain(102))
    idle = asyncio.create_task(switch.when_toggles_complete())
    await asyncio.sleep(0)
    assert not idle.done()

    await switch.stop_toggles()
    await asyncio.wait_for(idle, 10)

    assert observer.events == ["press Hall seq=101", "press Hall seq=102"]


async def test_cancelling_a_wait_for_the_toggles_does_not_stop_them() -> None:
    answer = asyncio.Event()

    async def respond(request: RecordedRequest) -> tuple[int, str]:
        await answer.wait()
        return 200, LIGHT_OFF if request.method == "GET" else SUCCESS

    observer = RecordingObserver()
    switch = create_runner(InMemoryBridge(respond).client(), observer)
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(switch.when_toggles_complete(), 0.01)
    answer.set()
    await asyncio.wait_for(switch.when_toggles_complete(), 10)

    assert observer.events == ["press Hall seq=101", "bridge up", "toggled Hall on changed=1 unchanged=0 failed=0"]


async def test_observer_exceptions_do_not_stop_the_toggle(caplog: pytest.LogCaptureFixture) -> None:
    bridge = LightBridge()
    observer = ThrowingObserver()
    switch = create_runner(bridge.client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    assert observer.calls == 3
    assert bridge.lights["l1"] is True
    failures = [
        record for record in caplog.records if record.getMessage() == "Runner observer ThrowingObserver failed."
    ]
    assert len(failures) == 3
    assert all(record.levelno == logging.WARNING and record.exc_info for record in failures)


async def test_an_observer_without_overrides_uses_the_empty_defaults() -> None:
    bridge = LightBridge()
    switch = create_runner(bridge.client(), SilentObserver())

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()
    await switch.ping_bridge()

    assert bridge.lights["l1"] is True


async def test_a_switch_with_nothing_left_to_toggle_reports_its_presses_and_fails_each_toggle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every entry of the switch resolves to nothing (decision 32): the press is still reported, and its toggle fails
    with a message that says why, without asking the bridge."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    bridge = LightBridge()
    observer = RecordingObserver()
    nothing = SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", None, ["Porch"]), [], ["Hall (1000abcdef): no light"])
    switch = create_runner(bridge.client(), observer, nothing)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    switch.handle_packet(plain(102))
    await switch.when_toggles_complete()

    assert NO_TARGETS == "None of the lights, rooms and zones of this switch is on the Hue bridge."
    assert observer.events == [
        "press Hall seq=101",
        f"toggle failed Hall: {NO_TARGETS}",
        "press Hall seq=102",
        f"toggle failed Hall: {NO_TARGETS}",
    ]
    assert bridge.requests == []
    assert runner_log(caplog)[-2:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 102)."),
        (logging.ERROR, f"Hall (1000abcdef): could not toggle the lights: {NO_TARGETS}"),
    ]


# Encrypted broadcasts, queued presses and the age of a press.


@pytest.mark.parametrize("ceiling_on", [False, True])
@pytest.mark.parametrize("desk_on", [False, True])
@pytest.mark.parametrize("room_on", [False, True])
async def test_encrypted_presses_resolve_and_toggle_mixed_targets_without_false_presses(
    ceiling_on: bool, desk_on: bool, room_on: bool
) -> None:
    settings = SwitchSettings(" 1000ABCDEF ", "Hall", DEVICE_KEY, [" ceiling ", "l1", "Desk"], [" room:Hall ", "r1"])
    config = EngineConfig(HueSettings("bridge.test", app_key="qa-only-app-key"), [settings])
    config.validate_for_run()
    initial = [("l1", ceiling_on), ("l2", desk_on), ("g1", room_on)]
    states = dict(initial)
    writes: list[tuple[str, bool]] = []

    def respond(request: RecordedRequest) -> tuple[int, str]:
        if request.method == "GET":
            match request.path:
                case "/clip/v2/resource/light":
                    data: list[dict[str, Any]] = [
                        {"id": "l1", "metadata": {"name": "Ceiling"}, "on": {"on": states["l1"]}},
                        {"id": "l2", "metadata": {"name": "Desk"}, "on": {"on": states["l2"]}},
                    ]
                case "/clip/v2/resource/grouped_light":
                    data = [{"id": "g1", "on": {"on": states["g1"]}}]
                case "/clip/v2/resource/room":
                    data = [
                        {
                            "id": "r1",
                            "metadata": {"name": "Hall"},
                            "services": [{"rid": "g1", "rtype": "grouped_light"}],
                        }
                    ]
                case "/clip/v2/resource/zone":
                    data = []
                case _:
                    raise AssertionError(f"Unexpected GET {request.path}")
            return 200, json.dumps({"errors": [], "data": data})

        assert request.method == "PUT"
        resource = {
            "/clip/v2/resource/light/l1": "l1",
            "/clip/v2/resource/light/l2": "l2",
            "/clip/v2/resource/grouped_light/g1": "g1",
        }[request.path]
        states[resource] = request.on
        writes.append((resource, request.on))
        return 200, SUCCESS

    bridge = InMemoryBridge(respond)
    hue = bridge.client("qa-only-app-key")
    bindings = (await resolve_bindings(config, hue)).bindings
    assert len(bindings) == 1
    assert bindings[0].targets == [
        HueTarget(HueTargetKind.LIGHT, "l1", "Ceiling"),
        HueTarget(HueTargetKind.LIGHT, "l2", "Desk"),
        HueTarget(HueTargetKind.GROUPED_LIGHT, "g1", "Hall"),
    ]
    switch = Runner(config, bindings, hue, clock=lambda: NOW)

    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(101, ttl=0))
    switch.handle_packet(encrypted(500, device_id=OTHER_DEVICE_ID))
    switch.handle_packet(encrypted(501, device_id=OTHER_DEVICE_ID))
    switch.handle_packet(encrypted(101))
    await asyncio.wait_for(switch.when_toggles_complete(), 10)

    first_on = not any(on for _, on in initial)
    first_writes = [(resource, first_on) for resource, on in initial if on != first_on]
    assert writes == first_writes
    assert all(on == first_on for on in states.values())

    switch.handle_packet(encrypted(101))
    switch.handle_packet(encrypted(102, relay="off"))
    switch.handle_packet(encrypted(103, relay="off", led="off"))
    switch.handle_packet(encrypted(1, relay="off", led="off"))
    switch.handle_packet(to_packet(probe(DEVICE_ID, ADDRESS), ADDRESS, NOW))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    await asyncio.wait_for(switch.when_toggles_complete(), 10)
    assert writes == first_writes

    switch.handle_packet(encrypted(3, relay="off", led="off", rssi=-50))
    await asyncio.wait_for(switch.when_toggles_complete(), 10)

    assert writes == first_writes + [(resource, not first_on) for resource, _ in initial]
    assert all(on != first_on for on in states.values())
    assert all(request.headers["hue-application-key"] == "qa-only-app-key" for request in bridge.requests)


@pytest.mark.parametrize("reject_first_read", [False, True])
async def test_queued_presses_read_the_previous_toggle_state_and_recover_from_a_failed_read(
    reject_first_read: bool,
) -> None:
    on = False
    reads = 0
    steps: list[tuple[str, bool]] = []

    def respond(request: RecordedRequest) -> tuple[int, str]:
        nonlocal on, reads
        if request.method == "GET":
            assert request.path == "/clip/v2/resource/light"
            steps.append(("read", on))
            reads += 1
            if reads == 1 and reject_first_read:
                return 403, '{"errors":[{"description":"unauthorized user"}],"data":[]}'
            return 200, json.dumps({"errors": [], "data": [{"id": "l1", "on": {"on": on}}]})

        assert (request.method, request.path) == ("PUT", "/clip/v2/resource/light/l1")
        on = request.on
        steps.append(("write", on))
        return 200, SUCCESS

    bridge = InMemoryBridge(respond)
    binding = hall(DEVICE_KEY)
    switch = create_runner(bridge.client("qa-only-app-key"), binding=binding)

    await binding.gate.acquire()
    try:
        switch.handle_packet(encrypted(100))
        switch.handle_packet(encrypted(101))
        switch.handle_packet(encrypted(102))
        waiting = asyncio.create_task(switch.when_toggles_complete())
        await asyncio.sleep(0)
        assert bridge.requests == []
        assert not waiting.done()
    finally:
        binding.gate.release()
        await asyncio.wait_for(switch.when_toggles_complete(), 10)
    await waiting

    assert on == reject_first_read
    assert steps == (
        [("read", False), ("read", False), ("write", True)]
        if reject_first_read
        else [("read", False), ("write", True), ("read", True), ("write", False)]
    )


@pytest.mark.parametrize(("age", "dropped"), [(timedelta(milliseconds=5001), True), (timedelta(seconds=5), False)])
async def test_a_queued_press_just_beyond_five_seconds_is_dropped_and_releases_the_gate(
    age: timedelta, dropped: bool
) -> None:
    bridge = InMemoryBridge(lambda request: (200, LIGHT_OFF if request.method == "GET" else SUCCESS))
    observer = RecordingObserver()
    binding = hall(DEVICE_KEY)
    switch = create_runner(bridge.client("qa-only-app-key"), observer, binding)

    await binding.gate.acquire()
    try:
        switch.handle_packet(encrypted(100))
        switch.handle_packet(encrypted(101, received_at=NOW - age))
        switch.handle_packet(encrypted(102))
        await asyncio.sleep(0)
        assert bridge.requests == []
    finally:
        binding.gate.release()
        await asyncio.wait_for(switch.when_toggles_complete(), 10)

    toggled = "toggled Hall on changed=1 unchanged=0 failed=0"
    presses = ["press Hall seq=101", "press Hall seq=102"]
    if dropped:
        assert observer.events == [*presses, "dropped Hall", "bridge up", toggled]
        assert [request.method for request in bridge.requests] == ["GET", "PUT"]
    else:  # exactly 5 s isn't more than 5 s
        assert observer.events == [*presses, "bridge up", toggled, toggled]
        assert [request.method for request in bridge.requests] == ["GET", "PUT", "GET", "PUT"]
    assert all(request.path.startswith("/clip/v2/resource/light") for request in bridge.requests)
    assert bridge.requests[1].on is True


async def test_an_unexpected_error_while_toggling_is_logged_with_its_trace_and_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenToggler:
        def __init__(self, hue: HueClient, logger: logging.Logger) -> None:
            pass

        async def toggle(self, targets: list[HueTarget]) -> ToggleResult:
            raise RuntimeError("the toggle broke")

    monkeypatch.setattr(runner, "HueToggler", BrokenToggler)
    observer = KeyObserver()
    switch = create_runner(LightBridge().client(), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    # An unexpected error says nothing about the bridge: no reachability report and no rejected key.
    assert observer.events == ["press Hall seq=101", "toggle failed Hall: the toggle broke"]
    [record] = [
        r for r in caplog.records if r.getMessage() == "Hall (1000abcdef): unexpected error while toggling the lights."
    ]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError


async def test_a_keep_alive_that_fails_unexpectedly_reports_the_bridge_as_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_ping() -> None:
        raise RuntimeError("the keep-alive broke")

    hue = LightBridge().client()
    monkeypatch.setattr(hue, "ping", broken_ping)
    observer = KeyObserver()

    await create_runner(hue, observer).ping_bridge()

    assert observer.events == ["bridge down: the keep-alive broke"]


def test_a_device_id_mapped_twice_is_refused() -> None:
    first = SwitchBinding(SwitchSettings(DEVICE_ID, "Hall"), [CEILING])
    second = SwitchBinding(SwitchSettings(" 1000ABCDEF ", "Hall again"), [CEILING])
    config = EngineConfig(switches=[first.settings, second.settings])

    with pytest.raises(ValueError, match=r"^More than one switch has the device id 1000abcdef\.$"):
        Runner(config, [first, second], LightBridge().client())


# What the runner logs.


async def test_the_runner_logs_what_each_broadcast_means(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(mdns.TRACE, logger=runner.__name__)
    switch = create_runner(LightBridge().client(), binding=hall(DEVICE_KEY))

    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(101, relay="off"))
    switch.handle_packet(encrypted(102, relay="off", led="off"))
    switch.handle_packet(encrypted(1, relay="off", led="off"))
    switch.handle_packet(to_packet(probe(DEVICE_ID, ADDRESS), ADDRESS, NOW))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    switch.handle_packet(encrypted(3, relay="off", led="off", rssi=-50))
    await switch.when_toggles_complete()
    switch.handle_packet(plain(5, OTHER_DEVICE_ID))
    switch.handle_packet(plain(6, OTHER_DEVICE_ID))

    name = "Hall (1000abcdef)"
    assert runner_log(caplog) == [
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; relay0=on relaySeparation=1 rssi=-70)."),
        (mdns.TRACE, f"{name}: duplicate broadcast (seq 100)."),
        (logging.INFO, f"{name}: ignored, relay changed (relay 0 on -> off)."),
        (logging.INFO, f"{name}: ignored, settings changed (sledOnline)."),
        (logging.INFO, f"{name}: ignored, device restarted (seq went from 102 to 1)."),
        (logging.INFO, f"{name} (re)joined the network."),
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 2; relay0=off relaySeparation=1 rssi=-70)."),
        (logging.INFO, f"{name}: wall switch operated (seq 3)."),
        (logging.INFO, f"{name}: turned ON light 'Ceiling' in N ms."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at 192.0.2.5."),
    ]


def test_a_missing_seq_or_address_is_logged_as_none(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    switch = create_runner(LightBridge().client())

    # Without SRV and A records, a copy from this host gives no address.
    for device_id in (DEVICE_ID, OTHER_DEVICE_ID):
        copy = announcement(device_id, None, "payload", "iv", ip=None)
        switch.handle_packet(to_packet(copy, ADDRESS, NOW, from_this_host=True))

    assert runner_log(caplog) == [
        (logging.INFO, "Hall (1000abcdef): online at None (seq None; payload not decoded)."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at None."),
    ]


async def test_payload_problems_and_a_switch_that_is_not_detached_are_warned_about_when_they_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    switch = create_runner(LightBridge().client(), binding=hall(DEVICE_KEY))
    short_iv = announcement(DEVICE_ID, 100, "AAAA", base64.b64encode(bytes(15)).decode(), ip=ADDRESS)

    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(encrypted(100, separation=0))
    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(encrypted(100, separation=0))

    name = "Hall (1000abcdef)"
    unreadable = (
        logging.WARNING,
        f"{name}: cannot read the LAN payload (iv has 15 bytes instead of 16); falling back to sequence numbers only.",
    )
    assert runner_log(caplog) == [
        unreadable,
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; payload not decoded)."),
        (
            logging.WARNING,
            f"{name} is not in detached relay mode, so the wall switch cuts power to the lights. Enable 'Detach relay' "
            "in the eWeLink app.",
        ),
        unreadable,
    ]


async def test_toggles_log_their_outcome(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    status = 200

    def respond(request: RecordedRequest) -> tuple[int, str]:
        if status != 200:
            return status, "{}"
        if request.method == "GET":
            data = [
                {"id": "l1", "on": {"on": False}},
                {"id": "l2", "on": {"on": False}},
                {"id": "l3", "on": {"on": True}},
            ]
            return 200, json.dumps({"errors": [], "data": data})
        return 404, '{"errors":[{"description":"not found"}],"data":[]}'

    targets = [HueTarget(HueTargetKind.LIGHT, f"l{i}", name) for i, name in enumerate("ABC", 1)]
    binding = SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", None, ["A", "B", "C"]), targets)
    switch = create_runner(InMemoryBridge(respond).client(), binding=binding)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()
    status = 503
    switch.handle_packet(plain(102))
    await switch.when_toggles_complete()
    status = 200
    switch.handle_packet(plain(103))
    await switch.when_toggles_complete()
    switch.handle_packet(plain(104, received_at=NOW - timedelta(seconds=10)))
    await switch.when_toggles_complete()

    name = "Hall (1000abcdef)"
    not_switched = (
        logging.ERROR,
        "Could not switch light 'C' off: Hue bridge returned 404 Not Found for PUT /clip/v2/resource/light/l3: not found",
    )
    turned_off = (
        logging.INFO,
        f"{name}: turned OFF light 'A', light 'B', light 'C' in N ms (2 already off, 1 failed).",
    )
    assert runner_log(caplog) == [
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; payload not decoded)."),
        (logging.INFO, f"{name}: wall switch operated (seq 101)."),
        not_switched,
        turned_off,
        (logging.INFO, f"{name}: wall switch operated (seq 102)."),
        (logging.WARNING, f"Hue bridge unavailable: {BRIDGE_DOWN_503}"),
        (logging.ERROR, f"{name}: could not toggle the lights: {BRIDGE_DOWN_503}"),
        (logging.INFO, f"{name}: wall switch operated (seq 103)."),
        not_switched,
        (logging.INFO, "Hue bridge reachable again."),
        turned_off,
        (logging.INFO, f"{name}: wall switch operated (seq 104)."),
        (logging.WARNING, f"{name}: dropped a press that waited more than 5s for the bridge."),
    ]


# Resolving the names of the lights, rooms and zones.

LIGHTS = '{"data":[{"id":"l1","metadata":{"name":"Ceiling"}},{"id":"l2","metadata":{"name":"Desk"}}]}'
ROOMS = '{"data":[{"id":"r1","metadata":{"name":"Hall"},"services":[{"rid":"g1","rtype":"grouped_light"}]}]}'


async def test_resolving_waits_for_the_bridge_while_its_errors_are_transient(caplog: pytest.LogCaptureFixture) -> None:
    answers = iter([(503, "{}"), (200, LIGHTS), (502, ""), (200, LIGHTS), (200, ROOMS), (200, SUCCESS)])
    bridge = InMemoryBridge(lambda request: next(answers))
    config = EngineConfig(HueSettings("bridge.test"), [SwitchSettings(DEVICE_ID, "Hall", None, ["Desk"], ["Hall"])])
    logger = logging.getLogger("tests.engine.supervisor")

    bindings = (await resolve_bindings(config, bridge.client(), logger)).bindings

    assert [binding.targets for binding in bindings] == [
        [HueTarget(HueTargetKind.LIGHT, "l2", "Desk"), HueTarget(HueTargetKind.GROUPED_LIGHT, "g1", "Hall")]
    ]
    assert [f"{request.method} {request.path}" for request in bridge.requests] == [
        "GET /clip/v2/resource/light",
        "GET /clip/v2/resource/light",
        "GET /clip/v2/resource/room",
        "GET /clip/v2/resource/light",
        "GET /clip/v2/resource/room",
        "GET /clip/v2/resource/zone",
    ]
    assert [(r.name, r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logger.name,
            logging.WARNING,
            "Hue bridge at bridge.test not usable yet (Hue bridge returned 503 Service Unavailable for GET "
            "/clip/v2/resource/light); retrying in 10s.",
        ),
        (
            logger.name,
            logging.WARNING,
            "Hue bridge at bridge.test not usable yet (Hue bridge returned 502 Bad Gateway for GET "
            "/clip/v2/resource/room); retrying in 10s.",
        ),
    ]


async def test_resolving_keeps_each_switchs_problems_to_itself() -> None:
    """An entry that resolves to nothing is its switch's problem: the switch keeps the targets that resolve, and every
    other switch resolves as if it weren't there."""
    bridge = InMemoryBridge(lambda request: (200, {"light": LIGHTS, "room": ROOMS}.get(request.path[18:], SUCCESS)))
    config = EngineConfig(
        HueSettings("bridge.test"),
        [
            SwitchSettings(DEVICE_ID, "Hall", None, ["Ceiling", "Nope"]),
            SwitchSettings(OTHER_DEVICE_ID, None, None, [], ["room:Garage"]),
            SwitchSettings("1000555555", "Porch", None, ["Desk"], ["Hall"]),
        ],
    )

    bindings = (await resolve_bindings(config, bridge.client())).bindings

    assert [(binding.settings, binding.targets, binding.problems) for binding in bindings] == [
        (
            config.switches[0],
            [CEILING],
            ["Hall (1000abcdef): no Hue light named 'Nope'. Known lights: 'Ceiling', 'Desk'"],
        ),
        (config.switches[1], [], ["1000999999: no Hue room or zone named 'room:Garage'. Known: 'room:Hall'"]),
        (
            config.switches[2],
            [HueTarget(HueTargetKind.LIGHT, "l2", "Desk"), HueTarget(HueTargetKind.GROUPED_LIGHT, "g1", "Hall")],
            [],
        ),
    ]


def test_a_resolution_remembers_what_each_entry_of_the_configuration_found() -> None:
    """Decision 32: what each entry found now; for one that found nothing, what it found before; and nothing for the
    switches and entries that the configuration no longer has. A rename comes with the switch's entry."""
    lights = [HueLight("l1", "Ceiling lamp", False), HueLight("l2", "Desk", False)]
    groups = [HueGroup("r1", "Hall", "room", "g1")]
    hall = SwitchSettings(DEVICE_ID, "Hall", None, ["Ceiling", "Desk", "Porch"], ["Garden"])
    ceiling = EntryKey(DEVICE_ID, "lights", "ceiling")
    porch = EntryKey(DEVICE_ID, "lights", "porch")
    remembered = {
        ceiling: RememberedResource("light", "l1", "Ceiling"),
        porch: RememberedResource("light", "l7", "Porch"),
        EntryKey(DEVICE_ID, "lights", "attic"): RememberedResource("light", "l5", "Attic"),
        EntryKey(OTHER_DEVICE_ID, "groups", "hall"): RememberedResource("room", "r1", "Hall"),
    }
    config = EngineConfig(switches=[hall])

    resolved = runner.resolve_switches(config, lights, groups, remembered)

    lamp = RememberedResource("light", "l1", "Ceiling lamp")
    assert resolved.config is config
    assert resolved.remembered == {
        ceiling: lamp,
        EntryKey(DEVICE_ID, "lights", "desk"): RememberedResource("light", "l2", "Desk"),
        porch: RememberedResource("light", "l7", "Porch"),
    }
    assert resolved.renames == [Renamed(ceiling, "Ceiling", "Ceiling", lamp, "Ceiling lamp")]
    (binding,) = resolved.bindings
    assert [str(target) for target in binding.targets] == ["light 'Ceiling lamp'", "light 'Desk'"]
    assert binding.problems == [
        "Hall (1000abcdef): no Hue light named 'Porch'. Known lights: 'Ceiling lamp', 'Desk'",
        "Hall (1000abcdef): no Hue room or zone named 'Garden'. Known: 'room:Hall'",
    ]


async def test_resolving_gives_up_when_the_bridge_refuses() -> None:
    bridge = InMemoryBridge(lambda request: (403, '{"errors":[{"description":"unauthorized user"}]}'))
    config = EngineConfig(HueSettings("bridge.test"), [SwitchSettings(DEVICE_ID, "Hall", None, ["Desk"])])

    with pytest.raises(HueApiError) as err:
        await resolve_bindings(config, bridge.client())

    assert err.value.is_authentication_error
    assert len(bridge.requests) == 1


# The runner on a listener, which it shares with the LAN monitor (decision 8).


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeNetwork:
    monkeypatch.setattr(mdns, "NETWORK_CHANGE_DEBOUNCE", 0.01)
    monkeypatch.setattr(runner, "DISCOVERY_QUERY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(runner, "MISSING_SWITCH_QUERY_INTERVAL", 0.01)
    return FakeNetwork()


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class LoggingBridge(LightBridge):
    """A LightBridge that logs each request, so that tests can see it among the runner's log lines."""

    log = logging.getLogger("tests.engine.bridge")

    def _answer(self, request: RecordedRequest) -> tuple[int, str]:
        self.log.info("%s %s", request.method, request.path)
        return super()._answer(request)


def _broadcast(network: FakeNetwork, seq: int) -> None:
    network.deliver(announcement(DEVICE_ID, seq, f"payload{seq}", "iv", ip=ADDRESS), (ADDRESS, 5353))


async def test_run_asks_for_the_switches_keeps_the_bridge_warm_and_toggles_on_presses(
    network: FakeNetwork, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runner, "KEEP_WARM_INTERVAL", 0.01)
    caplog.set_level(logging.INFO, logger=runner.__name__)
    bridge = LightBridge()
    observer = RecordingObserver()
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    listening = asyncio.create_task(listener.run())
    transport = await network.next_open()
    switch = create_runner(bridge.client(), observer)
    running = asyncio.create_task(switch.run(listener))

    # Three queries at once, then one more whenever the interval passes with the switch unseen.
    await until(lambda: caplog.text.count("Not seen on the LAN yet: Hall (1000abcdef)") >= 2)
    await until(lambda: bridge.paths.count("GET /clip/v2/resource/bridge") >= 3)
    assert len(transport.sent) >= 5
    assert set(transport.sent) == {(QUERY, "192.0.2.1")}
    assert observer.events == ["bridge up"]
    assert runner_log(caplog)[:3] == [
        (logging.INFO, "Hall (1000abcdef) -> light 'Ceiling'"),
        (
            logging.INFO,
            "Hall (1000abcdef): no deviceKey configured, so every new state broadcast counts as a press (relay/setting "
            "changes cannot be filtered).",
        ),
        (logging.INFO, "Listening for wall switch presses."),
    ]

    _broadcast(network, 100)
    queries = len(transport.sent)
    _broadcast(network, 101)
    await switch.when_toggles_complete()
    await asyncio.sleep(0.05)

    assert len(transport.sent) == queries
    assert observer.events == ["bridge up", "press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]
    assert bridge.lights["l1"] is True

    await _stop(running)
    pings = len(bridge.requests)
    _broadcast(network, 102)
    network.network_changed()
    restarted = await network.next_open()
    await asyncio.sleep(0.05)

    assert not listening.done()
    assert listener.is_open
    assert restarted.sent == []
    assert len(bridge.requests) == pings
    assert observer.events == ["bridge up", "press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]
    await _stop(listening)


async def test_stopping_the_runner_ends_its_queries_and_its_keep_alive_while_the_listener_goes_on(
    network: FakeNetwork,
) -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    listening = asyncio.create_task(listener.run())
    transport = await network.next_open()
    running = asyncio.create_task(create_runner(bridge.client(), observer).run(listener))

    # The keep-alive goes out at once; the next would wait 45 s.
    await until(lambda: len(transport.sent) >= 5)
    assert observer.events == ["bridge up"]
    assert bridge.paths == ["GET /clip/v2/resource/bridge"]

    await _stop(running)
    sent = len(transport.sent)
    await asyncio.sleep(0.05)

    assert len(transport.sent) == sent
    assert not listening.done()
    await _stop(listening)


async def test_every_restart_of_the_socket_takes_a_new_baseline(network: FakeNetwork) -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    switch = create_runner(bridge.client(), observer)
    running = asyncio.create_task(switch.run(listener))  # before the socket is open: it asks once it is
    listening = asyncio.create_task(listener.run())
    first = await network.next_open()
    await until(lambda: len(first.sent) >= 3)
    _broadcast(network, 100)

    network.network_changed()
    second = await network.next_open()
    await until(lambda: len(second.sent) >= 3)
    _broadcast(network, 101)
    await switch.when_toggles_complete()
    assert observer.events == ["bridge up"]

    _broadcast(network, 102)
    await switch.when_toggles_complete()

    assert observer.events == ["bridge up", "press Hall seq=102", "toggled Hall on changed=1 unchanged=0 failed=0"]
    await _stop(running)
    await _stop(listening)


async def test_run_sends_its_first_ping_and_query_before_it_first_waits(
    network: FakeNetwork, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(runner, "DISCOVERY_QUERY_DELAYS", (0.0, 1.0, 2.0))
    caplog.set_level(logging.INFO)
    bridge = LoggingBridge()
    observer = RecordingObserver()
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    listening = asyncio.create_task(listener.run())
    transport = await network.next_open()
    switch = create_runner(bridge.client(), observer)

    running = asyncio.get_running_loop().create_task(switch.run(listener), eager_start=True)

    # Nothing awaited: run() went on until it first waited, the first ping and the first of three queries included.
    assert [r.getMessage() for r in caplog.records if r.name in (runner.__name__, LoggingBridge.log.name)] == [
        "Hall (1000abcdef) -> light 'Ceiling'",
        "Hall (1000abcdef): no deviceKey configured, so every new state broadcast counts as a press (relay/setting "
        "changes cannot be filtered).",
        "GET /clip/v2/resource/bridge",
        "Listening for wall switch presses.",
    ]
    assert observer.events == ["bridge up"]
    assert transport.sent == [(QUERY, "192.0.2.1")]
    await _stop(running)
    await _stop(listening)


async def test_run_names_what_each_switch_toggles(network: FakeNetwork, caplog: pytest.LogCaptureFixture) -> None:
    """Also a switch that has nothing left to toggle (decision 32)."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    nothing = SwitchBinding(SwitchSettings(OTHER_DEVICE_ID, None, "key", ["Porch"]), [], ["1000999999: no light"])
    bindings = [hall(), nothing]
    switch = Runner(EngineConfig(switches=[b.settings for b in bindings]), bindings, LightBridge().client())

    running = asyncio.get_running_loop().create_task(switch.run(listener), eager_start=True)

    assert runner_log(caplog)[:3] == [
        (logging.INFO, "Hall (1000abcdef) -> light 'Ceiling'"),
        (
            logging.INFO,
            "Hall (1000abcdef): no deviceKey configured, so every new state broadcast counts as a press (relay/setting "
            "changes cannot be filtered).",
        ),
        (logging.INFO, "1000999999 -> (none)"),
    ]
    await _stop(running)


async def test_a_broadcast_right_after_a_restart_of_the_socket_is_a_new_baseline(network: FakeNetwork) -> None:
    bridge = LightBridge()
    observer = RecordingObserver()
    listener = MdnsListener(network.open, network.watch, clock=lambda: NOW)
    listening = asyncio.create_task(listener.run())
    first = await network.next_open()
    switch = create_runner(bridge.client(), observer)
    running = asyncio.create_task(switch.run(listener))
    await until(lambda: len(first.sent) >= 3)  # the switches were asked, after the first socket's reset
    _broadcast(network, 100)

    network.network_changed()
    await network.next_open()
    _broadcast(network, 101)

    # The new socket's started callback, which starts eagerly, reset the baselines before its first packet.
    assert observer.events == ["bridge up"]
    assert bridge.paths == ["GET /clip/v2/resource/bridge"]
    await _stop(running)
    await _stop(listening)


# A rejected key, reported for Home Assistant's reauthentication (decision 21).


REJECTED = f"The Hue bridge rejected the application key; {PAIR_AGAIN}."


async def test_each_ping_that_the_bridge_refuses_the_key_reports_it(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=runner.__name__)
    bridge = LightBridge()
    observer = KeyObserver()
    switch = create_runner(bridge.client(), observer)

    for status in (200, 401, 401, 403, 503, 200):
        bridge.status = status
        await switch.ping_bridge()

    # After each refusal, right after the reachability report, which comes only on changes as before.
    assert observer.events == [
        "bridge up",
        f"bridge down: {REJECTED}",
        "key rejected",
        "key rejected",
        "key rejected",
        "bridge down: Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/bridge",
        "bridge up",
    ]
    assert runner_log(caplog) == [
        (logging.WARNING, f"Hue bridge unavailable: {REJECTED}"),
        (logging.INFO, "Hue bridge reachable again."),
    ]


async def test_each_toggle_that_the_bridge_refuses_the_key_reports_it(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=runner.__name__)
    bridge = LightBridge()
    observer = KeyObserver()
    switch = create_runner(bridge.client(), observer)
    switch.handle_packet(plain(100))
    caplog.clear()

    for seq, status in ((101, 401), (102, 403), (103, 503)):
        bridge.status = status
        switch.handle_packet(plain(seq))
        await switch.when_toggles_complete()

    bridge_down = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"
    assert observer.events == [
        "press Hall seq=101",
        f"bridge down: {REJECTED}",
        "key rejected",
        f"toggle failed Hall: {REJECTED}",
        "press Hall seq=102",
        "key rejected",
        f"toggle failed Hall: {REJECTED}",
        "press Hall seq=103",
        f"bridge down: {bridge_down}",
        f"toggle failed Hall: {bridge_down}",
    ]
    assert runner_log(caplog) == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 101)."),
        (logging.WARNING, f"Hue bridge unavailable: {REJECTED}"),
        (logging.ERROR, f"Hall (1000abcdef): could not toggle the lights: {REJECTED}"),
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 102)."),
        (logging.ERROR, f"Hall (1000abcdef): could not toggle the lights: {REJECTED}"),
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 103)."),
        (logging.ERROR, f"Hall (1000abcdef): could not toggle the lights: {bridge_down}"),
    ]
    assert bridge.lights["l1"] is False


async def test_a_refused_write_is_a_failed_target_without_a_rejected_key() -> None:
    bridge = LightBridge()
    observer = KeyObserver()
    switch = create_runner(bridge.client(), observer)

    def refuse_writes(request: RecordedRequest) -> tuple[int, str]:
        return (401, "{}") if request.method == "PUT" else LightBridge._answer(bridge, request)

    bridge._respond = refuse_writes
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    # The toggle read the states, so the bridge is up; the refused write is a failed target, as before.
    assert observer.events == ["press Hall seq=101", "bridge up", "toggled Hall on changed=0 unchanged=0 failed=1"]


async def test_the_rejected_key_goes_through_the_observer_guard(caplog: pytest.LogCaptureFixture) -> None:
    bridge = LightBridge()
    bridge.status = 403
    throwing = ThrowingObserver()

    await create_runner(bridge.client(), throwing).ping_bridge()
    await create_runner(bridge.client(), SilentObserver()).ping_bridge()

    assert throwing.calls == 2
    failures = [r for r in caplog.records if r.getMessage() == "Runner observer ThrowingObserver failed."]
    assert len(failures) == 2
