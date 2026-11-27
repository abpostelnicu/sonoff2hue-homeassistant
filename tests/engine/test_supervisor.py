"""Tests of the supervisor: how it follows the configuration, and every state, message and restart of ``Supervisor``,
on virtual time with fake lights, a fake resolver and a fake network."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from datetime import timedelta
import inspect
import logging
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import supervisor
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.lights import ToggleResult
from custom_components.sonoff2hue.engine.mdns import MdnsListener
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.runner import RunnerObserver, SwitchBinding
from custom_components.sonoff2hue.engine.status import ServiceState, ServiceStatus, StatusSnapshot, StatusTracker
from custom_components.sonoff2hue.engine.supervisor import Supervisor

from .fakes import FakeNetwork, VirtualTime
from .packets import announcement
from .test_lights import FakeLights

DEVICE_ID = "1000abcdef"
OTHER_DEVICE_ID = "1000999999"
ADDRESS = "192.0.2.5"
CEILING = "light.ceiling"
DESK = "light.desk"
RUNNING = "1 wall switch toggles 1 light."
NO_SWITCHES = "Add a switch: pick a MINIR4M and the lights it should toggle."
RESTARTING = (logging.INFO, "Configuration changed; restarting the switches.")
State = ServiceState


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


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


class Resolver:
    """Resolves the switches' lights as Home Assistant's resolver does: each light that Home Assistant has, by the
    entity id that it has now, and why each other light is left out. It records when it was asked, in seconds of
    virtual time, and raises the errors in :attr:`errors`, one per resolution, as a bug would."""

    def __init__(self, time: VirtualTime) -> None:
        self._time = time
        self.entity_ids: dict[str, str] = {CEILING: CEILING, DESK: DESK}
        """The lights that Home Assistant has, by how a switch's settings keep them."""
        self.times: list[float] = []
        self.errors: list[Exception] = []

    def __call__(self, config: EngineConfig) -> list[SwitchBinding]:
        self.times.append(self._time.elapsed)
        if self.errors:
            raise self.errors.pop(0)
        return [self._resolve(switch) for switch in config.switches]

    def _resolve(self, switch: SwitchSettings) -> SwitchBinding:
        targets: list[str] = []
        problems: list[str] = []
        for target in switch.targets:
            if (entity_id := self.entity_ids.get(target)) is None:
                problems.append(f"{switch.display_name}: {target} was removed from Home Assistant.")
            else:
                targets.append(entity_id)
        return SwitchBinding(switch, targets, problems)


class Harness:
    """A supervisor on virtual time, with the config entry's listener on a fake network, as the LAN monitor runs it,
    and Home Assistant's lights and resolver."""

    def __init__(self, time: VirtualTime, config: EngineConfig, observer: RunnerObserver | None = None) -> None:
        self.time = time
        self.lights = FakeLights({CEILING: False, DESK: False})
        self.resolver = Resolver(time)
        self.network = FakeNetwork()
        self.listener = MdnsListener(self.network.open, self.network.watch, clock=time.utc)
        self.registry = DeviceRegistry(time.utc)
        self.status = Status(config, self.registry, time)
        self.supervisor = Supervisor(
            config,
            self.listener,
            self.status,
            control=self.lights,
            resolve=self.resolver,
            observer=observer,
            clock=time.utc,
        )
        self.listening: asyncio.Task[None] | None = None
        self.running: asyncio.Task[None] | None = None

    async def listen(self) -> None:
        self.listening = asyncio.create_task(self.listener.run())
        await self.network.next_open()

    async def start(self) -> None:
        await self.listen()
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

    def broadcast(self, seq: int, device_id: str = DEVICE_ID) -> None:
        self.network.deliver(announcement(device_id, seq, f"payload{seq}", "iv", ip=ADDRESS), (ADDRESS, 5353))


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


def hall(*lights: str) -> SwitchSettings:
    return SwitchSettings(DEVICE_ID, "Hall", None, list(lights or [CEILING]))


