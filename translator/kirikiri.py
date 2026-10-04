"""Kirikiri / KAG visual novel engine parser (.ks script files).

Supports Kirikiri2/KAG3 games (2000s–present). KAG is the scripting layer
that TyranoScript was later built on, so the syntax is similar but not identical.

File format (.ks):
  - Plain text (cp932 or UTF-8), line-oriented
  - @name chara="Speaker" — sets speaker for following dialogue
  - Plain text lines after @name — dialogue content (may span multiple lines)
  - @e — end of unvoiced text block
  - @ve — end of voiced text block
  - @PV storage="voice_file" — voice cue (before @name)
  - *label — jump target
  - *| — page break (separates dialogue pages)
  - @ or [ prefix — engine commands (skip)
  - ; prefix — comments (skip)
  - 地 (chi) as chara name — narration (narrator voice)

Project structure:
  - data/scenario/*.ks — script files (loose or packed in .xp3 archives)

XP3 archive format:
  - Magic: XP3\\r\\n \\n\\x1a\\x8bg\\x01 (11 bytes)
  - Index offset at byte 11 (uint64 LE)
  - Index may be redirected (flag 0x80) or inline (flag 0x01=compressed, 0x00=raw)
  - File entries: 'File' chunks containing 'info' (path), 'segm' (data location), 'adlr' (checksum)
  - File data stored as zlib-compressed segments at offsets within the archive
"""

import logging
import os
import re
import shutil
import struct
import zlib
from pathlib import Path

from .project_model import TranslationEntry
from . import JAPANESE_RE

log = logging.getLogger(__name__)

# Speaker tags — three common KAG conventions:
#   @name chara="Speaker Name"           (KAG3 style)
#   [cn name="Speaker" voice="..."]      (alternate KAG style)
#   【Speaker Name】                      (full-width bracket style)
_NAME_TAG = re.compile(r'@name\s+chara="([^"]*)"', re.IGNORECASE)
_CN_TAG = re.compile(r'\[cn\s+name="([^"]*)"', re.IGNORECASE)
_BRACKET_SPEAKER = re.compile(r'^【([^】]+)】\s*$')

# Voice cue: @PV storage="voice_file"
_VOICE_TAG = re.compile(r'@PV\s+storage="([^"]*)"', re.IGNORECASE)

# End markers — @e / @ve (KAG3) or [en] (alternate)
_END_UNVOICED = re.compile(r'^@e\s*$', re.IGNORECASE)
_END_VOICED = re.compile(r'^@ve\s*$', re.IGNORECASE)
_END_CN = re.compile(r'^\[en\]\s*$', re.IGNORECASE)

# Command line: starts with @ or [
_COMMAND_LINE = re.compile(r'^[@\[]')

# Label line: starts with *
_LABEL_LINE = re.compile(r'^\*')

# Comment line: starts with ;
_COMMENT_LINE = re.compile(r'^;')

# Inline formatting tags that are part of dialogue text, not standalone commands
_INLINE_TAGS = re.compile(
    r'^\[(ruby|/ruby|font|/font|style|/style|b|/b|i|/i|u|/u|r|l|heart|'
    r'graph|emb|ch|line|resetfont|resetstyle)\b', re.IGNORECASE
)


def _is_inline_tag(line: str) -> bool:
    """Check if a [tag] line is an inline formatting tag (part of text)."""
    return bool(_INLINE_TAGS.match(line))

# Characters LLMs emit that cp932 can't encode (from CrowdParser table)
_CP932_REPLACEMENTS = {
    "\u2014": "--",   # em dash
    "\u2013": "-",    # en dash
    "\u00b7": ".",    # middle dot
    "\u2026": "...",  # horizontal ellipsis
    "\u2018": "'", "\u2019": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"',   # curly double quotes
    "\u00a0": " ",    # no-break space
}


def _cp932_safe(text: str) -> str:
    """Map common non-cp932 characters to ASCII; '?' for anything left."""
    for src, dst in _CP932_REPLACEMENTS.items():
        text = text.replace(src, dst)
    try:
        text.encode("cp932")
    except UnicodeEncodeError:
        log.warning("Translation has characters cp932 can't encode; "
                    "replacing with '?': %r", text[:60])
        text = text.encode("cp932", errors="replace").decode("cp932")
    return text


