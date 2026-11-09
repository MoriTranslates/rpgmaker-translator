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


# ── 3.x archives are refused before unpacking ────────────────────────────

def _dx_header(crypt_version):
    head = bytearray(64)
    head[0:2] = b"DX"
    head[2:4] = struct.pack("<H", 8)
    struct.pack_into("<I", head, 44, crypt_version << 16)
    return bytes(head)


@pytest.mark.parametrize("content, is3", [
    (_dx_header(0x12C), True),          # Wolf 3.00
    (_dx_header(0x15E), True),          # Wolf 3.50
    (_dx_header(1000), True),           # Pro
    (_dx_header(0xC8), True),           # ChaCha20 Pro
    (_dx_header(0), False),             # 2.225+ plain DXArchive v8
    (b"\xb4\x6d\x68\x1c" + bytes(60), False),   # 2.x encrypted header
    (b"DX", False),                     # too short to judge
])
def test_archive_header_version(tmp_path, content, is3):
    f = tmp_path / "Data.wolf"
    f.write_bytes(content)
    assert wolf_unpack.archive_is_wolf3(f) is is3


def test_wolf3_archive_refused_without_unpacking(tmp_path, monkeypatch):
    def must_not_run(game_dir):
        raise AssertionError("3.x archive was unpacked")
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", must_not_run)
    game = tmp_path / "W3"
    game.mkdir()
    (game / "GamePro.exe").write_bytes(b"MZ")
    (game / "Data.wolf").write_bytes(_dx_header(0x14B))
    before = _snapshot(game)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    (msg,) = p.load_warnings
    assert msg.startswith(wolfrpg.WOLF3_UNSUPPORTED)
    assert "Your game files were not changed" in msg
    assert _snapshot(game) == before
    with pytest.raises(wolfrpg.WolfVersionUnsupported, match="3.x isn't supported"):
        p.save_project(str(game), [])
    assert _snapshot(game) == before


def test_wolf3_data_found_after_unpacking_is_still_refused(tmp_path, fake_uw):
    """A 3.x archive the header check can't see: wolf3_marker still runs."""
    def wolf3_output(cwd):
        _write_good_output(cwd)
        (cwd / "Data" / "BasicData" / "DataBase.dat").write_bytes(
            b"\x00W\x00\x00OLUFM\x00" + b"\x00" * 6)
    FakePopen.behaviour = staticmethod(wolf3_output)
    game = _game(tmp_path)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    assert p.load_warnings[0].startswith(wolfrpg.WOLF3_UNSUPPORTED)
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
