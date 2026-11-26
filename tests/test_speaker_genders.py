"""Speaker genders for [Speaker: X] names that aren't actors.

In "My Happiness 2" none of the four main characters were actors (the only
actor had costume tags like 守人 私服), so no gender hint ever reached the
model and lines came out as "Is he not home?" about the heroine.
"""

import json
import os
import zipfile

import pytest
from qt_helpers import dispose

from translator import speakers as S
from translator.ai_client import AIClient
from translator.project_model import TranslationEntry, TranslationProject

FW_SPACE = chr(0x3000)


def _e(original, speaker="", translation="", eid=None, status="translated"):
    ctx = f"[Speaker: {speaker}]\nprev" if speaker else "prev"
    return TranslationEntry(eid or f"Map001.json/{abs(hash((original, speaker)))}",
                            "Map001.json", "dialog", original, translation,
                            status, ctx)


# ── speakers.py basics ─────────────────────────────────────────────

def test_speaker_of_and_collect():
    es = [_e("あ", "幸来"), _e("い", "幸来"), _e("う", "守人"), _e("え")]
    assert S.speaker_of("[Speaker: 幸来]\nx") == "幸来"
    assert S.speaker_of("") == "" and S.speaker_of("no tag") == ""
    assert S.collect_speakers(es) == {"幸来": 2, "守人": 1}


@pytest.mark.parametrize("tag, base", [
    ("守人 私服", "守人"),
    ("守人" + FW_SPACE + "私服", "守人"),
    ("守人", ""),
    ("Male Clerk 1", "Male"),     # harmless: only used as a fallback alias
    ("", ""),
])
def test_base_name(tag, base):
    assert S.base_name(tag) == base


def test_clean_speaker_genders_drops_bad_rows():
    raw = {"幸来": "female", "守人": "male", "x": "other", 3: "male",
           " ": "male", "y": None}
    assert S.clean_speaker_genders(raw) == {"幸来": "female", "守人": "male"}
    assert S.clean_speaker_genders(["not", "a", "dict"]) == {}
    assert S.clean_speaker_genders(None) == {}


# ── lookup ─────────────────────────────────────────────────────────

GLOSSARY = {"幸来": "Yukiko", "守人": "Mamoru"}


@pytest.mark.parametrize("speaker, expected", [
    ("幸来", "female"),
    ("Yukiko", "female"),          # context renamed to English, key is JP
    ("yukiko", "female"),          # case-insensitive
    ("守人 私服", "male"),          # costume tag -> base name
    ("Mamoru", "male"),
    ("築本", ""),                   # not set
    ("", ""),
])
def test_lookup_speaker_gender(speaker, expected):
    genders = {"幸来": "female", "守人": "male", "築本": "unknown"}
    assert S.lookup_speaker_gender(speaker, genders, GLOSSARY) == expected


def test_lookup_english_key_matches_japanese_speaker():
    assert S.lookup_speaker_gender("幸来", {"Yukiko": "female"}, GLOSSARY) == "female"


def test_lookup_without_map_or_glossary():
    assert S.lookup_speaker_gender("幸来", {}, GLOSSARY) == ""
    assert S.lookup_speaker_gender("幸来", {"幸来": "female"}, None) == "female"


# ── prefill from actors ────────────────────────────────────────────

def test_prefill_uses_actor_names_base_names_and_keeps_existing():
    out = S.prefill_speaker_genders(
        ["守人 私服", "守人", "幸来", "築本"],
        actor_names={1: "守人", 2: "築本"},
        actor_genders={1: "male", 2: "unknown"},
        existing={"幸来": "female"})
    assert out == {"守人 私服": "male", "守人": "male", "幸来": "female",
                   "築本": "unknown"}


def test_prefill_existing_choice_beats_actor():
    out = S.prefill_speaker_genders(["守人"], {1: "守人"}, {1: "male"},
                                    existing={"守人": "unknown"})
    assert out == {"守人": "unknown"}


# ── suggestions ────────────────────────────────────────────────────

@pytest.mark.parametrize("speaker, lines, expected", [
    ("守人", ["僕は帰るよ。", "僕だって…", "僕が悪かった"], "male"),
    ("枚方", ["俺のせいだ！", "俺は行くぜ。"], "male"),
    ("築本", ["わしに任せろ。", "わしの店だ"], "male"),
    ("幸来", ["もう知らないわよ！", "そうかしら？", "いいのよ。"], "female"),
    ("由美子", [], "female"),           # name ending 子 (weight 2)
    ("源治", [], "male"),               # name ending 治
    ("女性店員", ["いらっしゃいませ"], "female"),
    ("男性店員", ["いらっしゃいませ"], "male"),
    ("Female Staff", ["いらっしゃいませ"], "female"),   # not "male"
    ("Mysterious Woman", [], "female"),                 # not "man"
    ("Male Clerk 2", [], "male"),
    ("Fortune Teller", ["占いですね"], ""),             # no cues
    ("???", ["私は…わよ"], ""),                          # one weak cue
    ("Yukiko & Hirakata", [], ""),
])
def test_suggest_gender(speaker, lines, expected):
    g, reasons = S.suggest_gender(speaker, lines)
    assert g == expected
    if g:
        assert reasons


