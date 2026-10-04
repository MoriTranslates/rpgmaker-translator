"""Synthetic tests for binary-engine parsers (Wolf RPG, RM2K/2K3, VX Ace, Crowd)."""

import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translator import wolfrpg  # noqa: E402
from translator import rpgmaker_2k as rm2k  # noqa: E402
from translator import rpgmaker_ace as ace  # noqa: E402
from translator import crowd  # noqa: E402
from translator.project_model import TranslationEntry  # noqa: E402


# ── Wolf RPG ──────────────────────────────────────────────────────────

def _wstr(s: str) -> bytes:
    b = s.encode('cp932') + b'\x00'
    return struct.pack('<I', len(b)) + b


def _wolf_db_bytes(strings_per_row: list[list[str]]) -> bytes:
    """One type, fields: 1 int + N strings, one row per list item."""
    n_str = len(strings_per_row[0])
    out = bytearray(b'\x00' * 10)          # magic placeholder
    out.append(0x01)                        # version
    out += struct.pack('<I', 1)             # type count
    out += wolfrpg.WolfDatabase.DAT_TYPE_SEP
    out += struct.pack('<I', 0)             # unknown1
    out += struct.pack('<I', 1 + n_str)     # fields_size
    out += struct.pack('<I', 0x03E8)        # int field
    for k in range(n_str):
        out += struct.pack('<I', 0x07D0 + k)
    out += struct.pack('<I', len(strings_per_row))
    for i, row in enumerate(strings_per_row):
        out += struct.pack('<I', 100 + i)
        for s in row:
            out += _wstr(s)
    out += b'\x01'                          # trailing version byte
    return bytes(out)


def _parse_wolf_db(data: bytes) -> list[tuple]:
    db = wolfrpg.WolfDatabase(Path('X.dat'))
    db._types = [{'name': 't', 'fields': []}]
    db._parse_dat(data)
    return db.strings


def test_wolf_apply_string_patches_includes_nul():
    buf = bytearray(b'AB' + _wstr('あい') + _wstr('う') + b'Z')
    off1 = 2
    off2 = 2 + 4 + len('あい'.encode('cp932')) + 1
    raw1 = struct.unpack_from('<I', buf, off1)[0]
    raw2 = struct.unpack_from('<I', buf, off2)[0]
    # Deliberately unsorted to check descending application
    wolfrpg._apply_string_patches(buf, [
        (off1, 4 + raw1, b'Hello'),
        (off2, 4 + raw2, b'Hi'),
    ])
    assert bytes(buf) == b'AB' + _wstr('Hello') + _wstr('Hi') + b'Z'


def test_wolf_export_database_roundtrip(tmp_path):
    rows = [['剣', '説明です'], ['盾', '守る']]
    src = tmp_path / 'src.dat'
    dst = tmp_path / 'dst.dat'
    src.write_bytes(_wolf_db_bytes(rows))

    db = wolfrpg.WolfDatabase(src)
    db._types = [{'name': 't', 'fields': []}]
    parser = wolfrpg.WolfRPGParser()
    reason = parser._export_database(db, src, dst, {
        'Type0/Data0/F0': 'Sword',
        'Type0/Data0/F1': 'A long description',
        'Type0/Data1/F1': 'Guards',
    })
    assert reason is None
    strings = _parse_wolf_db(dst.read_bytes())
    texts = {(t, d, f): s for t, d, f, s in strings}
    # Untranslated Japanese one survives intact; translated ones are English
    # (not extracted since no Japanese) — verify by full re-parse layout.
    assert texts == {(0, 1, 0): '盾'}
    expected = _wolf_db_bytes([['Sword', 'A long description'], ['盾', 'Guards']])
    assert dst.read_bytes() == expected


def test_wolf_export_database_lz4_reports_reason(tmp_path):
    data = bytearray(_wolf_db_bytes([['剣']]))
    data[10] = 0xC4
    src = tmp_path / 's.dat'
    src.write_bytes(bytes(data))
    db = wolfrpg.WolfDatabase(src)
    db._types = [{}]
    reason = wolfrpg.WolfRPGParser()._export_database(
        db, src, tmp_path / 'd.dat', {'Type0/Data0/F0': 'x'})
    assert reason and 'LZ4' in reason


