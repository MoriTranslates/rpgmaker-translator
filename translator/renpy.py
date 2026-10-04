"""Ren'Py .rpy script parser — load, extract, export."""

import logging
import os
import re
import shutil

from translator.project_model import TranslationEntry

log = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────

# Character definition: define alias = Character("Name", ...)
_CHAR_DEF_RE = re.compile(
    r'^define\s+(\w+)\s*=\s*Character\(\s*"([^"]*)"', re.MULTILINE)

# Dialogue: alias "text"
_DIALOGUE_RE = re.compile(
    r'^(\s+)(\w+)\s+"((?:[^"\\]|\\.)*)"\s*$')

# Narration: "text" (indented, no alias, not a choice, not a define)
_NARRATION_RE = re.compile(
    r'^(\s+)"((?:[^"\\]|\\.)*)"\s*$')

# Menu choice: "Choice text":  (or "Choice text" if condition:)
_CHOICE_RE = re.compile(
    r'^(\s+)"((?:[^"\\]|\\.)+)"(\s+if\s+.+)?:\s*$')

# Label definition: label name:
_LABEL_RE = re.compile(r'^label\s+(\w+)\s*:')

# Lines to skip (non-translatable Ren'Py commands).
# Every keyword must end at a word boundary (\s, \s*:, \b ...) — a bare
# prefix like "pass" would also swallow dialogue from a character alias
# such as `passerby "..."`.
_SKIP_RE = re.compile(
    r'^\s*(?:'
    r'default\s|init\s|python\s*:|image\s|transform\s|'
    r'scene\s|show\s|hide\s|with\s|play\s|stop\s|queue\s|'
    r'pause\s|jump\s|call\s|return\b|pass\b|'
    r'\$|if\s|elif\s|else\s*:|for\s|while\s|'
    r'#|label\s|menu\s*:|screen\s|style\s|'
    r'window\s|nvl\s|voice\s|camera\s|'
    r'font\s|color\s|hover_color\s|outlines\s|xalign\s|yalign\s|'
    r'padding\s|margin\s*\(|size\s|text_align\s|layout\s|spacing\s|'
    r'xsize\s|ysize\s|xpos\s|ypos\s|xmaximum\s|ymaximum\s|'
    r'background\s|foreground\s|bar\s|vbar\s|at\s|use\s|'
    r'add\s|hbox\s*:|vbox\s*:|grid\s|frame\s*:|'
    r'viewport\s|textbutton\s|imagebutton\s|input\s|'
    r'key\s|on\s|tag\s|zorder\s|modal\s|'
    r'action\s|sensitive\s|insensitive\s|'
    r'text\s|timer\s|has\s|'
    r'selected_color\s|idle_color\s|hover_outlines\s|'
    r'ground\s|unscrollable\s|mousewheel\s|draggable\s|'
    r'child_size\s|scrollbars\s|side_xalign\s'
    r')',
    re.IGNORECASE)

# Standard Ren'Py boilerplate files that don't contain game dialogue
_SKIP_FILES = {"gui.rpy", "screens.rpy", "options.rpy"}

# Style/config value patterns that look like narration but aren't
_STYLE_VALUE_RE = re.compile(
    r'^[#0-9A-Fa-f]{3,8}$|'           # color codes: #fff, FF0000
    r'^[\w\-]+\.\w{2,4}$|'            # file names: font.ttf, bg.png
    r'^\d+(\.\d+)?$|'                  # bare numbers: 25, 0.5
    r'^(?:True|False|None)$',          # Python literals
    re.IGNORECASE)

# Ren'Py inline tags to extract as placeholders: {i}, {/i}, {b}, {/b},
# {color=...}, {/color}, {size=...}, {/size}, etc.
RENPY_TAG_RE = re.compile(
    r'\{/?(?:i|b|u|s|plain|color|size|font|alpha|cps|nw|fast|w|p|vspace|image|space|art)'
    r'(?:=[^}]*)?\}')

