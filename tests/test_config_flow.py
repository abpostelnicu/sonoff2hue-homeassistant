"""The setup flow, reconfigure and reauth (decision 22): every step, error, abort and retry.

Pairing runs over HTTPS against the fake Hue bridge on 127.0.0.1. The search, which needs the network, has a stand-in,
and so has the pairing loop where a test answers its attempts one by one. Every result is checked against
``translations/en.json``: the texts it shows must exist, with the placeholders they use.
"""

import asyncio
from collections.abc import Iterator
import functools
import json
import logging
import string
from typing import Any
from unittest.mock import ANY, AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import EVENT_DATA_ENTRY_FLOW_PROGRESSED, FlowResultType, UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_capture_events
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import config_flow
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
from custom_components.sonoff2hue.engine import hue_setup, supervisor, yaml_import
from custom_components.sonoff2hue.engine.config import ConfigError
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.hue_client import AiohttpTransport, HueApiError, HueBridgeInfo
from custom_components.sonoff2hue.engine.hue_setup import (
    DiscoveredBridge,
    PairingResult,
    PairingStatus,
    PairingTimeoutError,
)
from tools.fake_hue.bridge import BRIDGE_ID, NAME, FakeBridge, Fault

from .engine.fakes import until
from .engine.test_hue_client import closed_port
from .hue import FakeHueHost

OTHER_ID = "001788FFFE000099"
NOT_PRESSED = PairingResult(PairingStatus.LINK_BUTTON_NOT_PRESSED, None)
NO_ANSWER = PairingResult(
    PairingStatus.REQUEST_FAILED, None, "bridge not reachable: Connection refused (192.0.2.10:443)"
)
BUSY = PairingResult(PairingStatus.REQUEST_FAILED, None, "unexpected response (HTTP 503): busy")
PAIRED = PairingResult(PairingStatus.PAIRED, "new-app-key")
TIMEOUT = "The link button was not pressed within 60 seconds. Try again."
SWITCH = ConfigSubentryData(
    data={
        CONF_DEVICE_ID: "1000abcdef",
        CONF_NAME: "Hallway switch",
        CONF_DEVICE_KEY: None,
        CONF_LIGHTS: ["Hallway"],
        CONF_GROUPS: [],
    },
    subentry_type=SUBENTRY_SWITCH,
    title="Hallway switch",
    unique_id="1000abcdef",
)


# Driving the flow, and checking its texts.


async def shown(hass: HomeAssistant, result: ConfigFlowResult) -> ConfigFlowResult:
    """``result``, once Home Assistant has found every text it shows, with the placeholders those use."""
    domain = result.get("translation_domain") or DOMAIN
    texts = await async_get_translations(hass, "en", "config", [domain])
    prefix = f"component.{domain}.config"
    step = f"{prefix}.step.{result.get('step_id')}"
    keys: list[str] = []
    match result["type"]:
        case FlowResultType.FORM:
            schema = result["data_schema"]
            keys += [f"{step}.title", *(f"{step}.data.{field}" for field in (schema.schema if schema else ()))]
            keys += [f"{prefix}.error.{error}" for error in (result["errors"] or {}).values()]
        case FlowResultType.MENU:
            keys += [f"{step}.title", *(f"{step}.menu_options.{option}" for option in result["menu_options"])]
        case FlowResultType.SHOW_PROGRESS:
            keys += [f"{step}.title", f"{prefix}.progress.{result['progress_action']}"]
        case FlowResultType.ABORT:
            keys.append(f"{prefix}.abort.{result['reason']}")
        case FlowResultType.CREATE_ENTRY:
            keys.append(f"{prefix}.create_entry.{result['description']}")
        case other:
            pytest.fail(f"The flow showed a {other}")
    if f"{step}.description" in texts:
        keys.append(f"{step}.description")
    given = set(result.get("description_placeholders") or {})
    for key in keys:
        assert key in texts, f"{key} is missing from translations/en.json"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the flow doesn't give"
    return result


async def start(hass: HomeAssistant) -> ConfigFlowResult:
    return await shown(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))


