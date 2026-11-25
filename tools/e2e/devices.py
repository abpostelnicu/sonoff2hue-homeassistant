"""The fake Hue bridge and the simulated wall switch of the end-to-end test, run as ``scripts/develop`` runs them."""

import asyncio
import contextlib
import os
from pathlib import Path
import signal
import sys

import aiohttp

from . import POLL_INTERVAL, StepError


class FakeHueBridge:
    """The fake Hue bridge, ``python -m tools.fake_hue``, in a process of its own on 127.0.0.1, with its link button
    released until the test presses it. Its ``/_fake/`` endpoints show what it was asked to switch."""

    def __init__(self, root: Path, port: int, log: Path, session: aiohttp.ClientSession) -> None:
        self.root = root
        self.port = port
        self.address = f"127.0.0.1:{port}"
        """Its address, as the setup flow takes it."""
        self.log = log
        self._session = session
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Starts it and waits until it answers."""
        environment = os.environ | {
            "FAKE_HUE_BIND": "127.0.0.1",
            "FAKE_HUE_PORT": str(self.port),
            "FAKE_HUE_LINK_BUTTON": "released",
        }
        with self.log.open("w", encoding="utf-8") as log:
            self._process = process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tools.fake_hue",
                cwd=self.root,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        while True:
            if process.returncode is not None:
                raise StepError(f"The fake Hue bridge exited with code {process.returncode}; see its log, {self.log}.")
            with contextlib.suppress(aiohttp.ClientConnectionError):
                await self.get("/_fake/")
                return
            await asyncio.sleep(POLL_INTERVAL)

    async def get(self, path: str) -> str:
        return await self._request("GET", path)

    async def post(self, path: str) -> str:
        return await self._request("POST", path)

    async def puts(self) -> list[str]:
        """Every PUT it got, in order: what each switched, such as ``light 'Hallway' on``."""
        return (await self.get("/_fake/puts")).splitlines()

    async def _request(self, method: str, path: str) -> str:
        # Its certificate is made at each start, and only the integration pins it.
        async with self._session.request(method, f"https://{self.address}{path}", ssl=False) as response:
            text = await response.text()
            if response.status >= 300:
                raise StepError(f"The fake Hue bridge answered {method} {path} with {response.status}: {text}")
            return text

    async def stop(self) -> None:
        """Stops it as Ctrl+C does."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGINT)
        try:
            async with asyncio.timeout(10):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()


class SimulatedSwitch:
    """The simulated MINIR4M: ``tools/send-ewelink-announcements.py``, the .NET app's script, which sends the eWeLink
    announcements of a device in DIY mode, 1.5 seconds apart. As ``scripts/develop press`` does, it sends them on the
    interface of the default route (``0.0.0.0``) with a multicast TTL of 0, so they loop back to this machine only."""

    def __init__(self, root: Path, device_id: str) -> None:
        self.root = root
        self.device_id = device_id
        self._process: asyncio.subprocess.Process | None = None
        self._output: asyncio.Task[None] | None = None

    async def announce(self, seqs: list[int]) -> None:
        """Starts sending an announcement for each ``seq``, in order, and shows each as it goes out."""
        self._process = process = await asyncio.create_subprocess_exec(
            sys.executable,
            "tools/send-ewelink-announcements.py",
            "0.0.0.0",  # noqa: S104 - the multicast interface: the default route's, as scripts/develop press has it
            self.device_id,
            *(str(seq) for seq in seqs),
            cwd=self.root,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._output = asyncio.get_running_loop().create_task(_show(process))

    async def done(self) -> None:
        """Ends the script, which only waits 1.5 seconds after its last announcement, once that has had its effect, and
        fails if it couldn't send."""
        process, output = self._process, self._output
        if process is None or output is None:
            return
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        code = await process.wait()
        await output
        if code not in {0, -signal.SIGTERM}:
            raise StepError(f"The simulated switch's script exited with code {code}.")


async def _show(process: asyncio.subprocess.Process) -> None:
    assert process.stdout is not None  # noqa: S101 - a pipe, as asked for
    async for line in process.stdout:
        print(f"        {line.decode(errors='replace').rstrip()}")
