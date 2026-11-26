"""Tests of the bridge supervisor: how it follows the configuration, and every state, message and retry of
``BridgeSupervisor``, on virtual time with an in-memory bridge and a fake network."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from datetime import timedelta
import inspect
import itertools
import json
import logging
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import supervisor
from custom_components.sonoff2hue.engine.config import EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueBridgeInfo, HueClient, TransportError
from custom_components.sonoff2hue.engine.hue_setup import DiscoveredBridge
from custom_components.sonoff2hue.engine.mdns import MdnsListener
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.runner import ResolvedSwitches, RunnerObserver, SwitchBinding
from custom_components.sonoff2hue.engine.status import ServiceState, ServiceStatus, StatusSnapshot, StatusTracker
from custom_components.sonoff2hue.engine.supervisor import (
    BridgeMoved,
    BridgeSearch,
    BridgeSupervisor,
    HueClientFactory,
    create_hue_client,
)
from custom_components.sonoff2hue.engine.targets import EntryKey, Remembered, RememberedResource, Renamed
from custom_components.sonoff2hue.engine.toggler import ToggleResult

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
    """A Hue bridge with the light Ceiling (l1), whose name a test may change and which it may delete (``gone``), which
    records when each request came, and how many clients were made for it and closed. While ``unreachable`` is set,
    no request gets an answer: it fails as that transport error."""

    def __init__(self, time: VirtualTime) -> None:
        super().__init__(self._answer)
        self._time = time
        self._faults: list[list[Any]] = []
        self.on = False
        self.name = "Ceiling"
        self.gone = False
        self.hang = False
        self.unreachable: TransportError | None = None
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
        """A client factory: clients for the configured address and key, which reach this bridge."""

        def create(hue: HueSettings) -> HueClient:
            self.created += 1
            return HueClient(hue.bridge_ip or "", hue.app_key or "", hue.certificate_sha256, self._transport)

        return create

    async def close(self) -> None:
        self.closed += 1

    def _answer(self, request: RecordedRequest) -> tuple[int, str] | Awaitable[tuple[int, str]]:
        self.times.append((self._time.elapsed, f"{request.method} {request.path}"))
        if self.unreachable is not None:
            raise self.unreachable
        if self.hang:
            return asyncio.get_running_loop().create_future()
        for fault in self._faults:
            path, status, body, count = fault
            if path == request.path and count != 0:
                fault[3] = None if count is None else count - 1
                return status, body
        if request.method == "PUT" and request.path == "/clip/v2/resource/light/l1":
            if self.gone:
                return 404, '{"errors":[{"description":"not found"}],"data":[]}'
            self.on = request.on
            return 200, NO_ERRORS
        light = {"id": "l1", "metadata": {"name": self.name}, "on": {"on": self.on}}
        data: list[dict[str, Any]] = {
            "/clip/v2/resource/bridge": [{"id": "b1", "type": "bridge"}],
            "/clip/v2/resource/light": [] if self.gone else [light],
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
        observer: RunnerObserver | None = None,
        remembered: Remembered | None = None,
        on_resolved: Callable[[ResolvedSwitches], None] | None = None,
        search: BridgeSearch | None = None,
        on_bridge_moved: Callable[[BridgeMoved], None] | None = None,
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
            observer=observer,
            remembered=remembered,
            on_resolved=on_resolved,
            search=search,
            on_bridge_moved=on_bridge_moved,
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


# Following the configuration.


async def test_follows_the_configuration(time: VirtualTime, caplog: pytest.LogCaptureFixture) -> None:
    """For every change of the configuration, the supervisor is cancelled and a new one runs the new configuration,
    with the same tracker, as the runtime does it (decision 23)."""
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
    await until(lambda: status.service.state == State.RUNNING)
    assert any("no Hue light named 'Kitchen'" in problem for problem in status.service.problems)

    await reload(
        EngineConfig(HueSettings("bridge.test", app_key="key"), [SwitchSettings(DEVICE_ID, "Hall", None, ["Ceiling"])])
    )
    await until(lambda: status.service.state == State.RUNNING and status.service.problems == ())
    assert status.snapshot().switches[0].targets == ("light 'Ceiling'",)
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


async def test_lights_rooms_and_zones_not_on_the_bridge_are_their_switchs_problems_and_the_rest_runs(
    start: Callable[..., Awaitable[Harness]], caplog: pytest.LogCaptureFixture
) -> None:
    """Decision 32: the switch toggles what resolves, another switch runs as if nothing were missing, and each problem
    is warned about."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    porch = SwitchSettings("1000999999", "Porch", None, ["Ceiling"])
    harness = await start(EngineConfig(PAIRED, [hall("Ceiling", "Kitchen", groups=("room:Garage",)), porch]))
    await asyncio.sleep(1)

    kitchen = "Hall (1000abcdef): no Hue light named 'Kitchen'. Known lights: 'Ceiling'"
    garage = "Hall (1000abcdef): no Hue room or zone named 'room:Garage'. Known: (none)"
    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.RUNNING, "2 wall switches toggle 2 Hue lights, rooms and zones; 2 are missing."),
    ]
    assert harness.status.service.problems == (kitchen, garage)
    assert [(s.targets, s.problems) for s in harness.status.snapshot().switches] == [
        (("light 'Ceiling'",), (kitchen, garage)),
        (("light 'Ceiling'",), ()),
    ]
    assert supervisor_log(caplog) == [(logging.WARNING, kitchen), (logging.WARNING, garage)]

    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.bridge.on is True


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

    # The supervisor's logger is the one that says so: the supervisor passes its own to resolve_bindings.
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


