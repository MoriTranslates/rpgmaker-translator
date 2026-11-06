"""Batch-run error handling: one system message, blocked replies, fatal errors.

No network: the OpenAI SDK and the chat layer are replaced with fakes.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import openai  # noqa: E402
import pytest  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient  # noqa: E402
from translator.project_model import TranslationEntry  # noqa: E402

pytest.importorskip("PyQt6")

HISTORY = [("昨日は雨だった", "It rained yesterday."), ("そうか", "I see.")]
PAYLOAD = [("Line1", "おはよう", "", "dialog"), ("Line2", "こんにちは", "", "dialog")]


def _capture(client, monkeypatch, reply):
    """Replace client._chat; record each messages list; return `reply`."""
    seen = []

    def fake_chat(*, messages, **kwargs):
        seen.append(messages)
        return {"message": {"content": reply}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    return seen


def _assert_one_system_first(messages):
    systems = [i for i, m in enumerate(messages) if m["role"] == "system"]
    assert systems == [0], f"system messages at {systems}"


@pytest.mark.parametrize("cloud", [False, True])
def test_translate_batch_with_history_has_one_system_message(monkeypatch, cloud):
    c = AIClient()
    if cloud:
        c.provider = "Google Gemini"
    seen = _capture(c, monkeypatch,
                    json.dumps({"Line1": "Good morning", "Line2": "Hello"}))
    c.translate_batch(PAYLOAD, history=HISTORY)
    assert len(seen) == 1
    _assert_one_system_first(seen[0])
    assert "It rained yesterday." in seen[0][0]["content"]
    assert "Translation History" in seen[0][0]["content"]


def test_translate_batch_without_history_has_one_system_message(monkeypatch):
    c = AIClient()
    seen = _capture(c, monkeypatch,
                    json.dumps({"Line1": "Good morning", "Line2": "Hello"}))
    c.translate_batch(PAYLOAD)
    _assert_one_system_first(seen[0])


def test_other_builders_have_at_most_one_system_message(monkeypatch):
    c = AIClient()
    seen = _capture(c, monkeypatch, "Good morning")
    c.translate("おはよう", history=HISTORY)
    c.polish("Good morning")
    seen_json = _capture(c, monkeypatch, json.dumps({"Line1": "Hi", "Line2": "Yo"}))
    c.polish_batch([("Line1", "Hi"), ("Line2", "Yo")])
    for messages in seen + seen_json:
        _assert_one_system_first(messages)


# ── _chat_openai with blocked / odd responses ──────────────────────────

def _openai_client(monkeypatch, response):
    fake_sdk_client = SimpleNamespace(
        with_options=lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **p: response))))
    c = AIClient()
    c.provider = "Google Gemini"
    c.api_key = "x"
    monkeypatch.setattr(c, "_get_openai_client", lambda mod: fake_sdk_client)
    return c


def _resp(choices):
    return SimpleNamespace(choices=choices, usage=None)


def _chat(c):
    return c._chat_openai(messages=[{"role": "user", "content": "hi"}])


def test_message_none_raises_content_blocked(monkeypatch):
    c = _openai_client(monkeypatch, _resp(
        [SimpleNamespace(message=None, finish_reason="stop")]))
    with pytest.raises(ai_client.ContentBlocked, match="content filter"):
        _chat(c)


def test_content_filter_finish_reason_raises_content_blocked(monkeypatch):
    c = _openai_client(monkeypatch, _resp([SimpleNamespace(
        message=SimpleNamespace(content=None), finish_reason="content_filter")]))
    with pytest.raises(ai_client.ContentBlocked, match="Google Gemini"):
        _chat(c)


def test_content_blocked_is_a_value_error():
    assert issubclass(ai_client.ContentBlocked, ValueError)


def test_empty_choices_does_not_crash(monkeypatch):
    c = _openai_client(monkeypatch, _resp([]))
    assert _chat(c) == {"message": {"content": ""}}


def test_empty_content_with_stop_is_not_blocked(monkeypatch):
    c = _openai_client(monkeypatch, _resp([SimpleNamespace(
        message=SimpleNamespace(content=""), finish_reason="stop")]))
    assert _chat(c)["message"]["content"] == ""


def test_translate_batch_on_blocked_reply_raises_value_error(monkeypatch):
    c = _openai_client(monkeypatch, _resp(
        [SimpleNamespace(message=None, finish_reason="content_filter")]))
    with pytest.raises(ValueError, match="content filter"):
        c.translate_batch(PAYLOAD)


def test_invalid_key_raises_fatal_invalid_api_key(monkeypatch):
    def boom(**p):
        raise openai.AuthenticationError(
            "bad key", response=SimpleNamespace(
                status_code=401, headers={}, request=None), body=None)
    sdk = SimpleNamespace(with_options=lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=boom))))
    c = AIClient()
    c.provider = "Google Gemini"
    monkeypatch.setattr(c, "_get_openai_client", lambda mod: sdk)
    with pytest.raises(ai_client.InvalidApiKey):
        _chat(c)


def test_ollama_404_raises_model_not_found(monkeypatch):
    import fake_ai
    llm = fake_ai.install(monkeypatch)
    llm.post = lambda url, json=None, timeout=None, **kw: fake_ai.FakeResponse(
        {"error": "nope"}, status_code=404)
    monkeypatch.setattr(ai_client.requests, "post", llm.post)
    c = AIClient()
    with pytest.raises(ai_client.ModelNotFound):
        c._chat(messages=[{"role": "user", "content": "hi"}])


# ── Workers ──────────────────────────────────────────────────────────

def _entries(n):
    return [TranslationEntry(id=f"e{i}", file="Map001.json", field="dialog",
                             original=f"日本語{i}") for i in range(n)]


class _Client:
    """Stub client: scripted translate_batch / translate."""

    def __init__(self, batch_fn, single_fn):
        self.batch_fn = batch_fn
        self.single_fn = single_fn
        self.batch_calls = 0
        self.single_calls = 0
        import threading
        self.cancel_event = threading.Event()

    def translate_batch(self, payload, history=None):
        self.batch_calls += 1
        return self.batch_fn(payload)

    def translate(self, text, context="", field="", history=None, **kw):
        self.single_calls += 1
        return self.single_fn(text)


def _run_worker(client, entries, batch_size=4):
    from translator.translation_engine import BatchTranslationWorker
    w = BatchTranslationWorker(client, entries, batch_size=batch_size)
    done, errors, fatal = {}, {}, []
    w.entry_done.connect(lambda eid, t: done.__setitem__(eid, t))
    w.error.connect(lambda eid, m: errors.__setitem__(eid, m))
    w.fatal_error.connect(fatal.append)
    w.run()
    return done, errors, fatal


def test_blocked_batch_falls_back_and_blocked_line_gets_error():
    def batch(payload):
        raise ai_client.ContentBlocked("Blocked by Google Gemini's content filter")

    def single(text):
        if text == "日本語1":
            raise ai_client.ContentBlocked("Blocked by Google Gemini's content filter")
        return "ok " + text

    client = _Client(batch, single)
    entries = _entries(3)
    done, errors, fatal = _run_worker(client, entries)
    assert set(done) == {"e0", "e2"}
    assert "content filter" in errors["e1"]
    assert "e1" not in done and fatal == []


def test_partly_blocked_batch_keeps_returned_keys():
    client = _Client(
        lambda payload: {"Line1": "one", "Line2": "two"},
        lambda text: (_ for _ in ()).throw(
            ai_client.ContentBlocked("Blocked by Google Gemini's content filter")))
    done, errors, fatal = _run_worker(client, _entries(4))
    assert done == {"e0": "one", "e1": "two"}
    assert set(errors) == {"e2", "e3"}


def test_empty_translation_is_never_stored():
    client = _Client(lambda payload: {"Line1": "", "Line2": "two"},
                     lambda text: "single")
    done, errors, fatal = _run_worker(client, _entries(2))
    assert done["e1"] == "two"
    assert done["e0"] == "single"  # empty batch value fell back to a real line


@pytest.mark.parametrize("exc", [
    lambda: ai_client.ModelNotFound("Model 'x' not found. Run: ollama pull x"),
    lambda: ai_client.InvalidApiKey("Invalid API key for Google Gemini."),
])
def test_worker_stops_on_fatal_error(exc):
    def batch(payload):
        raise exc()
    client = _Client(batch, lambda t: "x")
    done, errors, fatal = _run_worker(client, _entries(12), batch_size=4)
    assert client.batch_calls == 1
    assert client.single_calls == 0
    assert len(fatal) == 1
    assert done == {} and errors == {}


@pytest.mark.parametrize("exc", [
    lambda: ai_client.ModelNotFound("Model 'x' not found."),
    lambda: ai_client.InvalidApiKey("Invalid API key for Google Gemini."),
])
def test_engine_stops_once_and_emits_message_once(qapp, monkeypatch, exc):
    import time
    from translator.translation_engine import TranslationEngine
    c = AIClient()
    calls = []

    def boom(payload, history=None):
        calls.append(1)
        raise exc()

    monkeypatch.setattr(c, "translate_batch", boom)
    monkeypatch.setattr(c, "translate",
                        lambda *a, **k: calls.append(1) or "x")
    engine = TranslationEngine(c)
    engine.batch_size = 4
    engine.num_workers = 1
    downs, finished, errors = [], [], []
    engine.server_down.connect(downs.append)
    engine.finished.connect(lambda: finished.append(1))
    engine.error.connect(lambda eid, m: errors.append(eid))
    engine.translate_batch(_entries(12))
    deadline = time.monotonic() + 20
    while not finished and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert len(calls) == 1
    assert len(downs) == 1
    assert errors == []
