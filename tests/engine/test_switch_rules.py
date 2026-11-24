"""Tests of the server's switch rules (engine/switch_rules.py), the port of the switch endpoints in the .NET app's
Web/Management/ConfigEndpoints.cs. The first tests are WebConfigTests' cases for switches; the parity tests at the end
replay the switchValidation and switchWrites cases of fixtures/dotnet_engine.json, which tools/dotnet_oracle made by
running the app's own code."""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import hue_client, switch_rules
from custom_components.sonoff2hue.engine.config import ConfigError, HueSettings, SwitchSettings
from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueGroup, HueLight
from custom_components.sonoff2hue.engine.switch_rules import (
    ConnectHue,
    HueResources,
    SwitchConflictError,
    SwitchInput,
    SwitchNotFoundError,
    SwitchValidationError,
)

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dotnet_engine.json").read_text(encoding="utf-8"))
_DEVICE = "1000abcdef"
_OTHER = "1000999999"
PAIRED = HueSettings(
    bridge_ip="192.0.2.20", bridge_id="001788fffe000001", app_key="test-app-key", certificate_sha256="AA"
)
UNPAIRED = HueSettings()

CEILING = HueLight("l1", "Ceiling", True)
FLOOR_LAMP = HueLight("l2", "Floor lamp", False)
HALLWAY = HueGroup("r1", "Hallway", "room", "g1")


class FakeBridge:
    """The lights and groups a bridge answers with, or the error it fails with."""

    def __init__(
        self,
        lights: Sequence[HueLight] = (CEILING, FLOOR_LAMP),
        groups: Sequence[HueGroup] = (HALLWAY,),
        error: Exception | None = None,
    ) -> None:
        self.lights, self.groups, self.error = list(lights), list(groups), error
        self.opened = 0
        self.closed = 0

    async def get_lights(self) -> list[HueLight]:
        if self.error is not None:
            raise self.error
        return self.lights

    async def get_groups(self) -> list[HueGroup]:
        return self.groups

    def connect(self, hue: HueSettings) -> AbstractAsyncContextManager[HueResources]:
        @asynccontextmanager
        async def opened() -> AsyncIterator[HueResources]:
            self.opened += 1
            try:
                yield self
            finally:
                self.closed += 1

        return opened()


async def create(
    switches: list[SwitchSettings],
    switch: SwitchInput,
    hue: HueSettings = PAIRED,
    bridge: FakeBridge | None = None,
) -> switch_rules.SwitchWrite:
    write = await switch_rules.create_switch(lambda: switches, switch, hue, (bridge or FakeBridge()).connect)
    switches.append(write.switch)
    return write


async def update(
    switches: list[SwitchSettings],
    device_id: str,
    switch: SwitchInput,
    hue: HueSettings = PAIRED,
    bridge: FakeBridge | None = None,
) -> switch_rules.SwitchWrite:
    write = await switch_rules.update_switch(lambda: switches, device_id, switch, hue, (bridge or FakeBridge()).connect)
    assert write.index is not None
    switches[write.index] = write.switch
    return write


# WebConfigTests


async def test_create_switch_saves_it_with_its_device_key() -> None:
    switches: list[SwitchSettings] = []

    write = await create(
        switches,
        SwitchInput("Hall", "1000ABCDEF", "secret-device-key", ["Ceiling"], ["room:Hallway"]),
    )

    assert write.warnings == []
    assert switches == [
        SwitchSettings(_DEVICE, "Hall", "secret-device-key", ["Ceiling"], ["room:Hallway"]),
    ]
    assert switch_rules.has_device_key(switches[0])


async def test_create_switch_validates_the_input() -> None:
    switches: list[SwitchSettings] = []

    with pytest.raises(SwitchValidationError) as invalid:
        await create(switches, SwitchInput("x", "not valid!", lights=[]))
    with pytest.raises(SwitchValidationError) as unknown_light:
        await create(switches, SwitchInput(device_id=_DEVICE, lights=["Kitchen"]))

    assert invalid.value.status == 400
    assert list(invalid.value.errors) == ["deviceId", "targets"]
    assert unknown_light.value.errors == {
        "targets": [f"{_DEVICE}: no Hue light named 'Kitchen'. Known lights: 'Ceiling', 'Floor lamp'"]
    }
    assert switches == []


