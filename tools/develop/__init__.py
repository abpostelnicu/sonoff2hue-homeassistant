"""The configuration folder of the Home Assistant that ``scripts/develop`` runs for development, without hardware.

``python -m tools.develop <folder>`` creates it: ``configuration.yaml`` from this package, once, and Home Assistant's
HTTP settings in ``.storage/http``, with its server on 127.0.0.1 only. Home Assistant 2026.11 keeps those settings in
that store and nowhere else: it moves an ``http:`` section of ``configuration.yaml`` there once, as a trial that it
reverts after five minutes, restarting, unless it is confirmed in its UI, and it raises a repair for as long as the
section stays. So the store is written before Home Assistant first starts, with the http integration's own schema and
its defaults for every other setting, as the store it would write itself.
"""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import socket
from typing import Any

from homeassistant.components.http import config as http_config
from homeassistant.components.http.const import CONF_SERVER_HOST, CONF_SERVER_PORT

HOST = "127.0.0.1"
DEFAULT_PORT = 8123
CONFIGURATION = Path(__file__).with_name("configuration.yaml")


def http_store(port: int) -> dict[str, Any]:
    """Home Assistant's HTTP store with its server on 127.0.0.1 and ``port``, confirmed (``stable``), and the migration
    of an ``http:`` section done."""
    config = dict(http_config.HTTP_STORAGE_SCHEMA({CONF_SERVER_HOST: [HOST], CONF_SERVER_PORT: port}))
    config |= {
        http_config.HTTP_CONFIG_CREATED_AT: datetime.now(UTC).isoformat(),
        http_config.HTTP_CONFIG_ERROR: None,
        http_config.HTTP_CONFIG_ERROR_MESSAGE: None,
    }
    return {
        "version": http_config.STORAGE_VERSION,
        "minor_version": http_config.STORAGE_MINOR_VERSION,
        "key": http_config.STORAGE_KEY,
        "data": {
            http_config.KEY_STABLE: config,
            http_config.KEY_PENDING: None,
            http_config.KEY_YAML_MIGRATION_DONE: True,
        },
    }


def prepare(folder: Path, port: int | None = None) -> int:
    """Creates ``folder`` with ``configuration.yaml`` and the HTTP store, where they are missing, and writes the store
    again when ``port`` is given; a ``configuration.yaml`` that exists is left as it is. Returns the port that Home
    Assistant will listen on."""
    folder.mkdir(parents=True, exist_ok=True)
    configuration = folder / "configuration.yaml"
    if not configuration.exists():
        shutil.copyfile(CONFIGURATION, configuration)

    store = folder / ".storage" / http_config.STORAGE_KEY
    if port is not None or not store.exists():
        store.parent.mkdir(exist_ok=True)
        # Private, as Home Assistant writes it.
        with os.fdopen(os.open(store, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
            json.dump(http_store(DEFAULT_PORT if port is None else port), file, indent=4)
    return listening_port(store)


def listening_port(store: Path) -> int:
    """The port in Home Assistant's HTTP store that it will listen on: a pending config's, which it tries first unless
    it failed, else the stable one's."""
    data = json.loads(store.read_text(encoding="utf-8"))["data"]
    pending = data[http_config.KEY_PENDING]
    config = pending if pending and pending[http_config.HTTP_CONFIG_ERROR] is None else data[http_config.KEY_STABLE]
    return int(config[CONF_SERVER_PORT])


def port_is_free(port: int) -> bool:
    """Whether a server can listen on 127.0.0.1 and ``port``, as Home Assistant's does (with SO_REUSEADDR)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True
