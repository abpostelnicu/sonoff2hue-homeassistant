"""The switch form's own checks, before the switch rules (``switch_rules.py``) check a switch again and save it.

The form shows every problem at once, and only a form without problems goes on to the rules:

- the device: chosen from the list or typed, 4 to 40 ASCII letters and digits, and not used by another switch;
- the name: at most 64 characters, once stripped;
- at least one light, room or zone;
- a new key, when the current one is to be replaced.

It gives the rules the stripped name or ``None``; the device id, stripped if typed; the key ``None`` to keep it, ``""``
to remove it, else the stripped input; and the picker's entries (``picker.selection_to_config``), or the typed ones
split at commas (``picker.split_list``).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .config import SwitchSettings
from .switch_rules import DEVICE_ID_PATTERN, MAX_NAME_LENGTH, SwitchInput

KEY_WARNING = (
    "The current key belongs to the previous device. Choose \u201cReplace it\u201d or \u201cRemove it\u201d when you "
    "change the device."
)

type KeyMode = Literal["keep", "replace", "remove", "set"]
"""For a switch with a key (``switch_rules.has_device_key``): keep, replace or remove it. Otherwise ``set``, with an
optional key."""


@dataclass(frozen=True, slots=True)
class Problem:
    """A problem of the form, on its field: ``deviceId``, ``name``, ``targets`` (the picker), ``lights`` (the typed
    entries) or ``deviceKey``."""

    field: str
    message: str


@dataclass(frozen=True, slots=True)
class Identified:
    """What a press does while identify-by-press waits for one: whether it selects the device, and the status line."""

    selected: bool
    message: str


def same_id(a: str | None, b: str | None) -> bool:
    """Whether two device ids are the same, without regard to case; ``None`` is empty."""
    return (a or "").casefold() == (b or "").casefold()


def switch_label(name: str | None, device_id: str | None) -> str:
    """How the form names a switch: its stripped name, else its device id, else "Switch"."""
    return (name or "").strip() or device_id or "Switch"


def used_by(switches: Iterable[SwitchSettings], device_id: str | None, original_id: str | None) -> str | None:
    """The label of the first other switch with the device, or ``None``. ``original_id`` is the edited switch's id,
    ``None`` when one is added."""
    for switch in switches:
        switch_id = switch.normalized_device_id
        if same_id(switch_id, device_id) and not same_id(switch_id, original_id):
            return switch_label(switch.name, switch_id)
    return None


def current_device_id(manual: bool, typed: str, selected: str | None) -> str:
    """The typed id, stripped, or the chosen one."""
    return typed.strip() if manual else (selected or "")


def device_key_value(mode: KeyMode, typed: str) -> str | None:
    """``None`` keeps the current key, ``""`` removes it, else the typed key, stripped."""
    if mode == "keep":
        return None
    if mode == "remove":
        return ""
    return typed.strip()


def key_warning(mode: KeyMode, has_key: bool, device_id: str, original_id: str | None) -> str | None:
    """A kept key belongs to the previous device, and the rules drop it when the id changes."""
    if has_key and mode == "keep" and device_id and not same_id(device_id, original_id):
        return KEY_WARNING
    return None


def form_input(
    name: str,
    device_id: str,
    key_mode: KeyMode,
    key_text: str,
    lights: Sequence[str | None],
    groups: Sequence[str | None],
) -> SwitchInput:
    """The switch the form gives the rules, from its inputs: the name stripped, or ``None`` if empty."""
    return SwitchInput(
        name.strip() or None, device_id, device_key_value(key_mode, key_text), list(lights), list(groups)
    )


def join_list(values: Sequence[str] | None) -> str:
    """The typed entries' first text, when the bridge's lights can't be listed."""
    return ", ".join(values or [])


def form_problems(
    body: SwitchInput,
    switches: Iterable[SwitchSettings],
    original_id: str | None,
    *,
    manual_device: bool,
    manual_targets: bool,
    key_mode: KeyMode,
) -> list[Problem]:
    """The form's problems, in the form's order. ``manual_device`` is set when the id was typed, and ``manual_targets``
    when the entries were typed because the bridge's lights couldn't be listed."""
    problems: list[Problem] = []
    device_id = body.device_id or ""
    if not device_id:
        message = "Enter the device id." if manual_device else "Choose the device, or enter its id manually."
        problems.append(Problem("deviceId", message))
    elif not DEVICE_ID_PATTERN.fullmatch(device_id):
        problems.append(Problem("deviceId", "A device id has 4 to 40 letters and digits."))
    elif (owner := used_by(switches, device_id, original_id)) is not None:
        problems.append(Problem("deviceId", f"This device is already used by {owner}."))
    if len(body.name or "") > MAX_NAME_LENGTH:
        problems.append(Problem("name", f"Use at most {MAX_NAME_LENGTH} characters."))
    if not body.lights and not body.groups:
        problems.append(Problem("lights" if manual_targets else "targets", "Choose at least one light, room or zone."))
    if key_mode == "replace" and not body.device_key:
        problems.append(Problem("deviceKey", "Enter the new device key, or keep or remove the current one."))
    return problems


def identify(switches: Iterable[SwitchSettings], original_id: str | None, device_id: str) -> Identified:
    """What a press of the device does while identify-by-press waits: a device used by another switch isn't
    selected."""
    owner = used_by(switches, device_id, original_id)
    if owner is not None:
        return Identified(
            False,
            f"{device_id} reported a press, but it is already used by {owner}. Operate the switch you want to add.",
        )
    return Identified(True, f"Selected {device_id}: it reported a press.")


def field_errors(errors: Mapping[str, list[str]], manual_targets: bool) -> tuple[dict[str, str], list[str]]:
    """The rules' messages on the form's fields: joined by field, in order, and the messages of keys that no field has.
    The picker's field shows the errors of ``targets``, ``lights`` and ``groups`` together, and the typed entries have a
    field each. Keys match without regard to case."""
    fields = {
        "name": "name",
        "deviceid": "deviceId",
        "devicekey": "deviceKey",
        "targets": "targets",
        "lights": "lights" if manual_targets else "targets",
        "groups": "groups" if manual_targets else "targets",
    }
    by_field: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for key, value in errors.items():
        messages = [message for message in value if message]
        field = fields.get(key.casefold())
        if field is None:
            unmatched.extend(messages)
        else:
            by_field.setdefault(field, []).extend(messages)
    return {field: " ".join(messages) for field, messages in by_field.items()}, unmatched
