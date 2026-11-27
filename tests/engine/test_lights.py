"""Tests of the toggler (engine/lights.py): which lights go on or off, which count as failed and why, side by side and
each within 5 seconds, and what it logs."""

import asyncio
from collections.abc import AsyncIterator
import logging

import pytest

from custom_components.sonoff2hue.engine.lights import LightError, LightFailure, Toggler, ToggleResult

from .fakes import VirtualTime, until

LOGGER = logging.getLogger("tests.engine.lights")
CEILING = "light.ceiling"
DESK = "light.desk"
LAMP = "light.lamp"


class FakeLights:
    """Lights for the toggler, by entity id: on (``True``), off (``False``) or in an unknown state (``None``). A light
    that isn't in :attr:`states`, or is in :attr:`unavailable`, is unavailable. :meth:`turn` records each call, waits
    while the light is held or has a delay, raises the light's error if it has one (for one call only if it came from
    :meth:`fail_once`), and else sets its state; a call that is cancelled is recorded in :attr:`cancelled`."""

    def __init__(self, states: dict[str, bool | None] | None = None) -> None:
        self.states: dict[str, bool | None] = dict(states or {})
        self.unavailable: set[str] = set()
        self.calls: list[tuple[str, bool]] = []
        self.cancelled: list[str] = []
        self.errors: dict[str, Exception] = {}
        self.delays: dict[str, float] = {}
        self._held: dict[str, asyncio.Event] = {}
        self._once: dict[str, Exception] = {}

    def is_available(self, light: str) -> bool:
        return light in self.states and light not in self.unavailable

    def is_on(self, light: str) -> bool | None:
        return self.states.get(light)

    def hold(self, light: str) -> asyncio.Event:
        """Keeps the calls for the light from ending until the event that it returns is set."""
        self._held[light] = event = asyncio.Event()
        return event

    def fail_once(self, light: str, error: Exception) -> None:
        """Makes the next call for the light raise ``error``."""
        self._once[light] = error

    async def turn(self, light: str, on: bool) -> None:
        self.calls.append((light, on))
        try:
            if (held := self._held.get(light)) is not None:
                await held.wait()
            if (delay := self.delays.get(light)) is not None:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled.append(light)
            raise
        if (error := self._once.pop(light, None) or self.errors.get(light)) is not None:
            raise error
        self.states[light] = on


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == LOGGER.name]


async def toggle(lights: FakeLights, *names: str) -> ToggleResult:
    return await Toggler(lights, LOGGER).toggle(list(names))


# On or off.


@pytest.mark.parametrize(
    ("ceiling", "desk", "turned_on", "calls"),
    [
        (False, False, True, [(CEILING, True), (DESK, True)]),
        (True, False, False, [(CEILING, False)]),
        (True, True, False, [(CEILING, False), (DESK, False)]),
        # A light whose state isn't known isn't on, and it is switched either way.
        (None, False, True, [(CEILING, True), (DESK, True)]),
        (None, True, False, [(CEILING, False), (DESK, False)]),
    ],
)
async def test_if_any_light_is_on_all_go_off_else_all_go_on(
    ceiling: bool | None, desk: bool, turned_on: bool, calls: list[tuple[str, bool]]
) -> None:
    lights = FakeLights({CEILING: ceiling, DESK: desk})

    result = await toggle(lights, CEILING, DESK)

    assert result == ToggleResult(turned_on, len(calls), 2 - len(calls), 0)
    assert lights.calls == calls
    assert lights.states == {CEILING: turned_on, DESK: turned_on}


async def test_unavailable_lights_count_as_failed_and_are_left_alone(caplog: pytest.LogCaptureFixture) -> None:
    """Only the available lights tell whether a light is on: an unavailable one that was on doesn't count."""
    lights = FakeLights({CEILING: True, DESK: False})
    lights.unavailable.add(CEILING)

    result = await toggle(lights, CEILING, DESK, LAMP)

    assert result == ToggleResult(
        True, 1, 0, 2, (LightFailure(CEILING, "it is unavailable"), LightFailure(LAMP, "it is unavailable"))
    )
    assert lights.calls == [(DESK, True)]
    assert lights.states == {CEILING: True, DESK: True}
    assert logged(caplog) == [
        (logging.ERROR, "Could not switch light.ceiling on: it is unavailable"),
        (logging.ERROR, "Could not switch light.lamp on: it is unavailable"),
    ]


async def test_without_an_available_light_nothing_is_switched() -> None:
    lights = FakeLights({CEILING: True})
    lights.unavailable.add(CEILING)

    assert await toggle(lights, CEILING) == ToggleResult(True, 0, 0, 1, (LightFailure(CEILING, "it is unavailable"),))
    assert lights.calls == []


