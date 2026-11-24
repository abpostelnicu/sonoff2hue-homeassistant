"""Builds the inputs of tests/engine/fixtures/dotnet_engine.json: edge cases for the engine that the .NET app's own code
then evaluates (Program.cs, "engine"). Run by tools/dotnet_oracle/generate.sh; see README.md.
"""

import base64
import hashlib
import json
from pathlib import Path
import sys

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.sonoff2hue.engine import ewelink
from custom_components.sonoff2hue.engine.dns import DnsRecordType
from tests.engine.packets import DnsPacketBuilder, a, announcement, name, probe, srv, txt
from tools.dotnet_oracle import switch_cases
from tools.dotnet_oracle.yaml_cases import uri_cases, yaml_cases

KEY = "a1b2c3d4-e5f6-4711-8899-aabbccddeeff"
IV = bytes(range(16))
DEVICE = "1000abcdef"
DATA_FIELD_LENGTH = 249


def encrypted_raw(plaintext: bytes, key: str = KEY) -> tuple[str, str]:
    """Encrypts bytes that needn't be UTF-8, which ewelink.encrypt can't."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    aes_key = hashlib.md5(key.encode(), usedforsecurity=False).digest()
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode(), base64.b64encode(IV).decode()


JSON_TEXTS = [
    "{}",
    "null",
    "[1]",
    "1",
    '"x"',
    "{",
    "NaN",
    '{"a":NaN}',
    '{"a":Infinity}',
    '{"a":01}',
    '{"a":1,}',
    "{'a':1}",
    '{"a":1} ',
    ' \n{"a":1}\t',
    '{"a":1}\u0000',
    '\ufeff{"a":1}',
    '{"a":1} // comment',
    '{"a":1,"a":2}',
    '{"a":1,"\\u0061":2}',
    '{"a":1,"A":2}',
    '{"switches":[{"outlet":0,"outlet":1,"switch":"on"}]}',
    '{"switch":"\\ud800"}',
    '{"\\ud800":1}',
    '{"a":"\\ud83d\\ude00"}',
    '{"a":1e400,"b":-0,"c":1.0,"d":1E2,"e":-7.50}',
    "[" * 64 + "]" * 64,
    "[" * 65 + "]" * 65,
    '{"a":"' + "[" * 70 + '"}',
    '{"a":"\\"[[["}',
]

DETECTOR_PARAMS = {
    "base": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":3,"rssi":-70}',
    "relayOff": '{"switches":[{"outlet":0,"switch":"off"}],"relaySeparation":1,"sledOnline":"on","swMode":3,"rssi":-70}',
    "relayUpper": '{"switches":[{"outlet":0,"switch":"ON"}],"relaySeparation":1,"sledOnline":"on","swMode":3,"rssi":-71}',
    "ledUpper": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"ON","swMode":3,"rssi":-70}',
    "swModeDecimal": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":3.0,"rssi":-70}',
    "swModeString": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":"3","rssi":-70}',
    "swModeTrue": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":true,"rssi":-70}',
    "swModeOne": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":1,"rssi":-70}',
    "swModeNull": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","swMode":null,"rssi":-70}',
    "noSwMode": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1,"sledOnline":"on","rssi":-70}',
    "separationString": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":" 1 ","sledOnline":"on","swMode":3}',
    "separationZero": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":0,"sledOnline":"on","swMode":3}',
    "separationDecimal": '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1.0,"sledOnline":"on","swMode":3}',
    "outletString": '{"switches":[{"outlet":"1","switch":"on"},{"outlet":" 0","switch":"off"}]}',
    "outletString2": '{"switches":[{"outlet":"1","switch":"off"},{"outlet":" 0","switch":"off"}]}',
    "outletMissing": '{"switches":[{"switch":"on"},{"switch":"on"},{"outlet":0,"switch":"off"}]}',
    "outletMissing2": '{"switches":[{"switch":"on"},{"switch":"off"},{"outlet":0,"switch":"off"}]}',
    "outletNegative": '{"switches":[{"outlet":-3,"switch":"on"}],"switch":"off"}',
    "outletNegative2": '{"switches":[{"outlet":-3,"switch":"off"}],"switch":"off"}',
    "topLevel": '{"switch":"on","startup":"stay"}',
    "topLevel2": '{"switch":"on","startup":"on"}',
    "topLevel3": '{"switch":"off","startup":"on"}',
    "nonStringSwitch": '{"switches":[{"outlet":0,"switch":1}],"switch":true}',
    "configureA": '{"switches":[{"outlet":0,"switch":"on"}],"configure":[{"outlet":0,"startup":"stay"}]}',
    "configureB": '{"switches":[{"outlet":0,"switch":"on"}],"configure":[{"startup":"stay","outlet":0}]}',
    "configureC": '{"switches":[{"outlet":0,"switch":"on"}],"configure":[{"outlet":0,"startup":"on"}]}',
    "configureDup": '{"switches":[{"outlet":0,"switch":"on"}],"configure":[{"outlet":0,"outlet":0,"startup":"stay"}]}',
    "dupTop": '{"relaySeparation":1,"relaySeparation":1}',
    "dupSwitchItem": '{"switches":[{"outlet":0,"switch":"on","switch":"on"}],"relaySeparation":1}',
    "loneSurrogateSwitch": '{"switches":[{"outlet":0,"switch":"\\ud800"}]}',
    "loneSurrogateLed": '{"switches":[{"outlet":0,"switch":"on"}],"sledOnline":"\\ud800"}',
    "loneSurrogateLed2": '{"switches":[{"outlet":0,"switch":"on"}],"sledOnline":"\\udc00"}',
    "loneSurrogateOutlet": '{"switches":[{"outlet":"\\ud800","switch":"on"}]}',
}


def step(seq: int | None, params: str | None, iv: str | None = "iv", data: str | None = None) -> dict[str, object]:
    return {"seq": seq, "iv": iv, "data": f"payload{seq}" if data is None else data, "params": params}


P = DETECTOR_PARAMS
DETECTOR_SEQUENCES = {
    "pressesWithoutParams": [step(1, None), step(2, None), step(2, None), step(4, None), step(3, None), step(5, None)],
    "noSeq": [
        step(None, None, data="a"),
        step(None, None, data="a"),
        step(None, None, iv=None, data="a"),
        step(None, None, data="b"),
        step(7, None, data="b"),
        step(None, None, data="b"),
    ],
    "relayCaseOnly": [step(1, P["base"]), step(2, P["relayUpper"]), step(3, P["relayOff"])],
    "settingsCase": [step(1, P["base"]), step(2, P["ledUpper"])],
    "numbersByValue": [
        step(1, P["base"]),
        step(2, P["swModeDecimal"]),
        step(3, P["swModeString"]),
        step(4, P["swModeTrue"]),
        step(5, P["swModeOne"]),
        step(6, P["swModeTrue"]),
        step(7, P["swModeNull"]),
        step(8, P["swModeNull"].replace("-70", "-71")),
        step(9, P["noSwMode"]),
        step(10, P["base"]),
    ],
    "separation": [step(1, P["separationString"]), step(2, P["separationZero"]), step(3, P["separationDecimal"])],
    "outlets": [
        step(1, P["outletString"]),
        step(2, P["outletString2"]),
        step(3, P["outletMissing"]),
        step(4, P["outletMissing2"]),
        step(5, P["outletNegative"]),
        step(6, P["outletNegative2"]),
    ],
    "topLevel": [
        step(1, P["topLevel"]),
        step(2, P["topLevel2"]),
        step(3, P["topLevel3"]),
        step(4, P["nonStringSwitch"]),
    ],
    "objectsUnordered": [step(1, P["configureA"]), step(2, P["configureB"]), step(3, P["configureC"])],
    "lateParams": [step(1, None), step(1, P["base"]), step(2, P["relayOff"])],
    "paramsDisappear": [step(1, P["base"]), step(2, None), step(3, P["relayOff"])],
    "duplicateTopLevel": [step(1, P["dupTop"]), step(2, P["base"]), step(3, P["dupTop"]), step(4, P["base"])],
    "duplicateInSwitchItem": [step(1, P["dupSwitchItem"]), step(2, P["base"]), step(3, P["base"])],
    "duplicateInSetting": [step(1, P["configureA"]), step(2, P["configureDup"]), step(3, P["configureA"])],
    "loneSurrogates": [
        step(1, P["base"]),
        step(2, P["loneSurrogateSwitch"]),
        step(3, P["base"]),
        step(4, P["loneSurrogateLed"]),
        step(5, P["loneSurrogateLed"]),
        step(6, P["loneSurrogateLed2"]),
        step(7, P["base"]),
        step(8, P["loneSurrogateOutlet"]),
    ],
}

SUMMARIZE = [
    *DETECTOR_PARAMS.values(),
    '{"switch":"on","fwVersion":"<1.0 & ä>","rssi":-7.50,"swMode":true,"triggerType":null}',
    '{"fwVersion":"\\"q\\" \\\\ \\n\\t\\b\\f\\r\\u0001\\u007f é 😀 +`\'","rssi":1e5,"swMode":{"a":1},"triggerType":[1]}',
    '{"fwVersion":"\\"\\"x\\"\\"","relaySeparation":"\\u0022"}',
    '{"fwVersion":"\\ud800"}',
    '{"switches":[{"outlet":2,"switch":"on"},{"outlet":2,"switch":"off"},{"outlet":1,"switch":"x"}]}',
]

DEEP_EQUALS = [
    ("1", "1.0"),
    ("1", "1e0"),
    ("10", "1e1"),
    ("true", "1"),
    ("1", "1.0000000000000001"),
    ("0", "-0"),
    ("1e400", "1e400"),
    ("1e400", "1e401"),
    ('"A"', '"\\u0041"'),
    ('{"a":1,"b":2}', '{"b":2,"a":1}'),
    ('{"a":1}', '{"a":1,"b":2}'),
    ('{"a":null}', "{}"),
    ("[1,2]", "[2,1]"),
    ("[]", "{}"),
    ("null", "null"),
    ('"a"', "1"),
    ('{"a":1,"a":1}', '{"a":1}'),
    ('"\\ud800"', '"\\ud800"'),
    ('"\\ud800"', '"x"'),
    ('"\\ud800x"', '"x"'),
    ('"\\ud800\\ud800"', '"x"'),
    ('"x\\udc00\\ud800"', '"x"'),
    ('"x"', '"\\udfff"'),
    ('{"\\ud800":1,"a":1,"a":2}', "{}"),
    ('{"a":1,"a":2,"\\udc00":1}', "{}"),
]


BRIDGE_ADDRESSES: list[str | None] = [
    # HueBridgeAddressTests
    "192.168.1.20",
    " 192.168.1.20 ",
    "127.0.0.1:8443",
    "bridge.local",
    "Bridge.Local:443",
    "fe80::1",
    "[fe80::1]:8443",
    "not a host",
    "192.168.1.20/api",
    "https://192.168.1.20",
    "user@192.168.1.20",
    "192.168.1.20:99999",
    "192.168.1.20?debug=1",
    "192.168.1.20#top",
    "[fe80::1",
    None,
    "",
    "   ",
    # IPv4 forms that Uri reads
    "192.168.1",
    "192.168",
    "3232235796",
    "0x7f.1",
    "0177.0.0.1",
    "0x7F000001",
    "123",
    "0",
    "256.1.1.1",
    "1.2.3.4.5",
    "192.168.1.20.",
    "1.2.3.",
    "0x",
    "0x.0x.0x.0x",
    "08.1.1.1",
    "1.2.3.0400",
    "1.2.3.0377",
    "4294967295",
    "4294967296",
    "1.16777215",
    "1.16777216",
    "1.2.65535",
    "1.2.65536",
    "0xFFFFFFFF",
    "0x100000000",
    "00000000000000000001.1.1.1",
    "1..1.1",
    "+1.1.1.1",
    "\u0661.1.1.1",
    # ports
    "192.168.1.20:",
    "192.168.1.20:0",
    "192.168.1.20:065535",
    "192.168.1.20:00000000000000000080",
    "192.168.1.20:+1",
    "192.168.1.20:65535",
    "192.168.1.20:65536",
    "192.168.1.20: 80",
    "192.168.1.20 :80",
    "192.168.1.20:\u0668\u0660",
    "bridge:80:80",
    "bridge:abc",
    ":80",
    ":",
    # host names
    "BRIDGE",
    "bridge_1.local",
    "_bridge",
    "-bridge.local",
    "bridge-.local",
    "bridge..local",
    ".bridge",
    "bridge.local.",
    "bridge.local..",
    "a" * 63 + ".local",
    "a" * 64 + ".local",
    ".".join(["a" * 63] * 4),
    ".".join(["a" * 63] * 5),
    "1.2.3.a",
    "1a.2.3.4",
    "0x1g.1.1.1",
    "bridge%1",
    "bridge!",
    "bridge$",
    "bridge=1",
    "bridge,1",
    "bridge;1",
    "bridge'1",
    "bridge(1)",
    "bridge~1",
    "bridge&1",
    "bridge*1",
    "bridge+1",
    'bridge"1',
    "bridge<1>",
    "bridge\\1",
    "bridge^1",
    "bridge{1}",
    "bridge|1",
    "bridge`1",
    "bridge\t1",
    "bri dge",
    "bridge#",
    "bridge?",
    "bridge/",
    "@bridge",
    "bridge@",
    "user:pass@bridge",
    "[",
    "]",
    "bridge%20x",
    "%41bridge",
    "bridge%2Elocal",
    "bridge\u0000",
    "bridge\u007f",
    # IPv6
    "::1",
    "[::1]",
    "[::1]:443",
    "FE80::1",
    "fe80:0:0:0:0:0:0:1",
    "fe80:0000:0000:0000:0000:0000:0000:0001",
    "2001:db8:0:0:1:0:0:1",
    "fe80::1%eth0",
    "[fe80::1%eth0]:8443",
    "[fe80::1%25eth0]",
    "fe80::1%",
    "fe80::1%1",
    "::ffff:192.168.1.20",
    "[::ffff:192.168.1.20]",
    "::192.168.1.20",
    "fe80::1::2",
    "[fe80::1]x",
    "[fe80::1]:",
    "[fe80::1]:99999",
    "[]",
    "[192.168.1.20]",
    "[bridge]",
    "[::1]]",
    "[[::1]]",
    "1:2:3:4:5:6:7:8:9",
    "1:2:3:4:5:6:7::8",
    # Unicode and IDN
    "b\u00fccher.local",
    "B\u00dcCHER.LOCAL",
    "xn--bcher-kva.local",
    "stra\u00dfe.local",
    "\u0130stanbul.local",
    "caf\u00e9",
    "bridge\u3000",
    "\u3000bridge",
    "brid\u00adge.local",
    "\ufb01.local",
    "\u0661\u0662\u0663",
    "bridge\u200b.local",
    "\U0001f600.local",
    "bridge.\uff4c\uff4f\uff43\uff41\uff4c",
    "\u03a3\u03b1\u03c2.local",
    "\u0431\u0440\u0438\u0434\u0436.local",
    "xn--.local",
    "xn--zz.local",
    "\u00e9" * 63 + ".local",
    # paths, user info and other Uri details
    "bridge/.",
    "bridge/..",
    "bridge/./",
    "bridge/a/..",
    "bridge/a/../",
    "bridge/%2e",
    "bridge/%2E%2E",
    "bridge/.%2e",
    "bridge//",
    "bridge/%2F",
    "bridge/./..",
    "bridge/.\\",
    "bridge\\.",
    "bridge/;",
    "bridge/...",
    "bridge/a./..",
    "bridge/ ./",
    "bridge/%2e%2E/%2E",
    "bridge/%252e",
    "@bridge",
    "@@bridge",
    "@bridge:8443",
    "@[::1]",
    "@1.2.3.4",
    ":@bridge",
    "@",
    "a@b@c",
    "loopback",
    "LOOPBACK",
    "localhost",
    "LocalHost",
    "Loopback.local",
    "localhost.",
    "1.2.65536:80",
    "1.2.3.4x",
    "0x7f.0x0.0x0.0x1",
    "0X7F.1",
    "01.02.03.04",
    "255.255.255.255",
    "1.2.3.4.",
    "a" * 250 + ".b",
    "a" * 254 + ".b",
    "a" * 255 + ".b",
    "a-b_c.d_e-f",
    "a.-b.c",
    "a.b.-c",
    "a._b",
    "1-a.local",
    "\u0080bridge",
    "bri\u00a0dge",
    # IPv6 details
    "[fe80::1/64]",
    "[fe80::1/6]",
    "[fe80::1/123]",
    "fe80::1/64",
    "[fe80::1%eth0/64]",
    "[fe80::1%a b]",
    "fe80::1%a b",
    "[fe80::1%]",
    "fe80::1%%",
    "fe80::1%eth0:8443",
    "[fe80::1%eth0]",
    "[fe80::1%25]",
    "[::1.2.3.04]",
    "::1.2.3.04",
    "[::1.2.3.4]",
    "::1.2.3.4",
    "[1.2.3.4]",
    "fe80::5efe:192.168.1.1",
    "::0.0.1.2",
    "::ffff:0:1",
    "1::",
    "::",
    "[::]",
    "0:0:0:0:0:0:0:0",
    "1:0:0:2:0:0:0:3",
    "1:0:0:0:2:0:0:3",
    "::7f00:1",
    "::ffff:7f00:1",
    "fe80::00001",
    ":1::",
    "1:2:3:4:5:6:7::",
    "1:2:3:4:5:6::7:8",
    "::ffff:1.2.3.4.5",
    "[::1]:0x10",
    "[::1]:8443/",
    "[::1%\u00e9]",
    # Unicode case and IDN details
    "\u00c0\u00c9\u00ce.local",
    "\u0100\u0112\u012a.local",
    "\u0391\u0392\u0393.local",
    "A\u03a3.local",
    "\u01c5.local",
    "\u216b.local",
    "\uff21.local",
    "\u00b5.local",
    "\u00ff.local",
    "\u0178.local",
    "\u212a.local",
    "\u1e9e.local",
    "\U00010400.local",
    "A\U00010400.local",
    "bridge\u200e.local",
    "\u200ebridge.local",
    "bridge\u202e.local",
    "bridge\u2010.local",
    "cafe\u0301.local",
    "xn--b\u00fccher.local",
    "XN--b\u00fccher.local",
    "a\u05d0.local",
    "\u05d01.local",
    "\u05d0\u05d1.local",
    "\u05d0a\u05d1.local",
    "\u0661a.local",
    "\u0627\u0644.local",
    "\u3002bridge",
    "bridge\u3002local",
    "bridge\uff0elocal",
    "bridge\uff61local",
    "bridge\u3002",
    "a\u3002\u3002b",
    "\u65e5\u672c\u8a9e" * 7,
    "\u65e5\u672c\u8a9e" * 3,
    "\U0001f600" * 15,
    "\U0001f600" * 14,
    "\u00e9" * 60,
    "\u00e9" * 59,
    "\u00e9" * 20 + ".local",
    ".".join(["\u00e9" * 20] * 13),
    ".".join(["b\u00fccher"] * 40),
    "B\u00fccher." + "a" * 64,
]

PAIRING_RESPONSES: list[tuple[int, str]] = [
    (200, '[{"success":{"username":"new-app-key","clientkey":"00AA"}}]'),
    (200, '[{"error":{"type":101,"address":"","description":"link button not pressed"}}]'),
    (503, "<html><body>busy</body></html>"),
    (200, ""),
    (200, "[]"),
    (200, "[1]"),
    (200, '[{"success":{"username":42}}]'),
    (503, "<html>\n  <body>busy</body>\n</html>"),
    (502, " "),
    (200, '[{"error":{"type":7,"address":"/devicetype","description":"invalid value for parameter, devicetype"}}]'),
    (200, '[{"error":{"type":7}}]'),
    (200, '[{"error":{"description":""}}]'),
    (200, '[{"error":{"description":" "}}]'),
    (200, '[{"error":{"type":"101"}}]'),
    (200, '[{"error":{"type":101.0}}]'),
    (200, '[{"error":{"type":1.01e2}}]'),
    (200, '[{"error":{"type":2147483648}}]'),
    (200, '[{"error":{"type":-1,"description":null}}]'),
    (200, '[{"error":{"type":101,"type":7}}]'),
    (200, '[{"error":null}]'),
    (200, '[{"error":"link button not pressed"}]'),
    (200, '[{"success":{"username":""}}]'),
    (200, '[{"success":{"username":" "}}]'),
    (200, '[{"success":{"username":null}},{"success":{"username":"second"}}]'),
    (200, '[{"success":{"username":"key"},"error":{"type":101}}]'),
    (200, '[{"error":{"type":101},"success":{"username":"key"}}]'),
    (200, '[{"success":{"username":"a","username":"b"}}]'),
    (200, '[{"success":{"USERNAME":"key"}}]'),
    (200, '[{"SUCCESS":{"username":"key"}}]'),
    (200, '[{"success":"key"}]'),
    (200, '[{"success":{"username":"\\ud800"}}]'),
    (200, '[{"success":{"username":"k\\u00e9y"}}]'),
    (200, '[{"success":{"username":"key"}},{"error":{"type":101}}]'),
    (200, "[null]"),
    (200, "null"),
    (200, '{"success":{"username":"key"}}'),
    (200, '[{"success":{"username":"key"}}] trailing'),
    (200, '[{"success":{"username":"key"}}]\u0000'),
    (200, '[{"success":{"username":"key"}}]\n'),
    (200, '[{"success":{"username":"key"},}]'),
    (200, "[" * 70 + "]" * 70),
    (200, "x" * 130),
    (200, "a\u3000b\u001cc\u0085d"),
    (200, "\U0001f600" * 70),
    (200, "a" + "\U0001f600" * 70),
    (200, "a" * 119 + "\U0001f600"),
    (200, "a" * 120),
    (200, "a" * 121),
    (200, "\t\n "),
    (404, '[{"error":{"type":3,"address":"/x","description":"resource, /x, not available"}}]'),
    (207, '[{"error":{"type":101}}]'),
]

LIGHTS = (
    '{"errors":[],"data":[{"id":"l1","type":"light","metadata":{"name":"Ceiling"},"on":{"on":true}},'
    '{"id":"l2","type":"light","metadata":{"name":"Floor lamp"},"on":{"on":false}}]}'
)
ROOMS = (
    '{"errors":[],"data":[{"id":"r1","type":"room","metadata":{"name":"Living room"},'
    '"services":[{"rid":"g1","rtype":"grouped_light"}]}]}'
)
ZONES = (
    '{"errors":[],"data":[{"id":"z1","type":"zone","metadata":{"name":"Downstairs"},'
    '"services":[{"rid":"g2","rtype":"grouped_light"}]}]}'
)
MAY_NOT_HAVE_EFFECT = (
    '{"data":[{"rid":"l1","rtype":"light"}],"errors":[{"description":"device (light) has communication issues, '
    'command (.on.on) may not have effect"}]}'
)
PUT_OK = '{"errors":[],"data":[{"rid":"l1","rtype":"light"}]}'


def answer(status: int, body: str, reason: str | None = None, content_type: str | None = None) -> dict[str, object]:
    return {"status": status, "body": body, "reason": reason, "contentType": content_type}


def raw_answer(content: bytes, content_type: str | None = None, status: int = 200) -> dict[str, object]:
    """An answer whose body is these bytes rather than UTF-8 text."""
    return {**answer(status, "", None, content_type), "bodyBase64": base64.b64encode(content).decode()}


def light(on: bool | None = True) -> dict[str, object]:
    return {"kind": "Light", "id": "l1", "on": on}


def hue_client_cases() -> list[dict[str, object]]:
    """Name → (operation, answers in order, the last repeated; a target and on for setOn)."""
    cases: dict[str, tuple[str, list[dict[str, object]], dict[str, object] | None]] = {
        "lights": ("lights", [answer(200, LIGHTS)], None),
        "groups": ("groups", [answer(200, ROOMS), answer(200, ZONES)], None),
        "lightStates": ("lightStates", [answer(200, LIGHTS)], None),
        "groupedLightStates": (
            "groupedLightStates",
            [answer(200, '{"data":[{"id":"g1","on":{"on":true}},{"id":"g2","on":{"on":false}}]}')],
            None,
        ),
        "ping": ("ping", [answer(200, '{"errors":[],"data":[{"id":"b1","type":"bridge"}]}')], None),
        "setOnGroup": ("setOn", [answer(200, PUT_OK)], {"kind": "GroupedLight", "id": "g1", "on": True}),
        "setOnLightOff": ("setOn", [answer(200, PUT_OK)], {"kind": "Light", "id": "l2", "on": False}),
        "setOnEscaped": ("setOn", [answer(200, PUT_OK)], {"kind": "Light", "id": "l 1/\u00fc?#%+~_.-", "on": True}),
        "setOnDotDot": ("setOn", [answer(200, PUT_OK)], {"kind": "Light", "id": "..", "on": True}),
        "setOnDot": ("setOn", [answer(200, PUT_OK)], {"kind": "Light", "id": ".", "on": True}),
        "setOnEmptyId": ("setOn", [answer(200, PUT_OK)], {"kind": "Light", "id": "", "on": True}),
        "setOnEmoji": ("setOn", [answer(200, PUT_OK)], {"kind": "GroupedLight", "id": "\U0001f600", "on": False}),
        "forbidden": ("lights", [answer(403, '{"errors":[{"description":"unauthorized user"}]}')], None),
        "unauthorizedNoBody": ("lights", [answer(401, "")], None),
        "forbiddenTwoErrors": ("setOn", [answer(403, '{"errors":[{"description":"a"},{}]}')], light()),
        "busy": ("lights", [answer(503, "{}")], None),
        "busyReason": ("lights", [answer(503, "", "Busy")], None),
        "tooMany": ("setOn", [answer(429, '{"errors":[{"description":"rate limit"}]}')], light()),
        "serverError": ("lights", [answer(500, "<html>oops</html>")], None),
        "badGateway": ("lights", [answer(502, "")], None),
        "gatewayTimeout": ("lights", [answer(504, "")], None),
        "notFound": ("setOn", [answer(404, '{"errors":[{"description":"not found"}],"data":[]}')], light()),
        "unknownStatus": ("lights", [answer(599, "")], None),
        "unknownStatusReason": ("lights", [answer(599, "", "Odd")], None),
        "redirect": ("lights", [answer(300, "")], None),
        "teapot": ("lights", [answer(418, "")], None),
        "multiStatusPut": ("setOn", [answer(207, MAY_NOT_HAVE_EFFECT)], light()),
        "multiStatusGet": ("lights", [answer(207, MAY_NOT_HAVE_EFFECT)], None),
        "okWithErrorsGet": ("lightStates", [answer(200, '{"errors":[{"description":"x"}],"data":[]}')], None),
        "noDescription": ("setOn", [answer(200, '{"errors":[{}],"data":[]}')], light(False)),
        "blankDescription": ("setOn", [answer(200, '{"errors":[{"description":" \\u3000"}],"data":[]}')], light()),
        "twoDescriptions": ("setOn", [answer(200, '{"errors":[{"description":"a"},{"description":"b"}]}')], light()),
        "nullError": ("setOn", [answer(200, '{"errors":[null],"data":[]}')], light()),
        "errorsString": ("setOn", [answer(200, '{"errors":"x"}')], light()),
        "errorsStringGet": ("lights", [answer(200, '{"errors":"x","data":[]}')], None),
        "errorsEmpty": ("setOn", [answer(200, '{"errors":[]}')], light()),
        "plainOkPut": ("setOn", [answer(200, "OK")], light()),
        "plainOkGet": ("lights", [answer(200, "OK")], None),
        "emptyPut": ("setOn", [answer(204, "")], light()),
        "emptyGet": ("lights", [answer(200, "")], None),
        "nullBody": ("lights", [answer(200, "null")], None),
        "arrayBody": ("lights", [answer(200, "[]")], None),
        "dataNull": ("lights", [answer(200, '{"data":null}')], None),
        "dataMissing": ("lightStates", [answer(200, "{}")], None),
        "dataNullItem": ("lights", [answer(200, '{"data":[null]}')], None),
        "dataNullItemStates": ("lightStates", [answer(200, '{"data":[null]}')], None),
        "dataNotObject": ("lights", [answer(200, '{"data":[1]}')], None),
        "onString": ("lights", [answer(200, '{"data":[{"id":"l1","on":{"on":"true"}}]}')], None),
        "onNull": ("lights", [answer(200, '{"data":[{"id":"l1","on":{"on":null}}]}')], None),
        "onEmpty": ("lightStates", [answer(200, '{"data":[{"id":"l1","on":{}}]}')], None),
        "onObjectNull": ("lightStates", [answer(200, '{"data":[{"id":"l1","on":null}]}')], None),
        "onNumber": ("lights", [answer(200, '{"data":[{"id":"l1","on":{"on":1}}]}')], None),
        "idNumber": ("lights", [answer(200, '{"data":[{"id":1}]}')], None),
        "idNull": ("lights", [answer(200, '{"data":[{"id":null,"on":{"on":true}}]}')], None),
        "idNullStates": ("lightStates", [answer(200, '{"data":[{"id":null,"on":{"on":true}}]}')], None),
        "idMissing": ("lightStates", [answer(200, '{"data":[{"on":{"on":true}}]}')], None),
        "idMissingLights": ("lights", [answer(200, '{"data":[{"on":{"on":true}}]}')], None),
        "caseInsensitive": (
            "lights",
            [answer(200, '{"DATA":[{"ID":"l1","Metadata":{"NAME":"x"},"ON":{"ON":true}}],"Errors":[]}')],
            None,
        ),
        "turkishI": ("lights", [answer(200, '{"data":[{"\\u0130d":"l1"}]}')], None),
        "duplicateProperty": ("lights", [answer(200, '{"data":[{"id":"l1","id":"l2","on":{"on":true}}]}')], None),
        "duplicateData": ("lights", [answer(200, '{"data":[{"id":"l1"}],"data":[{"id":"l2"}]}')], None),
        "duplicateIds": (
            "lightStates",
            [answer(200, '{"data":[{"id":"l1","on":{"on":true}},{"id":"L1","on":{"on":false}}]}')],
            None,
        ),
        "duplicateIdsLights": (
            "lights",
            [answer(200, '{"data":[{"id":"l1","on":{"on":true}},{"id":"L1","on":{"on":false}}]}')],
            None,
        ),
        "duplicateIdsTurkish": (
            "lightStates",
            [answer(200, '{"data":[{"id":"\\u0131","on":{"on":true}},{"id":"I","on":{"on":false}}]}')],
            None,
        ),
        "metadataNull": ("lights", [answer(200, '{"data":[{"id":"l1","metadata":null}]}')], None),
        "nameNull": ("lights", [answer(200, '{"data":[{"id":"l1","metadata":{"name":null}}]}')], None),
        "nameNumber": ("lights", [answer(200, '{"data":[{"id":"l1","metadata":{"name":1}}]}')], None),
        "metadataArray": ("lights", [answer(200, '{"data":[{"id":"l1","metadata":[]}]}')], None),
        "twoGroupedLights": (
            "groups",
            [
                answer(
                    200,
                    '{"data":[{"id":"r1","services":[{"rid":"g1","rtype":"grouped_light"},'
                    '{"rid":"g2","rtype":"grouped_light"}]}]}',
                ),
                answer(200, '{"data":[]}'),
            ],
            None,
        ),
        "rtypeCase": (
            "groups",
            [
                answer(200, '{"data":[{"id":"r1","services":[{"rid":"g1","rtype":"GROUPED_LIGHT"}]}]}'),
                answer(200, "{}"),
            ],
            None,
        ),
        "servicesNullItem": (
            "groups",
            [answer(200, '{"data":[{"id":"r1","services":[null]}]}'), answer(200, "{}")],
            None,
        ),
        "servicesNullItemLater": (
            "groups",
            [
                answer(200, '{"data":[{"id":"r1","services":[{"rid":"g1","rtype":"grouped_light"},null]}]}'),
                answer(200, "{}"),
            ],
            None,
        ),
        "serviceNoRid": (
            "groups",
            [answer(200, '{"data":[{"id":"r1","services":[{"rtype":"grouped_light"}]}]}'), answer(200, "{}")],
            None,
        ),
        "serviceNullRid": (
            "groups",
            [
                answer(200, '{"data":[{"id":"r1","services":[{"rid":null,"rtype":"grouped_light"}]}]}'),
                answer(200, "{}"),
            ],
            None,
        ),
        "serviceNullRtype": (
            "groups",
            [answer(200, '{"data":[{"id":"r1","services":[{"rid":"g1","rtype":null}]}]}'), answer(200, "{}")],
            None,
        ),
        "servicesNull": ("groups", [answer(200, '{"data":[{"id":"r1","services":null}]}'), answer(200, "{}")], None),
        "zoneFails": ("groups", [answer(200, ROOMS), answer(503, "")], None),
        "tooDeep": ("lights", [answer(200, '{"data":[],"x":' + "[" * 70 + "]" * 70 + "}")], None),
        "deepOk": ("lights", [answer(200, '{"data":[],"x":' + "[" * 62 + "]" * 62 + "}")], None),
        "nan": ("lights", [answer(200, '{"data":[],"x":NaN}')], None),
        "comment": ("lights", [answer(200, '{"data":[]/* c */}')], None),
        "trailingComma": ("lights", [answer(200, '{"data":[],}')], None),
        "trailingText": ("lights", [answer(200, '{"data":[]} x')], None),
        "loneSurrogateId": ("lights", [answer(200, '{"data":[{"id":"\\ud800"}]}')], None),
        "loneSurrogateIgnored": ("lights", [answer(200, '{"data":[{"id":"l1","type":"\\ud800"}]}')], None),
        "loneSurrogateKey": ("lights", [answer(200, '{"data":[{"id":"l1","\\ud800":1}]}')], None),
        "loneSurrogateNestedKey": ("lights", [answer(200, '{"data":[{"id":"l1","x":{"\\ud800":1}}]}')], None),
        "loneSurrogateDescription": ("setOn", [answer(200, '{"errors":[{"description":"\\ud800"}]}')], light()),
        "unicode": (
            "lights",
            [answer(200, '{"data":[{"id":"l\\u00e9","metadata":{"name":"K\\u00fcche \U0001f600"}}]}')],
            None,
        ),
        "latin1Charset": ("lights", [answer(200, LIGHTS, content_type="application/json; charset=iso-8859-1")], None),
        "unknownCharset": ("lights", [answer(200, LIGHTS, content_type="application/json; charset=x-unknown")], None),
        "numberHandling": ("lights", [answer(200, '{"data":[{"id":"l1","on":{"on":true},"x":"1"}]}')], None),
        "utf8Bom": ("lights", [raw_answer(b"\xef\xbb\xbf" + LIGHTS.encode())], None),
        "utf8BomCharset": (
            "lights",
            [raw_answer(b"\xef\xbb\xbf" + LIGHTS.encode(), "application/json; charset=utf-8")],
            None,
        ),
        "utf16BomNoCharset": ("lights", [raw_answer(b"\xff\xfe" + LIGHTS.encode("utf-16-le"))], None),
        "utf16BeBomNoCharset": ("lights", [raw_answer(b"\xfe\xff" + LIGHTS.encode("utf-16-be"))], None),
        "utf32BomNoCharset": ("lights", [raw_answer(b"\xff\xfe\x00\x00" + LIGHTS.encode("utf-32-le"))], None),
        "utf16Charset": (
            "lights",
            [raw_answer(b"\xff\xfe" + LIGHTS.encode("utf-16-le"), "application/json; charset=utf-16")],
            None,
        ),
        "utf16CharsetNoBom": (
            "lights",
            [raw_answer(LIGHTS.encode("utf-16-le"), "application/json; charset=utf-16")],
            None,
        ),
        "utf16BomUtf8Charset": (
            "lights",
            [raw_answer(b"\xff\xfe" + LIGHTS.encode("utf-16-le"), "application/json; charset=utf-8")],
            None,
        ),
        "quotedCharset": ("lights", [raw_answer(LIGHTS.encode(), 'application/json; charset="utf-8"')], None),
        "asciiHighBytes": (
            "lights",
            [
                raw_answer(
                    b'{"data":[{"id":"l\xe9","metadata":{"name":"\x80\xff"}}]}', "application/json; charset=us-ascii"
                )
            ],
            None,
        ),
        "latin1Bytes": (
            "lights",
            [
                raw_answer(
                    b'{"data":[{"id":"l\xe9","metadata":{"name":"\x80\xff"}}]}', "application/json; charset=latin1"
                )
            ],
            None,
        ),
        "invalidUtf8": (
            "lights",
            [raw_answer(b'{"data":[{"id":"l\xe9\xc3","metadata":{"name":"\xed\xa0\x80"}}]}')],
            None,
        ),
        "windows1252": ("lights", [raw_answer(LIGHTS.encode(), "application/json; charset=windows-1252")], None),
        "utf7": ("lights", [raw_answer(LIGHTS.encode(), "application/json; charset=utf-7")], None),
        "utf8NoHyphen": ("lights", [raw_answer(LIGHTS.encode(), "application/json; charset=utf8")], None),
        "invalidCharsetError": ("lights", [raw_answer(b"{}", "application/json; charset=x-unknown", 503)], None),
    }
    return [
        {"name": label, "operation": operation, "answers": answers, "target": target}
        for label, (operation, answers, target) in cases.items()
    ]


FINGERPRINTS = [
    "aa:bb:cc",
    " AB CD ",
    "\u3000ab\u3000",
    "\ufb00",
    "\u0131",
    "\u00df",
    "a:b c\td",
    "\u017f",
]

IP_ADDRESSES = [
    "192.168.1.20",
    "192.168.1",
    "0x7f.1",
    "1.2.3.4/",
    "1.2.3.4:80",
    " 1.2.3.4",
    "",
    "fe80::1",
    "fe80::1%eth0",
    "fe80::1%",
    "fe80::1%a/b",
    "fe80::1%a]b",
    "[fe80::1]",
    "[fe80::1]:80",
    "[fe80::1]:0x50",
    "[fe80::1]:8x",
    "[fe80::1]x",
    "[fe80::1",
    "fe80::1]",
    "[fe80::1%eth0]:443",
    "[]",
    "[::]",
    "::",
    ":",
    ":::",
    "1::2::3",
    "::ffff:1.2.3.4",
    "::1.2.3.04",
    "fe80::1/64",
    "1:2:3:4:5:6:7:8",
    "1:2:3:4:5:6:7:8:9",
    "\u0661.1.1.1",
]

# Hue lights and groups as the resolver gets them: ids, names and kinds, any of them null where .NET allows it.
type Resource = dict[str, str | None]

RESOLVER_LIGHTS: list[Resource] = [
    {"id": "l1", "name": "Ceiling"},
    {"id": "l2", "name": "Floor lamp"},
    {"id": "l3", "name": "Spot"},
    {"id": "l4", "name": "Spot"},
]
RESOLVER_GROUPS: list[Resource] = [
    {"id": "r1", "name": "Kitchen", "kind": "room", "grouped": "g1"},
    {"id": "z1", "name": "Kitchen", "kind": "zone", "grouped": "g2"},
    {"id": "r2", "name": "Hallway", "kind": "room", "grouped": "g3"},
    {"id": "r3", "name": "Empty", "kind": "room", "grouped": None},
]
SORTING_LIGHTS: list[Resource] = [
    {"id": f"s{i}", "name": name}
    for i, name in enumerate(
        [
            "beta",
            "Alpha",
            "alpha",
            "\u00c4rger",
            "Zulu",
            "_x",
            "\u0131",
            "I",
            "i",
            "\u00e9",
            "E",
            "Spot",
            "spot",
            "Spot",
        ]
    )
]


def resolver_cases() -> list[dict[str, object]]:
    def case(
        label: str,
        lights: list[str],
        groups: list[str],
        hue_lights: list[Resource] = RESOLVER_LIGHTS,
        hue_groups: list[Resource] = RESOLVER_GROUPS,
        name: str | None = "Hall",
    ) -> dict[str, object]:
        return {
            "label": label,
            "name": name,
            "deviceId": " 1000ABCDEF ",
            "lights": lights,
            "groups": groups,
            "hueLights": hue_lights,
            "hueGroups": hue_groups,
        }

    return [
        case("namesAndIds", ["ceiling", "l2", "Ceiling"], ["hallway", "zone:kitchen"], name=None),
        case("unknownAndAmbiguous", ["Spot", "Nope"], ["Kitchen", "Empty", "Garage"]),
        case("trimmedAndPrefixed", ["  floor LAMP\u3000"], [" ROOM: Kitchen ", "zone:", "room:zone:x", "Zone:KITCHEN"]),
        case("duplicates", ["Ceiling", "l1", "L1", "ceiling"], ["r2", "Hallway", "room:hallway", "g3"]),
        case("knownListOrder", ["nothing"], ["nothing"], hue_lights=SORTING_LIGHTS),
        case("noLights", ["x"], ["y"], hue_lights=[], hue_groups=[]),
        case("paddedName", ["Spot light"], [], hue_lights=[{"id": "x1", "name": "  Spot light "}]),
        case("nullName", ["x"], [], hue_lights=[{"id": "n1", "name": None}]),
        case("nullNameMatchedById", ["N1"], [], hue_lights=[{"id": "n1", "name": None}]),
        case("nullId", ["Lamp"], [], hue_lights=[{"id": None, "name": "Lamp"}]),
        case("nullIdUnknown", ["x"], [], hue_lights=[{"id": None, "name": "Lamp"}, {"id": None, "name": "Lamp"}]),
        case(
            "nullGroupName",
            [],
            ["r9", "x"],
            hue_groups=[{"id": "r9", "name": None, "kind": "room", "grouped": "g9"}, *RESOLVER_GROUPS],
        ),
        case("turkishI", ["\u0131d", "ID"], [], hue_lights=[{"id": "id", "name": "x"}, {"id": "\u0131d", "name": "y"}]),
        case("nameEqualsOtherId", ["l2"], [], hue_lights=[{"id": "l1", "name": "l2"}, {"id": "l2", "name": "Desk"}]),
        case("sameIdForALightAndAGroup", ["g1"], ["r1"], hue_lights=[{"id": "g1", "name": "Lamp"}]),
        case(
            "nullIdsAmbiguous",
            ["Lamp"],
            [],
            hue_lights=[{"id": "b", "name": "Lamp"}, {"id": None, "name": "Lamp"}, {"id": "A", "name": "Lamp"}],
        ),
        case(
            "blankNames",
            [" "],
            ["room:"],
            hue_lights=[{"id": "x1", "name": "  "}],
            hue_groups=[{"id": "r1", "name": "\u3000", "kind": "room", "grouped": "g1"}],
        ),
        # Code point order, not UTF-16 order: characters outside the Basic Multilingual Plane sort after U+FF21.
        case(
            "knownListCodePointOrder",
            ["nothing"],
            [],
            hue_lights=[
                {"id": f"u{i}", "name": name}
                for i, name in enumerate(["\uff21", "\U0001d400", "\ue000", "\U00010428", "\U00010400", "\u00ff", "a"])
            ],
        ),
    ]


# StringComparer.OrdinalIgnoreCase.Compare, which orders the names that the resolver's messages list. The strings go to
# .NET as UTF-16 code units, so that lone surrogates survive the JSON file.
COMPARISONS: list[tuple[str | None, str | None]] = [
    ("a", "B"),
    ("B", "a"),
    ("a", "A"),
    ("_x", "alpha"),
    ("a", "ab"),
    ("", ""),
    ("", "a"),
    ("abc\x00", "abc"),
    ("\u0131", "i"),
    ("\u017f", "s"),
    ("\u212a", "k"),
    ("\u00df", "ss"),
    ("\u00e9", "E"),
    ("\u00ff", "\u0178"),
    ("\u01c5", "\u01c4"),
    ("\u03c3", "\u03c2"),
    ("\uff21", "\U00010428"),
    ("\U00010428", "\U00010400"),
    ("\U0001d400", "\ue000"),
    ("\U0001d400", "\uffff"),
    ("\U00010400", "\ud801"),
    ("\ud801", "\ud801\udc00"),
    ("\ud801x", "\ud801y"),
    ("\udc00", "\ue000"),
    ("\udc00", "\U00010000"),
    ("\udc00\ud801", "\udc00"),
    ("x\U00010428y", "X\U00010400Z"),
    (None, "a"),
    ("a", None),
    (None, None),
]


def utf16_units(value: str | None) -> list[int] | None:
    if value is None:
        return None
    data = value.encode("utf-16-le", "surrogatepass")
    return [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]


def states(*resources: tuple[str, bool]) -> str:
    """A CLIP v2 answer with the on state of each resource."""
    data = [{"id": resource, "on": {"on": on}} for resource, on in resources]
    return json.dumps({"errors": [], "data": data}, separators=(",", ":"))


def toggle_cases() -> list[dict[str, object]]:
    """``HueToggler.ToggleAsync`` on a client with canned answers: name → (targets as [kind, id, name], the answers in
    order, the last repeated)."""
    ceiling, desk, hall = ("Light", "l1", "Ceiling"), ("Light", "l2", "Desk"), ("GroupedLight", "g1", "Hall")
    off = answer(200, states(("l1", False), ("l2", False)))
    rejected = answer(403, '{"errors":[{"description":"unauthorized user"}]}')
    cases: dict[str, tuple[list[tuple[str, str | None, str | None]], list[dict[str, object]]]] = {
        "allOff": ([ceiling, desk, hall], [off, answer(200, states(("g1", False))), answer(200, PUT_OK)]),
        "anyOn": (
            [ceiling, desk, hall],
            [answer(200, states(("l1", False), ("l2", True))), answer(200, states(("g1", False))), answer(200, PUT_OK)],
        ),
        "allOn": ([ceiling, hall], [answer(200, states(("l1", True))), answer(200, states(("g1", True)))]),
        "stateIgnoresCase": ([("Light", "L1", "Ceiling")], [answer(200, states(("l1", True))), answer(200, PUT_OK)]),
        "unknownState": (
            [ceiling, ("Light", "gone", "Gone")],
            [answer(200, states(("l1", True))), answer(200, PUT_OK)],
        ),
        "duplicateTargets": ([ceiling, ceiling], [off, answer(200, PUT_OK)]),
        "noTargets": ([], [answer(200, PUT_OK)]),
        "onlyGroups": ([hall], [answer(200, states(("g1", False))), answer(200, PUT_OK)]),
        "retriedWrite": ([ceiling, desk], [off, answer(503, "{}"), answer(200, PUT_OK)]),
        "failedWrite": ([ceiling, desk], [off, answer(503, "{}"), answer(503, "{}"), answer(200, PUT_OK)]),
        "rateLimitedWrite": (
            [ceiling],
            [off, answer(429, '{"errors":[{"description":"rate limit"}]}'), answer(200, PUT_OK)],
        ),
        "rejectedWrite": (
            [ceiling, desk],
            [off, answer(404, '{"errors":[{"description":"not found"}],"data":[]}'), answer(200, PUT_OK)],
        ),
        "multiStatusWrite": ([ceiling, desk], [off, answer(207, MAY_NOT_HAVE_EFFECT), answer(200, PUT_OK)]),
        "keyRejectedWrite": ([ceiling, desk], [off, rejected]),
        "retriedRead": ([ceiling], [answer(503, ""), off, answer(200, PUT_OK)]),
        "malformedReadRetried": (
            [ceiling],
            [answer(200, '{"data":[{"id":"l1","on":{"on":"true"}}]}'), off, answer(200, PUT_OK)],
        ),
        "failedRead": ([ceiling], [answer(503, "")]),
        "rejectedRead": ([ceiling], [rejected]),
        "failedGroupRead": ([ceiling, hall], [off, answer(502, "")]),
        "nullTargetId": ([("Light", None, "Lamp")], [off]),
        "nullTargetIdAfterAnOnTarget": (
            [ceiling, ("Light", None, "Lamp")],
            [answer(200, states(("l1", True))), answer(200, PUT_OK)],
        ),
        "nullTargetName": ([("Light", "l1", None)], [off, answer(503, "")]),
        "nullInTheStates": ([ceiling], [answer(200, '{"data":[null]}')]),
        "duplicateStateIds": (
            [ceiling],
            [answer(200, '{"data":[{"id":"l1","on":{"on":true}},{"id":"L1","on":{"on":false}}]}')],
        ),
    }
    return [{"name": name, "targets": targets, "answers": answers} for name, (targets, answers) in cases.items()]


ENCODINGS = [
    "utf-8",
    "UTF-8",
    "utf8",
    " utf-8",
    "",
    "latin1",
    "Latin1",
    "l1",
    "iso-8859-1",
    "iso_8859-1:1987",
    "cp819",
    "ibm819",
    "csisolatin1",
    "iso-ir-100",
    "iso8859-1",
    "iso_8859-1",
    "us-ascii",
    "ascii",
    "us",
    "cp367",
    "ibm367",
    "csascii",
    "iso-ir-6",
    "iso646-us",
    "iso_646.irv:1991",
    "ansi_x3.4-1968",
    "ansi_x3.4-1986",
    "utf-16",
    "utf-16le",
    "utf-16be",
    "unicode",
    "unicodefffe",
    "ucs-2",
    "iso-10646-ucs-2",
    "csunicode",
    "utf-32",
    "utf-32le",
    "utf-32be",
    "utf-7",
    "csunicode11utf7",
    "unicode-1-1-utf-7",
    "unicode-2-0-utf-7",
    "x-unicode-1-1-utf-7",
    "x-unicode-2-0-utf-7",
    "unicode-1-1-utf-8",
    "unicode-2-0-utf-8",
    "x-unicode-1-1-utf-8",
    "x-unicode-2-0-utf-8",
    "windows-1252",
    "iso-8859-15",
    "cp1252",
    "x-unknown",
]

STATUS_LINES: list[tuple[int, str | None]] = [
    (404, None),
    (404, "Not Found"),
    (404, "Nope"),
    (404, ""),
    (404, " "),
    (599, None),
    (503, "Busy"),
]


def packet_cases() -> dict[str, tuple[str, bytes, str]]:
    """Name → (sender, packet bytes, note)."""
    plain = '{"switches":[{"outlet":0,"switch":"on"}],"relaySeparation":1}'
    cases: dict[str, tuple[str, bytes, str]] = {
        "announcement": ("10.0.0.5", announcement(DEVICE, 771, "x" * 600, "iv"), ""),
        "plain": ("10.0.0.5", announcement(DEVICE, 1, plain, None, encrypted=False), ""),
        "probe": ("10.0.0.6", probe("1000ABCDEF"), ""),
        "goodbye": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", ttl=0), ""),
        "zbbridge": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", instance_prefix="zbbridgeu-"), ""),
        "ihost": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", instance_prefix="ihost-"), ""),
        "eWelink": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", instance_prefix="eWelink_"), ""),
        "EWELINKnoUnderscore": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", instance_prefix="EWELINKx"), ""),
        "dotlessI": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv", instance_prefix="eWeL\u0131nk_"), ""),
        "truncated": ("10.0.0.5", announcement(DEVICE, 1, "abc", "iv")[:-3], ""),
        "short": ("10.0.0.5", bytes(5), ""),
        "loop": ("10.0.0.5", bytes((0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0xC0, 12)), ""),
        "labelType": ("10.0.0.5", bytes((0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0x40)), ""),
        "longName": (
            "10.0.0.5",
            DnsPacketBuilder().answer(".".join(["a" * 63] * 17), DnsRecordType.A, a("10.0.0.1")).build(),
            "",
        ),
        "emojiName": (
            "10.0.0.5",
            DnsPacketBuilder().answer(".".join(["😀" * 15] * 17), DnsRecordType.A, a("10.0.0.1")).build(),
            "",
        ),
        "query": ("10.0.0.5", DnsPacketBuilder(response=False).question("_ewelink._tcp.local", 12).build(), ""),
    }

    def txt_case(*entries: str, record_name: str = f"eWeLink_{DEVICE}._ewelink._tcp.local") -> bytes:
        return DnsPacketBuilder().answer(record_name, DnsRecordType.TXT, txt(*entries)).build()

    for label, seq in {
        "seqNul": "12\0",
        "seqSpaces": " 7 ",
        "seqUnderscore": "1_0",
        "seqNegative": "-1",
        "seqOverflow": "9223372036854775808",
        "seqArabic": "٣",
        "seqEmpty": "",
    }.items():
        cases[label] = ("10.0.0.5", txt_case(f"id={DEVICE}", f"seq={seq}"), "")
    cases["idBlank"] = ("10.0.0.5", txt_case("id= \t", "seq=1"), "")
    cases["idMissingShortInstance"] = ("10.0.0.5", txt_case("seq=1", record_name="eWeLink._ewelink._tcp.local"), "")
    cases["idUpperSpaces"] = ("10.0.0.5", txt_case("ID= 1000ABCDEF\u3000", "seq=1"), "")
    cases["idTurkish"] = ("10.0.0.5", txt_case("id=İD\u0130", "seq=1"), "")
    cases["encryptVariants"] = (
        "10.0.0.5",
        DnsPacketBuilder()
        .answer("eWeLink_1._ewelink._tcp.local", DnsRecordType.TXT, txt("encrypt=TRUE", "seq=1"))
        .answer("eWeLink_2._ewelink._tcp.local", DnsRecordType.TXT, txt("encrypt=yes", "seq=1"))
        .answer("eWeLink_3._ewelink._tcp.local", DnsRecordType.TXT, txt("ENCRYPT=true", "seq=1", "data2=b", "data1=a"))
        .answer("eWeLink_4._ewelink._tcp.LOCAL", DnsRecordType.TXT, txt("seq=1", "data5=x", "data3=c"))
        .build(),
        "",
    )
    cases["addressMatching"] = (
        "10.0.0.5",
        DnsPacketBuilder()
        .answer("eWeLink_1._ewelink._tcp.local", DnsRecordType.TXT, txt("seq=1"))
        .additional("EWELINK_1._EWELINK._TCP.LOCAL", DnsRecordType.SRV, srv(8081, "Host-1.local"))
        .additional("host-1.LOCAL", DnsRecordType.A, a("10.9.8.7"))
        .answer("eWeLink_2._ewelink._tcp.local", DnsRecordType.TXT, txt("seq=1"))
        .additional("eWeLink_2._ewelink._tcp.local", DnsRecordType.SRV, srv(8081, "host-2.local"))
        .additional("host-2.local", DnsRecordType.AAAA, bytes(15) + b"\x01")
        .answer("eWeLink_3._ewelink._tcp.local", DnsRecordType.TXT, txt("seq=1"))
        .additional("eWeLink_3._ewelink._tcp.local", DnsRecordType.SRV, b"\x00\x00\x00\x00\x1f")
        .build(),
        "",
    )
    cases["probeAuthorities"] = (
        "10.0.0.5",
        DnsPacketBuilder(response=False)
        .question("eWeLink_X._ewelink._tcp.local", DnsRecordType.ANY)
        .authority("eWeLink_X._ewelink._tcp.local", DnsRecordType.SRV, srv(8081, "eWeLink_X.local"))
        .authority("EWELINK_x.local", DnsRecordType.A, a("10.0.0.1"))
        .authority("eWeLink_.local", DnsRecordType.A, a("10.0.0.1"))
        .authority("eWeLink_\u0131d.local", DnsRecordType.A, a("10.0.0.1"))
        .authority("other.local", DnsRecordType.A, a("10.0.0.1"))
        .build(),
        "",
    )
    cases["txtOdd"] = (
        "10.0.0.5",
        DnsPacketBuilder()
        .answer(
            f"eWeLink_{DEVICE}._ewelink._tcp.local",
            DnsRecordType.TXT,
            txt("seq=1", "SEQ=2", "=x", "flag", "data1=a=b") + b"\x00" + b"\x02\xc3\x28",
        )
        .build(),
        "",
    )
    cases["badUtf8Label"] = (
        "10.0.0.5",
        bytes((0, 0, 0x84, 0, 0, 0, 0, 1, 0, 0, 0, 0))
        + b"\x04ab\xed\xa0"
        + name("local")
        + bytes((0, 1, 0, 1, 0, 0, 0, 120, 0, 4))
        + a("10.0.0.1"),
        "",
    )
    cases["ptrBadPointer"] = (
        "10.0.0.5",
        DnsPacketBuilder().answer("x.local", DnsRecordType.PTR, b"\xc0\xff").build(),
        "",
    )
    cases["srvShort"] = ("10.0.0.5", DnsPacketBuilder().answer("x.local", DnsRecordType.SRV, b"\x00" * 6).build(), "")
    cases["aWrongLength"] = (
        "10.0.0.5",
        DnsPacketBuilder().answer("x.local", DnsRecordType.A, b"\x01\x02\x03").build(),
        "",
    )
    cases["txtOverrun"] = ("10.0.0.5", DnsPacketBuilder().answer("x.local", DnsRecordType.TXT, b"\x05ab").build(), "")
    return cases


def read_params_cases() -> dict[str, dict[str, object]]:
    data, iv = ewelink.encrypt('{"switch":"on","relaySeparation":1}', KEY, IV)
    cases: dict[str, dict[str, object]] = {
        "ok": {"encrypted": True, "iv": iv, "data": data, "key": KEY},
        "keyWithSpaces": {"encrypted": True, "iv": iv, "data": data, "key": f" \t{KEY}\u3000"},
        "wrongKey": {"encrypted": True, "iv": iv, "data": data, "key": "wrong"},
        "noKey": {"encrypted": True, "iv": iv, "data": data, "key": None},
        "blankKey": {"encrypted": True, "iv": iv, "data": data, "key": " \u2028"},
        "noIv": {"encrypted": True, "iv": None, "data": data, "key": KEY},
        "emptyIv": {"encrypted": True, "iv": "", "data": data, "key": KEY},
        "ivNotBase64": {"encrypted": True, "iv": "not base64!", "data": data, "key": KEY},
        "ivNonAscii": {"encrypted": True, "iv": "ä" + iv[1:], "data": data, "key": KEY},
        "ivSpaces": {"encrypted": True, "iv": " " + iv[:8] + "\n" + iv[8:] + "\t", "data": data, "key": KEY},
        "ivFormFeed": {"encrypted": True, "iv": iv[:8] + "\f" + iv[8:], "data": data, "key": KEY},
        "iv8": {"encrypted": True, "iv": base64.b64encode(bytes(8)).decode(), "data": data, "key": KEY},
        "iv17": {"encrypted": True, "iv": base64.b64encode(bytes(17)).decode(), "data": data, "key": KEY},
        "ivNoPadding": {"encrypted": True, "iv": iv.rstrip("="), "data": data, "key": KEY},
        "ivExtraPadding": {"encrypted": True, "iv": iv + "==", "data": data, "key": KEY},
        "ivTrailingBits": {"encrypted": True, "iv": iv[:-3] + "x==", "data": data, "key": KEY},
        "dataEmpty": {"encrypted": True, "iv": iv, "data": "", "key": KEY},
        "dataSpace": {"encrypted": True, "iv": iv, "data": " ", "key": KEY},
        "dataNotBlock": {"encrypted": True, "iv": iv, "data": base64.b64encode(bytes(20)).decode(), "key": KEY},
        "dataNotBase64": {"encrypted": True, "iv": iv, "data": "abc", "key": KEY},
        "plainEmpty": {"encrypted": False, "iv": None, "data": "", "key": None},
    }
    for i, json_text in enumerate(JSON_TEXTS):
        enc_data, enc_iv = ewelink.encrypt(json_text, KEY, IV)
        cases[f"encryptedJson{i}"] = {"encrypted": True, "iv": enc_iv, "data": enc_data, "key": KEY}
        cases[f"plainJson{i}"] = {"encrypted": False, "iv": None, "data": json_text, "key": KEY}
    for label, raw in {
        "invalidUtf8": b'{"switch":"\xed\xa0\x80"}',
        "overlong": b'{"switch":"\xc0\xaf"}',
        "truncatedUtf8": b'{"switch":"\xe2\x82"}',
        "bom": b'\xef\xbb\xbf{"switch":"on"}',
    }.items():
        enc_data, enc_iv = encrypted_raw(raw)
        cases[label] = {"encrypted": True, "iv": enc_iv, "data": enc_data, "key": KEY}
    return cases


def main() -> None:
    cases = {
        "packets": [
            {"name": label, "sender": sender, "hex": data.hex()} for label, (sender, data, _) in packet_cases().items()
        ],
        "readParams": [{"name": label, **case} for label, case in read_params_cases().items()],
        "detector": [{"name": label, "steps": steps} for label, steps in DETECTOR_SEQUENCES.items()],
        "summarize": [{"params": params} for params in SUMMARIZE],
        "deepEquals": [{"left": left, "right": right} for left, right in DEEP_EQUALS],
        "bridgeAddress": [{"address": address} for address in BRIDGE_ADDRESSES],
        "pairingResponse": [{"status": status, "body": body} for status, body in PAIRING_RESPONSES],
        "hueClient": hue_client_cases(),
        "fingerprint": [{"fingerprint": fingerprint} for fingerprint in FINGERPRINTS],
        "statusLine": [{"status": status, "reason": reason} for status, reason in STATUS_LINES],
        "encodings": [{"name": name} for name in ENCODINGS],
        "ipAddress": [{"address": address} for address in IP_ADDRESSES],
        "targets": resolver_cases(),
        "toggle": toggle_cases(),
        "compareIgnoreCase": [{"left": utf16_units(a), "right": utf16_units(b)} for a, b in COMPARISONS],
        "switchValidation": switch_cases.validation_cases(),
        "switchWrites": switch_cases.write_cases(),
        "yaml": yaml_cases(),
        "wellFormedUri": uri_cases(),
    }
    out = Path(sys.argv[1])
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out}.")


if __name__ == "__main__":
    main()
