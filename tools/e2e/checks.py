"""How the end-to-end test reports its checks, as the .NET app's ``local.sh test`` does, and the integration's bus events
that it receives."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any


class Checks:
    """The checks, each reported as it is made, and the time each step took."""

    def __init__(self) -> None:
        self.count = 0
        self.failures = 0
        self.timings: list[tuple[str, float]] = []
        self._started = time.monotonic()

    def step(self, title: str) -> None:
        print(f"\n==> {title}", flush=True)

    def ok(self, what: str) -> None:
        self.count += 1
        print(f"  ok    {what}", flush=True)

    def fail(self, what: str, details: str | None = None) -> None:
        self.count += 1
        self.failures += 1
        print(f"  FAIL  {what}", flush=True)
        if details:
            for line in details.splitlines():
                print(f"        {line}", flush=True)

    def expect(self, what: str, passed: bool, details: str | None = None) -> bool:
        if passed:
            self.ok(what)
        else:
            self.fail(what, details)
        return passed

    def expect_equal(self, what: str, expected: object, actual: object) -> bool:
        return self.expect(what, actual == expected, f"expected: {expected}\ngot:      {actual}")

    def timed(self, what: str, started: float) -> float:
        """Records how long ``what`` took since ``started`` (``time.monotonic()``), and returns it."""
        elapsed = time.monotonic() - started
        self.timings.append((what, elapsed))
        return elapsed

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started


@dataclass(frozen=True, slots=True)
class Event:
    event_type: str
    data: dict[str, Any]


class Events:
    """The bus events that a subscription brought, in the order Home Assistant fired them."""

    def __init__(self) -> None:
        self.received: list[Event] = []
        self._changed = asyncio.Event()

    def add(self, event: dict[str, Any]) -> None:
        self.received.append(Event(event["event_type"], event["data"]))
        self._changed.set()

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [event.data for event in self.received if event.event_type == event_type]

    async def wait(self, condition: Callable[[list[Event]], bool]) -> None:
        """Waits until ``condition`` holds for the events received."""
        while not condition(self.received):
            self._changed.clear()
            await self._changed.wait()
