"""translator.output_checks: one test group per function (pure, no client)."""

from translator import output_checks as oc


# ── ruby_to_base ────────────────────────────────────────────────────

def test_ruby_to_base():
    assert oc.ruby_to_base(r"\r[淫紋,いんもん]が光った") == "淫紋が光った"
    assert oc.ruby_to_base(r"\R[魔王,まおう]") == "魔王"
    # not a ruby code (no comma) / other codes are left alone
    assert oc.ruby_to_base(r"\C[2]赤\C[0]") == r"\C[2]赤\C[0]"
    assert oc.ruby_to_base("") == ""


# ── is_builtin_glossary_pair ────────────────────────────────────────

def test_is_builtin_glossary_pair():
    assert oc.is_builtin_glossary_pair("胸", "Breasts")
    assert not oc.is_builtin_glossary_pair("胸", "Chest")  # user changed it
    assert not oc.is_builtin_glossary_pair("ミガロ", "Migaro")


# ── normalise_line_breaks ───────────────────────────────────────────

def test_normalise_line_breaks():
    assert oc.normalise_line_breaks("a\r\nb\rc\nd") == "a\nb\nc\nd"
    assert oc.normalise_line_breaks("plain") == "plain"


# ── replace_jp_ellipsis ─────────────────────────────────────────────

def test_replace_jp_ellipsis():
    two = chr(0x2026) * 2
    assert oc.replace_jp_ellipsis(f"Well{two} ok{chr(0x2026)}") == f"Well... ok{chr(0x2026)}"
    assert oc.replace_jp_ellipsis(f"Bon{two}", "French") == f"Bon{two}"
    assert oc.replace_jp_ellipsis(f"ellway{two}", "Pig Latin") == "ellway..."


# ── restore_name_case ───────────────────────────────────────────────

def test_restore_name_case():
    g = {"ミガロ": "Migaro", "胸": "Breasts", "聖剣": "Holy Sword", "宿": "inn"}
    src = "ミガロは聖剣を胸に抱いた。宿。"
    out = "migaro held the holy sword to her breasts. MIGARO! the inn"
    assert oc.restore_name_case(out, src, g) == (
        "Migaro held the Holy Sword to her breasts. MIGARO! the inn")


def test_restore_name_case_needs_term_in_source_and_word_boundary():
    g = {"ミガロ": "Migaro"}
    assert oc.restore_name_case("migaro", "別の行", g) == "migaro"
    assert oc.restore_name_case("migarox migaro", "ミガロ", g) == "migarox Migaro"
    assert oc.restore_name_case("«CODE1»migaro", "ミガロ«CODE1»", g) == "«CODE1»Migaro"
    assert oc.restore_name_case("x", "ミガロ", None) == "x"


# ── speaker_label_name / restore_speaker_label_case ─────────────────

def test_speaker_label_name():
    assert oc.speaker_label_name('ミナ\n"おはよう"') == "ミナ"
    assert oc.speaker_label_name("京子：\nただいま") == "京子"
    assert oc.speaker_label_name("[ミナ]\nやあ") == "ミナ"
    assert oc.speaker_label_name("人魚", field="speaker_name") == "人魚"
    assert oc.speaker_label_name("ミナ\nやあ", glossary={"ミナ": "Mina"}) == "ミナ"
    assert oc.speaker_label_name("ミナ\nやあ", actor_names={1: "ミナ"}) == "ミナ"
    # not labels
    assert oc.speaker_label_name("ミナ\nやあ") is None           # unknown, unquoted
    assert oc.speaker_label_name("少女は笑った。\nそして") is None  # a sentence
    assert oc.speaker_label_name("やあ") is None                  # one line
    assert oc.speaker_label_name("«CODE1»\n「やあ」") is None      # a code
    assert oc.speaker_label_name("Mina\n「やあ」") is None         # already English


