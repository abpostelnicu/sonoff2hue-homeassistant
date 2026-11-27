"""The config entry's diagnostics (decision 26): the configuration, the status, the LAN's devices and the activity,
without the device keys, also in the file that Home Assistant serves."""

import base64
from datetime import UTC, datetime, timedelta
import hashlib
from http import HTTPStatus
import json
import logging

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.sonoff2hue import diagnostics
from custom_components.sonoff2hue.const import CONF_DEVICE_KEY
from custom_components.sonoff2hue.runtime import INTEGRATION_LOGGER

from .fakes import (
    CEILING,
    DEVICE_ADDRESS,
    DEVICE_ID,
    DEVICE_KEY,
    OTHER_DEVICE_ID,
    Lan,
    Lights,
    entry,
    relay,
    switch,
)
from .platforms import settle, start, tick

REDACTED = "**REDACTED**"
UNCONFIGURED_DEVICE_ID = "1000fedcba"
"""A made-up MINIR4M that no switch maps."""
DESK = "light.desk"
REMOVED = "0123456789abcdef0123456789abcdef"
"""A light's entity registry id that the registry doesn't have."""


async def download(hass: HomeAssistant, hass_client: ClientSessionGenerator, entry_id: str) -> str:
    """The diagnostics file as Home Assistant's frontend downloads it, with what Home Assistant adds, such as the repair
    issues."""
    assert await async_setup_component(hass, "diagnostics", {})
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{entry_id}")
    assert response.status == HTTPStatus.OK
    return await response.text()