def supervisor_log(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == supervisor.__name__]


# Following the configuration.


async def test_follows_the_configuration(time: VirtualTime, caplog: pytest.LogCaptureFixture) -> None:
    """For every change of the configuration, the supervisor is cancelled and a new one runs the new configuration,
    with the same tracker, as the runtime does it (decision 23); here the listener, the tracker and the supervisors
    keep the wall clock's time, without clocks of their own."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    lights = FakeLights({CEILING: False})
    resolver = Resolver(time)
    network = FakeNetwork()
    listener = MdnsListener(network.open, network.watch)
    listening = asyncio.create_task(listener.run())
    await network.next_open()
    status = StatusTracker(EngineConfig(), DeviceRegistry())
    current: Supervisor | None = None
    running: asyncio.Task[None] | None = None

    async def reload(config: EngineConfig) -> None:
        nonlocal current, running
        if current is not None and running is not None:
            current.config_changed()
            await _cancel(running)
        status.config_changed(config)
        current = Supervisor(config, listener, status, control=lights, resolve=resolver)
        running = asyncio.create_task(current.run())
        await asyncio.sleep(1)

    await reload(EngineConfig())
    assert (status.service.state, status.service.message) == (State.NOT_CONFIGURED, NO_SWITCHES)

    await reload(EngineConfig([hall("light.kitchen")]))
    assert status.service.state == State.RUNNING
    assert status.service.problems == ("Hall (1000abcdef): light.kitchen was removed from Home Assistant.",)

    await reload(EngineConfig([hall()]))
    assert (status.service.state, status.service.message, status.service.problems) == (State.RUNNING, RUNNING, ())
    assert status.snapshot().switches[0].targets == (CEILING,)

    for seq in (1, 2):
        network.deliver(announcement(DEVICE_ID, seq, f"payload{seq}", "iv", ip=ADDRESS), (ADDRESS, 5353))
    await asyncio.sleep(1)
    assert lights.calls == [(CEILING, True)]

    assert running is not None
    await _cancel(running)
    await _cancel(listening)
    status.close()
    assert supervisor_log(caplog).count(RESTARTING) == 2


# The states and their messages, in the order of the checks.


async def test_without_switches_it_asks_for_one(start: Callable[..., Awaitable[Harness]]) -> None:
    harness = await start(EngineConfig())
    await asyncio.sleep(60)

    assert harness.status.history == [(0, State.NOT_CONFIGURED, NO_SWITCHES)]
    assert harness.status.service.problems == ()
    assert harness.resolver.times == []
    assert harness.network.transports[-1].sent == []  # no runner asks for the switches


async def test_configuration_errors_are_listed_one_by_one(start: Callable[..., Awaitable[Harness]]) -> None:
    switches = [
        SwitchSettings(DEVICE_ID, "Hall", None, ["\u3000"]),
        SwitchSettings(DEVICE_ID.upper(), None, None, [CEILING, " "]),
    ]
    harness = await start(EngineConfig(switches))
    harness.supervisor.targets_changed()  # nothing runs, so there is nothing to resolve again
    await asyncio.sleep(60)

    assert harness.status.history == [(0, State.INVALID_CONFIG, "The configuration has errors.")]
    assert harness.status.service.problems == (
        "switches[0] ('Hall'): empty light.",
        "switches[1]: deviceId '1000ABCDEF' is configured more than once; put all its lights in one switch.",
        "switches[1]: empty light.",
    )
    assert harness.resolver.times == []


async def test_lights_that_home_assistant_lacks_are_their_switchs_problems_and_the_rest_runs(
    start: Callable[..., Awaitable[Harness]], caplog: pytest.LogCaptureFixture
) -> None:
    """Decision 32: the switch toggles the lights that Home Assistant has, another switch runs as if nothing were
    missing, and each problem is warned about."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    porch = SwitchSettings(OTHER_DEVICE_ID, "Porch", None, [DESK])
    harness = await start(EngineConfig([hall(CEILING, "light.kitchen", "light.garage"), porch]))
    await asyncio.sleep(1)

    kitchen = "Hall (1000abcdef): light.kitchen was removed from Home Assistant."
    garage = "Hall (1000abcdef): light.garage was removed from Home Assistant."
    assert harness.status.history == [(0, State.RUNNING, "2 wall switches toggle 2 lights; 2 are missing.")]
    assert harness.status.service.problems == (kitchen, garage)
    assert [(s.targets, s.problems) for s in harness.status.snapshot().switches] == [
        ((CEILING,), (kitchen, garage)),
        ((DESK,), ()),
    ]
    assert supervisor_log(caplog) == [(logging.WARNING, kitchen), (logging.WARNING, garage)]

    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.lights.calls == [(CEILING, True)]


