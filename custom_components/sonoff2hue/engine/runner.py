"""Wall switch presses in, toggles of Home Assistant's lights out.

The :class:`Runner` attaches to the config entry's :class:`MdnsListener`, which the LAN monitor runs too (decision 8):
it adds a packet handler and a started callback, and removes them when it stops, while the listener goes on. Each time
the socket (re)starts, the callback resets the switch tracker and asks the switches for their state, their baseline.
The tracker then tells which broadcast is a press, and each press toggles the switch's lights through the
:class:`Toggler` (decision 40).

Concurrency (decision 14): each toggle is a task, and the toggles of one switch take turns on its :class:`asyncio.Lock`
in the order of the presses. Tasks start eagerly (``eager_start=True``) and a delay of 0 isn't awaited, so a task runs
until it first waits before the code that started it goes on: a stale press is dropped, or a toggle that needs no I/O
ends, before the next event of its packet is handled. Stopping tells everything to stop before it awaits anything, then
waits for all of it to end.
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import logging
from operator import methodcaller
import time

from .config import EngineConfig, SwitchSettings
from .dns import DnsRecordType
from .ewelink import SERVICE_TYPE
from .lights import LightControl, Toggler, ToggleResult
from .mdns import TRACE, MdnsListener, MdnsPacket
from .press import SwitchEventKind, SwitchTracker, TrackedEvent, summarize

_LOGGER = logging.getLogger(__name__)

MAX_PRESS_AGE = timedelta(seconds=5)
"""A press that waited longer for the lights is dropped rather than toggling them late."""
DISCOVERY_QUERY_DELAYS = (0.0, 1.0, 2.0)
"""Seconds before each of the first queries after the socket (re)starts, so queries at 0, 1 and 3 s."""
MISSING_SWITCH_QUERY_INTERVAL = 30.0
"""Seconds between the queries while a switch hasn't been seen on the LAN."""
NO_TARGETS = "None of the lights of this switch is in Home Assistant."
"""Why a press of a switch that has nothing to toggle fails: Home Assistant has none of its lights, or it has none, as
a switch migrated from version 1 can be (decision 32)."""


@dataclass(eq=False)
class SwitchBinding:
    """A switch with the lights it toggles, by their entity ids, and why its other lights are left out."""

    settings: SwitchSettings
    targets: list[str]
    problems: list[str] = field(default_factory=list)
    """Why each of the switch's lights that Home Assistant doesn't have is left out; the switch toggles the others
    (decision 32)."""
    gate: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Serializes the toggles of one switch, so that a quick double press reads the state that the first one left."""
    warned_not_detached: bool = False
    last_decode_error: str | None = None


class RunnerObserver:
    """Optional hooks into the :class:`Runner`, for example for entities. Every method does nothing by default, so an
    observer overrides only what it needs. An exception raised by one is logged and otherwise ignored."""

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        """The wall switch of ``binding`` was operated; a toggle of its lights follows."""

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        """A toggle ran. ``result.failed`` counts the lights that weren't switched; a toggle cancelled because the
        runner stopped is not reported at all."""

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        """A toggle could not run at all, for example because none of the switch's lights is in Home Assistant."""

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        """A press waited more than 5 seconds for the lights and was dropped instead of toggling late."""


def bindings_by_device_id(bindings: Iterable[SwitchBinding]) -> dict[str, SwitchBinding]:
    """The bindings by their switches' device ids, casefolded. Raises :class:`ValueError` when two switches have the
    same device id, which :meth:`EngineConfig.validate_for_run` refuses before anything runs."""
    by_device_id: dict[str, SwitchBinding] = {}
    for binding in bindings:
        device_id = binding.settings.normalized_device_id
        if (key := device_id.casefold()) in by_device_id:
            raise ValueError(f"More than one switch has the device id {device_id}.")
        by_device_id[key] = binding
    return by_device_id


def labels(targets: Iterable[str]) -> str:
    """The lights, as the log lines list them, or ``(none)``."""
    return ", ".join(targets) or "(none)"


def _now() -> datetime:
    return datetime.now(UTC)


