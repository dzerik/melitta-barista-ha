# Contributing to Melitta Barista Smart

Thanks for your interest in contributing! Here's how to get started.

## Reporting Bugs

1. Check [existing issues](https://github.com/dzerik/melitta-barista-ha/issues) first.
2. Open a new issue using the **Bug Report** template.
3. Include your HA version, integration version, machine model, and relevant logs.

## Suggesting Features

Open an issue using the **Feature Request** template with a clear description of the use case.

## Development Setup

```bash
git clone https://github.com/dzerik/melitta-barista-ha.git
cd melitta-barista-ha
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
```

## Running Tests

```bash
python -m pytest tests/ --timeout=10
```

## Code Style

- Python 3.11+ (no PEP 695 `type` statements)
- Linting: `ruff check custom_components/melitta_barista/`
- Logger: always use `logging.getLogger("melitta_barista")`

## Pull Request Process

1. Fork the repository and create a branch: `feat/<name>`, `fix/<name>`, etc.
2. Make your changes and add/update tests where applicable.
3. Ensure all tests pass and `ruff check` reports no errors.
4. Submit a PR with a clear description of the changes.

## Commit Messages

Format: `type: short description`

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `perf`, `test`
