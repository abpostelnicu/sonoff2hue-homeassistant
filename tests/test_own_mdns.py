"""The integration listens with its own mDNS socket and never relies on Home Assistant's mDNS (decision 30).

No module of the integration imports python-zeroconf, Home Assistant's zeroconf or network integration, or the resolver
that answers ``.local`` names through Home Assistant's zeroconf; the engine imports nothing from Home Assistant; and the
manifest has no discovery matcher that Home Assistant's zeroconf serves and depends on neither integration. The modules
are read with ``ast``, not imported, and the tests at the end show that the checks find each form they look for.
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parents[1]
_INTEGRATION = _ROOT / "custom_components" / "sonoff2hue"
_ENGINE = _INTEGRATION / "engine"
_PACKAGE = "custom_components.sonoff2hue"
_ENGINE_PACKAGE = f"{_PACKAGE}.engine"

_MDNS_MODULES = (
    "zeroconf",
    "homeassistant.components.zeroconf",
    "homeassistant.components.network",
    "aiohttp_asyncmdnsresolver",
)
_MDNS_INTEGRATIONS = frozenset({"zeroconf", "network"})
# The discovery matchers of a manifest that Home Assistant's zeroconf integration serves.
_MDNS_MATCHERS = ("zeroconf", "homekit")


def _imported_modules(source: str, package: str) -> list[str]:
    """Every module that ``source`` imports, by its absolute name. ``from x import y`` imports ``x``, and ``x.y`` as
    well, since ``y`` may be a submodule. Relative imports are taken from ``package``, the module's own package.
    ``importlib.import_module`` and ``__import__`` with a literal name count too."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Import(names=names):
                modules.extend(alias.name for alias in names)
            case ast.ImportFrom(module=module, names=names, level=level):
                base = package.rsplit(".", level - 1)[0] if level else ""
                imported = ".".join(part for part in (base, module) if part)
                modules.append(imported)
                modules.extend(f"{imported}.{alias.name}" for alias in names if alias.name != "*")
            case ast.Call(
                func=ast.Name(id="__import__") | ast.Attribute(attr="import_module"),
                args=[ast.Constant(value=str() as name), *_],
            ):
                modules.append(name)
    return modules


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _mdns_imports(source: str, package: str = _PACKAGE) -> list[str]:
    """The modules of Home Assistant's mDNS that ``source`` imports."""
    modules = _imported_modules(source, package)
    return [module for module in modules if any(_within(module, mdns) for mdns in _MDNS_MODULES)]


def _home_assistant_imports(source: str, package: str = _ENGINE_PACKAGE) -> list[str]:
    """The modules of Home Assistant that ``source`` imports."""
    return [module for module in _imported_modules(source, package) if _within(module, "homeassistant")]


def _manifest_problems(manifest: dict[str, Any]) -> list[str]:
    """What in a manifest relies on Home Assistant's mDNS: a matcher, or a dependency on its zeroconf or network."""
    problems = [f"the matcher {key!r}" for key in _MDNS_MATCHERS if key in manifest]
    problems += [
        f"{key}: {name}"
        for key in ("dependencies", "after_dependencies")
        for name in manifest.get(key, [])
        if name in _MDNS_INTEGRATIONS
    ]
    return problems


def _modules(folder: Path) -> list[tuple[str, str, str]]:
    """Each module under ``folder``: its path, its source and its package."""
    return [
        (
            path.relative_to(_ROOT).as_posix(),
            path.read_text(encoding="utf-8"),
            ".".join(path.relative_to(_ROOT).parent.parts),
        )
        for path in sorted(folder.rglob("*.py"))
    ]


def test_no_module_imports_home_assistants_mdns() -> None:
    modules = _modules(_INTEGRATION)

    assert {"custom_components/sonoff2hue/runtime.py", "custom_components/sonoff2hue/engine/mdns.py"} <= {
        path for path, _, _ in modules
    }
    assert [f"{path}: {module}" for path, source, package in modules for module in _mdns_imports(source, package)] == []


