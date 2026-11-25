"""The logbook's entries for the wall switches: each press and what came of it, in the .NET app's words (decision 26).

The .NET app's dashboard showed them under "Recent activity" as its log lines. Here they come from the runtime's bus
events, ``sonoff2hue_press`` and ``sonoff2hue_toggle`` (``const.py``), which name the switch's Home Assistant device in
``device_id`` once it has one, so that the logbook shows them on that device's page too. An entry's name is the switch as
the log lines name it, "<name> (<device id>)" or the device id alone, and the logbook writes it before the message, so
the message is the rest of the line:

- a press: "wall switch operated (seq 12)."
- ``toggled``: "turned OFF light 'Ceiling', group 'Hall' in 85 ms (1 already off, 1 failed)."
- ``failed``: "could not toggle the lights: <the error>"
- ``dropped``: "dropped a press that waited more than 5s for the bridge."

The logbook also describes the events stored in Home Assistant's database, which may be older than this code, and events
that someone else fired, so a value that is missing or of another type gives an entry rather than an error.
"""

from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME, LazyEventPartialState
from homeassistant.const import ATTR_NAME
from homeassistant.core import HomeAssistant, callback

from .const import (
    ATTR_ELAPSED_MS,
    ATTR_FAILED,
    ATTR_MESSAGE,
    ATTR_RESULT,
    ATTR_SEQ,
    ATTR_SWITCH_ID,
    ATTR_TARGETS,
    ATTR_TURNED_ON,
    ATTR_UNCHANGED,
    DOMAIN,
    EVENT_PRESS,
    EVENT_TOGGLE,
    RESULT_DROPPED,
    RESULT_TOGGLED,
)
from .engine.config import SwitchSettings
from .engine.runner import MAX_PRESS_AGE

type Description = dict[str, str]
type DescribeEvent = Callable[[LazyEventPartialState], Description]


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event: Callable[[str, str, DescribeEvent], None]) -> None:
    """Describes the runtime's bus events for the logbook."""
    async_describe_event(DOMAIN, EVENT_PRESS, _describe_press)
    async_describe_event(DOMAIN, EVENT_TOGGLE, _describe_toggle)


@callback
def _describe_press(event: LazyEventPartialState) -> Description:
    """A press, as the runner logs it: {Switch}: wall switch operated (seq {Seq})."""
    data = event.data
    return _entry(data, f"wall switch operated (seq {_text(data.get(ATTR_SEQ))}).")


@callback
def _describe_toggle(event: LazyEventPartialState) -> Description:
    """What came of a press, as the runner logs it: turned on or off, dropped, or failed, as any other result reads too."""
    data = event.data
    result = data.get(ATTR_RESULT)
    if result == RESULT_TOGGLED:
        message = _toggled(data)
    elif result == RESULT_DROPPED:
        message = f"dropped a press that waited more than {MAX_PRESS_AGE.total_seconds():g}s for the bridge."
    else:
        message = f"could not toggle the lights: {_text(data.get(ATTR_MESSAGE))}"
    return _entry(data, message)


def _toggled(data: Mapping[str, Any]) -> str:
    """{Switch}: turned {State} {Targets} in {Elapsed} ms{Notes}."""
    state = "on" if data.get(ATTR_TURNED_ON) else "off"
    notes = []
    if _positive(unchanged := data.get(ATTR_UNCHANGED)):
        notes.append(f"{unchanged} already {state}")
    if _positive(failed := data.get(ATTR_FAILED)):
        notes.append(f"{failed} failed")
    targets = data.get(ATTR_TARGETS)
    names = ", ".join(str(target) for target in targets) if isinstance(targets, list) else _text(targets)
    elapsed = _text(data.get(ATTR_ELAPSED_MS))
    detail = f" ({', '.join(notes)})" if notes else ""
    return f"turned {state.upper()} {names} in {elapsed} ms{detail}."


def _entry(data: Mapping[str, Any], message: str) -> Description:
    """The switch as the runner's log lines name it (``SwitchSettings.display_name``), and the message."""
    switch_id = data.get(ATTR_SWITCH_ID)
    name = data.get(ATTR_NAME)
    switch = SwitchSettings("" if switch_id is None else str(switch_id), None if name is None else str(name))
    return {LOGBOOK_ENTRY_NAME: switch.display_name, LOGBOOK_ENTRY_MESSAGE: message}


def _text(value: object) -> str:
    """A value as .NET's logging writes it: null is "(null)"."""
    return "(null)" if value is None else str(value)


def _positive(value: object) -> bool:
    return isinstance(value, int) and value > 0
