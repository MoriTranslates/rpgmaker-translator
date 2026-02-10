# Contributing

Thanks for your interest in contributing to RPG Maker Translator!

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Install dependencies: `pip install -r requirements.txt`
4. Run the app: `python main.py`

## Development Setup

- **Python 3.10+** required
- **PyQt6** for the GUI
- **Ollama** running locally for translation testing (optional for UI-only changes)

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
- Test that the app launches and basic functionality works
- Follow existing code style (no linter enforced, just match the patterns you see)

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

## Questions?

Open an issue or start a discussion. We're happy to help!
