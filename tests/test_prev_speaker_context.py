"""Who said the earlier lines (lesson from game 2).

Most "you" vs "he" errors depend on who spoke before. The MV/MZ context now
labels each earlier line "[name]: ...", like the VX Ace parser, and a batch
prompt says who says each line.
"""

import json

import pytest
from test_mv_export import _cmd, build_game

from translator.ai_client import AIClient
from translator.project_model import TranslationEntry, TranslationProject
from translator.rpgmaker_mv import RPGMakerMVParser


def _write_map(game, blocks):
    cmds = []
    for lines in blocks:
        cmds.append(_cmd(101, ["", 0, 0, 2]))
        cmds.extend(_cmd(401, [line]) for line in lines)
    cmds.append(_cmd(0, []))
    with open(f"{game}/data/Map001.json", "w", encoding="utf-8") as f:
        json.dump({"displayName": "", "events": [None, {
            "id": 1, "name": "EV001", "pages": [{"list": cmds}]}]},
            f, ensure_ascii=False)


@pytest.fixture
def parsed(tmp_path):
    game = build_game(str(tmp_path / "game"))
    _write_map(game, [
        ["\\n<幸来>「おはよう」"],
        ["静かな朝だった。"],
        ["\\n<守人>「家にいないの？」"],
    ])
    p = RPGMakerMVParser()
    p.game_font = None
    return [e for e in p.load_project(game) if e.field == "dialog"
            and e.file == "Map001.json"]


def test_earlier_lines_are_labelled_with_their_speaker(parsed):
    first, narration, third = parsed
    assert narration.context.startswith("[幸来]: 「おはよう」")
    assert "[Speaker:" not in narration.context      # narration has no speaker
    assert third.context.startswith("[Speaker: 守人]")
    assert "[幸来]: 「おはよう」" in third.context
    # narration lines get no label
    assert "\n静かな朝だった。" in third.context or \
        third.context.endswith("静かな朝だった。")


def test_current_speaker_still_found_first(parsed):
    from translator.speakers import speaker_of
    assert speaker_of(parsed[2].context) == "守人"
    assert speaker_of(parsed[1].context) == ""        # label isn't [Speaker:]


# ── renaming labels to English ─────────────────────────────────────

@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from qt_helpers import dispose_main_window
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    w = mw.MainWindow()
    w._closing = True
    yield w
    dispose_main_window(w)


def test_update_speaker_names_renames_labels(win):
    e = TranslationEntry("Map001.json/x/dialog_3", "Map001.json", "dialog",
                         "「何？」", context=(
                             "[Speaker: 守人]\n[幸来]: 「おはよう」\n---\n"
                             "静かだ。\n---\n[村人]: 「やあ」"))
    names = [TranslationEntry(f"Map001.json/speaker/{jp}", "Map001.json",
                              "speaker_name", jp, en, "translated")
             for jp, en in (("守人", "Mamoru"), ("幸来", "Yukiko"))]
    win.project = TranslationProject(entries=[e] + names)
    win._update_speaker_names([], {})
    assert e.context == ("[Speaker: Mamoru]\n[Yukiko]: 「おはよう」\n---\n"
                         "静かだ。\n---\n[村人]: 「やあ」")


# ── batch: who says each line ──────────────────────────────────────

def test_batch_prompt_says_who_says_each_line(fake_llm):
    client = AIClient()
    client.glossary = {"幸来": "Yukiko"}
    client.translate_batch([
        ("Line1", "「おはよう」", "[Speaker: 幸来]", "dialog"),
        ("Line2", "静かな朝だった。", "", "dialog"),
        ("Line3", "「家にいないの？」", "[Speaker: 守人]", "dialog"),
        ("Line4", "「誰？」", "", "dialog"),
    ])
    sent = json.dumps(fake_llm.chat_calls()[-1]["messages"], ensure_ascii=False)
    assert ("Who says each line: Line1 = Yukiko; Line2 = narration; "
            "Line3 = 守人; Line4 = unnamed speaker") in sent


def test_batch_with_one_speaker_skips_the_list(fake_llm):
    client = AIClient()
    client.translate_batch([
        ("Line1", "「おはよう」", "[Speaker: 幸来]", "dialog"),
        ("Line2", "「行こう」", "[Speaker: 幸来]", "dialog"),
    ])
    sent = json.dumps(fake_llm.chat_calls()[-1]["messages"], ensure_ascii=False)
    assert "Who says each line" not in sent
