"""The sensors (decision 25): Status and eWeLink devices seen on the service device, and each switch's Last action,
with the LAN on a fake network and the lights in Home Assistant's state machine."""

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import ATTR_OPTIONS, SensorDeviceClass
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.sonoff2hue import runtime
from custom_components.sonoff2hue.engine.config import EngineConfig
from custom_components.sonoff2hue.engine.runner import NO_TARGETS, SwitchBinding
from custom_components.sonoff2hue.engine.status import PRESS_DROPPED
from custom_components.sonoff2hue.engine.supervisor import NO_SWITCHES

from .fakes import CEILING, OTHER_DEVICE_ID, Lan, Lights, entry, switch
from .platforms import DEVICES_SEEN, LAST_ACTION, RUNNING, STATUS, settle, start, state_of, tick

HALL = "Hall (1000abcdef)"
DESK = "light.desk"
REMOVED = "0123456789abcdef0123456789abcdef"
"""A light's entity registry id that the registry doesn't have."""


def disable(hass: HomeAssistant, *entity_ids: str) -> None:
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)


# Status.


@pytest.mark.parametrize(
    ("switches", "state", "message", "problems"),
    [
        ([switch()], "running", RUNNING, []),
        (
            [switch(targets=[CEILING, REMOVED])],
            "running",
            "1 wall switch toggles 1 light; 1 is missing.",
            [f"{HALL}: a light was removed from Home Assistant."],
        ),
        (
            [switch(targets=[" "])],
            "invalid_config",
            "The configuration has errors.",
            ["switches[0] ('Hall'): empty light."],
        ),
        ([], "not_configured", NO_SWITCHES, []),
    ],
    ids=["running", "running with a missing light", "invalid config", "no switch"],
)
@pytest.mark.usefixtures("lan", "lights")
async def test_status_shows_the_service(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    switches: list[ConfigSubentryData],
    state: str,
    message: str,
    problems: list[str],
) -> None:
    started = dt_util.utcnow()

    await start(hass, entry(*switches))
    await tick(freezer, 0.5)

    status = state_of(hass, STATUS)
    assert status.state == state
    assert status.attributes["message"] == message
    assert status.attributes["problems"] == problems
    assert status.attributes["since"] == started
    assert status.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.ENUM
    assert status.attributes[ATTR_OPTIONS] == ["not_configured", "invalid_config", "running", "error"]


