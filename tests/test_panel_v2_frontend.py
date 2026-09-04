"""Contract tests for the panel's UI Contract v2 consumers (Zone I-H).

Like the other *_frontend tests, there is no JS test runner in this repo,
so these are regex-based checks over the shipped panel source plus a
`node --check` syntax gate. They pin the §6.1/§6.3 consumer wiring:

1. The panel shell fetches `melitta_barista/i18n/get` once per HA locale
   alongside the contract fetch, feeds the pure server-string registry,
   and degrades gracefully when the command is missing (0.91 backend).
2. The registry (www/i18n/server-strings.js) is pure — zero hass
   coupling — and implements the normative §6.3.5 preference order:
   server string → client bundle string → humanized raw token.
3. Status/manipulation/value tokens render through that chain in
   melitta-status.js / melitta-brew-wizard.js / melitta-sommelier.js —
   raw-token rendering is gone.
4. melitta-recipes.js prefers `contract.parameters` with the §6.1.5
   three-tier fallback (parameters → v1 vocabularies/limits → consts)
   and resolves DirectKey category labels via
   `values.directkey_category.*`.
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
_REGISTRY = _WWW / "i18n" / "server-strings.js"
_SYSTEM = _WWW / "components" / "melitta-system.js"
_STATUS = _WWW / "components" / "melitta-status.js"
_WIZARD = _WWW / "components" / "melitta-brew-wizard.js"
_SOMMELIER = _WWW / "components" / "melitta-sommelier.js"
_RECIPES = _WWW / "components" / "melitta-recipes.js"

_EDITED_FILES = (
    _PANEL, _REGISTRY, _SYSTEM, _STATUS, _WIZARD, _SOMMELIER, _RECIPES,
)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── syntax gate ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _EDITED_FILES, ids=lambda p: p.name)
def test_node_check_passes(path: Path):
    """Every v2-consumer JS file must parse under `node --check`."""
    if shutil.which("node") is None:
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{path.name}: {result.stderr}"


# ── panel shell: i18n fetch wiring ───────────────────────────────────────


def test_panel_fetches_i18n_ws_command():
    """The shell calls melitta_barista/i18n/get with the HA locale."""
    src = _src(_PANEL)
    assert re.search(
        r'callWS\(\{\s*type:\s*"melitta_barista/i18n/get",\s*locale', src
    ), "panel must fetch server strings via the i18n/get WS command"


def test_panel_fetches_i18n_once_per_locale():
    """A locale guard prevents re-fetching for an unchanged locale.

    The guard may carry extra conditions (an in-flight latch was added in
    0.94), so the assertion pins the semantics — an early return while the
    fetched locale is unchanged — rather than the exact expression.
    """
    src = _src(_PANEL)
    assert "_i18nLocale" in src, "panel must track the fetched locale"
    assert re.search(r"locale\s*===\s*this\._i18nLocale[^\n]*\)\s*return", src), (
        "panel must skip the fetch while the locale is unchanged"
    )


def test_panel_feeds_registry_and_degrades_gracefully():
    """Success feeds setServerStrings; failure clears it — no panel error."""
    src = _src(_PANEL)
    assert re.search(
        r'import \{ setServerStrings \} from "\./i18n/server-strings\.js"', src
    ), "panel must import the pure registry setter"
    assert re.search(r"setServerStrings\(strings\)", src)
    # The catch path clears the registry instead of raising a panel error.
    catch = re.search(
        r"catch \(e\) \{[^}]*setServerStrings\(null\)", src, re.DOTALL
    )
    assert catch, "i18n fetch failure must degrade to bundle fallback"
    assert "this._serverStrings = null" in src


def test_panel_passes_server_strings_down():
    """Components receive .serverStrings as a re-render trigger prop."""
    src = _src(_PANEL)
    for tag in ("melitta-sommelier", "melitta-recipes", "melitta-system"):
        assert re.search(
            rf"<{tag}[^>]*\.serverStrings=\$\{{this\._serverStrings\}}", src
        ), f"panel must pass .serverStrings into <{tag}>"


def test_system_threads_props_to_subviews():
    """melitta-system forwards contract + serverStrings to its children."""
    src = _src(_SYSTEM)
    assert re.search(
        r"<melitta-status[^>]*\.serverStrings=\$\{this\.serverStrings\}", src
    )
    assert re.search(
        r"<melitta-recipes[^>]*\.contract=\$\{this\.contract\}", src
    )
    assert re.search(
        r"<melitta-recipes[^>]*\.serverStrings=\$\{this\.serverStrings\}", src
    )


# ── pure registry: fallback layering ─────────────────────────────────────


def test_registry_exports_the_normative_surface():
    """setServerStrings / serverString / resetServerStrings (§6.3.5.6)."""
    src = _src(_REGISTRY)
    for name in (
        "setServerStrings", "serverString", "resetServerStrings",
        "humanizeToken", "labelFor", "displayNameFor",
    ):
        assert re.search(rf"export function {name}\(", src), (
            f"registry must export {name}()"
        )


def _strip_js_comments(src: str) -> str:
    """Remove /* */ and // comments so purity checks see only code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)


def test_registry_is_pure():
    """Zero hass coupling — the fetch half lives in the panel shell."""
    code = _strip_js_comments(_src(_REGISTRY))
    assert "hass" not in code
    assert "callWS" not in code
    assert "import" not in code.replace("import.meta", ""), (
        "the registry must not import anything (pure singleton module)"
    )


