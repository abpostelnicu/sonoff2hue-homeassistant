"""Runs the bridge whenever the configuration is complete, and reports why it can't run otherwise.

A :class:`BridgeSupervisor` runs one :class:`EngineConfig` (decision 21). It checks, in this order, that the bridge is
paired, that a switch is configured, that :meth:`EngineConfig.validate_for_run` passes and that a Hue client can be made
for the bridge's settings. Then it waits for the bridge, resolves the switches' lights, rooms and zones, and runs the
:class:`Runner` on the config entry's mDNS listener, which the LAN monitor runs and keeps running. Each switch is resolved
on its own: an entry that resolves to nothing is a problem of that switch, which toggles the others, and never stops
another switch (decision 32). While the runner runs, the supervisor resolves the switches again, every 60 s while one
of them has such an entry, else every 15 minutes, and at once after a toggle found a target gone, and hands the new
targets to the running runner. Each step is reported to the :class:`StatusTracker`, which is also the runner's observer.
A check that fails holds its state until the configuration changes; an unexpected failure starts the bridge again 30 s
later.

While the bridge is unreachable at its address, as the supervisor waits for it and as the runner reports it, the
supervisor looks for it on the network with its :class:`BridgeSearch` (decision 33): once it has been unreachable for
60 s, then at the search's rates, mDNS at most every 5 minutes and Signify's discovery endpoint at most every 15, until
it is reachable again. A bridge at another address with the paired bridge's id is the paired one only with the pinned
certificate, and is then reported to ``on_bridge_moved``, once; without a pin, its address goes to the status, which
the runtime turns into a repair. An address that is a host name is never replaced, so no search is made for it.

Use, for the Home Assistant runtime (decision 23): run :meth:`BridgeSupervisor.run` as a background task. For each
change of the configuration, call :meth:`BridgeSupervisor.config_changed`, cancel the task, and start a supervisor for
the new configuration once it has ended. Cancelling it stops the runner and the search and closes the Hue client, and
leaves the listener running. ``on_key_rejected`` is called whenever the bridge rejects the application key, while the
supervisor connects to it and while the runner runs, which is when the runtime starts Home Assistant's
reauthentication. ``observer`` gets the runner's reports after the status tracker, for the runtime's bus events.
``remembered`` and ``on_resolved`` follow renames (decision 32): the supervisor resolves with the resource that each
entry last found, and reports what each resolution found and which resources were renamed, which the runtime keeps and
saves. ``search`` and ``on_bridge_moved`` follow the bridge to a new address (decision 33): the runtime gives every
supervisor of its config entry the same search, and saves the address that it reports.
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import NoReturn, Protocol

from .config import ConfigError, EngineConfig, HueSettings
from .hue_address import create_base_url
from .hue_client import PAIR_AGAIN, HueApiError, HueBridgeInfo, HueClient, HueTarget, matches_pin
from .hue_setup import DiscoveredBridge, get_bridge_info
from .mdns import MdnsListener
from .runner import ResolvedSwitches, Runner, RunnerObserver, SwitchBinding, resolve_bindings, resolve_once
from .status import ServiceState, StatusTracker
from .targets import Remembered
from .toggler import ToggleResult

_LOGGER = logging.getLogger(__name__)

BRIDGE_RETRY_DELAY = 10.0
"""Seconds between the attempts to reach the bridge."""
FAILURE_RETRY_DELAY = 30.0
"""Seconds before the bridge is started again after an unexpected stop."""
TARGETS_CHECK_INTERVAL = 15 * 60.0
"""Seconds between the resolutions of the switches while the runner runs, which notice a rename (decision 32)."""
MISSING_TARGETS_CHECK_INTERVAL = 60.0
"""Seconds between them while a switch has an entry that resolves to nothing, which notice a fix within a minute."""
SEARCH_AFTER = 60.0
"""Seconds that the bridge is unreachable at its address before the supervisor looks for it on the network (decision
33)."""
MDNS_SEARCH_INTERVAL = 5 * 60.0
"""Seconds from one search with mDNS to the next, at the least."""
CLOUD_SEARCH_INTERVAL = 15 * 60.0
"""Seconds from one request to Signify's discovery endpoint to the next, at the least: Signify allows one request per
15 minutes per client."""
DUE_TOGETHER = 0.001
"""Seconds within which the sources that fall due are asked in one search. Their times are sums of the intervals on the
event loop's clock, in floating point, where the endpoint's 15 minutes and three of mDNS's 5 can round apart by a
fraction of a nanosecond."""

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


class BridgeFinder(Protocol):
    """Lists the bridges on the network, with mDNS, Signify's discovery endpoint or both, as ``hue_setup.discover``
    does."""

    def __call__(self, *, mdns: bool, cloud: bool) -> Awaitable[list[DiscoveredBridge]]: ...


type BridgeReader = Callable[[str], Awaitable[HueBridgeInfo]]
"""Reads the bridge at an address, its id and the SHA-256 of its certificate, without the application key, as
``hue_setup.get_bridge_info`` does; it raises :class:`HueApiError` when no Hue bridge answers there."""


class BridgeSearch:
    """How a supervisor looks for its bridge on the network (decision 33): ``find`` lists the bridges, and ``read``
    reads each one that may be it, without the application key. It keeps when each source was last asked, on the event
    loop's clock, so that mDNS is asked at most every 5 minutes and Signify's endpoint at most every 15, also across the
    supervisors that share it: the runtime makes one for its config entry, whose supervisors never run side by side."""

    def __init__(self, find: BridgeFinder, read: BridgeReader = get_bridge_info) -> None:
        self._find = find
        self.read = read
        self._mdns_at: float | None = None
        self._cloud_at: float | None = None

    def next_at(self, now: float) -> float:
        """When the next search may start, on the event loop's clock: ``now`` while a source is due."""
        return max(now, min(_due(self._mdns_at, MDNS_SEARCH_INTERVAL), _due(self._cloud_at, CLOUD_SEARCH_INTERVAL)))

    async def find(self, at: float) -> list[DiscoveredBridge]:
        """Asks the sources that are due at ``at``, a time that :meth:`next_at` gave, or within a millisecond of it, and
        counts ``at`` as their last search; the bridges that they list."""
        mdns = _due(self._mdns_at, MDNS_SEARCH_INTERVAL) <= at + DUE_TOGETHER
        cloud = _due(self._cloud_at, CLOUD_SEARCH_INTERVAL) <= at + DUE_TOGETHER
        if mdns:
            self._mdns_at = at
        if cloud:
            self._cloud_at = at
        if not mdns and not cloud:
            return []
        return await self._find(mdns=mdns, cloud=cloud)