# Lights that aren't switched.


async def test_a_light_that_is_not_switched_counts_as_failed_with_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lights = FakeLights({CEILING: True, DESK: True, LAMP: False})
    lights.errors[DESK] = LightError("Request failed: the light didn't answer")

    result = await toggle(lights, CEILING, DESK, LAMP)

    assert result == ToggleResult(False, 1, 1, 1, (LightFailure(DESK, "Request failed: the light didn't answer"),))
    assert lights.states == {CEILING: False, DESK: True, LAMP: False}
    assert logged(caplog) == [
        (logging.ERROR, "Could not switch light.desk off: Request failed: the light didn't answer")
    ]


async def test_an_unexpected_error_is_logged_with_its_traceback_and_its_message_is_the_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failure without a message is named by its type. An unavailable light comes first, as it isn't asked."""
    lights = FakeLights({CEILING: False, DESK: False})
    lights.errors[CEILING] = RuntimeError("the action broke")
    lights.errors[DESK] = ValueError()

    result = await toggle(lights, CEILING, DESK, LAMP)

    assert result.failures == (
        LightFailure(LAMP, "it is unavailable"),
        LightFailure(CEILING, "the action broke"),
        LightFailure(DESK, "ValueError"),
    )
    assert (result.changed, result.failed) == (0, 3)
    assert logged(caplog) == [
        (logging.ERROR, "Switching light.ceiling on failed unexpectedly."),
        (logging.ERROR, "Switching light.desk on failed unexpectedly."),
        (logging.ERROR, "Could not switch light.lamp on: it is unavailable"),
        (logging.ERROR, "Could not switch light.ceiling on: the action broke"),
        (logging.ERROR, "Could not switch light.desk on: ValueError"),
    ]
    tracebacks = [record.exc_info for record in caplog.records if record.exc_info is not None]
    assert [type(exc_info[1]) for exc_info in tracebacks] == [RuntimeError, ValueError]


async def test_a_timeout_of_the_lights_own_is_an_unexpected_error(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    """Only the 5 seconds mean that a light didn't answer: one that fails at once with a TimeoutError did answer."""
    lights = FakeLights({CEILING: False})
    lights.errors[CEILING] = TimeoutError("the integration's request timed out")

    result = await toggle(lights, CEILING)

    assert result.failures == (LightFailure(CEILING, "the integration's request timed out"),)
    assert time.elapsed == 0
    assert logged(caplog) == [
        (logging.ERROR, "Switching light.ceiling on failed unexpectedly."),
        (logging.ERROR, "Could not switch light.ceiling on: the integration's request timed out"),
    ]


# Side by side, each within 5 seconds.


async def test_the_lights_are_switched_side_by_side() -> None:
    lights = FakeLights({CEILING: False, DESK: False})
    ceiling, desk = lights.hold(CEILING), lights.hold(DESK)
    toggling = asyncio.create_task(toggle(lights, CEILING, DESK))
    await until(lambda: len(lights.calls) == 2)

    # Both were asked before either answered; the second answers first.
    desk.set()
    await until(lambda: lights.states[DESK] is True)
    assert not toggling.done()
    ceiling.set()

    assert await toggling == ToggleResult(True, 2, 0, 0)


async def test_a_light_that_does_not_answer_within_5_seconds_counts_as_failed_and_holds_up_no_other(
    time: VirtualTime, caplog: pytest.LogCaptureFixture
) -> None:
    lights = FakeLights({CEILING: False, DESK: False, LAMP: False})
    lights.hold(CEILING)  # never answers
    lights.delays[DESK] = 4.9

    result = await toggle(lights, CEILING, DESK, LAMP)

    assert time.elapsed == 5
    assert result == ToggleResult(True, 2, 0, 1, (LightFailure(CEILING, "it didn't answer within 5 s"),))
    assert lights.states == {CEILING: False, DESK: True, LAMP: True}
    assert lights.cancelled == [CEILING]
    assert logged(caplog) == [(logging.ERROR, "Could not switch light.ceiling on: it didn't answer within 5 s")]


async def test_cancelling_a_toggle_cancels_the_calls_for_its_lights_and_reports_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lights = FakeLights({CEILING: False, DESK: False})
    lights.hold(CEILING)
    lights.hold(DESK)
    toggling = asyncio.create_task(toggle(lights, CEILING, DESK))
    await until(lambda: len(lights.calls) == 2)

    toggling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await toggling

    assert lights.cancelled == [CEILING, DESK]
    assert lights.states == {CEILING: False, DESK: False}
    assert logged(caplog) == []