async def test_a_switch_without_lights_runs_and_its_presses_fail_while_the_others_toggle(
    start: Callable[..., Awaitable[Harness]], caplog: pytest.LogCaptureFixture
) -> None:
    """A switch migrated from version 1 can be left without any light: it isn't a problem of the configuration, which
    would stop every switch, and each of its presses fails, as for a switch whose lights were all removed (decision
    32)."""
    caplog.set_level(logging.INFO)
    attic = SwitchSettings(OTHER_DEVICE_ID, "Attic", None, [])
    harness = await start(EngineConfig([hall(), attic]))
    await asyncio.sleep(1)

    assert harness.status.history == [(0, State.RUNNING, "2 wall switches toggle 1 light.")]
    assert harness.status.service.problems == ()
    assert [switch.targets for switch in harness.status.snapshot().switches] == [(CEILING,), ()]

    for seq, device_id in ((1, OTHER_DEVICE_ID), (2, OTHER_DEVICE_ID), (1, DEVICE_ID), (2, DEVICE_ID)):
        harness.broadcast(seq, device_id)
    await asyncio.sleep(1)

    assert harness.lights.calls == [(CEILING, True)]
    hall_status, attic_status = harness.status.snapshot().switches
    assert hall_status.last_action is not None
    assert (hall_status.last_action.turned_on, hall_status.last_error) == (True, None)
    assert attic_status.last_action is None
    assert attic_status.last_error is not None
    assert attic_status.last_error.message == "None of the lights of this switch is in Home Assistant."
    lines = [record.getMessage() for record in caplog.records]
    assert "Attic (1000999999) -> (none)" in lines
    assert (
        "Attic (1000999999): could not toggle the lights: None of the lights of this switch is in Home Assistant."
        in lines
    )


async def test_once_the_status_says_running_every_broadcast_reaches_the_runner(time: VirtualTime) -> None:
    """The runner starts listening in the same turn of the event loop in which the status says that it runs: a
    broadcast right after is the switch's baseline, and the next one a press."""
    harness = Harness(time, EngineConfig([hall()]))
    await harness.listen()

    harness.running = asyncio.get_running_loop().create_task(harness.supervisor.run(), eager_start=True)
    assert harness.status.service.state == State.RUNNING
    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.lights.calls == [(CEILING, True)]
    await harness.stop()


async def test_presses_toggle_the_lights_and_reach_the_tracker_then_the_observer(
    start: Callable[..., Awaitable[Harness]],
) -> None:
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
    harness = await start(EngineConfig([hall()]), observer=observer)
    observer.status = harness.status
    await asyncio.sleep(1)

    harness.broadcast(1)
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.lights.calls == [(CEILING, True)]
    (switch,) = harness.status.snapshot().switches
    assert switch.last_action is not None
    assert (switch.last_action.turned_on, switch.last_action.changed, switch.last_action.failed) == (True, 1, 0)
    assert switch.last_error is None
    # The supervisor's observer got the runner's reports, each after the tracker.
    assert reports == [("press", 2), ("toggled", True)]


