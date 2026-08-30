"""Translation parity checks for Home Assistant config/options flows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "translations"
)


def _flatten(value: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested translation dictionaries to dotted keys."""
    result: dict[str, str] = {}
    for key, item in value.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, dotted))
        else:
            result[dotted] = item
    return result


def _load(locale: str) -> dict:
    """Load one Home Assistant translation file."""
    return json.loads((_TRANSLATIONS_DIR / f"{locale}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "locale",
    [p.stem for p in sorted(_TRANSLATIONS_DIR.glob("*.json")) if p.stem != "en"],
)
def test_options_translation_keys_match_english(locale: str) -> None:
    """Every supported locale must expose every options-flow translation key."""
    english = _flatten(_load("en")["options"])
    translated = _flatten(_load(locale)["options"])

    assert translated.keys() == english.keys(), (
        f"{locale}.json options-flow translation keys differ from en.json; "
        f"missing={sorted(english.keys() - translated.keys())}, "
        f"extra={sorted(translated.keys() - english.keys())}"
    )


def test_greek_options_flow_has_no_english_fallback_strings() -> None:
    """Greek options flow should not silently fall back to English labels."""
    english = _flatten(_load("en")["options"])
    greek = _flatten(_load("el")["options"])

    identical = {
        key
        for key, value in greek.items()
        if key in english and value == english[key]
    }
    assert not identical, f"Greek options strings still fall back to English: {sorted(identical)}"
