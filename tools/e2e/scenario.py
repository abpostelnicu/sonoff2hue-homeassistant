"""The end-to-end test's steps: Home Assistant and the integration set up as a user sets them up, a switch added for
two of the development configuration's lights, presses, one light given a new entity id and the other disabled, a
restart and a press after it; then sonoff2hue 1.x's configuration, which the next start migrates, and its repair, which
gives the switch its lights again."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Final
import uuid

import aiohttp
from homeassistant.const import ATTR_NAME

from custom_components.sonoff2hue.const import (
    ATTR_CHANGED,
    ATTR_ELAPSED_MS,
    ATTR_FAILED,
    ATTR_RESULT,
    ATTR_SEQ,
    ATTR_SWITCH_ID,
    ATTR_TARGETS,
    ATTR_TURNED_ON,
    ATTR_UNCHANGED,
    DOMAIN,
    ENTRY_TITLE,
    EVENT_PRESS,
    EVENT_TOGGLE,
    RESULT_TOGGLED,
    SUBENTRY_SWITCH,
)
from custom_components.sonoff2hue.engine.supervisor import NO_SWITCHES
from custom_components.sonoff2hue.sensor import ACTION_TURNED_OFF, ACTION_TURNED_ON

from . import (
    BRIDGE_ID,
    DEVICE_ID,
    HALLWAY,
    LIGHTS,
    LIVING_ROOM,
    POLL_INTERVAL,
    PORCH,
    RENAMED,
    SWITCH_NAME,
    V1_GROUPS,
    V1_LIGHTS,
    StepError,
    boolean_of,
    storage,
    within,
)
from .checks import Checks, Event, Events
from .devices import SimulatedSwitch
from .hass import HomeAssistant, WebSocket

START_TIMEOUT: Final = 300.0
"""Seconds for Home Assistant to finish starting; its first start after an install compiles its modules."""
IDENTIFY_TIMEOUT: Final = 15.0
"""Seconds for identify-by-press to select the switch after its press; it waits up to 60 seconds."""
STATUS_TIMEOUT: Final = 30.0
"""Seconds for something that follows a change to show it: the integration's status, such as running after a setup,
its repair issues, and the stored configuration, which Home Assistant writes a moment after a change."""
LIGHTS_TIMEOUT: Final = 5.0
"""Seconds for the lights' states to show a toggle that has ended: the light actions return once they are switched."""
EVENTS_TIMEOUT: Final = 15.0
"""Seconds for the last announcement's press and toggle, after the switch's announcements, 1.5 seconds apart."""
STOP_TIMEOUT: Final = 60.0
"""Seconds for Home Assistant to stop for a restart."""
LOG_TIMEOUT: Final = 5.0
"""Seconds for a line to reach Home Assistant's log file, which a thread of its own writes."""

RESTARTING: Final = "Configuration changed; restarting the switches."
"""The supervisor's line when a change of the configuration, such as a switch added, restarts the switches (decision
23)."""
LISTENING: Final = "Listening for wall switch presses."
"""The runner's line as it starts, which each start of the switches has to log."""
DISPLAY_NAME: Final = f"{SWITCH_NAME} ({DEVICE_ID})"
"""The switch as the integration's log names it."""
SWITCH_ADDRESS: Final = "192.0.2.10"
"""The address in the simulated switch's A record."""
TWO_LIGHTS: Final = "1 wall switch toggles 2 lights."
ONE_MISSING: Final = "1 wall switch toggles 1 light; 1 is missing."
DISABLED: Final = f"{DISPLAY_NAME}: {LIVING_ROOM} is disabled."
"""Why the switch leaves out the light that the test disabled, which the integration warns about at each start of the
switches that finds it, and which the switch's repair names."""
V1_ENTRIES: Final = [*V1_LIGHTS, *V1_GROUPS]
"""The switch's lights, rooms and zones of sonoff2hue 1.x, in its order, which the migration finds no light for."""
_QUOTED_ENTRIES: Final = ", ".join(f"'{entry}'" for entry in V1_ENTRIES)
UNMIGRATED: Final = (
    f"{DISPLAY_NAME}: found no light in Home Assistant for {_QUOTED_ENTRIES}; edit the switch to choose them."
)
"""The migration's warning about them."""

type Issue = tuple[str, str, str, bool, dict[str, str]]
"""A repair issue of the integration: its id, severity, translation key, whether it is fixable, and its
placeholders."""

MISSING_ISSUE: Final[Issue] = (
    f"missing_targets_{DEVICE_ID}",
    "warning",
    "missing_targets",
    True,
    {"name": DISPLAY_NAME, "problems": f"- {DISABLED}"},
)
"""The switch's repair while it misses the disabled light, which its *Edit switch* dialog fixes."""
UNMIGRATED_PLACEHOLDERS: Final = {"name": DISPLAY_NAME, "entries": "\n".join(f"- `{entry}`" for entry in V1_ENTRIES)}
UNMIGRATED_ISSUE: Final[Issue] = (f"unmigrated_{DEVICE_ID}", "warning", "unmigrated", True, UNMIGRATED_PLACEHOLDERS)
"""The switch's repair after the migration, which its *Edit switch* dialog fixes."""

CONFIG_FLOWS: Final = "/api/config/config_entries/flow"
SUBENTRY_FLOWS: Final = "/api/config/config_entries/subentries/flow"
REPAIR_FLOWS: Final = "/api/repairs/issues/fix"
SUBENTRIES_FLOW: Final = "config_subentries_flow"
"""A flow result's ``next_flow`` names a subentry flow by this type, as the frontend reads it."""

# sonoff2hue 1.x's config entry: the Hue bridge that it paired itself, and its device with the Reachable entity.
V1_BRIDGE_ADDRESS: Final = "192.0.2.10"
V1_DEVICE_NAME: Final = "Hue bridge"
V1_MANUFACTURER: Final = "Signify Netherlands B.V."
V1_REACHABLE: Final = "binary_sensor.hue_bridge_reachable"
V1_LIGHT_ID: Final = "made-up-light-id"
"""The Hue light that 1.x's store says the switch's light last found, which no light of Home Assistant has."""

# A line of Home Assistant's log: time, level, thread, logger and message.
_LOG_LINE = re.compile(r"^\S+ \S+ (?P<level>[A-Z]+) \([^)]*\) \[(?P<logger>[^\]]+)\] ")


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    """The repository's root, whose ``custom_components/sonoff2hue`` Home Assistant loads."""
    work: Path
    """Home Assistant's configuration folder (``config``) and the logs."""
    hass_port: int


