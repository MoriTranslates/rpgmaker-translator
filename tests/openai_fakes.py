"""Fake requests/responses for building openai SDK errors in tests.

openai reads ``request.headers`` when an error is constructed, so tests
can't pass ``request=None`` (it broke with openai 3.60). The request comes
from the HTTP library the SDK itself is built on (see http_lib): importing
``httpx`` directly only worked where some other package had installed it.
"""

from http_lib import httpx

FAKE_REQUEST = httpx.Request("POST", "https://api.invalid/v1/chat/completions")


def fake_response(status: int):
    """An HTTP response with ``status``, as openai's errors expect."""
    return httpx.Response(status, request=FAKE_REQUEST)
