"""Tests of the status tracker: the supervisor's states, the runner's reports per switch, and how the parts of the state
combine and reach the listeners."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
import json
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.lights import ToggleResult
from custom_components.sonoff2hue.engine.press import SwitchTracker
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.runner import RunnerObserver, SwitchBinding
from custom_components.sonoff2hue.engine.status import (
    LanStatus,
    ServiceState,
    ServiceStatus,
    StatusSnapshot,
    StatusTracker,
    SwitchError,
    SwitchStatus,
    ToggleAction,
)

from .fakes import VirtualTime
from .packets import announcement, to_packet
from .test_press import KEY, minir4m_json

DEVICE = "1000abcdef"
OTHER = "1000999999"
ADDRESS = "192.0.2.5"
START = VirtualTime.START
CEILING = "light.ceiling"
DESK = "light.desk"
LAMP = "light.lamp"
NO_LIGHTS = "None of the lights of this switch is in Home Assistant."
State = ServiceState


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


def at(seconds: float) -> datetime:
    """The virtual wall clock, ``seconds`` after the start."""
    return START + timedelta(seconds=seconds)


class Status:
    """A tracker and its registry on virtual time, with the snapshots it published and when."""

    def __init__(self, time: VirtualTime, config: EngineConfig) -> None:
        self.time = time
        self.registry = DeviceRegistry(time.utc)
        self.tracker = StatusTracker(config, self.registry, time.utc)
        self.published: list[tuple[float, StatusSnapshot]] = []
        self.remove_listener = self.tracker.add_listener(
            lambda snapshot: self.published.append((time.elapsed, snapshot))
        )
        # The LAN monitor's tracker, which can read the encrypted broadcasts of DEVICE.
        self._lan = SwitchTracker(EngineConfig([SwitchSettings(DEVICE, device_key=KEY, targets=[CEILING])]))

    @property
    def times(self) -> list[float]:
        return [seconds for seconds, _ in self.published]

    def broadcast(self, device_id: str, seq: int, iv: str | None = None) -> None:
        """A broadcast of ``device_id``, received now, as the LAN monitor hands it to the registry."""
        data, valid_iv = ewelink.encrypt(json.dumps(minir4m_json(), separators=(",", ":")), KEY)
        packet = to_packet(announcement(device_id, seq, data, iv or valid_iv, ip=ADDRESS), ADDRESS, self.time.utc())
        self.registry.apply(self._lan.process(packet), packet.received_at)


def binding(settings: SwitchSettings, *lights: str) -> SwitchBinding:
    return SwitchBinding(settings, list(lights))


def hall_switch(device_id: str = DEVICE) -> SwitchSettings:
    return SwitchSettings(device_id, "Hall", None, [CEILING])


# The service.


async def test_a_new_tracker_is_starting_and_knows_nothing_yet(time: VirtualTime) -> None:
    status = Status(time, EngineConfig())

    assert status.tracker.snapshot() == StatusSnapshot(
        START, ServiceStatus(State.NOT_CONFIGURED, "Starting.", (), START), LanStatus(False, 0, None), ()
    )
    status.tracker.close()


async def test_since_moves_only_when_the_state_the_message_or_the_problems_change(time: VirtualTime) -> None:
    status = Status(time, EngineConfig())
    tracker = status.tracker
    changes: list[tuple[ServiceState, str | None, Any, float]] = [
        (State.NOT_CONFIGURED, "Starting.", None, 0),
        (State.NOT_CONFIGURED, "Add", None, 2),
        (State.NOT_CONFIGURED, "Add", None, 2),
        (State.NOT_CONFIGURED, "Add", [], 2),
        (State.NOT_CONFIGURED, "Fix", None, 5),
        (State.INVALID_CONFIG, "Fix", None, 6),
        (State.INVALID_CONFIG, "Fix", ["a", "b"], 7),
        (State.INVALID_CONFIG, "Fix", ("a", "b"), 7),
        (State.INVALID_CONFIG, "Fix", ["b", "a"], 9),
        (State.INVALID_CONFIG, None, ["b", "a"], 10),
        (State.ERROR, None, ["b", "a"], 11),
    ]

    for state, message, problems, since in changes:
        await asyncio.sleep(1)
        tracker.set_service(state, message, problems)
        assert tracker.service == ServiceStatus(state, message, tuple(problems or ()), at(since))

    await asyncio.sleep(1)
    # A snapshot half a second after each change; none for the calls that changed nothing.
    assert status.times == pytest.approx([2.5, 5.5, 6.5, 7.5, 9.5, 10.5, 11.5])
    tracker.close()


async def test_running_names_the_switches_and_how_many_lights_they_toggle(time: VirtualTime) -> None:
    hall, other = hall_switch(), SwitchSettings(OTHER, None, None, [DESK, LAMP])
    status = Status(time, EngineConfig([hall, other]))
    tracker = status.tracker
    messages = [
        ([binding(hall, CEILING)], "1 wall switch toggles 1 light."),
        ([binding(hall, CEILING, DESK)], "1 wall switch toggles 2 lights."),
        ([binding(hall, CEILING), binding(other, DESK, LAMP)], "2 wall switches toggle 3 lights."),
        # A switch without lights counts as a switch, and nothing is missing.
        ([binding(hall, CEILING), binding(other)], "2 wall switches toggle 1 light."),
        ([], "0 wall switches toggle 0 lights."),
    ]

    for bindings, message in messages:
        tracker.set_running(bindings)
        assert (tracker.service.state, tracker.service.message) == (State.RUNNING, message)

    tracker.set_running([binding(hall, CEILING), binding(other, DESK, LAMP)])
    assert [s.targets for s in tracker.snapshot().switches] == [(CEILING,), (DESK, LAMP)]

    # Any other state forgets them.
    tracker.set_service(State.ERROR, "The switches stopped unexpectedly: resolving broke")
    assert [s.targets for s in tracker.snapshot().switches] == [None, None]
    tracker.close()


async def test_running_again_with_new_lights_publishes_them_without_moving_since(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig([hall]))
    status.tracker.set_running([binding(hall, CEILING)])
    await asyncio.sleep(1)

    # The service didn't change, so neither does "since"; the lights did, so a snapshot shows them.
    status.tracker.set_running([binding(hall, DESK)])
    await asyncio.sleep(1)
    # Nothing changed at all: no snapshot.
    status.tracker.set_running([binding(hall, DESK)])
    await asyncio.sleep(1)

    snapshot = status.tracker.snapshot()
    assert snapshot.service.since == START
    assert snapshot.switches[0].targets == (DESK,)
    assert status.times == pytest.approx([0.5, 1.5])
    assert status.published[-1][1].switches[0].targets == (DESK,)
    status.tracker.close()


async def test_missing_lights_are_their_switchs_problems_and_the_services(time: VirtualTime) -> None:
    """The message says how many are missing (decision 32); a switch's problems show with it, and all of them as the
    service's, in the configuration's order."""
    hall, other = hall_switch(), SwitchSettings(OTHER, None, None, [DESK, LAMP, "light.porch"])
    status = Status(time, EngineConfig([hall, other]))
    tracker = status.tracker
    lamp = "1000999999: light.lamp was removed from Home Assistant."
    porch = "1000999999: light.porch is disabled."

    tracker.set_running([binding(hall, CEILING), SwitchBinding(other, [DESK], [lamp])])
    assert (tracker.service.message, tracker.service.problems) == (
        "2 wall switches toggle 2 lights; 1 is missing.",
        (lamp,),
    )
    await asyncio.sleep(1)
    tracker.set_running([binding(hall, CEILING), SwitchBinding(other, [], [lamp, porch])])

    assert tracker.service == ServiceStatus(
        State.RUNNING, "2 wall switches toggle 1 light; 2 are missing.", (lamp, porch), at(1)
    )
    assert [(s.targets, s.problems) for s in tracker.snapshot().switches] == [((CEILING,), ()), ((), (lamp, porch))]

    # The same lights with other problems.
    await asyncio.sleep(1)
    lamp = "1000999999: light.lamp is disabled."
    tracker.set_running([binding(hall, CEILING), SwitchBinding(other, [], [lamp, porch])])

    assert tracker.service == ServiceStatus(
        State.RUNNING, "2 wall switches toggle 1 light; 2 are missing.", (lamp, porch), at(2)
    )
    assert [(s.targets, s.problems) for s in tracker.snapshot().switches] == [((CEILING,), ()), ((), (lamp, porch))]

    # Any other state forgets them too.
    tracker.set_service(State.INVALID_CONFIG, "The configuration has errors.", ["switches[0]: empty light."])
    assert [(s.targets, s.problems) for s in tracker.snapshot().switches] == [(None, ()), (None, ())]
    tracker.close()


async def test_a_device_id_mapped_twice_is_refused_and_changes_nothing(time: VirtualTime) -> None:
    status = Status(time, EngineConfig([hall_switch()]))
    status.tracker.set_running([binding(hall_switch(), CEILING)])

    with pytest.raises(ValueError, match=r"^More than one switch has the device id 1000abcdef\.$"):
        status.tracker.set_running([binding(hall_switch(), DESK), binding(hall_switch(DEVICE.upper()), CEILING)])

    assert status.tracker.snapshot().switches[0].targets == (CEILING,)
    status.tracker.close()


# The runner's reports.


async def test_toggles_failures_and_dropped_presses_are_kept_per_switch(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig([hall, SwitchSettings(OTHER, None, None, [DESK])]))
    tracker = status.tracker
    observer: RunnerObserver = tracker
    three = binding(hall, CEILING, DESK, LAMP)

    observer.on_toggled(three, ToggleResult(True, 1, 0, 2), timedelta(microseconds=142999))
    snapshot = tracker.snapshot().switches
    assert snapshot[0].last_action == ToggleAction(START, True, 1, 0, 2, 142)
    assert snapshot[0].last_error == SwitchError(START, "2 of 3 lights could not be switched.")
    assert (snapshot[1].last_action, snapshot[1].last_error) == (None, None)

    # A toggle without failures keeps the last error.
    await asyncio.sleep(1)
    observer.on_toggled(three, ToggleResult(False, 3, 0, 0), timedelta(seconds=1))
    assert tracker.snapshot().switches[0].last_action == ToggleAction(at(1), False, 3, 0, 0, 1000)
    assert tracker.snapshot().switches[0].last_error == SwitchError(START, "2 of 3 lights could not be switched.")

    await asyncio.sleep(1)
    observer.on_toggle_failed(three, NO_LIGHTS)
    assert tracker.snapshot().switches[0].last_error == SwitchError(at(2), NO_LIGHTS)

    # The switch is found without regard to case.
    await asyncio.sleep(1)
    observer.on_press_dropped(binding(SwitchSettings(DEVICE.upper()), CEILING))
    assert tracker.snapshot().switches[0].last_error == SwitchError(
        at(3), "A press was dropped because it waited more than 5 seconds for the lights."
    )
    assert tracker.snapshot().switches[1].last_error is None
    tracker.close()


# The LAN.


async def test_switches_show_what_the_lan_says_about_their_devices(time: VirtualTime) -> None:
    status = Status(time, EngineConfig([hall_switch(), SwitchSettings(f" {OTHER.upper()} ", "Unseen")]))
    status.registry.listening = True
    status.broadcast(DEVICE, 1)
    await asyncio.sleep(10)
    status.broadcast(DEVICE, 2)
    await asyncio.sleep(10)
    status.broadcast("1000555555", 7, iv="aXY=")

    snapshot = status.tracker.snapshot()
    assert snapshot.switches == (
        SwitchStatus("Hall", DEVICE, True, ADDRESS, at(10), at(10), True, None, None, (), None, None),
        SwitchStatus("Unseen", OTHER, False, None, None, None, None, None, None, (), None, None),
    )
    assert snapshot.lan == LanStatus(True, 2, at(20))

    # Online means heard within the last 5 minutes; a broadcast that can't be read shows why.
    await asyncio.sleep(290)
    assert status.tracker.snapshot().switches[0].online
    await asyncio.sleep(0.001)
    assert not status.tracker.snapshot().switches[0].online
    status.broadcast(DEVICE, 3, iv="aXY=")
    assert status.tracker.snapshot().switches[0].decode_error == "iv has 2 bytes instead of 16"
    status.tracker.close()


async def test_the_registry_publishes_its_listening_state_and_the_switches_devices_only(time: VirtualTime) -> None:
    status = Status(time, EngineConfig([hall_switch()]))

    status.broadcast("1000555555", 1)
    await asyncio.sleep(1)
    status.broadcast(DEVICE, 1)
    await asyncio.sleep(1)
    status.broadcast(DEVICE, 1)  # a repeat changes nothing that the registry reports
    await asyncio.sleep(1)
    status.registry.listening = True
    await asyncio.sleep(1)

    assert status.times == pytest.approx([1.5, 3.5])
    assert [snapshot.lan for _, snapshot in status.published] == [
        LanStatus(False, 2, at(1)),
        LanStatus(True, 2, at(2)),
    ]
    status.tracker.close()


# Publishing.


async def test_changes_are_published_together_half_a_second_after_the_first(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig([hall]))
    tracker = status.tracker

    # The supervisor's report and the runner's that follow it reach the listeners as one snapshot.
    tracker.set_running([binding(hall, CEILING)])
    await asyncio.sleep(0.2)
    tracker.on_toggle_failed(binding(hall, CEILING), NO_LIGHTS)
    await asyncio.sleep(0.5)
    tracker.on_press_dropped(binding(hall, CEILING))
    await asyncio.sleep(1)

    assert status.times == pytest.approx([0.5, 1.2])
    first, second = (snapshot for _, snapshot in status.published)
    assert (first.service.state, first.switches[0].targets) == (State.RUNNING, (CEILING,))
    assert first.switches[0].last_error == SwitchError(at(0.2), NO_LIGHTS)
    assert second.switches[0].last_error == SwitchError(
        at(0.7), "A press was dropped because it waited more than 5 seconds for the lights."
    )
    tracker.close()


async def test_listeners_can_be_removed_and_closing_drops_a_pending_snapshot(time: VirtualTime) -> None:
    status = Status(time, EngineConfig([hall_switch()]))
    other: list[StatusSnapshot] = []
    status.tracker.add_listener(other.append)

    status.tracker.set_service(State.NOT_CONFIGURED, "Add a switch: pick a MINIR4M and the lights it should toggle.")
    await asyncio.sleep(1)
    status.remove_listener()
    status.tracker.set_running([binding(hall_switch(), CEILING)])
    await asyncio.sleep(1)
    assert status.times == pytest.approx([0.5])
    assert len(other) == 2

    status.tracker.set_service(State.ERROR, "The switches stopped unexpectedly: resolving broke")
    status.tracker.close()
    status.tracker.close()
    status.registry.listening = True
    status.broadcast(DEVICE, 1)
    status.tracker.on_toggle_failed(binding(hall_switch(), CEILING), NO_LIGHTS)
    await asyncio.sleep(1)

    assert len(other) == 2
    assert status.registry.listening


async def test_a_listener_that_raises_stops_the_ones_after_it_and_reaches_the_event_loops_handler(
    time: VirtualTime,
) -> None:
    errors: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
    status = Status(time, EngineConfig([hall_switch()]))
    later: list[StatusSnapshot] = []

    def fail(snapshot: StatusSnapshot) -> None:
        raise RuntimeError("the listener broke")

    status.tracker.add_listener(fail)
    status.tracker.add_listener(later.append)
    status.tracker.set_running([binding(hall_switch(), CEILING)])
    await asyncio.sleep(1)

    assert [str(context["exception"]) for context in errors] == ["the listener broke"]
    assert status.times == pytest.approx([0.5])
    assert later == []
    status.tracker.close()


async def test_a_new_configuration_shows_its_switches_and_keeps_their_last_actions(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig([hall]))
    tracker = status.tracker
    tracker.on_toggled(binding(hall, CEILING), ToggleResult(True, 1, 0, 0), timedelta(milliseconds=5))
    await asyncio.sleep(1)

    # Editing the switches keeps every switch's last action, also of one that comes back.
    tracker.config_changed(EngineConfig([SwitchSettings(OTHER, "Porch", None, [DESK])]))
    assert [s.name for s in tracker.snapshot().switches] == ["Porch"]
    tracker.config_changed(EngineConfig([SwitchSettings(DEVICE, "Hallway", None, [DESK])]))
    assert tracker.snapshot().switches[0].last_action == ToggleAction(START, True, 1, 0, 0, 5)
    await asyncio.sleep(1)

    assert status.times == pytest.approx([0.5, 1.5])
    assert [s.name for s in status.published[-1][1].switches] == ["Hallway"]
    tracker.close()
