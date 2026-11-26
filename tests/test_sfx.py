"""Sound effects (lesson from game 2: パンッ -> "Pan!", ドピュッ -> "DOPPY").

The default SFX glossary gives the model English words; romanised SFX left
in a translation are flagged for review, never changed automatically.
"""

import pytest

from translator.default_glossary import ONOMATOPOEIA, get_all_defaults
from translator.output_checks import romanised_sfx
from translator.post_processor import run_post_processing
from translator.project_model import TranslationEntry


@pytest.mark.parametrize("text, flagged", [
    ("Zuppuh... Juph, Zuuh, Zupp!!", ["Zuppuh", "Juph", "Zuuh", "Zupp"]),
    ("DOPPY", ["DOPPY"]),     # ドピュッ spelled out
    ("happy puppy", []),      # English despite the -ppy ending
    ("Guh... Zuzu... Guguu... Zuchuu!!!!", ["Zuzu", "Guguu", "Zuchuu"]),
    ("zuzu, juro... mmh", ["zuzu", "juro"]),
])
def test_flags_romanised_sfx(text, flagged):
    assert romanised_sfx(text) == flagged


@pytest.mark.parametrize("text", [
    "But he couldn't escape! Damage taken.",       # English words
    "Squish, squelch... slurp, spurt!!",            # translated SFX
    "Gahahaha! Guh... chup, chuu",                  # laughs, grunts, kisses
    "Pan! Pant, gush, bump.",                       # real words
    "Hah... Haa... Fufu♡",
    "",
])
def test_leaves_english_alone(text):
    assert romanised_sfx(text) == []


def test_glossary_names_are_not_flagged():
    assert romanised_sfx("Tsukimoto and Genji", {"築本": "Tsukimoto",
                                                  "源治": "Genji"}) == []
    assert romanised_sfx("Tsukimoto") == ["Tsukimoto"]


def test_without_word_list_nothing_is_flagged(monkeypatch):
    from translator import output_checks as oc
    monkeypatch.setattr(oc, "_ENGLISH_WORDS", frozenset())
    assert romanised_sfx("Zuppuh but been") == []


def test_post_processing_flags_but_never_changes():
    e = TranslationEntry("Map001.json/1", "Map001.json", "dialog", "ズプッ",
                         "Zuppuh!!", "translated")
    r = run_post_processing([e], project_type="rpgmaker")
    assert r.romanised_sfx_ids == ["Map001.json/1"]
    assert e.translation == "Zuppuh!!"
    assert "romanised sound effects (not changed)" in str(r)
    # Other target languages aren't checked (the word list is English)
    r2 = run_post_processing([e], project_type="rpgmaker", target_language="French")
    assert r2.romanised_sfx_ids == []


# ── glossary entries ───────────────────────────────────────────────

SMALL_TSU = chr(0x30C3)


def test_new_sfx_keys_end_with_small_tsu_or_repeat():
    """Single-hit keys keep their small ッ so they can't sit inside a normal
    word (パンッ is not パンツ)."""
    for jp in ONOMATOPOEIA:
        if len(jp) >= 4 and jp[: len(jp) // 2] == jp[len(jp) // 2:]:
            continue        # repeated form (パンパン)
        if jp in ("ドピュ", "ジュルル"):
            continue        # older entry / drawn-out slurp
        assert jp.endswith(SMALL_TSU), jp


@pytest.mark.parametrize("word", ["ビュッフェ", "パンツ", "ずっと", "グッと"])
def test_no_sfx_key_inside_common_words(word):
    assert not [k for k in ONOMATOPOEIA if k in word]


def test_sfx_entries_are_in_the_defaults():
    defaults = get_all_defaults()
    assert defaults["パンッ"] == "Slap"
    assert defaults["ドピュッ"] == "Spurt"
