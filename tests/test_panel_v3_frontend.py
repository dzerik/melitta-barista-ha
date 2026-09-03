"""Contract tests for the panel's UI Contract v3 consumers (Zone I-M).

Like the other *_frontend tests, there is no JS test runner in this repo,
so these are regex-based checks over the shipped panel source plus a
`node --check` syntax gate. They pin the §9.2/§9.3 consumer wiring:

1. The panel shell fetches `melitta_barista/vocab/get` once per session,
   type-guards the response, degrades gracefully on failure (§9.0.4) and
   passes `.vocab` (plus `.serverStrings`) down to the sommelier, beans
   and additives components.
2. melitta-recipes.js prefers the served per-row `category` token
   (§9.3.4) — the `(id - 302) % 10` math and the DIRECTKEY_OFFSET copy
   are deleted — and reads empty-slot component defaults from the
   `save_directkey` action-catalog entry (§9.3.5) with the hardcoded
   service-schema mirrors demoted to fallback.
3. melitta-sommelier.js / melitta-beans.js / melitta-additives.js build
   their enum option lists from the served vocabulary with the existing
   hardcoded arrays as the fallback tier (§9.2.6.1) and resolve labels
   via the `sommelier.<family>.<token>` server strings (§9.2.6.2) — the
   beans tab's raw-token rendering of roast/bean_type/origin is gone.
4. Free-form families (milk, flavor notes, extras item names) stay
   free-form (§9.2.4): no vocab gating on their inputs.
5. Every touched JS file parses (`node --check`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_WWW = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
)
_PANEL = _WWW / "melitta-panel.js"
_RECIPES = _WWW / "components" / "melitta-recipes.js"
_SOMMELIER = _WWW / "components" / "melitta-sommelier.js"
_BEANS = _WWW / "components" / "melitta-beans.js"
_ADDITIVES = _WWW / "components" / "melitta-additives.js"

_EDITED_FILES = (_PANEL, _RECIPES, _SOMMELIER, _BEANS, _ADDITIVES)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src: str) -> str:
    """Remove /* */ and // comments so structural checks see only code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)


