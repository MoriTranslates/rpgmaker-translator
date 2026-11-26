"""Suspect finder (lesson from game 2: every check passed, ~1 line in 5 wrong).

Made-up lines in the shapes game 2's errors had. On game 2 itself each
check was measured against the hand review (44-100% of flags were real
errors, against a ~30% base rate).
"""

import pytest
from qt_helpers import dispose

from translator import suspects as S
from translator.project_model import TranslationEntry

L, R = chr(0x300C), chr(0x300D)


def _e(original, translation, *, speaker="", field="dialog", eid=None,
       status="translated"):
    ctx = f"[Speaker: {speaker}]\nprev" if speaker else "prev"
    return TranslationEntry(eid or f"Map001.json/{abs(hash((original, translation)))}",
                            "Map001.json", field, original, translation, status, ctx)


# ── single checks ──────────────────────────────────────────────────

def test_pov():
    assert S.check_pov(_e("僕は家に帰った。", "He went home."))
    assert not S.check_pov(_e("僕は家に帰った。", "I went home."))
    assert not S.check_pov(_e("雨が降っていた。", "It was raining."))        # no 僕
    assert not S.check_pov(_e(f"{L}僕は帰る{R}", "He goes home.", speaker="A"))  # speech


@pytest.mark.parametrize("jp, en, flagged", [
    ("これは寝取らせではない。", "This is definitely cuckolding.", True),
    ("行かなかった", "I went.", True),
    ("これは寝取らせではない。", "This isn't cuckolding.", False),
    ("行かないと。", "I have to go.", False),                    # ないと = must
    ("すいません", "Sorry.", False),
    ("頷くことしか出来なかった。", "All I could do was nod.", False),  # しか…ない
    ("情けない話だ。", "It's pathetic.", False),                  # adjective
    ("いつもの幸来に変わりない。", "Yukiko is just like always.", False),
    ("体調が悪かったりしないの？", "Are you feeling sick?", False),  # question
])
def test_negation(jp, en, flagged):
    assert bool(S.check_negation(_e(jp, en))) is flagged


@pytest.mark.parametrize("jp, en, flagged", [
    (f"{L}家にいないの？{R}", "Is he not home?", True),
    (f"{L}彼は家にいないの？{R}", "Isn't he home?", False),
    (f"{L}彼女は家にいないの？{R}", "Isn't he home?", True),    # 彼女 is not 彼
    (f"{L}店長はどこ？{R}", "Where is he?", False),
    (f"{L}守人さんは？{R}", "Where is he?", False),            # male name
    (f"{L}家にいないの？{R}", "Aren't you home?", False),
])
def test_he_without_a_man(jp, en, flagged):
    kind, _ = S.check_pronoun(_e(jp, en), male_names=["守人"])
    assert (kind == "he_no_male") is flagged


def test_body():
    assert S.check_body(_e("あっ", "My pussy is so wet..."), "male")
    assert S.check_body(_e("あっ", "My cock is so hard..."), "female")
    assert not S.check_body(_e("あっ", "My cock is so hard..."), "male")
    assert not S.check_body(_e("あっ", "My pussy..."), "")       # unknown speaker


def test_name_missing():
    names = {"幸来": "Yukiko"}
    assert S.check_name_missing(_e("幸来が笑った。", "Mamoru smiled."), names)
    assert not S.check_name_missing(_e("幸来が笑った。", "Yukiko smiled."), names)
    assert not S.check_name_missing(_e("幸来が笑った。", "She smiled."), names)
    assert not S.check_name_missing(_e("幸来、おはよう", "Morning to you."), names)


def test_fullwidth_and_too_long():
    assert S.check_fullwidth(_e("あ", "Wait" + chr(0xFF01)))
    assert S.check_fullwidth(_e("あ", "Wait 幸来"))
    assert not S.check_fullwidth(_e("あ", "「Wait」".replace(chr(0x300C), '"')
                                     .replace(chr(0x300D), '"')))
    assert S.check_too_long(_e("あ", "1\n2\n3\n4\n5"))
    assert not S.check_too_long(_e("あ", "1\n2\n3\n4\n"))
    assert not S.check_too_long(_e("あ", "<WordWrap>1\n2\n3\n4\n5"))


