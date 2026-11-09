"""Wolf RPG Editor game parser.

Handles:
  - Detection: Game.exe + Data.wolf or Data/ folder
  - DXArchive unpacking: Data.wolf → loose files (bundled UberWolf CLI,
    see wolf_unpack.py)
  - Binary parsing: .mps maps, CommonEvent.dat, DataBase.dat
  - Export: writes translated strings back into binary files
  - Versions: WOLF RPG Editor 2.x (Shift-JIS text) and plain 3.x (UTF-8
    text), decided per file from its own header.  Compressed (3.5) and
    protected (Pro-encrypted) files are refused, never half-read.

Binary format reference: WolfTL by Sinflower (MIT), Wolf_RPG_Decompyler by
Daviid-P.  The 3.x handling (UTF-8 marker bytes, map tile-data marker,
common-event terminator, compressed/protected version bytes) follows WolfTL
(https://github.com/Sinflower/WolfTL, MIT License, Copyright (c) 2024
Sinflower; FileCoder.hpp, Map.hpp, CommonEvents.hpp, Database.hpp).
"""

from __future__ import annotations

import logging
import os
import re
import struct
import shutil
from pathlib import Path
from typing import Optional

from . import game_io, wolf_unpack
from .project_model import TranslationEntry
from .wolf_unpack import WolfUnpackError

# The only data folders the exporter writes (and backs up / restores).
_WOLF_DATA_SUBDIRS = ('BasicData', 'MapData')

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# File headers, 2.x form.  WOLF RPG Editor 3.x stores text as UTF-8 and
# marks each such file with 'U' (0x55) in one byte that is 0x00 in 2.x
# (WolfTL FileCoder.hpp MagicNumber): file offset 6 in the .dat files,
# 16 in .mps maps, 9 in Game.dat.  Every file is judged by its own byte.
MAGIC_COMMON = bytes([0x00, 0x57, 0x00, 0x00, 0x4F, 0x4C, 0x00, 0x46, 0x43, 0x00])
MAGIC_DB     = bytes([0x00, 0x57, 0x00, 0x00, 0x4F, 0x4C, 0x00, 0x46, 0x4D, 0x00])
MAGIC_GAMEDAT = MAGIC_DB
MAGIC_MAP    = bytes([0x00] * 10 + [0x57, 0x4F, 0x4C, 0x46, 0x4D, 0x00, 0x00, 0x00, 0x00, 0x00])
UTF8_MARK = 0x55
_DAT_UTF8_AT = 6
_MAP_UTF8_AT = 16
_GAMEDAT_UTF8_AT = 9

# Versions whose data is LZ4-compressed (WOLF RPG Editor 3.5; WolfTL
# CommonEvents.hpp / Database.hpp / FileCoder.hpp).
_CE_COMPRESSED_VERSIONS = (0x93, 0xCC)
_DB_COMPRESSED_VERSION = 0xC4
_MAP_COMPRESSED_VERSION = 0x65          # and every later map version
# A file whose byte 1 is 0x50 ('P') and byte 5 its protection version
# (0x54 "V3.1", 0x55 "V3.3", 0x57+ "V3.5" in WolfTL; UberWolf calls all of
# them Pro protection) is encrypted.  WolfTL reads every such file as
# UTF-8 (3.x).  This version neither decrypts nor re-encrypts it.
_PROTECTED_BYTE1 = 0x50
_PROTECTED_MIN_VERSION = 0x50


def is_protected(head: bytes) -> bool:
    """True for a data file encrypted by WOLF RPG Editor's protection."""
    return (len(head) > 5 and head[1] == _PROTECTED_BYTE1
            and head[5] >= _PROTECTED_MIN_VERSION)

# The data files this parser reads and writes, besides MapData/*.mps.
_CE_NAME = 'CommonEvent.dat'
_DB_NAMES = ('DataBase.dat', 'CDataBase.dat', 'SysDatabase.dat')

WOLF_UNSUPPORTED = "This Wolf RPG game can't be translated by this version"
REASON_COMPRESSED = ("is compressed (WOLF RPG Editor 3.5 format), which this "
                     "version can't read or write yet")
REASON_PROTECTED = ("is encrypted by WOLF RPG Editor's data protection, which "
                    "this version doesn't remove")


class WolfVersionUnsupported(RuntimeError):
    """The game was made with a Wolf RPG Editor version we can't write."""


class WolfArchiveUnsupported(WolfVersionUnsupported):
    """Data.wolf's header shows a format we can't unpack: refused first."""

    def __init__(self, reason: str):
        self.where = 'Data.wolf'
        self.reason = reason
        super().__init__(f'{WOLF_UNSUPPORTED}: Data.wolf {reason}. Your game '
                         f'files were not changed.')


class WolfTextError(ValueError):
    """A string doesn't decode in its file's encoding (would be garbled)."""

    def __init__(self, encoding: str, offset: int):
        self.where = ''
        self.encoding = encoding
        self.offset = offset
        name = 'UTF-8' if encoding == 'utf-8' else 'Shift-JIS'
        super().__init__(
            f"has text that isn't valid {name} (at byte {offset}), so it "
            "would show up garbled")


def header_utf8(data: bytes, magic: bytes, utf8_at: int) -> Optional[bool]:
    """True/False (UTF-8 or Shift-JIS) when *data* starts with *magic*.

    The byte at *utf8_at* may be 0x00 (2.x) or 0x55 (3.x); anything else,
    or any other difference, is not this header: None.
    """
    if len(data) < len(magic):
        return None
    mark = data[utf8_at]
    if mark not in (0x00, UTF8_MARK):
        return None
    head = bytearray(data[:len(magic)])
    head[utf8_at] = 0x00
    if bytes(head) != magic:
        return None
    return mark == UTF8_MARK


