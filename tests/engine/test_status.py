"""Tests of the status tracker: the runner's reports per switch, and how the parts of the state combine."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
import json

import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.config import EngineConfig, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueTarget, HueTargetKind
from custom_components.sonoff2hue.engine.press import SwitchTracker
from custom_components.sonoff2hue.engine.registry import DeviceRegistry
from custom_components.sonoff2hue.engine.runner import RunnerObserver, SwitchBinding
from custom_components.sonoff2hue.engine.status import (
    HueStatus,
    LanStatus,
    ServiceState,
    ServiceStatus,
    StatusSnapshot,
    StatusTracker,
    SwitchError,
    SwitchStatus,
    ToggleAction,
)
from custom_components.sonoff2hue.engine.toggler import ToggleResult

from .fakes import VirtualTime
from .packets import announcement, to_packet
from .test_press import KEY, minir4m_json

DEVICE = "1000abcdef"
OTHER = "1000999999"
ADDRESS = "192.0.2.5"
START = VirtualTime.START
PAIRED = HueSettings("192.0.2.20", "001788fffe000001", "app-key", "AB" * 32)
CEILING = HueTarget(HueTargetKind.LIGHT, "l1", "Ceiling")
DESK = HueTarget(HueTargetKind.LIGHT, "l2", "Desk")
HALL = HueTarget(HueTargetKind.GROUPED_LIGHT, "g1", "Hall")
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
        self._lan = SwitchTracker(EngineConfig(switches=[SwitchSettings(DEVICE, device_key=KEY, lights=["Ceiling"])]))

    @property
    def times(self) -> list[float]:
        return [seconds for seconds, _ in self.published]

    def broadcast(self, device_id: str, seq: int, iv: str | None = None) -> None:
        """A broadcast of ``device_id``, received now, as the LAN monitor hands it to the registry."""
        data, valid_iv = ewelink.encrypt(json.dumps(minir4m_json(), separators=(",", ":")), KEY)
        packet = to_packet(announcement(device_id, seq, data, iv or valid_iv, ip=ADDRESS), ADDRESS, self.time.utc())
        self.registry.apply(self._lan.process(packet), packet.received_at)


def binding(settings: SwitchSettings, *targets: HueTarget) -> SwitchBinding:
    return SwitchBinding(settings, list(targets))


def hall_switch(device_id: str = DEVICE) -> SwitchSettings:
    return SwitchSettings(device_id, "Hall", None, ["Ceiling"])


# The runner's reports.


async def test_reports_runner_outcomes_per_switch(time: VirtualTime) -> None:
    config = EngineConfig(switches=[hall_switch()])
    status = Status(time, config)
    hall = binding(config.switches[0], CEILING)
    observer: RunnerObserver = status.tracker

    status.tracker.set_running([hall])
    observer.on_toggled(
        hall, ToggleResult(turned_on=False, changed=1, unchanged=0, failed=0), timedelta(milliseconds=142)
    )
    observer.on_bridge_reachability(False, "Hue bridge request timed out.")
    observer.on_press_dropped(hall)

    snapshot = status.tracker.snapshot()
    assert snapshot.service.state == State.RUNNING
    (switch,) = snapshot.switches
    assert switch.targets == ("light 'Ceiling'",)
    assert switch.last_action is not None
    assert (switch.last_action.turned_on, switch.last_action.changed, switch.last_action.elapsed_ms) == (False, 1, 142)
    assert switch.last_error is not None
    assert "dropped" in switch.last_error.message
    status.tracker.close()


# How the parts combine.


async def test_a_new_tracker_is_starting_and_knows_nothing_yet(time: VirtualTime) -> None:
    status = Status(time, EngineConfig())

    assert status.tracker.snapshot() == StatusSnapshot(
        START,
        ServiceStatus(State.CONNECTING, "Starting.", (), START),
        HueStatus(False, None, None, False, None, None, None),
        LanStatus(False, 0, None),
        (),
    )
    status.tracker.close()


async def test_since_moves_only_when_the_state_the_message_or_the_problems_change(time: VirtualTime) -> None:
    status = Status(time, EngineConfig())
    tracker = status.tracker
    changes = [
        (State.NOT_CONFIGURED, "Pair", None, 1),
        (State.NOT_CONFIGURED, "Pair", None, 1),
        (State.NOT_CONFIGURED, "Pair", [], 1),
        (State.NOT_CONFIGURED, "Add", None, 4),
        (State.INVALID_CONFIG, "Add", None, 5),
        (State.INVALID_CONFIG, "Add", ["a", "b"], 6),
        (State.INVALID_CONFIG, "Add", ("a", "b"), 6),
        (State.INVALID_CONFIG, "Add", ["b", "a"], 8),
        (State.INVALID_CONFIG, None, ["b", "a"], 9),
        (State.ERROR, None, ["b", "a"], 10),
    ]

    for state, message, problems, since in changes:
        await asyncio.sleep(1)
        tracker.set_service(state, message, problems)
        assert tracker.service == ServiceStatus(state, message, tuple(problems or ()), at(since))

    await asyncio.sleep(1)
    # A snapshot half a second after each change; none for the calls that changed nothing.
    assert status.times == pytest.approx([1.5, 4.5, 5.5, 6.5, 8.5, 9.5, 10.5])
    tracker.close()


async def test_running_names_the_switches_and_their_targets(time: VirtualTime) -> None:
    hall, other = hall_switch(), SwitchSettings(OTHER, None, None, ["Desk"], ["room:Hall"])
    status = Status(time, EngineConfig(PAIRED, [hall, other]))
    tracker = status.tracker
    messages = [
        ([binding(hall, CEILING)], "1 wall switch toggles 1 Hue light, room or zone."),
        ([binding(hall, CEILING, DESK)], "1 wall switch toggles 2 Hue lights, rooms and zones."),
        ([binding(hall, CEILING), binding(other, DESK, HALL)], "2 wall switches toggle 3 Hue lights, rooms and zones."),
        ([], "0 wall switches toggle 0 Hue lights, rooms and zones."),
    ]

    for bindings, message in messages:
        tracker.set_service(State.CONNECTING, "Connecting to the Hue bridge at 192.0.2.20.")
        tracker.set_running(bindings)
        assert tracker.service.state == State.RUNNING
        assert tracker.service.message == message

    tracker.set_running([binding(hall, CEILING), binding(other, DESK, HALL)])
    assert [s.targets for s in tracker.snapshot().switches] == [("light 'Ceiling'",), ("light 'Desk'", "group 'Hall'")]

    # Any other state forgets them.
    tracker.set_service(State.CONNECTING, "Connecting to the Hue bridge at 192.0.2.20.")
    assert [s.targets for s in tracker.snapshot().switches] == [None, None]
    tracker.close()


async def test_running_again_with_new_targets_publishes_them_without_moving_since(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig(PAIRED, [hall]))
    status.tracker.set_running([binding(hall, CEILING)])
    await asyncio.sleep(1)

    # The service didn't change, so neither does "since"; the targets did, so a snapshot shows them.
    status.tracker.set_running([binding(hall, DESK)])
    await asyncio.sleep(1)
    # Nothing changed at all: no snapshot.
    status.tracker.set_running([binding(hall, DESK)])
    await asyncio.sleep(1)

    snapshot = status.tracker.snapshot()
    assert snapshot.service.since == START
    assert snapshot.switches[0].targets == ("light 'Desk'",)
    assert status.times == pytest.approx([0.5, 1.5])
    assert status.published[-1][1].switches[0].targets == ("light 'Desk'",)
    status.tracker.close()


async def test_missing_targets_are_their_switchs_problems_and_the_services(time: VirtualTime) -> None:
    """The message says how many are missing (decision 32); a switch's problems show with it, and all of them as the
    service's, in the configuration's order."""
    hall, other = hall_switch(), SwitchSettings(OTHER, None, None, ["Desk", "Porch"], ["room:Garage"])
    status = Status(time, EngineConfig(PAIRED, [hall, other]))
    tracker = status.tracker
    porch, garage = "1000999999: no Hue light named 'Porch'.", "1000999999: no Hue room or zone named 'room:Garage'."

    tracker.set_running([binding(hall, CEILING), SwitchBinding(other, [DESK], [porch])])
    assert (tracker.service.message, tracker.service.problems) == (
        "2 wall switches toggle 2 Hue lights, rooms and zones; 1 is missing.",
        (porch,),
    )
    await asyncio.sleep(1)
    tracker.set_running([binding(hall, CEILING), SwitchBinding(other, [], [porch, garage])])

    assert tracker.service == ServiceStatus(
        State.RUNNING, "2 wall switches toggle 1 Hue light, room or zone; 2 are missing.", (porch, garage), at(1)
    )
    assert [(s.targets, s.problems) for s in tracker.snapshot().switches] == [
        (("light 'Ceiling'",), ()),
        ((), (porch, garage)),
    ]

    # Any other state forgets them too.
    tracker.set_service(State.CONNECTING, "Connecting to the Hue bridge at 192.0.2.20.")
    assert [(s.targets, s.problems) for s in tracker.snapshot().switches] == [(None, ()), (None, ())]
    tracker.close()


async def test_a_device_id_mapped_twice_is_refused_and_changes_nothing(time: VirtualTime) -> None:
    status = Status(time, EngineConfig(PAIRED, [hall_switch()]))
    status.tracker.set_running([binding(hall_switch(), CEILING)])

    with pytest.raises(ValueError, match=r"^More than one switch has the device id 1000abcdef\.$"):
        status.tracker.set_running([binding(hall_switch(), DESK), binding(hall_switch(DEVICE.upper()), CEILING)])

    assert status.tracker.snapshot().switches[0].targets == ("light 'Ceiling'",)
    status.tracker.close()


async def test_reachability_keeps_its_message_only_while_unreachable_and_shows_only_while_paired(
    time: VirtualTime,
) -> None:
    paired = Status(time, EngineConfig(PAIRED))
    unpaired = Status(time, EngineConfig(HueSettings("192.0.2.20", certificate_sha256="AB" * 32)))

    for status in (paired, unpaired):
        status.tracker.set_bridge_reachability(False, "Hue bridge request timed out.")
    first = paired.tracker.snapshot().hue
    await asyncio.sleep(10)
    for status in (paired, unpaired):
        status.tracker.set_bridge_reachability(True, "ignored while reachable")
    second = paired.tracker.snapshot().hue
    await asyncio.sleep(10)
    paired.tracker.set_bridge_reachability(True, None)

    assert first == HueStatus(
        True, "192.0.2.20", "001788fffe000001", True, False, "Hue bridge request timed out.", START
    )
    assert second == HueStatus(True, "192.0.2.20", "001788fffe000001", True, True, None, at(10))
    # Every check counts, also one that changed nothing else.
    assert paired.tracker.snapshot().hue.checked_at == at(20)
    assert unpaired.tracker.snapshot().hue == HueStatus(False, "192.0.2.20", None, True, None, None, None)
    for status in (paired, unpaired):
        status.tracker.close()


@pytest.mark.parametrize(
    ("hue", "paired", "pinned"),
    [
        (HueSettings("192.0.2.20", app_key="app-key"), True, False),
        (HueSettings("192.0.2.20", app_key="app-key", certificate_sha256=" \u3000"), True, False),
        (HueSettings("192.0.2.20", app_key="\u2028", certificate_sha256="AB"), False, True),
        (HueSettings("192.0.2.20", app_key="\x1f"), False, False),
        (HueSettings(" ", app_key="app-key"), False, False),
    ],
)
async def test_paired_and_pinned_take_white_space_for_missing(
    time: VirtualTime, hue: HueSettings, paired: bool, pinned: bool
) -> None:
    status = Status(time, EngineConfig(hue))
    status.tracker.set_bridge_reachability(True, None)

    snapshot = status.tracker.snapshot().hue
    assert (snapshot.paired, snapshot.certificate_pinned, snapshot.reachable) == (
        paired,
        pinned,
        True if paired else None,
    )
    status.tracker.close()


async def test_toggles_failures_and_dropped_presses_are_kept_per_switch(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig(PAIRED, [hall, SwitchSettings(OTHER, None, None, ["Desk"])]))
    tracker = status.tracker
    three = binding(hall, CEILING, DESK, HALL)

    tracker.on_toggled(
        three, ToggleResult(turned_on=True, changed=1, unchanged=0, failed=2), timedelta(microseconds=142999)
    )
    snapshot = tracker.snapshot().switches
    assert snapshot[0].last_action == ToggleAction(START, True, 1, 0, 2, 142)
    assert snapshot[0].last_error == SwitchError(START, "2 of 3 lights could not be switched.")
    assert (snapshot[1].last_action, snapshot[1].last_error) == (None, None)

    # A toggle without failures keeps the last error.
    await asyncio.sleep(1)
    tracker.on_toggled(three, ToggleResult(turned_on=False, changed=3, unchanged=0, failed=0), timedelta(seconds=1))
    assert tracker.snapshot().switches[0].last_action == ToggleAction(at(1), False, 3, 0, 0, 1000)
    assert tracker.snapshot().switches[0].last_error == SwitchError(START, "2 of 3 lights could not be switched.")

    await asyncio.sleep(1)
    tracker.on_toggle_failed(three, "Hue bridge request timed out.")
    assert tracker.snapshot().switches[0].last_error == SwitchError(at(2), "Hue bridge request timed out.")

    await asyncio.sleep(1)
    tracker.on_press_dropped(binding(SwitchSettings(DEVICE.upper()), CEILING))
    assert tracker.snapshot().switches[0].last_error == SwitchError(
        at(3), "A press was dropped because it waited more than 5 seconds for the Hue bridge."
    )
    tracker.close()


async def test_switches_show_what_the_lan_says_about_their_devices(time: VirtualTime) -> None:
    status = Status(time, EngineConfig(PAIRED, [hall_switch(), SwitchSettings(f" {OTHER.upper()} ", "Unseen")]))
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


async def test_changes_are_published_together_half_a_second_after_the_first(time: VirtualTime) -> None:
    status = Status(time, EngineConfig(PAIRED, [hall_switch()]))
    tracker = status.tracker

    # The supervisor's two calls for one failed attempt reach the listeners as one snapshot.
    tracker.set_bridge_reachability(False, "Hue bridge request timed out.")
    tracker.set_service(State.CONNECTING, "Waiting for the Hue bridge at 192.0.2.20: Hue bridge request timed out.")
    await asyncio.sleep(0.2)
    tracker.on_toggle_failed(binding(hall_switch(), CEILING), "Hue bridge request timed out.")
    await asyncio.sleep(0.5)
    tracker.set_bridge_reachability(True, None)
    await asyncio.sleep(1)

    assert status.times == pytest.approx([0.5, 1.2])
    first = status.published[0][1]
    assert first.service.message == "Waiting for the Hue bridge at 192.0.2.20: Hue bridge request timed out."
    assert (first.hue.reachable, first.hue.message) == (False, "Hue bridge request timed out.")
    assert first.switches[0].last_error == SwitchError(at(0.2), "Hue bridge request timed out.")
    assert status.published[1][1].hue.reachable is True
    tracker.close()


async def test_the_registry_publishes_its_listening_state_and_the_switches_devices_only(time: VirtualTime) -> None:
    status = Status(time, EngineConfig(PAIRED, [hall_switch()]))

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


async def test_listeners_can_be_removed_and_closing_drops_a_pending_snapshot(time: VirtualTime) -> None:
    status = Status(time, EngineConfig(PAIRED, [hall_switch()]))
    other: list[StatusSnapshot] = []
    status.tracker.add_listener(other.append)

    status.tracker.set_service(State.CONNECTING, "Connecting to the Hue bridge at 192.0.2.20.")
    await asyncio.sleep(1)
    status.remove_listener()
    status.tracker.set_service(State.RUNNING, "1 wall switch toggles 1 Hue light, room or zone.")
    await asyncio.sleep(1)
    assert status.times == pytest.approx([0.5])
    assert len(other) == 2

    status.tracker.set_service(State.ERROR, "Hue bridge returned 404 Not Found for GET /clip/v2/resource/light")
    status.tracker.close()
    status.tracker.close()
    status.registry.listening = True
    status.broadcast(DEVICE, 1)
    status.tracker.set_bridge_reachability(False, "Hue bridge request timed out.")
    await asyncio.sleep(1)

    assert len(other) == 2
    assert status.registry.listening


async def test_a_new_configuration_keeps_the_reachability_of_the_same_bridge(time: VirtualTime) -> None:
    hall = hall_switch()
    status = Status(time, EngineConfig(PAIRED, [hall]))
    tracker = status.tracker
    tracker.set_bridge_reachability(True, None)
    tracker.on_toggled(binding(hall, CEILING), ToggleResult(True, 1, 0, 0), timedelta(milliseconds=5))

    # Editing the switches keeps the bridge's state and every switch's last action, also of one that comes back.
    tracker.config_changed(EngineConfig(PAIRED, [SwitchSettings(OTHER, "Porch", None, ["Desk"])]))
    assert tracker.snapshot().hue.reachable is True
    assert [s.name for s in tracker.snapshot().switches] == ["Porch"]
    tracker.config_changed(EngineConfig(PAIRED, [SwitchSettings(DEVICE, "Hallway", None, ["Desk"])]))
    assert tracker.snapshot().switches[0].last_action == ToggleAction(START, True, 1, 0, 0, 5)

    # The bridge is its address, key and pin, where a missing pin is an empty one; its id doesn't count.
    unpinned = HueSettings(PAIRED.bridge_ip, PAIRED.bridge_id, PAIRED.app_key, None)
    tracker.config_changed(EngineConfig(unpinned))
    assert tracker.snapshot().hue.reachable is None
    tracker.set_bridge_reachability(False, "Hue bridge request timed out.")
    tracker.config_changed(EngineConfig(HueSettings(PAIRED.bridge_ip, "001788fffe000002", PAIRED.app_key, "")))
    assert tracker.snapshot().hue.reachable is False
    # Each of the three counts on its own.
    tracker.config_changed(EngineConfig(HueSettings("192.0.2.20|app", app_key="key", certificate_sha256="")))
    tracker.set_bridge_reachability(True, None)
    tracker.config_changed(EngineConfig(HueSettings("192.0.2.20", app_key="app|key")))
    assert tracker.snapshot().hue.reachable is None
    tracker.set_bridge_reachability(True, None)
    tracker.config_changed(EngineConfig(HueSettings("192.0.2.21", app_key="app|key")))
    assert tracker.snapshot().hue == HueStatus(True, "192.0.2.21", None, False, None, None, None)

    await asyncio.sleep(1)
    assert status.times == pytest.approx([0.5])
    tracker.close()
