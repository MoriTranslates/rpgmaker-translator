"""Safe file I/O shared by every engine writer.

Everything that writes into a user's game folder goes through here so the
guarantees are the same for all engines:

* ``atomic_write_bytes`` / ``atomic_write_text`` / ``atomic_copy`` — write to
  a temp file next to the target, then ``os.replace``.  A crash, full disk or
  serialiser exception leaves the previous file intact instead of a
  truncated game file.
* ``backup_tree_once`` / ``backup_file_once`` — the first-export backup is
  built under a temp name and renamed when complete, so an interrupted copy
  is never mistaken for a full backup later.
* ``restore_tree`` — copies the backup to a temp dir first and only then
  swaps it in, so a failed restore leaves the live folder untouched.
* ``ExportManifest`` + ``refresh_changed_originals`` — remember what each
  export wrote.  If a live file later differs from both the backup and what
  we wrote, the game was updated (or patched by hand) after the backup was
  taken; the backup is re-baselined from it instead of silently rolling the
  update back on the next export.
* ``PartialExportError`` — raised when some files were written and others
  failed, so the UI never reports "Export Complete" for a partial export.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil

log = logging.getLogger(__name__)


class PartialExportError(OSError):
    """Some files were exported, others failed.  ``problems`` lists them."""

    def __init__(self, problems: list[str], summary: str = ""):
        self.problems = list(problems)
        shown = "\n  ".join(self.problems[:20])
        more = (f"\n  ... and {len(self.problems) - 20} more"
                if len(self.problems) > 20 else "")
        head = summary or (
            f"{len(self.problems)} file(s) could not be exported and still "
            f"contain the previous text:")
        super().__init__(f"{head}\n  {shown}{more}")


def raise_if_problems(problems: list[str], summary: str = ""):
    if problems:
        raise PartialExportError(problems, summary)


def raise_export_problems(errors: list[str], skipped: dict[str, int]):
    """Raise PartialExportError for unwritten files and/or skipped lines.

    *skipped* maps a file to the number of translations that were NOT
    written because the game text at their place no longer matched their
    original (game update or hand edit): the game's new text was kept.
    """
    problems = list(errors) + [
        f"{fn}: {n} translated line(s) not written — the game text there "
        f"changed, so the game's new text was kept"
        for fn, n in sorted(skipped.items())]
    if not problems:
        return
    parts = []
    if errors:
        parts.append(f"{len(errors)} file(s) could not be written "
                     f"(is the game running?)")
    if skipped:
        parts.append(f"{sum(skipped.values())} translated line(s) were not "
                     f"written because the game's text changed — re-open "
                     f"the project to pick up the new lines")
    raise PartialExportError(problems,
                             "Export incomplete — " + "; ".join(parts) + ":")


# ── Atomic writes ────────────────────────────────────────────────────

def _tmp_name(path: str) -> str:
    return path + ".rmt-tmp"


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write *data* to *path* via temp file + fsync + os.replace."""
    path = os.fspath(path)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str, text: str, encoding: str = "utf-8",
                      errors: str = "strict", newline: str | None = None) -> None:
    """Text counterpart of :func:`atomic_write_bytes`.

    ``newline`` has the same meaning as for :func:`open` (``""`` keeps the
    string's own line endings untouched).
    """
    path = os.fspath(path)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "w", encoding=encoding, errors=errors,
                  newline=newline) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_copy(src: str, dst: str) -> None:
    """``shutil.copy2`` that never leaves a half-written *dst*."""
    dst = os.fspath(dst)
    tmp = _tmp_name(dst)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ── Backups ──────────────────────────────────────────────────────────

def backup_file_once(src: str, backup: str) -> bool:
    """Copy *src* to *backup* atomically if no backup exists yet."""
    if os.path.exists(backup):
        return False
    atomic_copy(src, backup)
    return True


def backup_tree_once(src_dir: str, backup_dir: str,
                     only: list[str] | None = None) -> bool:
    """Copy *src_dir* to *backup_dir* if no backup exists yet.

    The copy is built in a temp folder and renamed when complete.  With
    ``only``, just those direct children of *src_dir* are copied (used when
    the data lives in the game root and copying the whole game would be
    wrong).  Returns True when a new backup was made.
    """
    src_dir, backup_dir = os.fspath(src_dir), os.fspath(backup_dir)
    if os.path.isdir(backup_dir):
        return False
    tmp = backup_dir + ".rmt-tmp"
    try:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        if only is None:
            shutil.copytree(src_dir, tmp)
        else:
            os.makedirs(tmp)
            for name in only:
                s = os.path.join(src_dir, name)
                d = os.path.join(tmp, name)
                if os.path.dirname(name):      # e.g. "chapters/ch2.rpy"
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                if os.path.isdir(s):
                    shutil.copytree(s, d)
                elif os.path.isfile(s):
                    shutil.copy2(s, d)
        os.rename(tmp, backup_dir)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise OSError(
            f"Could not back up {src_dir} — is the game running?\n\n"
            f"Close the game and try again.\n({exc})") from exc
    log.info("Backed up %s -> %s", src_dir, backup_dir)
    return True


