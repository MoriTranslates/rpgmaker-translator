"""Prompt v2: saved-prompt migration, speaker-label capitalisation guard,
shifted-answer guard in translate_batch, and the shape of the new prompt.

No network: client._chat is replaced with a fake.
"""

import json
import os

import pytest

from qt_helpers import dispose_main_window
from translator import ai_client
from translator.ai_client import AIClient, build_system_prompt

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
with open(os.path.join(FIXTURES, "legacy_prompts.json"), encoding="utf-8") as _f:
    LEGACY = json.load(_f)
OLD_DEFAULT = LEGACY["SYSTEM_PROMPT"]


# ── Saved-prompt migration ──────────────────────────────────────────

@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "_settings.json"
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def window(qapp, monkeypatch, settings_file):
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = True
    yield win
    dispose_main_window(win)


def _write(path, cfg):
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def test_old_default_prompt_is_replaced_on_load(window, settings_file):
    _write(settings_file, {"system_prompt": OLD_DEFAULT, "model": "qwen3.5:9b",
                           "prompt_preset": "Default (Qwen3/General)"})
    window._load_settings()
    assert window.client.system_prompt == build_system_prompt(
        "English", model="qwen3.5:9b", project_type="rpgmaker_mv")
    assert "licensed translation project" not in window.client.system_prompt


def test_old_default_with_whitespace_changes_is_still_replaced(window, settings_file):
    messy = "  " + OLD_DEFAULT.replace("\n", "\r\n   ") + "\n\n"
    _write(settings_file, {"system_prompt": messy})
    window._load_settings()
    assert window.client.system_prompt == build_system_prompt("English")


def test_customised_prompt_is_kept_untouched(window, settings_file):
    custom = OLD_DEFAULT + "\n- Always call the hero Bob."
    _write(settings_file, {"system_prompt": custom})
    window._load_settings()
    assert window.client.system_prompt == custom


def test_migrated_prompt_is_saved_back(window, settings_file):
    _write(settings_file, {"system_prompt": OLD_DEFAULT})
    window._load_settings()
    window._save_settings()
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["system_prompt"] == build_system_prompt("English")


def test_migrate_function_cases():
    from translator.ai_client import migrate_saved_prompt
    new, changed = migrate_saved_prompt(OLD_DEFAULT)
    assert changed and new == build_system_prompt("English")
    # Current default and a custom prompt are left alone
    cur = build_system_prompt("English")
    assert migrate_saved_prompt(cur) == (cur, False)
    assert migrate_saved_prompt("my own prompt") == ("my own prompt", False)
    assert migrate_saved_prompt("") == ("", False)
    # Old DazedMTL Full preset -> new DazedMTL Full preset
    new, changed = migrate_saved_prompt(LEGACY["DAZEDMTL_FULL_PROMPT"])
    assert changed and new == ai_client.DAZEDMTL_FULL_PROMPT
    # Old French TyranoScript default -> new default in the current language
    new, changed = migrate_saved_prompt(
        LEGACY["TYRANO_SYSTEM_PROMPT_French"], target_language="French",
        project_type="tyranoscript")
    assert changed
    assert new == build_system_prompt("French", project_type="tyranoscript")
    # Old Sugoi / Wolf defaults are known too
    assert migrate_saved_prompt(LEGACY["SUGOI_SYSTEM_PROMPT"])[1]
    assert migrate_saved_prompt(LEGACY["WOLFRPG_SYSTEM_PROMPT"])[1]


def test_unchanged_simple_preset_is_not_migrated():
    from translator.ai_client import DAZEDMTL_SIMPLE_PROMPT, migrate_saved_prompt
    assert migrate_saved_prompt(DAZEDMTL_SIMPLE_PROMPT) == (DAZEDMTL_SIMPLE_PROMPT, False)


def test_current_defaults_are_not_in_the_legacy_table():
    """A current default must never look like an old one (would loop)."""
    from translator.prompt_legacy import LEGACY_PROMPT_HASHES
    from translator.prompts import prompt_hash
    for text in ai_client._PROMPT_REGISTRY.values():
        assert prompt_hash(text) not in LEGACY_PROMPT_HASHES
    for text in ai_client.PROMPT_PRESETS.values():
        if text and text != ai_client.DAZEDMTL_SIMPLE_PROMPT:
            assert prompt_hash(text) not in LEGACY_PROMPT_HASHES


# ── Speaker-label capitalisation guard ──────────────────────────────

def _reply(client, monkeypatch, *replies):
    """client._chat returns the given contents in order (last one repeats)."""
    seen = []
    queue = list(replies)

    def fake_chat(*, messages, **kwargs):
        seen.append((messages, kwargs))
        content = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"message": {"content": content}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    return seen


