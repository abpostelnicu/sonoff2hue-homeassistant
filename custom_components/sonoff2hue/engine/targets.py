"""The Hue lights, rooms and zones that a switch toggles: its ``lights`` and ``groups`` entries, resolved against the
bridge's resources.

An entry names a resource by its name or its id, without regard to case or to white space around it
(:func:`refers_to`). A ``groups`` entry may start with ``room:`` or ``zone:``, for a room and a zone that share a name
(:func:`split_kind`). The switch picker (``picker.py``) matches entries by the same rules.

A target's label shows its id after the name when another resource of its kind has the same name, by :func:`name_key`:
another light, for a light, and another room, or zone, for a room or zone. The switch dialog lists them the same way.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal

from .config import SwitchSettings
from .hue_client import HueGroup, HueLight, HueTarget, HueTargetKind

type GroupKind = Literal["room", "zone"]

GROUP_KINDS: tuple[GroupKind, ...] = ("room", "zone")


def name_key(value: str) -> str:
    """A name or id as entries are compared with it: stripped and casefolded."""
    return value.strip().casefold()


def refers_to(entry: str, resource_id: str, name: str) -> bool:
    """Whether an entry names the resource with this id or name. An empty entry names nothing."""
    key = name_key(entry)
    return key != "" and key in (name_key(resource_id), name_key(name))


def split_kind(entry: str) -> tuple[GroupKind | None, str]:
    """A ``groups`` entry as its ``room:`` or ``zone:`` prefix, in any case, or ``None`` without one, and the name or id,
    both stripped."""
    value = entry.strip()
    for kind in GROUP_KINDS:
        prefix = f"{kind}:"
        if value[: len(prefix)].casefold() == prefix:
            return kind, value[len(prefix) :].strip()
    return None, value


def resolve(
    settings: SwitchSettings, lights: Sequence[HueLight], groups: Sequence[HueGroup], errors: list[str]
) -> list[HueTarget]:
    """The targets of a switch, each once. An entry that matches no resource, or several, adds a message to ``errors``
    with the names it could mean; so does a room or zone without lights. An empty entry, which ``validate_for_run``
    refuses, matches nothing."""
    name = settings.display_name
    light_names = Counter(name_key(light.name) for light in lights)
    group_names = Counter((group.kind, name_key(group.name)) for group in groups)
    targets: list[HueTarget] = []
    for entry in settings.lights:
        reference = entry.strip()
        matches = [light for light in lights if refers_to(reference, light.id, light.name)]
        if len(matches) == 1:
            light = matches[0]
            label_id = light.id if light_names[name_key(light.name)] > 1 else None
            targets.append(HueTarget(HueTargetKind.LIGHT, light.id, light.name, label_id))
        elif not matches:
            known = _quoted(light.name for light in lights)
            errors.append(f"{name}: no Hue light named '{reference}'. Known lights: {known}")
        else:
            ids = _quoted(match.id for match in matches)
            errors.append(f"{name}: {len(matches)} lights are named '{reference}'; use the light id instead ({ids}).")

    for entry in settings.groups:
        raw = entry.strip()
        kind, reference = split_kind(raw)
        found = [
            group
            for group in groups
            if (kind is None or group.kind == kind) and refers_to(reference, group.id, group.name)
        ]
        if not found:
            known = _quoted(_group_name(group) for group in groups)
            errors.append(f"{name}: no Hue room or zone named '{raw}'. Known: {known}")
        elif len(found) > 1:
            names = _quoted(_group_name(group) for group in found)
            errors.append(f"{name}: '{raw}' matches {names}; prefix it with 'room:' or 'zone:'.")
        elif (group := found[0]).grouped_light_id is None:
            errors.append(f"{name}: {group.kind} '{group.name}' has no lights.")
        else:
            label_id = group.id if group_names[group.kind, name_key(group.name)] > 1 else None
            targets.append(HueTarget(HueTargetKind.GROUPED_LIGHT, group.grouped_light_id, group.name, label_id))

    distinct: dict[tuple[HueTargetKind, str], HueTarget] = {}
    for target in targets:
        distinct.setdefault((target.kind, target.id), target)
    return list(distinct.values())


def _group_name(group: HueGroup) -> str:
    return f"{group.kind}:{group.name}"


def _quoted(values: Iterable[str]) -> str:
    """The distinct values, ordered without regard to case and quoted; ``(none)`` for none."""
    distinct = dict.fromkeys(values)
    return ", ".join(f"'{value}'" for value in sorted(distinct, key=str.casefold)) or "(none)"
