"""eWeLink LAN broadcasts: the announcements a device makes with mDNS, and its encrypted payload.

A port of the .NET app's ``Ewelink/EwelinkAnnouncement.cs`` and ``Ewelink/EwelinkCrypto.cs``.
"""

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
from ipaddress import IPv4Address, IPv6Address, ip_address
import os
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import jsonnode, text
from .dns import DnsMessage, DnsRecordType, DnsResourceRecord
from .mdns import MdnsPacket

SERVICE_TYPE = "_ewelink._tcp.local"
_IV_LENGTH = 16
_BASE64_WHITE_SPACE = str.maketrans("", "", " \t\r\n")  # what Convert.FromBase64String skips
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True, slots=True)
class EwelinkAnnouncement:
    """One state broadcast: the TXT record of an ``eWeLink_<deviceid>._ewelink._tcp.local`` service. The device
    publishes it again, with ``seq`` one higher, on every change of its state."""

    device_id: str
    instance_name: str
    seq: int | None
    device_type: str | None
    encrypted: bool
    iv: str | None
    data: str
    address: IPv4Address | IPv6Address | None
    received_at: datetime


def announcements(packet: MdnsPacket) -> list[EwelinkAnnouncement]:
    """The eWeLink announcements in a packet (``EwelinkAnnouncement.FromPacket``)."""
    if not packet.message.is_response:
        return []

    records = packet.message.all_records
    found: list[EwelinkAnnouncement] = []
    for record in records:
        # TTL 0 is a "goodbye" record (the device leaves the network), not a state update.
        if record.type != DnsRecordType.TXT or record.txt is None or record.ttl == 0:
            continue
        if not text.ends_with_ignore_case(record.name, "." + SERVICE_TYPE):
            continue

        instance = record.name[: len(record.name) - len(SERVICE_TYPE) - 1]
        # The same filter as SonoffLAN: eWeLink_xxx and eWelink_xxx are devices; ihost-* and zbbridgeu-* are not.
        if not text.starts_with_ignore_case(instance, "ewelink"):
            continue

        entries = record.txt
        device_id = entries.get("id")
        if text.is_null_or_white_space(device_id):
            device_id = instance[8:] if len(instance) > 8 else None
        if device_id is None or text.is_null_or_white_space(device_id):
            continue

        data = "".join(entries.get(f"data{i}") or "" for i in range(1, 5))
        encrypt = entries.get("encrypt")
        found.append(
            EwelinkAnnouncement(
                device_id=text.lower_invariant(text.trim(device_id)),
                instance_name=instance,
                seq=text.try_parse_int(entries.get("seq"), 64),
                device_type=entries.get("type"),
                encrypted=encrypt is not None and text.equals_ignore_case(encrypt, "true"),
                iv=entries.get("iv"),
                data=data,
                address=_find_address(records, record.name) or _sender(packet),
                received_at=packet.received_at,
            )
        )
    return found


def probing_device_ids(message: DnsMessage) -> list[str]:
    """Ids of eWeLink devices that probe for their mDNS names: they just booted or (re)joined the network. Probes are
    queries that carry the proposed records in their authority section."""
    if message.is_response:
        return []
    ids: list[str] = []
    for record in message.authorities:
        device_id = try_get_device_id(record.name)
        if device_id is not None and device_id not in ids:
            ids.append(device_id)
    return ids


def try_get_device_id(name: str) -> str | None:
    """The id in ``eWeLink_<id>._ewelink._tcp.local`` or ``eWeLink_<id>.local``."""
    if not text.starts_with_ignore_case(name, "ewelink_"):
        return None
    rest = name[8:]
    device_id = rest.split(".", 1)[0]
    return text.lower_invariant(device_id) if device_id else None