def file_refusal(path, kind: str) -> Optional[str]:
    """Why this version must not read or write one data file, or None.

    *kind* is 'ce', 'db' or 'map'.  Only header bytes are read.  Files
    that are merely damaged or of an unknown layout are not refused here:
    their parse fails and is reported for that file alone.
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
    except OSError:
        return None     # absent/unreadable: the load reports it per file
    if is_protected(head):
        return REASON_PROTECTED
    if kind == 'map':
        if (header_utf8(head, MAGIC_MAP, _MAP_UTF8_AT) is not None
                and len(head) >= 24
                and struct.unpack_from('<I', head, 20)[0]
                >= _MAP_COMPRESSED_VERSION):
            return REASON_COMPRESSED
        return None
    magic = MAGIC_COMMON if kind == 'ce' else MAGIC_DB
    if header_utf8(head, magic, _DAT_UTF8_AT) is None or len(head) < 11:
        return None
    version = head[10]
    if kind == 'ce' and version in _CE_COMPRESSED_VERSIONS:
        return REASON_COMPRESSED
    if kind == 'db' and version == _DB_COMPRESSED_VERSION:
        return REASON_COMPRESSED
    return None


def find_unsupported(data_dir) -> Optional[tuple[str, str]]:
    """(relative path, reason) of the first file this version must refuse.

    Every file the exporter would write is checked (each map too: a few
    header bytes each).  One such file refuses the whole game: a project
    missing part of the game, or a game with some files written, would be
    worse than a clear "not yet".
    """
    data_dir = Path(data_dir)
    basic = data_dir / 'BasicData'
    for name, kind in [(_CE_NAME, 'ce')] + [(n, 'db') for n in _DB_NAMES]:
        reason = file_refusal(basic / name, kind)
        if reason:
            return f'BasicData/{name}', reason
    map_dir = data_dir / 'MapData'
    if map_dir.is_dir():
        for mps in sorted(map_dir.glob('*.mps')):
            reason = file_refusal(mps, 'map')
            if reason:
                return f'MapData/{mps.name}', reason
    return None


# Map structure markers
MAP_EVENT_INDICATOR = 0x6F
MAP_EVENT_TERMINATOR = 0x66
MAP_EVENT_MAGIC1 = bytes([0x39, 0x30, 0x00, 0x00])
MAP_EVENT_MAGIC2 = bytes([0x00, 0x00, 0x00, 0x00])
MAP_PAGE_START = 0x79
MAP_PAGE_END = 0x7A
MAP_EVENT_END = 0x70

# CommonEvent markers
CE_HEADER = 0x8E
CE_DATA_INDICATOR = 0x8F
CE_END1 = 0x91
CE_END2 = 0x92

# Command codes (from WolfTL Command.hpp)
CMD_MESSAGE        = 101
CMD_CHOICES        = 102
CMD_COMMENT        = 103
CMD_SET_STRING     = 122
CMD_PICTURE        = 150
CMD_DB             = 250
CMD_COMMON_BY_NAME = 300
CMD_CHOICE_CASE    = 401
CMD_MOVE           = 201

# Japanese detection
JAPANESE_RE = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]')

# Filter out file paths, color codes, and non-display strings from DB
_DB_SKIP_RE = re.compile(
    r'^[\w\-/\\]+\.\w{2,4}$'   # file paths (BGM/foo.mp3)
    # asset paths with spaces, brackets etc. ("SE2/着信01 (2).wav")
    r'|^[^\n:*?"<>|]+\.(?i:png|jpe?g|bmp|gif|webp|ogg|wav|mp3|mp4|webm|mid'
    r'|midi|avi|wmv|mpg|ttf|otf|ico|txt|csv|dat|mps|wolf)$'
    r'|^\d+$'                   # pure numbers
    r'|^#[0-9A-Fa-f]{3,8}$'    # color codes
    r'|^$',                     # empty
    re.MULTILINE
)


# Message speaker prefix: optional "@N" face line, then a short "Name：" line.
# Shared by _extract_speaker / _clean_message / _rebuild_message so load and
# export always agree on what the prefix is.
_SPEAKER_PREFIX_RE = re.compile(r'^(@\d+\r?\n)?([^\n：]{1,20})：\r?\n')
_FACE_PREFIX_RE = re.compile(r'^@\d+\r?\n')


# ── Binary reader ────────────────────────────────────────────────────────

def text_encoding(is_utf8: bool) -> str:
    """The text encoding of a file: UTF-8 (3.x mark) or Shift-JIS (cp932)."""
    return 'utf-8' if is_utf8 else 'cp932'


def fit_encoding(text: str, is_utf8: bool) -> tuple[str, bool]:
    """(text the file can store, whether anything had to be replaced).

    Characters the file's encoding can't hold (an accented letter in a
    Shift-JIS game, a lone surrogate anywhere) become '?'.  The caller
    reports every replacement; nothing is replaced silently.
    """
    enc = text_encoding(is_utf8)
    try:
        text.encode(enc)
        return text, False
    except UnicodeEncodeError:
        return text.encode(enc, errors='replace').decode(enc), True


class BinaryReader:
    """Lightweight binary reader for Wolf RPG files.

    ``is_utf8`` comes from the file's own header byte.  ``read_string``
    decodes strictly (a bad byte raises WolfTextError rather than turning
    into U+FFFD and being written back as '?'); ``skip_string`` steps over
    strings that are kept as raw bytes; ``read_label`` is for names that
    are only shown in logs and never written back.
    """

    __slots__ = ('data', 'pos', 'is_utf8')

    def __init__(self, data: bytes | bytearray, is_utf8: bool = False):
        self.data = data
        self.pos = 0
        self.is_utf8 = is_utf8

    def read(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError(f'unexpected end of file at byte {self.pos}')
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def read_byte(self) -> int:
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_uint32(self) -> int:
        val = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return val

    def _raw_string(self) -> tuple[int, bytes]:
        offset = self.pos
        length = self.read_uint32()
        if length == 0:
            return offset, b''
        raw = self.read(length)
        # Strip trailing null
        if raw[-1] == 0:
            raw = raw[:-1]
        return offset, raw

    def read_string(self) -> str:
        offset, raw = self._raw_string()
        enc = text_encoding(self.is_utf8)
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            raise WolfTextError(enc, offset) from None

    def read_label(self) -> str:
        return self._raw_string()[1].decode(
            text_encoding(self.is_utf8), errors='replace')

    def skip_string(self):
        self._raw_string()

    def skip(self, n: int):
        self.read(n)

    def eof(self) -> bool:
        return self.pos >= len(self.data)

    def peek(self) -> int:
        return self.data[self.pos]


# ── Binary writer ────────────────────────────────────────────────────────

class BinaryWriter:
    """Writes Wolf RPG binary data.  Strings are encoded strictly in the
    file's encoding: translations are fitted first (fit_encoding)."""

    __slots__ = ('buf', 'is_utf8')

    def __init__(self, is_utf8: bool = False):
        self.buf = bytearray()
        self.is_utf8 = is_utf8

    def write(self, data: bytes | bytearray):
        self.buf.extend(data)

    def write_byte(self, val: int):
        self.buf.append(val & 0xFF)

    def write_uint32(self, val: int):
        self.buf.extend(struct.pack('<I', val))

    def write_string(self, s: str):
        encoded = s.encode(text_encoding(self.is_utf8)) + b'\x00'
        self.write_uint32(len(encoded))
        self.write(encoded)

    def getvalue(self) -> bytes:
        return bytes(self.buf)


# ── Command parsing ──────────────────────────────────────────────────────

class WolfCommand:
    """A single Wolf RPG event command."""

    __slots__ = ('code', 'int_args', 'string_args', 'indent',
                 'move_data', 'v35_data')

    def __init__(self, code: int, int_args: list[int],
                 string_args: list[str], indent: int):
        self.code = code
        self.int_args = int_args
        self.string_args = string_args
        self.indent = indent
        self.move_data = b''   # Extra data for Move commands
        self.v35_data = b''    # Extra data for v3.5

    @staticmethod
    def read(reader: BinaryReader) -> 'WolfCommand':
        arg_count = reader.read_byte() - 1
        code = reader.read_uint32()
        int_args = [reader.read_uint32() for _ in range(arg_count)]
        indent = reader.read_byte()
        str_count = reader.read_byte()
        string_args = [reader.read_string() for _ in range(str_count)]

        cmd = WolfCommand(code, int_args, string_args, indent)

        terminator = reader.read_byte()
        if terminator == 0x01:
            # Move command has extra data after terminator
            cmd.move_data = _read_move_data(reader)
        elif terminator != 0x00:
            raise ValueError(f'Bad command terminator: {terminator:#x}')

        return cmd

    def write(self, writer: BinaryWriter):
        writer.write_byte(len(self.int_args) + 1)
        writer.write_uint32(self.code)
        for arg in self.int_args:
            writer.write_uint32(arg)
        writer.write_byte(self.indent)
        writer.write_byte(len(self.string_args))
        for s in self.string_args:
            writer.write_string(s)
        if self.move_data:
            writer.write_byte(0x01)
            writer.write(self.move_data)
        else:
            writer.write_byte(0x00)

    def is_translatable(self) -> bool:
        """Check if this command contains translatable text."""
        if not self.string_args:
            return False
        if self.code == CMD_MESSAGE:
            return True
        if self.code == CMD_CHOICES:
            return True
        if self.code == CMD_SET_STRING:
            return any(JAPANESE_RE.search(s) for s in self.string_args)
        if self.code == CMD_PICTURE:
            # Only picture-as-text (type bits 4-6 == 2)
            if self.int_args:
                pic_type = (self.int_args[0] >> 4) & 0x07
                if pic_type == 2:  # text type
                    return any(JAPANESE_RE.search(s) for s in self.string_args)
            return False
        return False


def _read_move_data(reader: BinaryReader) -> bytes:
    """Read the extra data after a Move command (route commands)."""
    start = reader.pos
    # 5 unknown bytes + 1 flags byte
    reader.skip(6)
    route_count = reader.read_uint32()
    for _ in range(route_count):
        _id = reader.read_byte()
        arg_count = reader.read_byte()
        reader.skip(arg_count * 4)
        # Route terminator: 01 00
        reader.skip(2)
    return reader.data[start:reader.pos]


# ── Route command skip helper for writing ────────────────────────────────

def _read_route_commands(reader: BinaryReader) -> list:
    """Read route commands (for Page parsing), return raw bytes."""
    count = reader.read_uint32()
    start = reader.pos - 4
    for _ in range(count):
        reader.read_byte()   # id
        argc = reader.read_byte()
        reader.skip(argc * 4)
        reader.skip(2)       # terminator 01 00
    return reader.data[start:reader.pos]


# ── Map parsing ──────────────────────────────────────────────────────────

