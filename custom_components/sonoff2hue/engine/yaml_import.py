"""Reads a pasted ``sonoff2hue.yaml`` into the engine's settings (decisions 22 and 30).

PyYAML reads the text, with its C loader when it was built with libyaml. Null is the only value it reads implicitly:
``yes``, ``0x10``, ``010``, ``1e3`` and ``2001-12-14`` stay the text written, as every setting is text. The text is
refused, in this order, for what PyYAML can't read; for a second document, a tag other than ``!!str`` on a value or
nesting deeper than ``_MAX_DEPTH``; for a key that isn't a single value or that its mapping repeats; for what doesn't fit
the probatio schema of the settings, such as an empty entry of ``lights`` or ``groups``; and for an empty entry of
``switches``. Each message names the first such problem in the text, at its line and column, which the composed node
tree gives the schema's errors too.
"""

from collections.abc import Sequence
from functools import partial
import re
from typing import TYPE_CHECKING, Any, Final, Protocol

import probatio as vol
import yaml
from yaml.reader import ReaderError

from .config import ConfigError, EngineConfig, HueSettings, SwitchSettings

if TYPE_CHECKING:
    _SafeLoader = yaml.CSafeLoader
else:  # the C loader when PyYAML was built with libyaml
    _SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_YAML_TAGS: Final = "tag:yaml.org,2002:"
_STR: Final = _YAML_TAGS + "str"
_NULL: Final = _YAML_TAGS + "null"
_MAX_DEPTH: Final = 32
"""How deep lists and mappings may nest. The settings need four levels; the limit keeps composing from recursing
deeply."""
_LINE_BREAK: Final = re.compile(r"\r\n?|[\n\x85\u2028\u2029]")
"""The line breaks that YAML counts."""
_SINGLE_VALUE: Final = "expected a single value here, not a list or 'key: value' lines."


class _Loader(_SafeLoader):
    """PyYAML's safe loader, reading null as the only implicit value: any other plain value stays the text written."""


# PyYAML's other implicit resolvers would read yes, 0x10 or 2001-12-14 as a boolean, a number or a date.
_Loader.yaml_implicit_resolvers = {}
_Loader.add_implicit_resolver(_NULL, re.compile(r"(?:~|null|Null|NULL)?\Z"), ["~", "n", "N", ""])


class _Mark(Protocol):
    """Where PyYAML found something: the line and the column, counted from 0."""

    @property
    def line(self) -> int: ...

    @property
    def column(self) -> int: ...


_TEXT: Final = vol.Maybe(str)
_TEXTS: Final = vol.Maybe([str])
_HUE_FIELDS: Final = dict.fromkeys(("bridgeIp", "bridgeId", "appKey", "certificateSha256"), _TEXT)
_SWITCH_FIELDS: Final = {"name": _TEXT, "deviceId": _TEXT, "deviceKey": _TEXT, "lights": _TEXTS, "groups": _TEXTS}


def _mapping(fields: dict[str, Any]) -> vol.Maybe[Any]:
    """Null, or a mapping with any of these keys and no other."""
    return vol.Maybe({vol.Optional(key): value for key, value in fields.items()})


_TOP_FIELDS: Final = {"hue": _mapping(_HUE_FIELDS), "switches": vol.Maybe([_mapping(_SWITCH_FIELDS)])}
_SCHEMA: Final = vol.Schema(_mapping(_TOP_FIELDS))


def parse(text: str) -> EngineConfig:
    """The settings in the text of a ``sonoff2hue.yaml``.

    Raises :class:`ConfigError`, whose message names the text "Config" and gives the problem's line and column where it
    has them. Null sections, lists and values become empty, but an empty entry of ``switches`` is refused.
    """
    try:
        loaded = _load(text)
    except yaml.MarkedYAMLError as error:
        raise _config_error(error.problem or str(error), error.problem_mark) from None
    except ReaderError as error:
        # The reader stops at the first character it doesn't take. Its position counts bytes in the C loader and
        # characters in PyYAML's own, so the character is looked up instead.
        raise _character_error(text, text.index(chr(error.character)), error.reason) from None
    except UnicodeEncodeError as error:  # the C loader encodes the text as UTF-8, which has no lone surrogates
        raise _character_error(text, error.start, error.reason) from None
    if loaded is None:  # no document at all
        return EngineConfig()
    root, data = loaded
    try:
        settings = _SCHEMA(data)
    except vol.Invalid as error:
        raise _schema_error(root, error) from None
    return _engine_config(settings)


def _load(text: str) -> tuple[yaml.Node, Any] | None:
    """The document's node tree and the data that it holds, or None without a document."""
    _check_events(text)
    loader = _Loader(text)
    try:
        root = loader.get_single_node()
        if root is None:
            return None
        _check_keys(root)
        return root, loader.construct_document(root)
    finally:
        loader.dispose()


def _check_events(text: str) -> None:
    """Refuses, before the text is composed, a second document, a tag other than ``!!str`` on a value, and nesting
    deeper than ``_MAX_DEPTH``."""
    documents = depth = 0
    for event in yaml.parse(text, Loader=_Loader):
        match event:
            case yaml.DocumentStartEvent():
                documents += 1
                if documents > 1:
                    raise _config_error("only one document is allowed, but another one starts here.", event.start_mark)
            case yaml.ScalarEvent(tag=tag) if tag not in (None, _STR):
                raise _config_error(_unsupported(tag), event.start_mark)
            case yaml.CollectionStartEvent(tag=tag):
                if tag is not None:
                    raise _config_error(_unsupported(tag), event.start_mark)
                depth += 1
                if depth > _MAX_DEPTH:
                    raise _config_error(f"the text nests more than {_MAX_DEPTH} levels deep here.", event.start_mark)
            case yaml.CollectionEndEvent():
                depth -= 1