async def go(
    hass: HomeAssistant, result: ConfigFlowResult, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Submits ``user_input`` to the step; without it, shows the step again, as the frontend does when the flow
    changed."""
    return await shown(hass, await hass.config_entries.flow.async_configure(result["flow_id"], user_input))


async def choose(hass: HomeAssistant, result: ConfigFlowResult, option: str) -> ConfigFlowResult:
    assert result["type"] is FlowResultType.MENU
    return await go(hass, result, {"next_step_id": option})


async def done(hass: HomeAssistant, result: ConfigFlowResult) -> ConfigFlowResult:
    """The step after a progress step, once its task has finished."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await go(hass, result)


async def pair_at(hass: HomeAssistant, address: str) -> ConfigFlowResult:
    """A setup flow that pairs the bridge at ``address``: its progress step, or the form with the error."""
    return await go(hass, await choose(hass, await start(hass), "address"), {"address": address})


def suggested(result: ConfigFlowResult, field: str) -> Any:
    """The value a form suggests for ``field``."""
    for key in result["data_schema"].schema:
        if key == field:
            return (key.description or {}).get("suggested_value")
    return pytest.fail(f"The form has no field {field}")


def choices(result: ConfigFlowResult) -> tuple[str, list[dict[str, str]]]:
    """The bridge that the form of the bridges found chooses at first, and its choices."""
    [(key, selector)] = result["data_schema"].schema.items()
    assert key == "bridge"
    assert (selector.config["mode"], selector.config["translation_key"]) == ("list", "bridge")
    return key.default(), selector.config["options"]


def paired_entry(address: str, *, bridge_id: str = BRIDGE_ID) -> MockConfigEntry:
    """The config entry of a paired bridge, with one switch."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=ENTRY_TITLE,
        unique_id=bridge_id,
        data={
            CONF_BRIDGE_IP: address,
            CONF_BRIDGE_ID: bridge_id,
            CONF_APP_KEY: "old-app-key",
            CONF_CERTIFICATE_SHA256: "00" * 32,
        },
        subentries_data=[SWITCH],
    )


def flow_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == config_flow.__name__]


def flow_log(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    """The flow's log lines, with their levels."""
    return [(record.levelno, record.getMessage()) for record in flow_records(caplog)]


# Stand-ins.


@pytest.fixture(autouse=True)
def setup_entry() -> Iterator[AsyncMock]:
    """The config entry's setup and unload, which these tests don't check; a reload calls both."""
    with (
        patch("custom_components.sonoff2hue.async_setup_entry", return_value=True) as setup,
        patch("custom_components.sonoff2hue.async_unload_entry", return_value=True),
    ):
        yield setup


class Search:
    """Stands in for the search, which needs the network: each search gives the next of ``results``, bridges or an
    error, unless the test holds it."""

    def __init__(self) -> None:
        self.results: list[list[DiscoveredBridge] | Exception] = []
        self.calls: list[tuple[object, float]] = []
        self.held = False
        self.cancelled = False

    async def __call__(self, session: object, duration: float) -> list[DiscoveredBridge]:
        self.calls.append((session, duration))
        if self.held:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def search() -> Iterator[Search]:
    stand_in = Search()
    with patch.object(config_flow, "discover", stand_in):
        yield stand_in


class Attempts:
    """Stands in for the attempts of the pairing loop: each waits until the test answers it."""

    def __init__(self) -> None:
        self.waiting: list[asyncio.Future[PairingResult]] = []
        self.made = 0
        self.answered = 0
        self.cancelled = False

    async def __call__(self, ip: str, certificate: str, device_type: str) -> PairingResult:
        self.made += 1
        future: asyncio.Future[PairingResult] = asyncio.get_running_loop().create_future()
        self.waiting.append(future)
        try:
            return await future
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def answer(self, result: PairingResult) -> None:
        """Answers the next attempt, once the pairing loop makes it."""
        await until(lambda: bool(self.waiting))
        self.waiting.pop(0).set_result(result)
        self.answered += 1

    async def next_attempt(self) -> None:
        """Waits until the pairing loop, done with the answered attempts, makes another."""
        await until(lambda: self.made > self.answered)


@pytest.fixture
def attempts() -> Iterator[Attempts]:
    """The real pairing loop without its 2 seconds between attempts; the test answers its attempts."""
    stand_in = Attempts()
    with patch.object(config_flow, "pair", functools.partial(hue_setup.pair, attempt=stand_in, interval=0)):
        yield stand_in


class Pairings:
    """Stands in for the whole pairing loop: each pairing waits until the test answers it, by bridge id."""

    def __init__(self) -> None:
        self.waiting: dict[str, asyncio.Future[str]] = {}
        self.finished: list[str] = []
        self.cancelled: list[str] = []

    async def __call__(self, info: HueBridgeInfo, device_type: str, **_: Any) -> str:
        assert info.bridge_id is not None
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.waiting[info.bridge_id] = future
        try:
            app_key = await future
        except asyncio.CancelledError:
            self.cancelled.append(info.bridge_id)
            raise
        self.finished.append(info.bridge_id)
        return app_key


@pytest.fixture
def pairings() -> Iterator[Pairings]:
    stand_in = Pairings()
    with patch.object(config_flow, "pair", stand_in):
        yield stand_in


def pairing_ends(*outcomes: str | Exception) -> Any:
    """A stand-in for the whole pairing loop: the pairings end with these keys or failures, one after the other."""
    remaining = list(outcomes)

    async def stand_in(info: HueBridgeInfo, device_type: str, **_: Any) -> str:
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return patch.object(config_flow, "pair", stand_in)


@pytest.fixture
def transports(monkeypatch: pytest.MonkeyPatch) -> list[AiohttpTransport]:
    """Every HTTPS transport to the bridge that the engine opens."""
    made: list[AiohttpTransport] = []
    init = AiohttpTransport.__init__

    def record(self: AiohttpTransport, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(AiohttpTransport, "__init__", record)
    return made


# The start.


async def test_the_setup_offers_to_find_the_bridge_enter_its_address_or_import_a_file(hass: HomeAssistant) -> None:
    result = await start(hass)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["discover", "address", "import_yaml"]


async def test_a_second_entry_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, title=ENTRY_TITLE).add_to_hass(hass)

    result = await start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert (result["reason"], result["translation_domain"]) == ("single_instance_allowed", "homeassistant")


async def test_the_frontend_gets_each_step_over_http(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, search: Search, fake_hue_pressed: FakeHueHost
) -> None:
    """The steps as Home Assistant's flow API serializes them for the frontend, which fetches a progress step again
    when told that it changed."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    address = fake_hue_pressed.address
    search.results.append([DiscoveredBridge(address, BRIDGE_ID, "mDNS")])

    async def send(user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        path = f"/api/config/config_entries/flow/{flow_id}"
        response = await (client.get(path) if user_input is None else client.post(path, json=user_input))
        assert response.status == 200, await response.text()
        step: dict[str, Any] = await response.json()
        return step

    response = await client.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    step = await response.json()
    flow_id = step["flow_id"]
    assert (step["type"], step["step_id"], step["menu_options"]) == (
        "menu",
        "user",
        ["discover", "address", "import_yaml"],
    )

    step = await send({"next_step_id": "discover"})
    assert (step["type"], step["step_id"], step["progress_action"]) == ("progress", "discover", "discover")
    await hass.async_block_till_done()

    step = await send()
    assert (step["type"], step["step_id"]) == ("form", "pick")
    [field] = step["data_schema"]
    assert (field["name"], field["required"], field["default"]) == ("bridge", True, address)
    assert field["selector"]["select"]["options"] == [
        {"value": address, "label": f"{address} — {BRIDGE_ID} — mDNS"},
        {"value": "other", "label": "Another address"},
    ]

    step = await send({"bridge": "other"})
    assert (step["type"], step["step_id"]) == ("form", "address")
    assert [(field["name"], field["required"], list(field["selector"])) for field in step["data_schema"]] == [
        ("address", True, ["text"])
    ]

    step = await send({"address": address})
    assert (step["type"], step["step_id"], step["progress_action"]) == ("progress", "link", "link")
    assert step["description_placeholders"]["certificate"] == fake_hue_pressed.fingerprint
    await hass.async_block_till_done()

    step = await send()
    assert (step["type"], step["title"], step["description"]) == ("create_entry", "sonoff2hue", "paired")
    assert hass.config_entries.async_entries(DOMAIN)[0].data[CONF_BRIDGE_IP] == address


# Pairing the bridge at an address.


async def test_pairing_the_bridge_at_an_address_adds_the_entry(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=config_flow.__name__)
    address = fake_hue_pressed.address
    result = await choose(hass, await start(hass), "address")

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "address", {})
    assert suggested(result, "address") == ""

    result = await go(hass, result, {"address": f" {address}\t"})

    assert (result["type"], result["step_id"], result["progress_action"]) == (
        FlowResultType.SHOW_PROGRESS,
        "link",
        "link",
    )
    assert result["description_placeholders"] == {
        "name": NAME,
        "model": "BSB002",
        "bridge_id": BRIDGE_ID,
        "firmware": "1972004020",
        "address": address,
        "certificate": fake_hue_pressed.fingerprint,
        "error": "",
    }

    result = await done(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert (entry.title, entry.unique_id, entry.subentries) == ("sonoff2hue", BRIDGE_ID, {})
    assert entry.data == {
        CONF_BRIDGE_IP: address,
        CONF_BRIDGE_ID: BRIDGE_ID,
        CONF_APP_KEY: ANY,
        CONF_CERTIFICATE_SHA256: fake_hue_pressed.fingerprint,
    }
    assert fake_hue_pressed.bridge.is_authorized(entry.data[CONF_APP_KEY])
    assert result["description_placeholders"] == {"name": NAME, "device_type": hue_setup.build_device_type()}
    assert flow_log(caplog) == [
        (logging.INFO, f"Pairing with the Hue bridge {NAME} at {address}: waiting for its link button."),
        (logging.INFO, f"Paired with the Hue bridge {NAME} at {address}; saved its credentials to the configuration."),
    ]
    assert hass.config_entries.flow.async_progress() == []


async def test_pairing_waits_for_the_link_button(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    with patch.object(config_flow, "pair", functools.partial(hue_setup.pair, interval=0.01)):
        result = await pair_at(hass, fake_hue.address)
        await until(lambda: "POST /api 200" in fake_hue.bridge.describe_requests())
        await fake_hue.request("POST", "_fake/link-button")
        result = await done(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert fake_hue.bridge.is_authorized(result["result"].data[CONF_APP_KEY])


@pytest.mark.parametrize(
    ("address", "message"),
    [
        (
            "not a host",
            "'not a host' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, "
            "for example 192.168.1.20 or bridge.local:8443.",
        ),
        (
            " https://192.0.2.10/ ",
            "'https://192.0.2.10/' is not a valid Hue bridge address. Use a host name or IP address, optionally with a "
            "port, for example 192.168.1.20 or bridge.local:8443.",
        ),
        ("\u3000 ", "The Hue bridge address is empty."),
    ],
)
async def test_the_address_is_checked_as_dotnet_checks_it(hass: HomeAssistant, address: str, message: str) -> None:
    result = await pair_at(hass, address)

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "address")
    assert result["errors"] == {"address": "invalid_address"}
    assert result["description_placeholders"] == {"error": message}
    assert suggested(result, "address") == address.strip()


@pytest.mark.usefixtures("socket_enabled")
async def test_an_address_where_nothing_answers(hass: HomeAssistant) -> None:
    port = closed_port()

    result = await pair_at(hass, f"127.0.0.1:{port}")

    assert (result["step_id"], result["errors"]) == ("address", {"base": "cannot_connect"})
    assert result["description_placeholders"] == {
        "error": f"No Hue bridge answered at 127.0.0.1:{port}: Connection refused (127.0.0.1:{port})"
    }
    assert suggested(result, "address") == f"127.0.0.1:{port}"


async def test_an_address_of_something_else(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body='{"name":"Not a bridge"}'))

    result = await pair_at(hass, fake_hue.address)

    assert (result["step_id"], result["errors"]) == ("address", {"base": "cannot_connect"})
    assert result["description_placeholders"] == {
        "error": f"The device at {fake_hue.address} does not look like a Hue bridge."
    }


async def test_an_answer_that_dotnet_fails_on(
    hass: HomeAssistant, fake_hue: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body='{"bridgeid":"a","BRIDGEID":"b"}'))

    result = await pair_at(hass, fake_hue.address)

    assert (result["step_id"], result["errors"]) == ("address", {"base": "unexpected"})
    assert result["description_placeholders"]["error"].startswith("An item with the same key has already been added.")
    [(level, message)] = flow_log(caplog)
    assert (level, message) == (logging.ERROR, f"Reading the Hue bridge at {fake_hue.address} failed.")
    assert flow_records(caplog)[-1].exc_info is not None


# Finding the bridge.


async def test_the_search_offers_the_bridges_found_and_another_address(hass: HomeAssistant, search: Search) -> None:
    search.results.append(
        [
            DiscoveredBridge("192.0.2.10", "001788FFFE000001", "mDNS"),
            DiscoveredBridge("192.0.2.20", None, "discovery.meethue.com"),
        ]
    )

    result = await choose(hass, await start(hass), "discover")

    assert (result["type"], result["step_id"], result["progress_action"]) == (
        FlowResultType.SHOW_PROGRESS,
        "discover",
        "discover",
    )

    result = await done(hass, result)

    assert search.calls == [(async_get_clientsession(hass), 5.0)]
    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "pick")
    assert choices(result) == (
        "192.0.2.10",
        [
            {"value": "192.0.2.10", "label": "192.0.2.10 — 001788FFFE000001 — mDNS"},
            {"value": "192.0.2.20", "label": "192.0.2.20 — discovery.meethue.com"},
            {"value": "other", "label": "Another address"},
        ],
    )
    selectors = await async_get_translations(hass, "en", "selector", [DOMAIN])
    assert selectors[f"component.{DOMAIN}.selector.bridge.options.other"] == "Another address"

    result = await go(hass, result, {"bridge": "other"})

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "address", {})


async def test_choosing_a_bridge_found_pairs_it(
    hass: HomeAssistant, search: Search, fake_hue_pressed: FakeHueHost
) -> None:
    search.results.append([DiscoveredBridge(fake_hue_pressed.address, BRIDGE_ID, "mDNS")])
    result = await done(hass, await choose(hass, await start(hass), "discover"))

    result = await go(hass, result, {"bridge": fake_hue_pressed.address})

    assert (result["type"], result["step_id"]) == (FlowResultType.SHOW_PROGRESS, "link")
    result = await done(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_BRIDGE_IP] == fake_hue_pressed.address


@pytest.mark.usefixtures("socket_enabled")
async def test_a_bridge_found_that_does_not_answer(hass: HomeAssistant, search: Search) -> None:
    port = closed_port()
    search.results.append(
        [
            DiscoveredBridge("192.0.2.10", "001788FFFE000001", "mDNS"),
            DiscoveredBridge(f"127.0.0.1:{port}", None, "mDNS"),
        ]
    )
    result = await done(hass, await choose(hass, await start(hass), "discover"))

    result = await go(hass, result, {"bridge": f"127.0.0.1:{port}"})

    assert (result["step_id"], result["errors"]) == ("pick", {"base": "cannot_connect"})
    assert result["description_placeholders"] == {
        "error": f"No Hue bridge answered at 127.0.0.1:{port}: Connection refused (127.0.0.1:{port})"
    }
    assert choices(result)[0] == f"127.0.0.1:{port}"


async def test_when_no_bridge_answers_the_search_can_run_again(hass: HomeAssistant, search: Search) -> None:
    search.results += [[], [DiscoveredBridge("192.0.2.10", "001788FFFE000001", "mDNS")]]

    result = await done(hass, await choose(hass, await start(hass), "discover"))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "no_bridges",
        ["discover", "address"],
    )

    result = await done(hass, await choose(hass, result, "discover"))

    assert (result["step_id"], len(search.calls)) == ("pick", 2)


async def test_a_search_that_fails(hass: HomeAssistant, search: Search, caplog: pytest.LogCaptureFixture) -> None:
    message = "An item with the same key has already been added. Key: id (Parameter 'key')"
    search.results.append(UnexpectedError("ArgumentException", message))

    result = await done(hass, await choose(hass, await start(hass), "discover"))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "discovery_failed",
        ["discover", "address"],
    )
    assert result["description_placeholders"] == {"error": message}
    assert flow_log(caplog) == [(logging.ERROR, "Searching for Hue bridges failed.")]
    assert flow_records(caplog)[-1].exc_info is not None


async def test_closing_the_flow_stops_the_search(hass: HomeAssistant, search: Search) -> None:
    search.held = True
    result = await choose(hass, await start(hass), "discover")
    await until(lambda: bool(search.calls))

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert search.cancelled
    assert hass.config_entries.flow.async_progress() == []


# Waiting for the link button.


async def test_the_progress_shows_attempts_that_get_no_answer(
    hass: HomeAssistant, fake_hue: FakeHueHost, attempts: Attempts
) -> None:
    changes = async_capture_events(hass, EVENT_DATA_ENTRY_FLOW_PROGRESSED)
    result = await pair_at(hass, fake_hue.address)
    flow_id = result["flow_id"]

    async def attempt(answer: PairingResult, *, changed: bool) -> ConfigFlowResult:
        """Answers an attempt and waits for the next; the progress step, shown again, then tells what happened."""
        before = len(changes)
        await attempts.answer(answer)
        await attempts.next_attempt()
        assert [event.data for event in changes[before:]] == (
            [{"handler": DOMAIN, "flow_id": flow_id, "refresh": True}] if changed else []
        )
        return await go(hass, result)

    result = await attempt(NOT_PRESSED, changed=False)
    assert (result["progress_action"], result["description_placeholders"]["error"]) == ("link", "")

    result = await attempt(NO_ANSWER, changed=True)
    assert result["progress_action"] == "link_retrying"
    assert result["description_placeholders"]["error"] == "bridge not reachable: Connection refused (192.0.2.10:443)"

    result = await attempt(NO_ANSWER, changed=False)
    result = await attempt(BUSY, changed=True)
    assert result["description_placeholders"]["error"] == "unexpected response (HTTP 503): busy"

    result = await attempt(NOT_PRESSED, changed=True)
    assert (result["progress_action"], result["description_placeholders"]["error"]) == ("link", "")

    await attempts.answer(PAIRED)
    result = await done(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_APP_KEY] == "new-app-key"


async def test_pairing_gives_up_after_60_seconds(
    hass: HomeAssistant, fake_hue: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None)):
        result = await done(hass, await pair_at(hass, fake_hue.address))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "link_timeout",
        ["retry", "user"],
    )
    assert result["description_placeholders"] == {"error": TIMEOUT}
    assert flow_log(caplog)[-1] == (
        logging.WARNING,
        f"Pairing with the Hue bridge {NAME} at {fake_hue.address} gave up: its link button wasn't pressed within 60 "
        "seconds. Start the pairing again in the sonoff2hue setup.",
    )

    result = await choose(hass, result, "user")

    assert (result["type"], result["step_id"]) == (FlowResultType.MENU, "user")


async def test_retry_reads_the_bridge_again_and_pairs(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None), "new-app-key"):
        result = await done(hass, await pair_at(hass, fake_hue.address))
        result = await choose(hass, result, "retry")

        assert (result["type"], result["step_id"]) == (FlowResultType.SHOW_PROGRESS, "link")
        result = await done(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_APP_KEY] == "new-app-key"
    assert fake_hue.bridge.describe_requests().count("GET /api/0/config 200") == 2


async def test_retry_shows_why_the_bridge_cannot_be_read_again(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None)):
        result = await done(hass, await pair_at(hass, fake_hue.address))
    fake_hue.fail(Fault("/api/0/config", status=200, body="{}"))

    result = await choose(hass, result, "retry")

    assert (result["step_id"], result["errors"]) == ("address", {"base": "cannot_connect"})
    assert suggested(result, "address") == fake_hue.address


@pytest.mark.parametrize(
    "failure",
    [
        HueApiError("The Hue bridge refused pairing: invalid value for parameter, devicetype (error 7).", 200),
        HueApiError(
            "The TLS certificate of 192.0.2.10 changed during pairing. Make sure this address belongs to your Hue bridge "
            "and pair it again."
        ),
        ConfigError("The Hue bridge address is empty."),
    ],
)
async def test_a_bridge_that_refuses_ends_the_pairing(
    hass: HomeAssistant, fake_hue: FakeHueHost, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    with pairing_ends(failure):
        result = await done(hass, await pair_at(hass, fake_hue.address))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "link_failed",
        ["retry", "user"],
    )
    assert result["description_placeholders"] == {"error": str(failure)}
    assert flow_log(caplog)[-1] == (
        logging.WARNING,
        f"Pairing with the Hue bridge at {fake_hue.address} failed: {failure}",
    )


async def test_an_unexpected_failure_ends_the_pairing(
    hass: HomeAssistant, fake_hue: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    with pairing_ends(
        UnexpectedError("NullReferenceException", "Object reference not set to an instance of an object.")
    ):
        result = await done(hass, await pair_at(hass, fake_hue.address))

    assert (result["step_id"], result["description_placeholders"]) == (
        "link_failed",
        {"error": "Pairing failed: Object reference not set to an instance of an object."},
    )
    assert flow_log(caplog)[-1] == (logging.ERROR, f"Pairing with the Hue bridge at {fake_hue.address} failed.")
    assert flow_records(caplog)[-1].exc_info is not None


async def test_closing_the_flow_stops_the_pairing(
    hass: HomeAssistant, fake_hue: FakeHueHost, attempts: Attempts
) -> None:
    result = await pair_at(hass, fake_hue.address)
    await attempts.answer(NOT_PRESSED)
    await attempts.next_attempt()

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert attempts.cancelled
    assert hass.config_entries.flow.async_progress() == []
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_closing_the_flow_while_the_bridge_is_read_starts_no_pairing(
    hass: HomeAssistant, fake_hue: FakeHueHost, attempts: Attempts
) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body=json.dumps(FakeBridge.public_config()), delay=0.3))
    result = await choose(hass, await start(hass), "address")
    submit = hass.async_create_task(
        hass.config_entries.flow.async_configure(result["flow_id"], {"address": fake_hue.address})
    )
    await until(lambda: not fake_hue.bridge.faults)

    hass.config_entries.flow.async_abort(result["flow_id"])

    with pytest.raises(UnknownFlow):
        await submit
    async with asyncio.timeout(5):
        await hass.async_block_till_done()  # a pairing started anyway would keep it waiting
    assert attempts.made == 0


async def test_closing_the_flow_during_a_request_closes_its_connection(
    hass: HomeAssistant, fake_hue: FakeHueHost, transports: list[AiohttpTransport]
) -> None:
    fake_hue.fail(Fault("/api", method="POST", status=200, body="[]", delay=0.5))
    result = await pair_at(hass, fake_hue.address)
    await until(lambda: not fake_hue.bridge.faults)

    hass.config_entries.flow.async_abort(result["flow_id"])
    # Home Assistant doesn't wait for tasks that are being cancelled (async_block_till_done skips them).
    await until(lambda: transports[-1]._session is None)

    assert len(transports) == 2  # the bridge's identity, then the first attempt to pair
    assert transports[0]._session is None
    assert hass.config_entries.flow.async_progress() == []


# One bridge, one entry.


async def test_a_flow_that_adds_the_entry_ends_the_other_pairings(
    hass: HomeAssistant, fake_hue: FakeHueHost, pairings: Pairings
) -> None:
    first = await pair_at(hass, fake_hue.address)
    fake_hue.fail(Fault("/api/0/config", status=200, body=f'{{"bridgeid":"{OTHER_ID}","name":"Other bridge"}}'))
    second = await pair_at(hass, fake_hue.address)
    assert (first["step_id"], second["step_id"]) == ("link", "link")
    await until(lambda: len(pairings.waiting) == 2)

    pairings.waiting[BRIDGE_ID].set_result("new-app-key")
    await until(lambda: pairings.finished == [BRIDGE_ID])
    result = await go(hass, first)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert pairings.cancelled == [OTHER_ID]
    assert hass.config_entries.flow.async_progress() == []


async def test_two_flows_cannot_pair_the_same_bridge(
    hass: HomeAssistant, fake_hue: FakeHueHost, pairings: Pairings
) -> None:
    first = await pair_at(hass, fake_hue.address)
    await until(lambda: BRIDGE_ID in pairings.waiting)

    second = await pair_at(hass, fake_hue.address)

    assert (second["type"], second["reason"], second["translation_domain"]) == (
        FlowResultType.ABORT,
        "already_in_progress",
        "homeassistant",
    )
    hass.config_entries.flow.async_abort(first["flow_id"])
    await hass.async_block_till_done()
    assert pairings.cancelled == [BRIDGE_ID]


# Reconfigure.


async def test_reconfigure_pairs_again_and_keeps_the_switches(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost, setup_entry: AsyncMock
) -> None:
    entry = paired_entry(fake_hue_pressed.address)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    subentries = dict(entry.subentries)

    result = await shown(hass, await entry.start_reconfigure_flow(hass))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "reconfigure",
        ["discover", "address"],
    )

    result = await choose(hass, result, "address")

    assert suggested(result, "address") == fake_hue_pressed.address

    result = await done(hass, await go(hass, result, {"address": fake_hue_pressed.address}))
    await hass.async_block_till_done()

    assert (result["type"], result["reason"], result["translation_domain"]) == (
        FlowResultType.ABORT,
        "reconfigure_successful",
        "homeassistant",
    )
    assert (entry.unique_id, entry.title, entry.subentries) == (BRIDGE_ID, ENTRY_TITLE, subentries)
    assert entry.data[CONF_APP_KEY] != "old-app-key"
    assert fake_hue_pressed.bridge.is_authorized(entry.data[CONF_APP_KEY])
    assert entry.data[CONF_CERTIFICATE_SHA256] == fake_hue_pressed.fingerprint
    assert setup_entry.call_count == 2


async def test_reconfigure_can_pair_another_bridge(hass: HomeAssistant, fake_hue_pressed: FakeHueHost) -> None:
    entry = paired_entry("192.0.2.10", bridge_id=OTHER_ID)
    entry.add_to_hass(hass)

    result = await choose(hass, await shown(hass, await entry.start_reconfigure_flow(hass)), "address")
    result = await done(hass, await go(hass, result, {"address": fake_hue_pressed.address}))
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == BRIDGE_ID
    assert entry.data == {
        CONF_BRIDGE_IP: fake_hue_pressed.address,
        CONF_BRIDGE_ID: BRIDGE_ID,
        CONF_APP_KEY: ANY,
        CONF_CERTIFICATE_SHA256: fake_hue_pressed.fingerprint,
    }


@pytest.mark.parametrize(
    ("paired_id", "chosen"),
    [(BRIDGE_ID.lower(), "192.0.2.20"), ("001788FFFE000077", "192.0.2.10")],
    ids=["paired bridge found", "paired bridge not found"],
)
async def test_reconfigure_chooses_the_paired_bridge_first(
    hass: HomeAssistant, search: Search, paired_id: str, chosen: str
) -> None:
    entry = paired_entry("192.0.2.20", bridge_id=paired_id)
    entry.add_to_hass(hass)
    search.results.append(
        [
            DiscoveredBridge("192.0.2.10", "001788FFFE000001", "mDNS"),
            DiscoveredBridge("192.0.2.15", None, "discovery.meethue.com"),
            DiscoveredBridge("192.0.2.20", BRIDGE_ID, "mDNS"),
        ]
    )

    result = await choose(hass, await shown(hass, await entry.start_reconfigure_flow(hass)), "discover")
    result = await done(hass, result)

    assert choices(result)[0] == chosen


async def test_reconfigure_offers_to_choose_another_bridge_after_a_failure(
    hass: HomeAssistant, fake_hue: FakeHueHost
) -> None:
    entry = paired_entry(fake_hue.address)
    entry.add_to_hass(hass)

    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None)):
        result = await choose(hass, await shown(hass, await entry.start_reconfigure_flow(hass)), "address")
        result = await done(hass, await go(hass, result, {"address": fake_hue.address}))

    assert (result["step_id"], result["menu_options"]) == ("link_timeout", ["retry", "reconfigure"])

    result = await choose(hass, result, "reconfigure")

    assert (result["type"], result["step_id"]) == (FlowResultType.MENU, "reconfigure")


# Reauth.


async def test_reauth_pairs_the_stored_bridge_again(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost, setup_entry: AsyncMock
) -> None:
    entry = paired_entry(fake_hue_pressed.address)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await shown(hass, await entry.start_reauth_flow(hass))

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "reauth_confirm", {})
    assert result["description_placeholders"] == {"address": fake_hue_pressed.address, "name": ENTRY_TITLE}

    result = await go(hass, result, {})

    assert (result["type"], result["step_id"]) == (FlowResultType.SHOW_PROGRESS, "link")

    result = await done(hass, result)
    await hass.async_block_till_done()

    assert (result["type"], result["reason"], result["translation_domain"]) == (
        FlowResultType.ABORT,
        "reauth_successful",
        "homeassistant",
    )
    assert fake_hue_pressed.bridge.is_authorized(entry.data[CONF_APP_KEY])
    assert (entry.unique_id, entry.data[CONF_BRIDGE_IP]) == (BRIDGE_ID, fake_hue_pressed.address)
    assert setup_entry.call_count == 2


async def test_reauth_refuses_another_bridge_at_the_stored_address(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    entry = paired_entry(fake_hue.address, bridge_id=OTHER_ID)
    entry.add_to_hass(hass)

    result = await go(hass, await shown(hass, await entry.start_reauth_flow(hass)), {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "wrong_bridge")
    assert result["description_placeholders"] == {
        "address": fake_hue.address,
        "bridge_id": BRIDGE_ID,
        "expected": OTHER_ID,
    }
    assert entry.data[CONF_APP_KEY] == "old-app-key"


@pytest.mark.usefixtures("socket_enabled")
async def test_reauth_shows_why_the_stored_bridge_cannot_be_read(hass: HomeAssistant) -> None:
    port = closed_port()
    entry = paired_entry(f"127.0.0.1:{port}")
    entry.add_to_hass(hass)

    result = await go(hass, await shown(hass, await entry.start_reauth_flow(hass)), {})

    assert (result["step_id"], result["errors"]) == ("reauth_confirm", {"base": "cannot_connect"})
    assert result["description_placeholders"]["error"] == (
        f"No Hue bridge answered at 127.0.0.1:{port}: Connection refused (127.0.0.1:{port})"
    )

    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_BRIDGE_IP: "not a host"})
    result = await go(hass, result, {})

    assert (result["step_id"], result["errors"]) == ("reauth_confirm", {"base": "invalid_address"})
    assert result["description_placeholders"]["error"].startswith("'not a host' is not a valid Hue bridge address.")


async def test_reauth_offers_only_to_retry(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    entry = paired_entry(fake_hue.address)
    entry.add_to_hass(hass)

    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None), "new-app-key"):
        result = await go(hass, await shown(hass, await entry.start_reauth_flow(hass)), {})
        result = await done(hass, result)

        assert (result["step_id"], result["menu_options"]) == ("link_timeout", ["retry"])

        result = await done(hass, await choose(hass, result, "retry"))
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_APP_KEY] == "new-app-key"


# Importing a sonoff2hue.yaml.

SWITCHES = """switches:
  - name: Hallway switch
    deviceId: 1000ABCDEF
    deviceKey: made-up-device-key
    lights:
      - Hallway
    groups: []
  - deviceId: "1000999999"
    groups:
      - room:Living room
"""
"""Two switches as the .NET app's file has them: one named with a device key, one without either."""
IMPORTED_SWITCHES = [
    (
        "1000abcdef",
        "Hallway switch",
        {
            CONF_DEVICE_ID: "1000ABCDEF",
            CONF_NAME: "Hallway switch",
            CONF_DEVICE_KEY: "made-up-device-key",
            CONF_LIGHTS: ["Hallway"],
            CONF_GROUPS: [],
        },
    ),
    (
        "1000999999",
        "1000999999",
        {
            CONF_DEVICE_ID: "1000999999",
            CONF_NAME: None,
            CONF_DEVICE_KEY: None,
            CONF_LIGHTS: [],
            CONF_GROUPS: ["room:Living room"],
        },
    ),
]
"""The subentries of SWITCHES: unique id, title and data."""


def config_file(**hue: str | None) -> str:
    """A sonoff2hue.yaml with this hue section (camelCase keys; None leaves a key out) and SWITCHES."""
    lines = [f"  {key}: {value}" for key, value in hue.items() if value is not None]
    return ("hue:\n" + "".join(line + "\n" for line in lines) if lines else "") + SWITCHES


def switches_of(entry: Any) -> list[tuple[str | None, str, dict[str, Any]]]:
    """The entry's subentries as (unique id, title, data), checking that each is a switch."""
    assert {subentry.subentry_type for subentry in entry.subentries.values()} == {SUBENTRY_SWITCH}
    return [(subentry.unique_id, subentry.title, dict(subentry.data)) for subentry in entry.subentries.values()]


async def import_file(hass: HomeAssistant, text: str) -> ConfigFlowResult:
    """A setup flow that imports ``text``: what it shows after reading and checking it."""
    result = await choose(hass, await start(hass), "import_yaml")
    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "import_yaml", {})
    assert suggested(result, "config") is None
    return await go(hass, result, {"config": text})


