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
    }
    out = Path(sys.argv[1])
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out}.")


if __name__ == "__main__":
    main()
