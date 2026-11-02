"""Regression tests for the 2026-11 review hardening pass (v1.1.1).

Covers data-safety of game writes (atomic backups / writes / restores),
stale English after reverts, honest partial-export reporting, backup
re-baselining after a game update, and protection of reviewed work.
"""

import os
import shutil

import pytest

from translator import game_io
from translator.project_model import TranslationEntry


def _entry(id_, file, field, original, translation="", status="translated"):
    return TranslationEntry(id=id_, file=file, field=field, original=original,
                            translation=translation, status=status)


# ── game_io primitives ───────────────────────────────────────────────

def test_atomic_write_bytes_keeps_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "f.bin"
    target.write_bytes(b"OLD")

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        game_io.atomic_write_bytes(str(target), b"NEW")
    monkeypatch.setattr(os, "replace", real_replace)
    assert target.read_bytes() == b"OLD"
    assert not list(tmp_path.glob("*.rmt-tmp"))


def test_backup_tree_once_interrupted_copy_is_not_a_backup(tmp_path, monkeypatch):
    src = tmp_path / "Data"
    src.mkdir()
    (src / "a.dat").write_bytes(b"A")
    backup = tmp_path / "Data_original"

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        game_io.backup_tree_once(str(src), str(backup))
    monkeypatch.undo()
    assert not backup.exists()
    assert game_io.backup_tree_once(str(src), str(backup))
    assert (backup / "a.dat").read_bytes() == b"A"


