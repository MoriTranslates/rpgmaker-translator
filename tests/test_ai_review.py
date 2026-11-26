"""AI review pass (lessons from game 2: ~1,100 fixes from a scene-by-scene
review; two fixes once landed on the wrong lines because ids were guessed).

Every proposal is validated against the line it was written for, and only
ticked fixes are applied.
"""

import json

import pytest
from qt_helpers import dispose

from translator import review as R
from translator.project_model import TranslationEntry

L, Rq = chr(0x300C), chr(0x300D)


def _e(eid, original, translation, *, speaker="", status="translated",
       field="dialog"):
    ctx = f"[Speaker: {speaker}]\nprev" if speaker else "prev"
    return TranslationEntry(eid, "Map001.json", field, original, translation,
                            status, ctx)


def _scene(n, prefix="Map001.json/E1/p0", start=0):
    return [_e(f"{prefix}/dialog_{i}", f"台詞{i}です", f"Line {i}.")
            for i in range(start, start + n)]


# ── chunks ─────────────────────────────────────────────────────────

def test_chunks_pack_whole_scenes_and_split_big_ones():
    es = (_scene(30, "A/E1/p0") + _scene(30, "B/E1/p0", 30)
          + _scene(10, "C/E1/p0", 60) + _scene(130, "D/E1/p0", 70))
    chunks = R.build_chunks(es, max_lines=60)
    sizes = [len(c.lines) for c in chunks]
    assert sizes == [60, 10, 60, 60, 10]
    assert chunks[0].lines[0][0] == "L1" and chunks[0].lines[-1][0] == "L60"


def test_chunks_skip_unreviewable_and_dedupe():
    es = [_e("a/1", "同じ", "Same."), _e("b/1", "同じ", "Same."),
          _e("a/2", "済み", "Done.", status="reviewed"),
          _e("a/3", "未", "", status="untranslated"),
          _e("a/4", "名前", "Name", field="name"),
          _e("a/5", "はい", "Yes", field="choice")]
    ids = [e.id for c in R.build_chunks(es) for _, e in c.lines]
    assert ids == ["a/1", "a/5"]


def test_chunks_only_ids():
    es = _scene(5)
    chunks = R.build_chunks(es, only_ids={es[1].id, es[3].id})
    assert [e.id for _, e in chunks[0].lines] == [es[1].id, es[3].id]
    assert R.build_chunks(es, only_ids=set()) == []


# ── messages ───────────────────────────────────────────────────────

def test_character_sheet_and_messages():
    sheet = R.character_sheet({"幸来": "female", "守人": "male"},
                              {"幸来": "Yukiko", "守人": "Mamoru", "勇君": "Yuu-kun",
                               "剣": "sword"}, ["Mamoru", "Yukiko"])
    assert "幸来 = Yukiko: female" in sheet and "守人 = Mamoru: male" in sheet
    assert "勇君 = Yuu-kun" in sheet and "sword" not in sheet
    assert "first-person voice of Mamoru, or of Yukiko" in sheet
    chunk = R._chunk([_e("x/1", f"{L}家にいないの？{Rq}", '"Is he not home?"',
                         speaker="守人"),
                      _e("x/2", "雨だった。", "It rained.")])
    msgs = R.build_messages(chunk, sheet)
    user = msgs[1]["content"]
    assert "L1 [守人]\nJP: 「家にいないの？」\nEN: \"Is he not home?\"" in user
    assert "L2 [narration]" in user
    assert "Reply with ONLY a JSON object" in msgs[0]["content"]


# ── parsing ────────────────────────────────────────────────────────

