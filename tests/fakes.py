"""Stand-ins for the network in the tests that run the integration in Home Assistant (decision 23): the config entry's LAN
on a fake network, and a Hue bridge in memory. The ``lan`` and ``bridge`` fixtures make the runtime use them, in place
of ``runtime.new_listener`` and ``runtime.create_hue_client``."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import json
from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_ID,
    CONF_BRIDGE_IP,
    CONF_CERTIFICATE_SHA256,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_GROUPS,
    CONF_LIGHTS,
    CONF_NAME,
    DOMAIN,
    ENTRY_TITLE,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.config import HueSettings
from custom_components.sonoff2hue.engine.hue_client import HueClient
from custom_components.sonoff2hue.engine.mdns import MdnsListener
from custom_components.sonoff2hue.engine.supervisor import HueClientFactory

from .engine.fakes import FakeNetwork
from .engine.packets import announcement
from .hue import InMemoryBridge, RecordedRequest

DEVICE_ID = "1000abcdef"
"""A made-up MINIR4M."""
OTHER_DEVICE_ID = "1000999999"
DEVICE_ADDRESS = "192.0.2.5"
BRIDGE_ADDRESS = "bridge.test"
BRIDGE_ID = "001788FFFE000001"
APP_KEY = "app-key"
PIN = "AB" * 32
DEVICE_KEY = "test-device-key"
NO_ERRORS = '{"errors":[],"data":[]}'


class Lan:
    """The config entry's LAN: the listener's fake network, and the broadcasts of MINIR4Ms on it."""

    def __init__(self) -> None:
        self.network = FakeNetwork()
        self.listeners: list[MdnsListener] = []

    def listener(self, clock: Callable[[], datetime]) -> MdnsListener:
        """``runtime.new_listener``: a listener on the fake network."""
        listener = MdnsListener(self.network.open, self.network.watch, clock=clock)
        self.listeners.append(listener)
        return listener

    def broadcast(
        self,
        seq: int,
        device_id: str = DEVICE_ID,
        *,
        params: dict[str, Any] | None = None,
        key: str | None = None,
        iv: str = "iv",
    ) -> None:
        """A MINIR4M's state broadcast: ``params`` in plain text, or encrypted with ``key``; without either, an encrypted
        payload that nothing decrypts without its key."""
        if params is None:
            data, encrypted = f"payload{seq}", True
        elif key is None:
            data, encrypted = json.dumps(params, separators=(",", ":")), False
        else:
            data, iv = ewelink.encrypt(json.dumps(params, separators=(",", ":")), key)
            encrypted = True
        self.network.deliver(
            announcement(device_id, seq, data, iv, encrypted=encrypted, ip=DEVICE_ADDRESS), (DEVICE_ADDRESS, 5353)
        )


def relay(detached: bool) -> dict[str, Any]:
    """A MINIR4M's params, in detached relay mode or not."""
    return {"switches": [{"outlet": 0, "switch": "off"}], "relaySeparation": 1 if detached else 0}


class Bridge(InMemoryBridge):
    """A Hue bridge in memory, with the lights Ceiling (l1) and Desk (l2) and the room Hall (r1), which switches both
    through its grouped light g1. It records the requests, and how many clients were made for it and closed. ``hold``
    keeps the answers to requests for a path until the test releases them."""

    def __init__(self) -> None:
        super().__init__(self._answer)
        self.on = {"l1": False, "l2": False}
        self.created = 0
        self.closed = 0
        self._faults: list[list[Any]] = []
        self._held: dict[str, asyncio.Event] = {}

    def clients(self) -> HueClientFactory:
        """``runtime.create_hue_client``: clients for the configured address, key and pin, which reach this bridge."""

        def create(hue: HueSettings) -> HueClient:
            client = HueClient(hue.bridge_ip or "", hue.app_key or "", hue.certificate_sha256, self._transport)
            self.created += 1
            return client

        return create

    async def close(self) -> None:
        self.closed += 1

    def fail(self, path: str, status: int, body: str = "{}", count: int | None = None) -> None:
        """Answers the next ``count`` requests for ``path``, or all of them, with ``status`` and ``body``."""
        self._faults.append([path, status, body, count])

    def heal(self) -> None:
        self._faults.clear()

    def hold(self, path: str) -> asyncio.Event:
        """Keeps the answers to the requests for ``path`` until the event that it returns is set."""
        self._held[path] = event = asyncio.Event()
        return event

    def puts(self) -> list[tuple[str, bool]]:
        """Every PUT: the resource and the ``on`` it asked for."""
        return [(request.path.rsplit("/", 1)[-1], request.on) for request in self.requests if request.method == "PUT"]

    def _answer(self, request: RecordedRequest) -> tuple[int, str] | Awaitable[tuple[int, str]]:
        held = self._held.get(request.path)
        return self._now(request) if held is None or held.is_set() else self._later(held, request)

    async def _later(self, held: asyncio.Event, request: RecordedRequest) -> tuple[int, str]:
        await held.wait()
        return self._now(request)

    def _now(self, request: RecordedRequest) -> tuple[int, str]:
        for fault in self._faults:
            path, status, body, count = fault
            if path == request.path and count != 0:
                fault[3] = None if count is None else count - 1
                return status, body
        if request.method == "PUT":
            target = request.path.rsplit("/", 1)[-1]
            for light in ["l1", "l2"] if target == "g1" else [target]:
                self.on[light] = request.on
            return 200, NO_ERRORS
        data: list[dict[str, Any]] = {
            "/clip/v2/resource/bridge": [{"id": "b1", "type": "bridge"}],
            "/clip/v2/resource/light": [
                {"id": light, "metadata": {"name": name}, "on": {"on": self.on[light]}}
                for light, name in (("l1", "Ceiling"), ("l2", "Desk"))
            ],
            "/clip/v2/resource/room": [
                {"id": "r1", "metadata": {"name": "Hall"}, "services": [{"rid": "g1", "rtype": "grouped_light"}]}
            ],
            "/clip/v2/resource/zone": [],
            "/clip/v2/resource/grouped_light": [{"id": "g1", "on": {"on": any(self.on.values())}}],
        }[request.path]
        return 200, json.dumps({"errors": [], "data": data})


def switch(
    device_id: str = DEVICE_ID,
    name: str | None = "Hall",
    *,
    lights: list[str | None] | None = None,
    groups: list[str | None] | None = None,
    key: str | None = None,
) -> ConfigSubentryData:
    """A ``switch`` subentry, as the switch flow and the import store it."""
    return ConfigSubentryData(
        data={
            CONF_DEVICE_ID: device_id,
            CONF_NAME: name,
            CONF_DEVICE_KEY: key,
            CONF_LIGHTS: ["Ceiling"] if lights is None else lights,
            CONF_GROUPS: [] if groups is None else groups,
        },
        subentry_type=SUBENTRY_SWITCH,
        title=name or device_id,
        unique_id=device_id.strip().lower(),
    )


def paired(
    *switches: ConfigSubentryData,
    address: str = BRIDGE_ADDRESS,
    app_key: str | None = APP_KEY,
    pin: str | None = PIN,
    bridge_id: str = BRIDGE_ID,
) -> MockConfigEntry:
    """The config entry of a paired bridge, with ``switches``."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=ENTRY_TITLE,
        unique_id=bridge_id,
        data={CONF_BRIDGE_IP: address, CONF_BRIDGE_ID: bridge_id, CONF_APP_KEY: app_key, CONF_CERTIFICATE_SHA256: pin},
        subentries_data=list(switches),
    )
