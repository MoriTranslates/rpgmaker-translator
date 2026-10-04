"""Image translation pipeline: OCR parsing, coord mapping, RPGMV crypto, two-state."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest
from PIL import Image

from translator.image_translator import (
    ImageTranslator, TextRegion, decrypt_rpgmvp, encrypt_to_rpgmvp,
)


class _FakeVisionClient:
    """Returns a canned OCR reply and records the image size it was sent."""

    def __init__(self, reply: str):
        self.reply = reply
        self.sent_sizes = []

    def vision_chat(self, b64, prompt, system=""):
        import base64
        from io import BytesIO
        with Image.open(BytesIO(base64.b64decode(b64))) as im:
            self.sent_sizes.append(im.size)
        return self.reply


# ── _parse_ocr_response ─────────────────────────────────────────────

def test_parse_normalized_coords_small_image():
    raw = json.dumps([{"text": "はじめから", "bbox": [500, 500, 999, 999]}])
    regions = ImageTranslator._parse_ocr_response(raw, 816, 624)
    assert len(regions) == 1
    assert regions[0].bbox == (408, 312, 816, 624)


def test_parse_pixel_coords_above_1000():
    raw = json.dumps([{"text": "テスト", "bbox": [100, 100, 1500, 900]}])
    regions = ImageTranslator._parse_ocr_response(raw, 1920, 1080)
    assert regions[0].bbox == (100, 100, 1500, 900)


def test_parse_prose_around_array():
    raw = ('Sure! Here is the [result] you asked for:\n'
           '```json\n[{"text": "セーブ", "bbox": [0, 0, 999, 999]}]\n```\n'
           'Let me know [if] you need more.')
    regions = ImageTranslator._parse_ocr_response(raw, 100, 100)
    assert [r.text for r in regions] == ["セーブ"]
    assert regions[0].bbox == (0, 0, 100, 100)


def test_parse_null_text_skipped():
    raw = json.dumps([
        {"text": None, "bbox": [0, 0, 10, 10]},
        {"text": "ロード", "bbox": [10, 10, 20, 20]},
    ])
    regions = ImageTranslator._parse_ocr_response(raw, 999, 999)
    assert [r.text for r in regions] == ["ロード"]


def test_parse_garbage_returns_empty():
    assert ImageTranslator._parse_ocr_response("no json here", 100, 100) == []


# ── _ocr_attempt coordinate mapping ─────────────────────────────────

def test_ocr_attempt_maps_back_to_original_size():
    img = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    client = _FakeVisionClient(
        json.dumps([{"text": "テスト", "bbox": [400, 400, 600, 500]}]))
    tr = ImageTranslator(client)
    regions = tr._ocr_attempt(img, 1920, 1080, max_dim=1280)
    assert client.sent_sizes == [(1280, 720)]
    assert len(regions) == 1
    x1, y1, x2, y2 = regions[0].bbox
    for got, want in zip((x1, y1, x2, y2), (769, 432, 1153, 540)):
        assert abs(got - want) <= 2, regions[0].bbox


def test_ocr_attempt_upscaled_send_img_maps_to_original():
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    upscaled = img.resize((800, 400))
    client = _FakeVisionClient(
        json.dumps([{"text": "テスト", "bbox": [0, 0, 500, 500]}]))
    tr = ImageTranslator(client)
    regions = tr._ocr_attempt(img, 200, 100, max_dim=None, send_img=upscaled)
    assert client.sent_sizes == [(800, 400)]
    x1, y1, x2, y2 = regions[0].bbox
    assert (x1, y1) == (0, 0)
    assert abs(x2 - 100) <= 1 and abs(y2 - 50) <= 1


# ── RPGMV encryption ────────────────────────────────────────────────

_KEY = "0123456789abcdef0123456789abcdef"


def test_encrypt_decrypt_round_trip(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(png)
    enc = tmp_path / "out" / "a.rpgmvp"
    encrypt_to_rpgmvp(str(png), str(enc), _KEY)
    assert enc.read_bytes()[:5] == b"RPGMV"
    assert decrypt_rpgmvp(str(enc), _KEY) == png.read_bytes()


def test_decrypt_rejects_non_rpgmv(tmp_path):
    bad = tmp_path / "bad.rpgmvp"
    bad.write_bytes(b"not an encrypted file at all, definitely not")
    with pytest.raises(ValueError):
        decrypt_rpgmvp(str(bad), _KEY)


# ── Two-state inference ─────────────────────────────────────────────

def test_infer_two_state_rejects_opaque_image():
    img = Image.new("RGBA", (200, 100), (40, 80, 120, 255))
    regions = [TextRegion(text="はじめから", bbox=(50, 10, 150, 40),
                          translation="New Game")]
    ok, merged = ImageTranslator._infer_two_state(img, regions, 200, 100)
    assert ok is False and merged == []


def test_infer_two_state_requires_translation():
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    # Visible content in both halves, text region on transparent bg
    for y in range(10, 90, 3):
        for x in range(20, 180, 3):
            img.putpixel((x, y), (255, 255, 255, 255))
    for x in range(30, 170):
        for y in range(5, 45):
            if (x + y) % 2:
                img.putpixel((x, y), (0, 0, 0, 0))
    region = TextRegion(text="はじめから", bbox=(60, 15, 140, 35))
    ok, _ = ImageTranslator._infer_two_state(img, [region], 200, 100)
    assert ok is False


# ── Verify parsing / translation failure ────────────────────────────

@pytest.mark.parametrize("reply,ok,issues", [
    ("I can't tell.", None, ["verify unparseable"]),
    ('{"ok": false, "issues": "text cut off"}', False, ["text cut off"]),
    ('Result: {"ok": false, "issues": [{"type": "jp"}]}', False, ['{"type": "jp"}']),
    ('{"ok": true, "issues": []}', True, []),
])
def test_verify_render_parsing(tmp_path, reply, ok, issues):
    path = tmp_path / "r.png"
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(path)
    result = ImageTranslator(_FakeVisionClient(reply)).verify_render(str(path))
    assert result == {"ok": ok, "issues": issues}


class _FakeTranslateClient:
    def __init__(self, reply):
        self.reply = reply

    def translate_name(self, text, hint=""):
        return self.reply


@pytest.mark.parametrize("reply", ["", "はじめから", "New はじめ"])
def test_translate_for_bbox_failure_returns_empty(reply):
    tr = ImageTranslator(_FakeTranslateClient(reply))
    assert tr._translate_for_bbox("はじめから", "", 20, 200, 40) == ""
