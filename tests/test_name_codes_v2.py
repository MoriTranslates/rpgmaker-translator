"""A name/variable code (\\n[1], \\N[1], \\V[3]) must never be replaced by the
character's current name: the reply is retried, never accepted."""

import json

import pytest

from translator import output_checks as oc
from translator.ai_client import AIClient, build_system_prompt


def test_name_code_mismatch():
    f = oc.name_code_mismatch
    assert f(r"\n[1]は興奮した", r"\n[1] got excited") is None
    assert f(r"\n[1]は興奮した", "Shadow got excited")
    assert f(r"\N[1]と\N[1]", r"\N[1] and Shadow")              # count differs
    assert f(r"\V[3]ゴールド", r"\V[3] gold") is None
    assert f(r"\V[3]ゴールド", "3 gold")
    assert f(r"\C[2]赤\C[0]", "red") is None                   # formatting codes not checked
    assert f("普通の文", "A plain line.") is None
    assert f(r"\N<\n[1]>やあ", r"\N<\n[1]>Hi") is None


def _client(monkeypatch, replies):
    c = AIClient()
    c.actor_names = {1: "シャドウ"}
    c.actor_names_en = {1: "Shadow"}
    c.actor_genders = {1: "male"}
    seen = []
    queue = list(replies)

    def fake_chat(*, messages, **kwargs):
        seen.append((messages, kwargs))
        content = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"message": {"content": content}}

    monkeypatch.setattr(c, "_chat", fake_chat)
    return c, seen


def test_single_name_replaced_then_fixed_on_retry(monkeypatch):
    c, seen = _client(monkeypatch, ["Shadow got excited.", "«CODE1» got excited."])
    out = c.translate(r"\n[1]は興奮した。", field="dialog")
    assert out == r"\n[1] got excited."
    assert len(seen) == 2
    assert "«CODE1»" in seen[1][0][-1]["content"]


def test_single_name_replaced_twice_is_rejected(monkeypatch):
    c, _seen = _client(monkeypatch, ["Shadow got excited."])
    with pytest.raises(ValueError, match="name code"):
        c.translate(r"\n[1]は興奮した。", field="dialog")


def test_batch_name_replaced_is_retried_single(monkeypatch):
    c, seen = _client(monkeypatch, [
        json.dumps({"Line1": "Shadow got excited.", "Line2": "Okay."}),
        "«CODE1» got excited.",
    ])
    out = c.translate_batch([("Line1", r"\n[1]は興奮した。", "", "dialog"),
                             ("Line2", "うん", "", "dialog")])
    assert out == {"Line1": r"\n[1] got excited.", "Line2": "Okay."}


def test_name_code_hint_says_copy_the_marker(monkeypatch):
    c, seen = _client(monkeypatch, ["«CODE1» got excited."])
    c.translate(r"\n[1]は興奮した。", field="dialog")
    user = seen[0][0][-1]["content"]
    assert "«CODE1» = name code for Shadow (he/him)" in user
    assert "never write the name" in user


def test_prompt_wording_v2_round2():
    p = build_system_prompt("English")
    assert "copy the marker, never write the name" in p
    assert "only about the narrator's own pronoun" in p
    assert "keep their roles" in p
    assert "slapping sound of sex" in p
