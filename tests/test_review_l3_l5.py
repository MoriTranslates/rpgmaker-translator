"""Final blind review (3 Nov 2026), findings L3 and L5.

L3: a game file this app wrote that is now missing from the game was
removed outside the translator (game update, hand delete).  The export must
not bring it back from the old backup; it is reported like any other file
changed outside.  A missing file this app never wrote keeps the old
behaviour (it is written from the backup).

L5: uninstall.bat removes every file the package added, also when its name
has spaces, Japanese or cmd special characters; a name that could leave the
data folder is never put in a command.
"""

import glob
import json
import os
import subprocess
from types import SimpleNamespace as NS

import pytest

from translator import game_io


def _entry(eid, file, field, original, translation="", status="untranslated"):
    from translator.project_model import TranslationEntry
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


def _drop_record(manifest_path, key):
    """Make the manifest say this app never wrote *key*."""
    with open(manifest_path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["files"].pop(key)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(raw, f)


# ── L3: check_live ───────────────────────────────────────────────────

def _manifest_game(tmp_path):
    live, bak = tmp_path / "data", tmp_path / "data_original"
    live.mkdir()
    bak.mkdir()
    for name in ("a.json", "b.json", "c.json"):
        (bak / name).write_text("JP " + name, encoding="utf-8")
        (live / name).write_text("EN " + name, encoding="utf-8")
    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    m.start_with_backup()
    m.begin()
    for name in ("a.json", "b.json", "c.json"):
        m.record(name, str(live / name))
    m.finish()
    return live, bak


def _state(live, bak, name):
    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    return game_io.check_live(name, str(live / name), str(bak / name), m)


def test_check_live_missing_file_we_wrote_is_outside(tmp_path):
    live, bak = _manifest_game(tmp_path)
    os.remove(live / "a.json")
    assert _state(live, bak, "a.json") == "outside"
    assert _state(live, bak, "b.json") == "ok"            # still present


def test_check_live_missing_file_we_never_wrote_is_ok(tmp_path):
    live, bak = _manifest_game(tmp_path)
    _drop_record(str(bak) + ".manifest.json", "a.json")
    os.remove(live / "a.json")
    assert _state(live, bak, "a.json") == "ok"            # unchanged behaviour


def test_check_live_missing_file_only_the_journal_knows_is_outside(tmp_path):
    """An export crashed after writing a.json: only the journal says so."""
    live, bak = _manifest_game(tmp_path)
    mpath = str(bak) + ".manifest.json"
    _drop_record(mpath, "a.json")
    m = game_io.ExportManifest(mpath)
    tmp = tmp_path / "tmp"
    tmp.write_text("EN a.json", encoding="utf-8")
    m._intend(str(live / "a.json"), str(tmp))
    os.remove(live / "a.json")
    assert _state(live, bak, "a.json") == "outside"


def test_check_live_missing_live_and_backup_is_ok(tmp_path):
    live, bak = _manifest_game(tmp_path)
    os.remove(live / "a.json")
    os.remove(bak / "a.json")
    assert _state(live, bak, "a.json") == "ok"            # nothing to restore


# ── L3: MV ───────────────────────────────────────────────────────────

def _mv_export(mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    return entries


def _has_en(path):
    with open(path, encoding="utf-8-sig") as f:
        return "EN " in f.read()


def test_mv_file_removed_after_export_is_not_brought_back(mv_game, mv_parser):
    entries = _mv_export(mv_game, mv_parser)
    data = os.path.join(mv_game, "data")
    os.remove(os.path.join(data, "Map001.json"))          # a game update
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.outside == ["Map001.json"]
    assert "changed outside the translator" in str(ei.value)
    assert not os.path.exists(os.path.join(data, "Map001.json"))
    assert os.path.isfile(os.path.join(data + "_original", "Map001.json"))
    assert _has_en(os.path.join(data, "Items.json"))      # present: written


def test_mv_missing_file_never_written_is_still_written(mv_game, mv_parser):
    entries = _mv_export(mv_game, mv_parser)
    data = os.path.join(mv_game, "data")
    _drop_record(data + "_original.manifest.json", "Map001.json")
    os.remove(os.path.join(data, "Map001.json"))
    mv_parser.save_project(mv_game, entries)              # complete
    assert _has_en(os.path.join(data, "Map001.json"))
    assert _has_en(os.path.join(data, "Items.json"))


def test_mv_patch_zip_leaves_out_a_file_the_game_no_longer_has(
        mv_game, mv_parser, tmp_path):
    import zipfile
    entries = _mv_export(mv_game, mv_parser)
    os.remove(os.path.join(mv_game, "data", "Map001.json"))
    zp = str(tmp_path / "p.zip")
    try:
        mv_parser.export_patch_zip(mv_game, entries, zp, game_title="T")
    except game_io.PartialExportError as exc:
        assert "Map001.json" in exc.outside
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert not any(n.endswith("/Map001.json") for n in names)
    assert any(n.endswith("/Items.json") for n in names)


# ── L3: Kirikiri (reset loop and translated files) ───────────────────

def _kirikiri(tmp_path):
    from translator.kirikiri import KirikiriParser
    scen = tmp_path / "data" / "scenario"
    scen.mkdir(parents=True)
    (scen / "a.ks").write_bytes("*start\r\n台詞です[p]\r\n".encode("cp932"))
    (scen / "b.ks").write_bytes("*start\r\n別の台詞[p]\r\n".encode("cp932"))
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        if e.file == "a.ks":
            e.translation, e.status = "EN line", "reviewed"
    p.save_project(str(tmp_path), entries)
    return p, entries, scen


@pytest.mark.parametrize("gone", ["a.ks", "b.ks"])   # translated / reset
def test_kirikiri_file_removed_after_export_is_not_brought_back(tmp_path,
                                                                  gone):
    p, entries, scen = _kirikiri(tmp_path)
    os.remove(scen / gone)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert ei.value.outside == [gone]
    assert not (scen / gone).exists()
    other = scen / ("b.ks" if gone == "a.ks" else "a.ks")
    assert other.is_file()
    assert (b"EN line" in (scen / "a.ks").read_bytes()) if gone == "b.ks" \
        else True


def test_kirikiri_missing_file_never_written_is_still_reset(tmp_path):
    p, entries, scen = _kirikiri(tmp_path)
    _drop_record(str(scen.parent / "scenario_original") + ".manifest.json",
                 "b.ks")
    os.remove(scen / "b.ks")
    p.save_project(str(tmp_path), entries)                # complete
    assert "別の台詞" in (scen / "b.ks").read_bytes().decode("cp932")
    assert b"EN line" in (scen / "a.ks").read_bytes()


# ── L3: Wolf (maps reloaded from the backup) ─────────────────────────

def _wolf(tmp_path, monkeypatch):
    from translator import wolfrpg
    from test_smoke_findings import _WOLF2_DAT, _wolf_game
    mps = b"\x00" * 10 + b"WOLFM\x00" + b"\x00" * 8
    game = _wolf_game(tmp_path, dat=_WOLF2_DAT, mps=mps)
    (game / "Data" / "MapData" / "Map002.mps").write_bytes(mps + b"2")
    p = wolfrpg.WolfRPGParser()

    def reload(backup_dir):
        # Maps whose events hold no text: each one is copied back from the
        # backup, exactly like the real reload of an untranslated map.
        p.maps = [NS(name=f.stem, path=f, events=[])
                  for f in sorted((backup_dir / "MapData").glob("*.mps"))]
        p.common_events, p.databases, p._reload_failures = None, [], []
    monkeypatch.setattr(p, "_reload_from_backup", reload)
    p.save_project(str(game), [])
    manifest = glob.glob(str(tmp_path / "**" / "*.manifest.json"),
                         recursive=True)
    assert len(manifest) == 1
    return p, game / "Data" / "MapData", manifest[0]


def test_wolf_map_removed_after_export_is_not_brought_back(tmp_path,
                                                           monkeypatch):
    p, maps, _m = _wolf(tmp_path, monkeypatch)
    os.remove(maps / "Map002.mps")
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(maps.parent.parent), [])
    assert ei.value.outside == ["MapData/Map002.mps"]
    assert not (maps / "Map002.mps").exists()
    assert (maps / "Map001.mps").is_file()


def test_wolf_missing_map_never_written_is_still_copied(tmp_path,
                                                        monkeypatch):
    p, maps, manifest = _wolf(tmp_path, monkeypatch)
    _drop_record(manifest, "MapData/Map002.mps")
    os.remove(maps / "Map002.mps")
    p.save_project(str(maps.parent.parent), [])           # complete
    assert (maps / "Map002.mps").is_file()


# ── L3: Tyrano and VX Ace (also rebuild every file of the backup) ────

def test_tyrano_file_removed_after_export_is_not_brought_back(tmp_path):
    from test_smoke_findings import _tyrano_game
    from translator.tyranoscript import TyranoScriptParser
    ks, _lang = _tyrano_game(tmp_path, "*start\n台詞です[p]\n")
    (ks.parent / "second.ks").write_text("*start\n別の台詞[p]\n",
                                         encoding="utf-8")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        if e.file == "first.ks":
            e.translation, e.status = "EN " + e.id, "reviewed"
    p.save_project(str(tmp_path), entries)
    os.remove(ks.parent / "second.ks")
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert ei.value.outside == ["second.ks"]
    assert not (ks.parent / "second.ks").exists()
    assert "EN first.ks" in ks.read_text(encoding="utf-8")


def test_ace_file_removed_after_export_is_not_brought_back(tmp_path):
    from test_smoke_findings import _ace_game_dir
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game = _ace_game_dir(tmp_path)
    data = game / "Data"
    (data / "Map002.rvdata2").write_bytes(ruby_writes(["JP2"]))
    entries = [_entry("Map001.rvdata2/x", "Map001.rvdata2", "f", "JP"),
               _entry("Map002.rvdata2/x", "Map002.rvdata2", "f", "JP2")]
    RPGMakerAceParser().save_project(str(game), entries)
    os.remove(data / "Map002.rvdata2")
    with pytest.raises(game_io.PartialExportError) as ei:
        RPGMakerAceParser().save_project(str(game), entries)
    assert ei.value.outside == ["Map002.rvdata2"]
    assert not (data / "Map002.rvdata2").exists()
    assert (data / "Map001.rvdata2").is_file()


# ── L5: uninstall.bat and package file names ─────────────────────────

_ODD_NAMES = [
    "sp ace.json",
    "テスト.json",
    "a&b.json",
    "100%.json",
    "%cd%.json",
    "c^d.json",
    "bang!x!.json",
    "(paren).json",
    "x&echo PWNED&.json",
]


def _uninstall(names):
    from translator.rpgmaker_mv import RPGMakerMVParser
    return RPGMakerMVParser._build_uninstall_bat(
        "data", "js", False, "T", package_files=names)


def test_uninstall_bat_lists_odd_names_quoted_and_escaped():
    bat = _uninstall(_ODD_NAMES)
    assert 'del /F /Q "data\\sp ace.json"' in bat
    assert 'del /F /Q "data\\テスト.json"' in bat
    assert 'del /F /Q "data\\a&b.json"' in bat           # literal in quotes
    assert 'del /F /Q "data\\100%%.json"' in bat         # % doubled
    assert 'del /F /Q "data\\%%cd%%.json"' in bat
    assert 'del /F /Q "data\\c^d.json"' in bat
    assert 'del /F /Q "data\\bang!x!.json"' in bat
    assert 'del /F /Q "data\\(paren).json"' in bat
    assert "setlocal DisableDelayedExpansion" in bat     # ! stays literal


@pytest.mark.parametrize("bad", [
    "..\\evil.json", "../evil.json", "sub\\x.json", "sub/x.json", "..",
    "a..b.json", "C:x.json", 'q"&del x&".json', "*.json", "a?.json",
    "trail.json.", "trail.json ", "", "nl\nx.json", "a|b.json", "a>b.json",
])
def test_uninstall_bat_never_deletes_a_name_that_could_leave_the_folder(bad):
    bat = _uninstall([bad, "ok.json"])
    dels = [ln for ln in bat.splitlines()
            if " del " in ln and '"data\\' in ln]
    assert len(dels) == 1 and "ok.json" in dels[0]        # only the safe one
    assert all("evil" not in ln and "x.json" not in ln
               for ln in bat.splitlines() if "if " in ln)  # no command at all
    assert "were not removed automatically" in bat          # honest about it


def test_uninstall_bat_keeps_the_existing_guards():
    bat = _uninstall(["sp ace.json"])
    line = next(ln for ln in bat.splitlines() if " del " in ln)
    assert line.startswith('if not exist "data_original\\sp ace.json" '
                           'if exist "data\\sp ace.json" del /F /Q ')


def test_uninstall_bat_counts_a_file_it_could_not_remove_as_failed():
    # Blind review 2026-11-05: "Could not remove" was only echoed, so the
    # script still ended with "Done! Original Japanese files restored."
    bat = _uninstall(["sp ace.json"])
    line = next(ln for ln in bat.splitlines() if "Could not remove" in ln)
    assert "set FAIL=1" in line


@pytest.mark.skipif(os.name != "nt", reason="needs cmd.exe")
def test_uninstall_bat_removes_odd_names_and_nothing_else(tmp_path):
    """The real cmd.exe, in a temp folder with harmless files only."""
    player = tmp_path / "player"
    data, orig = player / "data", player / "data_original"
    data.mkdir(parents=True)
    orig.mkdir()
    (orig / "Items.json").write_text("JP", encoding="utf-8")
    (data / "Items.json").write_text("EN", encoding="utf-8")
    for n in _ODD_NAMES:
        (data / n).write_text("added", encoding="utf-8")
    (data / "mine.json").write_text("player", encoding="utf-8")
    (tmp_path / "evil.json").write_text("outside", encoding="utf-8")
    (player / "evil.json").write_text("outside", encoding="utf-8")
    bat = _uninstall(_ODD_NAMES + ["..\\evil.json", "..\\..\\evil.json"])
    (player / "uninstall.bat").write_bytes(bat.encode("utf-8"))
    r = subprocess.run(["cmd", "/c", str(player / "uninstall.bat")],
                       cwd=player, stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, out
    assert "PWNED" not in [ln.strip() for ln in out.splitlines()]  # not run
    assert sorted(os.listdir(data)) == ["Items.json", "mine.json"], out
    assert (data / "Items.json").read_text(encoding="utf-8") == "JP"
    assert (tmp_path / "evil.json").is_file()
    assert (player / "evil.json").is_file()
    assert "evil.json" in out                     # reported as left behind
