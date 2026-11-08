"""The HTTP library the installed openai SDK is built on.

openai 3.41 uses ``httpx``; 3.42 and later use ``httpx2`` (same Request /
Response API). Tests that build fake API errors import ``httpx`` from here so
they run against whichever one the SDK actually uses.
"""

import openai._base_client as _base

httpx = getattr(_base, "httpx2", None) or _base.httpx