class WolfMap:
    """Parses a Wolf RPG .mps map file."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        self.events: list[WolfEvent] = []
        self.is_utf8 = False
        self._header_data = b''
        self._raw = b''

    def load(self):
        self._raw = self.path.read_bytes()
        is_utf8 = header_utf8(self._raw, MAGIC_MAP, _MAP_UTF8_AT)
        if is_utf8 is None:
            raise ValueError(f'{self.path.name}: bad map magic')
        self.is_utf8 = is_utf8
        reader = BinaryReader(self._raw, is_utf8)
        reader.skip(len(MAGIC_MAP))

        version = reader.read_uint32()
        if version >= _MAP_COMPRESSED_VERSION:
            # find_unsupported refuses these first; never half-read one
            raise ValueError(f'{self.path.name} {REASON_COMPRESSED}')
        reader.read_byte()      # unknown2
        reader.skip_string()    # unknown3 (kept as raw bytes in the header)

        reader.read_uint32()    # tileset id
        width = reader.read_uint32()
        height = reader.read_uint32()
        event_count = reader.read_uint32()

        # v3.5 extra fields
        layer_count = 3
        if version >= 0x67:
            reader.read_uint32()  # unknown4
            layer_count = reader.read_uint32()

        # Tile data.  A map ending right after its header has none (and no
        # events); a 3.x (UTF-8) map may store FFFFFFFF instead of its
        # tiles (WolfTL Map.hpp).
        last = len(self._raw) - 1
        if reader.pos != last:
            if (is_utf8 and reader.pos + 4 <= len(self._raw) and struct.unpack_from(
                    '<I', self._raw, reader.pos)[0] == 0xFFFFFFFF):
                reader.skip(4)
            else:
                reader.skip(width * height * layer_count * 4)

        # Save everything up to events as header
        self._header_data = self._raw[:reader.pos]

        # Read events
        while reader.pos < last and reader.peek() == MAP_EVENT_INDICATOR:
            reader.read_byte()  # consume indicator
            ev = WolfEvent.read(reader)
            self.events.append(ev)
        if len(self.events) != event_count:
            raise ValueError(f'{self.path.name}: {len(self.events)} events '
                             f'read, header says {event_count}')

        terminator = reader.read_byte()
        if terminator != MAP_EVENT_TERMINATOR:
            raise ValueError(f'{self.path.name}: bad map terminator {terminator:#x}')
        if not reader.eof():
            raise ValueError(f'{self.path.name}: '
                             f'{len(self._raw) - reader.pos} unexpected '
                             'bytes after the end of the map')

    def save(self, path: Optional[Path] = None):
        """Write map back to file with modified commands."""
        writer = BinaryWriter(self.is_utf8)
        writer.write(self._header_data)
        for ev in self.events:
            writer.write_byte(MAP_EVENT_INDICATOR)
            ev.write(writer)
        writer.write_byte(MAP_EVENT_TERMINATOR)
        target = path or self.path
        game_io.atomic_write_bytes(target, writer.getvalue())


class WolfEvent:
    """A map event containing pages of commands."""

    __slots__ = ('id', 'name', 'x', 'y', 'pages', '_magic1', '_magic2')

    @staticmethod
    def read(reader: BinaryReader) -> 'WolfEvent':
        ev = WolfEvent()
        ev._magic1 = reader.read(4)
        if ev._magic1 != MAP_EVENT_MAGIC1:
            raise ValueError(f'Bad event magic1: {ev._magic1.hex()}')
        ev.id = reader.read_uint32()
        ev.name = reader.read_string()
        ev.x = reader.read_uint32()
        ev.y = reader.read_uint32()
        page_count = reader.read_uint32()
        ev._magic2 = reader.read(4)

        ev.pages = []
        page_id = 0
        while reader.peek() == MAP_PAGE_START:
            reader.read_byte()
            page = WolfPage.read(reader, page_id)
            ev.pages.append(page)
            page_id += 1

        end = reader.read_byte()
        if end != MAP_EVENT_END:
            raise ValueError(f'Bad event end: {end:#x}')

        if len(ev.pages) != page_count:
            raise ValueError(f'Page count mismatch: expected {page_count}, got {len(ev.pages)}')

        return ev

    def write(self, writer: BinaryWriter):
        writer.write(self._magic1)
        writer.write_uint32(self.id)
        writer.write_string(self.name)
        writer.write_uint32(self.x)
        writer.write_uint32(self.y)
        writer.write_uint32(len(self.pages))
        writer.write(self._magic2)
        for page in self.pages:
            writer.write_byte(MAP_PAGE_START)
            page.write(writer)
        writer.write_byte(MAP_EVENT_END)


class WolfPage:
    """An event page containing commands."""

    __slots__ = ('id', 'commands', '_pre_command_data', '_post_command_data',
                 '_features')

    @staticmethod
    def read(reader: BinaryReader, page_id: int) -> 'WolfPage':
        page = WolfPage()
        page.id = page_id

        # Pre-command data: unknown1(4) + graphic_name(str) + 4 bytes +
        #   conditions(1+4+16+16) + movement(4) + flags(1) + route_flags(1) + routes
        pre_start = reader.pos
        reader.read_uint32()     # unknown1
        reader.skip_string()     # graphic name (kept as raw bytes)
        reader.skip(4)           # direction, frame, opacity, render mode
        reader.skip(1 + 4 + 16 + 16)  # conditions
        reader.skip(4)           # movement
        reader.skip(1)           # flags
        reader.skip(1)           # route flags
        # Route commands
        route_count = reader.read_uint32()
        for _ in range(route_count):
            reader.read_byte()   # id
            argc = reader.read_byte()
            reader.skip(argc * 4)
            reader.skip(2)       # terminator 01 00
        page._pre_command_data = reader.data[pre_start:reader.pos]

        # Commands
        cmd_count = reader.read_uint32()
        page.commands = []
        for _ in range(cmd_count):
            cmd = WolfCommand.read(reader)
            page.commands.append(cmd)

        # Post-command data: features(4) + shadow(1) + collision_w(1) + collision_h(1)
        post_start = reader.pos
        page._features = reader.read_uint32()
        reader.skip(3)  # shadow, collision w/h
        if page._features > 3:
            reader.skip(1)  # page transfer
        page._post_command_data = reader.data[post_start:reader.pos]

        terminator = reader.read_byte()
        if terminator != MAP_PAGE_END:
            raise ValueError(f'Bad page terminator: {terminator:#x}')

        return page

    def write(self, writer: BinaryWriter):
        writer.write(self._pre_command_data)
        writer.write_uint32(len(self.commands))
        for cmd in self.commands:
            cmd.write(writer)
        writer.write(self._post_command_data)
        writer.write_byte(MAP_PAGE_END)


# ── CommonEvent parsing ──────────────────────────────────────────────────

class WolfCommonEvent:
    """A single common event."""

    __slots__ = ('id', 'int_id', 'name', 'description', 'commands',
                 '_pre_data', '_post_data')

    @staticmethod
    def read(reader: BinaryReader, idx: int) -> 'WolfCommonEvent':
        ce = WolfCommonEvent()
        ce.id = idx

        indicator = reader.read_byte()
        if indicator != CE_HEADER:
            raise ValueError(f'Bad CE header: {indicator:#x}')

        ce.int_id = reader.read_uint32()
        unknown1 = reader.read_uint32()
        unknown2 = reader.read(7)
        ce._pre_data = struct.pack('<I', unknown1) + unknown2

        ce.name = reader.read_string()

        cmd_count = reader.read_uint32()
        ce.commands = [WolfCommand.read(reader) for _ in range(cmd_count)]

        # Capture everything from unknown11 onward as _post_data
        # (unknown11 + description + all trailing structures)
        # (all kept as raw bytes: skipped, not decoded)
        post_start = reader.pos
        reader.skip_string()    # unknown11
        ce.description = reader.read_label()
        indicator = reader.read_byte()
        if indicator != CE_DATA_INDICATOR:
            raise ValueError(f'Bad CE data indicator: {indicator:#x}')

        # unknown3: string array
        count = reader.read_uint32()
        for _ in range(count):
            reader.skip_string()

        # unknown4: byte array
        count = reader.read_uint32()
        reader.skip(count)

        # unknown5: array of string arrays
        count = reader.read_uint32()
        for _ in range(count):
            sub_count = reader.read_uint32()
            for _ in range(sub_count):
                reader.skip_string()

        # unknown6: array of int arrays
        count = reader.read_uint32()
        for _ in range(count):
            sub_count = reader.read_uint32()
            reader.skip(sub_count * 4)

        # unknown7: 0x1D bytes
        reader.skip(0x1D)

        # unknown8: 100 strings
        for _ in range(100):
            reader.skip_string()

        # End markers
        indicator = reader.read_byte()
        if indicator != CE_END1:
            raise ValueError(f'Bad CE end1: {indicator:#x}')
        reader.skip_string()  # unknown9

        indicator = reader.read_byte()
        if indicator == CE_END2:
            reader.skip_string()  # unknown10
            reader.read_uint32()  # unknown12
            end = reader.read_byte()
            if end != CE_END2:
                raise ValueError(f'Bad CE end2: {end:#x}')
        elif indicator != CE_END1:
            raise ValueError(f'Bad CE final: {indicator:#x}')

        ce._post_data = reader.data[post_start:reader.pos]

        return ce

    def write(self, writer: BinaryWriter):
        writer.write_byte(CE_HEADER)
        writer.write_uint32(self.int_id)
        writer.write(self._pre_data)
        writer.write_string(self.name)
        writer.write_uint32(len(self.commands))
        for cmd in self.commands:
            cmd.write(writer)
        # _post_data includes unknown11 + description + all trailing structures
        writer.write(self._post_data)


class WolfCommonEvents:
    """Container for all common events from CommonEvent.dat."""

    def __init__(self, path: Path):
        self.path = path
        self.events: list[WolfCommonEvent] = []
        self.is_utf8 = False
        self._magic = b''
        self._version = 0
        self._terminator = 0

    def load(self):
        data = self.path.read_bytes()
        if is_protected(data):
            # find_unsupported refuses these first; never half-read one
            raise ValueError(f'{_CE_NAME} {REASON_PROTECTED}')
        is_utf8 = header_utf8(data, MAGIC_COMMON, _DAT_UTF8_AT)
        if is_utf8 is None:
            raise ValueError(f'Bad CommonEvent magic: {data[:10].hex()}')
        self.is_utf8 = is_utf8
        reader = BinaryReader(data, is_utf8)
        self._magic = reader.read(len(MAGIC_COMMON))

        self._version = reader.read_byte()
        if self._version in _CE_COMPRESSED_VERSIONS:
            raise ValueError(f'{_CE_NAME} {REASON_COMPRESSED}')

        event_count = reader.read_uint32()
        for i in range(event_count):
            ce = WolfCommonEvent.read(reader, i)
            self.events.append(ce)

        # 2.x ends with 0x8F, 3.x with e.g. 0x90: WolfTL accepts >= 0x89
        self._terminator = reader.read_byte()
        if self._terminator < 0x89:
            raise ValueError(f'Bad CommonEvent terminator: '
                             f'{self._terminator:#x}')
        if not reader.eof():
            raise ValueError(f'{len(data) - reader.pos} unexpected bytes '
                             f'after the last common event')

    def save(self, path: Optional[Path] = None):
        writer = BinaryWriter(self.is_utf8)
        writer.write(self._magic)
        writer.write_byte(self._version)
        writer.write_uint32(len(self.events))
        for ce in self.events:
            ce.write(writer)
        writer.write_byte(self._terminator)
        target = path or self.path
        game_io.atomic_write_bytes(target, writer.getvalue())


# ── Database parsing ─────────────────────────────────────────────────────

class WolfDatabase:
    """Parses DataBase.dat + .project file pairs for translatable strings.

    Wolf RPG databases use a two-file format:
      - .project file: type/field definitions (names, field types, structure)
      - .dat file: actual data values keyed by the project structure

    Each type has fields that are either int or string (index_info >= 0x07D0).
    Each data entry stores int values first, then string values.
    """

    # Type separator in .dat files
    DAT_TYPE_SEP = bytes([0xFE, 0xFF, 0xFF, 0xFF])
    STRING_INDICATOR = 0x0001D4C0
    FIELD_STRING_START = 0x07D0
    FIELD_INT_START = 0x03E8

    def __init__(self, dat_path: Path, project_path: Optional[Path] = None):
        self.dat_path = dat_path
        self.project_path = project_path or dat_path.with_suffix('.project')
        self.name = dat_path.stem
        self.strings: list[tuple[int, int, int, str]] = []  # (type_idx, data_idx, field_idx, text)
        self._types: list[dict] = []  # Parsed type info from .project

    def load(self):
        """Parse database files and extract translatable strings."""
        if not self.project_path.is_file():
            return  # Can't parse .dat without .project definitions

        dat_data = self.dat_path.read_bytes()
        proj_data = self.project_path.read_bytes()
        is_utf8 = self.dat_header(dat_data, self.dat_path.name)

        # Parse .project file first to get type/field structure.  It has
        # no header of its own: its text is in the encoding of its .dat.
        self._parse_project(proj_data, is_utf8)

        # Parse .dat file using the structure from .project
        self._parse_dat(dat_data)

    @staticmethod
    def dat_header(data: bytes, name: str = 'database') -> bool:
        """UTF-8 flag of a database .dat; ValueError if it isn't one we read."""
        if is_protected(data):
            raise ValueError(f'{name} {REASON_PROTECTED}')
        is_utf8 = header_utf8(data, MAGIC_DB, _DAT_UTF8_AT)
        if is_utf8 is None:
            raise ValueError(f'{name}: bad database magic {data[:10].hex()}')
        if len(data) > 10 and data[10] == _DB_COMPRESSED_VERSION:
            raise ValueError(f'{name} {REASON_COMPRESSED}')
        return is_utf8

    def _parse_project(self, data: bytes, is_utf8: bool = False):
        """Parse .project file for type and field definitions.

        Its strings are names and descriptions, never extracted or written
        back, so they are read leniently (read_label / skip_string).
        """
        reader = BinaryReader(data, is_utf8)
        type_count = reader.read_uint32()
        if type_count > 100000:
            raise ValueError(f'{self.project_path.name}: implausible type '
                             f'count {type_count}')

        for _ in range(type_count):
            type_info = {}
            type_info['name'] = reader.read_label()

            # Field definitions
            field_count = reader.read_uint32()
            fields = []
            for _ in range(field_count):
                field_name = reader.read_label()
                fields.append({'name': field_name})
            type_info['fields'] = fields

            # Data names
            data_count = reader.read_uint32()
            data_names = []
            for _ in range(data_count):
                data_names.append(reader.read_label())
            type_info['data_names'] = data_names

            # Description
            type_info['description'] = reader.read_label()

            # Field type list
            field_type_list_size = reader.read_uint32()
            for i in range(min(field_count, field_type_list_size)):
                fields[i]['type'] = reader.read_byte()
            # Skip remaining
            remaining = field_type_list_size - min(field_count, field_type_list_size)
            if remaining > 0:
                reader.skip(remaining)

            # Unknown1 strings per field
            count = reader.read_uint32()
            for i in range(count):
                reader.skip_string()

            # StringArgs per field
            count = reader.read_uint32()
            for i in range(count):
                sub_count = reader.read_uint32()
                for _ in range(sub_count):
                    reader.skip_string()

            # IntArgs per field
            count = reader.read_uint32()
            for i in range(count):
                sub_count = reader.read_uint32()
                reader.skip(sub_count * 4)

            # Default values per field
            count = reader.read_uint32()
            reader.skip(count * 4)

            self._types.append(type_info)
        if not reader.eof():
            raise ValueError(f'{self.project_path.name}: '
                             f'{len(data) - reader.pos} unexpected bytes '
                             'after the last type')

    def _parse_dat(self, data: bytes):
        """Parse .dat file using structure from .project.

        Strict: a header, layout or ending that doesn't match raises, so a
        database is either read whole or reported (never half-read).
        """
        reader = BinaryReader(data, self.dat_header(data, self.dat_path.name))
        reader.skip(len(MAGIC_DB))
        version = reader.read_byte()

        # Type count
        type_count = reader.read_uint32()
        if type_count != len(self._types):
            raise ValueError(f'{self.dat_path.name}: {type_count} types, but '
                             f'its .project has {len(self._types)}')

        for type_idx in range(type_count):
            self._parse_dat_type(reader, type_idx)

        # Ends with its version byte again (WolfTL Database.hpp)
        if reader.eof() or reader.read_byte() != version or not reader.eof():
            raise ValueError(f'{self.dat_path.name}: unexpected end of the '
                             'database')

    def _parse_dat_type(self, reader: BinaryReader, type_idx: int):
        """Parse one type from the .dat file."""
        type_info = self._types[type_idx]

        # Type separator: FE FF FF FF
        sep = reader.read(4)
        if sep != self.DAT_TYPE_SEP:
            raise ValueError(f'Bad type separator: {sep.hex()}')

        unknown1 = reader.read_uint32()
        fields_size = reader.read_uint32()

        if unknown1 == self.STRING_INDICATOR:
            reader.skip_string()  # unknown2

        # Read field index_info values
        field_infos = []
        for _ in range(fields_size):
            index_info = reader.read_uint32()
            is_string = index_info >= self.FIELD_STRING_START
            is_int = index_info >= self.FIELD_INT_START and not is_string
            field_infos.append({
                'index_info': index_info,
                'is_string': is_string,
                'is_valid': is_string or is_int,
            })

        # Data count
        data_count = reader.read_uint32()

        # Read data entries
        int_count = sum(1 for f in field_infos if f['is_valid'] and not f['is_string'])
        str_count = sum(1 for f in field_infos if f['is_string'])

        for data_idx in range(data_count):
            # Int values first
            reader.skip(int_count * 4)

            # String values
            for si in range(str_count):
                text = reader.read_string()
                if (text and JAPANESE_RE.search(text)
                        and not _DB_SKIP_RE.match(text.split('\n')[0])):
                    self.strings.append((type_idx, data_idx, si, text))