# ── whole run ──────────────────────────────────────────────────────

def test_find_suspects_groups_and_skips():
    es = [
        _e("僕は家に帰った。", "He went home.", eid="pov"),
        _e("これはデートではない。", "This is a date.", eid="neg"),
        _e(f"{L}あっ{R}", "My pussy...", speaker="Mamoru", eid="body"),
        _e("ズプッ", "Zuppuh!!", eid="sfx"),
        _e("同じ行です", "Same line.", eid="d1"),
        _e("同じ行です", "Another line.", eid="d2"),
        _e("僕は家に帰った。", "He went home.", eid="rev", status="reviewed"),
        _e("僕は家に帰った。", "", eid="untr", status="untranslated"),
    ]
    rep = S.find_suspects(es, speaker_genders={"Mamoru": "male"})
    ids = {k: {s.id for s in v} for k, v in rep.by_kind.items()}
    assert ids["pov"] == {"pov"}
    assert ids["negation"] == {"neg"}
    assert ids["body"] == {"body"}
    assert ids["romaji_sfx"] == {"sfx"}
    assert ids["inconsistent"] == {"d1", "d2"}
    assert "rev" not in rep.ids() and "untr" not in rep.ids()
    rep2 = S.find_suspects(es, speaker_genders={"Mamoru": "male"},
                           include_reviewed=True)
    assert "rev" in rep2.ids()
    # kinds limits the run
    assert set(S.find_suspects(es, kinds=["pov"]).by_kind) == {"pov"}


def test_non_english_target_runs_only_language_free_checks():
    es = [_e("僕は家に帰った。", "Il est rentré.", eid="a")]
    rep = S.find_suspects(es, target_language="French")
    assert "pov" not in rep.by_kind


# ── dialog + menu ──────────────────────────────────────────────────

def _entries():
    return [_e("僕は家に帰った。", "He went home.", eid="pov"),
            _e("ズプッ", "Zuppuh <b>!!", eid="sfx"),
            _e("僕は寝た。", "He slept.", eid="rev", status="reviewed")]


def test_dialog_lists_kinds_and_returns_checked_ids(qapp):
    from translator.widgets.suspect_dialog import SuspectDialog
    es = _entries()
    dlg = SuspectDialog(es, lambda inc: S.find_suspects(es, include_reviewed=inc))
    try:
        from PyQt6.QtCore import Qt
        # "He went home." for 僕 is both a lost "I" and a "he" with no man
        assert dlg._kinds == ["pov", "he_no_male", "romaji_sfx"]
        assert dlg.selected_ids() == {"pov", "sfx"}
        for row in (0, 1):
            dlg.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        assert dlg.selected_ids() == {"sfx"}
        dlg.include_reviewed.setChecked(True)          # reruns the checks
        assert "rev" in dlg.selected_ids()
        dlg.table.selectRow(2)
        assert "&lt;b&gt;" in dlg.examples.toHtml()    # escaped
    finally:
        dispose(dlg)


def test_dialog_with_nothing_flagged(qapp):
    from translator.widgets.suspect_dialog import SuspectDialog
    es = [_e("雨だ。", "It's raining.")]
    dlg = SuspectDialog(es, lambda inc: S.find_suspects(es))
    try:
        assert not dlg.show_btn.isEnabled()
        assert "No suspect" in dlg.total_label.text()
    finally:
        dispose(dlg)


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
    w.project = TranslationProject(entries=_entries())
    w.trans_table.set_entries(w.project.entries)
    yield w
    dispose_main_window(w)


def test_menu_filters_table_to_suspects(win, monkeypatch):
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw.SuspectDialog, "exec", lambda self: True)
    win._find_suspects()
    assert win.trans_table._id_filter == {"pov", "sfx"}
    win._enable_project_actions()
    assert win.suspects_action.isEnabled()


def test_menu_cancel_leaves_table(win, monkeypatch):
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw.SuspectDialog, "exec", lambda self: False)
    win._find_suspects()
    assert win.trans_table._id_filter is None
