"""Prepares the configuration folder of the development Home Assistant: ``python -m tools.develop <folder>``.

``SONOFF2HUE_DEV_PORT`` sets Home Assistant's port; when it is set, the HTTP store is written again with it. Exits with
1 when the port is in use, rather than let Home Assistant start, fail to listen and fall back to its recovery mode.
"""

import os
from pathlib import Path
import sys

from . import HOST, port_is_free, prepare

USAGE = "Usage: python -m tools.develop <configuration folder>"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    port = None
    port_text = os.environ.get("SONOFF2HUE_DEV_PORT", "").strip()
    if port_text:
        if not (port_text.isascii() and port_text.isdigit() and 1 <= int(port_text) <= 65535):
            print(f"SONOFF2HUE_DEV_PORT must be a port number from 1 to 65535, not '{port_text}'.", file=sys.stderr)
            return 2
        port = int(port_text)

    folder = Path(argv[1])
    listening = prepare(folder, port)
    if not port_is_free(listening):
        print(
            f"Home Assistant's port {HOST}:{listening} is in use, by a Home Assistant that still runs or by another "
            "program. Stop it, or choose another port with SONOFF2HUE_DEV_PORT.",
            file=sys.stderr,
        )
        return 1
    print(f"Home Assistant: http://{HOST}:{listening}/, with its configuration folder {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
