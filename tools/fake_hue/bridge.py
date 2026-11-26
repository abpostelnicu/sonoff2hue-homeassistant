"""The fake bridge's state: its lights, the devices that own them, its rooms and zones, which tests can rename and
delete, the application keys it issued, its link button, and the logs of the requests, PUTs and POSTs it answered."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import secrets
import string
import time
from typing import Any

BRIDGE_ID = "FA4E00FFFE000001"
NAME = "Fake Hue bridge"
LINK_BUTTON_WINDOW = 30.0
"""Seconds that pairing stays open after a press of the link button, as on a real bridge."""
MAX_NAME_LENGTH = 32
"""The characters of a room's or zone's name, as on a real bridge."""
# fmt: off
ARCHETYPES = frozenset({
    "living_room", "kitchen", "dining", "bedroom", "kids_bedroom", "bathroom", "nursery", "recreation", "office", "gym",
    "hallway", "toilet", "front_door", "garage", "terrace", "garden", "driveway", "carport", "home", "downstairs",
    "upstairs", "top_floor", "attic", "guest_room", "staircase", "lounge", "man_cave", "computer", "studio", "music",
    "tv", "reading", "closet", "storage", "laundry_room", "balcony", "porch", "barbecue", "pool", "other",
})
# fmt: on
"""The archetypes of a room or zone, one of which a new zone needs."""

_MAX_LINES = 10_000
_KEY_CHARACTERS = string.ascii_uppercase + string.ascii_lowercase + string.digits


class FakeIds:
    """The fake bridge's CLIP v2 resource ids. They are fixed, so that logs and tests can name them."""

    CEILING = "11111111-0000-4000-8000-000000000001"
    FLOOR_LAMP = "11111111-0000-4000-8000-000000000002"
    HALLWAY = "11111111-0000-4000-8000-000000000003"
    LIVING_ROOM = "22222222-0000-4000-8000-000000000001"
    DOWNSTAIRS = "22222222-0000-4000-8000-000000000002"
    LIVING_ROOM_LIGHTS = "33333333-0000-4000-8000-000000000001"
    DOWNSTAIRS_LIGHTS = "33333333-0000-4000-8000-000000000002"
    BRIDGE = "44444444-0000-4000-8000-000000000001"
    CEILING_DEVICE = "55555555-0000-4000-8000-000000000001"
    FLOOR_LAMP_DEVICE = "55555555-0000-4000-8000-000000000002"
    HALLWAY_DEVICE = "55555555-0000-4000-8000-000000000003"
    DIMMER_SWITCH = "55555555-0000-4000-8000-000000000004"
    """The living room's dimmer switch, a device that owns no light."""

    @staticmethod
    def group(number: int) -> str:
        """Room or zone ``number``: 1 is the living room and 2 the zone downstairs, then the zones that
        ``POST /clip/v2/resource/zone`` creates, in order."""
        return f"22222222-0000-4000-8000-{number:012d}"

    @staticmethod
    def grouped_light(number: int) -> str:
        """The grouped light of room or zone ``number``."""
        return f"33333333-0000-4000-8000-{number:012d}"


@dataclass
class _Light:
    id: str
    name: str
    device_id: str
    on: bool = False


@dataclass
class _Group:
    """A room, whose children are devices, or a zone, whose children are lights."""

    id: str
    type: str
    name: str
    archetype: str
    grouped_light_id: str
    lights: list[_Light]
    devices: list[str] = field(default_factory=list)
    """A room's children: the devices of its lights, and others."""

    @property
    def on(self) -> bool:
        return any(light.on for light in self.lights)

    @property
    def children(self) -> list[dict[str, str]]:
        if self.type == "room":
            return [{"rid": device_id, "rtype": "device"} for device_id in self.devices]
        return [{"rid": light.id, "rtype": "light"} for light in self.lights]


class RefusedError(Exception):
    """A request that the fake bridge refuses, as a real bridge would, for the reason it gives."""


