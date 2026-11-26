"""The room or zone that the switch dialog offers for a switch's single lights (decision 34).

A room's or zone's grouped light switches all its lights with one command, where a switch's single lights are switched
one after another, with one command each. So when a switch would be saved with 2 or more single lights, the dialog
offers what :func:`find_offer` finds against the bridge's lists:

- the room or zone that holds exactly those lights, unless one of the switch's rooms and zones does already, which
  leaves nothing to offer;
- else to create a zone of them on the bridge, named after the switch (:func:`zone_name`).

The room or zone chosen, or the zone created, then takes the place of exactly those lights (:func:`with_group`).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .config import SwitchSettings
from .hue_client import HueGroup, HueLight
from .picker import encode_group, resolve_group
from .switch_rules import BridgeLists, SwitchInput
from .targets import GroupKind, name_key, refers_to

MAX_NAME_LENGTH = 32
"""The characters of a room's or zone's name on the bridge."""


@dataclass(frozen=True, slots=True)
class UseGroup:
    """``group``, a room or zone, holds exactly the switch's single lights, ``lights``."""

    group: HueGroup
    lights: tuple[HueLight, ...]


@dataclass(frozen=True, slots=True)
class CreateZone:
    """No room or zone holds exactly the switch's single lights, ``lights``: a zone of them, named ``name``."""

    name: str
    lights: tuple[HueLight, ...]


type Offer = UseGroup | CreateZone


def single_lights(switch: SwitchSettings, lights: Sequence[HueLight]) -> tuple[HueLight, ...]:
    """The lights that the switch's ``lights`` entries name, each once, in the entries' order. An entry that names no
    light, or several, names none."""
    found: dict[str, HueLight] = {}
    for entry in switch.lights:
        if (light := _light_named(entry, lights)) is not None:
            found.setdefault(light.id, light)
    return tuple(found.values())


def find_offer(switch: SwitchSettings, lists: BridgeLists) -> Offer | None:
    """What to offer for the switch: nothing for fewer than 2 single lights; else the room or zone that holds exactly
    them, rooms first and each kind by name, as the picker lists them, or nothing when the switch has one of those
    already; else a zone of them, named after the switch, or after its device id without a name."""
    chosen = single_lights(switch, lists.lights)
    if len(chosen) < 2:
        return None
    ids = frozenset(light.id for light in chosen)
    rooms, zones = _by_name(lists.groups, "room"), _by_name(lists.groups, "zone")
    if not (matches := [group for group in (*rooms, *zones) if group.light_ids == ids]):
        return CreateZone(zone_name(switch.name or switch.normalized_device_id, lists.groups), chosen)
    if any(_names(entry, group, rooms, zones) for entry in switch.groups for group in matches):
        return None
    return UseGroup(matches[0], chosen)


def zone_name(label: str, groups: Sequence[HueGroup]) -> str:
    """``label`` as the name of a new zone: stripped and cut to the bridge's 32 characters, then with " 2", " 3" and so
    on when a room or zone has that name or id already, compared as the resolver compares names, so that
    ``zone:<name>`` names the new zone alone."""
    taken = {name_key(text) for group in groups for text in (group.name, group.id)}
    name, number = _cut(label, MAX_NAME_LENGTH), 1
    while name_key(name) in taken:
        number += 1
        suffix = f" {number}"
        name = _cut(label, MAX_NAME_LENGTH - len(suffix)) + suffix
    return name


def with_group(switch: SwitchInput, group: HueGroup, lists: BridgeLists) -> SwitchInput:
    """The switch with ``group`` in place of the lights it holds: the ``lights`` entries that name one of them go, and
    the group joins the ``groups`` entries, by the picker's rule (``room:<name>`` or ``zone:<name>``, or its id when
    another of its kind has its name), unless an entry names it already. ``lists`` are the bridge's, which a new zone
    may be missing from; the rest of the switch stays as it is."""
    kind: GroupKind = "room" if group.kind == "room" else "zone"
    groups = [other for other in lists.groups if (other.kind, other.id) != (group.kind, group.id)] + [group]
    rooms, zones = _by_name(groups, "room"), _by_name(groups, "zone")
    lights = [entry for entry in switch.lights if not _names_a_light_of(entry, group, lists.lights)]
    entries = list(switch.groups)
    if not any(_names(entry, group, rooms, zones) for entry in entries):
        entries.append(encode_group(kind, group, rooms if kind == "room" else zones))
    return replace(switch, lights=lights, groups=entries)


def _light_named(entry: str, lights: Sequence[HueLight]) -> HueLight | None:
    """The light that an entry names, by its name or id, when it names exactly one."""
    matches = [light for light in lights if refers_to(entry, light.id, light.name)]
    return matches[0] if len(matches) == 1 else None


def _names_a_light_of(entry: str, group: HueGroup, lights: Sequence[HueLight]) -> bool:
    """Whether a ``lights`` entry names one of the lights that the room or zone holds."""
    light = _light_named(entry, lights)
    return light is not None and light.id in group.light_ids


def _names(entry: str, group: HueGroup, rooms: Sequence[HueGroup], zones: Sequence[HueGroup]) -> bool:
    """Whether a ``groups`` entry names this room or zone."""
    resolved = resolve_group(entry, rooms, zones)
    return resolved.item is not None and (resolved.kind, resolved.item.id) == (group.kind, group.id)


def _by_name(groups: Sequence[HueGroup], kind: str) -> list[HueGroup]:
    """The rooms or the zones in the picker's order: by name without regard to case, those of one name in the bridge's
    order."""
    return sorted((group for group in groups if group.kind == kind), key=lambda group: group.name.casefold())


def _cut(text: str, length: int) -> str:
    return text.strip()[:length].rstrip()
