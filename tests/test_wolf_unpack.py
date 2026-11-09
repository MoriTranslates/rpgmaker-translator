"""Data.wolf unpacking with the bundled UberWolf CLI (translator/wolf_unpack.py).

The release exe used to import an unpacker from the dev-only _reference/
folder, so every packed Wolf game failed with an ImportError.  These tests
pin the replacement: subprocess mocked, game folder never damaged.
"""

import os
import pathlib
import re
import struct
import subprocess
import sys

import pytest

from translator import wolf_unpack, wolfrpg

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCHIVE = b"encrypted archive bytes" * 10


# ── helpers ──────────────────────────────────────────────────────────────

def _game(tmp_path, name="Game"):
    game = tmp_path / name
    game.mkdir()
    (game / "Game.exe").write_bytes(b"MZ")
    (game / "Data.wolf").write_bytes(ARCHIVE)
    return game


def _snapshot(folder):
    """Every path under folder with its bytes (None for folders)."""
    return {str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def _write_good_output(cwd):
    data = cwd / "Data"
    (data / "BasicData").mkdir(parents=True)
    (data / "BasicData" / "CommonEvent.dat").write_bytes(b"ce")
    (data / "BasicData" / "Game.dat").write_bytes(b"unpacked game.dat")
    (data / "MapData").mkdir()
    (data / "MapData" / "Map001.mps").write_bytes(b"map")
    # pictures/sounds: a complete unpack is at least the archive's size
    (data / "Picture").mkdir()
    (data / "Picture" / "title.png").write_bytes(
        b"p" * (cwd / "Data.wolf").stat().st_size)


class FakePopen:
    """Stands in for UberWolfCli: `behaviour(cwd)` writes what it would."""

    calls: list = []
    behaviour = staticmethod(_write_good_output)
    hang = False

    def __init__(self, args, **kw):
        FakePopen.calls.append({"args": args, **kw})
        self.pid = 4242
        self.returncode = 0
        self.killed = False
        self._timeouts = 1 if FakePopen.hang else 0
        FakePopen.behaviour(pathlib.Path(kw["cwd"]))

    def communicate(self, timeout=None):
        if self._timeouts:
            self._timeouts -= 1
            raise subprocess.TimeoutExpired("UberWolfCli.exe", timeout)
        return (b"Unpacking: Data.wolf ... Done\r\n", None)

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_uw(tmp_path, monkeypatch):
    """A fake UberWolfCli.exe on the dev lookup path + mocked Popen."""
    exe = tmp_path / "tools" / "UberWolfCli.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ fake")
    monkeypatch.setenv(wolf_unpack.ENV_OVERRIDE, str(exe))
    FakePopen.calls = []
    FakePopen.behaviour = staticmethod(_write_good_output)
    FakePopen.hang = False
    monkeypatch.setattr(wolf_unpack.subprocess, "Popen", FakePopen)
    return exe


# ── the reported bug: frozen exe, no _reference ──────────────────────────

def test_translator_never_imports_from_reference():
    """_reference/ is a gitignored dev folder; it is never in the release."""
    bad = []
    for f in [*ROOT.joinpath("translator").rglob("*.py"), ROOT / "main.py"]:
        text = f.read_text(encoding="utf-8")
        if "_reference" in text or "Wolf_RPG_Decompyler import" in text:
            bad.append(str(f.relative_to(ROOT)))
    assert bad == []


def _freeze(monkeypatch, bundle):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)


def test_frozen_exe_finds_only_the_bundled_unpacker(tmp_path, monkeypatch):
    bundle = tmp_path / "_internal"
    bundled = bundle / "third_party" / "UberWolf" / "UberWolfCli.exe"
    other = tmp_path / "elsewhere" / "UberWolfCli.exe"
    other.parent.mkdir()
    other.write_bytes(b"MZ")
    monkeypatch.setenv(wolf_unpack.ENV_OVERRIDE, str(other))
    monkeypatch.setenv("PATH", str(other.parent))
    _freeze(monkeypatch, bundle)
    assert wolf_unpack.find_uberwolf() is None      # not bundled: none
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    assert wolf_unpack.find_uberwolf() == bundled