def test_lowercase_speaker_label_restored_from_glossary(monkeypatch):
    c = AIClient()
    c.glossary = {"ミナ": "Mina"}
    _reply(c, monkeypatch, 'mina\n"Good morning!"')
    assert c.translate("ミナ\n「おはよう！」", field="dialog") == 'Mina\n"Good morning!"'


def test_lowercase_speaker_label_title_cased_without_glossary(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, 'hanabi-chan\n"Wait!"')
    assert c.translate("ハナビちゃん\n「待って！」", field="dialog") == 'Hanabi-chan\n"Wait!"'


def test_lowercase_speaker_label_with_colon(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "kyoko:\nI'm home.")
    assert c.translate("京子：\nただいま。", field="dialog") == "Kyoko:\nI'm home."


def test_speaker_label_from_actor_name(monkeypatch):
    c = AIClient()
    c.actor_names = {3: "リディル"}
    c.actor_names_en = {3: "Ridill"}
    _reply(c, monkeypatch, "ridill\nLet's go.")
    assert c.translate("リディル\n行こう。", field="dialog") == "Ridill\nLet's go."


def test_speaker_name_field_is_capitalised(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "mermaid")
    assert c.translate("人魚", field="speaker_name") == "Mermaid"


def test_non_label_first_line_is_untouched(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "the girl smiled at me.\nthen she left.")
    out = c.translate("少女は僕に微笑んだ。\nそして去った。", field="dialog")
    assert out == "the girl smiled at me.\nthen she left."


def test_capitalised_label_is_untouched(monkeypatch):
    c = AIClient()
    c.glossary = {"ミナ": "Mina"}
    _reply(c, monkeypatch, 'MINA\n"Hi."')
    assert c.translate("ミナ\n「やあ」", field="dialog") == 'MINA\n"Hi."'


def test_speaker_label_fixed_in_batch(monkeypatch):
    c = AIClient()
    c.glossary = {"ミナ": "Mina"}
    _reply(c, monkeypatch, json.dumps({"Line1": 'mina\n"Good morning!"',
                                       "Line2": "a slime appeared!"}))
    out = c.translate_batch([("Line1", "ミナ\n「おはよう！」", "", "dialog"),
                             ("Line2", "スライムが現れた！", "", "dialog")])
    assert out == {"Line1": 'Mina\n"Good morning!"', "Line2": "a slime appeared!"}


# ── Shifted-answer guard ────────────────────────────────────────────