# Game title in options.rpy
_TITLE_RE = re.compile(
    r'define\s+config\.name\s*=\s*_?\(\s*"([^"]*)"\s*\)')


def _read_rpy(fpath: str, lines: bool = False):
    """Read a .rpy file (UTF-8, BOM-tolerant, lossy fallback)."""
    try:
        with open(fpath, "r", encoding="utf-8-sig") as f:
            return f.readlines() if lines else f.read()
    except UnicodeDecodeError:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines() if lines else f.read()


def _classify_line(line: str):
    """Classify one script line (without newline) for load AND export.

    Returns (kind, match).  kind is one of "label", "define", "choice",
    "dialog", "narration" or None.  Only choice/dialog/narration consume a
    dialogue index — load and export must number entries identically.
    """
    m = _LABEL_RE.match(line)
    if m:
        return "label", m
    stripped = line.strip()
    if not stripped or _SKIP_RE.match(stripped):
        return None, None
    if stripped.startswith("define "):
        return "define", _CHAR_DEF_RE.match(stripped)
    m = _CHOICE_RE.match(line)
    if m:
        return ("choice", m) if m.group(2).strip() else (None, None)
    m = _DIALOGUE_RE.match(line)
    if m:
        return ("dialog", m) if m.group(3).strip() else (None, None)
    m = _NARRATION_RE.match(line)
    if m:
        text = m.group(2).strip()
        if text and not _STYLE_VALUE_RE.match(text):
            return "narration", m
    return None, None


def _escape_rpy(text: str) -> str:
    """Escape bare double quotes for a Ren'Py string literal.

    Originals are captured raw (an already-escaped \\" stays as is), so
    only unescaped quotes are touched; backslashes are never doubled.
    Real newlines become the \\n escape so the literal stays on one line.
    """
    text = text.replace("\r\n", "\n").replace("\n", "\\n")
    return re.sub(r'(?<!\\)"', r'\\"', text)


# ── Parser ────────────────────────────────────────────────────────────

