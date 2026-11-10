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


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://LocalHost:11434", "http://127.0.0.1:11434"),
    ("http://localhost", "http://127.0.0.1"),
    ("https://localhost:8080/v1", "https://127.0.0.1:8080/v1"),
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
    "",
    "not a url",
])
def test_other_urls_unchanged(url):
    assert _ipv4_loopback(url) == url


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
    assert len(seen) >= 3
    assert all(u.startswith("http://127.0.0.1:11434/") for u in seen), seen


def test_unreachable_message_shows_typed_url(monkeypatch):
    import requests

    def fake_post(*a, **kw):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    client = AIClient(base_url="http://localhost:11434")
    with pytest.raises(ai_client.ServerUnreachable, match="localhost:11434"):
        client._chat(messages=[])