def test_parse_review_shapes():
    chunk = R._chunk(_scene(3))
    raw = "```json\n" + json.dumps({
        "L1": {"fix": "Fixed 0.", "why": "negation"},
        "L2": "Fixed 1.",
        "L9": {"fix": "x", "why": "y"},
        "L3": 5}) + "\n```"
    props = R.parse_review(raw, chunk)
    by_key = {p.key: p for p in props}
    assert by_key["L1"].new == "Fixed 0." and by_key["L1"].why == "negation"
    assert by_key["L1"].entry_id == chunk.lines[0][1].id
    assert by_key["L1"].old == "Line 0."
    assert by_key["L2"].new == "Fixed 1."
    assert by_key["L9"].problem.startswith("unknown line key")
    assert "L3" not in by_key
    assert R.parse_review("{}", chunk) == []
    with pytest.raises(ValueError):
        R.parse_review("no json here", chunk)


# ── validation ─────────────────────────────────────────────────────

def _prop(entry, new, **kw):
    p = R.Proposal("L1", entry.id, entry.original, entry.translation, new, "why")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


@pytest.mark.parametrize("old, new, problem", [
    ('"Is he not home?"', '"Aren\'t you home?"', ""),
    ('"Is he not home?"', "Aren't you home?", "quote marks"),
    ("\\C[2]Hi\\C[0] there.", "\\C[2]Hello\\C[0] there.", ""),
    ("\\C[2]Hi\\C[0] there.", "Hello there.", "control codes"),
    ("Hi.", "Hi 幸来.", "Japanese"),
    ("Hi.", "Hi" + chr(0x2028) + ".", "invisible"),
    ("Hi.", "Hi.", "no change"),
    ("Hi.", "  ", "empty"),
    ("\nHi.", "Hello.", "leading blank"),
    ("Hi.", "1\n2\n3\n4\n5", "more than 4 lines"),
    ("<WordWrap>Hi.", "<WordWrap>1\n2\n3\n4\n5", ""),
])
def test_validate_rules(old, new, problem):
    e = _e("x/1", "台詞", old)
    got = R.validate(_prop(e, new), e)
    assert (problem in got) if problem else got == ""


def test_validate_stale_and_reviewed_and_missing():
    e = _e("x/1", "台詞", "Old.")
    p = _prop(e, "New.")
    e.translation = "Edited by hand."
    assert "edited since" in R.validate(p, e)
    e.translation = "Old."
    e.original = "別の台詞"
    assert "Japanese of this line changed" in R.validate(p, e)
    e.original = "台詞"
    e.status = "reviewed"
    assert "Reviewed" in R.validate(p, e)
    assert "no longer exists" in R.validate(p, None)


# ── apply ──────────────────────────────────────────────────────────

def test_apply_updates_copies_but_not_reviewed_or_different():
    a = _e("A/1", "同じ", "Old.")
    copy = _e("B/1", "同じ", "Old.")
    reviewed = _e("C/1", "同じ", "Old.", status="reviewed")
    other = _e("D/1", "同じ", "Different old.")
    es = [a, copy, reviewed, other]
    changed = R.apply([_prop(a, "New.")], es)
    assert changed == ["A/1", "B/1"]
    assert (a.translation, copy.translation) == ("New.", "New.")
    assert reviewed.translation == "Old." and other.translation == "Different old."


def test_apply_skips_proposals_that_went_stale():
    a = _e("A/1", "台詞", "Old.")
    p = _prop(a, "New.")
    a.translation = "User edit."
    assert R.apply([p], [a]) == []
    assert a.translation == "User edit."


def test_apply_never_hits_a_different_line_with_same_english():
    """Game 2 lesson: a fix must land on the line it was written for."""
    a = _e("A/1", "行かない", "I'll go.")
    b = _e("B/1", "行く", "I'll go.")
    R.apply([_prop(a, "I won't go.")], [a, b])
    assert a.translation == "I won't go." and b.translation == "I'll go."


# ── request ────────────────────────────────────────────────────────

def test_request_review_uses_client_chat(monkeypatch):
    from translator.ai_client import AIClient
    client = AIClient()
    chunk = R._chunk(_scene(2))
    seen = {}

    def fake_chat(*, messages, **kw):
        seen["messages"], seen["kw"] = messages, kw
        return {"message": {"content": "<think>hm</think>"
                            + json.dumps({"L2": {"fix": "Better.", "why": "x"}})}}
    monkeypatch.setattr(client, "_chat", fake_chat)
    props = R.request_review(client, chunk, "Character sheet:\n- (none given)")
    assert [p.new for p in props] == ["Better."]
    assert seen["kw"]["format"] == "json"
    assert seen["messages"][0]["content"] == R.REVIEW_SYSTEM


