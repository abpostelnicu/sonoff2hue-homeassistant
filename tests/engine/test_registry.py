"""Tests of the device registry; a port of the .NET app's ``DeviceRegistry`` test, plus what its users rely on."""

from datetime import UTC, datetime, timedelta
import json
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
from custom_components.sonoff2hue.engine.press import (
    EMPTY_UPDATE,
    SwitchEvent,
    SwitchEventKind,
    SwitchTracker,
    TrackedEvent,
    TrackerUpdate,
)
from custom_components.sonoff2hue.engine.registry import ONLINE_WINDOW, DeviceRegistry, LanDevice

from .packets import announcement, probe, to_packet
from .test_press import KEY, minir4m_json

DEVICE = "1000abcdef"
START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
Kind = SwitchEventKind
DOTLESS_I = "\u0131"
SIGMA, FINAL_SIGMA, CAPITAL_SIGMA = "\u03c3", "\u03c2", "\u03a3"


class Clock:
    """The registry's clock, moved by the test."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def feed(
    registry: DeviceRegistry, tracker: SwitchTracker, data: bytes, seconds: float = 0, sender: str = "10.0.0.5"
) -> None:
    """Hands a packet received ``seconds`` after the start to the tracker, and its result to the registry, as the LAN
    monitor does."""
    packet = to_packet(data, sender, START + timedelta(seconds=seconds))
    registry.apply(tracker.process(packet), packet.received_at)


def encrypted(seq: int, **params: Any) -> bytes:
    """A MINIR4M broadcast, encrypted with :data:`KEY`."""
    data, iv = ewelink.encrypt(json.dumps(minir4m_json(**params), separators=(",", ":")), KEY)
    return announcement(DEVICE, seq, data, iv)


def test_tracks_presses_from_lan_broadcasts() -> None:
    registry = DeviceRegistry(Clock())
    tracker = SwitchTracker(EngineConfig())
    changes: list[Kind | None] = []
    registry.add_changed_callback(lambda device: changes.append(device.last_event))

    for seq in (10, 10, 11):
        feed(registry, tracker, announcement(DEVICE, seq, "cGF5bG9hZA==", "aXY=", ip="10.0.0.7"))

    (device,) = registry.snapshot()
    assert changes == [Kind.BASELINE, Kind.PRESS]
    assert device.address == "10.0.0.7"
    assert device.seq == 11
    assert device.last_press_at is not None
    assert registry.is_online(device)


def test_keeps_what_earlier_broadcasts_showed() -> None:
    registry = DeviceRegistry(Clock())
    tracker = SwitchTracker(EngineConfig(switches=[SwitchSettings(device_id=DEVICE, device_key=KEY, lights=["Lamp"])]))
    changes: list[LanDevice] = []
    registry.add_changed_callback(changes.append)

    feed(registry, tracker, encrypted(1, relay="on"), seconds=1)
    feed(registry, tracker, encrypted(1, relay="on"), seconds=2)  # a repeat
    feed(registry, tracker, encrypted(2, relay="on", rssi=-60), seconds=3)
    feed(registry, tracker, encrypted(3, relay="off"), seconds=4)
    # Without seq and with an IV that can't be used: only the payload compare and the sender are left.
    feed(registry, tracker, announcement(DEVICE, None, "cGF5bG9hZA==", "AAAAAAAAAAA="), seconds=5, sender="10.0.0.9")

    at = [START + timedelta(seconds=s) for s in range(6)]
    baseline, press, relay, unknown_seq = changes
    assert baseline == LanDevice(
        DEVICE,
        "10.0.0.5",
        "plug",
        True,
        1,
        at[1],
        at[1],
        Kind.BASELINE,
        at[1],
        None,
        True,
        "relay0=on relaySeparation=1 swMode=3 triggerType=17 rssi=-75 fwVersion=1.3.1",
        None,
    )
    assert press.last_event == Kind.PRESS
    assert (press.first_seen_at, press.last_seen_at, press.last_event_at, press.last_press_at) == (
        at[1],
        at[3],
        at[3],
        at[3],
    )
    assert relay.last_event == Kind.RELAY_CHANGED
    assert relay.last_press_at == at[3]
    assert relay.summary is not None
    assert relay.summary.startswith("relay0=off ")
    # The seq, the summary and detached mode are kept; the decode error is the last broadcast's own.
    assert unknown_seq == LanDevice(
        DEVICE,
        "10.0.0.5",  # the A record's address, not the sender's
        "plug",
        True,
        3,
        at[1],
        at[5],
        Kind.PRESS,
        at[5],
        at[5],
        True,
        relay.summary,
        "iv has 8 bytes instead of 16",
    )
    assert registry.find(DEVICE) == unknown_seq


def test_a_fresh_tracker_leaves_what_the_registry_knew() -> None:
    registry = DeviceRegistry(Clock())
    keyed = SwitchTracker(EngineConfig(switches=[SwitchSettings(device_id=DEVICE, device_key=KEY, lights=["Lamp"])]))
    feed(registry, keyed, encrypted(1, relay="on"), seconds=1)

    # As the LAN monitor's fresh tracker after a config change that removed the key: nothing can be decoded now.
    feed(registry, SwitchTracker(EngineConfig()), encrypted(2, relay="off"), seconds=2)

    device = registry.find(DEVICE)
    assert device is not None
    assert (device.seq, device.last_event, device.detached, device.summary, device.decode_error) == (
        2,
        Kind.BASELINE,
        True,
        "relay0=on relaySeparation=1 swMode=3 triggerType=17 rssi=-75 fwVersion=1.3.1",
        None,
    )


def test_a_repeat_only_moves_last_seen() -> None:
    registry = DeviceRegistry(Clock())
    tracker = SwitchTracker(EngineConfig())
    changes: list[LanDevice] = []
    registry.add_changed_callback(changes.append)

    feed(registry, tracker, announcement(DEVICE, 7, "abc", "iv"), seconds=1)
    feed(registry, tracker, announcement(DEVICE, 7, "abc", "iv"), seconds=30)

    (device,) = registry.snapshot()
    assert len(changes) == 1
    assert (device.last_event, device.last_event_at, device.last_seen_at) == (
        Kind.BASELINE,
        START + timedelta(seconds=1),
        START + timedelta(seconds=30),
    )


def test_is_online_within_five_minutes_of_the_last_broadcast() -> None:
    clock = Clock()
    registry = DeviceRegistry(clock)
    feed(registry, SwitchTracker(EngineConfig()), announcement(DEVICE, 1, "abc", "iv"))
    (device,) = registry.snapshot()

    clock.now = START + ONLINE_WINDOW
    assert registry.is_online(device)
    clock.now += timedelta(microseconds=1)
    assert not registry.is_online(device)
    clock.now = START - timedelta(hours=1)  # heard "in the future", as .NET counts it
    assert registry.is_online(device)


def test_last_packet_at_counts_every_packet() -> None:
    registry = DeviceRegistry(Clock())
    tracker = SwitchTracker(EngineConfig())
    assert registry.last_packet_at is None

    feed(registry, tracker, announcement(DEVICE, 1, "abc", "iv"), seconds=1)
    feed(registry, tracker, probe("1000999999"), seconds=2)
    registry.apply(EMPTY_UPDATE, START + timedelta(seconds=3))

    assert registry.last_packet_at == START + timedelta(seconds=3)
    assert [device.device_id for device in registry.snapshot()] == [DEVICE]


def tracked(device_id: str, kind: Kind = Kind.BASELINE, at: datetime = START) -> TrackedEvent:
    """A tracker's result for a device id that the tracker wouldn't produce from a packet (it lowercases ids)."""
    entry = EwelinkAnnouncement(device_id, f"eWeLink_{device_id}", 1, "plug", False, None, "{}", None, at)
    return TrackedEvent(entry, SwitchEvent(kind, 1), None, None, None, None)


def test_snapshot_sorts_ids_as_ordinal_ignore_case_does() -> None:
    registry = DeviceRegistry(Clock())
    ids = ["\ue000", "_x", "Ab", "\U00010000", "aa", DOTLESS_I, "b", "ab2", "i"]
    registry.apply(TrackerUpdate((), tuple(tracked(device_id) for device_id in ids)), START)

    # By the code points of the invariant uppercase: 'A' and 'I' before '_' (U+005F), the dotless i (U+0131) stays
    # itself, and U+E000 comes before U+10000, although .NET holds that one as the surrogates U+D800 U+DC00.
    assert [device.device_id for device in registry.snapshot()] == [
        "aa",
        "Ab",
        "ab2",
        "b",
        "i",
        "_x",
        DOTLESS_I,
        "\ue000",
        "\U00010000",
    ]


def test_ids_are_the_same_device_when_ordinal_ignore_case_says_so() -> None:
    registry = DeviceRegistry(Clock())
    changes: list[str] = []
    registry.add_changed_callback(lambda device: changes.append(device.device_id))
    later = START + timedelta(seconds=1)
    sigma, final_sigma, dotless = SIGMA + "1", FINAL_SIGMA + "1", DOTLESS_I + "1"

    registry.apply(TrackerUpdate((), (tracked(sigma), tracked(dotless))), START)
    registry.apply(
        TrackerUpdate((), (tracked(final_sigma, Kind.PRESS, later), tracked("i1", Kind.PRESS, later))), later
    )

    # The final sigma and the sigma have the same uppercase; the dotless i and the i don't.
    assert [device.device_id for device in registry.snapshot()] == ["i1", dotless, final_sigma]
    found = registry.find(CAPITAL_SIGMA + "1")
    assert found is not None
    assert (found.first_seen_at, found.last_press_at) == (START, later)
    i1, dotless_i1, _ = registry.snapshot()
    assert registry.find("I1") == i1
    assert registry.find(dotless) == dotless_i1  # Python's str.upper() would turn the dotless i into an I
    assert registry.find("x") is None
    assert changes == [sigma, dotless, final_sigma, "i1"]


def test_listening_changes_are_reported_once() -> None:
    registry = DeviceRegistry(Clock())
    calls: list[bool] = []
    remove = registry.add_listening_changed_callback(lambda: calls.append(registry.listening))
    registry.add_listening_changed_callback(lambda: calls.append(not registry.listening))

    registry.listening = True
    registry.listening = True
    remove()
    registry.listening = False

    assert calls == [True, False, True]


def test_changed_callbacks_run_in_order_and_a_failure_reaches_the_caller() -> None:
    registry = DeviceRegistry(Clock())
    first: list[str] = []
    third: list[str] = []
    registry.add_changed_callback(lambda device: first.append(device.device_id))

    def failing(device: LanDevice) -> None:
        raise RuntimeError("boom")

    remove = registry.add_changed_callback(failing)
    registry.add_changed_callback(lambda device: third.append(device.device_id))

    # As a .NET event: the exception stops the remaining callbacks and reaches the caller, after the update was made.
    with pytest.raises(RuntimeError, match="boom"):
        registry.apply(TrackerUpdate((), (tracked("1000aaaaaa"), tracked("1000bbbbbb"))), START)
    assert first == ["1000aaaaaa"]
    assert third == []
    assert len(registry.snapshot()) == 2

    remove()
    registry.apply(TrackerUpdate((), (tracked("1000cccccc"),)), START)
    assert first == ["1000aaaaaa", "1000cccccc"]
    assert third == ["1000cccccc"]
