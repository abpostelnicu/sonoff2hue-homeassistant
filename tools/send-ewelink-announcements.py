#!/usr/bin/env python3
"""Sends synthetic eWeLink LAN announcements, the way a MINIR4M announces its state over mDNS.

Usage: send-ewelink-announcements.py <interface-ipv4> <device-id> <seq> [<seq> ...]

Each seq becomes one multicast DNS response with PTR, TXT, SRV and A records (the layout of the captured packets in
tests/Sonoff2Hue.Tests/TestPackets.cs), unencrypted like a DIY-mode device, sent 1.5 seconds apart. The multicast TTL
is 0, so the packets loop back to this host only and never reach a network. CI uses this to check that the container
receives mDNS next to the host's own mDNS responder. It runs on Linux and on macOS, next to mDNSResponder; the VS Code
task "Press the simulated wall switch" uses it with a sonoff2hue running on the same machine.
"""
import socket
import struct
import sys
import time

PTR, TXT, SRV, A = 12, 16, 33, 1
DATA = '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"triggerType":17}'


def name(n):
    return b"".join(bytes([len(p.encode())]) + p.encode() for p in n.split(".")) + b"\0"


def record(n, rtype, rdata, ttl, flush=False):
    return name(n) + struct.pack(">HHIH", rtype, 0x8001 if flush else 1, ttl, len(rdata)) + rdata


def announcement(device, seq, ip):
    instance, host = f"eWeLink_{device}._ewelink._tcp.local", f"eWeLink_{device}.local"
    txt = ["txtvers=1", f"id={device}", "type=plug", "apivers=1", f"seq={seq}", f"data1={DATA}"]
    answers = [
        record("_ewelink._tcp.local", PTR, name(instance), 4500),
        record(instance, TXT, b"".join(bytes([len(t)]) + t.encode() for t in txt), 4500, True),
    ]
    additionals = [
        record(instance, SRV, struct.pack(">HHH", 0, 0, 8081) + name(host), 120, True),
        record(host, A, socket.inet_aton(ip), 120, True),
    ]
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(answers), 0, len(additionals))
    return header + b"".join(answers + additionals)


def main(argv):
    if len(argv) < 4:
        sys.exit(__doc__.strip().splitlines()[2])
    interface_ip, device, seqs = argv[1], argv[2], [int(s) for s in argv[3:]]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if sys.platform == "darwin":
        # mDNSResponder holds UDP 5353 with SO_REUSEPORT, and macOS shares a port only between sockets that all set it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    sock.bind(("0.0.0.0", 5353))
    for seq in seqs:
        sock.sendto(announcement(device, seq, "192.0.2.10"), ("224.0.0.251", 5353))
        print(f"sent seq={seq} via {interface_ip}", flush=True)
        time.sleep(1.5)


if __name__ == "__main__":
    main(sys.argv)
