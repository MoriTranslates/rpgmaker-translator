"""Inject translation splash screen into exported RPG Maker games.

Copies the pre-made TranslationSplash.png to img/system/ and injects
the TranslationSplash.js plugin into plugins.js so it displays before
the title screen.
"""

import os
import re
import shutil
from pathlib import Path

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_SPLASH_PNG = os.path.join(_ASSETS_DIR, "TranslationSplash.png")
_PLUGIN_JS = os.path.join(_ASSETS_DIR, "plugins", "TranslationSplash.js")


def inject_splash(game_dir: str) -> bool:
    """Inject translation splash into an RPG Maker game.

    Copies the splash PNG to img/system/, copies the plugin JS to
    js/plugins/, and adds the plugin entry to plugins.js.

    Returns True if injection succeeded.
    """
    if not os.path.isfile(_SPLASH_PNG) or not os.path.isfile(_PLUGIN_JS):
        return False

    # Copy splash image
    system_dir = os.path.join(game_dir, "img", "system")
    os.makedirs(system_dir, exist_ok=True)
    shutil.copy2(_SPLASH_PNG, os.path.join(system_dir, "TranslationSplash.png"))

    # Copy plugin JS
    plugins_dir = os.path.join(game_dir, "js", "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    shutil.copy2(_PLUGIN_JS, os.path.join(plugins_dir, "TranslationSplash.js"))

    # Inject into plugins.js
    plugins_path = os.path.join(game_dir, "js", "plugins.js")
    _inject_plugin_entry(plugins_path)

    return True


def _inject_plugin_entry(plugins_js_path: str):
    """Add TranslationSplash to the plugins.js array if not already present."""
    if not os.path.isfile(plugins_js_path):
        return

    text = Path(plugins_js_path).read_text(encoding="utf-8")
    if "TranslationSplash" in text:
        return  # already injected

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

    if new_text != text:
        Path(plugins_js_path).write_text(new_text, encoding="utf-8")