# Written into <folder>.rmt-old right after restore_tree moved the live
# folder aside, removed again before that copy is deleted.  Only a folder
# holding it is a swap that was killed half-way; anything else named
# .rmt-old is junk from a failed cleanup and is never put back in place.
SWAP_MARKER = ".rmt-swap-in-progress"

RECOVERED_NOTE = (
    "Restore Originals was interrupted last time (the app or PC stopped "
    "half-way), so the game's {name} folder was missing. It has been put "
    "back exactly as it was before that restore -- still translated. Run "
    "Restore Originals again if you wanted the original files.")


_recovery_notes: list[str] = []


def take_recovery_notes() -> list[str]:
    """User-facing notes for recoveries since the last call (then cleared)."""
    notes = list(_recovery_notes)
    _recovery_notes.clear()
    return notes


def interrupted_swap(live_dir: str) -> bool:
    """Read-only check: was a restore of *live_dir* killed mid-swap?"""
    live_dir = os.fspath(live_dir)
    return (not os.path.exists(live_dir)
            and os.path.isfile(os.path.join(live_dir + ".rmt-old", SWAP_MARKER)))


def _set_aside(path: str) -> str:
    """Rename *path* to a free ``<path>.leftover-N`` name; never delete."""
    n = 1
    while os.path.exists(f"{path}.leftover-{n}"):
        n += 1
    os.rename(path, f"{path}.leftover-{n}")
    log.warning("Kept leftover folder %s as %s.leftover-%d", path, path, n)
    return f"{path}.leftover-{n}"


def recover_interrupted_restore(live_dir: str) -> bool:
    """Undo a restore that was killed between its two renames.

    ``restore_tree`` moves *live_dir* to ``<live_dir>.rmt-old`` (marking it
    with ``SWAP_MARKER``) and then renames the finished copy into place.  A
    kill in between leaves no *live_dir*: the game won't start.  Put the
    folder back as it was before that restore; the half-built copy is kept
    aside, never deleted.  Call this only from explicit open / export /
    restore paths (never from detection) and tell the user: see
    ``RECOVERED_NOTE``.  Returns True if it recovered.
    """
    live_dir = os.fspath(live_dir)
    if not interrupted_swap(live_dir):
        return False
    aside = live_dir + ".rmt-old"
    os.rename(aside, live_dir)
    try:
        os.remove(os.path.join(live_dir, SWAP_MARKER))
    except OSError as exc:
        # Harmless: the folder is back; a stale marker inside a live folder
        # is never looked at (only <name>.rmt-old is).
        log.warning("Could not remove %s: %s", SWAP_MARKER, exc)
    staging = live_dir + ".rmt-restore"
    if os.path.isdir(staging):
        _set_aside(staging)
    log.warning("Recovered %s from an interrupted Restore Originals",
                live_dir)
    _recovery_notes.append(
        RECOVERED_NOTE.format(name=os.path.basename(live_dir) + "/"))
    return True


