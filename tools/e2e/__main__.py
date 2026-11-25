"""Runs the end-to-end test: ``python -m tools.e2e`` from the repository root, as ``scripts/e2e`` does. Exits with 0 when
every check passed, else 1."""

import asyncio
from pathlib import Path
import shutil
import signal
import socket
import sys

import aiohttp

from .scenario import EndToEnd, Settings

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".dev" / "e2e"
MARKER = ".sonoff2hue-e2e"
"""Marks the work folder as the test's own, which it may delete."""


def free_ports(count: int) -> list[int]:
    """Ports on 127.0.0.1 that nothing listens on, all different: each run takes new ones, so the test runs next to a
    development Home Assistant and fake bridge of ``scripts/develop``."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def prepare(work: Path) -> None:
    """Makes the work folder afresh, after deleting an earlier run's."""
    if work.exists():
        if not (work / MARKER).exists():
            raise SystemExit(f"{work} exists, but the end-to-end test didn't make it. Move it away, and run again.")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / MARKER).touch()


async def run(settings: Settings) -> bool:
    # CI cancels a job with SIGINT, then SIGTERM: either way, Home Assistant and the fake bridge are stopped first.
    task = asyncio.current_task()
    if task is not None:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with aiohttp.ClientSession() as session:
        return await EndToEnd(settings, session).run()


def main() -> int:
    hass_port, fake_hue_port = free_ports(2)
    prepare(WORK)
    try:
        passed = asyncio.run(run(Settings(ROOT, WORK, hass_port, fake_hue_port)))
    except KeyboardInterrupt, asyncio.CancelledError:
        print(f"\nInterrupted. Home Assistant and the fake Hue bridge were stopped; the logs are in {WORK}.")
        return 130
    if passed:
        shutil.rmtree(WORK)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
