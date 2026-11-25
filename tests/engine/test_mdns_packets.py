"""The mDNS layer's recorded cases (mdns_packets.json): raw packets, each with the message that dns.py parses from it,
or the error it refuses it with, and the eWeLink announcements and probes that ewelink.py reads from it. Every case must
come out exactly as recorded (decision 30)."""

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.dns import DnsFormatError, DnsMessage, DnsResourceRecord
from custom_components.sonoff2hue.engine.mdns import MdnsPacket

_CASES: list[dict[str, Any]] = json.loads((Path(__file__).parent / "mdns_packets.json").read_text(encoding="utf-8"))
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["name"])
def test_packet(case: dict[str, Any]) -> None:
    data = bytes.fromhex(case["hex"])
    if "parseError" in case:
        with pytest.raises(DnsFormatError) as err:
            DnsMessage.parse(data)
        assert str(err.value) == case["parseError"]
        return

    message = DnsMessage.parse(data)

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
