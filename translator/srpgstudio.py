"""SRPG Studio parser — extracts translatable text from data.dts archives.

SRPG Studio stores all game data in a single encrypted archive (data.dts).
Resources (images, audio) live as external .srk files; the project data
(Project.srpgs blob) at the end of data.dts contains all game text:
character names, dialogue, item descriptions, skill names, map labels, etc.

Encryption:
  - v < 1301: RC4 key = MD5(first len("keyset") bytes of "keyset" as UTF-16LE)
  - v >= 1301: RC4 key = MD5(first len("_dynamic") bytes of "_dynamic" as UTF-16LE)
  Note: Sinflower's C++ code passes wstring::length() (char count) as the
  byte count to CryptHashData, so only the first N bytes of the UTF-16LE
  representation are hashed (not N*2).  We replicate this behaviour.

String format inside Project.srpgs:
  [uint32 byte_length] [UTF-16LE data including \\x00\\x00 terminator]

Translation approach:
  1. Decrypt → extract Project.srpgs blob
  2. Scan for length-prefixed UTF-16LE strings containing Japanese
  3. Present as TranslationEntry objects (same interface as RPG Maker)
  4. On export: sequential streaming rewrite of the blob (handles size changes)
  5. Re-encrypt and write back to data.dts
"""

import logging
import os
import re
import shutil
import struct
from typing import Optional

from Crypto.Cipher import ARC4
from Crypto.Hash import MD5

from . import JAPANESE_RE
from .project_model import TranslationEntry

log = logging.getLogger(__name__)

# SDTS header constants
_SDTS_MAGIC = b"SDTS"
_HEADER_SIZE = 168  # 24 fixed bytes + 36 × 4 section offsets
_NEW_CRYPT_VERSION = 1301  # v >= 1301 uses "_dynamic" key
_SECTION_COUNT = 36

# Encryption keys (replicate Sinflower's truncated-length behaviour)
_KEY_OLD = "keyset"
_KEY_NEW = "_dynamic"


def _derive_rc4_key(password: str) -> bytes:
    """Derive RC4 key matching SRPG Studio's CryptoAPI usage.

    The C++ code passes wstring::length() (character count) as the byte
    count to CryptHashData, so only the first len(password) bytes of the
    UTF-16LE representation are hashed — not len(password)*2.
    """
    utf16 = password.encode("utf-16-le")
    truncated = utf16[:len(password)]  # Bug-compatible with Sinflower
    return MD5.new(truncated).digest()


def _rc4_crypt(data: bytes, password: str) -> bytes:
    """Encrypt or decrypt data with RC4 (symmetric)."""
    key = _derive_rc4_key(password)
    return ARC4.new(key).decrypt(data)


def _has_japanese(text: str) -> bool:
    """Check if text contains any Japanese characters."""
    return bool(JAPANESE_RE.search(text))


# Characters that indicate a string is clean displayable text (not binary noise)
_CLEAN_RE = re.compile(
    r'^['
    r'\u0020-\u007E'    # ASCII printable
    r'\u3000-\u9FFF'    # CJK (hiragana, katakana, kanji)
    r'\uFF00-\uFFEF'    # Fullwidth forms
    r'\u2000-\u206F'    # General punctuation
    r'\u00A0-\u00FF'    # Latin-1 supplement
    r'\u2190-\u27FF'    # Arrows, symbols
    r'\u0300-\u036F'    # Combining diacriticals
    r'\n\r\t'
    r'♡♥…―─　'
    r']+$'
)