def test_frozen_exe_unpacks_data_wolf_without_reference(tmp_path, monkeypatch):
    """The exact F95zone report: a packed game opened in the release exe."""
    bundle = tmp_path / "_internal"
    bundled = bundle / "third_party" / "UberWolf" / "UberWolfCli.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ")
    _freeze(monkeypatch, bundle)
    monkeypatch.delenv(wolf_unpack.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(sys, "path",
                        [p for p in sys.path if "_reference" not in p])
    monkeypatch.delitem(sys.modules, "Wolf_RPG_Decompyler", raising=False)
    FakePopen.calls = []
    FakePopen.behaviour = staticmethod(_write_good_output)
    FakePopen.hang = False
    monkeypatch.setattr(wolf_unpack.subprocess, "Popen", FakePopen)
    game = _game(tmp_path)

    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))

    assert not any("_reference" in w for w in p.load_warnings)
    assert (game / "Data" / "BasicData" / "CommonEvent.dat").is_file()
    assert (game / "Data.wolf").read_bytes() == ARCHIVE
    assert sorted(x.name for x in game.iterdir()) == ["Data", "Data.wolf", "Game.exe"]
    assert not any("_reference" in s for s in sys.path)
    assert "Wolf_RPG_Decompyler" not in sys.modules
    (call,) = FakePopen.calls
    assert call["executable"] == str(bundled)
    assert call["args"][0] == "UberWolfCli.exe"     # bare name: see run_uberwolf
    assert call["args"][1] == "--"
    assert not call.get("shell")
    assert call["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cwd = pathlib.Path(call["cwd"])
    assert cwd.parent == game and cwd.name.startswith(wolf_unpack.TEMP_PREFIX)
    assert call["args"][2] == str(cwd / "Data.wolf")


# ── failures leave the game folder as it was ─────────────────────────────

def test_missing_unpacker_gives_plain_message(tmp_path, monkeypatch):
    monkeypatch.setattr(wolf_unpack, "find_uberwolf", lambda: None)
    game = _game(tmp_path)
    before = _snapshot(game)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith("This game's data is packed in Data.wolf and the "
                          "built-in unpacker (UberWolf) couldn't open it:")
    assert "missing from this copy of the app" in msg
    assert "unpack it yourself with UberWolf" in msg
    for word in ("_reference", "Clone", "clone", "ImportError"):
        assert word not in msg
    assert _snapshot(game) == before


def test_failed_unpack_leaves_folder_unchanged(tmp_path, fake_uw):
    FakePopen.behaviour = staticmethod(lambda cwd: None)   # writes nothing
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="could not decrypt"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_partial_unpack_is_not_moved_into_place(tmp_path, fake_uw):
    def partial(cwd):
        (cwd / "Data" / "BasicData").mkdir(parents=True)
        (cwd / "Data" / "BasicData" / "Game.dat").write_bytes(b"half")
    FakePopen.behaviour = staticmethod(partial)
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_timeout_kills_the_tree_and_cleans_up(tmp_path, fake_uw, monkeypatch):
    killed = []
    monkeypatch.setattr(wolf_unpack.ollama_procs, "kill_tree",
                        lambda pid: killed.append(pid) or True)
    FakePopen.hang = True
    FakePopen.behaviour = staticmethod(
        lambda cwd: (cwd / "Data" / "BasicData").mkdir(parents=True))
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="did not finish"):
        wolf_unpack.unpack_data_wolf(game, timeout=120)
    assert killed == [4242]
    assert _snapshot(game) == before


def test_unpacker_that_cannot_start(tmp_path, fake_uw, monkeypatch):
    def boom(*a, **k):
        raise OSError("blocked by antivirus")
    monkeypatch.setattr(wolf_unpack.subprocess, "Popen", boom)
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="blocked by antivirus"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_file_named_data_is_never_replaced(tmp_path, fake_uw):
    game = _game(tmp_path)
    (game / "Data").write_bytes(b"not a folder")
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="in the way"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before
    assert FakePopen.calls == []


def test_temp_folder_creation_failure(tmp_path, fake_uw, monkeypatch):
    def nope(**k):
        raise PermissionError("read-only folder")
    monkeypatch.setattr(wolf_unpack.tempfile, "mkdtemp", nope)
    game = _game(tmp_path)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="read-only folder"):
        wolf_unpack.unpack_data_wolf(game)