def _apply_string_patches(data: bytearray,
                          patches: list[tuple[int, int, bytes]]):
    """Replace length-prefixed NUL-terminated strings in ``data`` in place.

    Each patch is (offset_of_length_prefix, old_total_len, new_bytes) where
    old_total_len = 4 + stored length (stored length includes the NUL).
    Applied in descending offset order so earlier offsets stay valid.
    """
    for offset, old_total_len, new_bytes in sorted(
            patches, key=lambda p: p[0], reverse=True):
        data[offset:offset + old_total_len] = (
            struct.pack('<I', len(new_bytes) + 1) + new_bytes + b'\x00')


# ── DXArchive unpacker ───────────────────────────────────────────────────

def unpack_data_wolf(game_dir: Path) -> Path:
    """Unpack Data.wolf into Data/ with the bundled UberWolf CLI.

    Returns the data folder.  Raises WolfUnpackError (a plain message for
    users) when it can't; the game folder is then left as it was.
    """
    return wolf_unpack.unpack_data_wolf(Path(game_dir))


# ── Main parser ──────────────────────────────────────────────────────────

class WolfRPGParser:
    """Parser for Wolf RPG Editor games."""

    def __init__(self):
        self.game_dir: Optional[Path] = None
        self.data_dir: Optional[Path] = None
        self.maps: list[WolfMap] = []
        self.common_events: Optional[WolfCommonEvents] = None
        self.databases: list[WolfDatabase] = []
        self.context_size: int = 3
        # Files the last load_project() could not read, for the UI
        self.load_warnings: list[str] = []

    # ── Detection ─────────────────────────────────────────────────────

    @staticmethod
    def detect(folder: str) -> bool:
        """Check if folder is a Wolf RPG game."""
        p = Path(folder)
        has_wolf = (p / 'Data.wolf').is_file()
        has_data = (p / 'Data').is_dir()

        # Game.exe, or GamePro.exe (WOLF RPG Editor Pro / 3.x runtime)
        has_exe = any((p / n).is_file() for n in (
            'Game.exe', 'game.exe', 'GamePro.exe', 'gamepro.exe'))
        if not has_exe:
            # Accept if Data.wolf exists (exe may be renamed)
            if not has_wolf:
                return False

        # Must have Data.wolf or Data/ folder with .mps files
        if has_wolf:
            return True
        if has_data:
            mps = list((p / 'Data').glob('**/*.mps'))
            return len(mps) > 0

        return False

    # ── Loading ───────────────────────────────────────────────────────

    def load_project(self, folder: str, context_size: int | None = None) -> list[TranslationEntry]:
        """Load all translatable strings from a Wolf RPG game."""
        if context_size is not None:
            self.context_size = context_size
        self.game_dir = Path(folder)
        entries: list[TranslationEntry] = []
        self.maps = []
        self.common_events = None
        self.databases = []

        self.load_warnings = []

        # Find data directory
        existed = self._unpack_targets_existing()
        try:
            self.data_dir = self._find_data_dir()
        except WolfArchiveUnsupported as e:
            log.warning('Wolf RPG game not supported (%s %s)', e.where, e.reason)
            self.load_warnings.append(
                self._refusal_message(e.where, e.reason, existed))
            return []
        except WolfUnpackError as e:
            log.warning('Data.wolf unpack failed: %s', e.reason)
            self.load_warnings.append(str(e))
            return []

        bad = find_unsupported(self.data_dir)
        if bad:
            # Compressed or protected: offer nothing rather than a partial
            # project (and an export that writes only part of the game).
            log.warning('Wolf RPG game not supported (%s %s)', *bad)
            self.load_warnings.append(self._refusal_message(*bad, existed))
            return []

        try:
            entries = self._load_files()
        except WolfTextError as e:
            # Text that doesn't decode would be shown garbled and written
            # back damaged: refuse the game, not just that file.
            log.warning('Wolf RPG game not supported (%s %s)', e.where, e)
            self.maps, self.common_events, self.databases = [], None, []
            self.load_warnings = [self._refusal_message(e.where, str(e),
                                                        existed)]
            return []

        # Assign IDs as string paths (file/field) for event viewer compatibility
        for entry in entries:
            entry.id = f'{entry.file}/{entry.field}'

        return entries

    def _load_files(self) -> list[TranslationEntry]:
        """Parse every map, CommonEvent.dat and database in data_dir.

        A file that fails to parse is a load warning for that file alone;
        a WolfTextError (with ``where`` set) is raised to refuse the game.
        """
        entries: list[TranslationEntry] = []
        # Parse maps
        map_dir = self.data_dir / 'MapData'
        if map_dir.is_dir():
            for mps_file in sorted(map_dir.glob('*.mps')):
                try:
                    wolf_map = WolfMap(mps_file)
                    wolf_map.load()
                    self.maps.append(wolf_map)
                    entries.extend(self._extract_map_entries(wolf_map, self.context_size))
                except WolfTextError as e:
                    e.where = f'MapData/{mps_file.name}'
                    raise
                except Exception as e:
                    log.warning('Failed to parse %s: %s', mps_file.name, e)
                    self.load_warnings.append(f'MapData/{mps_file.name}: {e}')

        # Parse common events
        basic_dir = self.data_dir / 'BasicData'
        ce_path = basic_dir / _CE_NAME if basic_dir.is_dir() else None
        if ce_path and ce_path.is_file():
            try:
                self.common_events = WolfCommonEvents(ce_path)
                self.common_events.load()
                entries.extend(self._extract_ce_entries(self.context_size))
            except WolfTextError as e:
                e.where = f'BasicData/{_CE_NAME}'
                raise
            except Exception as e:
                self.common_events = None
                log.warning('Failed to parse CommonEvent.dat: %s', e)
                self.load_warnings.append(f'BasicData/CommonEvent.dat: {e}')

        # Parse databases (skip SysDataBaseBasic — system config only, no text)
        if basic_dir and basic_dir.is_dir():
            for db_name in _DB_NAMES:
                db_path = basic_dir / db_name
                if db_path.is_file():
                    try:
                        db = WolfDatabase(db_path)
                        db.load()
                        self.databases.append(db)
                        entries.extend(self._extract_db_entries(db))
                    except WolfTextError as e:
                        e.where = f'BasicData/{db_name}'
                        raise
                    except Exception as e:
                        log.warning('Failed to parse %s: %s', db_name, e)
                        self.load_warnings.append(f'BasicData/{db_name}: {e}')
        return entries

    def _find_data_dir(self) -> Path:
        """Find or create the Data/ directory."""
        # Check Data/ folder with actual game content
        data_dir = self.game_dir / 'Data'
        if data_dir.is_dir():
            has_maps = (data_dir / 'MapData').is_dir()
            has_basic = (data_dir / 'BasicData').is_dir()
            if has_maps or (has_basic and any(
                    (data_dir / 'BasicData').glob('CommonEvent.dat'))):
                return data_dir

        # Check for loose BasicData/MapData in game root
        if (self.game_dir / 'BasicData').is_dir():
            if (self.game_dir / 'MapData').is_dir():
                return self.game_dir

        # Need to unpack Data.wolf
        wolf_file = self.game_dir / 'Data.wolf'
        if wolf_file.is_file():
            # Protected (Pro) and 3.5 archives are refused from the archive
            # header, before unpacking.  find_unsupported still checks
            # every file that does get unpacked.
            reason = wolf_unpack.archive_refusal(wolf_file)
            if reason:
                raise WolfArchiveUnsupported(reason)
            return unpack_data_wolf(self.game_dir)

        raise FileNotFoundError(
            f'No Data/ folder or Data.wolf in {self.game_dir}')

    # ── Entry extraction ──────────────────────────────────────────────

    def _extract_map_entries(self, wolf_map: WolfMap,
                             context_size: int) -> list[TranslationEntry]:
        """Extract translatable entries from a map."""
        entries = []
        recent_context = []
        file_name = f'MapData/{wolf_map.name}'

        for ev in wolf_map.events:
            for page in ev.pages:
                for ci, cmd in enumerate(page.commands):
                    if not cmd.is_translatable():
                        continue

                    # Build context from recent dialogue
                    context = '\n'.join(recent_context[-context_size:]) if context_size else ''

                    if cmd.code == CMD_MESSAGE:
                        text = cmd.string_args[0] if cmd.string_args else ''
                        if not text or not JAPANESE_RE.search(text):
                            continue
                        # Extract speaker from message format: @N\nSpeaker：\nText
                        speaker = self._extract_speaker(text)
                        entry = TranslationEntry(
                            id=0,
                            file=file_name,
                            field=f'Ev{ev.id}/p{page.id}/msg{ci}',
                            original=self._clean_message(text),
                            translation='',
                            status='untranslated',
                            context=f'[Speaker: {speaker}]\n{context}' if speaker else context,
                        )
                        entries.append(entry)
                        recent_context.append(text[:80])

                    elif cmd.code == CMD_CHOICES:
                        for si, choice in enumerate(cmd.string_args):
                            if choice and JAPANESE_RE.search(choice):
                                entry = TranslationEntry(
                                    id=0,
                                    file=file_name,
                                    field=f'Ev{ev.id}/p{page.id}/choice{ci}_{si}',
                                    original=choice,
                                    translation='',
                                    status='untranslated',
                                    context=context,
                                )
                                entries.append(entry)

                    elif cmd.code == CMD_SET_STRING:
                        for si, s in enumerate(cmd.string_args):
                            if s and JAPANESE_RE.search(s):
                                entry = TranslationEntry(
                                    id=0,
                                    file=file_name,
                                    field=f'Ev{ev.id}/p{page.id}/str{ci}_{si}',
                                    original=s,
                                    translation='',
                                    status='untranslated',
                                    context=context,
                                )
                                entries.append(entry)

                    elif cmd.code == CMD_PICTURE:
                        text = cmd.string_args[0] if cmd.string_args else ''
                        if text and JAPANESE_RE.search(text):
                            entry = TranslationEntry(
                                id=0,
                                file=file_name,
                                field=f'Ev{ev.id}/p{page.id}/pic{ci}',
                                original=text,
                                translation='',
                                status='untranslated',
                                context=context,
                            )
                            entries.append(entry)

        return entries

    def _extract_ce_entries(self, context_size: int) -> list[TranslationEntry]:
        """Extract translatable entries from common events."""
        entries = []

        for ce in self.common_events.events:
            recent_context = []
            file_name = 'CommonEvents'

            for ci, cmd in enumerate(ce.commands):
                if not cmd.is_translatable():
                    continue

                context = '\n'.join(recent_context[-context_size:]) if context_size else ''

                if cmd.code == CMD_MESSAGE:
                    text = cmd.string_args[0] if cmd.string_args else ''
                    if not text or not JAPANESE_RE.search(text):
                        continue
                    speaker = self._extract_speaker(text)
                    entry = TranslationEntry(
                        id=0,
                        file=file_name,
                        field=f'CE{ce.int_id}/msg{ci}',
                        original=self._clean_message(text),
                        translation='',
                        status='untranslated',
                        context=f'[Speaker: {speaker}]\n{context}' if speaker else context,
                    )
                    entries.append(entry)
                    recent_context.append(text[:80])

                elif cmd.code == CMD_CHOICES:
                    for si, choice in enumerate(cmd.string_args):
                        if choice and JAPANESE_RE.search(choice):
                            entry = TranslationEntry(
                                id=0,
                                file=file_name,
                                field=f'CE{ce.int_id}/choice{ci}_{si}',
                                original=choice,
                                translation='',
                                status='untranslated',
                                context=context,
                            )
                            entries.append(entry)

                elif cmd.code == CMD_SET_STRING:
                    for si, s in enumerate(cmd.string_args):
                        if s and JAPANESE_RE.search(s):
                            entry = TranslationEntry(
                                id=0,
                                file=file_name,
                                field=f'CE{ce.int_id}/str{ci}_{si}',
                                original=s,
                                translation='',
                                status='untranslated',
                                context=context,
                            )
                            entries.append(entry)

                elif cmd.code == CMD_PICTURE:
                    text = cmd.string_args[0] if cmd.string_args else ''
                    if text and JAPANESE_RE.search(text):
                        entry = TranslationEntry(
                            id=0,
                            file=file_name,
                            field=f'CE{ce.int_id}/pic{ci}',
                            original=text,
                            translation='',
                            status='untranslated',
                            context=context,
                        )
                        entries.append(entry)

        return entries

    def _extract_db_entries(self, db: WolfDatabase) -> list[TranslationEntry]:
        """Extract translatable entries from a database file."""
        entries = []
        file_name = f'Database/{db.name}'

        for type_idx, data_idx, field_idx, text in db.strings:
            entry = TranslationEntry(
                id=0,
                file=file_name,
                field=f'Type{type_idx}/Data{data_idx}/F{field_idx}',
                original=text,
                translation='',
                status='untranslated',
                context='',
            )
            entries.append(entry)

        return entries

    # ── Message helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_speaker(text: str) -> str:
        """Extract speaker name from Wolf RPG message format.

        Format: @N\\nSpeaker：\\nText  or  Speaker：\\nText
        """
        match = _SPEAKER_PREFIX_RE.match(text)
        return match.group(2).strip() if match else ''

    @staticmethod
    def _clean_message(text: str) -> str:
        """Clean message text for display.

        Removes @N prefixes and speaker：prefix, keeps just the dialogue.
        Only the first line (after an optional @N line) is treated as a
        speaker line, matching _rebuild_message exactly.
        """
        match = _SPEAKER_PREFIX_RE.match(text)
        if match:
            return text[match.end():].strip()
        # No speaker format, return as-is but strip @N prefix
        text = _FACE_PREFIX_RE.sub('', text)
        return text.strip()

    # ── Export ────────────────────────────────────────────────────────

    def save_project(self, project_dir: str, entries: list[TranslationEntry]):
        """Write translations back into game files."""
        # Don't rely on load_project state — export can run straight after
        # restoring a saved project state.
        self.game_dir = Path(project_dir)
        existed = self._unpack_targets_existing()
        try:
            self.data_dir = self._find_data_dir()
        except WolfArchiveUnsupported as e:
            raise WolfVersionUnsupported(self._refusal_message(
                e.where, e.reason, existed)) from None

        # Refuse before anything is backed up or written: compressed or
        # protected files can't be written (live files, and the backup the
        # export reads from).
        backup = self._backup_dir_for(self.data_dir)
        bad = find_unsupported(self.data_dir) or (
            find_unsupported(backup) if backup.is_dir() else None)
        if bad:
            raise WolfVersionUnsupported(self._refusal_message(*bad, existed))

        # Create backup on first export.  Only the BasicData/ and MapData/
        # subtrees are ever written, so only those are backed up -- when the
        # data sits in the game root this must never copy (or later restore)
        # the whole game folder with its save files.
        backup_dir = self._backup_dir_for(self.data_dir)
        manifest = game_io.ExportManifest(str(backup_dir) + '.manifest.json')
        if game_io.backup_tree_once(str(self.data_dir), str(backup_dir),
                                    only=list(_WOLF_DATA_SUBDIRS)):
            manifest.start_with_backup()
        self.last_export_notes: list[str] = []

        # Files changed outside the translator (game update, editor
        # re-save, hand edit) are not written; the backup is never touched.
        outside = set(game_io.changed_outside(
            self._data_file_pairs(backup_dir), manifest))
        # their entries' file names, so their lines aren't counted again
        outside_files = set()
        for key in outside:
            stem = key.rsplit('/', 1)[1].rsplit('.', 1)[0]
            outside_files.add(f'MapData/{stem}' if key.startswith('MapData/')
                              else 'CommonEvents' if stem == 'CommonEvent'
                              else f'Database/{stem}')

        # Re-read maps, common events and DB structure from backup for
        # idempotent re-export
        self._reload_from_backup(backup_dir)

        # Build lookup: field → translation (+ the cleaned original it came from)
        translations = {}
        originals = {}
        key_file = {}
        for entry in entries:
            if entry.translation and entry.status in ('translated', 'reviewed'):
                key = f'{entry.file}/{entry.field}'
                translations[key] = entry.translation
                originals[key] = entry.original
                key_file[key] = entry.file
        # keys whose place was found (written, or counted as changed)
        self._visited_keys: set[str] = set()
        # (key, encoding name) of translations written with '?' for
        # characters their file can't store; reported in last_export_notes
        self._unfit: list[tuple[str, str]] = []
        skipped: list[str] = list(self._reload_problems(translations))
        written: list[Path] = []
        # file -> translations not written because the game text changed
        changed: dict[str, int] = {}

        # Apply to maps
        for wolf_map in self.maps:
            file_name = f'MapData/{wolf_map.name}'
            if f'MapData/{wolf_map.path.name}' in outside:
                continue
            modified = False
            for ev in wolf_map.events:
                for page in ev.pages:
                    if self._apply_commands(
                            page.commands, f'{file_name}/Ev{ev.id}/p{page.id}',
                            translations, originals, file_name, changed,
                            is_utf8=wolf_map.is_utf8):
                        modified = True

            # Write to live data dir (map was loaded from backup).  An
            # untouched map is copied back from the backup so lines whose
            # translation was reverted don't keep stale English.
            live_path = self.data_dir / 'MapData' / wolf_map.path.name
            try:
                if modified:
                    wolf_map.save(live_path)
                else:
                    game_io.atomic_copy(str(wolf_map.path), str(live_path))
                written.append(live_path)
            except (OSError, ValueError) as exc:
                # ValueError: text the file's encoding can't store (the
                # writer is strict); the file is not written at all
                skipped.append(f'MapData/{wolf_map.path.name} ({exc})')

        # Apply to common events
        if self.common_events and 'BasicData/CommonEvent.dat' not in outside:
            file_name = 'CommonEvents'
            modified = False
            for ce in self.common_events.events:
                if self._apply_commands(
                        ce.commands, f'{file_name}/CE{ce.int_id}',
                        translations, originals, 'CommonEvent.dat', changed,
                        is_utf8=self.common_events.is_utf8):
                    modified = True

            live_ce = self.data_dir / 'BasicData' / 'CommonEvent.dat'
            try:
                if modified:
                    self.common_events.save(live_ce)
                else:
                    game_io.atomic_copy(str(self.common_events.path),
                                        str(live_ce))
                written.append(live_ce)
            except (OSError, ValueError) as exc:
                # ValueError: as for maps above; nothing was written
                skipped.append(f'CommonEvent.dat ({exc})')

        # Apply to databases.  A database that can't be written is reported
        # once, with its cause; its lines are not counted again below as
        # "game text changed" (review M2).
        unsupported: list[str] = []
        reported_dbs: set[str] = set()
        for db in self.databases:
            file_name = f'Database/{db.name}'
            if f'BasicData/{db.name}.dat' in outside:
                continue
            db_trans = {}
            db_orig = {}
            for key, val in translations.items():
                if key.startswith(file_name + '/'):
                    # key = "Database/DataBase/Type0/Data1/F2"
                    field_part = key[len(file_name) + 1:]
                    db_trans[field_part] = val
                    db_orig[field_part] = originals.get(key)
            backup_dat = backup_dir / 'BasicData' / f'{db.name}.dat'
            live_dat = self.data_dir / 'BasicData' / f'{db.name}.dat'
            if not backup_dat.is_file():
                if db_trans:
                    reported_dbs.add(file_name)
                    unsupported.append(
                        f'{db.name}.dat (not in the backup -- added after '
                        f'the first export?; {len(db_trans)} translations '
                        f'not written)')
                continue
            if not db_trans:
                # Nothing translated (any more): put the original back.
                try:
                    game_io.atomic_copy(str(backup_dat), str(live_dat))
                    written.append(live_dat)
                except OSError as exc:
                    skipped.append(f'{db.name}.dat ({exc})')
                continue
            io_error = False
            try:
                reason = self._export_database(
                    db, backup_dat, live_dat, db_trans, db_orig)
                if self._db_changed and not reason:
                    changed[f'{db.name}.dat'] = self._db_changed
                if not reason:
                    self._visited_keys.update(
                        f'{file_name}/{k}' for k in self._db_visited)
            except OSError as exc:
                reason, io_error = str(exc), True
            except (ValueError, IndexError, struct.error) as exc:
                # layout or text the walk can't follow (WolfTextError is a
                # ValueError): nothing was written for this database
                reason = f'unexpected DB layout ({exc})'
            if reason:
                log.warning('DB %s not exported: %s', db.name, reason)
                reported_dbs.add(file_name)
                (skipped if io_error else unsupported).append(
                    f'{db.name}.dat ({reason}, {len(db_trans)} '
                    f'translations not written)')
            else:
                written.append(live_dat)
        loaded_dbs = {f'Database/{db.name}/' for db in self.databases}
        unloaded = sorted({
            key.split('/')[1] for key in translations
            if key.startswith('Database/')
            and not any(key.startswith(p) for p in loaded_dbs)})
        for name in unloaded:
            skipped.append(f'{name}.dat (could not be re-read from backup)')

        # Remember what we wrote so a later game update can be told apart
        for path in written:
            manifest.record(self._manifest_key(path), str(path))
        manifest.finish()

        # Move Data.wolf aside so the game reads from the folder.
        # os.replace: a stale .bak from an earlier export must not make
        # this fail on Windows.
        wolf_file = self.game_dir / 'Data.wolf'
        if wolf_file.is_file():
            os.replace(wolf_file, self.game_dir / 'Data.wolf.bak')

        # Entries whose place no longer exists (event, command or database
        # row removed by a game update) were never written: count them.
        # Files that could not be re-read are already reported above.
        unreadable = {prefix.rstrip('/') for _label, prefix
                      in getattr(self, '_reload_failures', [])}
        loaded_dbs = {f'Database/{db.name}' for db in self.databases}
        for key, trans in translations.items():
            if key in self._visited_keys or trans == originals.get(key):
                continue
            file = key_file.get(key, '')
            if (file in unreadable or file in reported_dbs
                    or file in outside_files) or (
                    file.startswith('Database/') and file not in loaded_dbs):
                continue
            label = (file.split('/', 1)[1] + '.mps' if file.startswith('MapData/')
                     else 'CommonEvent.dat' if file == 'CommonEvents'
                     else file.split('/')[-1] + '.dat')
            changed[label] = changed.get(label, 0) + 1
        if self._unfit:
            names = sorted({enc for _key, enc in self._unfit})
            shown = ', '.join(key for key, _enc in self._unfit[:5])
            more = (f' and {len(self._unfit) - 5} more'
                    if len(self._unfit) > 5 else '')
            self.last_export_notes.append(
                f"{len(self._unfit)} translated line(s) contain characters "
                f"this game's text ({' / '.join(names)}) can't store; they "
                f"were written with '?' in their place: {shown}{more}. "
                "Edit those lines (for example, use plain quotes and "
                "dashes) and export again.")
            log.warning('Wolf export: %d translation(s) had unstorable '
                        'characters replaced with "?"', len(self._unfit))
        game_io.raise_export_problems(skipped, changed,
                                      unsupported=unsupported,
                                      outside=sorted(outside),
                                      manifest=manifest)

    def _apply_commands(self, commands, key_prefix: str, translations: dict,
                        originals: dict, label: str,
                        changed: dict, is_utf8: bool = False) -> bool:
        """Write translations into one event's command list.

        Entries are addressed by command index, so each write first checks
        that the command still holds the entry's original text.  After a
        game update a changed command keeps its new text and is counted in
        *changed* (under *label*) so the export reports it.  Returns True
        when anything was written.  *is_utf8* is the file's encoding flag:
        translations are fitted to it (see fit_encoding) and every line
        that needed a '?' is recorded for the export notes.
        """
        modified = False

        def fitted(key):
            text, replaced = fit_encoding(translations[key], is_utf8)
            if replaced:
                unfit = getattr(self, '_unfit', None)
                if unfit is not None:
                    unfit.append((key, 'UTF-8' if is_utf8 else 'Shift-JIS'))
            return text

        def still_there(key, raw, message=False):
            getattr(self, "_visited_keys", set()).add(key)
            orig = originals.get(key)
            if orig is None:
                return True
            if message:
                # Exactly what the parser extracts: the message minus its
                # speaker / face prefix.  (A looser "ends with" check let an
                # updated line that merely ended with the old text through,
                # and wrote half-Japanese, half-English messages.)
                ok = self._clean_message(raw) == orig
            else:
                ok = raw == orig
            if not ok and translations[key] != orig:
                changed[label] = changed.get(label, 0) + 1
            return ok

        for ci, cmd in enumerate(commands):
            if cmd.code == CMD_MESSAGE and cmd.string_args:
                key = f'{key_prefix}/msg{ci}'
                if key in translations and still_there(
                        key, cmd.string_args[0], message=True):
                    # Rebuild full message with speaker prefix
                    cmd.string_args[0] = self._rebuild_message(
                        cmd.string_args[0], fitted(key),
                        originals.get(key))
                    modified = True

            elif cmd.code in (CMD_CHOICES, CMD_SET_STRING):
                kind = 'choice' if cmd.code == CMD_CHOICES else 'str'
                for si in range(len(cmd.string_args)):
                    key = f'{key_prefix}/{kind}{ci}_{si}'
                    if key in translations and still_there(
                            key, cmd.string_args[si]):
                        cmd.string_args[si] = fitted(key)
                        modified = True

            elif cmd.code == CMD_PICTURE:
                key = f'{key_prefix}/pic{ci}'
                if (key in translations and cmd.string_args
                        and still_there(key, cmd.string_args[0])):
                    cmd.string_args[0] = fitted(key)
                    modified = True
        return modified

    def _export_database(self, db: WolfDatabase, src_path: Path,
                         dst_path: Path, translations: dict[str, str],
                         originals: Optional[dict] = None
                         ) -> Optional[str]:
        """Patch database .dat file with translations.

        Reads from src_path, replaces strings, writes to dst_path.
        translations keys are like "Type0/Data1/F2".  With *originals*, a
        string is only replaced while it still holds its original text;
        the others are counted in ``self._db_changed``.
        Returns None on success, or a short reason string if skipped.
        """
        self._db_changed = 0
        self._db_visited: set[str] = set()
        data = bytearray(src_path.read_bytes())

        if is_protected(data):
            return 'encrypted (protected) database, export not supported'
        if len(data) > 10 and data[10] == _DB_COMPRESSED_VERSION:
            return 'LZ4-compressed (WOLF RPG Editor 3.5) DB export not supported'
        is_utf8 = header_utf8(bytes(data), MAGIC_DB, _DAT_UTF8_AT)
        if is_utf8 is None:
            return 'not a database file this version can read (bad header)'
        reader = BinaryReader(bytes(data), is_utf8)
        reader.skip(len(MAGIC_DB))
        version = reader.read_byte()

        type_count = reader.read_uint32()
        if type_count != len(db._types):
            return 'type count mismatch with .project'

        # Build list of (byte_offset, old_len_bytes, new_string) patches
        patches = []
        for type_idx in range(type_count):
            type_info = db._types[type_idx]
            sep = reader.read(4)
            if sep != db.DAT_TYPE_SEP:
                return 'unexpected DB layout'

            unknown1 = reader.read_uint32()
            fields_size = reader.read_uint32()

            if unknown1 == db.STRING_INDICATOR:
                reader.skip_string()

            field_infos = []
            for _ in range(fields_size):
                index_info = reader.read_uint32()
                is_string = index_info >= db.FIELD_STRING_START
                is_int = index_info >= db.FIELD_INT_START and not is_string
                field_infos.append({
                    'is_string': is_string,
                    'is_valid': is_string or is_int,
                })

            data_count = reader.read_uint32()
            int_count = sum(1 for f in field_infos if f['is_valid'] and not f['is_string'])
            str_count = sum(1 for f in field_infos if f['is_string'])

            for data_idx in range(data_count):
                reader.skip(int_count * 4)
                for si in range(str_count):
                    str_offset = reader.pos  # offset of length prefix
                    text = reader.read_string()
                    key = f'Type{type_idx}/Data{data_idx}/F{si}'
                    if key in translations:
                        self._db_visited.add(key)
                    orig = (originals or {}).get(key)
                    if (key in translations and orig is not None
                            and text != orig):
                        # Game updated: keep its new text, report the skip
                        if translations[key] != orig:
                            self._db_changed += 1
                        continue
                    if key in translations:
                        # Stored length prefix includes the trailing NUL that
                        # read_string strips -- use the raw on-disk length.
                        raw_len = struct.unpack_from('<I', data, str_offset)[0]
                        text, replaced = fit_encoding(translations[key], is_utf8)
                        if replaced and getattr(self, '_unfit', None) is not None:
                            self._unfit.append(
                                (f'Database/{db.name}/{key}',
                                 'UTF-8' if is_utf8 else 'Shift-JIS'))
                        new_bytes = text.encode(text_encoding(is_utf8))
                        patches.append((str_offset, 4 + raw_len, new_bytes))

        # The layout must end exactly as the loader expects (version byte,
        # then EOF); otherwise the offsets above can't be trusted.
        if reader.eof() or reader.read_byte() != version or not reader.eof():
            return 'unexpected DB layout'

        _apply_string_patches(data, patches)

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        game_io.atomic_write_bytes(dst_path, bytes(data))
        log.info("Exported DB translations to %s", dst_path.name)
        return None

    @staticmethod
    def _backup_dir_for(data_dir: Path) -> Path:
        return data_dir.parent / (data_dir.name + '_original')

    def _manifest_key(self, live_path: Path) -> str:
        return live_path.relative_to(self.data_dir).as_posix()

    def _data_file_pairs(self, backup_dir: Path):
        """(key, live, backup) for every data file the exporter writes."""
        wanted = {'BasicData/CommonEvent.dat', 'BasicData/DataBase.dat',
                  'BasicData/CDataBase.dat', 'BasicData/SysDatabase.dat'}
        for sub, pattern in (('MapData', '*.mps'), ('BasicData', '*.dat')):
            seen = set()
            for base in (self.data_dir / sub, backup_dir / sub):
                if base.is_dir():
                    seen.update(f.name for f in base.glob(pattern))
            for name in sorted(seen):
                key = f'{sub}/{name}'
                if sub == 'MapData' or key in wanted:
                    yield (key, str(self.data_dir / key), str(backup_dir / key))

    def _reload_problems(self, translations: dict) -> list[str]:
        """Files that failed to re-read from backup and have translations."""
        out = []
        for label, prefix in self._reload_failures:
            n = sum(1 for k in translations if k.startswith(prefix))
            if n:
                out.append(f'{label} (could not be re-read from backup, '
                           f'{n} translations)')
        return out

    def _reload_from_backup(self, backup_dir: Path):
        """Re-read maps and common events from backup for idempotent re-export."""
        self._reload_failures: list[tuple[str, str]] = []
        # Reload maps
        self.maps = []
        map_dir = backup_dir / 'MapData'
        if map_dir.is_dir():
            for mps in sorted(map_dir.glob('*.mps')):
                try:
                    wolf_map = WolfMap(mps)
                    wolf_map.load()
                    self.maps.append(wolf_map)
                except Exception:
                    log.warning("Skip map %s on reload", mps.name, exc_info=True)
                    self._reload_failures.append(
                        (f'MapData/{mps.name}', f'MapData/{mps.stem}/'))  # keys use the stem

        # Reload common events (same BasicData/ layout as load_project)
        self.common_events = None
        ce_path = backup_dir / 'BasicData' / _CE_NAME
        if ce_path.is_file():
            try:
                self.common_events = WolfCommonEvents(ce_path)
                self.common_events.load()
            except Exception:
                self.common_events = None
                log.warning("Skip CE reload", exc_info=True)
                self._reload_failures.append(
                    ('CommonEvent.dat', 'CommonEvents/'))

        # Reload database structure (.project types) — needed when export
        # runs after restoring a saved state without load_project.
        self.databases = []
        basic_dir = backup_dir / 'BasicData'
        for db_name in _DB_NAMES:
            db_path = basic_dir / db_name
            if db_path.is_file():
                try:
                    db = WolfDatabase(db_path)
                    db.load()
                    self.databases.append(db)
                except Exception as exc:
                    # Not lost: its translations are reported as "could
                    # not be re-read from backup" by save_project.
                    log.warning("Skip DB %s on reload: %s", db_name, exc)

    @staticmethod
    def _rebuild_message(original: str, translated: str,
                         cleaned: Optional[str] = None) -> str:
        """Rebuild full message with original speaker prefix + translated text.

        ``cleaned`` is the entry.original the translation was made from.
        When given and found in the raw message, everything before it is
        kept verbatim as the prefix. This keeps projects saved under the
        older (multi-line) cleaning regex exporting correctly: the prefix is
        whatever that version stripped, so nothing is duplicated or lost.
        """
        if cleaned:
            idx = original.rfind(cleaned)
            if idx >= 0:
                return original[:idx] + translated
        match = _SPEAKER_PREFIX_RE.match(original)
        if match:
            return match.group(0) + translated
        match = _FACE_PREFIX_RE.match(original)
        if match:
            return match.group(0) + translated
        return translated

    def _unpack_targets_existing(self) -> set:
        """Folders an unpack of Data.wolf would create, that exist now."""
        return {p for p in (self.game_dir / 'Data',
                            self.game_dir / 'decompiled_temp') if p.exists()}

    def _refusal_message(self, where: str, reason: str, existed: set) -> str:
        """The refusal (file + plain reason), true about the game folder.

        Finding the version may need Data.wolf unpacked into Data/ first.
        A Data/ folder next to Data.wolf can be loaded by the engine instead
        of the archive, so folders this call created are removed again.
        """
        leftover = []
        for path in (self.game_dir / 'Data', self.game_dir / 'decompiled_temp'):
            if path in existed or not path.exists():
                continue
            try:
                shutil.rmtree(path)          # created by this call: ours
            except OSError as exc:
                log.warning('Could not remove %s: %s', path, exc)
                leftover.append(path.name)
        if leftover:
            tail = (f" The folder(s) {', '.join(leftover)}/ unpacked from "
                    "Data.wolf to check the version could not be removed: "
                    "delete them before playing (the game itself uses "
                    "Data.wolf).")
        else:
            tail = " Your game files were not changed."
        return f'{WOLF_UNSUPPORTED}: {where} {reason}.{tail}'

    def restore_originals(self, folder: str):
        """Restore original files from backup."""
        game_dir = Path(folder)
        self.game_dir = game_dir
        data_dir = self._find_data_dir()
        self.data_dir = data_dir
        backup_dir = self._backup_dir_for(data_dir)
        if not backup_dir.is_dir():
            raise FileNotFoundError(
                f'No backup found at {backup_dir}. Export the game at least '
                'once before restoring.')
        # Only the subtrees the exporter writes are swapped back.  Never
        # delete data_dir itself: in the root layout it IS the game folder
        # (save files, the translation state, everything).
        game_io.restore_tree(str(backup_dir), str(data_dir),
                             only=list(_WOLF_DATA_SUBDIRS))

        # Restore Data.wolf if we renamed it
        wolf_bak = game_dir / 'Data.wolf.bak'
        if wolf_bak.is_file() and not (game_dir / 'Data.wolf').is_file():
            wolf_bak.rename(game_dir / 'Data.wolf')

    # ── Game title ────────────────────────────────────────────────────

    def get_game_title(self, folder: str) -> str:
        """Try to read game title from Game.dat."""
        game_dat = Path(folder) / 'Data' / 'BasicData' / 'Game.dat'
        if not game_dat.is_file():
            game_dat = Path(folder) / 'BasicData' / 'Game.dat'
        if not game_dat.is_file():
            # Try decompiled path
            game_dat = Path(folder) / 'decompiled_all' / 'BasicData' / 'Game.dat'
        if not game_dat.is_file():
            return Path(folder).name

        try:
            data = game_dat.read_bytes()
            # Protected (encrypted) or unknown header: no title from it.
            # Its text is UTF-8 when its own header says so (3.x).
            is_utf8 = header_utf8(data, MAGIC_GAMEDAT, _GAMEDAT_UTF8_AT)
            if is_utf8 is None:
                return Path(folder).name
            reader = BinaryReader(data[len(MAGIC_GAMEDAT):], is_utf8)
            # Game.dat format: byte_array (unknown1), uint32 (string_count),
            # then string[0] = title
            unknown1_count = reader.read_uint32()
            reader.skip(unknown1_count)  # skip unknown1 byte array
            string_count = reader.read_uint32()
            if string_count < 1:
                return Path(folder).name
            title = reader.read_string()
            return title if title else Path(folder).name
        except Exception:
            # Cosmetic only (a title suggestion); a Game.dat we can't read
            # (damaged, or text that doesn't decode) just uses the folder name
            return Path(folder).name