class EndToEnd:
    """One run of the end-to-end test."""

    def __init__(self, settings: Settings, session: aiohttp.ClientSession) -> None:
        root, work = settings.root, settings.work
        self.settings = settings
        self.checks = Checks()
        self.config = work / "config"
        self.hass = HomeAssistant(root, self.config, settings.hass_port, work / "hass.log", session)
        self.switch = SimulatedSwitch(root, DEVICE_ID)
        self._entry_id: str | None = None
        self._flow_changes = Events()
        """The announcements that a flow changed (``data_entry_flow_progressed``), of any flow."""
        self._light_ids: dict[str, str] = {}
        """The development lights' entity registry ids, by the entity ids that they start with."""
        self._entity_ids: dict[str, str] = {}
        """The integration's entities, by their translation keys."""
        self._expected_problems: list[str] = []
        """The integration's warnings and errors that a step causes on purpose, which the log may hold."""
        self._stopped_starts = 0
        """How many of Home Assistant's starts have been stopped, and their logs checked."""

    async def run(self) -> bool:
        """Runs every step, stops Home Assistant, and reports; ``True`` if every check passed."""
        try:
            await self._steps()
        except StepError as failure:
            self.checks.fail(str(failure))
        except (aiohttp.ClientError, ConnectionError) as err:
            code = self.hass.exit_code
            state = "it still runs" if code is None else f"it exited with code {code}"
            self.checks.fail(f"Home Assistant's API didn't answer ({err!r}); {state}.")
        except Exception:  # noqa: BLE001 - reported as the failure it is, and everything is stopped
            self.checks.fail("The test stopped with an unexpected error.", traceback.format_exc())
        finally:
            await self._stop()

        checks = self.checks
        if checks.failures:
            self._show_logs()
        print()
        print("Timings: " + ", ".join(f"{what} {seconds:.1f} s" for what, seconds in checks.timings))
        if checks.failures:
            print(f"FAIL: {checks.failures} of {checks.count} checks failed, in {checks.elapsed:.1f} s.")
        else:
            print(f"PASS: all {checks.count} checks passed in {checks.elapsed:.1f} s.")
        return not checks.failures

    async def _steps(self) -> None:
        await self._set_up_home_assistant()
        await self._start_home_assistant()
        websocket = await self.hass.websocket()
        try:
            await self._find_lights(websocket)
            switch_flow = await self._set_up_integration(websocket)
            await self._add_switch(websocket, switch_flow)
            await self._wait_until_running(TWO_LIGHTS)
            await self._press_twice(websocket)
            await self._rename_light(websocket)
            await self._disable_light(websocket)
            await self._restart(websocket)
        finally:
            await websocket.close()
        websocket = await self.hass.websocket()
        try:
            await self._press_after_restart(websocket)
        finally:
            await websocket.close()
        await self._stop()
        await self._migrate_from_version_1()

    # Starting everything.

    async def _set_up_home_assistant(self) -> None:
        checks, settings = self.checks, self.settings
        checks.step(f"Set up the development Home Assistant, with its configuration folder {self.config}")
        started = time.monotonic()
        environment = os.environ | {
            "SONOFF2HUE_DEV_CONFIG": str(self.config),
            "SONOFF2HUE_DEV_PORT": str(settings.hass_port),
        }
        process = await asyncio.create_subprocess_exec(
            settings.root / "scripts" / "develop", "setup", cwd=settings.root, env=environment
        )
        if (code := await process.wait()) != 0:
            raise StepError(f"scripts/develop setup exited with code {code}.")
        elapsed = checks.timed("setup", started)
        checks.ok(f"scripts/develop setup made Home Assistant's environment and configuration folder ({elapsed:.1f} s)")

    async def _start_home_assistant(self) -> None:
        checks, hass = self.checks, self.hass
        checks.step(f"Start Home Assistant on {hass.url}/, and onboard it through its API")
        started = time.monotonic()
        await hass.start()
        async with within(START_TIMEOUT, "Home Assistant's HTTP server didn't answer"):
            await hass.wait_until_answering()
        checks.ok(f"its HTTP server answers ({time.monotonic() - started:.1f} s)")
        await hass.onboard()
        checks.ok("onboarded with a throwaway user, and signed in with a token for the REST and WebSocket APIs")
        async with within(START_TIMEOUT, "Home Assistant didn't finish starting"):
            await hass.wait_until_running()
        elapsed = checks.timed("Home Assistant's first start", started)
        checks.ok(f"it has finished starting: its core state is RUNNING ({elapsed:.1f} s)")

    async def _find_lights(self, websocket: WebSocket) -> None:
        """The development configuration's lights: template lights, in the entity registry by their unique ids, so that
        a switch keeps them by their registry ids, and off, as their input booleans start."""
        checks = self.checks
        checks.step("Find the development configuration's lights, which Home Assistant's template integration provides")
        found = {entity["entity_id"]: entity for entity in await self._entity_registry(websocket)}
        lights = [found.get(light) for light in LIGHTS]
        checks.expect_equal(
            "they are in the entity registry: template lights, each with a unique id",
            [(light, "template", True) for light in LIGHTS],
            [
                (light, None, False) if entity is None else (light, entity["platform"], bool(entity["unique_id"]))
                for light, entity in zip(LIGHTS, lights, strict=True)
            ],
        )
        self._light_ids = {entity["entity_id"]: entity["id"] for entity in lights if entity is not None}
        if len(self._light_ids) != len(LIGHTS):
            raise StepError("The development configuration's lights are missing; see the check above.")
        await self._check_lights(dict.fromkeys(LIGHTS, "off"), "all off")

    # The flows.

    async def _set_up_integration(self, websocket: WebSocket) -> str:
        """Adds the integration as a user does: the setup flow's one step, a confirmation. The entry has no switch yet,
        so the flow opens *Add switch* next, the switch subentry flow, whose id this returns."""
        checks = self.checks
        checks.step("Set up sonoff2hue through the setup flow's API, as the frontend does: one confirmation")
        started = time.monotonic()
        await websocket.subscribe("data_entry_flow_progressed", self._flow_changes.add)
        flow = await self._flow("POST", CONFIG_FLOWS, {"handler": DOMAIN})
        _expect_step(flow, "form", "user")
        checks.expect_equal("the setup flow's one step asks for nothing", [], flow.get("data_schema"))
        result = await self._flow("POST", f"{CONFIG_FLOWS}/{flow['flow_id']}", {})
        _expect_step(result, "create_entry", None)
        elapsed = checks.timed("the setup flow", started)
        checks.ok(f"confirmed, the flow added the entry ({elapsed:.1f} s)")
        checks.expect_equal("the entry's title", ENTRY_TITLE, result.get("title"))

        self._entry_id = entry_id = result["result"]["entry_id"]
        status, entries = await self.hass.request("GET", f"/api/config/config_entries/entry?domain={DOMAIN}")
        states = [(entry["entry_id"], entry["state"]) for entry in entries] if status == 200 else entries
        checks.expect_equal("the integration has one entry, and it is loaded", [(entry_id, "loaded")], states)
        service = await self._wait_for_status("not_configured", NO_SWITCHES)
        checks.expect_equal(
            "its status, in its diagnostics, asks for a switch", ("not_configured", NO_SWITCHES), _state(service)
        )
        return _next_subentry_flow(result, "The setup flow didn't open the switch subentry flow next")

    async def _add_switch(self, websocket: WebSocket, flow_id: str) -> None:
        """Adds the switch in the switch subentry flow that the setup flow opened, *Add switch* (decision 24): it
        identifies the MINIR4M by a press of its wall switch, which the LAN monitor reports, then takes its name and
        its lights, which Home Assistant's entity picker gives by their entity ids. The switch keeps them by their
        entity registry ids (decision 40).

        Identify-by-press selects the first device that reports a press, and the switch's announcements reach every
        listener on the machine, as those of a test that runs at the same time in another checkout reach this one. So
        the switch's state, which the LAN monitor takes as a baseline, comes first, and its press right after
        identify-by-press starts, so that another device's press can hardly come in between."""
        checks = self.checks
        checks.step(f"Add the switch in the switch subentry flow that the setup flow opened: identify {DEVICE_ID}")
        started = time.monotonic()
        path = f"{SUBENTRY_FLOWS}/{flow_id}"
        flow = await self._flow("GET", path)
        _expect_step(flow, "menu", "user")
        checks.expect_equal(
            "the flow is Add switch of the entry", [self._entry_id, SUBENTRY_SWITCH], flow.get("handler")
        )
        await self.switch.announce([1])
        try:
            async with within(STATUS_TIMEOUT, "The LAN monitor didn't report the switch's state"):
                await self._wait_for_device(seq=1)
        finally:
            await self.switch.done()
        checks.ok("the LAN monitor has the switch's state, seq 1, in the diagnostics' devices")
        seen = len(self._flow_changes.received)
        flow = await self._flow("POST", path, {"next_step_id": "identify"})
        _expect_step(flow, "progress", "identify")
        await self.switch.announce([2])
        try:
            async with within(IDENTIFY_TIMEOUT, "Identify-by-press didn't select the switch"):
                flow = await self._follow(path, flow, seen)
        finally:
            await self.switch.done()
        _expect_step(flow, "form", "settings")
        checks.expect_equal(
            "identify-by-press selected the switch's device",
            f"Selected {DEVICE_ID}: it reported a press.",
            flow["description_placeholders"].get("device"),
        )
        picker = _field(flow, "lights").get("selector", {}).get("entity") or {}
        checks.expect_equal(
            "the form's lights are Home Assistant's entity picker, for several lights",
            (["light"], True),
            (picker.get("domain"), picker.get("multiple")),
        )
        result = await self._flow("POST", path, {"name": SWITCH_NAME, "lights": [HALLWAY, LIVING_ROOM], "advanced": {}})
        _expect_step(result, "create_entry", None)
        elapsed = checks.timed("the switch subentry flow", started)
        checks.ok(
            f"the switch {SWITCH_NAME} was added, with {HALLWAY} and {LIVING_ROOM} by their entity ids ({elapsed:.1f} s)"
        )
        checks.expect_equal("the flow's last text", "added", result.get("description"))

        subentries = await websocket.command({"type": "config_entries/subentries/list", "entry_id": self._entry_id})
        checks.expect_equal(
            "the entry has the switch as a switch subentry",
            [(SUBENTRY_SWITCH, SWITCH_NAME, DEVICE_ID)],
            [(item["subentry_type"], item["title"], item["unique_id"]) for item in subentries],
        )
        await self._check_saved_switch("the switch keeps the lights by their entity registry ids")

    async def _follow(self, path: str, flow: dict[str, Any], seen: int) -> dict[str, Any]:
        """Follows a progress step until it ends: the step's task runs in the background, and Home Assistant announces
        each change of the flow, as the frontend gets it, which then fetches the flow. ``seen`` is how many announcements
        had come before the request that showed ``flow``: the task can end, and its announcement come, before the answer
        to that request."""
        while flow["type"] == "progress":
            await self._flow_changes.wait(_flow_changed(flow["flow_id"], after=seen))
            seen = len(self._flow_changes.received)
            flow = await self._flow("GET", path)
        return flow

    async def _flow(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """A request to a flow API, as the frontend makes it: a new flow, a step taken, or the flow as it is now."""
        status, answer = await self.hass.request(method, path, body)
        if status != 200 or not isinstance(answer, dict):
            raise StepError(f"{method} {path} answered {status}: {answer}")
        return answer

    # The integration at work.

    async def _press_twice(self, websocket: WebSocket) -> None:
        """Two presses: the first after the switch's state, as adding the switch restarted the switches with a new
        runner, for which the switch's last seq, 2, is its state, a baseline. The first press turns both lights on, the
        second turns them off; the third light, which no switch has, stays off."""
        checks = self.checks
        started = time.monotonic()
        checks.step(f"Press the wall switch: {DEVICE_ID} announces seq 2 again, its state, then 3")
        await self._expect_toggle(
            websocket,
            [2, 3],
            turned_on=True,
            targets=[HALLWAY, LIVING_ROOM],
            lights={HALLWAY: "on", LIVING_ROOM: "on", PORCH: "off"},
        )
        checks.step("Press it again: seq 4")
        await self._expect_toggle(
            websocket,
            [4],
            turned_on=False,
            targets=[HALLWAY, LIVING_ROOM],
            lights={HALLWAY: "off", LIVING_ROOM: "off", PORCH: "off"},
        )
        checks.timed("two presses", started)
        await self._expect_logged(
            RESTARTING,
            LISTENING,
            f"{DISPLAY_NAME} -> {HALLWAY}, {LIVING_ROOM}",
            f"{DISPLAY_NAME}: online at {SWITCH_ADDRESS} (seq 2;",
            f"{DISPLAY_NAME}: wall switch operated (seq 3).",
            f"{DISPLAY_NAME}: turned ON {HALLWAY}, {LIVING_ROOM} in ",
            f"{DISPLAY_NAME}: wall switch operated (seq 4).",
            f"{DISPLAY_NAME}: turned OFF {HALLWAY}, {LIVING_ROOM} in ",
        )

    async def _rename_light(self, websocket: WebSocket) -> None:
        """Gives the switch's first light a new entity id, as the entity's settings in Home Assistant's UI do. The
        switch keeps its lights by their entity registry ids, which stay, and the integration resolves them again when
        the registry changes, without restarting the switches: the press after it, the switch's next seq alone, is a
        press for the runner that already had the switch's state, and toggles the light under its new id."""
        checks = self.checks
        checks.step(f"Give {HALLWAY} the entity id {RENAMED} through the entity registry's API, then press: seq 5")
        started = time.monotonic()
        before = await self.hass.error_log()
        answer = await websocket.command(
            {"type": "config/entity_registry/update", "entity_id": HALLWAY, "new_entity_id": RENAMED}
        )
        entry = answer.get("entity_entry") or {}
        checks.expect_equal(
            "the entity registry renamed it, under the same registry id",
            (RENAMED, self._light_ids[HALLWAY]),
            (entry.get("entity_id"), entry.get("id")),
        )
        await self._wait_for_targets([RENAMED, LIVING_ROOM])
        checks.ok(f"the integration toggles it under its new id: the status's targets are {RENAMED}, {LIVING_ROOM}")
        await self._check_lights({HALLWAY: None, RENAMED: "off"}, "the light has its state under its new id only")

        await self._expect_toggle(
            websocket,
            [5],
            turned_on=True,
            targets=[RENAMED, LIVING_ROOM],
            lights={RENAMED: "on", LIVING_ROOM: "on", PORCH: "off"},
        )
        checks.timed("the rename and its press", started)
        after = await self.hass.error_log()
        checks.expect_equal(
            "the switches didn't restart for it: no new configuration and no new runner",
            (before.count(RESTARTING), before.count(LISTENING)),
            (after.count(RESTARTING), after.count(LISTENING)),
        )
        await self._check_saved_switch("the switch's settings are as they were: the lights by their registry ids")
        await self._expect_logged(
            f"{DISPLAY_NAME} -> {RENAMED}, {LIVING_ROOM}", f"{DISPLAY_NAME}: turned ON {RENAMED}, {LIVING_ROOM} in "
        )

    async def _disable_light(self, websocket: WebSocket) -> None:
        """Disables the switch's other light, as the entity's settings in Home Assistant's UI do: the switches run on
        without it, the status and a repair say so, and the next press toggles the first light alone (decision 32)."""
        checks = self.checks
        checks.step(f"Disable {LIVING_ROOM} through the entity registry's API, then press: seq 6")
        started = time.monotonic()
        self._expected_problems.append(DISABLED)
        answer = await websocket.command(
            {"type": "config/entity_registry/update", "entity_id": LIVING_ROOM, "disabled_by": "user"}
        )
        checks.expect_equal(
            "the entity registry disabled it", "user", (answer.get("entity_entry") or {}).get("disabled_by")
        )
        service = await self._wait_for_status("running", ONE_MISSING, [DISABLED])
        checks.ok(f"the status runs on, and says what is missing: {service['message']} {DISABLED}")
        checks.expect_equal(
            "Repairs has the switch's issue: a warning, which its switch's dialog fixes, with the problem",
            [MISSING_ISSUE],
            await self._wait_for_issues(websocket, lambda issues: issues == [MISSING_ISSUE]),
        )

        await self._expect_toggle(
            websocket,
            [6],
            turned_on=False,
            targets=[RENAMED],
            lights={RENAMED: "off", LIVING_ROOM: None, boolean_of(LIVING_ROOM): "on", PORCH: "off"},
            problems=[DISABLED],
        )
        checks.timed("the disabling and its press", started)
        await self._expect_logged(DISABLED, f"{DISPLAY_NAME} -> {RENAMED}", f"{DISPLAY_NAME}: turned OFF {RENAMED} in ")

    async def _restart(self, websocket: WebSocket) -> None:
        checks, hass = self.checks, self.hass
        checks.step("Restart Home Assistant, as its UI does (the homeassistant.restart action)")
        started = time.monotonic()
        async with within(STOP_TIMEOUT, "Home Assistant didn't stop for the restart"):
            await hass.restart(websocket)
        checks.ok(f"it stopped, saving its state, with exit code 100 ({time.monotonic() - started:.1f} s)")
        async with within(START_TIMEOUT, "Home Assistant didn't finish starting again"):
            await hass.wait_until_running()
        elapsed = checks.timed("the restart", started)
        checks.ok(f"started again, it has finished starting ({elapsed:.1f} s since the restart)")

    async def _press_after_restart(self, websocket: WebSocket) -> None:
        """A restart starts from a new baseline, so the switch's first broadcast after it, here its last seq again, is
        never a press, and the next seq is one. The switch still has its first light by its new id, and still misses
        the disabled one, which the repair names again."""
        checks = self.checks
        checks.step("After the restart: the switch announces its last seq again (6), then the next (7)")
        await self._wait_until_running(ONE_MISSING, [DISABLED])
        checks.expect_equal(
            "Repairs has the switch's issue again",
            [MISSING_ISSUE],
            await self._wait_for_issues(websocket, lambda issues: issues == [MISSING_ISSUE]),
        )
        started = time.monotonic()
        await self._expect_toggle(
            websocket,
            [6, 7],
            turned_on=True,
            targets=[RENAMED],
            lights={RENAMED: "on", LIVING_ROOM: None, PORCH: "off"},
            problems=[DISABLED],
        )
        checks.timed("a press after the restart", started)
        # The runner's own word: the repeated seq was the switch's state, a baseline.
        await self._expect_logged(
            LISTENING,
            DISABLED,
            f"{DISPLAY_NAME} -> {RENAMED}",
            f"{DISPLAY_NAME}: online at {SWITCH_ADDRESS} (seq 6;",
            f"{DISPLAY_NAME}: turned ON {RENAMED} in ",
        )

    # sonoff2hue 1.x's configuration, migrated (decision 40).

    async def _migrate_from_version_1(self) -> None:
        """sonoff2hue 1.x paired a Hue bridge itself, and kept its switches' lights, rooms and zones by their names on
        the bridge. With Home Assistant stopped, the test makes the stored configuration 1.x's, and the next start
        migrates it. No light of Home Assistant's Hue integration has those names, so the switch has no light then,
        and a repair asks to choose them, which the test does as a user does: the repair opens the switch's dialog."""
        started = time.monotonic()
        self._expected_problems = [UNMIGRATED]
        self._make_version_1()
        await self._start_again()
        websocket = await self.hass.websocket()
        try:
            await self._check_migration(websocket)
            await self._repair(websocket)
            await self._press_after_repair(websocket)
        finally:
            await websocket.close()
        await self._stop("the stop after the migration")
        self.checks.timed("the migration step", started)

    def _make_version_1(self) -> None:
        """Rewrites Home Assistant's storage, while it is stopped, as sonoff2hue 1.x left it: the config entry of
        version 1 with the paired bridge, and the switch with its lights, rooms and zones by their names; the store
        of the Hue resources that they last found; and the bridge's device, identified by the bridge id, with its
        Reachable entity. The light that the scenario disabled is enabled again, as after a user enabled it and
        restarted, so that the switch can have both its lights again."""
        checks, config = self.checks, self.config
        entry_id = self._entry_id
        checks.step("With Home Assistant stopped, make its stored configuration sonoff2hue 1.x's")

        entries = storage.read(config, storage.CONFIG_ENTRIES)
        entry = _one(entries["data"]["entries"], lambda item: item["domain"] == DOMAIN, "sonoff2hue's config entry")
        entry |= {
            "version": 1,
            "minor_version": 1,
            "unique_id": BRIDGE_ID,
            "data": {
                "bridge_ip": V1_BRIDGE_ADDRESS,
                "bridge_id": BRIDGE_ID,
                "app_key": "made-up-key",
                "certificate_sha256": None,
            },
        }
        subentry = _one(entry["subentries"], lambda item: item["subentry_type"] == SUBENTRY_SWITCH, "the switch")
        subentry["data"] = {
            "device_id": DEVICE_ID,
            "name": SWITCH_NAME,
            "device_key": None,
            "lights": V1_LIGHTS,
            "groups": V1_GROUPS,
        }
        storage.write(config, storage.CONFIG_ENTRIES, entries)

        key = f"{DOMAIN}.{entry_id}"
        resource = {"switch": DEVICE_ID, "field": "lights", "entry": V1_LIGHTS[0], "kind": "light", "id": V1_LIGHT_ID}
        storage.write(config, key, storage.new(key, {"resources": [resource]}))

        devices = storage.read(config, storage.DEVICE_REGISTRY)
        device = _one(
            devices["data"]["devices"], lambda item: [DOMAIN, entry_id] in item["identifiers"], "the service device"
        )
        device |= {
            "identifiers": [[DOMAIN, BRIDGE_ID]],
            "name": V1_DEVICE_NAME,
            "manufacturer": V1_MANUFACTURER,
            "entry_type": None,
        }
        storage.write(config, storage.DEVICE_REGISTRY, devices)

        registry = storage.read(config, storage.ENTITY_REGISTRY)
        entities: list[dict[str, Any]] = registry["data"]["entities"]
        for item in entities:
            if item["platform"] == DOMAIN and item["unique_id"] in {f"{entry_id}_status", f"{entry_id}_devices_seen"}:
                item["unique_id"] = item["unique_id"].replace(f"{entry_id}_", f"{BRIDGE_ID}_", 1)
        online = _one(
            entities,
            lambda item: item["platform"] == DOMAIN and item["translation_key"] == "online",
            "the switch's Online entity",
        )
        entities.append(
            online
            | {
                "id": uuid.uuid4().hex,
                "entity_id": V1_REACHABLE,
                "unique_id": f"{BRIDGE_ID}_reachable",
                "translation_key": "reachable",
                "original_name": "Reachable",
                "object_id_base": "Reachable",
                "device_id": device["id"],
                "config_subentry_id": None,
                "previous_unique_id": None,
            }
        )
        _one(entities, lambda item: item["entity_id"] == LIVING_ROOM, f"{LIVING_ROOM}")["disabled_by"] = None
        storage.write(config, storage.ENTITY_REGISTRY, registry)
        checks.ok(f"the entry is version 1 with the bridge {BRIDGE_ID}, and the switch has {', '.join(V1_ENTRIES)}")
        checks.ok(f"the bridge's device is identified by its id again, with Reachable, {V1_REACHABLE}")

    async def _start_again(self) -> None:
        checks, hass = self.checks, self.hass
        checks.step("Start Home Assistant again, which migrates the configuration of sonoff2hue 1.x")
        started = time.monotonic()
        await hass.start()
        async with within(START_TIMEOUT, "Home Assistant didn't finish starting"):
            await hass.wait_until_running()
        elapsed = checks.timed("Home Assistant's start with 1.x's configuration", started)
        checks.ok(f"it has finished starting ({elapsed:.1f} s)")

    async def _check_migration(self, websocket: WebSocket) -> None:
        """What the migration made of 1.x's configuration: an entry of version 2 without the bridge, and a switch
        without lights but with the entries that it found no light for, which a repair names; the bridge's device is
        the service device again, with its entities but for Reachable."""
        checks, entry_id = self.checks, self._entry_id
        entry, subentry = await self._stored_entry(lambda entry, _: entry["version"] == 2)
        checks.expect_equal(
            "the stored entry is version 2, without data or a unique id",
            (2, 1, {}, None),
            (entry["version"], entry["minor_version"], entry["data"], entry["unique_id"]),
        )
        checks.expect_equal(
            "the switch keeps its device, name and key, has no light, and keeps the entries that it found no light for",
            {
                "device_id": DEVICE_ID,
                "name": SWITCH_NAME,
                "device_key": None,
                "targets": [],
                "unmigrated": V1_ENTRIES,
            },
            subentry["data"],
        )
        checks.expect(
            "1.x's store of the Hue resources that the switches found is gone",
            not storage.path(self.config, f"{DOMAIN}.{entry_id}").exists(),
        )

        devices = await websocket.command({"type": "config/device_registry/list"})
        service = [
            device
            for device in devices
            if any(domain == DOMAIN for domain, _ in device["identifiers"]) and device["config_subentry_id"] is None
        ]
        checks.expect_equal(
            "the bridge's device is the service device again, identified by the entry's id",
            [([[DOMAIN, entry_id]], "sonoff2hue", None, "service")],
            [
                (device["identifiers"], device["name"], device["manufacturer"], device["entry_type"])
                for device in service
            ],
        )
        entities = [
            (entity["translation_key"], entity["entity_id"], entity["unique_id"])
            for entity in await self._entity_registry(websocket)
            if entity["platform"] == DOMAIN and entity["device_id"] in {device["id"] for device in service}
        ]
        checks.expect_equal(
            "Status and eWeLink devices seen keep their entity ids, with the service device's unique ids; Reachable is "
            "gone",
            sorted((key, self._entity_ids.get(key), f"{entry_id}_{key}") for key in ("devices_seen", "status")),
            sorted(entities),
        )
        checks.expect_equal("Reachable has no state", None, await self._state_of(V1_REACHABLE))
        issue_id = UNMIGRATED_ISSUE[0]
        issues = await self._wait_for_issues(websocket, lambda issues: issue_id in {issue[0] for issue in issues})
        checks.expect_equal(
            "Repairs asks to choose the switch's lights again: a warning, which its switch's dialog fixes",
            [UNMIGRATED_ISSUE],
            [issue for issue in issues if issue[0] == issue_id],
        )
        await self._check_lights(
            {RENAMED: "on", LIVING_ROOM: "on", PORCH: "off"}, "as before the stop, with the light enabled again"
        )
        await self._expect_logged(UNMIGRATED)

    async def _repair(self, websocket: WebSocket) -> None:
        """Fixes the issue as a user does in Repairs: its repair flow explains it, and opens the switch's *Edit switch*
        dialog, the reconfigure flow of its subentry, where the user keeps the device and picks the lights."""
        checks = self.checks
        checks.step("Fix the issue as a user does: its repair flow, then the switch's dialog, which it opens")
        started = time.monotonic()
        flow = await self._flow("POST", REPAIR_FLOWS, {"handler": DOMAIN, "issue_id": UNMIGRATED_ISSUE[0]})
        _expect_step(flow, "form", "confirm")
        checks.expect_equal(
            "the repair flow explains the issue, with its placeholders",
            UNMIGRATED_PLACEHOLDERS,
            flow.get("description_placeholders"),
        )
        result = await self._flow("POST", f"{REPAIR_FLOWS}/{flow['flow_id']}", {})
        _expect_step(result, "abort", None)
        checks.expect_equal(
            "confirmed, the repair flow ends to open the switch's dialog", "edit_switch", result.get("reason")
        )
        path = f"{SUBENTRY_FLOWS}/{_next_subentry_flow(result, "The repair flow didn't open the switch's dialog")}"
        flow = await self._flow("GET", path)
        _expect_step(flow, "menu", "reconfigure")
        checks.expect_equal(
            "the dialog is Edit switch of the switch, which offers to keep its device first",
            ("keep", DEVICE_ID),
            ((flow.get("menu_options") or [None])[0], (flow.get("description_placeholders") or {}).get("device_id")),
        )
        flow = await self._flow("POST", path, {"next_step_id": "keep"})
        _expect_step(flow, "form", "settings")
        checks.expect_equal(
            "its settings name the entries of 1.x that have no light, and have no light chosen",
            (
                "\n\nFrom sonoff2hue 1.x, without a light in Home Assistant: "
                f"{', '.join(f'`{entry}`' for entry in V1_ENTRIES)}. Choose their lights again.",
                [],
            ),
            (flow["description_placeholders"].get("missing"), _suggested(flow, "lights")),
        )
        result = await self._flow("POST", path, {"name": SWITCH_NAME, "lights": [RENAMED, LIVING_ROOM], "advanced": {}})
        _expect_step(result, "abort", None)
        checks.expect_equal(
            f"the dialog saved the switch with {RENAMED} and {LIVING_ROOM}",
            ("saved", {"switch": SWITCH_NAME}),
            (result.get("reason"), result.get("description_placeholders")),
        )
        checks.timed("the repair", started)

        checks.expect_equal(
            "Repairs has no issue of the integration any more",
            [],
            await self._wait_for_issues(websocket, lambda issues: not issues),
        )
        _, subentry = await self._stored_entry(lambda _, subentry: bool(subentry["data"].get("targets")))
        checks.expect_equal(
            "the switch is saved with its lights by their registry ids, and without the entries of 1.x",
            {
                "device_id": DEVICE_ID,
                "name": SWITCH_NAME,
                "device_key": None,
                "targets": [self._light_ids[HALLWAY], self._light_ids[LIVING_ROOM]],
            },
            subentry["data"],
        )
        await self._wait_until_running(TWO_LIGHTS)

    async def _press_after_repair(self, websocket: WebSocket) -> None:
        """The switch with its lights again: saving it started the switches with a new runner, for which the switch's
        last seq is its state, and the next one a press, which turns both lights off."""
        checks = self.checks
        checks.step("Press the wall switch: its last seq again (7), its state for the new runner, then 8")
        started = time.monotonic()
        await self._expect_toggle(
            websocket,
            [7, 8],
            turned_on=False,
            targets=[RENAMED, LIVING_ROOM],
            lights={RENAMED: "off", LIVING_ROOM: "off", PORCH: "off"},
        )
        checks.timed("a press after the repair", started)
        await self._expect_logged(
            RESTARTING,
            LISTENING,
            f"{DISPLAY_NAME} -> {RENAMED}, {LIVING_ROOM}",
            f"{DISPLAY_NAME}: online at {SWITCH_ADDRESS} (seq 7;",
            f"{DISPLAY_NAME}: wall switch operated (seq 8).",
            f"{DISPLAY_NAME}: turned OFF {RENAMED}, {LIVING_ROOM} in ",
        )

    # What the integration shows.

    async def _wait_until_running(self, message: str, problems: Sequence[str] = ()) -> None:
        """Waits until the integration's status is running, with ``message`` and ``problems``. The supervisor sets it as
        it starts the runner, which adds its packet handler to the mDNS listener in the same turn of the event loop, so
        from then on a broadcast reaches the runner."""
        started = time.monotonic()
        service = await self._wait_for_status("running", message, list(problems))
        self.checks.ok(
            "the integration's status, in its diagnostics, is running: "
            f"{service['message']} ({time.monotonic() - started:.1f} s)"
        )

    async def _wait_for_status(
        self, state: str, message: str | None = None, problems: list[str] | None = None
    ) -> dict[str, Any]:
        """Waits until the integration's status, ``entry.runtime_data.status``, has ``state``, and ``message`` and
        ``problems`` if given, as its diagnostics show it (decision 26), and returns its service: state, message and
        problems."""
        service: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(STATUS_TIMEOUT):
                while True:
                    if (status := await self._status()) is not None:
                        current: dict[str, Any] = status["service"]
                        service = current
                        if (
                            current["state"] == state
                            and (message is None or current["message"] == message)
                            and (problems is None or current["problems"] == problems)
                        ):
                            return current
                    await asyncio.sleep(POLL_INTERVAL)
        except TimeoutError:
            wanted = ", ".join(
                [state]
                + ([] if message is None else [f"with the message {message!r}"])
                + ([] if problems is None else [f"with the problems {problems}"])
            )
            raise StepError(
                f"The integration's status isn't {wanted} within {STATUS_TIMEOUT:g} s; its diagnostics show {service}."
            ) from None

    async def _wait_for_targets(self, targets: list[str]) -> None:
        """Waits until the switch's status has ``targets``, the lights that it toggles, as the runner has them."""
        shown: object = None
        try:
            async with asyncio.timeout(STATUS_TIMEOUT):
                while True:
                    status = await self._status()
                    shown = None if status is None or len(status["switches"]) != 1 else status["switches"][0]["targets"]
                    if shown == targets:
                        return
                    await asyncio.sleep(POLL_INTERVAL)
        except TimeoutError:
            raise StepError(
                f"The switch's targets aren't {targets} within {STATUS_TIMEOUT:g} s, but {shown}."
            ) from None

    async def _issues(self, websocket: WebSocket) -> list[Issue]:
        """The integration's repair issues, as the WebSocket command ``repairs/list_issues`` lists them."""
        listed = await websocket.command({"type": "repairs/list_issues"})
        return [
            (
                issue["issue_id"],
                issue["severity"],
                issue["translation_key"],
                issue["is_fixable"],
                issue["translation_placeholders"],
            )
            for issue in listed["issues"]
            if issue["domain"] == DOMAIN
        ]

    async def _wait_for_issues(self, websocket: WebSocket, condition: Callable[[list[Issue]], bool]) -> list[Issue]:
        """The integration's repair issues once ``condition`` holds for them, else as they are after the status's
        timeout, for the check to report: the runtime raises and deletes them from the status's published snapshots,
        up to 500 ms after a change."""
        issues = await self._issues(websocket)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(STATUS_TIMEOUT):
                while not condition(issues):
                    await asyncio.sleep(POLL_INTERVAL)
                    issues = await self._issues(websocket)
        return issues

    async def _wait_for_device(self, seq: int) -> None:
        """Waits until the LAN monitor has the switch's broadcast of ``seq``, as the diagnostics' devices show it."""
        while True:
            diagnostics = await self._diagnostics()
            devices = [] if diagnostics is None else diagnostics["lan_devices"] or []
            if any(device["device_id"] == DEVICE_ID and device["seq"] == seq for device in devices):
                return
            await asyncio.sleep(POLL_INTERVAL)

    async def _diagnostics(self) -> dict[str, Any] | None:
        """The integration's diagnostics of the config entry: ``config``, ``status``, ``lan_devices`` and ``activity``;
        ``None`` until Home Assistant has registered its diagnostics platform, a moment after its first setup."""
        assert self._entry_id is not None  # noqa: S101 - known once the setup flow added the entry
        download = await self.hass.diagnostics(self._entry_id)
        data: dict[str, Any] | None = None if download is None else download["data"]
        return data

    async def _status(self) -> dict[str, Any] | None:
        """The integration's status as its diagnostics show it, or None while there is none: before the diagnostics
        platform is registered, or while the entry isn't loaded."""
        diagnostics = await self._diagnostics()
        status: dict[str, Any] | None = None if diagnostics is None else diagnostics["status"]
        return status

    async def _stored_entry(
        self, condition: Callable[[dict[str, Any], dict[str, Any]], bool]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The config entry and its switch's subentry as Home Assistant stored them, once ``condition`` holds for them:
        it writes the config entries a moment after a change. No API shows their data."""
        entry: dict[str, Any] | None = None
        subentry: dict[str, Any] | None = None
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(STATUS_TIMEOUT):
                while True:
                    entries = storage.read(self.config, storage.CONFIG_ENTRIES)["data"]["entries"]
                    entry = next((item for item in entries if item["entry_id"] == self._entry_id), None)
                    switches = [] if entry is None else entry["subentries"]
                    subentry = next((item for item in switches if item["subentry_type"] == SUBENTRY_SWITCH), None)
                    if entry is not None and subentry is not None and condition(entry, subentry):
                        return entry, subentry
                    await asyncio.sleep(POLL_INTERVAL)
        if entry is None or subentry is None:
            raise StepError(f"Home Assistant's stored config entries have no entry {self._entry_id} with a switch.")
        return entry, subentry

    async def _check_saved_switch(self, what: str) -> None:
        """The switch as the runtime's configuration has it, the subentry's values as stored: its device, name and the
        two lights by their registry ids."""
        diagnostics = await self._diagnostics()
        switches = [] if diagnostics is None else diagnostics["config"]["switches"]
        self.checks.expect_equal(
            what,
            [(DEVICE_ID, SWITCH_NAME, [self._light_ids[HALLWAY], self._light_ids[LIVING_ROOM]])],
            [(item["device_id"], item["name"], item["targets"]) for item in switches],
        )

    async def _check_status(self, turned_on: bool, targets: list[str], problems: Sequence[str] = ()) -> None:
        """The status after a toggle: running, and the switch online at its address, in detached mode, with its targets,
        its problems, its last toggle, and no error."""
        status = await self._status()
        switches = [] if status is None else status["switches"]
        actual = (
            None
            if status is None or len(switches) != 1
            else (
                status["service"]["state"],
                switches[0]["device_id"],
                switches[0]["online"],
                switches[0]["address"],
                switches[0]["detached"],
                switches[0]["targets"],
                switches[0]["problems"],
                (switches[0]["last_action"] or {}).get("turned_on"),
                (switches[0]["last_error"] or {}).get("message"),
            )
        )
        self.checks.expect_equal(
            f"the status in the diagnostics: running; the switch online at {SWITCH_ADDRESS}, detached, with its targets, "
            f"{'its problem' if problems else 'no problem'}, its last toggle {'on' if turned_on else 'off'} and no error",
            ("running", DEVICE_ID, True, SWITCH_ADDRESS, True, targets, list(problems), turned_on, None),
            actual,
        )

    async def _check_entities(self, websocket: WebSocket, turned_on: bool, seq: int) -> None:
        """What the UI shows (decision 25), through the REST API: the service device's Status sensor, and the switch's
        Last action sensor and Press event, with the event type and the seq of the last press. They are found in the
        entity registry by their translation keys. Status and Last action follow the status's published snapshots, up
        to 500 ms after a change, so the test waits for them."""
        self._entity_ids = entity_ids = {
            entity["translation_key"]: entity["entity_id"]
            for entity in await self._entity_registry(websocket)
            if entity["platform"] == DOMAIN
        }
        expected: dict[str, object] = {
            "status": "running",
            "last_action": ACTION_TURNED_ON if turned_on else ACTION_TURNED_OFF,
            "press": ("press", seq),
        }
        shown: dict[str, object] = dict.fromkeys(expected)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(STATUS_TIMEOUT):
                while True:
                    states = {key: await self._entity_state(entity_ids.get(key)) for key in expected}
                    press = states["press"]
                    shown = {
                        "status": None if states["status"] is None else states["status"]["state"],
                        "last_action": None if states["last_action"] is None else states["last_action"]["state"],
                        "press": None
                        if press is None
                        else (press["attributes"].get("event_type"), press["attributes"].get("seq")),
                    }
                    if shown == expected:
                        break
                    await asyncio.sleep(POLL_INTERVAL)
        self.checks.expect_equal(
            f"the UI's entities: Status ({entity_ids.get('status')}), Last action ({entity_ids.get('last_action')}) "
            f"and Press ({entity_ids.get('press')}), with the event type and seq {seq}",
            expected,
            shown,
        )

    async def _check_lights(self, expected: Mapping[str, str | None], what: str) -> None:
        """The states of lights, or of their input booleans, over the REST API: ``None`` for one that has none. The
        light actions return once the lights are switched, so the states show a toggle as it ends, but the test waits
        a little, so that a late one shows as what it is."""
        shown: dict[str, str | None] = {}
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(LIGHTS_TIMEOUT):
                while True:
                    shown = {entity_id: await self._state_of(entity_id) for entity_id in expected}
                    if shown == expected:
                        break
                    await asyncio.sleep(POLL_INTERVAL)
        self.checks.expect_equal(f"the lights, over the REST API: {what}", dict(expected), shown)

    async def _entity_registry(self, websocket: WebSocket) -> list[dict[str, Any]]:
        registry: list[dict[str, Any]] = await websocket.command({"type": "config/entity_registry/list"})
        return registry

    async def _entity_state(self, entity_id: str | None) -> dict[str, Any] | None:
        """An entity's state and attributes, as the REST API gives them, or None without the entity."""
        if entity_id is None:
            return None
        status, answer = await self.hass.request("GET", f"/api/states/{entity_id}")
        return answer if status == 200 and isinstance(answer, dict) else None

    async def _state_of(self, entity_id: str) -> str | None:
        """An entity's state, as the REST API gives it, or None without one."""
        state = await self._entity_state(entity_id)
        return None if state is None else state.get("state")

    async def _expect_toggle(
        self,
        websocket: WebSocket,
        seqs: list[int],
        *,
        turned_on: bool,
        targets: list[str],
        lights: Mapping[str, str | None],
        problems: Sequence[str] = (),
    ) -> None:
        """Sends the switch's announcements, of which only the last is to be a press, and checks what came of it: the
        press and its toggle of ``targets`` on the bus, the lights, the status and the entities. Every target is to
        change."""
        events = await _subscribe(websocket)
        await self._announce(seqs, events)
        self._check_events(events, seqs[-1], turned_on, targets)
        state = "on" if turned_on else "off"
        await self._check_lights(lights, f"{', '.join(targets)} turned {state}")
        await self._check_status(turned_on, targets, problems)
        await self._check_entities(websocket, turned_on, seqs[-1])

    async def _announce(self, seqs: list[int], events: Events) -> None:
        """Sends the switch's announcements and waits until the last one's press has been toggled, or until it is clear
        that it won't be."""
        await self.switch.announce(seqs)
        try:
            # If they don't come, the checks show what did.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(1.5 * len(seqs) + EVENTS_TIMEOUT):
                    await events.wait(lambda received: _toggled(received, seqs[-1]))
        finally:
            await self.switch.done()

    async def _expect_logged(self, *lines: str) -> None:
        """Checks that Home Assistant's log since it last started has each line. The integration logs them before it
        fires the events that the test waited for, but a thread of Home Assistant's writes the log file, so the test
        waits for them."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(LOG_TIMEOUT):
                await self.hass.wait_for_log(*lines)
        log = await self.hass.error_log()
        for line in lines:
            self.checks.expect(f'the integration logged "{line}…"', line in log)

    def _check_events(self, events: Events, seq: int, turned_on: bool, targets: list[str]) -> None:
        """The press, followed by its toggle of the targets, each changed."""
        checks = self.checks
        checks.expect_equal(
            "Home Assistant's bus had a press, followed by its toggle",
            [EVENT_PRESS, EVENT_TOGGLE],
            [event.event_type for event in events.received],
        )
        checks.expect_equal(
            f"the {EVENT_PRESS} event names the switch and the press's seq",
            [(DEVICE_ID, SWITCH_NAME, seq)],
            [(data.get(ATTR_SWITCH_ID), data.get(ATTR_NAME), data.get(ATTR_SEQ)) for data in events.of(EVENT_PRESS)],
        )
        toggles = events.of(EVENT_TOGGLE)
        checks.expect_equal(
            f"the {EVENT_TOGGLE} event: toggled, turned on {str(turned_on).lower()}, its lights by their entity ids, "
            f"all {len(targets)} changed",
            [(DEVICE_ID, RESULT_TOGGLED, turned_on, targets, len(targets), 0, 0)],
            [
                tuple(data.get(key) for key in (ATTR_SWITCH_ID, ATTR_RESULT, ATTR_TURNED_ON, ATTR_TARGETS))
                + tuple(data.get(key) for key in (ATTR_CHANGED, ATTR_UNCHANGED, ATTR_FAILED))
                for data in toggles
            ],
        )
        if toggles:
            print(f"        the toggle took {', '.join(f'{data.get(ATTR_ELAPSED_MS)} ms' for data in toggles)}")

    # Stopping, and the logs.

    async def _stop(self, label: str = "the stop") -> None:
        """Stops Home Assistant as Ctrl+C does, if it has started since it was last stopped so, and checks the logs of
        its starts since then: they may hold only the integration's warnings and errors that the steps expect. Its
        time goes into the timings as ``label``."""
        checks, hass = self.checks, self.hass
        starts = hass.starts - self._stopped_starts
        if starts == 0:
            return
        checks.step("Stop Home Assistant, as Ctrl+C does")
        started = time.monotonic()
        code = await hass.stop()
        self._stopped_starts = hass.starts
        checks.expect("Home Assistant stopped cleanly on Ctrl+C, with exit code 0", code == 0, f"exit code: {code}")
        checks.timed(label, started)
        if self._entry_id is None:
            return
        allowed = "".join(f', but for "{text}"' for text in self._expected_problems)
        for log in self._logs(starts):
            if log.exists():
                problems = _integration_problems(log, self._expected_problems)
                checks.expect(
                    f"{log.name} has no warning or error of the integration, nor an error about it{allowed}",
                    not problems,
                    "\n".join(problems),
                )

    def _logs(self, starts: int) -> list[Path]:
        """Home Assistant's logs of its last ``starts`` starts, oldest first, of the two that it keeps: it moves the
        last one to ``home-assistant.log.1`` when it starts, so after its first start that holds nothing."""
        logs = [self.config / "home-assistant.log.1", self.config / "home-assistant.log"]
        return logs[-min(starts, len(logs)) :] if starts > 0 else []

    def _show_logs(self) -> None:
        print("\nThe integration's lines in Home Assistant's log:")
        for log in self._logs(self.hass.starts):
            if log.exists():
                print(f"--- {log}")
                lines = [line for line in _read(log) if "[custom_components.sonoff2hue" in line]
                for line in lines[-80:]:
                    print(line)
        if self.hass.log.exists():
            print(f"\nThe last lines of {self.hass.log}:")
            for line in _read(self.hass.log)[-60:]:
                print(line)
        print(f"\nThe logs are in {self.settings.work}.")


async def _subscribe(websocket: WebSocket) -> Events:
    """The integration's events, from now on."""
    events = Events()
    await websocket.subscribe(EVENT_PRESS, events.add)
    await websocket.subscribe(EVENT_TOGGLE, events.add)
    return events


def _flow_changed(flow_id: str, after: int) -> Callable[[list[Event]], bool]:
    """Whether Home Assistant announced a change of the flow after the first ``after`` announcements."""
    return lambda events: any(event.data.get("flow_id") == flow_id for event in events[after:])


def _toggled(events: list[Event], seq: int) -> bool:
    """Whether the press of ``seq`` came, and a toggle after it."""
    types = [event.event_type for event in events]
    for index, event in enumerate(events):
        if event.event_type == EVENT_PRESS and event.data.get(ATTR_SEQ) == seq:
            return EVENT_TOGGLE in types[index + 1 :]
    return False


def _state(service: dict[str, Any]) -> tuple[str, str | None]:
    return service["state"], service["message"]


def _next_subentry_flow(result: dict[str, Any], failure: str) -> str:
    """The id of the subentry flow that a flow's result opens next, as the frontend opens it."""
    next_flow = result.get("next_flow")
    if not (isinstance(next_flow, list) and len(next_flow) == 2 and next_flow[0] == SUBENTRIES_FLOW):
        raise StepError(f"{failure}: its next_flow is {next_flow}.")
    flow_id: str = next_flow[1]
    return flow_id


def _field(form: dict[str, Any], name: str) -> dict[str, Any]:
    """A form's field, as the frontend gets it."""
    for item in form.get("data_schema") or []:
        if item.get("name") == name:
            field: dict[str, Any] = item
            return field
    raise StepError(f"The form has no field {name}: {form.get('data_schema')}")


def _suggested(form: dict[str, Any], name: str) -> object:
    """The value that a form suggests for a field, or None without one."""
    return (_field(form, name).get("description") or {}).get("suggested_value")


def _one(items: list[dict[str, Any]], condition: Callable[[dict[str, Any]], bool], what: str) -> dict[str, Any]:
    """The one item of Home Assistant's storage that ``condition`` holds for."""
    found = [item for item in items if condition(item)]
    if len(found) != 1:
        raise StepError(f"Home Assistant's storage has {len(found)} of {what}, where the test expects one.")
    return found[0]


def _expect_step(result: dict[str, Any], result_type: str, step_id: str | None) -> None:
    if result.get("type") != result_type or (step_id is not None and result.get("step_id") != step_id):
        shown = {key: result.get(key) for key in ("type", "step_id", "errors", "description_placeholders", "reason")}
        raise StepError(f"The flow should be at {result_type} {step_id or ''}, but is at {shown}.")


def _integration_problems(log: Path, expected: Sequence[str] = ()) -> list[str]:
    """The integration's warnings and errors in a Home Assistant log, other errors that name it, and the reports of
    Home Assistant's frame helper about it, but for the lines that hold one of ``expected``, which a step caused on
    purpose. A log in which no line of the integration's is recognized is one too: its lines would have been missed."""
    problems = []
    seen = False
    for line in _read(log):
        match = _LOG_LINE.match(line)
        if match is None:
            continue
        level, logger = match["level"], match["logger"]
        ours = logger == "custom_components.sonoff2hue" or logger.startswith("custom_components.sonoff2hue.")
        seen = seen or ours
        if (
            (ours and level in {"WARNING", "ERROR", "CRITICAL"})
            or (level in {"ERROR", "CRITICAL"} and DOMAIN in line)
            or "custom integration 'sonoff2hue'" in line
        ) and not any(text in line for text in expected):
            problems.append(line)
    if not seen:
        problems.append("No line of the integration was recognized in it: the scan doesn't know the log's format.")
    return problems


def _read(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8", errors="replace").splitlines()
