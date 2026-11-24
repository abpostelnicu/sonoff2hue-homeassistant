"""YAML as the .NET app parses it: a port of YamlDotNet 18.1.0's scanner and parser (``YamlDotNet.Core``).

PyYAML and LibYAML, which Home Assistant ships, come from the same design as YamlDotNet, but YamlDotNet has diverged:
it skips tabs between tokens, reads ``[@a]`` and ``[:a]`` as plain scalars, rejects ``[a]#c``, an anchored scalar
followed by another entry in a flow mapping, and a byte order mark inside the text, and it checks nothing about which
characters may appear. So this is a literal port, method by method, on UTF-16 code units as .NET's strings hold them
(decision 15): the same tokens, events, marks and messages, its look-ahead buffer included.

Where YamlDotNet throws something other than a ``YamlException`` (an ``InvalidOperationException`` from the scanner, an
``ArgumentException`` for a tag that isn't a valid URI), the port raises ``UnexpectedError`` (decision 9).
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum, IntEnum

from .errors import UnexpectedError
from .yaml_uri import is_well_formed_uri_string

# --- Text: .NET strings are UTF-16 code units. ---


def to_utf16(text: str) -> str:
    """The UTF-16 code units of a text, one per character, as .NET's ``string`` holds them."""
    if all(ord(char) <= 0xFFFF for char in text):
        return text
    data = text.encode("utf-16-le", "surrogatepass")
    return "".join(chr(data[i] | (data[i + 1] << 8)) for i in range(0, len(data), 2))


def from_utf16(units: str) -> str:
    """A text from UTF-16 code units: surrogate pairs become one character, lone surrogates stay."""
    if all(not 0xD800 <= ord(char) <= 0xDFFF for char in units):
        return units
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


# --- Marks and exceptions ---


@dataclass(frozen=True, slots=True)
class Mark:
    """A position: the index in UTF-16 code units, and the line and column counted from 1."""

    index: int
    line: int
    column: int

    def __str__(self) -> str:
        return f"Line: {self.line}, Col: {self.column}, Idx: {self.index}"


EMPTY_MARK = Mark(0, 1, 1)


class YamlError(Exception):
    """``YamlException`` and its subclasses, whose .NET name ``kind`` keeps. ``inner`` is the .NET exception it wraps."""

    def __init__(
        self,
        start: Mark,
        end: Mark,
        message: str,
        kind: str = "YamlException",
        inner: UnexpectedError | None = None,
    ) -> None:
        super().__init__(message)
        self.start = start
        self.end = end
        self.message = message
        self.kind = kind
        self.inner = inner


def _syntax_error(start: Mark, end: Mark, message: str) -> YamlError:
    return YamlError(start, end, message, "SyntaxErrorException")


def _semantic_error(start: Mark, end: Mark, message: str) -> YamlError:
    return YamlError(start, end, message, "SemanticErrorException")


INVALID_OPERATION = "Operation is not valid due to the current state of the object."


def _invalid_operation(message: str = INVALID_OPERATION) -> UnexpectedError:
    return UnexpectedError("InvalidOperationException", message)


class _Cursor:
    __slots__ = ("index", "line", "line_offset")

    def __init__(self, other: _Cursor | None = None) -> None:
        if other is None:
            self.index, self.line, self.line_offset = 0, 1, 0
        else:
            self.index, self.line, self.line_offset = other.index, other.line, other.line_offset

    def mark(self) -> Mark:
        return Mark(self.index, self.line, self.line_offset + 1)

    def skip(self) -> None:
        self.index += 1
        self.line_offset += 1

    def skip_line_by_offset(self, offset: int) -> None:
        self.index += offset
        self.line += 1
        self.line_offset = 0

    def force_skip_line_after_non_break(self) -> None:
        if self.line_offset != 0:
            self.line += 1
            self.line_offset = 0


class _LookAheadBuffer:
    """``LookAheadBuffer``: a ring of twice the capacity, filled a block at a time from a ``StringReader``. Peeking
    more than a block beyond what was read overwrites characters that weren't read yet, as in .NET."""

    __slots__ = (
        "_block_size",
        "_buffer",
        "_count",
        "_end_of_input",
        "_first_index",
        "_mask",
        "_position",
        "_text",
        "_write_offset",
    )

    def __init__(self, text: str, capacity: int) -> None:
        self._text = text
        self._position = 0
        self._block_size = capacity
        self._buffer = ["\0"] * (capacity * 2)
        self._mask = capacity * 2 - 1
        self._first_index = 0
        self._write_offset = 0
        self._count = 0
        self._end_of_input = False

    @property
    def end_of_input(self) -> bool:
        return self._end_of_input and self._count == 0

    def peek(self, offset: int) -> str:
        if offset >= self._count:
            self._fill_buffer()
        if offset < self._count:
            return self._buffer[(self._first_index + offset) & self._mask]
        return "\0"

    def cache(self, length: int) -> None:
        if length >= self._count:
            self._fill_buffer()

    def _fill_buffer(self) -> None:
        if self._end_of_input:
            return
        remaining = self._block_size
        while True:
            read = min(remaining, len(self._text) - self._position)
            if read == 0:
                self._end_of_input = True
                return
            self._buffer[self._write_offset : self._write_offset + read] = self._text[
                self._position : self._position + read
            ]
            self._position += read
            remaining -= read
            self._write_offset += read
            self._count += read
            if remaining <= 0:
                break
        if self._write_offset == len(self._buffer):
            self._write_offset = 0

    def skip(self, length: int) -> None:
        self._first_index = (self._first_index + length) & self._mask
        self._count -= length


# --- Tokens ---


class ScalarStyle(IntEnum):
    ANY = 0
    PLAIN = 1
    SINGLE_QUOTED = 2
    DOUBLE_QUOTED = 3
    LITERAL = 4
    FOLDED = 5

    @property
    def dotnet_name(self) -> str:
        return ("Any", "Plain", "SingleQuoted", "DoubleQuoted", "Literal", "Folded")[self]


class _Token:
    __slots__ = ("end", "start")

    def __init__(self, start: Mark, end: Mark) -> None:
        self.start = start
        self.end = end


class _StreamStart(_Token):
    __slots__ = ()


class _StreamEnd(_Token):
    __slots__ = ()


