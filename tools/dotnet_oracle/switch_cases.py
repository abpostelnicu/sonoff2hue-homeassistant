"""The switch API's cases for tests/engine/fixtures/dotnet_engine.json, which the .NET app's own code evaluates
(Switches.cs): ``SwitchValidation.Validate`` and the endpoints that add and change a switch. engine_cases.py adds
them.
"""

from typing import Any

DEVICE = "1000abcdef"
OTHER = "1000999999"

NAMES = [
    None,
    "",
    "   ",
    "Hall",
    "  Hall\u3000",
    "x" * 64,
    "x" * 65,
    " " + "x" * 64 + " ",
    "x" * 62 + "\U0001f600",
    "x" * 63 + "\U0001f600",
    "Ha\x07ll",
    "Hall\x85",
    "Ha\x85ll",
    "Ha\x7fll",
    "Ha\x9fll",
    "Ha\xa0ll",
    "\ufeffHall",
    "Ha\u200bll",
    "Hall\u2028",
    "Ha\u2028ll",
    "\x1cHall",
    "\u0130stanbul",
    "\t\n",
]

DEVICE_IDS = [
    None,
    "",
    "  ",
    "abc",
    "abcd",
    "ABCD",
    " 1000ABCDEF ",
    "x" * 40,
    "x" * 41,
    "1000abcdef\x85",
    "\xa01000abcdef\u3000",
    "\ufeff1000abcdef",
    "1000abcdef\u200b",
    "1000\u212aabc",
    "1000\u0130abc",
    "1000\u0131abc",
    "\uff11000abcdef",
    "1000-abcdef",
    "1000 abcdef",
    "1000_abcdef",
    "1000abcdef\U0001f600",
    "\u01c5123",
    "1000\x00abc",
]

DEVICE_KEYS = [
    None,
    "",
    "  ",
    "secret",
    " secret\u3000",
    "x" * 128,
    "x" * 129,
    "x" * 126 + "\U0001f600",
    "x" * 127 + "\U0001f600",
    "se cret",
    "se\tcret",
    "se\xa0cret",
    "se\u200bcret",
    "se\x85cret",
    "se\x00cret",
    "se\u2028cret",
    "\ufeffsecret",
    "secret\x85",
    "s\u00e9cr\u00e8t",
]

REFERENCE_LISTS: list[list[str | None] | None] = [
    None,
    [],
    [""],
    ["  "],
    [None],
    ["Ceiling"],
    [" Ceiling ", "ceiling", "CEILING", "Floor lamp"],
    ["Stra\u00dfe", "STRASSE", "STRA\u1e9eE", "strasse"],
    ["\u039f\u0394\u039f\u03a3", "\u03bf\u03b4\u03bf\u03c2", "\u03bf\u03b4\u03bf\u03c3"],
    ["\u0131", "I", "i"],
    ["\u212a", "k", "K"],
    ["\u00b5", "\u039c", "\u03bc"],
    ["\u0130", "i\u0307", "I\u0307"],
    ["\u017f", "S", "s"],
    ["x" * 200],
    ["x" * 201],
    ["x" * 199 + "\U0001f600"],
    ["x" * 198 + "\U0001f600"],
    ["Ce\x07iling"],
    ["Ceiling\x85", "\x85Ceiling"],
    ["\ufeffCeiling", "Ceiling"],
    ["Ceiling\u2028", "Ceiling"],
    ["room:Hallway", "ROOM:hallway", "zone:Hallway"],
    ["Ceiling"] * 50,
    ["Ceiling"] * 51,
    [f"Light {i}" for i in range(50)],
    [f"Light {i}" for i in range(51)],
    ["a", "", "b"],
    ["a", None],
    ["\U00010400", "\U00010428"],
]


def _input(
    name: str | None = None,
    device_id: str | None = DEVICE,
    device_key: str | None = None,
    lights: list[str | None] | None = None,
    groups: list[str | None] | None = None,
) -> dict[str, Any]:
    return {"name": name, "deviceId": device_id, "deviceKey": device_key, "lights": lights, "groups": groups}


def validation_cases() -> list[dict[str, Any]]:
    cases = [_input(name=name, lights=["Ceiling"]) for name in NAMES]
    cases += [_input(device_id=device_id, lights=["Ceiling"]) for device_id in DEVICE_IDS]
    cases += [_input(device_key=key, lights=["Ceiling"]) for key in DEVICE_KEYS]
    cases += [_input(lights=lights) for lights in REFERENCE_LISTS]
    cases += [_input(groups=groups) for groups in REFERENCE_LISTS]
    cases += [_input(lights=lights, groups=["room:Hallway"]) for lights in REFERENCE_LISTS[:6]]
    cases += [
        _input("x" * 65, "x", "se cret", ["Ceiling"] * 51, [""]),
        _input("Ha\x07ll", "", "x" * 129, [], []),
        _input("Ha\x07ll", None, "x" * 129, None, None),
        _input(None, "abc", None, [""], []),
        _input(None, "abc", None, [], [""]),
        _input(None, "abc", None, [], None),
        _input("Hall", " 1000ABCDEF ", " secret ", [" Ceiling ", "ceiling"], ["room:Hallway ", " ROOM:hallway"]),
    ]
    return cases


