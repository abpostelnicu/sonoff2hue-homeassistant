"""The setup flow, with reconfigure and reauth: it finds the Hue bridge or takes its address, then pairs with it, as the
.NET app's Hue bridge page (``Web/Management/HueEndpoints.cs``, ``HuePairing.cs`` and the UI's ``views/hue.js``) and its
``hue-pair`` command did, with the same rules and messages.

The steps (decision 22): a menu (``user``, or ``reconfigure``) leads to the search (``discover``, a progress step) and
the bridges found (``pick``), or to the address (``address``). The bridge's identity and the certificate to pin are read
from there, then ``link``, a progress step, waits up to 60 seconds for the link button. On success ``paired`` saves the
bridge; otherwise ``link_timeout`` or ``link_failed`` offers to retry. Reauth goes from ``reauth_confirm`` to ``link``
with the stored bridge.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, Final

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import probatio

from .const import CONF_APP_KEY, CONF_BRIDGE_ID, CONF_BRIDGE_IP, CONF_CERTIFICATE_SHA256, DOMAIN, ENTRY_TITLE
from .engine import text
from .engine.config import ConfigError
from .engine.errors import UnexpectedError
from .engine.hue_address import try_create_base_url
from .engine.hue_client import HueApiError, HueBridgeInfo
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

_LOGGER = logging.getLogger(__name__)

PAIR_MENU: Final = ["discover", "address"]
"""The ways to the bridge: search the network for it, or enter its address."""
SETUP_MENU: Final = [*PAIR_MENU]
"""The ways to set up; the import of a ``sonoff2hue.yaml`` will join them."""

ADDRESS: Final = "address"
BRIDGE: Final = "bridge"
OTHER: Final = "other"
"""The choice of another address than the bridges found."""

ADDRESS_SCHEMA: Final = probatio.Schema({probatio.Required(ADDRESS): TextSelector()})


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

    # The ways in.

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find the bridge, or enter its address."""
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
            current = self._get_reconfigure_entry().data.get(CONF_BRIDGE_ID)
            for bridge in self._bridges:
                if current and bridge.bridge_id and text.equals_ignore_case(bridge.bridge_id, current):
                    return bridge.ip
        return self._bridges[0].ip

    async def async_step_address(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Takes the bridge's address, checked as the .NET app checks it (``HueBridgeAddress``)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        address = ""
        if user_input is not None:
            address = text.trim(user_input[ADDRESS])
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
        """Retry, or choose another bridge, as the web UI offered; reauth is bound to the stored bridge."""
        if self.source == SOURCE_REAUTH:
            return ["retry"]
        return ["retry", "reconfigure" if self.source == SOURCE_RECONFIGURE else "user"]

    async def async_step_retry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pairs again at the same address, reading the bridge again, as the web UI's Retry did."""
        if self.source == SOURCE_REAUTH:
            return await self.async_step_reauth_confirm({})
        return await self.async_step_address({ADDRESS: self._info.ip})

    async def async_step_paired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Saves the bridge: in a new config entry, or in the one being reconfigured or reauthenticated, which then
        reloads."""
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
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), unique_id=info.bridge_id, data_updates=data
            )
        return self.async_create_entry(
            title=ENTRY_TITLE,
            data=data,
            description="paired",
            description_placeholders={
                "name": info.name if info.name is not None else "the Hue bridge",
                "device_type": self._paired.device_type,
            },
        )

    @callback
    def async_remove(self) -> None:
        """Stops a search or a pairing that still runs when the flow is closed or aborted, as Home Assistant also does
        for the task of the progress step shown. Their connections and the search's mDNS socket close as they stop."""
        self._removed = True
        for task in (self._search, self._pairing):
            if task is not None:
                task.cancel()
