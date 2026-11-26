"""The switch form's picker of Hue lights, rooms and zones.

It resolves a switch's ``lights`` and ``groups`` entries against the bridge's resources, by the rules that
``targets.resolve`` follows, and turns the picker's selection back into entries:

- A light is saved by its name if no other light has that name, else by its id. A room or zone is saved as
  ``room:<name>`` or ``zone:<name>`` if no other room, or zone, has that name, else by its id (``targets.encode_light``
  and ``targets.encode_group``, which the runtime also uses for a renamed resource's new name, decision 32).
- An entry that still resolves to a selected resource keeps its own spelling, and a resource is saved once.
- An entry that doesn't resolve (no match, or several) is kept unless it was removed.

Entries are compared by identity: the switch flow removes an unresolved entry by setting ``removed`` on the entry that
:func:`unresolved_entries` returned, and chooses resources by adding their ids to the selection's sets.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .targets import GroupKind, encode_group, encode_light, refers_to, split_kind

type Kind = Literal["light", "room", "zone"]
type ListName = Literal["lights", "groups"]
type Status = Literal["ok", "missing", "ambiguous"]


class Resource(Protocol):
    """A light, room or zone as the picker sees it."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Resources:
    """The bridge's lights, rooms and zones, in the order the picker lists them."""

    lights: Sequence[Resource]
    rooms: Sequence[Resource]
    zones: Sequence[Resource]


@dataclass(frozen=True, slots=True)
class Resolution:
    """An entry resolved: ``ok`` with the resource it names (and its kind, for a room or zone), or ``missing`` or
    ``ambiguous`` with the number of resources it matches."""

    status: Status
    item: Resource | None = None
    kind: GroupKind | None = None
    count: int | None = None


@dataclass(eq=False, slots=True)
class Entry:
    """An entry of the switch's current ``lights`` or ``groups``, as resolved when the picker opened. ``kind`` and
    ``id`` are set when it resolved (``status`` ``ok``), ``count`` when it didn't."""

    list_name: ListName
    value: str
    status: Status
    kind: Kind | None = None
    id: str | None = None
    count: int | None = None
    removed: bool = False


@dataclass(eq=False, slots=True)
class Selection:
    """The ids chosen in the picker, by kind, and the switch's current entries."""

    light: set[str] = field(default_factory=set)
    room: set[str] = field(default_factory=set)
    zone: set[str] = field(default_factory=set)
    entries: list[Entry] = field(default_factory=list)

    def ids(self, kind: Kind) -> set[str]:
        """The ids chosen of ``kind``."""
        if kind == "light":
            return self.light
        return self.room if kind == "room" else self.zone


@dataclass(frozen=True, slots=True)
class Targets:
    """A switch's ``lights`` and ``groups`` as the picker saves them."""

    lights: list[str]
    groups: list[str]


def resolve_light(entry: str, lights: Sequence[Resource]) -> Resolution:
    """A ``lights`` entry, by name or id, against the bridge's lights."""
    found = [light for light in lights if refers_to(entry, light.id, light.name)]
    if len(found) == 1:
        return Resolution("ok", item=found[0])
    return Resolution("ambiguous" if found else "missing", count=len(found))


def resolve_group(entry: str, rooms: Sequence[Resource], zones: Sequence[Resource]) -> Resolution:
    """A ``groups`` entry (``room:<name>``, ``zone:<name>``, a name or an id) against the rooms and zones."""
    kind, reference = split_kind(entry)
    candidates: list[tuple[GroupKind, Resource]] = [
        *(("room", item) for item in (rooms if kind != "zone" else [])),
        *(("zone", item) for item in (zones if kind != "room" else [])),
    ]
    found = [(found_kind, item) for found_kind, item in candidates if refers_to(reference, item.id, item.name)]
    if len(found) == 1:
        return Resolution("ok", item=found[0][1], kind=found[0][0])
    return Resolution("ambiguous" if found else "missing", count=len(found))


def create_selection(resources: Resources, lights: Sequence[str] = (), groups: Sequence[str] = ()) -> Selection:
    """The picker's selection for a switch's current ``lights`` and ``groups``."""
    selection = Selection()
    for value in lights:
        resolved = resolve_light(value, resources.lights)
        if resolved.status == "ok" and resolved.item is not None:
            selection.entries.append(Entry("lights", value, "ok", kind="light", id=resolved.item.id))
            selection.light.add(resolved.item.id)
        else:
            selection.entries.append(Entry("lights", value, resolved.status, count=resolved.count))
    for value in groups:
        resolved = resolve_group(value, resources.rooms, resources.zones)
        if resolved.status == "ok" and resolved.item is not None and resolved.kind is not None:
            selection.entries.append(Entry("groups", value, "ok", kind=resolved.kind, id=resolved.item.id))
            selection.ids(resolved.kind).add(resolved.item.id)
        else:
            selection.entries.append(Entry("groups", value, resolved.status, count=resolved.count))
    return selection


def unresolved_entries(selection: Selection) -> list[Entry]:
    """The entries that don't resolve (missing or ambiguous) and weren't removed."""
    return [entry for entry in selection.entries if entry.status != "ok" and not entry.removed]


def selection_to_config(selection: Selection, resources: Resources) -> Targets:
    """The switch's ``lights`` and ``groups`` for the selection, in order. Entries that still resolve to a selected
    resource keep their spelling, new selections are encoded, and unresolved entries are kept unless removed."""
    result = Targets([], [])
    lists = {"lights": result.lights, "groups": result.groups}
    saved: set[tuple[Kind, str]] = set()
    for entry in selection.entries:
        if entry.status != "ok":
            if not entry.removed:
                lists[entry.list_name].append(entry.value.strip())
        elif entry.kind is not None and entry.id in selection.ids(entry.kind) and (entry.kind, entry.id) not in saved:
            lists[entry.list_name].append(entry.value.strip())
            saved.add((entry.kind, entry.id))

    def add(kind: Kind, items: Sequence[Resource], encode: Callable[[Resource], str]) -> None:
        for item in items:
            if item.id in selection.ids(kind) and (kind, item.id) not in saved:
                (result.lights if kind == "light" else result.groups).append(encode(item))
                saved.add((kind, item.id))

    add("light", resources.lights, lambda item: encode_light(item, resources.lights))
    add("room", resources.rooms, lambda item: encode_group("room", item, resources.rooms))
    add("zone", resources.zones, lambda item: encode_group("zone", item, resources.zones))
    return result


def split_list(text: str) -> list[str]:
    """Typed entries separated by commas, stripped, without empty ones."""
    return [value for value in (part.strip() for part in text.split(",")) if value]
