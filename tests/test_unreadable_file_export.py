"""Final review M1: one unreadable source file must not abort the whole
Kirikiri / CSV / Crowd export (manifest left pending, journal leaked)."""
import json
import os

import pytest

from translator import crowd, game_io
from translator.csv_game import CSVGameParser
from translator.kirikiri import KirikiriParser


@pytest.fixture(autouse=True)
def _clean_write_ahead():
    game_io._write_ahead.clear()
    yield
    game_io._write_ahead.clear()


def _translate(entries):
    for e in entries:
        e.translation, e.status = "EN " + e.original, "translated"


def _assert_no_pending_state(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        assert json.load(f)["pending"] is False
    assert game_io._write_ahead == {}


def test_kirikiri_unreadable_script_is_reported_and_others_exported(
        tmp_path, monkeypatch):
    scen = tmp_path / "data" / "scenario"
    scen.mkdir(parents=True)
    for name in ("a.ks", "b.ks"):
        (scen / name).write_text("*start\nこんにちは[p]\n", encoding="utf-8")
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    _translate(entries)

    real = p._detect_encoding

    def boom(path):
        if path.endswith("a.ks"):
            raise OSError("simulated: file locked by antivirus")
        return real(path)

    monkeypatch.setattr(p, "_detect_encoding", boom)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert any("a.ks" in x for x in ei.value.errors)
    assert "EN こんにちは" in (scen / "b.ks").read_text(encoding="utf-8")
    assert "こんにちは" in (scen / "a.ks").read_text(encoding="utf-8")
    _assert_no_pending_state(tmp_path / "data" / "scenario_original.manifest.json")


def test_csv_unreadable_file_is_reported_and_others_exported(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("a.x", "b.x"):
        (data / name).write_bytes(
            ("com,hen1,hen2,hen2_en\r\n" + "s,s,一行目,\r\n").encode("utf-8"))
    p = CSVGameParser()
    entries = p.load_project(str(tmp_path))
    assert {e.file for e in entries} == {"a.x", "b.x"}
    _translate(entries)
    # a.x becomes unreadable text (invalid UTF-8) after it was loaded
    (data / "a.x").write_bytes(b"com,hen1,hen2,hen2_en\r\ns,s,\xff\xfe\x81,\r\n")

    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert any("a.x" in x for x in ei.value.errors)
    assert "EN 一行目" in (data / "b.x").read_text(encoding="utf-8")
    _assert_no_pending_state(tmp_path / "data_original.manifest.json")


def _sce(text):
    body = f"$ main $  0 w000001a@!アリス@n{text}  1 CF 1-2 "
    return bytes(crowd.encrypt_sce(body.encode("cp932")))


def test_crowd_unreadable_script_is_reported_and_others_exported(
        tmp_path, monkeypatch):
    (tmp_path / "a.sce").write_bytes(_sce("こんにちは"))
    (tmp_path / "b.sce").write_bytes(_sce("こんにちは"))
    p = crowd.CrowdParser()
    entries = []
    for name in ("a.sce", "b.sce"):
        from translator.project_model import TranslationEntry
        entries.append(TranslationEntry(
            id=f"{name}/dialogue/0", file=name, field="dialogue",
            original="こんにちは", translation="Hello", status="translated"))

    real = crowd.decrypt_sce
    calls = []

    def flaky(data, *a, **kw):
        calls.append(1)
        if len(calls) == 1:          # the first script read (a.sce)
            raise ValueError("simulated: corrupt script")
        return real(data, *a, **kw)

    monkeypatch.setattr(crowd, "decrypt_sce", flaky)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert any("a.sce" in x for x in ei.value.errors)
    out = bytes(real((tmp_path / "b.sce").read_bytes())).decode("cp932")
    assert "Hello" in out
    _assert_no_pending_state(tmp_path / "sce_original.manifest.json")
