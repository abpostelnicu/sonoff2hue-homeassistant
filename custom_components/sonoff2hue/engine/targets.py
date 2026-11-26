"""The Hue lights, rooms and zones that a switch toggles: its ``lights`` and ``groups`` entries, resolved against the
bridge's resources.

An entry names a resource by its name or its id, without regard to case or to white space around it
(:func:`refers_to`). A ``groups`` entry may start with ``room:`` or ``zone:``, for a room and a zone that share a name
(:func:`split_kind`). The switch picker (``picker.py``) matches entries by the same rules, and saves a resource as
:func:`encode_light` and :func:`encode_group` spell it.

A target's label shows its id after the name when another resource of its kind has the same name, by :func:`name_key`:
another light, for a light, and another room, or zone, for a room or zone. The switch dialog lists them the same way.

Renames are followed with the remembered resources (decision 32): the light, room or zone that each entry last found,
by its kind, id and name, keyed by the switch and the entry (:class:`EntryKey`). An entry that matches no resource by
its name or id, or several, resolves to its remembered resource while the bridge has that one, of the same kind and
id. When the entry no longer names it, the resource was renamed, and the picker's spelling of it is the entry's new
one. An entry that resolves neither way is missing: the resolver's message says why, and the switch toggles the rest.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol

from .config import SwitchSettings
from .hue_client import HueGroup, HueLight, HueTarget, HueTargetKind

type GroupKind = Literal["room", "zone"]
type ResourceKind = Literal["light", "room", "zone"]
type Field = Literal["lights", "groups"]
"""The setting that an entry is in."""

GROUP_KINDS: tuple[GroupKind, ...] = ("room", "zone")


class Named(Protocol):
    """A light, room or zone, as the picker's spelling of it needs it."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...


class EntryKey(NamedTuple):
    """An entry of a switch, as its remembered resource is keyed: the switch's normalized device id, the setting that the
    entry is in, and the entry as :func:`name_key` compares it."""

    device_id: str
    field: Field
    entry: str

    @classmethod
    def of(cls, settings: SwitchSettings, field: Field, entry: str) -> EntryKey:
        return cls(settings.normalized_device_id, field, name_key(entry))


@dataclass(frozen=True, slots=True)
class RememberedResource:
    """The light, room or zone that an entry found: its kind, its id (a room's or zone's own, not its grouped light's)
    and its name then."""

    kind: ResourceKind
    id: str
    name: str


type Remembered = Mapping[EntryKey, RememberedResource]
"""The resource that each entry last found."""


@dataclass(frozen=True, slots=True)
class Renamed:
    """An entry whose light, room or zone was renamed on the bridge: only its remembered id found it, and the entry no
    longer names it. ``resource`` is the resource as it is now, and ``new_entry`` the picker's spelling of it."""

    key: EntryKey
    entry: str
    """The entry as the switch has it."""
    old_name: str
    """The resource's name as it was remembered."""
    resource: RememberedResource
    new_entry: str


@dataclass(frozen=True, slots=True)
class SwitchResolution:
    """A switch's entries resolved: its targets, each once; the resolver's message for each entry that resolves to
    nothing; the resource that each entry found, to remember; and the entries whose resources were renamed."""

    targets: list[HueTarget]
    problems: list[str]
    found: dict[EntryKey, RememberedResource]
    renamed: list[Renamed]


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


def keys_of(settings: SwitchSettings) -> list[EntryKey]:
    """The keys of the switch's entries, in order."""
    return [EntryKey.of(settings, "lights", entry) for entry in settings.lights] + [
        EntryKey.of(settings, "groups", entry) for entry in settings.groups
    ]


def resolve(
    settings: SwitchSettings, lights: Sequence[HueLight], groups: Sequence[HueGroup], errors: list[str]
) -> list[HueTarget]:
    """The targets of a switch, each once, by the entries' names and ids alone. An entry that matches no resource, or
    several, adds a message to ``errors`` with the names it could mean; so does a room or zone without lights. An empty
    entry, which ``validate_for_run`` refuses, matches nothing. The switch rules check a switch to save with it."""
    resolution = resolve_switch(settings, lights, groups)
    errors.extend(resolution.problems)
    return resolution.targets