# ── existing Data/ is never deleted or overwritten ───────────────────────

def test_existing_data_folder_is_merged_without_overwriting(tmp_path, fake_uw):
    game = _game(tmp_path)
    (game / "Data" / "BasicData").mkdir(parents=True)
    (game / "Data" / "BasicData" / "Game.dat").write_bytes(b"USER EDITED")
    (game / "Data" / "Picture").mkdir()
    (game / "Data" / "Picture" / "mine.png").write_bytes(b"png")
    out = wolf_unpack.unpack_data_wolf(game)
    assert out == game / "Data"
    assert (out / "BasicData" / "Game.dat").read_bytes() == b"USER EDITED"
    assert (out / "Picture" / "mine.png").read_bytes() == b"png"
    assert (out / "BasicData" / "CommonEvent.dat").read_bytes() == b"ce"
    assert (out / "MapData" / "Map001.mps").is_file()
    assert sorted(x.name for x in game.iterdir()) == ["Data", "Data.wolf", "Game.exe"]


def test_failed_merge_takes_back_what_it_moved(tmp_path, fake_uw, monkeypatch):
    game = _game(tmp_path)
    (game / "Data" / "Picture").mkdir(parents=True)
    (game / "Data" / "Picture" / "mine.png").write_bytes(b"png")
    before = _snapshot(game)
    real_rename = os.rename
    seen = []

    def flaky(src, dst):
        seen.append(dst)
        # merge: 1st move works (BasicData), 2nd (MapData) fails
        if len(seen) == 2 and pathlib.Path(dst).parent == game / "Data":
            raise OSError("disk full")
        return real_rename(src, dst)
    monkeypatch.setattr(wolf_unpack.os, "rename", flaky)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="disk full"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_new_data_folder_and_archive_untouched(tmp_path, fake_uw):
    game = _game(tmp_path)
    wolf_unpack.unpack_data_wolf(game)
    assert (game / "Data.wolf").read_bytes() == ARCHIVE
    assert (game / "Data" / "BasicData" / "Game.dat").read_bytes() == b"unpacked game.dat"
    assert sorted(x.name for x in game.iterdir()) == ["Data", "Data.wolf", "Game.exe"]


def test_copy_used_when_hard_link_is_not_possible(tmp_path, fake_uw, monkeypatch):
    def no_link(src, dst):
        raise OSError("exFAT: no hard links")
    monkeypatch.setattr(wolf_unpack.os, "link", no_link)
    copied = []

    def behaviour(cwd):
        copied.append((cwd / "Data.wolf").read_bytes())
        _write_good_output(cwd)
    FakePopen.behaviour = staticmethod(behaviour)
    game = _game(tmp_path)
    wolf_unpack.unpack_data_wolf(game)
    assert copied == [ARCHIVE]
    assert (game / "Data.wolf").read_bytes() == ARCHIVE


# ── Pro / 3.5 archives are refused before unpacking; plain 3.x unpacks ───

def _dx_header(crypt_version):
    head = bytearray(64)
    head[0:2] = b"DX"
    head[2:4] = struct.pack("<H", 8)
    struct.pack_into("<I", head, 44, crypt_version << 16)
    return bytes(head)


@pytest.mark.parametrize("content, refusal", [
    (_dx_header(0x12C), None),          # Wolf 3.00 (plain)
    (_dx_header(0x13A), None),          # Wolf 3.14 (plain)
    (_dx_header(0x14B), None),          # Wolf 3.31 (plain)
    (_dx_header(0x15E), wolf_unpack.ARCHIVE_WOLF35),    # 3.50: compressed
    (_dx_header(1000), wolf_unpack.ARCHIVE_PROTECTED),  # Pro
    (_dx_header(0x3F2), wolf_unpack.ARCHIVE_PROTECTED), # Pro, later key
    (_dx_header(0xC8), wolf_unpack.ARCHIVE_PROTECTED),  # ChaCha20 Pro
    (_dx_header(0x64), wolf_unpack.ARCHIVE_PROTECTED),  # unknown to us
    (_dx_header(0), None),              # 2.225+ plain DXArchive v8
    (b"\xb4\x6d\x68\x1c" + bytes(60), None),   # 2.x encrypted header
    (b"DX", None),                      # too short to judge
])
def test_archive_header_refusal(tmp_path, content, refusal):
    f = tmp_path / "Data.wolf"
    f.write_bytes(content)
    assert wolf_unpack.archive_refusal(f) == refusal


