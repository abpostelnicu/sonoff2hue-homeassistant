"""Reads a ``sonoff2hue.yaml`` as the .NET app does: a port of ``AppConfig.Parse`` (``Configuration/AppConfig.cs``).

The app deserializes the text with YamlDotNet 18.1.0 (camelCase keys, duplicate keys checked) into ``AppConfig``, then
normalizes what YAML leaves null and explains YamlDotNet's errors in its own words. This module does the same on the
events of ``yaml_parser``, the port of YamlDotNet's parser. It ports the parts of YamlDotNet's deserializer that this
model reaches, call by call: the alias, recursion and node value deserializers, the type resolvers for tags, the null,
scalar, dictionary, collection and object node deserializers, and YamlDotNet's ``TypeConverter``, with .NET's exception
messages where ``AppConfig`` passes them on or matches them. Decision 15 records how messages name the pasted text and
where the port knowingly differs.
"""

from collections.abc import Callable
from functools import partial
import re

from .config import ConfigError, EngineConfig, HueSettings, SwitchSettings
from .errors import UnexpectedError
from .yaml_parser import (
    EMPTY_MARK,
    Alias,
    DocumentEnd,
    DocumentStart,
    MappingEnd,
    MappingStart,
    Mark,
    NodeEvent,
    Parser,
    ParsingEvent,
    Scalar,
    ScalarStyle,
    SequenceEnd,
    SequenceStart,
    StreamEnd,
    StreamStart,
    YamlError,
    from_utf16,
)

WHERE = "Config"
"""How ``AppConfig.Parse(string)`` names a text that doesn't come from a file, and so the pasted text."""

_MAXIMUM_RECURSION = 130  # DeserializerBuilder's default
_CORE_LIB = "System.Private.CoreLib, Version=10.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e"
_APP_ASSEMBLY = "sonoff2hue, Version=1.0.1.0, Culture=neutral, PublicKeyToken=null"
_TAG_PREFIX = "tag:yaml.org,2002:"


# --- .NET types: the model, and the types that YamlDotNet maps tags to. ---


class _Type:
    """A .NET type, with what the deserializer needs to know about it."""

    __slots__ = ("assembly", "display", "full_name", "item", "kind", "properties")

    def __init__(
        self,
        full_name: str,
        assembly: str,
        kind: str,
        *,
        item: _Type | None = None,
        properties: dict[str, _Type] | None = None,
        display: str | None = None,
    ) -> None:
        self.full_name = full_name
        self.assembly = assembly
        self.kind = kind
        self.item = item
        self.properties = properties or {}
        self.display = display or full_name
        """``Type.ToString()``, which differs from the full name for generic types."""

    @property
    def assembly_qualified_name(self) -> str:
        return f"{self.full_name}, {self.assembly}"


def _list_type(item: _Type) -> _Type:
    return _Type(
        f"System.Collections.Generic.List`1[[{item.assembly_qualified_name}]]",
        _CORE_LIB,
        "list",
        item=item,
        display=f"System.Collections.Generic.List`1[{item.full_name}]",
    )


_OBJECT = _Type("System.Object", _CORE_LIB, "object")
_STRING = _Type("System.String", _CORE_LIB, "string")
_BOOLEAN = _Type("System.Boolean", _CORE_LIB, "bool")
_INT32 = _Type("System.Int32", _CORE_LIB, "int")
_DOUBLE = _Type("System.Double", _CORE_LIB, "double")
_DATE_TIME = _Type("System.DateTime", _CORE_LIB, "datetime")
_LIST_STRING = _list_type(_STRING)
_LIST_OBJECT = _list_type(_OBJECT)
_DICTIONARY = _Type(
    f"System.Collections.Generic.Dictionary`2[[{_OBJECT.assembly_qualified_name}],[{_OBJECT.assembly_qualified_name}]]",
    _CORE_LIB,
    "dict",
    display="System.Collections.Generic.Dictionary`2[System.Object,System.Object]",
)
_HUE_SETTINGS = _Type(
    "Sonoff2Hue.Configuration.HueSettings",
    _APP_ASSEMBLY,
    "model",
    properties={"bridgeIp": _STRING, "bridgeId": _STRING, "appKey": _STRING, "certificateSha256": _STRING},
)
_SWITCH_SETTINGS = _Type(
    "Sonoff2Hue.Configuration.SwitchSettings",
    _APP_ASSEMBLY,
    "model",
    properties={
        "name": _STRING,
        "deviceId": _STRING,
        "deviceKey": _STRING,
        "lights": _LIST_STRING,
        "groups": _LIST_STRING,
    },
)
_LIST_SWITCHES = _list_type(_SWITCH_SETTINGS)
_APP_CONFIG = _Type(
    "Sonoff2Hue.Configuration.AppConfig",
    _APP_ASSEMBLY,
    "model",
    properties={"hue": _HUE_SETTINGS, "switches": _LIST_SWITCHES},
)

