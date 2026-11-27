"""The switch subentry flow: adds and changes the switches, one config subentry of type ``switch`` per MINIR4M. Its
rules and messages are the engine's: the form's checks (``switch_form``) and the rules a switch meets before it is
saved (``switch_rules``); this module shows them in Home Assistant's dialogs.

The steps (decision 24): a menu (``user``, or ``reconfigure`` with ``keep`` first) chooses how to find the MINIR4M:
``identify``, a progress step that waits up to 60 seconds for a press; ``pick``, the devices seen on the LAN; or
``manual``, its device id. ``settings`` then takes the name, the lights, with Home Assistant's entity picker, and the
device key, and saves the switch, its lights by their entity registry ids (decision 40). A switch of 2 or more single
lights is first offered the Hue room or zone whose light switches exactly them (``group_offer``), in their place
(decision 34). Home Assistant's own UI deletes a switch.

The flow starts only while the config entry is loaded, as it needs the entry's runtime (decision 23): its registry of
the devices seen on the LAN. A saved switch reaches the runtime through the entry's update listener, which restarts the
supervisor.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import math
from typing import Any, Final

from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import probatio

from .const import CONF_DEVICE_ID, CONF_DEVICE_KEY, CONF_NAME, CONF_TARGETS, CONF_UNMIGRATED, SUBENTRY_SWITCH
from .engine import switch_form, switch_rules
from .engine.config import SwitchSettings
from .engine.press import SwitchEventKind
from .engine.registry import DeviceRegistry, LanDevice
from .engine.switch_form import KeyMode
from .engine.switch_rules import (
    SwitchConflictError,
    SwitchInput,
    SwitchNotFoundError,
    SwitchValidationError,
    SwitchWrite,
)
from .lights import LIGHT_DOMAIN, GroupOffer, entity_ids_of, find_group, resolve_switch, target_of
from .runtime import Sonoff2HueConfigEntry, config_from_entry

IDENTIFY_TIMEOUT: Final = 60.0
"""How long ``identify`` waits for a press."""

DEVICE_MENU: Final = ["identify", "pick", "manual"]
"""The ways to the MINIR4M: press its wall switch, pick it from the devices seen on the LAN, or enter its device id."""

# The fields of the dialogs.
DEVICE: Final = "device"
DEVICE_ID: Final = "device_id"
NAME: Final = "name"
LIGHTS: Final = "lights"
ADVANCED: Final = "advanced"
KEY_MODE: Final = "key_mode"
NEW_DEVICE_KEY: Final = "new_device_key"
DEVICE_KEY: Final = "device_key"

KEY_MODES: Final[list[KeyMode]] = ["keep", "replace", "remove"]
KEY_SELECTOR: Final = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off"))
LIGHT_SELECTOR: Final = EntitySelector(EntitySelectorConfig(domain=LIGHT_DOMAIN, multiple=True))
MANUAL_SCHEMA: Final = probatio.Schema({probatio.Optional(DEVICE_ID): TextSelector()})

# The form's fields that the messages of the form's checks and of the switch rules go to, with their error keys and
# placeholders. The device has no field of its own here: its messages go to the form as a whole, with the messages of
# keys that no field has.
_FIELDS: Final = {
    "name": (NAME, "invalid_name", "name_error"),
    "deviceKey": (ADVANCED, "invalid_device_key", "key_error"),
    "targets": (LIGHTS, "invalid_lights", "lights_error"),
}
_PLACEHOLDERS: Final = ("base_error", *(placeholder for _, _, placeholder in _FIELDS.values()))

_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True, slots=True)
class _Press:
    """The device that identify-by-press selected, and the status line that says so."""

    device_id: str
    message: str


def lan_registry(entry: Sonoff2HueConfigEntry) -> DeviceRegistry | None:
    """The devices seen on the LAN, which the runtime of the loaded config entry keeps (decision 23), or ``None`` while
    the entry isn't loaded."""
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data.registry