@pytest.mark.parametrize("version, words", [
    (1000, "is protected (WOLF RPG Editor Pro)"),
    (0x15E, "is a WOLF RPG Editor 3.5 archive"),
])
def test_pro_and_35_archives_refused_without_unpacking(tmp_path, monkeypatch,
                                                       version, words):
    def must_not_run(game_dir):
        raise AssertionError("archive was unpacked")
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", must_not_run)
    game = tmp_path / "W3"
    game.mkdir()
    (game / "GamePro.exe").write_bytes(b"MZ")
    (game / "Data.wolf").write_bytes(_dx_header(version))
    before = _snapshot(game)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith(wolfrpg.WOLF_UNSUPPORTED)
    assert f"Data.wolf {words}" in msg
    assert "Your game files were not changed" in msg
    assert _snapshot(game) == before
    with pytest.raises(wolfrpg.WolfVersionUnsupported) as ei:
        p.save_project(str(game), [])
    assert f"Data.wolf {words}" in str(ei.value)
    assert _snapshot(game) == before


def test_plain_wolf3_archive_is_unpacked(tmp_path, fake_uw):
    game = _game(tmp_path)
    (game / "Data.wolf").write_bytes(_dx_header(0x14B))
    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))
    assert len(FakePopen.calls) == 1                 # UberWolf ran
    args = FakePopen.calls[0]["args"]
    assert "--" in args
    assert not any(a in ("-u", "--unprotect") for a in args)
    assert (game / "Data" / "MapData" / "Map001.mps").is_file()
    assert not any("can't be translated" in w for w in p.load_warnings)


def test_compressed_data_found_after_unpacking_is_still_refused(tmp_path,
                                                                fake_uw):
    """An archive the header check passes: every unpacked file is checked."""
    def wolf35_output(cwd):
        _write_good_output(cwd)
        (cwd / "Data" / "BasicData" / "DataBase.dat").write_bytes(
            b"\x00W\x00\x00OLUFM\x00\xc4" + b"\x00" * 6)
    FakePopen.behaviour = staticmethod(wolf35_output)
    game = _game(tmp_path)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    assert p.load_warnings[0].startswith(wolfrpg.WOLF_UNSUPPORTED)
    assert "DataBase.dat is compressed" in p.load_warnings[0]
    # the Data/ this call created is removed again (existing refusal rule)
    assert sorted(x.name for x in game.iterdir()) == ["Data.wolf", "Game.exe"]


# ── build pin ────────────────────────────────────────────────────────────

def test_uberwolf_pin_and_licence_are_in_the_repo():
    folder = ROOT / "third_party" / "UberWolf"
    pin = dict(line.split("=", 1) for line in
               (folder / "PIN.txt").read_text(encoding="utf-8").split()
               if "=" in line)
    assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
    assert pin["url"] == ("https://github.com/Sinflower/UberWolf/releases/"
                          f"download/{pin['version']}/UberWolfCli.exe")
    assert "MIT License" in (folder / "LICENSE").read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "rpgmaker_translator.spec").read_text(encoding="utf-8")
    assert "_uberwolf_data()" in spec and "PIN.txt" in spec
    build = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "fetch_uberwolf.ps1" in build
    # the binary itself is downloaded at build time, never committed
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "third_party/UberWolf/UberWolfCli.exe" in ignore


# ── review round (wolf 1.2): partial unpacks, leftovers, updates ─────────

R3_OUTPUT = (b"Unpacking: Data.wolf ... Error writing Picture/title.png: "
             b"disk full\r\nFailed\r\n")