# DeserializerBuilder's tag mappings. YamlDotNet rejects every other tag ("Encountered an unresolved tag").
_TAG_MAPPINGS = {
    _TAG_PREFIX + "map": _DICTIONARY,
    _TAG_PREFIX + "str": _STRING,
    _TAG_PREFIX + "bool": _BOOLEAN,
    _TAG_PREFIX + "float": _DOUBLE,
    _TAG_PREFIX + "int": _INT32,
    _TAG_PREFIX + "timestamp": _DATE_TIME,
}


# --- .NET values ---


class _ModelObject:
    """An ``AppConfig``, ``HueSettings`` or ``SwitchSettings``, with its properties' values."""

    __slots__ = ("type", "values")

    def __init__(self, model: _Type) -> None:
        self.type = model
        self.values: dict[str, object] = dict.fromkeys(model.properties)
        if model is _APP_CONFIG:
            self.values["hue"] = _ModelObject(_HUE_SETTINGS)
            self.values["switches"] = _List(_LIST_SWITCHES)
        elif model is _SWITCH_SETTINGS:
            self.values["deviceId"] = ""
            self.values["lights"] = _List(_LIST_STRING)
            self.values["groups"] = _List(_LIST_STRING)


class _List(list[object]):
    """A ``List<T>``."""

    def __init__(self, list_type: _Type) -> None:
        super().__init__()
        self.type = list_type


class _Boxed:
    """A boxed .NET value type: ``bool``, ``int`` or ``DateTime``. Equal when the type and the value are."""

    __slots__ = ("type", "value")

    def __init__(self, value_type: _Type, value: object) -> None:
        self.type = value_type
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Boxed) and other.type is self.type and other.value == self.value

    def __hash__(self) -> int:
        return hash((self.type.full_name, self.value))


class _Dictionary:
    """A ``Dictionary<object, object>``: keys equal as ``object.Equals`` says, in insertion order."""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[_Key, object] = {}

    def contains(self, key: object) -> bool:
        return _Key(key) in self._items

    def set(self, key: object, value: object) -> None:
        if key is None:  # pragma: no cover (no node that is null can hold the alias that resolves to it)
            raise UnexpectedError("ArgumentNullException", "Value cannot be null. (Parameter 'key')")
        self._items[_Key(key)] = value


class _Key:
    """A dictionary key with .NET's default equality: strings and boxed values by value, other objects by reference."""

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Key):
            return NotImplemented
        if isinstance(self.value, (str, _Boxed)):
            return type(self.value) is type(other.value) and self.value == other.value
        return self.value is other.value

    def __hash__(self) -> int:
        if isinstance(self.value, (str, _Boxed)):
            return hash(self.value)
        return id(self.value)


def _type_of(value: object) -> _Type:
    match value:
        case str():
            return _STRING
        case _Boxed() | _ModelObject() | _List():
            return value.type
        case _Dictionary():
            return _DICTIONARY
    raise TypeError(value)  # pragma: no cover


def _to_string(value: object) -> str:
    """``object.ToString()`` with the invariant culture, as YamlDotNet's duplicate-key message shows a key."""
    match value:
        case str():
            return value
        case _Boxed() if value.type is _BOOLEAN:
            return "True" if value.value else "False"
        case _Boxed() if value.type is _DATE_TIME:
            return "01/01/0001 00:00:00"  # the only DateTime there can be: the default one, from "!!timestamp {}"
        case _Boxed():
            return str(value.value)  # an int, or the double 0 from "!!float {}"
    return _type_of(value).display


def _convert_to_string(value: object) -> str:
    """The source type's ``TypeConverter`` converting a value to a string, as YamlDotNet's ``ChangeType`` does."""
    match value:
        case _Boxed() if value.type is _DATE_TIME:
            return ""  # DateTimeConverter writes DateTime.MinValue as an empty string
        case _Boxed():
            return _to_string(value)  # BooleanConverter, Int32Converter, DoubleConverter ("R")
        case _List() | _Dictionary():
            return "(Collection)"  # CollectionConverter
    return _type_of(value).full_name  # TypeConverter: value.ToString()


# --- Exceptions that YamlDotNet throws and catches ---


class _SerializationError(Exception):
    """``SerializationException``, which ``ObjectNodeDeserializer`` turns into a ``YamlException``."""


