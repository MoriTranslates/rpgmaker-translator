"""YEP_MessageCore end-of-message name boxes ("「…」\\n<name>") and the
YEP "Word Wrap Space" export warning.

Made-up Japanese lines only.  Each test builds a tiny MV game in tmp_path
(test_mv_export.build_game) and swaps in its own Map001 dialogue.
"""

import json
import os

import pytest

from test_mv_export import _cmd, _read, build_game
from translator.project_model import (
    TranslationEntry, TranslationProject, fold_legacy_end_namebox,
)
from translator.rpgmaker_mv import RPGMakerMVParser
from translator.state_migration import migrate_entries, transfer_entry

NB = "\\n<八尋>"        # the YEP namebox code as it appears in a 401 line


# ── helpers ──────────────────────────────────────────────────────

def _write_map(game, blocks):
    """Map001 with one event page: a 101 header + 401 lines per block."""
    cmds = []
    for lines in blocks:
        cmds.append(_cmd(101, ["", 0, 0, 2]))
        cmds.extend(_cmd(401, [line]) for line in lines)
    cmds.append(_cmd(0, []))
    path = os.path.join(game, "data", "Map001.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"displayName": "", "events": [None, {
            "id": 1, "name": "EV001", "pages": [{"list": cmds}]}]},
            f, ensure_ascii=False)


def _game(tmp_path, blocks):
    game = build_game(str(tmp_path / "game"))
    _write_map(game, blocks)
    return game


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = None
    return p


def _dialogs(entries):
    return [e for e in entries if e.field == "dialog"]


def _speakers(entries):
    return {e.original: e for e in entries if e.field == "speaker_name"}


def _map_texts(game):
    data = _read(os.path.join(game, "data", "Map001.json"))
    return [c["parameters"][0] for c in data["events"][1]["pages"][0]["list"]
            if c["code"] == 401]


def _translate(entries, names=None):
    for e in entries:
        if e.field == "speaker_name":
            if names and e.original in names:
                e.translation = names[e.original]
                e.status = "translated"
        elif e.field == "dialog" and e.status == "untranslated":
            e.translation = "EN " + e.original.replace("\n", " / ")
            e.status = "translated"


# ── parsing ──────────────────────────────────────────────────────