async def test_a_key_rejected_while_running_calls_the_hook_after_each_refusal(time: VirtualTime) -> None:
    rejected: list[float] = []
    harness = Harness(time, EngineConfig(PAIRED, [hall()]), on_key_rejected=lambda: rejected.append(time.elapsed))
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)

    # The keep-alive requests at 45 and 90 s are refused, the one at 135 s fails otherwise; then a toggle is refused.
    harness.bridge.fail("/clip/v2/resource/bridge", 403, '{"errors":[{"description":"unauthorized user"}]}', count=2)
    harness.bridge.fail("/clip/v2/resource/bridge", 503, count=1)
    await asyncio.sleep(140)
    harness.bridge.fail("/clip/v2/resource/light", 401)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert rejected == pytest.approx([45, 90, 141])
    # The bridge goes on running: no restart, no other state, the same reports.
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.RUNNING, RUNNING)]
    # The supervisor's ping and the runner's first keep-alive at 0 s, then every 45 s.
    assert harness.bridge.requested("/clip/v2/resource/bridge") == pytest.approx([0, 0, 45, 90, 135])
    assert (harness.bridge.created, harness.bridge.closed) == (1, 0)
    snapshot = harness.status.snapshot()
    rejected_key = f"The Hue bridge rejected the application key; {PAIR_AGAIN}."
    assert (snapshot.hue.reachable, snapshot.hue.message) == (False, rejected_key)
    assert snapshot.switches[0].last_error is not None
    assert snapshot.switches[0].last_error.message == rejected_key
    await harness.stop()


@pytest.mark.parametrize("with_observer", [False, True])
def test_the_runners_observer_passes_every_report_to_the_tracker(with_observer: bool) -> None:
    """Each of the runner's reports reaches the tracker with its arguments, then the supervisor's observer if it has
    one, and a rejected key reaches the hook too."""
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    class Recorder:
        def __init__(self, who: str) -> None:
            self._who = who

        def __getattr__(self, name: str) -> Callable[..., None]:
            return lambda *args: calls.append((self._who, name, args))

    hooked: list[bool] = []
    observer = supervisor._Observer(
        Recorder("tracker"),  # type: ignore[arg-type]
        lambda: hooked.append(True),
        Recorder("observer") if with_observer else None,  # type: ignore[arg-type]
    )
    expected: list[tuple[str, str, tuple[object, ...]]] = []
    for name, method in vars(RunnerObserver).items():
        if callable(method) and not name.startswith("_"):
            args = tuple(object() for _ in list(inspect.signature(method).parameters)[1:])
            getattr(observer, name)(*args)
            expected.append(("tracker", name, args))
            if with_observer:
                expected.append(("observer", name, args))

    assert calls == expected
    assert [name for _, name, _ in expected][-1] == "on_key_rejected"
    assert hooked == [True]


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
    reports: list[tuple[str, object]] = []

    class Observer(RunnerObserver):
        """Records what the runner reports, and what the tracker knew by then."""

        status: StatusTracker

        def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
            reports.append(("press", seq))

        def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
            (switch,) = self.status.snapshot().switches
            reports.append(("toggled", switch.last_action is not None and switch.last_action.turned_on))

    observer = Observer()
    harness = await start(EngineConfig(PAIRED, [hall()]), observer=observer)
    observer.status = harness.status
    await asyncio.sleep(1)

    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.bridge.on is True
    (switch,) = harness.status.snapshot().switches
    assert switch.last_action is not None
    assert (switch.last_action.turned_on, switch.last_action.changed, switch.last_action.failed) == (True, 1, 0)
    assert switch.last_error is None
    # The supervisor's observer got the runner's reports, each after the tracker.
    assert reports == [("press", 2), ("toggled", True)]