def test_suggest_gender_mixed_cues_says_nothing_but_explains():
    g, reasons = S.suggest_gender("X", ["僕は", "僕が", "わよ", "かしら？"])
    assert g == ""
    assert reasons and reasons[0].startswith("mixed cues")


def test_suggest_gender_watashi_is_neutral():
    assert S.suggest_gender("X", ["私は" * 20])[0] == ""


# ── samples ────────────────────────────────────────────────────────

def test_sample_lines_skips_short_and_duplicates_and_spreads():
    es = [_e("あっ", "A")] + [_e(f"これは{i}番目の台詞です", "A") for i in range(40)]
    es.append(_e("これは0番目の台詞です", "A"))
    picked = S.sample_lines(es, limit=8)
    assert len(picked) == 8
    texts = [e.original for e in picked]
    assert "あっ" not in texts
    assert len(set(texts)) == 8
    assert texts[0] == "これは0番目の台詞です" and texts[-1] != "これは7番目の台詞です"


# ── AIClient speaker hint ──────────────────────────────────────────

@pytest.fixture
def client():
    return AIClient()


def test_hint_for_non_actor_speaker(client):
    client.speaker_genders = {"幸来": "female"}
    client.glossary = dict(GLOSSARY)
    hint = client._build_speaker_hint("[Speaker: 幸来]\nprev")
    assert "Yukiko is FEMALE" in hint


def test_hint_for_renamed_english_context(client):
    client.speaker_genders = {"幸来": "female"}
    client.glossary = dict(GLOSSARY)
    assert "FEMALE" in client._build_speaker_hint("[Speaker: Yukiko]")


def test_hint_for_actor_with_costume_tag(client):
    client.actor_names = {1: "守人"}
    client.actor_genders = {1: "male"}
    assert "MALE" in client._build_speaker_hint("[Speaker: 守人 私服]")


def test_actor_with_unknown_gender_falls_back_to_speaker_map(client):
    client.actor_names = {1: "守人"}
    client.actor_genders = {1: "unknown"}
    client.speaker_genders = {"守人": "male"}
    assert "is MALE" in client._build_speaker_hint("[Speaker: 守人]")


def test_actor_gender_wins_over_speaker_map(client):
    client.actor_names = {1: "守人"}
    client.actor_genders = {1: "male"}
    client.speaker_genders = {"守人": "female"}
    assert "is MALE" in client._build_speaker_hint("[Speaker: 守人]")


def test_no_hint_without_data(client):
    assert client._build_speaker_hint("[Speaker: 幸来]") == ""
    client.speaker_genders = {"幸来": "unknown"}
    assert client._build_speaker_hint("[Speaker: 幸来]") == ""
    assert client._build_speaker_hint("") == ""


def test_batch_prompt_carries_speaker_hint(fake_llm):
    client = AIClient()
    client.speaker_genders = {"幸来": "female"}
    client.translate_batch([("Line1", "「家にいないの？」", "[Speaker: 幸来]", "dialog")])
    sent = json.dumps(fake_llm.chat_calls()[-1]["messages"], ensure_ascii=False)
    assert "幸来 is FEMALE" in sent


# ── project save / load / patch ────────────────────────────────────

def test_state_roundtrip(tmp_path):
    p = TranslationProject(entries=[_e("あいう", "幸来")])
    p.speaker_genders = {"幸来": "female", "守人": "male"}
    path = str(tmp_path / "state.json")
    p.save_state(path)
    assert TranslationProject.load_state(path).speaker_genders == p.speaker_genders


def test_old_state_without_field_loads_empty(tmp_path):
    p = TranslationProject(entries=[_e("あいう", "幸来")])
    path = str(tmp_path / "state.json")
    p.save_state(path)
    data = json.load(open(path, encoding="utf-8"))
    assert "speaker_genders" not in data          # nothing written when empty
    assert TranslationProject.load_state(path).speaker_genders == {}


