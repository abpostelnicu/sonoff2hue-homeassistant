"""The fake Hue bridge over real HTTPS on 127.0.0.1, for tests (``tools/fake_hue``); a port of the .NET app's
``FakeHueTestHost``. Tests that use it need sockets: ``pytest.mark.usefixtures("socket_enabled")``."""

import json
from typing import Any, Self

import aiohttp

from custom_components.sonoff2hue.engine.hue_client import HueClient
from custom_components.sonoff2hue.engine.hue_setup import build_device_type
from tools.fake_hue.bridge import FakeBridge, Fault
from tools.fake_hue.server import RunningBridge, start


class ManualClock:
    """A monotonic clock that the test moves, in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeHueHost:
    """A fake bridge serving HTTPS on a free port, with a manual clock and a client for its own endpoints."""

    def __init__(self, running: RunningBridge, clock: ManualClock) -> None:
        self.running = running
        self.clock = clock
        self._session = aiohttp.ClientSession(
            base_url=f"https://{running.address}", connector=aiohttp.TCPConnector(ssl=False)
        )

    @classmethod
    async def start(cls, *, link_button_always_pressed: bool = False) -> Self:
        clock = ManualClock()
        bridge = FakeBridge(link_button_always_pressed=link_button_always_pressed, clock=clock)
        return cls(await start(bridge), clock)

    @property
    def address(self) -> str:
        """``127.0.0.1:<port>``, as sonoff2hue's bridge address."""
        return self.running.address

    @property
    def fingerprint(self) -> str:
        return self.running.fingerprint

    @property
    def bridge(self) -> FakeBridge:
        return self.running.bridge

    def fail(self, fault: Fault) -> None:
        """Answers the requests that ``fault`` matches with it instead."""
        self.bridge.faults.append(fault)

    def hue_client(self, app_key: str) -> HueClient:
        """sonoff2hue's Hue client for the fake bridge, with its certificate pinned."""
        return HueClient(self.address, app_key, self.fingerprint)

    async def request(self, method: str, path: str, body: Any = None, **kwargs: Any) -> tuple[int, str]:
        """Sends a request to the fake bridge; ``body`` is sent as JSON unless it is a string."""
        data = body if isinstance(body, str) or body is None else json.dumps(body)
        async with self._session.request(method, "/" + path, data=data, **kwargs) as response:
            return response.status, await response.text()

    async def request_app_key(self, *, generate_client_key: bool = True) -> Any:
        """Sends the pairing request that sonoff2hue sends and returns the bridge's answer."""
        status, text = await self.request(
            "POST", "api", {"devicetype": build_device_type(), "generateclientkey": generate_client_key}
        )
        assert status == 200
        return json.loads(text)

    async def pair(self) -> str:
        """Presses the link button, pairs, and returns the new application key."""
        status, _ = await self.request("POST", "_fake/link-button")
        assert status == 200
        answer = await self.request_app_key()
        app_key: str = answer[0]["success"]["username"]
        return app_key

    async def get_text(self, path: str) -> str:
        status, text = await self.request("GET", path)
        assert status == 200
        return text

    async def stop(self) -> None:
        await self._session.close()
        await self.running.stop()