class _VersionDirective(_Token):
    __slots__ = ("major", "minor")

    def __init__(self, major: int, minor: int, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.major = major
        self.minor = minor


class _TagDirective(_Token):
    __slots__ = ("handle", "prefix")

    def __init__(self, handle: str, prefix: str, start: Mark = EMPTY_MARK, end: Mark = EMPTY_MARK) -> None:
        super().__init__(start, end)
        if not handle:
            raise UnexpectedError("ArgumentNullException", "Tag handle must not be empty. (Parameter 'handle')")
        if not _is_tag_handle(handle):
            raise UnexpectedError(
                "ArgumentException",
                "Tag handle must start and end with '!' and contain alphanumerical characters only. (Parameter 'handle')",
            )
        if not prefix:
            raise UnexpectedError("ArgumentNullException", "Tag prefix must not be empty. (Parameter 'prefix')")
        self.handle = handle
        self.prefix = prefix


def _is_tag_handle(handle: str) -> bool:
    """``^!([0-9A-Za-z_\\-]*!)?$``."""
    if handle == "!":
        return True
    return (
        len(handle) >= 2
        and handle[0] == "!"
        and handle[-1] == "!"
        and all(_is_alpha_numeric_dash_or_underscore(char) for char in handle[1:-1])
    )


class _DocumentStart(_Token):
    __slots__ = ()


class _DocumentEnd(_Token):
    __slots__ = ()


class _BlockSequenceStart(_Token):
    __slots__ = ()


class _BlockMappingStart(_Token):
    __slots__ = ()


class _BlockEnd(_Token):
    __slots__ = ()


class _FlowSequenceStart(_Token):
    __slots__ = ()


class _FlowSequenceEnd(_Token):
    __slots__ = ()


class _FlowMappingStart(_Token):
    __slots__ = ()


class _FlowMappingEnd(_Token):
    __slots__ = ()


class _BlockEntry(_Token):
    __slots__ = ()


class _FlowEntry(_Token):
    __slots__ = ()


class _Key(_Token):
    __slots__ = ()


class _Value(_Token):
    __slots__ = ()


class _AnchorAlias(_Token):
    __slots__ = ("value",)

    def __init__(self, value: str, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.value = value


class _Anchor(_Token):
    __slots__ = ("value",)

    def __init__(self, value: str, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.value = value


class _Tag(_Token):
    __slots__ = ("handle", "suffix")

    def __init__(self, handle: str, suffix: str, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.handle = handle
        self.suffix = suffix


class _Scalar(_Token):
    __slots__ = ("style", "value")

    def __init__(self, value: str, style: ScalarStyle, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.value = value
        self.style = style


class _Error(_Token):
    __slots__ = ("value",)

    def __init__(self, value: str, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.value = value


# --- Characters (CharacterAnalyzer) ---


def _is_alpha_numeric_dash_or_underscore(char: str) -> bool:
    return "0" <= char <= "9" or "A" <= char <= "Z" or "a" <= char <= "z" or char in "_-"


def _as_hex(char: str) -> int:
    """``CharacterAnalyzer.AsHex``, which doesn't check that the character is a hexadecimal digit."""
    code = ord(char)
    if char <= "9":
        return code - ord("0")
    if char <= "F":
        return code - ord("A") + 10
    return code - ord("a") + 10


def _is_hex(char: str) -> bool:
    return "0" <= char <= "9" or "A" <= char <= "F" or "a" <= char <= "f"


def _int32(value: int) -> int:
    """C#'s unchecked ``int`` arithmetic."""
    value &= 0xFFFFFFFF
    return value - 0x1_0000_0000 if value & 0x8000_0000 else value


def _convert_from_utf32(code: int) -> str:
    """``char.ConvertFromUtf32``, as UTF-16 code units."""
    if code < 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        raise UnexpectedError(
            "ArgumentOutOfRangeException",
            "A valid UTF32 value is between 0x000000 and 0x10ffff, inclusive, and should not include surrogate "
            "codepoint values (0x00d800 ~ 0x00dfff). (Parameter 'utf32')",
        )
    if code < 0x10000:
        return chr(code)
    code -= 0x10000
    return chr(0xD800 + (code >> 10)) + chr(0xDC00 + (code & 0x3FF))


def _convert_to_utf32(high: int, low: int) -> int:
    """``char.ConvertToUtf32(char, char)``."""
    if not 0xD800 <= high <= 0xDBFF:
        raise UnexpectedError(
            "ArgumentOutOfRangeException",
            "A valid high surrogate character is between 0xd800 and 0xdbff, inclusive. (Parameter 'highSurrogate')",
        )
    if not 0xDC00 <= low <= 0xDFFF:
        raise UnexpectedError(
            "ArgumentOutOfRangeException",
            "A valid low surrogate character is between 0xdc00 and 0xdfff, inclusive. (Parameter 'lowSurrogate')",
        )
    return ((high - 0xD800) << 10) + (low - 0xDC00) + 0x10000


_SIMPLE_ESCAPE_CODES = {
    "0": "\0",
    "a": "\x07",
    "b": "\x08",
    "t": "\x09",
    "\t": "\x09",
    "n": "\x0a",
    "v": "\x0b",
    "f": "\x0c",
    "r": "\x0d",
    "e": "\x1b",
    " ": "\x20",
    '"': '"',
    "\\": "\\",
    "/": "/",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}

_MAX_VERSION_NUMBER_LENGTH = 9


class _SimpleKey:
    __slots__ = ("_cursor", "is_possible", "is_required", "token_number")

    def __init__(self, is_required: bool = False, token_number: int = 0, cursor: _Cursor | None = None) -> None:
        self.is_possible = cursor is not None
        self.is_required = is_required
        self.token_number = token_number
        self._cursor = _Cursor(cursor)

    @property
    def index(self) -> int:
        return self._cursor.index

    @property
    def line(self) -> int:
        return self._cursor.line

    @property
    def line_offset(self) -> int:
        return self._cursor.line_offset

    @property
    def mark(self) -> Mark:
        return self._cursor.mark()


class _Scanner:
    """``YamlDotNet.Core.Scanner``, with comments skipped and simple keys of at most 1024 characters."""

    def __init__(self, text: str) -> None:
        self._buffer = _LookAheadBuffer(text, 1024)
        self._cursor = _Cursor()
        self._indents: list[int] = []
        self._tokens: deque[_Token] = deque()
        self._simple_keys: list[_SimpleKey] = []
        self._stream_start_produced = False
        self._stream_end_produced = False
        self._plain_scalar_followed_by_comment = False
        self._flow_collection_fetched = False
        self._start_flow_collection_fetched = False
        self._indent = -1
        self._flow_scalar_fetched = False
        self._simple_key_allowed = False
        self._flow_level = 0
        self._tokens_parsed = 0
        self._token_available = False
        self._previous: _Token | None = None
        self._previous_anchor: _Anchor | None = None
        self._max_key_size = 1024
        self.current: _Token | None = None

    # Character analysis.

    def _peek(self, offset: int = 0) -> str:
        return self._buffer.peek(offset)

    def _check(self, expected: str, offset: int = 0) -> bool:
        """``Check(char)`` for one character, ``Check(string)`` (any of them) for several."""
        char = self._buffer.peek(offset)
        return char == expected if len(expected) == 1 else char in expected

    def _is_alpha(self, offset: int = 0) -> bool:
        return _is_alpha_numeric_dash_or_underscore(self._buffer.peek(offset))

    def _is_digit(self, offset: int = 0) -> bool:
        return "0" <= self._buffer.peek(offset) <= "9"

    def _as_digit(self, offset: int = 0) -> int:
        return ord(self._buffer.peek(offset)) - ord("0")

    def _is_hex(self, offset: int) -> bool:
        return _is_hex(self._buffer.peek(offset))

    def _as_hex(self, offset: int) -> int:
        return _as_hex(self._buffer.peek(offset))

    def _is_space(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) == " "

    def _is_zero(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) == "\0"

    def _is_tab(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) == "\t"

    def _is_white(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) in " \t"

    def _is_break(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) in "\r\n\x85\u2028\u2029"

    def _is_crlf(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) == "\r" and self._buffer.peek(offset + 1) == "\n"

    def _is_break_or_zero(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) in "\r\n\x85\u2028\u2029\0"

    def _is_white_break_or_zero(self, offset: int = 0) -> bool:
        return self._buffer.peek(offset) in " \t\r\n\x85\u2028\u2029\0"

    def _is_document_start(self) -> bool:
        return (
            not self._buffer.end_of_input
            and self._cursor.line_offset == 0
            and self._check("-", 0)
            and self._check("-", 1)
            and self._check("-", 2)
            and self._is_white_break_or_zero(3)
        )

    def _is_document_end(self) -> bool:
        return (
            not self._buffer.end_of_input
            and self._cursor.line_offset == 0
            and self._check(".", 0)
            and self._check(".", 1)
            and self._check(".", 2)
            and self._is_white_break_or_zero(3)
        )

    def _is_document_indicator(self) -> bool:
        return self._is_document_start() or self._is_document_end()

    # The scanner's interface.

    @property
    def current_position(self) -> Mark:
        return self._cursor.mark()

    def move_next(self) -> bool:
        if self.current is not None:
            self.consume_current()
        return self.move_next_without_consuming()

    def move_next_without_consuming(self) -> bool:
        if not self._token_available and not self._stream_end_produced:
            self._fetch_more_tokens()
        if self._tokens:
            self.current = self._tokens.popleft()
            self._token_available = False
            return True
        self.current = None
        return False

    def consume_current(self) -> None:
        self._tokens_parsed += 1
        self._token_available = False
        self._previous = self.current
        self.current = None

    def _read_current_character(self) -> str:
        char = self._peek(0)
        self._skip()
        return char

    def _read_line(self) -> str:
        if self._check("\r\n\x85"):
            self._skip_line()
            return "\n"
        char = self._peek(0)
        self._skip_line()
        return char

    def _insert_token(self, index: int, token: _Token) -> None:
        """``InsertionQueue.Insert``."""
        if index > len(self._tokens):
            raise _invalid_operation("Cannot insert outside of the bounds of the queue")
        if index < 0:
            raise _invalid_operation("A token was inserted before the head of the queue")  # pragma: no cover
        self._tokens.insert(index, token)

    def _fetch_more_tokens(self) -> None:
        while True:
            needs_more_tokens = False
            if not self._tokens:
                needs_more_tokens = True
            else:
                for key in reversed(self._simple_keys):
                    if key.is_possible and key.token_number == self._tokens_parsed:
                        needs_more_tokens = True
                        break
            if not needs_more_tokens:
                break
            self._fetch_next_token()
        self._token_available = True

    def _stale_simple_keys(self) -> None:
        for key in reversed(self._simple_keys):
            if key.is_possible and (
                key.line < self._cursor.line or key.index + self._max_key_size < self._cursor.index
            ):
                if key.is_required:
                    mark = self._cursor.mark()
                    self._tokens.append(_Error("While scanning a simple key, could not find expected ':'.", mark, mark))
                key.is_possible = False

    def _fetch_next_token(self) -> None:
        if not self._stream_start_produced:
            self._fetch_stream_start()
            return

        self._scan_to_next_token()
        self._stale_simple_keys()
        self._unroll_indent(self._cursor.line_offset)
        self._buffer.cache(4)

        if self._buffer.end_of_input:
            self._fetch_stream_end()

        if self._cursor.line_offset == 0 and self._check("%"):
            self._fetch_directive()
            return

        if self._is_document_start():
            self._plain_scalar_followed_by_comment = False
            self._fetch_document_indicator(is_start_token=True)
            return

        if self._is_document_end():
            self._plain_scalar_followed_by_comment = False
            self._fetch_document_indicator(is_start_token=False)
            return

        if self._check("["):
            self._fetch_flow_collection_start(is_sequence_token=True)
            return
        if self._check("{"):
            self._fetch_flow_collection_start(is_sequence_token=False)
            return
        if self._check("]"):
            self._fetch_flow_collection_end(is_sequence_token=True)
            return
        if self._check("}"):
            self._fetch_flow_collection_end(is_sequence_token=False)
            return
        if self._check(","):
            self._fetch_flow_entry()
            return

        if self._check("-"):
            if self._is_white_break_or_zero(1):
                self._fetch_block_entry()
                return
            if self._flow_level > 0 and self._check(",[]{}", 1):
                self._tokens.append(_Error("Invalid key indicator format.", self._cursor.mark(), self._cursor.mark()))

        # '?' starts a key when a blank follows; in the flow context it may otherwise start a plain scalar.
        if self._check("?") and self._is_white_break_or_zero(1):
            self._fetch_key()
            return

        if (
            self._check(":")
            and (self._flow_level > 0 or self._is_white_break_or_zero(1))
            and not (self._simple_key_allowed and self._flow_level > 0)
            and not (self._flow_scalar_fetched and self._check(":", 1))
        ) and (
            self._is_white_break_or_zero(1)
            or self._check(",", 1)
            or self._flow_scalar_fetched
            or self._flow_collection_fetched
            or self._start_flow_collection_fetched
        ):
            self._fetch_value()
            return

        if self._check("*"):
            self._fetch_anchor(is_alias=True)
            return
        if self._check("&"):
            self._fetch_anchor(is_alias=False)
            return
        if self._check("!"):
            self._fetch_tag()
            return
        if self._check("|") and self._flow_level == 0:
            self._fetch_block_scalar(is_literal=True)
            return
        if self._check(">") and self._flow_level == 0:
            self._fetch_block_scalar(is_literal=False)
            return
        if self._check("'"):
            self._fetch_quoted_scalar(is_single_quoted=True)
            return
        if self._check('"'):
            self._fetch_quoted_scalar(is_single_quoted=False)
            return

        is_invalid_plain_scalar_character = self._is_white_break_or_zero() or self._check("-?:,[]{}#&*!|>'\"%@`")
        is_plain_scalar = (
            not is_invalid_plain_scalar_character
            or (self._check("-") and not self._is_white(1))
            or (self._check("?:") and not self._is_white_break_or_zero(1))
            or (self._simple_key_allowed and self._flow_level > 0)
        )
        if is_plain_scalar:
            if self._plain_scalar_followed_by_comment:
                start_mark = self._cursor.mark()
                self._tokens.append(
                    _Error(
                        "While scanning plain scalar, found a comment between adjacent scalars.", start_mark, start_mark
                    )
                )
            if (
                self._flow_scalar_fetched or (self._flow_collection_fetched and not self._start_flow_collection_fetched)
            ) and (self._check(":")):
                self._skip()
            self._flow_scalar_fetched = False
            self._flow_collection_fetched = False
            self._start_flow_collection_fetched = False
            self._plain_scalar_followed_by_comment = False
            self._fetch_plain_scalar()
            return

        if self._simple_key_allowed and self._indent >= self._cursor.line_offset and self._is_tab():
            raise YamlError(
                EMPTY_MARK,
                EMPTY_MARK,
                "While scanning a mapping, found invalid tab as indentation.",
                "SyntaxErrorException",
            )

        if self._is_white_break_or_zero():
            self._skip()
            return

        start = self._cursor.mark()
        self._skip()
        end = self._cursor.mark()
        raise _syntax_error(
            start, end, "While scanning for the next token, found character that cannot start any token."
        )

    def _check_white_space(self) -> bool:
        return self._check(" ") or ((self._flow_level > 0 or not self._simple_key_allowed) and self._check("\t"))

    def _skip(self) -> None:
        self._cursor.skip()
        self._buffer.skip(1)

    def _skip_line(self) -> None:
        if self._is_crlf():
            self._cursor.skip_line_by_offset(2)
            self._buffer.skip(2)
        elif self._is_break():
            self._cursor.skip_line_by_offset(1)
            self._buffer.skip(1)
        elif not self._is_zero():
            raise _invalid_operation("Not at a break.")

    def _scan_to_next_token(self) -> None:
        while True:
            while self._check_white_space():
                self._skip()
            self._process_comment()
            if self._is_break():
                self._skip_line()
                if self._flow_level == 0:
                    self._simple_key_allowed = True
            else:
                break

    def _process_comment(self) -> None:
        if self._check("#"):
            self._skip()
            while self._is_space():
                self._skip()
            while not self._is_break_or_zero():
                self._read_current_character()

    def _fetch_stream_start(self) -> None:
        self._simple_keys.append(_SimpleKey())
        self._simple_key_allowed = True
        self._stream_start_produced = True
        mark = self._cursor.mark()
        self._tokens.append(_StreamStart(mark, mark))

    def _unroll_indent(self, column: int) -> None:
        if self._flow_level != 0:
            return
        while self._indent > column:
            mark = self._cursor.mark()
            self._tokens.append(_BlockEnd(mark, mark))
            self._indent = self._indents.pop()

    def _fetch_stream_end(self) -> None:
        self._cursor.force_skip_line_after_non_break()
        self._unroll_indent(-1)
        self._remove_simple_key()
        self._simple_key_allowed = False
        self._stream_end_produced = True
        mark = self._cursor.mark()
        self._tokens.append(_StreamEnd(mark, mark))

    def _fetch_directive(self) -> None:
        self._unroll_indent(-1)
        self._remove_simple_key()
        self._simple_key_allowed = False
        token = self._scan_directive()
        if token is not None:
            self._tokens.append(token)

    def _scan_directive(self) -> _Token | None:
        start = self._cursor.mark()
        self._skip()
        name = self._scan_directive_name(start)
        directive: _Token
        if name == "YAML":
            if isinstance(self._previous, (_DocumentStart, _StreamStart, _DocumentEnd)):
                directive = self._scan_version_directive_value(start)
            else:
                raise _semantic_error(
                    start,
                    self._cursor.mark(),
                    "While scanning a version directive, did not find preceding <document end>.",
                )
        elif name == "TAG":
            directive = self._scan_tag_directive_value(start)
        else:
            while not self._buffer.end_of_input and not self._check("#") and not self._is_break():
                self._skip()
            return None

        while self._is_white():
            self._skip()
        self._process_comment()
        if not self._is_break_or_zero():
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a directive, did not find expected comment or line break."
            )
        if self._is_break():
            self._skip_line()
        return directive

    def _fetch_document_indicator(self, is_start_token: bool) -> None:
        self._unroll_indent(-1)
        self._remove_simple_key()
        self._simple_key_allowed = False
        start = self._cursor.mark()
        self._skip()
        self._skip()
        self._skip()
        if is_start_token:
            self._tokens.append(_DocumentStart(start, self._cursor.mark()))
            return
        error_token: _Token | None = None
        while not self._buffer.end_of_input and not self._is_break() and not self._check("#"):
            if not self._is_white():
                error_token = _Error(
                    "While scanning a document end, found invalid content after '...' marker.",
                    start,
                    self._cursor.mark(),
                )
                break
            self._skip()
        self._tokens.append(_DocumentEnd(start, start))
        if error_token is not None:
            self._tokens.append(error_token)

    def _fetch_flow_collection_start(self, is_sequence_token: bool) -> None:
        self._save_simple_key()
        self._increase_flow_level()
        self._simple_key_allowed = True
        start = self._cursor.mark()
        self._skip()
        self._tokens.append(_FlowSequenceStart(start, start) if is_sequence_token else _FlowMappingStart(start, start))
        self._start_flow_collection_fetched = True

    def _increase_flow_level(self) -> None:
        self._simple_keys.append(_SimpleKey())
        self._flow_level += 1

    def _fetch_flow_collection_end(self, is_sequence_token: bool) -> None:
        self._remove_simple_key()
        self._decrease_flow_level()
        self._simple_key_allowed = False
        start = self._cursor.mark()
        self._skip()
        error_token: _Token | None = None
        token: _Token
        if is_sequence_token:
            if self._check("#"):
                error_token = _Error(
                    "While scanning a flow sequence end, found invalid comment after ']'.", start, start
                )
            token = _FlowSequenceEnd(start, start)
        else:
            token = _FlowMappingEnd(start, start)
        self._tokens.append(token)
        if error_token is not None:
            self._tokens.append(error_token)
        self._flow_collection_fetched = True

    def _decrease_flow_level(self) -> None:
        if self._flow_level > 0:
            self._flow_level -= 1
            self._simple_keys.pop()

    def _fetch_flow_entry(self) -> None:
        self._remove_simple_key()
        self._simple_key_allowed = True
        start = self._cursor.mark()
        self._skip()
        end = self._cursor.mark()
        if self._check("#"):
            self._tokens.append(_Error("While scanning a flow entry, found invalid comment after comma.", start, end))
            return
        self._tokens.append(_FlowEntry(start, end))

    def _fetch_block_entry(self) -> None:
        if self._flow_level == 0:
            if not self._simple_key_allowed:
                if self._previous_anchor is not None and self._previous_anchor.end.line == self._cursor.line:
                    raise _semantic_error(
                        self._previous_anchor.start,
                        self._previous_anchor.end,
                        "Anchor before sequence entry on same line is not allowed.",
                    )
                mark = self._cursor.mark()
                self._tokens.append(_Error("Block sequence entries are not allowed in this context.", mark, mark))
            self._roll_indent(self._cursor.line_offset, -1, True, self._cursor.mark())
        self._remove_simple_key()
        self._simple_key_allowed = True
        start = self._cursor.mark()
        self._skip()
        self._tokens.append(_BlockEntry(start, self._cursor.mark()))

    def _fetch_key(self) -> None:
        if self._flow_level == 0:
            if not self._simple_key_allowed:
                mark = self._cursor.mark()
                raise _syntax_error(mark, mark, "Mapping keys are not allowed in this context.")
            self._roll_indent(self._cursor.line_offset, -1, False, self._cursor.mark())
        self._remove_simple_key()
        self._simple_key_allowed = self._flow_level == 0
        start = self._cursor.mark()
        self._skip()
        self._tokens.append(_Key(start, self._cursor.mark()))

    def _fetch_value(self) -> None:
        simple_key = self._simple_keys[-1]
        if simple_key.is_possible:
            self._insert_token(simple_key.token_number - self._tokens_parsed, _Key(simple_key.mark, simple_key.mark))
            self._roll_indent(simple_key.line_offset, simple_key.token_number, False, simple_key.mark)
            simple_key.is_possible = False
            self._simple_key_allowed = False
        else:
            local_simple_key_allowed = self._flow_level == 0
            if local_simple_key_allowed:
                if not self._simple_key_allowed:
                    mark = self._cursor.mark()
                    self._tokens.append(_Error("Mapping values are not allowed in this context.", mark, mark))
                    return
                self._roll_indent(self._cursor.line_offset, -1, False, self._cursor.mark())
                if self._cursor.line_offset == 0 and simple_key.line_offset == 0:
                    self._insert_token(len(self._tokens), _Key(simple_key.mark, simple_key.mark))
                    local_simple_key_allowed = False
            self._simple_key_allowed = local_simple_key_allowed
        start = self._cursor.mark()
        self._skip()
        self._tokens.append(_Value(start, self._cursor.mark()))

    def _roll_indent(self, column: int, number: int, is_sequence: bool, position: Mark) -> None:
        if self._flow_level > 0:
            return
        if self._indent < column:
            self._indents.append(self._indent)
            self._indent = column
            token: _Token = (
                _BlockSequenceStart(position, position) if is_sequence else _BlockMappingStart(position, position)
            )
            if number == -1:
                self._tokens.append(token)
            else:
                self._insert_token(number - self._tokens_parsed, token)

    def _fetch_anchor(self, is_alias: bool) -> None:
        self._save_simple_key()
        self._simple_key_allowed = False
        self._tokens.append(self._scan_anchor(is_alias))

    def _scan_anchor(self, is_alias: bool) -> _Token:
        start = self._cursor.mark()
        self._skip()
        is_alias_key = False
        if is_alias:
            key = self._simple_keys[-1]
            is_alias_key = key.is_required and key.is_possible
        value: list[str] = []
        while not self._is_white_break_or_zero():
            if not self._check("[]{},") and not (is_alias_key and self._check(":") and self._is_white_break_or_zero(1)):
                value.append(self._read_current_character())
            else:
                break
        if not value or not (self._is_white_break_or_zero() or self._check("?:,]}%@`")):
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning an anchor or alias, found value containing disallowed: []{},",
            )
        name = "".join(value)
        if is_alias:
            return _AnchorAlias(name, start, self._cursor.mark())
        self._previous_anchor = _Anchor(name, start, self._cursor.mark())
        return self._previous_anchor

    def _fetch_tag(self) -> None:
        self._save_simple_key()
        self._simple_key_allowed = False
        self._tokens.append(self._scan_tag())

    def _scan_tag(self) -> _Tag:
        start = self._cursor.mark()
        if self._check("<", 1):
            handle = ""
            self._skip()
            self._skip()
            suffix = self._scan_tag_uri(None, start)
            if not self._check(">"):
                raise _syntax_error(start, self._cursor.mark(), "While scanning a tag, did not find the expected '>'.")
            self._skip()
        else:
            first_part = self._scan_tag_handle(False, start)
            if len(first_part) > 1 and first_part[0] == "!" and first_part[-1] == "!":
                handle = first_part
                suffix = self._scan_tag_uri(None, start)
            else:
                suffix = self._scan_tag_uri(first_part, start)
                handle = "!"
                if not suffix:
                    suffix = handle
                    handle = ""
        if not self._is_white_break_or_zero() and not self._check(","):
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a tag, did not find expected whitespace, comma or line break.",
            )
        return _Tag(handle, suffix, start, self._cursor.mark())

    def _fetch_block_scalar(self, is_literal: bool) -> None:
        self._save_simple_key()
        self._simple_key_allowed = True
        self._tokens.append(self._scan_block_scalar(is_literal))

    def _scan_block_scalar(self, is_literal: bool) -> _Scalar:
        value: list[str] = []
        leading_break: list[str] = []
        trailing_breaks: list[str] = []
        chomping = 0
        increment = 0
        current_indent = 0
        leading_blank = False
        is_first_line: list[bool | None] = [None]

        start = self._cursor.mark()
        self._skip()

        if self._check("+-"):
            chomping = 1 if self._check("+") else -1
            self._skip()
            if self._is_digit():
                if self._check("0"):
                    raise _syntax_error(
                        start,
                        self._cursor.mark(),
                        "While scanning a block scalar, found an indentation indicator equal to 0.",
                    )
                increment = self._as_digit()
                self._skip()
        elif self._is_digit():
            if self._check("0"):
                raise _syntax_error(
                    start,
                    self._cursor.mark(),
                    "While scanning a block scalar, found an indentation indicator equal to 0.",
                )
            increment = self._as_digit()
            self._skip()
            if self._check("+-"):
                chomping = 1 if self._check("+") else -1
                self._skip()

        if self._check("#"):
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a block scalar, found a comment without whtespace after '>' indicator.",
            )

        while self._is_white():
            self._skip()
        self._process_comment()

        if not self._is_break_or_zero():
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a block scalar, did not find expected comment or line break.",
            )

        if self._is_break():
            self._skip_line()
            if is_first_line[0] is None:
                is_first_line[0] = True
            elif is_first_line[0] is True:
                is_first_line[0] = False

        end = [self._cursor.mark()]

        if increment != 0:
            current_indent = self._indent + increment if self._indent >= 0 else increment

        current_indent = self._scan_block_scalar_breaks(current_indent, trailing_breaks, is_literal, end, is_first_line)
        is_first_line[0] = False

        while self._cursor.line_offset == current_indent and not self._is_zero() and not self._is_document_end():
            trailing_blank = self._is_white()
            if not is_literal and leading_break[:1] == ["\n"] and not leading_blank and not trailing_blank:
                if not trailing_breaks:
                    value.append(" ")
                leading_break.clear()
            else:
                value.extend(leading_break)
                leading_break.clear()
            value.extend(trailing_breaks)
            trailing_breaks.clear()
            leading_blank = self._is_white()
            while not self._is_break_or_zero():
                value.append(self._read_current_character())
            line_break = self._read_line()
            if line_break != "\0":
                leading_break.append(line_break)
            current_indent = self._scan_block_scalar_breaks(
                current_indent, trailing_breaks, is_literal, end, is_first_line
            )

        if chomping != -1:
            value.extend(leading_break)
        if chomping == 1:
            value.extend(trailing_breaks)

        style = ScalarStyle.LITERAL if is_literal else ScalarStyle.FOLDED
        return _Scalar("".join(value), style, start, end[0])

    def _scan_block_scalar_breaks(
        self,
        current_indent: int,
        breaks: list[str],
        is_literal: bool,
        end: list[Mark],
        is_first_line: list[bool | None],
    ) -> int:
        max_indent = 0
        indent_of_first_line = -1
        end[0] = self._cursor.mark()
        while True:
            while (current_indent == 0 or self._cursor.line_offset < current_indent) and self._is_space():
                self._skip()
            max_indent = max(max_indent, self._cursor.line_offset)
            if not self._is_break():
                if is_literal and is_first_line[0] is True:
                    local_indent = self._cursor.line_offset
                    i = 0
                    while not self._is_break(i) and self._is_space(i):
                        i += 1
                        local_indent += 1
                    if self._is_break(i) and local_indent > self._cursor.line_offset:
                        is_first_line[0] = False
                        indent_of_first_line = local_indent
                break
            if is_first_line[0] is True:
                is_first_line[0] = False
                indent_of_first_line = self._cursor.line_offset
            breaks.append(self._read_line())
            end[0] = self._cursor.mark()

        if is_literal and indent_of_first_line > 1 and current_indent < indent_of_first_line - 1:
            raise _semantic_error(
                end[0], self._cursor.mark(), "While scanning a literal block scalar, found extra spaces in first line."
            )
        if not is_literal and max_indent > self._cursor.line_offset and indent_of_first_line > -1:
            raise _semantic_error(
                end[0],
                self._cursor.mark(),
                "While scanning a literal block scalar, found more spaces in lines above first content line.",
            )
        if current_indent == 0 and (self._cursor.line_offset > 0 or self._indent > -1):
            current_indent = max(max_indent, self._indent + 1, 1)
        return current_indent

    def _fetch_quoted_scalar(self, is_single_quoted: bool) -> None:
        self._save_simple_key()
        self._simple_key_allowed = False
        self._flow_scalar_fetched = self._flow_level > 0
        scalar = self._scan_flow_scalar(is_single_quoted)
        self._tokens.append(scalar)
        if not is_single_quoted and self._check("#"):
            start = self._cursor.mark()
            self._tokens.append(
                _Error(
                    "While scanning a flow sequence end, found invalid comment after double-quoted scalar.",
                    start,
                    start,
                )
            )

    def _scan_flow_scalar(self, is_single_quoted: bool) -> _Scalar:
        start = self._cursor.mark()
        self._skip()
        value: list[str] = []
        whitespaces: list[str] = []
        leading_break: list[str] = []
        trailing_breaks: list[str] = []
        has_leading_blanks = False
        quote = "'" if is_single_quoted else '"'

        while True:
            if self._is_document_indicator():
                raise _syntax_error(
                    start, self._cursor.mark(), "While scanning a quoted scalar, found unexpected document indicator."
                )
            if self._is_zero():
                raise _syntax_error(
                    start, self._cursor.mark(), "While scanning a quoted scalar, found unexpected end of stream."
                )
            if has_leading_blanks and not is_single_quoted and self._indent >= self._cursor.line_offset:
                raise _syntax_error(
                    start,
                    self._cursor.mark(),
                    "While scanning a multi-line double-quoted scalar, found wrong indentation.",
                )
            has_leading_blanks = False

            while not self._is_white_break_or_zero():
                if is_single_quoted and self._check("'", 0) and self._check("'", 1):
                    value.append("'")
                    self._skip()
                    self._skip()
                elif self._check(quote):
                    break
                elif not is_single_quoted and self._check("\\") and self._is_break(1):
                    self._skip()
                    self._skip_line()
                    has_leading_blanks = True
                    break
                elif not is_single_quoted and self._check("\\"):
                    self._scan_escape(start, value)
                else:
                    value.append(self._read_current_character())

            if self._check(quote):
                break

            while self._is_white() or self._is_break():
                if self._is_white():
                    if not has_leading_blanks:
                        whitespaces.append(self._read_current_character())
                    else:
                        self._skip()
                elif not has_leading_blanks:
                    whitespaces.clear()
                    leading_break.append(self._read_line())
                    has_leading_blanks = True
                else:
                    trailing_breaks.append(self._read_line())

            if has_leading_blanks:
                if leading_break[:1] == ["\n"]:
                    if not trailing_breaks:
                        value.append(" ")
                    else:
                        value.extend(trailing_breaks)
                else:
                    value.extend(leading_break)
                    value.extend(trailing_breaks)
                leading_break.clear()
                trailing_breaks.clear()
            else:
                value.extend(whitespaces)
                whitespaces.clear()

        self._skip()
        style = ScalarStyle.SINGLE_QUOTED if is_single_quoted else ScalarStyle.DOUBLE_QUOTED
        return _Scalar("".join(value), style, start, self._cursor.mark())

    def _scan_escape(self, start: Mark, value: list[str]) -> None:
        """An escape sequence in a double-quoted scalar, at its backslash."""
        code_length = 0
        escape_character = self._peek(1)
        if escape_character == "x":
            code_length = 2
        elif escape_character == "u":
            code_length = 4
        elif escape_character == "U":
            code_length = 8
        elif (unescaped := _SIMPLE_ESCAPE_CODES.get(escape_character)) is not None:
            value.append(unescaped)
        else:
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a quoted scalar, found unknown escape character."
            )
        self._skip()
        self._skip()
        if code_length == 0:
            return

        character = 0
        for k in range(code_length):
            if not self._is_hex(k):
                raise _syntax_error(
                    start,
                    self._cursor.mark(),
                    "While scanning a quoted scalar, did not find expected hexadecimal number.",
                )
            character = _int32((character << 4) + self._as_hex(k))

        if 0xD800 <= character <= 0xDFFF:
            for _ in range(code_length):
                self._skip()
            if self._peek(0) == "\\" and self._peek(1) in "uU":
                self._skip()
                code_length = 4 if self._peek(0) == "u" else 8
                self._skip()
                low_surrogate = 0
                for k in range(code_length):
                    if not self._is_hex(0):
                        raise _syntax_error(
                            start,
                            self._cursor.mark(),
                            "While scanning a quoted scalar, did not find expected hexadecimal number.",
                        )
                    low_surrogate = _int32((low_surrogate << 4) + self._as_hex(k))
                for _ in range(code_length):
                    self._skip()
                character = _convert_to_utf32(character & 0xFFFF, low_surrogate & 0xFFFF)
            else:
                raise _syntax_error(
                    start, self._cursor.mark(), "While scanning a quoted scalar, found invalid Unicode surrogates."
                )
        elif character > 0x10FFFF:
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a quoted scalar, found invalid Unicode character escape code.",
            )
        else:
            for _ in range(code_length):
                self._skip()
        value.append(_convert_from_utf32(character))

    def _fetch_plain_scalar(self) -> None:
        self._save_simple_key()
        self._simple_key_allowed = False
        is_multiline = [False]
        scalar = self._scan_plain_scalar(is_multiline)
        if is_multiline[0] and self._check(":") and self._flow_level == 0 and self._indent < self._cursor.line_offset:
            mark = self._cursor.mark()
            self._tokens.append(_Error("While scanning a multiline plain scalar, found invalid mapping.", mark, mark))
        self._tokens.append(scalar)

    def _scan_plain_scalar(self, is_multiline: list[bool]) -> _Scalar:
        value: list[str] = []
        whitespaces: list[str] = []
        leading_break: list[str] = []
        trailing_breaks: list[str] = []
        has_leading_blanks = False
        current_indent = self._indent + 1
        start = self._cursor.mark()
        end = start
        key = self._simple_keys[-1]

        while True:
            if self._is_document_indicator():
                break
            if self._check("#"):
                if self._indent < 0 and self._flow_level == 0:
                    self._plain_scalar_followed_by_comment = True
                break

            is_alias_value = self._check("*") and not (key.is_possible and key.is_required)

            while not self._is_white_break_or_zero():
                if (
                    self._check(":")
                    and not is_alias_value
                    and (self._is_white_break_or_zero(1) or (self._flow_level > 0 and self._check(",", 1)))
                ) or (self._flow_level > 0 and self._check(",[]{}")):
                    if self._flow_level == 0 and not key.is_possible:
                        mark = self._cursor.mark()
                        self._tokens.append(
                            _Error("While scanning a plain scalar value, found invalid mapping.", mark, mark)
                        )
                    break

                if has_leading_blanks or whitespaces:
                    if has_leading_blanks:
                        if leading_break[:1] == ["\n"]:
                            if not trailing_breaks:
                                value.append(" ")
                            else:
                                value.extend(trailing_breaks)
                        else:
                            value.extend(leading_break)
                            value.extend(trailing_breaks)
                        leading_break.clear()
                        trailing_breaks.clear()
                        has_leading_blanks = False
                    else:
                        value.extend(whitespaces)
                        whitespaces.clear()

                if self._flow_level > 0 and self._cursor.line_offset < current_indent:
                    raise _invalid_operation()

                value.append(self._read_current_character())
                end = self._cursor.mark()

            if not (self._is_white() or self._is_break()):
                break

            while self._is_white() or self._is_break():
                if self._is_white():
                    if has_leading_blanks and self._cursor.line_offset < current_indent and self._is_tab():
                        raise _syntax_error(
                            start,
                            self._cursor.mark(),
                            "While scanning a plain scalar, found a tab character that violate indentation.",
                        )
                    if not has_leading_blanks:
                        whitespaces.append(self._read_current_character())
                    else:
                        self._skip()
                else:
                    is_multiline[0] = True
                    if not has_leading_blanks:
                        whitespaces.clear()
                        leading_break.append(self._read_line())
                        has_leading_blanks = True
                    else:
                        trailing_breaks.append(self._read_line())

            if self._flow_level == 0 and self._cursor.line_offset < current_indent:
                break

        if has_leading_blanks:
            self._simple_key_allowed = True

        return _Scalar("".join(value), ScalarStyle.PLAIN, start, end)

    def _remove_simple_key(self) -> None:
        key = self._simple_keys[-1]
        if key.is_possible and key.is_required:
            raise _syntax_error(key.mark, key.mark, "While scanning a simple key, could not find expected ':'.")
        key.is_possible = False

    def _scan_directive_name(self, start: Mark) -> str:
        name: list[str] = []
        while self._is_alpha():
            name.append(self._read_current_character())
        if not name:
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a directive, could not find expected directive name."
            )
        if self._buffer.end_of_input:
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a directive, found unexpected end of stream."
            )
        if not self._is_white_break_or_zero():
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a directive, found unexpected non-alphabetical character."
            )
        return "".join(name)

    def _skip_whitespaces(self) -> None:
        while self._is_white():
            self._skip()

    def _scan_version_directive_value(self, start: Mark) -> _VersionDirective:
        self._skip_whitespaces()
        major = self._scan_version_directive_number(start)
        if not self._check("."):
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a %YAML directive, did not find expected digit or '.' character.",
            )
        self._skip()
        minor = self._scan_version_directive_number(start)
        return _VersionDirective(major, minor, start, start)

    def _scan_tag_directive_value(self, start: Mark) -> _TagDirective:
        self._skip_whitespaces()
        handle = self._scan_tag_handle(True, start)
        if not self._is_white():
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a %TAG directive, did not find expected whitespace."
            )
        self._skip_whitespaces()
        prefix = self._scan_tag_uri(None, start)
        if not self._is_white_break_or_zero():
            raise _syntax_error(
                start,
                self._cursor.mark(),
                "While scanning a %TAG directive, did not find expected whitespace or line break.",
            )
        return _TagDirective(handle, prefix, start, start)

    def _scan_tag_uri(self, head: str | None, start: Mark) -> str:
        tag: list[str] = []
        if head is not None and len(head) > 1:
            tag.append(head[1:])
        while self._is_alpha() or self._check(";/?:@&=+$.!~*'()[]%") or (self._check(",") and not self._is_break(1)):
            if self._check("%"):
                tag.append(self._scan_uri_escapes(start))
            elif self._check("+"):
                tag.append(" ")
                self._skip()
            else:
                tag.append(self._read_current_character())
        if not tag:
            return ""
        result = "".join(tag)
        if result.endswith(","):
            mark = self._cursor.mark()
            raise _syntax_error(mark, mark, "Unexpected comma at end of tag")
        return result

    def _scan_uri_escapes(self, start: Mark) -> str:
        octets = bytearray()
        width = 0
        while True:
            if not (self._check("%") and self._is_hex(1) and self._is_hex(2)):
                raise _syntax_error(start, self._cursor.mark(), "While scanning a tag, did not find URI escaped octet.")
            octet = (self._as_hex(1) << 4) + self._as_hex(2)
            if width == 0:
                width = (
                    1
                    if octet & 0x80 == 0x00
                    else 2
                    if octet & 0xE0 == 0xC0
                    else 3
                    if octet & 0xF0 == 0xE0
                    else 4
                    if octet & 0xF8 == 0xF0
                    else 0
                )
                if width == 0:
                    raise _syntax_error(
                        start, self._cursor.mark(), "While scanning a tag, found an incorrect leading UTF-8 octet."
                    )
            elif octet & 0xC0 != 0x80:
                raise _syntax_error(
                    start, self._cursor.mark(), "While scanning a tag, found an incorrect trailing UTF-8 octet."
                )
            octets.append(octet)
            self._skip()
            self._skip()
            self._skip()
            width -= 1
            if width <= 0:
                break
        result = to_utf16(octets.decode("utf-8", "replace"))
        if not result or len(result) > 2:
            raise _syntax_error(start, self._cursor.mark(), "While scanning a tag, found an incorrect UTF-8 sequence.")
        return result

    def _scan_tag_handle(self, is_directive: bool, start: Mark) -> str:
        if not self._check("!"):
            raise _syntax_error(start, self._cursor.mark(), "While scanning a tag, did not find expected '!'.")
        handle = [self._read_current_character()]
        while self._is_alpha():
            handle.append(self._read_current_character())
        if self._check("!"):
            handle.append(self._read_current_character())
        elif is_directive and (len(handle) != 1 or handle[0] != "!"):
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a tag directive, did not find expected '!'."
            )
        return "".join(handle)

    def _scan_version_directive_number(self, start: Mark) -> int:
        value = 0
        length = 0
        while self._is_digit():
            length += 1
            if length > _MAX_VERSION_NUMBER_LENGTH:
                raise _syntax_error(
                    start, self._cursor.mark(), "While scanning a %YAML directive, found extremely long version number."
                )
            value = value * 10 + self._as_digit()
            self._skip()
        if length == 0:
            raise _syntax_error(
                start, self._cursor.mark(), "While scanning a %YAML directive, did not find expected version number."
            )
        return value

    def _save_simple_key(self) -> None:
        is_required = self._flow_level == 0 and self._indent == self._cursor.line_offset
        if self._simple_key_allowed:
            key = _SimpleKey(is_required, self._tokens_parsed + len(self._tokens), self._cursor)
            self._remove_simple_key()
            self._simple_keys.pop()
            self._simple_keys.append(key)


