"""The switch editor's own checks: a port of the pure logic of the .NET app's web UI dialog ``views/switcheditor.js``, and
of the helpers it uses from ``format.js`` (``sameId``, ``switchLabel``) and ``ui.js`` (``applyFieldErrors``).

The dialog checked the form before it sent anything, with JavaScript's string rules (``jstext.py``), and showed every
problem at once; only a form without problems went to the server, which checked it again (``switch_rules.py``):

- the device: chosen from the list or typed, 4 to 40 ASCII letters and digits, and not used by another switch;
- the name: at most 64 UTF-16 code units, once trimmed;
- at least one light, room or zone;
- a new key, when the current one is to be replaced.

It sent the trimmed name or ``None``; the device id, trimmed if typed; the key ``None`` to keep it, ``""`` to remove it,
else the trimmed input; and the picker's entries (``picker.selection_to_config``), or the typed ones split at commas
(``picker.split_list``).

UI only, and not ported: the dialog's elements, texts, focus and flashing; the inputs' maximum lengths (40 and 64), which
only limit typing; the order of the device list (unused devices first, then by the latest press, then by id with the
browser's collation) and of the picker's lists (``localeCompare``); listing the edited and the preselected device even
when the LAN hasn't reported it; turning identify-by-press on when a switch is added without a preselected device; and
the answers to a stale ETag (412) or a deleted switch (404), which Home Assistant handles itself. A device event counted
as a press for identify-by-press when its ``lastEvent`` was ``press`` and its ``lastPressAt`` was set and new.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Literal

from . import jstext
from .config import SwitchSettings
from .switch_rules import SwitchInput

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9]{4,40}")
"""``/^[A-Za-z0-9]{4,40}$/``, for ``fullmatch``: JavaScript's ``$`` doesn't match before a final newline as Python's does."""

MAX_NAME_LENGTH = 64

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
    """``sameId``: device ids compared with JavaScript's ``toLowerCase()``, without trimming."""
    return jstext.lower(jstext.string(a)) == jstext.lower(jstext.string(b))


def switch_label(name: str | None, device_id: str | None) -> str:
    """``switchLabel``: the trimmed name, else the device id, else "Switch"."""
    return (jstext.trim(name) if name is not None else "") or device_id or "Switch"


def used_by(switches: Iterable[SwitchSettings], device_id: str | None, original_id: str | None) -> str | None:
    """``usedBy``: the label of the first other switch with the device, or ``None``. ``original_id`` is the edited
    switch's id, ``None`` when one is added. The dialog read the switches from ``GET /api/config``, which gave their
    normalized ids."""
    for switch in switches:
        switch_id = switch.normalized_device_id
        if same_id(switch_id, device_id) and not same_id(switch_id, original_id):
            return switch_label(switch.name, switch_id)
    return None


def current_device_id(manual: bool, typed: str, selected: str | None) -> str:
    """``currentDeviceId``: the typed id, trimmed, or the chosen one."""
    return jstext.trim(typed) if manual else jstext.string(selected)


def device_key_value(mode: KeyMode, typed: str) -> str | None:
    """``deviceKeyValue``: ``None`` keeps the current key, ``""`` removes it, else the typed key, trimmed."""
    if mode == "keep":
        return None
    if mode == "remove":
        return ""
    return jstext.trim(typed)


def key_warning(mode: KeyMode, has_key: bool, device_id: str, original_id: str | None) -> str | None:
    """``updateKeyWarning``: a kept key belongs to the previous device, and the server drops it when the id changes."""
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
    """The body the dialog sent, from its inputs: the name trimmed, or ``None`` if empty."""
    trimmed = jstext.trim(name)
    return SwitchInput(trimmed or None, device_id, device_key_value(key_mode, key_text), list(lights), list(groups))


def join_list(values: Sequence[str] | None) -> str:
    """The typed entries' first text, ``(values ?? []).join(', ')``, when the bridge's lights can't be listed."""
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
    """``validate``: the form's problems, in the dialog's order. ``manual_device`` is set when the id was typed, and
    ``manual_targets`` when the entries were typed because the bridge's lights couldn't be listed."""
    problems: list[Problem] = []
    device_id = jstext.string(body.device_id)
    if not device_id:
        message = "Enter the device id." if manual_device else "Choose the device, or enter its id manually."
        problems.append(Problem("deviceId", message))
    elif not DEVICE_ID_PATTERN.fullmatch(device_id):
        problems.append(Problem("deviceId", "A device id has 4 to 40 letters and digits."))
    elif (owner := used_by(switches, device_id, original_id)) is not None:
        problems.append(Problem("deviceId", f"This device is already used by {owner}."))
    if jstext.length(jstext.string(body.name)) > MAX_NAME_LENGTH:
        problems.append(Problem("name", f"Use at most {MAX_NAME_LENGTH} characters."))
    if not body.lights and not body.groups:
        problems.append(Problem("lights" if manual_targets else "targets", "Choose at least one light, room or zone."))
    if key_mode == "replace" and not body.device_key:
        problems.append(Problem("deviceKey", "Enter the new device key, or keep or remove the current one."))
    return problems


def identify(switches: Iterable[SwitchSettings], original_id: str | None, device_id: str) -> Identified:
    """``onPress`` while identify-by-press waits: a device used by another switch isn't selected."""
    owner = used_by(switches, device_id, original_id)
    if owner is not None:
        return Identified(
            False,
            f"{device_id} reported a press, but it is already used by {owner}. Operate the switch you want to add.",
        )
    return Identified(True, f"Selected {device_id}: it reported a press.")


def field_errors(errors: Mapping[str, list[str]], manual_targets: bool) -> tuple[dict[str, str], list[str]]:
    """``applyFieldErrors`` with the dialog's fields: the server's messages joined by field, in order, and the messages
    of keys that no field has. The picker's field shows the errors of ``targets``, ``lights`` and ``groups`` together,
    and the typed entries have a field each. Keys match without regard to case."""
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
        field = fields.get(jstext.lower(key))
        if field is None:
            unmatched.extend(messages)
        else:
            by_field.setdefault(field, []).extend(messages)
    return {field: " ".join(messages) for field, messages in by_field.items()}, unmatched
