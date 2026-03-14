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

# Lines to skip (non-translatable Ren'Py commands)
_SKIP_RE = re.compile(
    r'^\s*(?:'
    r'default\s|init\s|python\s*:|image\s|transform\s|'
    r'scene\s|show\s|hide\s|with\s|play\s|stop\s|queue\s|'
    r'pause\s|jump\s|call\s|return|pass|'
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
                     context_size: int = 3) -> list[TranslationEntry]:
        """Extract all translatable strings from a Ren'Py project."""
        game_dir = os.path.join(project_dir, "game")
        entries: list[TranslationEntry] = []

        # Parse character definitions first (for speaker context)
        self._char_names = {}
        for fname in sorted(os.listdir(game_dir)):
            if fname.endswith(".rpy"):
                fpath = os.path.join(game_dir, fname)
                self._parse_char_defs(fpath)

        # Extract character names from names.rpy (or wherever defines are)
        for fname in sorted(os.listdir(game_dir)):
            if not fname.endswith(".rpy"):
                continue
            fpath = os.path.join(game_dir, fname)
            entries.extend(self._extract_char_name_entries(fpath, fname))

        # Extract translatable strings from each .rpy file
        for fname in sorted(os.listdir(game_dir)):
            if not fname.endswith(".rpy"):
                continue
            if fname in _SKIP_FILES:
                continue
            fpath = os.path.join(game_dir, fname)
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
        try:
            with open(fpath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        for match in _CHAR_DEF_RE.finditer(content):
            alias, name = match.group(1), match.group(2)
            if name:  # skip empty names (narrator variants)
                self._char_names[alias] = name

    def _extract_char_name_entries(self, fpath: str,
                                   fname: str) -> list[TranslationEntry]:
        """Extract character name definitions as translatable entries."""
        entries = []
        try:
            with open(fpath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
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

        try:
            with open(fpath, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.rstrip("\n\r")

            # Track labels for entry IDs
            label_match = _LABEL_RE.match(line)
            if label_match:
                current_label = label_match.group(1)
                dialogue_index = 0
                continue

            # Track menu blocks
            if re.match(r'\s+menu\s*:', line):
                continue

            # Skip non-translatable lines (including define)
            stripped = line.strip()
            if not stripped or _SKIP_RE.match(stripped):
                continue
            # Also skip character definitions (handled separately)
            if stripped.startswith("define "):
                continue

            # Menu choices
            choice_match = _CHOICE_RE.match(line)
            if choice_match:
                text = choice_match.group(2)
                if text.strip():
                    entry_id = f"{fname}/{current_label}/choice_{dialogue_index}"
                    entries.append(TranslationEntry(
                        id=entry_id,
                        file=fname,
                        field="choice",
                        original=text,
                        translation="",
                        status="untranslated",
                        context="\n".join(recent_context[-context_size:]),
                    ))
                    dialogue_index += 1
                continue

            # Dialogue: character "text"
            dlg_match = _DIALOGUE_RE.match(line)
            if dlg_match:
                alias = dlg_match.group(2)
                text = dlg_match.group(3)
                if text.strip():
                    speaker = self._char_names.get(alias, alias)
                    entry_id = (f"{fname}/{current_label}/"
                                f"dialog_{dialogue_index}")
                    ctx_parts = recent_context[-context_size:]
                    if speaker:
                        ctx_parts.insert(0, f"[Speaker: {speaker}]")
                    entries.append(TranslationEntry(
                        id=entry_id,
                        file=fname,
                        field="dialog",
                        original=text,
                        translation="",
                        status="untranslated",
                        context="\n".join(ctx_parts),
                    ))
                    recent_context.append(f"{speaker}: {text[:60]}")
                    dialogue_index += 1
                continue

            # Narration: "text"
            narr_match = _NARRATION_RE.match(line)
            if narr_match:
                text = narr_match.group(2)
                if text.strip() and not _STYLE_VALUE_RE.match(text.strip()):
                    entry_id = (f"{fname}/{current_label}/"
                                f"dialog_{dialogue_index}")
                    entries.append(TranslationEntry(
                        id=entry_id,
                        file=fname,
                        field="dialog",
                        original=text,
                        translation="",
                        status="untranslated",
                        context="\n".join(
                            recent_context[-context_size:]),
                    ))
                    recent_context.append(text[:60])
                    dialogue_index += 1
                continue

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
            return

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
        try:
            with open(source_path, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            with open(source_path, "r", encoding="utf-8",
                      errors="replace") as f:
                lines = f.readlines()

        current_label = "start"
        dialogue_index = 0
        changed = False
        output_lines = list(lines)

        for line_idx, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n\r")

            # Track labels
            label_match = _LABEL_RE.match(line)
            if label_match:
                current_label = label_match.group(1)
                dialogue_index = 0
                continue

            stripped = line.strip()
            if not stripped or _SKIP_RE.match(stripped):
                continue

            # Menu choices
            choice_match = _CHOICE_RE.match(line)
            if choice_match:
                entry_id = f"{fname}/{current_label}/choice_{dialogue_index}"
                if entry_id in trans_map:
                    entry = trans_map[entry_id]
                    indent = choice_match.group(1)
                    condition = choice_match.group(3) or ""
                    new_line = f'{indent}"{entry.translation}"{condition}:\n'
                    output_lines[line_idx] = new_line
                    changed = True
                dialogue_index += 1
                continue

            # Character definitions
            char_match = _CHAR_DEF_RE.match(stripped)
            if char_match:
                alias = char_match.group(1)
                entry_id = f"{fname}/define/{alias}"
                if entry_id in trans_map:
                    entry = trans_map[entry_id]
                    old_name = char_match.group(2)
                    new_line = raw_line.replace(
                        f'"{old_name}"', f'"{entry.translation}"', 1)
                    output_lines[line_idx] = new_line
                    changed = True
                continue

            # Dialogue
            dlg_match = _DIALOGUE_RE.match(line)
            if dlg_match:
                entry_id = (f"{fname}/{current_label}/"
                            f"dialog_{dialogue_index}")
                if entry_id in trans_map:
                    entry = trans_map[entry_id]
                    indent = dlg_match.group(1)
                    alias = dlg_match.group(2)
                    # Escape any quotes in translation
                    escaped = entry.translation.replace('\\', '\\\\')
                    escaped = escaped.replace('"', '\\"')
                    new_line = f'{indent}{alias} "{escaped}"\n'
                    output_lines[line_idx] = new_line
                    changed = True
                dialogue_index += 1
                continue

            # Narration
            narr_match = _NARRATION_RE.match(line)
            if narr_match:
                entry_id = (f"{fname}/{current_label}/"
                            f"dialog_{dialogue_index}")
                if entry_id in trans_map:
                    entry = trans_map[entry_id]
                    indent = narr_match.group(1)
                    escaped = entry.translation.replace('\\', '\\\\')
                    escaped = escaped.replace('"', '\\"')
                    new_line = f'{indent}"{escaped}"\n'
                    output_lines[line_idx] = new_line
                    changed = True
                dialogue_index += 1
                continue

        if changed:
            with open(target_path, "w", encoding="utf-8") as f:
                f.writelines(output_lines)

        return sum(1 for eid in trans_map
                   if eid.startswith(f"{fname}/"))

    # ── Restore originals ─────────────────────────────────────────────

    def restore_originals(self, project_dir: str):
        """Restore original .rpy files from backup."""
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        if not os.path.isdir(backup_dir):
            log.warning("No backup found at %s", backup_dir)
            return
        restored = 0
        for fname in os.listdir(backup_dir):
            if fname.endswith(".rpy"):
                src = os.path.join(backup_dir, fname)
                dst = os.path.join(game_dir, fname)
                shutil.copy2(src, dst)
                restored += 1
        log.info("Restored %d original .rpy files", restored)