def _data_only_output(cwd):
    """The data files came out, the pictures/sounds did not."""
    data = cwd / "Data"
    (data / "BasicData").mkdir(parents=True)
    (data / "BasicData" / "CommonEvent.dat").write_bytes(b"ce")
    (data / "MapData").mkdir()
    (data / "MapData" / "Map001.mps").write_bytes(b"map")


def test_partial_unpack_with_quiet_output_is_refused(tmp_path, fake_uw):
    """M1: UberWolf printed "Done" (or nothing readable) but stopped short."""
    FakePopen.behaviour = staticmethod(_data_only_output)
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError,
                       match="only part of the archive came out"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_partial_unpack_is_not_merged_into_existing_data(tmp_path, fake_uw):
    FakePopen.behaviour = staticmethod(_data_only_output)
    game = _game(tmp_path)
    (game / "Data" / "Picture").mkdir(parents=True)
    (game / "Data" / "Picture" / "mine.png").write_bytes(b"png")
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


def test_failed_line_in_output_fails_the_unpack(tmp_path, fake_uw,
                                                monkeypatch):
    """M1: a readable "Failed" is trusted even when the sizes look fine."""
    monkeypatch.setattr(FakePopen, "communicate",
                        lambda self, timeout=None: (R3_OUTPUT, None))
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="stopped part way"):
        wolf_unpack.unpack_data_wolf(game)
    assert _snapshot(game) == before


@pytest.mark.parametrize("output, failed", [
    ("Unpacking: Data.wolf ... Done", False),
    ("", False),
    ("Unpacking: Data.wolf ... Error writing x.png: disk full", True),
    ("Unpacking: Data.wolf ... Searching for game executable in: C:\\G\r\n"
     "Could not find the game executable\r\nFailed", True),
    ("Error: cannot open file", True),
    ("Unpacking: Data.wolf ... Done\r\nSearching in: C:\\Error Games\\x",
     False),
])
def test_reported_failure(output, failed):
    assert bool(wolf_unpack._reported_failure(output)) is failed


def test_r3_partial_unpack_is_not_loaded_or_exported(tmp_path, fake_uw,
                                                     monkeypatch):
    """The review's repro: the project must not load, Data.wolf stays."""
    FakePopen.behaviour = staticmethod(_data_only_output)
    monkeypatch.setattr(FakePopen, "communicate",
                        lambda self, timeout=None: (R3_OUTPUT, None))
    game = _game(tmp_path)
    before = _snapshot(game)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith("This game's data is packed in Data.wolf")
    assert _snapshot(game) == before
    with pytest.raises(wolf_unpack.WolfUnpackError):
        p.save_project(str(game), [])
    assert _snapshot(game) == before


def test_not_enough_free_space_refuses_before_unpacking(tmp_path, fake_uw,
                                                         monkeypatch):
    free = len(ARCHIVE) * wolf_unpack.FREE_SPACE_FACTOR - 1
    monkeypatch.setattr(wolf_unpack.shutil, "disk_usage",
                        lambda p: shutil_usage(free))
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError,
                       match="not enough free space"):
        wolf_unpack.unpack_data_wolf(game)
    assert FakePopen.calls == []
    assert _snapshot(game) == before


def test_copy_fallback_needs_room_for_the_copy(tmp_path, fake_uw,
                                               monkeypatch):
    free = len(ARCHIVE) * wolf_unpack.FREE_SPACE_FACTOR  # enough to link
    monkeypatch.setattr(wolf_unpack.shutil, "disk_usage",
                        lambda p: shutil_usage(free))

    def no_link(src, dst):
        raise OSError("exFAT: no hard links")
    monkeypatch.setattr(wolf_unpack.os, "link", no_link)
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(wolf_unpack.WolfUnpackError,
                       match="not enough free space"):
        wolf_unpack.unpack_data_wolf(game)
    assert FakePopen.calls == []
    assert _snapshot(game) == before


def shutil_usage(free):
    import shutil
    return shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - free, free)


