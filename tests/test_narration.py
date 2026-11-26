"""First-person narrator per scene (lessons from "My Happiness 2").

Most scenes are told by Mamoru (僕), some by Yukiko (私). Without a narrator
hint the model wrote the protagonist's own thoughts as "he".
"""

import json

import pytest
from qt_helpers import dispose

from translator import narration as N
from translator.ai_client import AIClient
from translator.project_model import TranslationEntry, TranslationProject

L = chr(0x300C)
R = chr(0x300D)


def _say(eid, who, text):
    return TranslationEntry(eid, "Map001.json", "dialog", text, "", "untranslated",
                            f"[Speaker: {who}]\nprev")


def _narr(eid, text, ctx="prev"):
    return TranslationEntry(eid, "Map001.json", "dialog", text, "", "untranslated", ctx)


def _cast():
    """Dialogue that teaches each narrator's word: 守人 says 僕, 幸来 says 私."""
    return ([_say(f"Map001.json/E0/p0/s{i}", "守人", f"{L}僕は行くよ{i}{R}")
             for i in range(4)]
            + [_say(f"Map001.json/E0/p0/y{i}", "幸来", f"{L}私も行く{i}{R}")
               for i in range(4)])


# ── word counting ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, word, n", [
    ("僕は僕だ", "僕", 2),
    ("私は私服を着た", "私", 1),         # 私服 = casual clothes, not "I"
    ("私立の学校", "私", 0),
    ("壁をこわした", "わし", 0),          # kana glued to kana before it
    ("わしの店だ", "わし", 1),
    ("「あたしね」", "あたし", 1),
    ("", "僕", 0),
])
def test_count_word(text, word, n):
    assert N.count_word(text, word) == n


def test_first_person_word_needs_enough_uses():
    assert N.first_person_word(["僕は", "僕が", "僕の"]) == "僕"
    assert N.first_person_word(["僕は", "僕が"]) == ""
    assert N.first_person_word(["私は"] * 3 + ["僕"] * 4) == "僕"


# ── which lines are narration ──────────────────────────────────────

def test_is_narration():
    assert N.is_narration(_narr("a/1", "静かな朝だった。"))
    assert not N.is_narration(_say("a/2", "幸来", "静かね"))
    assert not N.is_narration(_narr("a/3", f"{L}おはよう{R}"))
    assert not N.is_narration(_narr("a/4", chr(0xFF08) + "心の声" + chr(0xFF09)))
    assert not N.is_narration(_narr("a/5", "   "))
    e = _narr("a/6", "静かだ")
    e.namebox = "\\n<幸来>"
    assert not N.is_narration(e)
    e = _narr("a/7", "はい")
    e.field = "choice"
    assert not N.is_narration(e)


# ── assigning narrators ────────────────────────────────────────────

def test_narrator_words_and_shared_word_dropped():
    es = _cast() + [_say(f"x/{i}", "店長", f"{L}僕です{i}{R}") for i in range(5)]
    assert N.narrator_words(es, ["守人", "幸来"]) == {"守人": "僕", "幸来": "私"}
    # 店長 uses 僕 like 守人: can't be told apart, so only the first keeps it
    assert N.narrator_words(es, ["守人", "店長"]) == {"守人": "僕"}


def test_scene_follows_clues_in_order():
    es = _cast() + [
        # Scene A: no clue at first, then 私, then a line without a clue
        _narr("Map002.json/E1/p0/d0", "朝になった。"),
        _narr("Map002.json/E1/p0/d1", "私は目を覚ました。"),
        _narr("Map002.json/E1/p0/d2", "外は雨だった。"),
        _narr("Map002.json/E1/p0/d3", "その頃、僕は会社にいた。"),
        _narr("Map002.json/E1/p0/d4", "仕事が終わらない。"),
        # Scene B: no clue at all -> main narrator
        _narr("Map003.json/E1/p0/d0", "夜になった。"),
    ]
    out = N.assign_narrators(es, ["守人", "幸来"])
    assert out["Map002.json/E1/p0/d0"] == "幸来"   # before first clue: first clue
    assert out["Map002.json/E1/p0/d1"] == "幸来"
    assert out["Map002.json/E1/p0/d2"] == "幸来"
    assert out["Map002.json/E1/p0/d3"] == "守人"   # switch
    assert out["Map002.json/E1/p0/d4"] == "守人"
    assert out["Map003.json/E1/p0/d0"] == "守人"
    assert not any(k.startswith("Map001.json/E0") for k in out)   # speech


