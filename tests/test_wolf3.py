"""WOLF RPG Editor 3.x (UTF-8) support, and what is still refused.

Fixtures are built byte by byte from the format by tests/wolf_build.py
(independent of the parser); expected output files are built the same
way, so the exporter is checked against the format, not against itself.
"""

import struct

import pytest

import wolf_build as B
from translator import wolfrpg

EMOJI = chr(0x1F600)
EM_DASH = chr(0x2014)          # not in Shift-JIS (cp932)
E_ACUTE = chr(0xE9)            # not in Shift-JIS (cp932)


def _translate(entries, mapping):
    """Give each entry (by field suffix) its translation; return entries."""
    for e in entries:
        for suffix, text in mapping.items():
            if e.id.endswith(suffix):
                e.translation = text
                e.status = 'translated'
    return entries


def _map_events(utf8, message, choices, name='村人'):
    cmds = [B.command(wolfrpg.CMD_MESSAGE, [message], utf8=utf8),
            B.command(wolfrpg.CMD_CHOICES, choices, utf8=utf8)]
    return [B.event(1, name, [B.page(cmds, utf8)], utf8)]


def _ce_events(utf8, message):
    return [B.common_event(7, '共通', [
        B.command(wolfrpg.CMD_MESSAGE, [message], utf8=utf8)], utf8)]


def _game(tmp_path, utf8=True, message='@1\nアリス：\nこんにちは',
          choices=('はい', 'いいえ'), ce_message='セーブしますか？',
          rows=(('剣', '鋭い剣'),), name='Game'):
    root = tmp_path / name
    return B.write_game(
        root,
        maps={'Map001.mps': B.map_file(_map_events(utf8, message, list(choices)),
                                       utf8=utf8)},
        ce=B.ce_file(_ce_events(utf8, ce_message), utf8=utf8),
        dbs={'DataBase': B.db_files([list(r) for r in rows], utf8=utf8)},
        gamedat=B.game_dat('ゲーム名', utf8=utf8))


# ── header marker: per file ──────────────────────────────────────────────

def test_header_utf8_reads_each_files_own_marker():
    hu = wolfrpg.header_utf8
    assert hu(B.dat_magic('ce', True), wolfrpg.MAGIC_COMMON, 6) is True
    assert hu(B.dat_magic('ce', False), wolfrpg.MAGIC_COMMON, 6) is False
    assert hu(B.dat_magic('db', True), wolfrpg.MAGIC_DB, 6) is True
    assert hu(B.dat_magic('db', True), wolfrpg.MAGIC_COMMON, 6) is None
    assert hu(B.map_file([], utf8=True), wolfrpg.MAGIC_MAP, 16) is True
    assert hu(B.map_file([], utf8=False), wolfrpg.MAGIC_MAP, 16) is False
    assert hu(B.game_dat('t', utf8=True), wolfrpg.MAGIC_GAMEDAT, 9) is True
    other = bytearray(B.dat_magic('ce', False))
    other[6] = 0x41                                  # neither 0x00 nor 'U'
    assert hu(bytes(other), wolfrpg.MAGIC_COMMON, 6) is None
    assert hu(b'\x00W', wolfrpg.MAGIC_COMMON, 6) is None


# ── 3.x round trip ───────────────────────────────────────────────────────

