"""Stand-ins in the tests that run the integration in Home Assistant (decision 23): the config entry's LAN on a fake
network, and lights in Home Assistant's state machine, switched by ``light`` actions of the tests' own (decision 40). The
``lan`` and ``lights`` fixtures put them in place."""

import asyncio
from collections.abc import Iterable, Mapping
import json
from typing import Any

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    CONF_TARGETS,
    DOMAIN,
    ENTRY_TITLE,
    ENTRY_VERSION,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.mdns import MdnsListener

from .engine.fakes import FakeNetwork
from .engine.packets import announcement

DEVICE_ID = "1000abcdef"
"""A made-up MINIR4M."""
OTHER_DEVICE_ID = "1000999999"
DEVICE_ADDRESS = "192.0.2.5"
DEVICE_KEY = "test-device-key"
ENCRYPTION_IV = bytes(range(16))
"""The IV of the broadcasts that :class:`Lan` encrypts. A device draws a random one; a fixed one makes a payload sent
under another switch's key fail the same way in every run. Under a wrong key, about one IV in 265 decrypts to valid
padding, and the decode error then says that the payload is not valid JSON instead of that decryption failed."""
CEILING = "light.ceiling"
"""The light of :func:`switch` by default, which :meth:`Lights.add` makes as ``lights.add("ceiling")``."""


class Lan:
    """The config entry's LAN: the listener's fake network, and the broadcasts of MINIR4Ms on it."""

    def __init__(self) -> None:
        self.network = FakeNetwork()
        self.listeners: list[MdnsListener] = []

    def listener(self, clock: Any) -> MdnsListener:
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


class Lights:
    """Lights in Home Assistant's state machine, with ``light.turn_on`` and ``light.turn_off`` actions of their own:
    each call sets the state of the light that it names, keeping its attributes, and is recorded in :attr:`calls`, in
    the order the calls came. A light can be made to fail, with Home Assistant's error, or to wait until the test lets
    it go."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[tuple[str, str]] = []
        """Each call: ``turn_on`` or ``turn_off``, and the light's entity id."""
        self._failing: dict[str, str] = {}
        self._held: dict[str, asyncio.Event] = {}
        for service in ("turn_on", "turn_off"):
            hass.services.async_register("light", service, self._handle)

    def add(
        self,
        object_id: str,
        *,
        on: bool = False,
        name: str | None = None,
        registered: bool = True,
        platform: str = "test",
        unique_id: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> str:
        """A light, ``light.<object_id>``, off unless ``on``, named ``name`` (else by its object id) and, unless
        ``registered`` is false, in the entity registry under ``platform`` with ``unique_id`` (else its object id).
        Returns its entity id."""
        entity_id = f"light.{object_id}"
        if registered:
            entity_id = (
                er.async_get(self.hass)
                .async_get_or_create(
                    "light", platform, unique_id or object_id, suggested_object_id=object_id, original_name=name
                )
                .entity_id
            )
        self.hass.states.async_set(
            entity_id, "on" if on else "off", {"friendly_name": name or object_id, **(attributes or {})}
        )
        return entity_id

    def target(self, entity_id: str) -> str:
        """How a switch's settings keep the light: its entity registry id."""
        entry = er.async_get(self.hass).async_get(entity_id)
        assert entry is not None, f"{entity_id} isn't in the entity registry"
        return entry.id

    def is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == "on"

    def turned(self) -> list[tuple[str, str]]:
        """The calls so far, and forgets them."""
        calls, self.calls = self.calls, []
        return calls

    def fail(self, entity_id: str, message: str = "Request failed: the light didn't answer") -> None:
        """Makes the calls for the light raise Home Assistant's error with ``message``, as the Hue integration does
        when the bridge refuses."""
        self._failing[entity_id] = message

    def heal(self) -> None:
        self._failing.clear()

    def hold(self, entity_id: str) -> asyncio.Event:
        """Keeps the calls for the light from ending until the event that it returns is set."""
        self._held[entity_id] = event = asyncio.Event()
        return event

    async def _handle(self, call: ServiceCall) -> None:
        for entity_id in _entity_ids(call.data.get("entity_id")):
            self.calls.append((call.service, entity_id))
            if (held := self._held.get(entity_id)) is not None:
                await held.wait()
            if (message := self._failing.get(entity_id)) is not None:
                raise HomeAssistantError(message)
            state = self.hass.states.get(entity_id)
            attributes = {} if state is None else dict(state.attributes)
            # As a light entity writes its new state, with the context of the action that changed it.
            self.hass.states.async_set(
                entity_id, "on" if call.service == "turn_on" else "off", attributes, context=call.context
            )


def _entity_ids(value: object) -> Iterable[str]:
    if isinstance(value, str):
        return [value]
    return [str(entity_id) for entity_id in value] if isinstance(value, list | tuple) else []


def switch(
    device_id: str = DEVICE_ID,
    name: str | None = "Hall",
    *,
    targets: list[str] | None = None,
    key: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> ConfigSubentryData:
    """A ``switch`` subentry, as the switch flow stores it, with ``light.ceiling`` unless ``targets`` says otherwise;
    ``extra`` adds to its data."""
    return ConfigSubentryData(
        data={
            CONF_DEVICE_ID: device_id,
            CONF_NAME: name,
            CONF_DEVICE_KEY: key,
            CONF_TARGETS: [CEILING] if targets is None else targets,
            **(extra or {}),
        },
        subentry_type=SUBENTRY_SWITCH,
        title=name or device_id,
        unique_id=device_id.strip().lower(),
    )


def entry(*switches: ConfigSubentryData) -> MockConfigEntry:
    """The config entry, with ``switches``."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=ENTRY_TITLE,
        data={},
        version=ENTRY_VERSION,
        minor_version=1,
        subentries_data=list(switches),
    )