def test_line_with_both_words_follows_the_run():
    es = _cast() + [
        _narr("S/E/p/0", "私は考えた。"),
        _narr("S/E/p/1", "僕と私の話だ。"),
    ]
    assert N.assign_narrators(es, ["守人", "幸来"])["S/E/p/1"] == "幸来"


def test_no_narrators_means_no_assignment():
    assert N.assign_narrators(_cast() + [_narr("S/E/p/0", "朝だ")], []) == {}
    assert N.assign_narrators(_cast(), ["", None and "x"]) == {}


def test_single_narrator_covers_everything():
    es = _cast() + [_narr("S/E/p/0", "私は考えた。"), _narr("S/E/p/1", "朝だ")]
    out = N.assign_narrators(es, ["守人"])
    assert set(out.values()) == {"守人"}


# ── suggestions ────────────────────────────────────────────────────

def test_suggest_narrators_ranks_by_narration_use():
    es = _cast() + [_narr(f"S/E/p/{i}", "僕は思った。") for i in range(20)] \
        + [_narr(f"T/E/p/{i}", "私は思った。") for i in range(5)]
    picks, why = N.suggest_narrators(es, {"守人", "幸来"})
    assert picks == ["守人", "幸来"]
    assert "僕 ×20" in why and "私 ×5" in why


def test_suggest_narrators_drops_rare_second():
    es = _cast() + [_narr(f"S/E/p/{i}", "僕は思った。") for i in range(40)] \
        + [_narr("T/E/p/0", "私は思った。")]
    assert N.suggest_narrators(es, {"守人", "幸来"})[0] == ["守人"]


def test_suggest_narrators_without_first_person_narration():
    picks, why = N.suggest_narrators(_cast() + [_narr("S/E/p/0", "朝だ")],
                                     {"守人", "幸来"})
    assert picks == [] and "no first-person" in why


def test_clean_narrators():
    assert N.clean_narrators(["守人", "守人", " 幸来 ", "", 3, "x"]) == ["守人", "幸来"]
    assert N.clean_narrators("守人") == []
    assert N.clean_narrators(None) == []


def test_narration_pov_keys_by_original_and_context():
    es = _cast() + [_narr("S/E/p/0", "私は考えた。", ctx="")]
    assert N.narration_pov(es, ["守人", "幸来"]) == {("私は考えた。", ""): "幸来"}


# ── AIClient ───────────────────────────────────────────────────────

@pytest.fixture
def client():
    c = AIClient()
    c.speaker_genders = {"幸来": "female", "守人": "male"}
    c.glossary = {"幸来": "Yukiko", "守人": "Mamoru"}
    c.narration_pov = {("私は考えた。", "prev"): "幸来",
                       ("朝だ。", ""): "守人"}
    return c


def test_narrator_hint(client):
    hint = client._build_narrator_hint("私は考えた。", "prev")
    assert "Yukiko's own first-person narration" in hint
    assert "Yukiko is FEMALE" in hint
    assert "never as he/she" in hint
    assert client._build_narrator_hint("other", "prev") == ""


def test_speech_keeps_speaker_hint_not_narrator(client):
    client.narration_pov[("x", "[Speaker: 守人]")] = "幸来"
    hint = client._voice_hint("x", "[Speaker: 守人]")
    assert "Speaker: Mamoru is MALE" in hint and "narration" not in hint


def test_single_prompt_carries_narrator_hint(client, fake_llm):
    fake_llm.translate_fn = lambda _s: "Morning."
    client.translate("朝だ。", context="", field="dialog")
    user = fake_llm.user_messages()[-1]
    assert "Mamoru's own first-person narration" in user


def test_batch_prompt_labels_narrator_hint(client, fake_llm):
    client.translate_batch([
        ("Line1", "私は考えた。", "prev", "dialog"),
        ("Line2", "知らない行", "prev", "dialog"),
    ])
    sent = json.dumps(fake_llm.chat_calls()[-1]["messages"], ensure_ascii=False)
    assert "Line1: Narration: this line is Yukiko" in sent