class _NestedParse(Exception):  # noqa: N818
    """``AppConfig.Parse`` called on a top-level scalar's value: by ``ScalarNodeDeserializer`` through the type
    inspector (``via_converter`` false), or by ``TypeConverter`` for a ``!!str`` scalar (``via_converter`` true)."""

    def __init__(self, value: str, via_converter: bool) -> None:
        super().__init__(value)
        self.value = value
        self.via_converter = via_converter


def _invalid_cast(source: _Type, destination: _Type) -> UnexpectedError:
    return UnexpectedError(
        "InvalidCastException", f"Invalid cast from '{source.full_name}' to '{destination.full_name}'."
    )


def _overflow(message: str = "Arithmetic operation resulted in an overflow.") -> UnexpectedError:
    return UnexpectedError("OverflowException", message)


def _format_error(value: str) -> UnexpectedError:
    return UnexpectedError("FormatException", f"The input string '{value}' was not in a correct format.")


_UINT64_OVERFLOW = "Value was either too large or too small for a UInt64."


# --- The parser as YamlDotNet's deserializer reads it (IParser and ParserExtensions) ---


class _Events:
    def __init__(self, parser: Parser) -> None:
        self._parser = parser

    @property
    def current(self) -> ParsingEvent | None:
        return self._parser.current

    def move_next(self) -> bool:
        return self._parser.move_next()

    def accept[E: ParsingEvent](self, kind: type[E]) -> E | None:
        if self._parser.current is None and not self._parser.move_next():
            raise UnexpectedError("EndOfStreamException", "Attempted to read past the end of the stream.")
        current = self._parser.current
        return current if isinstance(current, kind) else None

    def try_consume[E: ParsingEvent](self, kind: type[E]) -> E | None:
        event = self.accept(kind)
        if event is not None:
            self._parser.move_next()
        return event

    def consume[E: ParsingEvent](self, kind: type[E], name: str) -> E:
        event = self.accept(kind)
        if event is None:
            current = self._parser.current
            if current is None:
                raise YamlError(EMPTY_MARK, EMPTY_MARK, f"Expected '{name}', got nothing.")
            raise YamlError(
                current.start, current.end, f"Expected '{name}', got '{current.dotnet_name}' (at {current.start})."
            )
        self._parser.move_next()
        return event

    def skip_this_and_nested_events(self) -> None:
        depth = 0
        while True:
            depth += self.consume(ParsingEvent, "ParsingEvent").nesting_increase
            if depth <= 0:
                break


# --- The deserializer ---


class _ValuePromise:
    """An anchor's value that isn't known yet, because the alias is inside the anchored node."""

    __slots__ = ("alias", "handlers", "has_value", "value")

    def __init__(self, alias: str | None = None, value: object = None, has_value: bool = False) -> None:
        self.alias = alias
        self.value = value
        self.has_value = has_value
        self.handlers: list[Callable[[object], None]] = []

    def set_value(self, value: object) -> None:
        if self.has_value:  # pragma: no cover
            raise UnexpectedError("InvalidOperationException", "Value already set")
        self.has_value = True
        self.value = value
        for handler in list(self.handlers):
            handler(value)


