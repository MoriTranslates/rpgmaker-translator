# Contributing

Thanks for your interest in contributing to RPG Maker Translator!

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Create a virtual environment and install dev dependencies:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
4. Run the app: `python main.py`

## Development Setup

- **Python 3.12+** required (CI tests 3.12 and 3.14 on Windows, 3.14 on Linux; releases are built with 3.14)
- **PyQt6** for the GUI
- **Ollama** running locally for translation testing (optional for UI-only changes; the test suite uses a fake LLM)

### Dependency files

| File | Purpose |
|------|---------|
| `requirements.txt` | Runtime deps as compatible ranges. Read by `pyproject.toml`, so this is the one place to add a runtime dependency. |
| `requirements-dev.txt` | Runtime + pytest, ruff, pyinstaller. Mirror changes in `[project.optional-dependencies].dev` in `pyproject.toml`. |
| `requirements-lock.txt` | Exact pins used by the release build. Regenerate after changing either file above: fresh venv → `pip install -r requirements-dev.txt` → `pip freeze` (drop `setuptools`). |

### Tests and lint

```powershell
python -m pytest -q        # headless Qt (QT_QPA_PLATFORM=offscreen is set in tests/conftest.py)
python -m ruff check .     # same check CI runs
```

CI (`.github/workflows/ci.yml`) runs both on every push and pull request to `master`.
Ruff currently enforces only syntax errors and pyflakes bugs (`E9`, `F`, with a few
pre-existing categories ignored in `pyproject.toml`); please don't add new findings.

## How to Contribute

### Bug Reports
- Use the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.md) issue template
- Include steps to reproduce, expected vs actual behavior
- Mention your OS, Python version, and Ollama model if relevant

### Feature Requests
- Use the [Feature Request](.github/ISSUE_TEMPLATE/feature_request.md) issue template
- Describe the use case and why it would be useful

### Pull Requests
- Create a feature branch from `master`
- Keep changes focused — one feature or fix per PR
- Run `python -m pytest -q` and `python -m ruff check .` — CI must be green
- Test that the app launches and basic functionality works
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for user-visible changes
- Follow existing code style (match the patterns you see)

## Project Structure

```
translator/
  ollama_client.py       # LLM API wrapper
  rpgmaker_mv.py         # Game file parser & exporter
  project_model.py       # Data model
  translation_engine.py  # Batch translation worker
  text_processor.py      # Word wrap & plugin analysis
  widgets/               # PyQt6 GUI components
```

## Guidelines

- Keep PRs small and focused
- Don't add dependencies without discussion
- Preserve backward compatibility with existing save states when possible
- Test with at least one RPG Maker MV or MZ project if touching parser/export code

## Releasing (maintainers)

Versions follow [SemVer](https://semver.org/). The version lives in one place: `translator/version.py`.

1. Make sure `master` is green in CI.
2. Bump `__version__` in `translator/version.py` (e.g. `1.1.0`).
3. In `CHANGELOG.md`, rename `## [Unreleased]` content into a new section
   `## [1.1.0] - YYYY-MM-DD` and leave an empty `## [Unreleased]` above it.
4. Commit: `git commit -am "Release v1.1.0"`.
5. Tag and push:
   ```powershell
   git tag -a v1.1.0 -m "v1.1.0"
   git push origin master v1.1.0
   ```
6. `.github/workflows/release.yml` then: checks the tag equals `v` + `__version__` and that the
   CHANGELOG section exists → runs ruff + tests → builds the PyInstaller onedir bundle
   (`packaging/build_windows.ps1`) → smoke-starts the exe → uploads
   `RPGMakerTranslator-1.1.0-win64.zip` + `.sha256` to a GitHub Release whose notes are that
   CHANGELOG section. Tags with a hyphen (`v1.1.0-rc.1`) are published as pre-releases.

To test packaging without releasing, run the **Release** workflow manually (Actions tab →
Release → Run workflow): it builds the zip as a downloadable workflow artifact and skips publishing.
Locally: `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` (output in `dist\`).

If a release build fails, fix it on `master`, delete the tag (`git tag -d v1.1.0; git push origin :refs/tags/v1.1.0`),
and tag again.

## Questions?

Open an issue or start a discussion. We're happy to help!
