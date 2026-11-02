"""Repro / falsification tests for claims in the Gemini 3.8 Flash review
(2026-11-01) of v1.1.0 @ c2958bd.

Convention:
  * ``test_repro_*``  — asserts the CURRENT (buggy) behaviour. PASS = bug reproduced.
  * ``test_refute_*`` — asserts the behaviour Gemini said does NOT happen.
                        PASS = Gemini's claim is false.
No production code is modified.
"""

import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import zipfile

import pytest

from translator.project_model import TranslationEntry


def _cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


def _entry(id_, file, field, original, translation="", status="translated"):
    return TranslationEntry(id=id_, file=file, field=field, original=original,
                            translation=translation, status=status)


# ── Risk 1: MV export matches dialogue by text (FIFO), not by entry ID ─────

def test_fixed_risk1_mv_translation_stays_on_its_own_event():
    """FIXED: event A's line is untranslated, event B's identical line is
    translated.  B gets its English; A stays Japanese (it used to be the
    other way round)."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    data = {"events": [
        None,
        {"id": 1, "name": "A", "pages": [{"list": [
            _cmd(101, ["", 0, 0, 2]), _cmd(401, ["はい"]), _cmd(0, [])]}]},
        {"id": 2, "name": "B", "pages": [{"list": [
            _cmd(101, ["", 0, 0, 2]), _cmd(401, ["はい"]), _cmd(0, [])]}]},
    ]}
    # Only event B's line is translated (event A's entry is untranslated and
    # therefore filtered out before _apply_translations_fast, as save_project does).
    entries = [_entry("Map001.json/Ev2(B)/p0/dialog_1", "Map001.json",
                      "dialog", "はい", "Yes (event B)")]
    RPGMakerMVParser()._apply_translations_fast(data, entries)
    a_text = data["events"][1]["pages"][0]["list"][1]["parameters"][0]
    b_text = data["events"][2]["pages"][0]["list"][1]["parameters"][0]
    assert a_text == "はい"
    assert b_text == "Yes (event B)"


def test_fixed_risk1_mv_context_specific_translations_not_swapped():
    """FIXED: same JP line translated differently per event, entries listed
    in a different order than the file -> each lands on its own event."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    data = {"events": [
        None,
        {"id": 1, "name": "A", "pages": [{"list": [
            _cmd(401, ["そうか"]), _cmd(0, [])]}]},
        {"id": 2, "name": "B", "pages": [{"list": [
            _cmd(401, ["そうか"]), _cmd(0, [])]}]},
    ]}
    entries = [
        _entry("Map001.json/Ev2(B)/p0/dialog_1", "Map001.json", "dialog",
               "そうか", "I see... (sad, B)"),
        _entry("Map001.json/Ev1(A)/p0/dialog_1", "Map001.json", "dialog",
               "そうか", "Oh, really? (A)"),
    ]
    RPGMakerMVParser()._apply_translations_fast(data, entries)
    assert data["events"][1]["pages"][0]["list"][0]["parameters"][0] == "Oh, really? (A)"
    assert data["events"][2]["pages"][0]["list"][0]["parameters"][0] == "I see... (sad, B)"


def test_fixed_risk1_mv_same_page_duplicate_and_choices_by_position():
    """Two identical lines on one page, only the second translated; two
    identical choice lists, only the second translated."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    data = {"events": [None, {"id": 1, "name": "A", "pages": [{"list": [
        _cmd(401, ["はい"]), _cmd(102, [["行く", "やめる"], 1]),
        _cmd(101, ["", 0, 0, 2]),
        _cmd(401, ["はい"]), _cmd(102, [["行く", "やめる"], 1]),
        _cmd(0, [])]}]}]}
    entries = [
        _entry("Map001.json/Ev1(A)/p0/dialog_2", "Map001.json", "dialog",
               "はい", "Yes, second"),
        _entry("Map001.json/Ev1(A)/p0/choice_2_0", "Map001.json", "choice",
               "行く", "Go"),
    ]
    RPGMakerMVParser()._apply_translations_fast(data, entries)
    lst = data["events"][1]["pages"][0]["list"]
    assert lst[0]["parameters"][0] == "はい"
    assert lst[1]["parameters"][0] == ["行く", "やめる"]
    assert lst[3]["parameters"][0] == "Yes, second"
    assert lst[4]["parameters"][0] == ["Go", "やめる"]


def test_fixed_risk1_mv_moved_line_falls_back_within_its_page():
    """If the ID's position no longer holds the original (game update moved
    it), text matching is still used -- but only inside the same page."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    data = {"events": [None,
        {"id": 1, "name": "A", "pages": [{"list": [
            _cmd(401, ["新しい行"]), _cmd(101, ["", 0, 0, 2]),
            _cmd(401, ["こんにちは"]), _cmd(0, [])]}]},
        {"id": 2, "name": "B", "pages": [{"list": [
            _cmd(401, ["こんにちは"]), _cmd(0, [])]}]},
    ]}
    entries = [_entry("Map001.json/Ev1(A)/p0/dialog_1", "Map001.json",
                      "dialog", "こんにちは", "Hello")]
    RPGMakerMVParser()._apply_translations_fast(data, entries)
    assert data["events"][1]["pages"][0]["list"][0]["parameters"][0] == "新しい行"
    assert data["events"][1]["pages"][0]["list"][2]["parameters"][0] == "Hello"
    assert data["events"][2]["pages"][0]["list"][0]["parameters"][0] == "こんにちは"