class _Deserializer:
    """``Deserializer.Deserialize(parser, typeof(AppConfig))`` with AppConfig's ``DeserializerBuilder``."""

    def __init__(self, events: _Events) -> None:
        self._events = events
        self._aliases: dict[str, _ValuePromise] = {}
        self._depth = 0

    def deserialize_value(self, expected: _Type) -> object:
        """``AliasValueDeserializer``, then ``MaximumRecursionValueDeserializer``, then ``NodeValueDeserializer``."""
        events = self._events
        alias = events.try_consume(Alias)
        if alias is not None:
            promise = self._aliases.get(alias.value)
            if promise is None:
                raise YamlError(
                    alias.start,
                    alias.end,
                    f"Alias ${alias.value} cannot precede anchor declaration",
                    "AnchorNotFoundException",
                )
            return promise.value if promise.has_value else promise

        anchor: str | None = None
        node_event = events.accept(NodeEvent)
        if node_event is not None and node_event.anchor is not None:
            anchor = node_event.anchor
            self._aliases.setdefault(anchor, _ValuePromise(alias=anchor))

        current = events.current
        assert current is not None  # noqa: S101 (accept moved to an event)
        if self._depth >= _MAXIMUM_RECURSION:
            raise YamlError(
                current.start,
                current.end,
                "Maximum level of recursion reached",
                "MaximumRecursionLevelReachedException",
            )
        self._depth += 1
        value = self._node_value(expected)
        self._depth -= 1

        if anchor is not None:
            promise = self._aliases.get(anchor)
            if promise is None:  # pragma: no cover
                self._aliases[anchor] = _ValuePromise(value=value, has_value=True)
            elif not promise.has_value:
                promise.set_value(value)
            else:
                self._aliases[anchor] = _ValuePromise(value=value, has_value=True)
        return value

    def check_aliases(self) -> None:
        """``AliasState.OnDeserialization``."""
        for promise in self._aliases.values():
            if not promise.has_value:  # pragma: no cover
                raise YamlError(
                    EMPTY_MARK, EMPTY_MARK, f"Anchor '{promise.alias}' not found", "AnchorNotFoundException"
                )

    def _node_value(self, expected: _Type) -> object:
        events = self._events
        node_event = events.accept(NodeEvent)
        node_type = self._type_from_event(node_event, expected)
        start = EMPTY_MARK if node_event is None else node_event.start
        end = EMPTY_MARK if node_event is None else node_event.end
        try:
            for deserializer in (
                self._null_node,
                self._scalar_node,
                self._dictionary_node,
                self._collection_node,
                self._object_node,
            ):
                found, value = deserializer(node_type)
                if found:
                    return _change_type(value, expected)
        except YamlError:
            raise
        except UnexpectedError as ex:
            raise YamlError(start, end, "Exception during deserialization", inner=ex) from ex
        raise YamlError(
            start,
            end,
            f"No node deserializer was able to deserialize the node into type {expected.assembly_qualified_name}",
        )

    @staticmethod
    def _type_from_event(node_event: NodeEvent | None, current: _Type) -> _Type:
        """The type resolvers: a tag in the tag mappings sets the type, any other tag is an error, and ``object``
        becomes a list or a dictionary for a sequence or a mapping."""
        if node_event is not None and node_event.tag is not None:
            mapped = _TAG_MAPPINGS.get(node_event.tag)
            if mapped is not None:
                return mapped
            raise YamlError(node_event.start, node_event.end, f"Encountered an unresolved tag '{node_event.tag}'")
        if current is _OBJECT:
            if isinstance(node_event, SequenceStart):
                return _LIST_OBJECT
            if isinstance(node_event, MappingStart):
                return _DICTIONARY
        return current

    def _null_node(self, expected: _Type) -> tuple[bool, object]:
        """``NullNodeDeserializer``: the null tag, or a plain scalar that isn't a key and reads as null."""
        event = self._events.accept(NodeEvent)
        if event is not None and (
            event.tag == _TAG_PREFIX + "null"
            or (
                isinstance(event, Scalar)
                and event.style is ScalarStyle.PLAIN
                and not event.is_key
                and event.value in ("", "~", "null", "Null", "NULL")
            )
        ):
            self._events.skip_this_and_nested_events()
            return True, None
        return False, None

    def _scalar_node(self, expected: _Type) -> tuple[bool, object]:
        """``ScalarNodeDeserializer``, for the types this model reaches."""
        scalar = self._events.try_consume(Scalar)
        if scalar is None:
            return False, None
        value = scalar.value
        match expected.kind:
            case "bool":
                return True, _Boxed(_BOOLEAN, _parse_boolean(value))
            case "int":
                return True, _Boxed(_INT32, _parse_int32(value))
            case "double":
                raise _unsupported_tag(scalar, "float")
            case "datetime":
                raise _unsupported_tag(scalar, "timestamp")
            case "string" | "object":
                return True, value
        if expected is _APP_CONFIG:
            # The type inspector finds AppConfig's static Parse(string) and calls it with the scalar's value.
            raise _NestedParse(value, via_converter=False)
        return True, _change_type(value, expected)

    def _dictionary_node(self, expected: _Type) -> tuple[bool, object]:
        """``DictionaryNodeDeserializer`` with ``DictionaryDeserializer``, for ``Dictionary<object, object>``."""
        if expected is not _DICTIONARY:
            return False, None
        result = _Dictionary()
        mapping = self._events.consume(MappingStart, "MappingStart")
        while self._events.try_consume(MappingEnd) is None:
            key = self.deserialize_value(_OBJECT)
            value = self.deserialize_value(_OBJECT)
            if isinstance(key, _ValuePromise):
                self._pending_key(result, key, value, mapping)
            else:
                if key is None:
                    raise UnexpectedError(
                        "ArgumentException", "Empty key names are not supported yet. (Parameter 'tKey')"
                    )
                if isinstance(value, _ValuePromise):
                    value.handlers.append(partial(_set_resolved_value, result, key))
                else:
                    _try_assign(result, key, value, mapping)
        return True, result

    @staticmethod
    def _pending_key(result: _Dictionary, key: _ValuePromise, value: object, mapping: MappingStart) -> None:
        if not isinstance(value, _ValuePromise):
            key.handlers.append(lambda resolved: result.set(resolved, value))
            return
        parts: dict[str, object] = {}

        def on_key(resolved: object) -> None:
            if "value" in parts:
                _try_assign(result, resolved, parts["value"], mapping)
            else:
                parts["key"] = resolved

        def on_value(resolved: object) -> None:
            if "key" in parts:
                _try_assign(result, parts["key"], resolved, mapping)
            else:
                parts["value"] = resolved

        key.handlers.append(on_key)
        value.handlers.append(on_value)

    def _collection_node(self, expected: _Type) -> tuple[bool, object]:
        """``CollectionNodeDeserializer``, for ``List<T>``."""
        if expected.kind != "list":
            return False, None
        item_type = expected.item
        assert item_type is not None  # noqa: S101
        result = _List(expected)
        self._events.consume(SequenceStart, "SequenceStart")
        while self._events.try_consume(SequenceEnd) is None:
            value = self.deserialize_value(item_type)
            if isinstance(value, _ValuePromise):
                result.append(None)
                value.handlers.append(partial(_set_resolved_item, result, len(result) - 1, item_type))
            else:
                result.append(_change_type(value, item_type))
        return True, result

    def _object_node(self, expected: _Type) -> tuple[bool, object]:
        """``ObjectNodeDeserializer``: a mapping into an object's writable properties."""
        if self._events.try_consume(MappingStart) is None:
            return False, None
        value = _create(expected)
        consumed: set[str] = set()
        while self._events.try_consume(MappingEnd) is None:
            name = self._events.consume(Scalar, "Scalar")
            if name.value in consumed:
                raise YamlError(name.start, name.end, f"Encountered duplicate key {name.value}")
            consumed.add(name.value)
            try:
                property_type = _get_property(expected, name.value)
                property_value = self.deserialize_value(property_type)
                if isinstance(property_value, _ValuePromise):
                    property_value.handlers.append(partial(_write_resolved, value, name.value, property_type))
                else:
                    _write(value, name.value, _change_type(property_value, property_type))
            except _SerializationError as ex:
                raise YamlError(name.start, name.end, str(ex)) from ex
            except YamlError:
                raise
            except UnexpectedError as ex:
                raise YamlError(name.start, name.end, "Exception during deserialization", inner=ex) from ex
        return True, value


