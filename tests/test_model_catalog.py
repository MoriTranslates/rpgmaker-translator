"""Model suggestion catalog: built-in load, untrusted remote refresh,
tag validation, pull worker safety, installed badge."""

import copy
import json
import os
import sys

import pytest
from qt_helpers import dispose

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from translator import model_catalog as mc  # noqa: E402
from translator.widgets import model_suggestion_dialog as msd  # noqa: E402

REAL_TAGS = [
    "qwen3.5:9b",
    "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q4_K_M",
    "hf.co/mradermacher/translategemma-12b-it-GGUF:Q6_K",
]
BAD_TAGS = ["--insecure", "x; rm -rf", "a b", "", "-x", "a\nb", "a&b", "a|b",
            "$(x)", "a" * 300, "../x", "x`y`", None, 5]


def _good():
    with open(mc.resource_path("models.json"), encoding="utf-8") as f:
        return json.load(f)


# -- tag validation --------------------------------------------------

@pytest.mark.parametrize("tag", REAL_TAGS)
def test_real_tags_valid(tag):
    assert mc.is_valid_tag(tag)


@pytest.mark.parametrize("tag", BAD_TAGS)
def test_bad_tags_invalid(tag):
    assert not mc.is_valid_tag(tag)


# -- built-in catalog ------------------------------------------------

def test_builtin_loads_and_tags_valid():
    cat = mc.load_builtin()
    assert len(cat.entries) >= 10
    assert all(mc.is_valid_tag(e.tag) for e in cat.entries)
    assert cat.pick_for_vram_mb(0) == "qwen3.5:9b"
    assert cat.pick_for_vram_mb(12282, "Russian") == "qwen3.5:9b"
    assert cat.pick_for_vram_mb(16376, "Russian") == "qwen3.5:9b-q8_0"
    # sections: newer-than-3.5 models are only ever experimental
    rec = {e.tag for e in cat.by_section("recommended")}
    assert not any(t.startswith(("qwen3.6", "qwen3.8")) for t in rec)
    assert all(e.notes for e in cat.by_section("experimental"))
    assert cat.by_section("specialist")


def test_builtin_missing_file_falls_back(monkeypatch):
    monkeypatch.setattr(mc, "resource_path", lambda *a: "/nonexistent/x.json")
    cat = mc.load_builtin()
    assert cat.entries and cat.pick_for_vram_mb(0) == "qwen3.5:9b"


def test_models_json_is_bundled_in_spec():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "packaging", "rpgmaker_translator.spec"),
              encoding="utf-8") as f:
        assert '"models.json"' in f.read()


# -- remote fetch ----------------------------------------------------

class _Resp:
    def __init__(self, body=b"", status=200, chunks=None, delay=None):
        self.status_code = status
        self._body = body
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=8192):
        if self._chunks is not None:
            yield from self._chunks
            return
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


def _fetch(resp=None, exc=None, **kw):
    def get(url, **kwargs):
        assert kwargs["timeout"] <= 5 and kwargs["stream"] is True
        if exc:
            raise exc
        return resp
    return mc.fetch_remote_catalog(get=get, **kw)


def _body(data):
    return json.dumps(data).encode()


def test_remote_success_replaces():
    data = _good()
    data["recommended"][0]["label"] = "Remote Qwen"
    cat = _fetch(_Resp(_body(data)))
    assert cat is not None
    assert cat.entries[0].label == "Remote Qwen"


def _mut(fn):
    d = copy.deepcopy(_good())
    fn(d)
    return _body(d)


