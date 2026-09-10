"""Parity checks for the machine-event entity and device-trigger translations.

The event entity (``event.py``) and the device triggers (``device_trigger.py``)
both render their labels through Home Assistant's translation files, so a locale
that is missing one of these blocks shows the raw slug (``brew_started``) in the
automation editor. These tests pin the full key set in all 29 files.

Note: these are the ENTITY and TRIGGER names of the HA UI. The spoken narration
sentences live in ``narration_strings/`` and are checked separately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.melitta_barista.lifecycle import EVENT_TYPES

_COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "melitta_barista"
)
_TRANSLATIONS_DIR = _COMPONENT_DIR / "translations"
_LOCALES = sorted(p.stem for p in _TRANSLATIONS_DIR.glob("*.json"))

_EXPECTED_LOCALE_COUNT = 29


def _load(locale: str) -> dict:
    """Load one Home Assistant translation file."""
    return json.loads(
        (_TRANSLATIONS_DIR / f"{locale}.json").read_text(encoding="utf-8")
    )


def _event_states(payload: dict) -> dict[str, str]:
    """Return the entity.event.machine_event event_type state map of one file."""
    return payload["entity"]["event"]["machine_event"]["state_attributes"][
        "event_type"
    ]["state"]


def test_locale_inventory_is_complete() -> None:
    """All 29 shipped locales are covered by the checks below."""
    assert len(_LOCALES) == _EXPECTED_LOCALE_COUNT, _LOCALES
    assert "en" in _LOCALES


@pytest.mark.parametrize("locale", _LOCALES)
def test_event_entity_block_present(locale: str) -> None:
    """Every locale names the event entity and all six event types."""
    machine_event = _load(locale)["entity"]["event"]["machine_event"]

    assert machine_event["name"].strip()
    assert list(_event_states(_load(locale))) == EVENT_TYPES
    for event_type, label in _event_states(_load(locale)).items():
        assert isinstance(label, str) and label.strip(), event_type


@pytest.mark.parametrize("locale", _LOCALES)
def test_device_trigger_block_present(locale: str) -> None:
    """Every locale labels all six device triggers."""
    trigger_type = _load(locale)["device_automation"]["trigger_type"]

    assert list(trigger_type) == EVENT_TYPES
    for event_type, label in trigger_type.items():
        assert isinstance(label, str) and label.strip(), event_type


@pytest.mark.parametrize("locale", _LOCALES)
def test_trigger_labels_match_event_labels(locale: str) -> None:
    """A trigger and its event read the same in the UI — one vocabulary, not two."""
    payload = _load(locale)

    assert payload["device_automation"]["trigger_type"] == _event_states(payload)


def test_english_translation_matches_strings_json() -> None:
    """en.json mirrors strings.json, which is the normative English source."""
    strings = json.loads((_COMPONENT_DIR / "strings.json").read_text(encoding="utf-8"))
    english = _load("en")

    assert english["entity"]["event"] == strings["entity"]["event"]
    assert english["device_automation"] == strings["device_automation"]


@pytest.mark.parametrize("locale", [loc for loc in _LOCALES if loc != "en"])
def test_non_english_locales_are_actually_translated(locale: str) -> None:
    """No locale silently ships the English labels as a fallback."""
    english = _event_states(_load("en"))
    translated = _event_states(_load(locale))

    identical = {
        key for key, value in translated.items() if value == english[key]
    }
    assert not identical, f"{locale}.json reuses English labels for {sorted(identical)}"