def _due(asked_at: float | None, interval: float) -> float:
    """When a source asked at ``asked_at`` may be asked again; at any time if it never was."""
    return -math.inf if asked_at is None else asked_at + interval


@dataclass(frozen=True, slots=True)
class BridgeMoved:
    """The bridge of ``config`` answered at ``address``, another address than its own, with its bridge id and the pinned
    certificate (decision 33)."""

    config: EngineConfig
    address: str


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
        search: BridgeSearch | None = None,
        on_bridge_moved: Callable[[BridgeMoved], None] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        """``listener`` is the config entry's mDNS listener, which the runner shares and which keeps running when the
        supervisor stops. ``on_key_rejected`` runs on the event loop soon after the bridge rejected the application key:
        while the supervisor connects to it, which leaves it in the error state until the next change, and after each
        keep-alive request or toggle that the bridge refuses so while the runner runs, which goes on running. One that
        raises goes to the loop's exception handler. ``observer`` gets each of the runner's reports right after the
        status tracker. ``remembered`` is the resource that each entry last found, and ``on_resolved`` runs on the
        event loop soon after each resolution, with what it found and the renames, which the runtime remembers and
        saves (decision 32); one that raises goes to the loop's exception handler too. ``search`` looks for the bridge
        while it is unreachable at its address, without one it isn't looked for, and ``on_bridge_moved`` runs on the
        event loop soon after the search found it at another address with the pinned certificate, as ``on_resolved``
        does (decision 33). ``clock`` is the runner's, for the age of a press, and has to agree with the listener's."""
        self._config = config
        self._listener = listener
        self._status = status
        self._hue_clients = hue_clients
        self._on_key_rejected = on_key_rejected
        self._observer = observer
        self._remembered: Remembered = remembered or {}
        self._on_resolved = on_resolved
        self._search = search
        self._on_bridge_moved = on_bridge_moved
        self._clock = clock
        self._running = False
        self._waiting_after_failure = False
        self._search_task: asyncio.Task[None] | None = None
        """The search of the time that the bridge is unreachable at its address: its 60 s, then its searches."""
        self._ending_searches: set[asyncio.Task[None]] = set()
        """Searches that are cancelled and haven't ended yet."""
        self._looking = False
        """Whether the search looks for the bridge, its 60 s over."""
        self._waiting_error: str | None = None
        """While the supervisor waits for the bridge, why it doesn't answer, for the message that says so."""
        self._moved = False
        """Whether the search found the bridge with its pin, which this supervisor reports once."""
        self._warned: set[str] = set()
        """The addresses that the search warned about: once each."""

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

            self._status.set_running(resolved.bindings)
            check_now = asyncio.Event()
            observer = _Observer(
                self._status, self._report_key_rejected, self._observer, check_now.set, self._bridge_reachable
            )
            runner = Runner(settings, resolved.bindings, hue, observer, self._clock)
            # Eager: the runner adds its packet handler in this turn of the event loop, in which the status says that
            # it runs, so that from then on a broadcast reaches it.
            running = asyncio.get_running_loop().create_task(runner.run(self._listener), eager_start=True)
            try:
                await self._keep_targets_current(hue, runner, running, check_now, resolved)
            finally:
                running.cancel()
                await asyncio.wait([running])
        finally:
            await self._stop_search()
            await hue.close()

    async def _keep_targets_current(
        self,
        hue: HueClient,
        runner: Runner,
        running: asyncio.Task[None],
        check_now: asyncio.Event,
        resolved: ResolvedSwitches,
    ) -> None:
        """Resolves the switches again while the runner runs (decision 32): every 60 s while a switch has an entry that
        resolves to nothing, else every 15 minutes, and at once when ``check_now`` is set, after a toggle found targets
        gone. The new targets and problems go to the running runner and to the status, and the resolution is reported
        as at the start, renames included. A bridge error changes nothing. Returns when the runner returns, and raises
        what it raised."""
        running.add_done_callback(lambda _: check_now.set())
        while True:
            problems = any(binding.problems for binding in resolved.bindings)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(MISSING_TARGETS_CHECK_INTERVAL if problems else TARGETS_CHECK_INTERVAL):
                    await check_now.wait()
            if running.done():
                running.result()
                return

            check_now.clear()
            try:
                current = await resolve_once(self._config, hue, self._remembered)
            except HueApiError as err:
                _LOGGER.debug(
                    "The lights, rooms and zones stay as they were: the Hue bridge didn't list them (%s).", err
                )
                continue
            self._report_resolved(current, resolved)
            runner.update_targets(current.bindings)
            self._status.set_running(current.bindings)
            resolved = current

    def _report_key_rejected(self) -> None:
        """Runs the hook on the event loop's next turn, so that one that raises can't stop the supervisor or the
        runner."""
        if self._on_key_rejected is not None:
            asyncio.get_running_loop().call_soon(self._on_key_rejected)

    def _report_resolved(self, resolved: ResolvedSwitches, previous: ResolvedSwitches | None = None) -> None:
        """Warns about each problem that ``previous`` didn't have, remembers what the resolution found, for the next
        one, and runs the hook on the event loop's next turn, as :meth:`_report_key_rejected` does."""
        known = (
            set() if previous is None else {problem for binding in previous.bindings for problem in binding.problems}
        )
        for binding in resolved.bindings:
            for problem in binding.problems:
                if problem not in known:
                    _LOGGER.warning("%s", problem)
        self._remembered = resolved.remembered
        if self._on_resolved is not None:
            asyncio.get_running_loop().call_soon(self._on_resolved, resolved)

    async def _wait_for_bridge(self, hue: HueClient, bridge_ip: str) -> None:
        self._status.set_service(ServiceState.CONNECTING, f"Connecting to the Hue bridge at {bridge_ip}.")
        warned = False
        try:
            while True:
                try:
                    await hue.ping()
                except HueApiError as err:
                    if not err.is_transient:
                        raise
                    self._status.set_bridge_reachability(False, str(err))
                    self._waiting_error = str(err)
                    self._show_waiting()
                    if not warned:
                        _LOGGER.warning(
                            "Hue bridge at %s not reachable (%s); retrying every %g s.",
                            bridge_ip,
                            err,
                            BRIDGE_RETRY_DELAY,
                        )
                        warned = True
                    self._bridge_reachable(False)

                    await asyncio.sleep(BRIDGE_RETRY_DELAY)
                else:
                    self._status.set_bridge_reachability(True, None)
                    return
        finally:
            # The bridge answered, or the supervisor stops: either way, the search is over.
            self._waiting_error = None
            self._cancel_search()

    def _show_waiting(self) -> None:
        """While the supervisor waits for the bridge: why it doesn't answer, and whether the search looks for it."""
        if (error := self._waiting_error) is None:
            return
        message = f"Waiting for the Hue bridge at {self._config.hue.bridge_ip or ''}: {error}"
        if self._looking:
            message = f"{message.removesuffix('.')}; looking for it on the network."
        self._status.set_service(ServiceState.CONNECTING, message)

    # Following the bridge to a new address (decision 33).

    def _bridge_reachable(self, reachable: bool) -> None:
        """A report of the bridge's reachability at its address, as the supervisor waits for it or from the runner: a
        bridge that is unreachable starts the search, which looks for it 60 s later, unless it runs already, and one
        that is reachable ends it."""
        if reachable:
            self._cancel_search()
            return
        searching = self._search_task is not None and not self._search_task.done()
        if not searching and self._search is not None and self._can_search():
            self._search_task = asyncio.get_running_loop().create_task(self._look_for_bridge(self._search))

    def _can_search(self) -> bool:
        """Whether the supervisor looks for its bridge: by its bridge id, at an address that is an IP address, as a host
        name is never replaced, the system's resolver following it; and not after the search found the bridge with
        its pin, which it reports once."""
        hue = self._config.hue
        if self._moved or _is_blank(hue.bridge_id):
            return False
        try:
            return create_base_url(hue.bridge_ip or "").is_ip_address
        except ConfigError:
            return False

    async def _look_for_bridge(self, search: BridgeSearch) -> None:
        """Waits 60 s, then looks for the bridge at the search's rates, until it is cancelled, as the bridge is reachable
        again or the supervisor stops, or until it found the bridge with its pin. It logs one line when it begins to
        look, and nothing for a search that finds nothing."""
        await asyncio.sleep(SEARCH_AFTER)
        _LOGGER.info(
            "The Hue bridge at %s has been unreachable for %g s; looking for it on the network.",
            self._config.hue.bridge_ip,
            SEARCH_AFTER,
        )
        self._looking = True
        self._show_waiting()
        loop = asyncio.get_running_loop()
        while True:
            at = search.next_at(loop.time())
            if (delay := at - loop.time()) > 0:
                await asyncio.sleep(delay)
            try:
                if await self._recognize(search, await search.find(at)):
                    return
            except Exception:
                _LOGGER.exception("Looking for the Hue bridge on the network failed.")

    async def _recognize(self, search: BridgeSearch, bridges: list[DiscoveredBridge]) -> bool:
        """Reads the bridges that may be the paired one: at another address than its own, with its bridge id first, then
        those without an id, each read for its id without the application key. One with the paired bridge's id and the
        pinned certificate is reported once, which ends the search; one with another certificate is warned about. Without
        a pin, the first one with its id goes to the status, for the runtime's repair. Returns whether the bridge was
        found with its pin."""
        hue = self._config.hue
        bridge_id = (hue.bridge_id or "").casefold()
        own = _address_key(hue.bridge_ip or "")
        candidates = sorted(
            (bridge for bridge in bridges if bridge.bridge_id is None or bridge.bridge_id.casefold() == bridge_id),
            key=lambda bridge: bridge.bridge_id is None,
        )
        for bridge in candidates:
            address = bridge.ip
            if _address_key(address) in (None, own):
                continue
            try:
                info = await search.read(address)
            except HueApiError, ConfigError:
                continue
            if info.bridge_id.casefold() != bridge_id:
                continue
            if _is_blank(hue.certificate_sha256):
                self._status.set_bridge_found(address)
                self._warn_once(
                    address,
                    "The Hue bridge %s answers at %s instead of %s, but its certificate isn't pinned, so sonoff2hue "
                    "doesn't move there by itself; %s there.",
                    hue.bridge_id,
                    address,
                    hue.bridge_ip,
                    PAIR_AGAIN,
                )
                return False
            if matches_pin(info.certificate_sha256, hue.certificate_sha256 or ""):
                self._moved = True
                if self._on_bridge_moved is not None:
                    asyncio.get_running_loop().call_soon(self._on_bridge_moved, BridgeMoved(self._config, address))
                return True
            self._warn_once(
                address,
                "A device with the Hue bridge's id %s answers at %s, but its certificate isn't the pinned one, so "
                "sonoff2hue doesn't use that address. If the bridge was replaced or reset, %s.",
                hue.bridge_id,
                address,
                PAIR_AGAIN,
            )
        return False

    def _warn_once(self, address: str, message: str, *args: object) -> None:
        if address not in self._warned:
            self._warned.add(address)
            _LOGGER.warning(message, *args)

    def _cancel_search(self) -> None:
        """Ends the search without waiting for it, as the bridge is reachable at its address again."""
        task, self._search_task = self._search_task, None
        self._looking = False
        if task is not None and not task.done():
            task.cancel()
            self._ending_searches.add(task)
            task.add_done_callback(self._ending_searches.discard)

    async def _stop_search(self) -> None:
        """Ends the search and waits for it, and for those cancelled before, as the supervisor stops."""
        self._cancel_search()
        if self._ending_searches:
            await asyncio.wait(list(self._ending_searches))


