"""The engine against the .NET app itself: every case in fixtures/dotnet_engine.json, which tools/dotnet_oracle made by
running the app's own code, must come out the same here."""

import base64
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink, hue_address, hue_client, hue_setup, jsonnode
from custom_components.sonoff2hue.engine.dns import DnsFormatError, DnsMessage, DnsResourceRecord
from custom_components.sonoff2hue.engine.errors import UnexpectedError
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
from custom_components.sonoff2hue.engine.hue_address import BridgeUrl
from custom_components.sonoff2hue.engine.mdns import MdnsPacket
from custom_components.sonoff2hue.engine.press import PressDetector, summarize

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dotnet_engine.json").read_text(encoding="utf-8"))
_DEVICE = "1000abcdef"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Where the port knowingly differs, with the reason. System.Text.Json compares an escaped string with another one by
# their UTF-8 lengths and the text before the first escape before it unescapes anything, so a string with a lone
# surrogate escape can compare as unequal without the exception it raises everywhere else. The port raises whenever such
# a string is read. No eWeLink firmware sends lone surrogates.
_KNOWN_DIFFERENCES = {
    ("deepEquals", '"\\ud800\\ud800"', '"x"'),
    ("deepEquals", '"x\\udc00\\ud800"', '"x"'),
}


def _cases(section: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = _FIXTURE[section]
    return cases


def _thrown(err: UnexpectedError) -> str:
    """The .NET app's "<type>: <message>" for an exception it doesn't expect."""
    return f"{err.dotnet_type}: {err}"


def _message_of(dotnet: str) -> str:
    return dotnet.split(": ", 1)[1]


def _address(address: IPv4Address | IPv6Address | None) -> str | None:
    return None if address is None else str(address)


def _records(records: tuple[DnsResourceRecord, ...]) -> list[dict[str, Any]]:
    return [
        {
            "name": r.name,
            "type": int(r.type),
            "cacheFlush": r.cache_flush,
            "ttl": r.ttl,
            "ptr": r.ptr_domain,
            "address": _address(r.address),
            "srvTarget": r.srv_target,
            "srvPort": r.srv_port,
            "txt": None if r.txt is None else [[key, value] for key, value in r.txt.items()],
        }
        for r in records
    ]


@pytest.mark.parametrize("case", _cases("packets"), ids=lambda case: case["name"])
def test_packets(case: dict[str, Any]) -> None:
    if "parseError" in case:
        assert case["parseError"].startswith("FormatException: ")
        with pytest.raises(DnsFormatError) as err:
            DnsMessage.parse(bytes.fromhex(case["hex"]))
        assert str(err.value) == _message_of(case["parseError"])
        return

    message = DnsMessage.parse(bytes.fromhex(case["hex"]))

    assert {
        "id": message.id,
        "isResponse": message.is_response,
        "questions": [[q.name, int(q.type), q.unicast_response] for q in message.questions],
        "answers": _records(message.answers),
        "authorities": _records(message.authorities),
        "additionals": _records(message.additionals),
    } == case["message"]

    packet = MdnsPacket(message, (case["sender"], 5353), _EPOCH)
    assert [
        {
            "deviceId": a.device_id,
            "instanceName": a.instance_name,
            "seq": a.seq,
            "deviceType": a.device_type,
            "encrypted": a.encrypted,
            "iv": a.iv,
            "data": a.data,
            "address": _address(a.address),
        }
        for a in ewelink.announcements(packet)
    ] == case["announcements"]
    assert ewelink.probing_device_ids(message) == case["probes"]


def _contains_broken(value: jsonnode.JsonValue) -> str | None:
    """The message of the first broken value that serializing would hit, as .NET's ToJsonString does."""
    match value:
        case jsonnode.BrokenString():
            return value.message
        case jsonnode.BrokenObject() if "surrogate" in value.message:
            return value.message
        case dict():
            return next((m for item in value.values() if (m := _contains_broken(item)) is not None), None)
        case list():
            return next((m for item in value if (m := _contains_broken(item)) is not None), None)
        case _:
            return None


def _canonical(value: jsonnode.JsonValue) -> Any:
    match value:
        case dict():
            return {"object": {key: _canonical(item) for key, item in value.items()}}
        case list():
            return [_canonical(item) for item in value]
        case jsonnode.JsonNumber():
            return {"number": value.text}
        case jsonnode.BrokenObject():
            return {"broken": value.message}
        case _:
            return value


@pytest.mark.parametrize("case", _cases("readParams"), ids=lambda case: case["name"])
def test_read_params(case: dict[str, Any]) -> None:
    announcement = EwelinkAnnouncement(
        _DEVICE, f"eWeLink_{_DEVICE}", 1, "plug", case["encrypted"], case["iv"], case["data"], None, _EPOCH
    )

    params, error = ewelink.read_params(announcement, case["key"])

    assert "throws" not in case
    assert error == case["error"]
    dotnet = case["params"]
    if dotnet is None:
        assert params is None
    elif "throws" in dotnet:
        assert params is not None
        assert _contains_broken(params) == _message_of(dotnet["throws"])
    else:
        assert params is not None
        assert _canonical(params) == _canonical(jsonnode.parse(dotnet["json"]))


@pytest.mark.parametrize("case", _cases("detector"), ids=lambda case: case["name"])
def test_detector(case: dict[str, Any]) -> None:
    detector = PressDetector()
    results: list[dict[str, Any]] = []
    for step in case["steps"]:
        params = None if step["params"] is None else jsonnode.parse(step["params"])
        assert params is None or isinstance(params, (dict, jsonnode.BrokenObject))
        announcement = EwelinkAnnouncement(
            _DEVICE, f"eWeLink_{_DEVICE}", step["seq"], "plug", True, step["iv"], step["data"], None, _EPOCH
        )
        result: dict[str, Any] = {}
        try:
            event = detector.process(announcement, params)
        except jsonnode.JsonAccessError as err:
            result["throws"] = _thrown(err)
        else:
            result.update(kind=event.kind.value, seq=event.seq, detail=event.detail)
        result.update(isDetached=detector.is_detached, hasBaseline=detector.has_baseline)
        results.append(result)

    assert results == case["results"]


@pytest.mark.parametrize("case", _cases("summarize"), ids=lambda case: case["params"][:60])
def test_summarize(case: dict[str, Any]) -> None:
    params = jsonnode.parse(case["params"])
    assert isinstance(params, (dict, jsonnode.BrokenObject))

    if "throws" in case:
        with pytest.raises(jsonnode.JsonAccessError) as err:
            summarize(params)
        assert _thrown(err.value) == case["throws"]
    else:
        assert summarize(params) == case["summary"]


@pytest.mark.parametrize("case", _cases("deepEquals"), ids=lambda case: f"{case['left']} {case['right']}")
def test_deep_equals(case: dict[str, Any]) -> None:
    left, right = jsonnode.parse(case["left"]), jsonnode.parse(case["right"])

    if "throws" in case or ("deepEquals", case["left"], case["right"]) in _KNOWN_DIFFERENCES:
        with pytest.raises(jsonnode.JsonAccessError) as err:
            jsonnode.deep_equals(left, right)
        if "throws" in case:
            assert _thrown(err.value) == case["throws"]
    else:
        assert jsonnode.deep_equals(left, right) is case["equal"]


def test_fixture_comes_from_the_app() -> None:
    assert _FIXTURE["app"].startswith("sonoff2hue v")
    assert _FIXTURE["runtime"].startswith("10.")


# The Hue bridge: addresses, pairing answers and the CLIP v2 client.

_PAIR_AGAIN = "run 'sonoff2hue hue-pair' again"


def _hue_thrown(err: Exception) -> str:
    if isinstance(err, hue_client.HueApiError):
        return "HueApiException: " + str(err).replace(hue_client.PAIR_AGAIN, _PAIR_AGAIN)
    assert isinstance(err, UnexpectedError)
    return _thrown(err)


@pytest.mark.parametrize("case", _cases("bridgeAddress"), ids=lambda case: repr(case["address"])[:60])
def test_bridge_address(case: dict[str, Any]) -> None:
    url, error = hue_address.try_create_base_url(case["address"])

    if "error" in case:
        assert url is None
        assert error == case["error"]
    else:
        assert url is not None
        assert (url.uri, url.host, url.port) == (case["uri"], case["host"], case["port"])
        assert url.idn_host == case.get("idnHost")
        assert ("idnHostThrows" in case) == (url.idn_host is None)


@pytest.mark.parametrize("case", _cases("pairingResponse"), ids=lambda case: f"{case['status']} {case['body'][:50]}")
def test_pairing_response(case: dict[str, Any]) -> None:
    if "throws" in case:
        with pytest.raises((hue_client.HueApiError, UnexpectedError)) as err:
            hue_setup.parse_pairing_response(case["status"], case["body"])
        assert _hue_thrown(err.value) == case["throws"]
        assert getattr(err.value, "status", None) == case.get("statusCode")
    else:
        result = hue_setup.parse_pairing_response(case["status"], case["body"])
        assert {"status": result.status.value, "appKey": result.app_key, "error": result.error} == case["result"]


class _CannedTransport:
    """Answers each request with the next of a case's answers, the last one repeated, and records the requests."""

    def __init__(self, url: BridgeUrl, answers: list[dict[str, Any]]) -> None:
        self._url = url
        self._answers = answers
        self.requests: list[dict[str, Any]] = []

    async def send(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str] | None = None
    ) -> hue_client.HttpAnswer:
        self.requests.append(
            {
                "method": method,
                "uri": hue_client.request_url(self._url, path),
                "key": (headers or {}).get("hue-application-key"),
                "contentType": None if body is None else hue_client.JSON_CONTENT_TYPE,
                "body": None if body is None else body.decode(),
            }
        )
        answer = self._answers[min(len(self.requests) - 1, len(self._answers) - 1)]
        content = base64.b64decode(answer["bodyBase64"]) if "bodyBase64" in answer else (answer["body"] or "").encode()
        content_type = answer.get("contentType")
        charset = None
        if content_type is not None and "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1]
        return hue_client.HttpAnswer(answer["status"], answer.get("reason"), content, charset)

    async def close(self) -> None:
        pass