@pytest.mark.parametrize("with_observer", [False, True])
def test_the_runners_observer_passes_every_report_to_the_status_then_the_observer(with_observer: bool) -> None:
    """Each of the runner's reports reaches the status tracker with its arguments, then the supervisor's observer if it
    has one."""
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    class Recorder:
        def __init__(self, who: str) -> None:
            self._who = who

        def __getattr__(self, name: str) -> Callable[..., None]:
            return lambda *args: calls.append((self._who, name, args))

    observers = (Recorder("status"), Recorder("observer")) if with_observer else (Recorder("status"),)
    observer = supervisor._Observer(observers)  # type: ignore[arg-type]
    expected: list[tuple[str, str, tuple[object, ...]]] = []
    for name, method in vars(RunnerObserver).items():
        if callable(method) and not name.startswith("_"):
            args = tuple(object() for _ in list(inspect.signature(method).parameters)[1:])
            getattr(observer, name)(*args)
            expected += [(who._who, name, args) for who in observers]

    assert calls == expected
    assert {name for _, name, _ in expected} == {"on_press", "on_toggled", "on_toggle_failed", "on_press_dropped"}


# Resolving again while the switches run (decision 32).


async def test_the_lights_are_resolved_again_when_they_may_have_changed(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """Without a restart: the runner keeps the switch's baseline and takes the new lights, and the status shows them;
    any number of reports until the supervisor gets to them make one resolution."""
    caplog.set_level(logging.INFO)
    harness = Harness(time, EngineConfig([hall()]))
    published: list[StatusSnapshot] = []
    harness.status.add_listener(published.append)
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)
    await asyncio.sleep(100)

    harness.resolver.entity_ids[CEILING] = "light.ceiling_lamp"  # the light's entity id was changed
    harness.lights.states["light.ceiling_lamp"] = False
    harness.supervisor.targets_changed()
    harness.supervisor.targets_changed()
    await asyncio.sleep(1)

    assert harness.resolver.times == pytest.approx([0, 101])
    assert harness.status.snapshot().switches[0].targets == ("light.ceiling_lamp",)
    # The status published the new lights, without moving since.
    assert published[-1].switches[0].targets == ("light.ceiling_lamp",)
    assert published[-1].service.since == VirtualTime.START

    harness.broadcast(1)  # the switch's state, as before: no press
    harness.broadcast(2)
    await asyncio.sleep(1)

    assert harness.lights.calls == [("light.ceiling_lamp", True)]
    assert harness.status.history == [(0, State.RUNNING, RUNNING)]
    lines = [record.getMessage() for record in caplog.records]
    assert lines.count("Listening for wall switch presses.") == 1
    assert "Hall (1000abcdef) -> light.ceiling_lamp" in lines
    await harness.stop()


async def test_each_resolution_warns_about_the_problems_that_the_one_before_did_not_have(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig([hall(CEILING, DESK)]))
    resolver = harness.resolver
    del resolver.entity_ids[DESK]
    await harness.start()

    async def resolve_again() -> None:
        harness.supervisor.targets_changed()
        await asyncio.sleep(1)

    await resolve_again()
    del resolver.entity_ids[CEILING]
    await resolve_again()
    resolver.entity_ids |= {CEILING: CEILING, DESK: DESK}
    await resolve_again()
    del resolver.entity_ids[DESK]
    await resolve_again()

    desk = "Hall (1000abcdef): light.desk was removed from Home Assistant."
    ceiling = "Hall (1000abcdef): light.ceiling was removed from Home Assistant."
    assert supervisor_log(caplog) == [(logging.WARNING, desk), (logging.WARNING, ceiling), (logging.WARNING, desk)]
    assert harness.status.history == [
        (0, State.RUNNING, "1 wall switch toggles 1 light; 1 is missing."),
        (1, State.RUNNING, "1 wall switch toggles 0 lights; 2 are missing."),
        (2, State.RUNNING, "1 wall switch toggles 2 lights."),
        (3, State.RUNNING, "1 wall switch toggles 1 light; 1 is missing."),
    ]
    await harness.stop()