def test_the_engine_imports_nothing_from_home_assistant() -> None:
    modules = _modules(_ENGINE)

    assert {"custom_components/sonoff2hue/engine/dns.py", "custom_components/sonoff2hue/engine/mdns.py"} <= {
        path for path, _, _ in modules
    }
    assert [
        f"{path}: {module}" for path, source, package in modules for module in _home_assistant_imports(source, package)
    ] == []


def test_the_manifest_relies_on_no_mdns_of_home_assistants() -> None:
    manifest = json.loads((_INTEGRATION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["domain"] == "sonoff2hue"
    assert _manifest_problems(manifest) == []


# The checks themselves


@pytest.mark.parametrize(
    "source",
    [
        "import zeroconf",
        "import zeroconf.asyncio",
        "import zeroconf as zc",
        "import ifaddr, zeroconf",
        "from zeroconf import ServiceBrowser",
        "from zeroconf.asyncio import AsyncZeroconf",
        "import homeassistant.components.zeroconf",
        "import homeassistant.components.zeroconf as zc",
        "from homeassistant.components.zeroconf import async_get_async_instance",
        "from homeassistant.components import zeroconf",
        "from homeassistant.components import sensor, zeroconf",
        "import homeassistant.components.network",
        "from homeassistant.components.network import async_get_adapters",
        "from homeassistant.components import network",
        "from homeassistant.components import network as net",
        "import aiohttp_asyncmdnsresolver",
        "from aiohttp_asyncmdnsresolver.api import AsyncMDNSResolver",
        "def browse():\n    import zeroconf\n",
        "if TYPE_CHECKING:\n    from zeroconf import Zeroconf\n",
        "try:\n    import zeroconf\nexcept ImportError:\n    zeroconf = None\n",
        "importlib.import_module('homeassistant.components.zeroconf')",
        "__import__('zeroconf')",
    ],
)
def test_each_form_of_an_mdns_import_is_found(source: str) -> None:
    assert _mdns_imports(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "import ifaddr",
        "import zeroconfig",
        "from homeassistant.components.sensor import SensorEntity",
        "from homeassistant.components import repairs",
        "from .engine.mdns import MdnsListener",
        "from . import zeroconf",  # a module of the integration's own
        "importlib.import_module(name)",
    ],
)
def test_other_imports_are_not_mdns_imports(source: str) -> None:
    assert _mdns_imports(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "import homeassistant",
        "import homeassistant.core",
        "import homeassistant.util.dt as dt_util",
        "from homeassistant.core import HomeAssistant",
        "from homeassistant import config_entries",
        "if TYPE_CHECKING:\n    from homeassistant.core import HomeAssistant\n",
    ],
)
def test_each_form_of_a_home_assistant_import_is_found(source: str) -> None:
    assert _home_assistant_imports(source) != []


def test_relative_imports_are_taken_from_the_modules_package() -> None:
    assert _imported_modules("from . import dns", _ENGINE_PACKAGE) == [_ENGINE_PACKAGE, f"{_ENGINE_PACKAGE}.dns"]
    assert _imported_modules("from ..const import DOMAIN", _ENGINE_PACKAGE) == [
        f"{_PACKAGE}.const",
        f"{_PACKAGE}.const.DOMAIN",
    ]
    assert _home_assistant_imports("import asyncio\nimport homeassistantlike\nfrom .dns import DnsMessage") == []


@pytest.mark.parametrize(
    "manifest",
    [
        {"zeroconf": ["_hue._tcp.local."]},
        {"zeroconf": [{"type": "_ewelink._tcp.local."}]},
        {"homekit": {"models": ["BSB002"]}},
        {"dependencies": ["zeroconf"]},
        {"dependencies": ["http", "network"]},
        {"after_dependencies": ["zeroconf"]},
        {"after_dependencies": ["network"]},
    ],
)
def test_each_reliance_of_a_manifest_on_home_assistants_mdns_is_found(manifest: dict[str, Any]) -> None:
    assert _manifest_problems(manifest) != []


def test_a_manifest_without_a_reliance_on_home_assistants_mdns() -> None:
    manifest = {"connectivity": ["network"], "dependencies": ["http"], "after_dependencies": ["repairs"], "dhcp": []}

    assert _manifest_problems(manifest) == []
