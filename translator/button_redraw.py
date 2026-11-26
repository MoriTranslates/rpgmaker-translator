"""Redraw picture buttons with English text (lessons from game 2, item 17).

Games like My Happiness 2 draw choice text into button images
(kz_PictureChoices: ``img/choices/C_はい.rpgmvp``). Translating them by hand
took about 20 minutes per game. The steps here:

1. **Read**: the Japanese label comes from the file name (``C_はい`` -> はい)
   or, if the name has none, from OCR. Labels go to a CSV the user can edit.
2. **Translate**: the English column is filled by the AI or by hand.
3. **Blank**: buttons of one style (same size and look) are stacked and a
   clean, text-free background is rebuilt from them, per pixel, from the
   buttons that have no text there. The text colours, outline and size are
   measured from the originals.
4. **Draw**: every button of that style gets the English on the same blank,
   so the whole game uses one consistent button.

Writing into the game keeps the original under ``img_original/`` (the same
backup Translate Images uses, so patches ship the new buttons) and replaces
each file through a verified temp file.

Pillow only: numpy isn't a dependency of the app.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import JAPANESE_RE
from .image_translator import (ENCRYPTED_EXTS, _find_font, decrypt_rpgmvp,
                               encrypt_to_rpgmvp)

log = logging.getLogger(__name__)

# A style needs this many buttons to rebuild its background from the others.
MIN_GROUP = 3
# At most this many buttons feed one blank (time grows with the count).
MAX_BLANK_SOURCES = 40
BUTTON_EXTS = (".png",) + ENCRYPTED_EXTS
CSV_COLUMNS = ("file", "japanese", "english")

_DIFF_TEXT = 40      # channel difference from the blank that counts as text
_EDGE = 40           # edge strength that counts as a stroke


# ── 1. Labels ────────────────────────────────────────────────────

_LEADING_CODE_RE = re.compile(r"^[0-9A-Za-z]+")


def label_from_filename(filename: str) -> str:
    """Japanese label in a button file name, or "" if it has none.

    ``C_H02_二人のセックスを見たい。.rpgmvp`` -> ``二人のセックスを見たい。``
    ``Button01朝_02会話.rpgmvp`` -> ``会話`` (last part with Japanese, minus
    a leading number or code).
    ``C_二人の_セックス.png`` -> ``二人の_セックス``: parts with no ASCII
    letters or digits before the last one belong to the label too.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    parts = stem.split("_")
    last = max((i for i, p in enumerate(parts) if JAPANESE_RE.search(p)), default=-1)
    if last < 0:
        return ""
    first = last
    while first > 0 and JAPANESE_RE.search(parts[first - 1]) and not re.search(
            r"[A-Za-z0-9]", parts[first - 1]):
        first -= 1
    head = _LEADING_CODE_RE.sub("", parts[first]).strip()
    if not JAPANESE_RE.search(head):
        head = parts[first].strip()
    return "_".join([head] + parts[first + 1:last + 1]).strip()


_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")


def split_ocr_text(text: str) -> tuple[str, str]:
    """OCR text of a button -> (japanese, english already on the button).

    Some games print the English under the Japanese (``会話
-talk-``); that
    is the game's own wording, so it is used as the English. A button with
    English and no Japanese gives ("", english): nothing to translate.
    """
    jp, en = [], []
    for part in (text or "").split():
        if JAPANESE_RE.search(part):
            jp.append(part)
        else:
            en.extend(_LATIN_WORD_RE.findall(part))
    english = " ".join(en)
    if english:
        english = english[0].upper() + english[1:]
    return " ".join(jp), english


def clean_button_labels(raw) -> dict[str, list[str]]:
    """Saved labels as {rel: [japanese, english]} (hand-edited files may differ)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if (isinstance(k, str) and k.strip() and isinstance(v, (list, tuple))
                and len(v) == 2 and all(isinstance(x, str) for x in v)):
            out[k] = [v[0], v[1]]
    return out


def write_labels_csv(path: str, rows: list[tuple[str, str, str]]) -> None:
    """Save (file, japanese, english) rows; UTF-8 with BOM so Excel reads it."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        w.writerows(rows)
    os.replace(tmp, path)