# ── Risk 2: note-separator stripping truncates real content ───────────────

def test_fixed_risk2_heavy_dash_scene_break_is_kept():
    """FIXED: a ━━━ scene break rendered as '---' no longer deletes the rest."""
    from translator.ai_client import AIClient
    source = "第一章\n━━━━━━\nそして朝が来た。"
    llm_out = "Chapter One\n------\nAnd then morning came."
    assert AIClient._strip_notes(llm_out, source) == llm_out


def test_fixed_risk2_fullwidth_asterisks_kept():
    from translator.ai_client import AIClient
    source = "ここまで\n＊＊＊\nここから"
    llm_out = "Up to here\n***\nFrom here"
    assert AIClient._strip_notes(llm_out, source) == llm_out


def test_fixed_risk2_unlisted_separator_style_without_source_line_kept():
    """Even a separator style no list covers keeps the text when the part
    before it doesn't account for every source line."""
    from translator.ai_client import AIClient
    source = "朝だ。\n鳥が鳴く。\n今日も一日が始まる。"
    llm_out = "Morning.\n---\nBirds sing.\nAnother day begins."
    assert AIClient._strip_notes(llm_out, source) == llm_out


def test_fixed_risk2_labelled_note_after_separator_still_stripped():
    from translator.ai_client import AIClient
    out = AIClient._strip_notes("Hello.\n---\nNote: literally 'good day'.",
                                "こんにちは。\n――\n")
    assert out == "Hello."


def test_refute_risk2_mitigation_when_source_has_ascii_or_common_dash_line():
    """Gemini says it strips 'regardless'; it does not when the source itself
    has a ---/――/ーー separator line."""
    from translator.ai_client import AIClient
    source = "第一章\n――――\nそして朝が来た。"
    llm_out = "Chapter One\n----\nAnd then morning came."
    assert AIClient._strip_notes(llm_out, source) == llm_out


# ── Risk 3: SRPG Studio heuristic scanner treats ints as strings ───────────

def test_fixed_risk3_srpg_scanner_ignores_unterminated_integers():
    """FIXED: two uint32 fields (4, 0x30443042) look like the UTF-16 string
    'あい' but have no NUL terminator, so they are no longer offered for
    translation.  A real terminated string is still found."""
    from translator.srpgstudio import SRPGStudioParser
    blob = b"\x00" * 8 + struct.pack("<II", 4, 0x30443042) + b"\x00" * 8
    found = SRPGStudioParser()._scan_strings(blob)
    assert not any(t == "あい" for _o, _l, t in found)
    real = struct.pack("<I", 6) + "あい\x00".encode("utf-16-le")
    assert SRPGStudioParser()._scan_strings(b"\x01" * 4 + real) == [(4, 6, "あい")]
    # A NUL inside the claimed length is not one string either
    bad = struct.pack("<I", 8) + "あ\x00い\x00".encode("utf-16-le")
    assert SRPGStudioParser()._scan_strings(bad) == []


def test_by_design_risk3_srpg_patch_changes_blob_length():
    """Still true by design: the project blob runs to EOF, so a length
    change only shifts data after it inside the blob (see review R3)."""
    from translator.srpgstudio import SRPGStudioParser
    blob = b"HDR!" + struct.pack("<I", 6) + "勇者\x00".encode("utf-16-le") + b"TAIL"
    strings = SRPGStudioParser()._scan_strings(blob)
    patched = SRPGStudioParser._patch_blob(blob, strings, {"勇者": "The Hero"})
    assert len(patched) != len(blob)   # every later byte offset shifts


