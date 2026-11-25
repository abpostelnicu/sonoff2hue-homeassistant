"""Tests of press detection: the decisions on a device's broadcasts, the tracker of every device, and summaries."""

from datetime import UTC, datetime
import json
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink
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


def minir4m(**kwargs: Any) -> dict[str, Any]:
    """:func:`minir4m_json` as the tracker reads it from a broadcast."""
    params = json.loads(json.dumps(minir4m_json(**kwargs)))
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


def test_a_duplicate_supplies_the_params_that_the_baseline_lacked() -> None:
    detector = PressDetector()
    detector.process(at(5), None)

    assert detector.process(at(5), minir4m(relay="on")).kind == Kind.DUPLICATE
    relay = detector.process(at(6), minir4m(relay="off"))

    assert (relay.kind, relay.detail) == (Kind.RELAY_CHANGED, "relay 0 on -> off")


def test_without_previous_params_a_new_sequence_is_a_press() -> None:
    detector = PressDetector()
    detector.process(at(5), None)

    assert detector.process(at(6), minir4m(relay="off")).kind == Kind.PRESS
    assert detector.process(at(7), minir4m(relay="on")).kind == Kind.RELAY_CHANGED


def test_a_missing_sequence_after_a_known_one_compares_payloads() -> None:
    detector = PressDetector()
    detector.process(at(5, data="aaa"), None)  # the payload "aaa5"

    assert detector.process(at(None, data="aaa5"), None).kind == Kind.DUPLICATE
    assert detector.process(at(None, data="bbb"), None).kind == Kind.PRESS
    assert detector.process(at(None, data="bbb"), None).kind == Kind.DUPLICATE
    # A seq after a broadcast without one has only the payload to compare with.
    assert detector.process(at(6, data="aaa"), None).kind == Kind.PRESS


def test_a_single_channel_relay_change_is_not_a_press() -> None:
    detector = PressDetector()
    detector.process(at(1), {"switch": "on"})

    relay = detector.process(at(2), {"switch": "off"})

    assert (relay.kind, relay.detail) == (Kind.RELAY_CHANGED, "relay on -> off")


def test_relay_states_compare_without_regard_to_case() -> None:
    detector = PressDetector()
    detector.process(at(1), {"switches": [{"outlet": 0, "switch": "on"}]})

    assert detector.process(at(2), {"switches": [{"outlet": 0, "switch": "ON"}]}).kind == Kind.PRESS
    # An outlet that only one broadcast has doesn't count either.
    assert detector.process(at(3), {"switches": [{"outlet": 1, "switch": "off"}]}).kind == Kind.PRESS


def test_settings_changes_list_every_changed_setting_in_order() -> None:
    detector = PressDetector()
    detector.process(at(1), {"swMode": 3, "lock": 0, "sledOnline": "on"})

    settings = detector.process(at(2), {"swMode": 2, "lock": 0, "sledOnline": "off", "rssi": -50})

    assert (settings.kind, settings.detail) == (Kind.SETTINGS_CHANGED, "sledOnline, swMode")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ({"lock": 1}, {"lock": True}),
        ({"pulseWidth": 500}, {"pulseWidth": 500.0}),
        ({"configure": [{"a": 1, "b": 2}]}, {"configure": [{"b": 2, "a": 1}]}),
        ({}, {"lock": 1}),
        ({"lock": 1}, {}),
    ],
)
def test_settings_compare_as_python_compares_them(before: dict[str, Any], after: dict[str, Any]) -> None:
    detector = PressDetector()
    detector.process(at(1), before)

    assert detector.process(at(2), after).kind == Kind.PRESS


@pytest.mark.parametrize(
    ("separation", "detached"),
    [
        (1, True),
        (0, False),
        (2, False),
        ("1", True),
        (" 0 ", False),
        ("\u0661", True),
        (True, None),
        (1.0, None),
        ("x", None),
    ],
)
def test_detached_mode_reads_relay_separation_as_a_whole_number(separation: Any, detached: bool | None) -> None:
    detector = PressDetector()

    detector.process(at(1), {"relaySeparation": separation})

    assert detector.is_detached is detached


def test_detached_mode_stays_as_it_was_when_a_payload_doesnt_say() -> None:
    detector = PressDetector()
    detector.process(at(1), {"relaySeparation": 1})

    detector.process(at(2), None)
    detector.process(at(3), {"switch": "on"})

    assert detector.is_detached is True


def test_summarize_shows_relay_and_diagnostics() -> None:
    summary = summarize(minir4m(relay="on", rssi=-75, trigger_type=17))

    assert summary == "relay0=on relaySeparation=1 swMode=3 triggerType=17 rssi=-75 fwVersion=1.3.1"
    assert summarize(None) == "payload not decoded"
    assert summarize({}) == ""


def test_summarize_orders_relays_by_outlet() -> None:
    params: dict[str, Any] = {
        "switches": [
            {"outlet": "2", "switch": "on"},
            {"switch": "off"},  # no outlet: its place in the list, 1
            {"outlet": 1.5, "switch": "off"},  # not a whole number: its place, 2, so it replaces outlet 2
            {"outlet": True, "switch": "on"},
            {"outlet": 0},
            "on",
        ],
        "switch": "on",
    }

    assert summarize(params) == "relay=on relay1=off relay2=off relay3=on"


def test_summarize_writes_values_as_json_does() -> None:
    params = json.loads(
        '{"switch":"o\\nn","fwVersion":"<1.0 & \u00e4 \\"q\\">","rssi":-7.50,"swMode":true,"triggerType":null,'
        '"relaySeparation":[1]}'
    )

    assert summarize(params) == 'relay=o\\nn swMode=true rssi=-7.5 fwVersion=<1.0 & \\u00e4 \\"q\\">'


def test_relay_changes_are_written_as_json_writes_them() -> None:
    detector = PressDetector()
    detector.process(at(1), {"switch": "on"})

    relay = detector.process(at(2), {"switch": "\u00f6ff\n"})

    assert relay.detail == "relay on -> \\u00f6ff\\n"


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
