"""Image translation module — OCR Japanese text from game images, translate, render English.

Pipeline: Multimodal model (OCR + bounding boxes) → same model (translation) → Pillow (render)
        → same model (verify rendered image for quality).

Uses a single multimodal model (e.g. Qwen 3.5) for all stages — no separate vision model needed.

Features:
- OCR with 3-attempt retry (scaled → full res → 2x upscale for small images)
- Enhanced OCR prompt with pixel dimensions and explicit bbox rules, 20% bbox padding
- Two render modes: "preserve" (icon-preserving) and "clean" (white boxes on black)
- Two-state sprite sheet detection for RPG Maker menu images
- Warm pixel detection for non-sprite images (pink/red = selected state)
- Translation deduplication (same JP text translated once across regions)
- Verify loop: re-checks rendered images via multimodal model for quality
- RPGMVP encryption round-trip: decrypt to read, encrypt_to_rpgmvp() to write back
- Export to game with img_original/ backup on first export
"""

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


# ── Image file extensions ────────────────────────────────────────

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
ENCRYPTED_EXTS = (".rpgmvp", ".png_")  # MV/MZ encrypted image formats
ALL_IMAGE_EXTS = IMAGE_EXTS + ENCRYPTED_EXTS

# RPG Maker MV/MZ encrypted file header length
_RPGMV_HEADER_LEN = 16


# ── RPG Maker MV/MZ encryption support ──────────────────────────

def read_encryption_key(project_dir: str) -> str:
    """Read encryptionKey from System.json (MV/MZ encrypted deployments)."""
    for candidate in (
        os.path.join(project_dir, "data", "System.json"),
        os.path.join(project_dir, "Data", "System.json"),
        os.path.join(project_dir, "www", "data", "System.json"),
        os.path.join(project_dir, "www", "Data", "System.json"),
    ):
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    system = json.load(f)
                key = system.get("encryptionKey", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError):
                continue
    return ""


def decrypt_rpgmvp(file_path: str, encryption_key: str) -> bytes:
    """Decrypt an .rpgmvp / .png_ file to raw PNG bytes.

    RPG Maker MV/MZ encryption format:
    - First 16 bytes: RPG Maker header (signature + version)
    - Remaining bytes: original PNG with first 16 bytes XOR'd with key
    """
    key_bytes = bytes.fromhex(encryption_key)
    if len(key_bytes) < 16:
        raise ValueError(f"Encryption key too short: {len(key_bytes)} bytes, need 16")

    with open(file_path, "rb") as f:
        data = f.read()

    # Skip the 16-byte RPG Maker header
    encrypted = data[_RPGMV_HEADER_LEN:]

    # XOR the first 16 bytes with the key to restore PNG header
    decrypted_head = bytes(b ^ k for b, k in zip(encrypted[:16], key_bytes))

    # Rest of the file is unencrypted
    return decrypted_head + encrypted[16:]


# RPG Maker MV header: "RPGMV\x00\x00\x00" + 8 bytes version/padding
_RPGMV_HEADER = b"RPGMV\x00\x00\x00\x00\x03\x01\x00\x00\x00\x00\x00"


def encrypt_to_rpgmvp(png_path: str, output_path: str, encryption_key: str):
    """Encrypt a PNG file back to .rpgmvp format for RPG Maker MV/MZ.

    Reverses the decryption: adds the 16-byte RPG Maker header and XORs
    the first 16 bytes of PNG data with the encryption key.
    """
    key_bytes = bytes.fromhex(encryption_key)
    if len(key_bytes) < 16:
        raise ValueError(f"Encryption key too short: {len(key_bytes)} bytes, need 16")

    with open(png_path, "rb") as f:
        png_data = f.read()

    # XOR the first 16 bytes of PNG with key
    encrypted_head = bytes(b ^ k for b, k in zip(png_data[:16], key_bytes))

    # Assemble: RPG Maker header + encrypted first 16 bytes + rest unchanged
    result = _RPGMV_HEADER + encrypted_head + png_data[16:]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(result)


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class TextRegion:
    """A detected Japanese text region in an image."""
    text: str                          # Original Japanese text
    bbox: tuple[int, int, int, int]    # (x1, y1, x2, y2) pixels from top-left
    translation: str = ""


@dataclass
class ImageResult:
    """Result of processing a single image."""
    source_path: str
    output_path: str = ""
    regions: list[TextRegion] = field(default_factory=list)
    skipped: bool = False              # True if no JP text found
    error: str = ""
    verified: bool = False             # True if verify loop passed
    verify_issues: list[str] = field(default_factory=list)


# ── OCR prompt ────────────────────────────────────────────────────

_OCR_SYSTEM = "You are a precise OCR system for Japanese video game screenshots. You detect ALL Japanese text, including stylized, outlined, shadowed, and decorative game UI text."

_OCR_USER_TEMPLATE = """\
This image is {w}x{h} pixels. Find ALL Japanese text in this game image.
For each text region, return a JSON array:
[{{"text": "Japanese text here", "bbox": [x1, y1, x2, y2]}}, ...]

CRITICAL bbox rules:
- Coordinates use a normalized 0-999 scale (NOT pixels). 0=top-left, 999=bottom-right.
  - x range: 0 to 999 maps to 0 to {w} pixels.
  - y range: 0 to 999 maps to 0 to {h} pixels.
- The bbox must be TIGHT — fit closely around the actual text characters.
  - x1 = left edge of the leftmost character.
  - y1 = top edge of the tallest character.
  - x2 = right edge of the rightmost character.
  - y2 = bottom edge of the lowest character (including descenders).
- Do NOT add extra padding or margins. We add our own padding later.
- For multi-line text blocks, return ONE region covering ALL lines (not separate regions per line).
- Group consecutive lines that belong to the same paragraph or label into a single region.

What to include:
- ALL Japanese text: hiragana, katakana, kanji — even single characters
- Stylized, outlined, glowing, shadowed, or colored text (common in game menus)
- Text on buttons, banners, speech bubbles, title screens
- Text that is partially transparent or has special effects
- Mixed Japanese+English text (like "LoveVessel（愛の器）")

What to IGNORE:
- Purely English text with no Japanese characters
- Standalone numbers
- Non-text graphics (icons, borders, patterns)

IMPORTANT: Do NOT miss any text. Scan the ENTIRE image systematically — top to bottom, left to right.
If no Japanese text is found, return: []
Return ONLY the JSON array, no other text."""

# Regex to extract a JSON array from LLM output (may have markdown fences)
_JSON_RE = re.compile(r'\[.*\]', re.DOTALL)

# Verify prompt for rendered image QA
_VERIFY_SYSTEM = "You are a QA checker for translated game images. Be thorough but concise."

_VERIFY_PROMPT = """\
Check this translated game UI image for quality issues.
Look for:
1. Any remaining Japanese text (hiragana, katakana, kanji)
2. English text that is cut off or overlapping the icon
3. Text that is too small or hard to read
4. Visual artifacts or rendering problems

Return JSON: {"ok": true/false, "issues": ["issue description", ...]}
If everything looks good, return: {"ok": true, "issues": []}"""


# ── Font discovery ────────────────────────────────────────────────