def test_wolf3_game_loads_exports_reloads_and_restores(tmp_path):
    game = _game(tmp_path)
    original = B.snapshot(game)
    p = wolfrpg.WolfRPGParser()
    entries = p.load_project(str(game))
    assert p.load_warnings == []
    got = {e.id: e.original for e in entries}
    assert got == {
        'MapData/Map001/Ev1/p0/msg0': 'こんにちは',
        'MapData/Map001/Ev1/p0/choice1_0': 'はい',
        'MapData/Map001/Ev1/p0/choice1_1': 'いいえ',
        'CommonEvents/CE7/msg0': 'セーブしますか？',
        'Database/DataBase/Type0/Data0/F0': '剣',
        'Database/DataBase/Type0/Data0/F1': '鋭い剣',
    }
    assert p.get_game_title(str(game)) == 'ゲーム名'
    assert p.databases[0]._types[0]['name'] == 'Items'   # .project as UTF-8

    hello = f'Hello {E_ACUTE}{EM_DASH} “hi” {EMOJI}'     # all fine in UTF-8
    _translate(entries, {'msg0': hello, 'choice1_0': 'Yes',
                         'choice1_1': 'No ' + EMOJI, 'CE7/msg0': 'Save?',
                         'F0': 'Sword', 'F1': 'Sharp sword ' + EMOJI})
    p2 = wolfrpg.WolfRPGParser()
    p2.save_project(str(game), entries)
    assert p2.last_export_notes == []

    data = game / 'Data'
    assert (data / 'MapData' / 'Map001.mps').read_bytes() == B.map_file(
        _map_events(True, '@1\nアリス：\n' + hello, ['Yes', 'No ' + EMOJI]))
    assert (data / 'BasicData' / 'CommonEvent.dat').read_bytes() == B.ce_file(
        _ce_events(True, 'Save?'))
    dat, _proj = B.db_files([['Sword', 'Sharp sword ' + EMOJI]])
    assert (data / 'BasicData' / 'DataBase.dat').read_bytes() == dat

    # what was written reads back at the same place
    m = wolfrpg.WolfMap(data / 'MapData' / 'Map001.mps')
    m.load()
    assert m.events[0].pages[0].commands[1].string_args == ['Yes', 'No ' + EMOJI]

    wolfrpg.WolfRPGParser().restore_originals(str(game))
    now = B.snapshot(game)
    assert {k: v for k, v in now.items() if k in original} == original


def test_untranslated_strings_keep_their_exact_bytes(tmp_path):
    game = _game(tmp_path)
    p = wolfrpg.WolfRPGParser()
    entries = p.load_project(str(game))
    _translate(entries, {'choice1_0': 'Yes'})
    wolfrpg.WolfRPGParser().save_project(str(game), entries)
    assert (game / 'Data' / 'MapData' / 'Map001.mps').read_bytes() == \
        B.map_file(_map_events(True, '@1\nアリス：\nこんにちは', ['Yes', 'いいえ']))


# ── 2.x unchanged; unstorable characters are reported, not silent ───────

def test_wolf2_game_still_reads_and_writes_shift_jis(tmp_path):
    game = _game(tmp_path, utf8=False)
    p = wolfrpg.WolfRPGParser()
    entries = p.load_project(str(game))
    assert p.load_warnings == []
    assert {e.original for e in entries} >= {'こんにちは', 'はい', '剣'}
    assert p.get_game_title(str(game)) == 'ゲーム名'
    _translate(entries, {'msg0': 'Hello', 'choice1_0': 'Yes',
                         'CE7/msg0': 'Save?', 'F0': 'Sword'})
    p2 = wolfrpg.WolfRPGParser()
    p2.save_project(str(game), entries)
    assert p2.last_export_notes == []
    data = game / 'Data'
    assert (data / 'MapData' / 'Map001.mps').read_bytes() == B.map_file(
        _map_events(False, '@1\nアリス：\nHello', ['Yes', 'いいえ']), utf8=False)
    assert (data / 'BasicData' / 'CommonEvent.dat').read_bytes() == B.ce_file(
        _ce_events(False, 'Save?'), utf8=False)


def test_wolf2_unstorable_characters_become_question_marks_and_are_reported(
        tmp_path):
    game = _game(tmp_path, utf8=False)
    entries = wolfrpg.WolfRPGParser().load_project(str(game))
    _translate(entries, {'p0/msg0': f'Caf{E_ACUTE}', 'choice1_0': 'Yes',
                         'F0': f'A{EM_DASH}B'})
    p = wolfrpg.WolfRPGParser()
    p.save_project(str(game), entries)
    data = game / 'Data'
    assert (data / 'MapData' / 'Map001.mps').read_bytes() == B.map_file(
        _map_events(False, '@1\nアリス：\nCaf?', ['Yes', 'いいえ']), utf8=False)
    dat, _proj = B.db_files([['A?B', '鋭い剣']], utf8=False)
    assert (data / 'BasicData' / 'DataBase.dat').read_bytes() == dat
    (note,) = p.last_export_notes
    assert note.startswith("2 translated line(s)")
    assert 'Shift-JIS' in note and "'?'" in note
    assert 'MapData/Map001/Ev1/p0/msg0' in note
    assert 'Database/DataBase/Type0/Data0/F0' in note


