"""Export-side tests for RPGMakerMVParser (save_project, plugins.js, patch zip).

Self-contained: each test builds a tiny fake MV game in tmp_path.
"""

import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from translator.project_model import TranslationEntry, TranslationProject  # noqa: E402
from translator.rpgmaker_mv import (  # noqa: E402
    CODE_COMMENT, CODE_COMMENT_CONT, RPGMakerMVParser, _cmd_escape,
)


# ── Fixture builder ──────────────────────────────────────────────

def _cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


NESTED_TEXT = "ネストした文章"


def _plugins():
    inner = json.dumps({"text": json.dumps(NESTED_TEXT, ensure_ascii=False)},
                       ensure_ascii=False)
    return [
        {"name": "MadeWithMv", "status": True, "description": "", "parameters": {}},
        {"name": "TestPlugin", "status": True, "description": "",
         "parameters": {
             "Message": "プラグインの文章",
             "List": json.dumps([inner], ensure_ascii=False),
         }},
    ]


def _write_json(path, data, bom=False):
    text = json.dumps(data, ensure_ascii=False)
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
        f.write(text)


def build_game(root, js_under_www=False):
    """Create a minimal MV game. Returns the project dir."""
    base = os.path.join(root, "www") if js_under_www else root
    data = os.path.join(base, "data")
    js = os.path.join(base, "js")
    fonts = os.path.join(base, "fonts")
    for d in (data, js, fonts):
        os.makedirs(d, exist_ok=True)

    # BOM-prefixed System.json (some tools save that way)
    _write_json(os.path.join(data, "System.json"), {
        "gameTitle": "勇者の物語",
        "locale": "ja_JP",
        "terms": {"messages": {"alwaysDash": "常時ダッシュ"},
                  "commands": ["戦う"], "params": [], "basic": []},
        "elements": [], "skillTypes": [], "weaponTypes": [],
        "armorTypes": [], "equipTypes": [],
    }, bom=True)
    _write_json(os.path.join(data, "Actors.json"), [None, {
        "id": 1, "name": "勇者", "nickname": "", "profile": "",
        "note": "<custom_mp_text:精力,体力>", "faceName": "", "faceIndex": 0,
    }])
    _write_json(os.path.join(data, "Items.json"), [None, {
        "id": 1, "name": "薬草", "description": "体力を回復する", "note": "",
    }])
    _write_json(os.path.join(data, "Troops.json"), [None])
    _write_json(os.path.join(data, "CommonEvents.json"), [None, {
        "id": 1, "name": "CE", "trigger": 0, "switchId": 1, "list": [
            _cmd(105, [2, False]),
            _cmd(405, ["スクロール一行目"]),
            _cmd(405, ["スクロール二行目"]),
            _cmd(0, []),
        ]}])
    _write_json(os.path.join(data, "Map001.json"), {
        "displayName": "始まりの村",
        "events": [None, {"id": 1, "name": "EV001", "pages": [{"list": [
            _cmd(101, ["", 0, 0, 2]),
            _cmd(401, ["こんにちは"]),
            _cmd(401, ["元気ですか？"]),
            _cmd(102, [["はい", "いいえ"], 1, 0, 2, 0]),
            _cmd(402, [0, "はい"]),
            _cmd(0, [], 1),
            _cmd(404, []),
            # Block-boundary case: a 2-line block whose first line equals
            # a later 1-line block.
            _cmd(101, ["", 0, 0, 2]),
            _cmd(401, ["やあ"]),
            _cmd(401, ["続きの行"]),
            _cmd(101, ["", 0, 0, 2]),
            _cmd(401, ["やあ"]),
            _cmd(356, ["ShowInfo お知らせです"]),
            _cmd(357, ["build/ARPG_Core", "cmd", "",
                       json.dumps({"Text": "アクションの文章"},
                                  ensure_ascii=False)]),
            _cmd(108, ["コメント一行目"]),
            _cmd(408, ["コメント二行目"]),
            _cmd(0, []),
        ]}]}],
    })
    with open(os.path.join(js, "plugins.js"), "w", encoding="utf-8") as f:
        f.write("var $plugins =\n" + json.dumps(_plugins(), ensure_ascii=False)
                + ";\n")
    with open(os.path.join(fonts, "gamefont.css"), "w", encoding="utf-8") as f:
        f.write("@font-face { font-family: GameFont; src: url(\"mplus.woff\"); }\n")
    return root