@pytest.mark.parametrize("body", [
    b"not json at all",
    b"\xff\xfe\x00bad utf8",
    b"[]",
    b'{"schema": 2}',
    _mut(lambda d: d.update(schema=99)),
    _mut(lambda d: d["recommended"][0].update(tag="--insecure")),
    _mut(lambda d: d["recommended"][0].update(tag="x; rm -rf")),
    _mut(lambda d: d["specialist"][0].update(tag="a b")),
    _mut(lambda d: d["experimental"][0].update(tag="-v")),
    _mut(lambda d: d["recommended"][0].update(vram_gb="8")),
    _mut(lambda d: d["recommended"][0].update(vram_gb=True)),
    _mut(lambda d: d["recommended"][0].update(vram_gb=-1)),
    _mut(lambda d: d["recommended"][0].update(label=5)),
    _mut(lambda d: d["recommended"][0].update(label="x" * 500)),
    _mut(lambda d: d["recommended"][0].update(notes="x" * 500)),
    _mut(lambda d: d["recommended"][0].update(label="a\x07b")),
    _mut(lambda d: d["recommended"][0].update(notes="a" + chr(0x2028) + "b")),
    _mut(lambda d: d["specialist"].append(dict(d["recommended"][0]))),  # dup tag
    _mut(lambda d: d.update(tier_picks={"8": "qwen3.5:9b"})),
    _mut(lambda d: d["tier_picks"].update({"16": "not-listed:1b"})),
    _mut(lambda d: d.update(recommended="oops")),
    _mut(lambda d: d.update(recommended=[], specialist=[], experimental=[])),
    _mut(lambda d: d["recommended"].append("string entry")),
])
def test_remote_invalid_falls_back(body):
    assert _fetch(_Resp(body)) is None


def test_remote_oversize_rejected():
    big = _body(_good()) + b" " * (mc.MAX_REMOTE_BYTES + 1)
    assert _fetch(_Resp(big)) is None


def test_remote_oversize_stops_reading():
    seen = []

    def gen():
        for _ in range(100):
            seen.append(1)
            yield b"x" * 8192
    assert _fetch(_Resp(chunks=gen())) is None
    assert len(seen) < 20


@pytest.mark.parametrize("status", [404, 500, 301, 403])
def test_remote_http_error(status):
    assert _fetch(_Resp(_body(_good()), status=status)) is None


def test_remote_timeout_and_network_errors():
    import requests
    for exc in (requests.Timeout("t"), requests.ConnectionError("c"),
                OSError("x"), RuntimeError("y")):
        assert _fetch(exc=exc) is None


def test_remote_slow_body_times_out():
    # Deadline already passed when the first chunk arrives
    resp = _Resp(_body(_good()))
    assert mc.fetch_remote_catalog(
        get=lambda *a, **k: resp, timeout=0) is None
    assert resp.closed


# -- pull worker -----------------------------------------------------

class _FakeProc:
    def __init__(self):
        self.stdout = iter(["pulling manifest\n"])
        self.returncode = 0

    def wait(self):
        pass

    def poll(self):
        return 0


def test_pull_worker_uses_list_args_no_shell(qapp, monkeypatch):
    calls = []

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return _FakeProc()
    monkeypatch.setattr(msd.subprocess, "Popen", fake_popen)
    w = msd._PullWorker("qwen3.5:9b")
    w.run()
    args, kw = calls[0]
    assert args == ["ollama", "pull", "--", "qwen3.5:9b"]
    assert isinstance(args, list) and not kw.get("shell")


@pytest.mark.parametrize("tag", ["--insecure", "x; rm -rf", "a b", ""])
def test_pull_worker_refuses_invalid_tags(qapp, monkeypatch, tag):
    def boom(*a, **k):
        raise AssertionError("Popen must not be called")
    monkeypatch.setattr(msd.subprocess, "Popen", boom)
    w = msd._PullWorker(tag)
    errors = []
    w.error.connect(errors.append)
    w.run()
    assert errors and "Invalid model tag" in errors[0]


# -- dialog ----------------------------------------------------------

@pytest.fixture
def make_dialog(qapp, monkeypatch):
    monkeypatch.setattr(msd, "_detect_gpu", lambda: (None, 0))
    made = []

    def _make(installed, **kw):
        d = msd.ModelSuggestionDialog(installed_models=installed, **kw)
        made.append(d)
        return d
    yield _make
    for d in made:
        dispose(d)