def test_restore_speaker_label_case():
    f = oc.restore_speaker_label_case
    assert f('mina\n"Hi"', 'ミナ\n"やあ"', glossary={"ミナ": "Mina"}) == 'Mina\n"Hi"'
    assert f('hanabi-chan\n"Hi"', 'ハナビちゃん\n"やあ"') == 'Hanabi-chan\n"Hi"'
    assert f("kyoko:\nhi", "京子：\nやあ") == "Kyoko:\nhi"
    assert f("[mina]\nhi", "[ミナ]\nやあ") == "[Mina]\nhi"
    assert f("ridill\nhi", "リディル\nやあ", actor_names={3: "リディル"},
             actor_names_en={3: "Ridill"}) == "Ridill\nhi"
    assert f("villager a", "村人Ａ", field="speaker_name") == "Villager A"
    # left alone: capitalised, not a label, too long
    assert f('MINA\n"Hi"', 'ミナ\n"やあ"') == 'MINA\n"Hi"'
    assert f("the girl smiled.\nok", "少女は笑った。\nうん") == "the girl smiled.\nok"
    assert f('a b c d e\n"x"', 'ミナ\n"やあ"') == 'a b c d e\n"x"'


# ── restore_leading_blank_line ──────────────────────────────────────

def test_restore_leading_blank_line():
    f = oc.restore_leading_blank_line
    assert f("ugh...!", "\nううっ") == "\nUgh...!"
    assert f('"no!"', "\n「いや！」") == '\n"No!"'
    assert f("Ugh", "\n\nううっ") == "\n\nUgh"
    assert f("\nUgh", "\nううっ") == "\nUgh"      # already there
    assert f("ugh", "ううっ") == "ugh"            # source has none
    assert f("", "\nううっ") == ""


# ── clean_output ────────────────────────────────────────────────────

def test_clean_output_runs_all_fixers():
    out = oc.clean_output(
        "\nミガロ\n\"やあ……\"", "migaro\r\n\"hey" + chr(0x2026) * 2 + "\"",
        glossary={"ミガロ": "Migaro"})
    assert out == "\nMigaro\n\"hey...\""
    assert oc.clean_output("x", "") == ""


# ── length_mismatch / duplicate_answers / problems / batch_problems ─

LONG = "あの森の奥には古い神殿があって、そこには誰も見たことのない宝が眠っているという噂を聞いたんだ。"
MOAN = "あぁぁぁぁぁぁっ！！イクイクイクイクイクイクイクイクイクイクイクイクーーーーーーーっ！！"


def test_length_mismatch():
    assert oc.length_mismatch(LONG, "Yeah.")
    assert oc.length_mismatch("はい", "Then let's meet at the village entrance tomorrow morning okay.")
    assert oc.length_mismatch(LONG, "I heard there is an old temple deep in that forest.") is None
    assert oc.length_mismatch(MOAN, "I'm cumming!") is None
    assert oc.length_mismatch("うん", "Yeah") is None
    assert oc.length_mismatch("«CODE1»", "A long answer for a line of codes only, really long.") is None


def test_duplicate_answers():
    src = {"Line1": "そうだね", "Line2": "行こう！", "Line3": "うん", "Line4": "ええ",
           "Line5": "同じ", "Line6": "同じ"}
    out = {"Line1": "Yeah, let's go!", "Line2": "Yeah, let's go!",
           "Line3": "Yeah.", "Line4": "Yeah.",
           "Line5": "It is the same.", "Line6": "It is the same."}
    bad = oc.duplicate_answers(src, out)
    assert set(bad) == {"Line1", "Line2"}
    assert "Line2" in bad["Line1"]


def test_problems_and_batch_problems():
    assert oc.problems(LONG, "Yeah.")
    assert oc.problems("うん", "Yeah.") == []
    found = oc.batch_problems({"Line1": LONG, "Line2": "うん"},
                              {"Line1": "Yeah.", "Line2": "Yeah."})
    assert list(found) == ["Line1"]
