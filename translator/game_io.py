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
  we wrote, the game was updated after the backup was taken; at export the
  backup is re-baselined from it (the old copy kept aside) instead of
  silently rolling the update back.  Never on resume, never for a file the
  backup lacks, never for a live file that looks translated.
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
    """Some files were exported, others failed.  ``problems`` lists them.

    The cause of each problem is kept apart so the UI can give the right
    advice without matching message text:

    * ``errors``      -- files that could not be written (locked, disk full)
    * ``changed``     -- {file: n} translations skipped because the game's
                         text at their place is no longer their original
    * ``unsafe``      -- translations not written because they would break
                         the line they go into (fix the translation)
    * ``unsupported`` -- parts of the game this version can't write
    A plain ``PartialExportError(problems)`` counts every problem as an error.
    """

    def __init__(self, problems: list[str], summary: str = "", *,
                 errors=None, changed=None, unsafe=None, unsupported=None):
        self.problems = list(problems)
        self.errors = list(self.problems if errors is None else errors)
        self.changed = dict(changed or {})
        self.unsafe = list(unsafe or [])
        self.unsupported = list(unsupported or [])
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


def changed_line_problem(fn: str, n: int) -> str:
    return (f"{fn}: {n} translated line(s) not written — the game's text there "
            f"changed (game update or edit), so the game's text was kept")


def raise_export_problems(errors: list[str], skipped: dict[str, int],
                          unsafe: list[str] = (), unsupported: list[str] = ()):
    """Raise PartialExportError for unwritten files and/or skipped lines.

    *skipped* maps a file to the number of translations that were NOT
    written because the game text at their place no longer matched their
    original (game update or hand edit): the game's text was kept.
    *unsafe* / *unsupported*: see ``PartialExportError``.
    """
    skipped = {fn: n for fn, n in skipped.items() if n}
    problems = (list(errors) + list(unsupported) + list(unsafe)
                + [changed_line_problem(fn, n)
                   for fn, n in sorted(skipped.items())])
    if not problems:
        return
    parts = []
    if errors:
        parts.append(f"{len(errors)} file(s) could not be written "
                     f"(is the game running?)")
    if unsupported:
        parts.append(f"{len(unsupported)} part(s) of the game can't be "
                     f"written by this version")
    if unsafe:
        parts.append("some translations would break the game's script")
    if skipped:
        parts.append(f"{sum(skipped.values())} translated line(s) were "
                     f"skipped because the game's text changed")
    raise PartialExportError(
        problems, "Export incomplete — " + "; ".join(parts) + ":",
        errors=errors, changed=skipped, unsafe=unsafe,
        unsupported=unsupported)


# What the user can do about lines whose game text changed.  The app does
# not merge a game update into a project (by design, for now).
CHANGED_LINES_ADVICE = (
    "Lines whose text in the game changed (game update or hand edit) were "
    "skipped and are listed above; the game keeps its own text there. "
    "Exporting again won't change them, and this project does not pick up "
    "new or changed lines. To translate the new version of the game, start "
    "a new project on a clean copy of it (Import Translations can bring "
    "over your existing work).")


def export_advice(partial) -> str:
    """What the user can do, by cause of the problems in *partial*."""
    advice = []
    errors = getattr(partial, "errors", None)
    if errors is None:
        errors = getattr(partial, "problems", None) or []
    if errors:
        advice.append("For files that could not be written: close the game, "
                      "free disk space if needed, and export again.")
    if getattr(partial, "unsupported", None):
        advice.append("Parts this version can't write keep their original "
                      "text; exporting again won't change that.")
    if getattr(partial, "unsafe", None):
        advice.append("Translations that would break a script line (an "
                      "unbalanced quote or backslash) were not written: "
                      "edit them and export again.")
    if getattr(partial, "changed", None):
        advice.append(CHANGED_LINES_ADVICE)
    return "\n\n".join(advice)


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


# (folder that was put back, user-facing note)
_recovery_notes: list[tuple[str, str]] = []


def _inside(path: str, root: str) -> bool:
    try:
        path = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
    except ValueError:              # different drives
        return False


def take_recovery_notes(project_dir: str | None = None) -> list[str]:
    """User-facing notes for recoveries since the last call (then cleared).

    With *project_dir*, only notes for folders inside that game are
    returned; notes about other games are dropped (they were logged when
    the recovery happened) so they never show up at an unrelated open.
    """
    notes = [note for path, note in _recovery_notes
             if project_dir is None or _inside(path, project_dir)]
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
    _recovery_notes.append((live_dir, RECOVERED_NOTE.format(
        name=os.path.basename(live_dir) + "/")))
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