def _switch(
    device_id: str, name: str | None = None, device_key: str | None = None, lights: list[str] | None = None
) -> dict[str, Any]:
    return {"name": name, "deviceId": device_id, "deviceKey": device_key, "lights": lights or ["Ceiling"], "groups": []}


def _create(**kwargs: Any) -> dict[str, Any]:
    return {"op": "create", "input": _input(**kwargs)}


def _update(route_id: str, **kwargs: Any) -> dict[str, Any]:
    """A change of the switch with ``route_id``, the id in the endpoint's path."""
    return {"op": "update", "deviceId": route_id, "input": _input(**kwargs)}


UNPAIRED: dict[str, str | None] = {"bridgeIp": None, "appKey": None}
"""Without a paired bridge, the endpoints save switches with a warning and never ask a bridge."""


def write_cases() -> list[dict[str, Any]]:
    """Scenarios of writes to one config, in order. Their bridges are never reachable, so no case needs the network:
    either it isn't paired, or its settings can't be used."""
    return [
        {
            "name": "create",
            "hue": UNPAIRED,
            "switches": [],
            "steps": [
                _create(
                    name=" Hall ",
                    device_id=" 1000ABCDEF ",
                    device_key=" k1 ",
                    lights=["Ceiling", "ceiling", " Floor lamp "],
                    groups=["room:Hallway"],
                ),
                _create(device_id=f" {DEVICE.upper()} ", lights=["Desk"]),
                _create(device_id="x"),
                _create(device_id=OTHER, device_key="", lights=["Desk"]),
                _create(device_id=OTHER, lights=["Desk"]),
            ],
        },
        {
            "name": "update",
            "hue": {"bridgeIp": "  ", "appKey": "test-app-key"},
            "switches": [_switch(DEVICE, "Hall", "k1"), _switch(OTHER, lights=["Desk"])],
            "steps": [
                _update(f"{DEVICE.upper()} ", name="Hall", lights=["Floor lamp"]),
                _update(DEVICE, name="Hall", device_key="k2", lights=["Floor lamp"]),
                _update(DEVICE, name="Hall", device_key="  ", lights=["Floor lamp"]),
                _update(DEVICE, device_key="k3", lights=["Ceiling"]),
                _update(DEVICE, device_id="1000AAAAAA", lights=["Ceiling"]),
                _update("1000aaaaaa", device_id="1000aaaaaa", device_key="k4", lights=["Ceiling"]),
                _update("1000aaaaaa", device_id=" 1000AAAAAA ", lights=["Ceiling"]),
                _update("1000aaaaaa", device_id=OTHER, lights=["Ceiling"]),
                _update("1000bbbbbb", device_id="1000bbbbbb", lights=["Ceiling"]),
                _update("1000bbbbbb", device_id="", lights=["Ceiling"]),
                _update(" 1000999999 ", name="Desk", device_id=OTHER, device_key="", groups=["zone:Upstairs"]),
                _update(OTHER, device_id=OTHER, lights=[]),
                _update("1000aaaaaa", device_id="1000aaaaaa", device_key="", lights=["Ceiling"]),
            ],
        },
        {
            "name": "storedIds",
            "hue": UNPAIRED,
            "switches": [
                _switch(" 1000ABCDEF ", "Spaced", "k1"),
                _switch("abc\u03c2", "Final sigma"),
                _switch("1000\u212a99", "Kelvin"),
            ],
            "steps": [
                _create(device_id=DEVICE, lights=["Desk"]),
                _update("1000ABCDEF", name="Spaced", lights=["Desk"]),
                _update("abc\u03a3", lights=["Desk"]),
                _update("abc\u03c2", device_id="1000cccccc", lights=["Desk"]),
                _update("1000\u212a99", device_id="1000k99", lights=["Desk"]),
                _create(device_id="1000K99", lights=["Desk"]),
            ],
        },
        {
            "name": "unusableAddress",
            "hue": {"bridgeIp": "bad host", "appKey": "test-app-key"},
            "switches": [_switch(DEVICE)],
            "steps": [
                _update(DEVICE, lights=["Ceiling"]),
                _create(device_id=OTHER, groups=["room:Hallway"]),
                _create(device_id="1000cccccc", lights=[]),
            ],
        },
        {
            "name": "unusableAppKey",
            "hue": {"bridgeIp": "192.0.2.10", "appKey": "test\napp-key"},
            "switches": [],
            "steps": [_create(device_id=DEVICE, lights=["Ceiling"])],
        },
    ]