def read_labels_csv(path: str) -> dict[str, tuple[str, str]]:
    """{file: (japanese, english)} from a CSV written by write_labels_csv.

    Extra columns and blank rows are ignored; a missing header is an error.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(CSV_COLUMNS) <= set(reader.fieldnames):
            raise ValueError(f"{os.path.basename(path)}: needs the columns "
                             + ", ".join(CSV_COLUMNS))
        out = {}
        for row in reader:
            name = (row.get("file") or "").strip()
            if name:
                out[name] = ((row.get("japanese") or "").strip(),
                             (row.get("english") or "").strip())
        return out


# ── Loading ──────────────────────────────────────────────────────

def backup_root(img_dir: str) -> str:
    """img_original/ next to img/ (shared with Translate Images and patches)."""
    return os.path.join(os.path.dirname(os.path.normpath(img_dir)), "img_original")


def load_image(path: str, key: str = "") -> Image.Image:
    """Open a .png or encrypted .rpgmvp/.png_ as RGBA."""
    if path.lower().endswith(ENCRYPTED_EXTS):
        if not key:
            raise ValueError("encrypted image but the game has no key")
        raw = decrypt_rpgmvp(path, key)
        with Image.open(BytesIO(raw)) as im:
            return _to_rgba(im)
    with Image.open(path) as im:
        return _to_rgba(im)


def _to_rgba(im: Image.Image) -> Image.Image:
    """RGBA copy; 16-bit and float greyscale are scaled down first
    (a plain convert clips them, so a mid grey came out pure white)."""
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    elif im.mode == "F":
        im = im.point(lambda v: v * 255).convert("L")
    return im.convert("RGBA")


@dataclass
class ButtonFile:
    rel: str                 # "choices/C_はい.rpgmvp" (relative to img/)
    image: Image.Image       # the untranslated original
    label: str = ""          # Japanese from the file name ("" = needs OCR)
    replaced: bool = False   # the game file already differs from its backup


def scan_folder(img_dir: str, subdir: str, key: str = "") -> tuple[list[ButtonFile], list[str]]:
    """Buttons in img/<subdir>, read from their backup when one exists.

    Returns (buttons, problems). A file that can't be read is reported,
    not fatal.
    """
    folder = os.path.join(img_dir, subdir)
    backups = os.path.join(backup_root(img_dir), subdir)
    out, problems = [], []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(BUTTON_EXTS):
            continue
        path = os.path.join(folder, name)
        bak = os.path.join(backups, name)
        has_bak = os.path.isfile(bak)
        try:
            image = load_image(bak if has_bak else path, key)
            replaced = has_bak and _file_bytes(bak) != _file_bytes(path)
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            # Reported per file; one bad or oversized image doesn't stop
            # the rest of the folder from loading.
            problems.append(f"{name}: {e}")
            continue
        out.append(ButtonFile(f"{subdir}/{name}", image,
                              label_from_filename(name), replaced))
    return out, problems


def _file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ── Translating and models ───────────────────────────────────────

# Two steps (tested on game 2's ten hardest buttons): asking for a short
# label in one go made models drop meaning (その提案はのめない -> "I can't
# make that proposal"). So step 1 translates fully and accurately (the main
# model, e.g. Sugoi), and step 2 shortens only labels too long for the
# button (the image model, e.g. Qwen).
BUTTON_HINT = (
    "This text is printed on a game button: a choice the player picks or a "
    "menu command. Translate it accurately and naturally. Keep the meaning, "
    "any negation, and who it is about: a choice is the player character "
    "speaking or acting, and 二人 means 'you two'.")


@dataclass
class ChoiceScene:
    """Where a picture-choice button is used in the game's script."""
    before: str = ""                                  # lines just before it
    options: list[str] = field(default_factory=list)  # every option, in order


_SCENE_MAX = 600      # characters of earlier lines sent with a label


def choice_scenes(entries) -> dict[str, ChoiceScene]:
    """{button file stem: scene} from the project's picture-choice entries.

    kz_PictureChoices-style choices are script entries whose text is the
    image name (``C_送っておく|350|200``). Their stored context holds the
    lines before the choice, and the other options of the same choice sit
    next to them (``.../choice_1_0``, ``.../choice_1_1``). A label alone
    doesn't say who is choosing or about what; this does. The first use of
    a button with any context wins.
    """
    groups: dict[str, list[str]] = {}
    firsts: dict[str, tuple[str, str]] = {}           # stem -> (group, context)
    for e in entries:
        if getattr(e, "field", "") != "choice":
            continue
        original = getattr(e, "original", "") or ""
        stem = original.split("|", 1)[0].strip()
        if not stem or "/" in stem or "\\" in stem:
            continue
        group = (getattr(e, "id", "") or "").rsplit("_", 1)[0]
        groups.setdefault(group, []).append(stem)
        ctx = (getattr(e, "context", "") or "").strip()
        if stem not in firsts or (ctx and not firsts[stem][1]):
            firsts[stem] = (group, ctx)
    out = {}
    for stem, (group, ctx) in firsts.items():
        before = ctx[-_SCENE_MAX:]
        opts = [label_from_filename(s) or s for s in groups.get(group, [])]
        out[stem] = ChoiceScene(before, opts)
    return out