async def test_the_diagnostics_hold_the_configuration_the_status_the_lan_and_the_activity(
    hass: HomeAssistant, lan: Lan, lights: Lights, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    """In plain JSON, which the downloaded file holds as it is. The configuration keeps the lights as stored, and the
    status has their entity ids. The platform is called directly: Home Assistant's HTTP API refuses the test client's
    token once the clock is moved past its 30 minutes, and while the clock is frozen, aiohttp's access log fails."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    started = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
    freezer.move_to(started)
    lights.add("ceiling")
    lights.add("desk")
    ceiling = lights.target(CEILING)
    config_entry = entry(
        switch(key=DEVICE_KEY, targets=[ceiling]), switch(OTHER_DEVICE_ID, None, targets=[DESK, REMOVED])
    )
    await start(hass, config_entry)
    lan.broadcast(1, params=relay(True), key=DEVICE_KEY)
    lan.broadcast(1, OTHER_DEVICE_ID)
    lan.broadcast(7, UNCONFIGURED_DEVICE_ID)
    await settle()
    # The unmapped MINIR4M stays silent for more than the 5 minutes that a device counts as online.
    await tick(freezer, 6 * 60)
    pressed = started + timedelta(minutes=6, microseconds=1)
    lights.fail(DESK)
    lan.broadcast(2, params=relay(True), key=DEVICE_KEY)
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()

    result = await diagnostics.async_get_config_entry_diagnostics(hass, config_entry)

    assert json.loads(json.dumps(result)) == result
    at, then = started.isoformat(), pressed.isoformat()
    missing = "1000999999: a light was removed from Home Assistant."
    assert result == {
        "config": {
            "switches": [
                {"device_id": DEVICE_ID, "name": "Hall", "device_key": REDACTED, "targets": [ceiling]},
                {"device_id": OTHER_DEVICE_ID, "name": None, "device_key": None, "targets": [DESK, REMOVED]},
            ],
        },
        "status": {
            "started_at": at,
            "service": {
                "state": "running",
                "message": "2 wall switches toggle 2 lights; 1 is missing.",
                "problems": [missing],
                "since": at,
            },
            "lan": {"listening": True, "devices_seen": 3, "last_packet_at": then},
            "switches": [
                {
                    "name": "Hall",
                    "device_id": DEVICE_ID,
                    "online": True,
                    "address": DEVICE_ADDRESS,
                    "last_seen_at": then,
                    "last_press_at": then,
                    "detached": True,
                    "decode_error": None,
                    "targets": [CEILING],
                    "problems": [],
                    "last_action": {
                        "at": then,
                        "turned_on": True,
                        "changed": 1,
                        "unchanged": 0,
                        "failed": 0,
                        "elapsed_ms": 0,
                    },
                    "last_error": None,
                },
                {
                    "name": None,
                    "device_id": OTHER_DEVICE_ID,
                    "online": True,
                    "address": DEVICE_ADDRESS,
                    "last_seen_at": then,
                    "last_press_at": then,
                    "detached": None,
                    "decode_error": None,
                    "targets": [DESK],
                    "problems": [missing],
                    "last_action": {
                        "at": then,
                        "turned_on": True,
                        "changed": 0,
                        "unchanged": 0,
                        "failed": 1,
                        "elapsed_ms": 0,
                    },
                    "last_error": {"at": then, "message": "1 of 1 lights could not be switched."},
                },
            ],
        },
        "lan_devices": [
            {
                "device_id": OTHER_DEVICE_ID,
                "address": DEVICE_ADDRESS,
                "device_type": "plug",
                "encrypted": True,
                "seq": 2,
                "first_seen_at": at,
                "last_seen_at": then,
                "last_event": "Press",
                "last_event_at": then,
                "last_press_at": then,
                "detached": None,
                "summary": None,
                "decode_error": None,
                "online": True,
            },
            {
                "device_id": DEVICE_ID,
                "address": DEVICE_ADDRESS,
                "device_type": "plug",
                "encrypted": True,
                "seq": 2,
                "first_seen_at": at,
                "last_seen_at": then,
                "last_event": "Press",
                "last_event_at": then,
                "last_press_at": then,
                "detached": True,
                "summary": "relay0=off relaySeparation=1",
                "decode_error": None,
                "online": True,
            },
            {
                "device_id": UNCONFIGURED_DEVICE_ID,
                "address": DEVICE_ADDRESS,
                "device_type": "plug",
                "encrypted": True,
                "seq": 7,
                "first_seen_at": at,
                "last_seen_at": at,
                "last_event": "Baseline",
                "last_event_at": at,
                "last_press_at": None,
                "detached": None,
                "summary": None,
                "decode_error": None,
                "online": False,
            },
        ],
        "activity": [
            {"id": entry_id, "at": when, "level": level, "category": category, "message": message, "exception": None}
            for entry_id, (when, level, category, message) in enumerate(
                [
                    (at, "warning", "supervisor", missing),
                    (at, "information", "runner", f"Hall (1000abcdef) -> {CEILING}"),
                    (at, "information", "runner", f"1000999999 -> {DESK}"),
                    (
                        at,
                        "information",
                        "runner",
                        "1000999999: no deviceKey configured, so every new state broadcast counts as a press "
                        "(relay/setting changes cannot be filtered).",
                    ),
                    (at, "information", "runner", "Listening for wall switch presses."),
                    (
                        at,
                        "information",
                        "runner",
                        "Hall (1000abcdef): online at 192.0.2.5 (seq 1; relay0=off relaySeparation=1).",
                    ),
                    (at, "information", "runner", "1000999999: online at 192.0.2.5 (seq 1; payload not decoded)."),
                    (then, "information", "runner", "Hall (1000abcdef): wall switch operated (seq 2)."),
                    (then, "information", "runner", "1000999999: wall switch operated (seq 2)."),
                    (then, "information", "runner", f"Hall (1000abcdef): turned ON {CEILING} in 0 ms."),
                    (then, "error", "runner", f"Could not switch {DESK} on: Request failed: the light didn't answer"),
                    (then, "information", "runner", f"1000999999: turned ON {DESK} in 0 ms (1 failed)."),
                ],
                start=1,
            )
        ],
    }


@pytest.mark.usefixtures("lights")
async def test_the_activity_is_the_last_1000_entries_as_the_buffer_has_them(
    hass: HomeAssistant, lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """Oldest first, with an exception's type and message."""
    caplog.set_level(logging.WARNING, logger=INTEGRATION_LOGGER.name)
    config_entry = entry()
    runtime_data = await start(hass, config_entry)
    for number in range(1, 1006):
        INTEGRATION_LOGGER.warning("Line %d.", number)
    INTEGRATION_LOGGER.error("Line with an exception.", exc_info=ValueError("Not a number."))

    result = await diagnostics.async_get_config_entry_diagnostics(hass, config_entry)

    activity = result["activity"]
    assert [line["message"] for line in activity] == [f"Line {number}." for number in range(7, 1006)] + [
        "Line with an exception."
    ]
    assert [line["id"] for line in activity] == list(range(7, 1007))
    (last,) = runtime_data.activity.get(0, 1)
    assert activity[-1] == {
        "id": last.id,
        "at": last.at.isoformat(),
        "level": "error",
        "category": "sonoff2hue",
        "message": "Line with an exception.",
        "exception": "ValueError: Not a number.",
    }


@pytest.mark.usefixtures("lan", "lights")
async def test_an_entry_that_is_not_loaded_has_only_its_configuration(hass: HomeAssistant) -> None:
    config_entry = entry(switch(key=DEVICE_KEY))
    await start(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED

    assert await diagnostics.async_get_config_entry_diagnostics(hass, config_entry) == {
        "config": {
            "switches": [{"device_id": DEVICE_ID, "name": "Hall", "device_key": REDACTED, "targets": [CEILING]}],
        },
        "status": None,
        "lan_devices": None,
        "activity": None,
    }


async def test_no_device_key_reaches_the_diagnostics_or_the_log(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    lan: Lan,
    lights: Lights,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Through the messages that name a switch or a failure: the switches' broadcasts decrypted with their keys, a
    payload that a key can't decrypt, a switch not in detached relay mode, toggles, a light that isn't switched, the
    switch test and a key replaced. Neither the files downloaded along the way, through Home Assistant's HTTP API, nor
    any line that the integration logs, at any level, holds a key or the AES key made of it."""
    caplog.set_level(1, logger=INTEGRATION_LOGGER.name)
    device_key = "8f14e45f-ceea-467a-9575-b3a5d4ce5ec7"
    other_device_key = "45c48cce-2e2d-4fbd-8aa0-4c1b5c0ffee5"
    new_device_key = "c9f0f895-fb98-4b91-99f5-1a5b2c0ffee6"
    lights.add("ceiling")
    lights.add("desk")
    config_entry = entry(
        switch(key=device_key), switch(OTHER_DEVICE_ID, "Desk", targets=[DESK], key=f" {other_device_key} ")
    )
    runtime_data = await start(hass, config_entry)
    started = await download(hass, hass_client, config_entry.entry_id)

    lan.broadcast(1, params=relay(True), key=device_key)
    lan.broadcast(2, params=relay(True), key=device_key)
    # Desk's payload under Hall's key, which Desk's key fails to decrypt: under the fake LAN's fixed IV, in every run.
    lan.broadcast(1, OTHER_DEVICE_ID, params=relay(True), key=device_key)
    lan.broadcast(2, OTHER_DEVICE_ID, params=relay(False), key=other_device_key)
    lights.fail(DESK)
    lan.broadcast(3, OTHER_DEVICE_ID, params=relay(False), key=other_device_key)
    await settle()
    await runtime_data.async_test_toggle(DEVICE_ID)
    running = await download(hass, hass_client, config_entry.entry_id)

    desk = next(subentry for subentry in config_entry.subentries.values() if subentry.unique_id == OTHER_DEVICE_ID)
    hass.config_entries.async_update_subentry(config_entry, desk, data={**desk.data, CONF_DEVICE_KEY: new_device_key})
    await settle()
    lan.broadcast(4, OTHER_DEVICE_ID, params=relay(True), key=new_device_key)
    await settle()
    replaced = await download(hass, hass_client, config_entry.entry_id)

    assert "Hall (1000abcdef): turned ON light.ceiling in" in running
    assert "Desk (1000999999): cannot read the LAN payload (decryption failed, check the deviceKey)" in running
    assert "Desk (1000999999) is not in detached relay mode" in running
    assert f"Could not switch {DESK} on: Request failed: the light didn't answer" in running
    assert "Hall (1000abcdef): test from Home Assistant turned OFF light.ceiling." in running
    assert "Configuration changed; restarting the switches." in replaced
    # The keys are there, redacted.
    assert [text.count(REDACTED) for text in (started, running, replaced)] == [2, 2, 2]
    keys = [device_key, other_device_key, new_device_key]
    # The AES key of each switch's payloads, which the engine makes of its device key.
    aes_keys = [hashlib.md5(key.encode(), usedforsecurity=False).digest() for key in keys]
    secrets = [
        *keys,
        *(aes_key.hex() for aes_key in aes_keys),
        *(repr(aes_key) for aes_key in aes_keys),
        *(base64.b64encode(aes_key).decode() for aes_key in aes_keys),
    ]
    formatter = logging.Formatter()
    logged = [formatter.format(record) for record in caplog.records if record.name.startswith(INTEGRATION_LOGGER.name)]
    for secret in secrets:
        for text in (started, running, replaced, *logged):
            assert secret not in text
