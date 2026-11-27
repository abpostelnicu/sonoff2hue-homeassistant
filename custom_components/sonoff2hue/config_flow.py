"""The setup flow: one confirmation adds the config entry, whose switches are its subentries (decision 40).

The integration needs nothing of its own to set up: the switches toggle the lights that Home Assistant has, so there is
no bridge to pair. The entry holds no data, and the switch subentry flow (``switch_flow.py``) adds each switch.
"""

from typing import Any

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    FlowType,
    SubentryFlowContext,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType

from .const import DOMAIN, ENTRY_TITLE, ENTRY_VERSION, SUBENTRY_SWITCH
from .switch_flow import SwitchSubentryFlow


class Sonoff2HueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adds the one config entry of sonoff2hue."""

    VERSION = ENTRY_VERSION
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """The switches, one subentry per MINIR4M, which the integration's page adds and changes (decision 24)."""
        return {SUBENTRY_SWITCH: SwitchSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Says what comes next, and adds the entry once confirmed."""
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title=ENTRY_TITLE, data={})

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Opens *Add switch* as soon as the entry is set up, as the entry does nothing without a switch."""
        switch = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, SUBENTRY_SWITCH), context=SubentryFlowContext(source=SOURCE_USER)
        )
        if switch["type"] is not FlowResultType.ABORT:
            result["next_flow"] = (FlowType.CONFIG_SUBENTRIES_FLOW, switch["flow_id"])
        return result
