"""Home Assistant as the end-to-end test runs it: its process, started and restarted as ``scripts/develop`` does, and its
APIs, used as its frontend uses them: onboarding, REST and WebSocket."""

import asyncio
from collections.abc import Callable
import contextlib
import itertools
import json
import os
from pathlib import Path
import secrets
import signal
from typing import Any, Final

import aiohttp

from . import POLL_INTERVAL, StepError

PYTHON: Final = Path(".dev/venv/bin/python")
"""Home Assistant's own environment, which ``scripts/develop setup`` makes from ``requirements_dev.txt``."""
SKIP_PIP_PACKAGES: Final = "pymicro-vad,pyspeex-noise"
"""As ``scripts/develop`` runs Home Assistant: ``assist_pipeline``'s requirements, which have no wheels for Python 3.14."""
RESTART_EXIT_CODE: Final = 100
STOP_TIMEOUT: Final = 60.0
"""Seconds that it may take to stop before it is killed."""


class WebSocket:
    """A connection to Home Assistant's WebSocket API, as its frontend has one: commands with their results, and the
    events of its subscriptions."""

    def __init__(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        self._connection = connection
        self._ids = itertools.count(1)
        self._results: dict[int, asyncio.Future[Any]] = {}
        self._handlers: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._reader = asyncio.get_running_loop().create_task(self._read())

    @classmethod
    async def connect(cls, session: aiohttp.ClientSession, url: str, token: str) -> WebSocket:
        connection = await session.ws_connect(f"{url}/api/websocket")
        try:
            await _expect(connection, "auth_required")
            await connection.send_json({"type": "auth", "access_token": token})
            await _expect(connection, "auth_ok")
        except BaseException:
            await connection.close()
            raise
        return cls(connection)

    async def command(self, message: dict[str, Any]) -> Any:
        """Sends a command and returns its result; raises :class:`StepError` with its error."""
        return await self._send(message)

    async def subscribe(self, event_type: str, handler: Callable[[dict[str, Any]], None]) -> None:
        """Subscribes to the events of a type: ``handler`` gets each event, in the order Home Assistant fired them."""
        await self._send({"type": "subscribe_events", "event_type": event_type}, handler)

    async def close(self) -> None:
        await self._connection.close()
        await self._reader

    async def _send(self, message: dict[str, Any], handler: Callable[[dict[str, Any]], None] | None = None) -> Any:
        message_id = next(self._ids)
        result = asyncio.get_running_loop().create_future()
        self._results[message_id] = result
        if handler is not None:
            # Before the command goes out: the events can come right after the result.
            self._handlers[message_id] = handler
        await self._connection.send_json({"id": message_id, **message})
        return await result

    async def _read(self) -> None:
        try:
            async for message in self._connection:
                if message.type is aiohttp.WSMsgType.TEXT:
                    data = json.loads(message.data)
                    for item in data if isinstance(data, list) else [data]:
                        self._dispatch(item)
        finally:
            for result in self._results.values():
                if not result.done():
                    result.set_exception(ConnectionError("Home Assistant closed the WebSocket connection."))
            self._results.clear()

    def _dispatch(self, item: dict[str, Any]) -> None:
        if item.get("type") == "result":
            result = self._results.pop(item["id"], None)
            if result is None or result.done():
                return
            if item.get("success"):
                result.set_result(item.get("result"))
            else:
                result.set_exception(StepError(f"The WebSocket command failed: {item.get('error')}"))
        elif item.get("type") == "event" and (handler := self._handlers.get(item["id"])) is not None:
            handler(item["event"])


async def _expect(connection: aiohttp.ClientWebSocketResponse, message_type: str) -> None:
    message = await connection.receive_json()
    if message.get("type") != message_type:
        raise StepError(f"The WebSocket API answered {message} instead of {message_type}.")


class HomeAssistant:
    """One Home Assistant: its configuration folder and port, its console's log, and the process that runs it."""

    def __init__(self, root: Path, config: Path, port: int, log: Path, session: aiohttp.ClientSession) -> None:
        self.root = root
        self.config = config
        self.url = f"http://127.0.0.1:{port}"
        self.log = log
        self.starts = 0
        self._session = session
        self._process: asyncio.subprocess.Process | None = None
        self._token: str | None = None

    async def start(self) -> None:
        """Starts it from the repository root, as ``scripts/develop hass`` does, so that it loads this checkout's
        ``custom_components/sonoff2hue``. Its own process group keeps a Ctrl+C in the terminal from reaching it, so that
        the test stops it when it is ready to."""
        self.starts += 1
        with self.log.open("a", encoding="utf-8") as log:
            log.write(f"--- Home Assistant's start {self.starts}, by the end-to-end test\n")
            log.flush()
            self._process = await asyncio.create_subprocess_exec(
                self.root / PYTHON,
                "-m",
                "homeassistant",
                "--config",
                self.config,
                "--skip-pip-packages",
                SKIP_PIP_PACKAGES,
                cwd=self.root,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

    @property
    def exit_code(self) -> int | None:
        return None if self._process is None else self._process.returncode

    def _check_running(self) -> None:
        if self._process is not None and self._process.returncode is not None:
            raise StepError(f"Home Assistant exited with code {self._process.returncode}; see its log, {self.log}.")

    async def wait_until_answering(self) -> None:
        """Waits until its HTTP server answers, which happens before it has finished starting: the onboarding API,
        which needs no sign-in."""
        while True:
            self._check_running()
            with contextlib.suppress(aiohttp.ClientConnectionError):
                async with self._session.get(f"{self.url}/api/onboarding") as response:
                    if response.status == 200:
                        return
            await asyncio.sleep(POLL_INTERVAL)

    async def onboard(self) -> None:
        """Takes onboarding's first step as its frontend does: a throwaway owner, whose password nothing keeps, and the
        tokens of its sign-in. The REST and WebSocket APIs need nothing more. Onboarding's next step would set up the
        integrations it suggests, such as ``met`` and ``radio_browser``, which need the network and packages of their
        own."""
        client_id = f"{self.url}/"
        status, answer = await self.request(
            "POST",
            "/api/onboarding/users",
            {
                "client_id": client_id,
                "name": "End-to-end test",
                "username": "e2e",
                "password": secrets.token_urlsafe(24),
                "language": "en",
            },
        )
        if status != 200 or not isinstance(answer, dict) or "auth_code" not in answer:
            raise StepError(f"Onboarding's user step answered {status}: {answer}")
        form = {"grant_type": "authorization_code", "code": answer["auth_code"], "client_id": client_id}
        async with self._session.post(f"{self.url}/auth/token", data=form) as response:
            tokens = await response.json()
            if response.status != 200 or "access_token" not in tokens:
                raise StepError(f"The sign-in answered {response.status}: {tokens}")
        self._token = tokens["access_token"]

    async def wait_until_running(self) -> None:
        """Waits until it has finished starting, its core state ``RUNNING``: stopping it earlier would abandon the start
        without saving."""
        while True:
            self._check_running()
            with contextlib.suppress(aiohttp.ClientConnectionError):
                status, answer = await self.request("GET", "/api/core/state")
                if status == 200 and isinstance(answer, dict) and answer.get("state") == "RUNNING":
                    return
            await asyncio.sleep(POLL_INTERVAL)

    async def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """A request to its REST API, signed in once onboarded: the answer's status, and its JSON, else its text."""
        headers = {} if self._token is None else {"Authorization": f"Bearer {self._token}"}
        async with self._session.request(method, self.url + path, json=body, headers=headers) as response:
            text = await response.text()
            status = response.status
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    async def websocket(self) -> WebSocket:
        if self._token is None:
            raise StepError("Home Assistant isn't onboarded yet.")
        return await WebSocket.connect(self._session, self.url, self._token)

    async def error_log(self) -> str:
        """Its log since it last started, as its API serves it (``home-assistant.log``)."""
        status, answer = await self.request("GET", "/api/error_log")
        if status != 200:
            raise StepError(f"GET /api/error_log answered {status}: {answer}")
        return str(answer)

    async def diagnostics(self, entry_id: str) -> dict[str, Any] | None:
        """A config entry's diagnostics, as its download serves them: the integration's own under ``data``, next to
        Home Assistant's system information and the domain's repair issues. ``None`` while it has none for the entry:
        when an integration is first loaded, Home Assistant imports its diagnostics platform in the background, and
        answers 404 until then."""
        path = f"/api/diagnostics/config_entry/{entry_id}"
        status, answer = await self.request("GET", path)
        if status == 404:
            return None
        if status != 200 or not isinstance(answer, dict):
            raise StepError(f"GET {path} answered {status}: {answer}")
        return answer

    async def wait_for_log(self, *texts: str, times: int = 1) -> None:
        """Waits until its log since it last started has each of ``texts``, each at least ``times`` times."""
        while True:
            self._check_running()
            log = await self.error_log()
            if all(log.count(text) >= times for text in texts):
                return
            await asyncio.sleep(POLL_INTERVAL)

    async def restart(self, websocket: WebSocket) -> None:
        """Restarts it as its UI does, with the ``homeassistant.restart`` action: it stops, saving its state, and exits
        with code 100, and is then started again, as ``scripts/develop`` does."""
        process = self._process
        if process is None:
            raise StepError("Home Assistant isn't running.")
        # The connection can close before the result comes, as Home Assistant stops.
        with contextlib.suppress(ConnectionError):
            await websocket.command({"type": "call_service", "domain": "homeassistant", "service": "restart"})
        code = await process.wait()
        if code != RESTART_EXIT_CODE:
            raise StepError(f"Home Assistant exited with code {code} instead of {RESTART_EXIT_CODE}, for a restart.")
        await self.start()

    async def stop(self) -> int | None:
        """Stops it as Ctrl+C does, so that it saves its state, and kills it if it hasn't stopped within a minute.
        Returns its exit code: 0 after a clean stop."""
        process = self._process
        if process is None:
            return None
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGINT)
            try:
                async with asyncio.timeout(STOP_TIMEOUT):
                    await process.wait()
            except TimeoutError:
                print(f"Home Assistant didn't stop within {STOP_TIMEOUT:g} s; killing it.")
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        return process.returncode
