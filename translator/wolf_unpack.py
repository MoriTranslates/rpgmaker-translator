"""Unpack a Wolf RPG ``Data.wolf`` archive with the bundled UberWolf CLI.

UberWolf (https://github.com/Sinflower/UberWolf, MIT License) ships a
command-line build, ``UberWolfCli.exe``.  Given ``X/Data.wolf`` it writes the
archive's contents to ``X/Data/`` (next to the archive; there is no output
option) and skips the archive when ``X/Data/`` already has files in it.  Its
exit code is 0 even when an archive could not be decrypted, so the result is
judged only by what it wrote.

So the game folder is never written to directly: the archive is linked (or
copied) into a fresh temporary folder inside the game folder, UberWolf runs
there, the output is checked, and only then is it renamed to ``Data/`` -- or,
when a ``Data/`` folder already exists, merged into it without replacing or
deleting anything that is already there.  ``Data.wolf`` itself is only read.

The release build bundles the CLI at ``third_party/UberWolf/UberWolfCli.exe``
inside the app (see packaging/fetch_uberwolf.ps1, which downloads the pinned
release and checks its SHA-256).  Running from source, the same path under
the repo root is used (run packaging/fetch_uberwolf.ps1 once), or
``UberWolfCli.exe`` on PATH, or the file named by ``RMT_UBERWOLF``.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import ollama_procs, resource_paths

log = logging.getLogger(__name__)

UBERWOLF_EXE = 'UberWolfCli.exe'
UBERWOLF_DIR = ('third_party', 'UberWolf')
UBERWOLF_URL = 'https://github.com/Sinflower/UberWolf'
ENV_OVERRIDE = 'RMT_UBERWOLF'      # source runs only
UNPACK_TIMEOUT_S = 600             # big archives (all images + audio) are slow
TEMP_PREFIX = '.rmt_unpack_'       # short: UberWolf uses MAX_PATH buffers

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class WolfUnpackError(RuntimeError):
    """Data.wolf could not be unpacked; ``str()`` is the message for users."""

    def __init__(self, reason: str):
        self.reason = reason.rstrip('. ')
        super().__init__(
            "This game's data is packed in Data.wolf and the built-in unpacker "
            f"(UberWolf) couldn't open it: {self.reason}. You can unpack it "
            f"yourself with UberWolf ({UBERWOLF_URL}), then open the game "
            "folder again.")


# ── Finding the unpacker ─────────────────────────────────────────────────

def find_uberwolf() -> Optional[Path]:
    """Path of UberWolfCli.exe, or None when this copy of the app has none.

    A release build only ever uses the copy bundled with it.  From source:
    ``RMT_UBERWOLF``, then ``<repo>/third_party/UberWolf/``, then PATH.
    """
    bundled = Path(resource_paths.resource_path(*UBERWOLF_DIR, UBERWOLF_EXE))
    if resource_paths.is_frozen():
        return bundled if bundled.is_file() else None
    env = os.environ.get(ENV_OVERRIDE)
    if env and Path(env).is_file():
        return Path(env)
    if bundled.is_file():
        return bundled
    found = shutil.which(UBERWOLF_EXE)
    return Path(found) if found else None


# ── Version check from the archive header ────────────────────────────────

# Crypt versions of plain (not Pro-protected) WOLF RPG Editor 3.x archives,
# which UberWolf opens with fixed keys (UberWolfLib WolfDec.cpp,
# DEFAULT_CRYPT_MODES): 3.00, 3.14, 3.31.
PLAIN_WOLF3_CRYPT_VERSIONS = frozenset({0x12C, 0x13A, 0x14B})
WOLF35_CRYPT_VERSION = 0x15E        # 3.50: its data files are compressed

ARCHIVE_WOLF35 = ("is a WOLF RPG Editor 3.5 archive (its data is "
                  "compressed), which this version can't read or write yet")
ARCHIVE_PROTECTED = ("is protected (WOLF RPG Editor Pro) or uses an archive "
                     "format this version doesn't open")


def archive_crypt_version(wolf_file) -> Optional[int]:
    """The DXArchive crypt version of Data.wolf, or None if not judged.

    2.x archives either have an encrypted header (no "DX" magic: None) or
    a plain DXArchive v8 header whose crypt version (high 16 bits of the
    Flags field at offset 44) is 0.  3.x and Pro archives store their
    non-zero crypt version there -- the value UberWolf picks its keys by.
    Only 48 bytes are read.
    """
    try:
        with open(wolf_file, 'rb') as f:
            head = f.read(48)
    except OSError as exc:
        # Not judged here: the unpack that follows reports the read error.
        log.warning('Could not read %s header: %s', wolf_file, exc)
        return None
    if len(head) < 48 or head[:2] != b'DX':
        return None
    return struct.unpack_from('<I', head, 44)[0] >> 16


def archive_refusal(wolf_file) -> Optional[str]:
    """Why Data.wolf must not be unpacked (plain words), or None.

    2.x and plain 3.x archives are unpacked; 3.5 archives (compressed
    data) and Pro-protected or unknown ones are refused before anything
    is written.  UberWolf's unprotect mode is never used here.
    """
    version = archive_crypt_version(wolf_file)
    if not version or version in PLAIN_WOLF3_CRYPT_VERSIONS:
        return None
    if version == WOLF35_CRYPT_VERSION:
        return ARCHIVE_WOLF35
    return ARCHIVE_PROTECTED


# ── Unpacking ────────────────────────────────────────────────────────────

def has_game_data(data_dir: Path) -> bool:
    """Data/ holds what the parser reads (maps or common events)."""
    if (data_dir / 'MapData').is_dir() and any(
            (data_dir / 'MapData').glob('*.mps')):
        return True
    return (data_dir / 'BasicData' / 'CommonEvent.dat').is_file()


def _link_or_copy(src: Path, dst: Path):
    """Hard-link src to dst (instant, no extra disk); copy if not possible.

    UberWolf only reads the archive, so a link never changes Data.wolf, and
    removing the link later leaves Data.wolf in place.
    """
    try:
        os.link(src, dst)
    except OSError as exc:
        # FAT/exFAT, network shares, other volume: a copy works everywhere.
        log.info('Hard link for %s failed (%s); copying instead', src, exc)
        shutil.copy2(src, dst)


def run_uberwolf(exe: Path, archive: Path, timeout: float) -> str:
    """Run UberWolfCli on one archive; return its output.

    Raises WolfUnpackError on a timeout or when it can't be started.  A
    zero exit code does NOT mean success (see the module docstring).
    """
    # UberWolf re-launches itself (once per decryption attempt) using its
    # argv[0], and that round trip garbles non-ASCII characters: from a
    # folder like C:\Users\José\... every attempt failed.  So the real path
    # goes in `executable` and argv[0] is the bare file name, which Windows
    # resolves to the folder the running exe was loaded from.
    args = [exe.name, '--', str(archive)]
    try:
        proc = subprocess.Popen(
            args, executable=str(exe), cwd=str(archive.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW)
    except OSError as exc:
        raise WolfUnpackError(f'it could not be started ({exc})') from exc
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # UberWolf starts a copy of itself per decryption attempt: end the
        # whole tree so nothing keeps writing into the temp folder.
        if not ollama_procs.kill_tree(proc.pid):
            proc.kill()
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            log.warning('UberWolf (PID %s) did not exit after kill', proc.pid)
        raise WolfUnpackError(
            f'it did not finish within {int(timeout // 60)} minutes') from None
    text = (out or b'').decode('utf-8', errors='replace').strip()
    log.info('UberWolf exit %s: %s', proc.returncode, text[-500:])
    return text


def _merge_without_overwrite(src: Path, dst: Path, skipped: list[str],
                             moved: list[tuple[Path, Path]], rel: str = ''):
    """Move src's items into dst; anything already in dst is kept as is.

    Every move is recorded in ``moved`` (source, target) so a failure part
    way can be undone by _undo_moves.
    """
    for item in sorted(src.iterdir()):
        target = dst / item.name
        name = f'{rel}{item.name}'
        if not target.exists() and not target.is_symlink():
            os.rename(item, target)
            moved.append((item, target))
        elif item.is_dir() and target.is_dir() and not target.is_symlink():
            _merge_without_overwrite(item, target, skipped, moved, name + '/')
        else:
            skipped.append(name)


def _undo_moves(moved: list[tuple[Path, Path]]) -> list[str]:
    """Move merged items back out of Data/; return those that stayed."""
    stuck = []
    for src, target in reversed(moved):
        try:
            os.rename(target, src)
        except OSError as exc:
            log.error('Could not take %s back out of Data/: %s', target, exc)
            stuck.append(str(target))
    return stuck


def _move_into_place(unpacked: Path, data_dir: Path):
    """Rename the unpacked folder to Data/, or merge into an existing one."""
    if not data_dir.exists():
        os.rename(unpacked, data_dir)       # atomic: all or nothing
        return
    skipped: list[str] = []
    moved: list[tuple[Path, Path]] = []
    try:
        _merge_without_overwrite(unpacked, data_dir, skipped, moved)
    except OSError as exc:
        stuck = _undo_moves(moved)
        if stuck:
            raise WolfUnpackError(
                f'the unpacked files could not be moved into Data/ ({exc}), '
                f'and {len(stuck)} item(s) already moved there could not be '
                'taken back out: ' + ', '.join(stuck[:5])) from exc
        raise WolfUnpackError(
            f'the unpacked files could not be moved into Data/ ({exc})'
        ) from exc
    if skipped:
        log.info('Kept %d item(s) already in Data/ instead of the unpacked '
                 'copy: %s', len(skipped), ', '.join(skipped[:10]))


def _remove_temp(temp: Path):
    try:
        shutil.rmtree(temp)
    except OSError as exc:
        # Safe to leave: the game only reads Data.wolf / Data/, and the
        # folder holds just a link/copy of Data.wolf plus unpacked files.
        log.warning('Could not remove the temporary unpack folder %s: %s',
                    temp, exc)


def unpack_data_wolf(game_dir, timeout: float = UNPACK_TIMEOUT_S) -> Path:
    """Unpack ``game_dir/Data.wolf`` into ``game_dir/Data``; return that path.

    Never deletes or overwrites anything in the game folder: an existing
    ``Data/`` only gains files it did not have.  On any failure the game
    folder is left as it was and WolfUnpackError explains why.
    """
    game_dir = Path(game_dir)
    wolf_file = game_dir / 'Data.wolf'
    data_dir = game_dir / 'Data'
    if not wolf_file.is_file():
        raise FileNotFoundError(f'No Data.wolf found in {game_dir}')
    if data_dir.exists() and not data_dir.is_dir():
        raise WolfUnpackError(
            'a file named "Data" is in the way of the unpacked folder')

    exe = find_uberwolf()
    if exe is None:
        raise WolfUnpackError(
            f'the unpacker is missing from this copy of the app '
            f'({UBERWOLF_EXE} was not found)')

    try:
        temp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=game_dir))
    except OSError as exc:
        raise WolfUnpackError(
            f'a temporary folder could not be created in the game folder '
            f'({exc})') from exc
    try:
        archive = temp / 'Data.wolf'
        try:
            _link_or_copy(wolf_file, archive)
        except OSError as exc:
            raise WolfUnpackError(
                f'Data.wolf could not be read or copied ({exc})') from exc
        output = run_uberwolf(exe, archive, timeout)
        unpacked = temp / 'Data'
        if not (unpacked.is_dir() and has_game_data(unpacked)):
            last = output.splitlines()[-1] if output else 'no output'
            raise WolfUnpackError(
                'it could not decrypt this archive (its format or encryption '
                f'is not one it knows; UberWolf said: "{last}")')
        try:
            _move_into_place(unpacked, data_dir)
        except OSError as exc:
            raise WolfUnpackError(
                f'the unpacked files could not be moved into Data/ ({exc})'
            ) from exc
    finally:
        _remove_temp(temp)
    return data_dir
