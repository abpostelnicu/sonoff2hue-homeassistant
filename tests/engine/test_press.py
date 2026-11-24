"""Tests of press detection; ports of the .NET app's PressDetectorTests."""

from datetime import UTC, datetime
import json
from typing import Any

from custom_components.sonoff2hue.engine import ewelink, jsonnode
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
from custom_components.sonoff2hue.engine.press import (
    PressDetector,
    SwitchEventKind,
    SwitchTracker,
    TrackerUpdate,
    summarize,
)

from .packets import announcement, probe, to_packet

DEVICE = "1000abcdef"
KEY = "a1b2c3d4-e5f6-4711-8899-aabbccddeeff"
Kind = SwitchEventKind


def at(seq: int | None, data: str = "payload", iv: str = "iv") -> EwelinkAnnouncement:
    return EwelinkAnnouncement(
        DEVICE,
        f"eWeLink_{DEVICE}",
        seq,
        "plug",
        True,
        iv,
        data + ("" if seq is None else str(seq)),
        None,
        datetime.now(UTC),
    )


def minir4m_json(
    relay: str = "on", rssi: int = -75, trigger_type: int = 17, led: str = "on", relay_separation: int = 1
) -> dict[str, Any]:
    """The shape of a MINIR4M fw 1.3.1 LAN payload (AlexxIT/SonoffLAN#1880)."""
    return {
        "fwVersion": "1.3.1",
        "rssi": rssi,
        "sledOnline": led,
        "swMode": 3,
        "swCtrlReverse": "off",
        "relaySeparation": relay_separation,
        "configure": [{"outlet": 0, "startup": "stay", "enableDelay": 0, "width": 1000}],
        "pulses": [{"outlet": 0, "pulse": "off", "switch": "off", "width": 500}],
        "switches": [{"outlet": 0, "switch": relay}],
        "triggerType": trigger_type,
        "autoUpgrade": {"enabled": True},
    }


def minir4m(**kwargs: Any) -> dict[str, jsonnode.JsonValue]:
    params = jsonnode.parse(json.dumps(minir4m_json(**kwargs)))
    assert isinstance(params, dict)
    return params


def test_without_payload_every_new_sequence_is_a_press() -> None:
    detector = PressDetector()

    assert detector.process(at(769), None).kind == Kind.BASELINE
    assert detector.process(at(770), None).kind == Kind.PRESS
    assert detector.process(at(770), None).kind == Kind.DUPLICATE
    assert detector.process(at(771), None).kind == Kind.PRESS
    assert detector.process(at(773), None).kind == Kind.PRESS


def test_sequence_going_backwards_is_treated_as_restart() -> None:
    detector = PressDetector()
    detector.process(at(772), None)

    restart = detector.process(at(1), None)

    assert restart.kind == Kind.SEQUENCE_RESET
    assert restart.detail == "seq went from 772 to 1"
    assert detector.process(at(2), None).kind == Kind.PRESS


def test_with_payload_press_is_new_sequence_with_unchanged_relay_and_settings() -> None:
    detector = PressDetector()
    detector.process(at(769), minir4m(relay="on", rssi=-70, trigger_type=17))

    # Physical presses only change volatile fields (rssi, triggerType, ...).
    press = detector.process(at(770), minir4m(relay="on", rssi=-75, trigger_type=10))

    assert press.kind == Kind.PRESS
    assert press.seq == 770
    assert detector.is_detached is True


def test_with_payload_relay_change_is_not_a_press() -> None:
    detector = PressDetector()
    detector.process(at(10), minir4m(relay="on"))

    relay = detector.process(at(11), minir4m(relay="off"))

    assert relay.kind == Kind.RELAY_CHANGED
    assert relay.detail == "relay 0 on -> off"
    assert detector.process(at(12), minir4m(relay="off")).kind == Kind.PRESS


def test_with_payload_settings_change_is_not_a_press() -> None:
    detector = PressDetector()
    detector.process(at(10), minir4m(led="on"))

    settings = detector.process(at(11), minir4m(led="off"))

    assert settings.kind == Kind.SETTINGS_CHANGED
    assert settings.detail == "sledOnline"


def test_reports_when_not_in_detached_mode() -> None:
    detector = PressDetector()

    detector.process(at(1), minir4m(relay_separation=0))

    assert detector.is_detached is False