def _junction(link, target):
    if os.name != "nt":
        os.symlink(target, link, target_is_directory=True)
        return
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_leftover_unpack_folders_are_removed_on_open(tmp_path):
    """M2: a killed unpack's temp folder (GBs) is cleaned on the next open;
    links, files and look-alike names are left alone."""
    game = tmp_path / "Game"
    (game / "Data" / "MapData").mkdir(parents=True)
    (game / "Game.exe").write_bytes(b"MZ")
    left = game / (wolf_unpack.TEMP_PREFIX + "abc123")
    (left / "Data" / "Picture").mkdir(parents=True)
    (left / "Data" / "Picture" / "big.png").write_bytes(b"x" * 100)
    (left / "Data.wolf").write_bytes(ARCHIVE)
    (game / (wolf_unpack.TEMP_PREFIX + "file")).write_bytes(b"keep")
    (game / ".rmt_unpackX").mkdir()                   # not our prefix
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_bytes(b"keep me")
    _junction(game / (wolf_unpack.TEMP_PREFIX + "link"), outside)

    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))

    assert not left.exists()
    assert (game / (wolf_unpack.TEMP_PREFIX + "file")).read_bytes() == b"keep"
    assert (game / ".rmt_unpackX").is_dir()
    assert (game / (wolf_unpack.TEMP_PREFIX + "link")).exists()
    assert (outside / "precious.txt").read_bytes() == b"keep me"


def test_leftover_folder_is_removed_before_unpacking(tmp_path, fake_uw):
    game = _game(tmp_path)
    left = game / (wolf_unpack.TEMP_PREFIX + "old")
    (left / "Data").mkdir(parents=True)
    wolf_unpack.unpack_data_wolf(game)
    assert sorted(x.name for x in game.iterdir()) == [
        "Data", "Data.wolf", "Game.exe"]


def test_interrupted_merge_is_finished_on_next_open(tmp_path, fake_uw):
    """M2: Data/ half-merged by a killed unpack is not trusted as is: the
    next open unpacks again and adds what is missing (nothing replaced)."""
    game = _game(tmp_path)
    data = game / "Data"
    (data / "MapData").mkdir(parents=True)        # merged before the kill
    (data / "MapData" / "Map001.mps").write_bytes(b"map")
    (data / "Picture").mkdir()
    (data / "Picture" / "mine.png").write_bytes(b"user's own")
    (data / wolf_unpack.MERGE_MARKER).write_bytes(b"")
    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))
    assert len(FakePopen.calls) == 1
    assert (data / "BasicData" / "CommonEvent.dat").read_bytes() == b"ce"
    assert (data / "Picture" / "title.png").is_file()
    assert (data / "Picture" / "mine.png").read_bytes() == b"user's own"
    assert not (data / wolf_unpack.MERGE_MARKER).exists()
    assert (game / "Data.wolf").read_bytes() == ARCHIVE


def test_merge_marker_stays_when_merge_cannot_be_undone(tmp_path, fake_uw,
                                                        monkeypatch):
    game = _game(tmp_path)
    (game / "Data" / "Picture").mkdir(parents=True)
    real_rename = os.rename
    seen = []

    def flaky(src, dst):
        seen.append(dst)
        if len(seen) == 2 and pathlib.Path(dst).parent == game / "Data":
            raise OSError("disk full")              # 2nd move fails
        if len(seen) > 2:
            raise OSError("locked")                 # and so does the undo
        return real_rename(src, dst)
    monkeypatch.setattr(wolf_unpack.os, "rename", flaky)
    with pytest.raises(wolf_unpack.WolfUnpackError, match="taken back out"):
        wolf_unpack.unpack_data_wolf(game)
    assert wolf_unpack.merge_was_interrupted(game / "Data")


def _exported_game(tmp_path):
    """A packed game after an export: Data/ + Data.wolf.bak."""
    game = tmp_path / "Game"
    (game / "Data" / "BasicData").mkdir(parents=True)
    (game / "Data" / "MapData").mkdir()
    (game / "Game.exe").write_bytes(b"MZ")
    (game / "Data.wolf.bak").write_bytes(b"old version archive")
    return game


def test_game_update_after_export_is_refused(tmp_path, monkeypatch):
    """M3: a new Data.wolf next to Data.wolf.bak and Data/ is an update;
    loading or exporting must not move it aside or use the old Data/."""
    def must_not_run(game_dir):
        raise AssertionError("archive was unpacked")
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", must_not_run)
    game = _exported_game(tmp_path)
    (game / "Data.wolf").write_bytes(b"NEW version archive")
    before = _snapshot(game)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith("This game has a new Data.wolf")
    assert "Data.wolf.bak" in msg and "Restore Originals" in msg
    with pytest.raises(wolfrpg.WolfGameUpdated):
        p.save_project(str(game), [])
    assert _snapshot(game) == before