async def test_create_switch_without_a_paired_bridge_is_saved_with_a_warning() -> None:
    switches: list[SwitchSettings] = []
    bridge = FakeBridge()

    write = await create(switches, SwitchInput(device_id=_DEVICE, lights=["Kitchen"]), UNPAIRED, bridge)

    assert write.warnings == ["The Hue bridge is not paired yet, so the light names were not checked."]
    assert len(switches) == 1
    assert bridge.opened == 0


async def test_update_switch_keeps_or_removes_the_device_key() -> None:
    switches: list[SwitchSettings] = []
    await create(switches, SwitchInput(device_id=_DEVICE, device_key="k1", lights=["Ceiling"]))

    await update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, None, ["Floor lamp"]))
    after_keep = switches[0]
    await update(switches, _DEVICE, SwitchInput("Hall", _DEVICE, "", ["Floor lamp"]))

    assert (after_keep.name, after_keep.device_key, after_keep.lights) == ("Hall", "k1", ["Floor lamp"])
    assert switches[0].device_key is None


async def test_an_unusable_bridge_address_is_a_warning() -> None:
    switches = [SwitchSettings(_DEVICE, lights=["Ceiling"])]
    hue = HueSettings(bridge_ip="bad host", app_key="test-app-key")
    bridge = FakeBridge(error=ConfigError("'bad host' is not a valid Hue bridge address."))

    write = await update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, lights=["Ceiling"]), hue, bridge)

    assert write.warnings == [
        "The Hue settings in the config can't be used, so the light names were not checked ('bad host' is not a valid "
        "Hue bridge address.)."
    ]


# The device id, the key and the second check


async def test_a_device_id_is_configured_once() -> None:
    switches = [SwitchSettings(_DEVICE, lights=["Ceiling"]), SwitchSettings(_OTHER, lights=["Ceiling"])]

    with pytest.raises(SwitchConflictError) as again:
        await create(switches, SwitchInput(device_id=" 1000ABCDEF ", lights=["Ceiling"]))
    with pytest.raises(SwitchConflictError) as taken:
        await update(switches, _DEVICE, SwitchInput(device_id=_OTHER, lights=["Ceiling"]))
    with pytest.raises(SwitchNotFoundError) as missing:
        await update(switches, "1000BBBBBB ", SwitchInput(device_id=_DEVICE, lights=["Ceiling"]))

    assert (again.value.status, str(again.value)) == (
        409,
        "Device 1000abcdef is already configured. Edit that switch instead.",
    )
    assert str(taken.value) == "Device 1000999999 is already configured by another switch."
    assert (missing.value.status, str(missing.value)) == (404, "No switch with device id 1000bbbbbb is configured.")


async def test_a_changed_device_id_drops_a_kept_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", lights=["Ceiling"])]

    await update(switches, _DEVICE, SwitchInput(device_id=" 1000ABCDEF", lights=["Ceiling"]))
    assert switches[0].device_key == "k1"

    await update(switches, _DEVICE, SwitchInput(device_id="1000aaaaaa", lights=["Ceiling"]))
    assert switches[0] == SwitchSettings("1000aaaaaa", lights=["Ceiling"])


async def test_a_blank_device_key_removes_the_key() -> None:
    switches = [SwitchSettings(_DEVICE, device_key="k1", lights=["Ceiling"])]

    await update(switches, _DEVICE, SwitchInput(device_id=_DEVICE, device_key=" \u3000", lights=["Ceiling"]))

    assert switches[0].device_key is None


async def test_the_device_id_is_checked_again_after_the_bridge() -> None:
    switches: list[SwitchSettings] = []
    calls: list[int] = []

    def current() -> list[SwitchSettings]:
        # Another flow adds the same device while the bridge is asked.
        calls.append(len(calls))
        return switches if len(calls) == 1 else [SwitchSettings(_DEVICE, lights=["Desk"])]

    with pytest.raises(SwitchConflictError):
        await switch_rules.create_switch(
            current, SwitchInput(device_id=_DEVICE, lights=["Ceiling"]), PAIRED, FakeBridge().connect
        )
    assert calls == [0, 1]