def test_hand_edited_bad_values_are_dropped(tmp_path):
    path = tmp_path / "state.json"
    p = TranslationProject(entries=[_e("あいう", "幸来")])
    p.save_state(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["speaker_genders"] = {"幸来": "girl", "守人": "male"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert TranslationProject.load_state(str(path)).speaker_genders == {"守人": "male"}


def test_patch_roundtrip(tmp_path):
    p = TranslationProject(entries=[_e("あいう", "幸来", "Hi")])
    p.speaker_genders = {"幸来": "female"}
    zpath = str(tmp_path / "patch.zip")
    p.export_patch(zpath, game_title="t")
    with zipfile.ZipFile(zpath) as zf:
        assert any(os.path.basename(n) == "patch.json" for n in zf.namelist())
    assert TranslationProject.import_patch(zpath).speaker_genders == {"幸来": "female"}


# ── dialog ─────────────────────────────────────────────────────────

@pytest.fixture
def dialog(qapp):
    from translator.widgets.speaker_gender_dialog import SpeakerGenderDialog
    es = ([_e("僕は帰るよ。", "守人 私服", eid=f"a{i}") for i in range(3)]
          + [_e(f"もう知らないわよ！{i}", "幸来", eid=f"b{i}") for i in range(5)]
          + [_e("<b>占い</b>ですね & more", "Fortune Teller", "Fortune <i>",
                eid="c0")])
    dlg = SpeakerGenderDialog(es, existing={"幸来": "unknown"},
                              actor_names={1: "守人"},
                              actor_genders={1: "male"},
                              glossary={"幸来": "Yukiko"})
    yield dlg
    dispose(dlg)


def test_dialog_lists_speakers_by_line_count_with_prefill(dialog):
    assert dialog._speakers == ["幸来", "守人 私服", "Fortune Teller"]
    assert dialog.get_genders() == {"幸来": "unknown", "守人 私服": "male",
                                    "Fortune Teller": "unknown"}
    assert "Yukiko" in dialog.table.item(0, 0).text()
    assert dialog.table.item(0, 2).text().startswith("female")


def test_use_suggestions_only_fills_unknown(dialog):
    dialog._combos[1].setCurrentText("female")   # user override on 守人 私服
    changed = dialog.use_suggestions()
    assert changed == 1                           # only 幸来; teller has none
    assert dialog.get_genders() == {"幸来": "female", "守人 私服": "female",
                                    "Fortune Teller": "unknown"}


def test_samples_escape_html(dialog):
    dialog._show_samples(2)
    html_out = dialog.samples.toHtml()
    assert "&lt;b&gt;" in html_out and "&lt;i&gt;" in html_out
    assert "&amp; more" in html_out


def test_dialog_with_no_speakers(qapp):
    from translator.widgets.speaker_gender_dialog import SpeakerGenderDialog
    dlg = SpeakerGenderDialog([_e("地の文")])
    assert dlg.get_genders() == {}
    assert "No speakers" in dlg.samples.toPlainText()
    dispose(dlg)


# ── main window: offer, save, import ───────────────────────────────

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
    w.project = TranslationProject(
        project_path="",
        entries=([_e(f"台詞{i}です", "幸来", eid=f"y{i}") for i in range(12)]
                 + [_e(f"台詞{i}です", "店員", eid=f"s{i}") for i in range(3)]))
    w._actors_ready = True
    yield w
    dispose_main_window(w)


def _count_questions(monkeypatch, answer):
    from translator.widgets import main_window as mw
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return answer
    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(question))
    return asked


def test_offer_lists_frequent_speakers_once(win, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    asked = _count_questions(monkeypatch, QMessageBox.StandardButton.No)
    opened = []
    monkeypatch.setattr(win, "_open_speaker_genders", lambda: opened.append(1))
    assert win._ensure_actors_ready() is True
    assert win._ensure_actors_ready() is True
    assert len(asked) == 1
    assert "幸来 (12 lines)" in asked[0] and "店員" not in asked[0]
    assert opened == []


def test_offer_yes_opens_dialog(win, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    _count_questions(monkeypatch, QMessageBox.StandardButton.Yes)
    opened = []
    monkeypatch.setattr(win, "_open_speaker_genders", lambda: opened.append(1))
    win._ensure_actors_ready()
    assert opened == [1]


def test_no_offer_when_genders_already_set_or_hinted(win, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    asked = _count_questions(monkeypatch, QMessageBox.StandardButton.No)
    win.project.speaker_genders = {"店員": "female"}
    win._ensure_actors_ready()
    assert asked == []
    # A speaker covered by an actor gets a hint, so nothing is missing
    win.project.speaker_genders = {}
    win._speaker_offer_done = False
    win.client.actor_names = {1: "幸来"}
    win.client.actor_genders = {1: "female"}
    win._ensure_actors_ready()
    assert asked == []


def test_open_dialog_saves_choices_and_drops_unknown(win, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    win.project.speaker_genders = {"Old Name": "male"}
    win._last_save_path = str(tmp_path / "state.json")

    class FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        def get_genders(self):
            return {"幸来": "female", "店員": "unknown"}
    monkeypatch.setattr(mw, "SpeakerGenderDialog", FakeDialog)
    win._open_speaker_genders()
    assert win.project.speaker_genders == {"Old Name": "male", "幸来": "female"}
    assert win.client.speaker_genders == win.project.speaker_genders
    saved = TranslationProject.load_state(win._last_save_path)
    assert saved.speaker_genders == win.project.speaker_genders


def test_cancelled_dialog_changes_nothing(win, monkeypatch):
    from translator.widgets import main_window as mw

    class FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return False
    monkeypatch.setattr(mw, "SpeakerGenderDialog", FakeDialog)
    win.project.speaker_genders = {"幸来": "male"}
    win._open_speaker_genders()
    assert win.project.speaker_genders == {"幸来": "male"}


def test_menu_action_enabled_with_project(win):
    win._enable_project_actions()
    assert win.speaker_genders_action.isEnabled()