async def test_a_renamed_light_is_followed_with_the_remembered_resources_and_reported(time: VirtualTime) -> None:
    """Decision 32: the entry resolves to the light it found before, and each resolution reports what was found and the
    rename, on the event loop's next turn, for the runtime to save."""
    ceiling = EntryKey(DEVICE_ID, "lights", "ceiling")
    resolutions: list[tuple[float, ResolvedSwitches]] = []
    harness = Harness(
        time,
        EngineConfig(PAIRED, [hall("Ceiling")]),
        remembered={ceiling: RememberedResource("light", "l1", "Ceiling")},
        on_resolved=lambda resolved: resolutions.append((time.elapsed, resolved)),
    )
    harness.bridge.name = "Ceiling lamp"
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    lamp = RememberedResource("light", "l1", "Ceiling lamp")
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.RUNNING, RUNNING)]
    assert harness.status.snapshot().switches[0].targets == ("light 'Ceiling lamp'",)
    assert harness.bridge.on is True
    ((at, resolved),) = resolutions
    assert at == 0
    assert resolved.config is harness.supervisor._config
    assert resolved.remembered == {ceiling: lamp}
    assert resolved.renames == [Renamed(ceiling, "Ceiling", "Ceiling", lamp, "Ceiling lamp")]
    await harness.stop()


async def test_an_on_resolved_that_raises_does_not_stop_the_supervisor(time: VirtualTime) -> None:
    errors: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))

    def remember(resolved: ResolvedSwitches) -> None:
        raise RuntimeError("saving broke")

    harness = Harness(time, EngineConfig(PAIRED, [hall()]), on_resolved=remember)
    await harness.start()
    await asyncio.sleep(1)

    assert [str(context["exception"]) for context in errors] == ["saving broke"]
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.RUNNING, RUNNING)]
    assert harness.running is not None
    assert not harness.running.done()
    await harness.stop()


# Resolving again while the runner runs (decision 32).

MISSING = "1 wall switch toggles 0 Hue lights, rooms and zones; 1 is missing."


async def test_the_switches_are_resolved_again_every_15_minutes_and_the_runner_takes_their_new_targets(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """Without a restart: the runner keeps the switch's baseline, and a rename is reported as at the start."""
    caplog.set_level(logging.INFO)
    resolutions: list[tuple[float, ResolvedSwitches]] = []
    harness = Harness(
        time,
        EngineConfig(PAIRED, [hall()]),
        on_resolved=lambda resolved: resolutions.append((time.elapsed, resolved)),
    )
    published: list[StatusSnapshot] = []
    harness.status.add_listener(published.append)
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)
    await asyncio.sleep(100)
    harness.bridge.name = "Ceiling lamp"
    await asyncio.sleep(800)

    # The resolutions at 0 and 900 s, each one read of the lights.
    assert harness.bridge.requested("/clip/v2/resource/light") == pytest.approx([0, 900])
    assert harness.status.snapshot().switches[0].targets == ("light 'Ceiling lamp'",)
    assert [(at, [rename.new_entry for rename in resolved.renames]) for at, resolved in resolutions] == [
        (0, []),
        (900, ["Ceiling lamp"]),
    ]
    # The status published the new targets, without moving since.
    assert published[-1].switches[0].targets == ("light 'Ceiling lamp'",)
    assert published[-1].service.since == VirtualTime.START

    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.bridge.on is True
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.RUNNING, RUNNING)]
    assert harness.bridge.created == 1
    lines = [record.getMessage() for record in caplog.records]
    assert lines.count("Listening for wall switch presses.") == 1
    assert "Hall (1000abcdef) -> light 'Ceiling lamp'" in lines
    await harness.stop()


