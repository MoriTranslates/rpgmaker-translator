"""Prompt v3 (lessons from "My Happiness 2") and migration of saved v2 defaults."""

import json
import os

import pytest

from translator import prompts as P
from translator.ai_client import TARGET_LANGUAGES

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
with open(os.path.join(FIXTURES, "legacy_prompts.json"), encoding="utf-8") as _f:
    LEGACY = json.load(_f)


def test_v2_default_migrates_to_v3():
    new, changed = P.migrate_saved_prompt(LEGACY["V2_SYSTEM_PROMPT"])
    assert changed and new == P.SYSTEM_PROMPT


def test_v2_sugoi_prompt_migrates_to_v3_sugoi():
    new, changed = P.migrate_saved_prompt(LEGACY["V2_DAZEDMTL_FULL_PROMPT"],
                                          model="sugoi-14b")
    assert changed and new == P.DAZEDMTL_FULL_PROMPT


def test_v2_non_english_engine_prompt_migrates():
    new, changed = P.migrate_saved_prompt(
        LEGACY["V2_TYRANO_SYSTEM_PROMPT_French"], target_language="French",
        project_type="tyranoscript")
    assert changed
    assert new == P.build_system_prompt("French", project_type="tyranoscript")


def test_edited_v2_prompt_is_left_alone():
    edited = LEGACY["V2_SYSTEM_PROMPT"] + "\n- My own rule."
    assert P.migrate_saved_prompt(edited) == (edited, False)


@pytest.mark.parametrize("lang", [lang for lang, *_ in TARGET_LANGUAGES])
def test_current_prompts_are_never_legacy(lang):
    """A current default matching a legacy hash would migrate forever."""
    for engine in ("rpgmaker_mv", "tyranoscript", "renpy", "wolfrpg", "crowd"):
        text = P.build_system_prompt(lang, project_type=engine)
        assert P.prompt_hash(text) not in P.LEGACY_PROMPT_HASHES
    sugoi = P.build_system_prompt(lang, model="sugoi")
    assert P.prompt_hash(sugoi) not in P.LEGACY_PROMPT_HASHES


def test_v3_rules_present():
    p = P.SYSTEM_PROMPT
    assert "negation" in p and "わけじゃない" in p
    assert 'the listener is "you"' in p
    assert "させる" in p and "られる" in p
    assert 'his penis is "my cock"' in p
    assert "bigger than mine" in p
    # Sugoi's prompt shares the core
    assert "negation" in P.DAZEDMTL_FULL_PROMPT


def test_batch_examples_show_negation_and_comparison():
    text = P.batch_instructions("English")
    assert "This isn't cuckolding" in text
    assert "bigger than Kenta-san's" in text
    # The examples are valid JSON with matching keys
    assert set(P._EXAMPLE_IN) == set(P._EXAMPLE_OUT)
