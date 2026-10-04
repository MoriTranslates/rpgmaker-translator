"""TranslationProject save/load + patch round trips."""

import json
from dataclasses import asdict

import pytest

from conftest import FAKE_MV_GAME
from translator.project_model import TranslationEntry, TranslationProject


@pytest.fixture
def project():
    from translator.rpgmaker_mv import RPGMakerMVParser
    entries = RPGMakerMVParser().load_project(FAKE_MV_GAME)
    # Exercise every status and every field type
    for i, e in enumerate(entries):
        e.translation = f"EN {i}\nline" if "\n" in e.original else f"EN {i}"
        e.status = ("untranslated", "translated", "reviewed", "skipped")[i % 4]
    return TranslationProject(
        project_path=FAKE_MV_GAME,
        project_type="rpgmaker_mv",
        entries=entries,
        glossary={"村人": "Villager", "アリス": "Alice"},
        actor_genders={1: "female", 2: "male", 3: "unknown"},
    )


def test_save_load_roundtrip_preserves_everything(project, tmp_path):
    path = tmp_path / "sub" / "state.json"
    project.save_state(str(path))
    assert not (tmp_path / "sub" / "state.json.tmp").exists()
    loaded = TranslationProject.load_state(str(path))
    assert loaded.project_path == project.project_path
    assert loaded.project_type == project.project_type
    assert loaded.glossary == project.glossary
    assert loaded.actor_genders == project.actor_genders
    assert all(isinstance(k, int) for k in loaded.actor_genders)
    assert [asdict(e) for e in loaded.entries] == [asdict(e) for e in project.entries]
    # namebox/has_face specifically
    nb = loaded.get_entry_by_id("Map001.json/Ev1(EV001)/p0/dialog_2")
    assert nb.namebox == "\\N<村人>"
    assert loaded.get_entry_by_id("Map001.json/Ev1(EV001)/p0/dialog_1").has_face is True


def test_save_is_utf8_unescaped(project, tmp_path):
    path = tmp_path / "state.json"
    project.save_state(str(path))
    raw = path.read_text(encoding="utf-8")
    assert "アリス" in raw


def test_load_ignores_unknown_fields_and_bad_gender_keys(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "project_path": "x",
        "project_type": "renpy",
        "entries": [{"id": "a", "file": "f", "field": "dialog",
                     "original": "こんにちは", "future_field": 1}],
        "actor_genders": {"1": "female", "abc": "male"},
    }), encoding="utf-8")
    p = TranslationProject.load_state(str(path))
    assert p.entries[0].namebox == ""          # default for missing field
    assert p.actor_genders == {1: "female"}
    assert p.glossary == {}


def test_counts_and_lookup(project):
    assert project.total == len(project.entries)
    assert project.translated_count == sum(
        e.status in ("translated", "reviewed") for e in project.entries)
    assert project.reviewed_count == sum(e.status == "reviewed" for e in project.entries)
    assert "Map001.json" in project.get_files()
    assert all(e.file == "Items.json" for e in project.get_entries_for_file("Items.json"))
    hits = project.search("アリス")
    assert any(e.id == "Actors.json/1/name" for e in hits)


def test_patch_roundtrip(project, tmp_path):
    zp = tmp_path / "patch.zip"
    project.export_patch(str(zp), game_title="Test", patch_version="2.0")
    patch = TranslationProject.import_patch(str(zp))
    exported = [e for e in project.entries if e.status in ("translated", "reviewed")]
    assert [asdict(e) for e in patch.entries] == [asdict(e) for e in exported]
    assert patch.actor_genders == project.actor_genders
    assert patch._patch_metadata["patch_version"] == "2.0"


def test_import_translations_by_id_then_text():
    old = TranslationProject(entries=[
        TranslationEntry("a", "f", "dialog", "はい", "Yes", "reviewed"),
        TranslationEntry("old_b", "f", "dialog", "いいえ", "No", "translated"),
    ])
    new = TranslationProject(entries=[
        TranslationEntry("a", "f", "dialog", "はい"),
        TranslationEntry("b", "f", "dialog", "いいえ"),
        TranslationEntry("c", "f", "dialog", "ありがとう"),
    ])
    stats = new.import_translations(old)
    assert stats == {"by_id": 1, "by_text": 1, "skipped": 0, "new": 1}
    assert [e.translation for e in new.entries] == ["Yes", "No", ""]
    assert new.entries[0].status == "reviewed"


def test_import_patch_tolerates_unknown_entry_fields(tmp_path):
    import zipfile
    zp = tmp_path / "patch.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("patch.json", json.dumps({"entries": [
            {"id": "a", "file": "f", "field": "dialog", "original": "はい",
             "translation": "Yes", "status": "translated", "speaker": "x"}]}))
    p = TranslationProject.import_patch(str(zp))
    assert p.entries[0].translation == "Yes"
