"""Builds the inputs of tests/engine/fixtures/js_picker.json: edge cases for the switch editor's picker that the .NET app's
own web UI modules then evaluate (oracle.mjs). Run by tools/js_oracle/generate.sh; see README.md.
"""

from itertools import chain, combinations
import json
from pathlib import Path
import sys

# White space that JavaScript, .NET or Python trims, and characters that look like it.
SPACES = [
    "\t",
    "\n",
    "\v",
    "\f",
    "\r",
    " ",
    "\x00",
    "\x1c",
    "\x1f",
    "\x85",
    "\xa0",
    "\u1680",
    "\u180e",
    "\u2000",
    "\u200a",
    "\u200b",
    "\u2028",
    "\u2029",
    "\u202f",
    "\u205f",
    "\u3000",
    "\ufeff",
]

# Letters whose case mappings differ between JavaScript, .NET and Python, or that take more than one UTF-16 code unit.
LETTERS = [
    "\u0130",  # capital I with dot above
    "i\u0307",
    "I",
    "i",
    "\u0131",  # dotless i
    "\u00df",  # sharp s
    "\u1e9e",  # capital sharp s
    "SS",
    "ss",
    "\u03a3",  # capital sigma
    "\u03c3",
    "\u03c2",  # final sigma
    "\u212a",  # Kelvin sign
    "k",
    "K",
    "\u00b5",  # micro sign
    "\u03bc",
    "\u039c",
    "\u01c5",  # Dz with caron, title case
    "\u01c6",
    "\u01c4",
    "\u017f",  # long s
    "\ufb00",  # ff ligature
    "\u2126",  # ohm sign
    "\u212b",  # angstrom sign
    "\u00e5",
    "\U00010400",  # Deseret capital long i
    "\U00010428",
    "\u1f88",  # alpha with psili and prosgegrammeni
    "\ud800",
    "\U0001f600",
]

# Final sigma: a capital sigma after a cased letter and not before one, skipping case-ignorable characters.
SIGMA_CONTEXTS = [
    "\u03a3",
    "\u0391\u03a3",
    "\u0391\u03a3\u0391",
    "\u0391\u03a3 \u0391",
    "\u0391\u03a3.",
    "\u0391\u03a3.\u0391",
    "\u0391.\u03a3",
    "\u0391'\u03a3",
    "\u0391\u03a3'",
    "\u0391\u03a3'\u0391",
    "\u0391\u03a3\u0301",
    "\u0391\u03a3\u0301\u0391",
    "\u0391\u0301\u03a3",
    "\u0391\u03a3\u0345",
    "\u0345\u03a3",
    "\u0391\u03a31",
    "1\u03a3",
    "\u0391\u03a3\u03a3",
    "\u039f\u0394\u039f\u03a3",
    "\u039f\u0394\u039f\u03a3 \u039f\u0394\u039f\u03a3",
    "a\u03a3",
    "A\u03a3b",
    "\u0391\u03a3\ufeff",
    "\u0391\u03a3\u00ad",
    "\u0391\u03a3\u200d",
    "\u03a3\ud800",
    "\ud800\u03a3",
    "\u0391\u03a3\U00010400",
    "\U00010400\u03a3",
]

LOWER = [
    *LETTERS,
    *SIGMA_CONTEXTS,
    "\u0130STANBUL",
    "\u01c4UNGLA",
    "\u038c\u03a3\u039f\u03a3",
    "\u03a3\u0391\u03a3",
    "Kitchen",
    "HALLWAY",
    "",
]

TRIM = [
    "",
    "Kitchen",
    *(f"{space}Kitchen{space}" for space in SPACES),
    *(f"Kit{space}chen" for space in SPACES),
    " \u3000\ufeff\u2028 Kitchen \u0085",
    "\u0085\ufeff",
    "\ufeff\u0085 a",
]

