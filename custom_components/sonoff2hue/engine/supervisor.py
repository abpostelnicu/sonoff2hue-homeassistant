"""Runs the bridge whenever the configuration is complete, and reports why it can't run otherwise.

A :class:`BridgeSupervisor` runs one :class:`EngineConfig` (decision 21). It checks, in this order, that the bridge is
paired, that a switch is configured, that :meth:`EngineConfig.validate_for_run` passes and that a Hue client can be made
for the bridge's settings. Then it waits for the bridge, resolves the switches' lights, rooms and zones, and runs the
:class:`Runner` on the config entry's mDNS listener, which the LAN monitor runs and keeps running. Each switch is resolved
on its own: an entry that resolves to nothing is a problem of that switch, which toggles the others, and never stops
another switch (decision 32). Each step is reported to the :class:`StatusTracker`, which is also the runner's observer.
A check that fails holds its state until the configuration changes; an unexpected failure starts the bridge again 30 s
later.

Use, for the Home Assistant runtime (decision 23): run :meth:`BridgeSupervisor.run` as a background task. For each
change of the configuration, call :meth:`BridgeSupervisor.config_changed`, cancel the task, and start a supervisor for
the new configuration once it has ended. Cancelling it stops the runner and closes the Hue client, and leaves the
listener running. ``on_key_rejected`` is called whenever the bridge rejects the application key, while the supervisor
connects to it and while the runner runs, which is when the runtime starts Home Assistant's reauthentication.
``observer`` gets the runner's reports after the status tracker, for the runtime's bus events. ``remembered`` and
``on_resolved`` follow renames (decision 32): the supervisor resolves with the resource that each entry last found, and
reports what each resolution found and which resources were renamed, which the runtime keeps and saves.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import NoReturn

from .config import ConfigError, EngineConfig, HueSettings
from .hue_client import PAIR_AGAIN, HueApiError, HueClient
from .mdns import MdnsListener
from .runner import ResolvedSwitches, Runner, RunnerObserver, SwitchBinding, resolve_bindings
from .status import ServiceState, StatusTracker
from .targets import Remembered
from .toggler import ToggleResult

_LOGGER = logging.getLogger(__name__)

BRIDGE_RETRY_DELAY = 10.0
"""Seconds between the attempts to reach the bridge."""
FAILURE_RETRY_DELAY = 30.0
"""Seconds before the bridge is started again after an unexpected stop."""

_PAIR_AGAIN_SENTENCE = PAIR_AGAIN[:1].upper() + PAIR_AGAIN[1:]
NOT_PAIRED = "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration."
"""The message while the bridge isn't paired; reconfiguring the integration pairs it (decision 22)."""
NO_SWITCHES = "Add a switch: pick a MINIR4M and the Hue lights it should toggle."
KEY_REJECTED = f"The Hue bridge no longer accepts sonoff2hue's application key. {_PAIR_AGAIN_SENTENCE}."
"""The message after the bridge rejected the application key, which says how to pair the bridge again."""
NOT_PINNED = (
    f"hue.certificateSha256 is not set, so the bridge certificate is not verified. {_PAIR_AGAIN_SENTENCE} and pin it."
)
"""The warning before the bridge runs without a pinned certificate, which says how to pin it."""

type HueClientFactory = Callable[[HueSettings], HueClient]


def create_hue_client(hue: HueSettings) -> HueClient:
    """The default :data:`HueClientFactory`: a client for the configured bridge, which checks the bridge's certificate
    against the pin, if there is one. Raises :class:`ConfigError` when the settings can't be used, such as a malformed
    bridge address or application key."""
    return HueClient(hue.bridge_ip or "", hue.app_key or "", hue.certificate_sha256)


def _now() -> datetime:
    return datetime.now(UTC)


