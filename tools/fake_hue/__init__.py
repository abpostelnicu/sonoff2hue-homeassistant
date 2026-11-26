"""A fake Philips Hue bridge for tests and local runs, without hardware.

Three lights, each owned by a device, a room of devices and a zone of lights, with exactly the Hue API calls that
sonoff2hue makes, over HTTPS with a certificate made at every start, and plain-text test endpoints under ``/_fake/``.
Run it with ``python -m tools.fake_hue``; ``FAKE_HUE_BIND``, ``FAKE_HUE_PORT`` and ``FAKE_HUE_LINK_BUTTON`` set it up.
"""