SPLIT_KIND = [
    None,
    "",
    " ",
    "room:Kitchen",
    "ROOM:Kitchen",
    "Room: Kitchen ",
    "rOoM:\u3000Kitchen\ufeff",
    "zone:Upstairs",
    "ZONE:upstairs",
    "Zone:",
    "zone: ",
    "room:zone:x",
    "zone:room:x",
    " room:x",
    "\ufeffroom:x",
    "\u0085room:x",
    "room :x",
    "room",
    "room;x",
    "rooms:x",
    "roomx",
    ":x",
    "x:room:",
    "\u0130room:x",
    "\u212aroom:x",
    "ROOM:\u0130",
    "light:x",
    "room:\ud800",
]


def resource(resource_id: str | None, name: str | None) -> dict[str, str | None]:
    return {"id": resource_id, "name": name}


# Lights whose names collide under JavaScript's rules, or look like ids.
RESOLVE_LIGHTS = [
    resource("l-hall", "Hallway"),
    resource("l-desk", "Desk lamp"),
    resource("l-desk2", "desk lamp "),
    resource("l-porch", "\ufeffPorch"),
    resource("l-nel", "Porch\u0085"),
    resource("l-iris", "\u0130ris"),
    resource("l-strasse1", "Stra\u00dfe"),
    resource("l-strasse2", "STRASSE"),
    resource("l-sigma", "\u039f\u0394\u039f\u03a3"),
    resource("l-kelvin", "\u212aitchen"),
    resource("l-micro", "\u00b5"),
    resource("l-ohm", "\u2126"),
    resource("l-look", "l-hall"),
    resource("ABC-1", "Garage"),
    resource("l-empty", ""),
    resource("l-blank", " \u3000"),
    resource("l-null", None),
    resource(None, "Nameless id"),
    resource(None, None),
    resource("null", "Null id"),
    resource("l-dup", "Twin"),
    resource("l-dup", "Twin"),
    resource("l-surrogate", "\ud800"),
    resource("l-emoji", "Lamp \U0001f600"),
]

RESOLVE_LIGHT_ENTRIES = [
    None,
    "",
    "  ",
    "Hallway",
    "hallway",
    " HALLWAY\u3000",
    "\ufeffhallway\ufeff",
    "\u0085hallway",
    "l-hall",
    "L-HALL",
    "desk lamp",
    "Desk Lamp",
    "Porch",
    "porch\u0085",
    "Porch\ufeff",
    "i\u0307ris",
    "\u0130RIS",
    "iris",
    "\u0131ris",
    "stra\u00dfe",
    "STRA\u1e9eE",
    "strasse",
    "\u039f\u0394\u039f\u03a3",
    "\u03bf\u03b4\u03bf\u03c2",
    "\u03bf\u03b4\u03bf\u03c3",
    "\u03bf\u03b4\u03bf\u03c2 ",
    "kitchen",
    "Kitchen",
    "\u212aitchen",
    "\u00b5",
    "\u03bc",
    "\u039c",
    "\u2126",
    "\u03c9",
    "abc-1",
    "ABC-1",
    "garage",
    "null",
    "Null id",
    "Nameless id",
    "twin",
    "l-dup",
    "\ud800",
    "lamp \U0001f600",
    "room:Hallway",
    "Hallway,Desk lamp",
]

RESOLVE_ROOMS = [
    resource("r-kitchen", "Kitchen"),
    resource("r-study", "Study"),
    resource("r-twin", "Twin"),
    resource("r-twin2", "TWIN"),
    resource("r-look", "z-up"),
    resource("r-iris", "\u0130ris"),
    resource("r-sigma", "\u039f\u0394\u039f\u03a3"),
    resource("r-null", None),
    resource(None, "Nameless room"),
    resource("r-prefix", "zone:Upstairs"),
]

RESOLVE_ZONES = [
    resource("z-kitchen", "kitchen"),
    resource("z-up", "Upstairs"),
    resource("z-down", "\ufeffDownstairs\ufeff"),
    resource("z-sigma", "\u03bf\u03b4\u03bf\u03c2"),
    resource("Z-ID", "Garden"),
    resource("z-room", "room:Study"),
]

