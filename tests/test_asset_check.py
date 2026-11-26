"""Post-export asset check: names in the exported data must still exist.

Real case (My Happiness ≠ Her Happiness 2): picture-choice names were
translated and the game stopped with "Failed to load: img/choices/C_Yes.png".
The original game also pointed at one missing picture (UI_Yukiko01) in an
unused event; that must not be blamed on the export.
"""

import json
import os

from translator.asset_check import check_export, export_note, find_missing


def _cmd(code, params):
    return {"code": code, "indent": 0, "parameters": params}


def _map(cmds):
    return {"events": [None, {"id": 1, "pages": [{"list": cmds + [_cmd(0, [])]}]}]}


ORIGINAL = [
    _cmd(231, [1, "立ち絵", 0, 0, 0, 0, 100, 100, 255, 0]),
    _cmd(241, [{"name": "朝の曲", "volume": 90, "pitch": 100, "pan": 0}]),
    _cmd(102, [["C_はい|350|200", "いいえ", "A|B"], -1, -1, 2, 0]),
    _cmd(231, [2, "UI_old", 0, 0, 0, 0, 100, 100, 255, 0]),   # dead in the original
]


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _touch(root, rel):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").close()


def _game(tmp_path, live_cmds, with_backup=True):
    root = str(tmp_path / "www")
    _write(os.path.join(root, "data", "Map001.json"), _map(live_cmds))
    if with_backup:
        _write(os.path.join(root, "data_original", "Map001.json"), _map(ORIGINAL))
    _touch(root, "img/pictures/立ち絵.rpgmvp")        # encrypted copy counts
    _touch(root, "audio/bgm/朝の曲.ogg")
    _touch(root, "img/choices/C_はい.png")
    return root


def test_untouched_export_reports_nothing_new(tmp_path):
    root = _game(tmp_path, ORIGINAL)
    new, pre = check_export(root)
    assert new == []
    assert pre == 1                      # UI_old was already missing
    assert export_note(root) == ""


def test_translated_names_are_reported(tmp_path):
    live = json.loads(json.dumps(ORIGINAL))
    live[0]["parameters"][1] = "Standing Picture"
    live[2]["parameters"][0][0] = "C_Yes|350|200"
    root = _game(tmp_path, live)
    new, pre = check_export(root)
    assert {(m.folder, m.name) for m in new} == {
        ("img/pictures", "Standing Picture"), ("img", "C_Yes")}
    assert pre == 1
    note = export_note(root)
    assert "C_Yes" in note and "Standing Picture" in note and "Failed to load" in note


def test_translated_audio_name_is_reported(tmp_path):
    live = json.loads(json.dumps(ORIGINAL))
    live[1]["parameters"][0]["name"] = "Morning Song"
    new, _ = check_export(_game(tmp_path, live))
    assert [(m.folder, m.name) for m in new] == [("audio/bgm", "Morning Song")]


def test_without_backup_everything_missing_counts(tmp_path):
    root = _game(tmp_path, ORIGINAL, with_backup=False)
    new, pre = check_export(root)
    assert [(m.folder, m.name) for m in new] == [("img/pictures", "UI_old")]
    assert pre == 0


def test_text_choices_with_a_bar_are_not_pictures(tmp_path):
    root = _game(tmp_path, ORIGINAL)
    names = {m.name for m in find_missing(os.path.join(root, "data"), root)}
    assert "A" not in names and "いいえ" not in names


def test_unreadable_data_file_is_skipped_not_fatal(tmp_path):
    root = _game(tmp_path, ORIGINAL)
    with open(os.path.join(root, "data", "Map002.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    new, pre = check_export(root)
    assert new == [] and pre == 1


def test_long_lists_are_capped_in_the_note(tmp_path):
    live = [_cmd(231, [i, f"pic{i}", 0, 0, 0, 0, 100, 100, 255, 0]) for i in range(15)]
    root = _game(tmp_path, live, with_backup=False)
    note = export_note(root, limit=10)
    assert "…and 5 more" in note
