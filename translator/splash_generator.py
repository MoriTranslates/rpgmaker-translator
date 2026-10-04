"""Inject translation splash screen into exported RPG Maker games.

Copies the pre-made TranslationSplash.png to img/system/ and injects
the TranslationSplash.js plugin into plugins.js so it displays before
the title screen.
"""

import json
import os
import re
import shutil
from pathlib import Path

from .image_translator import encrypt_to_rpgmvp, read_encryption_key
from .resource_paths import resource_path

_ASSETS_DIR = resource_path("assets")
_SPLASH_PNG = os.path.join(_ASSETS_DIR, "TranslationSplash.png")
_PLUGIN_JS = os.path.join(_ASSETS_DIR, "plugins", "TranslationSplash.js")


def _content_root(game_dir: str) -> str | None:
    """Folder holding data/ + js/ — the game root, or www/ for deployed MV."""
    for base in (game_dir, os.path.join(game_dir, "www")):
        for data in ("data", "Data"):
            if os.path.isdir(os.path.join(base, data)):
                return base
    return None


def _has_encrypted_images(root: str) -> bool:
    for data in ("data", "Data"):
        path = os.path.join(root, data, "System.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return bool(json.load(f).get("hasEncryptedImages"))
            except (json.JSONDecodeError, OSError):
                return False
    return False


def inject_splash(game_dir: str) -> bool:
    """Inject translation splash into an RPG Maker game.

    Copies the splash PNG to img/system/ (plus an encrypted copy for games
    with encrypted images), copies the plugin JS to js/plugins/, and adds
    the plugin entry to plugins.js.

    Returns True only if plugins.js now loads the splash plugin.
    """
    if not os.path.isfile(_SPLASH_PNG) or not os.path.isfile(_PLUGIN_JS):
        return False

    root = _content_root(game_dir)
    if not root:
        return False
    plugins_path = os.path.join(root, "js", "plugins.js")
    if not os.path.isfile(plugins_path):
        return False

    # Copy splash image
    system_dir = os.path.join(root, "img", "system")
    os.makedirs(system_dir, exist_ok=True)
    shutil.copy2(_SPLASH_PNG, os.path.join(system_dir, "TranslationSplash.png"))

    # Encrypted games only load the encrypted file (MV: .rpgmvp, MZ: .png_)
    if _has_encrypted_images(root):
        key = read_encryption_key(game_dir)
        if not key:
            return False
        is_mz = os.path.isfile(os.path.join(root, "js", "rmmz_core.js"))
        ext = ".png_" if is_mz else ".rpgmvp"
        encrypt_to_rpgmvp(
            _SPLASH_PNG, os.path.join(system_dir, "TranslationSplash" + ext), key)

    # Copy plugin JS
    plugins_dir = os.path.join(root, "js", "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    shutil.copy2(_PLUGIN_JS, os.path.join(plugins_dir, "TranslationSplash.js"))

    # Inject into plugins.js
    return _inject_plugin_entry(plugins_path)


def _inject_plugin_entry(plugins_js_path: str) -> bool:
    """Add TranslationSplash to the plugins.js array if not already present.

    Returns True if the entry is present afterwards.
    """
    if not os.path.isfile(plugins_js_path):
        return False

    text = Path(plugins_js_path).read_text(encoding="utf-8")
    if "TranslationSplash" in text:
        return True  # already injected

    entry = (
        '{"name":"TranslationSplash","status":true,"description":'
        '"Translation splash screen","parameters":{'
        '"FadeIn":"40","Wait":"180","FadeOut":"30"}}'
    )

    # Insert after the opening [
    new_text = re.sub(
        r'(var\s+\$plugins\s*=\s*\[)',
        r'\1\n' + entry + ',',
        text,
        count=1,
    )

    if new_text == text:
        return False
    Path(plugins_js_path).write_text(new_text, encoding="utf-8")
    return True
