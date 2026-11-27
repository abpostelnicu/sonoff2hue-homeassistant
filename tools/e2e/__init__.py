"""The end-to-end test (decision 27): a real Home Assistant with this checkout's integration and the simulated wall
switch (``tools/send-ewelink-announcements.py``), on this machine only, with the development configuration's lights:
template lights, which Home Assistant itself provides (``tools/develop/configuration.yaml``). ``scripts/e2e`` runs it,
and so does CI's *End-to-end* job.

It is the one test that runs the integration as Home Assistant loads it: its flows, the runtime, the mDNS socket and
Home Assistant's lights together, over real sockets and through restarts. It sets up Home Assistant as a user does,
through its API, and the integration as the frontend does: the setup flow adds the entry and opens the switch subentry
flow, which adds the switch, identified by a press, with two lights picked by their entity ids (decision 40). It
presses the switch and checks the lights, Home Assistant's bus, the integration's status and its entities. Then it
gives one light a new entity id, which the next press toggles under that id without restarting the switches, disables
the other, which a repair then names while the presses toggle the first alone, and restarts Home Assistant: the
switch's next broadcast, its last ``seq`` again, is no press, as a restart starts from a new baseline, while the press
after it still toggles. Last, with Home Assistant stopped, it makes the stored configuration sonoff2hue 1.x's, which
paired a Hue bridge itself: the next start migrates it, and the repair that it raises leads, as a user follows it, to
the switch's dialog, where the switch gets its lights again and toggles them.

Everything listens on 127.0.0.1, on a free port, and the switch's announcements have a multicast TTL of 0, so they loop
back to this machine and never reach a network. Home Assistant's configuration folder and the logs are in
``.dev/e2e``, which a pass removes and a failure keeps.
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

# The development configuration's lights (tools/develop/configuration.yaml): template lights, each kept on or off by an
# input boolean of its own, which keeps its state across restarts.
HALLWAY: Final = "light.hallway"
"""One of the switch's two lights, which the test gives a new entity id, ``RENAMED``, while Home Assistant runs."""
LIVING_ROOM: Final = "light.living_room"
"""The switch's other light, which the test disables while Home Assistant runs."""
PORCH: Final = "light.porch"
"""A light that no switch has, and which nothing switches."""
RENAMED: Final = "light.hallway_lamp"
"""``HALLWAY``'s entity id after the test renames it."""
LIGHTS: Final = (HALLWAY, LIVING_ROOM, PORCH)


def boolean_of(light: str) -> str:
    """The input boolean that keeps a development light on or off."""
    return light.replace("light.", "input_boolean.", 1)


# sonoff2hue 1.x's configuration, which the test makes of the stored one at the end, for the migration (decision 40).
BRIDGE_ID: Final = "001788fffe0e2e2e"
"""The Hue bridge that sonoff2hue 1.x paired itself: made up, as its address and key are."""
V1_LIGHTS: Final = ["Hallway"]
V1_GROUPS: Final = ["room:Living room"]
"""The switch's lights, rooms and zones as sonoff2hue 1.x kept them, by their names on the bridge. They are the
development lights' names, but those aren't the Hue integration's lights, so the migration finds no light for them."""

POLL_INTERVAL: Final = 0.1
"""Seconds between two looks at a state that nothing announces, such as a port that starts to answer."""


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