# ── Risk 4: cmd /c mklink with unquoted metacharacters ────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_fixed_risk4_junction_created_without_shell_for_ampersand_path(tmp_path):
    """FIXED: "Open in RPG Maker" creates junctions through the Win32 API,
    never through cmd, so a folder named with '&' (no spaces) works and
    cannot inject a second command."""
    import inspect
    from translator.utils import create_junction
    from translator.widgets import main_window
    assert "mklink" not in inspect.getsource(main_window.MainWindow._open_in_rpgmaker)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    ws = tmp_path / "Tom&echo"          # legal Windows folder name
    ws.mkdir()
    link = os.path.join(str(ws), "INJECTED")
    create_junction(link, str(src))
    assert os.path.isfile(os.path.join(link, "a.txt"))
    os.rmdir(link)                       # removes the junction, not the target
    assert (src / "a.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe only")
def test_refute_risk4_geminis_example_with_spaces_is_quoted(tmp_path):
    line = subprocess.list2cmdline(
        ["cmd", "/c", "mklink", "/J", r"C:\Games\Game & calc.exe\data", "x"])
    assert '"C:\\Games\\Game & calc.exe\\data"' in line


def test_refute_risk4_install_bat_paths_are_fixed_names():
    """data_rel can only be data/Data/www/data/www/Data (find_content_root);
    the game title is caret-escaped."""
    from translator.rpgmaker_mv import RPGMakerMVParser
    bat = RPGMakerMVParser._build_install_bat(
        "www/data", "www/js", 3, True, 'Evil" & calc & "', False)
    assert "& calc" not in bat.replace("^&", "")


# ── Risk 5: zip slip in extract_nwjs ──────────────────────────────────────

def test_refute_risk5_zipfile_extract_strips_dotdot(tmp_path):
    from translator.tyranoscript import TyranoScriptParser
    exe = tmp_path / "Game.exe"
    with zipfile.ZipFile(exe, "w") as zf:
        zf.writestr("../../evil.txt", "x")
        zf.writestr("/abs/evil2.txt", "x")
    dest = tmp_path / "game" / "extracted"
    TyranoScriptParser.extract_nwjs(str(exe), str(dest))
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()
    assert (dest / "evil.txt").exists()          # '..' removed, stays inside
    assert (dest / "abs" / "evil2.txt").exists()


def test_refute_risk5_zipfile_has_no_filter_argument():
    """Gemini's suggested fix `zf.extractall(dest, filter="data")` is a tarfile API."""
    import inspect
    assert "filter" not in inspect.signature(zipfile.ZipFile.extractall).parameters


# ── Risk 6: MZ encrypted image header ────────────────────────────────────

def test_refute_risk6_mz_uses_same_rpgmv_header():
    """rmmz_core.js Utils.decryptArrayBuffer() checks the header hex string
    "52,50,47,4d,56,0,0,0,0,3,1,0,0,0,0,0" — i.e. b'RPGMV...' — exactly the
    bytes this tool writes. There is no 'RPGMZ' signature."""
    from translator.image_translator import _RPGMV_HEADER
    mz_expected = ",".join(format(b, "x") for b in _RPGMV_HEADER)
    assert mz_expected == "52,50,47,4d,56,0,0,0,0,3,1,0,0,0,0,0"


# ── Risk 7: non-atomic restore (Kirikiri / Wolf / Ace) ───────────────────

