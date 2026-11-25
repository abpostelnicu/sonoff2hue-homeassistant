"""Toggling the targets of a switch as one unit: which way they go, the retry of a transient failure, failures that
don't stop the other targets, and a light and a room of the fake bridge over HTTPS."""

import asyncio
import logging

import pytest

from custom_components.sonoff2hue.engine import toggler
from custom_components.sonoff2hue.engine.config import SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueTarget, HueTargetKind
from custom_components.sonoff2hue.engine.targets import resolve
from custom_components.sonoff2hue.engine.toggler import HueToggler, ToggleResult

from ..hue import FakeHueHost, InMemoryBridge, RecordedRequest
from .fakes import until

# A real bridge's answer to a PUT for an unreachable light (openhab/openhab-addons#15511).
MAY_NOT_HAVE_EFFECT = (
    '{"data":[{"rid":"l1","rtype":"light"}],"errors":[{"description":"device (light) has communication issues, '
    'command (.on.on) may not have effect"}]}'
)
LOGGER = logging.getLogger("sonoff2hue.test")


def light(target_id: str) -> HueTarget:
    return HueTarget(HueTargetKind.LIGHT, target_id, target_id)


def group(target_id: str) -> HueTarget:
    return HueTarget(HueTargetKind.GROUPED_LIGHT, target_id, target_id)


class StubHue:
    """The bridge's states and the switches it was asked for, failing with 503 as many times as told."""

    def __init__(self, lights: dict[str, bool] | None = None, groups: dict[str, bool] | None = None) -> None:
        self.lights = lights or {}
        self.groups = groups or {}
        self.sets: list[tuple[str | None, bool]] = []
        self.failures_before_success: dict[str, int] = {}
        self.reads: list[str] = []

    async def get_light_states(self) -> dict[str, bool]:
        self.reads.append("light")
        return dict(self.lights)

    async def get_grouped_light_states(self) -> dict[str, bool]:
        self.reads.append("grouped_light")
        return dict(self.groups)

    async def set_on(self, target: HueTarget, on: bool) -> None:
        failures = self.failures_before_success.get(target.id or "", 0)
        if failures > 0:
            self.failures_before_success[target.id or ""] = failures - 1
            raise HueApiError("bridge busy", 503)
        self.sets.append((target.id, on))


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


async def test_a_write_answered_with_errors_counts_as_failed() -> None:
    def respond(request: RecordedRequest) -> tuple[int, str]:
        if request.method == "GET":
            return 200, '{"errors":[],"data":[{"id":"l1","on":{"on":false}},{"id":"l2","on":{"on":false}}]}'
        if request.path.endswith("/l1"):
            return 207, MAY_NOT_HAVE_EFFECT
        return 200, '{"errors":[],"data":[{"rid":"l2","rtype":"light"}]}'

    bridge = InMemoryBridge(respond)

    result = await HueToggler(bridge.client(), LOGGER).toggle([light("l1"), light("l2")])

    assert result == ToggleResult(turned_on=True, changed=1, unchanged=0, failed=1)
    assert len(bridge.requests) == 3


async def test_everything_goes_off_when_any_target_is_on() -> None:
    hue = StubHue(lights={"l1": True, "l2": False}, groups={"g1": False})

    result = await HueToggler(hue, LOGGER).toggle([light("l1"), light("l2"), group("g1")])

    assert result == ToggleResult(turned_on=False, changed=1, unchanged=2, failed=0)
    assert hue.sets == [("l1", False)]


async def test_everything_goes_on_when_all_are_off() -> None:
    hue = StubHue(lights={"l1": False, "l2": False}, groups={"g1": False})

    result = await HueToggler(hue, LOGGER).toggle([light("l1"), light("l2"), group("g1")])

    assert result == ToggleResult(turned_on=True, changed=3, unchanged=0, failed=0)
    assert hue.sets == [("l1", True), ("l2", True), ("g1", True)]


async def test_transient_errors_are_retried_once_and_failures_dont_stop_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hue = StubHue(lights={"l1": False, "l2": False, "l3": False})
    hue.failures_before_success = {"l1": 1, "l2": 5}

    result = await HueToggler(hue, LOGGER).toggle([light("l1"), light("l2"), light("l3")])

    assert result == ToggleResult(turned_on=True, changed=2, unchanged=0, failed=1)
    assert hue.sets == [("l1", True), ("l3", True)]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
        "Could not switch light 'l2' on: bridge busy"
    ]


def answers(*replies: tuple[str, int, str]) -> InMemoryBridge:
    """A bridge in memory that gives the requests of each method and path its replies in turn, then 200 and no
    errors."""
    queues: dict[str, list[tuple[int, str]]] = {}
    for request, status, body in replies:
        queues.setdefault(request, []).append((status, body))

    def respond(request: RecordedRequest) -> tuple[int, str]:
        queue = queues.get(f"{request.method} {request.path}", [])
        return queue.pop(0) if queue else (200, '{"errors":[],"data":[]}')

    return InMemoryBridge(respond)