def _status(dlg, tag):
    for table, col in ((dlg._qwen_table, 3), (dlg._table, 3)):
        for i in range(table.rowCount()):
            if table.item(i, 0).data(msd.Qt.ItemDataRole.UserRole) == tag:
                return table.item(i, col).text()
    raise KeyError(tag)


def test_installed_badge_exact_and_latest(make_dialog):
    dlg = make_dialog(["qwen3.5:9b", "qwen3.6"])
    assert _status(dlg, "qwen3.5:9b") == chr(0x2713) + " Installed"
    assert _status(dlg, "qwen3.5:4b") == "Available"
    # untagged installed name == ":latest"
    d2 = make_dialog(["qwen3.5:9b-q8_0:latest"])
    assert d2._is_installed("qwen3.5:9b-q8_0:latest")
    assert msd.normalize_model_tag("foo") == msd.normalize_model_tag("foo:latest")
    d3 = make_dialog(["foo"])
    assert d3._is_installed("foo:latest")
    assert not d3._is_installed("foo:other")


def test_dialog_remote_swap_and_none_keeps_builtin(make_dialog):
    dlg = make_dialog([])
    n = len(dlg._catalog.entries)
    dlg._on_catalog_fetched(None)
    assert len(dlg._catalog.entries) == n
    data = _good()
    data["recommended"][0]["label"] = "Swapped"
    dlg._on_catalog_fetched(mc.parse_catalog(data))
    assert "Swapped" in dlg._qwen_table.item(0, 0).text()
    assert dlg._get_selected_tag() == "qwen3.5:9b"


def test_dialog_typed_tag_validation(make_dialog, monkeypatch):
    dlg = make_dialog([])
    started = []
    monkeypatch.setattr(msd, "_PullWorker",
                        lambda *a, **k: started.append(a) or pytest.fail("no"))
    dlg._cmd_edit.setText("--insecure")
    assert not dlg._pull_btn.isEnabled()
    assert "Invalid model tag" in dlg._pull_status.text()
    dlg._pull_model()
    assert not started
    assert "Invalid model tag" in dlg._pull_status.text()
    dlg._cmd_edit.setText("ollama pull hf.co/foo/bar-GGUF:Q4_K_M")
    assert dlg._get_selected_tag() == "hf.co/foo/bar-GGUF:Q4_K_M"
    assert dlg._pull_btn.isEnabled()


def test_dialog_selects_tier_pick_and_experimental_label(make_dialog):
    dlg = make_dialog([])
    texts = [dlg._qwen_table.item(i, 0).text()
             for i in range(dlg._qwen_table.rowCount())]
    assert any("Qwen3.8 27B (experimental)" in t for t in texts)
    assert "(Recommended)" in texts[0]


# Blind review 2026-11-06 (M5): `ollama pull` output was read with the locale
# code page (cp1252); UTF-8 spinner characters raised UnicodeDecodeError.
def test_pull_worker_survives_utf8_spinner_and_bad_bytes(qapp, monkeypatch):
    import io
    spinner = chr(0x280F).encode("utf-8")
    data = (b"pulling manifest " + spinner + b"\n"
            + b"pulling abc " + chr(0x2819).encode("utf-8") + b" 50%\n"
            + b"bad byte \xff\xfe here\n"
            + b"success\n")

    class _Proc:
        returncode = 0

        def __init__(self, **kw):
            # Decode like subprocess does: the encoding/errors it was given,
            # else the locale code page.
            self.stdout = io.TextIOWrapper(
                io.BytesIO(data), encoding=kw.get("encoding") or "cp1252",
                errors=kw.get("errors") or "strict")

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(msd.subprocess, "Popen",
                        lambda args, **kw: _Proc(**kw))
    w = msd._PullWorker("qwen3.5:9b")
    lines, done, errors = [], [], []
    w.progress.connect(lines.append)
    w.finished_ok.connect(lambda: done.append(1))
    w.error.connect(errors.append)
    w.run()
    assert errors == []
    assert done == [1]
    assert any(chr(0x280F) in ln for ln in lines)
    assert lines[-1] == "success"


