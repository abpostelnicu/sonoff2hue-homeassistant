#!/usr/bin/env python3
"""Sends synthetic eWeLink LAN announcements, the way a MINIR4M announces its state over mDNS.

Usage: send-ewelink-announcements.py <interface-ipv4> <device-id> <seq> [<seq> ...]

Each seq becomes one multicast DNS response with PTR and TXT answers and SRV and A additional records, unencrypted like
a DIY-mode device's, sent 1.5 seconds apart. The multicast TTL is 0, so the packets loop back to this host only and
never reach a network. ``scripts/develop press``, which the VS Code task "Press the simulated wall switch" runs, and the
end-to-end test (``tools/e2e``) use it with Home Assistant running on the same machine. It runs on Linux and on macOS,
next to mDNSResponder, with the standard library only.
"""

import socket
import struct
import sys
import time

USAGE = "Usage: send-ewelink-announcements.py <interface-ipv4> <device-id> <seq> [<seq> ...]"
PTR, TXT, SRV, A = 12, 16, 33, 1
DATA = '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"triggerType":17}'
"""The device's params: its relay on, in detached mode."""
ADDRESS = "192.0.2.10"
"""The device's address in its A record."""
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
INTERVAL = 1.5


def name(value: str) -> bytes:
    """A DNS name, uncompressed."""
    return b"".join(_string(label) for label in value.split(".")) + b"\0"


def record(owner: str, record_type: int, rdata: bytes, ttl: int, flush: bool = False) -> bytes:
    """A resource record of class IN, with the cache-flush bit when ``flush``."""
    return name(owner) + struct.pack(">HHIH", record_type, 0x8001 if flush else 1, ttl, len(rdata)) + rdata


def announcement(device: str, seq: int, ip: str) -> bytes:
    """The response that announces the state of ``device`` with ``seq``, at the address ``ip``."""
    instance, host = f"eWeLink_{device}._ewelink._tcp.local", f"eWeLink_{device}.local"
    txt = ["txtvers=1", f"id={device}", "type=plug", "apivers=1", f"seq={seq}", f"data1={DATA}"]
    answers = [
        record("_ewelink._tcp.local", PTR, name(instance), 4500),
        record(instance, TXT, b"".join(_string(entry) for entry in txt), 4500, flush=True),
    ]
    additionals = [
        record(instance, SRV, struct.pack(">HHH", 0, 0, 8081) + name(host), 120, flush=True),
        record(host, A, socket.inet_aton(ip), 120, flush=True),
    ]
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(answers), 0, len(additionals))
    return header + b"".join(answers + additionals)


def _string(value: str) -> bytes:
    """A character string: its length in bytes, then its UTF-8 bytes."""
    data = value.encode()
    return bytes([len(data)]) + data


def main(argv: list[str]) -> None:
    """Sends an announcement for each seq in ``argv``, out of the interface with the address in ``argv[1]``."""
    if len(argv) < 4:
        sys.exit(USAGE)
    interface_ip, device, seqs = argv[1], argv[2], [int(seq) for seq in argv[3:]]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sys.platform == "darwin":
            # mDNSResponder holds UDP 5353 with SO_REUSEPORT, and macOS shares a port only between sockets that all set
            # it.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
        sock.bind(("0.0.0.0", MDNS_PORT))  # noqa: S104 - an mDNS response comes from port 5353
        for seq in seqs:
            sock.sendto(announcement(device, seq, ADDRESS), (MDNS_GROUP, MDNS_PORT))
            print(f"sent seq={seq} via {interface_ip}", flush=True)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main(sys.argv)
