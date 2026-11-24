"""Runs the fake Hue bridge: ``python -m tools.fake_hue`` from the repository root."""

import asyncio
from collections.abc import Mapping
import ipaddress
import logging
import os
import sys

from custom_components.sonoff2hue.engine import text

from .bridge import BRIDGE_ID, FakeBridge
from .server import HELP, start

DEFAULT_PORT = 18443

USAGE = """Usage: python -m tools.fake_hue

Settings, from the environment:
  FAKE_HUE_BIND         the address to listen on (default 127.0.0.1)
  FAKE_HUE_PORT         the HTTPS port (default 18443)
  FAKE_HUE_LINK_BUTTON  "released" (default): pairing answers error 101 until POST /_fake/link-button;
                        "pressed": pairing always succeeds
"""


class OptionsError(Exception):
    pass


def options(environment: Mapping[str, str]) -> tuple[str, int, bool]:
    """The address, port and link button from ``FAKE_HUE_*``, read as ``FakeHueOptions.FromEnvironment`` reads them;
    raises :class:`OptionsError`."""
    bind = "127.0.0.1"
    bind_text = environment.get("FAKE_HUE_BIND")
    if not text.is_null_or_white_space(bind_text):
        try:
            bind = str(ipaddress.ip_address(text.trim(bind_text or "")))
        except ValueError:
            raise OptionsError(
                f"FAKE_HUE_BIND must be an IP address, such as 127.0.0.1 or 0.0.0.0, not '{bind_text}'."
            ) from None

    port = DEFAULT_PORT
    port_text = environment.get("FAKE_HUE_PORT")
    if not text.is_null_or_white_space(port_text):
        number = text.try_parse_int(text.trim(port_text or ""), 32)
        if number is None or not 1 <= number <= 65535:
            raise OptionsError(f"FAKE_HUE_PORT must be a port number from 1 to 65535, not '{port_text}'.")
        port = number

    link_button = environment.get("FAKE_HUE_LINK_BUTTON")
    match None if link_button is None else text.lower_invariant(text.trim(link_button)):
        case None | "" | "released":
            pressed = False
        case "pressed":
            pressed = True
        case _:
            raise OptionsError(f"FAKE_HUE_LINK_BUTTON must be 'released' or 'pressed', not '{link_button}'.")
    return bind, port, pressed


async def main() -> int:
    if len(sys.argv) > 1:
        wants_help = sys.argv[1:] in (["help"], ["--help"], ["-h"])
        print(USAGE + "\n" + HELP, file=sys.stdout if wants_help else sys.stderr)
        return 0 if wants_help else 2
    try:
        bind, port, pressed = options(dict(os.environ))
    except OptionsError as err:
        print(err, file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("fake_hue")
    try:
        running = await start(FakeBridge(link_button_always_pressed=pressed), bind, port)
    except OSError as err:
        logger.error("The fake Hue bridge can't listen on %s:%s: %s", bind, port, err.strerror or err)  # noqa: TRY400
        return 1
    link_button = (
        "The link button is always pressed." if pressed else "Press its link button with POST /_fake/link-button."
    )
    logger.info(
        "Fake Hue bridge %s on https://%s/, certificate SHA-256 %s. %s Test endpoints: GET /_fake/.",
        BRIDGE_ID,
        running.address,
        running.fingerprint,
        link_button,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await running.stop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
