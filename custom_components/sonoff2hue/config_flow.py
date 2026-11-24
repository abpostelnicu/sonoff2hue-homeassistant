"""The setup flow. For now a single step that adds the config entry; the setup flow with pairing and the import of a
``sonoff2hue.yaml`` replaces it."""

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class Sonoff2HueConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adds the integration's config entry. There is only one (``single_config_entry``), and Home Assistant aborts
    the flow when it exists."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the step, then add the entry when it is submitted."""
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title="sonoff2hue", data={})
