"""Tests of eWeLink announcements, which the mDNS layer reads from its records, and of their payloads."""

import base64
from datetime import UTC, datetime
import hashlib
import importlib.util
from ipaddress import IPv4Address
import math
from pathlib import Path
from types import ModuleType

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import pytest

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.dns import DnsRecordType
from custom_components.sonoff2hue.engine.ewelink import EwelinkAnnouncement

from .packets import DnsPacketBuilder, announcement as announcement_packet, probe, to_packet, txt

# Generated independently with:
#   KEY=$(printf %s "$DEVICE_KEY" | openssl dgst -md5 -binary | xxd -p)
#   printf %s "$PLAINTEXT" | openssl enc -aes-128-cbc -K $KEY -iv 000102030405060708090a0b0c0d0e0f -base64 -A
DEVICE_KEY = "a1b2c3d4-e5f6-4711-8899-aabbccddeeff"
PLAINTEXT = '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"triggerType":17}'
IV = "AAECAwQFBgcICQoLDA0ODw=="
CIPHERTEXT = (
    "J+9y+eZHyS+FZqSSVM7iS1NhFFg5WHcGQ6klulGLCLGGBjRuAeuRU9kHqy8ll6WgvKH39zrIZzLlAiulbhezRzCxr7/PzLGhNRzPIN+UJQE="
)
DEVICE = "1000abcdef"


def _announcement(data: str, iv: str | None, encrypted: bool) -> EwelinkAnnouncement:
    return EwelinkAnnouncement(DEVICE, f"eWeLink_{DEVICE}", 1, "plug", encrypted, iv, data, None, datetime.now(UTC))


def test_decrypt_matches_openssl_reference_vector() -> None:
    assert ewelink.decrypt(CIPHERTEXT, IV, DEVICE_KEY) == PLAINTEXT


def test_encrypt_round_trips_and_matches_reference() -> None:
    data, iv = ewelink.encrypt(PLAINTEXT, DEVICE_KEY, base64.b64decode(IV))

    assert (data, iv) == (CIPHERTEXT, IV)
    random_data, random_iv = ewelink.encrypt(PLAINTEXT, DEVICE_KEY)
    assert ewelink.decrypt(random_data, random_iv, DEVICE_KEY) == PLAINTEXT


def test_announcements_parses_encrypted_announcement_split_across_data_fields() -> None:
    long_payload = "x" * 600
    packet = to_packet(announcement_packet("1000ABCDEF", 771, long_payload, IV, ip="10.110.0.154"))

    (parsed,) = ewelink.announcements(packet)

    assert parsed.device_id == "1000abcdef"
    assert parsed.instance_name == "eWeLink_1000ABCDEF"
    assert parsed.seq == 771
    assert parsed.device_type == "plug"
    assert parsed.encrypted
    assert parsed.iv == IV
    assert parsed.data == long_payload
    assert parsed.address == IPv4Address("10.110.0.154")
    assert parsed.received_at == packet.received_at


def test_announcements_ignores_goodbyes_queries_and_non_device_instances() -> None:
    goodbye = to_packet(announcement_packet(DEVICE, 1, "abc", IV, ttl=0))
    bridge = to_packet(announcement_packet("1001234567", 1, "abc", IV, instance_prefix="zbbridgeu-"))
    device_probe = to_packet(probe(DEVICE))

    assert ewelink.announcements(goodbye) == []
    assert ewelink.announcements(bridge) == []
    assert ewelink.announcements(device_probe) == []


def test_announcements_falls_back_to_the_instance_name_and_the_sender() -> None:
    # No "id" entry, and no SRV and A records, as some devices send it.
    data = (
        DnsPacketBuilder()
        .answer("eWelink_1000ABCDEF._ewelink._tcp.local", DnsRecordType.TXT, txt("seq=5", "data1={}", "encrypt=TRUE"))
        .build()
    )

    (parsed,) = ewelink.announcements(to_packet(data, sender="192.0.2.7"))

    assert parsed.device_id == "1000abcdef"
    assert parsed.instance_name == "eWelink_1000ABCDEF"
    assert parsed.address == IPv4Address("192.0.2.7")
    assert parsed.encrypted
    assert parsed.iv is None
    assert parsed.device_type is None


