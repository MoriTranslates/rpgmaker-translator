"""Model suggestion catalog: built-in ``models.json`` plus an optional,
validated refresh from GitHub.

The downloaded copy is untrusted.  It is size-capped, schema-checked and
every Ollama tag must match a strict pattern, so a tag can never turn into
an extra argument for ``ollama pull``.  Any problem means "keep the
built-in list" (fail closed); nothing here raises to the caller.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import NamedTuple

import requests

from .resource_paths import resource_path

log = logging.getLogger(__name__)

REMOTE_URL = ("https://raw.githubusercontent.com/MoriTranslates/"
              "rpgmaker-translator/master/models.json")
REMOTE_TIMEOUT_S = 5.0
MAX_REMOTE_BYTES = 64 * 1024
MAX_MODELS = 60
MAX_LABEL = 60
MAX_NOTES = 200
MAX_TAG = 200
FAMILIES = ("qwen", "sugoi", "other")

# Letters, digits and . _ : / - only; first character alphanumeric so a tag
# can never start with "-" and be read as an option.
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")


SECTIONS = ("recommended", "specialist", "experimental")
TIERS = (8, 12, 16)  # GB of VRAM; tier_picks needs one pick for each


class ModelEntry(NamedTuple):
    label: str
    tag: str
    vram_gb: float
    section: str  # one of SECTIONS
    notes: str


class Catalog(NamedTuple):
    entries: list
    tier_picks: dict  # {8: tag, 12: tag, 16: tag}

    def pick_for_vram_mb(self, vram_mb: float) -> str:
        """Recommended tag for the detected VRAM (no GPU -> smallest tier)."""
        gb = vram_mb / 1024 + 0.5  # a "12 GB" card reports a bit under 12
        tier = TIERS[0]
        for t in TIERS:
            if gb >= t:
                tier = t
        return self.tier_picks[tier]

    def by_section(self, *sections):
        return [e for e in self.entries if e.section in sections]


# Used only if the bundled models.json is missing or broken.
_FALLBACK = Catalog(
    [ModelEntry("Qwen3.5 9B", "qwen3.5:9b", 8, "recommended",
                "262K context, multimodal (text + images)")],
    {t: "qwen3.5:9b" for t in TIERS})


def is_valid_tag(tag) -> bool:
    """True if ``tag`` is safe to pass to ``ollama pull`` as one argument."""
    return (isinstance(tag, str) and 0 < len(tag) <= MAX_TAG
            and _TAG_RE.fullmatch(tag) is not None
            and ".." not in tag)


def _plain_text(value, max_len: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("text field is not a string")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError("empty text field")
    if len(value) > max_len:
        raise ValueError("text field too long")
    if any(ord(ch) < 32 or ord(ch) == 127 or 0x2028 <= ord(ch) <= 0x2029
           or ord(ch) in (0xFEFF, 0xFFFE, 0xFFFF) or 0xD800 <= ord(ch) <= 0xDFFF
           for ch in value):
        raise ValueError("control characters in text field")
    return value


def parse_catalog(data) -> Catalog:
    """Validate a decoded models.json; return a Catalog or raise ValueError.
    One bad entry rejects the whole file."""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("unsupported schema")
    out, seen = [], set()
    for section in SECTIONS:
        rows = data.get(section, [])
        if not isinstance(rows, list):
            raise ValueError(f"{section} is not a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("entry is not an object")
            tag = row.get("tag")
            if not is_valid_tag(tag):
                raise ValueError(f"invalid tag {str(tag)[:40]!r}")
            if tag.lower() in seen:
                raise ValueError("duplicate tag")
            seen.add(tag.lower())
            vram = row.get("vram_gb")
            if (isinstance(vram, bool) or not isinstance(vram, (int, float))
                    or not 0 < vram <= 1024):
                raise ValueError("bad vram_gb")
            out.append(ModelEntry(
                _plain_text(row.get("label"), MAX_LABEL, False), tag,
                float(vram), section,
                _plain_text(row.get("notes", ""), MAX_NOTES, True)))
    if not out or len(out) > MAX_MODELS:
        raise ValueError("bad number of models")
    recommended = {e.tag for e in out if e.section == "recommended"}
    picks = data.get("tier_picks")
    if not isinstance(picks, dict):
        raise ValueError("missing tier_picks")
    tier_picks = {}
    for t in TIERS:
        tag = picks.get(str(t))
        if tag not in recommended:
            raise ValueError(f"tier {t} pick is not a recommended model")
        tier_picks[t] = tag
    return Catalog(out, tier_picks)


def load_builtin() -> Catalog:
    """The catalog shipped with the app (never raises)."""
    try:
        with open(resource_path("models.json"), "r", encoding="utf-8") as f:
            return parse_catalog(json.load(f))
    except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
        log.warning("Bundled models.json unusable (%s); using minimal list", e)
        return _FALLBACK


def fetch_remote_catalog(url: str = REMOTE_URL,
                         timeout: float = REMOTE_TIMEOUT_S,
                         get=requests.get):
    """Download and validate the remote catalog.  Returns a Catalog,
    or None on any failure.  Blocking: call from a worker thread only."""
    try:
        deadline = time.monotonic() + timeout
        resp = get(url, timeout=timeout, stream=True, allow_redirects=False)
        try:
            if resp.status_code != 200:
                log.info("Model list refresh skipped: HTTP %s", resp.status_code)
                return None
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=8192):
                size += len(chunk)
                if size > MAX_REMOTE_BYTES:
                    log.warning("Model list refresh rejected: over %d bytes",
                                MAX_REMOTE_BYTES)
                    return None
                if time.monotonic() > deadline:
                    log.info("Model list refresh skipped: timed out")
                    return None
                chunks.append(chunk)
        finally:
            resp.close()
        return parse_catalog(json.loads(b"".join(chunks).decode("utf-8")))
    except Exception as e:  # noqa: BLE001
        # Harmless to drop: the remote list is an optional refresh, the
        # built-in list stays in use, and the reason is logged here.  Broad
        # on purpose: untrusted input must never reach the UI as an error.
        log.info("Model list refresh skipped (%s: %s)", type(e).__name__, e)
        return None
