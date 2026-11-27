"""The simulated wall switch of the end-to-end test, run as ``scripts/develop press`` runs it."""

import asyncio
import contextlib
from pathlib import Path
import signal
import sys

from . import StepError


class SimulatedSwitch:
    """The simulated MINIR4M: ``tools/send-ewelink-announcements.py``, which sends the eWeLink announcements of a
    device in DIY mode, 1.5 seconds apart. As ``scripts/develop press`` does, it sends them on the interface of the
    default route (``0.0.0.0``) with a multicast TTL of 0, so they loop back to this machine only."""

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
