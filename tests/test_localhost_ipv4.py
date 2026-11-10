"""Requests to a "localhost" server go to 127.0.0.1.

On Windows "localhost" is tried as IPv6 ::1 first. Ollama only listens on
IPv4, so every request waited ~2 s for the refused ::1 attempt (a third of a
10-line batch). The saved URL is never rewritten; only the address requests
connect to.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient, _ipv4_loopback  # noqa: E402

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


_REAL_PROXY_CHECK = ai_client._proxy_applies


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # This machine's own proxy settings must not decide these tests; the
    # proxy tests below restore the real check and set their own environment.
    monkeypatch.setattr(ai_client, "_proxy_applies", lambda url: False)


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://LocalHost:11434", "http://127.0.0.1:11434"),
    ("http://localhost", "http://127.0.0.1"),
    ("http://localhost:8080/v1/", "http://127.0.0.1:8080/v1/"),
])
def test_localhost_becomes_ipv4(url, expected):
    assert _ipv4_loopback(url) == expected


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434",
    "http://192.168.50.42:11434",
    "http://[::1]:11434",
    "http://localhost.example.com:11434",   # only the exact host name
    "http://mylocalhost:11434",
    "http://user:pw@localhost:11434",        # credentials: left alone
    "http://localhost:notaport",             # malformed: left for requests
    "https://localhost:8080/v1",             # https: certificate is for "localhost"
    "",
    "not a url",
])
def test_other_urls_unchanged(url):
    assert _ipv4_loopback(url) == url


def _clear_proxy_env(monkeypatch):
    monkeypatch.setattr(ai_client, "_proxy_applies", _REAL_PROXY_CHECK)
    for v in _PROXY_VARS:
        monkeypatch.delenv(v, raising=False)


def test_proxy_that_bypasses_localhost_keeps_localhost(monkeypatch):
    # NO_PROXY=localhost matches the name only: 127.0.0.1 would go through
    # the proxy and Ollama would look unreachable.
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost")
    assert _ipv4_loopback("http://localhost:11434") == "http://localhost:11434"


def test_proxy_that_bypasses_127_still_rewrites(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    assert _ipv4_loopback("http://localhost:11434") == "http://127.0.0.1:11434"


def test_unreadable_proxy_config_leaves_url_alone(monkeypatch):
    _clear_proxy_env(monkeypatch)

    def boom(url, no_proxy=None):
        raise OSError("registry unreadable")
    monkeypatch.setattr(ai_client.requests.utils, "get_environ_proxies", boom)
    assert _ipv4_loopback("http://localhost:11434") == "http://localhost:11434"


def test_request_url_keeps_saved_setting():
    client = AIClient(base_url="http://localhost:11434/")
    assert client.request_url == "http://127.0.0.1:11434"
    assert client.base_url == "http://localhost:11434"   # saved/shown as typed


def test_custom_provider_uses_ipv4():
    client = AIClient(base_url="http://localhost:8080/v1")
    client.provider = "Custom"
    assert client._get_openai_base_url() == "http://127.0.0.1:8080/v1"


def test_ollama_requests_go_to_ipv4(monkeypatch):
    seen = []

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": "ok"}, "models": []}

        def raise_for_status(self):
            pass

    def fake_post(url, *a, **kw):
        seen.append(url)
        return _Resp()

    def fake_get(url, *a, **kw):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client.requests, "get", fake_get)
    client = AIClient(base_url="http://localhost:11434")
    client._chat(messages=[])
    client.list_models()
    client.is_available()
    client.preload_model()
    client.unload_models()        # GET /api/ps; no models listed, so nothing to stop
    assert len(seen) >= 5
    assert all(u.startswith("http://127.0.0.1:11434/") for u in seen), seen


def test_settings_model_list_goes_to_ipv4(monkeypatch):
    import requests

    from translator.widgets.settings_dialog import _ModelFetcher
    seen = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "m"}]}

    monkeypatch.setattr(requests, "get", lambda url, *a, **kw: seen.append(url) or _Resp())
    got = []
    f = _ModelFetcher(None, "http://localhost:11434")
    f.done.connect(got.append)
    f.run()                       # run in this thread; no event loop needed
    assert seen == ["http://127.0.0.1:11434/api/tags"]
    assert got == [["m"]]


def test_unreachable_message_shows_typed_url(monkeypatch):
    import requests

    def fake_post(*a, **kw):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    client = AIClient(base_url="http://localhost:11434")
    with pytest.raises(ai_client.ServerUnreachable, match="localhost:11434"):
        client._chat(messages=[])
