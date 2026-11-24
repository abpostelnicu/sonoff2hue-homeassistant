"""Tests of the bridge supervisor: the .NET app's ``BridgeSupervisor_FollowsTheConfiguration``, and every state,
message and retry of ``BridgeSupervisor``, on virtual time with an in-memory bridge and a fake network."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from datetime import timedelta
import json
import logging
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import supervisor
from custom_components.sonoff2hue.engine.config import EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.hue_client import HueClient
from custom_components.sonoff2hue.engine.mdns import MdnsListener
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.status import ServiceState, ServiceStatus, StatusTracker
from custom_components.sonoff2hue.engine.supervisor import BridgeSupervisor, HueClientFactory, create_hue_client

from ..hue import InMemoryBridge, RecordedRequest
from .fakes import FakeNetwork, VirtualTime, until
from .packets import announcement

DEVICE_ID = "1000abcdef"
ADDRESS = "192.0.2.5"
PAIRED = HueSettings("bridge.test", "001788fffe000001", "app-key", "AB" * 32)
CONNECTING = "Connecting to the Hue bridge at bridge.test."
RUNNING = "1 wall switch toggles 1 Hue light, room or zone."
PAIR_AGAIN = "reconfigure the sonoff2hue integration to pair the bridge again"
KEY_REJECTED = (
    "The Hue bridge no longer accepts sonoff2hue's application key. Reconfigure the sonoff2hue integration to pair the "
    "bridge again."
)
BRIDGE_DOWN = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/bridge"
NO_ERRORS = '{"errors":[],"data":[]}'
State = ServiceState


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


class Bridge(InMemoryBridge):
    """A Hue bridge with the light Ceiling (l1), which records when each request came, and how many clients were made
    for it and closed."""

    def __init__(self, time: VirtualTime) -> None:
        super().__init__(self._answer)
        self._time = time
        self._faults: list[list[Any]] = []
        self.on = False
        self.hang = False
        self.times: list[tuple[float, str]] = []
        self.created = 0
        self.closed = 0

    def fail(self, path: str, status: int, body: str = "{}", count: int | None = None) -> None:
        """Answers the next ``count`` requests for ``path``, or all of them, with ``status`` and ``body``."""
        self._faults.append([path, status, body, count])

    def requested(self, path: str) -> list[float]:
        """When ``GET path`` came, in seconds of virtual time."""
        return [seconds for seconds, request in self.times if request == f"GET {path}"]

    def clients(self) -> HueClientFactory:
        """The .NET test's ``FakeFactory``: clients for the configured address and key, which reach this bridge."""

        def create(hue: HueSettings) -> HueClient:
            self.created += 1
            return HueClient(hue.bridge_ip or "", hue.app_key or "", hue.certificate_sha256, self._transport)

        return create

    async def close(self) -> None:
        self.closed += 1

    def _answer(self, request: RecordedRequest) -> tuple[int, str] | Awaitable[tuple[int, str]]:
        self.times.append((self._time.elapsed, f"{request.method} {request.path}"))
        if self.hang:
            return asyncio.get_running_loop().create_future()
        for fault in self._faults:
            path, status, body, count = fault
            if path == request.path and count != 0:
                fault[3] = None if count is None else count - 1
                return status, body
        if request.method == "PUT" and request.path == "/clip/v2/resource/light/l1":
            self.on = request.on
            return 200, NO_ERRORS
        data: list[dict[str, Any]] = {
            "/clip/v2/resource/bridge": [{"id": "b1", "type": "bridge"}],
            "/clip/v2/resource/light": [{"id": "l1", "metadata": {"name": "Ceiling"}, "on": {"on": self.on}}],
            "/clip/v2/resource/room": [],
            "/clip/v2/resource/zone": [],
        }[request.path]
        return 200, json.dumps({"errors": [], "data": data})