def test_lone_surrogate_in_a_utf8_translation_is_reported_not_a_crash(tmp_path):
    game = _game(tmp_path)
    entries = wolfrpg.WolfRPGParser().load_project(str(game))
    _translate(entries, {'choice1_0': 'Y' + chr(0xD800) + 'es'})
    p = wolfrpg.WolfRPGParser()
    p.save_project(str(game), entries)
    assert 'UTF-8' in p.last_export_notes[0]
    m = wolfrpg.WolfMap(game / 'Data' / 'MapData' / 'Map001.mps')
    m.load()
    assert m.events[0].pages[0].commands[1].string_args[0] == 'Y?es'


# ── each file uses its own marker; nothing leaks between games ───────────

def test_mixed_game_each_file_uses_its_own_encoding(tmp_path):
    root = tmp_path / 'Mixed'
    B.write_game(root,
                 maps={'Map001.mps': B.map_file(
                     _map_events(True, 'こんにちは', ['はい']), utf8=True)},
                 ce=B.ce_file(_ce_events(False, 'セーブ'), utf8=False))
    p = wolfrpg.WolfRPGParser()
    entries = p.load_project(str(root))
    assert {e.original for e in entries} == {'こんにちは', 'はい', 'セーブ'}
    _translate(entries, {'msg0': 'Hi ' + EMOJI, 'CE7/msg0': 'Save'})
    wolfrpg.WolfRPGParser().save_project(str(root), entries)
    data = root / 'Data'
    assert (data / 'MapData' / 'Map001.mps').read_bytes() == B.map_file(
        _map_events(True, 'Hi ' + EMOJI, ['はい']))
    assert (data / 'BasicData' / 'CommonEvent.dat').read_bytes() == B.ce_file(
        _ce_events(False, 'Save'), utf8=False)


def test_one_parser_reads_a_3x_then_a_2x_game_correctly(tmp_path):
    p = wolfrpg.WolfRPGParser()
    e3 = p.load_project(str(_game(tmp_path, utf8=True, name='G3')))
    e2 = p.load_project(str(_game(tmp_path, utf8=False, name='G2')))
    assert {e.original for e in e3} == {e.original for e in e2}
    assert all(chr(0xFFFD) not in e.original for e in e2)


# ── maps: FFFFFFFF tile marker, event count, end of file ─────────────────

def test_utf8_map_without_tile_data_round_trips(tmp_path):
    raw = B.map_file(_map_events(True, 'やあ', ['はい']), no_tiles=True)
    path = tmp_path / 'Map001.mps'
    path.write_bytes(raw)
    m = wolfrpg.WolfMap(path)
    m.load()
    assert m.events[0].pages[0].commands[0].string_args == ['やあ']
    m.save(tmp_path / 'out.mps')
    assert (tmp_path / 'out.mps').read_bytes() == raw


def test_wolf2_map_whose_tiles_start_with_ffffffff_reads_them_as_tiles(tmp_path):
    tiles = b'\xff\xff\xff\xff' + bytes(2 * 2 * 3 * 4 - 4)
    raw = B.map_file(_map_events(False, 'やあ', ['はい']), utf8=False,
                     tiles=tiles)
    path = tmp_path / 'Map001.mps'
    path.write_bytes(raw)
    m = wolfrpg.WolfMap(path)
    m.load()
    assert len(m.events) == 1
    m.save(tmp_path / 'out.mps')
    assert (tmp_path / 'out.mps').read_bytes() == raw


def test_map_with_only_a_header_loads(tmp_path):
    raw = bytearray(B.map_file([], utf8=True))
    raw = bytes(raw[:-(2 * 2 * 3 * 4 + 1)]) + b'\x66'   # no tiles, no events
    path = tmp_path / 'Map001.mps'
    path.write_bytes(raw)
    m = wolfrpg.WolfMap(path)
    m.load()
    assert m.events == []


@pytest.mark.parametrize('damage', ['event_count', 'trailing'])
def test_map_structure_is_checked(tmp_path, damage):
    raw = bytearray(B.map_file(_map_events(True, 'やあ', ['はい'])))
    if damage == 'event_count':
        struct.pack_into('<I', raw, 20 + 4 + 1 + 5 + 12, 2)   # header says 2
    else:
        raw += b'\x00'
    path = tmp_path / 'Map001.mps'
    path.write_bytes(bytes(raw))
    with pytest.raises(ValueError):
        wolfrpg.WolfMap(path).load()


# ── common events: terminator and end of file ────────────────────────────

@pytest.mark.parametrize('terminator, ok', [
    (0x8F, True), (0x90, True), (0x89, True), (0xFF, True), (0x88, False)])