def test_identical_data_wolf_and_bak_is_not_an_update(tmp_path):
    game = _exported_game(tmp_path)
    (game / "Data.wolf").write_bytes(b"old version archive")
    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))
    assert not any("new Data.wolf" in w for w in p.load_warnings)
    assert p.data_dir == game / "Data"


def test_v6_archive_header_is_not_judged_by_offset_44(tmp_path):
    """L1: before DXArchive v8 there is no Flags field at offset 44."""
    head = bytearray(_dx_header(1000))           # "Pro" bits at 44 ...
    head[2:4] = struct.pack("<H", 6)              # ... in a v6 header
    f = tmp_path / "Data.wolf"
    f.write_bytes(bytes(head))
    assert wolf_unpack.archive_crypt_version(f) is None
    assert wolf_unpack.archive_refusal(f) is None


def test_refusal_after_merge_says_what_was_added(tmp_path, fake_uw):
    """L3: files merged into an existing Data/ stay there; the message must
    not claim nothing changed."""
    def wolf35_output(cwd):
        _write_good_output(cwd)
        (cwd / "Data" / "BasicData" / "DataBase.dat").write_bytes(
            b"\x00W\x00\x00OLUFM\x00\xc4" + b"\x00" * 6)
    FakePopen.behaviour = staticmethod(wolf35_output)
    game = _game(tmp_path)
    (game / "Data" / "Save").mkdir(parents=True)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    msg = p.load_warnings[0]
    assert "Your game files were not changed" not in msg
    assert "added to the existing Data folder" in msg
    assert (game / "Data.wolf").read_bytes() == ARCHIVE


def test_restore_never_unpacks(tmp_path, monkeypatch):
    """L4: Restore Originals on a packed game with nothing exported."""
    def must_not_run(*a, **k):
        raise AssertionError("archive was unpacked")
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", must_not_run)
    monkeypatch.setattr(wolf_unpack, "unpack_data_wolf", must_not_run)
    game = _game(tmp_path)
    before = _snapshot(game)
    with pytest.raises(FileNotFoundError, match="Nothing to restore"):
        wolfrpg.WolfRPGParser().restore_originals(str(game))
    assert _snapshot(game) == before


def test_restore_without_data_folder_uses_the_backup(tmp_path, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("archive was unpacked")
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", must_not_run)
    game = tmp_path / "Game"
    (game / "Data_original" / "BasicData").mkdir(parents=True)
    (game / "Data_original" / "BasicData" / "CommonEvent.dat").write_bytes(
        b"jp")
    (game / "Data.wolf.bak").write_bytes(ARCHIVE)
    wolfrpg.WolfRPGParser().restore_originals(str(game))
    assert (game / "Data" / "BasicData" / "CommonEvent.dat").read_bytes() \
        == b"jp"
    assert (game / "Data.wolf").read_bytes() == ARCHIVE


# ── the window stays responsive while a Wolf game loads (M2) ─────────────

@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from qt_helpers import dispose_main_window

    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = True
    yield win
    dispose_main_window(win)


def test_wolf_load_runs_off_the_gui_thread(window, monkeypatch):
    import threading
    seen = {}

    def load(path):
        seen["thread"] = threading.current_thread()
        return ["entry"]
    monkeypatch.setattr(window.handler, "load_project", load)
    window._project_type = "wolfrpg"
    assert window._load_entries("C:/game") == ["entry"]
    assert seen["thread"] is not threading.main_thread()


def test_wolf_load_errors_reach_the_gui_thread(window, monkeypatch):
    def load(path):
        raise FileNotFoundError("No Data/ folder or Data.wolf")
    monkeypatch.setattr(window.handler, "load_project", load)
    window._project_type = "wolfrpg"
    with pytest.raises(FileNotFoundError, match="No Data/ folder"):
        window._load_entries("C:/game")