class Runner:
    """Listens for wall switch presses and toggles the lights mapped to each switch."""

    def __init__(
        self,
        config: EngineConfig,
        bindings: list[SwitchBinding],
        control: LightControl,
        observer: RunnerObserver | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """Raises :class:`ValueError` when two of ``bindings`` have the same device id. ``clock`` tells the age of a
        press, and has to agree with the listener's, which stamps each packet with the time it was received."""
        self._bindings = bindings_by_device_id(bindings)
        self._toggler = Toggler(control, _LOGGER)
        self._tracker = SwitchTracker(config)
        self._observer = observer
        self._clock = clock
        self._reported_unknown: set[str] = set()
        self._toggles: set[asyncio.Task[None]] = set()

    async def run(self, listener: MdnsListener) -> None:
        """Listens for presses on ``listener`` and toggles the lights until cancelled. When it ends, its packet handler
        and started callback are gone, and every toggle started so far has been stopped and has ended."""
        for binding in self._bindings.values():
            settings = binding.settings
            _LOGGER.info("%s -> %s", settings.display_name, labels(binding.targets))
            if not settings.device_key or settings.device_key.isspace():
                _LOGGER.info(
                    "%s: no deviceKey configured, so every new state broadcast counts as a press (relay/setting "
                    "changes cannot be filtered).",
                    settings.display_name,
                )

        loop = asyncio.get_running_loop()
        _LOGGER.info("Listening for wall switch presses.")
        remove_started = listener.add_started(self._discover_switches)
        remove_handler = listener.add_packet_handler(self.handle_packet)
        try:
            await loop.create_future()
        finally:
            # Everything is told to stop before anything is awaited: no packet starts a toggle while the started
            # callback is removed.
            remove_handler()
            toggles = self._cancel_toggles()
            await remove_started()
            if toggles:
                await asyncio.wait(toggles)

    async def _discover_switches(self, listener: MdnsListener) -> None:
        self._tracker.reset_all()

        # A few quick queries make every switch answer with its current state (the baseline). A delay of 0 isn't
        # awaited, so the first query goes out as the socket opens, before the callback first waits.
        for delay in DISCOVERY_QUERY_DELAYS:
            if delay > 0:
                await asyncio.sleep(delay)
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)

        # Keep asking only while some switch is missing: a switch without a baseline can't cause a false press.
        while True:
            await asyncio.sleep(MISSING_SWITCH_QUERY_INTERVAL)
            missing = [
                b for b in self._bindings.values() if not self._tracker.has_seen(b.settings.normalized_device_id)
            ]
            if not missing:
                return
            _LOGGER.warning("Not seen on the LAN yet: %s", ", ".join(b.settings.display_name for b in missing))
            listener.query(SERVICE_TYPE, DnsRecordType.PTR)

    def update_targets(self, bindings: Iterable[SwitchBinding]) -> None:
        """Takes the lights and problems of a new resolution of the switches while the runner runs, without restarting
        it (decision 32): each switch keeps its turn for toggles, its warnings and its tracker's baseline, and a toggle
        that a press started before goes on with the lights that it had. Logs the new lights of each switch whose
        lights changed."""
        for new in bindings:
            key = new.settings.normalized_device_id.casefold()
            current = self._bindings.get(key)
            if current is None or (current.targets, current.problems) == (new.targets, new.problems):
                continue
            self._bindings[key] = replace(current, targets=new.targets, problems=new.problems)
            if current.targets != new.targets:
                _LOGGER.info("%s -> %s", current.settings.display_name, labels(new.targets))

    def handle_packet(self, packet: MdnsPacket) -> None:
        """Handles a packet from the listener: logs what each broadcast of a configured switch means, and starts a
        toggle for each press."""
        update = self._tracker.process(packet)
        for device_id in update.rejoined:
            if (rejoined := self._bindings.get(device_id.casefold())) is not None:
                _LOGGER.info("%s (re)joined the network.", rejoined.settings.display_name)

        for tracked in update.events:
            announcement = tracked.announcement
            key = announcement.device_id.casefold()
            binding = self._bindings.get(key)
            if binding is None:
                if key not in self._reported_unknown:
                    self._reported_unknown.add(key)
                    _LOGGER.debug(
                        "Ignoring unconfigured eWeLink device %s at %s.",
                        announcement.device_id,
                        announcement.address,
                    )
                continue

            self._report_diagnostics(binding, tracked)
            name = binding.settings.display_name
            seq = tracked.event.seq
            match tracked.event.kind:
                case SwitchEventKind.PRESS:
                    _LOGGER.info("%s: wall switch operated (seq %s).", name, seq)
                    self._notify(methodcaller("on_press", binding, seq))
                    self._start_toggle(binding, announcement.received_at)
                case SwitchEventKind.BASELINE:
                    _LOGGER.info(
                        "%s: online at %s (seq %s; %s).",
                        name,
                        announcement.address,
                        seq,
                        summarize(tracked.params),
                    )
                case SwitchEventKind.RELAY_CHANGED:
                    _LOGGER.info("%s: ignored, relay changed (%s).", name, tracked.event.detail)
                case SwitchEventKind.SETTINGS_CHANGED:
                    _LOGGER.info("%s: ignored, settings changed (%s).", name, tracked.event.detail)
                case SwitchEventKind.SEQUENCE_RESET:
                    _LOGGER.info("%s: ignored, device restarted (%s).", name, tracked.event.detail)
                case SwitchEventKind.DUPLICATE:
                    _LOGGER.log(TRACE, "%s: duplicate broadcast (seq %s).", name, seq)

    def _report_diagnostics(self, binding: SwitchBinding, tracked: TrackedEvent) -> None:
        if tracked.decode_error != binding.last_decode_error:
            binding.last_decode_error = tracked.decode_error
            if tracked.decode_error is not None:
                _LOGGER.warning(
                    "%s: cannot read the LAN payload (%s); falling back to sequence numbers only.",
                    binding.settings.display_name,
                    tracked.decode_error,
                )

        if tracked.is_detached is False and not binding.warned_not_detached:
            binding.warned_not_detached = True
            _LOGGER.warning(
                "%s is not in detached relay mode, so the wall switch cuts power to the lights. Enable 'Detach relay' "
                "in the eWeLink app.",
                binding.settings.display_name,
            )

    async def when_toggles_complete(self) -> None:
        """Waits for every toggle started so far; toggles never fail, and stopped ones end quietly. Cancelling the wait
        doesn't stop them."""
        if self._toggles:
            await asyncio.wait(list(self._toggles))

    async def stop_toggles(self) -> None:
        """Cancels the running and queued toggles and waits for them; a cancelled toggle reports nothing."""
        if toggles := self._cancel_toggles():
            await asyncio.wait(toggles)

    def _cancel_toggles(self) -> list[asyncio.Task[None]]:
        toggles = list(self._toggles)
        for toggle in toggles:
            toggle.cancel()
        return toggles

    def _start_toggle(self, binding: SwitchBinding, pressed_at: datetime) -> None:
        # Eager: the task runs until it first waits before this returns, so a stale press is dropped, and a toggle that
        # needs no I/O ends, before the next event of the packet is handled.
        toggle = asyncio.get_running_loop().create_task(self._toggle_lights(binding, pressed_at), eager_start=True)
        if not toggle.done():
            self._toggles.add(toggle)
            toggle.add_done_callback(self._toggles.discard)

    async def _toggle_lights(self, binding: SwitchBinding, pressed_at: datetime) -> None:
        name = binding.settings.display_name
        async with binding.gate:
            try:
                if self._clock() - pressed_at > MAX_PRESS_AGE:
                    _LOGGER.warning(
                        "%s: dropped a press that waited more than %gs for the lights.",
                        name,
                        MAX_PRESS_AGE.total_seconds(),
                    )
                    self._notify(methodcaller("on_press_dropped", binding))
                    return

                if not binding.targets:
                    # The switch has no light to toggle, so the press is reported and its toggle fails.
                    _LOGGER.error("%s: could not toggle the lights: %s", name, NO_TARGETS)
                    self._notify(methodcaller("on_toggle_failed", binding, NO_TARGETS))
                    return

                started = time.perf_counter()
                result = await self._toggler.toggle(binding.targets)
                elapsed = timedelta(seconds=time.perf_counter() - started)
                state = "on" if result.turned_on else "off"
                notes = []
                if result.unchanged > 0:
                    notes.append(f"{result.unchanged} already {state}")
                if result.failed > 0:
                    notes.append(f"{result.failed} failed")
                _LOGGER.info(
                    "%s: turned %s %s in %s ms%s.",
                    name,
                    state.upper(),
                    labels(binding.targets),
                    elapsed // timedelta(milliseconds=1),
                    f" ({', '.join(notes)})" if notes else "",
                )
                self._notify(methodcaller("on_toggled", binding, result, elapsed))
            except Exception as err:
                _LOGGER.exception("%s: unexpected error while toggling the lights.", name)
                self._notify(methodcaller("on_toggle_failed", binding, str(err)))

    def _notify(self, callback: Callable[[RunnerObserver], None]) -> None:
        if self._observer is None:
            return
        try:
            callback(self._observer)
        except Exception:
            _LOGGER.warning("Runner observer %s failed.", type(self._observer).__name__, exc_info=True)