@pytest.mark.parametrize('raw, cleaned, speaker', [
    ('@1\nアリス：\nこんにちは\n元気？', 'こんにちは\n元気？', 'アリス'),
    ('アリス：\nこんにちは', 'こんにちは', 'アリス'),
    ('@2\nこんにちは', 'こんにちは', ''),
    # Colon line not first: nothing is stripped any more
    ('前置き\nアリス：\n本文', '前置き\nアリス：\n本文', ''),
])
def test_wolf_message_clean_and_rebuild(raw, cleaned, speaker):
    P = wolfrpg.WolfRPGParser
    assert P._clean_message(raw) == cleaned
    assert P._extract_speaker(raw) == speaker
    expected = raw[:len(raw) - len(cleaned)] + 'EN'
    # With and without the cleaned original, the prefix is kept verbatim
    assert P._rebuild_message(raw, 'EN', cleaned) == expected
    assert P._rebuild_message(raw, 'EN') == expected


def test_wolf_rebuild_tolerates_old_cleaning():
    raw = '前置き\nアリス：\n本文'
    old_cleaned = '本文'  # what the old DOTALL regex produced
    out = wolfrpg.WolfRPGParser._rebuild_message(raw, 'Text', old_cleaned)
    assert out == '前置き\nアリス：\nText'


def test_wolf_rebuild_keeps_face_prefix_without_speaker():
    out = wolfrpg.WolfRPGParser._rebuild_message('@3\nこんにちは', 'Hello')
    assert out == '@3\nHello'


def test_wolf_restore_without_backup_raises(tmp_path):
    (tmp_path / 'Data' / 'MapData').mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        wolfrpg.WolfRPGParser().restore_originals(str(tmp_path))


# ── RPG Maker 2000/2003 ───────────────────────────────────────────────

def test_rm2k_backup_name_helpers():
    assert rm2k._is_backup_name('Map0001_original.lmu')
    assert rm2k._is_backup_name('MAP0001_original.LMU')
    assert not rm2k._is_backup_name('Map0001.lmu')
    assert rm2k._backup_path(os.path.join('x', 'Map0001.LMU')) == \
        os.path.join('x', 'Map0001_original.LMU')
    assert rm2k._backup_path('RPG_RT.ldb') == 'RPG_RT_original.ldb'


def _cmd(code, s='', indent=0):
    return rm2k.EventCommand(code, indent, s, rm2k._encode_str(s), [])


def test_rm2k_message_overflow_inserts_lines_and_keeps_numbering():
    cmds = [
        _cmd(rm2k.CODE_SHOW_MESSAGE, 'あ'),
        _cmd(rm2k.CODE_SHOW_MESSAGE_LINE, 'い'),
        _cmd(rm2k.CODE_SHOW_MESSAGE, 'う'),
        _cmd(0),
    ]
    p = rm2k.RPGMaker2KParser()
    entries = p._extract_dialogue_commands(list(cmds), 'P', 'f', 3)
    assert [e.id for e in entries] == ['P/dialog_0', 'P/dialog_1']

    trans = {'P/dialog_0': 'l1\nl2\nl3\nl4\nl5\nl6', 'P/dialog_1': 'second'}
    assert p._apply_command_translations(cmds, 'P', trans)
    codes = [(c.code, c.string) for c in cmds]
    assert codes == [
        (rm2k.CODE_SHOW_MESSAGE, 'l1'),
        (rm2k.CODE_SHOW_MESSAGE_LINE, 'l2'),
        (rm2k.CODE_SHOW_MESSAGE_LINE, 'l3'),
        (rm2k.CODE_SHOW_MESSAGE_LINE, 'l4'),
        (rm2k.CODE_SHOW_MESSAGE, 'l5'),
        (rm2k.CODE_SHOW_MESSAGE_LINE, 'l6'),
        (rm2k.CODE_SHOW_MESSAGE, 'second'),   # dialog_1 not shifted
        (0, ''),
    ]


def test_rm2k_choice_numbering_skips_empty_options():
    cmds = [
        _cmd(rm2k.CODE_SHOW_CHOICE, ''),
        _cmd(rm2k.CODE_SHOW_CHOICE_OPT, ''),
        _cmd(rm2k.CODE_SHOW_CHOICE_OPT, 'はい'),
        _cmd(20141),
        _cmd(0),
    ]
    p = rm2k.RPGMaker2KParser()
    entries = p._extract_dialogue_commands(list(cmds), 'P', 'f', 3)
    assert [e.id for e in entries] == ['P/choice_0_0']
    p._apply_command_translations(cmds, 'P', {'P/choice_0_0': 'Yes'})
    assert cmds[1].string == '' and cmds[2].string == 'Yes'