@pytest.fixture
def game(tmp_path):
    return build_game(str(tmp_path / "game"))


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = None
    return p


def _translate(entries, fn=lambda e: "EN " + e.original.replace("\n", " / ")):
    for e in entries:
        if e.status == "untranslated":
            e.translation = fn(e)
            e.status = "translated"


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _map_list(game):
    return _read(os.path.join(game, "data", "Map001.json"))["events"][1]["pages"][0]["list"]


def _snapshot(game):
    out = {}
    for sub in ("data", "js"):
        d = os.path.join(game, sub)
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    out[f"{sub}/{name}"] = f.read()
    return out


def _by_id(entries):
    return {e.id: e for e in entries}


# ── Loading ──────────────────────────────────────────────────────

def test_bom_file_loads(game, parser):
    entries = parser.load_project(game)
    ids = _by_id(entries)
    assert ids["System.json/gameTitle"].original == "勇者の物語"
    assert parser.get_game_title(game) == "勇者の物語"
    assert parser.load_warnings == []


def test_corrupt_file_is_skipped_not_fatal(game, parser):
    with open(os.path.join(game, "data", "Map002.json"), "w") as f:
        f.write("{not json")
    with open(os.path.join(game, "data", "CommonEvents.json"), "w") as f:
        f.write("[")
    entries = parser.load_project(game)
    files = {e.file for e in entries}
    assert "Map001.json" in files
    assert "Map002.json" not in files
    assert any("Map002.json" in w for w in parser.load_warnings)
    assert any("CommonEvents.json" in w for w in parser.load_warnings)


def test_comment_codes_108_first_408_continuation(game, parser):
    parser.extract_comments = True
    assert (CODE_COMMENT, CODE_COMMENT_CONT) == (108, 408)
    entries = parser.load_project(game)
    comments = [e for e in entries if e.field == "comment"]
    assert [c.original for c in comments] == ["コメント一行目\nコメント二行目"]
    comments[0].translation = "Comment one\nComment two"
    comments[0].status = "translated"
    parser.save_project(game, entries)
    lst = _map_list(game)
    texts = [c["parameters"][0] for c in lst if c["code"] in (108, 408)]
    assert texts == ["Comment one", "Comment two"]


# ── save_project ─────────────────────────────────────────────────

def test_export_is_idempotent(game, parser):
    entries = parser.load_project(game)
    _translate(entries)
    parser.save_project(game, entries)
    first = _snapshot(game)
    parser.save_project(game, entries)
    assert _snapshot(game) == first
    sysjson = _read(os.path.join(game, "data", "System.json"))
    assert sysjson["gameTitle"] == "EN 勇者の物語"
    assert sysjson["locale"] == ""


def test_401_line_counts(game, parser):
    entries = parser.load_project(game)
    ids = _by_id(entries)
    two_line = next(e for e in entries
                    if e.field == "dialog" and e.original == "こんにちは\n元気ですか？")
    # 3 lines for a 2-line block -> one extra 401 inserted
    two_line.translation = "Hello\nHow are\nyou?"
    two_line.status = "translated"
    # 1 line for a 2-line block -> padded with an empty 401
    long_block = next(e for e in entries
                      if e.field == "dialog" and e.original == "やあ\n続きの行")
    long_block.translation = "Hey there"
    long_block.status = "translated"
    assert ids  # sanity
    parser.save_project(game, entries)
    lst = _map_list(game)
    t401 = [c["parameters"][0] for c in lst if c["code"] == 401]
    assert t401[:3] == ["Hello", "How are", "you?"]
    assert t401[3:5] == ["Hey there", ""]
    assert t401[5] == "やあ"  # untranslated single-line block untouched