def test_announcements_take_no_address_from_a_copy_that_this_host_relays() -> None:
    # The update after a press has no SRV and A records. Home Assistant OS's multicast plugin repeats it from its own
    # bridge, whose address is this host's (decision 19), so the copy doesn't say where the switch is.
    press = announcement_packet(DEVICE, 6, "abc", IV, ip=None)

    (relayed,) = ewelink.announcements(to_packet(press, sender="192.0.2.1", from_this_host=True))
    (direct,) = ewelink.announcements(to_packet(press, sender="192.0.2.7"))

    assert relayed.address is None
    assert (relayed.device_id, relayed.seq) == (DEVICE, 6)
    assert direct.address == IPv4Address("192.0.2.7")


def _simulator() -> ModuleType:
    """tools/send-ewelink-announcements.py, the simulated switch."""
    path = Path(__file__).parents[2] / "tools" / "send-ewelink-announcements.py"
    spec = importlib.util.spec_from_file_location("send_ewelink_announcements", path)
    assert spec is not None
    assert spec.loader is not None
    simulator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(simulator)
    return simulator


def test_the_simulated_switch_on_this_host_keeps_its_a_records_address() -> None:
    # The simulated switch sends from this host with TTL 0, with SRV and A records.
    packet = to_packet(_simulator().announcement(DEVICE, 5, "192.0.2.10"), "192.0.2.1", from_this_host=True)

    (parsed,) = ewelink.announcements(packet)

    assert (parsed.seq, parsed.address) == (5, IPv4Address("192.0.2.10"))


def test_the_simulated_switch_writes_its_txt_strings_with_their_lengths_in_bytes() -> None:
    simulator = _simulator()

    (parsed,) = ewelink.announcements(to_packet(simulator.announcement("1000\u00e9abcd", 7, "192.0.2.10")))

    assert (parsed.device_id, parsed.seq, parsed.encrypted, parsed.data) == ("1000\u00e9abcd", 7, False, simulator.DATA)


def test_announcements_skips_instances_without_an_id() -> None:
    data = DnsPacketBuilder().answer("eWeLink_._ewelink._tcp.local", DnsRecordType.TXT, txt("seq=5")).build()

    assert ewelink.announcements(to_packet(data)) == []


def test_probing_device_ids_detects_device_probes() -> None:
    device_probe = to_packet(probe("1000ABCDEF"))
    state = to_packet(announcement_packet(DEVICE, 1, "abc", IV))

    assert ewelink.probing_device_ids(device_probe.message) == [DEVICE]
    assert ewelink.probing_device_ids(state.message) == []


def test_read_params_decodes_with_key_and_reports_wrong_key() -> None:
    encrypted = _announcement(CIPHERTEXT, IV, encrypted=True)

    params, error = ewelink.read_params(encrypted, DEVICE_KEY)
    assert error is None
    assert params == {"switches": [{"outlet": 0, "switch": "on"}], "relaySeparation": 1, "triggerType": 17}
    # The key is stripped of white space first.
    assert ewelink.read_params(encrypted, f"\t{DEVICE_KEY} \n") == (params, None)

    assert ewelink.read_params(encrypted, None) == (None, None)
    assert ewelink.read_params(encrypted, "") == (None, None)
    assert ewelink.read_params(encrypted, "  ") == (None, None)

    params, error = ewelink.read_params(encrypted, "wrong-key")
    assert params is None
    assert error == "decryption failed, check the deviceKey"


def test_read_params_reads_plain_diy_payload() -> None:
    params, error = ewelink.read_params(_announcement('{"switch":"on"}', None, encrypted=False), None)

    assert error is None
    assert params == {"switch": "on"}
    assert ewelink.read_params(_announcement('{"switch":"on"}', None, encrypted=False), DEVICE_KEY) == (params, None)


def test_read_params_without_a_payload_reads_nothing() -> None:
    assert ewelink.read_params(_announcement("", IV, encrypted=True), DEVICE_KEY) == (None, None)
    assert ewelink.read_params(_announcement("", None, encrypted=False), None) == (None, None)