def _set_resolved_value(result: _Dictionary, key: object, resolved: object) -> None:
    result.set(key, resolved)


def _set_resolved_item(result: _List, index: int, item_type: _Type, resolved: object) -> None:
    result[index] = _change_type(resolved, item_type)


def _write_resolved(target: object, name: str, property_type: _Type, resolved: object) -> None:
    _write(target, name, _change_type(resolved, property_type))


def _try_assign(result: _Dictionary, key: object, value: object, mapping: MappingStart) -> None:
    if result.contains(key):
        raise YamlError(mapping.start, mapping.end, f"Encountered duplicate key {_to_string(key)}")
    result.set(key, value)


def _create(kind: _Type) -> object:
    """``DefaultObjectFactory.Create``."""
    match kind.kind:
        case "model":
            return _ModelObject(kind)
        case "bool":
            return _Boxed(_BOOLEAN, False)
        case "int":
            return _Boxed(_INT32, 0)
        case "double":
            return _Boxed(_DOUBLE, 0)
        case "datetime":
            return _Boxed(_DATE_TIME, 0)
    raise UnexpectedError("InvalidOperationException", f"Failed to create an instance of type '{kind.full_name}'.")


def _get_property(kind: _Type, name: str) -> _Type:
    """``TypeInspectorSkeleton.GetProperty``: the writable properties, by their camelCase names, case-sensitively."""
    property_type = kind.properties.get(name)
    if property_type is None:
        raise _SerializationError(f"Property '{name}' not found on type '{kind.full_name}'.")
    return property_type


def _write(target: object, name: str, value: object) -> None:
    if isinstance(target, _ModelObject):
        target.values[name] = value


def _change_type(value: object, destination: _Type) -> object:
    """YamlDotNet's ``TypeConverter.ChangeType`` for the types this model reaches."""
    if value is None:
        return None
    source = _type_of(value)
    if source is destination or destination is _OBJECT:
        return value
    # The source type's TypeConverter converts anything to a string.
    if destination is _STRING:
        return _convert_to_string(value)
    if source is _STRING and destination is _APP_CONFIG:
        # A static Parse(string) on the destination type.
        assert isinstance(value, str)  # noqa: S101
        raise _NestedParse(value, via_converter=True)
    # Convert.ChangeType
    if isinstance(value, (str, _Boxed)):
        raise _invalid_cast(source, destination)
    raise UnexpectedError("InvalidCastException", "Object must implement IConvertible.")


