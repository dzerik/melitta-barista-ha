"""Tests for the `ui_strings/` i18n asset files (UI Contract §6.3.3/§6.3.4, Zone I-F).

Enforces the asymmetric completeness rules: `en.json` must key every
token the contract builders can emit and carry no orphan keys; the other
28 locales are validated as key-subsets of `en.json` only (sparse locales
are legitimate — the WS loader overlays English per key).

Builder token constants are imported read-only (the dependency points
Zone I-E → Zone I-F, per the §8.3 plan).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.melitta_barista import BREW_DIRECTKEY_SCHEMA
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.brands.nivona._family_8000 import (
    RECIPES_8000_CHILLED,
)
from custom_components.melitta_barista.const import MachineType
from custom_components.melitta_barista.ui_contract import (
    FREESTYLE_AROMA_TOKENS,
    FREESTYLE_BLEND_TOKENS,
    FREESTYLE_INTENSITY_TOKENS,
    FREESTYLE_PROCESS_TOKENS,
    FREESTYLE_SHOTS_TOKENS,
    FREESTYLE_TEMPERATURE_TOKENS,
    MELITTA_RECIPE_NAME_KEYS,
    STATUS_INFO_MESSAGE_TOKENS,
    STATUS_MANIPULATION_TOKENS,
    STATUS_PROCESS_TOKENS,
    STATUS_SUB_PROCESS_TOKENS,
    build_action_catalog,
    build_capabilities_block,
)

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "melitta_barista"
UI_STRINGS_DIR = COMPONENT_DIR / "ui_strings"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"

# The five §3.3 contract recipe-category tokens (§6.3.4 `recipes.category`
# row; the panel's 7 DirectKey categories are a different set).
CONTRACT_RECIPE_CATEGORIES = ("espresso", "coffee", "milk_drink", "water", "my_coffee")

# §6.3.4: exactly these eight actions ship an `actions.<token>.description`
# (ported from the card bundles); all other actions ship without one.
DESCRIBED_ACTIONS = frozenset({
    "easy_clean", "intensive_clean", "descaling", "evaporating",
    "filter_insert", "filter_replace", "filter_remove", "switch_off",
})

# §6.2.3 known group render order.
KNOWN_GROUPS = ("brew", "control", "cleaning", "filter", "power", "danger")


class _FakeClient:
    """Duck-typed contract-input stand-in (Melitta TS: all 16 actions served)."""

    def __init__(self):
        profile = MelittaProfile()
        self.brand = profile
        self.capabilities = profile.capabilities_for("barista_ts")
        self.machine_type = MachineType.BARISTA_TS
        self.connected = True
        self.status = None
        self.recipe_cache_generation = 0
        self.brand_logo_url = None
        self.integration_version = "0.92.0"


def _action_catalog():
    """The full 16-entry §6.2.2 catalog (brand-independent token/group set)."""
    client = _FakeClient()
    return build_action_catalog(client, build_capabilities_block(client))


def _directkey_tokens():
    """The 7 DirectKey category tokens, read from the live service schema."""
    for key, validator in BREW_DIRECTKEY_SCHEMA.schema.items():
        if str(key) == "category":
            return list(validator.container)
    raise AssertionError("BREW_DIRECTKEY_SCHEMA has no 'category' key")


def _all_descriptors():
    """Every RecipeDescriptor of every registered family, plus 8000 chilled."""
    descriptors = []
    for profile in (MelittaProfile(), NivonaProfile()):
        for caps in profile.families.values():
            descriptors.extend(getattr(caps, "recipes", ()) or ())
    descriptors.extend(RECIPES_8000_CHILLED)
    return descriptors


def _required_en_keys():
    """Every key the contract builders can emit a token for (§6.3.4)."""
    keys = set()
    for token in STATUS_PROCESS_TOKENS:
        keys.add(f"status.process.{token}")
    for token in STATUS_SUB_PROCESS_TOKENS:
        keys.add(f"status.sub_process.{token}")
    for token in STATUS_MANIPULATION_TOKENS:
        keys.add(f"status.manipulation.{token}")
    for token in STATUS_INFO_MESSAGE_TOKENS:
        keys.add(f"status.info_message.{token}")
    value_families = {
        "process": FREESTYLE_PROCESS_TOKENS,
        "intensity": FREESTYLE_INTENSITY_TOKENS,
        "aroma": FREESTYLE_AROMA_TOKENS,
        "temperature": FREESTYLE_TEMPERATURE_TOKENS,
        "shots": FREESTYLE_SHOTS_TOKENS,
        "blend": FREESTYLE_BLEND_TOKENS,
    }
    for family, tokens in value_families.items():
        for token in tokens:
            keys.add(f"values.{family}.{token}")
    for token in _directkey_tokens():
        keys.add(f"values.directkey_category.{token}")
    for name_key in MELITTA_RECIPE_NAME_KEYS.values():
        keys.add(f"recipes.name.{name_key}")
    for descriptor in _all_descriptors():
        if descriptor.name_key:
            keys.add(f"recipes.name.{descriptor.name_key}")
    for category in CONTRACT_RECIPE_CATEGORIES:
        keys.add(f"recipes.category.{category}")
    catalog = _action_catalog()
    for entry in catalog:
        keys.add(f"actions.{entry['action']}.label")
        keys.add(f"actions._groups.{entry['group']}")
    for group in KNOWN_GROUPS:
        keys.add(f"actions._groups.{group}")
    return keys


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _locale_names(directory: Path) -> set[str]:
    return {path.stem for path in directory.glob("*.json")}


@pytest.fixture(scope="module")
def en_strings():
    """The English asset map (the completeness reference)."""
    return _load(UI_STRINGS_DIR / "en.json")


def _assert_flat_string_map(data, label):
    assert isinstance(data, dict), f"{label}: top level must be an object"
    for key, value in data.items():
        assert isinstance(key, str) and key, f"{label}: bad key {key!r}"
        assert "." in key, f"{label}: key {key!r} is not dot-joined"
        assert isinstance(value, str) and value.strip(), (
            f"{label}: value for {key!r} must be a non-empty string"
        )


def test_locale_set_matches_translations():
    """ui_strings/ ships exactly the 29 locales of translations/ (§6.3.3)."""
    assert _locale_names(UI_STRINGS_DIR) == _locale_names(TRANSLATIONS_DIR)
    assert len(_locale_names(UI_STRINGS_DIR)) == 29


def test_en_is_flat_string_map(en_strings):
    _assert_flat_string_map(en_strings, "en.json")


def test_en_complete(en_strings):
    """§6.3.3: every builder-emittable token has an en.json key."""
    missing = _required_en_keys() - set(en_strings)
    assert not missing, f"en.json is missing keys: {sorted(missing)}"


def test_en_no_orphans(en_strings):
    """§6.3.3: en.json carries no keys outside the derived keyspace."""
    allowed = _required_en_keys() | {
        f"actions.{action}.description" for action in DESCRIBED_ACTIONS
    }
    orphans = set(en_strings) - allowed
    assert not orphans, f"en.json has orphan keys: {sorted(orphans)}"


def test_en_descriptions_are_exactly_the_eight(en_strings):
    """§6.3.4: the 8 ported descriptions ship; other actions have none."""
    described = {
        key.split(".")[1]
        for key in en_strings
        if key.startswith("actions.") and key.endswith(".description")
    }
    assert described == set(DESCRIBED_ACTIONS)


def test_values_intensity_exactly_server_tokens(en_strings):
    """§6.3.4: `values.intensity` has the 5 server tokens; no `extra_strong`."""
    intensity = {
        key.removeprefix("values.intensity.")
        for key in en_strings
        if key.startswith("values.intensity.")
    }
    assert intensity == set(FREESTYLE_INTENSITY_TOKENS)
    assert "extra_strong" not in intensity


def test_key_casing_pinned(en_strings):
    """§6.3.1: keys embed tokens byte-equal — UPPER for status, lower elsewhere."""
    assert "status.process.READY" in en_strings
    assert "status.manipulation.MOVE_CUP_TO_FROTHER" in en_strings
    assert "status.info_message.FILL_BEANS_1" in en_strings
    assert "values.intensity.very_mild" in en_strings
    assert "actions.switch_off.label" in en_strings
    assert "actions._groups.danger" in en_strings


def test_authored_info_message_english_pinned(en_strings):
    """The §6.3.4 authored English info-message strings, verbatim."""
    assert en_strings["status.info_message.FILL_BEANS_1"] == "Fill bean hopper 1"
    assert en_strings["status.info_message.FILL_BEANS_2"] == "Fill bean hopper 2"
    assert en_strings["status.info_message.EASY_CLEAN"] == "Easy Clean recommended"
    assert en_strings["status.info_message.POWDER_FILLED"] == "Ground coffee filled"
    assert (
        en_strings["status.info_message.PREPARATION_CANCELLED"]
        == "Preparation cancelled"
    )


def test_every_nivona_name_key_covered(en_strings):
    """§6.3.6: every registered family's descriptor name_key resolves in en."""
    for caps in NivonaProfile().families.values():
        for descriptor in caps.recipes:
            assert descriptor.name_key, f"{descriptor.name!r} has no name_key"
            assert f"recipes.name.{descriptor.name_key}" in en_strings
    for descriptor in RECIPES_8000_CHILLED:
        assert descriptor.name_key, f"{descriptor.name!r} has no name_key"
        assert f"recipes.name.{descriptor.name_key}" in en_strings


def test_every_melitta_name_key_covered(en_strings):
    """All 24 Melitta name_keys resolve in en (§6.3.4 `recipes.name` row)."""
    assert len(MELITTA_RECIPE_NAME_KEYS) == 24
    for name_key in MELITTA_RECIPE_NAME_KEYS.values():
        assert f"recipes.name.{name_key}" in en_strings


@pytest.mark.parametrize(
    "locale",
    sorted(_locale_names(TRANSLATIONS_DIR) - {"en"}),
)
def test_locale_is_key_subset_of_en(locale, en_strings):
    """§6.3.3: non-en locales may be sparse but never invent keys."""
    data = _load(UI_STRINGS_DIR / f"{locale}.json")
    _assert_flat_string_map(data, f"{locale}.json")
    extra = set(data) - set(en_strings)
    assert not extra, f"{locale}.json has keys absent from en.json: {sorted(extra)}"