class RefreshResult(list):
    """Keys whose backup was re-baselined, plus what was refused / kept.

    ``refused``  -- keys whose live file looked like a translation (far less
                    Japanese than the backup): the backup was left alone.
    ``kept_dir`` -- folder holding the copies saved before anything was
                    replaced (old backups, and refused live files).
    True when anything was refreshed or refused, so callers that only
    check ``if refreshed:`` still show the note.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.refused: list[str] = []
        self.kept_dir = ""

    def __bool__(self):
        return bool(len(self) or self.refused)


# A live file is not taken as a game update when it holds less than this
# share of the backup's Japanese (only judged when the backup has at least
# _JP_MIN_FOR_CHECK Japanese characters).
_JP_KEEP_RATIO = 0.5
_JP_MIN_FOR_CHECK = 200


def _japanese_counts(path: str) -> tuple[int, int]:
    """Japanese characters in *path* read as UTF-8 and as cp932."""
    from . import JAPANESE_RE
    with open(path, "rb") as f:
        raw = f.read()
    return (len(JAPANESE_RE.findall(raw.decode("utf-8", "ignore"))),
            len(JAPANESE_RE.findall(raw.decode("cp932", "ignore"))))


def _looks_translated(live: str, bak: str) -> bool:
    """Does *live* hold far less Japanese than *bak* (a translation, a fan
    patch or a hand-edited export, rather than a game update)?"""
    b_utf8, b_sjis = _japanese_counts(bak)
    l_utf8, l_sjis = _japanese_counts(live)
    # compare in the encoding that finds the most Japanese in the backup
    b, live_n = (b_utf8, l_utf8) if b_utf8 >= b_sjis else (b_sjis, l_sjis)
    return b >= _JP_MIN_FOR_CHECK and live_n < b * _JP_KEEP_RATIO


def kept_copies_dir(manifest: ExportManifest) -> str:
    """Where copies are saved before a backup file is replaced.

    Beside the backup, never inside it (a restore would copy it into the
    game) -- ``data_original.manifest.json`` -> ``data_original.replaced``.
    """
    base = manifest.path
    if base.endswith(".manifest.json"):
        base = base[:-len(".manifest.json")]
    return base + ".replaced"


def _keep_copy(src: str, key: str, kept_dir: str, tag: str) -> str:
    """Copy *src* into *kept_dir* under a new name; raises on failure."""
    name = key.replace("/", "__").replace("\\", "__")
    n = 1
    while os.path.exists(os.path.join(kept_dir, f"{name}.{tag}-{n}")):
        n += 1
    dst = os.path.join(kept_dir, f"{name}.{tag}-{n}")
    os.makedirs(kept_dir, exist_ok=True)
    atomic_copy(src, dst)
    return dst


def refresh_changed_originals(pairs, manifest: ExportManifest) -> RefreshResult:
    """Re-baseline backups for game files that changed after the backup.

    *pairs* is an iterable of ``(key, live_path, backup_path)``.  A live
    file is copied over its backup only when **all** of these hold:

    * a manifest exists (so we know what earlier exports wrote — without one
      a difference from the backup is most likely our own previous output),
    * the backup HAS a copy of the file (a file the backup lacks can't be
      compared with any original: it may be a 1.1.0 export's English
      output, and must never be taken as the "original"),
    * the live file is not what we last wrote and differs from the backup,
    * the live file does not hold far less Japanese than the backup (then
      it is a translation -- a fan patch, a hand-edited export -- not a
      game update; it is refused and saved aside instead).

    Before a backup file is replaced, the old copy is saved in
    ``kept_copies_dir``; if that fails, OSError is raised and nothing is
    replaced (the export must then stop before writing the game).

    Always marks the manifest pending (``begin``) before returning, so
    callers write game files only after that marker is safely on disk.
    """
    result = RefreshResult()
    if not manifest.exists:
        manifest.begin()
        return result
    kept_dir = kept_copies_dir(manifest)
    for key, live, bak in pairs:
        if not os.path.isfile(live) or not os.path.isfile(bak):
            continue
        if manifest.is_ours(key, live):
            continue
        if _same_content(live, bak):
            continue
        if _looks_translated(live, bak):
            # The export will overwrite this live file from the backup:
            # keep what was there.  Failing to keep it stops the export.
            _keep_copy(live, key, kept_dir, "game")
            result.refused.append(key)
            result.kept_dir = kept_dir
            log.warning("Game file %s differs from the backup but holds far "
                        "less Japanese: not taken as a game update (copy "
                        "kept in %s)", key, kept_dir)
            continue
        _keep_copy(bak, key, kept_dir, "backup")
        result.kept_dir = kept_dir
        atomic_copy(live, bak)
        manifest.forget(key)
        result.append(key)
        log.warning("Game file %s changed since the backup was taken — "
                    "backup refreshed from the current file (old copy in "
                    "%s)", key, kept_dir)
    manifest.begin()
    return result


def read_only_source(key: str, live: str, bak: str,
                     manifest: ExportManifest) -> str:
    """Which copy of a game file to translate, without writing anything.

    For packages built from the game (the MV/MZ install zip): the backup's
    original, unless the live file is a game update the backup doesn't have
    yet (same rule as ``refresh_changed_originals``, but nothing is copied),
    or the backup has no copy at all.
    """
    if not os.path.isfile(bak):
        return live
    if (manifest.exists and os.path.isfile(live)
            and not manifest.is_ours(key, live)
            and not _same_content(live, bak)
            and not _looks_translated(live, bak)):
        return live
    return bak


def refreshed_note(refreshed: list[str]) -> str:
    """User-facing note for files whose backup was re-baselined (or not)."""
    parts = []
    kept = getattr(refreshed, "kept_dir", "")
    if len(refreshed):
        shown = ", ".join(refreshed[:8])
        more = (f" and {len(refreshed) - 8} more"
                if len(refreshed) > 8 else "")
        parts.append(
            f"{len(refreshed)} game file(s) changed since your first export "
            f"(game update?): {shown}{more}. The backup was refreshed from "
            f"the current files so the update is kept. This project does not "
            f"pick up new or changed lines: translated lines whose text "
            f"changed were skipped and are listed, and new lines stay "
            f"untranslated. To translate the new version of the game, start "
            f"a new project on a clean copy of it.")
    refused = getattr(refreshed, "refused", [])
    if refused:
        shown = ", ".join(refused[:8])
        more = f" and {len(refused) - 8} more" if len(refused) > 8 else ""
        parts.append(
            f"{len(refused)} game file(s) differ from the backup but look "
            f"translated, not updated: {shown}{more}. The backup was kept "
            f"as it was; a copy of each of those game files was saved "
            f"before exporting.")
    if parts and kept:
        parts.append(f"Copies of everything replaced are in {kept}.")
    return " ".join(parts)


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