async def test_a_switch_removed_while_the_bridge_is_asked_is_not_found() -> None:
    before = [SwitchSettings(_DEVICE, lights=["Ceiling"])]
    calls: list[int] = []

    def current() -> list[SwitchSettings]:
        calls.append(len(calls))
        return before if len(calls) == 1 else []

    with pytest.raises(SwitchNotFoundError) as err:
        await switch_rules.update_switch(
            current,
            _DEVICE,
            SwitchInput(device_id=_DEVICE, lights=["Ceiling"]),
            PAIRED,
            FakeBridge().connect,
        )
    assert str(err.value) == "No switch with device id 1000abcdef is configured."


async def test_validation_comes_before_the_switch_is_looked_up() -> None:
    with pytest.raises(SwitchValidationError) as err:
        await update([], "1000bbbbbb", SwitchInput(device_id=""))

    assert err.value.errors == {
        "deviceId": ["Choose a device or enter its device id."],
        "targets": ["Choose at least one light, room or zone."],
    }
    assert str(err.value) == "Choose a device or enter its device id. Choose at least one light, room or zone."


# The bridge


async def test_check_targets_resolves_against_a_reachable_bridge() -> None:
    bridge = FakeBridge()
    candidate = SwitchSettings(_DEVICE, "Hall", lights=["Ceiling", "Garage"], groups=["room:Hallway"])

    check = await switch_rules.check_targets(candidate, PAIRED, bridge.connect)

    assert check == switch_rules.TargetCheck(
        ["Hall (1000abcdef): no Hue light named 'Garage'. Known lights: 'Ceiling', 'Floor lamp'"], []
    )
    assert (bridge.opened, bridge.closed) == (1, 1)


async def test_check_targets_can_take_another_resolver() -> None:
    calls: list[tuple[SwitchSettings, list[HueLight], list[HueGroup]]] = []

    def resolve(settings: SwitchSettings, lights: list[HueLight], groups: list[HueGroup], errors: list[str]) -> None:
        calls.append((settings, lights, groups))
        errors.append("refused")

    candidate = SwitchSettings(_DEVICE, lights=["Ceiling"])
    check = await switch_rules.check_targets(candidate, PAIRED, FakeBridge().connect, resolve)

    assert check.errors == ["refused"]
    assert calls == [(candidate, [CEILING, FLOOR_LAMP], [HALLWAY])]


async def test_check_targets_only_warns_when_the_bridge_cant_be_reached() -> None:
    bridge = FakeBridge(error=HueApiError("Hue bridge not reachable: Connection refused (192.0.2.20:443)"))

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), PAIRED, bridge.connect)

    assert check.errors == []
    assert check.warnings == [
        "The Hue bridge could not be reached, so the light names were not checked (Hue bridge not reachable: "
        "Connection refused (192.0.2.20:443))."
    ]
    assert bridge.closed == 1


@pytest.mark.parametrize(
    "hue",
    [
        HueSettings(),
        HueSettings(bridge_ip="192.0.2.20"),
        HueSettings(app_key="test-app-key"),
        HueSettings(bridge_ip=" \u3000", app_key="test-app-key"),
        HueSettings(bridge_ip="192.0.2.20", app_key="\u2028"),
    ],
)
async def test_without_an_address_and_a_key_the_bridge_is_not_paired(hue: HueSettings) -> None:
    bridge = FakeBridge()

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), hue, bridge.connect)

    assert check.warnings == [switch_rules.NOT_PAIRED]
    assert bridge.opened == 0
    assert not switch_rules.is_paired(hue)


async def test_settings_that_cant_be_used_are_a_warning_before_the_bridge_is_asked() -> None:
    def connect(hue: HueSettings) -> AbstractAsyncContextManager[HueResources]:
        raise ConfigError("The Hue application key contains characters that can't be sent to the bridge.")

    check = await switch_rules.check_targets(SwitchSettings(_DEVICE), PAIRED, connect)

    assert check.warnings == [
        "The Hue settings in the config can't be used, so the light names were not checked (The Hue application key "
        "contains characters that can't be sent to the bridge.)."
    ]