# ── dialog ─────────────────────────────────────────────────────────

class _FakeClient:
    model = "test-model"
    provider = "Ollama (Local)"

    def __init__(self):
        import threading
        self.cancel_event = threading.Event()


def _run_dialog(qapp, monkeypatch, es, replies):
    from PyQt6.QtWidgets import QApplication
    from translator.widgets.review_dialog import ReviewDialog
    calls = iter(replies)

    def fake_request(client, chunk, sheet):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return R.parse_review(json.dumps(r), chunk)
    monkeypatch.setattr(R, "request_review", fake_request)
    dlg = ReviewDialog(es, _FakeClient(), "sheet", {"All": None})
    dlg.start()
    dlg._worker.wait(5000)
    QApplication.processEvents()
    QApplication.processEvents()
    return dlg


def test_dialog_runs_validates_and_ticks(qapp, monkeypatch):
    es = _scene(3)
    reply = {"L1": {"fix": "Fixed.", "why": "negation"},
             "L2": {"fix": "Fixed 2 幸来", "why": "bad"},
             "L3": {"fix": "Line 2.", "why": "same"}}
    dlg = _run_dialog(qapp, monkeypatch, es, [reply])
    try:
        assert dlg.stack.currentIndex() == 1
        assert [p.new for p in dlg.ticked()] == ["Fixed."]
        assert "1 fixes proposed, 2 rejected" in dlg.summary.text()
        dlg._tick_all(False)
        assert dlg.ticked() == []
        dlg._tick_all(True)
        assert len(dlg.ticked()) == 1
    finally:
        dispose(dlg)


def test_dialog_reports_failed_requests(qapp, monkeypatch):
    es = _scene(70)     # two chunks
    dlg = _run_dialog(qapp, monkeypatch, es,
                      [ValueError("Review reply is not JSON"),
                       {"L1": {"fix": "Fixed.", "why": "x"}}])
    try:
        assert "1 requests failed" in dlg.summary.text()
        assert len(dlg.ticked()) == 1
    finally:
        dispose(dlg)


# ── main window ────────────────────────────────────────────────────

@pytest.fixture
def win(qapp, monkeypatch, tmp_path):
    from qt_helpers import dispose_main_window
    from translator.project_model import TranslationProject
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    w = mw.MainWindow()
    w._closing = True
    w.project = TranslationProject(entries=_scene(3))
    w._last_save_path = str(tmp_path / "state.json")
    monkeypatch.setattr(w, "_ensure_ollama_ready", lambda: True)
    yield w
    dispose_main_window(w)


def test_menu_applies_only_ticked_and_supports_undo(win, monkeypatch):
    from translator.widgets import main_window as mw
    target = win.project.entries[1]
    prop = _prop(target, "Fixed line.")

    class FakeDialog:
        def __init__(self, entries, client, sheet, scopes, parent=None):
            FakeDialog.scopes = scopes

        def exec(self):
            return True

        def ticked(self):
            return [prop]
    monkeypatch.setattr(mw, "ReviewDialog", FakeDialog)
    win._ai_review()
    assert target.translation == "Fixed line."
    assert set(FakeDialog.scopes) == {
        "All translated lines", "Suspect lines only (Find Suspect Lines)",
        "Lines shown in the table"}
    assert not win._busy
    win._undo_last_bulk()
    assert target.translation == "Line 1."


def test_menu_close_without_applying_changes_nothing(win, monkeypatch):
    from translator.widgets import main_window as mw

    class FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return False
    monkeypatch.setattr(mw, "ReviewDialog", FakeDialog)
    win._ai_review()
    assert [e.translation for e in win.project.entries] == [
        "Line 0.", "Line 1.", "Line 2."]
