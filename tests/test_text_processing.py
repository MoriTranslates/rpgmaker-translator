"""Pins post_processor / text_processor / regex / glossary behaviour.

Each case is input -> expected for a fixed (or verified-correct) behaviour.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from translator import CONTROL_CODE_RE, JAPANESE_RE, TYRANO_CODE_RE  # noqa: E402
from translator import post_processor as pp  # noqa: E402
from translator.default_glossary import get_all_defaults  # noqa: E402
from translator.text_processor import PluginAnalyzer, TextProcessor  # noqa: E402
from translator.utils import event_prefix, extract_event_context  # noqa: E402


def mk(translation, original="x", field="dialog", file="Map001.json",
       status="translated"):
    return SimpleNamespace(translation=translation, original=original,
                           field=field, file=file, status=status, id="id1",
                           has_face=False)


def apply(fn, translation, *args, **kw):
    e = mk(translation, **kw)
    fn(e, *args)
    return e.translation


# ── HIGH 1: _fix_missing_spaces ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Everything is fine.", "Butterfly wings", "Senpai, wait!",
    "Somewhere far away", "Nevertheless, go.", "Understand?",
    "I love McDonald's", "\\pX[100]Hello", "Valentine", "Princess",
    "Goblins", "Lilith", "Seraphina", "oniichan", "nakadashi", "ohohoho",
    "kyaaaa", "Nooooo!", "Ahhhh!", "1,000 gold", "Ufufu",
])
def test_missing_spaces_leaves_real_words_names_romaji(text):
    assert apply(pp._fix_missing_spaces, text) == text


@pytest.mark.parametrize("text,expected", [
    ("usingher now", "using her now"),
    ("Usingher now", "Using her now"),
    ("sheturned away", "she turned away"),
    ("Hello,world", "Hello, world"),
    ('[emb exp="f.usingher"]usingher', '[emb exp="f.usingher"]using her'),
])
def test_missing_spaces_splits_concatenations(text, expected):
    assert apply(pp._fix_missing_spaces, text) == expected


def test_missing_spaces_respects_glossary_words():
    e = mk("herbody")
    assert not pp._fix_missing_spaces(e, {"herbody"})
    assert e.translation == "herbody"


def test_missing_spaces_is_tyranoscript_only():
    e = mk("usingher now")
    pp.run_post_processing([e])
    assert e.translation == "usingher now"
    e = mk("usingher now", file="scene.ks")
    pp.run_post_processing([e], project_type="tyranoscript")
    assert e.translation == "using her now"


# ── HIGH 2: _fix_quote_mismatch ──────────────────────────────────────

@pytest.mark.parametrize("orig", [
    '"なに？"', "“なに？”", "〝なに？〟", "「なに？」",
    "『なに？』", "（なに？）", "(なに？)",
])
def test_quote_mismatch_keeps_quoted_originals(orig):
    ids = []
    assert apply(pp._fix_quote_mismatch, '"Huh?"', ids, original=orig) == '"Huh?"'
    assert ids == []


def test_quote_mismatch_apostrophe_is_not_a_quote():
    assert apply(pp._fix_quote_mismatch, "'Tis late.", [],
                 original="もう遅い") == "'Tis late."


def test_quote_mismatch_still_catches_line_shift():
    ids = []
    e = mk('"Huh?"', original="彼は歩いた。")
    assert pp._fix_quote_mismatch(e, ids)
    assert e.translation == "" and e.status == "untranslated" and ids == ["id1"]


# ── MED 3: code leak cleanup keeps newlines ──────────────────────────

def test_code_leaks_preserve_newlines():
    assert apply(pp._fix_code_leaks, "Wait «CODE1»\n...what?", []) \
        == "Wait\n...what?"
    assert apply(pp._fix_code_leaks, "Hi «CODE1»!", []) == "Hi!"


# ── MED 4: uppercase \N / \C ─────────────────────────────────────────

@pytest.mark.parametrize("code", ["\\N[1]", "\\n[1]"])
def test_space_after_name_code_both_cases(code):
    assert apply(pp._fix_space_after_name_code, code + "She went") \
        == code + " She went"
    assert apply(pp._fix_space_after_name_code, code + "'s") == code + "'s"


@pytest.mark.parametrize("c", ["C", "c"])
def test_collapsed_color_codes_both_cases(c):
    out = apply(pp._fix_collapsed_color_codes, f"Got \\{c}[2]\\{c}[0]!", [],
                {"剣": "Sword"}, original=f"\\{c}[2]剣\\{c}[0]を得た")
    assert out == f"Got \\{c}[2]Sword\\{c}[0]!"


# ── MED 5: System term field names use dots ──────────────────────────

@pytest.mark.parametrize("field", ["terms.commands[0]", "terms.params[2]",
                                   "terms.basic[1]", "skillTypes", "elements"])
def test_capitalize_system_terms(field):
    assert apply(pp._fix_capitalize_terms, "item of power", field=field,
                 file="System.json") == "Item Of Power"


def test_capitalize_skips_messages():
    assert apply(pp._fix_capitalize_terms, "you won the battle",
                 field="terms.messages.victory", file="System.json") \
        == "you won the battle"


# ── MED 6/7/8: text_processor ────────────────────────────────────────

LONG_CHOICE = ("A very long choice text that definitely exceeds the fifty "
               "five char line limit yes")


@pytest.mark.parametrize("field,file", [
    ("choice", "Map001.json"), ("name", "Items.json"),
    ("terms.commands[0]", "System.json"), ("message1", "Skills.json"),
])
def test_process_all_skips_non_message_fields(field, file):
    e = mk(LONG_CHOICE, original="選択肢", field=field, file=file)
    assert TextProcessor(PluginAnalyzer()).process_all([e]) == 0
    assert e.translation == LONG_CHOICE


@pytest.mark.parametrize("field", ["dialog", "dialogue", "scroll_text",
                                   "description", "profile"])
def test_process_all_wraps_message_fields(field):
    e = mk(LONG_CHOICE, original="あ", field=field)
    TextProcessor(PluginAnalyzer()).process_all([e])
    assert "\n" in e.translation


def test_visual_length_estimates_visible_codes():
    tp = TextProcessor(PluginAnalyzer())
    assert tp._visual_length("\\N[1]") == 8
    assert tp._visual_length("\\P[2]") == 8
    assert tp._visual_length("\\V[1]") == 4
    assert tp._visual_length("\\I[5]") == 2
    assert tp._visual_length("%1") == 6
    assert tp._visual_length("\\C[2]Alice\\C[0]") == 5
    assert tp._visual_length("<br>\\.\\{") == 0


def test_analyzer_defaults_match_recalculated():
    a = PluginAnalyzer()
    before = (a.chars_per_line, a.face_chars_per_line)
    a._recalculate()
    assert (a.chars_per_line, a.face_chars_per_line) == before == (49, 39)


def test_manual_wrap_lines():
    tp = TextProcessor(PluginAnalyzer())
    out = tp.process_entry("あ", " ".join(["word"] * 30), use_tag=False)
    assert out.split("\n") == [" ".join(["word"] * 10)] * 3
    assert tp.process_entry("あ\nい", "") == ""


def test_plugin_wrap_merges_extra_lines():
    a = PluginAnalyzer()
    a.has_wordwrap_plugin = True
    a.wordwrap_tag = "<WordWrap>"
    assert TextProcessor(a).process_entry("あ\nい", "short a\nshort b\nshort c") \
        == "short a\nshort b short c"


def test_wrap_keeps_spaced_tag_together():
    tp = TextProcessor(PluginAnalyzer())
    assert tp._wrap_to_lines("x" * 50 + " <font color=red>hi", 55) \
        == ["x" * 50, "<font color=red>hi"]


# ── MED 9: refusal detection ─────────────────────────────────────────

def test_refusal_inside_normal_line_is_kept():
    text = "This book is sexually explicit."
    assert apply(pp._fix_llm_refusal, text, [],
                 original="この本は過激な内容だ。こ本は過激な") == text
    text = "Not even an AI could stop me!"
    assert apply(pp._fix_llm_refusal, text, [], original="ＡＩでも止められない！！") == text


@pytest.mark.parametrize("text", [
    "I cannot translate this content.",
    "  As an AI, I must decline.",
])
def test_refusal_at_start_is_cleared(text):
    ids = []
    assert apply(pp._fix_llm_refusal, text, ids, original="長い元の文章です、とても長い") == ""
    assert ids == ["id1"]


def test_refusal_boilerplate_longer_than_original():
    ids = []
    text = "Sorry, but this is sexually explicit content I will not handle."
    assert apply(pp._fix_llm_refusal, text, ids, original="あっ") == ""


# ── MED 10: split-word merging ───────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Pen is mightier", "Run away!", "Man age the shop", "Hold on a sec.",
    "Lean on me", "Is that you?", "Her eyes",
])
def test_split_words_keeps_real_phrases(text):
    assert apply(pp._fix_split_words, text) == text


@pytest.mark.parametrize("text,expected", [
    ("Act ive", "Active"), ("Dis appeared", "Disappeared"),
])
def test_split_words_merges_fragments(text, expected):
    assert apply(pp._fix_split_words, text) == expected


# ── MED 11: default glossary ─────────────────────────────────────────

def test_default_glossary_has_no_ambiguous_short_keys():
    g = get_all_defaults()
    for k in ["いく", "イク", "あん", "ちょっと", "本", "力", "役", "ブタ", "無",
              "地", "木", "運", "王", "服", "自分", "ミス", "くっ"]:
        assert k not in g, k
    assert g["愛人"] == "Mistress"
    assert g["恋人"] == "Lover"


# ── LOW 12: CONTROL_CODE_RE ──────────────────────────────────────────

@pytest.mark.parametrize("code", [
    "\\N[1]", "\\C[2]", "\\C[0]", "\\V[3]", "\\FS[24]", "\\I[5]", "\\pX[100]",
    "\\{", "\\}", "\\.", "\\|", "\\!", "\\>", "\\<", "\\^", "\\$",
    "<br>", "<WordWrap>", "</B>", "%1", "\\G", "\\\\", "\\N<Bob>",
    "\\n<\\N[1]>",
])
def test_control_code_re_whole_codes(code):
    assert CONTROL_CODE_RE.findall(f"a {code} b") == [code]
    assert CONTROL_CODE_RE.findall(f"あ{code}い") == [code]


def test_control_code_re_no_false_positives():
    assert CONTROL_CODE_RE.findall("5 < 6 and 7 > 3") == []
    assert CONTROL_CODE_RE.findall("\\Gold") == []
    assert CONTROL_CODE_RE.findall("Hello, world! 100% sure.") == []


def test_control_code_re_sequence():
    assert CONTROL_CODE_RE.findall("\\N[1]と\\C[2]剣\\C[0]を%1\\G") == [
        "\\N[1]", "\\C[2]", "\\C[0]", "%1", "\\G"]


@pytest.mark.parametrize("text", [
    "\\N[1]と\\C[2]剣\\C[0]を\\V[3]本",
    "\\FS[24]大\\{\\}\\.\\|\\!\\>\\<\\^\\$",
    "<br>あ<WordWrap>い",
    "\\N<ボブ>こんにちは",
    "\\pX[100]やあ",
    "%1は\\G手に入れた",
    "\\\\N[1]よ",
    "\\C[2]Alice\\C[0] \\N[1] \\V[12]",
])
def test_extract_restore_round_trip(text):
    from translator.ai_client import AIClient
    client = AIClient()
    clean, mapping = client._extract_codes(text)
    assert "\\" not in clean.replace("\\\\", "")
    assert client._restore_codes(clean, mapping) == text


# ── LOW 13: JAPANESE_RE ──────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("「Hello」", True), ("『x』", True), ("々", True), ("〆", True),
    ("ㇰ", True), ("ｱｲｳ", True), ("ひらがな", True), ("漢字", True),
    ("、。", False), ("ＡＢＣ", False), ("ASCII", False),
])
def test_japanese_re(text, expected):
    assert bool(JAPANESE_RE.search(text)) is expected


# ── LOW 14: skill message leading space ──────────────────────────────

def test_skill_message_space():
    assert apply(pp._fix_skill_message_space, "%1 casts %2!", file="Skills.json",
                 field="message1", original="%1は%2を唱えた！") == "%1 casts %2!"
    assert apply(pp._fix_skill_message_space, "casts Fire!", file="Skills.json",
                 field="message1", original="は炎を唱えた！") == " casts Fire!"


# ── LOW 15: Res is t... fragments ────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Res is ting is futile", "Resisting is futile"),
    ("You can't res is t me", "You can't resist me"),
    ("Res is tance", "Resistance"),
    ("He res is ted.", "He resisted."),
])
def test_res_is_t_fragments(text, expected):
    assert apply(pp._fix_compound_words, text) == expected


# ── LOW 16: dialogue quotes outside tags only ────────────────────────

def test_dialogue_quotes_keep_tag_attributes():
    assert apply(pp._fix_dialogue_quotes, 'Hi [emb exp="f.name"], "yes"') \
        == 'Hi [emb exp="f.name"], yes'
    assert apply(pp._fix_dialogue_quotes, '[ruby text="a b"]X') == '[ruby text="a b"]X'
    assert apply(pp._fix_dialogue_quotes, '"Hello there"') == "Hello there"


# ── LOW 17: compound words ───────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Every one of you will die.", "Every one of you will die."),
    ("Any one of these will do.", "Any one of these will do."),
    ("Some times are hard.", "Some times are hard."),
    ("Every one is here.", "Everyone is here."),
    ("I did it with out help.", "I did it without help."),
    ("It's alot.", "It's a lot."),
])
def test_compound_words(text, expected):
    assert apply(pp._fix_compound_words, text) == expected


# ── Already-correct helpers ──────────────────────────────────────────

def test_hallucinated_br():
    assert apply(pp._fix_hallucinated_br, "A<br>B", original="A B") == "A\nB"
    assert apply(pp._fix_hallucinated_br, "A<br>B", original="あ<br>い") == "A<br>B"


def test_name_dupes():
    assert apply(pp._fix_name_dupes, "Ria(ria) smiled") == "Ria smiled"
    assert apply(pp._fix_name_dupes, "Karen (Karen)") == "Karen"


def test_word_per_line():
    assert apply(pp._fix_word_per_line, "Ah...\nOh...\nNo!\nWhy?",
                 original="あ…") == "Ah... Oh... No! Why?"


def test_tyrano_code_re():
    assert TYRANO_CODE_RE.findall("a[r]b[rr]c[R][emb exp='x']") == [
        "[r]", "[rr]", "[R]", "[emb exp='x']"]


@pytest.mark.parametrize("entry_id,prefix,ctx", [
    ("CommonEvents.json/CE169(Name)/dialog_5", "CommonEvents.json/CE169(Name)", "CE169"),
    ("Map001.json/Ev3(EV003)/p0/dialog_5", "Map001.json/Ev3(EV003)/p0", "Ev3/p0"),
    ("Map001.json/displayName", "Map001.json", ""),
])
def test_utils_event_helpers(entry_id, prefix, ctx):
    assert event_prefix(entry_id) == prefix
    assert extract_event_context(entry_id) == ctx


# ── End-to-end regression guard ──────────────────────────────────────

GOOD_SENTENCES = [
    "Everything is fine.",
    "Nevertheless, we must press on.",
    "Do you understand? The butterfly flew away.",
    "Princess Valentine smiled at Seraphina.",
    "Every one of you will pay for this!",
    "The pen is mightier than the sword.",
    "Run away! The goblins are coming!",
    "I love McDonald's.",
    "\\N[1] drew the \\C[2]Sword\\C[0] and charged.",
    "Onii-chan, wait for me!",
    "'Tis but a scratch.",
    "This book is sexually explicit, you know.",
]


@pytest.mark.parametrize("project_type", ["rpgmaker", "tyranoscript"])
@pytest.mark.parametrize("text", GOOD_SENTENCES)
def test_pipeline_leaves_good_english_unchanged(text, project_type):
    e = mk(text, original="彼女は微笑んで静かにそう言ったのだった。")
    res = pp.run_post_processing([e], project_type=project_type)
    assert e.translation == text
    assert res.total_entries_fixed == 0
