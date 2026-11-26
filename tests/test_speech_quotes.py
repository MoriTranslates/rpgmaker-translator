"""Dialogue quotes: a 「...」 line whose translation lost its quotes gets "..." back.

Game 2 of "My Happiness" showed only 26 of 4,418 「」 lines kept quotes,
because the model (Sugoi 14B) drops them; the outer-quote stripper was not
the cause. The repair runs on the RAW Japanese and fails closed.
"""

import pytest

from translator.ai_client import AIClient
from translator.output_checks import restore_speech_quotes
from translator.post_processor import run_post_processing
from translator.project_model import TranslationEntry


# ── restore_speech_quotes ──────────────────────────────────────────

@pytest.mark.parametrize("original, reply, expected", [
    ("「こんにちは」", "Hello.", '"Hello."'),
    # multi-line message: quotes go around the whole thing
    ("「一行目\n二行目」", "Line one\nline two.", '"Line one\nline two."'),
    # surrounding whitespace / a leading blank line stay outside the quotes
    ("「はい」", "\nYes. ", '\n"Yes." '),
    ("  「はい」\n", "Yes.", '"Yes."'),
    # an inner 『』 inside one 「」 pair still counts as one quoted line
    ("「彼は『はい』と言った」", "He said yes.", '"He said yes."'),
    # leading tags (<WordWrap> from an old save or hand edit) stay outside
    ("「はい」", "<WordWrap>Yes.", '<WordWrap>"Yes."'),
    ("「はい」", "<WordWrap> <B>Yes.", '<WordWrap> <B>"Yes."'),
])
def test_wraps_reply_when_original_is_one_speech_pair(original, reply, expected):
    assert restore_speech_quotes(original, reply) == expected


@pytest.mark.parametrize("original", [
    "こんにちは",                    # narration
    "「A」と「B」",                  # two quotes, not one wrapper
    "「はい」「いいえ」",
    "\\C[2]「はい」",                # a code before the quote
    "「はい」\\n<築本",              # a name glued on after
    "けど……「はい」",              # starts mid-sentence
    "「…っ……そうか、名前で",      # opening only (split message)
    "名前で、呼んでたんだ」",        # closing only
    "『はい』",                      # emphasis quotes alone: not speech
    "",
])
def test_leaves_reply_alone_when_original_is_not_one_speech_pair(original):
    assert restore_speech_quotes(original, "Yes.") == "Yes."


@pytest.mark.parametrize("reply", [
    '"Hello."',                      # already quoted
    'He said "yes."',                # model kept some quoting
    chr(0x201C) + "Hello." + chr(0x201D),  # curly quotes
    "「Hello.」",                    # model left JP brackets
    chr(0xAB) + "Bonjour." + chr(0xBB),  # French guillemets
    chr(0x201E) + "Hallo." + chr(0x201C),  # German quotes
    "'Hello.'",                      # single-quoted: don't double-wrap
    "",
    "   ",
    "<WordWrap>",                    # only a tag, no text
])
def test_leaves_reply_alone_when_it_has_its_own_quotes_or_is_empty(reply):
    assert restore_speech_quotes("「こんにちは」", reply) == reply


def test_curly_apostrophe_does_not_count_as_a_quote():
    reply = "Don" + chr(0x2019) + "t go."
    assert restore_speech_quotes("「行かないで」", reply) == '"' + reply + '"'


def test_is_idempotent():
    once = restore_speech_quotes("「こんにちは」", "Hello.")
    assert restore_speech_quotes("「こんにちは」", once) == once


# ── AIClient: single and batch paths ───────────────────────────────

def test_single_translate_restores_dropped_quotes(fake_llm):
    fake_llm.translate_fn = lambda _s: "Hello."
    assert AIClient().translate("「こんにちは」", field="dialog") == '"Hello."'


def test_single_translate_keeps_quotes_the_model_kept(fake_llm):
    fake_llm.translate_fn = lambda _s: '"Hello."'
    assert AIClient().translate("「こんにちは」", field="dialog") == '"Hello."'


def test_single_translate_narration_gets_no_quotes(fake_llm):
    fake_llm.translate_fn = lambda _s: "It was quiet."
    assert AIClient().translate("静かだった。", field="dialog") == "It was quiet."


def test_batch_translate_restores_dropped_quotes(fake_llm):
    fake_llm.translate_fn = lambda _s: "Hello there."
    out = AIClient().translate_batch([
        ("Line1", "「こんにちは」", "", "dialog"),
        ("Line2", "静かな朝だった。", "", "dialog"),
    ])
    assert out["Line1"] == '"Hello there."'
    assert out["Line2"] == "Hello there."


@pytest.mark.parametrize("project_type", ["tyranoscript", "kirikiri", "crowd", "renpy"])
def test_vn_engines_never_get_quotes_back(fake_llm, project_type):
    fake_llm.translate_fn = lambda _s: "Hello."
    client = AIClient()
    client.project_type = project_type
    assert client.translate("「こんにちは」", field="dialog") == "Hello."


def test_pig_latin_is_not_wrapped(fake_llm):
    fake_llm.translate_fn = lambda _s: "Ellohay."
    client = AIClient()
    client.target_language = "Pig Latin"
    assert '"' not in client.translate("「こんにちは」", field="dialog")


# ── Post-processing of an already-translated project ───────────────

def _entry(original, translation, status="translated", eid="Map001/1"):
    return TranslationEntry(id=eid, file="Map001.json", field="dialog",
                            original=original, translation=translation,
                            status=status)


def test_post_processing_restores_quotes_and_counts_them():
    entries = [_entry("「こんにちは」", "Hello.", eid="a"),
               _entry("静かだった。", "It was quiet.", eid="b")]
    result = run_post_processing(entries, project_type="rpgmaker")
    assert entries[0].translation == '"Hello."'
    assert entries[1].translation == "It was quiet."
    assert result.speech_quotes == 1
    assert "dialogue quotes restored" in str(result)
    # a second run changes nothing
    again = run_post_processing(entries, project_type="rpgmaker")
    assert again.speech_quotes == 0
    assert entries[0].translation == '"Hello."'


def test_post_processing_quote_lands_on_text_after_wordwrap_strip():
    entry = _entry("「こんにちは」", "<WordWrap>Hello.")
    run_post_processing([entry], project_type="rpgmaker")
    assert entry.translation == '"Hello."'


def test_post_processing_never_touches_reviewed_lines():
    entry = _entry("「こんにちは」", "Hello.", status="reviewed")
    run_post_processing([entry], project_type="rpgmaker")
    assert entry.translation == "Hello."


def test_post_processing_skips_vn_engines():
    entry = _entry("「こんにちは」", "Hello.")
    result = run_post_processing([entry], project_type="tyranoscript")
    assert result.speech_quotes == 0
    assert '"' not in entry.translation