def restore_tree(backup_dir: str, live_dir: str,
                 only: list[str] | None = None) -> None:
    """Replace *live_dir* (or just its ``only`` children) with the backup.

    The backup is first copied to a temp folder next to the target; only
    when that copy succeeded is the live folder moved aside and the copy
    renamed into place.  If anything fails the live folder is put back, so
    the game is never left half-deleted.  Never deletes anything outside
    the replaced folder(s).
    """
    backup_dir, live_dir = os.fspath(backup_dir), os.fspath(live_dir)
    real_live = os.path.realpath(live_dir)
    real_bak = os.path.realpath(backup_dir)
    if (os.path.dirname(real_live) == real_live          # a drive root
            or real_bak == real_live
            or real_bak.startswith(real_live.rstrip(os.sep) + os.sep)
            or real_live.startswith(real_bak.rstrip(os.sep) + os.sep)):
        # Swapping out live_dir would delete (or recurse into) the backup.
        raise ValueError(
            f"Refusing to restore {live_dir} from {backup_dir}: the backup "
            "and the folder it replaces overlap.")
    if only is not None:
        for name in only:
            b = os.path.join(backup_dir, name)
            if os.path.isdir(b):
                restore_tree(b, os.path.join(live_dir, name))
            elif os.path.isfile(b):
                atomic_copy(b, os.path.join(live_dir, name))
        return

    staging = live_dir + ".rmt-restore"
    aside = live_dir + ".rmt-old"
    # A killed swap is put back first; any other leftover is moved out of
    # the way under a unique name -- never deleted, never put in place.
    recover_interrupted_restore(live_dir)
    for leftover in (staging, aside):
        if os.path.exists(leftover):
            _set_aside(leftover)
    try:
        shutil.copytree(backup_dir, staging)
        # Files the backup has never seen were added to the game after it
        # was taken (game update): keep them, restore only replaces.
        if os.path.isdir(live_dir):
            for dirpath, _dirs, files in os.walk(live_dir):
                rel = os.path.relpath(dirpath, live_dir)
                for fn in files:
                    dst = os.path.join(staging, rel, fn)
                    if not os.path.exists(dst):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(os.path.join(dirpath, fn), dst)
                        log.info("Restore keeps %s (not in the backup)",
                                 os.path.join(rel, fn))
    except BaseException:
        # Our own half-built copy: the backup and the live folder are
        # untouched, so removing it loses nothing.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    had_live = os.path.isdir(live_dir)
    if had_live:
        os.rename(live_dir, aside)
        try:
            with open(os.path.join(aside, SWAP_MARKER), "w") as f:
                f.write("restore in progress\n")
        except OSError:
            # Without the marker a kill now could not be recovered
            # automatically: undo the first rename and stop.
            os.rename(aside, live_dir)
            shutil.rmtree(staging, ignore_errors=True)
            raise
    try:
        os.rename(staging, live_dir)
    except BaseException:
        if had_live:
            os.rename(aside, live_dir)
            try:
                os.remove(os.path.join(live_dir, SWAP_MARKER))
            except OSError as exc:
                # Harmless: only <name>.rmt-old folders are checked for it
                log.warning("Could not remove %s: %s", SWAP_MARKER, exc)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if had_live:
        # Done: the old (translated) folder goes.  Drop the marker first so
        # a partly failed delete can never be mistaken for a killed swap.
        try:
            os.remove(os.path.join(aside, SWAP_MARKER))
        except OSError as exc:
            log.warning("Could not remove %s: %s", SWAP_MARKER, exc)
        shutil.rmtree(aside, ignore_errors=True)
        if os.path.exists(aside):
            # Partly deleted (locked file / antivirus): keep it out of the
            # way under a unique name instead of leaving a .rmt-old behind.
            _set_aside(aside)


# ── Export manifest / game-update detection ──────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_content(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
    except OSError:
        return False
    return sha256_file(a) == sha256_file(b)


class ExportManifest:
    """Records the size + hash of every file an export wrote.

    Stored as JSON *beside* the backup (never inside it, so a restore never
    copies it into the game, and parsers never see it as game data).
    """

    VERSION = 1

    def __init__(self, path: str):
        self.path = os.fspath(path)
        self.files: dict[str, dict] = {}
        self.exists = False
        self.pending = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                self.files = raw["files"]
                # A manifest left "pending" by an export that crashed or
                # couldn't record what it wrote can't tell our output from
                # a game update: treat it like no manifest (never refresh).
                self.exists = not raw.get("pending", False)
        except (OSError, ValueError):
            # Missing or unreadable manifest: behave as for a v1.1.0 backup
            # -- never re-baseline, the safe direction.
            pass

    def is_ours(self, key: str, live_path: str) -> bool:
        rec = self.files.get(key)
        if not rec or not os.path.isfile(live_path):
            return False
        try:
            if os.path.getsize(live_path) != rec.get("size"):
                return False
        except OSError:
            return False
        return sha256_file(live_path) == rec.get("sha256")

    def record(self, key: str, live_path: str) -> None:
        try:
            self.files[key] = {"size": os.path.getsize(live_path),
                               "sha256": sha256_file(live_path)}
        except OSError:
            self.files.pop(key, None)

    def forget(self, key: str) -> None:
        self.files.pop(key, None)

    def _write(self) -> None:
        atomic_write_text(self.path, json.dumps(
            {"version": self.VERSION, "pending": self.pending,
             "files": self.files}, ensure_ascii=False, indent=1))

    def begin(self) -> None:
        """Mark an export as in progress, BEFORE any game file is written.

        Raises OSError if that can't be recorded -- the export must then
        not touch the game, or a later export could mistake our own output
        for a game update and copy English into the backup.
        """
        self.pending = True
        self._write()

    def save(self) -> None:
        """Write the manifest, keeping the current pending state."""
        try:
            self._write()
        except OSError as exc:
            # Harmless to lose: if this was the pending marker, it is still
            # on disk from begin(); without a manifest nothing is refreshed.
            log.warning("Could not write export manifest %s: %s",
                        self.path, exc)

    def finish(self) -> None:
        """Record a completed export (clears the pending marker)."""
        self.pending = False
        self.save()
        self.exists = not self.pending


