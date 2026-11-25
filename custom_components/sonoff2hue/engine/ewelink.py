"""eWeLink LAN broadcasts: the announcements a device makes with mDNS, and its encrypted payload.

The announcements belong to the mDNS layer (decision 30). They compare names as :mod:`.dns` does, and read white space,
device ids and ``seq`` by rules of their own:

- White space is U+0009-U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000-U+200A, U+2028, U+2029, U+202F, U+205F and
  U+3000. U+001C-U+001F, which ``str.isspace()`` counts, aren't.
- A device id is trimmed of that white space and lowercased one character to one character: 'İ', whose lowercase is
  two characters, stays, and a capital sigma always becomes the small sigma, also at the end of a word.
- ``seq`` is a signed 64-bit decimal: an optional sign and ASCII digits, with optional ASCII white space around them,
  and trailing NULs ignored. Anything else, such as ``1_2``, other digits or a leading no-break space, gives no seq.

The payload is the device's params as a JSON object, in plain text in DIY mode, else encrypted with AES-128-CBC and
PKCS7 under the key MD5(device key), with a 16-byte IV; both are base64. It is read with ``base64`` (strictly: white
space isn't base64) and ``json``, as UTF-8 where invalid sequences become U+FFFD.
"""

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
from ipaddress import IPv4Address, IPv6Address, ip_address
import json
import os
import re

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .dns import (
    DnsMessage,
    DnsRecordType,
    DnsResourceRecord,
    ends_with_ignoring_case,
    equals_ignoring_case,
    starts_with_ignoring_case,
)
from .mdns import MdnsPacket

SERVICE_TYPE = "_ewelink._tcp.local"
_IV_LENGTH = 16

_WHITE_SPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
_SEQ = re.compile(r"[\t\n\v\f\r ]*([+-]?[0-9]+)[\t\n\v\f\r ]*\x00*")
_SEQ_LIMIT = 1 << 63

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
"""A value as ``json.loads`` reads it."""


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
    """The eWeLink announcements in a packet."""
    if not packet.message.is_response:
        return []

    records = packet.message.all_records
    found: list[EwelinkAnnouncement] = []
    for record in records:
        # TTL 0 is a "goodbye" record (the device leaves the network), not a state update.
        if record.type != DnsRecordType.TXT or record.txt is None or record.ttl == 0:
            continue
        if not ends_with_ignoring_case(record.name, "." + SERVICE_TYPE):
            continue

        instance = record.name[: len(record.name) - len(SERVICE_TYPE) - 1]
        # The same filter as SonoffLAN: eWeLink_xxx and eWelink_xxx are devices; ihost-* and zbbridgeu-* are not.
        if not starts_with_ignoring_case(instance, "ewelink"):
            continue

        entries = record.txt
        device_id = entries.get("id")
        if _is_blank(device_id):
            device_id = instance[8:] if len(instance) > 8 else None
        if device_id is None or _is_blank(device_id):
            continue

        data = "".join(entries.get(f"data{i}") or "" for i in range(1, 5))
        encrypt = entries.get("encrypt")
        found.append(
            EwelinkAnnouncement(
                device_id=_lower(_trim(device_id)),
                instance_name=instance,
                seq=_parse_seq(entries.get("seq")),
                device_type=entries.get("type"),
                encrypted=encrypt is not None and equals_ignoring_case(encrypt, "true"),
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
    if not starts_with_ignoring_case(name, "ewelink_"):
        return None
    rest = name[8:]
    device_id = rest.split(".", 1)[0]
    return _lower(device_id) if device_id else None


def _find_address(records: tuple[DnsResourceRecord, ...], service_name: str) -> IPv4Address | IPv6Address | None:
    target = next(
        (r.srv_target for r in records if r.type == DnsRecordType.SRV and equals_ignoring_case(r.name, service_name)),
        None,
    )
    if target is None:
        return None
    return next(
        (r.address for r in records if r.type == DnsRecordType.A and equals_ignoring_case(r.name, target)),
        None,
    )


def _sender(packet: MdnsPacket) -> IPv4Address | IPv6Address | None:
    """The sender's address, unless it is this host's own: then the packet is a relay's copy, such as the one that Home
    Assistant OS's multicast plugin repeats from its ``hassio`` bridge, and the device's address is unknown (decision
    19)."""
    return None if packet.from_this_host else ip_address(packet.remote[0])


def _trim(value: str) -> str:
    return value.strip(_WHITE_SPACE)


def _is_blank(value: str | None) -> bool:
    return value is None or not _trim(value)


def _lower(device_id: str) -> str:
    if device_id.isascii():
        return device_id.lower()
    return "".join(lower if len(lower := char.lower()) == 1 else char for char in device_id)


def _parse_seq(value: str | None) -> int | None:
    if value is None or (match := _SEQ.fullmatch(value)) is None:
        return None
    seq = int(match.group(1))
    return seq if -_SEQ_LIMIT <= seq < _SEQ_LIMIT else None


def decrypt(data: str, iv: str, device_key: str) -> str:
    """Decrypts a payload with ``device_key``; raises ``ValueError`` for malformed input."""
    return _decrypt(base64.b64decode(data, validate=True), base64.b64decode(iv, validate=True), device_key)


def encrypt(plaintext: str, device_key: str, iv: bytes | None = None) -> tuple[str, str]:
    """Encrypts a payload the way a device does; returns the data and the IV, both base64."""
    iv = os.urandom(_IV_LENGTH) if iv is None else iv
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(device_key)), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode(), base64.b64encode(iv).decode()


def read_params(announcement: EwelinkAnnouncement, device_key: str | None) -> tuple[dict[str, Json] | None, str | None]:
    """The device's params and an error. The params are ``None`` when they can't be read: no payload, or an encrypted
    payload without a device key; the error says why decoding failed. Never raises: the payload comes from the network
    and may be malformed."""
    if not announcement.data:
        return None, None

    if announcement.encrypted:
        if not device_key or device_key.isspace():
            return None, None
        if not announcement.iv:
            return None, "encrypted payload has no iv"
        try:
            iv = base64.b64decode(announcement.iv, validate=True)
        except ValueError:
            return None, "iv is not valid base64"
        if len(iv) != _IV_LENGTH:
            return None, f"iv has {len(iv)} bytes instead of {_IV_LENGTH}"
        try:
            ciphertext = base64.b64decode(announcement.data, validate=True)
        except ValueError:
            return None, "payload is not valid base64"
        try:
            payload = _decrypt(ciphertext, iv, device_key.strip())
        except ValueError:
            # A wrong key shows up as bad padding.
            return None, "decryption failed, check the deviceKey"
    else:
        payload = announcement.data

    try:
        params = json.loads(payload)
    except ValueError, RecursionError:  # RecursionError: nested too deeply
        return None, (
            "payload is not valid JSON, check the deviceKey" if announcement.encrypted else "payload is not valid JSON"
        )
    if isinstance(params, dict):
        return params, None
    return None, "payload is not a JSON object"


def _decrypt(ciphertext: bytes, iv: bytes, device_key: str) -> str:
    decryptor = Cipher(algorithms.AES(_key(device_key)), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8", "replace")


def _key(device_key: str) -> bytes:
    return hashlib.md5(device_key.encode(), usedforsecurity=False).digest()  # the protocol's key derivation