def resolve_switch(
    settings: SwitchSettings,
    lights: Sequence[HueLight],
    groups: Sequence[HueGroup],
    remembered: Remembered | None = None,
) -> SwitchResolution:
    """The switch's entries resolved, as :func:`resolve` does, and with an entry that matches no resource, or several,
    resolved to the resource remembered for it while the bridge has that one."""
    remembered = remembered or {}
    name = settings.display_name
    light_names = Counter(name_key(light.name) for light in lights)
    group_names = Counter((group.kind, name_key(group.name)) for group in groups)
    targets: list[HueTarget] = []
    problems: list[str] = []
    found: dict[EntryKey, RememberedResource] = {}
    renamed: list[Renamed] = []

    for entry in settings.lights:
        key = EntryKey.of(settings, "lights", entry)
        reference = entry.strip()
        matches = [light for light in lights if refers_to(reference, light.id, light.name)]
        memory = remembered.get(key)
        light = matches[0] if len(matches) == 1 else _remembered_light(memory, lights)
        if light is None:
            if matches:
                ids = _quoted(match.id for match in matches)
                problems.append(
                    f"{name}: {len(matches)} lights are named '{reference}'; use the light id instead ({ids})."
                )
            else:
                known = _quoted(item.name for item in lights)
                problems.append(f"{name}: no Hue light named '{reference}'. Known lights: {known}")
            continue

        resource = RememberedResource("light", light.id, light.name)
        found[key] = resource
        if memory is not None and len(matches) != 1 and not refers_to(reference, light.id, light.name):
            renamed.append(Renamed(key, entry, memory.name, resource, encode_light(light, lights)))
        label_id = light.id if light_names[name_key(light.name)] > 1 else None
        targets.append(HueTarget(HueTargetKind.LIGHT, light.id, light.name, label_id))

    for entry in settings.groups:
        key = EntryKey.of(settings, "groups", entry)
        raw = entry.strip()
        kind, reference = split_kind(raw)
        matching = [
            group
            for group in groups
            if (kind is None or group.kind == kind) and refers_to(reference, group.id, group.name)
        ]
        memory = remembered.get(key)
        group = matching[0] if len(matching) == 1 else _remembered_group(memory, kind, groups)
        if group is None:
            if matching:
                names = _quoted(map(_group_name, matching))
                problems.append(f"{name}: '{raw}' matches {names}; prefix it with 'room:' or 'zone:'.")
            else:
                problems.append(
                    f"{name}: no Hue room or zone named '{raw}'. Known: {_quoted(map(_group_name, groups))}"
                )
            continue

        group_kind: GroupKind = "room" if group.kind == "room" else "zone"
        resource = RememberedResource(group_kind, group.id, group.name)
        found[key] = resource
        if memory is not None and len(matching) != 1 and not refers_to(reference, group.id, group.name):
            same_kind = [other for other in groups if other.kind == group.kind]
            renamed.append(Renamed(key, entry, memory.name, resource, encode_group(group_kind, group, same_kind)))
        if group.grouped_light_id is None:
            problems.append(f"{name}: {group.kind} '{group.name}' has no lights.")
            continue
        label_id = group.id if group_names[group.kind, name_key(group.name)] > 1 else None
        targets.append(HueTarget(HueTargetKind.GROUPED_LIGHT, group.grouped_light_id, group.name, label_id))

    distinct: dict[tuple[HueTargetKind, str], HueTarget] = {}
    for target in targets:
        distinct.setdefault((target.kind, target.id), target)
    return SwitchResolution(list(distinct.values()), problems, found, renamed)


def is_unique_name(item: Named, items: Sequence[Named]) -> bool:
    """Whether no other of ``items`` has the item's name, by :func:`name_key`. An empty name is never unique."""
    name = name_key(item.name)
    return name != "" and sum(1 for other in items if name_key(other.name) == name) == 1


def encode_light(light: Named, lights: Sequence[Named]) -> str:
    """How the picker saves a light: its name if no other light has that name, else its id."""
    if is_unique_name(light, lights):
        return light.name.strip()
    return light.id


def encode_group(kind: GroupKind, group: Named, groups: Sequence[Named]) -> str:
    """How the picker saves a room or zone: ``room:<name>`` or ``zone:<name>`` if the name is unique among ``groups``,
    the rooms or the zones, else its id."""
    if is_unique_name(group, groups):
        return f"{kind}:{group.name.strip()}"
    return group.id


def _remembered_light(memory: RememberedResource | None, lights: Sequence[HueLight]) -> HueLight | None:
    """The bridge's light that ``memory`` is, if it is a light and the bridge has it."""
    if memory is None or memory.kind != "light":
        return None
    return next((light for light in lights if light.id == memory.id), None)


def _remembered_group(
    memory: RememberedResource | None, kind: GroupKind | None, groups: Sequence[HueGroup]
) -> HueGroup | None:
    """The bridge's room or zone that ``memory`` is, if it is of the entry's kind and the bridge has it."""
    if memory is None or memory.kind == "light" or (kind is not None and memory.kind != kind):
        return None
    return next((group for group in groups if group.kind == memory.kind and group.id == memory.id), None)


def _group_name(group: HueGroup) -> str:
    return f"{group.kind}:{group.name}"


def _quoted(values: Iterable[str]) -> str:
    """The distinct values, ordered without regard to case and quoted; ``(none)`` for none."""
    distinct = dict.fromkeys(values)
    return ", ".join(f"'{value}'" for value in sorted(distinct, key=str.casefold)) or "(none)"