def _switches_by_subentry(entry: Sonoff2HueConfigEntry) -> dict[str, SwitchSettings]:
    """The configured switches, by the id of their subentry, in their order: the runtime's configuration of the entry
    (``config_from_entry``), whose values are as stored."""
    subentry_ids = [key for key, subentry in entry.subentries.items() if subentry.subentry_type == SUBENTRY_SWITCH]
    return dict(zip(subentry_ids, config_from_entry(entry).switches, strict=True))


def _last_press(device: LanDevice) -> float:
    """When the device last reported a press, as a timestamp; 0 when it hasn't."""
    return 0.0 if device.last_press_at is None else device.last_press_at.timestamp()


def _round(value: float) -> int:
    """The nearest whole number, halves rounded up."""
    return math.floor(value + 0.5)


def _date(at: datetime, now: datetime) -> str:
    """A date as English writes it, in Home Assistant's time zone: "Nov 25 21:04", with the year when it isn't this
    one."""
    local = dt_util.as_local(at)
    year = "" if local.year == dt_util.as_local(now).year else f", {local.year}"
    return f"{_MONTHS[local.month - 1]} {local.day}{year} {local:%H:%M}"


def since(at: datetime, now: datetime) -> str:
    """When something happened, seen from ``now`` in Home Assistant's time zone: "just now", "12 s ago", "3 min ago",
    "2 h ago", "yesterday 21:04" or a date; "in 12 s" or "in 3 min" for a time ahead of ``now``."""
    seconds = _round((now - at).total_seconds())
    if seconds < -5:
        ahead = -seconds
        if ahead < 60:
            return f"in {ahead} s"
        if ahead < 3600:
            return f"in {_round(ahead / 60)} min"
        return _date(at, now)
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    local = dt_util.as_local(at)
    today = dt_util.start_of_local_day(dt_util.as_local(now))
    if local >= today or seconds < 6 * 3600:
        return f"{seconds // 3600} h ago"
    if local >= today - timedelta(days=1):
        return f"yesterday {local:%H:%M}"
    return _date(at, now)


def device_label(device: LanDevice, online: bool, owner: str | None, now: datetime) -> str:
    """A device in the list of the devices seen on the LAN: its id, address and type, whether it is online, its last
    press, and the switch that uses it."""
    last_press = "none yet" if device.last_press_at is None else since(device.last_press_at, now)
    parts = [
        f"{device.device_id} — {device.address or 'no address'} — {device.device_type or 'unknown type'}",
        "Online" if online else "Offline",
        f"Last press {last_press}",
    ]
    if owner is not None:
        parts.append(f"used by {owner}")
    return " · ".join(parts)


def missing_text(removed: Sequence[str], unmigrated: Sequence[str]) -> str:
    """What the settings form says about the switch's lights that it can't show: those that Home Assistant no longer
    has, and those of sonoff2hue 1.x that the migration found no light for; empty when there are none."""
    parts: list[str] = []
    if removed:
        parts.append("Not chosen, as Home Assistant no longer has them:\n" + "\n".join(f"- {r}" for r in removed))
    if unmigrated:
        names = ", ".join(f"`{entry}`" for entry in unmigrated)
        parts.append(f"From sonoff2hue 1.x, without a light in Home Assistant: {names}. Choose their lights again.")
    return "".join(f"\n\n{part}" for part in parts)


def _ended(err: SwitchConflictError | SwitchNotFoundError) -> AbortFlow:
    """Ends the flow with the rules' message: another switch has the device by now, or the switch being changed is
    gone."""
    return AbortFlow("conflict" if isinstance(err, SwitchConflictError) else "not_found", {"error": str(err)})


