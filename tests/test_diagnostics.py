"""The config entry's diagnostics (decision 26): the configuration, the status, the LAN's devices and the activity,
without the application key, the certificate pin or the device keys, also in the file that Home Assistant serves."""

from datetime import UTC, datetime, timedelta
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
from custom_components.sonoff2hue.const import CONF_APP_KEY, CONF_CERTIFICATE_SHA256
from custom_components.sonoff2hue.engine import toggler
from custom_components.sonoff2hue.engine.hue_client import CERTIFICATE_MISMATCH
from custom_components.sonoff2hue.engine.status import ServiceState
from custom_components.sonoff2hue.runtime import INTEGRATION_LOGGER
from tools.fake_hue.bridge import BRIDGE_ID as FAKE_BRIDGE_ID

from .engine.fakes import until
from .fakes import (
    BRIDGE_ADDRESS,
    BRIDGE_ID,
    DEVICE_ID,
    DEVICE_KEY,
    OTHER_DEVICE_ID,
    Bridge,
    Lan,
    paired,
    relay,
    switch,
)
from .hue import FakeHueHost
from .test_runtime import issues, reauth_flows, settle, start, tick

REDACTED = "**REDACTED**"
UNCONFIGURED_DEVICE_ID = "1000fedcba"
"""A made-up MINIR4M that no switch maps."""
LIGHTS_DOWN = "Hue bridge returned 503 Service Unavailable for GET /clip/v2/resource/light"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The toggler retries a failed request at once, instead of after 300 ms."""
    monkeypatch.setattr(toggler, "RETRY_DELAY", 0)


async def download(hass: HomeAssistant, hass_client: ClientSessionGenerator, entry_id: str) -> str:
    """The diagnostics file as Home Assistant's frontend downloads it, with what Home Assistant adds, such as the repair
    issues."""
    assert await async_setup_component(hass, "diagnostics", {})
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{entry_id}")
    assert response.status == HTTPStatus.OK
    return await response.text()


async def test_the_diagnostics_hold_the_configuration_the_status_the_lan_and_the_activity(
    hass: HomeAssistant, lan: Lan, bridge: Bridge, caplog: pytest.LogCaptureFixture, freezer: FrozenDateTimeFactory
) -> None:
    """In plain JSON, which the downloaded file holds as it is. The platform is called directly: Home Assistant's HTTP
    API refuses the test client's token once the clock is moved past its 30 minutes, and while the clock is frozen,
    aiohttp's access log fails."""
    caplog.set_level(logging.INFO, logger=INTEGRATION_LOGGER.name)
    started = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
    freezer.move_to(started)
    entry = paired(switch(key=DEVICE_KEY), switch(OTHER_DEVICE_ID, None, lights=["Desk"]))
    await start(hass, entry)
    lan.broadcast(1, params=relay(True), key=DEVICE_KEY)
    lan.broadcast(1, OTHER_DEVICE_ID)
    lan.broadcast(7, UNCONFIGURED_DEVICE_ID)
    await settle()
    # The unmapped MINIR4M stays silent for more than the 5 minutes that a device counts as online.
    await tick(freezer, 6 * 60)
    pressed = started + timedelta(minutes=6, microseconds=1)
    lan.broadcast(2, params=relay(True), key=DEVICE_KEY)
    bridge.fail("/clip/v2/resource/light", 503)
    lan.broadcast(2, OTHER_DEVICE_ID)
    await settle()

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert json.loads(json.dumps(result)) == result
    at, then = started.isoformat(), pressed.isoformat()
    assert result == {
        "config": {
            "hue": {
                "bridge_ip": BRIDGE_ADDRESS,
                "bridge_id": BRIDGE_ID,
                "app_key": REDACTED,
                "certificate_sha256": REDACTED,
            },
            "switches": [
                {"device_id": DEVICE_ID, "name": "Hall", "device_key": REDACTED, "lights": ["Ceiling"], "groups": []},
                {"device_id": OTHER_DEVICE_ID, "name": None, "device_key": None, "lights": ["Desk"], "groups": []},
            ],
        },
        "status": {
            "started_at": at,
            "service": {
                "state": "running",
                "message": "2 wall switches toggle 2 Hue lights, rooms and zones.",
                "problems": [],
                "since": at,
            },
            "hue": {
                "paired": True,
                "bridge_ip": BRIDGE_ADDRESS,
                "bridge_id": BRIDGE_ID,
                "certificate_pinned": True,
                "reachable": False,
                "message": LIGHTS_DOWN,
                "checked_at": then,
            },
            "lan": {"listening": True, "devices_seen": 3, "last_packet_at": then},
            "switches": [
                {
                    "name": "Hall",
                    "device_id": DEVICE_ID,
                    "online": True,
                    "address": "192.0.2.5",
                    "last_seen_at": then,
                    "last_press_at": then,
                    "detached": True,
                    "decode_error": None,
                    "targets": ["light 'Ceiling'"],
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
                    "address": "192.0.2.5",
                    "last_seen_at": then,
                    "last_press_at": then,
                    "detached": None,
                    "decode_error": None,
                    "targets": ["light 'Desk'"],
                    "last_action": None,
                    "last_error": {"at": then, "message": LIGHTS_DOWN},
                },
            ],
        },
        "lan_devices": [
            {
                "device_id": OTHER_DEVICE_ID,
                "address": "192.0.2.5",
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
                "address": "192.0.2.5",
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
                "address": "192.0.2.5",
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
            {"id": entry_id, "at": when, "level": level, "category": "runner", "message": message, "exception": None}
            for entry_id, (when, level, message) in enumerate(
                [
                    (at, "information", "Hall (1000abcdef) -> light 'Ceiling'"),
                    (at, "information", "1000999999 -> light 'Desk'"),
                    (
                        at,
                        "information",
                        "1000999999: no deviceKey configured, so every new state broadcast counts as a press "
                        "(relay/setting changes cannot be filtered).",
                    ),
                    (at, "information", "Listening for wall switch presses."),
                    (
                        at,
                        "information",
                        "Hall (1000abcdef): online at 192.0.2.5 (seq 1; relay0=off relaySeparation=1).",
                    ),
                    (at, "information", "1000999999: online at 192.0.2.5 (seq 1; payload not decoded)."),
                    (then, "information", "Hall (1000abcdef): wall switch operated (seq 2)."),
                    (then, "information", "Hall (1000abcdef): turned ON light 'Ceiling' in 0 ms."),
                    (then, "information", "1000999999: wall switch operated (seq 2)."),
                    (then, "warning", f"Hue bridge unavailable: {LIGHTS_DOWN}"),
                    (then, "error", f"1000999999: could not toggle the lights: {LIGHTS_DOWN}"),
                ],
                start=1,
            )
        ],
    }


async def test_the_activity_is_the_last_1000_entries_as_the_buffer_has_them(
    hass: HomeAssistant, lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """Oldest first, with an exception's type and message."""
    caplog.set_level(logging.WARNING, logger=INTEGRATION_LOGGER.name)
    entry = paired(switch(), app_key=None)
    runtime_data = await start(hass, entry)
    for number in range(1, 1006):
        INTEGRATION_LOGGER.warning("Line %d.", number)
    INTEGRATION_LOGGER.error("Line with an exception.", exc_info=ValueError("Not a number."))

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

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


@pytest.mark.usefixtures("lan", "bridge")
async def test_an_entry_that_is_not_loaded_has_only_its_configuration(hass: HomeAssistant) -> None:
    entry = paired(switch(key=DEVICE_KEY))
    await start(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED

    assert await diagnostics.async_get_config_entry_diagnostics(hass, entry) == {
        "config": {
            "hue": {
                "bridge_ip": BRIDGE_ADDRESS,
                "bridge_id": BRIDGE_ID,
                "app_key": REDACTED,
                "certificate_sha256": REDACTED,
            },
            "switches": [
                {"device_id": DEVICE_ID, "name": "Hall", "device_key": REDACTED, "lights": ["Ceiling"], "groups": []}
            ],
        },
        "status": None,
        "lan_devices": None,
        "activity": None,
    }


@pytest.mark.usefixtures("socket_enabled")
async def test_no_secret_reaches_the_diagnostics_or_the_log(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    lan: Lan,
    fake_hue_pressed: FakeHueHost,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Over HTTPS with the fake bridge, through the messages that name the bridge, a switch or a failure: a pin that isn't
    the bridge's certificate, a key that the bridge rejects, pairing again, toggles, a payload that the device key can't
    decrypt and the switch test. Neither the files downloaded along the way nor any line that the integration logs, at
    any level, holds a key or a pin."""
    caplog.set_level(1, logger=INTEGRATION_LOGGER.name)
    rejected_key = "Sec7RejectedAppKey0123456789abcdefABCDEF"
    wrong_pin = "C0FFEE" * 10 + "C0FE"
    device_key = "8f14e45f-ceea-467a-9575-b3a5d4ce5ec7"
    other_device_key = "45c48cce-2e2d-4fbd-8aa0-4c1b5c0ffee5"
    entry = paired(
        switch(lights=["Hallway"], key=device_key),
        switch(OTHER_DEVICE_ID, "Desk", lights=["Ceiling"], key=other_device_key),
        address=fake_hue_pressed.address,
        app_key=rejected_key,
        pin=wrong_pin,
        bridge_id=FAKE_BRIDGE_ID,
    )
    runtime_data = await start(hass, entry)
    await until(lambda: "certificate_changed" in issues(hass))
    pin_mismatch = await download(hass, hass_client, entry.entry_id)

    # With the bridge's own pin, the bridge rejects the key.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CERTIFICATE_SHA256: fake_hue_pressed.fingerprint}
    )
    await until(lambda: runtime_data.status.service.state is ServiceState.ERROR)
    await hass.async_block_till_done()
    key_rejected = await download(hass, hass_client, entry.entry_id)

    # Pairing again in the reauthentication gives a new key, and the switches run.
    (flow,) = reauth_flows(hass)
    await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["reason"] == "reauth_successful"
    await until(lambda: runtime_data.status.service.state is ServiceState.RUNNING)
    lan.broadcast(1, params=relay(True), key=device_key)
    lan.broadcast(2, params=relay(True), key=device_key)
    lan.broadcast(1, OTHER_DEVICE_ID, params=relay(True), key=device_key)
    await until(lambda: runtime_data.status.snapshot().switches[0].last_action is not None)
    await runtime_data.async_test_toggle(DEVICE_ID)
    await until(lambda: f"decode_error_{OTHER_DEVICE_ID}" in issues(hass))
    running = await download(hass, hass_client, entry.entry_id)

    assert CERTIFICATE_MISMATCH in pin_mismatch
    assert "The Hue bridge rejected the application key (unauthorized user)" in key_rejected
    assert "Paired with the Hue bridge" in running
    assert "Hall (1000abcdef): test from Home Assistant turned OFF light 'Hallway'." in running
    assert "cannot read the LAN payload (decryption failed, check the deviceKey)" in running
    # The secrets are there, redacted: the key, the pin and the two device keys.
    assert [text.count(REDACTED) for text in (pin_mismatch, key_rejected, running)] == [4, 4, 4]
    pins = [wrong_pin, fake_hue_pressed.fingerprint]
    secrets = [
        rejected_key,
        entry.data[CONF_APP_KEY],
        device_key,
        other_device_key,
        *pins,
        *(pin.lower() for pin in pins),
        *(":".join(pin[i : i + 2] for i in range(0, len(pin), 2)) for pin in pins),
        *(repr(bytes.fromhex(pin)) for pin in pins),
    ]
    formatter = logging.Formatter()
    logged = [formatter.format(record) for record in caplog.records if record.name.startswith(INTEGRATION_LOGGER.name)]
    for secret in secrets:
        for text in (pin_mismatch, key_rejected, running, *logged):
            assert secret not in text
