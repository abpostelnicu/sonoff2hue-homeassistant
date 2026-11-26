"""Fakes of the network for the mDNS listener: transports that record what they send, and a network that opens them."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import errno
from selectors import BaseSelector, SelectorKey


class FakeTransport:
    def __init__(
        self, interfaces: list[str], failing: set[str] | None = None, local_addresses: frozenset[str] | None = None
    ) -> None:
        self._interfaces = interfaces
        self._local_addresses = frozenset(interfaces) if local_addresses is None else local_addresses
        self.failing = failing or set()
        self.sent: list[tuple[bytes, str]] = []
        self.closed = False

    @property
    def interfaces(self) -> list[str]:
        return self._interfaces

    @property
    def local_addresses(self) -> frozenset[str]:
        return self._local_addresses

    def send(self, data: bytes, interface: str) -> None:
        if interface in self.failing:
            raise OSError(errno.ENETUNREACH, "Network is unreachable")
        self.sent.append((data, interface))

    def close(self) -> None:
        self.closed = True


class FakeNetwork:
    """Opens fake transports, or fails as told, and remembers the callbacks of the open one."""

    def __init__(self) -> None:
        self.failures: list[OSError] = []
        self.transports: list[FakeTransport] = []
        self.local_addresses: frozenset[str] | None = None  # the next transport's; by default its interfaces'
        self.opened = asyncio.Event()
        self.deliver: Callable[[bytes, tuple[str, int]], None] = lambda data, remote: None
        self.fail: Callable[[OSError], None] = lambda err: None
        self.network_changed: Callable[[], None] = lambda: None
        self.watching = False

    async def open(
        self, on_datagram: Callable[[bytes, tuple[str, int]], None], on_error: Callable[[OSError], None]
    ) -> FakeTransport:
        if self.failures:
            raise self.failures.pop(0)
        self.deliver, self.fail = on_datagram, on_error
        transport = FakeTransport(["192.0.2.1", "198.51.100.1"], {"198.51.100.1"}, self.local_addresses)
        self.transports.append(transport)
        self.opened.set()
        return transport

    def watch(self, on_change: Callable[[], None]) -> Callable[[], None]:
        self.network_changed = on_change
        self.watching = True

        def stop() -> None:
            self.watching = False

        return stop

    async def next_open(self) -> FakeTransport:
        await asyncio.wait_for(self.opened.wait(), 5)
        self.opened.clear()
        return self.transports[-1]


async def until(condition: Callable[[], bool], within: float = 5) -> None:
    """Waits until ``condition`` holds, checking every 10 ms, and fails after ``within`` seconds."""
    async with asyncio.timeout(within):
        while not condition():  # noqa: ASYNC110 - the state under test has no event to wait for
            await asyncio.sleep(0.01)


class VirtualTime:
    """Time on the running event loop that only moves when the loop would wait: it then jumps to the next timer, so
    sleeps of any length end at once and in order. Real I/O is still polled, and waited for when no timer is left.
    :meth:`utc` is a wall clock that moves with it, for the engine's ``clock`` parameters."""

    START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._time = loop.time
        self._selector: BaseSelector = loop._selector  # type: ignore[attr-defined]
        self._select = self._selector.select
        self.start = self.now = loop.time()
        loop.time = lambda: self.now  # type: ignore[method-assign]
        self._selector.select = self._jump  # type: ignore[method-assign]

    @property
    def elapsed(self) -> float:
        """Seconds since this virtual time started, to the nanosecond. The clock starts at the loop's real time, an
        arbitrary float, and each jump adds a timeout to it, so the difference can miss a round value by a few units in
        the last place, such as 899.9999999999999 for 900, depending on the machine's clock: rounding keeps the times
        that tests compare exact."""
        return round(self.now - self.start, 9)

    def utc(self) -> datetime:
        return self.START + timedelta(seconds=self.elapsed)

    def restore(self) -> None:
        self._loop.time = self._time  # type: ignore[method-assign]
        self._selector.select = self._select  # type: ignore[method-assign]

    def _jump(self, timeout: float | None = None) -> list[tuple[SelectorKey, int]]:
        events = self._select(0)
        if events or timeout == 0:
            return events
        if timeout is None:
            return self._select(None)
        self.now += timeout
        return events
