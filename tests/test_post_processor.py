"""translator/post_processor.py — individual fixes + run_post_processing."""

import copy

import pytest

from translator import post_processor as pp
from translator.project_model import TranslationEntry


def E(translation, original="原文", field="dialog", file="Map001.json",
      status="translated", id="Map001.json/Ev1(EV001)/p0/dialog_1"):
    return TranslationEntry(id=id, file=file, field=field, original=original,
                            translation=translation, status=status)


def test_name_dupes():
    e = E("Karen (karen) smiled.")
    assert pp._fix_name_dupes(e)
    assert e.translation == "Karen smiled."


def test_code_leaks_stripped():
    e = E("Hello «CODE3» there <<CODE1>> [CODE2].")
    assert pp._fix_code_leaks(e, [])
    assert e.translation == "Hello there."


def test_code_leak_orphan_possessive_queues_retranslation():
    ids = []
    e = E("«CODE1»'s hand trembled.")
    assert pp._fix_code_leaks(e, ids)
    assert e.status == "untranslated" and e.translation == ""
    assert ids == [e.id]


def test_llm_refusal_cleared():
    ids = []
    e = E("I'm sorry, I cannot translate sexually explicit content.")
    assert pp._fix_llm_refusal(e, ids)
    assert (e.translation, e.status, ids) == ("", "untranslated", [e.id])


def test_quote_mismatch_only_for_narration():
    ids = []
    narr = E('"Hello," she said.', original="彼女は言った。")
    assert pp._fix_quote_mismatch(narr, ids)
    speech = E('"Hello."', original="「こんにちは」")
    assert not pp._fix_quote_mismatch(speech, ids)


def test_hallucinated_br_only_when_original_has_none():
    e = E("Line one<br>Line two")
    assert pp._fix_hallucinated_br(e)
    assert e.translation == "Line one\nLine two"
    keep = E("A<br>B", original="あ<br>い")
    assert not pp._fix_hallucinated_br(keep)


def test_wordwrap_double_space_trailing():
    e = E("<WordWrap>Hello  there   friend.  ")
    pp._fix_wordwrap_tags(e)
    pp._fix_double_spaces(e)
    pp._fix_trailing_whitespace(e)
    assert e.translation == "Hello there friend."


def test_skill_message_gets_leading_space():
    e = E("casts Fire!", field="message1", file="Skills.json",
          id="Skills.json/2/message1")
    assert pp._fix_skill_message_space(e)
    assert e.translation == " casts Fire!"
    assert not pp._fix_skill_message_space(e)   # idempotent


def test_space_after_lowercase_name_code():
    e = E("\\n[1]went home.")
    assert pp._fix_space_after_name_code(e)
    assert e.translation == "\\n[1] went home."


def test_space_after_uppercase_name_code():
    e = E("\\N[1]went home.")
    assert pp._fix_space_after_name_code(e)
    assert e.translation == "\\N[1] went home."


def test_collapsed_color_code_lowercase_reconstructed():
    e = E("Talk to \\c[2]\\c[0] now.", original="\\c[2]アリス\\c[0]に話して")
    assert pp._fix_collapsed_color_codes(e, [], {"アリス": "Alice"})
    assert e.translation == "Talk to \\c[2]Alice\\c[0] now."


def test_collapsed_color_code_uppercase_reconstructed():
    e = E("Talk to \\C[2]\\C[0] now.", original="\\C[2]アリス\\C[0]に話して")
    assert pp._fix_collapsed_color_codes(e, [], {"アリス": "Alice"})
    assert e.translation == "Talk to \\C[2]Alice\\C[0] now."


def test_spurious_newlines_in_non_dialog():
    e = E("Max\nHP", original="最大ＨＰ", field="terms.params[0]",
          file="System.json")
    assert pp._fix_spurious_newlines(e)
    assert e.translation == "Max HP"
    desc = E("Restores\n50 HP.", original="HPを50回復", field="description")
    assert not pp._fix_spurious_newlines(desc)


def test_corrupt_speaker():
    ids = []
    e = E("This is clearly a whole sentence and definitely not a name at all.",
          field="speaker_name")
    assert pp._fix_corrupt_speaker(e, ids)
    assert e.status == "untranslated"


def test_compound_words():
    e = E("Infact, I did it alot and some thing happened.")
    assert pp._fix_compound_words(e)
    assert e.translation == "In fact, I did it a lot and something happened."


def test_capitalize_system_type_arrays():
    e = E("general armor", original="一般防具", field="armorTypes",
          file="System.json", id="System.json/armorTypes/1")
    assert pp._fix_capitalize_terms(e)
    assert e.translation == "General Armor"


def test_capitalize_system_menu_commands_from_parser():
    from conftest import FAKE_MV_GAME
    from translator.rpgmaker_mv import RPGMakerMVParser
    entry = next(e for e in RPGMakerMVParser().load_project(FAKE_MV_GAME)
                 if e.id == "System.json/terms/commands/1")
    entry.translation, entry.status = "run away", "translated"
    assert pp._fix_capitalize_terms(entry)
    assert entry.translation == "Run Away"


def _sample_entries():
    return [
        E("Hello  «CODE1» world.  ", original="\\C[2]こんにちは"),
        E("Karen (karen) smiled.", id="b"),
        E("<WordWrap>Line one<br>Line two", id="c"),
        E("I'm unable to translate this.", id="d"),
        E("casts Fire!", field="message1", file="Skills.json", id="e"),
        E("\\C[2]\\N[1]\\C[0], good morning!\nToday I have \\V[3] gold\\.",
          original="\\C[2]\\N[1]\\C[0]、おはよう！\n今日は\\V[3]ゴールド\\.", id="f"),
        E("untranslated stays", status="untranslated", id="g"),
    ]


def test_run_post_processing_summary_and_untouched_entries():
    entries = _sample_entries()
    res = pp.run_post_processing(entries)
    assert res.code_leaks == 1
    assert res.name_dupes == 1
    assert res.llm_refusals == 1
    assert res.retranslate_ids == ["d"]
    assert entries[0].translation == "Hello world."
    assert entries[2].translation == "Line one\nLine two"
    assert entries[4].translation == " casts Fire!"
    # Clean dialogue with control codes is not altered
    assert entries[5].translation == \
        "\\C[2]\\N[1]\\C[0], good morning!\nToday I have \\V[3] gold\\."
    assert entries[6].translation == "untranslated stays"
    assert "entries fixed" in str(res)


def test_run_post_processing_is_idempotent():
    entries = _sample_entries()
    pp.run_post_processing(entries)
    snapshot = copy.deepcopy(entries)
    res2 = pp.run_post_processing(entries)
    assert entries == snapshot
    assert res2.total_entries_fixed == 0


def test_run_post_processing_preserves_codes_on_fixture_translations():
    """Fake-translated fixture text goes through cleanup without losing codes."""
    import fake_ai
    from conftest import FAKE_MV_GAME
    from translator import CONTROL_CODE_RE
    from translator.rpgmaker_mv import RPGMakerMVParser
    entries = RPGMakerMVParser().load_project(FAKE_MV_GAME)
    for e in entries:
        e.translation = fake_ai.fake_translate(e.original)
        e.status = "translated"
    before = {e.id: CONTROL_CODE_RE.findall(e.translation) for e in entries}
    pp.run_post_processing(entries)
    after = {e.id: CONTROL_CODE_RE.findall(e.translation) for e in entries}
    assert before == after