def test_common_event_terminator(tmp_path, terminator, ok):
    path = tmp_path / 'CommonEvent.dat'
    path.write_bytes(B.ce_file(_ce_events(True, 'やあ'), terminator=terminator))
    ce = wolfrpg.WolfCommonEvents(path)
    if ok:
        ce.load()
        ce.save(tmp_path / 'out.dat')
        assert (tmp_path / 'out.dat').read_bytes() == path.read_bytes()
    else:
        with pytest.raises(ValueError, match='terminator'):
            ce.load()


def test_common_events_must_end_at_end_of_file(tmp_path):
    path = tmp_path / 'CommonEvent.dat'
    path.write_bytes(B.ce_file(_ce_events(True, 'やあ')) + b'\x00')
    with pytest.raises(ValueError, match='unexpected bytes'):
        wolfrpg.WolfCommonEvents(path).load()


# ── refused: compressed (3.5) and protected files ────────────────────────

def _protected(data: bytes) -> bytes:
    out = bytearray(data)
    out[1], out[5], out[7] = 0x50, 0x54, 0x4B        # "V3.1" header shape
    return bytes(out)


def _with(kind, change):
    def build(tmp_path):
        game = _game(tmp_path)
        basic = game / 'Data' / 'BasicData'
        target = {'ce': basic / 'CommonEvent.dat', 'db': basic / 'DataBase.dat',
                  'map': game / 'Data' / 'MapData' / 'Map001.mps'}[kind]
        target.write_bytes(change(target.read_bytes()))
        return game
    return build


def _version(offset, value, size=1):
    def change(data):
        out = bytearray(data)
        if size == 1:
            out[offset] = value
        else:
            struct.pack_into('<I', out, offset, value)
        return bytes(out)
    return change


@pytest.mark.parametrize('build, where, words', [
    (_with('ce', _version(10, 0x93)), 'BasicData/CommonEvent.dat', 'compressed'),
    (_with('ce', _version(10, 0xCC)), 'BasicData/CommonEvent.dat', 'compressed'),
    (_with('db', _version(10, 0xC4)), 'BasicData/DataBase.dat', 'compressed'),
    (_with('map', _version(20, 0x65, 4)), 'MapData/Map001.mps', 'compressed'),
    (_with('map', _version(20, 0x67, 4)), 'MapData/Map001.mps', 'compressed'),
    (_with('ce', _protected), 'BasicData/CommonEvent.dat', 'encrypted'),
    (_with('db', _protected), 'BasicData/DataBase.dat', 'encrypted'),
    (_with('map', _protected), 'MapData/Map001.mps', 'encrypted'),
])
def test_refused_game_offers_nothing_and_changes_nothing(tmp_path, build,
                                                         where, words):
    game = build(tmp_path)
    before = B.snapshot(tmp_path)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith(wolfrpg.WOLF_UNSUPPORTED)
    assert f'{where} is {words}' in msg
    assert msg.endswith('Your game files were not changed.')
    assert p.maps == [] and p.common_events is None and p.databases == []
    assert B.snapshot(tmp_path) == before
    # export (e.g. from a project saved earlier) is refused before any backup
    entry = wolfrpg.TranslationEntry(
        id='MapData/Map001/Ev1/p0/choice1_0', file='MapData/Map001',
        field='Ev1/p0/choice1_0', original='はい', translation='Yes',
        status='translated')
    with pytest.raises(wolfrpg.WolfVersionUnsupported, match=words):
        wolfrpg.WolfRPGParser().save_project(str(game), [entry])
    assert B.snapshot(tmp_path) == before


def test_export_is_refused_when_the_backup_is_compressed(tmp_path):
    game = _game(tmp_path)
    entries = wolfrpg.WolfRPGParser().load_project(str(game))
    wolfrpg.WolfRPGParser().save_project(str(game), entries)   # makes backup
    backup_ce = game / 'Data_original' / 'BasicData' / 'CommonEvent.dat'
    backup_ce.write_bytes(_version(10, 0x93)(backup_ce.read_bytes()))
    before = B.snapshot(tmp_path)
    with pytest.raises(wolfrpg.WolfVersionUnsupported, match='compressed'):
        wolfrpg.WolfRPGParser().save_project(str(game), entries)
    assert B.snapshot(tmp_path) == before