class SwitchSubentryFlow(ConfigSubentryFlow):
    """Adds a switch, or changes one (``reconfigure``): a MINIR4M and the lights it toggles."""

    def __init__(self) -> None:
        self._original: SwitchSettings | None = None
        """The switch being changed, as it was when its dialog opened; ``None`` while one is added."""
        self._device_id = ""
        self._manual_device = False
        """Whether the device id was typed, which changes the form's message for an empty one."""
        self._device_note: str | None = None
        """The status line of identify-by-press that selected the device."""
        self._device_error: tuple[str, str] | None = None
        """A device that identify-by-press selected, and the form's problem with it, for ``manual`` to show."""
        self._identify: asyncio.Task[_Press | None] | None = None
        self._identify_status: str | None = None
        """What the last press of a device that another switch uses said, while identify-by-press goes on."""
        self._lights: list[str] = []
        """The lights chosen, by their entity ids."""
        self._missing = ""
        """What the form says about the lights of the switch being changed that it can't show."""
        self._values: dict[str, Any] = {}
        """What the form was given, which it suggests when it is shown again."""
        self._body: SwitchInput | None = None
        """The switch that the room or zone offered would take the lights of."""
        self._offer: GroupOffer | None = None
        """The room or zone offered for the switch's single lights."""

    @property
    def _original_id(self) -> str | None:
        """The device id of the switch being changed, normalized."""
        return None if self._original is None else self._original.normalized_device_id

    @property
    def _has_key(self) -> bool:
        """Whether the switch being changed has a device key, which can then be kept, replaced or removed."""
        return self._original is not None and switch_rules.has_device_key(self._original)

    def _switches(self) -> list[SwitchSettings]:
        """The configured switches, in their order."""
        return list(_switches_by_subentry(self._get_entry()).values())

    def _loaded(self) -> bool:
        """Whether the config entry is loaded, as the flow needs its runtime: the devices seen on the LAN."""
        return self._get_entry().state is ConfigEntryState.LOADED

    # The ways in.

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Adds a switch: first its MINIR4M."""
        if not self._loaded():
            return self.async_abort(reason="entry_not_loaded")
        return self._device_menu("user")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Changes a switch: its device stays unless another one is chosen, and its lights are the ones it has, by
        their entity ids now."""
        if not self._loaded():
            return self.async_abort(reason="entry_not_loaded")
        subentry = self._get_reconfigure_subentry()
        original = self._original = _switches_by_subentry(self._get_entry())[subentry.subentry_id]
        self._device_id = original.normalized_device_id
        registry = er.async_get(self.hass)
        self._lights = entity_ids_of(registry, original.targets)
        removed = [
            problem.removeprefix(f"{original.display_name}: ")
            for problem in resolve_switch(registry, original).problems
            if not problem.endswith(" is disabled.")
        ]
        self._missing = missing_text(removed, list(subentry.data.get(CONF_UNMIGRATED) or ()))
        self._values[NAME] = original.name or ""
        return self._device_menu("reconfigure")

    def _device_menu(self, step_id: str) -> SubentryFlowResult:
        """The ways to the MINIR4M; when a switch is changed, keeping its device comes first."""
        if self._original is None:
            return self.async_show_menu(step_id=step_id, menu_options=DEVICE_MENU)
        device_id = self._original.normalized_device_id
        return self.async_show_menu(
            step_id=step_id,
            menu_options=["keep", *DEVICE_MENU],
            description_placeholders={
                "switch": switch_form.switch_label(self._original.name, device_id),
                "device_id": device_id,
            },
        )

    # The device.

    def _device_problem(self, device_id: str, *, manual: bool) -> str | None:
        """The form's problem with the device, if any: no id, a malformed one, or one that another switch uses."""
        problems = switch_form.form_problems(
            SwitchInput(device_id=device_id),
            self._switches(),
            self._original_id,
            manual_device=manual,
            key_mode="set",
        )
        return next((problem.message for problem in problems if problem.field == "deviceId"), None)

    def _select(self, device_id: str, *, manual: bool = False, note: str | None = None) -> None:
        self._device_id, self._manual_device, self._device_note = device_id, manual, note

    async def async_step_keep(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Keeps the switch's device."""
        device_id = self._original_id or ""
        if (problem := self._device_problem(device_id, manual=False)) is not None:
            return self._show_manual(device_id, problem)
        self._select(device_id)
        return await self.async_step_settings()

    async def async_step_identify(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Waits up to 60 seconds for a wall switch to report a press, and selects its device. A press of a device that
        another switch uses says so and goes on waiting."""
        if self._identify is None:
            self._identify_status = None
            self._identify = self.hass.async_create_task(self._async_wait_for_press(), eager_start=False)
        if not self._identify.done():
            return self.async_show_progress(
                step_id="identify",
                progress_action="identify" if self._identify_status is None else "identify_used",
                description_placeholders={"message": self._identify_status or ""},
                progress_task=self._identify,
            )
        task, self._identify = self._identify, None
        press = task.result()
        if press is None:
            return self.async_show_progress_done(next_step_id="identify_timeout")
        if (problem := self._device_problem(press.device_id, manual=False)) is not None:
            self._device_error = (press.device_id, problem)
            return self.async_show_progress_done(next_step_id="manual")
        self._select(press.device_id, note=press.message)
        return self.async_show_progress_done(next_step_id="settings")

    async def _async_wait_for_press(self) -> _Press | None:
        """The first press that ``switch_form.identify`` selects, or ``None`` after 60 seconds. A device event counts as
        a press when its last event is a press with a time that the device didn't have before. When the config entry is
        reloaded, it follows the new runtime's devices."""
        entry = self._get_entry()
        pressed: asyncio.Future[_Press] = self.hass.loop.create_future()
        last_presses: dict[str, datetime | None] = {}
        watched: DeviceRegistry | None = None
        unwatch: Callable[[], None] | None = None

        def on_device(device: LanDevice) -> None:
            key = device.device_id.casefold()
            previous = last_presses.get(key)
            last_presses[key] = device.last_press_at
            if (
                pressed.done()
                or device.last_event is not SwitchEventKind.PRESS
                or device.last_press_at is None
                or device.last_press_at == previous
            ):
                return
            outcome = switch_form.identify(self._switches(), self._original_id, device.device_id)
            if outcome.selected:
                pressed.set_result(_Press(device.device_id, outcome.message))
            elif outcome.message != self._identify_status:
                self._identify_status = outcome.message
                self.async_notify_flow_changed()

        @callback
        def watch() -> None:
            nonlocal watched, unwatch
            registry = lan_registry(entry)
            if registry is watched:
                return
            if unwatch is not None:
                unwatch()
            watched, unwatch = registry, None
            if registry is not None:
                last_presses.update(
                    (device.device_id.casefold(), device.last_press_at) for device in registry.snapshot()
                )
                unwatch = registry.add_changed_callback(on_device)

        watch()
        stop_watching = entry.async_on_state_change(watch)
        try:
            async with asyncio.timeout(IDENTIFY_TIMEOUT):
                return await pressed
        except TimeoutError:
            return None
        finally:
            stop_watching()
            if unwatch is not None:
                unwatch()

    async def async_step_identify_timeout(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """No press within 60 seconds."""
        return self._device_menu("identify_timeout")

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Offers the devices seen on the LAN: those that no switch uses first, then the latest press first, then by
        id."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        chosen = self._device_id
        if user_input is not None:
            chosen = user_input[DEVICE]
            if (problem := self._device_problem(chosen, manual=False)) is None:
                self._select(chosen)
                return await self.async_step_settings()
            errors[DEVICE] = "invalid_device"
            placeholders["error"] = problem
        registry = lan_registry(self._get_entry())
        devices = [] if registry is None else registry.snapshot()
        if registry is None or not devices:
            return await self.async_step_no_devices()
        switches = self._switches()
        owners = {
            device.device_id: switch_form.used_by(switches, device.device_id, self._original_id) for device in devices
        }
        devices.sort(key=lambda device: (owners[device.device_id] is not None, -_last_press(device)))
        now = dt_util.utcnow()
        options = [
            SelectOptionDict(
                value=device.device_id,
                label=device_label(device, registry.is_online(device), owners[device.device_id], now),
            )
            for device in devices
        ]
        schema = probatio.Schema(
            {
                probatio.Required(DEVICE): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        suggested = {DEVICE: chosen} if chosen in owners else {}
        return self.async_show_form(
            step_id="pick",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_no_devices(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """No device seen on the LAN yet."""
        return self._device_menu("no_devices")

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Takes the device id, checked as the form checks it."""
        if user_input is None:
            if self._device_error is not None:
                (selected, error), self._device_error = self._device_error, None
                return self._show_manual(selected, error)
            return self._show_manual("", None)
        typed = user_input.get(DEVICE_ID, "")
        device_id = typed.strip()
        if (problem := self._device_problem(device_id, manual=True)) is not None:
            return self._show_manual(typed, problem)
        self._select(device_id, manual=True)
        return await self.async_step_settings()

    def _show_manual(self, typed: str, problem: str | None) -> SubentryFlowResult:
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(MANUAL_SCHEMA, {DEVICE_ID: typed}),
            errors={} if problem is None else {DEVICE_ID: "invalid_device"},
            description_placeholders={"error": problem or ""},
        )

    # The lights, the name and the key.

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """The switch's name, the lights it toggles, and its device key."""
        if user_input is not None:
            return await self._submit(user_input)
        return self._show_settings()

    def _key_mode(self) -> KeyMode:
        if not self._has_key:
            return "set"
        mode: KeyMode = self._values.get(ADVANCED, {}).get(KEY_MODE, "keep")
        return mode

    def _show_settings(
        self, errors: dict[str, str] | None = None, messages: dict[str, str] | None = None
    ) -> SubentryFlowResult:
        """The form, with the given values suggested again; the device key's section is open when it has an error or
        the warning."""
        errors = errors or {}
        fields: dict[Any, Any] = {probatio.Optional(NAME): TextSelector(), probatio.Optional(LIGHTS): LIGHT_SELECTOR}
        suggested: dict[str, Any] = {NAME: self._values.get(NAME, ""), LIGHTS: list(self._lights)}

        mode = self._key_mode()
        warning = switch_form.key_warning(mode, self._has_key, self._device_id, self._original_id)
        keys: dict[str, Any] = self._values.get(ADVANCED, {})
        if self._has_key:
            key_fields: dict[Any, Any] = {
                probatio.Required(KEY_MODE): SelectSelector(
                    SelectSelectorConfig(options=KEY_MODES, mode=SelectSelectorMode.LIST, translation_key=KEY_MODE)
                ),
                data_entry_flow.Optional(NEW_DEVICE_KEY, visible={"field": KEY_MODE, "value": "replace"}): KEY_SELECTOR,
            }
            suggested[ADVANCED] = {KEY_MODE: mode, NEW_DEVICE_KEY: keys.get(NEW_DEVICE_KEY, "")}
        else:
            key_fields = {probatio.Optional(DEVICE_KEY): KEY_SELECTOR}
            suggested[ADVANCED] = {DEVICE_KEY: keys.get(DEVICE_KEY, "")}
        collapsed = warning is None and ADVANCED not in errors
        fields[probatio.Required(ADVANCED)] = section(probatio.Schema(key_fields), {"collapsed": collapsed})

        device = self._device_note or f"Device {self._device_id}."
        placeholders = dict.fromkeys(_PLACEHOLDERS, "") | (messages or {})
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(probatio.Schema(fields), suggested),
            errors=errors,
            description_placeholders={
                "device": device,
                "missing": self._missing,
                "key_warning": warning or "",
                **placeholders,
            },
        )

    async def _submit(self, user_input: Mapping[str, Any]) -> SubentryFlowResult:
        """Checks the switch with the form's checks, then with the switch rules, and saves it, after offering a room or
        zone for its single lights."""
        self._values[NAME] = name = user_input.get(NAME, "")
        self._values[ADVANCED] = keys = dict(user_input.get(ADVANCED) or {})
        self._lights = list(dict.fromkeys(user_input.get(LIGHTS) or ()))
        mode = self._key_mode()
        key = keys.get(NEW_DEVICE_KEY if self._has_key else DEVICE_KEY, "")
        body = switch_form.form_input(name, self._device_id, mode, key, self._lights)
        problems = switch_form.form_problems(
            body, self._switches(), self._original_id, manual_device=self._manual_device, key_mode=mode
        )
        # The device was checked when it was chosen; another switch that took it since is the rules' conflict.
        if problems := [problem for problem in problems if problem.field != "deviceId"]:
            return self._show_errors({problem.field: problem.message for problem in problems}, [])
        if (offer := find_group(self.hass, self._lights)) is not None:
            self._body, self._offer = body, offer
            return await self.async_step_group_offer()
        return self._check(body)

    def _check(self, body: SwitchInput) -> SubentryFlowResult:
        """Checks the switch with the switch rules, its lights as the settings keep them, and saves it."""
        registry = er.async_get(self.hass)
        stored = replace(body, targets=[target_of(registry, light) for light in body.targets])
        try:
            write = self._write(stored)
        except SwitchValidationError as err:
            return self._show_errors(*switch_form.field_errors(err.errors))
        except (SwitchConflictError, SwitchNotFoundError) as err:
            raise _ended(err) from err
        return self._save(write, body)

    def _show_errors(self, by_field: Mapping[str, str], unmatched: Sequence[str]) -> SubentryFlowResult:
        """The form again, with the messages on their fields; the device's, and those of keys that no field has, on
        the form as a whole."""
        errors: dict[str, str] = {}
        messages: dict[str, str] = {}
        base: list[str] = []
        for field, message in by_field.items():
            if field in _FIELDS:
                name, error, placeholder = _FIELDS[field]
                errors[name] = error
                messages[placeholder] = message
            else:
                base.append(message)
        if base := [*base, *unmatched]:
            errors["base"] = "not_saved"
            messages["base_error"] = " ".join(base)
        return self._show_settings(errors, messages)

    def _write(self, body: SwitchInput) -> SwitchWrite:
        """The switch rules' check of a new switch, or of the switch being changed, by the id it had. Raises
        :class:`~.engine.switch_rules.SwitchError`."""
        if self._original is None:
            return switch_rules.create_switch(self._switches(), body)
        return switch_rules.update_switch(self._switches(), self._original.normalized_device_id, body)

    # A room or zone in place of many lights (decision 34).

    async def async_step_group_offer(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """A Hue room or zone has exactly the switch's single lights: use it in their place, or keep the lights. Home
        Assistant runs the step again when the frontend fetches the flow, which shows the offer again."""
        offer, _ = self._offered()
        return self.async_show_menu(
            step_id="group_offer",
            menu_options=["use_room" if offer.kind == "room" else "use_zone", "keep_lights"],
            description_placeholders={"group": offer.name, "lights": ", ".join(offer.lights)},
        )

    def _offered(self) -> tuple[GroupOffer, SwitchInput]:
        """The offer shown, and the switch that it is for."""
        offer, body = self._offer, self._body
        assert offer is not None  # noqa: S101 - the steps of an offer come from its menu
        assert body is not None  # noqa: S101 - set with the offer
        return offer, body

    async def async_step_use_room(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Checks and saves the switch with the room in the place of its lights."""
        return self._use_group()

    async def async_step_use_zone(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Checks and saves the switch with the zone in the place of its lights."""
        return self._use_group()

    def _use_group(self) -> SubentryFlowResult:
        offer, body = self._offered()
        self._lights = [offer.entity_id]
        return self._check(replace(body, targets=[offer.entity_id]))

    async def async_step_keep_lights(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Checks and saves the switch with its single lights, as they were chosen."""
        _, body = self._offered()
        return self._check(body)

    def _save(self, write: SwitchWrite, body: SwitchInput) -> SubentryFlowResult:
        """Adds the subentry, or updates the one being changed, with the values that the rules give; its unique id is
        the normalized device id, and its title the name, or that id. Saving drops the leftovers of a migrated switch,
        as its lights have been chosen again. The texts name the switch by the name or the device id that the form was
        given."""
        switch = write.switch
        data = {
            CONF_DEVICE_ID: switch.device_id,
            CONF_NAME: switch.name,
            CONF_DEVICE_KEY: switch.device_key,
            CONF_TARGETS: list(switch.targets),
        }
        title = switch.name or switch.normalized_device_id
        placeholders = {"switch": body.name or body.device_id or ""}
        if self._original is None:
            return self.async_create_entry(
                title=title,
                data=data,
                unique_id=switch.normalized_device_id,
                description="added",
                description_placeholders=placeholders,
            )
        self.hass.config_entries.async_update_subentry(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            unique_id=switch.normalized_device_id,
            title=title,
            data=data,
        )
        return self.async_abort(reason="saved", description_placeholders=placeholders)
