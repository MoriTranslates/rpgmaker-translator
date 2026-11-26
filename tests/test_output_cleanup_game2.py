"""Full-width punctuation and leaked prompt lines (lessons from game 2).

Game 2 had ~265 English lines with ！？。、 or full-width spaces left in, and
one "Content type: dialogue line" copied from the prompt.
"""

import pytest

from translator.ai_client import AIClient
from translator.output_checks import (clean_output, normalise_fullwidth,
                                      strip_prompt_leaks)
from translator.post_processor import run_post_processing
from translator.project_model import TranslationEntry

FW_SPACE = chr(0x3000)
FW_EXCL = chr(0xFF01)
FW_Q = chr(0xFF1F)
JP_STOP = chr(0x3002)
JP_COMMA = chr(0x3001)
WAVE = chr(0xFF5E)
HEART = chr(0x2661)


# ── normalise_fullwidth ────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Wait" + FW_EXCL + FW_EXCL, "Wait!!"),
    ("Really" + FW_Q + FW_EXCL + FW_SPACE + "No way.", "Really?! No way."),
    ("Yes" + JP_COMMA + "that's it" + JP_STOP, "Yes, that's it."),
    ("Done" + JP_STOP + "Next", "Done. Next"),
    ("Fufu" + HEART + FW_SPACE + "Again", "Fufu" + HEART + " Again"),
    ("Hi! " + FW_SPACE + "there", "Hi! there"),           # no double space
    ("Mmm" + WAVE + HEART, "Mmm" + WAVE + HEART),          # ～ and ♡ kept
    ("「Hello」", '"Hello"'),
    ("【Notice】", "【Notice】"),            # [ ] would be a VN tag: left alone
    ("Price: " + "".join(chr(0xFF10 + d) for d in (1, 0, 0)) + "G", "Price: 100G"),
    ("Mmh......Chup" + FW_SPACE + "x", "Mmh......Chup x"),     # untouched elsewhere
    ("3.5 and 1,000" + FW_EXCL, "3.5 and 1,000!"),
])
def test_normalise_fullwidth(text, expected):
    assert normalise_fullwidth(text) == expected


@pytest.mark.parametrize("text", [
    "C_幸来の信頼について。|350|200",       # picture-choice file name
    "Yukiko said 幸来！",                    # Japanese left in: not ours
    "Plain English.",
    "",
])
def test_normalise_leaves_japanese_and_clean_text_alone(text):
    assert normalise_fullwidth(text) == text


def test_normalise_never_touches_codes():
    text = "\\n<幸来>Hi" + FW_EXCL
    # the name code holds Japanese, but codes are ignored for that check
    assert normalise_fullwidth(text) == "\\n<幸来>Hi!"
    assert normalise_fullwidth("«CODE1»Hi" + FW_EXCL) == "«CODE1»Hi!"


@pytest.mark.parametrize("lang", ["Chinese (Simplified)", "Japanese", "Korean"])
def test_normalise_skips_cjk_targets(lang):
    assert normalise_fullwidth("好" + FW_EXCL, lang) == "好" + FW_EXCL


# ── strip_prompt_leaks ─────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Hello.\nContent type: dialogue line", "Hello."),
    ("Content type: dialogue line\nHello.", "Hello."),
    ("Hello.\nSpeaker: Yukiko is FEMALE. Lines spoken by her...", "Hello."),
    ("Narration: this line is Mamoru's own first-person narration.\nIt rained.",
     "It rained."),
    ("Translate this:\nHello.", "Hello."),
    ("Line one\n\nline two", "Line one\n\nline two"),       # inner blank kept
    ("The content type: unknown.", "The content type: unknown."),  # not a line
    ("", ""),
])
def test_strip_prompt_leaks(text, expected):
    assert strip_prompt_leaks(text, "元の文") == expected


def test_strip_keeps_text_the_source_really_has():
    assert strip_prompt_leaks("Content type: X", "Content type: X") == "Content type: X"


def test_strip_keeps_leading_blank_line_the_source_had():
    assert strip_prompt_leaks("\nHello.\nContent type: dialogue line", "\nこんにちは") \
        == "\nHello."


# ── wired into the client ──────────────────────────────────────────

def test_clean_output_runs_both():
    out = clean_output("source", "Wait" + FW_EXCL + "\nContent type: dialogue line")
    assert out == "Wait!"


def test_single_translate_cleans_reply(fake_llm):
    fake_llm.translate_fn = lambda _s: ("No way" + FW_Q + FW_EXCL
                                        + "\nContent type: dialogue line")
    assert AIClient().translate("まさか！？", field="dialog") == "No way?!"


# ── post-processing ────────────────────────────────────────────────

def _entry(original, translation, status="translated", eid="Map001/1"):
    return TranslationEntry(id=eid, file="Map001.json", field="dialog",
                            original=original, translation=translation,
                            status=status)


def test_post_processing_fixes_existing_translations():
    es = [_entry("まさか！", "No way" + FW_EXCL, eid="a"),
          _entry("はい", "Yes.\nContent type: dialogue line", eid="b"),
          _entry("はい", "Content type: dialogue line", eid="c"),
          _entry("はい", "Yes" + FW_EXCL, status="reviewed", eid="d")]
    r = run_post_processing(es, project_type="rpgmaker")
    assert es[0].translation == "No way!"
    assert es[1].translation == "Yes."
    # Only prompt-like text: kept (re-queuing could loop forever)
    assert es[2].translation == "Content type: dialogue line"
    assert es[2].status == "translated" and "c" not in r.retranslate_ids
    assert es[3].translation == "Yes" + FW_EXCL          # reviewed: untouched
    assert r.fullwidth_punct == 1 and r.prompt_leaks == 1
    assert "full-width punctuation fixed" in str(r)
    again = run_post_processing(es, project_type="rpgmaker")
    assert again.fullwidth_punct == 0 and again.prompt_leaks == 0


def test_post_processing_respects_cjk_target():
    e = _entry("好！", "好" + FW_EXCL)
    run_post_processing([e], project_type="rpgmaker",
                        target_language="Chinese (Simplified)")
    assert e.translation == "好" + FW_EXCL