def _find_font(bold: bool = False) -> str | None:
    """Find a usable TrueType font on the system."""
    candidates = []
    if bold:
        candidates.append(r"C:\Windows\Fonts\arialbd.ttf")
    candidates += [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\Arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        # Linux / macOS fallbacks
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


_SYSTEM_FONT = _find_font()
_SYSTEM_FONT_BOLD = _find_font(bold=True) or _SYSTEM_FONT


# ── ImageTranslator ──────────────────────────────────────────────

class ImageTranslator:
    """Scans game images, OCRs Japanese text, translates, and renders English."""

    # Subdirectories most likely to contain translatable text
    PRIORITY_DIRS = {"pictures", "titles1", "titles2", "system"}
    # Subdirectories that rarely contain text (skip by default)
    SKIP_DIRS = {
        "faces", "characters", "sv_actors", "sv_enemies",
        "enemies", "battlebacks1", "battlebacks2",
        "parallaxes", "tilesets", "animations",
    }

    # Render modes
    RENDER_PRESERVE = "preserve"  # Icon-preserving: clear text, keep background
    RENDER_CLEAN = "clean"        # White boxes on black background (legacy)

    def __init__(self, client, encryption_key: str = ""):
        """
        Args:
            client: An AIClient instance (multimodal — handles OCR, translate, verify).
            encryption_key: RPG Maker MV/MZ encryption key (hex string from System.json).
        """
        self.client = client
        self.encryption_key = encryption_key

    # ── Image loading (handles encrypted files) ─────────────────

    def open_image(self, image_path: str) -> Image.Image:
        """Open an image file, decrypting .rpgmvp/.png_ if needed."""
        if (image_path.lower().endswith(ENCRYPTED_EXTS)
                and self.encryption_key):
            raw = decrypt_rpgmvp(image_path, self.encryption_key)
            return Image.open(BytesIO(raw))
        return Image.open(image_path)

    # ── Scanning ──────────────────────────────────────────────────

    @staticmethod
    def find_img_dir(project_dir: str) -> str | None:
        """Locate the img/ directory — handles both root and www/ layouts."""
        for candidate in (
            os.path.join(project_dir, "img"),
            os.path.join(project_dir, "www", "img"),
        ):
            if os.path.isdir(candidate):
                return candidate
        return None

    def scan_images(self, project_dir: str, subdirs: list[str]) -> list[str]:
        """Find all images (including encrypted .rpgmvp) in selected img/ subdirectories."""
        img_dir = self.find_img_dir(project_dir)
        if not img_dir:
            return []
        results = []
        for subdir in subdirs:
            folder = os.path.join(img_dir, subdir)
            if not os.path.isdir(folder):
                continue
            for fname in sorted(os.listdir(folder)):
                if fname.lower().endswith(ALL_IMAGE_EXTS):
                    results.append(os.path.join(folder, fname))
        return results

    @staticmethod
    def list_subdirs(project_dir: str) -> list[tuple[str, int]]:
        """List img/ subdirectories with image counts.

        Returns list of (subdir_name, image_count) tuples.
        """
        img_dir = ImageTranslator.find_img_dir(project_dir)
        if not img_dir:
            return []
        results = []
        for name in sorted(os.listdir(img_dir)):
            path = os.path.join(img_dir, name)
            if not os.path.isdir(path):
                continue
            count = sum(
                1 for f in os.listdir(path)
                if f.lower().endswith(ALL_IMAGE_EXTS)
            )
            if count > 0:
                results.append((name, count))
        return results

    # ── OCR via multimodal model ─────────────────────────────────

    def ocr_image(self, image_path: str, max_dim: int = 1280) -> list[TextRegion]:
        """Send image to model, extract Japanese text + bounding boxes.

        Automatically retries at full resolution if the first attempt
        (scaled down) returns no regions — vision models are flaky and
        sometimes need the full image to detect stylized text.
        """
        img = self.open_image(image_path)
        orig_w, orig_h = img.size

        regions = self._ocr_attempt(img, orig_w, orig_h, max_dim)

        # Retry at full resolution if first attempt found nothing
        if not regions and max(orig_w, orig_h) > max_dim:
            regions = self._ocr_attempt(img, orig_w, orig_h, max_dim=None)

        # Retry with upscale if image is small (< 400px) and nothing found
        if not regions and max(orig_w, orig_h) < 400:
            scale_up = 800 / max(orig_w, orig_h)
            upscaled = img.resize(
                (int(orig_w * scale_up), int(orig_h * scale_up)),
                Image.Resampling.LANCZOS,
            )
            regions = self._ocr_attempt(upscaled, orig_w, orig_h, max_dim=None)

        return regions

    def _ocr_attempt(
        self, img: Image.Image, orig_w: int, orig_h: int,
        max_dim: int | None = 1280,
    ) -> list[TextRegion]:
        """Single OCR attempt — resize, send to model, parse results."""
        send_img = img.copy()

        # Resize for VRAM efficiency
        if max_dim is not None:
            longest = max(send_img.size)
            if longest > max_dim:
                scale = max_dim / longest
                new_w = int(send_img.size[0] * scale)
                new_h = int(send_img.size[1] * scale)
                send_img = send_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Convert to base64
        b64 = self._to_base64(send_img)

        # Build prompt with actual image dimensions
        send_w, send_h = send_img.size
        prompt = _OCR_USER_TEMPLATE.format(w=send_w, h=send_h)

        # Call multimodal model
        raw = self.client.vision_chat(b64, prompt, system=_OCR_SYSTEM)

        # Parse response
        regions = self._parse_ocr_response(raw, orig_w, orig_h)

        # Scale bounding boxes back to original image dimensions
        img_w, img_h = send_img.size
        sx = orig_w / img_w if img_w > 0 else 1.0
        sy = orig_h / img_h if img_h > 0 else 1.0
        if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
            for r in regions:
                x1, y1, x2, y2 = r.bbox
                r.bbox = (
                    int(x1 * sx), int(y1 * sy),
                    int(x2 * sx), int(y2 * sy),
                )

        # Clamp bboxes to image bounds and discard invalid geometry
        valid_regions = []
        for r in regions:
            x1, y1, x2, y2 = r.bbox
            x1 = max(0, min(x1, orig_w))
            y1 = max(0, min(y1, orig_h))
            x2 = max(0, min(x2, orig_w))
            y2 = max(0, min(y2, orig_h))
            if x2 > x1 and y2 > y1:
                r.bbox = (x1, y1, x2, y2)
                valid_regions.append(r)
        regions = valid_regions

        # Filter out zero-area or tiny regions
        regions = [r for r in regions if
                   (r.bbox[2] - r.bbox[0]) > 5 and (r.bbox[3] - r.bbox[1]) > 5]

        # Merge nearby regions first, then tighten the merged boxes
        if img is not None and regions:
            check_img = img.convert("RGBA") if img.mode != "RGBA" else img
            regions = self._merge_nearby_regions(regions, orig_w, orig_h)
            regions = self._tighten_bboxes(check_img, regions, orig_w, orig_h)
            # Resolve overlapping bboxes — clip at midpoint
            regions = self._resolve_overlaps(regions)

        return regions

    @staticmethod
    def _tighten_bboxes(
        img: Image.Image, regions: list[TextRegion],
        img_w: int = 0, img_h: int = 0,
    ) -> list[TextRegion]:
        """Tighten OCR bboxes to actual text pixel boundaries.

        Uses Qwen's bbox as a starting search area (with small margin),
        then scans inward from each edge to find where text pixels actually are.
        This corrects for Qwen's tendency to return oversized bounding boxes.

        For transparent backgrounds: text pixels have alpha > threshold.
        For semi-transparent: text is fully opaque (255), bg is semi (e.g. 127).
        For opaque: uses luminance contrast against sampled background color.
        """
        if not img_w:
            img_w, img_h = img.size
        pixels = img.load()

        for r in regions:
            x1, y1, x2, y2 = r.bbox
            box_w = x2 - x1
            box_h = y2 - y1
            if box_w < 5 or box_h < 5:
                continue
            # Skip tightening for small regions — Qwen's coords are
            # already reasonable, and tightening picks up noise
            if box_w < 150 and box_h < 100:
                continue

            # Search margin: moderate, proportional to box size
            margin_x = max(15, box_w // 5)
            margin_y = max(10, box_h // 5)
            sx1 = max(0, x1 - margin_x)
            sy1 = max(0, y1 - margin_y)
            sx2 = min(img_w, x2 + margin_x)
            sy2 = min(img_h, y2 + margin_y)

            # Sample LOCAL background from edges of search area (not global corners)
            # This correctly detects semi-transparent overlay regions
            edge_alphas = []
            for cx in (sx1, sx2 - 1):
                for cy in range(sy1, sy2, max(1, (sy2 - sy1) // 8)):
                    cx_c = max(0, min(img_w - 1, cx))
                    cy_c = max(0, min(img_h - 1, cy))
                    edge_alphas.append(pixels[cx_c, cy_c][3])
            for cy in (sy1, sy2 - 1):
                for cx in range(sx1, sx2, max(1, (sx2 - sx1) // 8)):
                    cx_c = max(0, min(img_w - 1, cx))
                    cy_c = max(0, min(img_h - 1, cy))
                    edge_alphas.append(pixels[cx_c, cy_c][3])

            if edge_alphas:
                edge_alphas.sort()
                local_bg_alpha = edge_alphas[len(edge_alphas) // 2]
            else:
                local_bg_alpha = 0

            # Also check the CENTER of the bbox for a more reliable bg sample
            # (edges might be transparent while center has semi-transparent overlay)
            center_alphas = []
            mid_x = (x1 + x2) // 2
            for cy in (y1, y2 - 1):
                cy_c = max(0, min(img_h - 1, cy))
                mx_c = max(0, min(img_w - 1, mid_x))
                center_alphas.append(pixels[mx_c, cy_c][3])
            mid_y = (y1 + y2) // 2
            for cx in (x1, x2 - 1):
                cx_c = max(0, min(img_w - 1, cx))
                my_c = max(0, min(img_h - 1, mid_y))
                center_alphas.append(pixels[cx_c, my_c][3])
            if center_alphas:
                center_bg = sorted(center_alphas)[len(center_alphas) // 2]
                # Use the higher alpha (prefer semi-transparent detection)
                local_bg_alpha = max(local_bg_alpha, center_bg)

            # Determine text detection based on local background
            if local_bg_alpha < 50:
                def is_text(px):
                    return px[3] > 30
            elif local_bg_alpha < 220:
                alpha_thresh = local_bg_alpha + 40
                def is_text(px, _t=alpha_thresh):
                    return px[3] > _t
            else:
                # Opaque — sample edge colors for contrast detection
                edge_colors = []
                for cx in (sx1, sx2 - 1):
                    for cy in range(sy1, sy2, max(1, (sy2 - sy1) // 4)):
                        cx_c = max(0, min(img_w - 1, cx))
                        cy_c = max(0, min(img_h - 1, cy))
                        edge_colors.append(pixels[cx_c, cy_c])
                bg_r = sum(c[0] for c in edge_colors) // max(1, len(edge_colors))
                bg_g = sum(c[1] for c in edge_colors) // max(1, len(edge_colors))
                bg_b = sum(c[2] for c in edge_colors) // max(1, len(edge_colors))
                bg_lum = bg_r * 0.299 + bg_g * 0.587 + bg_b * 0.114

                def is_text(px, _bl=bg_lum):
                    if px[3] < 200:
                        return False
                    lum = px[0] * 0.299 + px[1] * 0.587 + px[2] * 0.114
                    return abs(lum - _bl) > 60

            # Scan columns from left edge inward to find first text column
            new_x1 = sx2  # default: no text found
            for x in range(sx1, sx2):
                for y in range(sy1, sy2, max(1, (sy2 - sy1) // 32)):
                    if is_text(pixels[x, y]):
                        new_x1 = x
                        break
                else:
                    continue
                break

            # Scan columns from right edge inward
            new_x2 = sx1
            for x in range(sx2 - 1, sx1 - 1, -1):
                for y in range(sy1, sy2, max(1, (sy2 - sy1) // 32)):
                    if is_text(pixels[x, y]):
                        new_x2 = x + 1
                        break
                else:
                    continue
                break

            # Scan rows from top edge inward
            new_y1 = sy2
            for y in range(sy1, sy2):
                for x in range(sx1, sx2, max(1, (sx2 - sx1) // 32)):
                    if is_text(pixels[x, y]):
                        new_y1 = y
                        break
                else:
                    continue
                break

            # Scan rows from bottom edge inward
            new_y2 = sy1
            for y in range(sy2 - 1, sy1 - 1, -1):
                for x in range(sx1, sx2, max(1, (sx2 - sx1) // 32)):
                    if is_text(pixels[x, y]):
                        new_y2 = y + 1
                        break
                else:
                    continue
                break

            # Only update if we found valid text bounds
            if new_x1 < new_x2 and new_y1 < new_y2:
                # Padding for glow/shadow/anti-aliasing — enough to catch
                # font descenders and text effects that the scan might miss
                pad = 8
                r.bbox = (
                    max(0, new_x1 - pad),
                    max(0, new_y1 - pad),
                    min(img_w, new_x2 + pad),
                    min(img_h, new_y2 + pad),
                )

        return regions

    @staticmethod
    def _resolve_overlaps(regions: list[TextRegion]) -> list[TextRegion]:
        """Clip overlapping bboxes so they don't interfere with each other.

        When two regions overlap, clip the smaller one at the edge of the larger.
        """
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                ax1, ay1, ax2, ay2 = regions[i].bbox
                bx1, by1, bx2, by2 = regions[j].bbox

                # Check for overlap
                ox1 = max(ax1, bx1)
                oy1 = max(ay1, by1)
                ox2 = min(ax2, bx2)
                oy2 = min(ay2, by2)

                if ox1 >= ox2 or oy1 >= oy2:
                    continue  # no overlap

                # Determine overlap direction — clip on the axis with less overlap
                overlap_w = ox2 - ox1
                overlap_h = oy2 - oy1

                area_a = (ax2 - ax1) * (ay2 - ay1)
                area_b = (bx2 - bx1) * (by2 - by1)

                if overlap_w < overlap_h:
                    # Horizontal overlap — clip left/right
                    mid_x = (ox1 + ox2) // 2
                    if ax1 < bx1:  # A is left, B is right
                        regions[i].bbox = (ax1, ay1, min(ax2, mid_x), ay2)
                        regions[j].bbox = (max(bx1, mid_x), by1, bx2, by2)
                    else:
                        regions[j].bbox = (bx1, by1, min(bx2, mid_x), by2)
                        regions[i].bbox = (max(ax1, mid_x), ay1, ax2, ay2)
                else:
                    # Vertical overlap — clip top/bottom
                    mid_y = (oy1 + oy2) // 2
                    if ay1 < by1:  # A is top, B is bottom
                        regions[i].bbox = (ax1, ay1, ax2, min(ay2, mid_y))
                        regions[j].bbox = (bx1, max(by1, mid_y), bx2, by2)
                    else:
                        regions[j].bbox = (bx1, by1, bx2, min(by2, mid_y))
                        regions[i].bbox = (ax1, max(ay1, mid_y), ax2, ay2)

        return regions

    @staticmethod
    def _expand_bboxes_to_text(
        img: Image.Image, regions: list[TextRegion],
        img_w: int = 0, img_h: int = 0,
    ) -> list[TextRegion]:
        """Expand OCR bboxes to cover actual text pixels.

        Vision models often give bboxes that are too small — especially the
        horizontal extent. This scans outward from each bbox to find where
        visible (non-background) pixels actually are, and expands the bbox
        to cover them.
        """
        if not img_w:
            img_w, img_h = img.size
        pixels = img.load()

        # First, detect global background alpha to set thresholds
        # Sample from image corners and edges
        corner_samples = []
        for cx, cy in [(5, 5), (img_w-5, 5), (5, img_h-5), (img_w-5, img_h-5),
                       (img_w//2, 5), (img_w//2, img_h-5)]:
            cx = max(0, min(img_w-1, cx))
            cy = max(0, min(img_h-1, cy))
            corner_samples.append(pixels[cx, cy][3])
        global_bg_alpha = sorted(corner_samples)[len(corner_samples)//2]

        # Set visibility threshold based on background type
        # Semi-transparent bg (alpha ~100-200): only fully opaque pixels are text
        # Transparent bg (alpha ~0): any visible pixel is text
        # Opaque bg (alpha ~255): use color contrast instead
        if global_bg_alpha > 50:
            # Semi-transparent or opaque — only high-alpha pixels are text
            visibility_threshold = max(200, global_bg_alpha + 50)
        else:
            visibility_threshold = 30

        for r in regions:
            x1, y1, x2, y2 = r.bbox
            box_h = y2 - y1
            box_w = x2 - x1
            if box_h < 5:
                continue

            # Moderate search margin — don't go too far
            search_margin = max(80, box_w)  # up to 1x bbox-width
            sx1 = max(0, x1 - search_margin)
            sx2 = min(img_w, x2 + search_margin)

            # Detect background by sampling edges of search area
            bg_samples = []
            for y in range(y1, y2, max(1, box_h // 4)):
                if sx1 >= 0:
                    bg_samples.append(pixels[sx1, y])
                if sx2 < img_w:
                    bg_samples.append(pixels[sx2 - 1, y])

            if not bg_samples:
                continue

            # Determine background: average alpha and color
            avg_a = sum(c[3] for c in bg_samples) // len(bg_samples)
            is_transparent_bg = avg_a < 50  # truly transparent, not semi

            # Use the global threshold for consistency
            threshold = visibility_threshold

            if not is_transparent_bg:
                # Non-transparent images: skip expansion, 20% padding is enough
                # Expansion on opaque/semi-transparent bgs is unreliable
                # because bg pixels can look like "text" to the detector
                continue

            if is_transparent_bg:

                # Expand left
                new_x1 = x1
                for x in range(x1 - 1, sx1 - 1, -1):
                    col_visible = 0
                    for y in range(y1, y2, max(1, box_h // 8)):
                        if pixels[x, y][3] > threshold:
                            col_visible += 1
                    if col_visible > 0:
                        new_x1 = x
                    elif x < new_x1 - 20:
                        break  # gap > 20px, stop expanding

                # Expand right
                new_x2 = x2
                for x in range(x2, sx2):
                    col_visible = 0
                    for y in range(y1, y2, max(1, box_h // 8)):
                        if pixels[x, y][3] > threshold:
                            col_visible += 1
                    if col_visible > 0:
                        new_x2 = x + 1
                    elif x > new_x2 + 20:
                        break

                # Expand up
                new_y1 = y1
                for y in range(y1 - 1, max(0, y1 - box_h) - 1, -1):
                    row_visible = 0
                    for x in range(new_x1, new_x2, max(1, (new_x2 - new_x1) // 8)):
                        if pixels[x, y][3] > threshold:
                            row_visible += 1
                    if row_visible > 0:
                        new_y1 = y
                    elif y < y1 - 5:
                        break

                # Expand down
                new_y2 = y2
                for y in range(y2, min(img_h, y2 + box_h)):
                    row_visible = 0
                    for x in range(new_x1, new_x2, max(1, (new_x2 - new_x1) // 8)):
                        if pixels[x, y][3] > threshold:
                            row_visible += 1
                    if row_visible > 0:
                        new_y2 = y + 1
                    elif y > y2 + 5:
                        break

                r.bbox = (new_x1, new_y1, new_x2, new_y2)
            else:
                # For opaque backgrounds: expand to pixels that differ from bg
                avg_r = sum(c[0] for c in bg_samples) // len(bg_samples)
                avg_g = sum(c[1] for c in bg_samples) // len(bg_samples)
                avg_b = sum(c[2] for c in bg_samples) // len(bg_samples)
                diff_threshold = 40

                def _is_text_pixel(x, y):
                    pr, pg, pb, pa = pixels[x, y]
                    if pa < 100:
                        return False
                    diff = abs(pr - avg_r) + abs(pg - avg_g) + abs(pb - avg_b)
                    return diff > diff_threshold

                # Expand left
                new_x1 = x1
                for x in range(x1 - 1, sx1 - 1, -1):
                    col_text = 0
                    for y in range(y1, y2, max(1, box_h // 8)):
                        if _is_text_pixel(x, y):
                            col_text += 1
                    if col_text > 0:
                        new_x1 = x
                    elif x < new_x1 - 20:
                        break

                # Expand right
                new_x2 = x2
                for x in range(x2, sx2):
                    col_text = 0
                    for y in range(y1, y2, max(1, box_h // 8)):
                        if _is_text_pixel(x, y):
                            col_text += 1
                    if col_text > 0:
                        new_x2 = x + 1
                    elif x > new_x2 + 20:
                        break

                r.bbox = (new_x1, y1, new_x2, y2)

        return regions

    @staticmethod
    def _merge_nearby_regions(
        regions: list[TextRegion], img_w: int, img_h: int,
    ) -> list[TextRegion]:
        """Merge vertically adjacent text regions into blocks.

        When OCR returns separate regions for each line of a paragraph,
        merge them into a single block for better clearing and rendering.
        Regions are merged if they overlap horizontally and are close vertically.
        """
        if len(regions) <= 1:
            return regions

        # Sort by y position
        sorted_regions = sorted(regions, key=lambda r: r.bbox[1])
        merged = []
        used = set()

        for i, r in enumerate(sorted_regions):
            if i in used:
                continue

            x1, y1, x2, y2 = r.bbox
            texts = [r.text]
            translations = [r.translation]

            # Look for nearby regions to merge
            for j in range(i + 1, len(sorted_regions)):
                if j in used:
                    continue
                ox1, oy1, ox2, oy2 = sorted_regions[j].bbox
                # Use the CANDIDATE's line height, not the growing merged
                # block height — otherwise the threshold inflates as we merge
                line_h = oy2 - oy1

                # Check vertical proximity — only merge lines that are
                # clearly part of the same paragraph (within 0.5x line height).
                # Larger gaps usually mean separate UI elements.
                vert_gap = oy1 - y2
                if vert_gap > line_h * 0.5:
                    break  # too far, likely a separate UI element
                # Allow overlapping regions to merge (tightened boxes often overlap)
                if vert_gap < -max(line_h, y2 - y1):
                    continue  # completely contained, skip

                # Check horizontal overlap (at least 30% overlap)
                overlap_x1 = max(x1, ox1)
                overlap_x2 = min(x2, ox2)
                overlap_w = overlap_x2 - overlap_x1
                min_w = min(x2 - x1, ox2 - ox1)
                if min_w > 0 and overlap_w / min_w > 0.3:
                    # Merge: expand bbox
                    x1 = min(x1, ox1)
                    y2 = max(y2, oy2)
                    x2 = max(x2, ox2)
                    texts.append(sorted_regions[j].text)
                    translations.append(sorted_regions[j].translation)
                    used.add(j)

            merged_text = "\n".join(texts)
            merged_translation = "\n".join(t for t in translations if t)
            merged.append(TextRegion(
                text=merged_text,
                bbox=(x1, y1, x2, y2),
                translation=merged_translation,
            ))

        return merged

    @staticmethod
    def _to_base64(img: Image.Image) -> str:
        """Convert a PIL Image to base64 string."""
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def _parse_ocr_response(raw: str, img_w: int = 0, img_h: int = 0) -> list[TextRegion]:
        """Parse vision model JSON output into TextRegion list.

        Handles both standard {"bbox": [x1,y1,x2,y2]} format and
        Qwen's {"bbox_2d": [x1,y1,x2,y2]} normalized coordinate format.
        Auto-detects if coordinates are in [0, 1000] range and scales them.
        """
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        # Extract JSON array
        m = _JSON_RE.search(text)
        if not m:
            return []

        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []

        regions = []
        for item in data:
            if not isinstance(item, dict):
                continue
            t = item.get("text", "").strip()
            # Accept both "bbox" and "bbox_2d" (Qwen format)
            bbox = item.get("bbox") or item.get("bbox_2d", [])
            if not t or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                coords = tuple(float(v) for v in bbox)
            except (ValueError, TypeError):
                continue
            regions.append(TextRegion(text=t, bbox=coords))

        # Normalize coordinates from [0, 999] to pixel space
        # Qwen always returns normalized coords regardless of prompt instructions
        if regions and img_w > 0 and img_h > 0:
            max_x = max(max(r.bbox[0], r.bbox[2]) for r in regions)
            max_y = max(max(r.bbox[1], r.bbox[3]) for r in regions)

            # Detect normalized coords: any coord exceeds image bounds,
            # or all coords are in 0-999 range (typical Qwen output)
            is_normalized = (
                max_x > img_w * 1.1 or max_y > img_h * 1.1
                or (max_x <= 999 and max_y <= 999 and
                    (img_w > 999 or img_h > 999))
            )
            if is_normalized:
                # Qwen uses [0, 999] scale (1000 divisions)
                sx = img_w / 999.0
                sy = img_h / 999.0
                for r in regions:
                    x1, y1, x2, y2 = r.bbox
                    r.bbox = (
                        int(x1 * sx), int(y1 * sy),
                        int(x2 * sx), int(y2 * sy),
                    )
                log.debug("OCR coords normalized from [0,999] to [0,%dx%d]",
                          img_w, img_h)

        # Convert any remaining float coords to int
        for r in regions:
            r.bbox = tuple(int(v) for v in r.bbox)

        return regions

    # ── Translation ───────────────────────────────────────────────

    def translate_regions(
        self, regions: list[TextRegion], *, context: str = "",
    ) -> list[TextRegion]:
        """Translate each region's Japanese text to fit its bounding box.

        Uses smart translation: meaning over literal, concise phrasing,
        with a retry loop that asks the model to shorten if it doesn't fit.

        Deduplicates: same JP text is translated once.
        """
        cache: dict[str, str] = {}

        for region in regions:
            if not region.text:
                continue
            if region.text in cache:
                region.translation = cache[region.text]
                continue

            jp_text = region.text
            box_w = region.bbox[2] - region.bbox[0]
            box_h = region.bbox[3] - region.bbox[1]

            # Calculate actual character budget from bbox pixel dimensions
            # Use font metrics: at ~14px font, English chars average ~8px wide
            # Multi-line: estimate lines from bbox height
            est_font = min(max(int(box_h * 0.22), 10), 28)
            char_w = est_font * 0.58  # avg char width for proportional font
            chars_per_line = max(1, int(box_w / char_w))
            est_lines = max(1, int(box_h / (est_font * 1.3)))
            max_en_chars = chars_per_line * est_lines

            # Count JP lines to guide translation structure
            jp_lines = jp_text.split("\n")
            is_multiline = len(jp_lines) > 1
            jp_char_count = len(jp_text.replace("\n", ""))

            # Build translation prompt
            prompt = self._build_image_translate_prompt(
                jp_text, max_en_chars, chars_per_line, est_lines,
                is_multiline, jp_char_count,
            )

            # Translate with retry: if too long, ask to shorten
            translation = self._translate_for_bbox(
                jp_text, prompt, max_en_chars, box_w, box_h,
            )
            # Post-process: normalize newlines, strip junk, remove romaji
            translation = translation.replace("\\n", "\n")
            # Strip parenthetical romanized Japanese e.g. "(Ai no Utsuwa)"
            translation = re.sub(
                r'\s*\([A-Z][a-z]+(?: no| no| ni)? [A-Z][a-z]+\)', '',
                translation)
            translation = "\n".join(
                line.strip() for line in translation.split("\n")
            )
            region.translation = translation
            cache[jp_text] = translation

        return regions

    def _build_image_translate_prompt(
        self, jp_text: str, max_chars: int, chars_per_line: int,
        est_lines: int, is_multiline: bool, jp_char_count: int,
    ) -> str:
        """Build a context-aware translation prompt for image text."""
        parts = []

        # Base context — adult game, natural English
        parts.append(
            "You are translating text from a Japanese adult (18+) RPG game image. "
            "Translate faithfully — do NOT censor or sanitize sexual/adult content. "
            "Use natural, concise English like a real English game would."
        )

        # Detect content type — check if all lines are short labels
        lines_list = jp_text.split("\n")
        all_short = all(len(line.strip()) <= 8 for line in lines_list)
        if jp_char_count <= 6 or (all_short and len(lines_list) <= 3):
            parts.append(
                "\nThis is a game UI LABEL. Use standard English game terms: "
                "はじめから=New Game, つづきから=Continue, "
                "オプション=Options, セーブ=Save, ロード=Load, "
                "装備=Equip, ステータス=Status, スキル=Skills, "
                "アイテム=Items, もどる=Back, やめる=Quit, "
                "ゲーム終了=Quit Game, 心の力=Spirit, "
                "負け癖=Losing Streak, 屈服=Submission, "
                "立ち向かう=Fight, 逃げる=Run, 戦う=Fight, "
                "攻撃=Attack, 防御=Defend, 魔法=Magic, "
                "たたかう=Fight, にげる=Run."
            )
        elif is_multiline:
            parts.append(
                "\nThis is tutorial/description text. "
                "Translate for MEANING, not word-for-word. "
                "Be EXTREMELY concise — like tooltip text. "
                "Each line should be SHORT (under 30 chars if possible). "
                "Cut filler, combine ideas, use abbreviations freely."
            )
        else:
            parts.append(
                "\nThis is game UI text. Be concise — "
                "prioritize clarity and brevity."
            )

        # Space constraint — the critical part
        parts.append(
            f"\nSPACE CONSTRAINT: Max ~{max_chars} characters total "
            f"(~{chars_per_line} chars/line, {est_lines} lines). "
            f"This MUST fit in the same space as the Japanese. "
            f"If too long: cut words, use abbreviations "
            f"(HP, MP, ATK, DEF, LV, EXP, MAX), "
            f"drop particles/filler. SHORTER IS BETTER."
        )

        if is_multiline:
            parts.append(
                "\nUse newlines (\\n) to separate distinct topics/sections."
            )

        parts.append(
            "\nRULES:"
            "\n- Do NOT include romanized Japanese (no romaji). "
            "Translate EVERYTHING to English."
            "\n- No parenthetical Japanese (no 'Love Vessel (Ai no Utsuwa)')."
            "\n- 射精/射結 = Ejaculation. Do not censor."
            "\n- Return ONLY the English translation. No quotes, no explanation."
        )

        return "\n".join(parts)

    def _translate_for_bbox(
        self, jp_text: str, hint: str, max_chars: int,
        box_w: int, box_h: int,
    ) -> str:
        """Translate with retry loop — if too long, ask model to shorten."""
        # First attempt
        try:
            result = self.client.translate_name(jp_text, hint=hint)
            if not result:
                return jp_text
        except Exception:
            return jp_text

        # Check if it fits using font metrics
        fits = self._check_text_fits(result, box_w, box_h)

        if fits:
            return result

        # Too long — retry with explicit shorten request (1 retry)
        shorten_hint = (
            f"{hint}\n\n"
            f"Your previous translation was TOO LONG:\n\"{result}\"\n"
            f"Make it SHORTER. Cut unnecessary words. "
            f"Use abbreviations (HP, MP, ATK, DEF, LV, EXP). "
            f"Max ~{max_chars} characters. Prioritize fitting over completeness."
        )
        try:
            shorter = self.client.translate_name(jp_text, hint=shorten_hint)
            if shorter and len(shorter) < len(result):
                return shorter
        except Exception:
            pass

        return result

    @staticmethod
    def _check_text_fits(text: str, box_w: int, box_h: int,
                         min_size: int = 10) -> bool:
        """Check if translated text fits in the bounding box at a readable size.

        Uses the same logic as _fit_text — tries sizes from max down to min_size.
        Returns True if it fits at min_size or above.
        """
        font_path = _SYSTEM_FONT_BOLD or _SYSTEM_FONT
        if not font_path:
            return True

        # Estimate max font from line count (same as _preserve_region)
        orig_lines = max(1, text.count("\n") + 1)
        est_line_h = box_h / orig_lines
        max_font = min(int(est_line_h * 0.75), 28)

        try:
            font, lines = ImageTranslator._fit_text(
                text, box_w, box_h, min_size=min_size, max_font=max_font)
            block = "\n".join(lines)
            dummy = Image.new("RGB", (1, 1))
            draw = ImageDraw.Draw(dummy)
            bb = draw.textbbox((0, 0), block, font=font)
            text_h = bb[3] - bb[1]
            # Fits if text height is within box and font is still readable
            return text_h <= box_h and font.size >= min_size
        except Exception:
            return True

    # ── Image rendering ───────────────────────────────────────────

    def render_translated(
        self, image_path: str, regions: list[TextRegion], output_path: str,
        mode: str = RENDER_PRESERVE,
    ):
        """Render translated text onto an image.

        Args:
            image_path: Path to original image.
            regions: OCR regions with translations.
            output_path: Where to save the rendered image.
            mode: "preserve" (icon-preserving) or "clean" (white boxes on black).
        """
        if mode == self.RENDER_PRESERVE:
            self._render_preserve(image_path, regions, output_path)
        else:
            self._render_clean(image_path, regions, output_path)

    def _render_preserve(
        self, image_path: str, regions: list[TextRegion], output_path: str,
    ):
        """Icon-preserving render: keep original background, clear text, draw English.

        For images with icons on the left and text on the right (common in
        RPG Maker menu/shop/mission images), this:
        1. Detects where the icon ends
        2. Clears only the text region (makes transparent)
        3. Cleans stray text pixels that bleed into the icon zone
        4. Draws English text in the original text color with shadow

        For two-state sprite sheets (title/menu buttons), uses a dedicated path
        that skips icon detection — clears entire text region, centers English text,
        and preserves top/bottom state colors.
        """
        img = self.open_image(image_path).convert("RGBA")
        img_w, img_h = img.size

        # Detect two-state sprite sheet
        is_two_state, merged = self._detect_two_state(regions, img_h)

        # If OCR only found one half but the image looks like a two-state
        # sprite (has visible content in both halves), mirror the regions
        if not is_two_state and regions:
            is_two_state, merged = self._infer_two_state(
                img, regions, img_w, img_h)

        if is_two_state and merged:
            # Two-state sprite sheets: dedicated render (no icon detection)
            # Upscale small images for better text rendering quality
            orig_size = (img_w, img_h)
            scale = 1
            if max(img_w, img_h) < 300:
                scale = max(2, 600 // max(img_w, img_h))
                img = img.resize(
                    (img_w * scale, img_h * scale),
                    Image.Resampling.LANCZOS,
                )
                img_w, img_h = img.size

            for text, bbox_top, bbox_bot in merged:
                if not text:
                    continue
                # Scale bboxes if upscaled
                if scale > 1:
                    bbox_top = tuple(v * scale for v in bbox_top)
                    bbox_bot = tuple(v * scale for v in bbox_bot)
                # Render top half (usually selected/highlighted state)
                self._render_sprite_text(img, bbox_top, text, img_w)
                # Render bottom half (usually unselected/dim state)
                self._render_sprite_text(img, bbox_bot, text, img_w)

            # Resize back to original dimensions if we upscaled
            if scale > 1:
                img = img.resize(orig_size, Image.Resampling.LANCZOS)
        else:
            for region in regions:
                if not region.translation:
                    continue
                self._preserve_region(img, region.bbox, region.translation, img_w)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "PNG")

    def _render_sprite_text(
        self, img: Image.Image, bbox: tuple, text: str, img_w: int,
    ):
        """Render English text for a two-state sprite sheet button.

        Unlike _preserve_region, this skips icon detection entirely —
        these are text-only images (title buttons, menu commands).
        Clears text pixels in the bbox, then draws centered English text
        with a glow/outline effect matching the original style.

        RPG Maker title buttons typically have:
        - A colored text core (pink for selected, gray for unselected)
        - A soft light-gray glow/outline around the text (the "cloud" effect)
        This method recreates that style using Pillow's stroke rendering.
        """
        x1, y1, x2, y2 = bbox
        box_w = x2 - x1
        box_h = y2 - y1
        if box_w < 10 or box_h < 10:
            return

        # Sample outline (glow) and core text colors before clearing
        outline_color, core_color = self._sample_sprite_colors(img, bbox)

        # Clear all visible pixels in the bbox
        pixels = img.load()
        for x in range(x1, x2):
            for y in range(y1, y2):
                r, g, b, a = pixels[x, y]
                if a > 10:
                    pixels[x, y] = (0, 0, 0, 0)

        # Calculate stroke width relative to image size (the "cloud" glow)
        stroke_w = max(2, min(box_h // 12, 8))

        # Draw centered English text with glow outline
        draw = ImageDraw.Draw(img)
        # Account for stroke in available space
        available_w = box_w - 8 - stroke_w * 2
        available_h = box_h - 4 - stroke_w * 2

        # Sprite buttons: always single line — shrink font until it fits
        font = self._fit_single_line(text, available_w, available_h)
        tb = draw.textbbox((0, 0), text, font=font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]

        # Center text in the bbox
        tx = x1 + (box_w - tw) // 2
        ty = y1 + (box_h - th) // 2

        # Draw with stroke (outline/glow) + fill (core color)
        draw.text(
            (tx, ty), text, font=font,
            fill=core_color,
            stroke_width=stroke_w,
            stroke_fill=outline_color,
        )

    @staticmethod
    def _sample_sprite_colors(
        img: Image.Image, bbox: tuple,
    ) -> tuple[tuple, tuple]:
        """Sample outline (glow) and core text colors from a sprite text region.

        Returns (outline_color, core_color):
        - outline_color: the glow/cloud effect (usually light gray)
        - core_color: the actual text fill (pink, dark gray, etc.)
        """
        x1, y1, x2, y2 = bbox
        crop = img.crop((x1, y1, x2, y2)).convert("RGBA")

        from collections import Counter

        # Collect all visible pixels
        visible = []
        for x in range(crop.size[0]):
            for y in range(crop.size[1]):
                r, g, b, a = crop.getpixel((x, y))
                if a > 100:
                    visible.append((r, g, b))

        if not visible:
            return ((220, 220, 220, 255), (200, 100, 150, 255))

        # Quantize to find color groups
        quantized = [(r // 32 * 32, g // 32 * 32, b // 32 * 32)
                     for r, g, b in visible]
        counts = Counter(quantized).most_common(10)

        # Most common is typically the outline/glow (light gray)
        outline_rgb = counts[0][0]
        outline_color = (*outline_rgb, 200)  # slightly transparent for soft glow

        # Find the core color: the most common color that's distinctly different
        # from the outline (different hue or significantly different brightness)
        core_color = (200, 100, 150, 255)  # fallback pink
        for rgb, count in counts[1:]:
            r, g, b = rgb
            or_, og, ob = outline_rgb
            # Different if: saturation differs, or brightness differs by 40+
            brightness_diff = abs((r + g + b) // 3 - (or_ + og + ob) // 3)
            max_diff = max(abs(r - or_), abs(g - og), abs(b - ob))
            if brightness_diff > 30 or max_diff > 40:
                core_color = (r, g, b, 255)
                break

        return (outline_color, core_color)

    def _preserve_region(
        self, img: Image.Image, bbox: tuple, text: str, img_w: int,
    ):
        """Clear Japanese text in a bbox and draw English, preserving background.

        Three strategies based on background type:
        1. Fully transparent (alpha ~0): clear all visible pixels (sprites/UI)
        2. Fully opaque (alpha ~255): fill with sampled background color (CGs)
        3. Semi-transparent overlay: clear only text-colored pixels, keep bg
        """
        x1, y1, x2, y2 = bbox
        box_w = x2 - x1
        box_h = y2 - y1
        if box_w < 10 or box_h < 10:
            return

        # Analyze background and text in this region
        bg_type, bg_data = self._analyze_region_bg(img, bbox)
        text_color = self._sample_text_color(img, bbox)

        pixels = img.load()
        img_h = img.size[1]
        clear_x = x1  # default draw start; transparent branch may adjust

        # --- Compute text layout FIRST so we know the actual text height ---
        draw = ImageDraw.Draw(img)
        draw_x = x1
        available_w = box_w - 8
        available_h = box_h - 4

        orig_line_count = max(1, text.count("\n") + 1)
        estimated_line_h = box_h / orig_line_count
        max_font_size = min(int(estimated_line_h * 0.65), 18)

        font, lines = self._fit_text(
            text, available_w, available_h, max_font=max_font_size)
        text_block = "\n".join(lines)
        tb = draw.textbbox((0, 0), text_block, font=font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]

        # Text position: top-aligned, left or centered
        if box_w > 200 and tw < box_w * 0.6:
            tx = x1 + (box_w - tw) // 2
        else:
            tx = draw_x + 4
        ty = y1 + 2

        # --- Clear/fill background, then draw text ---
        if bg_type == "opaque":
            # Only fill the area the English text needs (+ small padding),
            # not the entire bbox — avoids big opaque patches over artwork
            pad = 4
            fill_y2 = min(y2, ty + th + pad)
            bg_color, bg_gradient = bg_data
            for x in range(x1, x2):
                for y in range(y1, fill_y2):
                    fill = self._interpolate_bg(
                        bg_color, bg_gradient, x, y, x1, y1, x2, y2)
                    pixels[x, y] = fill

            bg_r, bg_g, bg_b, _ = bg_color
            bg_brightness = (bg_r + bg_g + bg_b) / 3
            txt_r, txt_g, txt_b = text_color[:3]
            if abs(bg_brightness - (txt_r+txt_g+txt_b)/3) < 60:
                text_color = (40, 40, 40, 255) if bg_brightness > 128 else (220, 220, 220, 255)
            stroke_w = max(1, box_h // 40)
            stroke_color = (255, 255, 255, 120) if bg_brightness > 128 else (0, 0, 0, 120)
            draw.text((tx, ty), text_block, font=font, fill=text_color,
                      stroke_width=stroke_w, stroke_fill=stroke_color)

        elif bg_type == "semi":
            bg_alpha = bg_data
            scan_threshold = 200
            clear_threshold = bg_alpha + 5

            max_scan = 60
            cy1, cy2 = y1, y2
            gap_limit = 5

            # Expand bottom
            gap = 0
            for y in range(y2, min(img_h, y2 + max_scan)):
                found = any(
                    pixels[x, y][3] > scan_threshold
                    for x in range(x1, x2, max(1, (x2 - x1) // 30))
                )
                if found:
                    cy2 = y + 1
                    gap = 0
                else:
                    gap += 1
                    if gap > gap_limit:
                        break

            # Expand top
            gap = 0
            for y in range(y1 - 1, max(-1, y1 - max_scan), -1):
                found = any(
                    pixels[x, y][3] > scan_threshold
                    for x in range(x1, x2, max(1, (x2 - x1) // 30))
                )
                if found:
                    cy1 = y
                    gap = 0
                else:
                    gap += 1
                    if gap > gap_limit:
                        break

            x_margin = 10
            cx1 = max(0, x1 - x_margin)
            cx2 = min(img_w, x2 + x_margin)
            for x in range(cx1, cx2):
                for y in range(max(0, cy1), min(img_h, cy2)):
                    if pixels[x, y][3] > clear_threshold:
                        pixels[x, y] = (255, 255, 255, bg_alpha)

            stroke_w = max(1, box_h // 30)
            draw.text((tx, ty), text_block, font=font, fill=text_color,
                      stroke_width=stroke_w, stroke_fill=(255, 255, 255, 100))

        else:
            # Fully transparent — clear all visible pixels in bbox
            if box_w > 100 and box_h < box_w * 0.6:
                icon_right = self._find_icon_right_edge(img, bbox)
                clear_x = max(x1, icon_right)
                tx = max(tx, clear_x + 4)

            for x in range(clear_x, x2):
                for y in range(y1, y2):
                    if pixels[x, y][3] > 10:
                        pixels[x, y] = (0, 0, 0, 0)

            shadow_color = (0, 0, 0, 180)
            draw.text((tx+1, ty+1), text_block, font=font, fill=shadow_color)
            draw.text((tx, ty), text_block, font=font, fill=text_color)

    @staticmethod
    def _analyze_region_bg(
        img: Image.Image, bbox: tuple,
    ) -> tuple[str, object]:
        """Analyze what kind of background a text region sits on.

        Returns:
            ("transparent", None) — fully transparent bg (sprites)
            ("semi", median_alpha) — semi-transparent overlay
            ("opaque", (bg_color, bg_gradient)) — solid/gradient background
        """
        x1, y1, x2, y2 = bbox
        img_w, img_h = img.size
        pixels = img.load()

        # Sample pixels from edges of the bbox and slightly outside
        margin = 3
        edge_alphas = []
        edge_colors = []

        # Sample edge pixels
        sample_points = []
        for x in range(max(0, x1), min(img_w, x2), max(1, (x2-x1) // 10)):
            sample_points.append((x, max(0, y1 - margin)))
            sample_points.append((x, min(img_h - 1, y2 + margin)))
        for y in range(max(0, y1), min(img_h, y2), max(1, (y2-y1) // 6)):
            sample_points.append((max(0, x1 - margin), y))
            sample_points.append((min(img_w - 1, x2 + margin), y))

        for sx, sy in sample_points:
            r, g, b, a = pixels[sx, sy]
            edge_alphas.append(a)
            if a > 30:
                edge_colors.append((r, g, b, a, sx, sy))

        if not edge_alphas:
            return ("transparent", None)

        median_alpha = sorted(edge_alphas)[len(edge_alphas) // 2]

        # Fully transparent
        if median_alpha < 30:
            return ("transparent", None)

        # Semi-transparent (overlay images: alpha 30-220)
        if median_alpha < 220:
            return ("semi", median_alpha)

        # Opaque — calculate background color + gradient
        opaque = [(r, g, b) for r, g, b, a, x, y in edge_colors if a > 200]
        if not opaque:
            return ("semi", median_alpha)

        avg_r = sum(c[0] for c in opaque) // len(opaque)
        avg_g = sum(c[1] for c in opaque) // len(opaque)
        avg_b = sum(c[2] for c in opaque) // len(opaque)
        bg_color = (avg_r, avg_g, avg_b, 255)

        # Detect horizontal gradient
        left_px = [(r, g, b) for r, g, b, a, x, y in edge_colors
                   if a > 200 and x < (x1 + x2) // 2]
        right_px = [(r, g, b) for r, g, b, a, x, y in edge_colors
                    if a > 200 and x >= (x1 + x2) // 2]

        bg_gradient = (0.0, 0.0, 0.0)
        if left_px and right_px:
            lr = sum(c[0] for c in left_px) // len(left_px)
            lg = sum(c[1] for c in left_px) // len(left_px)
            lb = sum(c[2] for c in left_px) // len(left_px)
            rr = sum(c[0] for c in right_px) // len(right_px)
            rg = sum(c[1] for c in right_px) // len(right_px)
            rb = sum(c[2] for c in right_px) // len(right_px)
            span = max(1, x2 - x1)
            bg_gradient = ((rr-lr)/span, (rg-lg)/span, (rb-lb)/span)
            bg_color = (lr, lg, lb, 255)

        return ("opaque", (bg_color, bg_gradient))

    # _sample_background removed — replaced by _analyze_region_bg above

    @staticmethod
    def _interpolate_bg(
        bg_color: tuple, bg_gradient: tuple,
        x: int, y: int,
        x1: int, y1: int, x2: int, y2: int,
    ) -> tuple:
        """Calculate background fill color at a specific pixel position.

        Supports horizontal gradients detected by _sample_background().
        """
        base_r, base_g, base_b, base_a = bg_color
        dr, dg, db = bg_gradient
        offset_x = x - x1
        r = max(0, min(255, int(base_r + dr * offset_x)))
        g = max(0, min(255, int(base_g + dg * offset_x)))
        b = max(0, min(255, int(base_b + db * offset_x)))
        return (r, g, b, base_a)

    @staticmethod
    def _find_icon_right_edge(
        img: Image.Image, bbox: tuple,
    ) -> int:
        """Find where the icon area ends within a bounding box.

        Scans columns in the bottom 2/3 of the bbox to find the transition
        from icon content to empty space. Returns the x coordinate where
        text should start.
        """
        x1, y1, x2, y2 = bbox
        box_w = x2 - x1
        box_h = y2 - y1

        # Only scan if the box is wide enough to have an icon + text layout
        if box_w < 100:
            return x1  # too narrow, no icon expected

        pixels = img.load()
        # Scan the icon zone: first 30% of box width
        scan_start = x1 + int(box_w * 0.15)
        scan_end = x1 + int(box_w * 0.40)
        y_scan_start = y1 + box_h // 3  # bottom 2/3 only

        # Scan from right to left for last column with significant content
        for x in range(scan_end, scan_start, -1):
            non_transparent = 0
            check_h = y2 - y_scan_start
            for y in range(y_scan_start, y2):
                r, g, b, a = pixels[x, y]
                if a > 30:
                    non_transparent += 1
            if check_h > 0 and non_transparent > check_h * 0.15:
                return x + 5  # small gap after icon

        return x1  # no icon detected, text starts at bbox left

    @staticmethod
    def _sample_text_color(
        img: Image.Image, bbox: tuple,
    ) -> tuple:
        """Sample the dominant text color from a region.

        Uses two strategies:
        1. Look for high-saturation pixels first (colored text on any bg)
        2. Fall back to most common high-alpha pixel (white/gray text on dark bg)
        """
        x1, y1, x2, y2 = bbox
        crop = img.crop((x1, y1, x2, y2)).convert("RGBA")

        from collections import Counter

        # Collect all opaque pixels
        opaque_pixels = []
        for pixel in crop.getdata():
            r, g, b, a = pixel
            if a > 150:
                opaque_pixels.append((r, g, b))

        if not opaque_pixels:
            return (255, 255, 50, 255)  # fallback: yellow

        # Strategy 1: find colored (non-gray) pixels — text is usually
        # colored while backgrounds are white/gray/black
        colored = []
        for r, g, b in opaque_pixels:
            # Saturation check: at least one channel differs by 50+ from the mean
            mean = (r + g + b) / 3
            max_diff = max(abs(r - mean), abs(g - mean), abs(b - mean))
            if max_diff > 40:
                colored.append((r, g, b))

        if colored and len(colored) > len(opaque_pixels) * 0.01:
            quantized = [(r//32*32, g//32*32, b//32*32, 255)
                         for r, g, b in colored]
            return Counter(quantized).most_common(1)[0][0]

        # Strategy 2: find pixels that contrast with the edge background
        # Sample edges to estimate bg
        w, h = crop.size
        edge_px = []
        for x in range(0, w, max(1, w//8)):
            if h > 2:
                edge_px.append(crop.getpixel((x, 0))[:3])
                edge_px.append(crop.getpixel((x, h-1))[:3])
        if edge_px:
            bg_r = sum(c[0] for c in edge_px) // len(edge_px)
            bg_g = sum(c[1] for c in edge_px) // len(edge_px)
            bg_b = sum(c[2] for c in edge_px) // len(edge_px)
            # Find pixels that differ from background
            contrasting = [(r, g, b) for r, g, b in opaque_pixels
                           if abs(r-bg_r)+abs(g-bg_g)+abs(b-bg_b) > 80]
            if contrasting:
                quantized = [(r//32*32, g//32*32, b//32*32, 255)
                             for r, g, b in contrasting]
                return Counter(quantized).most_common(1)[0][0]

        # Strategy 3: most common non-gray opaque pixel
        quantized = [(r//32*32, g//32*32, b//32*32, 255)
                     for r, g, b in opaque_pixels]
        return Counter(quantized).most_common(1)[0][0]

    def _render_clean(
        self, image_path: str, regions: list[TextRegion], output_path: str,
    ):
        """Clean render: white boxes on black background (legacy mode).

        Handles RPG Maker's two-state sprite sheet convention:
        many system/menu images are split vertically — top half = one state
        (unselected), bottom half = other state (selected).
        """
        img = self.open_image(image_path).convert("RGB")
        img_w, img_h = img.size

        # Start with a clean black background (same dimensions as original)
        result = Image.new("RGB", (img_w, img_h), (0, 0, 0))
        draw = ImageDraw.Draw(result)

        # Detect two-state sprite sheet
        is_two_state, merged = self._detect_two_state(regions, img_h)

        if is_two_state and merged:
            for text, bbox_top, bbox_bot in merged:
                if not text:
                    continue
                self._draw_state_box(
                    draw, text, bbox_top, img_w,
                    text_color=(20, 20, 20), border_color=(60, 60, 60),
                )
                self._draw_state_box(
                    draw, text, bbox_bot, img_w,
                    text_color=(200, 30, 30), border_color=(200, 30, 30),
                )
        else:
            for region in regions:
                if not region.translation:
                    continue
                x1, y1, x2, y2 = region.bbox
                if (x2 - x1) < 10 or (y2 - y1) < 10:
                    continue

                is_selected = self._has_warm_pixels(img, region.bbox)
                text_color = (200, 30, 30) if is_selected else (20, 20, 20)
                border_color = (200, 30, 30) if is_selected else (60, 60, 60)

                self._draw_state_box(
                    draw, region.translation, region.bbox, img_w,
                    text_color=text_color, border_color=border_color,
                )

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        result.save(output_path, "PNG")

    def _draw_state_box(
        self, draw: ImageDraw.ImageDraw, text: str,
        bbox: tuple, img_w: int, *,
        text_color: tuple, border_color: tuple,
    ):
        """Draw a white rounded box with centered text at the bbox position."""
        x1, y1, x2, y2 = bbox
        box_w = x2 - x1
        box_h = y2 - y1
        if box_w < 10 or box_h < 10:
            return

        box_fill = (255, 255, 255)
        radius = min(box_h // 3, box_w // 4, 12)

        draw.rounded_rectangle(
            [x1, y1, x2, y2],
            radius=radius, fill=box_fill, outline=border_color, width=2,
        )

        font, lines = self._fit_text(text, box_w - 8, box_h - 4)
        text_block = "\n".join(lines)
        tb = draw.textbbox((0, 0), text_block, font=font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]

        tx = x1 + (box_w - tw) // 2
        ty = y1 + (box_h - th) // 2
        draw.text((tx, ty), text_block, font=font, fill=text_color)

    # ── Verify loop ──────────────────────────────────────────────

    def verify_render(self, rendered_path: str) -> dict:
        """Send rendered image to model for quality check.

        Returns: {"ok": bool, "issues": list[str]}
        Issues might be: "Japanese remnants visible", "text cut off",
        "text overlaps icon", "text too small to read"
        """
        img = Image.open(rendered_path)
        b64 = self._to_base64(img)

        raw = self.client.vision_chat(b64, _VERIFY_PROMPT, system=_VERIFY_SYSTEM)

        # Parse JSON response
        try:
            # Strip markdown fences
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines)

            # Extract JSON object
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                result = json.loads(m.group())
                return {
                    "ok": bool(result.get("ok", False)),
                    "issues": list(result.get("issues", [])),
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # If we can't parse the response, assume it's okay
        return {"ok": True, "issues": []}

    # ── Two-state detection ──────────────────────────────────────

    @staticmethod
    def _detect_two_state(
        regions: list[TextRegion], img_h: int
    ) -> tuple[bool, list[tuple[str, tuple, tuple]]]:
        """Detect RPG Maker two-state sprite sheet pattern.

        Many menu/system images are split vertically: top half = unselected,
        bottom half = selected. OCR finds the same text in both halves.

        Returns:
            (is_two_state, merged_list) where merged_list contains
            (translation, top_bbox, bottom_bbox) tuples.
        """
        if len(regions) < 2:
            return False, []

        half_y = img_h // 2

        top_only = []
        bot_only = []
        for r in regions:
            cy = (r.bbox[1] + r.bbox[3]) // 2
            if cy < half_y:
                top_only.append(r)
            else:
                bot_only.append(r)

        top_only.sort(key=lambda r: r.bbox[1])
        bot_only.sort(key=lambda r: r.bbox[1])

        if len(top_only) != len(bot_only) or len(top_only) == 0:
            return False, []

        merged = []
        matched = 0
        for t, b in zip(top_only, bot_only):
            is_match = (t.text == b.text
                        or (t.translation and t.translation == b.translation))
            if is_match:
                matched += 1
            translation = t.translation or b.translation or ""
            if is_match:
                merged.append((translation, t.bbox, b.bbox))

        if matched < len(top_only) * 0.5:
            return False, []

        return True, merged

    @staticmethod
    def _infer_two_state(
        img: Image.Image, regions: list[TextRegion],
        img_w: int, img_h: int,
    ) -> tuple[bool, list[tuple[str, tuple, tuple]]]:
        """Infer two-state sprite when OCR only found one half.

        Checks if the image has visible content in both halves (top and
        bottom). If OCR regions are only in one half but both halves have
        pixels, this is likely a two-state sprite where the model missed
        the lighter/fainter half. Mirror each region to the other half
        and use full-width bboxes covering each half.
        """
        half_y = img_h // 2
        if half_y < 20:
            return False, []

        # Check if all OCR regions are in one half only
        in_top = [r for r in regions if (r.bbox[1] + r.bbox[3]) // 2 < half_y]
        in_bot = [r for r in regions if (r.bbox[1] + r.bbox[3]) // 2 >= half_y]

        if in_top and in_bot:
            return False, []  # regions in both halves — not a missed case

        # Check both halves have visible pixels
        pixels = img.load()
        margin = 5

        def _has_content(y_start, y_end):
            count = 0
            step = max(1, (y_end - y_start) // 20)
            x_step = max(1, img_w // 20)
            for y in range(y_start + margin, y_end - margin, step):
                for x in range(margin, img_w - margin, x_step):
                    r, g, b, a = pixels[x, y]
                    if a > 50:
                        count += 1
            return count > 3

        top_has = _has_content(0, half_y)
        bot_has = _has_content(half_y, img_h)

        if not (top_has and bot_has):
            return False, []  # one half is empty — not two-state

        # Mirror: create full-width bboxes for each half
        source = in_top or in_bot
        merged = []
        for r in source:
            text = r.translation or r.text
            # Use full-width bboxes with small margins for each half
            top_bbox = (margin, margin, img_w - margin, half_y - margin)
            bot_bbox = (margin, half_y + margin, img_w - margin, img_h - margin)
            merged.append((text, top_bbox, bot_bbox))

        return True, merged

    # ── Color detection helpers ──────────────────────────────────

    @staticmethod
    def _has_warm_pixels(img: Image.Image, bbox: tuple) -> bool:
        """Detect if a region contains pink/red/warm-colored text pixels."""
        x1, y1, x2, y2 = bbox
        crop = img.crop((x1, y1, x2, y2)).convert("RGB")

        max_sample = 80
        w, h = crop.size
        if w > max_sample or h > max_sample:
            scale = max_sample / max(w, h)
            crop = crop.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )

        pixels = list(crop.getdata())
        if not pixels:
            return False

        warm_count = 0
        for r, g, b in pixels:
            if r > 140 and r > g * 1.4 and r > b * 1.2:
                warm_count += 1

        return warm_count > len(pixels) * 0.03

    # ── Text fitting ─────────────────────────────────────────────

    @staticmethod
    def _fit_single_line(
        text: str, max_w: int, max_h: int, min_size: int = 6,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Find the largest font size that fits text on a single line."""
        font_path = _SYSTEM_FONT_BOLD or _SYSTEM_FONT
        if not font_path:
            return ImageFont.load_default()

        max_size = max(min_size, int(max_h * 0.9))
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)

        for size in range(max_size, min_size - 1, -1):
            font = ImageFont.truetype(font_path, size)
            bb = draw.textbbox((0, 0), text, font=font)
            if (bb[2] - bb[0]) <= max_w and (bb[3] - bb[1]) <= max_h:
                return font

        return ImageFont.truetype(font_path, min_size)

    @staticmethod
    def _fit_text(
        text: str, box_w: int, box_h: int, min_size: int = 8,
        max_font: int = 0,
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
        """Find font size and line wrapping that fits text within a bounding box.

        Args:
            max_font: If > 0, caps the font size (useful for matching original text scale).
        """
        font_path = _SYSTEM_FONT_BOLD or _SYSTEM_FONT
        if not font_path:
            font = ImageFont.load_default()
            return font, [text]

        max_size = max(min_size, int(box_h * 0.8))
        if max_font > 0:
            max_size = min(max_size, max_font)
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)

        for size in range(max_size, min_size - 1, -1):
            font = ImageFont.truetype(font_path, size)
            lines = _wrap_text(draw, text, font, box_w - 4)
            block = "\n".join(lines)
            bb = draw.textbbox((0, 0), block, font=font)
            if (bb[2] - bb[0]) <= box_w and (bb[3] - bb[1]) <= box_h:
                return font, lines

        font = ImageFont.truetype(font_path, min_size)
        lines = _wrap_text(draw, text, font, box_w - 4)
        return font, lines


def _wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int
) -> list[str]:
    """Word-wrap text to fit within max_width pixels.

    Respects explicit newlines — each \\n forces a line break.
    Then wraps long lines within max_width.
    """
    # Normalize: convert literal \n strings to actual newlines
    text = text.replace("\\n", "\n")

    # Split on explicit newlines first, then wrap each paragraph
    paragraphs = text.split("\n")
    all_lines = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        words = para.split()
        if not words:
            continue

        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            bb = draw.textbbox((0, 0), test, font=font)
            if (bb[2] - bb[0]) <= max_width:
                current = test
            else:
                all_lines.append(current)
                current = word
        all_lines.append(current)

    if not all_lines:
        return [text.strip()]

    lines = all_lines
    return lines