def refresh_changed_originals(pairs, manifest: ExportManifest,
                              mark_pending: bool = True) -> list[str]:
    """Re-baseline backups for game files that changed after the backup.

    *pairs* is an iterable of ``(key, live_path, backup_path)``.  A live
    file is copied over its backup only when **all** of these hold:

    * a manifest exists (so we know what earlier exports wrote — without one
      a difference from the backup is most likely our own previous output),
    * the live file is not what we last wrote, and
    * it differs from the backup (or the backup has no copy of it).

    With *mark_pending* (exports), always marks the manifest pending
    (``begin``) before returning, so callers write game files only after
    that marker is safely on disk.

    Returns the keys that were refreshed.
    """
    refreshed = []
    if not manifest.exists:
        if mark_pending:
            manifest.begin()
        return refreshed
    for key, live, bak in pairs:
        if not os.path.isfile(live):
            continue
        if manifest.is_ours(key, live):
            continue
        if os.path.isfile(bak) and _same_content(live, bak):
            continue
        os.makedirs(os.path.dirname(bak) or ".", exist_ok=True)
        atomic_copy(live, bak)
        manifest.forget(key)
        refreshed.append(key)
        log.warning("Game file %s changed since the backup was taken — "
                    "backup refreshed from the current file", key)
    if mark_pending:
        manifest.begin()
    return refreshed


def refresh_on_resume(pairs, manifest_path: str) -> list[str]:
    """The export's game-update refresh, run when a project is resumed.

    Without it the resume merge read the backup as it was before the
    update, saw no change, and the "re-open the project" advice did
    nothing until an export had run.  Same rules as an export: a live file
    goes into the backup only when the manifest knows what we last wrote
    and the live file is neither that nor the backup.  The manifest is not
    marked pending (nothing is about to be written to the game).
    """
    manifest = ExportManifest(manifest_path)
    if not manifest.exists:
        return []
    refreshed = refresh_changed_originals(pairs, manifest, mark_pending=False)
    if refreshed:
        manifest.save()
    return refreshed


def refreshed_note(refreshed: list[str], merges_on_reopen: bool = False) -> str:
    """User-facing note for files whose backup was re-baselined.

    *merges_on_reopen*: the engine's parser has ``load_originals`` and the
    main window merges new / changed lines when the project is resumed.
    Other engines say honestly that the new lines are not added.
    """
    if not refreshed:
        return ""
    shown = ", ".join(refreshed[:8])
    more = f" and {len(refreshed) - 8} more" if len(refreshed) > 8 else ""
    head = (f"{len(refreshed)} game file(s) changed since your first export "
            f"(game update?): {shown}{more}. The backup was refreshed from "
            f"the current files so the update is kept. ")
    if merges_on_reopen:
        return head + ("Re-open the project (and resume it) to add the new "
                       "and changed lines; lines that did not change keep "
                       "their translations.")
    return head + ("New or changed lines in those files are NOT added to "
                   "your project (this engine can't merge a game update "
                   "yet): they stay untranslated in the game.")


def tree_pairs(live_dir: str, backup_dir: str, suffixes: tuple[str, ...]):
    """``(relpath, live, backup)`` for files with *suffixes* under either tree."""
    rels = set()
    for base in (live_dir, backup_dir):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if fn.lower().endswith(suffixes):
                    rel = os.path.relpath(os.path.join(root, fn), base)
                    rels.add(rel.replace("\\", "/"))
    return [(rel, os.path.join(live_dir, rel), os.path.join(backup_dir, rel))
            for rel in sorted(rels)]


# ── Encoding: keep undecodable source bytes, replace unencodable text ──

def _preserve_or_replace(exc):
    """Codec error handler for writing text read with ``surrogateescape``.

    Lone surrogates U+DC80..U+DCFF are the original undecodable bytes and
    are written back unchanged; any other unencodable character (e.g. an
    accented letter in a cp932 file) becomes ``?``.
    """
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    out = bytearray()
    for ch in exc.object[exc.start:exc.end]:
        cp = ord(ch)
        out += bytes([cp - 0xDC00]) if 0xDC80 <= cp <= 0xDCFF else b"?"
    return bytes(out), exc.end


PRESERVE_OR_REPLACE = "rmt_preserve_or_replace"
import codecs as _codecs  # noqa: E402

_codecs.register_error(PRESERVE_OR_REPLACE, _preserve_or_replace)
