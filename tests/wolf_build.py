"""Builds small Wolf RPG Editor data files byte by byte, from the format.

Written independently of translator/wolfrpg.py (from the layouts in
WolfTL's Map.hpp / CommonEvents.hpp / Database.hpp / GameDat.hpp), so a
test can compare what the exporter wrote with the bytes this builder
produces for the same text.  ``utf8=True`` gives a 3.x file (UTF-8 text,
'U' marker byte), ``utf8=False`` a 2.x file (Shift-JIS).  No game data.
"""

import struct


def u32(n: int) -> bytes:
    return struct.pack('<I', n)


def s(text: str, utf8: bool) -> bytes:
    raw = text.encode('utf-8' if utf8 else 'cp932') + b'\x00'
    return u32(len(raw)) + raw


def raw_s(raw: bytes) -> bytes:
    """A string whose bytes are given as is (to plant invalid text)."""
    return u32(len(raw) + 1) + raw + b'\x00'


def dat_magic(kind: str, utf8: bool) -> bytes:
    last = b'C' if kind == 'ce' else b'M'
    return (b'\x00W\x00\x00OL' + (b'U' if utf8 else b'\x00') + b'F' + last
            + b'\x00')


def command(code: int, strs=(), ints=(), utf8=True, raw_strs=None) -> bytes:
    out = bytes([len(ints) + 1]) + u32(code)
    for i in ints:
        out += u32(i)
    out += bytes([0])                                   # indent
    parts = raw_strs if raw_strs is not None else [s(t, utf8) for t in strs]
    out += bytes([len(parts)]) + b''.join(parts)
    return out + b'\x00'                                # command terminator


def page(cmds: list, utf8=True) -> bytes:
    out = u32(0) + s('', utf8) + bytes(4)               # unknown1, graphic, 4
    out += bytes(1 + 4 + 16 + 16) + bytes(4) + b'\x00\x00'  # cond, move, flags
    out += u32(0)                                       # no route
    out += u32(len(cmds)) + b''.join(cmds)
    out += u32(0) + bytes(3)                            # features, shadow, w, h
    return out + b'\x7a'


def event(ev_id: int, name: str, pages: list, utf8=True) -> bytes:
    out = b'\x6f' + b'\x39\x30\x00\x00' + u32(ev_id) + s(name, utf8)
    out += u32(1) + u32(2) + u32(len(pages)) + bytes(4)
    for p in pages:
        out += b'\x79' + p
    return out + b'\x70'


def map_file(events: list, utf8=True, version=0x64, width=2, height=2,
             no_tiles=False, tiles: bytes = None) -> bytes:
    head = bytearray(bytes(10) + b'WOLFM\x00' + bytes(4))
    head[16] = 0x55 if utf8 else 0x00
    out = bytes(head) + u32(version) + b'\x00' + s('', utf8)
    out += u32(0) + u32(width) + u32(height) + u32(len(events))
    if version >= 0x67:
        out += u32(0) + u32(3)
    if no_tiles:
        out += b'\xff\xff\xff\xff'
    else:
        out += tiles if tiles is not None else bytes(width * height * 3 * 4)
    return out + b''.join(events) + b'\x66'


def common_event(int_id: int, name: str, cmds: list, utf8=True) -> bytes:
    out = b'\x8e' + u32(int_id) + u32(0) + bytes(7) + s(name, utf8)
    out += u32(len(cmds)) + b''.join(cmds)
    out += s('', utf8) + s('', utf8)                    # unknown11, description
    out += b'\x8f' + u32(0) + u32(0) + u32(0) + u32(0)  # unknown3..6
    out += bytes(0x1D) + s('', utf8) * 100              # unknown7, unknown8
    return out + b'\x91' + s('', utf8) + b'\x91'


def ce_file(events: list, utf8=True, version=None, terminator=None) -> bytes:
    version = version if version is not None else (0xC9 if utf8 else 0x8F)
    terminator = terminator if terminator is not None else (
        0x90 if utf8 else 0x8F)
    return (dat_magic('ce', utf8) + bytes([version]) + u32(len(events))
            + b''.join(events) + bytes([terminator]))


def db_files(rows: list, utf8=True, version=None, type_name='Items'):
    """(.dat bytes, .project bytes): one type, 1 int + N string fields."""
    version = version if version is not None else (0xC2 if utf8 else 0xC1)
    n_str = len(rows[0])
    dat = dat_magic('db', utf8) + bytes([version]) + u32(1)
    dat += b'\xfe\xff\xff\xff' + u32(0) + u32(1 + n_str) + u32(0x03E8)
    for k in range(n_str):
        dat += u32(0x07D0 + k)
    dat += u32(len(rows))
    for i, row in enumerate(rows):
        dat += u32(100 + i) + b''.join(s(t, utf8) for t in row)
    dat += bytes([version])
    proj = u32(1) + s(type_name, utf8) + u32(1 + n_str)
    proj += s('num', utf8) + b''.join(s(f'f{k}', utf8) for k in range(n_str))
    proj += u32(len(rows)) + b''.join(s(f'row{i}', utf8) for i in range(len(rows)))
    proj += s('', utf8) + u32(1 + n_str) + bytes(1 + n_str)
    proj += u32(0) + u32(0) + u32(0) + u32(0)
    return dat, proj


def game_dat(title: str, utf8=True) -> bytes:
    head = bytearray(dat_magic('db', False))
    head[9] = 0x55 if utf8 else 0x00
    return (bytes(head) + u32(0) + u32(2) + s(title, utf8) + s('0000-0000', utf8)
            + bytes(16))


def write_game(root, maps: dict = None, ce: bytes = None, dbs: dict = None,
               gamedat: bytes = None, exe='GamePro.exe'):
    """A game folder with Data/BasicData + Data/MapData; returns its path."""
    basic = root / 'Data' / 'BasicData'
    mapdir = root / 'Data' / 'MapData'
    basic.mkdir(parents=True)
    mapdir.mkdir(parents=True)
    (root / exe).write_bytes(b'MZ')
    for name, data in (maps or {}).items():
        (mapdir / name).write_bytes(data)
    if ce is not None:
        (basic / 'CommonEvent.dat').write_bytes(ce)
    for name, (dat, proj) in (dbs or {}).items():
        (basic / f'{name}.dat').write_bytes(dat)
        (basic / f'{name}.project').write_bytes(proj)
    if gamedat is not None:
        (basic / 'Game.dat').write_bytes(gamedat)
    return root


def snapshot(folder) -> dict:
    return {str(p.relative_to(folder)): p.read_bytes()
            for p in sorted(folder.rglob('*')) if p.is_file()}
