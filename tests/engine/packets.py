"""Raw DNS packets for the tests, built the way the devices send them.

A port of the .NET tests' ``TestPackets.cs``: names are written uncompressed.
"""

from ipaddress import IPv4Address
import struct

from custom_components.sonoff2hue.engine.dns import DnsRecordType


def name(value: str) -> bytes:
    out = bytearray()
    for label in value.split("."):
        encoded = label.encode()
        out.append(len(encoded))
        out += encoded
    out.append(0)
    return bytes(out)


def txt(*entries: str) -> bytes:
    out = bytearray()
    for entry in entries:
        encoded = entry.encode()
        out.append(len(encoded))
        out += encoded
    return bytes(out)


def srv(port: int, target: str) -> bytes:
    return bytes((0, 0, 0, 0)) + port.to_bytes(2, "big") + name(target)


def a(ip: str) -> bytes:
    return IPv4Address(ip).packed


class DnsPacketBuilder:
    def __init__(self, response: bool = True) -> None:
        self._response = response
        self._questions = bytearray()
        self._sections: dict[str, bytearray] = {"an": bytearray(), "ns": bytearray(), "ar": bytearray()}
        self._counts = {"qd": 0, "an": 0, "ns": 0, "ar": 0}

    def question(self, qname: str, record_type: int) -> DnsPacketBuilder:
        self._questions += name(qname) + struct.pack(">HH", record_type, 1)
        self._counts["qd"] += 1
        return self

    def answer(
        self, rname: str, record_type: int, rdata: bytes, ttl: int = 120, cache_flush: bool = False
    ) -> DnsPacketBuilder:
        return self._record("an", rname, record_type, rdata, ttl, cache_flush)

    def authority(self, rname: str, record_type: int, rdata: bytes, ttl: int = 120) -> DnsPacketBuilder:
        return self._record("ns", rname, record_type, rdata, ttl, False)

    def additional(
        self, rname: str, record_type: int, rdata: bytes, ttl: int = 120, cache_flush: bool = False
    ) -> DnsPacketBuilder:
        return self._record("ar", rname, record_type, rdata, ttl, cache_flush)

    def build(self) -> bytes:
        header = struct.pack(
            ">HBBHHHH",
            0,
            0x84 if self._response else 0x00,
            0,
            self._counts["qd"],
            self._counts["an"],
            self._counts["ns"],
            self._counts["ar"],
        )
        return header + self._questions + self._sections["an"] + self._sections["ns"] + self._sections["ar"]

    def _record(
        self, section: str, rname: str, record_type: int, rdata: bytes, ttl: int, cache_flush: bool
    ) -> DnsPacketBuilder:
        rclass = 0x8001 if cache_flush else 0x0001
        self._sections[section] += name(rname) + struct.pack(">HHIH", record_type, rclass, ttl, len(rdata)) + rdata
        self._counts[section] += 1
        return self


def announcement(
    device_id: str,
    seq: int | None,
    data: str,
    iv: str | None,
    encrypted: bool = True,
    ip: str = "10.0.0.5",
    ttl: int = 4500,
    instance_prefix: str = "eWeLink_",
) -> bytes:
    """A MINIR4M state broadcast: PTR and TXT answers, SRV and A additional records."""
    instance = f"{instance_prefix}{device_id}._ewelink._tcp.local"
    host = f"{instance_prefix}{device_id}.local"
    entries = ["txtvers=1", f"id={device_id}", "type=plug", "apivers=1"]
    if seq is not None:
        entries.append(f"seq={seq}")
    if encrypted:
        entries.append("encrypt=true")
        entries.append(f"iv={iv}")
    for i in range(4):
        chunk = data[i * 249 : (i + 1) * 249]
        if not chunk:
            break
        entries.append(f"data{i + 1}={chunk}")

    return (
        DnsPacketBuilder()
        .answer("_ewelink._tcp.local", DnsRecordType.PTR, name(instance))
        .answer(instance, DnsRecordType.TXT, txt(*entries), ttl, cache_flush=True)
        .additional(instance, DnsRecordType.SRV, srv(8081, host))
        .additional(host, DnsRecordType.A, a(ip))
        .build()
    )


def probe(device_id: str, ip: str = "10.0.0.5") -> bytes:
    """The probe a device sends for its names right after it boots or joins Wi-Fi."""
    return (
        DnsPacketBuilder(response=False)
        .question(f"eWeLink_{device_id}._ewelink._tcp.local", DnsRecordType.ANY)
        .question(f"eWeLink_{device_id}.local", DnsRecordType.ANY)
        .authority(
            f"eWeLink_{device_id}._ewelink._tcp.local",
            DnsRecordType.SRV,
            srv(8081, f"eWeLink_{device_id}.local"),
        )
        .authority(f"eWeLink_{device_id}.local", DnsRecordType.A, a(ip))
        .build()
    )