def translate_label(client, japanese: str, scene: ChoiceScene | None = None,
                    player: str = "") -> str:
    """Step 1: a full, accurate English translation of one button label.

    ``scene`` (from choice_scenes) and ``player`` (the protagonist's name)
    tell the model who is choosing and what about: alone, 送っておく came
    back as "Leave them alone" and その提案はのめない as "I can't refuse".
    """
    hint = BUTTON_HINT
    if player:
        hint += f" The player character is {player}."
    if scene and len(scene.options) > 1:
        hint += " The options in this choice are: " + " / ".join(scene.options) + "."
    if scene and scene.before:
        hint += "\nThe lines just before this choice:\n" + scene.before
    return client.translate(japanese, context=hint, field="choice").strip()


_SHORTEN_SYSTEM = "You turn translated game choices into short button labels."
_SHORTEN_USER = (
    "Japanese: {jp}\nFull English: {en}\n\n"
    "Rewrite the English as a short button label of 2 to 6 words with exactly "
    "the same meaning: keep any negation and who does what to whom. Use "
    "normal capitalisation (only the first word and names). Reply with the "
    "label only.")


def shorten_label(client, japanese: str, english: str) -> str:
    """Step 2: a shorter label with the same meaning, for buttons it overflows.

    Falls back to ``english`` if the reply is empty or no shorter. Words the
    model capitalised that weren't capitalised in ``english`` are lowered
    (Qwen title-cased whole labels: "Keep Working Together").
    """
    msgs = [{"role": "system", "content": _SHORTEN_SYSTEM},
            {"role": "user", "content": _SHORTEN_USER.format(jp=japanese, en=english)}]
    data = client._chat(messages=msgs,
                        options=client._base_options(num_predict=64, num_ctx=2048))
    raw = client._strip_thinking((data.get("message", {}) or {}).get("content", ""))
    lines = [ln.strip().strip('"').strip() for ln in (raw or "").splitlines()]
    lines = [ln for ln in lines if ln]        # a reply of just "" is empty
    if not lines or JAPANESE_RE.search(lines[0]) or len(lines[0]) >= len(english):
        return english
    known = set(english.split())
    words = lines[0].split()
    fixed = [words[0]] + [w if w in known or not w[:1].isupper() else w[:1].lower() + w[1:]
                          for w in words[1:]]
    return " ".join(fixed)


def fits(blank: "Blank", text: str, font_path: str | None = None) -> bool:
    """True if ``text`` fits the button at its normal size (no shrink/wrap)."""
    return not render_button(blank, text, font_path)[1]


IMAGE_MODEL = "qwen3.5:9b"     # recommended: reads stylised game text well


def vision_models(base_url: str = "http://localhost:11434", timeout: float = 5) -> list[str]:
    """Installed Ollama models that can read images (``vision`` capability).

    Returns [] if Ollama can't be reached: the caller falls back to the
    main model and says so.
    """
    import requests
    try:
        r = requests.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
        r.raise_for_status()
        models = r.json().get("models", [])
    except (requests.RequestException, ValueError):
        return []
    return [m.get("name", "") for m in models
            if isinstance(m, dict) and "vision" in (m.get("capabilities") or [])
            and m.get("name")]


def pick_image_model(models: list[str]) -> str:
    """The recommended model if installed, else another Qwen, else the first."""
    if IMAGE_MODEL in models:
        return IMAGE_MODEL
    qwen = [m for m in models if "qwen" in m.lower()]
    return (qwen or models or [""])[0]


# ── 2. Styles ────────────────────────────────────────────────────

def looks_like_buttons(group: list) -> bool:
    """True if a style group looks like a set of buttons.

    Buttons are wide and short and come in sets: 533x80 choices and 239x102
    menu buttons in game 2. Scene thumbnails (280x210), status screens
    (430x823) and single pictures don't fit. A guess: such styles are still
    listed, just unticked.
    """
    if len(group) < 2:
        return False
    w, h = group[0].image.size
    return h <= 160 and w >= 1.8 * h


def _signature(im: Image.Image) -> list[int]:
    """Colours of the left and right edges (text sits in the middle)."""
    small = im.convert("RGB").resize((10, 3), Image.Resampling.BOX)
    px = small.load()
    return [c for y in range(3) for x in (0, 9) for c in px[x, y]]


