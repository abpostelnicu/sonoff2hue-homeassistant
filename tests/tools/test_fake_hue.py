"""The fake Hue bridge answers sonoff2hue's own Hue client as a real bridge would: pairing, the lights, rooms and zones,
the test endpoints and faults, its settings and its certificate."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import re

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest

from custom_components.sonoff2hue.engine.hue_client import HueApiError, HueGroup, HueLight, HueTarget, HueTargetKind
from tools.fake_hue.__main__ import OptionsError, options
from tools.fake_hue.bridge import BRIDGE_ID, NAME, FakeIds, Fault
from tools.fake_hue.server import HELP, create_certificate, fingerprint

from ..hue import FakeHueHost

ALL_OFF = (
    "light 'Ceiling' off\n"
    "light 'Floor lamp' off\n"
    "light 'Hallway' off\n"
    "room 'Living room' off (Ceiling, Floor lamp)\n"
    "zone 'Downstairs' off (Ceiling, Floor lamp, Hallway)\n"
)


async def test_pairing_answers_error_101_until_the_link_button_is_pressed(fake_hue: FakeHueHost) -> None:
    refused = await fake_hue.request_app_key()
    assert json.dumps(refused, separators=(",", ":")) == (
        '[{"error":{"type":101,"address":"","description":"link button not pressed"}}]'
    )

    app_key = await fake_hue.pair()
    assert re.fullmatch("[A-Za-z0-9]{40}", app_key)
    hue = fake_hue.hue_client(app_key)
    try:
        assert len(await hue.get_lights()) == 3
    finally:
        await hue.close()


async def test_pairing_issues_a_client_key_only_when_asked_for(fake_hue_pressed: FakeHueHost) -> None:
    success = (await fake_hue_pressed.request_app_key())[0]["success"]
    assert re.fullmatch("[0-9A-F]{32}", success["clientkey"])

    without_client_key = (await fake_hue_pressed.request_app_key(generate_client_key=False))[0]["success"]
    assert "clientkey" not in without_client_key
    assert without_client_key["username"] != success["username"]


async def test_pairing_closes_again_thirty_seconds_after_the_press(fake_hue: FakeHueHost) -> None:
    assert (await fake_hue.request("POST", "_fake/link-button"))[0] == 200

    fake_hue.clock.now += 29
    assert "success" in (await fake_hue.request_app_key())[0]

    fake_hue.clock.now += 1
    assert (await fake_hue.request_app_key())[0]["error"]["type"] == 101


async def test_pairing_succeeds_at_once_when_the_link_button_is_always_pressed(fake_hue_pressed: FakeHueHost) -> None:
    assert "success" in (await fake_hue_pressed.request_app_key())[0]

    assert "always pressed" in (await fake_hue_pressed.request("POST", "_fake/link-button"))[1]


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ("{not json", 2),
        ("{}", 5),
        ('{"devicetype":42}', 5),
        ('{"devicetype":" \\u3000"}', 5),
        ('{"devicetype":"\\u001c"}', 5),
        ('{"devicetype":"an-application-name-that#is-far-too-long-for-a-bridge"}', 7),
        ('{"devicetype":"' + "a" * 41 + '"}', 7),
    ],
)
async def test_pairing_rejects_malformed_requests_like_a_bridge(
    fake_hue_pressed: FakeHueHost, body: str, error_type: int
) -> None:
    status, text = await fake_hue_pressed.request("POST", "api", body)

    assert status == 200
    assert json.loads(text)[0]["error"]["type"] == error_type


async def test_pairing_accepts_a_device_type_of_40_characters(fake_hue_pressed: FakeHueHost) -> None:
    status, text = await fake_hue_pressed.request("POST", "api", '{"devicetype":"' + "a" * 39 + '\\ud83d\\ude00"}')

    assert status == 200
    assert "success" in json.loads(text)[0]


async def test_public_config_needs_no_key_and_names_the_bridge(fake_hue: FakeHueHost) -> None:
    status, text = await fake_hue.request("GET", "api/0/config")

    assert status == 200
    config = json.loads(text)
    assert (config["bridgeid"], config["name"], config["modelid"]) == (BRIDGE_ID, NAME, "BSB002")
    assert config["swversion"]


async def test_clip_api_rejects_a_missing_or_unknown_application_key(fake_hue: FakeHueHost) -> None:
    await fake_hue.pair()

    assert await fake_hue.request("GET", "clip/v2/resource/light") == (
        403,
        '{"errors":[{"description":"unauthorized user"}],"data":[]}',
    )

    hue = fake_hue.hue_client("not-a-key-the-bridge-issued")
    try:
        with pytest.raises(HueApiError) as read:
            await hue.get_lights()
        assert read.value.is_authentication_error
        with pytest.raises(HueApiError) as write:
            await hue.set_on(HueTarget(HueTargetKind.LIGHT, FakeIds.HALLWAY, "Hallway"), True)
        assert write.value.is_authentication_error
    finally:
        await hue.close()

    assert await fake_hue.get_text("_fake/puts") == (
        f"rejected PUT /clip/v2/resource/light/{FakeIds.HALLWAY}: 403 unauthorized user\n"
    )
    assert await fake_hue.get_text("_fake/lights") == ALL_OFF


async def test_hue_client_reads_the_lights_rooms_and_zones(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        await hue.ping()
        lights, groups = await hue.get_lights_and_groups()
        light_states = await hue.get_light_states()
        grouped_states = await hue.get_grouped_light_states()
    finally:
        await hue.close()

    assert lights == [
        HueLight(FakeIds.CEILING, "Ceiling", False),
        HueLight(FakeIds.FLOOR_LAMP, "Floor lamp", False),
        HueLight(FakeIds.HALLWAY, "Hallway", False),
    ]
    assert groups == [
        HueGroup(
            FakeIds.LIVING_ROOM,
            "Living room",
            "room",
            FakeIds.LIVING_ROOM_LIGHTS,
            frozenset({FakeIds.CEILING, FakeIds.FLOOR_LAMP}),
        ),
        HueGroup(
            FakeIds.DOWNSTAIRS,
            "Downstairs",
            "zone",
            FakeIds.DOWNSTAIRS_LIGHTS,
            frozenset({FakeIds.CEILING, FakeIds.FLOOR_LAMP, FakeIds.HALLWAY}),
        ),
    ]
    assert light_states == {FakeIds.CEILING: False, FakeIds.FLOOR_LAMP: False, FakeIds.HALLWAY: False}
    assert grouped_states == {FakeIds.LIVING_ROOM_LIGHTS: False, FakeIds.DOWNSTAIRS_LIGHTS: False}


async def test_devices_own_the_lights_a_room_holds_devices_and_a_zone_lights(fake_hue: FakeHueHost) -> None:
    """As a real bridge lists them: each light names its device as its owner, and a room's children are devices, the
    living room's dimmer switch too, which owns no light; a zone's children are lights."""
    headers = {"hue-application-key": await fake_hue.pair()}

    answers = {
        kind: json.loads((await fake_hue.request("GET", f"clip/v2/resource/{kind}", headers=headers))[1])["data"]
        for kind in ("light", "room", "zone")
    }

    assert [(light["id"], light["owner"]) for light in answers["light"]] == [
        (FakeIds.CEILING, {"rid": FakeIds.CEILING_DEVICE, "rtype": "device"}),
        (FakeIds.FLOOR_LAMP, {"rid": FakeIds.FLOOR_LAMP_DEVICE, "rtype": "device"}),
        (FakeIds.HALLWAY, {"rid": FakeIds.HALLWAY_DEVICE, "rtype": "device"}),
    ]
    [room] = answers["room"]
    assert room["children"] == [
        {"rid": FakeIds.CEILING_DEVICE, "rtype": "device"},
        {"rid": FakeIds.FLOOR_LAMP_DEVICE, "rtype": "device"},
        {"rid": FakeIds.DIMMER_SWITCH, "rtype": "device"},
    ]
    [zone] = answers["zone"]
    assert zone["children"] == [
        {"rid": FakeIds.CEILING, "rtype": "light"},
        {"rid": FakeIds.FLOOR_LAMP, "rtype": "light"},
        {"rid": FakeIds.HALLWAY, "rtype": "light"},
    ]
    assert [group["services"] for group in (room, zone)] == [
        [{"rid": FakeIds.LIVING_ROOM_LIGHTS, "rtype": "grouped_light"}],
        [{"rid": FakeIds.DOWNSTAIRS_LIGHTS, "rtype": "grouped_light"}],
    ]


async def test_switching_a_light_and_a_room_switches_their_lights(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    hallway = HueTarget(HueTargetKind.LIGHT, FakeIds.HALLWAY, "Hallway")
    living_room = HueTarget(HueTargetKind.GROUPED_LIGHT, FakeIds.LIVING_ROOM_LIGHTS, "Living room")
    try:
        await hue.set_on(hallway, True)
        await hue.set_on(living_room, True)
        assert await fake_hue.get_text("_fake/lights") == ALL_OFF.replace(" off", " on")

        await hue.set_on(hallway, False)
        await hue.set_on(living_room, False)
    finally:
        await hue.close()

    assert await fake_hue.get_text("_fake/lights") == ALL_OFF
    assert await fake_hue.get_text("_fake/puts") == (
        "light 'Hallway' on\nroom 'Living room' on\nlight 'Hallway' off\nroom 'Living room' off\n"
    )


async def test_zone_is_on_while_any_of_its_lights_is_on(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        await hue.set_on(HueTarget(HueTargetKind.LIGHT, FakeIds.HALLWAY, "Hallway"), True)
        groups = await hue.get_grouped_light_states()
    finally:
        await hue.close()

    assert groups[FakeIds.DOWNSTAIRS_LIGHTS] is True
    assert groups[FakeIds.LIVING_ROOM_LIGHTS] is False


NEW_ZONE = {
    "children": [{"rid": FakeIds.CEILING, "rtype": "light"}, {"rid": FakeIds.HALLWAY, "rtype": "light"}],
    "metadata": {"name": "Hall", "archetype": "other"},
}


async def test_a_post_creates_a_zone_of_lights_with_its_grouped_light(fake_hue: FakeHueHost) -> None:
    headers = {"hue-application-key": await fake_hue.pair()}

    status, text = await fake_hue.request("POST", "clip/v2/resource/zone", NEW_ZONE, headers=headers)

    assert (status, json.loads(text)) == (200, {"data": [{"rid": FakeIds.group(3), "rtype": "zone"}], "errors": []})
    status, text = await fake_hue.request("GET", f"clip/v2/resource/zone/{FakeIds.group(3)}", headers=headers)
    assert (status, json.loads(text)["data"]) == (
        200,
        [
            {
                "id": FakeIds.group(3),
                "type": "zone",
                "metadata": {"name": "Hall", "archetype": "other"},
                "children": NEW_ZONE["children"],
                "services": [{"rid": FakeIds.grouped_light(3), "rtype": "grouped_light"}],
            }
        ],
    )
    put = await fake_hue.request(
        "PUT", f"clip/v2/resource/grouped_light/{FakeIds.grouped_light(3)}", {"on": {"on": True}}, headers=headers
    )
    assert put[0] == 200
    assert await fake_hue.get_text("_fake/lights") == (
        "light 'Ceiling' on\n"
        "light 'Floor lamp' off\n"
        "light 'Hallway' on\n"
        "room 'Living room' on (Ceiling, Floor lamp)\n"
        "zone 'Downstairs' on (Ceiling, Floor lamp, Hallway)\n"
        "zone 'Hall' on (Ceiling, Hallway)\n"
    )
    assert await fake_hue.get_text("_fake/posts") == "zone 'Hall' (Ceiling, Hallway)\n"
    assert await fake_hue.get_text("_fake/puts") == "zone 'Hall' on\n"


ROOM_CHILD = {"rid": FakeIds.LIVING_ROOM, "rtype": "room"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("{not json", "body contains invalid JSON"),
        ("[]", "the zone needs metadata, with its name and archetype"),
        ({"children": NEW_ZONE["children"]}, "the zone needs metadata, with its name and archetype"),
        ({**NEW_ZONE, "metadata": {"archetype": "other"}}, "the zone needs a name of 1 to 32 characters"),
        ({**NEW_ZONE, "metadata": {"name": "", "archetype": "other"}}, "the zone needs a name of 1 to 32 characters"),
        (
            {**NEW_ZONE, "metadata": {"name": "x" * 33, "archetype": "other"}},
            "the zone needs a name of 1 to 32 characters",
        ),
        ({**NEW_ZONE, "metadata": {"name": "Hall"}}, "the zone needs the archetype of a room or zone"),
        (
            {**NEW_ZONE, "metadata": {"name": "Hall", "archetype": "Other"}},
            "the zone needs the archetype of a room or zone",
        ),
        (
            {**NEW_ZONE, "metadata": {"name": "Hall", "archetype": ["other"]}},
            "the zone needs the archetype of a room or zone",
        ),
        ({"metadata": NEW_ZONE["metadata"]}, "the zone needs a list of children"),
        ({**NEW_ZONE, "children": [ROOM_CHILD]}, f"a zone's children are lights, not {json.dumps(ROOM_CHILD)}"),
        ({**NEW_ZONE, "children": [None]}, "a zone's children are lights, not null"),
        (
            {**NEW_ZONE, "children": [{"rid": "no-such-light", "rtype": "light"}]},
            "no light with the id 'no-such-light'",
        ),
    ],
)
async def test_a_post_refuses_a_zone_that_a_bridge_would_refuse_and_records_it(
    fake_hue: FakeHueHost, body: object, reason: str
) -> None:
    headers = {"hue-application-key": await fake_hue.pair()}

    status, text = await fake_hue.request("POST", "clip/v2/resource/zone", body, headers=headers)

    assert (status, json.loads(text)) == (400, {"errors": [{"description": reason}], "data": []})
    assert await fake_hue.get_text("_fake/posts") == f"rejected POST /clip/v2/resource/zone: 400 {reason}\n"
    assert await fake_hue.get_text("_fake/lights") == ALL_OFF


async def test_a_post_without_the_application_key_is_rejected_and_recorded(fake_hue: FakeHueHost) -> None:
    await fake_hue.pair()

    assert await fake_hue.request("POST", "clip/v2/resource/zone", NEW_ZONE) == (
        403,
        '{"errors":[{"description":"unauthorized user"}],"data":[]}',
    )
    assert await fake_hue.get_text("_fake/posts") == "rejected POST /clip/v2/resource/zone: 403 unauthorized user\n"
    assert await fake_hue.get_text("_fake/lights") == ALL_OFF


@pytest.mark.parametrize(
    ("path", "status", "description"),
    [
        (f"light/{FakeIds.HALLWAY}", 200, None),
        (f"zone/{FakeIds.LIVING_ROOM}", 404, f"no zone with the id '{FakeIds.LIVING_ROOM}'"),
        ("scene/s1", 404, "The fake bridge has no resources of type 'scene'."),
    ],
)
async def test_a_resource_is_found_by_its_type_and_id(
    fake_hue: FakeHueHost, path: str, status: int, description: str | None
) -> None:
    headers = {"hue-application-key": await fake_hue.pair()}

    answer = await fake_hue.request("GET", f"clip/v2/resource/{path}", headers=headers)

    assert answer[0] == status
    body = json.loads(answer[1])
    if description is None:
        assert [item["metadata"]["name"] for item in body["data"]] == ["Hallway"]
    else:
        assert body == {"errors": [{"description": description}], "data": []}
    assert (await fake_hue.request("GET", f"clip/v2/resource/{path}"))[0] == 403


@pytest.mark.parametrize(
    ("resource", "body", "status", "reason"),
    [
        (f"light/{FakeIds.HALLWAY}", '{"on":true}', 400, "the body has no boolean on.on"),
        (f"light/{FakeIds.HALLWAY}", "{not json", 400, "body contains invalid JSON"),
        ("light/no-such-light", '{"on":{"on":true}}', 404, "no light with the id 'no-such-light'"),
        (f"room/{FakeIds.LIVING_ROOM}", '{"on":{"on":true}}', 404, f"no room with the id '{FakeIds.LIVING_ROOM}'"),
    ],
)
async def test_put_rejects_bad_requests_and_records_them(
    fake_hue: FakeHueHost, resource: str, body: str, status: int, reason: str
) -> None:
    app_key = await fake_hue.pair()

    answer = await fake_hue.request(
        "PUT", f"clip/v2/resource/{resource}", body, headers={"hue-application-key": app_key}
    )

    assert answer[0] == status
    assert json.loads(answer[1])["errors"][0]["description"] == reason
    assert await fake_hue.get_text("_fake/puts") == f"rejected PUT /clip/v2/resource/{resource}: {status} {reason}\n"
    assert await fake_hue.get_text("_fake/lights") == ALL_OFF


async def test_hue_client_reports_an_unknown_resource_as_a_failure(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        with pytest.raises(HueApiError) as failure:
            await hue.set_on(HueTarget(HueTargetKind.GROUPED_LIGHT, "no-such-group", "Attic"), True)
    finally:
        await hue.close()

    assert failure.value.status == 404
    assert not failure.value.is_authentication_error


async def test_unknown_resource_type_is_not_found(fake_hue: FakeHueHost) -> None:
    app_key = await fake_hue.pair()

    status, text = await fake_hue.request("GET", "clip/v2/resource/scene", headers={"hue-application-key": app_key})

    assert status == 404
    assert '"errors":[{"description"' in text


async def test_requests_are_recorded_with_their_status_but_not_the_test_endpoints(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        await hue.get_lights()
    finally:
        await hue.close()
    await fake_hue.request("GET", "clip/v2/resource/light")

    lines = (await fake_hue.get_text("_fake/requests")).rstrip("\n").split("\n")

    assert [line[13:] for line in lines] == [
        "POST /api 200",
        "GET /clip/v2/resource/light 200",
        "GET /clip/v2/resource/light 403",
    ]
    assert all(re.match(r"\d\d:\d\d:\d\d\.\d{3} ", line) for line in lines)


async def test_reset_turns_the_lights_off_clears_the_logs_and_keeps_the_keys(fake_hue: FakeHueHost) -> None:
    hue = fake_hue.hue_client(await fake_hue.pair())
    try:
        await hue.set_on(HueTarget(HueTargetKind.GROUPED_LIGHT, FakeIds.DOWNSTAIRS_LIGHTS, "Downstairs"), True)
        await hue.create_zone("Hall", [FakeIds.HALLWAY])

        assert (await fake_hue.request("POST", "_fake/reset"))[0] == 204

        assert await fake_hue.get_text("_fake/lights") == ALL_OFF
        assert await fake_hue.get_text("_fake/puts") == ""
        assert await fake_hue.get_text("_fake/posts") == ""
        assert await fake_hue.get_text("_fake/requests") == ""
        assert (await fake_hue.request_app_key())[0]["error"]["type"] == 101
        assert len(await hue.get_lights()) == 3
        assert (await hue.create_zone("Hall", [FakeIds.HALLWAY])).id == FakeIds.group(3)
    finally:
        await hue.close()


async def test_help_lists_the_test_endpoints(fake_hue: FakeHueHost) -> None:
    text = await fake_hue.get_text("_fake/")

    assert text == HELP
    for endpoint in (
        "POST /_fake/link-button",
        "GET  /_fake/lights",
        "GET  /_fake/puts",
        "GET  /_fake/posts",
        "POST /_fake/reset",
    ):
        assert endpoint in text


async def test_faults_answer_the_requests_they_match_then_go(fake_hue: FakeHueHost) -> None:
    fake_hue.fail(Fault("/api/0/config", status=503, body="busy", method="GET", count=2))
    fake_hue.fail(Fault("/api/0/config", status=500, method="POST"))

    answers = [await fake_hue.request("GET", "api/0/config") for _ in range(3)]

    assert answers[:2] == [(503, "busy"), (503, "busy")]
    assert answers[2][0] == 200
    assert [fault.method for fault in fake_hue.bridge.faults] == ["POST"]


async def test_a_fault_can_delay_its_answer(fake_hue: FakeHueHost) -> None:
    fake_hue.fail(Fault("/api/0/config", status=200, body="{}", delay=0.2))

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await fake_hue.request("GET", "api/0/config")


def test_options_default_to_loopback_port_18443_and_a_released_link_button() -> None:
    assert options({}) == ("127.0.0.1", 18443, False)


def test_options_come_from_the_environment() -> None:
    environment = {"FAKE_HUE_BIND": " 0.0.0.0 ", "FAKE_HUE_PORT": " 18444 ", "FAKE_HUE_LINK_BUTTON": "Pressed"}

    assert options(environment) == ("0.0.0.0", 18444, True)  # noqa: S104 - the setting under test


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("FAKE_HUE_BIND", "localhost", "FAKE_HUE_BIND must be an IP address"),
        ("FAKE_HUE_PORT", "0", "FAKE_HUE_PORT must be a port number"),
        ("FAKE_HUE_PORT", "65536", "FAKE_HUE_PORT must be a port number"),
        ("FAKE_HUE_PORT", "https", "FAKE_HUE_PORT must be a port number"),
        ("FAKE_HUE_PORT", "18443.0", "FAKE_HUE_PORT must be a port number from 1 to 65535, not '18443.0'."),
        ("FAKE_HUE_LINK_BUTTON", "held", "FAKE_HUE_LINK_BUTTON must be 'released' or 'pressed', not 'held'."),
    ],
)
def test_invalid_options_are_rejected_with_their_name(name: str, value: str, message: str) -> None:
    with pytest.raises(OptionsError) as failure:
        options({name: value})

    assert str(failure.value).startswith(message)


def test_certificate_is_an_ecdsa_server_certificate_for_the_bridge_id_with_the_fingerprint_sonoff2hue_pins() -> None:
    now = datetime.now(UTC)

    certificate_pem, key_pem = create_certificate(BRIDGE_ID, now)

    certificate = x509.load_pem_x509_certificate(certificate_pem)
    key = serialization.load_pem_private_key(key_pem, None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert key.curve.key_size == 256
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "fa4e00fffe000001"
    usages = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usages) == [ExtendedKeyUsageOID.SERVER_AUTH]
    assert certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc
    der = certificate.public_bytes(serialization.Encoding.DER)
    assert fingerprint(certificate_pem) == hashlib.sha256(der).hexdigest().upper()
    assert re.fullmatch("[0-9A-F]{64}", fingerprint(certificate_pem))
