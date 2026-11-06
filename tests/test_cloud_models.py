"""Cloud model lists and per-model request settings.

No network: ``openai.OpenAI`` is replaced with a fake, and the chat call is
captured instead of sent.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import openai  # noqa: E402
import pytest  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient, PROVIDER_MODELS  # noqa: E402

# Shut down, or shutting down soon, per each provider's deprecation page
# (checked 2026-11-05). None of these may be offered as a suggestion.
DEAD_OR_DYING = {
    "gemini-2.0-flash", "gemini-2.0-flash-lite",
    "gemini-2.5-flash", "gemini-2.5-pro",
    "gpt-4.1-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-5",
    "deepseek-chat", "deepseek-reasoner",
    "claude-sonnet-4-5-20250929",
}


def test_no_dead_or_dying_model_in_fallback_lists():
    offered = {m for models in PROVIDER_MODELS.values() for m in models}
    assert not offered & DEAD_OR_DYING


def test_every_cloud_provider_has_two_to_four_fallback_models():
    for provider in ("OpenAI", "Google Gemini", "DeepSeek", "Anthropic"):
        assert 2 <= len(PROVIDER_MODELS[provider]) <= 4, provider


def test_unpriced_model_shows_unknown_cost_not_zero():
    c = AIClient()
    c.provider = "OpenAI"
    c.model = "some-model-without-a-price"
    c.session_input_tokens = 1000
    c.session_output_tokens = 1000
    text = c.format_session_cost()
    assert "$0.0000" not in text
    assert "unknown" in text.lower()


# ── Live list filtering ──────────────────────────────────────────────

def test_filter_drops_non_chat_models_and_strips_prefix():
    ids = [
        "models/gemini-3.8-flash", "models/gemini-embedding-2",
        "models/gemini-3.8-flash-tts", "models/imagen-4.0-generate-001",
        "gpt-6-luna", "text-embedding-3-small", "tts-1", "whisper-1",
        "dall-e-3", "gpt-image-1", "omni-moderation-latest",
        "gpt-4o-audio-preview", "gpt-realtime", "gpt-4o-search-preview",
        "gpt-4o-mini-transcribe", "gemini-3.8-live", "veo-3.0-generate-001",
        "deep-research-preview-04-2026", "deepseek-flash",
        "claude-haiku-5-5",
    ]
    assert ai_client.filter_chat_models(ids) == [
        "gemini-3.8-flash", "gpt-6-luna", "deepseek-flash", "claude-haiku-5-5",
    ]


def test_filter_drops_duplicates_and_blanks():
    ids = ["models/gemini-3.8-flash", "gemini-3.8-flash", "", "  ", None]
    assert ai_client.filter_chat_models(ids) == ["gemini-3.8-flash"]


class _FakeOpenAI:
    """Stands in for ``openai.OpenAI``; ``result`` is a list or an exception."""
    result: object = []
    calls: list = []

    def __init__(self, **kwargs):
        _FakeOpenAI.calls.append(kwargs)
        self.models = SimpleNamespace(list=self._list)

    def _list(self):
        if isinstance(_FakeOpenAI.result, BaseException):
            raise _FakeOpenAI.result
        return [SimpleNamespace(id=i) for i in _FakeOpenAI.result]


@pytest.fixture
def fake_openai(monkeypatch):
    _FakeOpenAI.result = []
    _FakeOpenAI.calls = []
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return _FakeOpenAI


def test_fetch_uses_live_list_with_suggestions_first(fake_openai):
    first = PROVIDER_MODELS["Google Gemini"][0]
    fake_openai.result = ["models/zz-new-model", f"models/{first}",
                          "models/gemini-embedding-2"]
    models, live = ai_client.fetch_cloud_models("Google Gemini", "key")
    assert live is True
    assert models == [first, "zz-new-model"]
    assert fake_openai.calls[0]["base_url"] == ai_client.PROVIDER_URLS["Google Gemini"]


def test_fetch_falls_back_when_list_call_raises(fake_openai):
    fake_openai.result = openai.APIConnectionError(request=None)
    models, live = ai_client.fetch_cloud_models("OpenAI", "key")
    assert live is False
    assert models == PROVIDER_MODELS["OpenAI"]


def test_fetch_falls_back_when_only_non_chat_models(fake_openai):
    fake_openai.result = ["text-embedding-3-small", "tts-1", "dall-e-3"]
    models, live = ai_client.fetch_cloud_models("OpenAI", "key")
    assert live is False
    assert models == PROVIDER_MODELS["OpenAI"]


def test_fetch_without_key_makes_no_call(fake_openai):
    models, live = ai_client.fetch_cloud_models("Anthropic", "")
    assert (models, live) == (PROVIDER_MODELS["Anthropic"], False)
    assert fake_openai.calls == []


def test_fetch_result_is_a_copy(fake_openai):
    fake_openai.result = RuntimeError("boom")
    models, _ = ai_client.fetch_cloud_models("OpenAI", "key")
    models.append("junk")
    assert "junk" not in PROVIDER_MODELS["OpenAI"]


# ── temperature / max_tokens per provider and model ─────────────────

class _Completions:
    def __init__(self):
        self.params = None

    def create(self, **params):
        self.params = params
        msg = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def _sent_params(provider, model, **kwargs):
    c = AIClient()
    c.provider = provider
    c.model = model
    c.api_key = "k"
    completions = _Completions()
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    fake.with_options = lambda **kw: fake
    c._get_openai_client = lambda _openai: fake
    c._chat_openai(messages=[{"role": "user", "content": "hi"}],
                   options={"temperature": 0, "num_predict": 77, "seed": 1},
                   **kwargs)
    return completions.params


@pytest.mark.parametrize("provider,model,takes_temperature", [
    ("OpenAI", "gpt-4.1-mini", True),
    ("OpenAI", "gpt-6-luna", False),
    ("OpenAI", "gpt-5.6-sol", False),
    ("OpenAI", "o4-mini", False),
    ("Anthropic", "claude-sonnet-5-5", False),
    ("Google Gemini", "gemini-3.8-flash", True),
    ("DeepSeek", "deepseek-flash", True),
    ("Custom", "qwen3.5:9b", True),
    ("Custom", "gpt-5.6-sol", False),
])
def test_temperature_only_where_accepted(provider, model, takes_temperature):
    params = _sent_params(provider, model)
    if takes_temperature:
        assert params["temperature"] == 0
        assert params["max_tokens"] == 77
        assert "max_completion_tokens" not in params
    else:
        assert "temperature" not in params
        assert "max_tokens" not in params
        assert "frequency_penalty" not in params
        assert params["max_completion_tokens"] == 77


def test_ollama_model_accepts_temperature():
    # Ollama never goes through the OpenAI path, but the rule must allow it.
    assert ai_client._accepts_sampling_params("Ollama (Local)", "qwen3.5:9b")


def test_anthropic_never_gets_json_object_mode():
    # Claude's OpenAI layer returns 400 for response_format json_object.
    params = _sent_params("Anthropic", "claude-haiku-5-5", format="json")
    assert "response_format" not in params


def test_anthropic_keeps_strict_json_schema():
    schema = {"name": "x", "strict": True, "schema": {"type": "object"}}
    params = _sent_params("Anthropic", "claude-haiku-5-5", format="json",
                          json_schema=schema)
    assert params["response_format"]["type"] == "json_schema"


# ── Settings dialog ─────────────────────────────────────────────────

@pytest.fixture
def dialog_for(qapp, monkeypatch):
    from translator.widgets import settings_dialog as sd
    started = []
    monkeypatch.setattr(sd.SettingsDialog, "_start_cloud_model_fetch",
                        lambda self: started.append(
                            self.provider_combo.currentText()))
    made = []

    def make(provider, model, api_key=""):
        c = AIClient()
        c.provider = provider
        c.model = model
        c.api_key = api_key
        dlg = sd.SettingsDialog(c)
        made.append(dlg)
        return dlg

    make.started = started
    yield make
    for dlg in made:
        dlg.deleteLater()


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_saved_model_kept_when_not_in_fallback_list(dialog_for):
    dlg = dialog_for("OpenAI", "gpt-4o")
    assert dlg.model_combo.currentText() == "gpt-4o"
    assert "gpt-4o" in _items(dlg.model_combo)


def test_saved_model_kept_after_live_list_arrives(dialog_for):
    dlg = dialog_for("OpenAI", "my-fine-tune", api_key="k")
    dlg._on_cloud_models_fetched("OpenAI", (["gpt-6-luna", "gpt-6-sol"], True))
    assert dlg.model_combo.currentText() == "my-fine-tune"
    assert {"gpt-6-luna", "gpt-6-sol"} <= set(_items(dlg.model_combo))


def test_live_fetch_only_with_api_key(dialog_for):
    dialog_for("OpenAI", "gpt-6-luna")
    assert dialog_for.started == []
    dialog_for("OpenAI", "gpt-6-luna", api_key="k")
    assert dialog_for.started == ["OpenAI"]


def test_failed_live_fetch_keeps_fallback_list(dialog_for):
    dlg = dialog_for("Google Gemini", "gemini-3.8-flash", api_key="k")
    dlg._on_cloud_models_fetched("Google Gemini", None)  # worker raised
    assert set(PROVIDER_MODELS["Google Gemini"]) <= set(_items(dlg.model_combo))
    assert dlg.model_combo.currentText() == "gemini-3.8-flash"


def test_live_list_for_other_provider_is_ignored(dialog_for):
    dlg = dialog_for("Anthropic", "claude-haiku-5-5", api_key="k")
    before = _items(dlg.model_combo)
    dlg._on_cloud_models_fetched("OpenAI", (["gpt-6-luna"], True))
    assert _items(dlg.model_combo) == before


def test_switching_provider_does_not_carry_other_providers_model(dialog_for):
    dlg = dialog_for("OpenAI", "gpt-6-luna")
    dlg.provider_combo.setCurrentText("Anthropic")
    assert dlg.model_combo.currentText() == PROVIDER_MODELS["Anthropic"][0]
    dlg.provider_combo.setCurrentText("OpenAI")
    assert dlg.model_combo.currentText() == "gpt-6-luna"


def test_worker_thread_fills_live_list(qapp, monkeypatch):
    from PyQt6.QtCore import QCoreApplication
    from translator.widgets import settings_dialog as sd
    calls = []

    def fake_fetch(provider, api_key):
        calls.append((provider, api_key))
        return ["gpt-6-luna", "gpt-zz-new"], True

    monkeypatch.setattr(sd, "fetch_cloud_models", fake_fetch)
    c = AIClient()
    c.provider, c.model, c.api_key = "OpenAI", "gpt-6-luna", "k"
    dlg = sd.SettingsDialog(c)
    try:
        dlg._cloud_model_worker.wait(5000)
        QCoreApplication.processEvents()
        assert calls == [("OpenAI", "k")]
        assert "gpt-zz-new" in _items(dlg.model_combo)
        assert dlg.model_combo.currentText() == "gpt-6-luna"
    finally:
        dlg.deleteLater()