def test_fixed_risk7_kirikiri_restore_failure_keeps_live_dir(tmp_path, monkeypatch):
    """FIXED: restore copies the backup aside first and only then swaps, so a
    failing copy leaves the live scenario folder untouched."""
    from translator.kirikiri import KirikiriParser
    scen = tmp_path / "data" / "scenario"
    scen.mkdir(parents=True)
    (scen / "a.ks").write_text("EN", encoding="utf-8")
    bak = tmp_path / "data" / "scenario_original"
    bak.mkdir()
    (bak / "a.ks").write_text("JP", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full / AV lock")
    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        KirikiriParser().restore_originals(str(tmp_path))
    assert (scen / "a.ks").read_text(encoding="utf-8") == "EN"  # game still runs
    assert (bak / "a.ks").read_text(encoding="utf-8") == "JP"  # originals safe


# ── Risk 8: glossary dict mutation across threads ─────────────────────────

def test_refute_risk8_filter_glossary_survives_concurrent_inserts():
    """_filter_glossary snapshots with dict.copy() (atomic in CPython), so a
    concurrent writer cannot raise 'dictionary changed size during iteration'."""
    from translator.ai_client import AIClient
    c = AIClient()
    c.glossary = {f"語{i}": f"w{i}" for i in range(2000)}
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            c.glossary[f"新{i}"] = "x"
            i += 1
            if i % 5000 == 0:
                for k in [k for k in list(c.glossary) if k.startswith("新")]:
                    c.glossary.pop(k, None)

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(3000):
            try:
                c._filter_glossary("語1と語2")
            except RuntimeError as e:   # pragma: no cover
                errors.append(e)
    finally:
        stop.set()
        t.join()
    assert errors == []


# ── Risk 9: CSV export overwrites a guessed adjacent column ──────────────

def test_fixed_risk9_csv_never_overwrites_numeric_flag_column(tmp_path):
    """FIXED: a column of ids/flags is not taken as the English column; the
    file is left alone and the skip is reported instead of silently
    succeeding."""
    from translator.csv_game import CSVGameParser
    from translator.game_io import PartialExportError
    data = tmp_path / "data"
    data.mkdir()
    rows = "com,hen1,msg,se\n" + "".join(
        f"s,s,台詞{i}です,{i % 3}\n" for i in range(10))
    (data / "scene.x").write_text(rows, encoding="utf-8")
    p = CSVGameParser()
    entries = p.load_project(str(tmp_path))
    assert entries
    for e in entries:
        e.translation, e.status = "Line", "translated"
    with pytest.raises(PartialExportError, match="scene.x"):
        p.save_project(str(tmp_path), entries)
    out = (data / "scene.x").read_text(encoding="utf-8").splitlines()
    assert out[1].split(",") == ["s", "s", "台詞0です", "0"]  # 'se' id kept


# ── Risk 10: <think> in game text ────────────────────────────────────────

def test_refute_risk10_think_tag_in_game_text_is_shielded_as_placeholder():
    """A literal <think> in RPG Maker source is extracted as a «CODEn»
    placeholder (HTML-like tag rule) and never reaches the stripper as text."""
    from translator.ai_client import AIClient
    c = AIClient()
    c.project_type = "rpgmaker_mv"
    cleaned, mapping = c._extract_codes("<think>本当にいなくなったの？</think>")
    assert "<think>" not in cleaned and "«CODE1»" in cleaned


def test_partial_risk10_closed_pair_strips_only_the_block():
    """Gemini's own example is a CLOSED pair, which _THINK_RE removes alone —
    not 'all text through to the end of the string'."""
    from translator.ai_client import AIClient
    assert AIClient._strip_thinking("<think>Is she gone?</think> Yes.") == "Yes."


# ── Section 4: Ren'Py [variable] interpolation unprotected ──────────────

def test_fixed_s4_renpy_square_bracket_vars_protected_and_escaped():
    """FIXED: [player] becomes a placeholder the LLM can't translate, and a
    literal '[' / '{' produced by the LLM is escaped as '[[' / '{{' so
    Ren'Py doesn't raise when the line is shown."""
    from translator.ai_client import AIClient
    from translator.renpy import _escape_rpy
    c = AIClient()
    c.project_type = "renpy"
    cleaned, mapping = c._extract_codes("[player]さん、こんにちは")
    assert "[player]" not in cleaned and mapping == {"«CODE1»": "[player]"}
    assert AIClient._restore_codes(cleaned, mapping).startswith("[player]")
    assert _escape_rpy("[sigh] {laughs}") == "[[sigh] {{laughs}"
    # Tokens carried over from the source, and known text tags, are kept
    assert _escape_rpy("[player], {i}wait{/i}{w=0.5}", "[player]{w=0.5}") ==         "[player], {i}wait{/i}{w=0.5}"


# ── Section 4: whitespace example ───────────────────────────────────────

def test_refute_s4_whitespace_example_round_trips():
    from translator.ai_client import AIClient
    c = AIClient()
    c.project_type = "rpgmaker_mv"
    cleaned, m = c._extract_codes("\\C[1]Hero\\C[0] appeared")
    assert AIClient._restore_codes(cleaned, m) == "\\C[1]Hero\\C[0] appeared"
    assert AIClient._restore_codes("«CODE1»Hero«CODE2»appeared", m) == \
        "\\C[1]Hero\\C[0] appeared"   # trailing space survives even if LLM drops it


# ── Missed: Crowd sanitiser reintroduces the double quote it removes ────

def test_fixed_missed_crowd_curly_quotes_never_become_double_quotes():
    """FIXED: curly quotes map to ' and no bare " reaches the .sce script."""
    from translator.crowd import CrowdParser
    out = CrowdParser._sanitize_translation("\u201cHello\u201d \"Hi\"")
    assert out == "'Hello' 'Hi'"
    assert '"' not in out