class Status(StatusTracker):
    """The tracker, recording every change of the service's state."""

    def __init__(self, config: EngineConfig, registry: DeviceRegistry, time: VirtualTime) -> None:
        super().__init__(config, registry, time.utc)
        self.changes: list[ServiceStatus] = []

    def set_service(self, state: ServiceState, message: str | None, problems: Any = None) -> None:
        before = self.service
        super().set_service(state, message, problems)
        if self.service is not before:
            self.changes.append(self.service)

    @property
    def history(self) -> list[tuple[float, ServiceState, str | None]]:
        """Each change: when, in seconds of virtual time, the state and the message."""
        return [
            ((change.since - VirtualTime.START).total_seconds(), change.state, change.message)
            for change in self.changes
        ]


class Harness:
    """A supervisor on virtual time, with the config entry's listener on a fake network, as the LAN monitor runs it."""

    def __init__(
        self,
        time: VirtualTime,
        config: EngineConfig,
        hue_clients: HueClientFactory | None = None,
        on_key_rejected: Callable[[], None] | None = None,
    ) -> None:
        self.time = time
        self.bridge = Bridge(time)
        self.network = FakeNetwork()
        self.listener = MdnsListener(self.network.open, self.network.watch, clock=time.utc)
        self.registry = DeviceRegistry(time.utc)
        self.status = Status(config, self.registry, time)
        self.supervisor = BridgeSupervisor(
            config,
            self.listener,
            self.status,
            hue_clients=hue_clients or self.bridge.clients(),
            on_key_rejected=on_key_rejected,
            clock=time.utc,
        )
        self.listening: asyncio.Task[None] | None = None
        self.running: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.listening = asyncio.create_task(self.listener.run())
        await self.network.next_open()
        self.running = asyncio.create_task(self.supervisor.run())
        await asyncio.sleep(0)

    async def stop_supervisor(self) -> None:
        assert self.running is not None
        await _cancel(self.running)

    async def stop(self) -> None:
        for task in (self.running, self.listening):
            if task is not None:
                await _cancel(task)
        self.status.close()

    def broadcast(self, seq: int) -> None:
        self.network.deliver(announcement(DEVICE_ID, seq, f"payload{seq}", "iv", ip=ADDRESS), (ADDRESS, 5353))


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
async def start(time: VirtualTime) -> AsyncIterator[Callable[..., Awaitable[Harness]]]:
    """Starts a supervisor for a configuration; it stops after the test."""
    started: list[Harness] = []

    async def make(config: EngineConfig, **kwargs: Any) -> Harness:
        harness = Harness(time, config, **kwargs)
        started.append(harness)
        await harness.start()
        return harness

    yield make
    for harness in started:
        await harness.stop()


def hall(*lights: str, groups: tuple[str, ...] = ()) -> SwitchSettings:
    return SwitchSettings(DEVICE_ID, "Hall", None, list(lights or ["Ceiling"]), list(groups))


def supervisor_log(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == supervisor.__name__]


# The .NET app's BridgeSupervisor test.