# ── XP3 archive constants ─────────────────────────────────────
_XP3_MAGIC = b'XP3\x0D\x0A\x20\x0A\x1A\x8B\x67\x01'
_XP3_INDEX_CONTINUE = 0x80
_XP3_INDEX_COMPRESSED = 0x01
_XP3_INDEX_UNCOMPRESSED = 0x00


def is_xp3_file(path: str) -> bool:
    """Check if a file is an XP3 archive."""
    try:
        with open(path, "rb") as f:
            return f.read(11) == _XP3_MAGIC
    except Exception:
        return False


def _read_xp3_index(f) -> bytes:
    """Read and return the (decompressed) XP3 file index from an open file.

    Seeks straight to the index instead of loading the whole archive.
    """
    f.seek(0, os.SEEK_END)
    file_size = f.tell()
    f.seek(0)
    if f.read(11) != _XP3_MAGIC:
        raise ValueError("Not an XP3 archive")

    index_offset = struct.unpack('<Q', f.read(8))[0]
    if not index_offset or index_offset >= file_size:
        raise ValueError(f"Invalid index offset: {index_offset}")

    f.seek(index_offset)
    flag = f.read(1)[0]

    if flag == _XP3_INDEX_CONTINUE:
        # Index is elsewhere: skip 8 bytes, read real offset
        f.seek(index_offset + 1 + 8)
        real_offset = struct.unpack('<Q', f.read(8))[0]
        if real_offset >= file_size:
            raise ValueError(f"Invalid redirected index offset: {real_offset}")
        index_offset = real_offset
        f.seek(index_offset)
        flag = f.read(1)[0]

    if flag == _XP3_INDEX_COMPRESSED:
        comp_size, uncomp_size = struct.unpack('<QQ', f.read(16))
        index_data = zlib.decompress(f.read(comp_size))
        if len(index_data) != uncomp_size:
            log.warning("XP3 index size mismatch: got %d, expected %d",
                        len(index_data), uncomp_size)
    elif flag == _XP3_INDEX_UNCOMPRESSED:
        uncomp_size = struct.unpack('<Q', f.read(8))[0]
        index_data = f.read(uncomp_size)
    else:
        raise ValueError(f"Unexpected XP3 index flag: 0x{flag:02x}")
    return index_data


