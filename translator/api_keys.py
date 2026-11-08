"""Per-provider API keys in the settings file.

The file holds ``api_keys`` ({provider: key}). ``api_key`` and
``api_key_provider`` are still written (the active provider's key) so older
versions keep working. Old single-key files are migrated on load: the key
goes under its recorded owner, or under the saved provider if that is a
cloud one. A key whose owner can't be known (an old file saved on Ollama)
is kept in the file as ``api_key_unowned`` but is never used.
"""

OLLAMA = "Ollama (Local)"


def _clean_keys(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {p: k.strip() for p, k in raw.items()
            if isinstance(p, str) and p and p != OLLAMA
            and isinstance(k, str) and k.strip()}


def load_into(client, cfg: dict) -> None:
    """Set the client's keys from a loaded settings dict."""
    provider = cfg.get("provider", OLLAMA)
    keys = _clean_keys(cfg.get("api_keys"))
    key = cfg.get("api_key", "")
    key = key.strip() if isinstance(key, str) else ""
    # Who the single-slot key belongs to; old files had none (never guess
    # for Ollama)
    owner = cfg.get("api_key_provider", "" if provider == OLLAMA else provider)
    if not isinstance(owner, str) or owner == OLLAMA:
        owner = ""
    unowned = cfg.get("api_key_unowned", "")
    unowned = unowned.strip() if isinstance(unowned, str) else ""
    if key:
        if owner:
            keys.setdefault(owner, key)
        elif not unowned:
            unowned = key
    client.api_keys = keys
    client.unowned_api_key = unowned
    if provider != OLLAMA:
        # The key in use is always the active provider's own
        client.api_key = keys.get(provider, "")
        client.api_key_provider = provider
    else:
        client.api_key = keys.get(owner, "") if owner else unowned
        client.api_key_provider = owner


def settings_fields(client) -> dict:
    """Key fields to write into the settings file."""
    keys = client.all_api_keys()
    unowned = getattr(client, "unowned_api_key", "") or ""
    if client.provider != OLLAMA:
        key, owner = keys.get(client.provider, ""), client.provider
    else:
        owner = client.api_key_provider or ""
        if owner == OLLAMA:
            owner = ""
        # Same pair an older version saved on Ollama
        key = keys.get(owner, "") if owner else unowned
    fields = {"api_key": key, "api_key_provider": owner, "api_keys": keys}
    if unowned:
        fields["api_key_unowned"] = unowned
    return fields