# --- Events ---


class ParsingEvent:
    __slots__ = ("end", "start")
    nesting_increase = 0
    dotnet_name = "ParsingEvent"

    def __init__(self, start: Mark, end: Mark) -> None:
        self.start = start
        self.end = end


class StreamStart(ParsingEvent):
    __slots__ = ()
    nesting_increase = 1
    dotnet_name = "StreamStart"


class StreamEnd(ParsingEvent):
    __slots__ = ()
    nesting_increase = -1
    dotnet_name = "StreamEnd"


class DocumentStart(ParsingEvent):
    __slots__ = ("is_implicit", "tags", "version")
    nesting_increase = 1
    dotnet_name = "DocumentStart"

    def __init__(
        self,
        version: tuple[int, int] | None,
        tags: list[tuple[str, str]],
        is_implicit: bool,
        start: Mark,
        end: Mark,
    ) -> None:
        super().__init__(start, end)
        self.version = version
        self.tags = tags
        self.is_implicit = is_implicit


class DocumentEnd(ParsingEvent):
    __slots__ = ("is_implicit",)
    nesting_increase = -1
    dotnet_name = "DocumentEnd"

    def __init__(self, is_implicit: bool, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.is_implicit = is_implicit


class Alias(ParsingEvent):
    """``AnchorAlias``, which isn't a node event in YamlDotNet."""

    __slots__ = ("value",)
    dotnet_name = "AnchorAlias"

    def __init__(self, value: str, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.value = value


class NodeEvent(ParsingEvent):
    __slots__ = ("anchor", "tag")

    def __init__(self, anchor: str | None, tag: str | None, start: Mark, end: Mark) -> None:
        super().__init__(start, end)
        self.anchor = anchor
        self.tag = tag


class Scalar(NodeEvent):
    __slots__ = ("is_key", "is_plain_implicit", "is_quoted_implicit", "style", "value")
    dotnet_name = "Scalar"

    def __init__(
        self,
        anchor: str | None,
        tag: str | None,
        value: str,
        style: ScalarStyle,
        is_plain_implicit: bool,
        is_quoted_implicit: bool,
        start: Mark,
        end: Mark,
        is_key: bool = False,
    ) -> None:
        super().__init__(anchor, tag, start, end)
        self.value = value
        self.style = style
        self.is_plain_implicit = is_plain_implicit
        self.is_quoted_implicit = is_quoted_implicit
        self.is_key = is_key


class CollectionStyle(Enum):
    ANY = "Any"
    BLOCK = "Block"
    FLOW = "Flow"


class SequenceStart(NodeEvent):
    __slots__ = ("is_implicit", "style")
    nesting_increase = 1
    dotnet_name = "SequenceStart"

    def __init__(
        self, anchor: str | None, tag: str | None, is_implicit: bool, style: CollectionStyle, start: Mark, end: Mark
    ) -> None:
        super().__init__(anchor, tag, start, end)
        self.is_implicit = is_implicit
        self.style = style


class SequenceEnd(ParsingEvent):
    __slots__ = ()
    nesting_increase = -1
    dotnet_name = "SequenceEnd"


class MappingStart(NodeEvent):
    __slots__ = ("is_implicit", "style")
    nesting_increase = 1
    dotnet_name = "MappingStart"

    def __init__(
        self, anchor: str | None, tag: str | None, is_implicit: bool, style: CollectionStyle, start: Mark, end: Mark
    ) -> None:
        super().__init__(anchor, tag, start, end)
        self.is_implicit = is_implicit
        self.style = style


class MappingEnd(ParsingEvent):
    __slots__ = ()
    nesting_increase = -1
    dotnet_name = "MappingEnd"


# --- Parser ---


class _State(Enum):
    STREAM_START = 0
    STREAM_END = 1
    IMPLICIT_DOCUMENT_START = 2
    DOCUMENT_START = 3
    DOCUMENT_CONTENT = 4
    DOCUMENT_END = 5
    BLOCK_NODE = 6
    BLOCK_NODE_OR_INDENTLESS_SEQUENCE = 7
    FLOW_NODE = 8
    BLOCK_SEQUENCE_FIRST_ENTRY = 9
    BLOCK_SEQUENCE_ENTRY = 10
    INDENTLESS_SEQUENCE_ENTRY = 11
    BLOCK_MAPPING_FIRST_KEY = 12
    BLOCK_MAPPING_KEY = 13
    BLOCK_MAPPING_VALUE = 14
    FLOW_SEQUENCE_FIRST_ENTRY = 15
    FLOW_SEQUENCE_ENTRY = 16
    FLOW_SEQUENCE_ENTRY_MAPPING_KEY = 17
    FLOW_SEQUENCE_ENTRY_MAPPING_VALUE = 18
    FLOW_SEQUENCE_ENTRY_MAPPING_END = 19
    FLOW_MAPPING_FIRST_KEY = 20
    FLOW_MAPPING_KEY = 21
    FLOW_MAPPING_VALUE = 22
    FLOW_MAPPING_EMPTY_VALUE = 23


_DEFAULT_TAG_DIRECTIVES = (("!", "!"), ("!!", "tag:yaml.org,2002:"))
_MAJOR_VERSION = 1
_MINOR_VERSION = 3


def _tag_name(value: str) -> str:
    """``new TagName(value)``: a tag that doesn't start with '!' has to be a well-formed URI."""
    if not value:
        raise UnexpectedError("ArgumentException", "Tag value must not be empty. (Parameter 'value')")
    if value[0] != "!" and not is_well_formed_uri_string(value):
        raise UnexpectedError("ArgumentException", "Global tags must be valid URIs. (Parameter 'value')")
    return value


class Parser:
    """``YamlDotNet.Core.Parser`` on a text, as ``new Parser(new StringReader(text))``."""

    def __init__(self, text: str) -> None:
        self._scanner = _Scanner(to_utf16(text))
        self._states: list[_State] = []
        self._tag_directives: dict[str, str] = {}
        self._state = _State.STREAM_START
        self._current_token: _Token | None = None
        self._version: tuple[int, int] | None = None
        self._pending: deque[ParsingEvent] = deque()
        self.current: ParsingEvent | None = None

    def move_next(self) -> bool:
        if self._state is _State.STREAM_END:
            self.current = None
            return False
        if not self._pending:
            self._pending.append(self._state_machine())
        self.current = self._pending.popleft()
        return True

    def _get_current_token(self) -> _Token | None:
        if self._current_token is None and self._scanner.move_next_without_consuming():
            self._current_token = self._scanner.current
        return self._current_token

    def _skip(self) -> None:
        if self._current_token is not None:
            self._current_token = None
            self._scanner.consume_current()

    def _state_machine(self) -> ParsingEvent:
        match self._state:
            case _State.STREAM_START:
                return self._parse_stream_start()
            case _State.IMPLICIT_DOCUMENT_START:
                return self._parse_document_start(is_implicit=True)
            case _State.DOCUMENT_START:
                return self._parse_document_start(is_implicit=False)
            case _State.DOCUMENT_CONTENT:
                return self._parse_document_content()
            case _State.DOCUMENT_END:
                return self._parse_document_end()
            case _State.BLOCK_NODE:
                return self._parse_node(is_block=True, is_indentless_sequence=False)
            case _State.BLOCK_NODE_OR_INDENTLESS_SEQUENCE:
                return self._parse_node(is_block=True, is_indentless_sequence=True)
            case _State.FLOW_NODE:
                return self._parse_node(is_block=False, is_indentless_sequence=False)
            case _State.BLOCK_SEQUENCE_FIRST_ENTRY:
                return self._parse_block_sequence_entry(is_first=True)
            case _State.BLOCK_SEQUENCE_ENTRY:
                return self._parse_block_sequence_entry(is_first=False)
            case _State.INDENTLESS_SEQUENCE_ENTRY:
                return self._parse_indentless_sequence_entry()
            case _State.BLOCK_MAPPING_FIRST_KEY:
                return self._parse_block_mapping_key(is_first=True)
            case _State.BLOCK_MAPPING_KEY:
                return self._parse_block_mapping_key(is_first=False)
            case _State.BLOCK_MAPPING_VALUE:
                return self._parse_block_mapping_value()
            case _State.FLOW_SEQUENCE_FIRST_ENTRY:
                return self._parse_flow_sequence_entry(is_first=True)
            case _State.FLOW_SEQUENCE_ENTRY:
                return self._parse_flow_sequence_entry(is_first=False)
            case _State.FLOW_SEQUENCE_ENTRY_MAPPING_KEY:
                return self._parse_flow_sequence_entry_mapping_key()
            case _State.FLOW_SEQUENCE_ENTRY_MAPPING_VALUE:
                return self._parse_flow_sequence_entry_mapping_value()
            case _State.FLOW_SEQUENCE_ENTRY_MAPPING_END:
                return self._parse_flow_sequence_entry_mapping_end()
            case _State.FLOW_MAPPING_FIRST_KEY:
                return self._parse_flow_mapping_key(is_first=True)
            case _State.FLOW_MAPPING_KEY:
                return self._parse_flow_mapping_key(is_first=False)
            case _State.FLOW_MAPPING_VALUE:
                return self._parse_flow_mapping_value(is_empty=False)
            case _State.FLOW_MAPPING_EMPTY_VALUE:
                return self._parse_flow_mapping_value(is_empty=True)
            case _:  # pragma: no cover
                raise _invalid_operation()

    def _parse_stream_start(self) -> ParsingEvent:
        current = self._get_current_token()
        if not isinstance(current, _StreamStart):
            start = EMPTY_MARK if current is None else current.start
            end = EMPTY_MARK if current is None else current.end
            raise _semantic_error(start, end, "Did not find expected <stream-start>.")
        self._skip()
        self._state = _State.IMPLICIT_DOCUMENT_START
        return StreamStart(current.start, current.end)

    def _parse_document_start(self, is_implicit: bool) -> ParsingEvent:
        if isinstance(self._current_token, _VersionDirective):
            raise YamlError(
                EMPTY_MARK,
                EMPTY_MARK,
                "While parsing a document start node, could not find document end marker before version directive.",
                "SyntaxErrorException",
            )

        current = self._get_current_token()
        if not is_implicit:
            while isinstance(current, _DocumentEnd):
                self._skip()
                current = self._get_current_token()

        if current is None:
            raise YamlError(
                EMPTY_MARK,
                EMPTY_MARK,
                "Reached the end of the stream while parsing a document start.",
                "SyntaxErrorException",
            )

        if isinstance(current, _Scalar) and self._state in (_State.IMPLICIT_DOCUMENT_START, _State.DOCUMENT_START):
            is_implicit = True

        if (
            is_implicit
            and not isinstance(current, (_VersionDirective, _TagDirective, _DocumentStart, _StreamEnd, _DocumentEnd))
        ) or isinstance(current, _BlockMappingStart):
            directives: dict[str, str] = {}
            self._process_directives(directives)
            self._states.append(_State.DOCUMENT_END)
            self._state = _State.BLOCK_NODE
            return DocumentStart(None, list(directives.items()), True, current.start, current.end)

        if not isinstance(current, (_StreamEnd, _DocumentEnd)):
            start = current.start
            directives = {}
            version = self._process_directives(directives)
            current = self._get_current_token()
            if current is None:
                raise YamlError(
                    EMPTY_MARK,
                    EMPTY_MARK,
                    "Reached the end of the stream while parsing a document start",
                    "SemanticErrorException",
                )
            if not isinstance(current, _DocumentStart):
                raise _semantic_error(current.start, current.end, "Did not find expected <document start>.")
            self._states.append(_State.DOCUMENT_END)
            self._state = _State.DOCUMENT_CONTENT
            end = current.end
            self._skip()
            return DocumentStart(version, list(directives.items()), False, start, end)

        if isinstance(current, _DocumentEnd):
            self._skip()
        self._state = _State.STREAM_END
        current = self._get_current_token()
        if current is None:
            raise YamlError(
                EMPTY_MARK,
                EMPTY_MARK,
                "Reached the end of the stream while parsing a document start",
                "SemanticErrorException",
            )
        event = StreamEnd(current.start, current.end)
        if self._scanner.move_next_without_consuming():
            raise _invalid_operation("The scanner should contain no more tokens.")
        return event

    def _process_directives(self, tags: dict[str, str]) -> tuple[int, int] | None:
        has_own_directives = False
        local_version: tuple[int, int] | None = None
        while True:
            current = self._get_current_token()
            if isinstance(current, _VersionDirective):
                if self._version is not None:
                    raise _semantic_error(current.start, current.end, "Found duplicate %YAML directive.")
                if current.major != _MAJOR_VERSION or current.minor > _MINOR_VERSION:
                    raise _semantic_error(current.start, current.end, "Found incompatible YAML document.")
                local_version = self._version = (current.major, current.minor)
                has_own_directives = True
            elif isinstance(current, _TagDirective):
                if current.handle in tags:
                    raise _semantic_error(current.start, current.end, "Found duplicate %TAG directive.")
                tags[current.handle] = current.prefix
                has_own_directives = True
            elif isinstance(current, _DocumentStart) and (
                self._version is None or (self._version[0] == 1 and self._version[1] > 1)
            ):
                if self._version is None:
                    self._version = (1, 2)
                has_own_directives = True
                break
            else:
                break
            self._skip()

        for handle, prefix in _DEFAULT_TAG_DIRECTIVES:
            tags.setdefault(handle, prefix)
        if has_own_directives:
            self._tag_directives.clear()
        for handle, prefix in tags.items():
            self._tag_directives.setdefault(handle, prefix)
        return local_version

    def _parse_document_content(self) -> ParsingEvent:
        current = self._get_current_token()
        if isinstance(current, (_VersionDirective, _TagDirective, _DocumentStart, _DocumentEnd, _StreamEnd)):
            self._state = self._states.pop()
            return _empty_scalar(self._scanner.current_position)
        return self._parse_node(is_block=True, is_indentless_sequence=False)

    def _parse_node(self, is_block: bool, is_indentless_sequence: bool, is_key: bool = False) -> ParsingEvent:
        error_token = self._get_current_token()
        if isinstance(error_token, _Error):
            raise _semantic_error(error_token.start, error_token.end, error_token.value)

        current = self._get_current_token()
        if current is None:
            raise YamlError(
                EMPTY_MARK, EMPTY_MARK, "Reached the end of the stream while parsing a node", "SemanticErrorException"
            )
        if isinstance(current, _AnchorAlias):
            self._state = self._states.pop()
            event: ParsingEvent = Alias(current.value, current.start, current.end)
            self._skip()
            return event

        start = current.start
        anchor_name: str | None = None
        tag_name: str | None = None
        last_anchor: _Anchor | None = None
        last_tag: _Tag | None = None

        while True:
            if anchor_name is None and isinstance(current, _Anchor):
                last_anchor = current
                anchor_name = current.value
                self._skip()
            elif tag_name is None and isinstance(current, _Tag):
                last_tag = current
                if not current.handle:
                    tag_name = _tag_name(current.suffix)
                elif current.handle in self._tag_directives:
                    tag_name = _tag_name(self._tag_directives[current.handle] + current.suffix)
                else:
                    raise _semantic_error(
                        current.start, current.end, "While parsing a node, found undefined tag handle."
                    )
                self._skip()
            elif isinstance(current, _Anchor):
                raise _semantic_error(current.start, current.end, "While parsing a node, found more than one anchor.")
            elif isinstance(current, _AnchorAlias):
                raise _semantic_error(current.start, current.end, "While parsing a node, did not find expected token.")
            elif isinstance(current, _Error):
                if last_tag is not None and last_anchor is not None and anchor_name is not None:
                    return Scalar(
                        anchor_name, None, "", ScalarStyle.ANY, False, False, last_anchor.start, last_anchor.end
                    )
                raise _semantic_error(current.start, current.end, current.value)
            else:
                break
            current = self._get_current_token()
            if current is None:
                raise YamlError(
                    EMPTY_MARK,
                    EMPTY_MARK,
                    "Reached the end of the stream while parsing a node",
                    "SemanticErrorException",
                )

        is_implicit = tag_name is None

        if is_indentless_sequence and isinstance(self._get_current_token(), _BlockEntry):
            self._state = _State.INDENTLESS_SEQUENCE_ENTRY
            return SequenceStart(anchor_name, tag_name, is_implicit, CollectionStyle.BLOCK, start, current.end)

        if isinstance(current, _Scalar):
            is_plain_implicit = False
            is_quoted_implicit = False
            if (current.style is ScalarStyle.PLAIN and tag_name is None) or tag_name in ("!", "?"):
                is_plain_implicit = True
            elif tag_name is None:
                is_quoted_implicit = True
            self._state = self._states.pop()
            self._skip()
            event = Scalar(
                anchor_name,
                tag_name,
                current.value,
                current.style,
                is_plain_implicit,
                is_quoted_implicit,
                start,
                current.end,
                is_key,
            )

            # CXX2: an anchored scalar followed by an error.
            if anchor_name is not None and self._scanner.move_next_without_consuming():
                self._current_token = self._scanner.current
                if isinstance(error := self._current_token, _Error):
                    raise _semantic_error(error.start, error.end, error.value)

            # T833: a flow mapping's value followed by something else than ',' or '}'.
            if (
                self._state is _State.FLOW_MAPPING_KEY
                and not isinstance(self._scanner.current, _FlowMappingEnd)
                and self._scanner.move_next_without_consuming()
            ):
                self._current_token = self._scanner.current
                if (following := self._current_token) is not None and not isinstance(
                    following, (_FlowEntry, _FlowMappingEnd)
                ):
                    raise _semantic_error(
                        following.start,
                        following.end,
                        "While parsing a flow mapping, did not find expected ',' or '}'.",
                    )
            return event

        if isinstance(current, _FlowSequenceStart):
            self._state = _State.FLOW_SEQUENCE_FIRST_ENTRY
            return SequenceStart(anchor_name, tag_name, is_implicit, CollectionStyle.FLOW, start, current.end)

        if isinstance(current, _FlowMappingStart):
            self._state = _State.FLOW_MAPPING_FIRST_KEY
            return MappingStart(anchor_name, tag_name, is_implicit, CollectionStyle.FLOW, start, current.end)

        if is_block:
            if isinstance(current, _BlockSequenceStart):
                self._state = _State.BLOCK_SEQUENCE_FIRST_ENTRY
                return SequenceStart(anchor_name, tag_name, is_implicit, CollectionStyle.BLOCK, start, current.end)
            if isinstance(current, _BlockMappingStart):
                self._state = _State.BLOCK_MAPPING_FIRST_KEY
                return MappingStart(anchor_name, tag_name, is_implicit, CollectionStyle.BLOCK, start, current.end)

        if anchor_name is not None or tag_name is not None:
            self._state = self._states.pop()
            return Scalar(anchor_name, tag_name, "", ScalarStyle.PLAIN, is_implicit, False, start, current.end)

        raise _semantic_error(current.start, current.end, "While parsing a node, did not find expected node content.")

    def _parse_document_end(self) -> ParsingEvent:
        current = self._get_current_token()
        if current is None:
            raise YamlError(
                EMPTY_MARK,
                EMPTY_MARK,
                "Reached the end of the stream while parsing a document end",
                "SemanticErrorException",
            )
        is_implicit = True
        start = current.start
        end = start
        if isinstance(current, _DocumentEnd):
            end = current.end
            self._skip()
            is_implicit = False
        elif not (
            isinstance(self._current_token, (_StreamEnd, _DocumentStart, _FlowSequenceEnd, _VersionDirective))
            or (isinstance(self.current, Scalar) and isinstance(self._current_token, _Error))
        ):
            raise _semantic_error(start, end, "Did not find expected <document end>.")

        if self._version is not None and self._version[0] == 1 and self._version[1] > 1:
            self._version = None
        self._state = _State.DOCUMENT_START
        return DocumentEnd(is_implicit, start, end)

    def _parse_block_sequence_entry(self, is_first: bool) -> ParsingEvent:
        if is_first:
            self._get_current_token()
            self._skip()

        current = self._get_current_token()
        if isinstance(current, _BlockEntry):
            mark = current.end
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_BlockEntry, _BlockEnd)):
                self._states.append(_State.BLOCK_SEQUENCE_ENTRY)
                return self._parse_node(is_block=True, is_indentless_sequence=False)
            self._state = _State.BLOCK_SEQUENCE_ENTRY
            return _empty_scalar(mark)
        if isinstance(current, _BlockEnd):
            self._state = self._states.pop()
            event = SequenceEnd(current.start, current.end)
            self._skip()
            return event
        raise _semantic_error(
            EMPTY_MARK if current is None else current.start,
            EMPTY_MARK if current is None else current.end,
            "While parsing a block collection, did not find expected '-' indicator.",
        )

    def _parse_indentless_sequence_entry(self) -> ParsingEvent:
        current = self._get_current_token()
        if isinstance(current, _BlockEntry):
            mark = current.end
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_BlockEntry, _Key, _Value, _BlockEnd)):
                self._states.append(_State.INDENTLESS_SEQUENCE_ENTRY)
                return self._parse_node(is_block=True, is_indentless_sequence=False)
            self._state = _State.INDENTLESS_SEQUENCE_ENTRY
            return _empty_scalar(mark)
        self._state = self._states.pop()
        return SequenceEnd(
            EMPTY_MARK if current is None else current.start, EMPTY_MARK if current is None else current.end
        )

    def _parse_block_mapping_key(self, is_first: bool) -> ParsingEvent:
        if is_first:
            self._get_current_token()
            self._skip()

        current = self._get_current_token()
        if isinstance(current, _Key):
            mark = current.end
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_Key, _Value, _BlockEnd)):
                self._states.append(_State.BLOCK_MAPPING_VALUE)
                return self._parse_node(is_block=True, is_indentless_sequence=True, is_key=True)
            self._state = _State.BLOCK_MAPPING_VALUE
            return _empty_scalar(mark)
        if isinstance(current, _Value):
            self._skip()
            return _empty_scalar(current.end)
        if isinstance(current, _AnchorAlias):
            self._skip()
            return Alias(current.value, current.start, current.end)
        if isinstance(current, _BlockEnd):
            self._state = self._states.pop()
            event = MappingEnd(current.start, current.end)
            self._skip()
            return event
        if isinstance(current, _Error):
            raise _syntax_error(current.start, current.end, current.value)
        raise _semantic_error(
            EMPTY_MARK if current is None else current.start,
            EMPTY_MARK if current is None else current.end,
            "While parsing a block mapping, did not find expected key.",
        )

    def _parse_block_mapping_value(self) -> ParsingEvent:
        current = self._get_current_token()
        if isinstance(current, _Value):
            mark = current.end
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_Key, _Value, _BlockEnd)):
                self._states.append(_State.BLOCK_MAPPING_KEY)
                return self._parse_node(is_block=True, is_indentless_sequence=True)
            self._state = _State.BLOCK_MAPPING_KEY
            return _empty_scalar(mark)
        if isinstance(current, _Error):
            raise _semantic_error(current.start, current.end, current.value)
        self._state = _State.BLOCK_MAPPING_KEY
        return _empty_scalar(EMPTY_MARK if current is None else current.start)

    def _parse_flow_sequence_entry(self, is_first: bool) -> ParsingEvent:
        if is_first:
            self._get_current_token()
            self._skip()

        current = self._get_current_token()
        if not isinstance(current, _FlowSequenceEnd):
            if not is_first:
                if isinstance(current, _FlowEntry):
                    self._skip()
                    current = self._get_current_token()
                else:
                    raise _semantic_error(
                        EMPTY_MARK if current is None else current.start,
                        EMPTY_MARK if current is None else current.end,
                        "While parsing a flow sequence, did not find expected ',' or ']'.",
                    )
            if isinstance(current, _Key):
                self._state = _State.FLOW_SEQUENCE_ENTRY_MAPPING_KEY
                event: ParsingEvent = MappingStart(None, None, True, CollectionStyle.FLOW, EMPTY_MARK, EMPTY_MARK)
                self._skip()
                return event
            if not isinstance(current, _FlowSequenceEnd):
                self._states.append(_State.FLOW_SEQUENCE_ENTRY)
                return self._parse_node(is_block=False, is_indentless_sequence=False)

        self._state = self._states.pop()
        event = SequenceEnd(
            EMPTY_MARK if current is None else current.start, EMPTY_MARK if current is None else current.end
        )
        self._skip()

        # C2SP: a flow mapping key on two lines.
        if self._scanner.move_next_without_consuming():
            self._current_token = self._scanner.current
            token = self._current_token
            if isinstance(token, _Error) and current is not None and token.start.index > current.end.index:
                raise _semantic_error(token.start, token.end, token.value)
        return event

    def _parse_flow_sequence_entry_mapping_key(self) -> ParsingEvent:
        current = self._get_current_token()
        if not isinstance(current, (_Value, _FlowEntry, _FlowSequenceEnd)):
            self._states.append(_State.FLOW_SEQUENCE_ENTRY_MAPPING_VALUE)
            return self._parse_node(is_block=False, is_indentless_sequence=False)
        mark = EMPTY_MARK if current is None else current.end
        self._skip()
        self._state = _State.FLOW_SEQUENCE_ENTRY_MAPPING_VALUE
        return _empty_scalar(mark)

    def _parse_flow_sequence_entry_mapping_value(self) -> ParsingEvent:
        current = self._get_current_token()
        if isinstance(current, _Value):
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_FlowEntry, _FlowSequenceEnd)):
                self._states.append(_State.FLOW_SEQUENCE_ENTRY_MAPPING_END)
                return self._parse_node(is_block=False, is_indentless_sequence=False)
        self._state = _State.FLOW_SEQUENCE_ENTRY_MAPPING_END
        return _empty_scalar(EMPTY_MARK if current is None else current.start)

    def _parse_flow_sequence_entry_mapping_end(self) -> ParsingEvent:
        self._state = _State.FLOW_SEQUENCE_ENTRY
        current = self._get_current_token()
        return MappingEnd(
            EMPTY_MARK if current is None else current.start, EMPTY_MARK if current is None else current.end
        )

    def _parse_flow_mapping_key(self, is_first: bool) -> ParsingEvent:
        if is_first:
            self._get_current_token()
            self._skip()

        current = self._get_current_token()
        if not isinstance(current, _FlowMappingEnd):
            if not is_first:
                if isinstance(current, _FlowEntry):
                    self._skip()
                    current = self._get_current_token()
                elif not isinstance(current, _Scalar):
                    raise _semantic_error(
                        EMPTY_MARK if current is None else current.start,
                        EMPTY_MARK if current is None else current.end,
                        "While parsing a flow mapping,  did not find expected ',' or '}'.",
                    )
            if isinstance(current, _Key):
                self._skip()
                current = self._get_current_token()
                if not isinstance(current, (_Value, _FlowEntry, _FlowMappingEnd)):
                    self._states.append(_State.FLOW_MAPPING_VALUE)
                    return self._parse_node(is_block=False, is_indentless_sequence=False, is_key=True)
                self._state = _State.FLOW_MAPPING_VALUE
                return _empty_scalar(EMPTY_MARK if current is None else current.start)
            if isinstance(current, _Scalar):
                self._states.append(_State.FLOW_MAPPING_VALUE)
                return self._parse_node(is_block=False, is_indentless_sequence=False, is_key=True)
            if not isinstance(current, _FlowMappingEnd):
                self._states.append(_State.FLOW_MAPPING_EMPTY_VALUE)
                return self._parse_node(is_block=False, is_indentless_sequence=False, is_key=True)

        self._state = self._states.pop()
        self._skip()
        return MappingEnd(
            EMPTY_MARK if current is None else current.start, EMPTY_MARK if current is None else current.end
        )

    def _parse_flow_mapping_value(self, is_empty: bool) -> ParsingEvent:
        current = self._get_current_token()
        if not is_empty and isinstance(current, _Value):
            self._skip()
            current = self._get_current_token()
            if not isinstance(current, (_FlowEntry, _FlowMappingEnd)):
                self._states.append(_State.FLOW_MAPPING_KEY)
                return self._parse_node(is_block=False, is_indentless_sequence=False)

        self._state = _State.FLOW_MAPPING_KEY
        if not is_empty and isinstance(current, _Scalar):
            self._skip()
            return Scalar(None, None, current.value, current.style, False, False, current.start, current.end)
        return _empty_scalar(EMPTY_MARK if current is None else current.start)


def _empty_scalar(position: Mark) -> Scalar:
    return Scalar(None, None, "", ScalarStyle.PLAIN, True, False, position, position)
