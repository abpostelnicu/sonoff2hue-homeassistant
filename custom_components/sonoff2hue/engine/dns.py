"""DNS messages as mDNS uses them: parsing packets and building queries.

A port of the .NET app's ``Mdns/DnsMessage.cs``.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import IntEnum
from ipaddress import IPv4Address, IPv6Address

from . import text


class DnsRecordType(IntEnum):
    """Record types the engine reads. Other types are kept as plain numbers."""

    A = 1
    PTR = 12
    TXT = 16
    AAAA = 28
    SRV = 33
    ANY = 255


class DnsFormatError(ValueError):
    """A packet that isn't a well-formed DNS message (``FormatException`` in .NET)."""


class TxtRecord(Mapping[str, str]):
    """The key/value strings of a TXT record. Keys are case-insensitive, and only the first occurrence of a key
    counts (RFC 6763 §6.4)."""

    __slots__ = ("_entries",)

    def __init__(self, entries: Iterable[tuple[str, str]] = ()) -> None:
        self._entries: dict[str, tuple[str, str]] = {}
        for key, value in entries:
            self._entries.setdefault(text.fold(key), (key, value))

    def __getitem__(self, key: str) -> str:
        return self._entries[text.fold(key)][1]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"TxtRecord({dict(self.items())!r})"


@dataclass(frozen=True, slots=True)
class DnsQuestion:
    name: str
    type: int
    unicast_response: bool


@dataclass(frozen=True, slots=True)
class DnsResourceRecord:
    """A parsed resource record. Only the fields that belong to its type are set."""

    name: str
    type: int
    cache_flush: bool
    ttl: int
    ptr_domain: str | None = None
    address: IPv4Address | IPv6Address | None = None
    srv_target: str | None = None
    srv_port: int = 0
    txt: TxtRecord | None = None


@dataclass(frozen=True, slots=True)
class DnsMessage:
    id: int = 0
    is_response: bool = False
    questions: tuple[DnsQuestion, ...] = ()
    answers: tuple[DnsResourceRecord, ...] = ()
    authorities: tuple[DnsResourceRecord, ...] = ()
    additionals: tuple[DnsResourceRecord, ...] = ()

    @property
    def all_records(self) -> tuple[DnsResourceRecord, ...]:
        """Answers and additional records together: mDNS responders put useful data in both."""
        return self.answers + self.additionals

    @staticmethod
    def parse(packet: bytes) -> DnsMessage:
        """Parses a packet; raises :class:`DnsFormatError` for a malformed one."""
        if len(packet) < 12:
            raise DnsFormatError("DNS packet shorter than header.")

        message_id = _u16(packet, 0)
        flags = _u16(packet, 2)
        qd_count, an_count, ns_count, ar_count = (_u16(packet, at) for at in (4, 6, 8, 10))

        offset = 12
        questions: list[DnsQuestion] = []
        for _ in range(qd_count):
            name, offset = _read_name(packet, offset)
            _ensure_available(packet, offset, 4)
            record_type = _u16(packet, offset)
            qclass = _u16(packet, offset + 2)
            offset += 4
            questions.append(DnsQuestion(name, _record_type(record_type), (qclass & 0x8000) != 0))

        answers, offset = _read_records(packet, offset, an_count)
        authorities, offset = _read_records(packet, offset, ns_count)
        additionals, offset = _read_records(packet, offset, ar_count)
        return DnsMessage(
            id=message_id,
            is_response=(flags & 0x8000) != 0,
            questions=tuple(questions),
            answers=answers,
            authorities=authorities,
            additionals=additionals,
        )