def test_restore_tree_failure_leaves_live_intact(tmp_path, monkeypatch):
    live = tmp_path / "scenario"
    live.mkdir()
    (live / "a.ks").write_text("EN", encoding="utf-8")
    bak = tmp_path / "scenario_original"
    bak.mkdir()
    (bak / "a.ks").write_text("JP", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("AV lock")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        game_io.restore_tree(str(bak), str(live))
    assert (live / "a.ks").read_text(encoding="utf-8") == "EN"
    monkeypatch.undo()
    game_io.restore_tree(str(bak), str(live))
    assert (live / "a.ks").read_text(encoding="utf-8") == "JP"
    assert not (tmp_path / "scenario.rmt-old").exists()
    assert not (tmp_path / "scenario.rmt-restore").exists()


def test_refresh_changed_originals_rules(tmp_path):
    live = tmp_path / "data"
    bak = tmp_path / "data_original"
    live.mkdir()
    bak.mkdir()
    (bak / "ours.json").write_text("JP1", encoding="utf-8")
    (bak / "update.json").write_text("JP2", encoding="utf-8")
    (live / "ours.json").write_text("EN1", encoding="utf-8")
    (live / "update.json").write_text("JP2", encoding="utf-8")
    (live / "new.json").write_text("NEW", encoding="utf-8")

    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    pairs = [(n, str(live / n), str(bak / n))
             for n in ("ours.json", "update.json", "new.json")]
    # No manifest yet (legacy backup): nothing is re-baselined.
    assert game_io.refresh_changed_originals(pairs, m) == []

    m.record("ours.json", str(live / "ours.json"))
    m.record("update.json", str(live / "update.json"))
    m.finish()
    # The game is updated: update.json changes, a new file appears.
    (live / "update.json").write_text("JP2-v2", encoding="utf-8")
    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    refreshed = game_io.refresh_changed_originals(pairs, m)
    assert sorted(refreshed) == ["new.json", "update.json"]
    assert (bak / "update.json").read_text(encoding="utf-8") == "JP2-v2"
    assert (bak / "new.json").read_text(encoding="utf-8") == "NEW"
    # Our own export output is never baked into the backup.
    assert (bak / "ours.json").read_text(encoding="utf-8") == "JP1"


# ── Wolf RPG ─────────────────────────────────────────────────────────

def _wolf_root_game(tmp_path):
    game = tmp_path / "WolfGame"
    (game / "BasicData").mkdir(parents=True)
    (game / "MapData").mkdir()
    (game / "Save").mkdir()
    (game / "Save" / "SaveData01.sav").write_bytes(b"SAVE")
    (game / "BasicData" / "DataBase.dat").write_bytes(b"JP-DB")
    return game


def test_wolf_root_layout_backup_and_restore_keep_saves(tmp_path):
    from translator.wolfrpg import WolfRPGParser
    game = _wolf_root_game(tmp_path)
    WolfRPGParser().save_project(str(game), [])
    backup = tmp_path / "WolfGame_original"
    # Only the data subtrees are backed up, not the whole game.
    assert sorted(p.name for p in backup.iterdir()) == ["BasicData", "MapData"]

    # Player keeps playing; the app autosaves into the game folder.
    (game / "Save" / "SaveData02.sav").write_bytes(b"NEW SAVE")
    (game / "_translation_state.json").write_text("{}", encoding="utf-8")
    (game / "BasicData" / "DataBase.dat").write_bytes(b"EN-DB")

    WolfRPGParser().restore_originals(str(game))
    assert (game / "Save" / "SaveData01.sav").read_bytes() == b"SAVE"
    assert (game / "Save" / "SaveData02.sav").read_bytes() == b"NEW SAVE"
    assert (game / "_translation_state.json").exists()
    assert (game / "BasicData" / "DataBase.dat").read_bytes() == b"JP-DB"


def test_wolf_failed_restore_leaves_game_untouched(tmp_path, monkeypatch):
    from translator.wolfrpg import WolfRPGParser
    game = _wolf_root_game(tmp_path)
    WolfRPGParser().save_project(str(game), [])
    (game / "BasicData" / "DataBase.dat").write_bytes(b"EN-DB")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        WolfRPGParser().restore_originals(str(game))
    assert (game / "BasicData" / "DataBase.dat").read_bytes() == b"EN-DB"
    assert (game / "Save" / "SaveData01.sav").read_bytes() == b"SAVE"


def test_wolf_reverted_db_goes_back_to_original(tmp_path):
    """Exporting with no translations left must not keep stale English."""
    from translator.wolfrpg import WolfRPGParser
    game = _wolf_root_game(tmp_path)
    WolfRPGParser().save_project(str(game), [])
    # Simulate a previous export's English output in the live file.
    (game / "BasicData" / "DataBase.dat").write_bytes(b"EN-DB")
    m = game_io.ExportManifest(str(tmp_path / "WolfGame_original") + ".manifest.json")
    m.record("BasicData/DataBase.dat", str(game / "BasicData" / "DataBase.dat"))
    m.finish()
    WolfRPGParser().save_project(str(game), [])
    assert (game / "BasicData" / "DataBase.dat").read_bytes() == b"JP-DB"


def test_wolf_game_update_is_not_rolled_back(tmp_path):
    from translator.wolfrpg import WolfRPGParser
    game = _wolf_root_game(tmp_path)
    WolfRPGParser().save_project(str(game), [])
    # Game update replaces the DB after the first export.
    (game / "BasicData" / "DataBase.dat").write_bytes(b"JP-DB v2")
    p = WolfRPGParser()
    p.save_project(str(game), [])
    assert (game / "BasicData" / "DataBase.dat").read_bytes() == b"JP-DB v2"
    assert (tmp_path / "WolfGame_original" / "BasicData" /
            "DataBase.dat").read_bytes() == b"JP-DB v2"
    assert p.last_export_notes and "changed since" in p.last_export_notes[0]


def test_wolf_existing_data_wolf_bak_does_not_break_export(tmp_path):
    from translator.wolfrpg import WolfRPGParser
    game = _wolf_root_game(tmp_path)
    (game / "Data.wolf").write_bytes(b"new archive")
    (game / "Data.wolf.bak").write_bytes(b"old archive")
    WolfRPGParser().save_project(str(game), [])
    assert not (game / "Data.wolf").exists()
    assert (game / "Data.wolf.bak").read_bytes() == b"new archive"


# ── VX Ace ───────────────────────────────────────────────────────────

def _ace_game(tmp_path):
    from rubymarshal.writer import writes
    game = tmp_path / "AceGame"
    (game / "Data").mkdir(parents=True)
    (game / "Data" / "Map001.rvdata2").write_bytes(writes(["JP"]))
    (game / "Data" / "Map002.rvdata2").write_bytes(writes(["JP2"]))
    return game


def test_ace_write_failure_never_truncates_live_file(tmp_path, monkeypatch):
    from translator import rpgmaker_ace as ace
    target = tmp_path / "Map001.rvdata2"
    target.write_bytes(b"ORIGINAL")

    def boom(_data):
        raise ValueError("cannot serialise")
    monkeypatch.setattr(ace, "ruby_writes", boom)
    with pytest.raises(ValueError):
        ace.RPGMakerAceParser._write_rvdata2(str(target), object())
    assert target.read_bytes() == b"ORIGINAL"


def test_ace_reverted_file_is_reset_and_failures_are_reported(tmp_path):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game = _ace_game(tmp_path)
    data = game / "Data"
    p = RPGMakerAceParser()
    p.save_project(str(game), [])           # creates Data_original
    # Backup made by v1.1.0 (no export manifest yet)
    os.remove(str(game / "Data_original") + ".manifest.json")
    original = (data / "Map001.rvdata2").read_bytes()
    # A previous export left English in Map001; its line is now reverted.
    (data / "Map001.rvdata2").write_bytes(b"STALE ENGLISH")
    # Map002's backup is unreadable but it has a translation.
    (game / "Data_original" / "Map002.rvdata2").write_bytes(b"\x04\x08garbage")
    entries = [
        _entry("Map001/x", "Map001.rvdata2", "dialog", "JP", "", "untranslated"),
        _entry("Map002/x", "Map002.rvdata2", "dialog", "JP2", "EN2"),
    ]
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "Map002.rvdata2" in str(ei.value)
    assert (data / "Map001.rvdata2").read_bytes() == original


def test_ace_restore_is_copy_then_swap(tmp_path, monkeypatch):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game = _ace_game(tmp_path)
    RPGMakerAceParser().save_project(str(game), [])
    (game / "Data" / "Map001.rvdata2").write_bytes(b"EN")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        RPGMakerAceParser().restore_originals(str(game))
    assert (game / "Data" / "Map001.rvdata2").read_bytes() == b"EN"


# ── RPG Maker 2000/2003 ──────────────────────────────────────────────

def _lcf(header: str, extra: bytes = b"") -> bytes:
    from translator import rpgmaker_2k as rm2k
    return rm2k._write_header(header) + extra


def test_rm2k_revert_all_resets_files_and_reports_failures(tmp_path):
    from translator.rpgmaker_2k import RPGMaker2KParser
    game = tmp_path
    # Backups made by v1.1.0 (no manifest) + stale English in the live files
    (game / "RPG_RT_original.ldb").write_bytes(_lcf("LcfDataBase"))
    (game / "RPG_RT.ldb").write_bytes(_lcf("LcfDataBase", b"EN"))
    (game / "Map0001_original.lmu").write_bytes(_lcf("LcfMapUnit"))
    (game / "Map0001.lmu").write_bytes(_lcf("LcfMapUnit", b"EN"))
    (game / "Map0002_original.lmu").write_bytes(b"\x05\xff\xff\xff\xff\xff")
    (game / "Map0002.lmu").write_bytes(b"\x05\xff\xff\xff\xff\xff")

    with pytest.raises(game_io.PartialExportError) as ei:
        RPGMaker2KParser().save_project(str(game), [])   # nothing translated
    assert "Map0002.lmu" in str(ei.value)
    assert (game / "RPG_RT.ldb").read_bytes() == _lcf("LcfDataBase")
    assert (game / "Map0001.lmu").read_bytes() == _lcf("LcfMapUnit")


# ── LLM output gates ─────────────────────────────────────────────────

def test_single_translate_rejects_invented_placeholder(monkeypatch):
    from translator.ai_client import AIClient
    c = AIClient()
    c.project_type = "rpgmaker_mv"

    def fake_chat(*, messages, **kwargs):
        return {"message": {"content": "«CODE1»Hello «CODE7» there"}}
    monkeypatch.setattr(c, "_chat", fake_chat)
    with pytest.raises(ValueError, match="placeholder"):
        c.translate(r"\C[2]こんにちは")


def test_single_translate_accepts_known_placeholders(monkeypatch):
    from translator.ai_client import AIClient
    c = AIClient()
    c.project_type = "rpgmaker_mv"

    def fake_chat(*, messages, **kwargs):
        return {"message": {"content": "«CODE1»Hello"}}
    monkeypatch.setattr(c, "_chat", fake_chat)
    assert c.translate(r"\C[2]こんにちは") == r"\C[2]Hello"


# ── Ren'Py export ────────────────────────────────────────────────────

def test_renpy_export_escapes_brackets_and_keeps_interpolation(tmp_path):
    from translator.renpy import RenPyParser
    game = tmp_path / "game"
    game.mkdir()
    (game / "script.rpy").write_text(
        'label start:\n    e "[player]、待って{w=0.5}"\n', encoding="utf-8")
    p = RenPyParser()
    entries = p.load_project(str(tmp_path))
    assert len(entries) == 1
    e = entries[0]
    e.translation, e.status = "[player], wait{w=0.5} [sighs] {laughs}", "translated"
    p.save_project(str(tmp_path), entries)
    out = (game / "script.rpy").read_text(encoding="utf-8")
    assert 'e "[player], wait{w=0.5} [[sighs] {{laughs}"' in out
    assert (tmp_path / "game_original" / "script.rpy").exists()


# ── RPG Maker MV/MZ ──────────────────────────────────────────────────

def _first_translatable(entries, file_prefix):
    return next(e for e in entries if e.file.startswith(file_prefix)
                and e.field in ("name", "dialog"))


def test_mv_game_update_after_export_is_not_rolled_back(mv_game, mv_parser):
    import json
    entries = mv_parser.load_project(mv_game)
    item = _first_translatable(entries, "Items.json")
    item.translation, item.status = "Potion EN", "translated"
    mv_parser.save_project(mv_game, entries)

    # Game update: a new item is added to Items.json after the first export
    items_path = os.path.join(mv_game, "data", "Items.json")
    backup_items = os.path.join(mv_game, "data_original", "Items.json")
    with open(backup_items, encoding="utf-8-sig") as f:
        updated = json.load(f)
    updated.append({"id": len(updated), "name": "アップデート品",
                    "description": "", "note": ""})
    with open(items_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False)

    mv_parser.save_project(mv_game, entries)
    with open(items_path, encoding="utf-8") as f:
        live = json.load(f)
    assert any(isinstance(x, dict) and x.get("name") == "アップデート品"
               for x in live)                      # update kept
    assert any(isinstance(x, dict) and x.get("name") == "Potion EN"
               for x in live)                      # translation still applied
    assert mv_parser.last_export_notes
    # Third export with no changes: nothing is re-baselined again
    mv_parser.save_project(mv_game, entries)
    assert mv_parser.last_export_notes == []


def test_mv_parser_restore_failure_leaves_data_intact(mv_game, mv_parser, monkeypatch):
    mv_parser.save_project(mv_game, mv_parser.load_project(mv_game))
    marker = os.path.join(mv_game, "data", "System.json")
    with open(marker, "rb") as f:
        before = f.read()

    def boom(*a, **k):
        raise OSError("AV lock")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        mv_parser.restore_originals(mv_game)
    with open(marker, "rb") as f:
        assert f.read() == before
    monkeypatch.undo()
    mv_parser.restore_originals(mv_game)
    assert os.path.isdir(os.path.join(mv_game, "data"))
    assert not os.path.exists(os.path.join(mv_game, "data.rmt-old"))


def test_vxace_remove_plugins_write_failure_keeps_scripts(tmp_path, monkeypatch):
    """A failed serialise used to leave a truncated Scripts.rvdata2."""
    import rubymarshal.writer
    from rubymarshal.classes import RubyString
    from translator import vxace_plugins
    scripts = [[1, RubyString("Main"), b"x"],
               [2, RubyString("Plugin " + vxace_plugins._TAG), b"y"]]
    path = tmp_path / "Scripts.rvdata2"
    path.write_bytes(rubymarshal.writer.writes(scripts))
    before = path.read_bytes()

    def boom(_obj):
        raise ValueError("cannot serialise")
    monkeypatch.setattr(rubymarshal.writer, "writes", boom)
    assert vxace_plugins.remove_plugins(str(path)) is False
    assert path.read_bytes() == before


# ── Kirikiri ─────────────────────────────────────────────────────────

def test_kirikiri_export_keeps_undecodable_bytes(tmp_path):
    """errors='replace' used to turn every undecodable byte in the whole
    file into '?'; they now survive untouched."""
    from translator.kirikiri import KirikiriParser
    ks = tmp_path / "data" / "scenario" / "a.ks"
    os.makedirs(ks.parent)
    junk = b"; comment \x81\x20 raw\r\n"   # 0x81 0x20 is invalid cp932
    body = "*start\r\n一行目です[p]\r\n".encode("cp932")
    ks.write_bytes(junk + body)
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    assert entries
    for e in entries:
        e.translation, e.status = "First line", "translated"
    p.save_project(str(tmp_path), entries)
    raw = ks.read_bytes()
    assert b"\x81\x20 raw" in raw
    assert b"First line" in raw


def test_kirikiri_export_without_scenario_is_an_error(tmp_path):
    from translator.kirikiri import KirikiriParser
    with pytest.raises(FileNotFoundError):
        KirikiriParser().save_project(str(tmp_path), [])


# ── Preflight: safety steps fail safe ────────────────────────────────

def test_crash_mid_export_never_bakes_our_output_into_backup(mv_game, mv_parser, monkeypatch):
    """If an export dies after writing some files (so the manifest can't
    record them), the next export must not treat our English output as a
    game update and copy it into data_original/."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    entries = mv_parser.load_project(mv_game)
    mv_parser.save_project(mv_game, entries)          # baseline + manifest
    item = next(e for e in entries if e.file == "Items.json" and e.field == "name")
    item.translation, item.status = "Potion EN", "translated"

    real = RPGMakerMVParser._save_plugins

    def die(*a, **k):
        raise KeyboardInterrupt("killed mid-export")
    monkeypatch.setattr(RPGMakerMVParser, "_save_plugins", die)
    with pytest.raises(KeyboardInterrupt):
        mv_parser.save_project(mv_game, entries)      # data/ written, no finish()
    monkeypatch.setattr(RPGMakerMVParser, "_save_plugins", real)

    p2 = RPGMakerMVParser()
    p2.save_project(mv_game, p2.load_project(mv_game))
    with open(os.path.join(mv_game, "data_original", "Items.json"),
              encoding="utf-8-sig") as f:
        assert "Potion EN" not in f.read()
    assert p2.last_export_notes == []


def test_export_aborts_before_writing_if_manifest_cannot_be_marked(tmp_path, monkeypatch):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game = _ace_game(tmp_path)
    RPGMakerAceParser().save_project(str(game), [])
    live = game / "Data" / "Map001.rvdata2"
    before = live.read_bytes()

    def no_disk(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(game_io.ExportManifest, "_write", no_disk)
    with pytest.raises(OSError):
        RPGMakerAceParser().save_project(str(game), [
            _entry("Map001/x", "Map001.rvdata2", "dialog", "JP", "EN")])
    assert live.read_bytes() == before


@pytest.mark.parametrize("live_rel, bak_rel", [
    ("game", "game/BasicData_original"),     # backup inside the replaced folder
    ("game", "game"),
    ("game/Data", "game"),                   # replaced folder inside the backup
])
def test_restore_tree_refuses_overlapping_paths(tmp_path, live_rel, bak_rel):
    (tmp_path / live_rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / bak_rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "game" / "save.dat").write_bytes(b"SAVE")
    with pytest.raises(ValueError):
        game_io.restore_tree(str(tmp_path / bak_rel), str(tmp_path / live_rel))
    assert (tmp_path / "game" / "save.dat").read_bytes() == b"SAVE"