# ── syntax gate ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _EDITED_FILES, ids=lambda p: p.name)
def test_node_check_passes(path: Path):
    """Every v3-consumer JS file must parse under `node --check`."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{path.name}: {result.stderr}"


# ── panel shell: vocab fetch wiring ──────────────────────────────────────


def test_panel_fetches_vocab_ws_command():
    """The shell calls melitta_barista/vocab/get (no entry_id — §9.2.2)."""
    src = _src(_PANEL)
    call = re.search(
        r'callWS\(\{\s*type:\s*"melitta_barista/vocab/get",?\s*\}\)', src
    )
    assert call, "panel must fetch the vocabulary via vocab/get"


def test_panel_type_guards_vocab_response():
    """The vocab object is type-guarded before installation."""
    src = _src(_PANEL)
    assert re.search(r'typeof result\.vocab === "object"', src), (
        "panel must type-guard the vocab/get response"
    )


def test_panel_vocab_failure_degrades_gracefully():
    """A failed vocab fetch clears the vocab — never a panel error."""
    src = _src(_PANEL)
    body = re.search(
        r"async _loadVocab\(\)\s*\{(.*?)\n  \}", src, re.DOTALL
    )
    assert body, "panel must own the vocab fetch in _loadVocab()"
    assert re.search(r"catch[^{]*\{[^}]*this\._vocab = null", body.group(1)), (
        "vocab fetch failure must degrade to the hardcoded fallback arrays"
    )


def test_panel_passes_vocab_down():
    """Sommelier/beans/additives receive .vocab as a prop."""
    src = _src(_PANEL)
    for tag in ("melitta-sommelier", "melitta-beans", "melitta-additives"):
        assert re.search(
            rf"<{tag}[^>]*\.vocab=\$\{{this\._vocab\}}", src
        ), f"panel must pass .vocab into <{tag}>"


def test_panel_passes_server_strings_to_vocab_consumers():
    """Beans/additives re-render when sommelier.* server strings arrive."""
    src = _src(_PANEL)
    for tag in ("melitta-beans", "melitta-additives"):
        assert re.search(
            rf"<{tag}[^>]*\.serverStrings=\$\{{this\._serverStrings\}}", src
        ), f"panel must pass .serverStrings into <{tag}>"


# ── recipes: served category token + catalog defaults ────────────────────


def test_recipes_prefers_served_category_token():
    """Tier 1 for the row category is the served `category` field (§9.3.4)."""
    src = _src(_RECIPES)
    assert re.search(r'typeof recipe\?\.category === "string"', src), (
        "recipes must read the served per-row category token"
    )


def test_recipes_id_math_deleted():
    """The (id - 302) % 10 duplication is gone (§10.1 Zone I-M)."""
    code = _strip_js_comments(_src(_RECIPES))
    assert "DIRECTKEY_OFFSET" not in code
    assert "DIRECTKEY_PROFILE_MULTIPLIER" not in code
    assert "302" not in code, "the BLE slot-id layout copy must be deleted"


def test_recipes_keeps_category_fallback_array():
    """DIRECTKEY_CATEGORIES stays as the tier-2 fixture (§5.3.6)."""
    src = _src(_RECIPES)
    assert "DIRECTKEY_CATEGORIES" in src


def test_recipes_reads_save_directkey_catalog_defaults():
    """Empty-slot defaults come from the save_directkey entry (§9.3.5)."""
    src = _src(_RECIPES)
    assert re.search(r'a\.action === "save_directkey"', src) or re.search(
        r'action === "save_directkey"', src
    ), "recipes must locate the save_directkey action-catalog entry"
    assert re.search(r"invocation\?\.params", src), (
        "defaults must be read from the entry's introspected params"
    )
    for name in ("process", "intensity", "aroma", "temperature", "shots"):
        assert re.search(rf"`{name}\$\{{n\}}`", src) or f"{name}${{n}}" in src, (
            f"param-name mapping must cover {name}<n>"
        )


def test_recipes_keeps_default_fallbacks():
    """The service-schema default mirrors stay as fallback fixtures."""
    src = _src(_RECIPES)
    assert "FALLBACK_DEFAULT_C1" in src
    assert "FALLBACK_DEFAULT_C2" in src
    code = _strip_js_comments(src)
    assert not re.search(r"\bDEFAULT_C1\b", code), (
        "the old primary-default const must be demoted to FALLBACK_DEFAULT_C1"
    )


def test_recipes_actions_access_optional_chained():
    """A contract without `actions` (or null contract) never throws."""
    code = _strip_js_comments(_src(_RECIPES))
    assert re.search(r"this\.contract\?\.actions", code)
    assert "this.contract.actions" not in code.replace(
        "this.contract?.actions", ""
    )


# ── sommelier: vocab-driven pickers ──────────────────────────────────────


def test_sommelier_declares_vocab_prop():
    src = _src(_SOMMELIER)
    assert re.search(r"vocab:\s*\{\s*attribute:\s*false\s*\}", src), (
        "sommelier must accept the .vocab prop"
    )


def test_sommelier_vocab_tokens_helper_with_fallback():
    """Option lists: served vocab.<family>.tokens → hardcoded arrays."""
    src = _src(_SOMMELIER)
    assert re.search(r"this\.vocab\?\.\[family\]\?\.tokens", src), (
        "sommelier must read served family token lists"
    )
    # The hardcoded arrays stay as the tier-2 fixtures (§5.3.6).
    for const in (
        "CUP_SIZES", "MOODS", "OCCASIONS", "TEMPERATURES",
        "CAFFEINE_PREFS", "DIETARY", "MODES",
    ):
        assert const in src, f"{const} must stay as the fallback fixture"


@pytest.mark.parametrize(
    "family, fallback",
    [
        ("cup_size", "CUP_SIZES"),
        ("mood", "MOODS"),
        ("occasion", "OCCASIONS"),
        ("temperature", "TEMPERATURES"),
        ("caffeine", "CAFFEINE_PREFS"),
        ("dietary", "DIETARY"),
    ],
)
def test_sommelier_pickers_are_vocab_driven(family: str, fallback: str):
    """Each enum picker renders _vocabTokens(<family>, <fallback>)."""
    src = _src(_SOMMELIER)
    assert re.search(
        rf'_vocabTokens\("{family}",\s*{fallback}\)', src
    ), f"the {family} picker must be vocab-driven with {fallback} fallback"


def test_sommelier_labels_use_server_strings():
    """Labels resolve via sommelier.<family>.<token> (§9.2.6.2)."""
    src = _src(_SOMMELIER)
    assert "`sommelier.${family}.${token}`" in src, (
        "sommelier labels must build family-scoped server keys"
    )
    assert "`sommelier.mode.${token}`" in src, (
        "mode labels must prefer the server strings"
    )


def test_sommelier_bundle_families_rekeyed():
    """Legacy bundle keys (cup/temp/diet) stay as the tier-2 fallback."""
    src = _src(_SOMMELIER)
    for vocab_family, bundle_family in (
        ("cup_size", "cup"), ("temperature", "temp"), ("dietary", "diet"),
    ):
        assert re.search(
            rf'{vocab_family}:\s*"{bundle_family}"', src
        ), f"vocab family {vocab_family} must map to bundle key {bundle_family}"


def test_sommelier_cup_volume_hint_is_guarded():
    """volumes_ml is advisory display data (§9.2.6.3) — optional-chained."""
    src = _src(_SOMMELIER)
    assert re.search(r"vocab\?\.cup_size\?\.volumes_ml", src), (
        "cup-size volume hints must come from the served metadata, guarded"
    )


# ── beans: vocab-driven selects, localized labels ────────────────────────


def test_beans_declares_vocab_prop():
    src = _src(_BEANS)
    assert re.search(r"vocab:\s*\{\s*attribute:\s*false\s*\}", src)
    assert re.search(r"serverStrings:\s*\{\s*attribute:\s*false\s*\}", src)


@pytest.mark.parametrize(
    "family, fallback",
    [("roast", "ROASTS"), ("bean_type", "BEAN_TYPES"), ("origin", "ORIGINS")],
)
def test_beans_pickers_are_vocab_driven(family: str, fallback: str):
    src = _src(_BEANS)
    assert fallback in src, f"{fallback} must stay as the fallback fixture"
    assert re.search(
        rf'_vocabTokens\("{family}",\s*{fallback}\)', src
    ), f"the {family} select must be vocab-driven with {fallback} fallback"


def test_beans_raw_token_rendering_gone():
    """Roast/bean_type/origin render via sommelier.* server strings."""
    src = _src(_BEANS)
    assert 'from "../i18n/server-strings.js"' in src
    assert "`sommelier.${family}.${token}`" in src, (
        "bean enum labels must build sommelier.<family>.<token> keys"
    )
    assert "<td>${b.roast}</td>" not in src, (
        "the beans table must not render the raw roast token"
    )
    assert re.search(r'_vocabLabel\("roast",', src)
    assert re.search(r'_vocabLabel\("origin",', src)


def test_beans_autofill_validates_against_active_lists():
    """LLM merge accepts only tokens from the active (served) lists."""
    src = _src(_BEANS)
    assert re.search(
        r'_vocabTokens\("roast", ROASTS\)\.includes\(parsed\.roast\)', src
    )
    assert not re.search(r"\bROASTS\.includes\(", src), (
        "autofill must validate against the served list, not the fixture"
    )


def test_beans_flavor_notes_stay_free_form():
    """Flavor notes remain a dynamic free-form tag field (§9.2.4)."""
    src = _src(_BEANS)
    assert "_addTagToBean" in src
    assert "tag-suggestions" in src


# ── additives: extras_kind vocab, milk stays local ───────────────────────


def test_additives_declares_vocab_prop():
    src = _src(_ADDITIVES)
    assert re.search(r"vocab:\s*\{\s*attribute:\s*false\s*\}", src)


def test_additives_kind_picker_is_vocab_driven():
    """Type picker follows served extras_kind, filtered to known tables."""
    src = _src(_ADDITIVES)
    assert re.search(r"vocab\?\.extras_kind\?\.tokens", src), (
        "additives must read the served extras_kind tokens"
    )
    assert "EXTRAS_KIND_TABLES" in src, (
        "the backing-table whitelist must gate served kinds (never invent"
        " a table for an unknown token — §9.0.3)"
    )
    # Milk is a panel-local storage type (free-form family, §9.2.4), not
    # an extras_kind slot — it must stay in the picker unconditionally.
    assert re.search(r'\.concat\("milk"\)|"milk"\s*\]', src), (
        "milk must remain in the kind picker outside the vocab"
    )


def test_additives_kind_labels_use_server_strings():
    src = _src(_ADDITIVES)
    assert 'from "../i18n/server-strings.js"' in src
    assert "`sommelier.extras_kind.${token}`" in src, (
        "kind labels must prefer sommelier.extras_kind.* server strings"
    )


# ── graceful absence (§9.0.4) ────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", (_SOMMELIER, _BEANS, _ADDITIVES), ids=lambda p: p.name
)
def test_vocab_access_is_optional_chained(path: Path):
    """No consumer requires the vocab — absence degrades per feature."""
    code = _strip_js_comments(_src(path))
    bare = re.sub(r"this\.vocab\?\.", "", code)
    assert not re.search(r"this\.vocab\.", bare), (
        f"{path.name}: vocab access must always be optional-chained"
    )
