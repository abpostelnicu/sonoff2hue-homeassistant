"""The end-to-end test's steps: Home Assistant and the integration set up as a user sets them up, a switch added, two
presses, a restart, and a press after it."""

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Final

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

from . import DEVICE_ID, LIGHT, POLL_INTERVAL, ROOM, SWITCH_NAME, TARGETS, StepError, puts, within
from .checks import Checks, Event, Events
from .devices import FakeHueBridge, SimulatedSwitch
from .hass import HomeAssistant, WebSocket

START_TIMEOUT: Final = 300.0
"""Seconds for Home Assistant to finish starting; its first start after an install compiles its modules."""
FAKE_HUE_TIMEOUT: Final = 30.0
PAIRING_TIMEOUT: Final = 75.0
"""The pairing waits up to 60 seconds for the link button."""
IDENTIFY_TIMEOUT: Final = 15.0
"""Seconds for identify-by-press to select the switch after its press; it waits up to 60 seconds."""
STATUS_TIMEOUT: Final = 30.0
"""Seconds for the integration's status to reach a state, such as running after a setup, for which the supervisor first
pings the bridge and resolves the names."""
EVENTS_TIMEOUT: Final = 15.0
"""Seconds for the last announcement's press and toggle, after the switch's announcements, 1.5 seconds apart."""
STOP_TIMEOUT: Final = 60.0
"""Seconds for Home Assistant to stop for a restart."""
LOG_TIMEOUT: Final = 5.0
"""Seconds for a line to reach Home Assistant's log file, which a thread of its own writes."""

RESTARTING: Final = "Configuration changed; restarting the bridge."
"""The supervisor's line when a change of the configuration, such as a switch added, restarts the bridge (decision 23)."""
LISTENING: Final = "Listening for wall switch presses."
"""The runner's line as it starts, which each start of the bridge has to log."""
DISPLAY_NAME: Final = f"{SWITCH_NAME} ({DEVICE_ID})"
"""The switch as the integration's log names it."""
SWITCH_ADDRESS: Final = "192.0.2.10"
"""The address in the simulated switch's A record."""

CONFIG_FLOWS: Final = "/api/config/config_entries/flow"
SUBENTRY_FLOWS: Final = "/api/config/config_entries/subentries/flow"

# A line of Home Assistant's log: time, level, thread, logger and message.
_LOG_LINE = re.compile(r"^\S+ \S+ (?P<level>[A-Z]+) \([^)]*\) \[(?P<logger>[^\]]+)\] ")


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    """The repository's root, whose ``custom_components/sonoff2hue`` Home Assistant loads."""
    work: Path
    """Home Assistant's configuration folder (``config``) and the logs."""
    hass_port: int
    fake_hue_port: int