def _unsupported_tag(scalar: Scalar, name: str) -> YamlError:
    """A ``!!float`` or ``!!timestamp`` value: .NET reads it with ``double.Parse`` or ``DateTime.Parse``, which the port
    doesn't reproduce (decision 15)."""
    return YamlError(
        scalar.start,
        scalar.end,
        f"Values tagged !!{name} are not supported. Write the value without the tag.",
        "NotSupported",
    )


# .NET's case-insensitive matching of these words is ASCII-only: it doesn't take U+017F (long s) for 's'.
_TRUE = re.compile(r"^(true|y|yes|on)$", re.IGNORECASE | re.ASCII)
_FALSE = re.compile(r"^(false|n|no|off)$", re.IGNORECASE | re.ASCII)


def _parse_boolean(value: str) -> bool:
    """``ScalarNodeDeserializer.DeserializeBooleanHelper``."""
    if _TRUE.search(value):
        return True
    if _FALSE.search(value):
        return False
    raise UnexpectedError("FormatException", f'The value "{value}" is not a valid YAML Boolean')


_INDEX_OUT_OF_RANGE = "Index was outside the bounds of the array."
_INT32_MIN = -(1 << 31)
_INT32_MAX = (1 << 31) - 1
_UINT64_MASK = (1 << 64) - 1


def _char_at(value: str, index: int) -> str:
    if index >= len(value):
        raise UnexpectedError("IndexOutOfRangeException", _INDEX_OUT_OF_RANGE)
    return value[index]


def _parse_int32(value: str) -> int:
    """``ScalarNodeDeserializer.DeserializeIntegerHelper`` for ``int``."""
    index = 0
    negative = False
    if _char_at(value, 0) == "-":
        index += 1
        negative = True
    elif value[0] == "+":
        index += 1

    result = 0
    if _char_at(value, index) == "0":
        if index == len(value) - 1:
            number_base = 10
        else:
            index += 1
            if value[index] == "b":
                number_base = 2
                index += 1
            elif value[index] == "x":
                number_base = 16
                index += 1
            else:
                number_base = 8
        digits = value[index:].replace("_", "")
        if number_base in (2, 8):
            result = _convert_to_uint64(digits, number_base)
        elif number_base == 16:
            result = _parse_uint64_hex(digits)
    else:
        chunks = value[index:].split(":")
        for chunk_index, chunk in enumerate(chunks):
            result = (result * 60) & _UINT64_MASK
            chunk_value = _parse_uint64(chunk.replace("_", ""))
            if chunk_index > 0 and chunk_value >= 60:
                raise UnexpectedError(
                    "FormatException",
                    f"Invalid sexagesimal (base-60) digit: '{chunk}'. Digits after the first must be less than 60.",
                )
            result = (result + chunk_value) & _UINT64_MASK

    if negative:
        if result == 1 << 63:
            number = -(1 << 63)
        elif result > (1 << 63) - 1:
            raise _overflow()  # checked(-(long)result): the cast itself is unchecked, the negation overflows
        else:
            number = -result
        if not _INT32_MIN <= number <= _INT32_MAX:
            raise _overflow()
        return number
    if result > _INT32_MAX:
        raise _overflow()
    return result


def _parse_uint64(value: str) -> int:
    """``ulong.Parse(value, CultureInfo.InvariantCulture)``: white space around, an optional sign, ASCII digits."""
    match = re.fullmatch(r"[\t\n\v\f\r ]*([+-]?)([0-9]+)[\t\n\v\f\r ]*\x00*", value)
    if match is None:
        raise _format_error(value)
    number = int(match.group(2))
    if match.group(1) == "-" and number != 0:
        raise _overflow(_UINT64_OVERFLOW)
    if number > _UINT64_MASK:
        raise _overflow(_UINT64_OVERFLOW)
    return number


def _parse_uint64_hex(value: str) -> int:
    """``ulong.Parse(value, NumberStyles.HexNumber, ...)``: white space around hexadecimal digits."""
    match = re.fullmatch(r"[\t\n\v\f\r ]*([0-9A-Fa-f]+)[\t\n\v\f\r ]*\x00*", value)
    if match is None:
        raise _format_error(value)
    number = int(match.group(1), 16)
    if number > _UINT64_MASK:
        raise _overflow(_UINT64_OVERFLOW)
    return number


