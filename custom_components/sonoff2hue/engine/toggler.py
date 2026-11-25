"""Toggles the targets of a switch as one unit.

If any target is on, all are turned off; otherwise all are turned on. This keeps the lights of one wall switch in sync.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
import logging

from .hue_client import HueApi, HueApiError, HueTarget, HueTargetKind

RETRY_DELAY = 0.3
"""Seconds before the one retry of a request that failed transiently."""


@dataclass(frozen=True, slots=True)
class ToggleResult:
    turned_on: bool
    changed: int
    unchanged: int
    failed: int


class HueToggler:
    def __init__(self, hue: HueApi, logger: logging.Logger) -> None:
        self._hue = hue
        self._logger = logger

    async def toggle(self, targets: list[HueTarget]) -> ToggleResult:
        """Reads the states, then switches the targets one after the other; a target the bridge doesn't switch counts
        as failed. Raises :class:`HueApiError` when the states can't be read."""
        kinds = {target.kind for target in targets}
        light_states: dict[str, bool] = (
            await _with_retry(self._hue.get_light_states) if HueTargetKind.LIGHT in kinds else {}
        )
        group_states: dict[str, bool] = (
            await _with_retry(self._hue.get_grouped_light_states) if HueTargetKind.GROUPED_LIGHT in kinds else {}
        )

        def state_of(target: HueTarget) -> bool | None:
            states = light_states if target.kind == HueTargetKind.LIGHT else group_states
            return None if target.id is None else states.get(target.id)

        turn_on = not any(state_of(target) is True for target in targets)
        changed = unchanged = failed = 0
        # Sequential on purpose: reuses the warm TLS connection and respects the bridge's rate limits.
        for target in targets:
            if state_of(target) == turn_on:
                unchanged += 1
                continue
            try:
                await _with_retry(partial(self._hue.set_on, target, turn_on))
                changed += 1
            except HueApiError as err:
                failed += 1
                # The bridge's message says what failed; a stack trace would add nothing.
                self._logger.error("Could not switch %s %s: %s", target, "on" if turn_on else "off", err)  # noqa: TRY400
        return ToggleResult(turn_on, changed, unchanged, failed)


async def _with_retry[T](action: Callable[[], Awaitable[T]]) -> T:
    try:
        return await action()
    except HueApiError as err:
        if not err.is_transient:
            raise
        await asyncio.sleep(RETRY_DELAY)
        return await action()