class BridgeSupervisor:
    """Runs the bridge for one configuration until cancelled, and reports each step to the status tracker."""

    def __init__(
        self,
        config: EngineConfig,
        listener: MdnsListener,
        status: StatusTracker,
        *,
        hue_clients: HueClientFactory = create_hue_client,
        on_key_rejected: Callable[[], None] | None = None,
        observer: RunnerObserver | None = None,
        remembered: Remembered | None = None,
        on_resolved: Callable[[ResolvedSwitches], None] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """``listener`` is the config entry's mDNS listener, which the runner shares and which keeps running when the
        supervisor stops. ``on_key_rejected`` runs on the event loop soon after the bridge rejected the application key:
        while the supervisor connects to it, which leaves it in the error state until the next change, and after each
        keep-alive request or toggle that the bridge refuses so while the runner runs, which goes on running. One that
        raises goes to the loop's exception handler. ``observer`` gets each of the runner's reports right after the
        status tracker. ``remembered`` is the resource that each entry last found, and ``on_resolved`` runs on the
        event loop soon after each resolution, with what it found and the renames, which the runtime remembers and
        saves (decision 32); one that raises goes to the loop's exception handler too. ``clock`` is the runner's, for
        the age of a press, and has to agree with the listener's."""
        self._config = config
        self._listener = listener
        self._status = status
        self._hue_clients = hue_clients
        self._on_key_rejected = on_key_rejected
        self._observer = observer
        self._remembered: Remembered = remembered or {}
        self._on_resolved = on_resolved
        self._clock = clock
        self._running = False
        self._waiting_after_failure = False

    def config_changed(self) -> None:
        """Logs that the bridge restarts for a new configuration, while the supervisor runs, unless it is waiting to
        start again after an unexpected stop. The runtime then cancels this supervisor and starts one for the new
        configuration."""
        if self._running and not self._waiting_after_failure:
            _LOGGER.info("Configuration changed; restarting the bridge.")

    async def run(self) -> None:
        """Runs the bridge until cancelled, and starts it again 30 s after an unexpected stop. When it ends, the runner
        has stopped and the Hue client is closed; the listener goes on."""
        self._running = True
        try:
            while True:
                try:
                    await self._run()

                    # The runner only returns when cancelled; anything else is unexpected.
                    await asyncio.sleep(FAILURE_RETRY_DELAY)
                except Exception as err:
                    _LOGGER.exception("The bridge stopped unexpectedly; restarting it in %g s.", FAILURE_RETRY_DELAY)
                    self._status.set_service(ServiceState.ERROR, f"The bridge stopped unexpectedly: {err}")
                    self._waiting_after_failure = True
                    try:
                        await asyncio.sleep(FAILURE_RETRY_DELAY)
                    finally:
                        self._waiting_after_failure = False
        finally:
            self._running = False

    async def _run(self) -> None:
        settings = self._config
        if _is_blank(settings.hue.bridge_ip) or _is_blank(settings.hue.app_key):
            self._status.set_service(ServiceState.NOT_CONFIGURED, NOT_PAIRED)
            await _wait_for_change()

        if not settings.switches:
            self._status.set_service(ServiceState.NOT_CONFIGURED, NO_SWITCHES)
            await _wait_for_change()

        try:
            settings.validate_for_run()
        except ConfigError as err:
            self._status.set_service(ServiceState.INVALID_CONFIG, "The configuration has errors.", _problems_of(err))
            await _wait_for_change()

        try:
            hue = self._hue_clients(settings.hue)
        except ConfigError as err:
            self._status.set_service(
                ServiceState.INVALID_CONFIG, "The Hue settings in the config can't be used.", [str(err)]
            )
            await _wait_for_change()

        try:
            try:
                await self._wait_for_bridge(hue, settings.hue.bridge_ip or "")
                resolved = await resolve_bindings(settings, hue, _LOGGER, self._remembered)
            except HueApiError as err:
                self._status.set_bridge_reachability(False, str(err))
                self._status.set_service(ServiceState.ERROR, KEY_REJECTED if err.is_authentication_error else str(err))
                if err.is_authentication_error:
                    self._report_key_rejected()
                await _wait_for_change()

            if _is_blank(settings.hue.certificate_sha256):
                _LOGGER.warning(NOT_PINNED)
            self._report_resolved(resolved)
            bindings = resolved.bindings
            for binding in bindings:
                for problem in binding.problems:
                    _LOGGER.warning("%s", problem)

            self._status.set_running(bindings)
            observer = _Observer(self._status, self._report_key_rejected, self._observer)
            await Runner(settings, bindings, hue, observer, self._clock).run(self._listener)
        finally:
            await hue.close()

    def _report_key_rejected(self) -> None:
        """Runs the hook on the event loop's next turn, so that one that raises can't stop the supervisor or the
        runner."""
        if self._on_key_rejected is not None:
            asyncio.get_running_loop().call_soon(self._on_key_rejected)

    def _report_resolved(self, resolved: ResolvedSwitches) -> None:
        """Remembers what the resolution found, for the next one, and runs the hook on the event loop's next turn, as
        :meth:`_report_key_rejected` does."""
        self._remembered = resolved.remembered
        if self._on_resolved is not None:
            asyncio.get_running_loop().call_soon(self._on_resolved, resolved)

    async def _wait_for_bridge(self, hue: HueClient, bridge_ip: str) -> None:
        self._status.set_service(ServiceState.CONNECTING, f"Connecting to the Hue bridge at {bridge_ip}.")
        warned = False
        while True:
            try:
                await hue.ping()
            except HueApiError as err:
                if not err.is_transient:
                    raise
                self._status.set_bridge_reachability(False, str(err))
                self._status.set_service(ServiceState.CONNECTING, f"Waiting for the Hue bridge at {bridge_ip}: {err}")
                if not warned:
                    _LOGGER.warning(
                        "Hue bridge at %s not reachable (%s); retrying every %g s.", bridge_ip, err, BRIDGE_RETRY_DELAY
                    )
                    warned = True

                await asyncio.sleep(BRIDGE_RETRY_DELAY)
            else:
                self._status.set_bridge_reachability(True, None)
                return


class _Observer(RunnerObserver):
    """The runner's observer: every report goes to the status tracker, then to the supervisor's ``observer`` if it has
    one, and a rejected key to the supervisor's hook as well."""

    def __init__(
        self, status: StatusTracker, key_rejected: Callable[[], None], observer: RunnerObserver | None = None
    ) -> None:
        self._observers: tuple[RunnerObserver, ...] = (status,) if observer is None else (status, observer)
        self._key_rejected = key_rejected

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        for observer in self._observers:
            observer.on_press(binding, seq)

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        for observer in self._observers:
            observer.on_toggled(binding, result, elapsed)

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        for observer in self._observers:
            observer.on_toggle_failed(binding, message)

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        for observer in self._observers:
            observer.on_press_dropped(binding)

    def on_bridge_reachability(self, reachable: bool, message: str | None) -> None:
        for observer in self._observers:
            observer.on_bridge_reachability(reachable, message)

    def on_key_rejected(self) -> None:
        for observer in self._observers:
            observer.on_key_rejected()
        self._key_rejected()


async def _wait_for_change() -> NoReturn:
    """Waits until the supervisor is cancelled, which the runtime does for every change of the configuration."""
    while True:
        await asyncio.get_running_loop().create_future()


def _is_blank(value: str | None) -> bool:
    return not value or value.isspace()


def _problems_of(err: ConfigError) -> list[str]:
    """:class:`ConfigError` messages list one problem per "  - " line; a message without such lines is one problem."""
    lines = (line.strip() for line in str(err).split("\n"))
    problems = [line.removeprefix("- ") for line in lines if line.startswith("- ")]
    return problems or [str(err)]