def test_hue_resources_are_sorted_by_name_without_regard_to_case() -> None:
    lights = [
        HueLight("a", "lamp", True),
        HueLight("b", "Desk", False),
        HueLight("c", "LAMP", True),
        HueLight(None, None, False),
    ]
    groups = [
        HueGroup("z1", "Upstairs", "zone", "g1"),
        HueGroup("r1", "study", "room", None),
        HueGroup("r2", "Kitchen", "room", "g2"),
        HueGroup("z2", "attic", "zone", "g3"),
        HueGroup("x1", "Other", "entertainment", "g4"),
    ]

    resources = switch_rules.hue_resources(lights, groups)

    assert [light.id for light in resources.lights] == [None, "b", "a", "c"]
    assert [room.id for room in resources.rooms] == ["r2", "r1"]
    assert [zone.id for zone in resources.zones] == ["z2", "z1"]


async def test_load_resources() -> None:
    resources = await switch_rules.load_resources(PAIRED, FakeBridge().connect)
    assert [light.name for light in resources.lights] == ["Ceiling", "Floor lamp"]

    with pytest.raises(switch_rules.ResourcesError) as not_paired:
        await switch_rules.load_resources(UNPAIRED, FakeBridge().connect)
    with pytest.raises(switch_rules.ResourcesError) as unreachable:
        await switch_rules.load_resources(
            PAIRED, FakeBridge(error=HueApiError("Hue bridge request timed out.")).connect
        )
    with pytest.raises(switch_rules.ResourcesError) as unusable:
        await switch_rules.load_resources(PAIRED, FakeBridge(error=ConfigError("Bad settings.")).connect)

    assert (not_paired.value.status, str(not_paired.value)) == (409, "Pair the Hue bridge first.")
    assert (unreachable.value.status, str(unreachable.value)) == (502, "Hue bridge request timed out.")
    assert (unusable.value.status, str(unusable.value)) == (409, "Bad settings.")


# Validation with .NET's string rules


@pytest.mark.parametrize(
    ("device_id", "normalized", "valid"),
    [
        (" 1000ABCDEF\u3000", "1000abcdef", True),
        ("1000\u212aabc", "1000kabc", True),  # the Kelvin sign lowercases to an ASCII k
        ("1000\u0130abc", "1000\u0130abc", False),
        ("\ufeff1000abcdef", "\ufeff1000abcdef", False),  # .NET doesn't trim U+FEFF
        ("1000abcdef\x85", "1000abcdef", True),  # but trims U+0085
        ("\uff11000abc", "\uff11000abc", False),
        ("abc", "abc", False),
        ("x" * 40, "x" * 40, True),
    ],
)
def test_device_ids_are_trimmed_lowercased_and_ascii(device_id: str, normalized: str, valid: bool) -> None:
    errors, settings = switch_rules.validate(SwitchInput(device_id=device_id, lights=["Ceiling"]))

    assert settings.device_id == normalized
    assert ("deviceId" not in errors) is valid


def test_lengths_count_utf16_code_units() -> None:
    emoji = "\U0001f600"

    assert "name" not in switch_rules.validate(SwitchInput("x" * 62 + emoji, _DEVICE, lights=["a"]))[0]
    assert "name" in switch_rules.validate(SwitchInput("x" * 63 + emoji, _DEVICE, lights=["a"]))[0]
    assert "lights" in switch_rules.validate(SwitchInput(device_id=_DEVICE, lights=["x" * 199 + emoji]))[0]


def test_duplicates_are_removed_with_dotnets_case_rules() -> None:
    lights = [
        "Ceiling",
        " ceiling ",
        "Stra\u00dfe",
        "STRA\u1e9eE",
        "\u03c3",
        "\u03c2",
        "\u00b5",
        "\u039c",
        "\u212a",
        "k",
    ]

    _, settings = switch_rules.validate(SwitchInput(device_id=_DEVICE, lights=lights))

    assert settings.lights == ["Ceiling", "Stra\u00dfe", "STRA\u1e9eE", "\u03c3", "\u00b5", "\u212a", "k"]


