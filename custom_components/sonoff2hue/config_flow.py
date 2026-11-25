"""The setup flow, with reconfigure and reauth: it finds the Hue bridge or takes its address, then pairs with it, as the
.NET app's Hue bridge page (``Web/Management/HueEndpoints.cs``, ``HuePairing.cs`` and the UI's ``views/hue.js``) and its
``hue-pair`` command did, with the same rules and messages.

The steps (decision 22): a menu (``user``, or ``reconfigure``) leads to the search (``discover``, a progress step) and
the bridges found (``pick``), or to the address (``address``). The bridge's identity and the certificate to pin are read
from there, then ``link``, a progress step, waits up to 60 seconds for the link button. On success ``paired`` saves the
bridge; otherwise ``link_timeout`` or ``link_failed`` offers to retry. Reauth goes from ``reauth_confirm`` to ``link``
with the stored bridge.

The menu's third way, ``import_yaml``, reads a pasted ``sonoff2hue.yaml`` as the .NET app read its file and runs the
checks of its ``run``. With the bridge's address and key, it connects as ``run`` did at startup and adds the entry with
the file's switches as subentries; ``import_failed`` offers to try again, to pair the bridge instead, or to paste the
file again. Without them, ``import_pair`` leads to the search or the address and to ``link`` first.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
import probatio

from .const import (
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
from .engine import yaml_import
from .engine.config import ConfigError, EngineConfig, HueSettings, SwitchSettings
from .engine.errors import UnexpectedError
from .engine.hue_address import try_create_base_url
from .engine.hue_client import PAIR_AGAIN, HueApiError, HueBridgeInfo
from .engine.hue_setup import (
    DISCOVERY_TIMEOUT,
    PAIRING_WINDOW,
    DiscoveredBridge,
    PairingResult,
    PairingStatus,
    PairingTimeoutError,
    build_device_type,
    discover,
    get_bridge_info,
    pair,
)
from .engine.supervisor import NOT_PINNED, create_hue_client
from .engine.switch_rules import is_paired
from .switch_flow import SwitchSubentryFlow

_LOGGER = logging.getLogger(__name__)

PAIR_MENU: Final = ["discover", "address"]
"""The ways to the bridge: search the network for it, or enter its address."""
SETUP_MENU: Final = [*PAIR_MENU, "import_yaml"]
"""The ways to set up: the two to the bridge, or importing a ``sonoff2hue.yaml``."""
IMPORT_FAILED_MENU: Final = ["import_check", "import_pair", "import_yaml"]
"""After the file's bridge failed: try again, pair the bridge instead and keep the switches, or paste the file again."""

ADDRESS: Final = "address"
BRIDGE: Final = "bridge"
OTHER: Final = "other"
"""The choice of another address than the bridges found."""

CONFIG: Final = "config"
"""The pasted ``sonoff2hue.yaml``."""

ADDRESS_SCHEMA: Final = probatio.Schema({probatio.Required(ADDRESS): TextSelector()})
IMPORT_SCHEMA: Final = probatio.Schema({probatio.Required(CONFIG): TextSelector(TextSelectorConfig(multiline=True))})

PAIR_INSTEAD: Final = "pair the bridge again"
"""What the engine's ``PAIR_AGAIN`` says while importing, where there is no integration to reconfigure yet and
``import_failed`` offers to pair the bridge."""


@dataclass(frozen=True, slots=True)
class _Paired:
    app_key: str
    device_type: str


@dataclass(frozen=True, slots=True)
class _Failed:
    """A search or a pairing that ended without a result: the step that says so, and the message it shows."""

    step: str
    message: str


class _BridgeError(Exception):
    """The bridge couldn't be read: the form's error key, and the engine's message, shown as the error's text."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error


def _bridge_name(info: HueBridgeInfo) -> str | None:
    """How the .NET app's log lines name the bridge: ``info.Name ?? info.BridgeId``."""
    return info.name if info.name is not None else info.bridge_id


def _label(bridge: DiscoveredBridge) -> str:
    """A bridge found by the search, as ``IP — bridge id — source``; the id is left out when the bridge gave none."""
    return " — ".join(part for part in (bridge.ip, bridge.bridge_id, bridge.source) if part)


def _check_for_run(config: EngineConfig) -> None:
    """The checks of the .NET app's ``run`` (``ValidateForRun``). Without the bridge's address and key, which pairing
    gives, only the switches' are left: stand-ins take the bridge's place."""
    if is_paired(config.hue):
        config.validate_for_run()
    else:
        EngineConfig(HueSettings(bridge_ip="paired", app_key="paired"), config.switches).validate_for_run()


