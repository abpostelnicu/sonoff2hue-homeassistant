"""The end-to-end test (decision 27): a real Home Assistant with this checkout's integration, the fake Hue bridge
(``tools/fake_hue``) and the simulated wall switch (``tools/send-ewelink-announcements.py``), all on this machine.
``scripts/e2e`` runs it, and so does CI's *End-to-end* job.

It is the one test that runs the integration as Home Assistant loads it: its flows, the runtime, the mDNS socket and
the Hue client together, over real sockets and through a restart. It sets up Home Assistant as a user does, through its
API, and the integration as the frontend does: the setup flow pairs the bridge, and the switch subentry flow adds the
switch, which a press identifies. It presses the switch twice and checks what reaches the bridge, Home Assistant's bus
and the integration's status. Then it renames the switch's light on the bridge, restarts Home Assistant, and checks that
the integration followed the rename and saved the new name in the switch, and that the switch's next broadcast, its last
``seq`` again, is no press, as a restart starts from a new baseline, while the press after it still toggles. Last, it
deletes the light: the next press toggles the room, the status runs on with the problem, and the repair says so
(decision 32).

Everything listens on 127.0.0.1, on free ports, and the switch's announcements have a multicast TTL of 0, so they loop
back to this machine and never reach a network. Home Assistant's configuration folder and the logs are in ``.dev/e2e``,
which a pass removes and a failure keeps.
"""

import asyncio
from collections.abc import AsyncIterator
import contextlib
import secrets
import time
from typing import Final

DEVICE_ID: Final = f"1000e2{secrets.token_hex(2)}"
"""The simulated MINIR4M's eWeLink device id: made up, and new at each run. The switch's announcements reach every
listener on this machine, so a test that runs at the same time, in another checkout, ignores this one's switch."""
SWITCH_NAME: Final = "End-to-end"
LIGHT: Final = "Hallway"
"""One of the fake bridge's lights, and its room, which the switch toggles."""
ROOM: Final = "Living room"
RENAMED_LIGHT: Final = "Hallway lamp"
"""The light's name after the test renames it on the bridge, while Home Assistant runs."""


def targets(light: str | None = LIGHT) -> list[str]:
    """The switch's targets as the bus events name them: the light, unless it is gone, and the room."""
    return [*([f"light '{light}'"] if light is not None else []), f"group '{ROOM}'"]


TARGETS: Final = targets()

POLL_INTERVAL: Final = 0.1
"""Seconds between two looks at a state that nothing announces, such as a port that starts to answer."""


def puts(on: bool, light: str = LIGHT) -> list[str]:
    """The fake bridge's record of one toggle's PUTs (``GET /_fake/puts``): the light, then the room's grouped light."""
    state = "on" if on else "off"
    return [f"light '{light}' {state}", f"room '{ROOM}' {state}"]


class StepError(Exception):
    """A step that the rest of the test depends on failed."""


@contextlib.asynccontextmanager
async def within(seconds: float, failure: str) -> AsyncIterator[None]:
    """Bounds a wait: raises :class:`StepError` with ``failure`` and the time it waited if the block takes longer than
    ``seconds``."""
    started = time.monotonic()
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError:
        raise StepError(f"{failure} within {time.monotonic() - started:.0f} s.") from None