def _iter_xp3_files(index_data: bytes):
    """Yield (path, segments) for each 'File' chunk of an XP3 index.

    segments = [(is_compressed, offset, uncomp_size, comp_size), ...].
    Unknown top-level chunks (hnfn, eliF, ...) are skipped by size.
    """
    pos = 0
    while pos + 12 <= len(index_data):
        chunk_name = index_data[pos:pos + 4]
        chunk_size = struct.unpack_from('<Q', index_data, pos + 4)[0]
        pos += 12
        chunk_end = pos + chunk_size
        if chunk_name != b'File':
            pos = chunk_end
            continue

        # Parse sub-chunks within this File entry
        file_path = None
        segments = []
        while pos + 12 <= chunk_end:
            sub_name = index_data[pos:pos + 4]
            sub_size = struct.unpack_from('<Q', index_data, pos + 4)[0]
            sub_start = pos + 12

            if sub_name == b'info':
                # flags(4) + uncomp_size(8) + comp_size(8) + path_len(2) + path(UTF-16LE)
                path_len = struct.unpack_from('<H', index_data, sub_start + 20)[0]
                path_bytes = index_data[sub_start + 22: sub_start + 22 + path_len * 2]
                file_path = path_bytes.decode('utf-16le', errors='replace')
            elif sub_name == b'segm':
                for s in range(sub_size // 28):
                    seg_off = sub_start + s * 28
                    is_comp = struct.unpack_from('<?', index_data, seg_off)[0]
                    seg_data_off, seg_uncomp, seg_comp = struct.unpack_from(
                        '<QQQ', index_data, seg_off + 4)
                    segments.append((is_comp, seg_data_off, seg_uncomp, seg_comp))
            # skip adlr, time, etc.

            pos = sub_start + sub_size

        pos = chunk_end
        if file_path and segments:
            yield file_path, segments


def _safe_xp3_target(output_dir: str, file_path: str) -> str | None:
    """Map an archive path to a location inside output_dir.

    Returns None for paths that would escape output_dir (absolute paths,
    drive letters, '..' traversal).
    """
    rel = file_path.replace("\\", "/").lstrip("/")
    if not rel or ":" in rel:
        return None
    rel = os.path.normpath(rel.replace("/", os.sep))
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    base = os.path.abspath(output_dir)
    target = os.path.abspath(os.path.join(base, rel))
    try:
        if os.path.commonpath([base, target]) != base:
            return None
    except ValueError:  # different drives on Windows
        return None
    return target


def extract_xp3(xp3_path: str, output_dir: str, filter_ext: str | None = None) -> list[str]:
    """Extract files from an XP3 archive.

    Args:
        xp3_path: Path to .xp3 file
        output_dir: Directory to extract into
        filter_ext: If set, only extract files with this extension (e.g. ".ks")

    Returns:
        List of extracted file paths (relative to output_dir)
    """
    extracted = []
    with open(xp3_path, "rb") as f:
        try:
            index_data = _read_xp3_index(f)
        except ValueError as e:
            raise ValueError(f"{e}: {xp3_path}") from None

        for file_path, segments in _iter_xp3_files(index_data):
            # Apply extension filter
            if filter_ext and not file_path.lower().endswith(filter_ext.lower()):
                continue

            out_path = _safe_xp3_target(output_dir, file_path)
            if out_path is None:
                log.warning("Skipping XP3 entry with unsafe path: %r", file_path)
                continue

            # Read and decompress file data from all segments
            parts = []
            for is_comp, seg_off, seg_uncomp, seg_comp in segments:
                f.seek(seg_off)
                seg_raw = f.read(seg_comp if is_comp else seg_uncomp)
                if is_comp:
                    seg_raw = zlib.decompress(seg_raw)
                parts.append(seg_raw)

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as out:
                out.write(b''.join(parts))
            extracted.append(file_path)

    log.info("Extracted %d files from %s", len(extracted), os.path.basename(xp3_path))
    return extracted


def find_scenario_xp3(project_dir: str) -> str | None:
    """Find an XP3 archive containing scenario .ks files."""
    # Look for common naming patterns
    candidates = []
    for root, dirs, files in os.walk(project_dir):
        for f in files:
            if f.lower().endswith(".xp3"):
                full = os.path.join(root, f)
                fl = f.lower()
                # Prioritize archives with "scenario" in the name
                if "scenario" in fl:
                    candidates.insert(0, full)
                elif "data" == fl.replace(".xp3", ""):
                    candidates.append(full)
        # Don't recurse too deep
        if root != project_dir:
            dirs.clear()

    # Test each candidate for .ks content
    for xp3_path in candidates:
        try:
            with open(xp3_path, "rb") as f:
                index_data = _read_xp3_index(f)
            # Check for .ks file paths in the index (UTF-16LE encoded)
            if b'.\x00k\x00s\x00' in index_data:  # ".ks" in UTF-16LE
                return xp3_path
        except Exception:
            continue
    return None


class KirikiriParser:
    """Parser for Kirikiri/KAG .ks script files."""

    def __init__(self):
        self.context_size = 3

    def load_project(self, project_dir: str, context_size: int | None = None) -> list[TranslationEntry]:
        """Parse all .ks files from a Kirikiri project."""
        if context_size is not None:
            self.context_size = context_size
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            log.warning("No scenario directory found in %s", project_dir)
            return []

        entries = []
        ks_files = sorted(Path(scenario_dir).rglob("*.ks"))

        for ks_path in ks_files:
            rel_path = str(ks_path.relative_to(Path(scenario_dir)))
            rel_path = rel_path.replace("\\", "/")
            file_entries = self._parse_ks_file(ks_path, rel_path)
            entries.extend(file_entries)

        log.info("Parsed %d entries from %d .ks files", len(entries), len(ks_files))
        return entries

    def save_project(self, project_dir: str, entries: list[TranslationEntry]):
        """Export translations back into .ks files."""
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            log.error("No scenario directory found for export")
            return

        backup_dir = os.path.join(os.path.dirname(scenario_dir), "scenario_original")

        # Create backup on first export
        if not os.path.exists(backup_dir):
            shutil.copytree(scenario_dir, backup_dir)
            log.info("Backed up scenario/ to scenario_original/")

        # Group entries by file
        by_file: dict[str, list[TranslationEntry]] = {}
        for entry in entries:
            if entry.translation and entry.status in ("translated", "reviewed"):
                by_file.setdefault(entry.file, []).append(entry)

        # Files with no (remaining) translations are reset from the backup
        # so reverted entries don't keep English from an earlier export.
        for ks in Path(backup_dir).rglob("*.ks"):
            rel_path = str(ks.relative_to(Path(backup_dir))).replace("\\", "/")
            if rel_path not in by_file:
                live_path = os.path.join(scenario_dir, rel_path)
                os.makedirs(os.path.dirname(live_path), exist_ok=True)
                shutil.copy2(str(ks), live_path)

        export_count = 0
        for rel_path, file_entries in by_file.items():
            # Always read from backup for idempotent re-export
            backup_path = os.path.join(backup_dir, rel_path)
            live_path = os.path.join(scenario_dir, rel_path)
            source = backup_path if os.path.exists(backup_path) else live_path

            if not os.path.exists(source):
                log.warning("Source file not found: %s", source)
                continue

            encoding = self._detect_encoding(source)
            content = self._read_file(source)
            lines = content.split("\n")

            # Re-parse the source exactly like load did to recover the real
            # line indices of each entry (blocks may contain blank lines).
            line_map: dict[str, list[int]] = {}
            self._parse_lines(lines, rel_path, line_map)

            translated_lines = self._apply_translations(
                lines, file_entries, line_map,
                cp932=(encoding == "cp932"))
            translated_content = "\n".join(translated_lines)

            # Keep the source encoding — the engine decides how to read the
            # file from its bytes (BOM / none), so never switch it silently.
            os.makedirs(os.path.dirname(live_path), exist_ok=True)
            with open(live_path, "w", encoding=encoding, errors="replace") as f:
                f.write(translated_content)

            export_count += len(file_entries)

        log.info("Exported %d translations to scenario/", export_count)

    def restore_originals(self, project_dir: str):
        """Restore original scenario files from backup."""
        scenario_dir = self._find_scenario_dir(project_dir)
        backup_dir = (os.path.join(os.path.dirname(scenario_dir), "scenario_original")
                      if scenario_dir else None)
        if not backup_dir or not os.path.isdir(backup_dir):
            raise FileNotFoundError(
                "No scenario_original/ backup exists. Export to game first to create one.")
        shutil.rmtree(scenario_dir)
        shutil.copytree(backup_dir, scenario_dir)
        log.info("Restored scenario/ from backup")

    def get_game_title(self, project_dir: str) -> str:
        """Try to extract game title from startup.tjs/Config.tjs or folder name."""
        title = self._find_title_in_dir(project_dir)
        if title:
            return title

        # Check inside XP3 archives for title-setting .tjs files
        # Skip archives > 50MB to avoid loading huge asset packs into memory
        import tempfile
        _MAX_XP3_TITLE_SIZE = 50 * 1024 * 1024
        for xp3_name in ["data.xp3", "data_system.xp3", "data_sys.xp3"]:
            xp3_path = os.path.join(project_dir, xp3_name)
            if not os.path.exists(xp3_path) or not is_xp3_file(xp3_path):
                continue
            if os.path.getsize(xp3_path) > _MAX_XP3_TITLE_SIZE:
                continue
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    extract_xp3(xp3_path, tmpdir, filter_ext=".tjs")
                    title = self._find_title_in_dir(tmpdir)
                    if title:
                        return title
            except Exception:
                continue

        return os.path.basename(project_dir)

    def _find_title_in_dir(self, search_dir: str) -> str | None:
        """Search .tjs files in a directory for System.title assignment."""
        for root, _dirs, files in os.walk(search_dir):
            for fname in files:
                if not fname.lower().endswith(".tjs"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    text = self._read_file(fpath)
                    # Match uncommented System.title = "..." (skip lines starting with //)
                    for line in text.split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("//"):
                            continue
                        m = re.search(r'System\.title\s*=\s*"([^"]+)"', stripped)
                        if m:
                            title = m.group(1)
                            # Skip generic engine titles
                            if "adv game" not in title.lower():
                                return title
                except Exception:
                    continue
        return None

    @staticmethod
    def is_kirikiri_project(path: str) -> bool:
        """Check if path is a Kirikiri/KAG project.

        Looks for data/scenario/*.ks with @name chara= tags (KAG style),
        a startup.tjs file (Kirikiri engine marker), or XP3 archives
        containing .ks scenario files.
        """
        if not os.path.isdir(path):
            return False

        # TyranoScript also uses .ks + 【】 — its tyrano/ runtime folder wins
        if (os.path.isdir(os.path.join(path, "tyrano")) or
                os.path.isdir(os.path.join(path, "extracted", "tyrano"))):
            return False

        # Check for startup.tjs (definitive Kirikiri marker)
        if os.path.exists(os.path.join(path, "data", "startup.tjs")):
            return True

        # Check for data/scenario/**/*.ks with KAG markers (recursive)
        scenario_dir = os.path.join(path, "data", "scenario")
        if os.path.isdir(scenario_dir):
            from pathlib import Path
            for ks_path in Path(scenario_dir).rglob("*.ks"):
                try:
                    with open(ks_path, "rb") as f:
                        head = f.read(4096)
                    text = head.decode("cp932", errors="replace")
                    text_lower = text.lower()
                    # KAG3 markers: @name chara=, [cn name=, [begintrans],
                    # [endtrans], startup.tjs reference, or 【】 speaker brackets
                    if (("@name " in text_lower and "chara=" in text_lower) or
                            ("[cn " in text_lower and "name=" in text_lower) or
                            "[begintrans]" in text_lower or
                            "[endtrans" in text_lower or
                            "【" in text):
                        return True
                except Exception:
                    continue

        # Check for XP3 archives containing scenario .ks files
        if find_scenario_xp3(path):
            return True

        return False

    # ── Private helpers ─────────────────────────────────────

    def _find_scenario_dir(self, project_dir: str) -> str | None:
        """Find the data/scenario/ directory, extracting from XP3 if needed."""
        candidates = [
            os.path.join(project_dir, "data", "scenario"),
            os.path.join(project_dir, "scenario"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d

        # No loose scenario dir — try extracting from XP3
        xp3_path = find_scenario_xp3(project_dir)
        if xp3_path:
            extract_dir = os.path.join(project_dir, "data", "scenario")
            log.info("Extracting scenario files from %s", os.path.basename(xp3_path))
            extracted = extract_xp3(xp3_path, extract_dir, filter_ext=".ks")
            if extracted:
                return extract_dir

        return None

    def _detect_encoding(self, path: str) -> str:
        """Detect if a file is UTF-16 (BOM), UTF-8 or cp932."""
        with open(path, "rb") as f:
            raw = f.read()
        # BOM check — Kirikiri commonly ships UTF-16LE scripts with a BOM
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return "utf-16"
        if raw[:3] == b"\xef\xbb\xbf":
            return "utf-8-sig"
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "cp932"

    def _read_file(self, path: str) -> str:
        """Read a text file, auto-detecting encoding."""
        encoding = self._detect_encoding(path)
        with open(path, "r", encoding=encoding, errors="replace") as f:
            return f.read()

    def _parse_ks_file(self, ks_path: Path, rel_path: str) -> list[TranslationEntry]:
        """Parse a single .ks file into TranslationEntry list."""
        content = self._read_file(str(ks_path))
        return self._parse_lines(content.split("\n"), rel_path)

    def _parse_lines(self, lines: list[str], rel_path: str,
                     line_map: dict[str, list[int]] | None = None
                     ) -> list[TranslationEntry]:
        """Parse .ks lines into entries.

        If line_map is given, it receives entry_id -> the exact source line
        indices that make up the entry's text (used by export).
        """
        entries = []
        recent_context: list[str] = []
        current_label = ""

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()

            # Track labels for context
            if stripped.startswith("*") and not stripped.startswith("*|"):
                current_label = stripped[1:]
                i += 1
                continue

            # Skip comment lines
            if _COMMENT_LINE.match(stripped):
                i += 1
                continue

            # Look for speaker tag: @name chara="..." or [cn name="..."]
            name_m = _NAME_TAG.match(stripped)
            cn_m = _CN_TAG.match(stripped) if not name_m else None
            if name_m or cn_m:
                speaker = (name_m or cn_m).group(1)
                # Strip fullwidth spaces from speaker name (e.g. "栄　太" → "栄太")
                speaker = speaker.replace("\u3000", "")
                first_text_line = -1
                text_lines = []
                text_idx = []
                is_cn_format = cn_m is not None

                # Collect text lines until end marker or next command
                j = i + 1
                while j < len(lines):
                    tline = lines[j].rstrip()
                    tstripped = tline.strip()

                    if not tstripped:
                        j += 1
                        continue

                    # Check end markers
                    if is_cn_format:
                        if _END_CN.match(tstripped):
                            j += 1  # consume [en]
                            break
                    else:
                        if (_END_UNVOICED.match(tstripped) or
                                _END_VOICED.match(tstripped)):
                            j += 1  # consume @e/@ve
                            break

                    # Check for commands — but skip inline formatting tags
                    if _LABEL_LINE.match(tstripped) or _COMMENT_LINE.match(tstripped):
                        break
                    if _COMMAND_LINE.match(tstripped):
                        # Inline tags like [ruby], [font], [style] are part of text
                        if not _is_inline_tag(tstripped):
                            break

                    if first_text_line < 0:
                        first_text_line = j
                    text_lines.append(tline)
                    text_idx.append(j)
                    j += 1

                if text_lines:
                    # Join multi-line text
                    full_text = "\n".join(text_lines)

                    # Determine field type
                    # 地 = narration, ト書き = stage directions (both = no speaker)
                    if speaker in ("地", "ト書き"):
                        field = "narration"
                        display_speaker = ""
                    else:
                        field = "dialogue"
                        display_speaker = speaker

                    # Only include entries with translatable text
                    if JAPANESE_RE.search(full_text) or self._has_translatable_text(full_text):
                        entry_id = f"{rel_path}/{field}/{first_text_line}"
                        if line_map is not None:
                            line_map[entry_id] = text_idx

                        # Build context
                        ctx_parts = []
                        if current_label:
                            ctx_parts.append(f"[Label: {current_label}]")
                        ctx_parts.extend(recent_context[-self.context_size:])

                        entry = TranslationEntry(
                            id=entry_id,
                            file=rel_path,
                            field=field,
                            original=full_text,
                            context="\n".join(ctx_parts),
                            namebox=display_speaker,
                        )
                        entries.append(entry)

                        # Update context
                        ctx_line = (f"[{display_speaker}] {full_text}"
                                    if display_speaker else full_text)
                        recent_context.append(ctx_line)
                        if len(recent_context) > self.context_size:
                            recent_context.pop(0)

                i = j
                continue

            # 【Speaker】 bracket format — speaker on own line, dialogue follows
            bracket_m = _BRACKET_SPEAKER.match(stripped)
            if bracket_m:
                speaker = bracket_m.group(1).replace("\u3000", "")
                text_lines = []
                text_idx = []
                first_text_line = -1
                j = i + 1
                while j < len(lines):
                    tline = lines[j].rstrip()
                    tstripped = tline.strip()
                    if not tstripped:
                        # Blank line = end of dialogue block
                        j += 1
                        break
                    # Stop at next speaker, command, or label
                    if (_BRACKET_SPEAKER.match(tstripped) or
                            _LABEL_LINE.match(tstripped) or
                            _COMMENT_LINE.match(tstripped)):
                        break
                    if _COMMAND_LINE.match(tstripped) and not _is_inline_tag(tstripped):
                        break
                    if first_text_line < 0:
                        first_text_line = j
                    text_lines.append(tline)
                    text_idx.append(j)
                    j += 1

                if text_lines:
                    full_text = "\n".join(text_lines)
                    field = "narration" if speaker in ("地", "ト書き") else "dialogue"
                    display_speaker = "" if field == "narration" else speaker

                    if JAPANESE_RE.search(full_text) or self._has_translatable_text(full_text):
                        ctx_parts = []
                        if current_label:
                            ctx_parts.append(f"[Label: {current_label}]")
                        ctx_parts.extend(recent_context[-self.context_size:])
                        if line_map is not None:
                            line_map[f"{rel_path}/{field}/{first_text_line}"] = text_idx
                        entries.append(TranslationEntry(
                            id=f"{rel_path}/{field}/{first_text_line}",
                            file=rel_path,
                            field=field,
                            original=full_text,
                            context="\n".join(ctx_parts),
                            namebox=display_speaker,
                        ))
                        ctx_line = (f"[{display_speaker}] {full_text}"
                                    if display_speaker else full_text)
                        recent_context.append(ctx_line)
                        if len(recent_context) > self.context_size:
                            recent_context.pop(0)

                i = j
                continue

            # Plain text lines (narration without speaker tag)
            # Only if the line has Japanese and isn't a command
            if (not _COMMAND_LINE.match(stripped) and
                    not _LABEL_LINE.match(stripped) and
                    stripped and JAPANESE_RE.search(stripped)):
                # Collect consecutive plain text lines
                text_lines = [line]
                text_idx = [i]
                first_text_line = i
                j = i + 1
                while j < len(lines):
                    tline = lines[j].rstrip()
                    tstripped = tline.strip()
                    if not tstripped:
                        j += 1
                        break
                    if (_COMMAND_LINE.match(tstripped) or
                            _LABEL_LINE.match(tstripped) or
                            _COMMENT_LINE.match(tstripped) or
                            _BRACKET_SPEAKER.match(tstripped)):
                        break
                    if _NAME_TAG.match(tstripped) or _CN_TAG.match(tstripped):
                        break
                    text_lines.append(tline)
                    text_idx.append(j)
                    j += 1

                full_text = "\n".join(text_lines)
                if JAPANESE_RE.search(full_text):
                    ctx_parts = []
                    if current_label:
                        ctx_parts.append(f"[Label: {current_label}]")
                    ctx_parts.extend(recent_context[-self.context_size:])
                    if line_map is not None:
                        line_map[f"{rel_path}/narration/{first_text_line}"] = text_idx
                    entries.append(TranslationEntry(
                        id=f"{rel_path}/narration/{first_text_line}",
                        file=rel_path,
                        field="narration",
                        original=full_text,
                        context="\n".join(ctx_parts),
                    ))
                    recent_context.append(full_text)
                    if len(recent_context) > self.context_size:
                        recent_context.pop(0)

                i = j
                continue

            i += 1

        return entries

    def _has_translatable_text(self, text: str) -> bool:
        """Check if text has content worth translating (non-empty, non-command)."""
        # Already translated text (English) is still valid content
        stripped = text.strip()
        if not stripped:
            return False
        # Skip if it's only whitespace, numbers, or punctuation
        if re.match(r'^[\s\d\W]*$', stripped):
            return False
        return True

    def _apply_translations(self, lines: list[str],
                            entries: list[TranslationEntry],
                            line_map: dict[str, list[int]],
                            cp932: bool = False) -> list[str]:
        """Apply translations to a list of source lines.

        Each entry replaces exactly the source lines it was parsed from
        (line_map, from re-parsing the source); the line count never changes.
        """
        result = list(lines)
        for entry in entries:
            if not entry.translation:
                continue
            indices = line_map.get(entry.id)
            if indices is None:
                # Entry not found by re-parse — fall back to the ID's start
                # line + the original's line count (contiguous block)
                try:
                    start = int(entry.id.rsplit("/", 1)[-1])
                except ValueError:
                    continue
                indices = list(range(start, start + len(entry.original.split("\n"))))
            indices = [k for k in indices if k < len(result)]
            if not indices:
                log.warning("Entry %s out of range", entry.id)
                continue

            num_original = len(indices)
            translation = entry.translation
            if cp932:
                translation = _cp932_safe(translation)
            translation_lines = translation.split("\n")

            # Pad or trim translation to match original line count
            # (preserves @e/@ve alignment)
            while len(translation_lines) < num_original:
                translation_lines[-1] += " "  # pad last line
                if len(translation_lines) < num_original:
                    translation_lines.append("")
            if len(translation_lines) > num_original:
                # Join excess into the last line
                last = " ".join(translation_lines[num_original - 1:])
                translation_lines = translation_lines[:num_original - 1] + [last]

            for k, text in zip(indices, translation_lines):
                result[k] = text

        return result
