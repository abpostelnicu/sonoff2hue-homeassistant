"""The rules a switch has to meet before it is saved, and the bridge's lights, rooms and zones for the picker.

The form's own checks (``switch_form.py``) come first, and then these rules check the input again:

- a name of at most 64 characters, without control characters, once stripped;
- a device id of 4 to 40 ASCII letters and digits, once stripped and lowercased;
- a device key of at most 128 characters, without white space or control characters, once stripped;
- at most 50 entries in ``lights`` and in ``groups``, each of 1 to 200 characters without control characters once
  stripped, with duplicates removed without regard to case;
- at least one light, room or zone, and a device id that no other switch has.

Their messages are reported by field (``name``, ``deviceId``, ``deviceKey``, ``lights``, ``groups`` and ``targets``), in
that order. Then the entries are checked against the bridge when it is paired and can be reached, and only warned about
otherwise, by ``targets.resolve`` (a parameter that tests can replace). A device key of ``None`` keeps the current key
and ``""`` removes it, and a changed device id drops a kept key, because the key belongs to the device. The device id is
checked again against the switches as they are after the bridge was asked (``current`` is called twice), because
another flow could have added it meanwhile.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
import re
from typing import Protocol
import unicodedata

from . import targets
from .config import ConfigError, HueSettings, SwitchSettings
from .hue_client import HueApiError, HueGroup, HueLight
from .picker import Resources

MAX_NAME_LENGTH = 64
MAX_DEVICE_KEY_LENGTH = 128
MAX_REFERENCE_LENGTH = 200
MAX_REFERENCES = 50

DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9]{4,40}")
"""A device id: 4 to 40 ASCII letters and digits, for ``fullmatch``."""

DEVICE_ID_EXAMPLE = "1000abcdef"
"""The made-up example in the message about a malformed device id."""

NOT_PAIRED = "The Hue bridge is not paired yet, so the light names were not checked."
PAIR_FIRST = "Pair the Hue bridge first."

type Errors = dict[str, list[str]]
"""Messages by the field they belong to, in the order of the checks."""


@dataclass(slots=True)
class SwitchInput:
    """A switch as the form gives it. ``device_key``: ``None`` keeps the current key, ``""`` removes it. An entry of
    ``lights`` or ``groups`` is ``None`` for a resource without an id, and counts as an empty entry."""

    name: str | None = None
    device_id: str | None = None
    device_key: str | None = None
    lights: Sequence[str | None] | None = None
    groups: Sequence[str | None] | None = None


class SwitchError(Exception):
    """A switch that wasn't saved; ``status`` says why, as an HTTP status code would."""

    status = 0


class SwitchValidationError(SwitchError):
    """400: ``errors`` holds the messages by field, and ``str()`` joins them."""

    status = 400

    def __init__(self, errors: Errors) -> None:
        super().__init__(" ".join(message for messages in errors.values() for message in messages))
        self.errors = errors


class SwitchConflictError(SwitchError):
    """409: the device id belongs to another switch."""

    status = 409


class SwitchNotFoundError(SwitchError):
    """404: the switch to change doesn't exist."""

    status = 404


def _blank(value: str | None) -> bool:
    return not value or value.isspace()


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def is_paired(hue: HueSettings) -> bool:
    """Whether the bridge has an address and an application key."""
    return not _blank(hue.bridge_ip) and not _blank(hue.app_key)


def has_device_key(switch: SwitchSettings) -> bool:
    """Whether the switch has a device key, which the form then offers to keep, replace or remove."""
    return not _blank(switch.device_key)


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

    lights = _clean_references(switch.lights, "lights", errors)
    groups = _clean_references(switch.groups, "groups", errors)
    if "lights" not in errors and "groups" not in errors and not lights and not groups:
        errors["targets"] = ["Choose at least one light, room or zone."]

    settings = SwitchSettings(
        device_id=device_id, name=name or None, device_key=device_key or None, lights=lights, groups=groups
    )
    return errors, settings


def _clean_references(values: Sequence[str | None] | None, field: str, errors: Errors) -> list[str]:
    """The entries, stripped, each once: the first of those that are equal without regard to case. The limits are
    checked before duplicates are removed."""
    cleaned = [(value or "").strip() for value in values or []]
    if len(cleaned) > MAX_REFERENCES:
        errors[field] = [f"Use at most {MAX_REFERENCES} entries."]
    elif any(not value or len(value) > MAX_REFERENCE_LENGTH or _has_control_characters(value) for value in cleaned):
        errors[field] = [f"Each entry needs 1 to {MAX_REFERENCE_LENGTH} characters."]

    distinct: dict[str, str] = {}
    for value in cleaned:
        distinct.setdefault(targets.name_key(value), value)
    return list(distinct.values())


# The bridge


class HueResources(Protocol):
    """What the checks read from the bridge; :class:`~.hue_client.HueClient` has it."""

    async def get_lights(self) -> list[HueLight]: ...

    async def get_groups(self) -> list[HueGroup]: ...


type ConnectHue = Callable[[HueSettings], AbstractAsyncContextManager[HueResources]]
"""Opens the bridge for the Hue settings: raises :class:`ConfigError` when the settings can't be used, and closes the
client afterwards, if it is its own."""

