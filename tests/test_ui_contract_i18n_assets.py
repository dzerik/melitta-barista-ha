"""Tests for the `ui_strings/` i18n asset files (UI Contract §6.3.3/§6.3.4, Zone I-F).

Enforces the completeness rules: `en.json` must key every token the
contract builders can emit and carry no orphan keys, and the other 28
locales must key exactly the served keyspace — subset (never invent a
key, §6.3.3) *and* superset (§6.3.7(b): complete over everything the
server serves, minus the explicitly reviewed `SPARSE_EXEMPT_KEYS`, which
is where §6.3.3's sparse allowance is invoked for future single-key
additions riding the loader's per-key en overlay). Placeholder spans are
compared against `en.json` in every locale (§6.3.7(a)).

Builder token constants are imported read-only (the dependency points
Zone I-E → Zone I-F, per the §8.3 plan).

Extended for v3 (Zone I-L, §9.1.4/§9.2.5): the `settings.*` and
`sommelier.*` domains — every emittable setting token, group, and vocab
token must resolve in `en.json`, with level/option tokens resolvable via
the §9.1.4 chain (per-setting `settings.<setting>.levels.<token>` OR the
shared `settings._levels.<token>` tier).

Extended for the 0.94 amendment (§6.3.7): the machine-domain families
moved off the three clients — `wizard.*` (brew-guide vocabulary, key
names byte-equal to the PWA bundle), `status.process.<TOKEN>.description`
/ `status.sub_process.<TOKEN>.description`, `sommelier.error.<code>` and
the five free-form suggestion label families. Status/description tokens
come from the builder constants read-only; the suggestion token lists are
pinned here explicitly, because those fields stay free-form (§9.2.4) and
have no server-side enumeration to import.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.melitta_barista import BREW_DIRECTKEY_SCHEMA
from custom_components.melitta_barista.brands.melitta import MelittaProfile
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.brands.nivona._family_8000 import (
    RECIPES_8000_CHILLED,
)
from custom_components.melitta_barista.const import MachineType
from custom_components.melitta_barista.sommelier_api import build_sommelier_vocab
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
    build_settings_block,
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

# §9.1.3 known settings groups (`settings._groups.*` — a keyspace separate
# from `actions._groups.*`).
SETTINGS_KNOWN_GROUPS = ("brew", "water", "power", "system")

# §9.1.4: exactly these six settings ship a `settings.<setting>.description`
# (ported from the PWA locales, en/de/ru); all other settings ship none.
DESCRIBED_SETTINGS = frozenset({
    "energy_saving", "auto_bean_select", "rinsing_disabled",
    "water_hardness", "auto_off_after", "brew_temperature",
})

# §6.3.7: the 29 brew-guide keys, with the placeholder set each one must
# carry. Key names and placeholder names are byte-equal to the PWA bundle
# (`src/locales/en.json`, `wizard.` prefix) — a rename here silently
# breaks every client that already ships these keys as its tier-2 bundle.
WIZARD_KEYS: dict[str, frozenset[str]] = {
    "wizard.title": frozenset(),
    "wizard.step_of": frozenset({"{n}", "{m}"}),
    "wizard.resumed": frozenset(),
    "wizard.restart": frozenset(),
    "wizard.step.cup": frozenset({"{cup}", "{ml}"}),
    "wizard.step.done": frozenset(),
    "wizard.step.machine": frozenset(),
    "wizard.step.machine_n": frozenset({"{n}", "{m}"}),
    "wizard.machine.start": frozenset(),
    "wizard.machine.start_full": frozenset(),
    "wizard.machine.retry": frozenset(),
    "wizard.machine.skip": frozenset(),
    "wizard.machine.failed": frozenset(),
    "wizard.machine.waiting": frozenset(),
    "wizard.machine.estimated": frozenset({"{sec}"}),
    "wizard.machine.im_done": frozenset(),
    "wizard.machine.during_hint": frozenset(),
    "wizard.machine.prompt": frozenset({"{prompt}"}),
    "wizard.machine.prompt_generic": frozenset(),
    "wizard.machine.confirm": frozenset(),
    "wizard.machine.confirm_manual": frozenset(),
    "wizard.machine.confirm_failed": frozenset(),
    "wizard.finish.title": frozenset(),
    "wizard.finish.message": frozenset(),
    "wizard.finish.button": frozenset(),
    "wizard.close.title": frozenset(),
    "wizard.close.message": frozenset(),
    "wizard.close.stay": frozenset(),
    "wizard.close.leave": frozenset(),
}

# §6.3.7: the only placeholder names any served string may use.
KNOWN_PLACEHOLDERS = frozenset({"{n}", "{m}", "{cup}", "{ml}", "{sec}", "{prompt}"})

# §6.3.7: `status.sub_process.<TOKEN>.description` ships only where the
# sentence adds meaning over the label — WATER ("Dispensing Water") does
# not, so it stays label-only. Process descriptions ship for all 12.
DESCRIBED_SUB_PROCESSES = frozenset({"GRINDING", "COFFEE", "STEAM", "PREPARE"})

# §6.3.7: the five sommelier LLM pre-flight codes.
SOMMELIER_ERROR_CODES = ("no_llm_agent", "no_llm_agent_selected",
                         "llm_agent_missing", "timeout", "unauthorized")

# §6.3.7 / §9.2.4: labels for the well-known values of fields that stay
# FREE-FORM. Pinned here, not imported: there is deliberately no
# server-side enumeration, `vocab/get` does not serve these families, and
# unknown user text must render verbatim. The lists may only grow.
SUGGESTION_TOKENS: dict[str, tuple[str, ...]] = {
    "milk": ("regular", "whole", "skim", "oat", "almond", "soy",
             "coconut", "cream"),
    "syrup": ("vanilla", "caramel", "hazelnut", "chocolate", "maple",
              "lavender", "peppermint"),
    "topping": ("cinnamon_powder", "whipped_cream", "cocoa_powder",
                "marshmallow", "caramel_drizzle"),
    "liqueur": ("baileys", "kahlua", "amaretto", "frangelico"),
    "note": ("chocolate", "nutty", "fruity", "floral", "caramel", "spicy",
             "earthy", "honey", "berry", "citrus"),
}


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


def _all_settings_blocks():
    """Every §9.1 settings block any (family, machine_type) can produce."""
    melitta, nivona = MelittaProfile(), NivonaProfile()
    blocks = []
    for family in melitta.families:
        for machine_type in (MachineType.BARISTA_TS, MachineType.BARISTA_T, None):
            blocks.append(
                build_settings_block(
                    melitta.capabilities_for(family), machine_type, melitta,
                )
            )
    for family in nivona.families:
        blocks.append(
            build_settings_block(nivona.capabilities_for(family), None, nivona)
        )
    return blocks


def _emittable_settings():
    """(setting tokens, groups, (setting, level/option token) pairs) union."""
    tokens: set[str] = set()
    groups: set[str] = set()
    level_pairs: set[tuple[str, str]] = set()
    for block in _all_settings_blocks():
        for entry in block:
            tokens.add(entry["setting"])
            groups.add(entry["group"])
            for level in entry.get("levels", ()):
                if level["token"]:
                    level_pairs.add((entry["setting"], level["token"]))
            for option in entry.get("options", ()):
                if option["token"]:
                    level_pairs.add((entry["setting"], option["token"]))
    return tokens, groups, level_pairs


def _vocab_keys():
    """Every `sommelier.<family>.<token>` key the vocab can emit (§9.2.5)."""
    return {
        f"sommelier.{family}.{token}"
        for family, spec in build_sommelier_vocab().items()
        for token in spec["tokens"]
    }


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
    setting_tokens, setting_groups, _level_pairs = _emittable_settings()
    for token in setting_tokens:
        keys.add(f"settings.{token}.label")
    for group in setting_groups | set(SETTINGS_KNOWN_GROUPS):
        keys.add(f"settings._groups.{group}")
    keys |= _vocab_keys()
    keys |= _amendment_keys()
    return keys


def _amendment_keys():
    """Every key of the §6.3.7 machine-domain families (0.94 amendment)."""
    keys = set(WIZARD_KEYS)
    for token in STATUS_PROCESS_TOKENS:
        keys.add(f"status.process.{token}.description")
    for token in DESCRIBED_SUB_PROCESSES:
        keys.add(f"status.sub_process.{token}.description")
    for code in SOMMELIER_ERROR_CODES:
        keys.add(f"sommelier.error.{code}")
    for family, tokens in SUGGESTION_TOKENS.items():
        for token in tokens:
            keys.add(f"sommelier.{family}.{token}")
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
    } | {
        f"settings.{setting}.description" for setting in DESCRIBED_SETTINGS
    }
    _tokens, _groups, level_pairs = _emittable_settings()
    for setting, token in level_pairs:
        allowed.add(f"settings.{setting}.levels.{token}")
        allowed.add(f"settings._levels.{token}")
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


def test_en_setting_level_tokens_resolve_via_chain(en_strings):
    """§9.1.4: every emitted level/option token resolves via the chain —
    `settings.<setting>.levels.<token>` OR the shared `settings._levels.<token>`."""
    _tokens, _groups, level_pairs = _emittable_settings()
    assert level_pairs, "no level/option tokens emitted — enumeration broken"
    for setting, token in sorted(level_pairs):
        assert (
            f"settings.{setting}.levels.{token}" in en_strings
            or f"settings._levels.{token}" in en_strings
        ), f"level token {token!r} of setting {setting!r} unresolvable in en"


def test_en_shared_levels_are_exactly_off_on(en_strings):
    """§9.1.4: the shared `_levels` tier carries exactly `off` and `on` in 0.93."""
    shared = {
        key.removeprefix("settings._levels.")
        for key in en_strings
        if key.startswith("settings._levels.")
    }
    assert shared == {"off", "on"}


def test_en_setting_descriptions_are_exactly_the_six(en_strings):
    """§9.1.4: the 6 ported setting descriptions ship; other settings none."""
    described = {
        key.split(".")[1]
        for key in en_strings
        if key.startswith("settings.") and key.endswith(".description")
    }
    assert described == set(DESCRIBED_SETTINGS)


def test_en_settings_authored_strings_pinned(en_strings):
    """The §9.1.4 newly authored English strings, verbatim."""
    assert en_strings["settings._groups.brew"] == "Brewing"
    assert en_strings["settings._groups.water"] == "Water"
    assert en_strings["settings._groups.power"] == "Power"
    assert en_strings["settings._groups.system"] == "System"
    assert en_strings["settings._levels.off"] == "Off"
    assert en_strings["settings._levels.on"] == "On"


def test_en_settings_key_casing_pinned(en_strings):
    """§6.3.1: settings/sommelier keys embed the lower_snake tokens byte-equal."""
    assert "settings.auto_bean_select.label" in en_strings
    assert "settings.water_hardness.levels.very_hard" in en_strings
    assert "settings.brew_temperature.levels.low" in en_strings
    assert "sommelier.cup_size.espresso_cup" in en_strings
    assert "sommelier.roast.medium_dark" in en_strings
    assert "sommelier.extras_kind.syrup" in en_strings
    assert "actions.save_directkey.label" in en_strings


def test_vocab_families_pinned():
    """§9.2.3: the 11 served vocab families (the sommelier keyspace roots)."""
    assert set(build_sommelier_vocab()) == {
        "roast", "bean_type", "origin", "mood", "occasion", "cup_size",
        "temperature", "caffeine", "dietary", "mode", "extras_kind",
    }


# ---------------------------------------------------------------------------
# 0.94 amendment — machine-domain families moved off the clients (§6.3.7)
# ---------------------------------------------------------------------------


def test_en_wizard_keys_exactly_pinned(en_strings):
    """§6.3.7: the 29 brew-guide keys ship, and nothing else under `wizard.`."""
    shipped = {key for key in en_strings if key.startswith("wizard.")}
    assert shipped == set(WIZARD_KEYS)
    assert len(WIZARD_KEYS) == 29


def test_en_wizard_placeholders_pinned(en_strings):
    """§6.3.7: placeholders are carried verbatim — none renamed or dropped."""
    for key, expected in sorted(WIZARD_KEYS.items()):
        found = {token for token in KNOWN_PLACEHOLDERS if token in en_strings[key]}
        assert found == expected, (
            f"{key}: placeholders {sorted(found)} != {sorted(expected)}"
        )


def test_en_uses_no_unknown_placeholders(en_strings):
    """§6.3.7: `{...}` spans are limited to the six declared placeholders."""
    unknown = {
        (key, span)
        for key, value in en_strings.items()
        for span in re.findall(r"\{[^}]*\}", value)
        if span not in KNOWN_PLACEHOLDERS
    }
    assert not unknown, f"undeclared placeholders: {sorted(unknown)}"


def test_en_process_descriptions_cover_every_token(en_strings):
    """§6.3.7: all 12 `status.process` tokens ship a `.description`."""
    described = {
        key.removeprefix("status.process.").removesuffix(".description")
        for key in en_strings
        if key.startswith("status.process.") and key.endswith(".description")
    }
    assert described == set(STATUS_PROCESS_TOKENS)
    assert len(STATUS_PROCESS_TOKENS) == 12


def test_en_sub_process_descriptions_are_the_pinned_subset(en_strings):
    """§6.3.7: sub-process descriptions ship only where they add meaning."""
    assert DESCRIBED_SUB_PROCESSES <= set(STATUS_SUB_PROCESS_TOKENS)
    described = {
        key.removeprefix("status.sub_process.").removesuffix(".description")
        for key in en_strings
        if key.startswith("status.sub_process.") and key.endswith(".description")
    }
    assert described == set(DESCRIBED_SUB_PROCESSES)


def test_en_descriptions_coexist_with_their_labels(en_strings):
    """§6.3.7: `.description` lives beside the bare label in a flat keyspace."""
    for token in STATUS_PROCESS_TOKENS:
        assert f"status.process.{token}" in en_strings
        assert f"status.process.{token}.description" in en_strings
    for token in DESCRIBED_SUB_PROCESSES:
        assert f"status.sub_process.{token}" in en_strings
        assert f"status.sub_process.{token}.description" in en_strings


def test_en_sommelier_error_codes_exactly_five(en_strings):
    """§6.3.7: one actionable hint per LLM pre-flight code, no extras."""
    shipped = {
        key.removeprefix("sommelier.error.")
        for key in en_strings
        if key.startswith("sommelier.error.")
    }
    assert shipped == set(SOMMELIER_ERROR_CODES)


@pytest.mark.parametrize("family", sorted(SUGGESTION_TOKENS))
def test_en_suggestion_family_tokens_pinned(family, en_strings):
    """§6.3.7: each free-form suggestion family labels exactly its tokens."""
    prefix = f"sommelier.{family}."
    shipped = {key.removeprefix(prefix) for key in en_strings if key.startswith(prefix)}
    assert shipped == set(SUGGESTION_TOKENS[family])


def test_suggestion_families_are_not_vocab_families():
    """§9.2.4 intact: labelling a token does not close the field.

    The five suggestion families are display sugar over fields that keep
    accepting arbitrary text, so they must never appear in `vocab/get`.
    """
    assert not set(SUGGESTION_TOKENS) & set(build_sommelier_vocab())


def test_amendment_family_counts_pinned():
    """§6.3.7 table: 29 + 12 + 4 + 5 + 34 = 84 new English keys."""
    assert len(WIZARD_KEYS) == 29
    assert len(STATUS_PROCESS_TOKENS) == 12
    assert len(DESCRIBED_SUB_PROCESSES) == 4
    assert len(SOMMELIER_ERROR_CODES) == 5
    assert {family: len(tokens) for family, tokens in SUGGESTION_TOKENS.items()} == {
        "milk": 8, "syrup": 7, "topping": 5, "liqueur": 4, "note": 10,
    }
    assert len(_amendment_keys()) == 84


# ---------------------------------------------------------------------------
# Per-locale rules (§6.3.3 subset, §6.3.7(b) completeness, §6.3.7(a) placeholders)
# ---------------------------------------------------------------------------

# §6.3.7(b): every locale must key everything the server actually serves.
# The §6.3.3 sparse allowance is NOT withdrawn — it is invoked per key
# instead of per locale. A token that may legitimately ship English-only
# (riding the WS loader's per-key en overlay until translations land) is
# listed here, which makes the exemption a reviewed, reviewable act
# rather than a silent regression of the whole bundle. Empty since the
# 0.94 wave closed the 212-vs-185 gap.
SPARSE_EXEMPT_KEYS: frozenset[str] = frozenset()

# Any `{...}` span, whether or not it is one of KNOWN_PLACEHOLDERS — a
# renamed placeholder must fail the per-locale comparison, not slip past
# it as "no known placeholder found".
_PLACEHOLDER_SPAN = re.compile(r"\{[^}]*\}")

NON_EN_LOCALES = sorted(_locale_names(TRANSLATIONS_DIR) - {"en"})


def _spans(value: str) -> list[str]:
    """Placeholder spans of a string, order-insensitive but count-sensitive."""
    return sorted(_PLACEHOLDER_SPAN.findall(value))


@pytest.fixture(scope="module")
def served_keyspace(en_strings):
    """The keys all 29 locales must carry (§6.3.7(b))."""
    return set(en_strings) - SPARSE_EXEMPT_KEYS


@pytest.mark.parametrize("locale", NON_EN_LOCALES)
def test_locale_is_key_subset_of_en(locale, en_strings):
    """§6.3.3: non-en locales may be sparse but never invent keys."""
    data = _load(UI_STRINGS_DIR / f"{locale}.json")
    _assert_flat_string_map(data, f"{locale}.json")
    extra = set(data) - set(en_strings)
    assert not extra, f"{locale}.json has keys absent from en.json: {sorted(extra)}"


@pytest.mark.parametrize("locale", NON_EN_LOCALES)
def test_locale_covers_the_served_keyspace(locale, served_keyspace):
    """§6.3.7(b): all 29 locales are complete over what the server serves.

    Guards the invariant the 0.94 wave established. Without it the gap
    that motivated the wave (en 212 keys against 185–191 elsewhere, so
    served strings such as `settings.*.description` reached non-English
    users in English) can silently reopen on any later commit.
    """
    data = _load(UI_STRINGS_DIR / f"{locale}.json")
    missing = served_keyspace - set(data)
    assert not missing, (
        f"{locale}.json is missing {len(missing)} served keys: {sorted(missing)}"
    )


@pytest.mark.parametrize("locale", NON_EN_LOCALES)
def test_locale_carries_placeholders_verbatim(locale, en_strings):
    """§6.3.7(a): translators may reorder placeholders, never rename or drop.

    The en-only pins (`test_en_wizard_placeholders_pinned`) say nothing
    about the 28 translated files, where the placeholder-bearing keys
    were hand-edited: a translation that drops `{sec}` or `{prompt}`
    would render a wizard step without its value at runtime.
    """
    data = _load(UI_STRINGS_DIR / f"{locale}.json")
    mismatched = {
        key: (_spans(value), _spans(en_strings[key]))
        for key, value in data.items()
        if key in en_strings and _spans(value) != _spans(en_strings[key])
    }
    assert not mismatched, f"{locale}.json placeholder drift: {mismatched}"