def test_registry_fallback_layering():
    """labelFor: server string → bundle string → humanized token."""
    src = _src(_REGISTRY)
    body = re.search(
        r"export function labelFor\([^)]*\)\s*\{(.*?)\n\}", src, re.DOTALL
    )
    assert body, "labelFor must exist"
    text = body.group(1)
    server = text.index("serverString(")
    bundle = text.index("bundleValue")
    humanize = text.index("humanizeToken(")
    assert server < bundle < humanize, (
        "labelFor must try server strings, then the bundle, then humanize"
    )


def test_registry_family_scoped_value_keys():
    """displayNameFor builds family-scoped values.<family>.<token> keys."""
    src = _src(_REGISTRY)
    assert "`values.${family}.${token}`" in src, (
        "value lookups must be family-scoped (§6.3.1 — bare tokens collide)"
    )


# ── status / wizard / sommelier: token rendering ─────────────────────────


def test_status_renders_tokens_via_server_strings():
    """Process + manipulation rows go through the §6.3.5 chain."""
    src = _src(_STATUS)
    assert 'from "../i18n/server-strings.js"' in src
    assert "`status.${kind}.${token}`" in src or (
        "status.process.${" in src and "status.manipulation.${" in src
    ), "status must build status.<kind>.<TOKEN> server keys"
    assert re.search(r'_statusToken\("process"', src)
    assert re.search(r'_statusToken\("manipulation"', src)


def test_wizard_prompt_uses_server_strings():
    """The confirmation prompt resolves status.manipulation.<TOKEN>."""
    src = _src(_WIZARD)
    assert "`status.manipulation.${st.manipulation}`" in src, (
        "wizard prompt must prefer the server manipulation string"
    )
    assert ').replace(/_/g, " ").toLowerCase()' not in src, (
        "inline raw-token humanization must be gone (registry owns it)"
    )


def test_wizard_badges_use_family_scoped_labels():
    """Component badges resolve process/intensity via values.<family>.*."""
    src = _src(_WIZARD)
    assert re.search(r'_valueLabel\("process",', src)
    assert re.search(r'_valueLabel\("intensity",', src)
    assert 'String(c.process).replaceAll("_", " ")' not in src


def test_sommelier_components_use_family_scoped_labels():
    """Recipe component chips render via the §6.3.5 chain, not raw."""
    src = _src(_SOMMELIER)
    assert 'from "../i18n/server-strings.js"' in src
    for family in ("process", "intensity", "aroma", "temperature"):
        assert re.search(rf'_valueLabel\("{family}",', src), (
            f"sommelier must label {family} tokens via _valueLabel"
        )
    assert "<span class=\"proc\">${comp.process}</span>" not in src, (
        "raw process token rendering must be gone"
    )


def test_sommelier_threads_server_strings_into_wizard():
    """The wizard re-renders when server strings arrive."""
    src = _src(_SOMMELIER)
    assert re.search(
        r"<melitta-brew-wizard[^>]*\.serverStrings=\$\{this\.serverStrings\}",
        src,
        re.DOTALL,
    )


# ── recipes: parameters catalog with three-tier fallback ─────────────────


def test_recipes_prefers_contract_parameters():
    """Tier 1 is the v2 parameters catalog (§6.1.5)."""
    src = _src(_RECIPES)
    assert re.search(r"this\.contract\?\.parameters\?\.\[", src) or re.search(
        r"this\.contract\?\.parameters", src
    ), "recipes must read contract.parameters"
    assert 'desc.kind !== "enum"' in src, (
        "unknown descriptor kinds must fall back (§6.0.3)"
    )
    assert 'desc.scope.includes("freestyle")' in src, (
        "descriptors scoped away from freestyle must not drive the editor"
    )


def test_recipes_keeps_tier2_and_tier3():
    """v1 vocabularies/limits stay as tier 2; consts stay as tier 3."""
    src = _src(_RECIPES)
    assert re.search(r"vocabularies\??\.freestyle", src), (
        "tier 2 (v1 vocabularies) must not be skipped — §6.1.5 forbids it"
    )
    assert re.search(r"limits\??\.portion_ml", src)
    assert "FALLBACK_FREESTYLE" in src
    assert "FALLBACK_PORTION_LIMITS" in src


def test_recipes_portion_range_tier1():
    """parameters.portion_ml drives clamps when it is a per-component range."""
    src = _src(_RECIPES)
    assert re.search(r"parameters\?\.portion_ml", src)
    assert 'range.kind === "range"' in src
    assert "range.per_component" in src


def test_recipes_directkey_category_labels():
    """Category labels resolve via values.directkey_category.* (§6.3.4)."""
    src = _src(_RECIPES)
    assert "`values.directkey_category.${token}`" in src


def test_recipes_option_labels_family_scoped():
    """Option labels go through displayNameFor(family, token)."""
    src = _src(_RECIPES)
    assert "displayNameFor" in src
    for family in ("process", "intensity", "aroma", "temperature", "shots"):
        assert re.search(rf'_optionLabel\("{family}",', src), (
            f"recipes must label {family} tokens with the family scope"
        )


# ── graceful absence ─────────────────────────────────────────────────────


def test_graceful_absence_guards():
    """No consumer requires a v2 field — a v1 document stays valid (§6.0.1)."""
    recipes = _src(_RECIPES)
    # All parameters access is optional-chained off the contract.
    assert re.search(r"this\.contract\?\.parameters", recipes)
    for bad in ("this.contract.parameters", "contract.parameters["):
        assert bad not in recipes.replace("this.contract?.parameters", ""), (
            "parameters access must always be optional-chained"
        )
    panel = _src(_PANEL)
    assert re.search(
        r'typeof result\.strings === "object"', panel
    ), "panel must type-guard the i18n response before installing it"
