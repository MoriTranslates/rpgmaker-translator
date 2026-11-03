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
* ``ExportManifest`` + ``changed_outside`` — remember what each export
  wrote.  A live file that later matches neither the backup nor what we
  wrote was changed outside the translator (game update, RPG Maker re-save,
  hand edit): it is not written and is reported.  The backup is never
  replaced from the live game.
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
    * ``outside``     -- files changed outside the translator (not written)
    * ``unverified``  -- files that could not be verified (not written)
    * ``unrecorded``  -- what was written could not be recorded
    A plain ``PartialExportError(problems)`` counts every problem as an error.
    """

    def __init__(self, problems: list[str], summary: str = "", *,
                 errors=None, changed=None, unsafe=None, unsupported=None,
                 outside=None, unverified=None, unrecorded=None):
        self.problems = list(problems)
        self.errors = list(self.problems if errors is None else errors)
        self.changed = dict(changed or {})
        self.unsafe = list(unsafe or [])
        self.unsupported = list(unsupported or [])
        self.outside = list(outside or [])
        self.unverified = list(unverified or [])
        self.unrecorded = list(unrecorded or [])
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


def outside_problem(fn: str) -> str:
    return (f"{fn}: changed outside the translator (game update, RPG Maker "
            f"re-save or hand edit) — not written, backup not touched")


def unverified_problem(fn: str) -> str:
    return (f"{fn}: can't be verified (an earlier export was interrupted or "
            f"couldn't record what it wrote) — not written, backup not "
            f"touched")


def raise_export_problems(errors: list[str], skipped: dict[str, int],
                          unsafe: list[str] = (), unsupported: list[str] = (),
                          outside: list[str] = (), manifest=None):
    """Raise PartialExportError for unwritten files and/or skipped lines.

    *skipped* maps a file to the number of translations that were NOT
    written because the game text at their place no longer matched their
    original (game update or hand edit): the game's text was kept.
    *unsafe* / *unsupported* / *outside*: see ``PartialExportError``.
    With the export's *manifest*, files it could not verify are told apart
    from *outside*, and a failed record or save of the manifest is reported.
    """
    skipped = {fn: n for fn, n in skipped.items() if n}
    unverified = sorted(k for k in outside
                        if manifest is not None and k in manifest.unverified)
    outside = [k for k in outside if k not in unverified]
    unrecorded = list(getattr(manifest, "record_failed", None) or [])
    problems = (list(errors)
                + [f"Couldn't record what was written, so the next export "
                   f"may flag these files: {r}" for r in unrecorded]
                + [unverified_problem(fn) for fn in unverified]
                + [outside_problem(fn) for fn in outside]
                + list(unsupported) + list(unsafe)
                + [changed_line_problem(fn, n)
                   for fn, n in sorted(skipped.items())])
    if not problems:
        return
    parts = []
    if errors:
        parts.append(f"{len(errors)} file(s) could not be written "
                     f"(is the game running?)")
    if unrecorded:
        parts.append("what was written could not be recorded")
    if unverified:
        parts.append(f"{len(unverified)} file(s) could not be verified and "
                     f"were not written")
    if outside:
        parts.append(f"{len(outside)} file(s) were changed outside the "
                     f"translator and were not written")
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
        unsupported=unsupported, outside=outside, unverified=unverified,
        unrecorded=unrecorded)


# What the user can do about lines whose game text changed.  The app does
# not merge a game update into a project (by design, for now).
CHANGED_LINES_ADVICE = (
    "Lines whose text in the game changed (game update or hand edit) were "
    "skipped and are listed above; the game keeps its own text there. "
    "Exporting again won't change them, and this project does not pick up "
    "new or changed lines. To translate the new version of the game, start "
    "a new project on a clean copy of it (Import Translations can bring "
    "over your existing work).")


OUTSIDE_ADVICE = (
    "Files changed outside the translator were not written and their backup "
    "was not touched. If the game was updated, start a new project on a "
    "clean copy of the updated game; if you edited it in RPG Maker, Restore "
    "Originals first or re-export from a clean copy.")


def export_advice(partial) -> str:
    """What the user can do, by cause of the problems in *partial*."""
    advice = []
    errors = getattr(partial, "errors", None)
    if errors is None:
        errors = getattr(partial, "problems", None) or []
    if errors:
        advice.append("For files that could not be written: close the game, "
                      "free disk space if needed, and export again.")
    if getattr(partial, "unrecorded", None):
        advice.append("What this export wrote could not be recorded (disk "
                      "full? a file in use?). Free some space and export "
                      "again. If the next export lists files as changed "
                      "outside the translator: if the game was updated, "
                      "reinstall or re-copy the game (or re-download the "
                      "update) and start a new project -- do not use Restore "
                      "Originals, it would mix old and new files. Only if "
                      "the game was NOT updated, run Restore Originals and "
                      "export again.")
    if getattr(partial, "unverified", None):
        advice.append("Files that can't be verified were not written: an "
                      "earlier export was interrupted, so the app can't tell "
                      "its own output from other changes. If the game was "
                      "updated, reinstall or re-copy the game (or "
                      "re-download the update) and start a new project -- "
                      "do not use Restore Originals, it would mix old and "
                      "new files. Only if the game was NOT updated, run "
                      "Restore Originals, then export again.")
    if getattr(partial, "outside", None):
        advice.append(OUTSIDE_ADVICE)
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
        _before_replace(tmp, path)
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
        _before_replace(tmp, path)
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
        _before_replace(tmp, dst)
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
                    if fn.endswith(".rmt-tmp"):
                        continue        # our own crashed atomic write
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
        # The restore itself is complete here: a failure from now on is
        # only an untidy leftover, reported as a note -- never raised as
        # "restore failed" (review M1).
        try:
            os.remove(os.path.join(aside, SWAP_MARKER))
        except OSError as exc:
            # Harmless: the live folder exists again, and a marker is only
            # acted on when the live folder is missing.
            log.warning("Could not remove %s: %s", SWAP_MARKER, exc)
        shutil.rmtree(aside, ignore_errors=True)
        if os.path.exists(aside):
            # Partly deleted (locked file / antivirus): keep it out of the
            # way under a unique name instead of leaving a .rmt-old behind.
            try:
                where = _set_aside(aside)
            except OSError as exc:
                log.warning("Could not move leftover %s aside: %s", aside, exc)
                where = aside
            _cleanup_notes.append((live_dir,
                f"The original files were restored, but the old translated "
                f"copy could not be fully deleted (a file is in use?). It "
                f"was left in {where}; delete that folder when the game is "
                f"closed."))


# (restored folder, note) -- leftovers of restores that otherwise succeeded
_cleanup_notes: list[tuple[str, str]] = []


def take_cleanup_notes(project_dir: str | None = None) -> list[str]:
    """Notes about leftovers from restores that otherwise succeeded.

    With *project_dir*, only notes for folders inside that game; all notes
    are cleared either way, so one never shows up for another restore.
    """
    notes = [note for path, note in _cleanup_notes
             if project_dir is None or _inside(path, project_dir)]
    _cleanup_notes.clear()
    return notes


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


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


# Manifests of exports in progress, by manifest path.  Every atomic write
# under a manifest's folder is journaled to it BEFORE the file is renamed
# into place (see ``_before_replace``), so a crash can never leave our own
# output unrecognisable.
_write_ahead: dict[str, "ExportManifest"] = {}


def _before_replace(tmp: str, dst: str) -> None:
    """Write-ahead hook of the atomic writers: journal *tmp*'s hash as the
    content about to land at *dst*.  Raises if it can't -- then the file is
    not written (no record, no write)."""
    for m in list(_write_ahead.values()):
        m._intend(dst, tmp)


class ExportManifest:
    """Records the size + hash of every file an export wrote.

    Stored as JSON *beside* the backup (never inside it, so a restore never
    copies it into the game, and parsers never see it as game data).

    Write-ahead: while an export runs (``begin`` .. ``finish``) every game
    file written under the manifest's folder is first journaled with the
    hash it is about to get (``<manifest>.journal``), and recorded after
    the write.  So after a crash, a failed record or a failed final save,
    the next export still recognises the app's own output.
    """

    VERSION = 1

    def __init__(self, path: str):
        self.path = os.fspath(path)
        self.journal = self.path + ".journal"
        self.root = _norm(os.path.dirname(self.path) or ".")
        # other folders whose writes are journaled too (Tyrano's system
        # files live beside, not under, the scenario folder)
        self.extra_roots: list[str] = []
        self.files: dict[str, dict] = {}
        # content this app was about to write, by normalised live path
        self.intended: dict[str, dict] = {}
        self.exists = False
        self.pending = False
        # The manifest on disk was left pending: an export crashed, or
        # could not save what it wrote.
        self.was_pending = False
        # Was there ever a manifest (or journal) for this backup?  Only a
        # project that never had one (exported with 1.1.0) gets the 1.1.0
        # behaviour; anything else that can't be verified is not written.
        self.present = os.path.exists(self.path) or os.path.exists(self.journal)
        # A record or the final save failed in THIS export (reported).
        self.record_failed: list[str] = []
        # Keys that could not be verified this export (see check_live).
        self.unverified: set[str] = set()
        # True when this manifest was created together with the backup, so
        # every write this app ever made to the game is recorded in it: a
        # file with no record was never written by us.  (Manifests made
        # after a backup from an older version can't say that.)
        self.tracks_all = False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
                self.files = raw["files"]
                self.tracks_all = raw.get("tracks_all") is True
                self.was_pending = bool(raw.get("pending", False))
                # saving again (e.g. note_own_files) keeps a crash marker
                self.pending = self.was_pending
                self.exists = not self.was_pending
        except (OSError, ValueError):
            # Missing or unreadable: no records.  If the file (or a
            # journal) is there at all, check_live treats unmatched files
            # as "can't verify" -- never as unchanged.
            pass
        self._read_journal()

    def _read_journal(self) -> None:
        try:
            with open(self.journal, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        self.intended[rec["path"]] = {
                            "size": rec["size"], "sha256": rec["sha256"]}
                    except (ValueError, KeyError, TypeError):
                        # A torn last line from a crash: that write never
                        # reached its rename (the line is written first).
                        continue
        except OSError:
            # No journal: nothing was in flight.
            pass

    def _intend(self, dst: str, tmp: str) -> None:
        d = _norm(dst)
        roots = [self.root] + [_norm(r) for r in self.extra_roots]
        if (d.endswith(".manifest.json") or d.endswith(".journal")
                or not any(d == r or d.startswith(r + os.sep)
                           for r in roots)):
            return
        rec = {"size": os.path.getsize(tmp), "sha256": sha256_file(tmp)}
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": d, **rec}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.intended[d] = rec

    @staticmethod
    def _matches(rec, live_path: str) -> bool:
        if not rec or not os.path.isfile(live_path):
            return False
        try:
            if os.path.getsize(live_path) != rec.get("size"):
                return False
        except OSError:
            return False
        return sha256_file(live_path) == rec.get("sha256")

    def is_ours(self, key: str, live_path: str) -> bool:
        """Is *live_path* what this app last wrote (record or journal)?"""
        return (self._matches(self.files.get(key), live_path)
                or self._matches(self.intended.get(_norm(live_path)),
                                 live_path))

    def record(self, key: str, live_path: str) -> None:
        try:
            self.files[key] = {"size": os.path.getsize(live_path),
                               "sha256": sha256_file(live_path)}
        except OSError as exc:
            # The journal still holds what was written, so the next export
            # recognises it; this export says the record failed.
            self.files.pop(key, None)
            self.record_failed.append(f"{key} ({exc})")
            log.warning("Could not record %s in %s: %s", key, self.path, exc)

    def forget(self, key: str) -> None:
        self.files.pop(key, None)

    def _write(self) -> None:
        atomic_write_text(self.path, json.dumps(
            {"version": self.VERSION, "pending": self.pending,
             "tracks_all": self.tracks_all,
             "files": self.files}, ensure_ascii=False, indent=1))
        self.present = True

    def begin(self) -> None:
        """Mark an export as in progress, BEFORE any game file is written,
        and journal every write from now on.

        Raises OSError if that can't be recorded -- the export must then
        not touch the game.
        """
        self.pending = True
        self._write()
        _write_ahead[self.path] = self

    def save(self) -> None:
        """Write the manifest, keeping the current pending state."""
        try:
            self._write()
        except OSError as exc:
            # Reported by finish()/raise_export_problems: the journal keeps
            # what was written, so the next export still recognises it.
            self.record_failed.append(f"export record ({exc})")
            log.warning("Could not write export manifest %s: %s",
                        self.path, exc)

    def finish(self) -> None:
        """Record a completed export (clears the pending marker).

        The journal is dropped only when everything was recorded and
        saved; otherwise it stays, and the failure is reported.
        """
        if _write_ahead.get(self.path) is self:
            del _write_ahead[self.path]
        self.pending = False
        self.save()
        self.exists = not self.pending
        if not self.record_failed:
            try:
                os.remove(self.journal)
            except FileNotFoundError:
                pass                    # nothing was journaled
            except OSError as exc:
                # Harmless: a journal only ever adds "this is ours" hashes
                # of files this app wrote.
                log.warning("Could not remove %s: %s", self.journal, exc)

    def start_with_backup(self) -> None:
        """A new backup was just made: this manifest sees every write."""
        self.tracks_all = True
        self.save()


# ── Files changed outside the translator: fail closed ────────────────
#
# The backup is never replaced from the live game.  Each export compares a
# live file with what this app wrote (manifest records and the write-ahead
# journal) and with the backup:
#   * matches one of them: ours or original -- exported as usual;
#   * matches none, manifest complete: changed outside the translator
#     (game update, RPG Maker re-save, hand edit) -- not written;
#   * matches none, but the manifest was left pending (an export crashed
#     or couldn't save its record) or has no record of the file: can't be
#     verified -- not written either ("couldn't check" is never "fine");
#   * no manifest or journal ever existed (a project exported with
#     1.1.0): the 1.1.0 behaviour.

def check_live(key: str, live: str, bak: str,
               manifest: ExportManifest) -> str:
    """"ok", "outside" or "unverified" for one game file.  Hashes are
    streamed, so file size doesn't matter."""
    if not os.path.isfile(live) or not os.path.isfile(bak):
        return "ok"
    if _same_content(live, bak) or manifest.is_ours(key, live):
        return "ok"
    if not manifest.present:
        return "ok"                         # 1.1.0 project: 1.1.0 behaviour
    if manifest.was_pending or not manifest.exists or (
            key not in manifest.files and not manifest.tracks_all):
        return "unverified"
    return "outside"