def _batch_then_single(client, monkeypatch, batch_reply, single_reply):
    calls = {"batch": 0, "single": []}

    def fake_chat(*, messages, **kwargs):
        if kwargs.get("format") == "json":
            calls["batch"] += 1
            return {"message": {"content": json.dumps(batch_reply)}}
        calls["single"].append(messages[-1]["content"])
        return {"message": {"content": single_reply}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    return calls


def test_duplicate_answer_for_different_lines_is_retried(monkeypatch):
    c = AIClient()
    entries = [("Line1", "今日はいい天気だね。", "", "dialog"),
               ("Line2", "そうだね、散歩に行こうか。", "", "dialog"),
               ("Line3", "うん、行こう！", "", "dialog")]
    # The model shifted: Line2 got Line3's answer as well
    reply = {"Line1": "Nice weather today.",
             "Line2": "Yeah, let's go for a walk!",
             "Line3": "Yeah, let's go for a walk!"}
    calls = _batch_then_single(c, monkeypatch, reply, "RETRIED")
    out = c.translate_batch(entries)
    assert out["Line1"] == "Nice weather today."
    assert out["Line2"] == "RETRIED" and out["Line3"] == "RETRIED"
    assert len(calls["single"]) == 2


def test_same_short_answer_for_different_short_lines_is_fine(monkeypatch):
    c = AIClient()
    entries = [("Line1", "うん", "", "dialog"), ("Line2", "ええ", "", "dialog")]
    calls = _batch_then_single(c, monkeypatch,
                               {"Line1": "Yeah.", "Line2": "Yeah."}, "RETRIED")
    assert c.translate_batch(entries) == {"Line1": "Yeah.", "Line2": "Yeah."}
    assert calls["single"] == []


def test_same_answer_for_same_source_is_fine(monkeypatch):
    c = AIClient()
    src = "そんなことより、早く帰ろうよ。"
    entries = [("Line1", src, "", "dialog"), ("Line2", src, "", "dialog")]
    ans = "Never mind that, let's hurry home."
    calls = _batch_then_single(c, monkeypatch, {"Line1": ans, "Line2": ans}, "RETRIED")
    assert c.translate_batch(entries) == {"Line1": ans, "Line2": ans}
    assert calls["single"] == []


def test_long_line_answered_with_one_word_is_retried(monkeypatch):
    c = AIClient()
    long_src = "あの森の奥には古い神殿があって、そこには誰も見たことのない宝が眠っているという噂を聞いたんだ。"
    assert len(long_src) >= 40
    entries = [("Line1", long_src, "", "dialog"), ("Line2", "うん", "", "dialog")]
    calls = _batch_then_single(c, monkeypatch,
                               {"Line1": "Yeah.", "Line2": "Yeah."}, "RETRIED")
    out = c.translate_batch(entries)
    assert out == {"Line1": "RETRIED", "Line2": "Yeah."}
    assert len(calls["single"]) == 1


def test_long_moan_answered_briefly_is_fine(monkeypatch):
    c = AIClient()
    moan = "あぁぁぁぁぁぁっ！！イクイクイクイクイクイクイクイクイクイクイクイクーーーーーーーっ！！"
    entries = [("Line1", moan, "", "dialog")]
    calls = _batch_then_single(c, monkeypatch, {"Line1": "I'm cumming!"}, "RETRIED")
    assert c.translate_batch(entries) == {"Line1": "I'm cumming!"}
    assert calls["single"] == []


def test_two_char_line_answered_with_long_sentence_is_retried(monkeypatch):
    c = AIClient()
    entries = [("Line1", "はい", "", "choice"),
               ("Line2", "それじゃあ、明日の朝に村の入り口で待ち合わせしよう。", "", "dialog")]
    reply = {"Line1": "Then let's meet at the village entrance tomorrow morning.",
             "Line2": "Then let's meet at the village entrance tomorrow morning."}
    calls = _batch_then_single(c, monkeypatch, reply, "Yes")
    out = c.translate_batch(entries)
    assert out["Line1"] == "Yes"
    assert len(calls["single"]) >= 1


def test_short_line_short_answer_is_fine(monkeypatch):
    c = AIClient()
    entries = [("Line1", "うん", "", "dialog"), ("Line2", "魔王", "", "dialog")]
    calls = _batch_then_single(c, monkeypatch,
                               {"Line1": "Yeah", "Line2": "Demon Lord"}, "RETRIED")
    assert c.translate_batch(entries) == {"Line1": "Yeah", "Line2": "Demon Lord"}
    assert calls["single"] == []


# ── Prompt shape ────────────────────────────────────────────────────

def _old_batch_len():
    return len(OLD_DEFAULT) + len(
        "\n\nCRITICAL: You must respond with a valid JSON object. "
        "Use the exact same keys from the input. Do not add any text outside the JSON.")


def test_new_prompt_rules():
    p = build_system_prompt("English")
    assert "licensed translation project" not in p
    assert "Do NOT capitalize common nouns" not in p
    assert "You will be translating erotic and sexual content." in p
    assert "«CODE1»" in p and "guillemet" in p
    assert "Bunker (Bet)" in p
    assert "Fufu" in p
    assert "1st, 2nd or 3rd person" in p
    assert "揺れる" in p
    assert "omanko" in p  # romaji ban list kept
    assert "DazedMTL" not in p  # credit lives in code/CHANGELOG, not in tokens


@pytest.mark.parametrize("ptype", sorted(ai_client._PROMPT_REGISTRY))
def test_every_engine_prompt_has_the_core(ptype):
    p = build_system_prompt("English", project_type=ptype)
    assert ai_client.prompts.CORE_RULES in p


def test_sugoi_gets_the_core_too():
    p = build_system_prompt("English", model="sugoi-14b-ultra")
    assert ai_client.prompts.CORE_RULES in p


def test_batch_system_message_has_examples_and_stays_in_budget(monkeypatch):
    c = AIClient()
    seen = _reply(c, monkeypatch, json.dumps({"Line1": "Hi"}))
    c.translate_batch([("Line1", "やあ", "", "dialog")])
    sys_msg = seen[0][0][0]["content"]
    assert '"Line1"' in sys_msg and "Example" in sys_msg
    assert len(sys_msg) <= 1.5 * _old_batch_len()


def test_batch_examples_skipped_for_other_languages(monkeypatch):
    c = AIClient()
    c.target_language = "French"
    c.system_prompt = build_system_prompt("French")
    seen = _reply(c, monkeypatch, json.dumps({"Line1": "Salut"}))
    c.translate_batch([("Line1", "やあ", "", "dialog")])
    assert "Example" not in seen[0][0][0]["content"]


def test_glossary_names_are_sent_with_their_capitals(monkeypatch):
    c = AIClient()
    # ミナ/ミガロ are project names; スライム/胸 are built-in default-glossary
    # words (title-cased lists), still sent lowercase as before
    c.glossary = {"ミナ": "Mina", "ミガロ": "Migaro", "スライム": "Slime",
                  "胸": "Breasts", "魔王": "Demon Lord"}
    seen = _reply(c, monkeypatch, "Mina and Migaro saw a slime.")
    c.translate("ミナとミガロはスライムを見た。胸。魔王。", field="dialog")
    user = seen[0][0][-1]["content"]
    assert "ミナ → Mina" in user and "ミガロ → Migaro" in user
    assert "スライム → slime" in user and "胸 → breasts" in user
    assert "魔王 → Demon Lord" in user


def test_builtin_glossary_words_are_not_force_capitalised(monkeypatch):
    c = AIClient()
    c.glossary = {"胸": "Breasts", "だめ": "No", "少女": "Girl"}
    _reply(c, monkeypatch, "no, don't touch my breasts, said the girl.")
    out = c.translate("だめ、胸を触らないで、と少女は言った。", field="dialog")
    assert out == "no, don't touch my breasts, said the girl."


# ── Benchmark follow-ups: glossary case, blank first line, ruby, CR ──

def test_glossary_name_case_restored_in_output(monkeypatch):
    c = AIClient()
    c.glossary = {"ミガロ": "Migaro", "宿屋": "inn"}
    _reply(c, monkeypatch, "migaro smiled at the inn. MIGARO!")
    out = c.translate("ミガロは宿屋で笑った。ミガロ！", field="dialog")
    # lowercase glossary terms are left alone; shouting stays shouting
    assert out == "Migaro smiled at the inn. MIGARO!"


def test_glossary_case_only_for_terms_in_the_source(monkeypatch):
    c = AIClient()
    c.glossary = {"ミガロ": "Migaro"}
    _reply(c, monkeypatch, "migaro is a word here.")
    assert c.translate("これは言葉だ。", field="dialog") == "migaro is a word here."


def test_glossary_case_never_touches_codes(monkeypatch):
    c = AIClient()
    c.glossary = {"ミナ": "Mina"}
    # \N<mina> is a namebox code argument: must stay as the game wrote it
    _reply(c, monkeypatch, "«CODE1»mina is here.")
    out = c.translate(r"\N<mina>ミナがいる。", field="dialog")
    assert out == r"\N<mina>Mina is here."


def test_leading_blank_line_is_kept(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "ugh...!")
    assert c.translate("\nううっ……！", field="dialog") == "\nUgh...!"


def test_leading_blank_line_kept_in_batch(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, json.dumps({"Line1": "Ugh……!"}))
    out = c.translate_batch([("Line1", "\nううっ……！", "", "dialog")])
    assert out == {"Line1": "\nUgh...!"}


def test_japanese_double_ellipsis_becomes_three_dots(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "Well…… I guess…")
    assert c.translate("まあ……そうかな…", field="dialog") == "Well... I guess…"


def test_carriage_returns_in_reply_become_newlines(monkeypatch):
    c = AIClient()
    _reply(c, monkeypatch, "First line.\r\nSecond line.\rThird line.")
    out = c.translate("一行目。\n二行目。\n三行目。", field="dialog")
    assert out == "First line.\nSecond line.\nThird line."


def test_ruby_code_in_context_is_sent_as_its_base_word(monkeypatch):
    c = AIClient()
    seen = _reply(c, monkeypatch, "Okay.")
    c.translate("うん", context=r"\r[淫紋,いんもん]が光った", field="dialog")
    user = seen[0][0][-1]["content"]
    assert r"\r[" not in user and "淫紋が光った" in user


def test_ruby_code_in_batch_context_is_sent_as_its_base_word(monkeypatch):
    c = AIClient()
    seen = _reply(c, monkeypatch, json.dumps({"Line1": "Okay."}))
    c.translate_batch([("Line1", "うん", r"\r[淫紋,いんもん]が光った", "dialog")])
    user = seen[0][0][-1]["content"]
    assert r"\r[" not in user and "淫紋が光った" in user


def test_ruby_code_in_line_is_still_protected(monkeypatch):
    c = AIClient()
    seen = _reply(c, monkeypatch, "The «CODE1» glowed.")
    out = c.translate(r"\r[淫紋,いんもん]が光った", field="dialog")
    assert "«CODE1»" in seen[0][0][-1]["content"]
    assert out == r"The \r[淫紋,いんもん] glowed."