def build_query(name: str, record_type: int, unicast_response: bool = False) -> bytes:
    """A standard multicast query for one name and type."""
    buffer = bytearray((0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    _write_name(buffer, name)
    buffer += record_type.to_bytes(2, "big")
    buffer += bytes((0x80 if unicast_response else 0x00, 1))  # class IN
    return bytes(buffer)


def _record_type(value: int) -> int:
    try:
        return DnsRecordType(value)
    except ValueError:
        return value


def _u16(packet: bytes, offset: int) -> int:
    return (packet[offset] << 8) | packet[offset + 1]


def _ensure_available(packet: bytes, offset: int, count: int) -> None:
    if offset < 0 or count < 0 or offset + count > len(packet):
        raise DnsFormatError("DNS packet truncated.")


def _read_records(packet: bytes, offset: int, count: int) -> tuple[tuple[DnsResourceRecord, ...], int]:
    records: list[DnsResourceRecord] = []
    for _ in range(count):
        name, offset = _read_name(packet, offset)
        _ensure_available(packet, offset, 10)
        record_type = _record_type(_u16(packet, offset))
        rclass = _u16(packet, offset + 2)
        ttl = int.from_bytes(packet[offset + 4 : offset + 8], "big")
        rd_length = _u16(packet, offset + 8)
        offset += 10
        _ensure_available(packet, offset, rd_length)
        rdata_start = offset
        rdata = packet[offset : offset + rd_length]
        offset += rd_length

        record = DnsResourceRecord(name, record_type, (rclass & 0x8000) != 0, ttl)
        if record_type == DnsRecordType.A and rd_length == 4:
            record = replace(record, address=IPv4Address(rdata))
        elif record_type == DnsRecordType.AAAA and rd_length == 16:
            record = replace(record, address=IPv6Address(rdata))
        elif record_type == DnsRecordType.PTR:
            record = replace(record, ptr_domain=_read_name(packet, rdata_start)[0])
        elif record_type == DnsRecordType.SRV and rd_length >= 7:
            record = replace(
                record,
                srv_port=_u16(rdata, 4),
                srv_target=_read_name(packet, rdata_start + 6)[0],
            )
        elif record_type == DnsRecordType.TXT:
            record = replace(record, txt=_parse_txt(rdata))
        records.append(record)
    return tuple(records), offset


def _parse_txt(rdata: bytes) -> TxtRecord:
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(rdata):
        length = rdata[i]
        i += 1
        if length == 0:
            continue
        if i + length > len(rdata):
            raise DnsFormatError("TXT string overruns record.")
        entry = _decode(rdata[i : i + length])
        i += length
        key, separator, value = entry.partition("=")
        entries.append((key, value if separator else ""))
    return TxtRecord(entries)


def _read_name(packet: bytes, offset: int) -> tuple[str, int]:
    """Reads a possibly compressed name at ``offset``; returns it with the offset after it."""
    labels: list[str] = []
    length_so_far = 0  # string.Length of the name read so far, in UTF-16 units, dots included
    position = offset
    next_offset: int | None = None
    jumps = 0

    while True:
        _ensure_available(packet, position, 1)
        length = packet[position]

        if (length & 0xC0) == 0xC0:
            _ensure_available(packet, position, 2)
            pointer = _u16(packet, position) & 0x3FFF
            if next_offset is None:
                next_offset = position + 2
            jumps += 1
            if jumps > 32:
                raise DnsFormatError("Too many DNS name compression pointers.")
            position = pointer
            continue

        if (length & 0xC0) != 0:
            raise DnsFormatError("Unsupported DNS label type.")

        position += 1
        if length == 0:
            break

        _ensure_available(packet, position, length)
        label = _decode(packet[position : position + length])
        length_so_far += text.utf16_length(label) + (1 if labels else 0)
        labels.append(label)
        position += length
        if length_so_far > 1024:
            raise DnsFormatError("DNS name too long.")

    return ".".join(labels), position if next_offset is None else next_offset


def _write_name(buffer: bytearray, name: str) -> None:
    for label in name.rstrip(".").split("."):
        encoded = label.encode()
        if len(encoded) == 0 or len(encoded) > 63:
            raise ValueError(f"Invalid DNS label '{label}'.")
        buffer.append(len(encoded))
        buffer += encoded
    buffer.append(0)


def _decode(data: bytes) -> str:
    # Encoding.UTF8.GetString never throws; it replaces invalid sequences with U+FFFD.
    return data.decode("utf-8", "replace")