def test_export_database_refuses_compressed_and_protected(tmp_path):
    dat, proj = B.db_files([['剣']])
    p = wolfrpg.WolfRPGParser()
    for change, words in ((_version(10, 0xC4), 'LZ4'), (_protected, 'encrypted')):
        src = tmp_path / 'DataBase.dat'
        src.write_bytes(change(dat))
        db = wolfrpg.WolfDatabase(src)
        db._types = [{}]
        reason = p._export_database(db, src, tmp_path / 'out.dat',
                                    {'Type0/Data0/F0': 'Sword'})
        assert words in reason
        assert not (tmp_path / 'out.dat').exists()


# ── text that doesn't decode: refuse the game, never load it garbled ────

def test_invalid_utf8_text_refuses_the_whole_game(tmp_path):
    game = _game(tmp_path)
    bad_cmd = B.command(wolfrpg.CMD_MESSAGE, raw_strs=[B.raw_s(b'ab\xe3\x81')])
    (game / 'Data' / 'MapData' / 'Map002.mps').write_bytes(B.map_file(
        [B.event(1, 'x', [B.page([bad_cmd])])]))
    before = B.snapshot(tmp_path)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []          # not even the good files
    (msg,) = p.load_warnings
    assert 'MapData/Map002.mps' in msg and "isn't valid UTF-8" in msg
    assert msg.endswith('Your game files were not changed.')
    assert B.snapshot(tmp_path) == before


def test_invalid_shift_jis_text_refuses_the_whole_game(tmp_path):
    game = _game(tmp_path, utf8=False)
    bad = B.common_event(3, 'x', [B.command(
        wolfrpg.CMD_MESSAGE, raw_strs=[B.raw_s(b'\x81\x20ok')])], utf8=False)
    (game / 'Data' / 'BasicData' / 'CommonEvent.dat').write_bytes(
        B.ce_file([bad], utf8=False))
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    assert "CommonEvent.dat has text that isn't valid Shift-JIS" in \
        p.load_warnings[0]


def test_bad_bytes_in_text_that_is_never_rewritten_do_not_refuse(tmp_path):
    # A page's graphic name is kept as raw bytes, never decoded or written
    # from text: it can't be garbled, so it doesn't block the game.
    game = _game(tmp_path)
    pg = bytearray(B.page([B.command(wolfrpg.CMD_MESSAGE, ['やあ'])]))
    pg[4:4 + 5] = B.raw_s(b'\xff')                   # graphic name: 1 bad byte
    raw = B.map_file([B.event(1, 'x', [bytes(pg)])])
    (game / 'Data' / 'MapData' / 'Map001.mps').write_bytes(raw)
    p = wolfrpg.WolfRPGParser()
    entries = p.load_project(str(game))
    assert p.load_warnings == []
    _translate(entries, {'msg0': 'Hi'})
    wolfrpg.WolfRPGParser().save_project(str(game), entries)
    out = (game / 'Data' / 'MapData' / 'Map001.mps').read_bytes()
    assert B.raw_s(b'\xff') in out and B.s('Hi', True) in out


def test_a_map_that_cant_be_written_is_reported_and_the_rest_exports(
        tmp_path, monkeypatch):
    from translator import game_io
    game = _game(tmp_path)
    map_path = game / 'Data' / 'MapData' / 'Map001.mps'
    map_before = map_path.read_bytes()
    entries = wolfrpg.WolfRPGParser().load_project(str(game))
    _translate(entries, {'choice1_0': 'Yes', 'CE7/msg0': 'Save?'})

    def boom(self, path=None):
        raise UnicodeEncodeError('utf-8', 'x', 0, 1, 'simulated')
    monkeypatch.setattr(wolfrpg.WolfMap, 'save', boom)
    with pytest.raises(game_io.PartialExportError) as ei:
        wolfrpg.WolfRPGParser().save_project(str(game), entries)
    assert any('Map001.mps' in p for p in ei.value.errors)
    assert map_path.read_bytes() == map_before           # untouched
    assert (game / 'Data' / 'BasicData' / 'CommonEvent.dat').read_bytes() == \
        B.ce_file(_ce_events(True, 'Save?'))             # the rest written


# ── title ────────────────────────────────────────────────────────────────

def test_protected_game_dat_gives_the_folder_name(tmp_path):
    game = _game(tmp_path)
    gd = game / 'Data' / 'BasicData' / 'Game.dat'
    gd.write_bytes(_protected(gd.read_bytes()))
    assert wolfrpg.WolfRPGParser().get_game_title(str(game)) == 'Game'
