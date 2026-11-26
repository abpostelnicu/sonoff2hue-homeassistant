"""The remembered resources (decision 32): the light, room or zone that each entry of the switches last found on the Hue
bridge, by its kind, id and name, so that a switch follows a rename.

They live in a Home Assistant store of the config entry's own, ``.storage/sonoff2hue.<entry id>``, tagged with the
bridge id, so that another bridge's ids are never used. The runtime loads them before it starts, gives them to each
bridge supervisor and to the switch test, and takes what each resolution found. They forget the switches and entries
that the configuration no longer has, and everything when the entry is paired with another bridge. A change is saved
10 seconds later, and at once when the entry unloads; Home Assistant writes a pending one when it stops.
:func:`async_remove` deletes the store with the entry. The switches' subentries and the ``sonoff2hue.yaml`` format stay
as they are.

The store's data: ``{"bridge_id": …, "resources": [{"switch": …, "field": …, "entry": …, "kind": …, "id": …,
"name": …}, …]}``, with the switch's normalized device id, ``lights`` or ``groups``, the entry as ``targets.name_key``
compares it, and the resource's kind (``light``, ``room`` or ``zone``), id and name. What can't be read is left out.
"""

from collections.abc import Mapping
import logging
from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .engine.config import EngineConfig
from .engine.targets import EntryKey, Field, Remembered, RememberedResource, ResourceKind, keys_of, name_key

_LOGGER = logging.getLogger(__name__)

STORE_VERSION: Final = 1
SAVE_DELAY: Final = 10.0
"""Seconds after a change that the store is written, so that the changes of a moment are written once."""

_KINDS: Final[dict[tuple[object, object], tuple[Field, ResourceKind]]] = {
    ("lights", "light"): ("lights", "light"),
    ("groups", "room"): ("groups", "room"),
    ("groups", "zone"): ("groups", "zone"),
}
"""The kinds of resource that the entries of each setting resolve to."""


def store_key(entry_id: str) -> str:
    """The key of a config entry's store, its file name in ``.storage``."""
    return f"{DOMAIN}.{entry_id}"


async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
    """Deletes a config entry's store, as the entry is removed."""
    await Store[dict[str, Any]](hass, STORE_VERSION, store_key(entry_id)).async_remove()


class RememberedResources:
    """The remembered resources of a config entry, for the bridge that it is paired with."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, store_key(entry_id), atomic_writes=True)
        self._bridge_id: str | None = None
        """The id of the bridge that the resources belong to; without one, none are kept."""
        self._resources: dict[EntryKey, RememberedResource] = {}
        self._pending = False

    @property
    def resources(self) -> Remembered:
        """The resource that each entry last found."""
        return self._resources

    async def async_load(self, config: EngineConfig) -> None:
        """Loads them for the configuration's bridge, before the runtime starts: those of another bridge, and of
        switches and entries that the configuration doesn't have, are left out."""
        try:
            data = await self._store.async_load()
        except HomeAssistantError as err:
            _LOGGER.warning("Can't read the remembered lights, rooms and zones (%s); starting without them.", err)
            data = None
        self._bridge_id = _bridge_id(config)
        self._resources = _read(data, self._bridge_id)
        self.config_changed(config)

    @callback
    def config_changed(self, config: EngineConfig) -> None:
        """Forgets the switches and entries that ``config`` doesn't have, and everything for another bridge."""
        bridge_id = _bridge_id(config)
        if not _same_bridge(bridge_id, self._bridge_id):
            self._replace(bridge_id, {})
            return
        keys = {key for switch in config.switches for key in keys_of(switch)}
        self._replace(bridge_id, {key: value for key, value in self._resources.items() if key in keys})

    @callback
    def update(self, resources: Mapping[EntryKey, RememberedResource], config: EngineConfig) -> None:
        """Takes what a resolution of ``config``, the current configuration, found."""
        self._replace(_bridge_id(config), dict(resources))

    @callback
    def remember(self, key: EntryKey, resource: RememberedResource) -> None:
        """Remembers one more entry's resource, such as a renamed resource's under its new entry."""
        if self._bridge_id is not None and self._resources.get(key) != resource:
            self._resources = {**self._resources, key: resource}
            self._schedule_save()

    async def async_flush(self) -> None:
        """Writes a change that is waiting to be saved now, as the entry unloads."""
        if self._pending:
            await self._store.async_save(self._data())

    def _replace(self, bridge_id: str | None, resources: dict[EntryKey, RememberedResource]) -> None:
        if bridge_id is None:
            resources = {}
        if bridge_id == self._bridge_id and resources == self._resources:
            return
        self._bridge_id = bridge_id
        self._resources = resources
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._bridge_id is not None:
            self._pending = True
            self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        """The store's data, as it is written."""
        self._pending = False
        return {
            "bridge_id": self._bridge_id,
            "resources": [
                {
                    "switch": key.device_id,
                    "field": key.field,
                    "entry": key.entry,
                    "kind": resource.kind,
                    "id": resource.id,
                    "name": resource.name,
                }
                for key, resource in self._resources.items()
            ],
        }


def _bridge_id(config: EngineConfig) -> str | None:
    """The configuration's bridge id, stripped, or ``None`` without one."""
    return (config.hue.bridge_id or "").strip() or None


def _same_bridge(first: object, second: str | None) -> bool:
    """Whether both are the same bridge id, without regard to case; ``None`` is no bridge."""
    return isinstance(first, str) and second is not None and first.strip().casefold() == second.casefold()


def _read(data: object, bridge_id: str | None) -> dict[EntryKey, RememberedResource]:
    """The resources in the store's data, if they belong to ``bridge_id``."""
    if not isinstance(data, dict) or not _same_bridge(data.get("bridge_id"), bridge_id):
        return {}
    records = data.get("resources")
    resources: dict[EntryKey, RememberedResource] = {}
    for record in records if isinstance(records, list) else []:
        if (item := _record(record)) is not None:
            resources[item[0]] = item[1]
    return resources


def _record(record: object) -> tuple[EntryKey, RememberedResource] | None:
    """A record of the store, or ``None`` if it isn't one."""
    if not isinstance(record, dict):
        return None
    switch, entry, resource_id, name = (record.get(key) for key in ("switch", "entry", "id", "name"))
    kinds = _KINDS.get((record.get("field"), record.get("kind")))
    if (
        kinds is None
        or not isinstance(switch, str)
        or not isinstance(entry, str)
        or not isinstance(resource_id, str)
        or not isinstance(name, str)
        or not switch.strip()
        or not resource_id
    ):
        return None
    field, kind = kinds
    return EntryKey(switch.strip().lower(), field, name_key(entry)), RememberedResource(kind, resource_id, name)
