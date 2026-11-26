"""Hue bridges for tests: the fake bridge of ``tools/fake_hue`` over real HTTPS on 127.0.0.1, which needs sockets
(``pytest.mark.usefixtures("socket_enabled")``), and a bridge in memory without them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from typing import Any, Self

import aiohttp

from custom_components.sonoff2hue.engine.hue_address import BridgeUrl
from custom_components.sonoff2hue.engine.hue_client import HttpAnswer, HueClient
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
    async def start(
        cls, *, link_button_always_pressed: bool = False, certificate: tuple[bytes, bytes] | None = None
    ) -> Self:
        """A new fake bridge, with a new certificate, or with ``certificate``, a certificate and its key in PEM
        (``create_certificate()``), which another fake bridge may present too."""
        clock = ManualClock()
        bridge = FakeBridge(link_button_always_pressed=link_button_always_pressed, clock=clock)
        return cls(await start(bridge, certificate=certificate), clock)

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


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    path: str
    body: str | None
    headers: dict[str, str]

    @property
    def on(self) -> bool:
        """The ``on.on`` of a PUT's body, which must be exactly ``{"on":{"on":<bool>}}``."""
        body = json.loads(self.body or "")
        assert list(body) == ["on"]
        assert list(body["on"]) == ["on"]
        on: bool = body["on"]["on"]
        return on


type Respond = Callable[[RecordedRequest], tuple[int, str] | Awaitable[tuple[int, str]]]


class InMemoryBridge:
    """A stand-in for the Hue bridge without sockets: every request gets the status and body that ``respond`` returns
    for it, and is recorded."""

    def __init__(self, respond: Respond) -> None:
        self._respond = respond
        self.requests: list[RecordedRequest] = []

    def client(self, app_key: str = "app-key") -> HueClient:
        return HueClient("bridge.test", app_key, None, self._transport)

    def _transport(self, url: BridgeUrl, pin: str | None, report: Any) -> InMemoryBridge:
        return self

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> HttpAnswer:
        request = RecordedRequest(method, "/" + path, None if body is None else body.decode(), dict(headers or {}))
        self.requests.append(request)
        answer = self._respond(request)
        status, text = answer if isinstance(answer, tuple) else await answer
        return HttpAnswer(status, None, text.encode())

    async def close(self) -> None:
        pass