class EndToEnd:
    """One run of the end-to-end test."""

    def __init__(self, settings: Settings, session: aiohttp.ClientSession) -> None:
        root, work = settings.root, settings.work
        self.settings = settings
        self.checks = Checks()
        self.config = work / "config"
        self.hass = HomeAssistant(root, self.config, settings.hass_port, work / "hass.log", session)
        self.fake = FakeHueBridge(root, settings.fake_hue_port, work / "fake-hue.log", session)
        self.switch = SimulatedSwitch(root, DEVICE_ID)
        self._entry_id: str | None = None
        self._flow_changes = Events()
        """The announcements that a flow changed (``data_entry_flow_progressed``), of any flow."""

    async def run(self) -> bool:
        """Runs every step, stops Home Assistant and the fake bridge, and reports; ``True`` if every check passed."""
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
        await self._start_fake_bridge()
        await self._start_home_assistant()
        websocket = await self.hass.websocket()
        try:
            await self._set_up_integration(websocket)
            await self._add_switch(websocket)
            await self._wait_until_running()
            await self._press_twice(websocket)
            await self._restart(websocket)
        finally:
            await websocket.close()
        websocket = await self.hass.websocket()
        try:
            await self._wait_until_running()
            await self._press_after_restart(websocket)
        finally:
            await websocket.close()

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

    async def _start_fake_bridge(self) -> None:
        checks = self.checks
        checks.step(f"Start the fake Hue bridge on https://{self.fake.address}/")
        started = time.monotonic()
        async with within(FAKE_HUE_TIMEOUT, f"The fake Hue bridge didn't answer on https://{self.fake.address}/"):
            await self.fake.start()
        elapsed = checks.timed("the fake bridge's start", started)
        checks.ok(f"it answers, with its link button released ({elapsed:.1f} s)")

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

    # The flows.

    async def _set_up_integration(self, websocket: WebSocket) -> None:
        """Adds the integration as a user does: the setup flow's *Enter the bridge address*, then its link step, with the
        bridge's link button pressed. The entry has no switch yet."""
        checks = self.checks
        checks.step("Set up sonoff2hue through the setup flow's API, as the frontend does: the bridge at its address")
        started = time.monotonic()
        await websocket.subscribe("data_entry_flow_progressed", self._flow_changes.add)
        flow = await self._flow("POST", CONFIG_FLOWS, {"handler": DOMAIN})
        _expect_step(flow, "menu", "user")
        path = f"{CONFIG_FLOWS}/{flow['flow_id']}"
        flow = await self._flow("POST", path, {"next_step_id": "address"})
        _expect_step(flow, "form", "address")
        await self.fake.post("/_fake/link-button")
        seen = len(self._flow_changes.received)
        flow = await self._flow("POST", path, {"address": self.fake.address})
        _expect_step(flow, "progress", "link")
        checks.ok("the bridge at the address answers, and the flow's link step asks it for a key")
        async with within(PAIRING_TIMEOUT, "The link step didn't end"):
            result = await self._follow(path, flow, seen)
        _expect_step(result, "create_entry", None)
        elapsed = checks.timed("the setup flow", started)
        checks.ok(f"paired with the fake bridge at {self.fake.address}, and the entry was added ({elapsed:.1f} s)")
        checks.expect_equal(
            "the entry's title and the flow's last text",
            (ENTRY_TITLE, "paired"),
            (result.get("title"), result["description"]),
        )

        self._entry_id = entry_id = result["result"]["entry_id"]
        status, entries = await self.hass.request("GET", f"/api/config/config_entries/entry?domain={DOMAIN}")
        states = [(entry["entry_id"], entry["state"]) for entry in entries] if status == 200 else entries
        checks.expect_equal("the integration has one entry, and it is loaded", [(entry_id, "loaded")], states)
        service = await self._wait_for_status("not_configured")
        checks.expect_equal(
            "its status, in its diagnostics, asks for a switch", ("not_configured", NO_SWITCHES), _state(service)
        )

    async def _add_switch(self, websocket: WebSocket) -> None:
        """Adds the switch as a user does with *Add switch*: the switch subentry flow (decision 24) identifies the
        MINIR4M by a press of its wall switch, which the LAN monitor reports, then takes its name and the light and the
        room it toggles from the bridge's lists, as the frontend shows them.

        Identify-by-press selects the first device that reports a press, and the switch's announcements reach every
        listener on the machine, as those of a test that runs at the same time in another checkout reach this one. So
        the switch's state, which the LAN monitor takes as a baseline, comes first, and its press right after
        identify-by-press starts, so that another device's press can hardly come in between."""
        checks = self.checks
        checks.step(f"Add the switch through the switch subentry flow's API: identify {DEVICE_ID} by a press")
        started = time.monotonic()
        flow = await self._flow("POST", SUBENTRY_FLOWS, {"handler": [self._entry_id, SUBENTRY_SWITCH]})
        _expect_step(flow, "menu", "user")
        path = f"{SUBENTRY_FLOWS}/{flow['flow_id']}"
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
        answer = {
            "name": SWITCH_NAME,
            "lights": [_option(flow, "lights", LIGHT)],
            "rooms": [_option(flow, "rooms", ROOM)],
            "advanced": {},
        }
        result = await self._flow("POST", path, answer)
        _expect_step(result, "create_entry", None)
        elapsed = checks.timed("the switch subentry flow", started)
        checks.ok(
            f"the switch {SWITCH_NAME} was added, with {LIGHT} and {ROOM} from the bridge's lists ({elapsed:.1f} s)"
        )
        checks.expect_equal("the flow's last text", "added", result.get("description"))

        subentries = await websocket.command({"type": "config_entries/subentries/list", "entry_id": self._entry_id})
        checks.expect_equal(
            "the entry has the switch as a switch subentry",
            [(SUBENTRY_SWITCH, SWITCH_NAME, DEVICE_ID)],
            [(item["subentry_type"], item["title"], item["unique_id"]) for item in subentries],
        )
        diagnostics = await self._diagnostics()
        switches = [] if diagnostics is None else diagnostics["config"]["switches"]
        checks.expect_equal(
            "the switch is saved with the light by its name and the room as room:<name>",
            [(DEVICE_ID, SWITCH_NAME, [LIGHT], [f"room:{ROOM}"])],
            [(item["device_id"], item["name"], item["lights"], item["groups"]) for item in switches],
        )

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

    async def _wait_until_running(self) -> None:
        """Waits until the integration's status is running. The supervisor sets it as it starts the runner, which adds
        its packet handler to the mDNS listener in the same turn of the event loop, so from then on a broadcast reaches
        the runner."""
        started = time.monotonic()
        service = await self._wait_for_status("running")
        label = "the runner's start" if self.hass.starts == 1 else "the runner's start after the restart"
        elapsed = self.checks.timed(label, started)
        self.checks.ok(
            f"the integration's status, in its diagnostics, is running: {service['message']} ({elapsed:.1f} s)"
        )

    async def _wait_for_status(self, state: str) -> dict[str, Any]:
        """Waits until the integration's status, ``entry.runtime_data.status``, has ``state``, as its diagnostics show it
        (decision 26), and returns its service: state, message and problems."""
        service: dict[str, Any] | None = None
        try:
            async with asyncio.timeout(STATUS_TIMEOUT):
                while True:
                    if (status := await self._status()) is not None:
                        current: dict[str, Any] = status["service"]
                        if (service := current)["state"] == state:
                            return current
                    await asyncio.sleep(POLL_INTERVAL)
        except TimeoutError:
            raise StepError(
                f"The integration's status isn't {state} within {STATUS_TIMEOUT:g} s; its diagnostics show {service}."
            ) from None

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

    async def _check_status(self, turned_on: bool) -> None:
        """The status after a toggle: running, and the switch online at its address, in detached mode, with its targets
        and its last toggle, without an error."""
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
                (switches[0]["last_action"] or {}).get("turned_on"),
                switches[0]["last_error"],
            )
        )
        self.checks.expect_equal(
            f"the status in the diagnostics: running; the switch online at {SWITCH_ADDRESS}, detached, with its targets, "
            f"its last toggle {'on' if turned_on else 'off'} and no error",
            ("running", DEVICE_ID, True, SWITCH_ADDRESS, True, TARGETS, turned_on, None),
            actual,
        )

    async def _check_entities(self, websocket: WebSocket, turned_on: bool, seq: int) -> None:
        """What the UI shows (decision 25), through the REST API: the bridge's Status sensor, and the switch's Last
        action sensor and Press event, with the event type and the seq of the last press. They are found in the entity
        registry by their translation keys. Status and Last action follow the status's published snapshots, up to
        500 ms after a change, so the test waits for them."""
        registry = await websocket.command({"type": "config/entity_registry/list"})
        entity_ids = {
            entity["translation_key"]: entity["entity_id"] for entity in registry if entity.get("platform") == DOMAIN
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

    async def _entity_state(self, entity_id: str | None) -> dict[str, Any] | None:
        """An entity's state and attributes, as the REST API gives them, or None without the entity."""
        if entity_id is None:
            return None
        status, answer = await self.hass.request("GET", f"/api/states/{entity_id}")
        return answer if status == 200 and isinstance(answer, dict) else None

    async def _press_twice(self, websocket: WebSocket) -> None:
        """Two presses, as ``scripts/develop press 2`` sends them: the last seq again, then the next two. Adding the
        switch restarted the bridge with a new runner, for which the last seq is the switch's state, a baseline."""
        checks = self.checks
        checks.step(f"Press the wall switch twice: {DEVICE_ID} announces seq 2 again, its state, then 3 and 4")
        events = await _subscribe(websocket)
        started = time.monotonic()
        await self._announce([2, 3, 4], events)
        checks.timed("two presses", started)

        checks.expect_equal(
            "the fake bridge got exactly the PUTs of two toggles: on, then off",
            puts(on=True) + puts(on=False),
            await self.fake.puts(),
        )
        lights = await self.fake.get("/_fake/lights")
        states = re.findall(r"^\w+ '[^']*' (on|off)", lights, re.MULTILINE)
        checks.expect("all of the fake bridge's lights are off again", bool(states) and set(states) == {"off"}, lights)
        self._check_events(events, [3, 4], [True, False])
        await self._check_status(turned_on=False)
        await self._check_entities(websocket, turned_on=False, seq=4)
        await self._expect_logged(
            RESTARTING,
            LISTENING,
            f"{DISPLAY_NAME}: online at {SWITCH_ADDRESS} (seq 2;",
            f"{DISPLAY_NAME}: wall switch operated (seq 3).",
            f"{DISPLAY_NAME}: turned ON {', '.join(TARGETS)} in ",
            f"{DISPLAY_NAME}: wall switch operated (seq 4).",
            f"{DISPLAY_NAME}: turned OFF {', '.join(TARGETS)} in ",
        )

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
        never a press, and the next seq is one."""
        checks = self.checks
        checks.step("After the restart: the switch announces its last seq again (4), then the next (5)")
        events = await _subscribe(websocket)
        started = time.monotonic()
        await self._announce([4, 5], events)
        checks.timed("a press after the restart", started)

        self._check_events(events, [5], [True])
        await self._check_status(turned_on=True)
        await self._check_entities(websocket, turned_on=True, seq=5)
        checks.expect_equal(
            "the fake bridge got no PUT for the repeated seq 4, and the PUTs of one toggle, on, for seq 5",
            puts(on=True) + puts(on=False) + puts(on=True),
            await self.fake.puts(),
        )
        # The runner's own word: the repeated seq was the switch's state, a baseline.
        await self._expect_logged(LISTENING, f"{DISPLAY_NAME}: online at {SWITCH_ADDRESS} (seq 4;")

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
        """Checks that Home Assistant's log has each line. The integration logs them before it fires the events that
        the test waited for, but a thread of Home Assistant's writes the log file, so the test waits for them."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(LOG_TIMEOUT):
                await self.hass.wait_for_log(*lines)
        log = await self.hass.error_log()
        for line in lines:
            self.checks.expect(f'the integration logged "{line}…"', line in log)

    def _check_events(self, events: Events, presses: list[int], turned_on: list[bool]) -> None:
        checks = self.checks
        order = [EVENT_PRESS, EVENT_TOGGLE] * len(presses)
        count = (
            "a press, followed by its toggle"
            if len(presses) == 1
            else f"{len(presses)} presses, each followed by its toggle"
        )
        checks.expect_equal(
            f"Home Assistant's bus had {count}",
            order,
            [event.event_type for event in events.received],
        )
        checks.expect_equal(
            f"{EVENT_PRESS} events name the switch and the presses' seq numbers",
            [(DEVICE_ID, SWITCH_NAME, seq) for seq in presses],
            [(data.get(ATTR_SWITCH_ID), data.get(ATTR_NAME), data.get(ATTR_SEQ)) for data in events.of(EVENT_PRESS)],
        )
        toggles = events.of(EVENT_TOGGLE)
        checks.expect_equal(
            f"{EVENT_TOGGLE} events: toggled, turned on: {', '.join(str(on).lower() for on in turned_on)}",
            [(DEVICE_ID, RESULT_TOGGLED, on, TARGETS, 2, 0, 0) for on in turned_on],
            [
                tuple(data.get(key) for key in (ATTR_SWITCH_ID, ATTR_RESULT, ATTR_TURNED_ON, ATTR_TARGETS))
                + tuple(data.get(key) for key in (ATTR_CHANGED, ATTR_UNCHANGED, ATTR_FAILED))
                for data in toggles
            ],
        )
        if toggles:
            took = ", ".join(f"{data.get(ATTR_ELAPSED_MS)} ms" for data in toggles)
            print(f"        the toggles took {took}")

    # Stopping, and the logs.

    async def _stop(self) -> None:
        checks, hass = self.checks, self.hass
        checks.step("Stop Home Assistant and the fake Hue bridge")
        started = time.monotonic()
        if hass.starts:
            code = await hass.stop()
            checks.expect("Home Assistant stopped cleanly on Ctrl+C, with exit code 0", code == 0, f"exit code: {code}")
        await self.fake.stop()
        checks.timed("the stop", started)
        for log in self._logs() if self._entry_id is not None else []:
            if log.exists():
                problems = _integration_problems(log)
                checks.expect(
                    f"{log.name} has no warning or error of the integration, nor an error about it",
                    not problems,
                    "\n".join(problems),
                )

    def _logs(self) -> list[Path]:
        """Home Assistant's log of each start, oldest first. It moves the last one to ``home-assistant.log.1`` when it
        starts, so after its first start that holds nothing."""
        logs = [self.config / "home-assistant.log.1", self.config / "home-assistant.log"]
        return logs[-self.hass.starts :] if self.hass.starts else []

    def _show_logs(self) -> None:
        print("\nThe integration's lines in Home Assistant's log:")
        for log in self._logs():
            if log.exists():
                print(f"--- {log}")
                lines = [line for line in _read(log) if "[custom_components.sonoff2hue" in line]
                for line in lines[-80:]:
                    print(line)
        for log, count in ((self.hass.log, 60), (self.fake.log, 20)):
            if log.exists():
                print(f"\nThe last lines of {log}:")
                for line in _read(log)[-count:]:
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


def _option(form: dict[str, Any], field: str, label: str) -> str:
    """The value of the option that a form's select field shows as ``label``, or as ``label`` with a light's state, as
    the frontend lets the user pick it."""
    for item in form.get("data_schema", []):
        if item.get("name") == field:
            for option in item["selector"]["select"]["options"]:
                if option["label"] == label or option["label"].startswith(f"{label} — "):
                    value: str = option["value"]
                    return value
    raise StepError(f"The form's field {field} has no option {label}: {form.get('data_schema')}")


def _expect_step(result: dict[str, Any], result_type: str, step_id: str | None) -> None:
    if result.get("type") != result_type or (step_id is not None and result.get("step_id") != step_id):
        shown = {key: result.get(key) for key in ("type", "step_id", "errors", "description_placeholders", "reason")}
        raise StepError(f"The flow should be at {result_type} {step_id or ''}, but is at {shown}.")


def _integration_problems(log: Path) -> list[str]:
    """The integration's warnings and errors in a Home Assistant log, other errors that name it, and the reports of
    Home Assistant's frame helper about it. A log in which no line of the integration's is recognized is one too: its
    lines would have been missed."""
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
        ):
            problems.append(line)
    if not seen:
        problems.append("No line of the integration was recognized in it: the scan doesn't know the log's format.")
    return problems


def _read(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8", errors="replace").splitlines()
