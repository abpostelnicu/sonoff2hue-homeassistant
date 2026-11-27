"""The repair flows of the issues that a switch's settings fix: some of its lights are no longer in Home Assistant, or
the migration from sonoff2hue 1.x found no light for some of its lights, rooms and zones (decision 40). The flow
explains the issue, with the issue's placeholders, and Submit opens the switch's *Edit switch* dialog, its subentry's
reconfigure flow. The issue stays until the runtime sees that its condition is gone."""

from homeassistant.components.repairs import FlowType, RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import SOURCE_RECONFIGURE, SubentryFlowContext
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
import probatio

from .const import DOMAIN, SUBENTRY_SWITCH


class EditSwitchRepairFlow(RepairsFlow):
    """Confirms, then hands over to the *Edit switch* dialog of the switch that raised the issue."""

    def __init__(self, entry_id: str | None, subentry_id: str | None) -> None:
        self._entry_id = entry_id
        self._subentry_id = subentry_id

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        if user_input is None:
            # As Home Assistant's own confirm flow does: the step's text takes the issue's placeholders.
            issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
            return self.async_show_form(
                step_id="confirm",
                data_schema=probatio.Schema({}),
                description_placeholders=None if issue is None else issue.translation_placeholders,
            )
        entry = None if self._entry_id is None else self.hass.config_entries.async_get_entry(self._entry_id)
        subentry_id = self._subentry_id
        if entry is None or subentry_id is None or subentry_id not in entry.subentries:
            return self.async_abort(reason="not_found")
        result = await self.hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_SWITCH),
            context=SubentryFlowContext(source=SOURCE_RECONFIGURE, subentry_id=subentry_id),
        )
        if result["type"] is FlowResultType.ABORT:
            # The switch's dialog can't open, as the integration isn't loaded.
            return self.async_abort(reason="not_loaded")
        # An abort keeps the issue, where finishing the repair would delete it before the switch is saved.
        return self.async_abort(reason="edit_switch", next_flow=(FlowType.CONFIG_SUBENTRIES_FLOW, result["flow_id"]))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """The flow of a fixable issue; the runtime gives each the ids of its config entry and of its switch's subentry."""
    entry_id = None if data is None else data.get("entry_id")
    subentry_id = None if data is None else data.get("subentry_id")
    return EditSwitchRepairFlow(
        entry_id if isinstance(entry_id, str) else None, subentry_id if isinstance(subentry_id, str) else None
    )
