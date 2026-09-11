"""Shared machinery for the "value still reads English" locale pins.

Key parity tests (`test_i18n_parity.py`, `test_config_translation_parity.py`,
`test_ui_contract_i18n_assets.py`) prove that every locale *has* every key.
They cannot see the failure mode that shipped for months and that a user
reported: the key is present, but its value is still the English source
string, so a Russian user reads "Total beverages" and a Dutch user reads the
whole connection-repair flow in English.

This module carries the shared loaders plus the allowlist of values that are
allowed to be byte-identical to English. The pins themselves live next to the
key-parity tests of the family they guard:

  translations/            -> tests/test_config_translation_parity.py
  ui_strings/              -> tests/test_ui_contract_i18n_assets.py
  www/i18n/locales/*.js    -> tests/test_i18n_parity.py

HOW TO EXTEND THE ALLOWLIST
---------------------------
There are exactly three reasons a locale may keep the English string, and
each has its own table below. Add to the one that actually applies, and add
the *narrowest* entry that works — a key, or a (locale, value) pair — never a
blanket prefix:

1. `LATIN_KEYS` — the value is a proper name that stays in Latin script in
   every language by house convention: drink names (Espresso, Latte
   Macchiato, Café Crème), spirits (Amaretto, Baileys), botanical names
   (Arabica, Robusta), brands and product features (Melitta Barista,
   ESPHome BLE proxy, Direct Key), bare tokens ("ID", "1", "~{sec}s").
   A generic noun is NOT a proper name: Coffee, Milk, Hot water, Cream,
   Milk froth and friends must be translated and are deliberately absent
   here. The spoken-narration files are a separate, fully translated family
   and are not covered by these pins.

2. `CONTRACT_FROZEN_TRANSLATION_KEYS` — the string is not free text: Home
   Assistant derives the entity_id from the entity name, and the UI Contract
   publishes that id as `entity.entity_suffix`. Translating those names
   would give every language a different entity_id and break the contract.
   Derived from the Nivona descriptor tables so it cannot drift; pinned
   independently by `test_anchored_nivona_names_slug_equal_across_all_29_locales`.

3. `SHARED_WITH_ENGLISH` — the English word simply IS the correct word in
   that language ("Filter" in German, "Aroma" in Italian, "No" in Spanish).
   Entries are per locale and per exact English value, so leaving "Filter"
   untranslated is fine in German and still fails in Hungarian, where it is
   "Szűrő". Only add a value here if it is genuinely what a native speaker
   writes — not because translating it looked hard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from custom_components.melitta_barista.brands.nivona import NivonaProfile


COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "melitta_barista"
)
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"
UI_STRINGS_DIR = COMPONENT_DIR / "ui_strings"
PANEL_LOCALES_DIR = COMPONENT_DIR / "www" / "i18n" / "locales"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def flatten(value, prefix: str = "") -> dict[str, str]:
    """Flatten a nested translation mapping to dot-joined keys."""
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(flatten(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, str):
        result[prefix] = value
    return result


def load_json_locale(path: Path) -> dict[str, str]:
    """Return {dotted key: string} for a translations/ or ui_strings/ file."""
    return flatten(json.loads(path.read_text(encoding="utf-8")))


_JS_KEY = re.compile(r'^[ \t]*"([^"\\]+)"[ \t]*:', re.MULTILINE)
_JS_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def load_js_locale(path: Path) -> dict[str, str]:
    """Return {key: string} for a panel locale ES module.

    Python cannot import ESM, so this parses the literal: every entry starts
    with `"key":` at the start of a line, and its value runs to the next such
    line. A handful of long values are written as `"a " + "b"` across several
    lines, so the value is the concatenation of every string literal in that
    span. Matches the key contract `test_i18n_parity.py` already relies on.
    """
    text = path.read_text(encoding="utf-8")
    entries: dict[str, str] = {}
    matches = list(_JS_KEY.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].rstrip().rstrip(",").rstrip()
        entries[match.group(1)] = "".join(
            json.loads(f'"{part}"') for part in _JS_STRING.findall(body)
        )
    return entries


# ---------------------------------------------------------------------------
# 1. Proper names that stay Latin in every locale (see module docstring)
# ---------------------------------------------------------------------------

_DRINK_SLUGS = (
    "espresso", "espresso_doppio", "espresso_macchiato",
    "ristretto", "ristretto_doppio",
    "lungo", "americano", "americano_extra", "long_black",
    "red_eye", "black_eye", "dead_eye",
    "cafe_creme", "cafe_creme_doppio", "cafe_au_lait",
    "cappuccino", "caffe_latte", "flat_white",
    "latte_macchiato", "latte_macchiato_extra", "latte_macchiato_triple",
)

LATIN_KEYS: dict[str, frozenset[str]] = {
    "translations": frozenset(
        # The recipe select lists the machine's drink catalogue by name.
        [f"entity.select.recipe.state.{slug}" for slug in _DRINK_SLUGS]
        + [
            # Counter sensors named after a drink. `entity.sensor.coffee`,
            # `.milk`, `.hot_water`, `.cream`… are generic and are NOT here.
            "entity.sensor.espresso.name",
            "entity.sensor.lungo.name",
            "entity.sensor.americano.name",
            "entity.sensor.cappuccino.name",
            "entity.sensor.caffe_latte.name",
            "entity.sensor.macchiato.name",
            "entity.sensor.latte_macchiato.name",
            # "My coffee" is the machine's own menu label for the user recipe
            # slot; most locales keep it, a few translate it. Both are fine.
            "entity.sensor.my_coffee.name",
        ]
    ),
    "ui_strings": frozenset(
        [f"recipes.name.{slug}" for slug in _DRINK_SLUGS]
        + [
            "recipes.category.espresso",
            "recipes.category.my_coffee",
            "values.directkey_category.espresso",
            "values.directkey_category.cappuccino",
            "values.directkey_category.cafe_creme",
            "values.directkey_category.latte_macchiato",
            # Liqueurs are brand/product names.
            "sommelier.liqueur.amaretto",
            "sommelier.liqueur.baileys",
            "sommelier.liqueur.frangelico",
            "sommelier.liqueur.kahlua",
            # Botanical species names; Cyrillic/Greek locales transliterate
            # them, Latin-script ones keep them verbatim.
            "sommelier.bean_type.arabica",
            "sommelier.bean_type.robusta",
            "sommelier.bean_type.arabica_robusta",
            # Loanword food name, spelled the same wherever it is used.
            "sommelier.topping.marshmallow",
            # Feature names the whole stack uses untranslated (the
            # brew_directkey / brew_freestyle actions).
            "actions.brew_directkey.label",
            "actions.brew_freestyle.label",
            # Space-constrained tokens on the machine's own display: bare
            # digits and the Std / Int+ / Med abbreviations.
            "values.shots.one",
            "values.shots.two",
            "values.shots.three",
            "values.aroma.standard",
            "values.aroma.intense",
            "values.intensity.medium",
        ]
    ),
    "panel": frozenset(
        [f"recipes.cat.{slug}" for slug in _DRINK_SLUGS]
        + [
            "panel.title",          # "Melitta Barista" — the brand.
            "tabs.sommelier",       # Feature name, kept in most locales.
            "sommelier.title",      # "AI Sommelier" — same feature name.
            "recipes.id",           # "ID".
            "diag.proxy_remote",    # "ESPHome BLE proxy" — product names.
            "wizard.machine.estimated",  # "~{sec}s" — a format token.
        ]
    ),
}


# ---------------------------------------------------------------------------
# 2. Names frozen because Home Assistant derives the entity_id from them
# ---------------------------------------------------------------------------

def _frozen_translation_keys() -> frozenset[str]:
    """Entity-name keys the UI Contract anchors, so they must stay English.

    slugify(name) has to equal the descriptor key in every locale (see
    UI Contract §9.1.2.1); translating them would hand every language its own
    entity_id and desynchronise `entity.entity_suffix` in the contract.
    """
    profile = NivonaProfile()
    return frozenset(
        "entity.{}.{}.name".format(
            "select" if descriptor.options else "number", descriptor.key
        )
        for family_key in profile.families
        for descriptor in profile.capabilities_for(family_key).settings
    )


CONTRACT_FROZEN_TRANSLATION_KEYS = _frozen_translation_keys()


# ---------------------------------------------------------------------------
# 3. English values that are also the correct word in a given language
# ---------------------------------------------------------------------------
# Per locale, the exact English strings a native speaker writes unchanged.
# Everything here was reviewed one by one; a word is listed for a language
# only when that language really spells it the same way — which is why
# "Filter" appears for German and Dutch but not for Hungarian or Czech.

SHARED_WITH_ENGLISH: dict[str, frozenset[str]] = {
    "bs": frozenset({
        "Aroma", "Bluetooth adapter", "Filter", "LLM model", "Model", "Status",
    }),
    "cs": frozenset({"1 shot", "Aroma", "Firmware", "Model", "Topping"}),
    "da": frozenset({
        "1 shot", "2 shots", "3 shots", "Aroma", "Auto", "Citrus", "Dessert",
        "Filter", "Firmware", "Mild", "Model", "Normal", "Note",
        "Portion (ml)", "Prompt", "Shots", "Standard", "Status", "System",
        "Topping", "Toppings", "Transport", "Type", "Variant",
    }),
    "de": frozenset({
        "Aroma", "Auto", "Dessert", "Filter", "Firmware", "Mild", "Name",
        "Normal", "Portion (ml)", "Shots", "Single Origin", "Snapshots",
        "Standard", "Status", "Syntax", "System", "Topping", "Toppings",
        "Transport", "Vegan", "Website",
    }),
    "es": frozenset({
        "Aroma", "Auto", "Chocolate", "Control", "Error", "Firmware",
        "Floral", "No", "Normal", "Topping", "Toppings",
    }),
    "et": frozenset({
        "1 shot", "Filter", "Transport", "Variant", "Vegan",
    }),
    "fr": frozenset({
        "Auto", "Caramel", "Composition", "Description", "Dessert",
        "Diagnostics", "Firmware", "Floral", "Intense", "Liqueur", "Mug",
        "Note", "Notes", "Portion (ml)", "Standard", "Transport", "Type",
    }),
    "hr": frozenset({
        "1 shot", "Aroma", "Bluetooth adapter", "Filter", "Firmware",
        "Model", "Status",
    }),
    "hu": frozenset({"Aroma", "Firmware"}),
    "it": frozenset({
        "1 shot", "Aroma", "Auto", "Dessert", "Firmware", "Mug", "No",
        "Preset", "Standard", "Topping",
    }),
    "lv": frozenset({"Process"}),
    "nb": frozenset({
        "1 shot", "2 shots", "3 shots", "Aroma", "Auto", "Dessert", "Filter",
        "Mild", "Normal", "Shots", "Standard", "Status", "System", "Topping",
        "Transport", "Type", "Variant",
    }),
    "nl": frozenset({
        "1 shot", "2 shots", "3 shots", "Aroma", "Citrus", "Component 1",
        "Component 2", "Dessert", "Filter", "Firmware", "Hard", "Machine:",
        "Medium", "Mild", "Model", "Placeholders", "Shots", "Status",
        "Topping", "Toppings", "Transport", "Type", "Variant", "Water",
        "Website",
    }),
    "pl": frozenset({
        "1 shot", "Auto", "Model", "Preset", "System", "Topping", "Transport",
    }),
    "pt": frozenset({
        "Aroma", "Chocolate", "Firmware", "Floral", "Normal",
    }),
    "ro": frozenset({
        "Auto", "Caramel", "Control", "Firmware", "Floral", "Model", "Normal",
        "Romantic", "Standard", "Topping", "Transport", "Vegan",
    }),
    "sk": frozenset({
        "1 shot", "Auto", "Filter", "Model", "Syntax", "Variant",
    }),
    "sl": frozenset({"1 shot", "Aroma", "Filter", "Model"}),
    "sv": frozenset({
        "1 shot", "2 shots", "3 shots", "Auto", "Citrus", "Dessert", "Filter",
        "Firmware", "Mild", "Normal", "Portion (ml)", "Process", "Shots",
        "Standard", "Status", "Syntax", "System", "Topping", "Toppings",
        "Transport", "Variant",
    }),
    "tr": frozenset({"1 shot", "Aroma", "Model", "Normal", "Vegan"}),
}


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------

def english_fallbacks(
    family: str,
    locale: str,
    english: dict[str, str],
    translated: dict[str, str],
) -> list[str]:
    """Keys whose value is still the English string without a justification.

    `family` selects the LATIN_KEYS table ("translations", "ui_strings" or
    "panel"); the (locale, value) allowlist is shared across families, since a
    word German spells like English spells the same way wherever it appears.
    """
    allowed_keys = LATIN_KEYS[family]
    if family == "translations":
        allowed_keys = allowed_keys | CONTRACT_FROZEN_TRANSLATION_KEYS
    shared_values = SHARED_WITH_ENGLISH.get(locale, frozenset())

    return sorted(
        key
        for key, value in translated.items()
        if key in english
        and value == english[key]
        and key not in allowed_keys
        and value not in shared_values
    )
