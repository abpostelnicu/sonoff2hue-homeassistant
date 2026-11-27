"""The rules a switch has to meet before it is saved.

The form's own checks (``switch_form.py``) come first, and then these rules check the input again:

- a name of at most 64 characters, without control characters, once stripped;
- a device id of 4 to 40 ASCII letters and digits, once stripped and lowercased;
- a device key of at most 128 characters, without white space or control characters, once stripped;
- at least one light and at most 50, each a reference of 1 to 255 characters without white space or control characters
  once stripped, each kept once;
- a device id that no other switch has.

Their messages are reported by field (``name``, ``deviceId``, ``deviceKey`` and ``targets``), in that order. A device
key of ``None`` keeps the current key and ``""`` removes it, and a changed device id drops a kept key, because the key
belongs to the device.
"""

from collections.abc import Sequence
from dataclasses import dataclass
import re
import unicodedata

from .config import SwitchSettings

MAX_NAME_LENGTH = 64
MAX_DEVICE_KEY_LENGTH = 128
MAX_TARGETS = 50
MAX_TARGET_LENGTH = 255
"""The longest light reference: Home Assistant's entity ids have at most 255 characters."""

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9]{4,40}")
"""A device id: 4 to 40 ASCII letters and digits, for ``fullmatch``."""

DEVICE_ID_EXAMPLE = "1000abcdef"
"""The made-up example in the message about a malformed device id."""

type Errors = dict[str, list[str]]
"""Messages by the field they belong to, in the order of the checks."""


@dataclass(slots=True)
class SwitchInput:
    """A switch as the form gives it. ``device_key``: ``None`` keeps the current key, ``""`` removes it. ``targets`` are
    the lights, as the switch's settings store them."""

    name: str | None = None
    device_id: str | None = None
    device_key: str | None = None
    targets: Sequence[str] = ()


class SwitchError(Exception):
    """A switch that wasn't saved; the subclass says why."""


class SwitchValidationError(SwitchError):
    """The switch breaks the rules: ``errors`` holds the messages by field, and ``str()`` joins them."""

    def __init__(self, errors: Errors) -> None:
        super().__init__(" ".join(message for messages in errors.values() for message in messages))
        self.errors = errors


class SwitchConflictError(SwitchError):
    """The device id belongs to another switch."""


class SwitchNotFoundError(SwitchError):
    """The switch to change doesn't exist."""


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def has_device_key(switch: SwitchSettings) -> bool:
    """Whether the switch has a device key, which the form then offers to keep, replace or remove."""
    return bool(switch.device_key and not switch.device_key.isspace())


def validate(switch: SwitchInput) -> tuple[Errors, SwitchSettings]:
    """The errors by field, none if the input is valid, and the switch to save."""
    errors: Errors = {}

    name = None if switch.name is None else switch.name.strip()
    if name is not None and len(name) > MAX_NAME_LENGTH:
        errors["name"] = [f"Use at most {MAX_NAME_LENGTH} characters."]
    elif name is not None and _has_control_characters(name):
        errors["name"] = ["The name contains control characters."]

    device_id = (switch.device_id or "").strip().lower()
    if not device_id:
        errors["deviceId"] = ["Choose a device or enter its device id."]
    elif not DEVICE_ID_PATTERN.fullmatch(device_id):
        errors["deviceId"] = [f"A device id has 4 to 40 letters and digits, e.g. {DEVICE_ID_EXAMPLE}."]

    device_key = None if switch.device_key is None else switch.device_key.strip()
    if device_key is not None and (
        len(device_key) > MAX_DEVICE_KEY_LENGTH
        or any(char.isspace() for char in device_key)
        or _has_control_characters(device_key)
    ):
        errors["deviceKey"] = [f"A device key has at most {MAX_DEVICE_KEY_LENGTH} characters and no spaces."]

    targets = list(dict.fromkeys(target.strip() for target in switch.targets))
    if not targets:
        errors["targets"] = ["Choose at least one light."]
    elif len(targets) > MAX_TARGETS:
        errors["targets"] = [f"Choose at most {MAX_TARGETS} lights."]
    elif any(
        not target
        or len(target) > MAX_TARGET_LENGTH
        or any(char.isspace() for char in target)
        or _has_control_characters(target)
        for target in targets
    ):
        errors["targets"] = [f"Each light needs 1 to {MAX_TARGET_LENGTH} characters without spaces."]

    settings = SwitchSettings(device_id=device_id, name=name or None, device_key=device_key or None, targets=targets)
    return errors, settings


@dataclass(frozen=True, slots=True)
class SwitchWrite:
    """A switch to save. ``index`` is the position of the switch it replaces."""

    switch: SwitchSettings
    index: int | None = None


def _position(switches: Sequence[SwitchSettings], device_id: str) -> int | None:
    """The position of the switch with ``device_id``, without regard to case."""
    return next((index for index, switch in enumerate(switches) if switch.has_device_id(device_id)), None)


def create_switch(switches: Sequence[SwitchSettings], switch: SwitchInput) -> SwitchWrite:
    """The switch to add to ``switches``. Raises :class:`SwitchError`."""
    errors, candidate = validate(switch)
    if errors:
        raise SwitchValidationError(errors)
    if _position(switches, candidate.device_id) is not None:
        raise SwitchConflictError(f"Device {candidate.device_id} is already configured. Edit that switch instead.")
    return SwitchWrite(candidate)


def update_switch(switches: Sequence[SwitchSettings], device_id: str, switch: SwitchInput) -> SwitchWrite:
    """The switch that replaces the one of ``switches`` with ``device_id``, and its position. Raises
    :class:`SwitchError`."""
    errors, candidate = validate(switch)
    if errors:
        raise SwitchValidationError(errors)
    current_id = device_id.strip().lower()
    index = _position(switches, current_id)
    if index is None:
        raise SwitchNotFoundError(f"No switch with device id {current_id} is configured.")
    same_device = switches[index].has_device_id(candidate.device_id)
    if not same_device and _position(switches, candidate.device_id) is not None:
        raise SwitchConflictError(f"Device {candidate.device_id} is already configured by another switch.")
    # The device key belongs to the device, so it is only kept while the device stays the same.
    if switch.device_key is None and same_device:
        candidate.device_key = switches[index].device_key
    return SwitchWrite(candidate, index)
