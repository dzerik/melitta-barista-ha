"""Contract tests for the panel's DirectKey recipe editor.

Like test_panel_icons_frontend.py, there is no JS test runner in this
repo, so these are regex-based checks over the shipped panel source.
They pin the editor's integration points:

1. The panel shell registers the "recipes" tab between "sommelier" and
   "beans", renders <melitta-recipes> for it and passes the UI Contract
   document down (single ui_contract/get fetch — no second fetch in the
   component).
2. The tab is capability-gated on `supports_recipe_writes` (UI Contract
   §3.3) so brands without DirectKey support hide it entirely.
3. The editor component exists, imports the shared drink-icon renderer
   and binds recipe icons into `.spec`.
4. Writes go through the existing HA services:
   `melitta_barista.save_directkey` (full component 1/2 field set) and
   `melitta_barista.reset_recipe` (per-recipe reset, behind a
   <melitta-confirm> prompt).
5. Form options and portion clamps come from the contract
   (vocabularies.freestyle / limits.portion_ml) with hardcoded fallbacks
   matching the service schema defaults.
6. Editing degrades gracefully while the machine is disconnected.
7. All new strings resolve through i18n, keys present in en AND ru, and
   the loader falls back to English for missing locales.
"""
from __future__ import annotations

import re
from pathlib import Path

_WWW = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
)
_PANEL = _WWW / "melitta-panel.js"
_RECIPES = _WWW / "components" / "melitta-recipes.js"
_I18N_INDEX = _WWW / "i18n" / "index.js"
_LOCALE_EN = _WWW / "i18n" / "locales" / "en.js"
_LOCALE_RU = _WWW / "i18n" / "locales" / "ru.js"


def _panel_src() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _recipes_src() -> str:
    return _RECIPES.read_text(encoding="utf-8")


# ── panel shell: tab registration & gating ───────────────────────────────


def test_recipes_tab_registered_between_sommelier_and_beans():
    """TAB_IDS carries "recipes" right after "sommelier", before "beans"."""
    src = _panel_src()
    tabs = re.search(r"const TAB_IDS = \[(.*?)\];", src, re.DOTALL)
    assert tabs, "panel must declare TAB_IDS"
    ids = re.findall(r'"([a-z_]+)"', tabs.group(1))
    assert "recipes" in ids, "recipes tab must be registered"
    assert ids.index("sommelier") < ids.index("recipes") < ids.index("beans"), (
        "recipes tab must sit between sommelier and beans"
    )


def test_panel_renders_recipes_component_with_contract():
    """The recipes tab renders <melitta-recipes> and feeds the contract."""
    src = _panel_src()
    assert re.search(r'case "recipes":', src)
    assert "<melitta-recipes" in src
    assert re.search(r"<melitta-recipes[^>]*\.contract=\$\{", src), (
        "panel must pass the fetched ui_contract document into the editor"
    )


def test_recipes_tab_gated_on_supports_recipe_writes():
    """Brands without DirectKey support hide the tab entirely (§3.3)."""
    src = _panel_src()
    assert "supports_recipe_writes" in src, (
        "panel must gate the recipes tab on capabilities.supports_recipe_writes"
    )
    assert re.search(r"_visibleTabs\s*\(", src), (
        "tab rendering must go through a capability-filtered tab list"
    )


def test_component_does_not_refetch_the_contract():
    """The contract is fetched once by the shell — never by the component."""
    assert "ui_contract/get" not in _recipes_src(), (
        "melitta-recipes must consume the .contract property, not refetch"
    )


# ── editor component ─────────────────────────────────────────────────────


def test_component_exists_and_imports_icon_renderer():
    """The editor ships and reuses the shared drink-icon renderer."""
    assert _RECIPES.is_file(), "missing melitta-recipes.js"
    src = _recipes_src()
    assert "ui/melitta-drink-icon.js" in src, (
        "editor must import the shared icon renderer"
    )
    assert re.search(r"<melitta-drink-icon[^>]*\.spec=\$\{[^}]*icon", src), (
        "editor must bind the recipe's icon field into <melitta-drink-icon .spec>"
    )


