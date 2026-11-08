"""Polish replies go through the same output guards as translations.

A polish reply that drops a name code (\\N[1] -> "Shadow") or leaves an
invented «CODE» marker would be written into the game. polish() must reject
it (raise, so the line is reported and its translation stays as it was) and
polish_batch() must leave that key out (the engine retries it on its own).
No network: AIClient._chat is replaced with a fake.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from translator.ai_client import AIClient  # noqa: E402

SRC = r"\N[1] is here."


def _client(monkeypatch, reply):
    c = AIClient()
    sent = []

    def fake_chat(**kw):
        sent.append(kw)
        content = reply(kw) if callable(reply) else reply
        return {"message": {"content": content}}
    monkeypatch.setattr(c, "_chat", fake_chat)
    return c


def _marker(c, text=SRC):
    _clean, code_map = c._extract_codes(text)
    return next(iter(code_map))


def test_polish_rejects_name_replaced_by_character_name(monkeypatch):
    c = _client(monkeypatch, "Shadow is here.")
    with pytest.raises(ValueError):
        c.polish(SRC)


def test_polish_rejects_invented_marker(monkeypatch):
    c = AIClient()
    m = _marker(c)
    c = _client(monkeypatch, f"{m}is here, «CODE2».")
    with pytest.raises(ValueError):
        c.polish(SRC)


def test_polish_rejects_bare_code_word_leak(monkeypatch):
    # Name code kept, but a bare "CODE7" the source never had is left in.
    m = _marker(AIClient())
    c = _client(monkeypatch, f"{m}is here, CODE7.")
    with pytest.raises(ValueError):
        c.polish(SRC)


def test_polish_accepts_clean_reply(monkeypatch):
    c = AIClient()
    m = _marker(c)
    c = _client(monkeypatch, f"{m}is right here.")
    assert c.polish(SRC) == r"\N[1] is right here."


def test_polish_without_codes_still_works(monkeypatch):
    c = _client(monkeypatch, "Good morning!")
    assert c.polish("good morning") == "Good morning!"


def test_polish_batch_rejects_bad_keys_and_keeps_good(monkeypatch):
    m = _marker(AIClient())
    reply = json.dumps({
        "a": "Shadow is here.",                 # name code replaced
        "b": f"{m}is here, «CODE2».",           # invented marker
        "c": f"{m}is right here.",              # clean
        "d": "Good morning!",                   # clean, no codes
    }, ensure_ascii=False)
    c = _client(monkeypatch, reply)
    out = c.polish_batch([("a", SRC), ("b", SRC), ("c", SRC),
                          ("d", "good morning")])
    assert "a" not in out
    assert "b" not in out
    assert out["c"] == r"\N[1] is right here."
    assert out["d"] == "Good morning!"