def test_more_than_fifty_entries_are_refused_before_duplicates_are_removed() -> None:
    errors, settings = switch_rules.validate(SwitchInput(device_id=_DEVICE, lights=["Ceiling"] * 51))

    assert errors == {"lights": ["Use at most 50 entries."]}
    assert settings.lights == ["Ceiling"]


def test_null_and_empty_entries_need_one_character() -> None:
    errors, _ = switch_rules.validate(SwitchInput(device_id=_DEVICE, lights=["Ceiling", None], groups=[" "]))

    assert errors == {
        "lights": ["Each entry needs 1 to 200 characters."],
        "groups": ["Each entry needs 1 to 200 characters."],
    }


# Parity with ConfigEndpoints.cs


def _cases(section: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = _FIXTURE[section]
    return cases


def _input(raw: dict[str, Any]) -> SwitchInput:
    return SwitchInput(raw["name"], raw["deviceId"], raw["deviceKey"], raw["lights"], raw["groups"])


def _shown(settings: SwitchSettings) -> dict[str, Any]:
    return {
        "name": settings.name,
        "deviceId": settings.device_id,
        "deviceKey": settings.device_key,
        "lights": settings.lights,
        "groups": settings.groups,
    }


def _ported(message: str) -> str:
    """The .NET message as the port words it: Home Assistant pairs again where the app's command line did."""
    return message.replace("run 'sonoff2hue hue-pair' again", hue_client.PAIR_AGAIN)


@pytest.mark.parametrize("case", _cases("switchValidation"), ids=lambda case: json.dumps(case["input"])[:80])
def test_validate_parity(case: dict[str, Any]) -> None:
    errors, settings = switch_rules.validate(_input(case["input"]))

    assert list(errors.items()) == list(case["errors"].items())
    assert _shown(settings) == case["settings"]


class _NoTransport:
    def __call__(self, *args: object) -> Any:
        raise AssertionError("the oracle's bridges are never reachable")


@asynccontextmanager
async def _client(hue: HueSettings) -> AsyncIterator[HueResources]:
    """Opens the bridge as HueClientFactory.Create does, with the real client's checks of the settings."""
    client = hue_client.HueClient(hue.bridge_ip or "", hue.app_key or "", hue.certificate_sha256, _NoTransport())
    try:
        yield client
    finally:
        await client.close()


_CONNECT: ConnectHue = _client


@pytest.mark.parametrize("scenario", _cases("switchWrites"), ids=lambda scenario: scenario["name"])
async def test_writes_parity(scenario: dict[str, Any]) -> None:
    hue = HueSettings(bridge_ip=scenario["hue"]["bridgeIp"], app_key=scenario["hue"]["appKey"])
    switches = [
        SwitchSettings(s["deviceId"], s["name"], s["deviceKey"], s["lights"], s["groups"]) for s in scenario["switches"]
    ]

    for step in scenario["steps"]:
        result: dict[str, Any]
        try:
            if step["op"] == "create":
                write = await switch_rules.create_switch(lambda: switches, _input(step["input"]), hue, _CONNECT)
                switches.append(write.switch)
                result = {"status": 201, "warnings": write.warnings}
            else:
                write = await switch_rules.update_switch(
                    lambda: switches, step["deviceId"], _input(step["input"]), hue, _CONNECT
                )
                assert write.index is not None
                switches[write.index] = write.switch
                result = {"status": 200, "warnings": write.warnings}
        except SwitchValidationError as err:
            result = {"status": 400, "errors": err.errors}
        except (SwitchConflictError, SwitchNotFoundError) as err:
            result = {"status": err.status, "detail": str(err)}

        expected = step["result"]
        if "warnings" in expected:
            expected = expected | {"warnings": [_ported(warning) for warning in expected["warnings"]]}
        assert result == expected, step
        assert list(result.get("errors", {})) == list(expected.get("errors", {}))
        assert [_shown(s) for s in switches] == step["switches"], step