class RenPyParser:
    """Parser for Ren'Py .rpy script files."""

    # ── Detection ─────────────────────────────────────────────────────

    @staticmethod
    def is_renpy_project(path: str) -> bool:
        """Return True if path looks like a Ren'Py project."""
        game_dir = os.path.join(path, "game")
        if not os.path.isdir(game_dir):
            return False
        # Must have at least one .rpy file
        has_rpy = any(f.endswith(".rpy") for f in os.listdir(game_dir))
        # Should also have renpy/ folder or a .py launcher
        has_renpy = (os.path.isdir(os.path.join(path, "renpy")) or
                     any(f.endswith(".py") for f in os.listdir(path)))
        return has_rpy and has_renpy

    # ── Load project ──────────────────────────────────────────────────

    def load_project(self, project_dir: str,
                     context_size: int = 3,
                     prefer_backup: bool = False) -> list[TranslationEntry]:
        """Extract all translatable strings from a Ren'Py project.

        With ``prefer_backup`` each .rpy is read from ``game_original/``
        when a backup copy exists (the live file holds exported English
        after the first export) — used by the saved-state ID migration.
        """
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        entries: list[TranslationEntry] = []

        def src_path(fname):
            if prefer_backup:
                bak = os.path.join(backup_dir, fname)
                if os.path.isfile(bak):
                    return bak
            return os.path.join(game_dir, fname)

        # Parse character definitions first (for speaker context)
        self._char_names = {}
        for fname in sorted(os.listdir(game_dir)):
            if fname.endswith(".rpy"):
                fpath = src_path(fname)
                self._parse_char_defs(fpath)

        # Extract character names from names.rpy (or wherever defines are)
        for fname in sorted(os.listdir(game_dir)):
            if not fname.endswith(".rpy"):
                continue
            fpath = src_path(fname)
            entries.extend(self._extract_char_name_entries(fpath, fname))

        # Extract translatable strings from each .rpy file
        for fname in sorted(os.listdir(game_dir)):
            if not fname.endswith(".rpy"):
                continue
            if fname in _SKIP_FILES:
                continue
            fpath = src_path(fname)
            if not os.path.isfile(fpath):
                continue
            try:
                file_entries = self._extract_file(
                    fpath, fname, context_size)
                entries.extend(file_entries)
            except Exception as e:
                log.error("Failed to parse %s: %s", fname, e)

        log.info("Loaded %d entries from Ren'Py project", len(entries))
        return entries

    def _parse_char_defs(self, fpath: str):
        """Extract character alias → name mappings from a file."""
        content = _read_rpy(fpath)
        for match in _CHAR_DEF_RE.finditer(content):
            alias, name = match.group(1), match.group(2)
            if name:  # skip empty names (narrator variants)
                self._char_names[alias] = name

    def _extract_char_name_entries(self, fpath: str,
                                   fname: str) -> list[TranslationEntry]:
        """Extract character name definitions as translatable entries."""
        entries = []
        content = _read_rpy(fpath)
        for match in _CHAR_DEF_RE.finditer(content):
            alias, name = match.group(1), match.group(2)
            if name and not name.startswith("{"):  # skip styled names
                entry_id = f"{fname}/define/{alias}"
                entries.append(TranslationEntry(
                    id=entry_id,
                    file=fname,
                    field="name",
                    original=name,
                    translation="",
                    status="untranslated",
                    context="[Character Definition]",
                ))
        return entries

    def _extract_file(self, fpath: str, fname: str,
                      context_size: int) -> list[TranslationEntry]:
        """Extract translatable strings from a single .rpy file."""
        entries = []
        recent_context: list[str] = []
        current_label = "start"
        dialogue_index = 0

        lines = _read_rpy(fpath, lines=True)

        for raw_line in lines:
            line = raw_line.rstrip("\n\r")
            kind, m = _classify_line(line)

            # Track labels for entry IDs
            if kind == "label":
                current_label = m.group(1)
                dialogue_index = 0
                continue
            # Character definitions are extracted separately
            if kind not in ("choice", "dialog", "narration"):
                continue

            ctx_parts = recent_context[-context_size:]
            if kind == "choice":
                text = m.group(2)
                entry_id = f"{fname}/{current_label}/choice_{dialogue_index}"
                field = "choice"
            elif kind == "dialog":
                alias = m.group(2)
                text = m.group(3)
                speaker = self._char_names.get(alias, alias)
                entry_id = f"{fname}/{current_label}/dialog_{dialogue_index}"
                field = "dialog"
                if speaker:
                    ctx_parts.insert(0, f"[Speaker: {speaker}]")
            else:
                text = m.group(2)
                entry_id = f"{fname}/{current_label}/dialog_{dialogue_index}"
                field = "dialog"

            entries.append(TranslationEntry(
                id=entry_id,
                file=fname,
                field=field,
                original=text,
                translation="",
                status="untranslated",
                context="\n".join(ctx_parts),
            ))
            if kind == "dialog":
                recent_context.append(f"{speaker}: {text[:60]}")
            elif kind == "narration":
                recent_context.append(text[:60])
            dialogue_index += 1

        return entries

    # ── Actors ────────────────────────────────────────────────────────

    def load_actors_raw(self, project_dir: str) -> list[dict]:
        """Load character definitions for gender dialog."""
        game_dir = os.path.join(project_dir, "game")
        self._char_names = {}
        for fname in sorted(os.listdir(game_dir)):
            if fname.endswith(".rpy"):
                self._parse_char_defs(os.path.join(game_dir, fname))

        actors = []
        for i, (alias, name) in enumerate(self._char_names.items(), 1):
            actors.append({
                "id": i,
                "name": name,
                "nickname": alias,
                "profile": "",
            })
        return actors

    # ── Game title ────────────────────────────────────────────────────

    def get_game_title(self, project_dir: str) -> str:
        """Read game title from options.rpy."""
        options = os.path.join(project_dir, "game", "options.rpy")
        if not os.path.isfile(options):
            return ""
        try:
            with open(options, "r", encoding="utf-8-sig") as f:
                content = f.read()
            match = _TITLE_RE.search(content)
            return match.group(1) if match else ""
        except Exception:
            return ""

    # ── Export ─────────────────────────────────────────────────────────

    def save_project(self, project_dir: str,
                     entries: list[TranslationEntry]):
        """Write translations back into .rpy files.

        Strategy: create backup of game/ as game_original/, then
        modify .rpy files in-place with translations.
        """
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")

        # Create backup on first export
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir, exist_ok=True)
            for fname in os.listdir(game_dir):
                if fname.endswith(".rpy"):
                    src = os.path.join(game_dir, fname)
                    dst = os.path.join(backup_dir, fname)
                    shutil.copy2(src, dst)
            log.info("Backed up .rpy files to game_original/")

        # Build translation map
        trans_map = {}
        for e in entries:
            if e.translation and e.status in ("translated", "reviewed"):
                trans_map[e.id] = e

        if not trans_map:
            log.warning("No translations to export")

        # Process each .rpy file
        exported = 0
        for fname in sorted(os.listdir(game_dir)):
            if not fname.endswith(".rpy"):
                continue
            # Read from backup (idempotent re-export)
            source = os.path.join(backup_dir, fname)
            if not os.path.isfile(source):
                source = os.path.join(game_dir, fname)
            target = os.path.join(game_dir, fname)
            count = self._export_file(source, target, fname, trans_map)
            exported += count

        log.info("Exported %d translations to .rpy files", exported)

    def _export_file(self, source_path: str, target_path: str,
                     fname: str, trans_map: dict) -> int:
        """Apply translations to a single .rpy file. Returns count."""
        lines = _read_rpy(source_path, lines=True)

        current_label = "start"
        dialogue_index = 0
        changed = False
        output_lines = list(lines)

        for line_idx, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n\r")
            kind, m = _classify_line(line)

            if kind == "label":
                current_label = m.group(1)
                dialogue_index = 0
                continue

            if kind == "define":
                if m is None:
                    continue
                entry = trans_map.get(f"{fname}/define/{m.group(1)}")
                if entry:
                    output_lines[line_idx] = raw_line.replace(
                        f'"{m.group(2)}"', f'"{_escape_rpy(entry.translation)}"', 1)
                    changed = True
                continue

            if kind is None:
                continue

            prefix = "choice" if kind == "choice" else "dialog"
            entry = trans_map.get(
                f"{fname}/{current_label}/{prefix}_{dialogue_index}")
            dialogue_index += 1
            if not entry:
                continue

            escaped = _escape_rpy(entry.translation)
            indent = m.group(1)
            if kind == "choice":
                condition = m.group(3) or ""
                new_line = f'{indent}"{escaped}"{condition}:\n'
            elif kind == "dialog":
                new_line = f'{indent}{m.group(2)} "{escaped}"\n'
            else:
                new_line = f'{indent}"{escaped}"\n'
            output_lines[line_idx] = new_line
            changed = True

        if changed:
            with open(target_path, "w", encoding="utf-8") as f:
                f.writelines(output_lines)
        elif (os.path.normcase(os.path.abspath(source_path)) !=
              os.path.normcase(os.path.abspath(target_path))):
            # No translations (or all reverted) — restore pristine backup so
            # stale English from a previous export doesn't linger.
            shutil.copy2(source_path, target_path)

        return sum(1 for eid in trans_map
                   if eid.startswith(f"{fname}/"))

    # ── Restore originals ─────────────────────────────────────────────

    def restore_originals(self, project_dir: str):
        """Restore original .rpy files from backup."""
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        if not os.path.isdir(backup_dir):
            raise FileNotFoundError(
                "No game_original/ backup exists. Export to game first to create one.")
        restored = 0
        for fname in os.listdir(backup_dir):
            if fname.endswith(".rpy"):
                src = os.path.join(backup_dir, fname)
                dst = os.path.join(game_dir, fname)
                shutil.copy2(src, dst)
                restored += 1
        log.info("Restored %d original .rpy files", restored)