LIGHT_OFF = '{"errors":[],"data":[{"id":"l1","on":{"on":false}}]}'


async def test_a_state_read_that_fails_transiently_is_tried_once_more() -> None:
    bridge = answers(("GET /clip/v2/resource/light", 503, ""), ("GET /clip/v2/resource/light", 200, LIGHT_OFF))

    result = await HueToggler(bridge.client(), LOGGER).toggle([light("l1")])

    assert result == ToggleResult(turned_on=True, changed=1, unchanged=0, failed=0)
    assert [f"{r.method} {r.path}" for r in bridge.requests] == [
        "GET /clip/v2/resource/light",
        "GET /clip/v2/resource/light",
        "PUT /clip/v2/resource/light/l1",
    ]


@pytest.mark.parametrize(("statuses", "reads"), [([503, 502], 2), ([401], 1), ([404], 1)])
async def test_a_state_read_that_fails_again_or_for_good_ends_the_toggle(statuses: list[int], reads: int) -> None:
    bridge = answers(*[("GET /clip/v2/resource/light", status, "") for status in statuses])

    with pytest.raises(HueApiError) as failure:
        await HueToggler(bridge.client(), LOGGER).toggle([light("l1")])

    assert failure.value.status == statuses[-1]
    assert [r.method for r in bridge.requests] == ["GET"] * reads


async def test_a_write_refused_for_good_is_not_tried_again(caplog: pytest.LogCaptureFixture) -> None:
    bridge = answers(
        ("GET /clip/v2/resource/light", 200, LIGHT_OFF),
        ("PUT /clip/v2/resource/light/l1", 404, '{"errors":[{"description":"not found"}]}'),
    )

    result = await HueToggler(bridge.client(), LOGGER).toggle([light("l1")])

    assert result == ToggleResult(turned_on=True, changed=0, unchanged=0, failed=1)
    assert [r.method for r in bridge.requests] == ["GET", "PUT"]
    message = "Could not switch light 'l1' on: Hue bridge returned 404 Not Found for PUT /clip/v2/resource/light/l1"
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [f"{message}: not found"]


async def test_targets_with_an_unknown_state_are_switched() -> None:
    hue = StubHue(lights={"l1": True})

    await HueToggler(hue, LOGGER).toggle([light("l1"), light("removed")])

    assert hue.sets == [("l1", False), ("removed", False)]


async def test_only_the_states_the_targets_need_are_read() -> None:
    hue = StubHue(lights={"l1": False}, groups={"g1": False})

    await HueToggler(hue, LOGGER).toggle([group("g1")])

    assert hue.reads == ["grouped_light"]


async def test_cancelling_during_the_retry_delay_sends_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toggler, "RETRY_DELAY", 3600)
    hue = StubHue(lights={"l1": False})
    hue.failures_before_success = {"l1": 1}
    toggle = asyncio.create_task(HueToggler(hue, LOGGER).toggle([light("l1")]))
    await until(lambda: hue.failures_before_success["l1"] == 0)

    toggle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await toggle

    assert hue.sets == []


async def test_toggling_a_light_and_a_room_of_the_fake_bridge_switches_them_on_then_off(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        # What the engine does for a switch mapped to the light 'Hallway' and the room 'Living room'.
        settings = SwitchSettings("1000abcdef", lights=["Hallway"], groups=["Living room"])
        errors: list[str] = []
        targets = resolve(settings, await hue.get_lights(), await hue.get_groups(), errors)
        assert errors == []
        switch = HueToggler(hue, LOGGER)

        assert await switch.toggle(targets) == ToggleResult(turned_on=True, changed=2, unchanged=0, failed=0)
        assert await fake_hue.get_text("_fake/lights") == (
            "light 'Ceiling' on\n"
            "light 'Floor lamp' on\n"
            "light 'Hallway' on\n"
            "room 'Living room' on (Ceiling, Floor lamp)\n"
            "zone 'Downstairs' on (Ceiling, Floor lamp, Hallway)\n"
        )

        assert await switch.toggle(targets) == ToggleResult(turned_on=False, changed=2, unchanged=0, failed=0)
    finally:
        await hue.close()

    assert await fake_hue.get_text("_fake/lights") == (
        "light 'Ceiling' off\n"
        "light 'Floor lamp' off\n"
        "light 'Hallway' off\n"
        "room 'Living room' off (Ceiling, Floor lamp)\n"
        "zone 'Downstairs' off (Ceiling, Floor lamp, Hallway)\n"
    )
    assert await fake_hue.get_text("_fake/puts") == (
        "light 'Hallway' on\nroom 'Living room' on\nlight 'Hallway' off\nroom 'Living room' off\n"
    )