def test_partial_match_never_splits_a_block(game, parser):
    """#8: a 1-line entry must not match the first line of a 2-line block."""
    entries = parser.load_project(game)
    single = [e for e in entries if e.field == "dialog" and e.original == "やあ"]
    assert len(single) == 1
    single[0].translation = "Hi"
    single[0].status = "translated"
    parser.save_project(game, entries)
    lst = _map_list(game)
    t401 = [c["parameters"][0] for c in lst if c["code"] == 401]
    # 2-line block keeps its Japanese, the real 1-line block is translated
    assert t401 == ["こんにちは", "元気ですか？", "やあ", "続きの行", "Hi"]


def test_single_401_mode_still_merges(game, parser):
    parser.single_401_mode = True
    entries = parser.load_project(game)
    _translate(entries, lambda e: "\n".join(
        line + "-EN" for line in e.original.split("\n")))
    parser.save_project(game, entries)
    lst = _map_list(game)
    t401 = [c["parameters"][0] for c in lst if c["code"] == 401]
    assert t401 == ["こんにちは-EN\n元気ですか？-EN", "やあ-EN\n続きの行-EN", "やあ-EN"]


def test_untranslate_all_then_reexport_restores_originals(game, parser):
    """#5: files/plugins whose translations were reverted go back to JP."""
    entries = parser.load_project(game)
    _translate(entries)
    parser.save_project(game, entries)
    assert _read(os.path.join(game, "data", "Items.json"))[1]["name"] == "EN 薬草"

    for e in entries:
        e.translation = ""
        e.status = "untranslated"
    parser.save_project(game, entries)

    backup = os.path.join(game, "data_original")
    for name in os.listdir(backup):
        live = _read(os.path.join(game, "data", name))
        orig = _read(os.path.join(backup, name))
        if name == "System.json":
            orig["locale"] = ""  # export always switches locale
        assert live == orig, name
    live_plugins = parser._load_plugins_js(os.path.join(game, "js", "plugins.js"))
    assert live_plugins == _plugins()


def test_speaker_names_apply_to_files_without_entries(tmp_path, parser):
    game = build_game(str(tmp_path / "g"))
    m = _read(os.path.join(game, "data", "Map001.json"))
    m["events"][1]["pages"][0]["list"][0]["parameters"] = ["", 0, 0, 2, "村人"]
    _write_json(os.path.join(game, "data", "Map001.json"), m)
    entries = parser.load_project(game)
    for e in entries:
        if e.field == "speaker_name":
            e.translation, e.status = "Villager", "translated"
    parser.save_project(game, entries)
    assert _map_list(game)[0]["parameters"][4] == "Villager"


def test_file_missing_from_stale_backup_falls_back_to_live(game, parser):
    entries = parser.load_project(game)
    parser.save_project(game, entries)  # creates data_original/
    # Game updated after the first export: new map not in the backup
    src = _read(os.path.join(game, "data", "Map001.json"))
    _write_json(os.path.join(game, "data", "Map002.json"), src)
    entries = parser.load_project(game)
    e = next(x for x in entries if x.id == "Map002.json/displayName")
    e.translation, e.status = "Start Village", "translated"
    parser.save_project(game, entries)
    assert _read(os.path.join(game, "data", "Map002.json"))["displayName"] == "Start Village"


def test_export_errors_are_collected_and_raised(game, parser):
    entries = parser.load_project(game)
    _translate(entries)
    parser.save_project(game, entries)
    with open(os.path.join(game, "data_original", "Items.json"), "w") as f:
        f.write("{broken")
    for e in entries:
        if e.file == "Actors.json" and e.field == "name":
            e.translation = "Brave"
    with pytest.raises(OSError, match="Items.json"):
        parser.save_project(game, entries)
    # Other files were still written
    assert _read(os.path.join(game, "data", "Actors.json"))[1]["name"] == "Brave"
    leftovers = [n for n in os.listdir(os.path.join(game, "data")) if n.endswith(".tmp")]
    assert leftovers == []


def test_backup_is_atomic(game, parser, monkeypatch):
    import translator.rpgmaker_mv as mv

    def boom(src, dst, *a, **k):
        os.makedirs(dst)
        raise OSError("disk full")

    monkeypatch.setattr(mv.shutil, "copytree", boom)
    entries = parser.load_project(game)
    with pytest.raises(OSError):
        parser.save_project(game, entries)
    assert not os.path.exists(os.path.join(game, "data_original"))
    assert not os.path.exists(os.path.join(game, "data_original.tmp"))


