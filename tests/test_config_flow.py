"""The setup flow: its one step for now, and the single config entry."""

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sonoff2hue.const import DOMAIN


async def test_the_user_step_adds_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    translations = await async_get_translations(hass, "en", "config", [DOMAIN])
    assert f"component.{DOMAIN}.config.step.user.description" in translations

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "sonoff2hue"
    assert result["data"] == {}
    assert result["result"].state is ConfigEntryState.LOADED


async def test_a_second_entry_is_refused(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, title="sonoff2hue").add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
