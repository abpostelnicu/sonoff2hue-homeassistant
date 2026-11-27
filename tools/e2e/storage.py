"""Home Assistant's storage, the files of its ``.storage`` folder, as the end-to-end test reads them, and rewrites them
while Home Assistant is stopped. A store's file holds its ``version``, ``minor_version`` and ``key``, and its ``data``.
Home Assistant writes them with a delay of its own, and every one that is due when it stops."""

import json
import os
from pathlib import Path
from typing import Any

CONFIG_ENTRIES = "core.config_entries"
DEVICE_REGISTRY = "core.device_registry"
ENTITY_REGISTRY = "core.entity_registry"


def path(config: Path, key: str) -> Path:
    """The file of the store ``key`` in Home Assistant's configuration folder ``config``."""
    return config / ".storage" / key


def read(config: Path, key: str) -> dict[str, Any]:
    """The store ``key``: its version, minor version, key and data."""
    store: dict[str, Any] = json.loads(path(config, key).read_text(encoding="utf-8"))
    return store


def write(config: Path, key: str, store: dict[str, Any]) -> None:
    """Writes the store ``key``, private as Home Assistant writes it, all at once: a file of its own, then renamed."""
    target = path(config, key)
    written = target.with_name(f"{target.name}.e2e")
    with os.fdopen(os.open(written, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
        json.dump(store, file, indent=2)
    written.replace(target)


def new(key: str, data: dict[str, Any], version: int = 1, minor_version: int = 1) -> dict[str, Any]:
    """A store as Home Assistant's ``Store`` writes it."""
    return {"version": version, "minor_version": minor_version, "key": key, "data": data}