# -- target-language picks (Sugoi is English only) ---------------------

SUGOI_IQ4 = "hf.co/mradermacher/Sugoi-14B-Ultra-HF-i1-GGUF:i1-IQ4_XS"
SUGOI_Q4KM = "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q4_K_M"
GB = 1024


def test_picks_by_language_and_vram():
    cat = mc.load_builtin()
    assert cat.pick_for_vram_mb(12 * GB, "English") == SUGOI_IQ4
    assert cat.pick_for_vram_mb(8 * GB, "English") == "qwen3.5:9b"
    assert cat.pick_for_vram_mb(16 * GB, "English") == SUGOI_Q4KM
    assert cat.pick_for_vram_mb(12 * GB, "Russian") == "qwen3.5:9b"
    assert cat.pick_for_vram_mb(16 * GB, "Russian") == "qwen3.5:9b-q8_0"
    assert cat.pick_for_vram_mb(8 * GB, "Russian") == "qwen3.5:9b"
    # default target (no language given) is English
    assert cat.pick_for_vram_mb(12 * GB) == SUGOI_IQ4


def test_sugoi_entries_are_english_only_and_missing_means_all():
    cat = mc.load_builtin()
    by_tag = {e.tag: e for e in cat.entries}
    for tag, e in by_tag.items():
        if "Sugoi" in tag:
            assert e.supports("English") and not e.supports("Russian"), tag
    assert by_tag["qwen3.5:9b"].languages == ()
    assert by_tag["qwen3.5:9b"].supports("Russian")
    assert not any(e.supports("French") for e in cat.entries
                   if "Sugoi" in e.tag)


def test_new_picks_are_all_in_recommended_and_default_is_universal():
    cat = mc.load_builtin()
    rec = {e.tag: e for e in cat.by_section("recommended")}
    for picks in cat.tier_picks_by_language.values():
        assert all(t in rec for t in picks.values())
    assert all(rec[t].languages == () for t in cat.tier_picks.values())


def test_remote_language_pick_for_model_lacking_language_rejected():
    data = _good()
    data["tier_picks_by_language"]["Russian"] = {
        "8": "qwen3.5:9b", "12": SUGOI_IQ4, "16": "qwen3.5:9b"}
    assert _fetch(_Resp(_body(data))) is None
    with pytest.raises(ValueError):
        mc.parse_catalog(data)


def test_remote_language_pick_not_in_recommended_rejected():
    data = _good()
    data["tier_picks_by_language"]["English"]["12"] = (
        "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q2_K")  # specialist only
    with pytest.raises(ValueError):
        mc.parse_catalog(data)


def test_default_tier_pick_must_work_for_every_language():
    data = _good()
    data["tier_picks"]["12"] = SUGOI_IQ4
    with pytest.raises(ValueError):
        mc.parse_catalog(data)


@pytest.mark.parametrize("bad", [
    "English", ["English", 5], [""], [], {"a": 1}, ["x" * 100]])
def test_bad_languages_field_rejected(bad):
    data = _good()
    data["recommended"][0]["languages"] = bad
    with pytest.raises(ValueError):
        mc.parse_catalog(data)


@pytest.mark.parametrize("bad", [
    [], "x", {"English": "qwen3.5:9b"}, {"English": {"8": "qwen3.5:9b"}},
    {"": {"8": "qwen3.5:9b", "12": "qwen3.5:9b", "16": "qwen3.5:9b"}}])
def test_bad_picks_by_language_rejected(bad):
    data = _good()
    data["tier_picks_by_language"] = bad
    with pytest.raises(ValueError):
        mc.parse_catalog(data)