def test_note_tag_translation_is_sanitized(game, parser):
    entries = parser.load_project(game)
    e = next(x for x in entries if x.id == "Actors.json/1/note/custom_mp_text/0")
    e.translation, e.status = "Stamina, <Lust>", "translated"
    parser.save_project(game, entries)
    note = _read(os.path.join(game, "data", "Actors.json"))[1]["note"]
    assert note == "<custom_mp_text:Stamina、 Lust,体力>"


# ── Plugin commands ──────────────────────────────────────────────

def test_mz_plugin_name_with_slash(game, parser):
    entries = parser.load_project(game)
    e = next(x for x in entries if "/plugin_mz_" in x.id)
    assert "/build/ARPG_Core/Text" in e.id
    e.translation, e.status = "Action text", "translated"
    parser.save_project(game, entries)
    cmd = next(c for c in _map_list(game) if c["code"] == 357)
    assert json.loads(cmd["parameters"][3])["Text"] == "Action text"


def test_mv_plugin_command_with_prefixed_context(game, parser):
    entries = parser.load_project(game)
    e = next(x for x in entries if "/plugin_mv_" in x.id)
    e.context = "[Speaker: Hero]\n" + e.context  # e.g. Set Speaker prefix
    e.translation, e.status = "A notice", "translated"
    parser.save_project(game, entries)
    cmd = next(c for c in _map_list(game) if c["code"] == 356)
    assert cmd["parameters"][0] == "ShowInfo A notice"


# ── plugins.js ───────────────────────────────────────────────────

def test_nested_json_string_plugin_value(game, parser):
    entries = parser.load_project(game)
    e = next(x for x in entries if x.original == NESTED_TEXT)
    e.translation, e.status = "Nested text", "translated"
    parser.save_project(game, entries)
    plugins = parser._load_plugins_js(os.path.join(game, "js", "plugins.js"))
    lst = json.loads(plugins[1]["parameters"]["List"])
    assert json.loads(json.loads(lst[0])["text"]) == "Nested text"


def test_inject_wordwrap_then_restore(game, parser):
    """#1: inject backs up plugins.js; restore + remove leaves a bootable game."""
    js = os.path.join(game, "js")
    plugins_js = os.path.join(js, "plugins.js")
    backup = os.path.join(js, "plugins_original.js")
    assert parser.inject_wordwrap_plugin(game, max_chars=40)
    assert os.path.isfile(backup)
    names = [p["name"] for p in parser._load_plugins_js(backup)]
    assert RPGMakerMVParser.INJECTED_PLUGIN_NAME not in names

    # Restore Originals flow (main_window): copy backup, then remove plugin
    shutil.copy2(backup, plugins_js)
    parser.remove_wordwrap_plugin(game)
    names = [p["name"] for p in parser._load_plugins_js(plugins_js)]
    assert RPGMakerMVParser.INJECTED_PLUGIN_NAME not in names
    assert not os.path.exists(os.path.join(js, "plugins", "TranslatorWordWrap.js"))


def test_remove_wordwrap_unregisters_without_backup(game, parser):
    plugins_js = os.path.join(game, "js", "plugins.js")
    assert parser.inject_wordwrap_plugin(game)
    os.remove(os.path.join(game, "js", "plugins_original.js"))
    parser.remove_wordwrap_plugin(game)
    names = [p["name"] for p in parser._load_plugins_js(plugins_js)]
    assert RPGMakerMVParser.INJECTED_PLUGIN_NAME not in names


def test_reexport_drops_stale_injection(game, parser):
    entries = parser.load_project(game)
    parser.save_project(game, entries)
    assert parser.inject_wordwrap_plugin(game)
    assert parser.disable_splash_plugin(game)
    parser.save_project(game, entries)  # regenerated from backup
    plugins = parser._load_plugins_js(os.path.join(game, "js", "plugins.js"))
    assert plugins == _plugins()


