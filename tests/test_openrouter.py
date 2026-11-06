"""OpenRouter as a named cloud provider.

No network: ``openai.OpenAI`` is replaced with a fake. The point of a named
provider is that its address is fixed, so it never reads or writes the
Ollama address the way "Custom" does.
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
from translator.ai_client import (  # noqa: E402
    CLOUD_DEFAULT_WORKERS, PROVIDER_MODELS, PROVIDER_URLS, PROVIDERS,
    AIClient, engine_batch_workers)

OR = "OpenRouter"
OR_URL = "https://openrouter.ai/api/v1"
OLLAMA_URL = "http://192.168.50.12:11434"


def _client(provider=OR, model="deepseek/deepseek-chat"):
    c = AIClient(base_url=OLLAMA_URL)
    c.provider = provider
    c.model = model
    c.api_key = "sk-or-test"
    return c


def test_registered_as_cloud_provider_with_fixed_url():
    assert OR in PROVIDERS
    assert PROVIDER_URLS[OR] == OR_URL
    assert len(PROVIDER_MODELS[OR]) == 3
    c = _client()
    assert c.is_cloud is True
    assert c._get_openai_base_url() == OR_URL
    assert c.base_url == OLLAMA_URL  # untouched


class _Recorder:
    inits: list = []

    def __init__(self, **kwargs):
        _Recorder.inits.append(kwargs)
        self.models = SimpleNamespace(list=lambda: [])
        msg = SimpleNamespace(content="ok")
        resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=None)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **p: resp))

    def with_options(self, **kw):
        return self


def test_chat_goes_to_openrouter_with_key_not_ollama_url(monkeypatch):
    _Recorder.inits = []
    monkeypatch.setattr(openai, "OpenAI", _Recorder)
    c = _client()
    c._chat_openai(messages=[{"role": "user", "content": "hi"}])
    assert len(_Recorder.inits) == 1
    assert _Recorder.inits[0]["base_url"] == OR_URL
    assert _Recorder.inits[0]["api_key"] == "sk-or-test"


@pytest.mark.parametrize("model,takes", [
    ("deepseek/deepseek-chat", True),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", True),
    ("openai/gpt-5.6-sol", False),
    ("openai/o4-mini", False),
    ("openai/gpt-4o", True),
])
def test_sampling_params_by_model(model, takes):
    assert ai_client._accepts_sampling_params(OR, model) is takes


def test_unpriced_model_has_unknown_cost():
    assert ai_client.get_model_pricing("deepseek/deepseek-chat")["input"] == 0.0
    c = _client()
    c.session_input_tokens = c.session_output_tokens = 1000
    assert "unknown" in c.format_session_cost().lower()


def test_online_batch_and_workers_apply():
    batch, workers = engine_batch_workers(
        {"batch_size": 5, "workers": 1}, True,
        "deepseek/deepseek-chat", 5)
    assert (batch, workers) == (30, CLOUD_DEFAULT_WORKERS)


# ── model list ──────────────────────────────────────────────────────

class _FakeOpenAI:
    result: object = []
    calls: list = []

    def __init__(self, **kwargs):
        _FakeOpenAI.calls.append(kwargs)
        self.models = SimpleNamespace(list=self._list)

    def _list(self):
        if isinstance(_FakeOpenAI.result, BaseException):
            raise _FakeOpenAI.result
        return _FakeOpenAI.result


@pytest.fixture
def fake_openai(monkeypatch):
    _FakeOpenAI.result = []
    _FakeOpenAI.calls = []
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return _FakeOpenAI


def _entry(mid, out=("text",)):
    return SimpleNamespace(id=mid, architecture={"output_modalities": list(out)})


def test_fetch_keeps_text_models_drops_image_only(fake_openai):
    fake_openai.result = [
        _entry("zz/new-model"),
        _entry("black-forest/flux-img", out=("image",)),
        _entry("deepseek/deepseek-chat"),
        _entry("google/multi", out=("text", "image")),
        SimpleNamespace(id="vendor/no-arch-field"),
    ]
    models, live = ai_client.fetch_cloud_models(OR, "key")
    assert live is True
    assert models == ["deepseek/deepseek-chat", "google/multi",
                      "vendor/no-arch-field", "zz/new-model"]
    assert fake_openai.calls[0]["base_url"] == OR_URL


def test_fetch_reads_real_sdk_model_objects(fake_openai):
    # the SDK keeps unknown fields such as "architecture" on the object
    m = openai.types.Model.model_validate({
        "id": "a/img", "created": 0, "object": "model", "owned_by": "x",
        "architecture": {"output_modalities": ["image"]}})
    t = openai.types.Model.model_validate({
        "id": "a/txt", "created": 0, "object": "model", "owned_by": "x",
        "architecture": {"output_modalities": ["text"]}})
    fake_openai.result = [m, t]
    assert ai_client.fetch_cloud_models(OR, "key") == (["a/txt"], True)


def test_fetch_failure_falls_back_to_curated_list(fake_openai):
    fake_openai.result = openai.APIConnectionError(request=None)
    models, live = ai_client.fetch_cloud_models(OR, "key")
    assert live is False
    assert models == PROVIDER_MODELS[OR]


def test_fetch_falls_back_when_nothing_is_text(fake_openai):
    fake_openai.result = [_entry("a/img", out=("image",))]
    assert ai_client.fetch_cloud_models(OR, "key") == (PROVIDER_MODELS[OR], False)


# ── settings dialog ─────────────────────────────────────────────────

@pytest.fixture
def dialog(qapp, monkeypatch):
    from translator.widgets import settings_dialog as sd
    monkeypatch.setattr(sd.SettingsDialog, "_start_cloud_model_fetch",
                        lambda self: None)
    monkeypatch.setattr(sd.SettingsDialog, "_start_model_fetch",
                        lambda self: None)
    monkeypatch.setattr(AIClient, "is_local_server", lambda self: False)
    made = []

    def make(client):
        dlg = sd.SettingsDialog(client)
        made.append(dlg)
        return dlg

    yield make
    for dlg in made:
        dlg.deleteLater()


def test_dialog_shows_key_hides_url_and_fills_models(dialog):
    dlg = dialog(_client(provider="Ollama (Local)", model="qwen3.5:9b"))
    dlg.provider_combo.setCurrentText(OR)
    assert dlg.api_key_edit.isHidden() is False
    assert dlg._url_label.isHidden() is True
    assert dlg.url_edit.isHidden() is True
    assert dlg.refresh_btn.isHidden() is True
    shown = [dlg.model_combo.itemText(i) for i in range(dlg.model_combo.count())]
    assert shown == PROVIDER_MODELS[OR]


def test_switching_ollama_openrouter_ollama_keeps_ollama_url(dialog):
    c = _client(provider="Ollama (Local)", model="qwen3.5:9b")
    dlg = dialog(c)
    dlg.provider_combo.setCurrentText(OR)
    dlg.provider_combo.setCurrentText("Ollama (Local)")
    assert dlg.url_edit.text() == OLLAMA_URL
    dlg.provider_combo.setCurrentText(OR)
    dlg._save()
    assert c.provider == OR
    assert c.base_url == OLLAMA_URL
    assert c._get_openai_base_url() == OR_URL
    # and back to Ollama: requests go to the Ollama address again
    c.provider = "Ollama (Local)"
    assert c.base_url == OLLAMA_URL


def test_model_name_whitespace_stripped_on_save(dialog):
    c = _client()
    dlg = dialog(c)
    dlg.model_combo.setCurrentText("  deepseek/deepseek-chat \t")
    dlg._save()
    assert c.model == "deepseek/deepseek-chat"


def test_model_name_punctuation_is_not_stripped(dialog):
    c = _client()
    dlg = dialog(c)
    dlg.model_combo.setCurrentText("deepseek/deepseek-chat.")
    dlg._save()
    assert c.model == "deepseek/deepseek-chat."


# ── typos and empty balances must stop the run, never cost money ────

class _Served:
    """Fake client whose reply says which model actually served it."""
    served = ""
    sent: list = []

    def __init__(self, **kwargs):
        def create(**p):
            _Served.sent.append(p["model"])
            msg = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                                   usage=None, model=_Served.served)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def with_options(self, **kw):
        return self


@pytest.mark.parametrize("model", [
    "nvidia/nemotron-3-ultra-550b-a55b:free.",   # the real copy-paste typo
    "deepseek/deepseek-chat,",
    "deepseek/deepseek-chat ",
])
def test_mistyped_model_stops_before_any_request(monkeypatch, model):
    _Served.sent = []
    monkeypatch.setattr(openai, "OpenAI", _Served)
    c = _client(model=model)
    with pytest.raises(ai_client.FatalProviderError, match="mistyped"):
        c._chat_openai(messages=[{"role": "user", "content": "hi"}])
    assert _Served.sent == []


def test_free_model_swapped_for_paid_stops_the_run(monkeypatch):
    _Served.sent = []
    _Served.served = "nvidia/nemotron-3-ultra-550b-a55b"   # :free dropped
    monkeypatch.setattr(openai, "OpenAI", _Served)
    c = _client(model="nvidia/nemotron-3-ultra-550b-a55b:free")
    with pytest.raises(ai_client.FatalProviderError, match="paid"):
        c._chat_openai(messages=[{"role": "user", "content": "hi"}])


def test_free_model_served_free_is_fine(monkeypatch):
    _Served.served = "nvidia/nemotron-3-ultra-550b-a55b:free"
    monkeypatch.setattr(openai, "OpenAI", _Served)
    c = _client(model="nvidia/nemotron-3-ultra-550b-a55b:free")
    out = c._chat_openai(messages=[{"role": "user", "content": "hi"}])
    assert out["message"]["content"] == "ok"


def test_paid_model_with_unreported_served_name_is_fine(monkeypatch):
    _Served.served = None
    monkeypatch.setattr(openai, "OpenAI", _Served)
    c = _client(model="deepseek/deepseek-chat")
    assert c._chat_openai(messages=[{"role": "user", "content": "hi"}])


def _status_error(code):
    import httpx
    req = httpx.Request("POST", OR_URL + "/chat/completions")
    resp = httpx.Response(code, request=req, json={"error": {"message": "x"}})
    return openai.APIStatusError("Insufficient credits", response=resp,
                                 body=None)


def test_out_of_credits_stops_the_run(monkeypatch):
    class _NoCredit(_Served):
        def __init__(self, **kwargs):
            def create(**p):
                raise _status_error(402)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=create))
    monkeypatch.setattr(openai, "OpenAI", _NoCredit)
    c = _client()
    with pytest.raises(ai_client.FatalProviderError, match="credit"):
        c._chat_openai(messages=[{"role": "user", "content": "hi"}])