RESOLVE_GROUP_ENTRIES = [
    None,
    "",
    " ",
    "room:",
    "zone: ",
    "Kitchen",
    "kitchen",
    "room:Kitchen",
    "ROOM:kitchen",
    "zone:KITCHEN",
    "Room :Kitchen",
    "Study",
    "room:study",
    "zone:study",
    "twin",
    "room:Twin",
    "zone:twin",
    "Upstairs",
    "z-up",
    "room:z-up",
    "zone:z-up",
    "r-study",
    "room:r-study",
    "zone:r-study",
    "Z-ID",
    "zone:z-id",
    "Downstairs",
    "zone:downstairs",
    "\ufeffdownstairs",
    "i\u0307ris",
    "\u0131ris",
    "room:\u0130RIS",
    "\u039f\u0394\u039f\u03a3",
    "\u03bf\u03b4\u03bf\u03c3",
    "room:\u03bf\u03b4\u03bf\u03c2",
    "zone:\u039f\u0394\u039f\u03a3",
    "Nameless room",
    "r-null",
    "zone:Upstairs",
    "room:zone:Upstairs",
    "room:Study",
    "zone:room:Study",
    "Garden",
    "garden",
]

# Lists that encodeLight and encodeGroup see; each of their items is encoded as a light, a room and a zone.
ENCODE_LISTS = [
    [resource("a", "Lamp"), resource("b", "Desk")],
    [resource("a", "Lamp"), resource("b", "lamp"), resource("c", " LAMP\u3000")],
    [resource("a", "Lamp"), resource("b", "Lamp\ufeff"), resource("c", "Lamp\u0085")],
    [resource("a", "  Lamp  "), resource("b", "\ufeffDesk\ufeff"), resource("c", "Desk\u0085")],
    [resource("a", "b"), resource("b", "Porch")],
    [resource("a", ""), resource("b", "   "), resource("c", None), resource(None, None), resource(None, "Only")],
    [resource("a", "Stra\u00dfe"), resource("b", "STRASSE"), resource("c", "STRA\u1e9eE")],
    [resource("a", "\u0130ris"), resource("b", "i\u0307ris"), resource("c", "IRIS")],
    [
        resource("a", "\u039f\u0394\u039f\u03a3"),
        resource("b", "\u03bf\u03b4\u03bf\u03c2"),
        resource("c", "\u03bf\u03b4\u03bf\u03c3"),
    ],
    [resource("a", "\u212aelvin"), resource("b", "kelvin")],
    [resource("a", "\u00b5"), resource("b", "\u03bc")],
    [resource("a", "Twin"), resource("a", "Twin")],
    [resource("a", "room:Study"), resource("b", "Study")],
]

_KINDS = ("light", "room", "zone")


