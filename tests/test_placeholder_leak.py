"""A model that rewrites the «CODE1» marker ("CODE1", CODE1, {CODE1} ...)
must never get the raw marker written into game text.

Live case (qwen3.5:4b): the source's actor-name code came back as
'"CODE1" has become aroused...!' and was saved as-is. A recoverable mangling
is restored to the real code; anything else is rejected like the existing
«CODEn» leak guard.

No network: AIClient._chat is replaced with a fake.
"""

import json

import pytest

from translator.ai_client import AIClient

SRC = "\\N[1]は興奮状態になってしまった…！"   # \N[1] = actor name code


def _client(monkeypatch, *replies):
    c = AIClient()
    queue = list(replies)

    def fake_chat(*, messages, **kw):
        return {"message": {"content": queue.pop(0) if len(queue) > 1 else queue[0]}}

    monkeypatch.setattr(c, "_chat", fake_chat)
    return c


@pytest.mark.parametrize("reply", [
    '"CODE1" has become aroused…!',
    "CODE1 has become aroused…!",
    "<<CODE1>> has become aroused…!",
    "[CODE1] has become aroused…!",
    "{CODE1} has become aroused…!",
    "code1 has become aroused…!",
    "'Code1' has become aroused…!",
    chr(0x201C) + "CODE1" + chr(0x201D) + " has become aroused…!",  # curly quotes
])
def test_mangled_marker_with_known_number_is_restored(monkeypatch, reply):
    out = _client(monkeypatch, reply).translate(SRC)
    assert out == "\\N[1] has become aroused…!"


@pytest.mark.parametrize("reply,want", [
    ('("CODE1") is here.', "(\\N[1]) is here."),
    ("CODE1's sword broke.", "\\N[1]'s sword broke."),
    ('"CODE1"', "\\N[1]"),
])
def test_surrounding_punctuation_survives_restore(monkeypatch, reply, want):
    assert _client(monkeypatch, reply).translate(SRC) == want


def test_quote_belonging_to_dialogue_is_kept(monkeypatch):
    out = _client(monkeypatch, 'He said "CODE1, wait!" and left.').translate(SRC)
    assert out == 'He said "\\N[1], wait!" and left.'


@pytest.mark.parametrize("reply", [
    '\\N[1]"CODE1" has become aroused…!',   # the live case: raw code + marker
    '"CODE7" has become aroused…!',          # number not in this line
    "CODE2 has become aroused…!",
    "«CODE1» and CODE1 again",               # marker twice: can't tell which
])
def test_unrecoverable_marker_is_rejected(monkeypatch, reply):
    with pytest.raises(ValueError, match="placeholder"):
        _client(monkeypatch, reply).translate(SRC)


@pytest.mark.parametrize("src,reply", [
    ("コードは何？", "What is the CODE?"),
    ("コードは1234", "The code is 1234."),
    ("コードは1234", "The CODE 1234 opens it."),
    ("CODE1を入力", "Enter CODE1."),   # the source itself says CODE1
    ("暗号", "Decode 12 lines"),
])
def test_legit_code_words_are_untouched(monkeypatch, src, reply):
    assert _client(monkeypatch, reply).translate(src) == reply


def test_batch_restores_or_retries_mangled_markers(monkeypatch):
    entries = [("Line1", SRC, "", "dialog"),
               ("Line2", "\\N[2]は眠った", "", "dialog")]
    batch_reply = json.dumps({"Line1": '"CODE1" has become aroused…!',
                              "Line2": "CODE5 fell asleep."})
    # Line2 is retried on its own; the retry also leaks -> left out.
    c = _client(monkeypatch, batch_reply, "CODE5 fell asleep.")
    out = c.translate_batch(entries)
    assert out == {"Line1": "\\N[1] has become aroused…!"}


def test_batch_retry_can_fix_a_leak(monkeypatch):
    entries = [("Line1", SRC, "", "dialog"),
               ("Line2", "\\N[2]は眠った", "", "dialog")]
    batch_reply = json.dumps({"Line1": "«CODE1» has become aroused…!",
                              "Line2": "\\N[2]CODE1 fell asleep."})
    c = _client(monkeypatch, batch_reply, "«CODE1» fell asleep.")
    out = c.translate_batch(entries)
    assert out == {"Line1": "\\N[1] has become aroused…!",
                   "Line2": "\\N[2] fell asleep."}