@pytest.mark.parametrize(
    ("iv", "data", "expected"),
    [
        ("not base64!", CIPHERTEXT, "iv is not valid base64"),
        ("AAECAwQFBgcICQoLDA0ODw", CIPHERTEXT, "iv is not valid base64"),  # without its padding
        ("\u00c4AECAwQFBgcICQoLDA0ODw==", CIPHERTEXT, "iv is not valid base64"),
        (IV, "not base64!", "payload is not valid base64"),
        (IV, CIPHERTEXT + "\u00c4", "payload is not valid base64"),
        (IV, "AAECAwQFBgcICQ==", "decryption failed, check the deviceKey"),  # 10 bytes, not a whole block
        ("", CIPHERTEXT, "encrypted payload has no iv"),
        (None, CIPHERTEXT, "encrypted payload has no iv"),
    ],
)
def test_read_params_reports_malformed_payloads_without_raising(iv: str | None, data: str, expected: str) -> None:
    params, error = ewelink.read_params(_announcement(data, iv, encrypted=True), DEVICE_KEY)

    assert params is None
    assert error == expected


@pytest.mark.parametrize("iv_length", [1, 8, 15, 17])
def test_malformed_encrypted_iv_is_reported_without_raising(iv_length: int) -> None:
    data, _ = ewelink.encrypt('{"switch":"on"}', DEVICE_KEY)
    iv = base64.b64encode(bytes(iv_length)).decode()

    params, error = ewelink.read_params(_announcement(data, iv, encrypted=True), DEVICE_KEY)

    assert params is None
    assert error == f"iv has {iv_length} bytes instead of 16"


def test_base64_with_white_space_is_not_valid() -> None:
    spaced = " \n".join(CIPHERTEXT[i : i + 8] for i in range(0, len(CIPHERTEXT), 8))

    assert ewelink.read_params(_announcement(CIPHERTEXT, f"\t{IV}\r\n", encrypted=True), DEVICE_KEY) == (
        None,
        "iv is not valid base64",
    )
    assert ewelink.read_params(_announcement(spaced, IV, encrypted=True), DEVICE_KEY) == (
        None,
        "payload is not valid base64",
    )


def test_a_key_that_cant_be_encoded_fails_to_decrypt() -> None:
    encrypted = _announcement(CIPHERTEXT, IV, encrypted=True)

    assert ewelink.read_params(encrypted, "key\ud800") == (None, "decryption failed, check the deviceKey")


@pytest.mark.parametrize(
    ("payload", "encrypted", "expected"),
    [
        ("[1]", False, "payload is not a JSON object"),
        ("null", False, "payload is not a JSON object"),
        ('"text"', False, "payload is not a JSON object"),
        ("42", False, "payload is not a JSON object"),
        ("[" * 65 + "]" * 65, False, "payload is not a JSON object"),
        ("{", False, "payload is not valid JSON"),
        ("{'a':1}", False, "payload is not valid JSON"),
        ('{"a":1}x', False, "payload is not valid JSON"),
        ("\ufeff{}", False, "payload is not valid JSON"),
        ("[" * 100_000, False, "payload is not valid JSON"),  # nested too deeply for the decoder
        ("{", True, "payload is not valid JSON, check the deviceKey"),
        ("[1]", True, "payload is not a JSON object"),
    ],
)
def test_read_params_reports_payloads_that_are_not_json_objects(payload: str, encrypted: bool, expected: str) -> None:
    if encrypted:
        data, iv = ewelink.encrypt(payload, DEVICE_KEY)
        announcement = _announcement(data, iv, encrypted=True)
    else:
        announcement = _announcement(payload, None, encrypted=False)

    assert ewelink.read_params(announcement, DEVICE_KEY) == (None, expected)


def test_read_params_takes_json_as_python_reads_it() -> None:
    payload = '{"a":NaN,"b":1,"b":2,"c":1.0,"d":"\\u00e4","e":[true,null]}'

    params, error = ewelink.read_params(_announcement(payload, None, encrypted=False), None)

    assert error is None
    assert params is not None
    nan = params.pop("a")
    assert isinstance(nan, float)
    assert math.isnan(nan)
    assert params == {"b": 2, "c": 1.0, "d": "\u00e4", "e": [True, None]}


def test_invalid_utf8_in_a_decrypted_payload_becomes_replacement_characters() -> None:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(b'{"fwVersion":"1.3\xff"}') + padder.finalize()
    key = hashlib.md5(DEVICE_KEY.encode(), usedforsecurity=False).digest()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(base64.b64decode(IV))).encryptor()
    data = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()

    assert ewelink.read_params(_announcement(data, IV, encrypted=True), DEVICE_KEY) == (
        {"fwVersion": "1.3\ufffd"},
        None,
    )