def test_reset_makes_next_broadcast_the_baseline() -> None:
    detector = PressDetector()
    detector.process(at(5), None)

    detector.reset()

    assert not detector.has_baseline
    assert detector.process(at(9), None).kind == Kind.BASELINE
    assert detector.has_baseline


def test_without_sequence_falls_back_to_payload_changes() -> None:
    detector = PressDetector()
    detector.process(at(None, data="aaa", iv="1"), None)

    assert detector.process(at(None, data="aaa", iv="1"), None).kind == Kind.DUPLICATE
    assert detector.process(at(None, data="bbb", iv="2"), None).kind == Kind.PRESS


def test_summarize_shows_relay_and_diagnostics() -> None:
    summary = summarize(minir4m(relay="on", rssi=-75, trigger_type=17))

    assert summary == "relay0=on relaySeparation=1 swMode=3 triggerType=17 rssi=-75 fwVersion=1.3.1"
    assert summarize(None) == "payload not decoded"


def test_summarize_writes_values_as_system_text_json_does() -> None:
    params = jsonnode.parse('{"switch":"on","fwVersion":"<1.0 & ä>","rssi":-7.50,"swMode":true}')
    assert isinstance(params, dict)

    assert summarize(params) == "relay=on swMode=true rssi=-7.50 fwVersion=\\u003C1.0 \\u0026 \\u00E4\\u003E"


def test_tracker_probe_from_rebooted_device_rebaselines() -> None:
    config = EngineConfig(switches=[SwitchSettings(device_id="1000ABCDEF", lights=["Lamp"])])
    tracker = SwitchTracker(config)

    first = tracker.process(to_packet(announcement(DEVICE, 100, "abc", "iv1")))
    press = tracker.process(to_packet(announcement(DEVICE, 101, "abd", "iv2")))
    rejoin = tracker.process(to_packet(probe(DEVICE)))
    seen_after_probe = tracker.has_seen(DEVICE)
    after_reboot = tracker.process(to_packet(announcement(DEVICE, 102, "abe", "iv3")))

    (baseline,) = first.events
    assert baseline.event.kind == Kind.BASELINE
    (tracked,) = press.events
    assert tracked.event.kind == Kind.PRESS
    assert tracked.settings is config.switches[0]
    assert rejoin.rejoined == (DEVICE,)
    assert not seen_after_probe
    assert after_reboot.events[0].event.kind == Kind.BASELINE
    assert tracker.has_seen(DEVICE)
    assert tracker.has_seen("1000ABCDEF")


def test_tracker_decrypts_with_configured_key_and_filters_relay_changes() -> None:
    tracker = SwitchTracker(EngineConfig(switches=[SwitchSettings(device_id=DEVICE, device_key=KEY, lights=["Lamp"])]))

    def send(seq: int, params: dict[str, Any]) -> TrackerUpdate:
        data, iv = ewelink.encrypt(json.dumps(params, separators=(",", ":")), KEY)
        return tracker.process(to_packet(announcement(DEVICE, seq, data, iv)))

    assert send(1, minir4m_json(relay="on")).events[0].event.kind == Kind.BASELINE
    assert send(2, minir4m_json(relay="on", rssi=-60)).events[0].event.kind == Kind.PRESS
    relay = send(3, minir4m_json(relay="off")).events[0]
    assert relay.event.kind == Kind.RELAY_CHANGED
    assert relay.is_detached is True
    assert relay.decode_error is None


def test_tracker_malformed_iv_still_counts_presses_by_sequence() -> None:
    tracker = SwitchTracker(EngineConfig(switches=[SwitchSettings(device_id=DEVICE, device_key=KEY, lights=["Lamp"])]))
    data, _ = ewelink.encrypt(json.dumps(minir4m_json()), KEY)
    short_iv = "AAAAAAAAAAA="  # 8 bytes

    first = tracker.process(to_packet(announcement(DEVICE, 1, data, short_iv)))
    second = tracker.process(to_packet(announcement(DEVICE, 2, data, short_iv)))

    (baseline,) = first.events
    assert baseline.event.kind == Kind.BASELINE
    assert baseline.decode_error == "iv has 8 bytes instead of 16"
    assert second.events[0].event.kind == Kind.PRESS


def test_tracker_returns_empty_update_for_unrelated_packets() -> None:
    tracker = SwitchTracker(EngineConfig())
    unrelated = to_packet(announcement(DEVICE, 1, "abc", "iv", instance_prefix="ihost-"))

    assert tracker.process(unrelated) == TrackerUpdate((), ())