async def test_while_a_switch_misses_an_entry_the_switches_are_resolved_again_every_minute(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """Until it is found; each problem is warned about once."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig(PAIRED, [hall("Porch")]))
    await harness.start()
    await asyncio.sleep(90)
    harness.bridge.name = "Porch"
    await asyncio.sleep(1000)

    assert harness.bridge.requested("/clip/v2/resource/light") == pytest.approx([0, 60, 120, 1020])
    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.RUNNING, MISSING),
        (120, State.RUNNING, RUNNING),
    ]
    assert supervisor_log(caplog) == [
        (logging.WARNING, "Hall (1000abcdef): no Hue light named 'Porch'. Known lights: 'Ceiling'")
    ]
    await harness.stop()


async def test_a_toggle_that_finds_a_target_gone_resolves_the_switches_again_at_once(time: VirtualTime) -> None:
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)
    await asyncio.sleep(1)
    harness.bridge.gone = True
    harness.broadcast(2)
    await asyncio.sleep(70)

    # The toggle's read and the resolution after its 404, both at 2 s, then one a minute later.
    assert harness.bridge.requested("/clip/v2/resource/light") == pytest.approx([0, 2, 2, 62])
    (switch,) = harness.status.snapshot().switches
    assert (switch.targets, switch.problems) == (
        (),
        ("Hall (1000abcdef): no Hue light named 'Ceiling'. Known lights: (none)",),
    )
    assert harness.status.history[-1] == (2, State.RUNNING, MISSING)
    assert switch.last_action is not None
    assert (switch.last_action.changed, switch.last_action.failed) == (0, 1)
    await harness.stop()


async def test_a_bridge_error_while_resolving_again_changes_nothing(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """The keep-alive tells whether the bridge is reachable; the next resolution comes after the same interval."""
    caplog.set_level(logging.DEBUG, logger=supervisor.__name__)
    resolutions: list[float] = []
    harness = Harness(
        time, EngineConfig(PAIRED, [hall()]), on_resolved=lambda resolved: resolutions.append(time.elapsed)
    )
    await harness.start()
    await asyncio.sleep(899)
    harness.bridge.fail("/clip/v2/resource/light", 503, count=1)
    harness.bridge.name = "Ceiling lamp"
    await asyncio.sleep(2)
    stayed = harness.status.snapshot().switches[0].targets
    await asyncio.sleep(900)

    assert stayed == ("light 'Ceiling'",)
    assert resolutions == pytest.approx([0, 1800])
    assert harness.bridge.requested("/clip/v2/resource/light") == pytest.approx([0, 900, 1800])
    assert harness.status.history == [(0, State.CONNECTING, CONNECTING), (0, State.RUNNING, RUNNING)]
    assert harness.status.snapshot().switches[0].targets == ("light 'Ceiling lamp'",)
    assert (
        logging.DEBUG,
        "The lights, rooms and zones stay as they were: the Hue bridge didn't list them (Hue bridge returned 503 Service "
        "Unavailable for GET /clip/v2/resource/light).",
    ) in supervisor_log(caplog)
    await harness.stop()


# Failures.


@pytest.fixture
def crash_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first resolving of the names fails as a bug would, with an exception that nobody expects."""
    resolve = supervisor.resolve_bindings
    calls = itertools.count()

    async def crash_first(*args: Any) -> list[SwitchBinding]:
        if next(calls) == 0:
            raise RuntimeError("resolving broke")
        return await resolve(*args)

    monkeypatch.setattr(supervisor, "resolve_bindings", crash_first)


@pytest.mark.usefixtures("crash_once")
async def test_an_unexpected_stop_starts_the_bridge_again_30_seconds_later(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))
    await harness.start()
    await asyncio.sleep(29)
    stopped = (harness.bridge.created, harness.bridge.closed)
    await asyncio.sleep(2)

    assert harness.status.history == [
        (0, State.CONNECTING, CONNECTING),
        (0, State.ERROR, "The bridge stopped unexpectedly: resolving broke"),
        (30, State.CONNECTING, CONNECTING),
        (30, State.RUNNING, RUNNING),
    ]
    (record,) = [record for record in caplog.records if record.name == supervisor.__name__]
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        "The bridge stopped unexpectedly; restarting it in 30 s.",
    )
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)
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

    # Without a word: the runner only returns when cancelled.
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


