"""The splash plugin runs on both RPG Maker MV and MZ, so it may only call
Scene_Base methods that both engines define. It called isFading(), which
only MZ has, and every MV game crashed ("undefined is not a function") when
the splash ended.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "assets", "plugins", "TranslationSplash.js")

# Scene_Base.prototype methods defined in both the real MV rpg_scenes.js
# (1.6.x) and the real MZ rmmz_scenes.js (1.x), read from shipped games.
SCENE_BASE_MV_AND_MZ = {
    "addWindow", "checkGameover", "create", "createWindowLayer",
    "fadeOutAll", "fadeSpeed", "initialize", "isActive", "isBusy",
    "isReady", "popScene", "slowFadeSpeed", "start", "startFadeIn",
    "startFadeOut", "stop", "terminate", "update", "updateChildren",
    "updateFade",
    "addChild",  # from PIXI.Container, which both engines' Stage extends
}
MZ_ONLY = {"isFading", "isStarted", "centerSprite", "scaleSprite",
           "executeAutosave", "requestAutosave"}


def _source():
    with open(PLUGIN, encoding="utf-8") as f:
        return f.read()


def _scene_methods_called(src):
    """Engine methods the scene relies on: this.foo() calls it doesn't define
    itself, plus explicit Scene_Base.prototype.foo.call(...) super calls."""
    called = set(re.findall(r"\bthis\.([A-Za-z_]\w*)\s*\(", src))
    defined = set(re.findall(
        r"Scene_TranslationSplash\.prototype\.([A-Za-z_]\w*)\s*=", src))
    supers = set(re.findall(r"Scene_Base\.prototype\.([A-Za-z_]\w*)\.call", src))
    return (called - defined) | supers


def test_splash_only_calls_methods_mv_and_mz_both_have():
    missing = _scene_methods_called(_source()) - SCENE_BASE_MV_AND_MZ
    assert not missing, f"not in MV and MZ Scene_Base: {sorted(missing)}"


def test_splash_uses_no_mz_only_scene_methods():
    src = _source()
    for name in MZ_ONLY:
        assert not re.search(rf"\.{name}\s*\(", src), name


def test_splash_waits_for_the_fade_before_leaving():
    # the fix must still wait for the fade-out, not jump to the title at once
    assert re.search(r"this\._done\s*&&\s*!this\.isBusy\(\)", _source())