type ResolveTargets = Callable[[SwitchSettings, list[HueLight], list[HueGroup], list[str]], object]
"""``targets.resolve``: adds a message to the list for each entry that doesn't resolve."""


@dataclass(frozen=True, slots=True)
class TargetCheck:
    errors: list[str]
    warnings: list[str]


async def check_targets(
    candidate: SwitchSettings, hue: HueSettings, connect: ConnectHue, resolve: ResolveTargets = targets.resolve
) -> TargetCheck:
    """Resolves the lights, rooms and zones against the bridge when it is paired and reachable; otherwise the switch is
    saved with a warning."""
    if not is_paired(hue):
        return TargetCheck([], [NOT_PAIRED])
    try:
        async with connect(hue) as client:
            lights = await client.get_lights()
            groups = await client.get_groups()
    except HueApiError as err:
        return TargetCheck([], [f"The Hue bridge could not be reached, so the light names were not checked ({err})."])
    except ConfigError as err:
        return TargetCheck(
            [], [f"The Hue settings in the config can't be used, so the light names were not checked ({err})."]
        )
    errors: list[str] = []
    resolve(candidate, lights, groups, errors)
    return TargetCheck(errors, [])


def hue_resources(lights: Sequence[HueLight], groups: Sequence[HueGroup]) -> Resources:
    """The picker's lights, rooms and zones, each list ordered by name without regard to case; resources of the same
    name stay in the bridge's order."""

    def by_name[T: (HueLight, HueGroup)](items: Sequence[T]) -> list[T]:
        return sorted(items, key=lambda item: (item.name or "").casefold())

    return Resources(
        by_name(lights),
        by_name([group for group in groups if group.kind == "room"]),
        by_name([group for group in groups if group.kind == "zone"]),
    )


class ResourcesError(Exception):
    """The picker's resources can't be loaded: ``status`` is 409 when the bridge isn't paired or its settings can't be
    used, and 502 when it can't be reached. The lights can then be typed."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def load_resources(hue: HueSettings, connect: ConnectHue) -> Resources:
    """The bridge's lights, rooms and zones for the picker."""
    if not is_paired(hue):
        raise ResourcesError(409, PAIR_FIRST)
    try:
        async with connect(hue) as client:
            lights = await client.get_lights()
            groups = await client.get_groups()
    except HueApiError as err:
        raise ResourcesError(502, str(err)) from err
    except ConfigError as err:
        raise ResourcesError(409, str(err)) from err
    return hue_resources(lights, groups)


# Adding and changing a switch


@dataclass(frozen=True, slots=True)
class SwitchWrite:
    """A switch to save, with the warnings to show. ``index`` is the position of the switch it replaces."""

    switch: SwitchSettings
    warnings: list[str]
    index: int | None = None


def _position(switches: Sequence[SwitchSettings], device_id: str) -> int | None:
    """The position of the switch with ``device_id``, without regard to case."""
    return next((index for index, switch in enumerate(switches) if switch.has_device_id(device_id)), None)


async def create_switch(
    current: Callable[[], Sequence[SwitchSettings]],
    switch: SwitchInput,
    hue: HueSettings,
    connect: ConnectHue,
    resolve: ResolveTargets = targets.resolve,
) -> SwitchWrite:
    """The switch to add. Raises :class:`SwitchError`."""
    errors, candidate = validate(switch)
    if errors:
        raise SwitchValidationError(errors)
    conflict = f"Device {candidate.device_id} is already configured. Edit that switch instead."
    if _position(current(), candidate.device_id) is not None:
        raise SwitchConflictError(conflict)

    check = await check_targets(candidate, hue, connect, resolve)
    if check.errors:
        raise SwitchValidationError({"targets": check.errors})

    if _position(current(), candidate.device_id) is not None:
        raise SwitchConflictError(conflict)
    return SwitchWrite(candidate, check.warnings)


async def update_switch(
    current: Callable[[], Sequence[SwitchSettings]],
    device_id: str,
    switch: SwitchInput,
    hue: HueSettings,
    connect: ConnectHue,
    resolve: ResolveTargets = targets.resolve,
) -> SwitchWrite:
    """The switch that replaces the one with ``device_id``, and its position. Raises :class:`SwitchError`."""
    errors, candidate = validate(switch)
    if errors:
        raise SwitchValidationError(errors)
    current_id = device_id.strip().lower()
    not_found = f"No switch with device id {current_id} is configured."
    if _position(current(), current_id) is None:
        raise SwitchNotFoundError(not_found)

    check = await check_targets(candidate, hue, connect, resolve)
    if check.errors:
        raise SwitchValidationError({"targets": check.errors})

    switches = current()
    index = _position(switches, current_id)
    if index is None:
        raise SwitchNotFoundError(not_found)
    same_device = switches[index].has_device_id(candidate.device_id)
    if not same_device and _position(switches, candidate.device_id) is not None:
        raise SwitchConflictError(f"Device {candidate.device_id} is already configured by another switch.")
    # The device key belongs to the device, so it is only kept while the device stays the same.
    if switch.device_key is None and same_device:
        candidate.device_key = switches[index].device_key
    return SwitchWrite(candidate, check.warnings, index)