def test_disable_splash_backs_up(game, parser):
    assert parser.disable_splash_plugin(game)
    backup = parser._load_plugins_js(os.path.join(game, "js", "plugins_original.js"))
    assert backup[0]["status"] is True


# ── Patch zip / bat scripts ──────────────────────────────────────

def test_cmd_escape():
    assert _cmd_escape('A & B (100%) <x> | ^ "q"') == \
        "A ^& B ^(100%%^) ^<x^> ^| ^^ 'q'"


def test_bat_parens_escaped_inside_blocks():
    inst = RPGMakerMVParser._build_install_bat(
        "www/data", "www/js", 5, True, "Tom & Jerry (100%)", font_name="Consolas")
    un = RPGMakerMVParser._build_uninstall_bat(
        "www/data", "www/js", True, "Tom & Jerry (100%)")
    assert "Tom ^& Jerry ^(100%%^)" in inst
    assert "Tom ^& Jerry ^(100%%^)" in un
    assert "Backup already exists ^(www\\data_original\\^)" in inst
    assert "^(5 files^)" in inst
    assert "No backup found ^(www\\data_original\\^)" in un
    assert "Swapped font to Consolas" in inst
    assert "www\\fonts\\gamefont.css" in inst


def test_patch_zip_font_path_root_layout(game, parser):
    parser.game_font = "Courier New"
    entries = parser.load_project(game)
    _translate(entries)
    zp = os.path.join(game, "..", "patch.zip")
    parser.export_patch_zip(game, entries, zp, game_title="A & B")
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
        inst = zf.read("install.bat").decode("utf-8")
    assert "_translation/fonts/gamefont.css" in names  # not js/fonts
    assert '"fonts\\gamefont.css"' in inst
    assert "Swapped font to Courier New" in inst


@pytest.mark.skipif(sys.platform != "win32", reason="needs cmd.exe")
def test_install_and_uninstall_bat_run(tmp_path, parser):
    parser.game_font = "Consolas"
    game = build_game(str(tmp_path / "game"))
    entries = parser.load_project(game)
    _translate(entries)
    zp = str(tmp_path / "patch.zip")
    parser.export_patch_zip(game, entries, zp, game_title="Tom & Jerry (100%)")
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(game)
    for script in ("install.bat", "uninstall.bat"):
        r = subprocess.run(["cmd", "/c", os.path.join(game, script)], cwd=game, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        assert r.returncode == 0, out
        assert "FAILED" not in out, out
        if script == "install.bat":
            assert _read(os.path.join(game, "data", "Items.json"))[1]["name"] == "EN 薬草"
            assert os.path.isfile(os.path.join(game, "fonts", "gamefont_original.css"))
    assert _read(os.path.join(game, "data", "Items.json"))[1]["name"] == "薬草"


# ── project_model ────────────────────────────────────────────────

def test_project_index_rebuilds_when_entries_extended():
    proj = TranslationProject(entries=[TranslationEntry("a/1", "a.json", "f", "x")])
    assert proj.get_entry_by_id("a/1") is not None
    proj.entries.append(TranslationEntry("b/1", "b.json", "f", "y"))
    assert proj.get_entry_by_id("b/1") is not None
    assert proj.get_files() == ["a.json", "b.json"]
    proj.entries = [TranslationEntry("c/1", "c.json", "f", "z")]
    assert proj.get_entries_for_file("c.json")[0].id == "c/1"
    assert proj.get_entry_by_id("a/1") is None


def test_import_patch_ignores_unknown_fields(tmp_path):
    zp = str(tmp_path / "p.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("patch.json", json.dumps({"entries": [{
            "id": "a/1", "file": "a.json", "field": "f", "original": "x",
            "translation": "X", "status": "translated", "future_field": 1,
        }]}))
    proj = TranslationProject.import_patch(zp)
    assert proj.entries[0].translation == "X"


def test_save_load_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    proj = TranslationProject(project_path="p",
                              entries=[TranslationEntry("a/1", "a.json", "f", "x")])
    proj.save_state(path)
    assert TranslationProject.load_state(path).entries[0].id == "a/1"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    del data["project_type"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    assert TranslationProject.load_state(path).project_type == "rpgmaker_mv"