class SRPGStudioParser:
    """Parser for SRPG Studio data.dts archives."""

    def __init__(self):
        self._require_japanese = True

    def _should_extract(self, text: str) -> bool:
        """Check if a string should be extracted as translatable."""
        if not text or not text.strip():
            return False
        if self._require_japanese:
            return _has_japanese(text)
        return True

    # ── Detection ─────────────────────────────────────────────

    @staticmethod
    def is_srpgstudio_project(path: str) -> bool:
        """Check if path contains an SRPG Studio game (data.dts)."""
        return os.path.isfile(os.path.join(path, "data.dts"))

    # ── Decryption / Encryption ───────────────────────────────

    @staticmethod
    def _read_header(filepath: str) -> dict:
        """Read the SDTS header from data.dts."""
        with open(filepath, "rb") as f:
            magic = f.read(4)
            if magic != _SDTS_MAGIC:
                raise ValueError(
                    f"Not an SRPG Studio archive (expected SDTS magic, "
                    f"got {magic!r})"
                )
            is_encrypted = struct.unpack("<I", f.read(4))[0]
            version = struct.unpack("<I", f.read(4))[0]
            _runtime = struct.unpack("<I", f.read(4))[0]
            flag = struct.unpack("<I", f.read(4))[0]
            project_offset_rel = struct.unpack("<I", f.read(4))[0]
            project_offset = project_offset_rel + _HEADER_SIZE

            section_offsets = []
            for _ in range(_SECTION_COUNT):
                section_offsets.append(struct.unpack("<I", f.read(4))[0])

            f.seek(0, 2)
            file_size = f.tell()

        return {
            "is_encrypted": bool(is_encrypted),
            "version": version,
            "flag": flag,
            "project_offset": project_offset,
            "project_length": file_size - project_offset,
            "file_size": file_size,
            "section_offsets": section_offsets,
        }

    @staticmethod
    def _get_password(version: int) -> str:
        """Return the encryption password for the given engine version."""
        if version >= _NEW_CRYPT_VERSION:
            return _KEY_NEW
        return _KEY_OLD

    def _extract_project_blob(self, dts_path: str) -> tuple[bytes, dict]:
        """Extract and decrypt the Project.srpgs blob from data.dts.

        Returns:
            (decrypted_blob, header_dict)
        """
        header = self._read_header(dts_path)
        with open(dts_path, "rb") as f:
            f.seek(header["project_offset"])
            raw = f.read(header["project_length"])

        if header["is_encrypted"]:
            password = self._get_password(header["version"])
            raw = _rc4_crypt(raw, password)
            log.info("Decrypted Project.srpgs (%d bytes, key=%r)",
                     len(raw), password)
        else:
            log.info("Project.srpgs is not encrypted (%d bytes)", len(raw))

        return raw, header

    # ── String scanning ───────────────────────────────────────

    def _scan_strings(self, blob: bytes) -> list[tuple[int, int, str]]:
        """Scan a binary blob for length-prefixed UTF-16LE strings.

        Returns:
            List of (byte_offset, byte_length, decoded_text) tuples.
            byte_offset points to the 4-byte length prefix.
            byte_length is the value stored in the prefix (UTF-16LE byte count).
        """
        results = []
        i = 0
        blob_len = len(blob)
        while i < blob_len - 6:
            str_len = struct.unpack_from("<I", blob, i)[0]
            # Valid string: even byte count, reasonable length, fits in blob
            if (str_len >= 4 and str_len <= 8000
                    and str_len % 2 == 0
                    and i + 4 + str_len <= blob_len):
                try:
                    text = blob[i + 4:i + 4 + str_len].decode("utf-16-le")
                    text = text.rstrip("\x00")
                    if text and self._should_extract(text) and _CLEAN_RE.match(text):
                        results.append((i, str_len, text))
                        i += 4 + str_len
                        continue
                except (UnicodeDecodeError, ValueError):
                    pass
            i += 2  # Align to 2-byte boundaries
        return results

    # ── Entry classification ──────────────────────────────────

    @staticmethod
    def _classify_entry(text: str, index: int) -> tuple[str, str]:
        """Classify a string into a field type and generate an entry ID.

        Returns:
            (field_type, entry_id) — field_type is used for LLM hints,
            entry_id is the unique key for this entry.
        """
        has_newline = "\n" in text
        length = len(text)

        if has_newline or length > 30:
            field = "dialogue"
        elif length <= 15:
            field = "name"
        else:
            field = "description"

        entry_id = f"SRPG/{index:05d}/{field}"
        return field, entry_id

    # ── Public interface (matches RPGMakerMVParser) ───────────

    def load_project(self, project_dir: str) -> list[TranslationEntry]:
        """Load all translatable entries from an SRPG Studio project.

        Args:
            project_dir: Path to the game folder containing data.dts.

        Returns:
            List of TranslationEntry objects.
        """
        dts_path = os.path.join(project_dir, "data.dts")
        if not os.path.isfile(dts_path):
            raise FileNotFoundError(
                f"No data.dts found in {project_dir}. "
                "Please select an SRPG Studio game folder."
            )

        blob, header = self._extract_project_blob(dts_path)
        strings = self._scan_strings(blob)

        log.info("Found %d translatable strings in Project.srpgs", len(strings))

        entries = []
        context_window: list[str] = []
        for idx, (offset, byte_len, text) in enumerate(strings):
            field, entry_id = self._classify_entry(text, idx)

            # Build context from recent strings
            context = "\n".join(context_window[-3:]) if context_window else ""

            entry = TranslationEntry(
                id=entry_id,
                file="Project.srpgs",
                field=field,
                original=text,
                context=context,
            )
            entries.append(entry)

            # Add short preview to context window
            preview = text.replace("\n", " ")[:60]
            context_window.append(preview)

        return entries

    def get_game_title(self, project_dir: str) -> str:
        """Try to extract the game title from the project.

        SRPG Studio doesn't have a simple System.json — the title is
        embedded in the binary blob.  We return the folder name as a
        reasonable fallback.
        """
        return os.path.basename(project_dir.rstrip("/\\"))

    def load_actors_raw(self, project_dir: str) -> list[dict]:
        """Load actor/character data for gender assignment.

        SRPG Studio characters are embedded in the binary blob without
        clear type markers.  We extract short name-like strings as
        potential character names.  The gender dialog lets users assign
        genders manually.
        """
        # For SRPG Studio we can't reliably distinguish character names
        # from item/skill names in the binary.  Return empty — user
        # assigns genders manually if needed.
        return []

    # ── Export ─────────────────────────────────────────────────

    def save_project(self, project_dir: str, entries: list[TranslationEntry]):
        """Write translations back into data.dts.

        Uses a sequential streaming rewrite of the Project.srpgs blob:
        for each translated string, the length prefix is updated and the
        new UTF-16LE bytes replace the old ones.  The blob size may change.
        The modified blob is re-encrypted and written back to data.dts.

        A backup of the original data.dts is created as data_original.dts
        on the first export.
        """
        dts_path = os.path.join(project_dir, "data.dts")
        backup_path = os.path.join(project_dir, "data_original.dts")

        # Backup on first export
        if not os.path.isfile(backup_path):
            shutil.copy2(dts_path, backup_path)
            log.info("Created backup: %s", backup_path)

        # Always read from backup for idempotent re-export
        source_path = backup_path if os.path.isfile(backup_path) else dts_path
        blob, header = self._extract_project_blob(source_path)

        # Scan original blob for string positions
        original_strings = self._scan_strings(blob)

        # Build translation lookup: original text → translated text
        # Use a list of (original, translation) to handle duplicates correctly
        translations = {}
        for e in entries:
            if e.translation and e.status in ("translated", "reviewed"):
                translations[e.original] = e.translation

        # Sequential streaming rewrite
        patched = self._patch_blob(blob, original_strings, translations)

        # Re-encrypt if the original was encrypted
        if header["is_encrypted"]:
            password = self._get_password(header["version"])
            patched = _rc4_crypt(patched, password)

        # Read the pre-project portion of the file (header + sections + scripts)
        with open(source_path, "rb") as f:
            pre_project = f.read(header["project_offset"])

        # Write the new data.dts
        with open(dts_path, "wb") as f:
            f.write(pre_project)
            f.write(patched)

        log.info("Exported translations to %s (%d bytes patched)",
                 dts_path, len(patched))

    @staticmethod
    def _patch_blob(blob: bytes,
                    strings: list[tuple[int, int, str]],
                    translations: dict[str, str]) -> bytes:
        """Rewrite the project blob with translated strings.

        Processes strings sequentially, copying unchanged bytes between
        them.  Each translated string gets a new length prefix and
        UTF-16LE encoding.  Untranslated strings are copied as-is.

        This handles size changes correctly because we rebuild the entire
        blob from scratch rather than patching at fixed offsets.
        """
        parts = []
        pos = 0  # Current read position in original blob

        for offset, byte_len, original_text in strings:
            # Copy unchanged bytes before this string
            if offset > pos:
                parts.append(blob[pos:offset])

            translation = translations.get(original_text)
            if translation:
                # Write new length prefix + translated UTF-16LE
                new_encoded = (translation + "\x00").encode("utf-16-le")
                new_len = len(new_encoded)
                parts.append(struct.pack("<I", new_len))
                parts.append(new_encoded)
            else:
                # Copy original string unchanged (length prefix + data)
                parts.append(blob[offset:offset + 4 + byte_len])

            pos = offset + 4 + byte_len

        # Copy remaining bytes after the last string
        if pos < len(blob):
            parts.append(blob[pos:])

        return b"".join(parts)

    def restore_originals(self, project_dir: str):
        """Restore original data.dts from backup."""
        dts_path = os.path.join(project_dir, "data.dts")
        backup_path = os.path.join(project_dir, "data_original.dts")

        if not os.path.isfile(backup_path):
            raise FileNotFoundError("No backup found (data_original.dts)")

        shutil.copy2(backup_path, dts_path)
        log.info("Restored original data.dts from backup")