async def test_importing_a_file_with_its_bridge_adds_the_entry_and_the_switches(
    hass: HomeAssistant, fake_hue: FakeHueHost
) -> None:
    app_key = await fake_hue.pair()

    result = await import_file(
        hass,
        config_file(
            bridgeIp=fake_hue.address,
            bridgeId=BRIDGE_ID,
            appKey=app_key,
            certificateSha256=fake_hue.fingerprint,
        ),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert (entry.title, entry.unique_id) == ("sonoff2hue", BRIDGE_ID)
    assert entry.data == {
        CONF_BRIDGE_IP: fake_hue.address,
        CONF_BRIDGE_ID: BRIDGE_ID,
        CONF_APP_KEY: app_key,
        CONF_CERTIFICATE_SHA256: fake_hue.fingerprint,
    }
    assert switches_of(entry) == IMPORTED_SWITCHES
    assert (result["description"], result["description_placeholders"]) == (
        "imported",
        {"address": fake_hue.address, "switches": "Hallway switch, 1000999999"},
    )
    assert "subentries" not in result  # their device keys stay out of the answer to the browser
    requests = fake_hue.bridge.describe_requests()
    assert "GET /clip/v2/resource/bridge 200" in requests
    assert "GET /api/0/config 200" in requests  # the bridge's own id, even when the file has one


@pytest.mark.parametrize("file_id", [None, '""'], ids=["left out", "blank"])
async def test_a_missing_bridge_id_comes_from_the_bridge(
    hass: HomeAssistant, fake_hue: FakeHueHost, file_id: str | None
) -> None:
    app_key = await fake_hue.pair()

    result = await import_file(
        hass,
        config_file(
            bridgeIp=fake_hue.address, bridgeId=file_id, appKey=app_key, certificateSha256=fake_hue.fingerprint
        ),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (result["result"].unique_id, result["result"].data[CONF_BRIDGE_ID]) == (BRIDGE_ID, BRIDGE_ID)
    assert (result["description"], result["description_placeholders"]) == (
        "imported",
        {"address": fake_hue.address, "switches": "Hallway switch, 1000999999"},
    )


@pytest.mark.parametrize(
    ("pin", "file_id", "description", "placeholders"),
    [
        (True, BRIDGE_ID.lower(), "imported", {}),
        (True, OTHER_ID, "imported_other_id", {"file_bridge_id": OTHER_ID, "bridge_id": BRIDGE_ID}),
        (
            False,
            OTHER_ID,
            "imported_other_id_unpinned",
            {"file_bridge_id": OTHER_ID, "bridge_id": BRIDGE_ID, "warning": supervisor.NOT_PINNED},
        ),
    ],
    ids=["the same id in other letter case", "another id", "another id and no pin"],
)
async def test_the_bridge_id_is_the_bridges_own(
    hass: HomeAssistant,
    fake_hue: FakeHueHost,
    pin: bool,
    file_id: str,
    description: str,
    placeholders: dict[str, str],
) -> None:
    """The bridge id is the unique id, which reauth and reconfigure compare with the bridge's: it comes from the bridge,
    and the new entry's text says when the file named another."""
    app_key = await fake_hue.pair()

    result = await import_file(
        hass,
        config_file(
            bridgeIp=fake_hue.address,
            bridgeId=file_id,
            appKey=app_key,
            certificateSha256=fake_hue.fingerprint if pin else None,
        ),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (result["result"].unique_id, result["result"].data[CONF_BRIDGE_ID]) == (BRIDGE_ID, BRIDGE_ID)
    assert (result["description"], result["description_placeholders"]) == (
        description,
        {"address": fake_hue.address, "switches": "Hallway switch, 1000999999"} | placeholders,
    )


@pytest.mark.parametrize("pin", [None, "' '"], ids=["left out", "blank"])
async def test_a_file_without_a_pin_is_imported_with_the_runs_warning(
    hass: HomeAssistant, fake_hue: FakeHueHost, pin: str | None
) -> None:
    app_key = await fake_hue.pair()

    result = await import_file(
        hass, config_file(bridgeIp=fake_hue.address, bridgeId=BRIDGE_ID, appKey=app_key, certificateSha256=pin)
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_CERTIFICATE_SHA256] == (None if pin is None else " ")
    assert (result["description"], result["description_placeholders"]["warning"]) == (
        "imported_unpinned",
        "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue "
        "integration to pair the bridge again and pin it.",
    )


@pytest.mark.parametrize(
    ("hue", "message"),
    [
        (
            {"certificateSha256": "00" * 32},
            "The Hue bridge TLS certificate does not match the pinned certificateSha256. If the bridge was replaced or "
            "reset, pair the bridge again.",
        ),
        (
            {"appKey": "made-up-app-key"},
            "The Hue bridge rejected the application key (unauthorized user); pair the bridge again.",
        ),
        (
            {"appKey": '"made-up\\tapp-key"'},
            "The Hue application key contains characters that can't be sent to the bridge; pair the bridge again.",
        ),
        (
            {"bridgeIp": "not a host"},
            "'not a host' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, for "
            "example 192.168.1.20 or bridge.local:8443.",
        ),
    ],
    ids=["another pin", "a key the bridge refuses", "a key that can't be sent", "a malformed address"],
)
async def test_a_bridge_that_refuses_the_files_settings(
    hass: HomeAssistant, fake_hue: FakeHueHost, hue: dict[str, str], message: str
) -> None:
    app_key = await fake_hue.pair()
    settings = {"bridgeIp": fake_hue.address, "appKey": app_key, "certificateSha256": fake_hue.fingerprint} | hue

    result = await import_file(hass, config_file(**settings))

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "import_failed",
        ["import_check", "import_pair", "import_yaml"],
    )
    assert result["description_placeholders"] == {"error": message}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.usefixtures("socket_enabled")
async def test_a_file_whose_bridge_does_not_answer(hass: HomeAssistant) -> None:
    port = closed_port()

    result = await import_file(hass, config_file(bridgeIp=f"127.0.0.1:{port}", appKey="made-up-app-key"))

    assert (result["step_id"], result["description_placeholders"]) == (
        "import_failed",
        {"error": f"Hue bridge not reachable: Connection refused (127.0.0.1:{port})"},
    )


async def test_a_bridge_that_gives_no_bridge_id(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    app_key = await fake_hue.pair()
    fake_hue.fail(Fault("/api/0/config", status=200, body="{}"))

    result = await import_file(hass, config_file(bridgeIp=fake_hue.address, appKey=app_key))

    assert (result["step_id"], result["description_placeholders"]) == (
        "import_failed",
        {"error": f"The device at {fake_hue.address} does not look like a Hue bridge."},
    )


async def test_a_bridge_answer_that_dotnet_fails_on(
    hass: HomeAssistant, fake_hue: FakeHueHost, caplog: pytest.LogCaptureFixture
) -> None:
    app_key = await fake_hue.pair()
    fake_hue.fail(Fault("/clip/v2/resource/bridge", status=200, body='{"errors":[null]}'))

    result = await import_file(hass, config_file(bridgeIp=fake_hue.address, appKey=app_key))

    assert (result["step_id"], result["description_placeholders"]) == (
        "import_failed",
        {"error": "Object reference not set to an instance of an object."},
    )
    assert flow_log(caplog) == [(logging.ERROR, f"Connecting to the Hue bridge at {fake_hue.address} failed.")]
    assert flow_records(caplog)[-1].exc_info is not None


async def test_trying_the_files_bridge_again(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    app_key = await fake_hue.pair()
    fake_hue.fail(Fault("/clip/v2/resource/bridge", status=503))

    result = await import_file(
        hass,
        config_file(
            bridgeIp=fake_hue.address, bridgeId=BRIDGE_ID, appKey=app_key, certificateSha256=fake_hue.fingerprint
        ),
    )

    assert result["description_placeholders"] == {
        "error": "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/bridge"
    }

    result = await choose(hass, result, "import_check")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert switches_of(result["result"]) == IMPORTED_SWITCHES


async def test_pairing_the_bridge_instead_keeps_the_files_switches(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost
) -> None:
    result = await import_file(hass, config_file(bridgeIp=fake_hue_pressed.address, appKey="made-up-app-key"))
    result = await choose(hass, result, "import_pair")

    assert (result["type"], result["step_id"], result["menu_options"]) == (
        FlowResultType.MENU,
        "import_pair",
        ["discover", "address"],
    )
    assert result["description_placeholders"] == {"switches": "Hallway switch, 1000999999"}

    result = await choose(hass, result, "address")

    assert suggested(result, "address") == fake_hue_pressed.address

    result = await done(hass, await go(hass, result, {"address": fake_hue_pressed.address}))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.data[CONF_APP_KEY] != "made-up-app-key"
    assert fake_hue_pressed.bridge.is_authorized(entry.data[CONF_APP_KEY])
    assert entry.data[CONF_CERTIFICATE_SHA256] == fake_hue_pressed.fingerprint
    assert switches_of(entry) == IMPORTED_SWITCHES
    assert (result["description"], result["description_placeholders"]) == (
        "paired_imported",
        {"name": NAME, "device_type": hue_setup.build_device_type(), "switches": "Hallway switch, 1000999999"},
    )
    assert "subentries" not in result


async def test_pasting_the_file_again_after_its_bridge_failed(hass: HomeAssistant, fake_hue: FakeHueHost) -> None:
    result = await import_file(hass, config_file(bridgeIp=fake_hue.address, appKey="made-up-app-key"))

    result = await choose(hass, result, "import_yaml")

    assert (result["type"], result["step_id"], result["errors"]) == (FlowResultType.FORM, "import_yaml", {})
    assert suggested(result, "config") is None


@pytest.mark.parametrize(
    ("hue", "address"),
    [({}, ""), ({"bridgeIp": "192.0.2.10"}, "192.0.2.10"), ({"appKey": "made-up-app-key"}, "")],
    ids=["no hue section", "an address without a key", "a key without an address"],
)
async def test_a_file_without_the_bridge_pairs_it_first(
    hass: HomeAssistant, fake_hue_pressed: FakeHueHost, hue: dict[str, str], address: str
) -> None:
    result = await import_file(hass, config_file(**hue))

    assert (result["type"], result["step_id"]) == (FlowResultType.MENU, "import_pair")

    result = await choose(hass, result, "address")

    assert suggested(result, "address") == address

    result = await done(hass, await go(hass, result, {"address": fake_hue_pressed.address}))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_BRIDGE_IP] == fake_hue_pressed.address
    assert switches_of(result["result"]) == IMPORTED_SWITCHES


async def test_choosing_another_bridge_while_importing_goes_back_to_pairing_for_the_switches(
    hass: HomeAssistant, fake_hue: FakeHueHost
) -> None:
    with pairing_ends(PairingTimeoutError(hue_setup.PAIRING_WINDOW, None)):
        result = await choose(hass, await import_file(hass, SWITCHES), "address")
        result = await done(hass, await go(hass, result, {"address": fake_hue.address}))

    assert (result["step_id"], result["menu_options"]) == ("link_timeout", ["retry", "import_pair"])

    result = await choose(hass, result, "import_pair")

    assert (result["type"], result["step_id"]) == (FlowResultType.MENU, "import_pair")


@pytest.mark.parametrize(
    "text",
    [
        "hue:\n  bridgeIP: 192.0.2.10\n",
        "switches:\n  - deviceId: 1000abcdef\n    lights: [Hallway]\n  -\n",
        "hue:\n  appKey: a\n  appKey: b\n" + SWITCHES,
    ],
    ids=["an unknown key", "an empty switch", "a duplicate key"],
)
async def test_a_file_that_dotnet_cannot_read(hass: HomeAssistant, text: str) -> None:
    with pytest.raises(ConfigError) as dotnet:
        yaml_import.parse(text)

    result = await import_file(hass, text)

    assert (result["step_id"], result["errors"]) == ("import_yaml", {"config": "invalid_config"})
    assert result["description_placeholders"] == {"error": str(dotnet.value)}
    assert suggested(result, "config") is None  # the keys in it are never shown back


async def test_the_reading_errors_name_the_line_and_column(hass: HomeAssistant) -> None:
    result = await import_file(hass, "hue:\n  bridgeIP: 192.0.2.10\n")

    assert result["description_placeholders"] == {
        "error": "Config, line 2, column 3: unknown key 'bridgeIP' under 'hue'. Valid keys are bridgeIp, bridgeId, "
        "appKey, certificateSha256 (case-sensitive)."
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "hue:\n  bridgeIp: 192.0.2.10\n  appKey: made-up-app-key\nswitches:\n  - deviceId: 1000abcdef\n"
            "    name: Hallway switch\n",
            "Invalid configuration:\n  - switches[0] ('Hallway switch'): add at least one entry to 'lights' or 'groups'.",
        ),
        (
            "# no switches yet\n",
            "Invalid configuration:\n  - No switches configured. Add at least one entry to 'switches'.",
        ),
        (
            SWITCHES + "  - deviceId: 1000abcdef\n    lights: [Ceiling]\n",
            "Invalid configuration:\n  - switches[2]: deviceId '1000abcdef' is configured more than once; put all its "
            "lights in one entry.",
        ),
    ],
    ids=["a switch without lights", "no switches", "a device twice"],
)
async def test_a_file_that_the_run_would_refuse(hass: HomeAssistant, text: str, message: str) -> None:
    """The run's own checks; a file without the bridge's address and key gets only the switches', since pairing gives
    those."""
    result = await import_file(hass, text)

    assert (result["step_id"], result["errors"]) == ("import_yaml", {"config": "invalid_config"})
    assert result["description_placeholders"] == {"error": message}


async def test_a_file_whose_reading_ends_dotnet(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    result = await import_file(hass, "a\n")

    assert (result["step_id"], result["errors"]) == ("import_yaml", {"config": "unreadable_config"})
    assert result["description_placeholders"] == {"error": "Stack overflow."}
    assert flow_log(caplog) == [(logging.ERROR, "Reading the pasted sonoff2hue.yaml failed.")]


async def test_an_imported_entry_is_answered_over_http_without_its_keys(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_hue: FakeHueHost
) -> None:
    """The frontend gets a new entry's result without its data but, from Home Assistant, with its subentries: the flow
    leaves those out, as their device keys are secrets too."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    app_key = await fake_hue.pair()
    text = config_file(bridgeIp=fake_hue.address, appKey=app_key, certificateSha256=fake_hue.fingerprint)

    response = await client.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    flow_id = (await response.json())["flow_id"]
    path = f"/api/config/config_entries/flow/{flow_id}"
    form = await (await client.post(path, json={"next_step_id": "import_yaml"})).json()
    response = await client.post(path, json={"config": text})
    created = await response.text()

    assert form["step_id"] == "import_yaml"
    assert json.loads(created)["type"] == "create_entry"
    for secret in (app_key, "made-up-device-key"):
        assert secret not in created
    assert switches_of(hass.config_entries.async_entries(DOMAIN)[0]) == IMPORTED_SWITCHES