def _switch_title(switch: SwitchSettings) -> str:
    """A switch subentry's title: its name, or its device id without one."""
    return (switch.name or "").strip() or switch.normalized_device_id


def _switch_titles(config: EngineConfig) -> str:
    """The file's switches, by their titles, for the dialogs: names and device ids, never the keys."""
    return ", ".join(_switch_title(switch) for switch in config.switches)


def _switch_subentry(switch: SwitchSettings) -> ConfigSubentryData:
    """A switch of the file as a ``switch`` subentry, with the values as written."""
    return ConfigSubentryData(
        data={
            CONF_DEVICE_ID: switch.device_id,
            CONF_NAME: switch.name,
            CONF_DEVICE_KEY: switch.device_key,
            CONF_LIGHTS: list(switch.lights),
            CONF_GROUPS: list(switch.groups),
        },
        subentry_type=SUBENTRY_SWITCH,
        title=_switch_title(switch),
        unique_id=switch.normalized_device_id,
    )


class Sonoff2HueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pairs the one Hue bridge (``single_config_entry``). Its unique id is the bridge id."""

    VERSION = 1

    # Set by the step that leads to the one that reads them.
    _bridges: list[DiscoveredBridge]
    _info: HueBridgeInfo
    _paired: _Paired
    _failure: _Failed

    def __init__(self) -> None:
        self._search: asyncio.Task[list[DiscoveredBridge] | _Failed] | None = None
        self._pairing: asyncio.Task[_Paired | _Failed] | None = None
        self._attempt_error: str | None = None
        """Why the last pairing attempt failed, while the bridge doesn't answer; ``None`` once it does."""
        self._removed = False
        self._import: EngineConfig | None = None
        """The imported file, once it passed the run's checks."""

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """The switches, one subentry per MINIR4M, which the integration's page adds and changes (decision 24)."""
        return {SUBENTRY_SWITCH: SwitchSubentryFlow}

    # The ways in.

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find the bridge, enter its address, or import a ``sonoff2hue.yaml``."""
        return self.async_show_menu(step_id="user", menu_options=SETUP_MENU)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pair again, with the same bridge or another; the switches stay."""
        return self.async_show_menu(step_id="reconfigure", menu_options=PAIR_MENU)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The bridge rejected the application key: pair the stored bridge again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Waits for the user before the pairing starts, since its 60 seconds run from then."""
        address: str = self._get_reauth_entry().data.get(CONF_BRIDGE_IP, "")
        errors: dict[str, str] = {}
        placeholders = {"address": address}
        if user_input is not None:
            try:
                return await self._async_start_pairing(address)
            except _BridgeError as failure:
                errors["base"] = failure.error
                placeholders["error"] = str(failure)
        return self.async_show_form(step_id="reauth_confirm", errors=errors, description_placeholders=placeholders)

    # Finding the bridge.

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Searches the network for 5 seconds, with mDNS and Signify's discovery endpoint, as the web UI did."""
        if self._search is None:
            self._search = self.hass.async_create_task(self._async_search(), eager_start=False)
        if not self._search.done():
            return self.async_show_progress(step_id="discover", progress_action="discover", progress_task=self._search)
        search, self._search = self._search, None
        found = search.result()
        if isinstance(found, _Failed):
            self._failure = found
            return self.async_show_progress_done(next_step_id=found.step)
        self._bridges = found
        return self.async_show_progress_done(next_step_id="pick" if found else "no_bridges")

    async def _async_search(self) -> list[DiscoveredBridge] | _Failed:
        try:
            return await discover(async_get_clientsession(self.hass), DISCOVERY_TIMEOUT)
        except Exception as err:
            # The .NET app answered 500 for any failure of the search, and the web UI said "The search failed".
            _LOGGER.exception("Searching for Hue bridges failed.")
            return _Failed("discovery_failed", str(err))

    async def async_step_no_bridges(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nothing answered: search again, or enter the address."""
        return self.async_show_menu(step_id="no_bridges", menu_options=PAIR_MENU)

    async def async_step_discovery_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The search failed: search again, or enter the address."""
        return self.async_show_menu(
            step_id="discovery_failed",
            menu_options=PAIR_MENU,
            description_placeholders={"error": self._failure.message},
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offers the bridges found, and another address."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        choice = self._default_bridge()
        if user_input is not None:
            choice = user_input[BRIDGE]
            if choice == OTHER:
                return await self.async_step_address()
            try:
                return await self._async_start_pairing(choice)
            except _BridgeError as failure:
                errors["base"] = failure.error
                placeholders["error"] = str(failure)
        options = [SelectOptionDict(value=bridge.ip, label=_label(bridge)) for bridge in self._bridges]
        options.append(SelectOptionDict(value=OTHER, label="Another address"))
        selector = SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST, translation_key=BRIDGE)
        )
        return self.async_show_form(
            step_id="pick",
            data_schema=probatio.Schema({probatio.Required(BRIDGE, default=choice): selector}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _default_bridge(self) -> str:
        """The bridge chosen at first: when reconfiguring, the paired one if it was found again (the web UI marked it as
        the current one), else the first, which the command line chose by default."""
        if self.source == SOURCE_RECONFIGURE:
            current: str | None = self._get_reconfigure_entry().data.get(CONF_BRIDGE_ID)
            for bridge in self._bridges:
                if current and bridge.bridge_id and bridge.bridge_id.casefold() == current.casefold():
                    return bridge.ip
        return self._bridges[0].ip

    async def async_step_address(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Takes the bridge's address, checked as the .NET app checks it (``HueBridgeAddress``)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        address = ""
        if user_input is not None:
            address = user_input[ADDRESS].strip()
            _, error = try_create_base_url(address)
            if error is not None:
                errors[ADDRESS] = "invalid_address"
                placeholders["error"] = error
            else:
                try:
                    return await self._async_start_pairing(address)
                except _BridgeError as failure:
                    errors["base"] = failure.error
                    placeholders["error"] = str(failure)
        elif self.source == SOURCE_RECONFIGURE:
            address = self._get_reconfigure_entry().data.get(CONF_BRIDGE_IP, "")
        elif self._import is not None:
            address = self._import.hue.bridge_ip or ""
        return self.async_show_form(
            step_id="address",
            data_schema=self.add_suggested_values_to_schema(ADDRESS_SCHEMA, {ADDRESS: address}),
            errors=errors,
            description_placeholders=placeholders,
        )

    # Pairing.

    async def _async_start_pairing(self, address: str) -> ConfigFlowResult:
        """Reads the bridge's identity and the certificate to pin (trust on first use), then waits for its link button,
        as ``HuePairing.StartAsync`` did. Raises :class:`_BridgeError` when no Hue bridge answers there."""
        try:
            info = await get_bridge_info(address)
        except HueApiError as err:
            raise _BridgeError("cannot_connect", str(err)) from err
        except ConfigError as err:
            raise _BridgeError("invalid_address", str(err)) from err
        except UnexpectedError as err:
            _LOGGER.exception("Reading the Hue bridge at %s failed.", address)
            raise _BridgeError("unexpected", str(err)) from err
        if self._removed:
            # Closed while the bridge was read: Home Assistant drops what this step returns and wouldn't stop a pairing.
            raise UnknownFlow
        await self.async_set_unique_id(info.bridge_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(
                reason="wrong_bridge",
                description_placeholders={
                    "address": info.ip,
                    "bridge_id": info.bridge_id or "",
                    "expected": self._get_reauth_entry().unique_id or "",
                },
            )
        self._info = info
        return await self.async_step_link()

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Asks the bridge for an application key every 2 seconds for 60 seconds, while the user presses its link
        button. While the bridge doesn't answer, the text says so, as the web UI did."""
        if self._pairing is None:
            self._attempt_error = None
            self._pairing = self.hass.async_create_task(self._async_pair(self._info), eager_start=False)
        if not self._pairing.done():
            return self.async_show_progress(
                step_id="link",
                progress_action="link" if self._attempt_error is None else "link_retrying",
                description_placeholders=self._link_placeholders(),
                progress_task=self._pairing,
            )
        pairing, self._pairing = self._pairing, None
        outcome = pairing.result()
        if isinstance(outcome, _Failed):
            self._failure = outcome
            return self.async_show_progress_done(next_step_id=outcome.step)
        self._paired = outcome
        return self.async_show_progress_done(next_step_id="paired")

    def _link_placeholders(self) -> dict[str, str]:
        """What ``hue-pair`` printed about the bridge, and why the last attempt failed."""
        info = self._info
        return {
            "name": info.name or "",
            "model": info.model_id or "",
            "bridge_id": info.bridge_id or "",
            "firmware": info.software_version or "",
            "address": info.ip,
            "certificate": info.certificate_sha256,
            "error": self._attempt_error or "",
        }

    async def _async_pair(self, info: HueBridgeInfo) -> _Paired | _Failed:
        """``HuePairing.PollAsync``, with its log lines; the outcome goes to the step that shows it."""
        device_type = build_device_type()
        _LOGGER.info("Pairing with the Hue bridge %s at %s: waiting for its link button.", _bridge_name(info), info.ip)
        try:
            app_key = await pair(info, device_type, on_attempt=self._on_attempt)
        except PairingTimeoutError as err:
            _LOGGER.warning(
                "Pairing with the Hue bridge %s at %s gave up: its link button wasn't pressed within %g seconds. Start "
                "the pairing again in the sonoff2hue setup.",
                _bridge_name(info),
                info.ip,
                PAIRING_WINDOW,
            )
            return _Failed("link_timeout", str(err))
        except (HueApiError, ConfigError) as err:
            _LOGGER.warning("Pairing with the Hue bridge at %s failed: %s", info.ip, err)
            return _Failed("link_failed", str(err))
        except Exception as err:
            _LOGGER.exception("Pairing with the Hue bridge at %s failed.", info.ip)
            return _Failed("link_failed", f"Pairing failed: {err}")
        return _Paired(app_key, device_type)

    @callback
    def _on_attempt(self, result: PairingResult) -> None:
        """An attempt didn't pair. Home Assistant shows the progress step again when told that the flow changed."""
        error = result.error if result.status is PairingStatus.REQUEST_FAILED else None
        if error != self._attempt_error:
            self._attempt_error = error
            self.async_notify_flow_changed()

    async def async_step_link_timeout(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The link button wasn't pressed within 60 seconds."""
        return self.async_show_menu(
            step_id="link_timeout", menu_options=self._retry_menu(), description_placeholders=self._failure_text()
        )

    async def async_step_link_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The bridge refused the pairing, or its certificate changed."""
        return self.async_show_menu(
            step_id="link_failed", menu_options=self._retry_menu(), description_placeholders=self._failure_text()
        )

    def _failure_text(self) -> dict[str, str]:
        return {"error": self._failure.message}

    def _retry_menu(self) -> list[str]:
        """Retry, or choose another bridge, as the web UI offered; reauth is bound to the stored bridge, and an import
        goes back to pairing the bridge for its switches."""
        if self.source == SOURCE_REAUTH:
            return ["retry"]
        if self.source == SOURCE_RECONFIGURE:
            return ["retry", "reconfigure"]
        return ["retry", "user" if self._import is None else "import_pair"]

    async def async_step_retry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pairs again at the same address, reading the bridge again, as the web UI's Retry did."""
        if self.source == SOURCE_REAUTH:
            return await self.async_step_reauth_confirm({})
        return await self.async_step_address({ADDRESS: self._info.ip})

    async def async_step_paired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Saves the bridge: in a new config entry, or in the one being reconfigured or reauthenticated, whose update
        listener then restarts the bridge with the new key and pin, without reloading the entry (decision 23)."""
        info = self._info
        data = {
            CONF_BRIDGE_IP: info.ip,
            CONF_BRIDGE_ID: info.bridge_id,
            CONF_APP_KEY: self._paired.app_key,
            CONF_CERTIFICATE_SHA256: info.certificate_sha256,
        }
        _LOGGER.info(
            "Paired with the Hue bridge %s at %s; saved its credentials to the configuration.",
            _bridge_name(info),
            info.ip,
        )
        if self.source == SOURCE_REAUTH:
            return self.async_update_and_abort(self._get_reauth_entry(), data_updates=data)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(
                self._get_reconfigure_entry(), unique_id=info.bridge_id, data_updates=data
            )
        placeholders = {
            "name": info.name if info.name is not None else "the Hue bridge",
            "device_type": self._paired.device_type,
        }
        if self._import is not None:
            return self.async_create_entry(
                title=ENTRY_TITLE,
                data=data,
                subentries=[_switch_subentry(switch) for switch in self._import.switches],
                description="paired_imported",
                description_placeholders={**placeholders, "switches": _switch_titles(self._import)},
            )
        return self.async_create_entry(
            title=ENTRY_TITLE, data=data, description="paired", description_placeholders=placeholders
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Keeps the switches' data, with their device keys, out of the answer to the browser, which gets a new entry's
        result without its data but with its subentries. The entry holds them already."""
        result.pop("subentries", None)
        return result

    # Importing a sonoff2hue.yaml.

    async def async_step_import_yaml(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Takes a pasted ``sonoff2hue.yaml``, read as ``AppConfig.Parse`` reads the file and checked as the .NET app's
        ``run`` checks it. The text holds the keys, so it is never shown back, not even after an error."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                config = yaml_import.parse(user_input[CONFIG])
                _check_for_run(config)
            except ConfigError as err:
                errors[CONFIG] = "invalid_config"
                placeholders["error"] = str(err)
            except UnexpectedError as err:
                _LOGGER.exception("Reading the pasted sonoff2hue.yaml failed.")
                errors[CONFIG] = "unreadable_config"
                placeholders["error"] = str(err)
            else:
                self._import = config
                if is_paired(config.hue):
                    return await self.async_step_import_check()
                return await self.async_step_import_pair()
        return self.async_show_form(
            step_id="import_yaml", data_schema=IMPORT_SCHEMA, errors=errors, description_placeholders=placeholders
        )

    async def async_step_import_check(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """With the file's bridge address and key: connects with the key and the pin, as the .NET app's ``run`` did at
        startup, then adds the entry with the file's switches."""
        config = self._imported()
        hue = config.hue
        connected = await self._async_connect(hue)
        if isinstance(connected, _Failed):
            self._failure = connected
            return await self.async_step_import_failed()
        await self.async_set_unique_id(connected)
        placeholders = {"address": hue.bridge_ip or "", "switches": _switch_titles(config)}
        description = "imported"
        file_id = hue.bridge_id
        if file_id and not file_id.isspace() and file_id.casefold() != connected.casefold():
            # Another bridge answers at the file's address; the same id in other letter case needs no word.
            description += "_other_id"
            placeholders |= {"file_bridge_id": file_id, "bridge_id": connected}
        pin = hue.certificate_sha256
        if not pin or pin.isspace():
            description += "_unpinned"
            placeholders["warning"] = NOT_PINNED
        return self.async_create_entry(
            title=ENTRY_TITLE,
            data={
                CONF_BRIDGE_IP: hue.bridge_ip,
                CONF_BRIDGE_ID: connected,
                CONF_APP_KEY: hue.app_key,
                CONF_CERTIFICATE_SHA256: hue.certificate_sha256,
            },
            subentries=[_switch_subentry(switch) for switch in config.switches],
            description=description,
            description_placeholders=placeholders,
        )

    async def _async_connect(self, hue: HueSettings) -> str | _Failed:
        """The bridge's own id, once the bridge accepted the file's key over a connection checked against the file's pin
        (the supervisor's first keep-alive). The id is the unique id, which reauth and reconfigure compare with the
        bridge's, so it isn't taken from the file. ``import_failed`` shows a failure, with "pair the bridge again" where
        the engine says to reconfigure the integration, which doesn't exist yet."""
        try:
            client = create_hue_client(hue)
        except ConfigError as err:
            return _Failed("import_failed", str(err).replace(PAIR_AGAIN, PAIR_INSTEAD))
        try:
            await client.ping()
            info = await get_bridge_info(hue.bridge_ip or "")
        except HueApiError as err:
            return _Failed("import_failed", str(err).replace(PAIR_AGAIN, PAIR_INSTEAD))
        except UnexpectedError as err:
            _LOGGER.exception("Connecting to the Hue bridge at %s failed.", hue.bridge_ip)
            return _Failed("import_failed", str(err))
        finally:
            await client.close()
        return info.bridge_id or ""

    async def async_step_import_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The file's bridge can't be used: try again, pair the bridge instead and keep the switches, or paste the file
        again."""
        return self.async_show_menu(
            step_id="import_failed", menu_options=IMPORT_FAILED_MENU, description_placeholders=self._failure_text()
        )

    def _imported(self) -> EngineConfig:
        """The imported file, which only the import's own steps read, once it is there."""
        assert self._import is not None  # noqa: S101 - set before the import's steps
        return self._import

    async def async_step_import_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pairs the bridge for the file's switches: find it, or enter its address."""
        return self.async_show_menu(
            step_id="import_pair",
            menu_options=PAIR_MENU,
            description_placeholders={"switches": _switch_titles(self._imported())},
        )

    @callback
    def async_remove(self) -> None:
        """Stops a search or a pairing that still runs when the flow is closed or aborted, as Home Assistant also does
        for the task of the progress step shown. Their connections and the search's mDNS socket close as they stop."""
        self._removed = True
        for task in (self._search, self._pairing):
            if task is not None:
                task.cancel()