def test_rm2k_size_chunk_updated():
    fields = {rm2k.PAGE_COMMANDS_SIZE: rm2k._write_ber(4),
              rm2k.PAGE_COMMANDS: b'\x00' * 4}
    cmds = [_cmd(rm2k.CODE_SHOW_MESSAGE, 'Hello'), _cmd(0)]
    rm2k._set_commands(fields, rm2k.PAGE_COMMANDS,
                       rm2k.PAGE_COMMANDS_SIZE, cmds)
    size, _ = rm2k._read_ber(fields[rm2k.PAGE_COMMANDS_SIZE], 0)
    assert size == len(fields[rm2k.PAGE_COMMANDS])
    # Absent size chunk is not invented
    f2 = {rm2k.CE_COMMANDS: b''}
    rm2k._set_commands(f2, rm2k.CE_COMMANDS, rm2k.CE_COMMANDS_SIZE, cmds)
    assert rm2k.CE_COMMANDS_SIZE not in f2


# ── VX Ace ────────────────────────────────────────────────────────────

def _rc(code, params):
    return SimpleNamespace(attributes={'@code': code, '@parameters': params,
                                       '@indent': 0})


def test_ace_empty_block_does_not_shift_export_index():
    cmds = [
        _rc(101, ['', 0, 0, 2]),
        _rc(401, ['']),                 # all-empty block: no entry, no index
        _rc(101, ['', 0, 0, 2]),
        _rc(401, ['こんにちは']),
        _rc(105, [2, False]),
        _rc(405, ['']),                 # all-empty scroll block
        _rc(102, [['はい', 'いいえ'], 2]),
        _rc(0, []),
    ]
    p = ace.RPGMakerAceParser()
    entries = p._parse_event_commands(cmds, 'Map001.rvdata2', 'M')
    ids = {e.id: e for e in entries}
    assert set(ids) == {'M/0', 'M/c1_0', 'M/c1_1'}

    for e, t in ((ids['M/0'], 'Hello'), (ids['M/c1_0'], 'Yes'),
                 (ids['M/c1_1'], 'No')):
        e.translation = t
        e.status = 'translated'
    p._apply_event_commands(cmds, 'M', ids)
    assert cmds[3].attributes['@parameters'][0] == 'Hello'
    assert cmds[1].attributes['@parameters'][0] == ''
    assert cmds[6].attributes['@parameters'][0] == ['Yes', 'No']


def test_ace_restore_without_backup_raises(tmp_path):
    (tmp_path / 'Data').mkdir()
    with pytest.raises(FileNotFoundError):
        ace.RPGMakerAceParser().restore_originals(str(tmp_path))


# ── Crowd ─────────────────────────────────────────────────────────────

def test_crowd_apply_keeps_lead_commands_and_trailing_ws():
    p = crowd.CrowdParser()
    content = 'w000001a@!アリス@nV 10 こんにちは  '
    entry = p._parse_entry_content(content, 'a.sce', 1, 'main', [])
    assert entry.original == 'こんにちは'
    entry.translation = 'Hello'
    out = p._apply_translation(content, entry)
    assert out == 'w000001a@!アリス@nV 10 Hello  '


def test_crowd_restore_without_backup_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        crowd.CrowdParser().restore_originals(str(tmp_path))


def test_crowd_export_roundtrip_preserves_undecodable_bytes(tmp_path):
    body = ('$ main $  0 w000001a@!アリス@nこんにちは  1 CF 1-2 ').encode('cp932')
    body += b'\x85\xff'  # bytes cp932 cannot decode
    (tmp_path / 'game.sce').write_bytes(bytes(crowd.encrypt_sce(body)))
    p = crowd.CrowdParser()
    e = TranslationEntry(id='game.sce/dialogue/0', file='game.sce',
                         field='dialogue', original='こんにちは',
                         translation='Hello', status='translated')
    p.save_project(str(tmp_path), [e])
    out = bytes(crowd.decrypt_sce((tmp_path / 'game.sce').read_bytes()))
    assert out.endswith(b'\x85\xff')
    assert b'Hello' in out
