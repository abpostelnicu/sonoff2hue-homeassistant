"""A fake Philips Hue bridge for tests and local runs, without hardware.

A port of the .NET app's ``tests/local/FakeHue``: three lights, a room and a zone, with exactly the Hue API calls that
sonoff2hue makes, over HTTPS with a certificate made at every start, and plain-text test endpoints under ``/_fake/``.
Run it with ``python -m tools.fake_hue``; ``FAKE_HUE_BIND``, ``FAKE_HUE_PORT`` and ``FAKE_HUE_LINK_BUTTON`` set it up.
"""
