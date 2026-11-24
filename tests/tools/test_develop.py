"""The development Home Assistant's configuration folder (tools/develop) and its packages (requirements_dev.txt)."""

import json
from pathlib import Path
import socket
import stat
from typing import Any

from homeassistant import components
from homeassistant.auth.mfa_modules import notify, totp
from homeassistant.components.http import config as http_config
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from tools import develop

ROOT = Path(__file__).parents[2]


async def test_home_assistant_serves_on_127_0_0_1_from_the_prepared_store(
    hass: HomeAssistant, hass_storage: dict[str, Any], tmp_path: Path
) -> None:
    assert develop.prepare(tmp_path / "config", 18123) == 18123
    store_file = tmp_path / "config" / ".storage" / "http"
    hass_storage[http_config.STORAGE_KEY] = json.loads(store_file.read_text(encoding="utf-8"))

    config = await http_config.async_load_config(hass, {})

    store = await http_config.async_get_and_load_store(hass)
    assert store.active_config_type is http_config.ActiveConfigType.STABLE
    assert (config["server_host"], config["server_port"]) == (["127.0.0.1"], 18123)
    # Home Assistant's own default settings, which its server reads, and the address.
    assert config.keys() == store.default.keys() | {"server_host"}
    assert not ir.async_get(hass).issues
    assert stat.S_IMODE(store_file.stat().st_mode) == 0o600


def test_prepare_keeps_the_configuration_and_writes_a_given_port_again(tmp_path: Path) -> None:
    folder = tmp_path / "config"
    assert develop.prepare(folder) == develop.DEFAULT_PORT
    configuration = folder / "configuration.yaml"
    assert configuration.read_text(encoding="utf-8") == develop.CONFIGURATION.read_text(encoding="utf-8")

    configuration.write_text("homeassistant:\n", encoding="utf-8")
    assert develop.prepare(folder) == develop.DEFAULT_PORT
    assert develop.prepare(folder, 18123) == 18123
    assert develop.prepare(folder) == 18123
    assert configuration.read_text(encoding="utf-8") == "homeassistant:\n"


def test_a_port_with_a_listener_is_not_free(socket_enabled: None) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert not develop.port_is_free(listener.getsockname()[1])


def test_requirements_dev_pins_what_home_assistant_requires() -> None:
    required: dict[str, set[str]] = {}
    manifests = Path(components.__file__).parent.glob("*/manifest.json")
    strings = [r for m in manifests for r in json.loads(m.read_text(encoding="utf-8")).get("requirements", [])]
    for string in [*strings, *totp.REQUIREMENTS, *notify.REQUIREMENTS]:
        requirement = Requirement(string)
        required.setdefault(canonicalize_name(requirement.name), set()).add(str(requirement.specifier))

    lines = (ROOT / "requirements_dev.txt").read_text(encoding="utf-8").splitlines()
    pins = [Requirement(text) for line in lines if (text := line.partition("#")[0].strip()) and text[0] != "-"]
    assert pins
    for pin in pins:
        assert required.get(canonicalize_name(pin.name)) == {str(pin.specifier)}, pin
