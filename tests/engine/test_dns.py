"""Tests of the DNS parser and query builder."""

from ipaddress import IPv4Address

import pytest

from custom_components.sonoff2hue.engine.dns import (
    DnsFormatError,
    DnsMessage,
    DnsRecordType,
    build_query,
)

from .packets import DnsPacketBuilder, a, name, srv, txt


def test_build_query_encodes_ptr_question() -> None:
    packet = build_query("_ewelink._tcp.local", DnsRecordType.PTR)

    expected = bytes((0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)) + b"\x08_ewelink\x04_tcp\x05local\x00" + bytes((0, 12, 0, 1))
    assert packet == expected

    parsed = DnsMessage.parse(packet)
    assert not parsed.is_response
    (question,) = parsed.questions
    assert question.name == "_ewelink._tcp.local"
    assert question.type == DnsRecordType.PTR
    assert not question.unicast_response


def test_build_query_sets_the_unicast_bit_and_rejects_bad_labels() -> None:
    assert build_query("x.local.", DnsRecordType.A, unicast_response=True)[-4:] == bytes((0, 1, 0x80, 1))
    with pytest.raises(ValueError, match=r"Invalid DNS label ''\."):
        build_query("a..local", DnsRecordType.A)
    with pytest.raises(ValueError, match="Invalid DNS label"):
        build_query("x" * 64 + ".local", DnsRecordType.A)


def test_parse_reads_ptr_srv_txt_and_a_records() -> None:
    data = (
        DnsPacketBuilder()
        .answer("_hue._tcp.local", DnsRecordType.PTR, name("Hue Bridge - 1A2B3C._hue._tcp.local"))
        .answer(
            "Hue Bridge - 1A2B3C._hue._tcp.local",
            DnsRecordType.TXT,
            txt("bridgeid=001788fffe1a2b3c", "modelid=BSB002", "flag"),
            cache_flush=True,
        )
        .additional("Hue Bridge - 1A2B3C._hue._tcp.local", DnsRecordType.SRV, srv(443, "001788fffe1a2b3c.local"))
        .additional("001788fffe1a2b3c.local", DnsRecordType.A, a("192.168.1.20"))
        .build()
    )

    message = DnsMessage.parse(data)

    assert message.is_response
    assert len(message.answers) == 2
    assert len(message.additionals) == 2
    assert message.answers[0].ptr_domain == "Hue Bridge - 1A2B3C._hue._tcp.local"
    record = message.answers[1]
    assert record.cache_flush
    assert record.txt is not None
    assert record.txt["bridgeid"] == "001788fffe1a2b3c"
    assert record.txt["MODELID"] == "BSB002"
    assert record.txt["flag"] == ""
    assert message.additionals[0].srv_port == 443
    assert message.additionals[0].srv_target == "001788fffe1a2b3c.local"
    assert message.additionals[1].address == IPv4Address("192.168.1.20")
    assert message.all_records == message.answers + message.additionals


def test_parse_follows_compression_pointers() -> None:
    packet = (
        bytes((0, 0, 0x84, 0, 0, 0, 0, 2, 0, 0, 0, 0))
        # offset 12: "_ewelink._tcp.local" PTR -> "eWeLink_1._ewelink._tcp.local" (compressed)
        + b"\x08_ewelink\x04_tcp\x05local\x00"
        + bytes((0, 12, 0, 1, 0, 0, 0, 120, 0, 12))
        + b"\x09eWeLink_1\xc0\x0c"
        # second record: its name is a pointer to the PTR target (offset 43)
        + bytes((0xC0, 43))
        + bytes((0, 16, 0x80, 1, 0, 0, 0, 120, 0, 6))
        + b"\x05seq=7"
    )

    message = DnsMessage.parse(packet)

    assert message.answers[0].ptr_domain == "eWeLink_1._ewelink._tcp.local"
    assert message.answers[1].name == "eWeLink_1._ewelink._tcp.local"
    assert message.answers[1].txt is not None
    assert message.answers[1].txt["seq"] == "7"


def test_parse_rejects_truncated_packets() -> None:
    data = DnsPacketBuilder().answer("x.local", DnsRecordType.A, a("10.0.0.1")).build()

    with pytest.raises(DnsFormatError, match="truncated"):
        DnsMessage.parse(data[:-2])
    with pytest.raises(DnsFormatError, match="shorter than header"):
        DnsMessage.parse(bytes(5))


def test_parse_rejects_compression_loops() -> None:
    packet = bytes((0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0xC0, 12))

    with pytest.raises(DnsFormatError, match="Too many DNS name compression pointers"):
        DnsMessage.parse(packet)


def test_txt_keys_are_case_insensitive_and_the_first_one_wins() -> None:
    data = DnsPacketBuilder().answer("x.local", DnsRecordType.TXT, txt("ID=1", "id=2", "a=b=c")).build()

    record = DnsMessage.parse(data).answers[0].txt

    assert record is not None
    assert dict(record) == {"ID": "1", "a": "b=c"}
    assert record["id"] == "1"


def test_unknown_record_types_keep_their_number() -> None:
    data = DnsPacketBuilder().answer("x.local", 47, b"\x01\x02").build()

    record = DnsMessage.parse(data).answers[0]

    assert record.type == 47
    assert record.txt is None
    assert record.address is None


def test_parse_reports_records_that_overrun_or_names_that_are_too_long() -> None:
    overrun = DnsPacketBuilder().answer("x.local", DnsRecordType.TXT, b"\x05ab").build()
    with pytest.raises(DnsFormatError, match="TXT string overruns record"):
        DnsMessage.parse(overrun)

    long_name = ".".join(["a" * 63] * 17)  # 1087 characters
    with pytest.raises(DnsFormatError, match="DNS name too long"):
        DnsMessage.parse(DnsPacketBuilder().answer(long_name, DnsRecordType.A, a("10.0.0.1")).build())

    bad_label = bytes((0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0x40))
    with pytest.raises(DnsFormatError, match="Unsupported DNS label type"):
        DnsMessage.parse(bad_label)