# ── project ────────────────────────────────────────────────────────

def test_project_roundtrip_and_old_files(tmp_path):
    p = TranslationProject(entries=[_narr("S/E/p/0", "朝だ")])
    path = str(tmp_path / "s.json")
    p.save_state(path)
    assert "narrators" not in json.load(open(path, encoding="utf-8"))
    assert TranslationProject.load_state(path).narrators == []
    p.narrators = ["守人", "幸来"]
    p.save_state(path)
    assert TranslationProject.load_state(path).narrators == ["守人", "幸来"]
    zpath = str(tmp_path / "p.zip")
    p.entries[0].translation, p.entries[0].status = "Morning", "translated"
    p.export_patch(zpath, game_title="t")
    assert TranslationProject.import_patch(zpath).narrators == ["守人", "幸来"]


# ── dialog ─────────────────────────────────────────────────────────

def test_dialog_narrator_controls(qapp):
    from translator.widgets.speaker_gender_dialog import SpeakerGenderDialog
    es = _cast() + [_narr(f"S/E/p/{i}", "僕は思った。") for i in range(20)] \
        + [_narr(f"T/E/p/{i}", "私は思った。") for i in range(5)]
    dlg = SpeakerGenderDialog(es, narrators=["幸来"])
    try:
        assert dlg.get_narrators() == ["幸来"]
        assert "Suggested: 守人, then 幸来" in dlg.narrator_hint_label.text()
        dlg.use_suggested_narrators()
        assert dlg.get_narrators() == ["守人", "幸来"]
        dlg.narrator_combos[1].setCurrentText("守人")      # duplicate
        assert dlg.get_narrators() == ["守人"]
    finally:
        dispose(dlg)


def test_dialog_without_suggestion_disables_button(qapp):
    from translator.widgets.speaker_gender_dialog import SpeakerGenderDialog
    dlg = SpeakerGenderDialog(_cast())
    try:
        assert not dlg.use_narrators_btn.isEnabled()
        assert dlg.get_narrators() == []
    finally:
        dispose(dlg)


# ── main window ────────────────────────────────────────────────────

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
    w.project = TranslationProject(project_path="", entries=_cast() + [
        _narr("S/E/p/0", "私は考えた。"), _narr("S/E/p/1", "朝だ")])
    w._actors_ready = True
    yield w
    dispose_main_window(w)


def test_refresh_uses_renamed_speaker_tags(win):
    # Narrators saved in Japanese; speaker tags renamed to English since.
    for e in win.project.entries:
        e.context = e.context.replace("守人", "Mamoru").replace("幸来", "Yukiko")
    win.client.glossary = {"守人": "Mamoru", "幸来": "Yukiko"}
    win.project.narrators = ["守人", "幸来"]
    win._refresh_narration_pov()
    assert win.client.narration_pov[("私は考えた。", "prev")] == "Yukiko"
    assert win.client.narration_pov[("朝だ", "prev")] == "Yukiko"


def test_dialog_apply_saves_narrators(win, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    win._last_save_path = str(tmp_path / "state.json")

    class FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        def get_genders(self):
            return {}

        def get_narrators(self):
            return ["守人", "幸来"]
    monkeypatch.setattr(mw, "SpeakerGenderDialog", FakeDialog)
    win._open_speaker_genders()
    assert win.project.narrators == ["守人", "幸来"]
    assert win.client.narration_pov[("私は考えた。", "prev")] == "幸来"
    assert TranslationProject.load_state(win._last_save_path).narrators == ["守人", "幸来"]


def test_offer_mentions_narrator_when_unset(win, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from translator.widgets import main_window as mw
    win.project.entries += [_narr(f"N/E/p/{i}", "僕は思った。") for i in range(5)]
    asked = []
    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    win._ensure_actors_ready()
    assert len(asked) == 1 and "suggested narrator: 守人" in asked[0]


def test_reset_clears_narration_pov(win):
    win.client.narration_pov = {("a", ""): "x"}
    win._reset_project_runtime_state()
    assert win.client.narration_pov == {}