def test_end_namebox_on_last_line_is_split_off(tmp_path, parser):
    game = _game(tmp_path, [["「おはよう」", "「行こうか」" + NB]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.original == "「おはよう」\n「行こうか」"
    assert d.namebox_end == NB
    assert d.namebox == ""
    assert d.context.startswith("[Speaker: 八尋]")
    # The name is translated like a start-of-line name box
    assert "八尋" in _speakers(entries)
    # Glossary / term checks only see the dialogue, never the speaker tag
    assert "八尋" not in d.original


def test_end_namebox_alone_on_its_own_line(tmp_path, parser):
    game = _game(tmp_path, [["「おはよう」", NB]])
    d = _dialogs(parser.load_project(game))[0]
    assert d.original == "「おはよう」"
    assert d.namebox_end == "\n" + NB


@pytest.mark.parametrize("name", ["ミス・アリス (仮)", "八尋 & 真白", "村人[1]",
                                  "\\C[6]八尋\\C[0]"])
def test_names_with_spaces_symbols_and_colour_codes(tmp_path, parser, name):
    code = "\\N<" + name + ">"
    game = _game(tmp_path, [["「やあ」" + code]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.original == "「やあ」"
    assert d.namebox_end == code
    assert name in _speakers(entries)


@pytest.mark.parametrize("lines", [
    ["「やあ」\\n<>"],                    # empty tag
    ["「やあ」\\n<   >"],                 # blank name
    ["「やあ」<八尋>"],                   # bare <name> is not a YEP code
    ["「やあ」<WordWrap>"],               # plugin tag
    ["<WordWrap>「やあ」", "続き<br>"],   # plugin tags at start / end
    ["「やあ」" + NB + "\\C[0]"],         # something follows the code
    ["「やあ」" + NB + "続き"],           # code mid-dialogue
    ["「やあ」" + NB, "続きの行"],        # code on a line that isn't last
    ["「やあ」" + NB, "", "", "続き"],    # text after the padding lines
    ["「やあ」\\n<八\n尋>"],              # name spanning a line break
])
def test_not_an_end_namebox(tmp_path, parser, lines):
    game = _game(tmp_path, [lines])
    d = _dialogs(parser.load_project(game))[0]
    assert d.namebox_end == ""
    assert d.original == "\n".join(lines)


def test_only_a_code_is_a_start_namebox_not_an_end_one(tmp_path, parser):
    game = _game(tmp_path, [[NB]])
    d = _dialogs(parser.load_project(game))[0]
    assert d.namebox == NB and d.namebox_end == "" and d.original == ""


def test_trailing_whitespace_kept_in_the_suffix(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB + "  \t"]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.original == "「やあ」"
    assert d.namebox_end == NB + "  \t"
    _translate(entries, {"八尋": "Yahiro"})
    parser.save_project(game, entries)
    assert _map_texts(game) == ["EN 「やあ」\\n<Yahiro>  \t"]


def test_padding_lines_after_the_code_belong_to_the_suffix(tmp_path, parser):
    """Real games pad messages to 4 lines with empty 401s after the code."""
    game = _game(tmp_path, [["「やあ」 " + NB, "", "", ""]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.original == "「やあ」"
    assert d.namebox_end == " " + NB + "\n\n\n"
    _translate(entries, {"八尋": "Yahiro"})
    parser.save_project(game, entries)
    assert _map_texts(game) == ["EN 「やあ」 \\n<Yahiro>", "", "", ""]


def test_padding_never_pushes_a_longer_translation_past_the_box(tmp_path,
                                                                parser):
    game = _game(tmp_path, [["「やあ」" + NB, "", "", ""]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = "One.\nTwo.\nThree."
    parser.save_project(game, entries)
    # 4 lines like the original: only one padding line is kept
    assert _map_texts(game) == ["One.", "Two.", "Three.\\n<Yahiro>", ""]
    _dialogs(entries)[0].translation = "1\n2\n3\n4\n5"
    parser.save_project(game, entries)
    # Longer than the box anyway: no padding added on top (same as before)
    assert _map_texts(game) == ["1", "2", "3", "4", "5\\n<Yahiro>"]


def test_start_and_end_codes_keep_today_behaviour(tmp_path, parser):
    """Both in one message: only the start one is a name box; the end code
    stays in the text (never guessed)."""
    game = _game(tmp_path, [["\\N<村人>「やあ」" + NB]])
    d = _dialogs(parser.load_project(game))[0]
    assert d.namebox == "\\N<村人>"
    assert d.namebox_end == ""
    assert d.original == "「やあ」" + NB


def test_bare_actor_label_wins_over_end_code(tmp_path, parser):
    game = _game(tmp_path, [["\\N[1]「やあ」" + NB]])
    d = _dialogs(parser.load_project(game))[0]
    assert d.namebox == "\\N[1]" and d.namebox_end == ""


def test_crlf_inside_a_401_line(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」\r\n" + NB]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.original == "「やあ」"
    assert d.namebox_end == "\r\n" + NB
    _translate(entries, {"八尋": "Yahiro"})
    parser.save_project(game, entries)
    assert _map_texts(game) == ["EN 「やあ」\r\n\\n<Yahiro>"]


def test_speaker_processing_off_leaves_text_alone(tmp_path, parser):
    parser.speaker_processing = False
    game = _game(tmp_path, [["「やあ」" + NB]])
    d = _dialogs(parser.load_project(game))[0]
    assert d.original == "「やあ」" + NB and d.namebox_end == ""


def test_actor_code_end_namebox(tmp_path, parser):
    code = "\\n<\\n[1]>"
    game = _game(tmp_path, [["「やあ」" + code]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.namebox_end == code
    assert d.context.startswith("[Speaker: 勇者]")
    assert "\\n[1]" not in _speakers(entries)
    _translate(entries)
    parser.save_project(game, entries)
    assert _map_texts(game) == ["EN 「やあ」" + code]


# ── export ───────────────────────────────────────────────────────

def test_export_appends_translated_name_at_the_end(tmp_path, parser):
    game = _game(tmp_path, [["「おはよう」", "「行こうか」" + NB],
                            ["「ただいま」", NB]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    parser.save_project(game, entries)
    assert _map_texts(game) == [
        # one translated line: the code goes on the (padded) last line
        "EN 「おはよう」 / 「行こうか」", "\\n<Yahiro>",
        "EN 「ただいま」", "\\n<Yahiro>",
    ]
    # Re-export reads the backup: same result (idempotent)
    first = _map_texts(game)
    parser.save_project(game, entries)
    assert _map_texts(game) == first


def test_export_keeps_jp_name_when_speaker_untranslated(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    entries = parser.load_project(game)
    _translate(entries)          # names left untranslated
    parser.save_project(game, entries)
    assert _map_texts(game) == ["EN 「やあ」" + NB]


def test_export_never_doubles_a_code_the_user_typed(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = "Hi!\\n<Yahi>"
    parser.save_project(game, entries)
    assert _map_texts(game) == ["Hi!\\n<Yahi>"]


def test_multiline_translation_puts_code_after_last_line(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = "Hey.\nLong time no see."
    parser.save_project(game, entries)
    assert _map_texts(game) == ["Hey.", "Long time no see.\\n<Yahiro>"]


# ── saved projects from before this change ───────────────────────

def _legacy(entries):
    """The entries the previous version produced: no end-namebox split,
    no speaker entry for names that only appear in end codes."""
    out = []
    for e in entries:
        if e.field == "speaker_name":
            continue
        d = dict(e.__dict__)
        d["original"] = d["original"] + d.pop("namebox_end")
        out.append(d)
    return out


def _write_old_state(path, game, entry_dicts):
    """A state file exactly as the previous version wrote it (no key)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"project_path": game, "project_type": "rpgmaker_mv",
                   "entries": entry_dicts, "glossary": {},
                   "actor_genders": {}, "id_scheme": 3},
                  f, ensure_ascii=False, indent=2)


def test_old_project_loads_and_exports_byte_identical(tmp_path, parser):
    blocks = [["「おはよう」"],                 # no tag
              ["「行こうか」" + NB],            # end tag on last line
              ["\\N<村人>「こんにちは」"],      # start tag
              ["「ただいま」", NB]]             # end tag alone
    game = _game(tmp_path, blocks)
    fresh = parser.load_project(game)
    old = _legacy(fresh)
    for d in old:
        if d["field"] == "dialog":
            # Old translations carry the JP code the way the AI kept it
            d["translation"] = "EN " + d["original"].replace("\n", " / ")
            d["status"] = "translated"
    state = str(tmp_path / "old_state.json")
    _write_old_state(state, game, old)

    project = TranslationProject.load_state(state)
    # Same IDs, same order, same text as saved
    assert [e.id for e in project.entries] == [d["id"] for d in old]
    assert [e.original for e in project.entries] == [d["original"] for d in old]
    assert all(e.namebox_end == "" for e in project.entries)

    map_path = os.path.join(game, "data", "Map001.json")
    expected = _read(map_path)
    parser.save_project(game, project.entries)
    # Exactly the old translation text, line for line (old behaviour)
    old_texts = [
        "EN 「おはよう」",
        "EN 「行こうか」" + NB,
        "\\N<村人>EN 「こんにちは」",
        "EN 「ただいま」 / " + NB, "",
    ]
    assert _map_texts(game) == old_texts
    # Byte for byte: the source map with only those 401 texts replaced,
    # serialized the way export writes JSON (checked against master by
    # hand as well, on a project saved by master)
    it = iter(old_texts)
    for c in expected["events"][1]["pages"][0]["list"]:
        if c["code"] == 401:
            c["parameters"][0] = next(it)
    # (text-mode write: CRLF on Windows; JSON escapes any CR in strings)
    with open(map_path, "rb") as f:
        assert f.read().replace(b"\r\n", b"\n") == json.dumps(
            expected, ensure_ascii=False, indent=2).encode("utf-8")


def test_lines_without_end_tags_identical_old_and_new(tmp_path, parser):
    """New-format save of the same game: lines without end codes export
    byte-identical to the old-format save."""
    blocks = [["「おはよう」", "「元気？」"], ["\\N<村人>「こんにちは」"]]
    game_a = _game(tmp_path / "a", blocks)
    game_b = _game(tmp_path / "b", blocks)
    new = parser.load_project(game_a)
    _translate(new)
    old_state = str(tmp_path / "s.json")
    _write_old_state(old_state, game_b, _legacy(new))
    old = TranslationProject.load_state(old_state).entries
    parser.save_project(game_a, new)
    RPGMakerMVParser().save_project(game_b, old)
    for name in ("Map001.json",):
        with open(os.path.join(game_a, "data", name), "rb") as f1, \
                open(os.path.join(game_b, "data", name), "rb") as f2:
            assert f1.read() == f2.read()


def test_new_state_roundtrip_keeps_namebox_end(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    project = TranslationProject(project_path=game,
                                 entries=parser.load_project(game))
    path = str(tmp_path / "state.json")
    project.save_state(path)
    loaded = TranslationProject.load_state(path)
    assert _dialogs(loaded.entries)[0].namebox_end == NB


def test_import_old_project_into_fresh_parse_folds_code_back(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    fresh = parser.load_project(game)
    old_dicts = _legacy(fresh)
    old_dicts = [d for d in old_dicts if d["field"] == "dialog"]
    for d in old_dicts:
        d["translation"] = "Hi!" + NB
        d["status"] = "translated"
    old = TranslationProject(entries=[TranslationEntry(**d) for d in old_dicts])
    project = TranslationProject(project_path=game, entries=fresh)
    stats = project.import_translations(old)
    assert stats["by_id"] == 1
    d = _dialogs(project.entries)[0]
    assert d.original == "「やあ」" + NB and d.namebox_end == ""
    parser.save_project(game, project.entries)
    assert _map_texts(game) == ["Hi!" + NB]       # exactly as before


def test_import_old_project_by_text_when_ids_differ(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    fresh = parser.load_project(game)
    old_dicts = _legacy(fresh)
    for d in old_dicts:
        d["id"] = d["id"].replace("dialog_1", "dialog_9")
        d["translation"] = "Hi!" + NB
        d["status"] = "translated"
    old = TranslationProject(entries=[TranslationEntry(**d) for d in old_dicts])
    project = TranslationProject(project_path=game, entries=fresh)
    stats = project.import_translations(old)
    assert stats["by_text"] == 1
    assert _dialogs(project.entries)[0].translation == "Hi!" + NB
    assert _dialogs(project.entries)[0].namebox_end == ""


def test_migration_matches_old_raw_text_and_folds_back(tmp_path, parser):
    game = _game(tmp_path, [["「やあ」" + NB]])
    fresh = parser.load_project(game)
    old_dicts = _legacy(fresh)
    old_dicts = [d for d in old_dicts if d["field"] == "dialog"]
    for d in old_dicts:
        d["translation"] = "Hi!" + NB
        d["status"] = "translated"
    old = [TranslationEntry(**d) for d in old_dicts]
    out, res = migrate_entries(old, [e for e in fresh
                                     if e.field != "speaker_name"], "mv")
    assert res.migrated == 1 and res.unmatched == 0
    d = _dialogs(out)[0]
    assert d.translation == "Hi!" + NB
    assert d.original == "「やあ」" + NB and d.namebox_end == ""


def test_fold_only_when_texts_really_match():
    new = TranslationEntry(id="x", file="f", field="dialog", original="「やあ」",
                           namebox_end=NB)
    other = TranslationEntry(id="x", file="f", field="dialog",
                             original="「違う」" + NB)
    assert not fold_legacy_end_namebox(other, new)
    assert new.namebox_end == NB
    same_new = TranslationEntry(id="x", file="f", field="dialog",
                                original="「やあ」", namebox_end=NB)
    assert not fold_legacy_end_namebox(same_new, new)   # new-format save
    # transfer between two new-format entries keeps the split
    transfer_entry(same_new, new)
    assert new.namebox_end == NB


# ── YEP "Word Wrap Space" ────────────────────────────────────────

def _set_yep(game, value="false", status=True, name="YEP_MessageCore",
             js="plugins.js"):
    path = os.path.join(game, "js", js)
    plugins = RPGMakerMVParser._load_plugins_js(os.path.join(game, "js",
                                                             "plugins.js"))
    plugins = [p for p in plugins if p.get("name") != name]
    params = {"Word Wrapping": "false", "Tight Wrap": "false"}
    if value is not None:
        params["Word Wrap Space"] = value
    plugins.insert(0, {"name": name, "status": status,
                       "description": "メッセージ", "parameters": params})
    with open(path, "w", encoding="utf-8") as f:
        f.write("var $plugins =\n" + json.dumps(plugins, ensure_ascii=False)
                + ";\n")


def _yep_param(game):
    for p in RPGMakerMVParser._load_plugins_js(
            os.path.join(game, "js", "plugins.js")):
        if p.get("name") == "YEP_MessageCore":
            return p["parameters"].get("Word Wrap Space")
    return None


@pytest.mark.parametrize("value,status,expected", [
    ("false", True, True),
    (" False ", True, True),
    ("true", True, False),
    ("false", False, False),     # plugin disabled
    (None, True, False),         # parameter missing
])
def test_yep_wrap_space_detection(tmp_path, parser, value, status, expected):
    game = build_game(str(tmp_path / "game"))
    _set_yep(game, value, status)
    assert parser.yep_word_wrap_space_off(game) is expected


def test_yep_detection_without_yep_or_plugins_js(tmp_path, parser):
    game = build_game(str(tmp_path / "game"))
    assert parser.yep_word_wrap_space_off(game) is False
    os.remove(os.path.join(game, "js", "plugins.js"))
    assert parser.yep_word_wrap_space_off(game) is False
    assert parser.enable_yep_word_wrap_space(game) is False


def test_yep_detection_unreadable_plugins_js(tmp_path, parser):
    game = build_game(str(tmp_path / "game"))
    with open(os.path.join(game, "js", "plugins.js"), "w",
              encoding="utf-8") as f:
        f.write("var $plugins = [ {broken ;\n")
    assert parser.yep_word_wrap_space_off(game) is False


def test_yep_fix_after_export_backs_up_and_changes_one_param(tmp_path, parser):
    game = build_game(str(tmp_path / "game"))
    _set_yep(game, "false")
    entries = parser.load_project(game)
    _translate(entries)
    parser.save_project(game, entries)
    assert parser.yep_word_wrap_space_off(game)
    before = RPGMakerMVParser._load_plugins_js(
        os.path.join(game, "js", "plugins.js"))

    assert parser.enable_yep_word_wrap_space(game) is True
    after = RPGMakerMVParser._load_plugins_js(
        os.path.join(game, "js", "plugins.js"))
    assert _yep_param(game) == "true"
    # Nothing else changed
    for p in before:
        if p["name"] == "YEP_MessageCore":
            p["parameters"]["Word Wrap Space"] = "true"
    assert after == before
    # The pristine original stays in the backup, so the check still reports
    # what the next export starts from (and Restore Originals undoes it)
    backup = RPGMakerMVParser._load_plugins_js(
        os.path.join(game, "js", "plugins_original.js"))
    assert next(p for p in backup if p["name"] == "YEP_MessageCore")[
        "parameters"]["Word Wrap Space"] == "false"
    # Our own change is recorded: the next export is not blocked by it
    parser.save_project(game, entries)
    assert "plugins.js" not in parser.last_outside
    assert parser.enable_yep_word_wrap_space(game) is True


def test_yep_fix_refuses_plugins_js_changed_outside(tmp_path, parser):
    game = build_game(str(tmp_path / "game"))
    _set_yep(game, "false")
    entries = parser.load_project(game)
    for e in entries:            # plugin text too: plugins.js gets a backup
        e.translation, e.status = "EN " + e.original, "translated"
    parser.save_project(game, entries)
    assert os.path.exists(os.path.join(game, "js", "plugins_original.js"))
    path = os.path.join(game, "js", "plugins.js")
    with open(path, "a", encoding="utf-8") as f:
        f.write("// game update\n")
    with open(path, "rb") as f:
        raw = f.read()
    assert parser.enable_yep_word_wrap_space(game) is False
    with open(path, "rb") as f:
        assert f.read() == raw


def test_yep_fix_noop_when_already_on(tmp_path, parser):
    game = build_game(str(tmp_path / "game"))
    _set_yep(game, "true")
    path = os.path.join(game, "js", "plugins.js")
    with open(path, "rb") as f:
        raw = f.read()
    assert parser.enable_yep_word_wrap_space(game) is False
    with open(path, "rb") as f:
        assert f.read() == raw
    assert not os.path.exists(os.path.join(game, "js", "plugins_original.js"))


def test_yep_warning_text_tells_how_to_fix():
    msg = RPGMakerMVParser.YEP_WRAP_SPACE_WARNING
    assert "YEP_MessageCore" in msg and "Word Wrap Space" in msg
    assert "true" in msg


# ── Export to Game prompt (UI) ───────────────────────────────────

@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from qt_helpers import dispose_main_window
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = True
    yield win
    dispose_main_window(win)


def _export_ui(window, monkeypatch, tmp_path, answer, wrap_off=True,
               keep_project=False, confirm=True):
    from translator.widgets import main_window as mw
    if not keep_project:
        e = TranslationEntry(id="a", file="Map001.json", field="dialog",
                             original="はい", translation="Yes",
                             status="translated")
        window.project = TranslationProject(project_path=str(tmp_path),
                                            entries=[e])
    window._closing = False
    window._disable_splash = False
    window._show_translation_splash = False
    calls = {"saved": 0, "fixed": 0, "asked": []}
    monkeypatch.setattr(mw.QDialog, "exec",
                        lambda self: (mw.QDialog.DialogCode.Accepted if confirm
                                      else mw.QDialog.DialogCode.Rejected))

    def warning(*a, **k):
        calls["asked"].append(a[1])
        return answer
    monkeypatch.setattr(mw.QMessageBox, "warning", warning)
    monkeypatch.setattr(mw.QMessageBox, "information",
                        lambda *a, **k: calls.setdefault("info", a[2]))
    monkeypatch.setattr(window, "_check_unwrapped_entries", lambda t: 0)
    monkeypatch.setattr(window.handler, "save_project",
                        lambda p, ents: calls.__setitem__(
                            "saved", calls["saved"] + 1))
    monkeypatch.setattr(window.parser, "yep_word_wrap_space_off",
                        lambda p: wrap_off)

    def fix(p):
        calls["fixed"] += 1
        return True
    monkeypatch.setattr(window.parser, "enable_yep_word_wrap_space", fix)
    window._export_to_game()
    window._closing = True
    return calls


def test_export_prompt_yes_fixes_after_export(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    calls = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.Yes)
    assert calls["asked"] == ["YEP Word Wrap Space Is Off"]
    assert calls["saved"] == 1 and calls["fixed"] == 1
    assert "Word Wrap Space" in calls["info"]


def test_export_prompt_no_exports_without_fixing(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    calls = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.No)
    assert calls["saved"] == 1 and calls["fixed"] == 0


def test_export_prompt_cancel_stops_export(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    calls = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.Cancel)
    assert calls["saved"] == 0 and calls["fixed"] == 0


def test_no_prompt_when_wrap_space_is_on(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    calls = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.Yes, wrap_off=False)
    assert calls["asked"] == [] and calls["fixed"] == 0
    assert calls["saved"] == 1


# ── review fixes: punctuation-only dialogue before an end code ───

DOTS = "「……」"          # nothing to translate once the code is split off


@pytest.mark.parametrize("lines,expected", [
    ([DOTS + NB], [DOTS + "\\n<Yahiro>"]),
    ([DOTS, NB, "", ""], [DOTS, "\\n<Yahiro>", "", ""]),
    ([DOTS + " " + NB + "  "], [DOTS + " \\n<Yahiro>  "]),
    (["……", "...", NB], ["……", "...", "\\n<Yahiro>"]),
])
def test_skipped_dialogue_still_gets_translated_end_name(tmp_path, parser,
                                                        lines, expected):
    game = _game(tmp_path, [lines])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    assert d.status == "skipped" and d.namebox_end
    original = d.original
    _translate(entries, {"八尋": "Yahiro"})
    parser.save_project(game, entries)
    assert _map_texts(game) == expected
    # The export never changes the project entry itself
    assert d.status == "skipped" and d.translation == ""
    assert d.original == original


def test_skipped_dialogue_name_untranslated_exports_unchanged(tmp_path, parser):
    lines = [DOTS, NB, "", ""]
    game = _game(tmp_path, [lines])
    entries = parser.load_project(game)
    parser.save_project(game, entries)          # no speaker translation
    assert _map_texts(game) == lines


def test_skipped_dialogue_end_name_in_patch_zip(tmp_path, parser):
    import zipfile
    game = _game(tmp_path, [[DOTS + NB], ["「やあ」" + NB]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    zp = str(tmp_path / "patch.zip")
    parser.export_patch_zip(game, entries, zp)
    with zipfile.ZipFile(zp) as zf:
        data = json.loads(zf.read("_translation/data/Map001.json"))
    texts = [c["parameters"][0]
             for c in data["events"][1]["pages"][0]["list"]
             if c["code"] == 401]
    assert texts == [DOTS + "\\n<Yahiro>", "EN 「やあ」\\n<Yahiro>"]
    assert not parser.last_patch_skipped      # nothing left unplaced


def test_user_skipped_line_without_end_code_still_not_exported(tmp_path,
                                                               parser):
    game = _game(tmp_path, [["「やあ」"]])
    entries = parser.load_project(game)
    d = _dialogs(entries)[0]
    d.translation, d.status = "Hi", "skipped"
    parser.save_project(game, entries)
    assert _map_texts(game) == ["「やあ」"]


def _old_dots_project(fresh, translation, new_id=None):
    old_dicts = [d for d in _legacy(fresh) if d["field"] == "dialog"]
    for d in old_dicts:
        d["translation"] = translation
        d["status"] = "translated"
        if new_id:
            d["id"] = d["id"].replace("dialog_1", new_id)
    return TranslationProject(
        entries=[TranslationEntry(**d) for d in old_dicts])


@pytest.mark.parametrize("new_id,stat", [(None, "by_id"),
                                         ("dialog_9", "by_text")])
def test_import_old_save_restores_skipped_split_line(tmp_path, parser,
                                                     new_id, stat):
    lines = [DOTS, NB, "", ""]
    game = _game(tmp_path, [lines])
    fresh = parser.load_project(game)
    old = _old_dots_project(fresh, "...\n\\n<Yahiro>\n\n", new_id)
    project = TranslationProject(project_path=game, entries=fresh)
    stats = project.import_translations(old)
    assert stats[stat] == 1
    d = _dialogs(project.entries)[0]
    assert d.status == "translated"
    assert d.original == "\n".join(lines) and d.namebox_end == ""
    # Name untranslated in the new project: the export is exactly the old
    # translation, as the previous version wrote it
    parser.save_project(game, project.entries)
    assert _map_texts(game) == ["...", "\\n<Yahiro>", "", ""]


def test_import_never_fills_skipped_line_from_another_line(tmp_path, parser):
    game = _game(tmp_path, [[DOTS + NB]])
    fresh = parser.load_project(game)
    # An old, different line with the same dialogue but no end code
    old = TranslationProject(entries=[TranslationEntry(
        id="Map001.json/Ev1(EV001)/p0/dialog_7", file="Map001.json",
        field="dialog", original=DOTS, translation="...",
        status="translated")])
    project = TranslationProject(project_path=game, entries=fresh)
    stats = project.import_translations(old)
    d = _dialogs(project.entries)[0]
    assert d.status == "skipped" and d.translation == ""
    assert d.namebox_end == NB
    assert stats["by_id"] == stats["by_text"] == 0


def test_import_keeps_a_skipped_line_the_user_already_filled(tmp_path, parser):
    game = _game(tmp_path, [[DOTS + NB]])
    fresh = parser.load_project(game)
    old = _old_dots_project(fresh, "Old" + NB)
    d = _dialogs(fresh)[0]
    d.translation = "Mine"          # user typed it but left it skipped
    project = TranslationProject(project_path=game, entries=fresh)
    project.import_translations(old)
    assert d.translation == "Mine" and d.namebox_end == NB


def test_migration_restores_skipped_split_line(tmp_path, parser):
    game = _game(tmp_path, [[DOTS + NB]])
    fresh = parser.load_project(game)
    old = _old_dots_project(fresh, "..." + NB).entries
    out, res = migrate_entries(old, [e for e in fresh
                                     if e.field != "speaker_name"], "mv")
    assert res.migrated == 1 and res.unmatched == 0
    d = _dialogs(out)[0]
    assert d.status == "translated" and d.translation == "..." + NB
    assert d.original == DOTS + NB and d.namebox_end == ""


# ── review fixes: translation that is only the name code ─────────

@pytest.mark.parametrize("typed", [
    "\\n<Tadashi>",
    "\\N<Tadashi>",
    "  \\n<Tadashi>  ",
    "\n\\n<Tadashi>",
    "\\n<Tadashi>\n\n",
    "\n\\n<Tadashi>\n\n",
])
@pytest.mark.parametrize("lines", [
    ["「ねえ」", NB],
    ["「ねえ」" + NB],
    ["「ねえ」", NB, "", ""],
])
def test_code_only_translation_is_never_doubled(tmp_path, parser, typed,
                                                lines):
    game = _game(tmp_path, [lines])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = typed
    parser.save_project(game, entries)
    out = "\n".join(_map_texts(game))
    assert out.count("<Tadashi>") == 1
    assert "八尋" not in out and "Yahiro" not in out


@pytest.mark.parametrize("typed,expected", [
    ("\\n<Tadashi>", ["\\n<Tadashi>", "", "", ""]),
    ("Hi!\\n<Tadashi>", ["Hi!\\n<Tadashi>", "", "", ""]),
    ("Hi!\n\\n<Tadashi>", ["Hi!", "\\n<Tadashi>", "", ""]),
])
def test_typed_code_shorter_than_the_block_blanks_the_rest(tmp_path, parser,
                                                           typed, expected):
    """The game block has the code's own line plus padding lines: a typed
    translation with fewer lines fills the rest with empty lines (it used
    to crash the export)."""
    game = _game(tmp_path, [["「ねえ」", NB, "", ""]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = typed
    parser.save_project(game, entries)
    assert _map_texts(game) == expected


def test_blank_name_code_in_translation_still_gets_the_real_name(tmp_path,
                                                                 parser):
    game = _game(tmp_path, [["「ねえ」" + NB]])
    entries = parser.load_project(game)
    _translate(entries, {"八尋": "Yahiro"})
    _dialogs(entries)[0].translation = "Hey\\n<  >"
    parser.save_project(game, entries)
    assert _map_texts(game) == ["Hey\\n<  >\\n<Yahiro>"]


# ── review fixes: Word Wrap Space answer remembered per project ──

def test_yep_choice_saved_with_project(tmp_path):
    p = TranslationProject(project_path=str(tmp_path))
    path = str(tmp_path / "s.json")
    p.save_state(path)
    with open(path, encoding="utf-8") as f:
        assert "yep_wrap_space" not in json.load(f)   # old format kept
    assert TranslationProject.load_state(path).yep_wrap_space == ""
    for value in ("on", "off"):
        p.yep_wrap_space = value
        p.save_state(path)
        assert TranslationProject.load_state(path).yep_wrap_space == value


@pytest.mark.parametrize("bad", ["yes", True, 1, None, ["on"]])
def test_yep_choice_bad_value_means_ask(tmp_path, bad):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"entries": [], "yep_wrap_space": bad}),
                    encoding="utf-8")
    assert TranslationProject.load_state(str(path)).yep_wrap_space == ""


def _saved_choice(tmp_path):
    path = tmp_path / "_translation_state.json"
    return TranslationProject.load_state(str(path)).yep_wrap_space


def test_export_prompt_yes_remembered_and_reapplied(window, monkeypatch,
                                                    tmp_path):
    from translator.widgets import main_window as mw
    first = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.Yes)
    assert first["asked"] == ["YEP Word Wrap Space Is Off"]
    assert first["fixed"] == 1
    assert window.project.yep_wrap_space == "on"
    assert _saved_choice(tmp_path) == "on"
    # Second export: plugins.js is rebuilt from the original again, so it
    # is fixed again without asking (a "No" can't silently undo it)
    second = _export_ui(window, monkeypatch, tmp_path,
                        mw.QMessageBox.StandardButton.No, keep_project=True)
    assert second["asked"] == []
    assert second["saved"] == 1 and second["fixed"] == 1
    assert window.project.yep_wrap_space == "on"


def test_export_prompt_no_remembered_not_asked_again(window, monkeypatch,
                                                     tmp_path):
    from translator.widgets import main_window as mw
    first = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.No)
    assert first["asked"] and first["fixed"] == 0
    assert window.project.yep_wrap_space == "off"
    assert _saved_choice(tmp_path) == "off"
    second = _export_ui(window, monkeypatch, tmp_path,
                        mw.QMessageBox.StandardButton.Yes, keep_project=True)
    assert second["asked"] == []
    assert second["saved"] == 1 and second["fixed"] == 0


def test_export_prompt_old_project_without_choice_still_asks(window,
                                                             monkeypatch,
                                                             tmp_path):
    from translator.widgets import main_window as mw
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"project_path": str(tmp_path), "entries": [{
        "id": "a", "file": "Map001.json", "field": "dialog",
        "original": "はい", "translation": "Yes", "status": "translated"}]}),
        encoding="utf-8")
    window.project = TranslationProject.load_state(str(path))
    calls = _export_ui(window, monkeypatch, tmp_path,
                       mw.QMessageBox.StandardButton.Yes, keep_project=True)
    assert calls["asked"] == ["YEP Word Wrap Space Is Off"]
    assert calls["fixed"] == 1


@pytest.mark.parametrize("answer", ["Yes", "No", "Cancel"])
def test_export_not_confirmed_or_cancelled_remembers_nothing(
        window, monkeypatch, tmp_path, answer):
    from translator.widgets import main_window as mw
    calls = _export_ui(window, monkeypatch, tmp_path,
                       getattr(mw.QMessageBox.StandardButton, answer),
                       confirm=False)
    assert calls["saved"] == 0 and calls["fixed"] == 0
    assert window.project.yep_wrap_space == ""