def _find_address(records: tuple[DnsResourceRecord, ...], service_name: str) -> IPv4Address | IPv6Address | None:
    target = next(
        (
            r.srv_target
            for r in records
            if r.type == DnsRecordType.SRV and text.equals_ignore_case(r.name, service_name)
        ),
        None,
    )
    if target is None:
        return None
    return next(
        (r.address for r in records if r.type == DnsRecordType.A and text.equals_ignore_case(r.name, target)),
        None,
    )


def _sender(packet: MdnsPacket) -> IPv4Address | IPv6Address | None:
    """The sender's address, unless it is this host's own: then the packet is a relay's copy, such as the one that Home
    Assistant OS's multicast plugin repeats from its ``hassio`` bridge, and the device's address is unknown (decision
    19)."""
    return None if packet.from_this_host else ip_address(packet.remote[0])


def decrypt(data: str, iv: str, device_key: str) -> str:
    """Decrypts a payload (AES-128-CBC, PKCS7, key MD5(device key)); raises ``ValueError`` for malformed input."""
    return _decrypt(_from_base64(data), _from_base64(iv), device_key)


def encrypt(plaintext: str, device_key: str, iv: bytes | None = None) -> tuple[str, str]:
    """Encrypts a payload the way a device does; returns the data and the IV, both base64."""
    iv = os.urandom(_IV_LENGTH) if iv is None else iv
    padder = padding.PKCS7(128).padder()
    padded = padder.update(_utf8(plaintext)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(device_key)), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode(), base64.b64encode(iv).decode()


def read_params(
    announcement: EwelinkAnnouncement, device_key: str | None
) -> tuple[dict[str, jsonnode.JsonValue] | jsonnode.BrokenObject | None, str | None]:
    """The device's params and an error (``EwelinkCrypto.TryReadParams``). The params are ``None`` when they can't be
    read: no payload, or an encrypted payload without a device key; the error says why decoding failed. Never raises:
    the payload comes from the network and may be malformed."""
    if not announcement.data:
        return None, None

    if announcement.encrypted:
        if device_key is None or text.is_null_or_white_space(device_key):
            return None, None
        if not announcement.iv:
            return None, "encrypted payload has no iv"
        try:
            iv = _from_base64(announcement.iv)
        except ValueError:
            return None, "iv is not valid base64"
        if len(iv) != _IV_LENGTH:
            return None, f"iv has {len(iv)} bytes instead of {_IV_LENGTH}"
        try:
            ciphertext = _from_base64(announcement.data)
        except ValueError:
            return None, "payload is not valid base64"
        try:
            payload = _decrypt(ciphertext, iv, text.trim(device_key))
        except ValueError:
            # A wrong key shows up as bad padding.
            return None, "decryption failed, check the deviceKey"
    else:
        payload = announcement.data

    try:
        params = jsonnode.parse(payload)
    except jsonnode.JsonError:
        return None, (
            "payload is not valid JSON, check the deviceKey" if announcement.encrypted else "payload is not valid JSON"
        )
    if isinstance(params, (dict, jsonnode.BrokenObject)):
        return params, None
    return None, "payload is not a JSON object"


def _decrypt(ciphertext: bytes, iv: bytes, device_key: str) -> str:
    decryptor = Cipher(algorithms.AES(_key(device_key)), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    # Encoding.UTF8.GetString replaces invalid sequences with U+FFFD, as "replace" does.
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8", "replace")


def _key(device_key: str) -> bytes:
    return hashlib.md5(_utf8(device_key), usedforsecurity=False).digest()  # the protocol's key derivation


def _utf8(value: str) -> bytes:
    # Encoding.UTF8.GetBytes writes a lone surrogate as U+FFFD.
    return _LONE_SURROGATE.sub("\ufffd", value).encode()


def _from_base64(value: str) -> bytes:
    """``Convert.FromBase64String``: skips spaces, tabs and line breaks, and is otherwise strict."""
    # binascii.Error, and the ValueError for characters outside ASCII, are both ValueErrors.
    return base64.b64decode(value.translate(_BASE64_WHITE_SPACE), validate=True)
