"""The repair flows of the issues that a switch's settings fix (decision 40): some of its lights are no longer in Home
Assistant, or the migration from sonoff2hue 1.x found no light for some of them. The flow confirms, with the issue's
placeholders, then opens the switch's *Edit switch* dialog, its subentry's reconfigure flow."""

from homeassistant.components.repairs import FlowType, RepairsFlowManager, repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.repairs import process_repair_fix_flow, start_repair_fix_flow
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import repairs
from custom_components.sonoff2hue.const import CONF_UNMIGRATED, DOMAIN, SUBENTRY_SWITCH

from .fakes import DEVICE_ID, entry, switch
from .platforms import start

HALL = f"Hall ({DEVICE_ID})"
MISSING_TARGETS = f"missing_targets_{DEVICE_ID}"
UNMIGRATED = f"unmigrated_{DEVICE_ID}"
GONE = "0123456789abcdef0123456789abcdef"
"""A made-up entity registry id, which the registry has never had."""

PLACEHOLDERS = {
    MISSING_TARGETS: {"name": HALL, "problems": f"- {HALL}: a light was removed from Home Assistant."},
    UNMIGRATED: {"name": HALL, "entries": "- `Pendant`\n- `room:Hall`"},
}


@pytest.fixture
async def manager(hass: HomeAssistant) -> RepairsFlowManager:
    """Home Assistant's repair flows."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return manager


async def started(hass: HomeAssistant, issue_id: str) -> MockConfigEntry:
    """The entry, set up with the switch Hall, which raises the issue."""
    if issue_id == MISSING_TARGETS:
        subentry = switch(targets=[GONE])
    else:
        subentry = switch(extra={CONF_UNMIGRATED: ["Pendant", "room:Hall"]})
    config_entry = entry(subentry)
    await start(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    return config_entry


def subentry_id_of(config_entry: MockConfigEntry) -> str:
    (subentry_id,) = config_entry.subentries
    return subentry_id


@pytest.mark.parametrize("issue_id", [MISSING_TARGETS, UNMIGRATED])
@pytest.mark.usefixtures("lan")
async def test_the_repair_opens_the_switchs_edit_dialog(
    hass: HomeAssistant, manager: RepairsFlowManager, issue_id: str
) -> None:
    config_entry = await started(hass, issue_id)

    result = await manager.async_init(DOMAIN, context={"issue_id": issue_id})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "confirm")
    assert result["description_placeholders"] == PLACEHOLDERS[issue_id]

    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "edit_switch")
    flow_type, flow_id = result["next_flow"]
    assert flow_type is FlowType.CONFIG_SUBENTRIES_FLOW
    flow = hass.config_entries.subentries.async_get(flow_id)
    assert flow["handler"] == (config_entry.entry_id, SUBENTRY_SWITCH)
    assert (flow["context"]["source"], flow["context"]["subentry_id"], flow["step_id"]) == (
        "reconfigure",
        subentry_id_of(config_entry),
        "reconfigure",
    )
    # The issue stays until the switch is saved with its lights.
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.usefixtures("lan")
async def test_the_frontend_gets_the_switchs_edit_dialog_as_the_next_flow(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    config_entry = await started(hass, UNMIGRATED)
    client = await hass_client()

    result = await start_repair_fix_flow(client, DOMAIN, UNMIGRATED)

    assert (result["type"], result["step_id"], result["description_placeholders"]) == (
        "form",
        "confirm",
        PLACEHOLDERS[UNMIGRATED],
    )

    result = await process_repair_fix_flow(client, result["flow_id"], {})

    assert (result["type"], result["reason"]) == ("abort", "edit_switch")
    flow_type, flow_id = result["next_flow"]
    assert flow_type == "config_subentries_flow"
    assert result["result"]["entry_id"] == config_entry.entry_id
    assert hass.config_entries.subentries.async_get(flow_id)["step_id"] == "reconfigure"


@pytest.mark.usefixtures("lan")
async def test_the_confirmation_shown_again_after_its_issue_went_has_no_placeholders(
    hass: HomeAssistant, manager: RepairsFlowManager
) -> None:
    """As the frontend fetches the flow: here the switch was saved meanwhile, without its leftovers."""
    config_entry = await started(hass, UNMIGRATED)
    result = await manager.async_init(DOMAIN, context={"issue_id": UNMIGRATED})
    (subentry,) = config_entry.subentries.values()
    data = {key: value for key, value in subentry.data.items() if key != CONF_UNMIGRATED}
    hass.config_entries.async_update_subentry(config_entry, subentry, data=data)

    result = await manager.async_configure(result["flow_id"])

    assert (result["type"], result["step_id"], result["description_placeholders"]) == (
        FlowResultType.FORM,
        "confirm",
        None,
    )


@pytest.mark.usefixtures("lan")
async def test_the_repair_of_a_switch_removed_meanwhile_ends(hass: HomeAssistant, manager: RepairsFlowManager) -> None:
    config_entry = await started(hass, MISSING_TARGETS)
    result = await manager.async_init(DOMAIN, context={"issue_id": MISSING_TARGETS})

    hass.config_entries.async_remove_subentry(config_entry, subentry_id_of(config_entry))
    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_found")
    assert hass.config_entries.subentries.async_progress() == []


@pytest.mark.usefixtures("lan")
async def test_the_repair_of_an_entry_removed_meanwhile_ends(hass: HomeAssistant, manager: RepairsFlowManager) -> None:
    config_entry = await started(hass, MISSING_TARGETS)
    result = await manager.async_init(DOMAIN, context={"issue_id": MISSING_TARGETS})

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_found")
    assert hass.config_entries.subentries.async_progress() == []


@pytest.mark.parametrize("data", [None, {"entry_id": 1, "subentry_id": None}], ids=["no data", "not ids"])
async def test_a_repair_without_its_switchs_ids_ends(
    hass: HomeAssistant, data: dict[str, str | int | float | None] | None
) -> None:
    flow = await repairs.async_create_fix_flow(hass, MISSING_TARGETS, data)
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_found")


@pytest.mark.usefixtures("lan")
async def test_the_repair_of_an_entry_that_isnt_loaded_ends(hass: HomeAssistant, manager: RepairsFlowManager) -> None:
    """The switch's dialog needs the integration loaded."""
    config_entry = await started(hass, MISSING_TARGETS)
    result = await manager.async_init(DOMAIN, context={"issue_id": MISSING_TARGETS})

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    result = await manager.async_configure(result["flow_id"], {})

    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "not_loaded")
    assert hass.config_entries.subentries.async_progress() == []
