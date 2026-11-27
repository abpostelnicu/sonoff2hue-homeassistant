"""The setup flow (decision 40): a confirmation adds the config entry, whose switches are its subentries, and *Add
switch* opens next.

The entry's runtime starts for real, with its LAN on the fake network of ``tests/fakes.py``. Every result is checked
against ``translations/en.json``: the texts it shows must exist, with the placeholders they use.
"""

import string
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue.const import DOMAIN, ENTRY_TITLE, SUBENTRY_SWITCH

from .fakes import entry

pytestmark = pytest.mark.usefixtures("lan")


async def shown(hass: HomeAssistant, result: ConfigFlowResult) -> ConfigFlowResult:
    """``result``, once Home Assistant has found every text it shows, with the placeholders those use."""
    domain = result.get("translation_domain") or DOMAIN
    texts = await async_get_translations(hass, "en", "config", [domain])
    prefix = f"component.{domain}.config"
    step = f"{prefix}.step.{result.get('step_id')}"
    keys: list[str] = []
    optional: list[str] = []
    match result["type"]:
        case FlowResultType.FORM:
            schema = result["data_schema"]
            keys += [f"{step}.title", *(f"{step}.data.{field}" for field in (schema.schema if schema else ()))]
            keys += [f"{prefix}.error.{error}" for error in (result["errors"] or {}).values()]
            optional.append(f"{step}.description")
        case FlowResultType.ABORT:
            keys.append(f"{prefix}.abort.{result['reason']}")
        case FlowResultType.CREATE_ENTRY:
            # The frontend shows the description's text where there is one, and the default text without one.
            optional.append(f"{prefix}.create_entry.{result.get('description') or 'default'}")
        case other:
            pytest.fail(f"The flow showed a {other}")
    given = set(result.get("description_placeholders") or {})
    for key in [*keys, *(key for key in optional if key in texts)]:
        assert key in texts, f"{key} is missing from translations/en.json"
        used = {field for _, field, _, _ in string.Formatter().parse(texts[key]) if field}
        assert used <= given, f"{key} uses {sorted(used - given)}, which the flow doesn't give"
    return result


async def start(hass: HomeAssistant) -> ConfigFlowResult:
    return await shown(hass, await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}))


async def test_the_setup_asks_only_for_a_confirmation(hass: HomeAssistant) -> None:
    result = await start(hass)

    assert (result["type"], result["step_id"], result["data_schema"], result["errors"]) == (
        FlowResultType.FORM,
        "user",
        None,
        None,
    )
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_confirming_adds_the_entry_and_opens_add_switch(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Over Home Assistant's HTTP flow API, as the frontend uses it: the new entry holds no data, and the answer names
    the switch dialog to open next, which shows the ways to the MINIR4M."""
    assert await async_setup_component(hass, "config", {})
    client = await hass_client()
    path = "/api/config/config_entries/flow"

    async def answer(request: Any) -> dict[str, Any]:
        response = await request
        assert response.status == 200, await response.text()
        step: dict[str, Any] = await response.json()
        return step

    step = await answer(client.post(path, json={"handler": DOMAIN}))
    assert (step["type"], step["step_id"], step["data_schema"]) == ("form", "user", [])

    created = await answer(client.post(f"{path}/{step['flow_id']}", json={}))

    [config_entry] = hass.config_entries.async_entries(DOMAIN)
    assert (created["type"], created["title"], created["result"]["entry_id"]) == (
        "create_entry",
        ENTRY_TITLE,
        config_entry.entry_id,
    )
    assert (config_entry.title, config_entry.data, config_entry.version, config_entry.minor_version) == (
        ENTRY_TITLE,
        {},
        2,
        1,
    )
    assert (config_entry.state, config_entry.subentries) == (ConfigEntryState.LOADED, {})
    flow_type, flow_id = created["next_flow"]
    assert flow_type == "config_subentries_flow"

    step = await answer(client.get(f"/api/config/config_entries/subentries/flow/{flow_id}"))

    assert (step["type"], step["handler"], step["step_id"], step["menu_options"]) == (
        "menu",
        [config_entry.entry_id, SUBENTRY_SWITCH],
        "user",
        ["identify", "pick", "manual"],
    )


async def test_no_switch_dialog_follows_an_entry_that_failed_to_set_up(hass: HomeAssistant) -> None:
    """*Add switch* needs the loaded entry, so it doesn't open after a failed setup."""
    result = await start(hass)

    with patch("custom_components.sonoff2hue.async_setup_entry", side_effect=ConfigEntryError("Broken")):
        result = await shown(hass, await hass.config_entries.flow.async_configure(result["flow_id"], {}))

    assert (result["type"], result["title"]) == (FlowResultType.CREATE_ENTRY, ENTRY_TITLE)
    assert "next_flow" not in result
    [config_entry] = hass.config_entries.async_entries(DOMAIN)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.subentries.async_progress() == []


async def test_a_second_entry_is_refused(hass: HomeAssistant) -> None:
    entry().add_to_hass(hass)

    result = await start(hass)

    assert (result["type"], result["reason"], result["translation_domain"]) == (
        FlowResultType.ABORT,
        "single_instance_allowed",
        "homeassistant",
    )


async def test_the_entry_offers_to_add_and_edit_switches(hass: HomeAssistant) -> None:
    """The integration's page has *Add switch*, and each switch *Edit switch*, with their texts; the entry itself has
    nothing to reconfigure."""
    config_entry = entry()
    config_entry.add_to_hass(hass)
    await (await async_get_integration(hass, DOMAIN)).async_get_platform("config_flow")

    assert config_entry.supported_subentry_types == {SUBENTRY_SWITCH: {"supports_reconfigure": True}}
    assert not config_entry.supports_reconfigure
    texts = await async_get_translations(hass, "en", "config_subentries", [DOMAIN])
    prefix = f"component.{DOMAIN}.config_subentries.{SUBENTRY_SWITCH}"
    for key in ("entry_type", "initiate_flow.user", "initiate_flow.reconfigure"):
        assert texts.get(f"{prefix}.{key}"), f"{prefix}.{key} is missing from translations/en.json"
