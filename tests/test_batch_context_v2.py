"""Prompt v2 batch context: per-line speaker hints, clean history pairs."""

import json

from translator.ai_client import AIClient, build_system_prompt


def _capture(client, monkeypatch, reply):
    seen = []

    def fake_chat(*, messages, **kwargs):
        seen.append(messages)
        return {"message": {"content": reply}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    return seen


def _client_with_actors():
    c = AIClient()
    c.actor_names = {1: "ミナ", 2: "タロウ"}
    c.actor_genders = {1: "female", 2: "male"}
    return c


def test_each_batch_line_gets_its_own_speaker_hint(monkeypatch):
    c = _client_with_actors()
    seen = _capture(c, monkeypatch, json.dumps(
        {"Line1": "Hi.", "Line2": "Yo.", "Line3": "Bye."}))
    c.translate_batch([
        ("Line1", "やあ", "[Speaker: ミナ]", "dialog"),
        ("Line2", "よう", "[Speaker: タロウ]", "dialog"),
        ("Line3", "またね", "[Speaker: ミナ]", "dialog"),
    ])
    user = seen[0][-1]["content"]
    assert "Line1, Line3: Speaker: ミナ is FEMALE" in user
    assert "Line2: Speaker: タロウ is MALE" in user


def test_single_speaker_batch_keeps_the_plain_hint(monkeypatch):
    c = _client_with_actors()
    seen = _capture(c, monkeypatch, json.dumps({"Line1": "Hi.", "Line2": "Yo."}))
    c.translate_batch([("Line1", "やあ", "[Speaker: ミナ]", "dialog"),
                       ("Line2", "よう", "[Speaker: ミナ]", "dialog")])
    user = seen[0][-1]["content"]
    assert "Speaker: ミナ is FEMALE" in user
    assert "Line1, Line2:" not in user


def test_speaker_hint_from_a_later_line_is_not_lost(monkeypatch):
    c = _client_with_actors()
    seen = _capture(c, monkeypatch, json.dumps({"Line1": "Hm.", "Line2": "Yo."}))
    c.translate_batch([("Line1", "ふむ", "", "narration"),
                       ("Line2", "よう", "[Speaker: タロウ]", "dialog")])
    assert "Line2: Speaker: タロウ is MALE" in seen[0][-1]["content"]


def test_history_pairs_show_no_raw_codes_or_ruby(monkeypatch):
    c = AIClient()
    seen = _capture(c, monkeypatch, json.dumps({"Line1": "Okay."}))
    history = [(r"\r[淫紋,いんもん]が\C[2]光った\C[0]", r"The \r[淫紋,いんもん] \C[2]glowed\C[0]."),
               ("そうか", "I see.")]
    c.translate_batch([("Line1", "うん", "", "dialog")], history=history)
    system = seen[0][0]["content"]
    hist = system.split("Translation History", 1)[1]
    assert "\\r[" not in hist and "\\C[" not in hist
    assert "淫紋が«CODE1»光った«CODE2»" in hist


def test_single_history_pairs_show_no_ruby(monkeypatch):
    c = AIClient()
    seen = _capture(c, monkeypatch, "Okay.")
    c.translate("うん", history=[(r"\r[淫紋,いんもん]が光った", "The crest glowed.")])
    sent = "\n".join(m["content"] for m in seen[0][1:])
    assert "\\r[" not in sent and "淫紋が光った" in sent


def test_unknown_gender_rule_removed():
    p = build_system_prompt("English")
    assert 'use the name or "they"' not in p
    assert "do NOT default to" not in p
    assert "Keep pronouns consistent with the previous lines and the character info." in p
    assert "lets me suck" in p and "ぱんぱん" in p
