"""The fake bridge's state: its lights, rooms and zones, the application keys it issued, its link button, and the logs
of the requests and PUTs it answered."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import secrets
import string
import time
from typing import Any

BRIDGE_ID = "FA4E00FFFE000001"
NAME = "Fake Hue bridge"
LINK_BUTTON_WINDOW = 30.0
"""Seconds that pairing stays open after a press of the link button, as on a real bridge."""

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


@dataclass
class _Light:
    id: str
    name: str
    on: bool = False


@dataclass
class _Group:
    id: str
    type: str
    name: str
    archetype: str
    grouped_light_id: str
    lights: list[_Light]

    @property
    def on(self) -> bool:
        return any(light.on for light in self.lights)


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
    """Three lights, a room and a zone, all off at the start; the application keys it issued; its link button; and the
    requests it answered. As on a real bridge, a room or zone is on while any of its lights is on, and switching it
    switches all of them."""

    link_button_always_pressed: bool = False
    clock: Callable[[], float] = time.monotonic
    faults: list[Fault] = field(default_factory=list)
    _app_keys: set[str] = field(default_factory=set)
    _requests: list[str] = field(default_factory=list)
    _puts: list[str] = field(default_factory=list)
    _link_button_pressed_until: float = float("-inf")

    def __post_init__(self) -> None:
        ceiling = _Light(FakeIds.CEILING, "Ceiling")
        floor_lamp = _Light(FakeIds.FLOOR_LAMP, "Floor lamp")
        hallway = _Light(FakeIds.HALLWAY, "Hallway")
        self._lights = [ceiling, floor_lamp, hallway]
        self._groups = [
            _Group(
                FakeIds.LIVING_ROOM,
                "room",
                "Living room",
                "living_room",
                FakeIds.LIVING_ROOM_LIGHTS,
                [ceiling, floor_lamp],
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

    def record_rejected_put(self, path: str, status: int, reason: str) -> None:
        _append(self._puts, f"rejected PUT {path}: {status} {reason}")

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

    def describe_requests(self) -> str:
        """One line per request to the Hue API, in order: time, method, path and status."""
        return _lines(self._requests)

    @property
    def puts(self) -> list[str]:
        return list(self._puts)

    def reset(self) -> None:
        """Turns every light off, clears the logs and releases the link button. Issued application keys stay valid."""
        for light in self._lights:
            light.on = False
        self._puts.clear()
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


def _lines(lines: list[str]) -> str:
    return "".join(line + "\n" for line in lines)


def _append(lines: list[str], line: str) -> None:
    lines.append(line)
    if len(lines) > _MAX_LINES:
        del lines[: len(lines) - _MAX_LINES]