def is_changed_outside(key: str, live: str, bak: str,
                       manifest: ExportManifest) -> bool:
    """Must *live* be left alone (changed outside, or can't be verified)?"""
    state = check_live(key, live, bak, manifest)
    if state == "unverified":
        manifest.unverified.add(key)
    return state != "ok"


def changed_outside(pairs, manifest: ExportManifest) -> list[str]:
    """Keys of *pairs* ``(key, live, backup)`` that must not be written.

    Those that could not be verified are also in ``manifest.unverified``.
    Always marks the manifest pending (``begin``) before returning, so
    callers write game files only after that marker is safely on disk.
    The backup is never written.
    """
    out = [key for key, live, bak in pairs
           if is_changed_outside(key, live, bak, manifest)]
    for key in out:
        log.warning("Game file %s was changed outside the translator or "
                    "can't be verified: not written", key)
    manifest.begin()
    return out


def never_written(key: str, manifest: ExportManifest) -> bool:
    """Has this app provably never written *key* into the game?

    Only a manifest created with the backup knows every write.  Used to
    take a first backup of a file the backup has no copy of; without that
    proof the file is left alone (it may hold an older export's English).
    """
    return (manifest.exists and manifest.tracks_all
            and key not in manifest.files)


def read_only_source(key: str, live: str, bak: str,
                     manifest: ExportManifest) -> str | None:
    """Which copy of a game file to translate, without writing anything.

    For packages built from the game (the MV/MZ install zip): the backup's
    original; the live file when the backup has no copy; None when the live
    file was changed outside the translator or can't be verified (left out
    and reported).
    """
    if is_changed_outside(key, live, bak, manifest):
        return None
    return bak if os.path.isfile(bak) else live


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