@pytest.mark.parametrize("case", _cases("hueClient"), ids=lambda case: case["name"])
async def test_hue_client(case: dict[str, Any]) -> None:
    transports: list[_CannedTransport] = []

    def factory(url: BridgeUrl, pin: str | None, on_certificate: Any) -> _CannedTransport:
        transports.append(_CannedTransport(url, case["answers"]))
        return transports[-1]

    client = hue_client.HueClient("192.168.1.20", "app-key", None, factory)
    result: Any = None
    thrown: Exception | None = None
    try:
        match case["operation"]:
            case "lights":
                result = [[light.id, light.name, light.on] for light in await client.get_lights()]
            case "groups":
                result = [[g.id, g.name, g.kind, g.grouped_light_id] for g in await client.get_groups()]
            case "lightStates":
                result = [[key, value] for key, value in (await client.get_light_states()).items()]
            case "groupedLightStates":
                result = [[key, value] for key, value in (await client.get_grouped_light_states()).items()]
            case "ping":
                await client.ping()
                result = "done"
            case "setOn":
                target = case["target"]
                kind = hue_client.HueTargetKind(target["kind"])
                await client.set_on(hue_client.HueTarget(kind, target["id"], "name"), target["on"])
                result = "done"
    except (hue_client.HueApiError, UnexpectedError) as err:
        thrown = err

    if "throws" in case:
        assert thrown is not None, result
        assert _hue_thrown(thrown) == case["throws"]
        if isinstance(thrown, hue_client.HueApiError):
            assert thrown.status == case["statusCode"]
            assert thrown.is_transient == case["isTransient"]
            assert thrown.is_authentication_error == case["isAuthenticationError"]
    else:
        assert thrown is None, thrown
        assert result == case["result"]
    assert transports[0].requests == case["requests"]


