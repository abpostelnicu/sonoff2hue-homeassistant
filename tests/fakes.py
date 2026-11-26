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
ENCRYPTION_IV = bytes(range(16))
"""The IV of the broadcasts that :class:`Lan` encrypts. A device draws a random one; a fixed one makes a payload sent
under another switch's key fail the same way in every run. Under a wrong key, about one IV in 265 decrypts to valid
padding, and the decode error then says that the payload is not valid JSON instead of that decryption failed."""
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
        """A MINIR4M's state broadcast: ``params`` in plain text, or encrypted with ``key`` under
        :data:`ENCRYPTION_IV`; without either, an encrypted payload that nothing decrypts without its key."""
        if params is None:
            data, encrypted = f"payload{seq}", True
        elif key is None:
            data, encrypted = json.dumps(params, separators=(",", ":")), False
        else:
            data, iv = ewelink.encrypt(json.dumps(params, separators=(",", ":")), key, ENCRYPTION_IV)
            encrypted = True
        self.network.deliver(
            announcement(device_id, seq, data, iv, encrypted=encrypted, ip=DEVICE_ADDRESS), (DEVICE_ADDRESS, 5353)
        )


def relay(detached: bool) -> dict[str, Any]:
    """A MINIR4M's params, in detached relay mode or not."""
    return {"switches": [{"outlet": 0, "switch": "off"}], "relaySeparation": 1 if detached else 0}


class Bridge(InMemoryBridge):
    """A Hue bridge in memory, with the lights Ceiling (l1) and Desk (l2) and the room Hall (r1), which switches both
    through its grouped light g1, and the zones that ``POST /clip/v2/resource/zone`` creates, which it lists and gives
    by id. ``names`` holds the names of l1, l2 and r1, which a test may change, and ``deleted`` the ones that a test
    deleted, as the Hue app does: they aren't listed, nor among the zones' children, and their PUTs are answered 404. It
    records the requests, and how many clients were made for it and closed. ``hold`` keeps the answers to requests for
    a path until the test releases them."""

    def __init__(self) -> None:
        super().__init__(self._answer)
        self.on = {"l1": False, "l2": False}
        self.names = {"l1": "Ceiling", "l2": "Desk", "r1": "Hall"}
        self.deleted: set[str] = set()
        self.created = 0
        self.closed = 0
        self.zones: list[dict[str, Any]] = []
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

    def fail(
        self, path: str, status: int, body: str = "{}", count: int | None = None, method: str | None = None
    ) -> None:
        """Answers the next ``count`` requests for ``path``, or all of them, with ``status`` and ``body``; only those
        with ``method``, when it is given."""
        self._faults.append([path, status, body, count, method])

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

    def _zones(self) -> list[dict[str, Any]]:
        """The zones that it lists, and that a POST adds to."""
        return self.zones

    def _create_zone(self, request: RecordedRequest) -> str:
        """Adds a zone of the POST's children, named as its metadata says, with a grouped light; its id is z1, z2, …"""
        zones = self._zones()
        zone_id = f"z{len(zones) + 1}"
        body = json.loads(request.body or "")
        zones.append(
            {
                "id": zone_id,
                "type": "zone",
                "metadata": body["metadata"],
                "children": body["children"],
                "services": [{"rid": f"grouped-{zone_id}", "rtype": "grouped_light"}],
            }
        )
        return zone_id

    def _listed(self, zone: dict[str, Any]) -> dict[str, Any]:
        """A zone as it is listed: without the lights that a test deleted among its children."""
        return {**zone, "children": [child for child in zone["children"] if child["rid"] not in self.deleted]}

    def _now(self, request: RecordedRequest) -> tuple[int, str]:
        for fault in self._faults:
            path, status, body, count, method = fault
            if path == request.path and count != 0 and method in (None, request.method):
                fault[3] = None if count is None else count - 1
                return status, body
        if request.method == "POST" and request.path == "/clip/v2/resource/zone":
            return 200, json.dumps({"errors": [], "data": [{"rid": self._create_zone(request), "rtype": "zone"}]})
        if request.path.startswith("/clip/v2/resource/zone/"):
            zone_id = request.path.rsplit("/", 1)[-1]
            zones = [self._listed(zone) for zone in self._zones() if zone["id"] == zone_id]
            return 200, json.dumps({"errors": [], "data": zones})
        lights = [light for light in ("l1", "l2") if light not in self.deleted]
        room = "r1" not in self.deleted
        if request.method == "PUT":
            target = request.path.rsplit("/", 1)[-1]
            if target in self.deleted or (target == "g1" and not room):
                return 404, '{"errors":[{"description":"not found"}],"data":[]}'
            for light in lights if target == "g1" else [target]:
                self.on[light] = request.on
            return 200, NO_ERRORS
        data: list[dict[str, Any]] = {
            "/clip/v2/resource/bridge": [{"id": "b1", "type": "bridge"}],
            "/clip/v2/resource/light": [
                {"id": light, "metadata": {"name": self.names[light]}, "on": {"on": self.on[light]}} for light in lights
            ],
            "/clip/v2/resource/room": [
                {
                    "id": "r1",
                    "metadata": {"name": self.names["r1"]},
                    "services": [{"rid": "g1", "rtype": "grouped_light"}],
                }
            ]
            if room
            else [],
            "/clip/v2/resource/zone": [self._listed(zone) for zone in self._zones()],
            "/clip/v2/resource/grouped_light": [{"id": "g1", "on": {"on": any(self.on[light] for light in lights)}}]
            if room
            else [],
        }[request.path]
        return 200, json.dumps({"errors": [], "data": data})


def switch(
    device_id: str = DEVICE_ID,
    name: str | None = "Hall",
    *,
    lights: list[str] | None = None,
    groups: list[str] | None = None,
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