@pytest.mark.usefixtures("lan", "lights")
async def test_status_shows_an_unexpected_failure(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve(_: HomeAssistant, config: EngineConfig) -> list[SwitchBinding]:
        raise RuntimeError("the lights can't be looked up")

    monkeypatch.setattr(runtime, "resolve", resolve)

    await start(hass, entry(switch()))
    await tick(freezer, 0.5)

    status = state_of(hass, STATUS)
    assert (status.state, status.attributes["message"], status.attributes["problems"]) == (
        "error",
        "The switches stopped unexpectedly: the lights can't be looked up",
        [],
    )


async def test_status_moves_since_with_each_change(
    hass: HomeAssistant, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    lights.add("ceiling")
    started = dt_util.utcnow()
    await start(hass, entry(switch()))
    await tick(freezer, 0.5)

    assert (state_of(hass, STATUS).state, state_of(hass, STATUS).attributes["since"]) == ("running", started)

    # The switch's light disabled: it has nothing left to toggle.
    await tick(freezer, 1)
    disabled = dt_util.utcnow()
    disable(hass, CEILING)
    await settle()
    await tick(freezer, 0.5)

    status = state_of(hass, STATUS)
    assert (status.state, status.attributes["message"], status.attributes["problems"]) == (
        "running",
        "1 wall switch toggles 0 lights; 1 is missing.",
        [f"{HALL}: {CEILING} is disabled."],
    )
    assert status.attributes["since"] == disabled

    # Another light, which the switches resolve again for, changes nothing: since stays.
    await tick(freezer, 1)
    lights.add("desk")
    await settle()
    await tick(freezer, 0.5)

    assert state_of(hass, STATUS).attributes["since"] == disabled

    await tick(freezer, 1)
    enabled = dt_util.utcnow()
    er.async_get(hass).async_update_entity(CEILING, disabled_by=None)
    await settle()
    await tick(freezer, 0.5)

    status = state_of(hass, STATUS)
    assert (status.state, status.attributes["message"], status.attributes["problems"]) == ("running", RUNNING, [])
    assert status.attributes["since"] == enabled


# eWeLink devices seen.


async def test_ewelink_devices_seen_counts_every_device_at_once(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    data = await start(hass, entry(switch()))

    seen = state_of(hass, DEVICES_SEEN)
    assert (seen.state, seen.attributes["listening"], seen.attributes["last_packet_at"]) == ("0", True, None)
    assert seen.attributes[ATTR_UNIT_OF_MEASUREMENT] == "devices"
    registry_entry = er.async_get(hass).async_get(DEVICES_SEEN)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC

    # A device that no switch maps, which the status tracker doesn't publish, counts at once.
    heard = dt_util.utcnow()
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()

    seen = state_of(hass, DEVICES_SEEN)
    assert (seen.state, seen.attributes["last_packet_at"]) == ("1", heard)

    lan.broadcast(1)
    await settle()

    assert state_of(hass, DEVICES_SEEN).state == "2"

    # The listener stopped: the status tracker publishes it.
    data.registry.listening = False
    await tick(freezer, 0.5)

    assert state_of(hass, DEVICES_SEEN).attributes["listening"] is False


async def test_ewelink_devices_seen_shows_the_last_packet_every_30_seconds(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, entry(switch()))
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()
    first = dt_util.utcnow()

    # A repeat changes nothing that the registry reports, but it is the last packet.
    await tick(freezer, 10)
    repeat = dt_util.utcnow()
    lan.broadcast(1, OTHER_DEVICE_ID)
    await settle()

    assert state_of(hass, DEVICES_SEEN).attributes["last_packet_at"] == first

    await tick(freezer, 20)

    assert state_of(hass, DEVICES_SEEN).attributes["last_packet_at"] == repeat
    assert state_of(hass, DEVICES_SEEN).state == "1"


# Last action.


def last_action(hass: HomeAssistant) -> tuple[str, dict[str, object]]:
    state = state_of(hass, LAST_ACTION)
    attributes = {key: state.attributes[key] for key in ("changed", "unchanged", "failed", "targets", "message")}
    return state.state, attributes


async def test_last_action_shows_each_toggle_and_a_dropped_press(
    hass: HomeAssistant, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    lights.add("ceiling")
    await start(hass, entry(switch()))
    await tick(freezer, 0.5)
    targets = [CEILING]

    action = state_of(hass, LAST_ACTION)
    assert action.state == STATE_UNKNOWN
    assert action.attributes[ATTR_OPTIONS] == ["turned_on", "turned_off", "failed", "dropped"]
    assert {key: action.attributes[key] for key in ("at", "elapsed_ms")} == {"at": None, "elapsed_ms": None}
    assert last_action(hass)[1] == {
        "changed": None,
        "unchanged": None,
        "failed": None,
        "targets": targets,
        "message": None,
    }

    # Turned on.
    lan.broadcast(1)
    pressed = dt_util.utcnow()
    lan.broadcast(2)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )
    assert state_of(hass, LAST_ACTION).attributes["at"] == pressed
    assert state_of(hass, LAST_ACTION).attributes["elapsed_ms"] == 0

    # Turned off.
    lan.broadcast(3)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_off",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )

    # The light doesn't answer: the toggle for seq 4 gives up on it after 5 seconds, while seq 5 and 6, a second
    # later, wait for their turn.
    held = lights.hold(CEILING)
    lan.broadcast(4)
    await settle()
    await tick(freezer, 1)
    lan.broadcast(5)
    lan.broadcast(6)
    await settle()
    await tick(freezer, 4)
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {
            "changed": 0,
            "unchanged": 0,
            "failed": 1,
            "targets": targets,
            "message": "1 of 1 lights could not be switched.",
        },
    )
    assert state_of(hass, LAST_ACTION).attributes["elapsed_ms"] == 5000

    # The light answers again as seq 5 turns it on, when seq 6 has waited more than 5 seconds: it is dropped.
    await tick(freezer, 0.5)
    held.set()
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "dropped",
        {"changed": None, "unchanged": None, "failed": None, "targets": targets, "message": PRESS_DROPPED},
    )
    assert lights.is_on(CEILING)

    # So seq 7 turns it off; the older error isn't this toggle's.
    await tick(freezer, 1)
    lan.broadcast(7)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_off",
        {"changed": 1, "unchanged": 0, "failed": 0, "targets": targets, "message": None},
    )


async def test_last_action_shows_the_lights_a_toggle_could_not_switch_until_a_later_error(
    hass: HomeAssistant, lan: Lan, lights: Lights, freezer: FrozenDateTimeFactory
) -> None:
    """The status tracker records the toggle and its error at the same time; the toggle shows, with the error. An error
    that came later is the newer."""
    lights.add("ceiling")
    lights.add("desk")
    lights.fail(DESK)
    await start(hass, entry(switch(targets=[CEILING, DESK])))

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "turned_on",
        {
            "changed": 1,
            "unchanged": 0,
            "failed": 1,
            "targets": [CEILING, DESK],
            "message": "1 of 2 lights could not be switched.",
        },
    )

    # Both lights disabled: the next press has nothing to toggle, and fails.
    await tick(freezer, 1)
    disable(hass, CEILING, DESK)
    await settle()
    failed = dt_util.utcnow()
    lan.broadcast(3)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "failed",
        {"changed": None, "unchanged": None, "failed": None, "targets": [], "message": NO_TARGETS},
    )
    assert state_of(hass, LAST_ACTION).attributes["at"] == failed


@pytest.mark.usefixtures("lan", "lights")
async def test_last_action_has_no_targets_while_the_switches_do_not_run(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass, entry(switch(targets=[" "])))
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        STATE_UNKNOWN,
        {"changed": None, "unchanged": None, "failed": None, "targets": None, "message": None},
    )


@pytest.mark.usefixtures("lights")
async def test_last_action_fails_with_the_reason_for_a_switch_with_nothing_left_to_toggle(
    hass: HomeAssistant, lan: Lan, freezer: FrozenDateTimeFactory
) -> None:
    """None of its lights is in Home Assistant (decision 32): each press fails, and says why."""
    await start(hass, entry(switch(targets=[REMOVED])))
    pressed = dt_util.utcnow()

    lan.broadcast(1)
    lan.broadcast(2)
    await settle()
    await tick(freezer, 0.5)

    assert last_action(hass) == (
        "failed",
        {"changed": None, "unchanged": None, "failed": None, "targets": [], "message": NO_TARGETS},
    )
    assert state_of(hass, LAST_ACTION).attributes["at"] == pressed
    assert state_of(hass, LAST_ACTION).attributes["elapsed_ms"] is None