def _old_parse_catalog(data):
    """Copy of the validator in the previous release (no language support):
    ignores unknown keys, needs every tier pick to be in recommended."""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("unsupported schema")
    out, seen = [], set()
    for section in mc.SECTIONS:
        for row in data.get(section, []):
            tag = row.get("tag")
            if not mc.is_valid_tag(tag) or tag.lower() in seen:
                raise ValueError("bad tag")
            seen.add(tag.lower())
            vram = row.get("vram_gb")
            if (isinstance(vram, bool) or not isinstance(vram, (int, float))
                    or not 0 < vram <= 1024):
                raise ValueError("bad vram_gb")
            mc._plain_text(row.get("label"), mc.MAX_LABEL, False)
            mc._plain_text(row.get("notes", ""), mc.MAX_NOTES, True)
            out.append((section, tag))
    if not out or len(out) > mc.MAX_MODELS:
        raise ValueError("bad number of models")
    recommended = {t for s, t in out if s == "recommended"}
    picks = data["tier_picks"]
    for t in mc.TIERS:
        if picks.get(str(t)) not in recommended:
            raise ValueError("pick not recommended")
    return {t: picks[str(t)] for t in mc.TIERS}


def test_new_models_json_passes_previous_release_validator():
    # Older app versions read the same remote file from GitHub master.
    assert _old_parse_catalog(_good()) == {
        8: "qwen3.5:9b", 12: "qwen3.5:9b", 16: "qwen3.5:9b-q8_0"}


def _gpu_dialog(make_dialog, monkeypatch, gb, language):
    monkeypatch.setattr(msd, "_detect_gpu", lambda: ("Test GPU", gb * 1024))
    return make_dialog([], target_language=language)


def _rows(table):
    return [table.item(i, 0) for i in range(table.rowCount())]


def test_dialog_english_12gb_recommends_sugoi_iq4(make_dialog, monkeypatch):
    dlg = _gpu_dialog(make_dialog, monkeypatch, 12, "English")
    assert dlg._recommended == SUGOI_IQ4
    assert dlg._get_selected_tag() == SUGOI_IQ4


def test_dialog_russian_12gb_recommends_qwen_and_no_sugoi(
        make_dialog, monkeypatch):
    dlg = _gpu_dialog(make_dialog, monkeypatch, 12, "Russian")
    assert dlg._recommended == "qwen3.5:9b"
    assert dlg._get_selected_tag() == "qwen3.5:9b"
    tags = [r.data(msd.Qt.ItemDataRole.UserRole)
            for r in _rows(dlg._qwen_table)]
    assert not any("Sugoi" in t for t in tags)
    assert not any("(Recommended)" in r.text() and "Sugoi" in r.text()
                   for r in _rows(dlg._qwen_table) + _rows(dlg._table))
    # Sugoi rows that remain (specialist list) are disabled, never selectable
    sug = _rows(dlg._table)
    assert sug
    for item in sug:
        assert not item.flags() & msd.Qt.ItemFlag.ItemIsEnabled
    assert "English only" in dlg._table.item(0, 3).text()


def test_dialog_english_shows_sugoi_enabled(make_dialog, monkeypatch):
    dlg = _gpu_dialog(make_dialog, monkeypatch, 16, "English")
    assert dlg._recommended == SUGOI_Q4KM
    for item in _rows(dlg._table):
        assert item.flags() & msd.Qt.ItemFlag.ItemIsEnabled


def test_dialog_remote_bad_language_pick_keeps_builtin(
        make_dialog, monkeypatch):
    dlg = _gpu_dialog(make_dialog, monkeypatch, 12, "Russian")
    data = _good()
    data["tier_picks_by_language"]["Russian"] = {
        "8": "qwen3.5:9b", "12": SUGOI_IQ4, "16": "qwen3.5:9b"}
    dlg._on_catalog_fetched(mc.fetch_remote_catalog(
        get=lambda *a, **k: _Resp(_body(data))))
    assert dlg._recommended == "qwen3.5:9b"