async def test_lights_that_change_before_the_switches_run_are_resolved_once_as_they_start(time: VirtualTime) -> None:
    harness = Harness(time, EngineConfig([hall()]))
    harness.supervisor.targets_changed()
    await harness.start()
    await asyncio.sleep(60)

    assert harness.resolver.times == [0]
    await harness.stop()


# Failures.


async def test_an_unexpected_stop_starts_the_switches_again_30_seconds_later(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """In between nothing runs: a press is lost, and a change of the lights waits for the start."""
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig([hall()]))
    harness.resolver.errors.append(RuntimeError("resolving broke"))
    await harness.start()
    await asyncio.sleep(10)
    harness.broadcast(1)
    harness.broadcast(2)
    harness.supervisor.targets_changed()
    await asyncio.sleep(21)

    assert harness.status.history == [
        (0, State.ERROR, "The switches stopped unexpectedly: resolving broke"),
        (30, State.RUNNING, RUNNING),
    ]
    (record,) = [record for record in caplog.records if record.name == supervisor.__name__]
    assert (record.levelno, record.getMessage()) == (
        logging.ERROR,
        "The switches stopped unexpectedly; restarting them in 30 s.",
    )
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)
    assert harness.resolver.times == [0, 30]
    assert harness.lights.calls == []
    await harness.stop()


async def test_a_failure_while_resolving_again_stops_the_runner_until_the_switches_start_again(
    time: VirtualTime,
) -> None:
    harness = Harness(time, EngineConfig([hall()]))
    await harness.start()
    await asyncio.sleep(1)
    harness.broadcast(1)
    harness.resolver.errors.append(RuntimeError("resolving broke"))
    harness.supervisor.targets_changed()
    await asyncio.sleep(1)
    harness.broadcast(2)  # no runner hears it
    await asyncio.sleep(30)
    harness.broadcast(2)  # the switch's state for the new runner
    harness.broadcast(3)
    await asyncio.sleep(1)

    assert harness.status.history == [
        (0, State.RUNNING, RUNNING),
        (1, State.ERROR, "The switches stopped unexpectedly: resolving broke"),
        (31, State.RUNNING, RUNNING),
    ]
    assert harness.lights.calls == [(CEILING, True)]
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
    harness = await start(EngineConfig([hall()]))
    await asyncio.sleep(45)

    # Without a word: the runner only returns when cancelled.
    assert harness.resolver.times == [0, 30]
    assert harness.status.history == [(0, State.RUNNING, RUNNING)]
    assert supervisor_log(caplog) == []


# Stopping, which a change of the configuration does.


async def test_stopping_while_running_stops_the_runner_and_leaves_the_listener_running(
    start: Callable[..., Awaitable[Harness]],
) -> None:
    harness = await start(EngineConfig([hall()]))
    await asyncio.sleep(5)
    harness.broadcast(1)

    await harness.stop_supervisor()
    harness.broadcast(2)
    harness.network.network_changed()
    restarted = await harness.network.next_open()
    harness.supervisor.targets_changed()
    await asyncio.sleep(100)

    assert harness.lights.calls == []
    assert harness.resolver.times == [0]
    assert restarted.sent == []  # the runner's started callback, which asks for the switches, is gone
    assert harness.listener.is_open
    assert harness.listening is not None
    assert not harness.listening.done()


# The log line of a restart for a new configuration.


async def test_config_changed_logs_the_restart_only_while_the_switches_run(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=supervisor.__name__)
    harness = Harness(time, EngineConfig([hall()]))
    harness.resolver.errors.append(RuntimeError("resolving broke"))

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
    assert supervisor_log(caplog) == [RESTARTING]

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
    harness = await start(EngineConfig())
    await asyncio.sleep(1)

    harness.supervisor.config_changed()

    assert supervisor_log(caplog) == [RESTARTING]