def group_styles(buttons: list[ButtonFile], tolerance: int = 30) -> list[list[ButtonFile]]:
    """Buttons grouped by style: same size and similar edge colours.

    Largest groups first. In game 2 this splits the 58 choice buttons from
    the morning / evening / night menu buttons.
    """
    groups: list[tuple[list[int], list[ButtonFile]]] = []
    for b in buttons:
        sig = _signature(b.image)
        for gsig, members in groups:
            if (members[0].image.size == b.image.size
                    and sum(abs(a - c) for a, c in zip(sig, gsig)) / len(sig) <= tolerance):
                members.append(b)
                break
        else:
            groups.append((sig, [b]))
    return sorted((m for _s, m in groups), key=len, reverse=True)


# ── Detection (cheap: file names and sizes only) ─────────────────

_PNG_SIG = bytes([0x89]) + b"PNG"
# Folders that never hold buttons (same list as Translate Images skips)
_NOT_BUTTON_DIRS = {"faces", "characters", "sv_actors", "sv_enemies", "enemies",
                    "battlebacks1", "battlebacks2", "parallaxes", "tilesets",
                    "animations"}


def image_size(path: str) -> tuple[int, int] | None:
    """Width and height from the PNG header, without decoding the image.

    Works for encrypted .rpgmvp/.png_ too: they add a 16-byte header and
    scramble only the PNG's first 16 bytes, and the size comes after that.
    None if the file isn't a PNG we can read.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(40)
    except OSError:
        return None
    if path.lower().endswith(ENCRYPTED_EXTS):
        off = 16              # the PNG signature and "IHDR" are scrambled
        if not head.startswith(b"RPGMV"):
            return None
    else:
        off = 0
        if not head.startswith(_PNG_SIG) or head[12:16] != b"IHDR":
            return None
    w = int.from_bytes(head[off + 16: off + 20], "big")
    h = int.from_bytes(head[off + 20: off + 24], "big")
    return (w, h) if w and h else None


@dataclass
class DetectedButtons:
    subdir: str
    size: tuple[int, int]
    count: int
    japanese_names: int


def detect_buttons(img_dir: str) -> list[DetectedButtons]:
    """Sets of button-shaped images that probably hold Japanese text.

    A set is 2+ images of one size in one folder, wide and short (see
    looks_like_buttons), with Japanese in the file names or in a
    picture-choice folder (``choices``). Images already replaced by an
    English version (a different file in img_original/) aren't counted.
    """
    found = []
    backups = backup_root(img_dir)
    try:
        subdirs = sorted(d for d in os.listdir(img_dir)
                         if os.path.isdir(os.path.join(img_dir, d))
                         and d.lower() not in _NOT_BUTTON_DIRS)
    except OSError:
        return []
    for sub in subdirs:
        sizes: dict[tuple[int, int], list[str]] = {}
        try:
            names = os.listdir(os.path.join(img_dir, sub))
        except OSError:
            continue
        is_choices = sub.lower() == "choices"
        for name in names:
            # Names first: only Japanese names (or a choices folder) can
            # count, so a folder of 1,500 English-named pictures costs nothing
            if not name.lower().endswith(BUTTON_EXTS) or not (
                    is_choices or JAPANESE_RE.search(name)):
                continue
            path = os.path.join(img_dir, sub, name)
            bak = os.path.join(backups, sub, name)
            if os.path.isfile(bak) and _file_bytes(bak) != _file_bytes(path):
                continue                       # already redrawn
            size = image_size(path)
            if size:
                sizes.setdefault(size, []).append(name)
        for (w, h), files in sizes.items():
            jp = sum(1 for n in files if JAPANESE_RE.search(n))
            if (len(files) >= 2 and h <= 160 and w >= 1.8 * h
                    and (jp or sub.lower() == "choices")):
                found.append(DetectedButtons(sub, (w, h), len(files), jp))
    return sorted(found, key=lambda d: -d.count)


def describe_detected(found: list[DetectedButtons]) -> str:
    """'74 buttons with Japanese text (58 in choices, 16 in choices)'."""
    total = sum(d.count for d in found)
    parts = ", ".join(f"{d.count} in {d.subdir} ({d.size[0]}x{d.size[1]})" for d in found)
    return f"{total} button images that may hold Japanese text: {parts}"


# ── 3. Blank button ──────────────────────────────────────────────

@dataclass
class Blank:
    background: Image.Image
    text_box: tuple[int, int, int, int]   # where labels sat: x0, y0, x1, y1
    text_height: int                      # typical label height in px
    centered: bool
    fill: tuple[int, int, int]
    outline: tuple[int, int, int] | None
    stroke: int
    margin: int                           # free space kept at the left/right
    holes: int = 0                        # pixels no button showed bare
    notes: list[str] = field(default_factory=list)


def _bin(img: Image.Image, threshold: int) -> Image.Image:
    return img.point(lambda v, t=threshold: 255 if v > t else 0)


def _diff_mask(im: Image.Image, bg: Image.Image, threshold: int) -> Image.Image:
    r, g, b = ImageChops.difference(im.convert("RGB"), bg.convert("RGB")).split()
    return _bin(ImageChops.lighter(ImageChops.lighter(r, g), b), threshold)


def _edges(im: Image.Image) -> Image.Image:
    return _bin(im.convert("L").filter(ImageFilter.FIND_EDGES), _EDGE)


def _masked_median(images: list[Image.Image], masks: list[Image.Image]):
    """Per-pixel median over the images whose mask is 0 there.

    Returns (background, holes) where holes marks pixels every image masked.
    """
    w, h = images[0].size
    n_px = w * h
    datas = [im.tobytes() for im in images]
    mdat = [m.tobytes() for m in masks]
    out = bytearray(4 * n_px)
    holes = bytearray(n_px)
    for p in range(n_px):
        free = [d for d, m in zip(datas, mdat) if not m[p]]
        if not free:
            holes[p] = 255
            free = datas
        k = len(free) // 2
        o = 4 * p
        for c in range(4):
            out[o + c] = sorted(d[o + c] for d in free)[k]
    return (Image.frombytes("RGBA", (w, h), bytes(out)),
            Image.frombytes("L", (w, h), bytes(holes)))


def _line_fill(get, good, n):
    """For one row or column: {i: (colour, gap)} for each bad index i.

    colour blends the nearest good pixels on both sides; gap is the
    distance between them (a short gap is a better guess).
    """
    out = {}
    for i in range(n):
        if good[i]:
            continue
        lo = next((j for j in range(i - 1, -1, -1) if good[j]), None)
        hi = next((j for j in range(i + 1, n) if good[j]), None)
        if lo is None and hi is None:
            continue
        if lo is None or hi is None:
            j = hi if lo is None else lo
            out[i] = (get(j), 2 * abs(i - j) + 1)
            continue
        t = (i - lo) / (hi - lo)
        a, b = get(lo), get(hi)
        out[i] = (tuple(a[c] + (b[c] - a[c]) * t for c in range(4)), hi - lo)
    return out


def _fill_holes(bg: Image.Image, holes: Image.Image) -> int:
    """Fill hole pixels from good pixels along both their row and column.

    The two guesses are weighted by how short each gap is, so a wide label
    is filled mostly from above and below and a tall one from the sides.
    That avoids the streaks a row-only fill leaves.
    """
    w, h = bg.size
    px, hp = bg.load(), holes.load()
    good = [[hp[x, y] == 0 for x in range(w)] for y in range(h)]
    guesses: dict[tuple[int, int], list] = {}
    for y in range(h):
        if all(good[y]):
            continue
        for x, g in _line_fill(lambda i, y=y: px[i, y], good[y], w).items():
            guesses.setdefault((x, y), []).append(g)
    for x in range(w):
        col = [good[y][x] for y in range(h)]
        if all(col):
            continue
        for y, g in _line_fill(lambda i, x=x: px[x, i], col, h).items():
            guesses.setdefault((x, y), []).append(g)
    for (x, y), gs in guesses.items():
        weights = [1.0 / gap for _c, gap in gs]
        total = sum(weights)
        px[x, y] = tuple(round(sum(c[k] * wt for (c, _g), wt in zip(gs, weights)) / total)
                         for k in range(4))
    return len(guesses)


def _row_inpaint(im: Image.Image, mask: Image.Image) -> Image.Image:
    bg = im.copy()
    _fill_holes(bg, mask)
    return bg


def _two_colours(pixels: list[tuple[int, int, int]]):
    """Split pixels into two colour groups (2-means). Returns (c1, n1, c2, n2)."""
    def lum(p):
        return p[0] * 3 + p[1] * 6 + p[2]
    c1, c2 = max(pixels, key=lum), min(pixels, key=lum)
    for _ in range(8):
        g1, g2 = [], []
        for p in pixels:
            d1 = sum((a - b) ** 2 for a, b in zip(p, c1))
            d2 = sum((a - b) ** 2 for a, b in zip(p, c2))
            (g1 if d1 <= d2 else g2).append(p)
        if not g1 or not g2:
            break
        c1 = tuple(sum(v) // len(g1) for v in zip(*g1))
        c2 = tuple(sum(v) // len(g2) for v in zip(*g2))
    return c1, len(g1), c2, len(g2)


def _median(values: list[int]) -> int:
    s = sorted(values)
    return s[len(s) // 2]


def build_blank(images: list[Image.Image]) -> Blank:
    """A text-free button and the text style, measured from one style group.

    With MIN_GROUP or more buttons the background is rebuilt per pixel from
    buttons with no text there. With fewer, the text area is painted over
    from its left/right neighbours: a similar look, not an exact one.
    """
    if not images:
        raise ValueError("no buttons")
    size = images[0].size
    if any(im.size != size for im in images):
        raise ValueError("buttons in one style must be the same size")
    if len(images) > MAX_BLANK_SOURCES:
        # Enough for a clean median; more only costs time (pure Python)
        step = len(images) / MAX_BLANK_SOURCES
        images = [images[int(i * step)] for i in range(MAX_BLANK_SOURCES)]
    w, h = size
    images = [im.convert("RGBA") for im in images]
    n = len(images)
    notes = []

    # Text strokes: edges that aren't part of the shared frame/artwork
    edges = [_edges(im) for im in images]
    if n >= MIN_GROUP:
        counts = edges[0].point(lambda v: 1 if v else 0)
        for e in edges[1:]:
            counts = ImageChops.add(counts, e.point(lambda v: 1 if v else 0))
        need = max(2, (n * 3 + 4) // 5)
        frame = counts.point(lambda v, k=need: 255 if v >= k else 0).filter(
            ImageFilter.MaxFilter(3))
        seeds = [ImageChops.subtract(e, frame).filter(ImageFilter.MaxFilter(7))
                 for e in edges]
        bg, holes = _masked_median(images, seeds)
        # Second pass: now the blank is clean, anything that differs is text
        # (a wide margin: faint anti-aliased edges would leave ghosts)
        masks = [ImageChops.lighter(_diff_mask(im, bg, _DIFF_TEXT // 2).filter(
            ImageFilter.MaxFilter(7)), s) for im, s in zip(images, seeds)]
        bg, holes = _masked_median(images, masks)
        # Kept in Blank.holes only: the new button is drawn from smoothed
        # colours, so these filled pixels never show and need no message.
        hole_count = _fill_holes(bg, holes)
    else:
        # Too few to compare: text = strong edges away from the outer frame
        mx, my = max(4, w // 25), max(4, h // 8)
        inner = Image.new("L", size, 0)
        ImageDraw.Draw(inner).rectangle((mx, my, w - mx - 1, h - my - 1), fill=255)
        masks = [ImageChops.multiply(e, inner).filter(ImageFilter.MaxFilter(9))
                 for e in edges]
        union = masks[0]
        for m in masks[1:]:
            union = ImageChops.lighter(union, m)
        box = union.getbbox()
        if box:
            union = Image.new("L", size, 0)
            ImageDraw.Draw(union).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
        bg = _row_inpaint(images[0], union)
        hole_count = union.point(lambda v: 255 if v else 0).histogram()[255]
        notes.append(f"only {n} button(s) in this style: the background is "
                     "painted over, so it will look similar, not identical")

    # Where labels sit, from the strong differences to the blank
    strong = [_diff_mask(im, bg, _DIFF_TEXT) for im in images]
    boxes = [m.getbbox() for m in strong]
    boxes = [b for b in boxes if b and (b[2] - b[0]) > 2 and (b[3] - b[1]) > 2]
    if not boxes:
        raise ValueError("no text found on these buttons")
    x0 = min(b[0] for b in boxes)
    x1 = max(b[2] for b in boxes)
    cy = _median([(b[1] + b[3]) // 2 for b in boxes])
    # Capped at about half the button: big menu labels with a subtitle
    # under them would otherwise give a giant single word.
    th = min(_median([b[3] - b[1] for b in boxes]), int(h * 0.5))
    centres = [(b[0] + b[2]) / 2 for b in boxes]
    centered = max(centres) - min(centres) <= max(6, w * 0.05)
    margin = max(2, min(x0, w - x1))
    text_box = (x0, max(0, cy - th // 2), x1, min(h, cy + th - th // 2))

    # Colours: the two main colours in the text, outline = the outer one
    samples, edge_hits = [], [0, 0]
    step = max(1, (sum(m.histogram()[255] for m in strong) // 20000))
    picks = []
    for im, m in zip(images, strong):
        ring = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(3)))
        rgb, mp, rp = im.convert("RGB").load(), m.load(), ring.load()
        for y in range(h):
            for x in range(w):
                if mp[x, y]:
                    picks.append((rgb[x, y], bool(rp[x, y])))
    picks = picks[::step]
    samples = [p for p, _r in picks]
    c1, n1, c2, n2 = _two_colours(samples)
    if sum((a - b) ** 2 for a, b in zip(c1, c2)) < 70 ** 2 or min(n1, n2) < len(samples) // 12:
        fill = c1 if n1 >= n2 else c2
        outline, stroke = None, 0
    else:
        for p, on_ring in picks:
            if on_ring:
                d1 = sum((a - b) ** 2 for a, b in zip(p, c1))
                d2 = sum((a - b) ** 2 for a, b in zip(p, c2))
                edge_hits[0 if d1 <= d2 else 1] += 1
        if edge_hits[0] / max(1, n1) > edge_hits[1] / max(1, n2):
            fill, outline, n_out = c2, c1, n1
        else:
            fill, outline, n_out = c1, c2, n2
        ring_total = max(1, sum(edge_hits)) / max(1, len(picks)) * len(samples)
        stroke = max(1, min(8, round(n_out / max(1.0, ring_total))))
        # Thin measured outlines vanish on busy backgrounds
        stroke = max(stroke, round(th / 10))

    return Blank(new_button(bg), text_box, th, centered, tuple(fill),
                 tuple(outline) if outline else None, stroke, margin,
                 hole_count, notes)


def _close(a, b, tol=40) -> bool:
    return sum(abs(x - y) for x, y in zip(a[:3], b[:3])) <= tol * 3 // 2


def new_button(measured: Image.Image) -> Image.Image:
    """A brand-new button in the colours of ``measured``: nothing copied.

    Only colours and the frame are measured: the frame colour and width at
    the edges, and the main colour of each column (smoothed). The result is
    a clean left-to-right gradient with light top shading, the same frame,
    and the original's transparency (rounded or cut corners stay).
    """
    w, h = measured.size
    src = measured.convert("RGBA")
    px = src.load()
    mid = h // 2

    # Frame: pixels from the left edge that keep the edge colour
    edge = px[0, mid]
    fw = 0
    while fw < max(1, h // 6) and _close(px[fw, mid], edge):
        fw += 1
    fw = max(1, fw)
    ring = [px[x, y] for x in range(w) for y in range(h)
            if (x < fw or y < fw or x >= w - fw or y >= h - fw) and px[x, y][3] > 0]
    frame = tuple(_median([p[c] for p in ring]) for c in range(3)) if ring else edge[:3]

    # Body: the median colour of each column inside the frame, smoothed
    cols = []
    for x in range(w):
        vals = [px[x, y] for y in range(fw, h - fw) if px[x, y][3] > 0] or [px[x, mid]]
        cols.append(tuple(_median([v[c] for v in vals]) for c in range(3)))
    win = max(1, w // 6)
    smooth = []
    for x in range(w):
        lo, hi = max(0, x - win), min(w, x + win + 1)
        seg = cols[lo:hi]
        smooth.append(tuple(sum(v[c] for v in seg) / len(seg) for c in range(3)))

    out = Image.new("RGBA", (w, h))
    op = out.load()
    for y in range(h):
        shade = 1.08 - 0.16 * (y / max(1, h - 1))       # lighter at the top
        for x in range(w):
            op[x, y] = tuple(max(0, min(255, round(v * shade))) for v in smooth[x]) + (255,)
    d = ImageDraw.Draw(out)
    for i in range(fw):
        d.rectangle((i, i, w - 1 - i, h - 1 - i), outline=frame)
    # thin highlight just inside the frame
    hl = tuple(min(255, v + 60) for v in frame)
    if w > 2 * fw + 2 and h > 2 * fw + 2:
        d.rectangle((fw, fw, w - 1 - fw, h - 1 - fw), outline=hl)
    out.putalpha(src.getchannel("A"))
    return out


# ── 4. Drawing ───────────────────────────────────────────────────

def default_font() -> str | None:
    """A bold, rounded system font if present (game 2 looked native with it)."""
    for path in (r"C:\Windows\Fonts\ARLRDBD.TTF", r"C:\Windows\Fonts\seguibl.ttf",
                 r"C:\Windows\Fonts\arialbd.ttf"):
        if os.path.isfile(path):
            return path
    return _find_font(bold=True)


def _font(path: str | None, size: int):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def render_button(blank: Blank, text: str, font_path: str | None = None,
                  max_size: int | None = None) -> tuple[Image.Image, str]:
    """The blank with ``text`` drawn in the measured style.

    Returns (image, note); note is "" or says the text was shrunk, wrapped
    or doesn't fit.
    """
    im = blank.background.copy()
    text = " ".join((text or "").split())
    if not text:
        return im, "no English text"
    d = ImageDraw.Draw(im)
    w, h = im.size
    stroke = blank.stroke

    def measure(lines, size):
        f = _font(font_path, size)
        boxes = [d.textbbox((0, 0), ln, font=f, stroke_width=stroke) for ln in lines]
        return f, boxes

    # Size so that "Ag" is as tall as the Japanese was
    size = max_size or max(8, blank.text_height)
    while size > 8:
        _f, (bb,) = measure(["Ag"], size)
        if bb[3] - bb[1] <= blank.text_height * 1.15:
            break
        size -= 1
    max_w = max(blank.text_box[2] - blank.text_box[0], w - 2 * blank.margin)
    min_size = max(8, int(size * 0.6))
    note = ""
    lines = [text]
    s = size
    while s >= min_size:
        f, boxes = measure(lines, s)
        if max(b[2] - b[0] for b in boxes) <= max_w:
            break
        s -= 1
    else:
        # Too long for one line: try two lines at the full size again
        words = text.split()
        best = None
        for i in range(1, len(words)):
            pair = [" ".join(words[:i]), " ".join(words[i:])]
            for s2 in range(size, min_size - 1, -1):
                f2, b2 = measure(pair, s2)
                height = sum(b[3] - b[1] for b in b2)
                if max(b[2] - b[0] for b in b2) <= max_w and height <= h - 4:
                    if best is None or s2 > best[0]:
                        best = (s2, pair)
                    break
        if best:
            s, lines = best
            note = "wrapped to two lines"
        else:
            s = min_size
            note = "too long: text runs past the button edges"
    f, boxes = measure(lines, s)
    if s < size and not note:
        note = f"shrunk to {round(100 * s / size)}% to fit"

    # Placed in the middle of the button: where the Japanese sat doesn't
    # matter, only the words and the style do.
    heights = [b[3] - b[1] for b in boxes]
    gap = max(1, s // 10)
    total = sum(heights) + gap * (len(lines) - 1)
    y = round(h / 2 - total / 2)
    y = max(1, min(y, h - total - 1))
    for ln, bb, lh in zip(lines, boxes, heights):
        lw = bb[2] - bb[0]
        x = max(1, min(round(w / 2 - lw / 2), w - lw - 1))
        kw = {"stroke_width": stroke, "stroke_fill": blank.outline} if blank.outline else {}
        d.text((x - bb[0], y - bb[1]), ln, font=f, fill=blank.fill, **kw)
        y += lh + gap
    return im, note


# ── Writing into the game ────────────────────────────────────────

def install_button(img_dir: str, rel: str, image: Image.Image, key: str = "") -> None:
    """Replace img/<rel> with ``image``, keeping the original in img_original/.

    The backup is made (and checked) before anything is replaced; if it
    fails nothing is written. The new file is written to a temp file,
    read back, and only then renamed over the old one.
    """
    dest = os.path.join(img_dir, rel)
    if not os.path.isfile(dest):
        raise FileNotFoundError(rel)
    encrypted = rel.lower().endswith(ENCRYPTED_EXTS)
    if encrypted and not key:
        raise ValueError(f"{rel}: encrypted, but the game has no key")
    if not encrypted and not rel.lower().endswith(".png"):
        raise ValueError(f"{rel}: only .png and encrypted PNG files are supported")

    backup = os.path.join(backup_root(img_dir), rel)
    if not os.path.isfile(backup):
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        tmp = backup + ".tmp"
        shutil.copy2(dest, tmp)
        if _file_bytes(tmp) != _file_bytes(dest):
            os.remove(tmp)
            raise OSError(f"{rel}: backup copy didn't match; nothing written")
        os.replace(tmp, backup)

    tmp = dest + ".redraw.tmp"
    png_tmp = dest + ".redraw.png.tmp"
    try:
        if encrypted:
            image.save(png_tmp, "PNG")
            encrypt_to_rpgmvp(png_tmp, tmp, key)
            check = load_image_bytes(decrypt_rpgmvp(tmp, key))
        else:
            image.save(tmp, "PNG")
            check = load_image_bytes(_file_bytes(tmp))
        if check.size != image.size:
            raise OSError(f"{rel}: written file didn't read back correctly")
        os.replace(tmp, dest)
    finally:
        for leftover in (tmp, png_tmp):
            if os.path.exists(leftover):
                os.remove(leftover)


def load_image_bytes(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as im:
        im.load()
        return im.copy()
