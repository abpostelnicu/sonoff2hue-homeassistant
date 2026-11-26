"""The repair flows of the issues that pairing the bridge again fixes: the certificate isn't pinned, or it changed
(decision 23), or the bridge answers at another address without a pin (decision 33). The flow explains the issue, with
the issue's placeholders, and Submit opens the integration's reconfigure flow, which pairs the bridge again and pins its
certificate. The issue stays until the runtime sees that its condition is gone."""

from homeassistant.components.repairs import FlowType, RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import probatio

from .const import DOMAIN


class PairAgainRepairFlow(RepairsFlow):
    """Confirms, then hands over to the reconfigure flow of the config entry that raised the issue."""

    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

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
        if entry is None:
            return self.async_abort(reason="not_loaded")
        result = await self.hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        # An abort keeps the issue, where finishing the repair would delete it before the bridge is paired again.
        return self.async_abort(reason="reconfigure", next_flow=(FlowType.CONFIG_FLOW, result["flow_id"]))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """The flow of a fixable issue; the runtime gives each the id of its config entry."""
    entry_id = None if data is None else data.get("entry_id")
    return PairAgainRepairFlow(entry_id if isinstance(entry_id, str) else None)
