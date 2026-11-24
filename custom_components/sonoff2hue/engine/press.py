"""Wall-switch presses from the state broadcasts of a MINIR4M.

A port of the .NET app's ``Ewelink/PressDetector.cs`` and ``Ewelink/SwitchTracker.cs``.

In detached relay mode the device sends the actual button event (``{"key":0}``) to the eWeLink cloud only. Over the LAN,
every press shows up as a new state broadcast with a higher ``seq`` whose relay state didn't change (AlexxIT/SonoffLAN
#1227 and #1880). So a press is "a new sequence number that isn't explained by a relay or settings change". Relay and
settings changes can only be told apart when the payload can be decoded, that is when the device key is configured.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from . import jsonnode, text
from .config import EngineConfig, SwitchSettings
from .ewelink import EwelinkAnnouncement, announcements, probing_device_ids, read_params
from .jsonnode import JsonValue
from .mdns import MdnsPacket

type Params = Mapping[str, JsonValue]

# Params that the user changes in the eWeLink app; a change in any of them is not a press. Volatile fields such as
# rssi, triggerType, fwVersion or autoUpgrade are deliberately not listed.
_SETTING_KEYS = (
    "sledOnline",
    "swMode",
    "swCtrlReverse",
    "relaySeparation",
    "configure",
    "pulses",
    "startup",
    "pulse",
    "pulseWidth",
    "lock",
)


class SwitchEventKind(Enum):
    BASELINE = "Baseline"
    """The first broadcast of the device since the start or a rejoin; it only records the current state."""
    DUPLICATE = "Duplicate"
    """A broadcast that was already processed (mDNS repeats, answers to other hosts' queries, reflectors)."""
    PRESS = "Press"
    """The wall switch was operated (detached relay mode)."""
    RELAY_CHANGED = "RelayChanged"
    """The relay itself changed (the eWeLink app, an automation, or the switch when not in detached mode)."""
    SETTINGS_CHANGED = "SettingsChanged"
    """A device setting changed (LED, power-on state, switch type, detach mode, ...)."""
    SEQUENCE_RESET = "SequenceReset"
    """The sequence number went backwards, typically after a reboot; the state was recorded again."""


@dataclass(frozen=True, slots=True)
class SwitchEvent:
    kind: SwitchEventKind
    seq: int | None
    detail: str | None = None


class PressDetector:
    """Turns the state broadcasts of one MINIR4/MINIR4M into wall-switch presses."""

    def __init__(self) -> None:
        self._has_baseline = False
        self._last_seq: int | None = None
        self._last_payload: str | None = None
        self._last_params: Params | None = None
        self.is_detached: bool | None = None
        """``relaySeparation`` from the last decoded payload; ``None`` when unknown."""

    @property
    def has_baseline(self) -> bool:
        """Whether a broadcast was seen since construction or the last :meth:`reset`."""
        return self._has_baseline

    def reset(self) -> None:
        """Forgets the baseline, e.g. after the mDNS socket was re-created."""
        self._has_baseline = False
        self._last_seq = None
        self._last_payload = None
        self._last_params = None

    def process(self, announcement: EwelinkAnnouncement, params: Params | None) -> SwitchEvent:
        if params is not None and (separation := _try_get_int(params.get("relaySeparation"))) is not None:
            self.is_detached = separation == 1

        seq = announcement.seq
        payload = (announcement.iv or "") + "|" + announcement.data

        if not self._has_baseline:
            self._remember(seq, payload, params)
            return SwitchEvent(SwitchEventKind.BASELINE, seq)

        if seq is not None and self._last_seq is not None:
            last = self._last_seq
            if seq == last:
                if self._last_params is None:
                    self._last_params = params
                return SwitchEvent(SwitchEventKind.DUPLICATE, seq)
            if seq < last:
                self._remember(seq, payload, params)
                return SwitchEvent(SwitchEventKind.SEQUENCE_RESET, seq, f"seq went from {last} to {seq}")
        elif payload == self._last_payload:
            return SwitchEvent(SwitchEventKind.DUPLICATE, seq)

        previous = self._last_params
        self._remember(seq, payload, params)

        if params is not None and previous is not None:
            if (relay_change := _describe_relay_change(previous, params)) is not None:
                return SwitchEvent(SwitchEventKind.RELAY_CHANGED, seq, relay_change)

            changed = [
                key
                for key in _SETTING_KEYS
                if key in params and key in previous and not jsonnode.deep_equals(params[key], previous[key])
            ]
            if changed:
                return SwitchEvent(SwitchEventKind.SETTINGS_CHANGED, seq, ", ".join(changed))

        return SwitchEvent(SwitchEventKind.PRESS, seq)

    def _remember(self, seq: int | None, payload: str, params: Params | None) -> None:
        self._has_baseline = True
        self._last_seq = seq
        self._last_payload = payload
        self._last_params = params


def summarize(params: Params | None) -> str:
    """A short summary of the interesting params, for logs."""
    if params is None:
        return "payload not decoded"

    parts = [f"relay={state}" if outlet < 0 else f"relay{outlet}={state}" for outlet, state in _relay_states(params)]
    for key in ("relaySeparation", "swMode", "triggerType", "rssi", "fwVersion"):
        value = params.get(key)
        if isinstance(value, (str, jsonnode.BrokenString, jsonnode.JsonNumber, bool)):
            parts.append(f"{key}={jsonnode.to_json_string(value).strip('"')}")
    return " ".join(parts)


def _describe_relay_change(before: Params, after: Params) -> str | None:
    old = dict(_relay_states(before))
    for outlet, state in _relay_states(after):
        previous = old.get(outlet)
        if previous is not None and not text.equals_ignore_case(previous, state):
            return f"relay {previous} -> {state}" if outlet < 0 else f"relay {outlet} {previous} -> {state}"
    return None


def _relay_states(params: Params) -> list[tuple[int, str]]:
    """The relay states by outlet, sorted; ``-1`` is the top-level ``switch`` of single-channel and DIY firmwares."""
    states: dict[int, str] = {}
    switches = params.get("switches")
    if isinstance(switches, list):
        for i, item in enumerate(switches):
            if isinstance(item, (dict, jsonnode.BrokenObject)):
                state = item.get("switch")
                if isinstance(state, (str, jsonnode.BrokenString)):
                    outlet = _try_get_int(item.get("outlet"))
                    states[i if outlet is None else outlet] = jsonnode.get_string(state)

    single = params.get("switch")
    if isinstance(single, (str, jsonnode.BrokenString)):
        states[-1] = jsonnode.get_string(single)
    return sorted(states.items())


def _try_get_int(value: JsonValue) -> int | None:
    """``TryGetInt``: a number written as a 32-bit integer, or a string that ``int.TryParse`` reads."""
    if isinstance(value, jsonnode.JsonNumber):
        return jsonnode.try_get_int32(value)
    if isinstance(value, (str, jsonnode.BrokenString)):
        return text.try_parse_int(jsonnode.get_string(value), 32)
    return None


@dataclass(frozen=True, slots=True)
class TrackedEvent:
    announcement: EwelinkAnnouncement
    event: SwitchEvent
    params: Params | None
    settings: SwitchSettings | None
    decode_error: str | None
    is_detached: bool | None


@dataclass(frozen=True, slots=True)
class TrackerUpdate:
    rejoined: tuple[str, ...]
    events: tuple[TrackedEvent, ...]


EMPTY_UPDATE = TrackerUpdate((), ())


class SwitchTracker:
    """Keeps one :class:`PressDetector` per eWeLink device seen on the LAN."""

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._detectors: dict[str, PressDetector] = {}

    def reset_all(self) -> None:
        """Forgets all baselines: the next broadcast of each device only records its state again."""
        for detector in self._detectors.values():
            detector.reset()

    def has_seen(self, device_id: str) -> bool:
        detector = self._detectors.get(text.fold(device_id))
        return detector is not None and detector.has_baseline

    def process(self, packet: MdnsPacket) -> TrackerUpdate:
        # A device that probes for its name has just booted or rejoined Wi-Fi: its next broadcast must not be mistaken
        # for a press, even if its sequence number moved on.
        rejoined = probing_device_ids(packet.message)
        for device_id in rejoined:
            if (detector := self._detectors.get(text.fold(device_id))) is not None:
                detector.reset()

        events: list[TrackedEvent] = []
        for announcement in announcements(packet):
            settings = self._config.find_switch(announcement.device_id)
            params, error = read_params(announcement, settings.device_key if settings is not None else None)
            detector = self._detectors.setdefault(text.fold(announcement.device_id), PressDetector())
            event = detector.process(announcement, params)
            events.append(TrackedEvent(announcement, event, params, settings, error, detector.is_detached))

        if not rejoined and not events:
            return EMPTY_UPDATE
        return TrackerUpdate(tuple(rejoined), tuple(events))
