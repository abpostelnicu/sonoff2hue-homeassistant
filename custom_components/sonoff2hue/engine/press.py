"""Wall-switch presses from the state broadcasts of a MINIR4M.

In detached relay mode the device sends the actual button event (``{"key":0}``) to the eWeLink cloud only. Over the LAN,
every press shows up as a new state broadcast with a higher ``seq`` whose relay state didn't change (AlexxIT/SonoffLAN
#1227 and #1880). So a press is "a new sequence number that isn't explained by a relay or settings change". Relay and
settings changes can only be told apart when the payload can be decoded, that is when the device key is configured.
The params are the values that ``json.loads`` reads, and settings compare with ``==``. Relay states compare without
regard to case, and numbers such as an outlet's may also be written as text.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import json

from .config import EngineConfig, SwitchSettings
from .ewelink import EwelinkAnnouncement, Json, announcements, probing_device_ids, read_params
from .mdns import MdnsPacket

type Params = Mapping[str, Json]

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
# The diagnostics that a summary shows after the relays.
_SUMMARY_KEYS = ("relaySeparation", "swMode", "triggerType", "rssi", "fwVersion")


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
        if params is not None and (separation := _integer(params.get("relaySeparation"))) is not None:
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
                key for key in _SETTING_KEYS if key in params and key in previous and params[key] != previous[key]
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
    """A short summary of the interesting params, for logs: the relays, then a few diagnostics, as JSON writes them."""
    if params is None:
        return "payload not decoded"

    parts = [f"relay{'' if outlet < 0 else outlet}={_text(state)}" for outlet, state in _relay_states(params)]
    parts += [f"{key}={value}" for key in _SUMMARY_KEYS if (value := _scalar(params.get(key))) is not None]
    return " ".join(parts)


def _describe_relay_change(before: Params, after: Params) -> str | None:
    old = dict(_relay_states(before))
    for outlet, state in _relay_states(after):
        previous = old.get(outlet)
        if previous is not None and previous.casefold() != state.casefold():
            change = f"{_text(previous)} -> {_text(state)}"
            return f"relay {change}" if outlet < 0 else f"relay {outlet} {change}"
    return None


def _relay_states(params: Params) -> list[tuple[int, str]]:
    """The relay states by outlet, sorted; ``-1`` is the top-level ``switch`` of single-channel and DIY firmwares. An
    entry of ``switches`` without a readable ``outlet`` counts by its place in the list."""
    states: dict[int, str] = {}
    if isinstance(switches := params.get("switches"), list):
        for index, item in enumerate(switches):
            if isinstance(item, dict) and isinstance(state := item.get("switch"), str):
                outlet = _integer(item.get("outlet"))
                states[index if outlet is None else outlet] = state

    if isinstance(single := params.get("switch"), str):
        states[-1] = single
    return sorted(states.items())


def _integer(value: Json) -> int | None:
    """A whole number, written as a number or as text that ``int()`` reads; ``None`` for anything else, booleans and
    numbers with a fraction included."""
    match value:
        case bool():
            return None
        case int():
            return value
        case str():
            try:
                return int(value)
            except ValueError:
                return None
        case _:
            return None


def _scalar(value: Json) -> str | None:
    """A text, number or boolean as JSON writes it, a text without its quotes; ``None`` for anything else."""
    match value:
        case str():
            return _text(value)
        case bool() | int() | float():
            return json.dumps(value)
        case _:
            return None


def _text(value: str) -> str:
    """A text as JSON writes it, without its quotes: quotes, backslashes, control and non-ASCII characters escaped, so
    that what a device sends can't break a log line."""
    return json.dumps(value)[1:-1]


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
        detector = self._detectors.get(device_id.casefold())
        return detector is not None and detector.has_baseline

    def process(self, packet: MdnsPacket) -> TrackerUpdate:
        # A device that probes for its name has just booted or rejoined Wi-Fi: its next broadcast must not be mistaken
        # for a press, even if its sequence number moved on.
        rejoined = probing_device_ids(packet.message)
        for device_id in rejoined:
            if (detector := self._detectors.get(device_id.casefold())) is not None:
                detector.reset()

        events: list[TrackedEvent] = []
        for announcement in announcements(packet):
            settings = self._config.find_switch(announcement.device_id)
            params, error = read_params(announcement, settings.device_key if settings is not None else None)
            detector = self._detectors.setdefault(announcement.device_id.casefold(), PressDetector())
            event = detector.process(announcement, params)
            events.append(TrackedEvent(announcement, event, params, settings, error, detector.is_detached))

        if not rejoined and not events:
            return EMPTY_UPDATE
        return TrackerUpdate(tuple(rejoined), tuple(events))
