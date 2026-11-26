"""Regressions for the second blind review of feature/game2-lessons."""

import json
import random
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from translator import review as R
from translator.image_translator import ImageTranslator
from translator.output_checks import clean_output, normalise_fullwidth
from translator.post_processor import run_post_processing
from translator.project_model import TranslationEntry
from translator.rpgmaker_mv import _js_escape_in


def _noisy(w=800, h=600, box=(80, 60, 320, 180), seed=1):
    """Busy background (a CG or a patterned button) with text strokes."""
    rnd = random.Random(seed)
    im = Image.new("L", (w, h))
    im.putdata([rnd.randrange(256) for _ in range(w * h)])
    d = ImageDraw.Draw(im)
    for x in range(box[0], box[2], 6):
        d.rectangle((x, box[1], x + 2, box[3]), fill=255)
    return im.convert("RGB")


# 1. A correct 0-999 reply on a busy image must stay on the 0-999 scale
def test_ocr_busy_image_keeps_the_prompt_scale():
    raw = json.dumps([{"text": "はい", "bbox": [100, 100, 400, 300]}])
    regions = ImageTranslator._parse_ocr_response(raw, 800, 600, img=_noisy())
    assert regions[0].bbox == (80, 60, 320, 180)


def test_ocr_striped_image_keeps_the_prompt_scale():
    im = Image.new("RGB", (800, 600), (30, 30, 60))
    d = ImageDraw.Draw(im)
    for y in range(0, 600, 4):
        d.line((0, y, 799, y), fill=(200, 200, 220))
    raw = json.dumps([{"text": "はい", "bbox": [100, 100, 400, 300]}])
    regions = ImageTranslator._parse_ocr_response(raw, 800, 600, img=im)
    assert regions[0].bbox == (80, 60, 320, 180)


# 2. NaN / Infinity in a box: skipped, never a crash
@pytest.mark.parametrize("bbox", ['["NaN", 0, 10, 10]', '[NaN, 0, 10, 10]',
                                  '["inf", 0, 10, 10]', '[0, 0, Infinity, 10]'])
def test_ocr_non_finite_box_is_skipped(bbox):
    raw = '[{"text": "a", "bbox": ' + bbox + '}, {"text": "b", "bbox": [0, 0, 500, 500]}]'
    regions = ImageTranslator._parse_ocr_response(raw, 100, 100, img=_noisy(100, 100, (0, 0, 50, 50)))
    assert [r.text for r in regions] == ["b"]


# 3. A translation can never break the JavaScript string it lands in
def _node_value(js_line):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", "let v; const $gameMessage={add:x=>{v=x}};"
                          + js_line + ";process.stdout.write(JSON.stringify(v))"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("translation", [
    "ends with backslash" + chr(92),
    "a" + chr(92) * 2 + "'b",
    "line1\r\nline2",
    "line1\rline2",
    "50% off " + chr(92) + "u",
    "bad " + chr(92) + "x1 hex",
    "Don't",
])
@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.timeout(60)  # Node's first start on a fresh CI runner took >10 s
def test_js_escape_always_parses(translation, quote):
    line = f"$gameMessage.add({quote}勇者{quote})"
    at = line.index("勇者")
    js = line[:at] + _js_escape_in(line, at, translation) + line[at + 2:]
    _node_value(js)


@pytest.mark.timeout(60)  # starts Node, see above
def test_js_escape_keeps_codes_in_js_form():
    line = "$gameMessage.add('勇者')"
    at = line.index("勇者")
    code = chr(92) * 2 + "C[2]Hi"            # \\C[2] in the script = \C[2] shown
    js = line[:at] + _js_escape_in(line, at, code) + line[at + 2:]
    assert _node_value(js) == chr(92) + "C[2]Hi"


# 4. The literal that was extracted is the one replaced
def test_script_string_export_replaces_the_right_literal(tmp_path):
    from translator.rpgmaker_mv import _replace_script_literal
    line = '$gameVariables.setValue(1, "勇者さま"); $gameVariables.setValue(2, \'勇者\');'
    out = _replace_script_literal(line, "勇者", "Hero")
    assert out == '$gameVariables.setValue(1, "勇者さま"); $gameVariables.setValue(2, \'Hero\');'
    out = _replace_script_literal(out, "勇者さま", 'Sir "Hero"')
    assert out.startswith('$gameVariables.setValue(1, "Sir ' + chr(92) + '"Hero' + chr(92) + '"");')
    assert _replace_script_literal(line, "無い", "x") is None


# 5. Line-start VN syntax is never created
@pytest.mark.parametrize("fw, out", [
    ("＊sigh＊", "＊sigh*"), ("＠Heh", "＠Heh"), ("；Ah", "；Ah"),
    ("＃Muttering", "＃Muttering"), ("Hi\n＊sigh＊", "Hi\n＊sigh*"),
])
def test_fullwidth_keeps_line_start_vn_syntax(fw, out):
    assert normalise_fullwidth(fw) == out
    assert clean_output("元", fw) == out


# 7. A backslash before a full-width mark never becomes a control code
@pytest.mark.parametrize("text", [chr(92) + "！", chr(92) + "｜", chr(92) + "＄",
                                  chr(92) + "Ｇ", chr(92) + "C［２］"])
def test_fullwidth_never_completes_a_control_code(text):
    out = normalise_fullwidth(text + "Hi！")
    assert out.startswith(text) and out.endswith("Hi!")


# 6. Quote restoring also covers Wolf RPG message and choice fields
@pytest.mark.parametrize("field, quoted", [
    ("Ev1/p0/msg3", True), ("CE5/msg2", True), ("Ev1/p0/choice2_0", True),
    ("Ev1/p0/str2_0", False), ("Type1/Data2/F3", False), ("name", False),
])
def test_quote_restoring_on_wolf_fields(field, quoted):
    from translator.ai_client import AIClient
    c = AIClient()
    got = c._postprocess_result("Hello", {}, '"x"', field, original="「やあ」")
    assert (got == '"Hello"') is quoted
    e = TranslationEntry("Map.json/1", "Map.json", field, "「やあ」", "Hello", "translated")
    run_post_processing([e], project_type="wolf")
    assert (e.translation == '"Hello"') is quoted


# 8. More invisible characters are refused in review fixes
@pytest.mark.parametrize("bad", [chr(0xFE0F), chr(0xE000), chr(0x3000),
                                 chr(0x0301)])
def test_review_rejects_more_invisible_chars(bad):
    e = TranslationEntry("x/1", "x", "dialog", "台詞", "Old line.", "translated")
    p = R.Proposal("L1", e.id, e.original, e.translation, f"New {bad}line.", "")
    assert R.validate(p, e)


def test_review_allows_accented_letters():
    e = TranslationEntry("x/1", "x", "dialog", "台詞", "Old line.", "translated")
    for new in ("Café time.", "Cafe" + chr(0x0301) + " time.", "Naïve."):
        assert R.validate(R.Proposal("L1", e.id, e.original, e.translation, new, ""), e) == ""


# 9. A fix that apply() would never use is refused with a reason
@pytest.mark.parametrize("field, status", [("name", "translated"),
                                           ("dialog", "untranslated"),
                                           ("dialog", "skipped")])
def test_review_rejects_fixes_apply_would_drop(field, status):
    e = TranslationEntry("x/1", "x", field, "台詞", "Old line.", status)
    p = R.Proposal("L1", e.id, e.original, e.translation, "New line.", "")
    assert R.validate(p, e)
    assert R.apply([p], [e]) == []