def _convert_to_uint64(value: str, number_base: int) -> int:
    """``Convert.ToUInt64(value, 2 or 8)`` (``ParseNumbers.StringToLong`` with ``IsTight`` and ``TreatAsUnsigned``)."""
    if not value:
        raise UnexpectedError("ArgumentOutOfRangeException", "Specified argument was out of the range of valid values.")
    index = 0
    if value[0] == "-":
        raise UnexpectedError("ArgumentException", "String cannot contain a minus sign if the base is not 10.")
    if value[0] == "+":
        index = 1
    digits = 0
    result = 0
    while index < len(value) and "0" <= value[index] <= "9" and int(value[index]) < number_base:
        result = result * number_base + int(value[index])
        if result > _UINT64_MASK:
            raise _overflow(_UINT64_OVERFLOW)
        index += 1
        digits += 1
    if digits == 0:
        raise UnexpectedError("FormatException", "Could not find any recognizable digits.")
    if index < len(value):
        raise UnexpectedError("FormatException", "Additional non-parsable characters are at the end of the string.")
    return result


# --- AppConfig.Parse ---


_UNKNOWN_KEY = re.compile(r"^Property '(?P<key>[^']*)' not found on type '(?P<type>[^']*)'")
_DUPLICATE_KEY = re.compile(r"^Encountered duplicate key (?P<key>.+)$")
_TARGET_TYPE = re.compile(r"(?:to '|of type '|into type )(?P<type>[^',\[]+)")

_SECTIONS = {
    "AppConfig": ("at the top level", ("hue", "switches")),
    "HueSettings": ("under 'hue'", ("bridgeIp", "bridgeId", "appKey", "certificateSha256")),
    "SwitchSettings": ("in a 'switches' entry", ("name", "deviceId", "deviceKey", "lights", "groups")),
}


def _describe(error: YamlError) -> str:
    """``AppConfig.Describe``: YamlDotNet's message in the app's words where it recognizes it."""
    message = error.message
    unknown_key = _UNKNOWN_KEY.search(message)
    if unknown_key is not None:
        type_name = unknown_key.group("type")
        section = _SECTIONS.get(type_name[type_name.rfind(".") + 1 :])
        if section is not None:
            location, valid_keys = section
            return (
                f"unknown key '{unknown_key.group('key')}' {location}. "
                f"Valid keys are {', '.join(valid_keys)} (case-sensitive)."
            )

    duplicate = _DUPLICATE_KEY.search(message)
    if duplicate is not None:
        return f"duplicate key '{duplicate.group('key')}'."

    target = _TARGET_TYPE.search(message if error.inner is None else str(error.inner))
    if target is not None:
        target_type = target.group("type")
        if "List`1" in target_type:
            return "expected a list here: '- item' lines, or [a, b]."
        if target_type == "System.String":
            return "expected a single value here, not a list or 'key: value' lines."
        return "expected 'key: value' lines here."

    return message


class _Level:
    """A top-level scalar's text, which ``AppConfig.Parse`` parses again (see ``parse``), and what the level that read
    it does with the outcome."""

    def __init__(
        self, events: _Events, has_document_start: bool, has_stream_start: bool, start: Mark, via_converter: bool
    ) -> None:
        self.events = events
        self.has_document_start = has_document_start
        self.has_stream_start = has_stream_start
        self.start = start
        self.via_converter = via_converter


def parse(text: str) -> EngineConfig:
    """The settings of a ``sonoff2hue.yaml``, as ``AppConfig.Parse(string)`` reads them.

    Raises :class:`ConfigError` with the app's messages, which name the text "Config", and :class:`UnexpectedError`
    where .NET throws an exception that the app doesn't catch (decision 9). A document that is a single scalar makes
    ``AppConfig.Parse`` call itself with the scalar's value; when that value is the text again, .NET's stack overflows
    and the process ends, which the port reports as a ``StackOverflowException``.
    """
    levels: list[_Level] = []
    seen = {text}
    current = text
    while True:
        try:
            outcome: _ModelObject | Exception = _parse_level(current, levels)
        except _NestedParse as nested:
            if nested.value in seen:
                raise UnexpectedError("StackOverflowException", "Stack overflow.") from None
            seen.add(nested.value)
            current = nested.value
            continue
        except (ConfigError, UnexpectedError) as ex:
            outcome = ex
        break

    # Each level returns what the level inside it read, or wraps its failure (see _Level).
    while levels:
        level = levels.pop()
        outcome = _finish_level(level, outcome)
    if isinstance(outcome, Exception):
        raise outcome
    return _engine_config(outcome)


