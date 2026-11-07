"""Cloud client fixes: fatal model errors, cut-off replies, OpenRouter
variant typos and content-filter reasons.

No network: the OpenAI SDK and requests.post are replaced with fakes.
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from translator import ai_client
from translator.ai_client import AIClient

MSGS = [{"role": "user", "content": "hi"}]
PAYLOAD = [("Line1", "おはよう", "", "dialog"), ("Line2", "こんにちは", "", "dialog")]


# ── helpers for the OpenAI-compatible path ───────────────────────────

class _SDK:
    """Fake OpenAI client: create() returns or raises `outcome`."""
    outcome = None
    sent: list = []

    def __init__(self, **kwargs):
        def create(**p):
            _SDK.sent.append(p["model"])
            if isinstance(_SDK.outcome, BaseException):
                raise _SDK.outcome
            return _SDK.outcome
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def with_options(self, **kw):
        return self


def _client(monkeypatch, outcome, provider="OpenRouter",
            model="deepseek/deepseek-chat"):
    _SDK.outcome = outcome
    _SDK.sent = []
    monkeypatch.setattr(openai, "OpenAI", _SDK)
    c = AIClient()
    c.provider = provider
    c.model = model
    c.api_key = "test-key"
    return c


def _status(cls, code, message, body=None):
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    resp = httpx.Response(code, request=req, json=body or {"error": {"message": message}})
    return cls(message, response=resp, body=body)


def _ok(text="ok", finish="stop", **extra):
    choice = SimpleNamespace(message=SimpleNamespace(content=text),
                             finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=None, **extra)


# ── 2. missing / blocked model stops the run ─────────────────────────

@pytest.mark.parametrize("cls,code,msg", [
    (openai.NotFoundError, 404,
     "The model `gpt-9` does not exist or you do not have access to it."),
    (openai.NotFoundError, 404,
     "models/gemini-9-pro is not found for API version v1beta, or is not "
     "supported for generateContent."),
    (openai.NotFoundError, 404,   # dotted names: "2.5" must not end the match
     "models/gemini-2.5-flash-old is not found for API version v1beta."),
    (openai.NotFoundError, 404,
     "The model `gpt-4.1-old` does not exist or you do not have access to it."),
    (openai.NotFoundError, 404, "No endpoints found for vendor/gone-model."),
    (openai.BadRequestError, 400, "vendor/typo-model is not a valid model ID"),
    (openai.PermissionDeniedError, 403,
     "Your project does not have access to model gpt-9-pro"),
])
def test_missing_or_forbidden_model_is_fatal(monkeypatch, cls, code, msg):
    c = _client(monkeypatch, _status(cls, code, msg))
    with pytest.raises(ai_client.ModelNotFound):
        c._chat_openai(messages=MSGS)


def test_openrouter_data_policy_block_is_fatal_and_points_to_privacy(monkeypatch):
    c = _client(monkeypatch, _status(
        openai.NotFoundError, 404,
        "No endpoints found matching your data policy (Free model publication). "
        "Configure: https://openrouter.ai/settings/privacy"),
        model="vendor/some-model:free")
    with pytest.raises(ai_client.ModelNotFound, match="openrouter.ai/settings/privacy"):
        c._chat_openai(messages=MSGS)


@pytest.mark.parametrize("body", [
    {"error": {"code": "model_not_found", "message": "nope"}},
    {"type": "error", "error": {"type": "not_found_error",
                                "message": "model: claude-old-1"}},
])
def test_model_error_in_body_only_is_fatal(monkeypatch, body):
    c = _client(monkeypatch, _status(openai.NotFoundError, 404, "Error code: 404", body))
    with pytest.raises(ai_client.ModelNotFound):
        c._chat_openai(messages=MSGS)


@pytest.mark.parametrize("cls,code,msg", [
    (openai.BadRequestError, 400,
     "This model's maximum context length is 8192 tokens. However, your "
     "messages resulted in 9000 tokens."),
    (openai.BadRequestError, 400, "Invalid JSON payload received."),
    (openai.NotFoundError, 404,
     "No endpoints found that can handle the requested parameters."),
    (openai.InternalServerError, 500, "model not found"),
    (openai.BadRequestError, 400,
     "The model does not support a context length of 300000 tokens"),
])
def test_per_request_errors_are_not_fatal(monkeypatch, cls, code, msg):
    c = _client(monkeypatch, _status(cls, code, msg))
    with pytest.raises(ConnectionError) as ei:
        c._chat_openai(messages=MSGS)
    assert not isinstance(ei.value, ai_client.FatalProviderError)


# ── 3. cut-off and partly filtered replies are never saved ───────────

def test_length_cut_reply_is_rejected(monkeypatch):
    c = _client(monkeypatch, _ok("Half a transla", finish="length"))
    with pytest.raises(ValueError, match="cut off"):
        c._chat_openai(messages=MSGS)


def test_partly_filtered_reply_is_blocked(monkeypatch):
    c = _client(monkeypatch, _ok("Some text then", finish="content_filter"))
    with pytest.raises(ai_client.ContentBlocked):
        c._chat_openai(messages=MSGS)


def test_cut_reply_is_not_a_fatal_or_server_error():
    assert issubclass(ai_client.ReplyTruncated, ValueError)
    assert not issubclass(ai_client.ReplyTruncated, ConnectionError)


def test_translate_single_line_rejects_cut_reply(monkeypatch):
    c = _client(monkeypatch, _ok("Half a transla", finish="length"))
    with pytest.raises(ValueError, match="cut off"):
        c.translate("おはよう")


def test_translate_batch_rejects_cut_reply(monkeypatch):
    c = _client(monkeypatch, _ok(json.dumps({"Line1": "Good morning"}) , finish="length"))
    with pytest.raises(ValueError, match="cut off"):
        c.translate_batch(PAYLOAD)


def _ollama_reply(monkeypatch, data):
    monkeypatch.setattr(ai_client.requests, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, text=json.dumps(data), json=lambda: data))


def test_ollama_length_done_reason_is_rejected(monkeypatch):
    _ollama_reply(monkeypatch, {"message": {"content": "Half a"},
                                "done": True, "done_reason": "length"})
    c = AIClient()
    with pytest.raises(ValueError, match="cut off"):
        c._chat(messages=MSGS)
    with pytest.raises(ValueError, match="cut off"):
        c.translate("おはよう")


def test_ollama_stop_done_reason_is_fine(monkeypatch):
    _ollama_reply(monkeypatch, {"message": {"content": "Good morning"},
                                "done": True, "done_reason": "stop"})
    assert AIClient().translate("おはよう") == "Good morning"


# ── 4. OpenRouter variant suffix typos ───────────────────────────────

@pytest.mark.parametrize("model", [
    "vendor/model:fre", "vendor/model:Free", "vendor/model:FREE",
    "vendor/model:frees", "vendor/model:nitr", "vendor/model:", "vendor/model:free ",
])
def test_openrouter_unknown_variant_stops_before_any_request(monkeypatch, model):
    c = _client(monkeypatch, _ok(), model=model)
    with pytest.raises(ai_client.FatalProviderError, match="mistyped"):
        c._chat_openai(messages=MSGS)
    assert _SDK.sent == []


@pytest.mark.parametrize("model", [
    "vendor/model", "vendor/model:free", "vendor/model:nitro", "vendor/model:floor",
    "vendor/model:exacto", "vendor/model:online", "vendor/model:thinking",
    "vendor/model:extended", "vendor/model:batch",
])
def test_openrouter_known_variants_are_sent(monkeypatch, model):
    c = _client(monkeypatch, _ok(model=model), model=model)
    assert c._chat_openai(messages=MSGS)["message"]["content"] == "ok"
    assert _SDK.sent == [model]


def test_variant_rule_is_openrouter_only(monkeypatch):
    c = _client(monkeypatch, _ok(), provider="Custom", model="qwen3:fre")
    assert c._chat_openai(messages=MSGS)["message"]["content"] == "ok"


# ── 5. why the content filter blocked ────────────────────────────────

def test_block_reason_from_prompt_feedback(monkeypatch):
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=None, finish_reason="content_filter")],
        usage=None, prompt_feedback={"block_reason": "PROHIBITED_CONTENT"})
    c = _client(monkeypatch, resp, provider="Google Gemini", model="gemini-3-flash")
    with pytest.raises(ai_client.ContentBlocked,
                       match="Google Gemini's content filter: PROHIBITED_CONTENT"):
        c._chat_openai(messages=MSGS)


def test_block_reason_from_real_sdk_object_extra_field(monkeypatch):
    from openai.types.chat import ChatCompletion
    resp = ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "content_filter",
                     "native_finish_reason": "SAFETY",
                     "message": {"role": "assistant", "content": None}}],
    })
    c = _client(monkeypatch, resp, model="m")
    with pytest.raises(ai_client.ContentBlocked, match="SAFETY"):
        c._chat_openai(messages=MSGS)


def test_block_reason_from_safety_ratings(monkeypatch):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=""), finish_reason="content_filter",
        safety_ratings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "probability": "LOW"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "blocked": True},
        ])
    c = _client(monkeypatch, SimpleNamespace(choices=[choice], usage=None),
                provider="Google Gemini", model="gemini-3-flash")
    with pytest.raises(ai_client.ContentBlocked) as ei:
        c._chat_openai(messages=MSGS)
    assert "SEXUALLY_EXPLICIT" in str(ei.value)
    assert "HARASSMENT" not in str(ei.value)


def test_block_reason_from_error_body(monkeypatch):
    body = {"error": {"message": "The prompt was blocked.",
                      "details": [{"block_reason": "PROHIBITED_CONTENT"}]}}
    c = _client(monkeypatch, _status(openai.BadRequestError, 400,
                                     "The prompt was blocked.", body),
                provider="Google Gemini", model="gemini-3-flash")
    with pytest.raises(ai_client.ContentBlocked, match="PROHIBITED_CONTENT"):
        c._chat_openai(messages=MSGS)


def test_block_without_reason_says_so(monkeypatch):
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=None, finish_reason="content_filter")],
        usage=None)
    c = _client(monkeypatch, resp, provider="Google Gemini", model="gemini-3-flash")
    with pytest.raises(ai_client.ContentBlocked, match="reason not given"):
        c._chat_openai(messages=MSGS)


def test_batch_block_log_line_names_the_reason(monkeypatch, caplog):
    from translator.project_model import TranslationEntry
    from translator.translation_engine import BatchTranslationWorker
    exc = ai_client.ContentBlocked(
        "Blocked by Google Gemini's content filter: PROHIBITED_CONTENT")

    class _C:
        import threading
        cancel_event = threading.Event()

        def translate_batch(self, payload, history=None):
            raise exc

        def translate(self, **kw):
            raise exc

    entries = [TranslationEntry(id=f"e{i}", file="Map001.json", field="dialog",
                                original=f"日本語{i}") for i in range(3)]
    w = BatchTranslationWorker(_C(), entries, batch_size=3)
    with caplog.at_level("WARNING"):
        w.run()
    assert ("Batch of 3 blocked (Blocked by Google Gemini's content filter: "
            "PROHIBITED_CONTENT)") in caplog.text
