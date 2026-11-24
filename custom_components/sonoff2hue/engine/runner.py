"""Wall switch presses in, Hue toggles out: a port of the .NET app's ``Bridge/Sonoff2HueRunner.cs`` and
``IRunnerObserver.cs``.

The runner attaches to an :class:`MdnsListener` that the integration runs for the LAN monitor too (decision 8 of this
repository): it adds a packet handler and a started callback, as the .NET runner's own listener had them, and removes
them when it stops.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from operator import methodcaller
import time

from . import text
from .config import ConfigError, EngineConfig, SwitchSettings
from .dns import DnsRecordType
from .errors import UnexpectedError
from .ewelink import SERVICE_TYPE
from .hue_client import HueApiError, HueClient, HueTarget
from .mdns import TRACE, MdnsListener, MdnsPacket
from .press import SwitchEventKind, SwitchTracker, TrackedEvent, summarize
from .targets import resolve
from .toggler import HueToggler, ToggleResult

_LOGGER = logging.getLogger(__name__)

MAX_PRESS_AGE = timedelta(seconds=5)
"""A press that waited longer for the bridge is dropped rather than toggling the lights late."""
KEEP_WARM_INTERVAL = 45.0
DISCOVERY_QUERY_DELAYS = (0.0, 1.0, 2.0)
"""Seconds before each of the first queries after the socket (re)starts, so queries at 0, 1 and 3 s."""
MISSING_SWITCH_QUERY_INTERVAL = 30.0
RESOLVE_RETRY_DELAY = 10.0


@dataclass(eq=False)
class SwitchBinding:
    """A switch with its resolved targets."""

    settings: SwitchSettings
    targets: list[HueTarget]
    gate: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Serializes the toggles of one switch, so that a quick double press reads the state that the first one left."""
    warned_not_detached: bool = False
    last_decode_error: str | None = None


class RunnerObserver:
    """Optional hooks into the :class:`Runner`, for example for entities. Every method does nothing by default, so an
    observer overrides only what it needs. An exception raised by one is logged and otherwise ignored."""

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        """The wall switch of ``binding`` was operated; a toggle of its targets follows."""

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        """A toggle ran. ``result.failed`` counts targets the bridge didn't switch; a toggle cancelled because the
        runner stopped is not reported at all."""

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        """A toggle could not run at all, for example because the bridge was unreachable or rejected the key."""

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        """A press waited more than 5 seconds for the bridge and was dropped instead of toggling late."""

    def on_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        """The bridge became usable or unusable, or the reason it is unusable changed. The first report follows the
        keep-alive request that :meth:`Runner.run` sends right away; toggle outcomes count too. These calls arrive in
        the order the changes happened."""

    def on_key_rejected(self) -> None:
        """The bridge rejected the application key (401 or 403): it ended a keep-alive request or a toggle. It comes
        after each such failure, right after the reachability report, so that Home Assistant can offer to pair the
        bridge again; the .NET app's observer has no such call, and nothing else changes."""


async def resolve_bindings(
    config: EngineConfig, hue: HueClient, logger: logging.Logger = _LOGGER
) -> list[SwitchBinding]:
    """Resolves the configured light, room and zone names; waits for the bridge if it isn't usable yet, logging that
    on ``logger``. Raises :class:`ConfigError` listing every name that doesn't resolve, and :class:`HueApiError` when
    the bridge refuses."""
    while True:
        try:
            lights = await hue.get_lights()
            groups = await hue.get_groups()
            errors: list[str] = []
            bindings = [SwitchBinding(s, resolve(s, lights, groups, errors)) for s in config.switches]
            if errors:
                raise ConfigError("Invalid configuration:\n" + "\n".join("  - " + error for error in errors))
        except HueApiError as err:
            if not err.is_transient:
                raise
            logger.warning("Hue bridge at %s not usable yet (%s); retrying in 10s.", _null(config.hue.bridge_ip), err)
            await asyncio.sleep(RESOLVE_RETRY_DELAY)
        else:
            return bindings


def _now() -> datetime:
    return datetime.now(UTC)