def test_component_saves_via_save_directkey_service():
    """Saves call the existing melitta_barista.save_directkey HA service."""
    src = _recipes_src()
    assert re.search(
        r'callService\(\s*"melitta_barista",\s*"save_directkey"', src
    ), "editor must save through hass.callService(save_directkey)"
    for field in (
        "category", "profile_id",
        "process1", "intensity1", "aroma1", "temperature1", "shots1",
        "portion1_ml",
        "process2", "intensity2", "aroma2", "temperature2", "shots2",
        "portion2_ml",
    ):
        assert field in src, f"save payload must carry {field!r}"


def test_component_resets_via_reset_recipe_service():
    """Per-recipe reset uses melitta_barista.reset_recipe with recipe_id."""
    src = _recipes_src()
    assert re.search(
        r'callService\(\s*"melitta_barista",\s*"reset_recipe"', src
    ), "editor must reset through hass.callService(reset_recipe)"
    assert "recipe_id" in src
    assert "melitta-confirm" in src, (
        "destructive reset must go through the melitta-confirm dialog"
    )


def test_portion_clamp_from_contract_with_fallback():
    """Portion limits come from limits.portion_ml with schema fallbacks."""
    src = _recipes_src()
    assert re.search(r"limits\??\.portion_ml", src), (
        "portion clamps must be read from the contract's limits.portion_ml"
    )
    assert re.search(r"min:\s*5,\s*max:\s*250,\s*step:\s*5", src), (
        "c1 fallback clamp must match the service schema (5-250, step 5)"
    )
    assert re.search(r"min:\s*0,\s*max:\s*250,\s*step:\s*5", src), (
        "c2 fallback clamp must match the service schema (0-250, step 5)"
    )


def test_vocabularies_from_contract_with_fallback():
    """Selects use vocabularies.freestyle; fallbacks match the schema."""
    src = _recipes_src()
    assert re.search(r"vocabularies\??\.freestyle", src), (
        "option lists must come from the contract's freestyle vocabularies"
    )
    for token in ("very_mild", "very_strong", "intense", "cold", "three"):
        assert f'"{token}"' in src, (
            f"fallback vocabularies must carry the schema token {token!r}"
        )


def test_component_disables_editing_while_disconnected():
    """Disconnected machine → cards disabled, notice shown."""
    src = _recipes_src()
    assert "_connected()" in src
    assert "recipes.disconnected" in src, (
        "editor must surface the localized disconnected notice"
    )


def test_component_has_busy_and_toast_states():
    """Busy flag guards double submits; results surface via the toast."""
    src = _recipes_src()
    assert "_busy" in src
    assert "melitta-toast" in src or "_showToast" in src, (
        "editor must report results through the shared toast component"
    )


# ── i18n ─────────────────────────────────────────────────────────────────

_REQUIRED_KEYS = (
    "tabs.recipes",
    "recipes.editor_title",
    "recipes.disconnected",
    "recipes.component1",
    "recipes.component2",
    "recipes.edit_title",
    "recipes.save_success",
    "recipes.save_failed",
    "recipes.reset_success",
    "recipes.reset_failed",
    "recipes.reset_confirm",
    "recipes.no_entity",
    "recipes.cat.espresso",
    "recipes.cat.water",
    "recipes.opt.very_mild",
    "recipes.opt.three",
)


def test_i18n_keys_present_in_en_and_ru():
    """All new editor strings exist in both shipped locales."""
    en = _LOCALE_EN.read_text(encoding="utf-8")
    ru = _LOCALE_RU.read_text(encoding="utf-8")
    for key in _REQUIRED_KEYS:
        assert f'"{key}"' in en, f"en.js must define {key!r}"
        assert f'"{key}"' in ru, f"ru.js must define {key!r}"


def test_i18n_loader_falls_back_to_english():
    """Missing locales / keys must resolve through the English dict."""
    src = _I18N_INDEX.read_text(encoding="utf-8")
    assert re.search(r"STRINGS\[lang\]\s*\|\|\s*STRINGS\.en", src), (
        "unknown locales must fall back to the English dictionary"
    )
    assert re.search(r"value\s*=\s*STRINGS\.en\[key\]", src), (
        "keys missing from a locale must fall back to the English value"
    )