def _parse_level(text: str, levels: list[_Level]) -> _ModelObject:
    """``AppConfig.Parse(yaml, source: null)`` up to the point where a top-level scalar calls it again."""
    events = _Events(Parser(text))
    try:
        has_stream_start = events.try_consume(StreamStart) is not None
        has_document_start = events.try_consume(DocumentStart) is not None
        result: object = None
        if events.accept(DocumentEnd) is None and events.accept(StreamEnd) is None:
            deserializer = _Deserializer(events)
            node = events.accept(NodeEvent)
            try:
                result = deserializer.deserialize_value(_APP_CONFIG)
            except _NestedParse as nested:
                start = EMPTY_MARK if node is None else node.start
                levels.append(_Level(events, has_document_start, has_stream_start, start, nested.via_converter))
                raise
            deserializer.check_aliases()
        _end_document(events, has_document_start, has_stream_start)
    except YamlError as ex:
        raise _config_error(ex) from None
    return _normalize(result)


def _end_document(events: _Events, has_document_start: bool, has_stream_start: bool) -> None:
    if has_document_start:
        events.consume(DocumentEnd, "DocumentEnd")
    if has_stream_start:
        events.consume(StreamEnd, "StreamEnd")


def _finish_level(level: _Level, outcome: _ModelObject | Exception) -> _ModelObject | Exception:
    """The rest of a level's ``AppConfig.Parse`` once the top-level scalar's own ``AppConfig.Parse`` has returned."""
    if isinstance(outcome, Exception):
        # Through the type inspector, MethodBase.Invoke wraps the exception; TypeConverter unwraps it again.
        inner = (
            _dotnet_exception(outcome)
            if level.via_converter
            else UnexpectedError(
                "TargetInvocationException", "Exception has been thrown by the target of an invocation."
            )
        )
        return _config_error(YamlError(level.start, level.start, "Exception during deserialization", inner=inner))
    try:
        _end_document(level.events, level.has_document_start, level.has_stream_start)
    except YamlError as ex:
        return _config_error(ex)
    except UnexpectedError as ex:
        return ex
    return outcome


def _dotnet_exception(error: Exception) -> UnexpectedError:
    if isinstance(error, UnexpectedError):
        return error
    return UnexpectedError("ConfigException", str(error))


def _config_error(error: YamlError) -> ConfigError:
    message = f"{WHERE}, line {error.start.line}, column {error.start.column}: {_describe(error)}"
    return ConfigError(from_utf16(message))


def _normalize(result: object) -> _ModelObject:
    """What ``AppConfig.Parse`` does after deserializing: null sections and lists become empty, an empty switch entry
    is an error."""
    config = result if isinstance(result, _ModelObject) else _ModelObject(_APP_CONFIG)
    if config.values["hue"] is None:
        config.values["hue"] = _ModelObject(_HUE_SETTINGS)
    if config.values["switches"] is None:
        config.values["switches"] = _List(_LIST_SWITCHES)
    switches = config.values["switches"]
    assert isinstance(switches, _List)  # noqa: S101
    for index, switch in enumerate(switches):
        if switch is None:
            raise ConfigError(f"{WHERE}: switches[{index}] is empty. Remove the stray '-' or fill in the entry.")
        assert isinstance(switch, _ModelObject)  # noqa: S101
        if switch.values["deviceId"] is None:
            switch.values["deviceId"] = ""
        for name in ("lights", "groups"):
            if switch.values[name] is None:
                switch.values[name] = _List(_LIST_STRING)
    return config


def _engine_config(config: _ModelObject) -> EngineConfig:
    hue = config.values["hue"]
    switches = config.values["switches"]
    assert isinstance(hue, _ModelObject)  # noqa: S101
    assert isinstance(switches, _List)  # noqa: S101
    return EngineConfig(
        hue=HueSettings(
            bridge_ip=_text(hue.values["bridgeIp"]),
            bridge_id=_text(hue.values["bridgeId"]),
            app_key=_text(hue.values["appKey"]),
            certificate_sha256=_text(hue.values["certificateSha256"]),
        ),
        switches=[_switch(switch) for switch in switches if isinstance(switch, _ModelObject)],
    )


def _switch(switch: _ModelObject) -> SwitchSettings:
    device_id = _text(switch.values["deviceId"])
    return SwitchSettings(
        device_id=device_id or "",
        name=_text(switch.values["name"]),
        device_key=_text(switch.values["deviceKey"]),
        lights=_names(switch.values["lights"]),
        groups=_names(switch.values["groups"]),
    )


def _names(values: object) -> list[str | None]:
    assert isinstance(values, _List)  # noqa: S101
    return [_text(value) for value in values]


def _text(value: object) -> str | None:
    if value is None:
        return None
    assert isinstance(value, str)  # noqa: S101
    return from_utf16(value)