class _Observer(RunnerObserver):
    """The runner's observer: every report goes to the status tracker, then to the supervisor's ``observer`` if it has
    one, a rejected key to the supervisor's hook as well, targets gone to ``targets_gone``, which checks the targets
    again, and the bridge's reachability to ``reachability``, which starts and ends the search for the bridge."""

    def __init__(
        self,
        status: StatusTracker,
        key_rejected: Callable[[], None],
        observer: RunnerObserver | None = None,
        targets_gone: Callable[[], None] | None = None,
        reachability: Callable[[bool], None] | None = None,
    ) -> None:
        self._observers: tuple[RunnerObserver, ...] = (status,) if observer is None else (status, observer)
        self._key_rejected = key_rejected
        self._targets_gone = targets_gone
        self._reachability = reachability

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
        if self._reachability is not None:
            self._reachability(reachable)

    def on_targets_gone(self, binding: SwitchBinding, targets: list[HueTarget]) -> None:
        for observer in self._observers:
            observer.on_targets_gone(binding, targets)
        if self._targets_gone is not None:
            self._targets_gone()

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


def _address_key(address: str) -> tuple[str, int] | None:
    """An address as the bridge's address compares: its host as shown and its port, so ``192.0.2.10`` and
    ``192.0.2.10:443`` are the same; ``None`` for one that can't be used."""
    try:
        url = create_base_url(address)
    except ConfigError:
        return None
    return url.host, url.port


def _problems_of(err: ConfigError) -> list[str]:
    """:class:`ConfigError` messages list one problem per "  - " line; a message without such lines is one problem."""
    lines = (line.strip() for line in str(err).split("\n"))
    problems = [line.removeprefix("- ") for line in lines if line.startswith("- ")]
    return problems or [str(err)]
