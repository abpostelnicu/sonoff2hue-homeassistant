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


def free_port() -> int:
    """A port on 127.0.0.1 that nothing listens on: each run takes a new one, so the test runs next to a development
    Home Assistant of ``scripts/develop``."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def prepare(work: Path) -> None:
    """Makes the work folder afresh, after deleting an earlier run's."""
    if work.exists():
        if not (work / MARKER).exists():
            raise SystemExit(f"{work} exists, but the end-to-end test didn't make it. Move it away, and run again.")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / MARKER).touch()


async def run(settings: Settings) -> bool:
    # CI cancels a job with SIGINT, then SIGTERM: either way, Home Assistant is stopped first.
    task = asyncio.current_task()
    if task is not None:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with aiohttp.ClientSession() as session:
        return await EndToEnd(settings, session).run()


def main() -> int:
    hass_port = free_port()
    prepare(WORK)
    try:
        passed = asyncio.run(run(Settings(ROOT, WORK, hass_port)))
    except KeyboardInterrupt, asyncio.CancelledError:
        print(f"\nInterrupted. Home Assistant was stopped; the logs are in {WORK}.")
        return 130
    if passed:
        shutil.rmtree(WORK)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