def selection_scenarios() -> list[dict[str, object]]:
    """Resources and a switch's entries. Each edit chooses the selected ids and removes some of the entries that
    unresolvedEntries listed when the picker opened, by their position in that list."""
    plain = {
        "lights": [resource("l1", "Hallway"), resource("l2", "Desk")],
        "rooms": [resource("r1", "Kitchen"), resource("r2", "Study")],
        "zones": [resource("z1", "Kitchen"), resource("z2", "Upstairs")],
    }
    ids = [("light", "l1"), ("light", "l2"), ("room", "r1"), ("room", "r2"), ("zone", "z1"), ("zone", "z2")]
    subsets = list(chain.from_iterable(combinations(ids, n) for n in range(len(ids) + 1)))

    def every_subset(removals: list[list[int]]) -> list[dict[str, object]]:
        return [
            {"select": {kind: [i for k, i in subset if k == kind] for kind in _KINDS}, "remove": remove}
            for subset in subsets
            for remove in removals
        ]

    clashes = {
        "lights": [resource("a1", "b2"), resource("b2", "Porch"), resource("c3", "Lamp"), resource("c4", "lamp")],
        "rooms": [resource("r1", "Office"), resource("r2", "Hall"), resource("r3", "hall ")],
        "zones": [resource("z1", "Office"), resource("z2", "Garden")],
    }
    strange = {
        "lights": [
            resource(None, None),
            resource("null", "Null id"),
            resource("n1", None),
            resource("e1", ""),
            resource("w1", "\u3000"),
            resource("s1", "\ufeffSpa"),
            resource("s2", "Spa\u0085"),
            resource("u1", "\u0130ris"),
            resource("u2", "Stra\u00dfe"),
            resource("u3", "\u039f\u0394\u039f\u03a3"),
        ],
        "rooms": [resource("r1", None), resource(None, "Nameless")],
        "zones": [],
    }
    return [
        {
            "name": "plain",
            "resources": plain,
            "current": {"lights": ["hallway", "Garage"], "groups": ["room:kitchen", "Kitchen", "z2", " zone:Cellar "]},
            "edits": every_subset([[], [0], [1], [0, 1, 2]]),
        },
        {
            "name": "plainEmpty",
            "resources": plain,
            "current": {"lights": [], "groups": None},
            "edits": every_subset([[]]),
        },
        {
            "name": "plainMissing",
            "resources": plain,
            "current": {},
            "edits": [{"select": {"light": ["l2"], "room": ["r2"], "zone": ["z1"]}, "remove": []}],
        },
        {
            "name": "plainDuplicates",
            "resources": plain,
            "current": {
                "lights": ["Hallway", " hallway ", "l1", "HALLWAY", "Desk", "Garage", "garage"],
                "groups": ["room:Kitchen", "r1", "zone:Kitchen", "z1", "zone:kitchen ", "Kitchen", "kitchen"],
            },
            "edits": every_subset([[], [0, 2]]),
        },
        {
            "name": "clashes",
            "resources": clashes,
            "current": {"lights": ["b2", "Lamp", "c3", "Porch"], "groups": ["Office", "room:Office", "hall", "z2"]},
            "edits": [
                {"select": {"light": light, "room": room, "zone": zone}, "remove": remove}
                for light in ([], ["a1"], ["b2"], ["a1", "b2"], ["c3", "c4"], ["c4", "a1"])
                for room in ([], ["r1"], ["r2", "r3"], ["r3"])
                for zone in ([], ["z1"], ["z2", "z1"])
                for remove in ([], [0, 1])
            ],
        },
        {
            "name": "strange",
            "resources": strange,
            "current": {
                "lights": [
                    "Null id",
                    "null",
                    "\ufeffspa",
                    "SPA\u0085",
                    "i\u0307ris",
                    "STRA\u1e9eE",
                    "\u03bf\u03b4\u03bf\u03c2",
                    "  ",
                ],
                "groups": ["room:", "Nameless", "room:nameless", "zone:Nameless", ""],
            },
            "edits": [
                {"select": {"light": light, "room": room, "zone": []}, "remove": remove}
                for light in (
                    [],
                    [None],
                    ["null"],
                    [None, "null"],
                    ["null", None],
                    ["n1", "e1", "w1"],
                    ["s1", "s2"],
                    ["u1", "u2", "u3"],
                    [None, "null", "n1", "e1", "w1", "s1", "s2", "u1", "u2", "u3"],
                )
                for room in ([], ["r1"], [None], ["r1", None])
                for remove in ([], [0], [0, 1, 2, 3])
            ],
        },
    ]


SPLIT_LIST = [
    None,
    "",
    ",",
    " , ,\u3000",
    "Hallway",
    "Hallway, Desk lamp",
    " Hallway ,Desk lamp,, ",
    "room:Kitchen, zone:Upstairs",
    "\ufeffHallway\ufeff,\u0085Desk\u0085",
    "a,b,a",
    "a;b",
    "a\uff0cb",
    "\u2028a\u2029,\u200bb\u200b",
]


def main() -> None:
    cases = {
        "trim": TRIM,
        "lower": LOWER,
        "splitKind": SPLIT_KIND,
        "resolveLight": {"lights": RESOLVE_LIGHTS, "entries": RESOLVE_LIGHT_ENTRIES},
        "resolveGroup": {"rooms": RESOLVE_ROOMS, "zones": RESOLVE_ZONES, "entries": RESOLVE_GROUP_ENTRIES},
        "encode": ENCODE_LISTS,
        "selection": selection_scenarios(),
        "splitList": SPLIT_LIST,
    }
    out = Path(sys.argv[1])
    # ASCII with escapes, so that lone surrogates reach JavaScript as they are.
    out.write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"Wrote {out}.")


if __name__ == "__main__":
    main()
