"""Toggles the lights of a switch as one unit, through Home Assistant (decision 40).

If any of the lights is on, all are turned off; otherwise all are turned on, which keeps the lights of one wall switch
in sync. A :class:`LightControl` reads the lights' states and switches them: Home Assistant's, in ``lights.py``, calls
the ``light`` actions. The lights are named by their entity ids. They are switched side by side, each on its own, so
that one that fails or doesn't answer never holds up the others, and a light already in the wanted state is left alone.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import logging
from typing import Protocol

SWITCH_TIMEOUT = 5.0
"""Seconds that a light may take to be switched before it counts as failed."""
UNAVAILABLE = "it is unavailable"
"""Why a light that Home Assistant can't switch now counts as failed."""


class LightError(Exception):
    """A light that wasn't switched; the message says why."""


class LightControl(Protocol):
    """Reads and switches the lights, by their entity ids."""

    def is_available(self, light: str) -> bool:
        """Whether the light can be switched now."""
        ...

    def is_on(self, light: str) -> bool | None:
        """Whether the light is on; ``None`` when that isn't known."""
        ...

    async def turn(self, light: str, on: bool) -> None:
        """Switches the light on or off. Raises :class:`LightError` when it isn't switched."""
        ...


@dataclass(frozen=True, slots=True)
class LightFailure:
    """A light that wasn't switched, and why."""

    light: str
    message: str


@dataclass(frozen=True, slots=True)
class ToggleResult:
    turned_on: bool
    changed: int
    unchanged: int
    failed: int
    """How many lights weren't switched: those of ``failures``."""
    failures: tuple[LightFailure, ...] = ()


class Toggler:
    def __init__(self, control: LightControl, logger: logging.Logger, timeout: float = SWITCH_TIMEOUT) -> None:
        self._control = control
        self._logger = logger
        self._timeout = timeout

    async def toggle(self, lights: Sequence[str]) -> ToggleResult:
        """Switches the lights side by side, by the states that the control has for them now. A light that is
        unavailable, that isn't switched or that doesn't answer within 5 seconds counts as failed, and is logged."""
        control = self._control
        available = [light for light in lights if control.is_available(light)]
        failures = [LightFailure(light, UNAVAILABLE) for light in lights if light not in available]
        turn_on = not any(control.is_on(light) is True for light in available)
        to_switch = [light for light in available if control.is_on(light) is not turn_on]
        outcomes = await asyncio.gather(*(self._turn(light, turn_on) for light in to_switch))
        not_switched = [failure for failure in outcomes if failure is not None]
        failures += not_switched
        state = "on" if turn_on else "off"
        for failure in failures:
            self._logger.error("Could not switch %s %s: %s", failure.light, state, failure.message)
        return ToggleResult(
            turn_on,
            len(to_switch) - len(not_switched),
            len(available) - len(to_switch),
            len(failures),
            tuple(failures),
        )

    async def _turn(self, light: str, on: bool) -> LightFailure | None:
        deadline = asyncio.timeout(self._timeout)
        try:
            async with deadline:
                await self._control.turn(light, on)
        except LightError as err:
            return LightFailure(light, str(err))
        except Exception as err:
            # Only the deadline means that the light didn't answer: a TimeoutError of the light's own is unexpected.
            if deadline.expired():
                return LightFailure(light, f"it didn't answer within {self._timeout:g} s")
            self._logger.exception("Switching %s %s failed unexpectedly.", light, "on" if on else "off")
            return LightFailure(light, str(err) or type(err).__name__)
        return None