async def test_follows_the_configuration(time: VirtualTime, caplog: pytest.LogCaptureFixture) -> None:
    """Every change of the configuration reloads the config entry: the supervisor is cancelled, and a new one runs the
    new configuration. The tracker is kept here, as in the .NET test."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    bridge = Bridge(time)
    network = FakeNetwork()
    listener = MdnsListener(network.open, network.watch, clock=time.utc)
    listening = asyncio.create_task(listener.run())
    await network.next_open()
    status = StatusTracker(EngineConfig(), DeviceRegistry(time.utc), time.utc)
    current: BridgeSupervisor | None = None
    running: asyncio.Task[None] | None = None

    async def reload(config: EngineConfig) -> None:
        nonlocal current, running
        if current is not None and running is not None:
            current.config_changed()
            await _cancel(running)
        status.config_changed(config)
        current = BridgeSupervisor(config, listener, status, hue_clients=bridge.clients(), clock=time.utc)
        running = asyncio.create_task(current.run())

    await reload(EngineConfig())
    await until(lambda: status.service.state == State.NOT_CONFIGURED)
    assert status.service.message is not None
    assert "Pair" in status.service.message

    await reload(
        EngineConfig(HueSettings("bridge.test", app_key="key"), [SwitchSettings(DEVICE_ID, "Hall", None, ["Kitchen"])])
    )
    await until(lambda: status.service.state == State.INVALID_CONFIG)
    assert any("no Hue light named 'Kitchen'" in problem for problem in status.service.problems)

    await reload(
        EngineConfig(HueSettings("bridge.test", app_key="key"), [SwitchSettings(DEVICE_ID, "Hall", None, ["Ceiling"])])
    )
    await until(lambda: status.service.state == State.RUNNING)
    assert status.snapshot().hue.reachable is True

    assert running is not None
    await _cancel(running)
    await _cancel(listening)
    status.close()
    assert [message for _, message in supervisor_log(caplog)].count(
        "Configuration changed; restarting the bridge."
    ) == 2
    assert bridge.closed == bridge.created == 2


# The states and their messages, in the order of the checks.


@pytest.mark.parametrize("with_switch", [False, True])
@pytest.mark.parametrize(
    "hue",
    [
        HueSettings(),
        HueSettings("bridge.test"),
        HueSettings(app_key="app-key"),
        HueSettings(" ", app_key="app-key"),
        HueSettings("bridge.test", app_key="\u3000"),
    ],
)
async def test_an_unpaired_bridge_asks_to_pair_it_first(
    start: Callable[..., Awaitable[Harness]], hue: HueSettings, with_switch: bool
) -> None:
    harness = await start(EngineConfig(hue, [hall()] if with_switch else []))
    await asyncio.sleep(60)

    assert harness.status.history == [
        (0, State.NOT_CONFIGURED, "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration.")
    ]
    assert (harness.bridge.created, harness.bridge.requests) == (0, [])


async def test_a_paired_bridge_without_switches_asks_for_one(start: Callable[..., Awaitable[Harness]]) -> None:
    harness = await start(EngineConfig(PAIRED))
    await asyncio.sleep(60)

    assert harness.status.history == [
        (0, State.NOT_CONFIGURED, "Add a switch: pick a MINIR4M and the Hue lights it should toggle.")
    ]
    assert harness.status.service.problems == ()
    assert (harness.bridge.created, harness.bridge.requests) == (0, [])


async def test_configuration_errors_are_listed_one_by_one(start: Callable[..., Awaitable[Harness]]) -> None:
    switches = [
        SwitchSettings(DEVICE_ID, "Hall", None, []),
        SwitchSettings(DEVICE_ID.upper(), None, None, ["Ceiling", " "]),
    ]
    harness = await start(EngineConfig(PAIRED, switches))
    await asyncio.sleep(60)

    assert harness.status.history == [(0, State.INVALID_CONFIG, "The configuration has errors.")]
    assert harness.status.service.problems == (
        "switches[0] ('Hall'): add at least one entry to 'lights' or 'groups'.",
        "switches[1]: deviceId '1000ABCDEF' is configured more than once; put all its lights in one entry.",
        "switches[1]: empty light/group name.",
    )
    assert (harness.bridge.created, harness.bridge.requests) == (0, [])


@pytest.mark.parametrize(
    ("hue", "problem"),
    [
        (
            HueSettings("not a host", app_key="app-key"),
            "'not a host' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, "
            "for example 192.168.1.20 or bridge.local:8443.",
        ),
        (
            HueSettings("bridge.test", app_key="app-key\n"),
            f"The Hue application key contains characters that can't be sent to the bridge; {PAIR_AGAIN}.",
        ),
    ],
)
async def test_hue_settings_that_cannot_be_used_are_named(
    start: Callable[..., Awaitable[Harness]], hue: HueSettings, problem: str
) -> None:
    harness = await start(EngineConfig(hue, [hall()]), hue_clients=create_hue_client)
    await asyncio.sleep(60)

    assert harness.status.history == [(0, State.INVALID_CONFIG, "The Hue settings in the config can't be used.")]
    assert harness.status.service.problems == (problem,)


async def test_lights_rooms_and_zones_that_are_not_on_the_bridge_are_listed(
    start: Callable[..., Awaitable[Harness]],
) -> None:
    harness = await start(EngineConfig(PAIRED, [hall("Ceiling", "Kitchen", groups=("room:Garage",))]))
    await asyncio.sleep(60)

    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.INVALID_CONFIG, "Some lights, rooms or zones were not found on the Hue bridge."),
    ]
    assert harness.status.service.problems == (
        "Hall (1000abcdef): no Hue light named 'Kitchen'. Known lights: 'Ceiling'",
        "Hall (1000abcdef): no Hue room or zone named 'room:Garage'. Known: (none)",
    )
    assert harness.status.snapshot().hue.reachable is True
    assert harness.bridge.closed == 0  # the client is closed only when the supervisor stops


async def test_the_bridge_is_tried_every_10_seconds_until_it_answers(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    bridge_down_502 = BRIDGE_DOWN.replace("503 Service Unavailable", "502 Bad Gateway")
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.fail("/clip/v2/resource/bridge", 503, count=2)
    harness.bridge.fail("/clip/v2/resource/bridge", 502, count=1)
    await harness.start()

    await asyncio.sleep(25)
    waiting = harness.status.snapshot()
    await asyncio.sleep(10)

    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.CONNECTING, f"Waiting for the Hue bridge at bridge.test: {BRIDGE_DOWN}"),
        (20, State.CONNECTING, f"Waiting for the Hue bridge at bridge.test: {bridge_down_502}"),
        (30, State.RUNNING, RUNNING),
    ]
    assert (waiting.hue.reachable, waiting.hue.message, waiting.hue.checked_at) == (
        False,
        bridge_down_502,
        time.START + timedelta(seconds=20),
    )
    assert harness.status.snapshot().hue.reachable is True
    # The pings at 0, 10, 20 and 30 s, then the runner's first keep-alive.
    assert harness.bridge.requested("/clip/v2/resource/bridge") == pytest.approx([0, 10, 20, 30, 30])
    assert supervisor_log(caplog) == [
        (logging.WARNING, f"Hue bridge at bridge.test not reachable ({BRIDGE_DOWN}); retrying every 10 s."),
    ]
    await harness.stop()


async def test_resolving_waits_while_the_bridge_is_not_usable_yet(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.fail("/clip/v2/resource/light", 503, count=2)
    await harness.start()
    await asyncio.sleep(30)

    # The supervisor's logger is the one that says so, as .NET passes its own logger.
    not_usable = (
        "Hue bridge at bridge.test not usable yet (Hue bridge returned 503 Service Unavailable for GET "
        "/clip/v2/resource/light); retrying in 10s."
    )
    assert supervisor_log(caplog) == [(logging.WARNING, not_usable), (logging.WARNING, not_usable)]
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (20, State.RUNNING, RUNNING)]
    assert harness.bridge.requested("/clip/v2/resource/light") == pytest.approx([0, 10, 20])
    await harness.stop()


@pytest.mark.parametrize(
    ("path", "status", "body", "message"),
    [
        (
            "/clip/v2/resource/bridge",
            403,
            '{"errors":[{"description":"unauthorized user"}]}',
            f"The Hue bridge rejected the application key (unauthorized user); {PAIR_AGAIN}.",
        ),
        ("/clip/v2/resource/light", 401, "{}", f"The Hue bridge rejected the application key; {PAIR_AGAIN}."),
    ],
)
async def test_a_rejected_key_asks_to_pair_again_and_calls_for_reauthentication(
    time: VirtualTime, path: str, status: int, body: str, message: str
) -> None:
    rejected: list[float] = []
    harness = Harness(time, EngineConfig(PAIRED, [hall()]), on_key_rejected=lambda: rejected.append(time.elapsed))
    harness.bridge.fail(path, status, body)
    await harness.start()
    requests = len(harness.bridge.requests)
    await asyncio.sleep(100)

    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.ERROR, KEY_REJECTED)]
    hue = harness.status.snapshot().hue
    assert (hue.reachable, hue.message) == (False, message)
    assert rejected == [0]
    assert len(harness.bridge.requests) == requests
    await harness.stop()


@pytest.mark.parametrize(
    ("path", "status", "body", "message"),
    [
        ("/clip/v2/resource/light", 404, "{}", "Hue bridge returned 404 Not Found for GET /clip/v2/resource/light"),
        (
            "/clip/v2/resource/bridge",
            207,
            '{"errors":[{"description":"device (light) has communication issues, command (.on.on) may not have effect"}]}',
            "The Hue bridge reported errors for GET /clip/v2/resource/bridge: device (light) has communication issues, "
            "command (.on.on) may not have effect",
        ),
    ],
)
async def test_other_refusals_show_the_bridges_message(
    time: VirtualTime, path: str, status: int, body: str, message: str
) -> None:
    rejected: list[float] = []
    harness = Harness(time, EngineConfig(PAIRED, [hall()]), on_key_rejected=lambda: rejected.append(time.elapsed))
    harness.bridge.fail(path, status, body)
    await harness.start()
    await asyncio.sleep(100)

    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.ERROR, message)]
    assert (harness.status.snapshot().hue.reachable, harness.status.snapshot().hue.message) == (False, message)
    assert rejected == []
    await harness.stop()


async def test_a_key_rejected_callback_that_raises_does_not_stop_the_supervisor(time: VirtualTime) -> None:
    errors: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))

    def reauthenticate() -> None:
        raise RuntimeError("reauth failed")

    harness = Harness(time, EngineConfig(PAIRED, [hall()]), on_key_rejected=reauthenticate)
    harness.bridge.fail("/clip/v2/resource/bridge", 403)
    await harness.start()
    await asyncio.sleep(100)

    assert [str(context["exception"]) for context in errors] == ["reauth failed"]
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.ERROR, KEY_REJECTED)]
    assert harness.running is not None
    assert not harness.running.done()
    await harness.stop()


@pytest.mark.parametrize(("pin", "warned"), [(None, True), (" ", True), ("AB" * 32, False)])
async def test_an_unpinned_certificate_is_warned_about(
    start: Callable[..., Awaitable[Harness]], caplog: pytest.LogCaptureFixture, pin: str | None, warned: bool
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = await start(EngineConfig(HueSettings("bridge.test", app_key="app-key", certificate_sha256=pin), [hall()]))
    await asyncio.sleep(1)

    assert harness.status.service.state == State.RUNNING
    assert supervisor_log(caplog) == (
        [
            (
                logging.WARNING,
                "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the "
                "sonoff2hue integration to pair the bridge again and pin it.",
            )
        ]
        if warned
        else []
    )


async def test_running_names_the_switches_and_their_targets(start: Callable[..., Awaitable[Harness]]) -> None:
    switches = [hall("Ceiling"), SwitchSettings("1000999999", None, None, ["Ceiling", "l1"])]
    harness = await start(EngineConfig(PAIRED, switches))
    await asyncio.sleep(1)

    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.RUNNING, "2 wall switches toggle 2 Hue lights, rooms and zones."),
    ]
    assert [switch.targets for switch in harness.status.snapshot().switches] == [
        ("light 'Ceiling'",),
        ("light 'Ceiling'",),
    ]


async def test_presses_toggle_the_lights_and_reach_the_tracker(start: Callable[..., Awaitable[Harness]]) -> None:
    harness = await start(EngineConfig(PAIRED, [hall()]))
    await asyncio.sleep(1)

    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.bridge.on is True
    (switch,) = harness.status.snapshot().switches
    assert switch.last_action is not None
    assert (switch.last_action.turned_on, switch.last_action.changed, switch.last_action.failed) == (True, 1, 0)
    assert switch.last_error is None


# Failures.


async def test_an_unexpected_stop_starts_the_bridge_again_30_seconds_later(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.fail("/clip/v2/resource/bridge", 200, '{"errors":[null],"data":[]}', count=1)
    await harness.start()
    await asyncio.sleep(29)
    stopped = (harness.bridge.created, harness.bridge.closed)
    await asyncio.sleep(2)

    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.ERROR, "The bridge stopped unexpectedly: Object reference not set to an instance of an object."),
        (30, State.CONNECTING, CONNECTING),
        (30, State.RUNNING, RUNNING),
    ]
    (record,) = [record for record in caplog.records if record.name == supervisor.__name__]
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        "The bridge stopped unexpectedly; restarting it in 30 s.",
    )
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], UnexpectedError)
    assert stopped == (1, 1)
    assert (harness.bridge.created, harness.bridge.closed) == (2, 1)
    assert harness.bridge.requested("/clip/v2/resource/bridge") == pytest.approx([0, 30, 30])
    await harness.stop()


async def test_a_runner_that_returns_is_started_again_30_seconds_later(
    start: Callable[..., Awaitable[Harness]], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class ReturningRunner:
        def __init__(self, *args: Any) -> None:
            pass

        async def run(self, listener: MdnsListener) -> None:
            pass

    monkeypatch.setattr(supervisor, "Runner", ReturningRunner)
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = await start(EngineConfig(PAIRED, [hall()]))
    await asyncio.sleep(45)

    # As in .NET, without a word: the runner only returns when cancelled.
    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.RUNNING, RUNNING),
        (30, State.CONNECTING, CONNECTING),
        (30, State.RUNNING, RUNNING),
    ]
    assert (harness.bridge.created, harness.bridge.closed) == (2, 2)
    assert supervisor_log(caplog) == []


# Stopping, which a reload of the config entry does.


async def test_stopping_while_waiting_for_the_bridge_closes_its_client(time: VirtualTime) -> None:
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.fail("/clip/v2/resource/bridge", 503)
    await harness.start()
    await asyncio.sleep(5)

    await harness.stop_supervisor()
    await asyncio.sleep(60)

    assert harness.bridge.requested("/clip/v2/resource/bridge") == [0]
    assert (harness.bridge.created, harness.bridge.closed) == (1, 1)
    assert harness.status.service.state == State.CONNECTING
    assert harness.listening is not None
    assert not harness.listening.done()
    await harness.stop()


async def test_stopping_during_a_request_closes_the_client(time: VirtualTime) -> None:
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.hang = True
    await harness.start()
    await asyncio.sleep(3)

    await harness.stop_supervisor()

    assert harness.bridge.requested("/clip/v2/resource/bridge") == [0]
    assert (harness.bridge.created, harness.bridge.closed) == (1, 1)
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING)]
    await harness.stop()


async def test_stopping_while_running_stops_the_runner_and_leaves_the_listener_running(
    start: Callable[..., Awaitable[Harness]],
) -> None:
    harness = await start(EngineConfig(PAIRED, [hall()]))
    await asyncio.sleep(5)
    harness.broadcast(1)
    pings = len(harness.bridge.requested("/clip/v2/resource/bridge"))

    await harness.stop_supervisor()
    harness.broadcast(2)
    harness.network.network_changed()
    restarted = await harness.network.next_open()
    await asyncio.sleep(100)

    assert harness.bridge.on is False
    assert len(harness.bridge.requested("/clip/v2/resource/bridge")) == pings
    assert (harness.bridge.created, harness.bridge.closed) == (1, 1)
    assert restarted.sent == []  # the runner's started callback, which queries for the switches, is gone
    assert harness.listener.is_open
    assert harness.listening is not None
    assert not harness.listening.done()


# The log line of a restart for a new configuration.


async def test_config_changed_logs_the_restart_as_dotnet_did(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    restart = (logging.INFO, "Configuration changed; restarting the bridge.")
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    harness.bridge.fail("/clip/v2/resource/bridge", 200, '{"errors":[null],"data":[]}', count=1)

    # Not before it runs.
    harness.supervisor.config_changed()
    assert supervisor_log(caplog) == []

    # Not while it waits to start again after an unexpected stop, as .NET swallowed that cancellation.
    await harness.start()
    await asyncio.sleep(10)
    caplog.clear()
    harness.supervisor.config_changed()
    assert supervisor_log(caplog) == []

    # While it runs.
    await asyncio.sleep(30)
    assert harness.status.service.state == State.RUNNING
    harness.supervisor.config_changed()
    assert supervisor_log(caplog) == [restart]

    # Not once it stopped.
    caplog.clear()
    await harness.stop_supervisor()
    harness.supervisor.config_changed()
    assert supervisor_log(caplog) == []
    await harness.stop()


async def test_config_changed_logs_while_the_supervisor_waits_for_a_change(
    start: Callable[..., Awaitable[Harness]], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = await start(EngineConfig(PAIRED))
    await asyncio.sleep(1)

    harness.supervisor.config_changed()

    assert supervisor_log(caplog) == [(logging.INFO, "Configuration changed; restarting the bridge.")]