@pytest.mark.parametrize("case", _cases("fingerprint"), ids=lambda case: case["fingerprint"])
def test_fingerprint(case: dict[str, Any]) -> None:
    assert hue_client.normalize_fingerprint(case["fingerprint"]) == case["normalized"]


@pytest.mark.parametrize("case", _cases("statusLine"), ids=lambda case: f"{case['status']} {case['reason']}")
def test_status_line(case: dict[str, Any]) -> None:
    answer = hue_client.HttpAnswer(case["status"], case["reason"], b"")

    assert answer.reason_phrase == case["reasonPhrase"]
    assert hue_setup.not_success_message(answer) == case["ensureSuccess"]


def test_reason_phrases() -> None:
    assert {int(code): phrase for code, phrase in _FIXTURE["reasonPhrases"].items()} == hue_client._REASON_PHRASES


@pytest.mark.parametrize("case", _cases("encodings"), ids=lambda case: repr(case["name"]))
def test_encoding_names(case: dict[str, Any]) -> None:
    answer = hue_client.HttpAnswer(200, None, b"", case["name"])

    if "codePage" in case:
        assert answer.read_as_string() == ""
        assert hue_client._code_page(case["name"]) == case["codePage"]
    elif case["throws"].startswith("NotSupportedException"):
        with pytest.raises(UnexpectedError) as err:
            answer.read_as_string()
        assert _thrown(err.value) == case["throws"]
    else:
        assert case["throws"].startswith("ArgumentException")
        with pytest.raises(UnexpectedError) as err:
            answer.read_as_string()
        assert err.value.dotnet_type == "InvalidOperationException"


@pytest.mark.parametrize("case", _cases("ipAddress"), ids=lambda case: repr(case["address"]))
def test_ip_address(case: dict[str, Any]) -> None:
    assert hue_address.is_ip_address(case["address"]) == case["valid"]