def _unsupported(tag: str) -> str:
    """The problem with a tag, written as YAML writes it: ``!!int`` for ``tag:yaml.org,2002:int``."""
    shown = "!!" + tag.removeprefix(_YAML_TAGS) if tag.startswith(_YAML_TAGS) else tag
    return f"the tag {shown} isn't supported here. Write the value without the tag."


def _check_keys(root: yaml.Node) -> None:
    """Refuses, in the order of the text, a key that isn't a single value and a key that its mapping repeats."""
    checked: set[yaml.Node] = set()

    def check(node: yaml.Node) -> None:
        if node in checked:  # an alias, or a node that holds itself
            return
        checked.add(node)
        if isinstance(node, yaml.MappingNode):
            keys: set[str | None] = set()
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    raise _config_error(_SINGLE_VALUE, key_node.start_mark)
                if (key := _key(key_node)) in keys:
                    raise _config_error(f"duplicate key '{key_node.value}'.", key_node.start_mark)
                keys.add(key)
                check(value_node)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                check(item)

    check(root)


def _key(node: yaml.ScalarNode) -> str | None:
    """A key as the loader constructs it: null, or the text written."""
    return None if node.tag == _NULL else node.value


def _schema_error(root: yaml.Node, error: vol.Invalid) -> ConfigError:
    """The schema's error that comes first in the text."""
    errors = error.errors if isinstance(error, vol.MultipleInvalid) else [error]
    node, problem = min(map(partial(_problem, root), errors), key=lambda found: found[0].start_mark.index)
    return _config_error(problem, node.start_mark)


def _problem(root: yaml.Node, error: vol.Invalid) -> tuple[yaml.Node, str]:
    """The node that the schema's error concerns, and the problem in words."""
    match error:
        case vol.ExtraKeysInvalid():
            key = _node_at(root, error.path, key=True)
            where, fields = _section(error.path[:-1])
            return key, f"unknown key '{key.value}' {where}. Valid keys are {', '.join(fields)} (case-sensitive)."
        case vol.DictInvalid():
            return _node_at(root, error.path), "expected 'key: value' lines here."
        case vol.SequenceTypeInvalid():
            return _node_at(root, error.path), "expected a list here: '- item' lines, or [a, b]."
    node = _node_at(root, error.path)
    if node.tag == _NULL:  # only an entry of 'lights' or 'groups' can't be null
        return node, _empty_entry(error.path[-2])
    return node, _SINGLE_VALUE  # where the schema expected str


def _empty_entry(field: str) -> str:
    """The problem with an empty entry of ``lights`` or ``groups``."""
    what = "light's" if field == "lights" else "room's or zone's"
    return f"an entry of '{field}' is empty. Write the {what} name or id, or remove the '-'."


def _node_at(root: yaml.Node, path: Sequence[Any], *, key: bool = False) -> yaml.Node:
    """The node of the value at this path in the data, or with ``key``, of the key that ends the path."""
    node: yaml.Node = root
    key_node: yaml.Node = root
    for segment in path:
        if isinstance(node, yaml.MappingNode):
            key_node, node = next((k, v) for k, v in node.value if _key(k) == segment)
        else:
            node = node.value[segment]
    return key_node if key else node


def _section(path: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """Where the mapping at this path is, in words, and its fields."""
    match path:
        case []:
            return "at the top level", _TOP_FIELDS
        case ["hue"]:
            return "under 'hue'", _HUE_FIELDS
    return "in a 'switches' entry", _SWITCH_FIELDS


def _engine_config(settings: dict[str, Any] | None) -> EngineConfig:
    """The settings, with the null sections, lists and values made empty."""
    settings = settings or {}
    hue = settings.get("hue") or {}
    switches = settings.get("switches") or []
    if None in switches:
        raise _config_error(f"switches[{switches.index(None)}] is empty. Remove the stray '-' or fill in the entry.")
    return EngineConfig(
        hue=HueSettings(
            bridge_ip=hue.get("bridgeIp"),
            bridge_id=hue.get("bridgeId"),
            app_key=hue.get("appKey"),
            certificate_sha256=hue.get("certificateSha256"),
        ),
        switches=[
            SwitchSettings(
                device_id=switch.get("deviceId") or "",
                name=switch.get("name"),
                device_key=switch.get("deviceKey"),
                lights=list(switch.get("lights") or []),
                groups=list(switch.get("groups") or []),
            )
            for switch in switches
        ],
    )


def _character_error(text: str, index: int, reason: str) -> ConfigError:
    """A character that YAML doesn't allow, at ``text[index]``."""
    breaks = list(_LINE_BREAK.finditer(text, 0, index))
    line_start = breaks[-1].end() if breaks else 0
    mark = yaml.Mark("<unicode string>", index, len(breaks), index - line_start, None, 0)
    return _config_error(f"unacceptable character #x{ord(text[index]):04x}: {reason}", mark)


def _config_error(problem: str, mark: _Mark | None = None) -> ConfigError:
    """A problem in the pasted text, which the messages call "Config", with its line and column when it has them."""
    if mark is None:
        return ConfigError(f"Config: {problem}")
    return ConfigError(f"Config, line {mark.line + 1}, column {mark.column + 1}: {problem}")
