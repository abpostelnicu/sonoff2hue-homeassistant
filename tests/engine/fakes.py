"""Fakes of the network for the mDNS listener: transports that record what they send, and a network that opens them."""

import asyncio
from collections.abc import Callable
import errno


class FakeTransport:
    def __init__(self, interfaces: list[str], failing: set[str] | None = None) -> None:
        self._interfaces = interfaces
        self.failing = failing or set()
        self.sent: list[tuple[bytes, str]] = []
        self.closed = False

    @property
    def interfaces(self) -> list[str]:
        return self._interfaces

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
        transport = FakeTransport(["192.0.2.1", "198.51.100.1"], failing={"198.51.100.1"})
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
