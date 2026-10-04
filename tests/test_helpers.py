"""Pure helpers: translator/__init__.py regexes, utils.py, text_processor.py."""

import os
import shutil

import pytest

from translator import CONTROL_CODE_RE, JAPANESE_RE, TYRANO_CODE_RE
from translator.utils import event_prefix, extract_event_context
from translator.text_processor import PluginAnalyzer, TextProcessor


# ── translator/__init__.py ───────────────────────────────────────────

@pytest.mark.parametrize("code", [
    "\\V[1]", "\\N[2]", "\\n[3]", "\\C[0]", "\\FS[24]", "\\I[64]", "\\P[1]",
    "\\F1[k_normal]", "\\{", "\\}", "\\$", "\\.", "\\|", "\\!", "\\>", "\\<",
    "\\^", "<br>", "<WordWrap>", "%1", "%12",
])
def test_control_code_re_matches_whole_code(code):
    assert CONTROL_CODE_RE.fullmatch(code)
    assert CONTROL_CODE_RE.findall(f"a{code}b") == [code]


def test_control_code_re_ignores_plain_text():
    assert CONTROL_CODE_RE.findall("Hello, world! 100% sure.") == []


def test_control_code_re_matches_currency_escape():
    assert CONTROL_CODE_RE.findall("%1\\G") == ["%1", "\\G"]


def test_japanese_re():
    assert JAPANESE_RE.search("ひらがな")
    assert JAPANESE_RE.search("カタカナ")
    assert JAPANESE_RE.search("漢字")
    assert JAPANESE_RE.search("ｶﾀｶﾅ")
    assert not JAPANESE_RE.search("ASCII only, ＡＢＣ fullwidth latin")


def test_tyrano_code_re():
    text = '[r]こんにちは[p][emb exp="f.name"][heart][ruby text="かん"]'
    assert TYRANO_CODE_RE.findall(text) == [
        "[r]", "[p]", '[emb exp="f.name"]', "[heart]", '[ruby text="かん"]']
    assert TYRANO_CODE_RE.findall("[jump target=*a]") == []


# ── translator/utils.py ──────────────────────────────────────────────

@pytest.mark.parametrize("entry_id,prefix", [
    ("CommonEvents.json/CE169(Name)/dialog_5", "CommonEvents.json/CE169(Name)"),
    ("Map001.json/Ev3(EV003)/p0/dialog_5", "Map001.json/Ev3(EV003)/p0"),
    ("noslash", ""),
])
def test_event_prefix(entry_id, prefix):
    assert event_prefix(entry_id) == prefix


@pytest.mark.parametrize("entry_id,ctx", [
    ("CommonEvents.json/CE169(リブパイズリ)/dialog_64", "CE169"),
    ("Map001.json/Ev3(EV003)/p0/dialog_5", "Ev3/p0"),
    ("Troops.json/Troop5(ゴブリン)/p0/dialog_1", "Troop5/p0"),
    ("Actors.json/1/name", "1"),
    ("Map001.json/displayName", ""),
])
def test_extract_event_context(entry_id, ctx):
    assert extract_event_context(entry_id) == ctx


# ── translator/text_processor.py ─────────────────────────────────────

LONG = ("\\C[2]Alice\\C[0] said that the weather today is lovely and we "
        "should go on a picnic \\N[1] \\V[12] together by the river\\. "
        "Then \\C[4]Bob\\C[0] agreed with \\FS[20]great\\FS[28] enthusiasm.")


@pytest.fixture
def tp():
    return TextProcessor(PluginAnalyzer())


def test_visual_length_ignores_codes(tp):
    assert tp._visual_length("\\C[2]Alice\\C[0]") == 5
    # Invisible codes are zero-width (visible ones like %1 / \N[n] may be
    # given an estimated width).
    assert tp._visual_length("<br>\\.\\C[3]\\{") == 0


@pytest.mark.parametrize("has_face", [False, True])
def test_manual_wrap_never_splits_codes(tp, has_face):
    out = tp.process_entry("一\n二", LONG, has_face=has_face)
    lines = out.split("\n")
    limit = (tp.analyzer.face_chars_per_line if has_face
             else tp.analyzer.chars_per_line)
    assert all(tp._visual_length(ln) <= limit for ln in lines)
    # Every code survives intact and in order
    assert CONTROL_CODE_RE.findall(out) == CONTROL_CODE_RE.findall(LONG)
    # Only whitespace changed
    assert out.replace("\n", " ") == LONG


def test_face_wrap_is_narrower(tp):
    plain = tp.process_entry("x", LONG).split("\n")
    face = tp.process_entry("x", LONG, has_face=True).split("\n")
    assert tp.analyzer.face_chars_per_line < tp.analyzer.chars_per_line
    assert len(face) >= len(plain)


def test_manual_wrap_pads_to_original_line_count(tp):
    out = tp.process_entry("一\n二\n三\n四", "Short.")
    assert out.split("\n") == ["Short.", "", "", ""]


def test_manual_wrap_strips_wordwrap_tag(tp):
    assert "<WordWrap>" not in tp.process_entry("x", "<WordWrap>Hello there")


def test_plugin_wordwrap_adds_tag_only_on_overflow():
    a = PluginAnalyzer()
    a.has_wordwrap_plugin = True
    a.wordwrap_tag = "<WordWrap>"
    tp = TextProcessor(a)
    short = tp.process_entry("一\n二", "Hi.\nBye.")
    assert short == "Hi.\nBye."
    long = tp.process_entry("一\n二", LONG)
    assert long.startswith("<WordWrap>")
    assert long.count("\n") == 1      # merged/padded to the original 2 slots
    # use_tag=False (DB fields) always falls back to manual wrapping
    assert "<WordWrap>" not in tp.process_entry("x", LONG, use_tag=False)


def test_process_all_counts_and_skips_untranslated():
    from translator.project_model import TranslationEntry
    tp = TextProcessor(PluginAnalyzer())
    e1 = TranslationEntry("a", "Map001.json", "dialog", "一", LONG, "translated")
    e2 = TranslationEntry("b", "Map001.json", "dialog", "一", LONG, "untranslated")
    assert tp.process_all([e1, e2]) == 1
    assert "\n" in e1.translation
    assert e2.translation == LONG
    assert tp.expanded_count == 1


def test_plugin_analyzer_detects_yep(tmp_path):
    from conftest import FAKE_MV_GAME
    game = tmp_path / "g"
    shutil.copytree(FAKE_MV_GAME, game)
    with open(game / "js" / "plugins.js", "w", encoding="utf-8") as f:
        f.write('var $plugins = [{"name":"YEP_MessageCore","status":true,'
                '"parameters":{"Default Width":"1000","Message Rows":"3",'
                '"Word Wrapping":"true"}}];\n')
    a = PluginAnalyzer()
    a.analyze_project(str(game))
    assert a.has_wordwrap_plugin
    assert a.wordwrap_tag == "<WordWrap>"
    assert a.message_width == 1000
    assert a.max_lines == 3
    assert a.chars_per_line == int((1000 - 48) / (28 * 0.55))


def test_manual_wrap_keeps_spaced_tag_together(tp):
    """A tag containing a space ('<font size=20>') is never split across
    lines: once both halves are joined the regex sees one zero-width code."""
    for n in range(40, 56):
        text = "a" * n + " <font size=20> tail tail"
        lines = tp.process_entry("x", text).split("\n")
        assert any("<font size=20>" in ln for ln in lines), n
