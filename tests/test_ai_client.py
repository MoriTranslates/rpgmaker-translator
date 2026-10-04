"""AIClient end-to-end through the fake HTTP layer.

The fake LLM only sees what AIClient sends over the wire, so these tests
exercise the real placeholder extraction / restoration, prompt building and
batch JSON parsing.
"""

import re

import pytest

import fake_ai
from conftest import FAKE_MV_GAME
from translator import CONTROL_CODE_RE
from translator.ai_client import AIClient

DIALOG = ("\\C[2]\\N[2]\\C[0]、おはよう！\n"
          "今日は\\V[3]ゴールドを持っているよ\\.\n"
          "一緒に冒険に行こう。")

RAW_CODE_RE = re.compile(r"\\[A-Za-z]+\[|\\[.|!><^$]")


def codes(text):
    return CONTROL_CODE_RE.findall(text)


@pytest.fixture
def client(fake_llm):
    return AIClient()


def test_translate_restores_control_codes(client, fake_llm):
    out = client.translate(DIALOG, field="dialog")
    assert out == ("\\C[2]\\N[2]\\C[0], Good morning!\n"
                   "Today I have \\V[3] gold with me\\.\n"
                   "Let's go on an adventure together.")
    assert codes(out) == codes(DIALOG)
    # The LLM saw opaque placeholders, never the raw engine codes
    sent = fake_llm.user_messages()[-1]
    assert "«CODE1»«CODE2»«CODE3»" in sent
    assert "«CODE5»" in sent
    payload_part = sent.split("Translate this:\n", 1)[1]
    assert not RAW_CODE_RE.search(payload_part)


def test_request_shape(client, fake_llm):
    client.translate("こんにちは", field="dialog")
    call = fake_llm.chat_calls()[-1]
    assert call["model"] == client.model
    assert call["stream"] is False
    assert call["options"]["temperature"] == 0
    assert call["options"]["seed"] == 42
    assert call["messages"][0]["role"] == "system"
    assert "Content type: dialogue line" in call["messages"][-1]["content"]


def test_translate_batch_restores_codes_per_entry(client, fake_llm):
    items = [
        ("Line1", DIALOG, "[Speaker: アリス]", "dialog"),
        ("Line2", "\\N[1]、ありがとう！", "", "dialog"),
        ("Line3", "はい", "", "choice"),
    ]
    out = client.translate_batch(items)
    assert set(out) == {"Line1", "Line2", "Line3"}
    assert codes(out["Line1"]) == codes(DIALOG)
    assert out["Line2"] == "\\N[1], Thank you!"
    assert out["Line3"] == "Yes"
    call = fake_llm.chat_calls()[-1]
    assert call["format"] == "json"
    # Exactly one request for the whole batch
    assert len(fake_llm.chat_calls()) == 1


def test_batch_accepts_markdown_fenced_json(monkeypatch):
    fake_ai.install(monkeypatch, batch_mode="fenced")
    out = AIClient().translate_batch([("Line1", "はい", "", "choice")])
    assert out == {"Line1": "Yes"}


def test_batch_garbage_raises_value_error(monkeypatch):
    fake_ai.install(monkeypatch, batch_mode="garbage")
    with pytest.raises(ValueError):
        AIClient().translate_batch([("Line1", "はい", "", "choice")])


def test_japanese_leftover_triggers_retry(monkeypatch):
    """First answer still has Japanese -> client retries with a fix prompt."""
    seen = []

    def lazy_then_good(text):
        seen.append(text)
        if len(seen) == 1:
            return "Hello 世界"          # leaves Japanese
        return "Hello world"

    llm = fake_ai.install(monkeypatch, translate_fn=lazy_then_good)
    out = AIClient().translate("こんにちは世界")
    assert out == "Hello world"
    assert len(llm.chat_calls()) == 2
    assert "still contains Japanese" in llm.user_messages()[-1]


def test_empty_response_is_an_error(monkeypatch):
    fake_ai.install(monkeypatch, translate_fn=lambda t: "")
    # Empty/garbage LLM output is a bad response, not a connection error
    with pytest.raises(ValueError):
        AIClient().translate("こんにちは")


def test_http_404_reports_missing_model(monkeypatch):
    from translator import ai_client

    class Resp:
        status_code = 404
        text = "model not found"

    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(ConnectionError, match="not found"):
        AIClient(model="nope:1b").translate("こんにちは")


def test_name_translation_and_batch(client):
    assert client.translate_name("アリス", "character name") == "Alice"
    assert client.translate_names_batch(
        [("a", "アリス", "name"), ("b", "ボブ", "name")]) == {"a": "Alice", "b": "Bob"}


def test_polish_preserves_codes(client):
    assert client.polish("hello \\C[2]world\\C[0]") == "Hello \\C[2]world\\C[0]"
    assert client.polish_batch([("Line1", "hi \\N[1]")]) == {"Line1": "Hi \\N[1]"}


def test_pronoun_and_speaker_hints(client, fake_llm):
    client.actor_genders = {1: "female", 2: "male"}
    client.actor_names = {1: "アリス", 2: "ボブ"}
    client.translate(DIALOG, context="[Speaker: アリス]", field="dialog")
    sent = fake_llm.user_messages()[-1]
    assert "«CODE2» = name of ボブ (he/him)" in sent
    assert "Speaker: アリス is FEMALE" in sent


def test_glossary_only_injects_matching_terms(client, fake_llm):
    client.glossary = {"村人": "Villager", "魔王": "Demon King"}
    client.translate("村人です", field="dialog")
    sent = fake_llm.user_messages()[-1]
    assert "村人 → villager" in sent
    assert "魔王" not in sent


def test_history_sent_as_message_pairs(client, fake_llm):
    client.translate("はい", history=[("こんにちは", "Hello")])
    msgs = fake_llm.chat_calls()[-1]["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user"]
    assert msgs[2]["content"] == "Hello"


def test_list_models_and_availability(client):
    assert client.list_models() == ["qwen3.5:9b"]
    assert client.is_available() is True


def test_translate_every_fixture_entry_keeps_codes(client):
    """Full fixture through the single-entry path: codes always survive."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    entries = RPGMakerMVParser().load_project(FAKE_MV_GAME)
    for e in entries:
        out = client.translate(e.original, context=e.context, field=e.field)
        assert codes(out) == codes(e.original), e.id
        assert out.count("\n") == e.original.count("\n"), e.id
        assert "«CODE" not in out, e.id


def test_extract_restore_roundtrip_unit(client):
    text = "\\C[2]A\\C[0] B \\V[10]%1<br>\\{x\\}"
    clean, mapping = client._extract_codes(text)
    assert "\\" not in clean and "<br>" not in clean
    assert client._restore_codes(clean, mapping) == text


def test_jp_brackets_converted(client):
    assert client._convert_jp_brackets("「a」『b』【c】（d）") == '"a""b"[c](d)'


def test_single_and_batch_paths_agree(client):
    single = client.translate("「こんにちは」", field="dialog")
    batch = client.translate_batch([("Line1", "「こんにちは」", "", "dialog")])
    assert single == batch["Line1"]
