"""The engine against the .NET app itself: every case in fixtures/dotnet_engine.json, which tools/dotnet_oracle made by
running the app's own code, must come out the same here."""

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink, jsonnode
from custom_components.sonoff2hue.engine.dns import DnsFormatError, DnsMessage, DnsResourceRecord
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement
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


def _thrown(err: Exception) -> str:
    """The message part of the .NET app's "<type>: <message>"."""
    return str(err)


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

    expected = [{**r, "throws": _message_of(r["throws"])} if "throws" in r else r for r in case["results"]]
    assert results == expected


@pytest.mark.parametrize("case", _cases("summarize"), ids=lambda case: case["params"][:60])
def test_summarize(case: dict[str, Any]) -> None:
    params = jsonnode.parse(case["params"])
    assert isinstance(params, (dict, jsonnode.BrokenObject))

    if "throws" in case:
        with pytest.raises(jsonnode.JsonAccessError) as err:
            summarize(params)
        assert str(err.value) == _message_of(case["throws"])
    else:
        assert summarize(params) == case["summary"]


@pytest.mark.parametrize("case", _cases("deepEquals"), ids=lambda case: f"{case['left']} {case['right']}")
def test_deep_equals(case: dict[str, Any]) -> None:
    left, right = jsonnode.parse(case["left"]), jsonnode.parse(case["right"])

    if "throws" in case or ("deepEquals", case["left"], case["right"]) in _KNOWN_DIFFERENCES:
        with pytest.raises(jsonnode.JsonAccessError) as err:
            jsonnode.deep_equals(left, right)
        if "throws" in case:
            assert str(err.value) == _message_of(case["throws"])
    else:
        assert jsonnode.deep_equals(left, right) is case["equal"]


def test_fixture_comes_from_the_app() -> None:
    assert _FIXTURE["app"].startswith("sonoff2hue v")
    assert _FIXTURE["runtime"].startswith("10.")