class Runner:
    """Listens for wall switch presses and toggles the lights mapped to each switch."""

    def __init__(
        self,
        config: EngineConfig,
        bindings: list[SwitchBinding],
        hue: HueClient,
        observer: RunnerObserver | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._bindings: dict[str, SwitchBinding] = {}
        for binding in bindings:
            key = text.fold(binding.settings.normalized_device_id)
            if key in self._bindings:
                raise UnexpectedError(
                    "ArgumentException",
                    f"An item with the same key has already been added. Key: {binding.settings.normalized_device_id}",
                )
            self._bindings[key] = binding
        self._hue = hue
        self._toggler = HueToggler(hue, _LOGGER)
        self._tracker = SwitchTracker(config)
        self._observer = observer
        self._clock = clock
        self._reported_unknown: set[str] = set()
        self._toggles: set[asyncio.Task[None]] = set()
        self._bridge_reachable: bool | None = None
        self._bridge_error: str | None = None

    async def run(self, listener: MdnsListener) -> None:
        """Listens for presses on ``listener`` and toggles the lights until cancelled. When it ends, its packet handler
        and started callback are gone, and the keep-alive and every toggle started so far have been stopped and have
        ended, so the client can be closed."""
        for binding in self._bindings.values():
            _LOGGER.info("%s -> %s", binding.settings.display_name, ", ".join(str(t) for t in binding.targets))
            if text.is_null_or_white_space(binding.settings.device_key):
                _LOGGER.info(
                    "%s: no deviceKey configured, so every new state broadcast counts as a press (relay/setting "
                    "changes cannot be filtered).",
                    binding.settings.display_name,
                )

        loop = asyncio.get_running_loop()
        # As RunAsync calls KeepBridgeWarmAsync: the first ping goes out before anything is awaited. The switches are
        # asked for their state after the next line, as the .NET runner's own listener opened its socket after it.
        keep_warm = loop.create_task(self._keep_bridge_warm(), eager_start=True)
        _LOGGER.info("Listening for wall switch presses.")
        remove_started = listener.add_started(self._discover_switches)
        remove_handler = listener.add_packet_handler(self.handle_packet)
        try:
            await loop.create_future()
        finally:
            # As cancelling the .NET runner's token does: everything is told to stop before anything is awaited.
            remove_handler()
            keep_warm.cancel()
            toggles = self._cancel_toggles()
            await remove_started()
            await asyncio.wait([keep_warm, *toggles])

    async def _discover_switches(self, listener: MdnsListener) -> None:
        self._tracker.reset_all()

        # A few quick queries make every switch answer with its current state (the baseline). A delay of 0 isn't
        # awaited, as .NET's Task.Delay(0) doesn't wait: the first query goes out at once.
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

    def handle_packet(self, packet: MdnsPacket) -> None:
        update = self._tracker.process(packet)
        for device_id in update.rejoined:
            if (rejoined := self._bindings.get(text.fold(device_id))) is not None:
                _LOGGER.info("%s (re)joined the network.", rejoined.settings.display_name)

        for tracked in update.events:
            announcement = tracked.announcement
            binding = self._bindings.get(text.fold(announcement.device_id))
            if binding is None:
                if (key := text.fold(announcement.device_id)) not in self._reported_unknown:
                    self._reported_unknown.add(key)
                    _LOGGER.debug(
                        "Ignoring unconfigured eWeLink device %s at %s.",
                        announcement.device_id,
                        _null(announcement.address),
                    )
                continue

            self._report_diagnostics(binding, tracked)
            name = binding.settings.display_name
            seq = tracked.event.seq
            match tracked.event.kind:
                case SwitchEventKind.PRESS:
                    _LOGGER.info("%s: wall switch operated (seq %s).", name, _null(seq))
                    self._notify(methodcaller("on_press", binding, seq))
                    self._start_toggle(binding, announcement.received_at)
                case SwitchEventKind.BASELINE:
                    _LOGGER.info(
                        "%s: online at %s (seq %s; %s).",
                        name,
                        _null(announcement.address),
                        _null(seq),
                        summarize(tracked.params),
                    )
                case SwitchEventKind.RELAY_CHANGED:
                    _LOGGER.info("%s: ignored, relay changed (%s).", name, _null(tracked.event.detail))
                case SwitchEventKind.SETTINGS_CHANGED:
                    _LOGGER.info("%s: ignored, settings changed (%s).", name, _null(tracked.event.detail))
                case SwitchEventKind.SEQUENCE_RESET:
                    _LOGGER.info("%s: ignored, device restarted (%s).", name, _null(tracked.event.detail))
                case SwitchEventKind.DUPLICATE:
                    _LOGGER.log(TRACE, "%s: duplicate broadcast (seq %s).", name, _null(seq))

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
        """Stops the running and queued toggles, as cancelling the .NET runner's token does, and waits for them; a
        stopped toggle reports nothing."""
        if toggles := self._cancel_toggles():
            await asyncio.wait(toggles)

    def _cancel_toggles(self) -> list[asyncio.Task[None]]:
        toggles = list(self._toggles)
        for toggle in toggles:
            toggle.cancel()
        return toggles

    async def ping_bridge(self) -> None:
        """One keep-alive request; its outcome also tells whether the bridge is usable."""
        try:
            await self._hue.ping()
        except Exception as err:  # noqa: BLE001 - expected or not, the bridge isn't usable now; the keep-alive goes on
            self._report_bridge_reachability(False, str(err))
            if isinstance(err, HueApiError) and err.is_authentication_error:
                self._notify(methodcaller("on_key_rejected"))
        else:
            self._report_bridge_reachability(True, None)

    def _start_toggle(self, binding: SwitchBinding, pressed_at: datetime) -> None:
        # Eager, as calling ToggleLightsAsync runs it until it first waits: a stale press is dropped, and a toggle that
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
                        "%s: dropped a press that waited more than %gs for the bridge.",
                        name,
                        MAX_PRESS_AGE.total_seconds(),
                    )
                    self._notify(methodcaller("on_press_dropped", binding))
                    return

                started = time.perf_counter()
                result = await self._toggler.toggle(binding.targets)
                elapsed = timedelta(seconds=time.perf_counter() - started)
                self._report_bridge_reachability(True, None)
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
                    ", ".join(str(target) for target in binding.targets),
                    elapsed // timedelta(milliseconds=1),
                    f" ({', '.join(notes)})" if notes else "",
                )
                self._notify(methodcaller("on_toggled", binding, result, elapsed))
            except HueApiError as err:
                # The bridge state could not be read, so the bridge is unusable right now.
                self._report_bridge_reachability(False, str(err))
                if err.is_authentication_error:
                    self._notify(methodcaller("on_key_rejected"))
                _LOGGER.error("%s: could not toggle the lights: %s", name, err)  # noqa: TRY400 - .NET logs no trace
                self._notify(methodcaller("on_toggle_failed", binding, str(err)))
            except Exception as err:
                _LOGGER.exception("%s: unexpected error while toggling the lights.", name)
                self._notify(methodcaller("on_toggle_failed", binding, str(err)))

    async def _keep_bridge_warm(self) -> None:
        # The first request goes out right away, so the bridge's state is known from the start; .NET's first
        # Task.Delay(TimeSpan.Zero) doesn't wait.
        while True:
            await self.ping_bridge()
            await asyncio.sleep(KEEP_WARM_INTERVAL)

    def _report_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        """Logs and reports changes only: usable or not, or a new reason while not usable."""
        error = None if reachable else message
        previous = self._bridge_reachable
        if previous == reachable and error == self._bridge_error:
            return

        self._bridge_reachable = reachable
        self._bridge_error = error
        if reachable and previous is False:
            _LOGGER.info("Hue bridge reachable again.")
        elif not reachable and previous is not False:
            _LOGGER.warning("Hue bridge unavailable: %s", _null(error))
        self._notify(methodcaller("on_bridge_reachability", reachable, error))

    def _notify(self, callback: Callable[[RunnerObserver], None]) -> None:
        if self._observer is None:
            return
        try:
            callback(self._observer)
        except Exception:
            _LOGGER.warning("Runner observer %s failed.", type(self._observer).__name__, exc_info=True)


def _null(value: object) -> object:
    """A value as .NET's logging writes it: null is "(null)"."""
    return "(null)" if value is None else value