@pytest.mark.usefixtures("crash_once")
async def test_config_changed_logs_the_restart_only_while_the_bridge_runs(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    restart = (logging.INFO, "Configuration changed; restarting the bridge.")
    harness = Harness(time, EngineConfig(PAIRED, [hall()]))

    # Not before it runs.
    harness.supervisor.config_changed()
    assert supervisor_log(caplog) == []

    # Not while it waits to start again after an unexpected stop.
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


# Following the bridge to a new address (decision 33).

OLD = "192.0.2.10"
NEW = "192.0.2.20"
STORED_ID = "001788FFFE100491"
"""The bridge id as the bridge's config gives it, which the entry stores."""
LISTED_ID = "001788fffe100491"
"""The same id as mDNS and Signify's endpoint list it."""
PIN = "AB" * 32
MOVING = HueSettings(OLD, STORED_ID, "app-key", PIN)
NO_ROUTE = TransportError("network", f"No route to host ({OLD}:443)")
NOT_REACHABLE = f"Hue bridge not reachable: No route to host ({OLD}:443)"
LOOKING = f"The Hue bridge at {OLD} has been unreachable for 60 s; looking for it on the network."
WAITING = f"Waiting for the Hue bridge at {OLD}: {NOT_REACHABLE}"


class Finder:
    """The network as the search sees it: the bridges that each search lists (``listed``), and what reading each
    address gives (``bridges``). It records each search, when it came and which sources it asked, and each address
    read. While ``hang`` is set, a search never ends; ``fail`` is what the next search raises."""

    def __init__(self, time: VirtualTime) -> None:
        self._time = time
        self.listed: list[DiscoveredBridge] = []
        self.bridges: dict[str, HueBridgeInfo] = {}
        self.searches: list[tuple[float, bool, bool]] = []
        self.reads: list[str] = []
        self.hang = False
        self.cancelled = 0
        self.fail: Exception | None = None

    def search(self) -> BridgeSearch:
        return BridgeSearch(self.find, self.read)

    async def find(self, *, mdns: bool, cloud: bool) -> list[DiscoveredBridge]:
        self.searches.append((self._time.elapsed, mdns, cloud))
        if (failure := self.fail) is not None:
            self.fail = None
            raise failure
        if self.hang:
            try:
                await asyncio.get_running_loop().create_future()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return list(self.listed)

    async def read(self, address: str) -> HueBridgeInfo:
        self.reads.append(address)
        if (info := self.bridges.get(address)) is None:
            raise HueApiError(f"No Hue bridge answered at {address}: Connection refused ({address}:443)")
        return info

    @property
    def timeline(self) -> list[tuple[float, bool, bool]]:
        """Each search: when it came, in seconds of virtual time to the millisecond, and the sources it asked."""
        return [(round(at, 3), mdns, cloud) for at, mdns, cloud in self.searches]


def bridge_at(address: str, *, bridge_id: str = STORED_ID, certificate: str = PIN) -> HueBridgeInfo:
    """What reading a bridge at ``address`` gives."""
    return HueBridgeInfo(address, bridge_id, "Hue Bridge", "BSB002", "1972004020", certificate)


def moving(hue: HueSettings = MOVING, *, finder: Finder, time: VirtualTime, **kwargs: Any) -> Harness:
    """A supervisor whose bridge doesn't answer at its address, with the search of ``finder``."""
    harness = Harness(time, EngineConfig(hue, [hall()]), search=finder.search(), **kwargs)
    harness.bridge.unreachable = NO_ROUTE
    return harness


BOTH = (True, True)
MDNS = (True, False)


@pytest.mark.parametrize(
    ("unreachable", "error", "looking"),
    [
        (NO_ROUTE, NOT_REACHABLE, f"{WAITING}; looking for it on the network."),
        (
            TransportError("timeout", "The request timed out."),
            "Hue bridge request timed out.",
            f"Waiting for the Hue bridge at {OLD}: Hue bridge request timed out; looking for it on the network.",
        ),
    ],
)
async def test_while_connecting_the_bridge_is_looked_for_once_it_has_been_unreachable_for_60_seconds(
    time: VirtualTime, caplog: pytest.LogCaptureFixture, unreachable: TransportError, error: str, looking: str
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    harness = moving(finder=finder, time=time)
    harness.bridge.unreachable = unreachable
    await harness.start()
    await asyncio.sleep(59)
    searches_before = list(finder.searches)
    await asyncio.sleep(2)

    assert searches_before == []
    assert finder.timeline == [(60, *BOTH)]
    # The states stay; the message says that the search looks for the bridge.
    assert harness.status.history == [
        (0, State.CONNECTING, f"Connecting to the Hue bridge at {OLD}."),
        (0, State.CONNECTING, f"Waiting for the Hue bridge at {OLD}: {error}"),
        (pytest.approx(60), State.CONNECTING, looking),
    ]
    assert supervisor_log(caplog) == [
        (logging.WARNING, f"Hue bridge at {OLD} not reachable ({error}); retrying every 10 s."),
        (logging.INFO, LOOKING),
    ]
    await harness.stop()


async def test_while_running_the_bridge_is_looked_for_once_it_has_been_unreachable_for_60_seconds(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """The runner notices with its keep-alive at 45 s; the state and the message stay, as the switches' message is
    the running state's."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    harness = Harness(time, EngineConfig(MOVING, [hall()]), search=finder.search())
    await harness.start()
    await asyncio.sleep(1)
    harness.bridge.unreachable = NO_ROUTE
    await asyncio.sleep(103)
    searches_before = list(finder.searches)
    await asyncio.sleep(2)

    assert searches_before == []
    assert finder.timeline == [(105, *BOTH)]
    assert harness.status.history == [
        (0, State.CONNECTING, f"Connecting to the Hue bridge at {OLD}."),
        (0, State.RUNNING, RUNNING),
    ]
    assert supervisor_log(caplog) == [(logging.INFO, LOOKING)]
    await harness.stop()


async def test_mdns_is_asked_at_most_every_5_minutes_and_signifys_endpoint_at_most_every_15(time: VirtualTime) -> None:
    finder = Finder(time)
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(2000)

    assert finder.timeline == [
        (60, *BOTH),
        (360, *MDNS),
        (660, *MDNS),
        (960, *BOTH),
        (1260, *MDNS),
        (1560, *MDNS),
        (1860, *BOTH),
    ]
    await harness.stop()


@pytest.mark.parametrize("first", [float.fromhex("0x1.42a1d573c246bp+7"), float.fromhex("0x1.dce539ad224a5p+7")])
async def test_the_sources_that_fall_due_at_once_are_asked_in_one_search(first: float) -> None:
    """At these times of the event loop's clock, the first search's time plus 15 minutes, and plus three times 5, round
    apart, the one or the other way: as on a runner whose clock had just started, as the loop's clock starts with it."""
    asked: list[tuple[bool, bool]] = []

    async def find(*, mdns: bool, cloud: bool) -> list[DiscoveredBridge]:
        asked.append((mdns, cloud))
        return []

    search = BridgeSearch(find)
    at = first
    for _ in range(4):
        at = search.next_at(at)
        await search.find(at)

    assert (((first + 300) + 300) + 300) != first + 900
    assert asked == [BOTH, MDNS, MDNS, BOTH]


async def test_the_rates_hold_when_the_bridge_is_unreachable_again(time: VirtualTime) -> None:
    """The next time that the bridge doesn't answer, the first search waits for a source to be due."""
    finder = Finder(time)
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(95)
    harness.bridge.unreachable = None
    await asyncio.sleep(10)
    # The ping at 100 s reached the bridge, and the runner's keep-alive did too; the next one, at 145 s, doesn't.
    harness.bridge.unreachable = NO_ROUTE
    await asyncio.sleep(900)

    assert harness.status.service.state is State.RUNNING
    assert finder.timeline == [(60, *BOTH), (360, *MDNS), (660, *MDNS), (960, *BOTH)]
    await harness.stop()


async def test_the_rates_hold_across_the_supervisors_that_share_a_search(time: VirtualTime) -> None:
    """As the runtime gives each supervisor of its config entry the same search."""
    finder = Finder(time)
    search = finder.search()
    first = Harness(time, EngineConfig(MOVING, [hall()]), search=search)
    first.bridge.unreachable = NO_ROUTE
    await first.start()
    await asyncio.sleep(100)
    await first.stop()

    second = Harness(time, EngineConfig(MOVING, [hall()]), search=search)
    second.bridge.unreachable = NO_ROUTE
    await second.start()
    await asyncio.sleep(900)

    # The second one's first 60 s end at 160 s; mDNS is due again at 360 s, and the endpoint at 960 s.
    assert finder.timeline == [(60, *BOTH), (360, *MDNS), (660, *MDNS), (960, *BOTH)]
    await second.stop()


@pytest.mark.parametrize("listed_id", [LISTED_ID, None])
async def test_the_bridge_found_with_its_id_and_pin_is_reported_once(
    time: VirtualTime, caplog: pytest.LogCaptureFixture, listed_id: str | None
) -> None:
    """Once, on the event loop's next turn, and the search ends. A pin is compared as the client compares it: here
    written in lower case with colons. A bridge listed without an id is read for it."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    finder.listed = [DiscoveredBridge(NEW, listed_id, "mDNS")]
    finder.bridges = {NEW: bridge_at(NEW)}
    reports: list[tuple[float, BridgeMoved]] = []
    pin = ":".join(["ab"] * 32)
    harness = moving(
        HueSettings(OLD, STORED_ID, "app-key", pin),
        finder=finder,
        time=time,
        on_bridge_moved=lambda moved: reports.append((time.elapsed, moved)),
    )
    await harness.start()
    await asyncio.sleep(2000)

    ((at, moved),) = reports
    assert at == pytest.approx(60)
    assert (moved.config, moved.address) == (harness.supervisor._config, NEW)
    assert finder.timeline == [(60, *BOTH)]
    assert finder.reads == [NEW]
    assert harness.status.snapshot().hue.found_at is None
    # The runtime logs the move, as it saves the new address.
    assert supervisor_log(caplog) == [
        (logging.WARNING, f"Hue bridge at {OLD} not reachable ({NOT_REACHABLE}); retrying every 10 s."),
        (logging.INFO, LOOKING),
    ]
    await harness.stop()


async def test_another_bridge_and_one_with_another_certificate_are_not_used(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """A bridge listed with another id isn't read. Those listed with the bridge's id are read first, then those without
    an id; another certificate is warned about once."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    finder.listed = [
        DiscoveredBridge("192.0.2.30", "001788fffe000002", "mDNS"),
        DiscoveredBridge("192.0.2.40", LISTED_ID, "mDNS"),
        DiscoveredBridge("192.0.2.50", None, "discovery.meethue.com"),
        DiscoveredBridge("192.0.2.60", LISTED_ID, "discovery.meethue.com"),
    ]
    finder.bridges = {
        "192.0.2.30": bridge_at("192.0.2.30"),
        "192.0.2.40": bridge_at("192.0.2.40", certificate="CD" * 32),
        "192.0.2.50": bridge_at("192.0.2.50", bridge_id="001788FFFE000003"),
        "192.0.2.60": bridge_at("192.0.2.60", bridge_id="001788FFFE000004"),
    }
    reports: list[BridgeMoved] = []
    harness = moving(finder=finder, time=time, on_bridge_moved=reports.append)
    await harness.start()
    await asyncio.sleep(700)

    assert reports == []
    assert finder.timeline == [(60, *BOTH), (360, *MDNS), (660, *MDNS)]
    assert finder.reads == ["192.0.2.40", "192.0.2.60", "192.0.2.50"] * 3
    assert supervisor_log(caplog)[2:] == [
        (
            logging.WARNING,
            f"A device with the Hue bridge's id {STORED_ID} answers at 192.0.2.40, but its certificate isn't the pinned "
            f"one, so sonoff2hue doesn't use that address. If the bridge was replaced or reset, {PAIR_AGAIN}.",
        )
    ]
    assert harness.status.snapshot().hue.found_at is None
    await harness.stop()


async def test_a_bridge_at_its_own_address_is_not_read(time: VirtualTime) -> None:
    finder = Finder(time)
    finder.listed = [DiscoveredBridge(OLD, LISTED_ID, "mDNS"), DiscoveredBridge(f"{OLD}:443", None, "mDNS")]
    finder.bridges = {OLD: bridge_at(OLD), f"{OLD}:443": bridge_at(f"{OLD}:443")}
    reports: list[BridgeMoved] = []
    harness = moving(finder=finder, time=time, on_bridge_moved=reports.append)
    await harness.start()
    await asyncio.sleep(400)

    assert finder.timeline == [(60, *BOTH), (360, *MDNS)]
    assert (finder.reads, reports) == ([], [])
    await harness.stop()


async def test_the_search_ends_when_the_bridge_answers_again(time: VirtualTime) -> None:
    """While connecting and while running: a search under way is cancelled, and no other follows."""
    finder = Finder(time)
    finder.hang = True
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(95)
    harness.bridge.unreachable = None
    await asyncio.sleep(10)

    # The search at 60 s, cancelled as the ping at 100 s reached the bridge.
    assert harness.status.service.state is State.RUNNING
    assert (finder.cancelled, len(finder.searches)) == (1, 1)

    finder.hang = False
    harness.bridge.unreachable = NO_ROUTE
    await asyncio.sleep(80)
    harness.bridge.unreachable = None
    await asyncio.sleep(1000)

    # The keep-alive failed at 145 s and reached the bridge at 190 s, before the 60 s were over: no search.
    assert len(finder.searches) == 1
    await harness.stop()


async def test_a_bridge_that_answers_within_60_seconds_is_not_looked_for(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(45)
    harness.bridge.unreachable = None
    await asyncio.sleep(1000)

    # The ping at 50 s reached the bridge.
    assert harness.status.service.state is State.RUNNING
    assert finder.searches == []
    assert LOOKING not in [message for _, message in supervisor_log(caplog)]
    await harness.stop()


async def test_stopping_the_supervisor_ends_the_search(time: VirtualTime) -> None:
    finder = Finder(time)
    finder.hang = True
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(70)
    tasks = asyncio.all_tasks()

    await harness.stop_supervisor()
    await asyncio.sleep(1000)

    assert (finder.cancelled, len(finder.searches)) == (1, 1)
    assert (harness.bridge.created, harness.bridge.closed) == (1, 1)
    assert [task for task in tasks if not task.done() and "look_for_bridge" in repr(task.get_coro())] == []
    await harness.stop()


async def test_stopping_the_supervisor_while_the_runner_runs_ends_the_search(time: VirtualTime) -> None:
    finder = Finder(time)
    harness = Harness(time, EngineConfig(MOVING, [hall()]), search=finder.search())
    await harness.start()
    await asyncio.sleep(1)
    harness.bridge.unreachable = NO_ROUTE
    await asyncio.sleep(400)

    await harness.stop_supervisor()
    await asyncio.sleep(1000)

    # The keep-alive failed at 45 s: a search at 105 s, and none at 405 s, as the supervisor stopped at 401 s.
    assert finder.timeline == [(105, *BOTH)]
    await harness.stop()


async def test_the_search_ends_when_the_bridge_answers_with_a_refusal(time: VirtualTime) -> None:
    """The bridge answered, so it isn't unreachable, though the supervisor waits for a change."""
    finder = Finder(time)
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(65)
    harness.bridge.unreachable = None
    harness.bridge.fail("/clip/v2/resource/bridge", 403, '{"errors":[{"description":"unauthorized user"}]}')
    await asyncio.sleep(1000)

    assert harness.status.service.state is State.ERROR
    assert finder.timeline == [(60, *BOTH)]
    await harness.stop()


@pytest.mark.parametrize(
    ("address", "bridge_id", "looked_for"),
    [
        ("bridge.test", STORED_ID, False),
        ("Hue-Bridge.local:8443", STORED_ID, False),
        (OLD, None, False),
        (OLD, " ", False),
        (f"{OLD}:8443", STORED_ID, True),
        ("[2001:db8::10]", STORED_ID, True),
        ("fe80::10%eth0", STORED_ID, True),
    ],
)
async def test_a_host_name_is_never_looked_for_nor_a_bridge_without_its_id(
    time: VirtualTime, caplog: pytest.LogCaptureFixture, address: str, bridge_id: str | None, looked_for: bool
) -> None:
    """The system's resolver follows a host name, so it is never replaced; an IPv6 address is an address."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    harness = moving(HueSettings(address, bridge_id, "app-key", PIN), finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(400)

    assert finder.timeline == ([(60, *BOTH), (360, *MDNS)] if looked_for else [])
    assert any("looking for it on the network" in message for _, message in supervisor_log(caplog)) is looked_for
    await harness.stop()


async def test_without_a_pin_the_bridge_found_goes_to_the_status_for_the_repair(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """It isn't used, whatever its certificate: without a pin, the client checks none. It is warned about once, and
    shown until the bridge answers at its own address again."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    finder.listed = [DiscoveredBridge(NEW, LISTED_ID, "mDNS")]
    finder.bridges = {NEW: bridge_at(NEW, certificate="CD" * 32)}
    reports: list[BridgeMoved] = []
    harness = moving(
        HueSettings(OLD, STORED_ID, "app-key", None), finder=finder, time=time, on_bridge_moved=reports.append
    )
    await harness.start()
    await asyncio.sleep(61)
    found = harness.status.snapshot().hue.found_at
    await asyncio.sleep(600)
    still = harness.status.snapshot().hue.found_at
    harness.bridge.unreachable = None
    await asyncio.sleep(10)

    assert (found, still) == (NEW, NEW)
    assert harness.status.snapshot().hue.found_at is None
    assert harness.status.service.state is State.RUNNING
    assert reports == []
    assert finder.timeline == [(60, *BOTH), (360, *MDNS), (660, *MDNS)]
    assert supervisor_log(caplog)[2:] == [
        (
            logging.WARNING,
            f"The Hue bridge {STORED_ID} answers at {NEW} instead of {OLD}, but its certificate isn't pinned, so "
            f"sonoff2hue doesn't move there by itself; {PAIR_AGAIN} there.",
        ),
        (
            logging.WARNING,
            "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue "
            "integration to pair the bridge again and pin it.",
        ),
    ]
    await harness.stop()


async def test_a_search_that_fails_unexpectedly_is_logged_and_the_next_one_comes_at_its_rate(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    finder = Finder(time)
    finder.fail = RuntimeError("discovery broke")
    harness = moving(finder=finder, time=time)
    await harness.start()
    await asyncio.sleep(400)

    assert finder.timeline == [(60, *BOTH), (360, *MDNS)]
    (record,) = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert (record.name, record.getMessage()) == (
        supervisor.__name__,
        "Looking for the Hue bridge on the network failed.",
    )
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)
    assert harness.status.service.state is State.CONNECTING
    await harness.stop()
