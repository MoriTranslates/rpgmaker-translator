"""Regressions for findings of the blind review of feature/game2-lessons."""

import pytest

from translator import review as R
from translator.output_checks import normalise_fullwidth, strip_prompt_leaks
from translator.post_processor import run_post_processing
from translator.project_model import TranslationEntry


def _e(eid, original, translation, field="dialog", ctx="prev",
       status="translated"):
    return TranslationEntry(eid, eid.split("/")[0], field, original,
                            translation, status, ctx)


# 1. A review fix must not spread to other speakers, fields or system text
def test_review_fix_only_reaches_same_field_and_speaker():
    a = _e("Map001.json/e1/p0/dialog_1", "はい", "Yes", ctx="[Speaker: Mina]")
    same = _e("Map005.json/e1/p0/dialog_9", "はい", "Yes", ctx="[Speaker: Mina]")
    other_sp = _e("Map002.json/e3/p0/dialog_1", "はい", "Yes", ctx="[Speaker: Kenta]")
    term = _e("System.json/terms/yes", "はい", "Yes", field="terms.yes")
    item = _e("Items.json/5/name", "はい", "Yes", field="name")
    p = R.Proposal("L1", a.id, a.original, a.translation, "Yes, dear", "x")
    changed = R.apply([p], [a, same, other_sp, term, item])
    assert changed == [a.id, same.id]
    assert other_sp.translation == term.translation == item.translation == "Yes"


# 2. Quote restoring only for message text, never names or script strings
@pytest.mark.parametrize("field", ["script_variable", "name", "description",
                                   "speaker_name", "plugin_param"])
def test_quotes_not_restored_outside_messages(field):
    e = _e("X.json/1", "「勇者」", "Hero", field=field)
    run_post_processing([e], project_type="rpgmaker")
    assert e.translation == "Hero"


def test_client_does_not_quote_non_message_fields():
    from translator.ai_client import AIClient
    c = AIClient()
    assert c._postprocess_result("Hero", {}, '"勇者"', "name",
                                 original="「勇者」") == "Hero"
    assert c._postprocess_result("Hero", {}, '"勇者"', "dialog",
                                 original="「勇者」") == '"Hero"'


# 3. Full-width marks that are syntax in some engine are left alone
@pytest.mark.parametrize("text", [
    "【Yukiko】Hello", "［Ｌａｕｇｈｓ］ Hi", "＜Ｌｏｖｅ＞ you",
    "Rate 50％１", "＼Ｃ［２］Mina", "｛x｝",
])
def test_fullwidth_never_creates_code_syntax(text):
    out = normalise_fullwidth(text)
    for ch in "[]<>%\\{}":
        assert ch not in out, (text, out)


# 4. Review fixes with control / format characters are rejected
@pytest.mark.parametrize("bad", ["\r\n", "\t", chr(0x85), chr(0x200E),
                                 chr(0xAD), chr(0x2060), chr(0x7F)])
def test_review_rejects_control_and_format_chars(bad):
    e = _e("x/1", "台詞", "Old line.")
    p = R.Proposal("L1", e.id, e.original, e.translation, f"New{bad}line.", "")
    assert R.validate(p, e)


# 5. Control codes must keep their order
def test_review_rejects_reordered_codes():
    e = _e("x/1", "台詞", "\\C[2]Mina\\C[0] is here.")
    p = R.Proposal("L1", e.id, e.original, e.translation,
                   "\\C[0]It's\\C[2] Mina here.", "")
    assert "control codes" in R.validate(p, e)


# 6. Odd project data doesn't crash the suspect finder
def test_suspects_survive_odd_data():
    from translator.suspects import find_suspects
    es = [_e("a/1", None, "x"), _e("a/2", None, "y"),
          _e("a/3", "彼は来た", "He came.")]
    find_suspects(es, glossary={"守人": 5, 7: "x"},
                  speaker_genders={"守人": "male"})


# 7. A line that is only prompt-like text is kept, not cleared forever
def test_prompt_leak_never_empties_a_line():
    assert strip_prompt_leaks("Content type: dialogue line", "内容") == \
        "Content type: dialogue line"
    e = _e("a/1", "内容", "Translate this:")
    r = run_post_processing([e], project_type="rpgmaker")
    assert e.translation == "Translate this:" and not r.retranslate_ids


# 2b. Script strings: a translation never ends the JS string early
def test_script_string_escapes_its_quote():
    from translator.rpgmaker_mv import _js_escape_in
    line = "$gameVariables.setValue(5, '勇者');"
    assert _js_escape_in(line, line.index("勇者"), "Don't") == "Don" + chr(92) + "'t"
    line = '$gameMessage.add("勇者");'
    assert _js_escape_in(line, line.index("勇者"), 'He said "hi"') == \
        'He said \\"hi\\"'
    # codes already in JS form are not doubled
    assert _js_escape_in(line, line.index("勇者"), "\\C[2]Hi") == "\\C[2]Hi"


def test_fresh_reply_that_is_only_a_leak_becomes_empty():
    from translator.output_checks import clean_output
    assert clean_output("源", "Content type: dialogue line") == ""
