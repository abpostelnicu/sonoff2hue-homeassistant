"""Runs the switches whenever the configuration is complete, and reports why they can't run otherwise.

A :class:`Supervisor` runs one :class:`EngineConfig` (decision 21). It checks, in this order, that a switch is
configured and that :meth:`EngineConfig.validate_for_run` passes. Then it resolves the switches' lights with its
resolver, which looks them up in Home Assistant (decision 40), and runs the :class:`Runner` on the config entry's mDNS
listener, which the LAN monitor runs and keeps running. Each switch is resolved on its own: a light that Home Assistant
doesn't have is a problem of that switch, which toggles the others, and never stops another switch (decision 32).
When the runtime reports that the lights may have changed (:meth:`Supervisor.targets_changed`), the supervisor resolves
them again and hands them to the running runner. Each step is reported to the :class:`StatusTracker`, which is also the
runner's observer. A check that fails holds its state until the configuration changes; an unexpected failure starts the
switches again 30 s later.

Use, for the Home Assistant runtime (decision 23): run :meth:`Supervisor.run` as a background task. For each change of
the configuration, call :meth:`Supervisor.config_changed`, cancel the task, and start a supervisor for the new
configuration once it has ended. Cancelling it stops the runner and leaves the listener running. ``observer`` gets the
runner's reports after the status tracker, for the runtime's bus events.
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
import logging
from typing import NoReturn

from .config import ConfigError, EngineConfig
from .lights import LightControl, ToggleResult
from .mdns import MdnsListener
from .runner import Runner, RunnerObserver, SwitchBinding
from .status import ServiceState, StatusTracker

_LOGGER = logging.getLogger(__name__)

FAILURE_RETRY_DELAY = 30.0
"""Seconds before the switches are started again after an unexpected stop."""

NO_SWITCHES = "Add a switch: pick a MINIR4M and the lights it should toggle."

type Resolver = Callable[[EngineConfig], list[SwitchBinding]]
"""Resolves each switch of the configuration to the lights that it toggles, and why each of its other lights is left
out, in the configuration's order."""


def _now() -> datetime:
    return datetime.now(UTC)


class Supervisor:
    """Runs the switches for one configuration until cancelled, and reports each step to the status tracker."""

    def __init__(
        self,
        config: EngineConfig,
        listener: MdnsListener,
        status: StatusTracker,
        *,
        control: LightControl,
        resolve: Resolver,
        observer: RunnerObserver | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """``listener`` is the config entry's mDNS listener, which the runner shares and which keeps running when the
        supervisor stops. ``control`` switches the lights, and ``resolve`` finds them. ``observer`` gets each of the
        runner's reports right after the status tracker. ``clock`` is the runner's, for the age of a press, and has to
        agree with the listener's."""
        self._config = config
        self._listener = listener
        self._status = status
        self._control = control
        self._resolve = resolve
        self._observer = observer
        self._clock = clock
        self._running = False
        self._waiting_after_failure = False
        self._check_now = asyncio.Event()

    def config_changed(self) -> None:
        """Logs that the switches restart for a new configuration, while the supervisor runs, unless it is waiting to
        start again after an unexpected stop. The runtime then cancels this supervisor and starts one for the new
        configuration."""
        if self._running and not self._waiting_after_failure:
            _LOGGER.info("Configuration changed; restarting the switches.")

    def targets_changed(self) -> None:
        """The lights may have changed, as Home Assistant's entity registry did: while the runner runs, the switches are
        resolved again soon, once for any number of calls until then."""
        self._check_now.set()

    async def run(self) -> None:
        """Runs the switches until cancelled, and starts them again 30 s after an unexpected stop. When it ends, the
        runner has stopped; the listener goes on."""
        self._running = True
        try:
            while True:
                try:
                    await self._run()

                    # The runner only returns when cancelled; anything else is unexpected.
                    await asyncio.sleep(FAILURE_RETRY_DELAY)
                except Exception as err:
                    _LOGGER.exception(
                        "The switches stopped unexpectedly; restarting them in %g s.", FAILURE_RETRY_DELAY
                    )
                    self._status.set_service(ServiceState.ERROR, f"The switches stopped unexpectedly: {err}")
                    self._waiting_after_failure = True
                    try:
                        await asyncio.sleep(FAILURE_RETRY_DELAY)
                    finally:
                        self._waiting_after_failure = False
        finally:
            self._running = False

    async def _run(self) -> None:
        config = self._config
        if not config.switches:
            self._status.set_service(ServiceState.NOT_CONFIGURED, NO_SWITCHES)
            await _wait_for_change()

        try:
            config.validate_for_run()
        except ConfigError as err:
            self._status.set_service(ServiceState.INVALID_CONFIG, "The configuration has errors.", _problems_of(err))
            await _wait_for_change()

        self._check_now.clear()
        bindings = self._resolve(config)
        _warn_new(bindings, [])
        self._status.set_running(bindings)
        observers: tuple[RunnerObserver, ...] = (
            (self._status,) if self._observer is None else (self._status, self._observer)
        )
        runner = Runner(config, bindings, self._control, _Observer(observers), self._clock)
        # Eager: the runner adds its packet handler in this turn of the event loop, in which the status says that it
        # runs, so that from then on a broadcast reaches it.
        running = asyncio.get_running_loop().create_task(runner.run(self._listener), eager_start=True)
        try:
            await self._keep_targets_current(runner, running, bindings)
        finally:
            running.cancel()
            await asyncio.wait([running])

    async def _keep_targets_current(
        self, runner: Runner, running: asyncio.Task[None], bindings: list[SwitchBinding]
    ) -> None:
        """Resolves the switches again each time :meth:`targets_changed` was called, while the runner runs: the new
        lights and problems go to the running runner and to the status. Returns when the runner returns, and raises what
        it raised."""
        check_now = self._check_now
        running.add_done_callback(lambda _: check_now.set())
        while True:
            await check_now.wait()
            if running.done():
                running.result()
                return

            check_now.clear()
            current = self._resolve(self._config)
            _warn_new(current, bindings)
            runner.update_targets(current)
            self._status.set_running(current)
            bindings = current


def _warn_new(bindings: Sequence[SwitchBinding], previous: Sequence[SwitchBinding]) -> None:
    """Warns about each problem that ``previous`` didn't have."""
    known = {problem for binding in previous for problem in binding.problems}
    for binding in bindings:
        for problem in binding.problems:
            if problem not in known:
                _LOGGER.warning("%s", problem)


class _Observer(RunnerObserver):
    """The runner's observer: every report goes to the status tracker, then to the supervisor's ``observer`` if it has
    one."""

    def __init__(self, observers: tuple[RunnerObserver, ...]) -> None:
        self._observers = observers

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


async def _wait_for_change() -> NoReturn:
    """Waits until the supervisor is cancelled, which the runtime does for every change of the configuration."""
    while True:
        await asyncio.get_running_loop().create_future()


def _problems_of(err: ConfigError) -> list[str]:
    """:class:`ConfigError` messages list one problem per "  - " line; a message without such lines is one problem."""
    lines = (line.strip() for line in str(err).split("\n"))
    problems = [line.removeprefix("- ") for line in lines if line.startswith("- ")]
    return problems or [str(err)]