@dataclass
class Fault:
    """An answer the fake bridge gives instead of the normal one, for tests: to the next ``count`` requests whose method
    is ``method`` (any if ``None``) and whose path starts with ``path``, after ``delay`` seconds."""

    path: str
    status: int = 503
    body: str = ""
    method: str | None = None
    delay: float = 0.0
    count: int = 1
    content_type: str = "application/json"


@dataclass
class FakeBridge:
    """Three lights, each owned by a device of its own, a room of devices and a zone of lights, all off at the start;
    the application keys it issued; its link button; and the requests it answered. As on a real bridge, a room or zone
    is on while any of its lights is on, and switching it switches all of them. Tests can rename and delete the lights,
    rooms and zones, as the Hue app does; they stay so until the fake bridge starts again."""

    link_button_always_pressed: bool = False
    clock: Callable[[], float] = time.monotonic
    faults: list[Fault] = field(default_factory=list)
    _app_keys: set[str] = field(default_factory=set)
    _requests: list[str] = field(default_factory=list)
    _puts: list[str] = field(default_factory=list)
    _posts: list[str] = field(default_factory=list)
    _link_button_pressed_until: float = float("-inf")

    def __post_init__(self) -> None:
        ceiling = _Light(FakeIds.CEILING, "Ceiling", FakeIds.CEILING_DEVICE)
        floor_lamp = _Light(FakeIds.FLOOR_LAMP, "Floor lamp", FakeIds.FLOOR_LAMP_DEVICE)
        hallway = _Light(FakeIds.HALLWAY, "Hallway", FakeIds.HALLWAY_DEVICE)
        self._lights = [ceiling, floor_lamp, hallway]
        self._groups = [
            _Group(
                FakeIds.LIVING_ROOM,
                "room",
                "Living room",
                "living_room",
                FakeIds.LIVING_ROOM_LIGHTS,
                [ceiling, floor_lamp],
                [FakeIds.CEILING_DEVICE, FakeIds.FLOOR_LAMP_DEVICE, FakeIds.DIMMER_SWITCH],
            ),
            _Group(
                FakeIds.DOWNSTAIRS,
                "zone",
                "Downstairs",
                "downstairs",
                FakeIds.DOWNSTAIRS_LIGHTS,
                [ceiling, floor_lamp, hallway],
            ),
        ]
        self._own_group_ids = frozenset(group.id for group in self._groups)
        self._zones_created = 0

    @staticmethod
    def public_config() -> dict[str, Any]:
        """The unauthenticated ``GET /api/0/config`` answer, which sonoff2hue reads before pairing."""
        return {
            "name": NAME,
            "datastoreversion": "172",
            "swversion": "1972004020",
            "apiversion": "1.72.0",
            "mac": "fa:4e:00:00:00:01",
            "bridgeid": BRIDGE_ID,
            "factorynew": False,
            "replacesbridgeid": None,
            "modelid": "BSB002",
            "starterkitid": "",
        }

    def press_link_button(self) -> None:
        self._link_button_pressed_until = self.clock() + LINK_BUTTON_WINDOW

    def try_pair(self) -> tuple[str, str] | None:
        """A new application key and client key, or ``None`` while the link button isn't pressed."""
        if not self.link_button_always_pressed and self.clock() >= self._link_button_pressed_until:
            return None
        app_key = "".join(secrets.choice(_KEY_CHARACTERS) for _ in range(40))
        self._app_keys.add(app_key)
        return app_key, secrets.token_hex(16).upper()

    def is_authorized(self, app_key: str | None) -> bool:
        return app_key is not None and app_key in self._app_keys

    def get_resources(self, resource_type: str) -> list[dict[str, Any]] | None:
        """The resources of a CLIP v2 type, or ``None`` for a type the fake bridge doesn't have."""
        match resource_type:
            case "light":
                return [
                    {
                        "id": light.id,
                        "type": "light",
                        "owner": {"rid": light.device_id, "rtype": "device"},
                        "metadata": {"name": light.name, "archetype": "classic_bulb"},
                        "on": {"on": light.on},
                    }
                    for light in self._lights
                ]
            case "room" | "zone":
                return [
                    {
                        "id": group.id,
                        "type": group.type,
                        "metadata": {"name": group.name, "archetype": group.archetype},
                        "children": group.children,
                        "services": [{"rid": group.grouped_light_id, "rtype": "grouped_light"}],
                    }
                    for group in self._groups
                    if group.type == resource_type
                ]
            case "grouped_light":
                return [
                    {
                        "id": group.grouped_light_id,
                        "type": "grouped_light",
                        "owner": {"rid": group.id, "rtype": group.type},
                        "on": {"on": group.on},
                    }
                    for group in self._groups
                ]
            case "bridge":
                return [{"id": FakeIds.BRIDGE, "type": "bridge", "bridge_id": BRIDGE_ID.lower()}]
            case _:
                return None

    def try_set_on(self, resource_type: str, resource_id: str, on: bool) -> bool:
        """Switches a light, or all lights of a room or zone through its grouped light; ``False`` if there's none."""
        if resource_type == "light" and (light := next((x for x in self._lights if x.id == resource_id), None)):
            light.on = on
            target = f"light '{light.name}'"
        elif resource_type == "grouped_light" and (
            group := next((x for x in self._groups if x.grouped_light_id == resource_id), None)
        ):
            for member in group.lights:
                member.on = on
            target = f"{group.type} '{group.name}'"
        else:
            return False
        _append(self._puts, f"{target} {_on_off(on)}")
        return True

    def rename(self, resource_id: str, name: str) -> str | None:
        """Renames the light, room or zone with this id, which keeps its id, its owner and its children; returns how it
        was described before, such as ``light 'Hallway'``, or ``None`` when there's none with that id. Raises
        :class:`RefusedError` for a room's or zone's name of more than 32 characters, as a real bridge would."""
        resource = self._find(resource_id)
        if resource is None:
            return None
        if isinstance(resource, _Group) and len(name) > MAX_NAME_LENGTH:
            raise RefusedError(f"a room's or zone's name has 1 to {MAX_NAME_LENGTH} characters")
        described = _describe(resource)
        resource.name = name
        return described

    def delete(self, resource_id: str) -> str | None:
        """Deletes the light, room or zone with this id, as the Hue app does. A light goes with its device: out of the
        rooms, whose children are devices, and out of the zones, whose children are lights. A room or zone takes its
        grouped light with it, and leaves its lights and devices. Returns how it was described, or ``None`` when there's
        none with that id."""
        resource = self._find(resource_id)
        if resource is None:
            return None
        if isinstance(resource, _Light):
            self._lights.remove(resource)
            for group in self._groups:
                if resource in group.lights:
                    group.lights.remove(resource)
                if resource.device_id in group.devices:
                    group.devices.remove(resource.device_id)
        else:
            self._groups.remove(resource)
        return _describe(resource)

    def _find(self, resource_id: str) -> _Light | _Group | None:
        resources: list[_Light | _Group] = [*self._lights, *self._groups]
        return next((resource for resource in resources if resource.id == resource_id), None)

    def record_rejected_put(self, path: str, status: int, reason: str) -> None:
        _append(self._puts, f"rejected PUT {path}: {status} {reason}")

    def create_zone(self, body: Any) -> str:
        """Creates the zone of ``POST /clip/v2/resource/zone``, with a grouped light, and returns its id. ``body`` is
        ``{"children":[{"rid":"<light id>","rtype":"light"},...],"metadata":{"name":"...","archetype":"..."}}``; raises
        :class:`RefusedError` for a body that a real bridge would refuse."""
        metadata = body.get("metadata") if isinstance(body, dict) else None
        if not isinstance(metadata, dict):
            raise RefusedError("the zone needs metadata, with its name and archetype")
        name, archetype = metadata.get("name"), metadata.get("archetype")
        if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME_LENGTH:
            raise RefusedError(f"the zone needs a name of 1 to {MAX_NAME_LENGTH} characters")
        if not isinstance(archetype, str) or archetype not in ARCHETYPES:
            raise RefusedError("the zone needs the archetype of a room or zone")
        children = body.get("children")
        if not isinstance(children, list):
            raise RefusedError("the zone needs a list of children")
        lights: list[_Light] = []
        for child in children:
            if not isinstance(child, dict) or child.get("rtype") != "light":
                raise RefusedError(f"a zone's children are lights, not {json.dumps(child)}")
            light = next((x for x in self._lights if x.id == child.get("rid")), None)
            if light is None:
                raise RefusedError(f"no light with the id '{child.get('rid')}'")
            if light not in lights:
                lights.append(light)
        # Numbered after the bridge's own room and zone and every zone created since the start or the last reset, so that
        # no id is used twice, even after a deletion.
        self._zones_created += 1
        number = len(self._own_group_ids) + self._zones_created
        zone = _Group(FakeIds.group(number), "zone", name, archetype, FakeIds.grouped_light(number), lights)
        self._groups.append(zone)
        _append(self._posts, f"zone '{name}' ({', '.join(light.name for light in lights)})")
        return zone.id

    def record_rejected_post(self, path: str, status: int, reason: str) -> None:
        _append(self._posts, f"rejected POST {path}: {status} {reason}")

    def record_request(self, method: str, path: str, status: int) -> None:
        _append(self._requests, f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} {method} {path} {status}")  # noqa: DTZ005 - local time, for people

    def describe_lights(self) -> str:
        """One line per light, room and zone, e.g. ``room 'Living room' on (Ceiling, Floor lamp)``."""
        lines = [f"light '{light.name}' {_on_off(light.on)}" for light in self._lights]
        lines += [
            f"{group.type} '{group.name}' {_on_off(group.on)} ({', '.join(light.name for light in group.lights)})"
            for group in self._groups
        ]
        return "".join(line + "\n" for line in lines)

    def describe_puts(self) -> str:
        """One line per PUT, in order: what it switched, e.g. ``light 'Hallway' on``, or why it was rejected."""
        return _lines(self._puts)

    def describe_posts(self) -> str:
        """One line per POST of a zone, in order: the zone and its lights, e.g. ``zone 'Hall' (Ceiling, Hallway)``, or
        why it was rejected."""
        return _lines(self._posts)

    def describe_requests(self) -> str:
        """One line per request to the Hue API, in order: time, method, path and status."""
        return _lines(self._requests)

    @property
    def puts(self) -> list[str]:
        return list(self._puts)

    @property
    def posts(self) -> list[str]:
        return list(self._posts)

    def reset(self) -> None:
        """Turns every light off, removes the zones that POSTs created, clears the logs and releases the link button.
        Issued application keys stay valid, and so do the renames and deletions of its own lights, room and zone."""
        for light in self._lights:
            light.on = False
        self._groups = [group for group in self._groups if group.id in self._own_group_ids]
        self._zones_created = 0
        self._puts.clear()
        self._posts.clear()
        self._requests.clear()
        self._link_button_pressed_until = float("-inf")

    def take_fault(self, method: str, path: str) -> Fault | None:
        """The fault for this request, if one is set; it counts down."""
        for fault in self.faults:
            if (fault.method is None or fault.method == method) and path.startswith(fault.path):
                fault.count -= 1
                if fault.count <= 0:
                    self.faults.remove(fault)
                return fault
        return None


def _on_off(on: bool) -> str:
    return "on" if on else "off"


def _describe(resource: _Light | _Group) -> str:
    """A light, room or zone as the logs name it, such as ``room 'Living room'``."""
    return f"light '{resource.name}'" if isinstance(resource, _Light) else f"{resource.type} '{resource.name}'"


def _lines(lines: list[str]) -> str:
    return "".join(line + "\n" for line in lines)


def _append(lines: list[str], line: str) -> None:
    lines.append(line)
    if len(lines) > _MAX_LINES:
        del lines[: len(lines) - _MAX_LINES]
