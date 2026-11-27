"""Home Assistant's lights, as the switches toggle them (decision 40).

:class:`HassLights` is the engine's ``LightControl`` for Home Assistant: it reads a light's state from the state machine
and switches it with the ``light.turn_on`` and ``light.turn_off`` actions, as an automation does, so any light that
Home Assistant has can be a switch's, the Hue integration's lights, rooms and zones among them. A switch's settings
keep its lights by their entity registry ids, which stay when an entity id is renamed (:func:`target_of`), and
:func:`resolve` finds the entity ids that they have now. :func:`async_track_targets` tells when that may have changed.
:func:`find_group` finds the Hue room or zone that holds exactly a switch's lights, which switches them with one
command (decision 34). The actions carry the press's context (:data:`PRESS_CONTEXT`), so that the logbook shows the
lights' changes as caused by the switch's Press.
"""

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .engine.config import EngineConfig, SwitchSettings
from .engine.lights import LightError
from .engine.runner import SwitchBinding

LIGHT_DOMAIN: Final = "light"
"""The domain of the lights and of their actions. Calling the actions needs nothing else of the ``light`` component, so
the integration doesn't depend on it."""
PRESS_CONTEXT: ContextVar[Context | None] = ContextVar("sonoff2hue_press_context", default=None)
"""The context of the light actions: a press's, which the runtime sets as it reports the press, or the switch test's.
The runner starts a press's toggle right after it reports the press, and the toggle's task takes a copy of the context
variables as it starts, so each toggle's actions, and the events about its outcome, carry the context of its own press.
Without one, Home Assistant gives each action a new context."""
ATTR_IS_HUE_GROUP: Final = "is_hue_group"
"""The attribute with which the Hue integration marks the light of a room or zone, its grouped light."""
ATTR_HUE_TYPE: Final = "hue_type"
"""The attribute that tells a Hue room (``room``) from a zone (``zone``)."""


class HassLights:
    """Reads Home Assistant's lights and switches them with its ``light`` actions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def is_available(self, light: str) -> bool:
        state = self._hass.states.get(light)
        return state is not None and state.state != STATE_UNAVAILABLE

    def is_on(self, light: str) -> bool | None:
        state = self._hass.states.get(light)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state == STATE_ON

    async def turn(self, light: str, on: bool) -> None:
        """Calls ``light.turn_on`` or ``light.turn_off`` for the light, with the press's context, and waits for it; an
        error that Home Assistant reports, such as the Hue bridge's, is a :class:`LightError` with its message."""
        try:
            await self._hass.services.async_call(
                LIGHT_DOMAIN,
                SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: light},
                blocking=True,
                context=PRESS_CONTEXT.get(),
            )
        except HomeAssistantError as err:
            raise LightError(str(err) or type(err).__name__) from err


def target_of(registry: er.EntityRegistry, entity_id: str) -> str:
    """How a switch's settings keep a light: by its entity registry id, which stays when its entity id is renamed, or
    by its entity id when the registry doesn't have it."""
    entry = registry.async_get(entity_id)
    return entity_id if entry is None else entry.id


def entity_ids_of(registry: er.EntityRegistry, targets: Sequence[str]) -> list[str]:
    """The entity ids that a switch's lights have now, in their order, leaving out those that Home Assistant no longer
    has."""
    resolved = (er.async_resolve_entity_id(registry, target) for target in targets)
    return list(dict.fromkeys(entity_id for entity_id in resolved if entity_id is not None))


def resolve(hass: HomeAssistant, config: EngineConfig) -> list[SwitchBinding]:
    """Each switch of ``config`` with the lights that it toggles, and why each of its other lights is left out."""
    registry = er.async_get(hass)
    return [resolve_switch(registry, switch) for switch in config.switches]


def resolve_switch(registry: er.EntityRegistry, switch: SwitchSettings) -> SwitchBinding:
    """The switch with the entity ids that its lights have now. A light that the registry no longer has, or that is
    disabled, is left out, with a message that names the switch; an entity id that the registry doesn't have is kept,
    and its toggles tell whether Home Assistant has it."""
    targets: list[str] = []
    problems: list[str] = []
    for target in switch.targets:
        entity_id = er.async_resolve_entity_id(registry, target)
        if entity_id is None:
            problems.append(f"{switch.display_name}: {_removed(registry, target)}")
            continue
        entry = registry.async_get(entity_id)
        if entry is not None and entry.disabled_by is not None:
            problems.append(f"{switch.display_name}: {entity_id} is disabled.")
            continue
        if entity_id not in targets:
            targets.append(entity_id)
    return SwitchBinding(switch, targets, problems)


def _removed(registry: er.EntityRegistry, target: str) -> str:
    """Why a light that the registry no longer has is left out, with its entity id if the registry remembers it."""
    deleted = next((entry for entry in registry.deleted_entities.values() if entry.id == target), None)
    if deleted is None:
        return "a light was removed from Home Assistant."
    return f"{deleted.entity_id} was removed from Home Assistant."


@callback
def async_track_targets(hass: HomeAssistant, changed: Callable[[], None]) -> CALLBACK_TYPE:
    """Calls ``changed`` after each change of the entity registry that may change what the switches' lights resolve
    to: a light added, removed, given another entity id, or enabled or disabled. Returns a function that stops it."""

    @callback
    def on_registry(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data = event.data
        if data["action"] == "update":
            ids = (data["entity_id"], data.get("old_entity_id") or "")
            if not ({"entity_id", "disabled_by"} & data["changes"].keys()) or not any(map(_is_light, ids)):
                return
        elif not _is_light(data["entity_id"]):
            return
        changed()

    return hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, on_registry)


def _is_light(entity_id: str) -> bool:
    return entity_id.startswith(f"{LIGHT_DOMAIN}.")


@dataclass(frozen=True, slots=True)
class GroupOffer:
    """The Hue room or zone that holds exactly a switch's lights: its light, its name, whether it is a ``room`` or a
    ``zone``, and the names of the lights that it would take the place of, in the switch's order."""

    entity_id: str
    name: str
    kind: str
    lights: tuple[str, ...]


def find_group(hass: HomeAssistant, lights: Sequence[str]) -> GroupOffer | None:
    """The Hue room or zone whose light switches exactly these 2 or more lights, none of them a room or zone itself: a
    room before a zone, then by name. ``None`` without one."""
    chosen = list(dict.fromkeys(lights))
    states = [hass.states.get(light) for light in chosen]
    if len(chosen) < 2 or any(state is None or state.attributes.get(ATTR_IS_HUE_GROUP) for state in states):
        return None
    members = set(chosen)
    groups = [
        state
        for state in hass.states.async_all(LIGHT_DOMAIN)
        if state.attributes.get(ATTR_IS_HUE_GROUP) is True
        and set(state.attributes.get(ATTR_ENTITY_ID) or ()) == members
    ]
    if not groups:
        return None
    group = min(groups, key=lambda state: (_kind(state.attributes) != "room", state.name.casefold(), state.entity_id))
    names = tuple(state.name for state in states if state is not None)
    return GroupOffer(group.entity_id, group.name, _kind(group.attributes), names)


def _kind(attributes: Mapping[str, Any]) -> str:
    """``zone`` for a Hue zone, else ``room``."""
    return "zone" if attributes.get(ATTR_HUE_TYPE) == "zone" else "room"
