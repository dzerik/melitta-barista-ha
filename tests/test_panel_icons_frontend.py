"""Contract tests for the panel-side IconSpec renderer and brand badge.

Like test_brew_wizard_frontend.py, there is no JS test runner in this
repo, so these are regex-based checks over the shipped panel source.
They pin the panel's consumer half of UI Contract v1 (docs/UI_CONTRACT.md
§3.6 IconSpec, §3.10 brand_theme, §5.3 client rules):

1. A shared drink-icon renderer module exists under www/components/ui/
   and degrades gracefully: unknown ``spec_version``/missing spec →
   default cup, unknown ``glass`` → ``cup`` geometry, unknown layer
   ``role`` → neutral grey (§5.3.2 — never throw).
2. ``color_hint`` is validated as strict ``#RRGGBB`` before it is used
   as a fill value (treated as escaped color data, never markup).
3. Renderer colors go through CSS custom properties with literal
   fallbacks so both HA themes work.
4. All four sommelier surfaces (generated cards, favorites, history,
   presets) import the renderer and place ``<melitta-drink-icon>`` next
   to the recipe name.
5. The panel shell fetches ``melitta_barista/ui_contract/get`` for the
   brand badge, guards absence of ``brand_theme`` (older backend → no
   badge, no error), validates accent colors, and falls back from
   ``logo_url`` to the wordmark text.
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
_ICON = _WWW / "components" / "ui" / "melitta-drink-icon.js"
_PANEL = _WWW / "melitta-panel.js"
_CONSUMERS = {
    "sommelier": _WWW / "components" / "melitta-sommelier.js",
    "favorites": _WWW / "components" / "melitta-sommelier-favorites.js",
    "history": _WWW / "components" / "melitta-sommelier-history.js",
    "presets": _WWW / "components" / "melitta-sommelier-presets.js",
}


def _icon_src() -> str:
    return _ICON.read_text(encoding="utf-8")


def _panel_src() -> str:
    return _PANEL.read_text(encoding="utf-8")


# ── renderer module ──────────────────────────────────────────────────────


def test_renderer_module_exists():
    """The shared renderer ships as www/components/ui/melitta-drink-icon.js."""
    assert _ICON.is_file(), "missing shared icon renderer module"
    assert "melitta-drink-icon" in _icon_src(), (
        "renderer must register the <melitta-drink-icon> custom element"
    )


def test_renderer_gates_on_spec_version():
    """Unknown spec_version must fall back to the default cup (§5.3.2)."""
    src = _icon_src()
    assert "spec_version" in src
    # The version gate must be an equality check against 1, not a
    # truthiness test — a future spec_version: 2 must NOT be rendered.
    assert re.search(r"spec_version\s*===?\s*1", src), (
        "renderer must strictly check spec_version === 1"
    )


def test_renderer_has_default_cup_fallback():
    """icon: null / invalid spec → neutral default drink, never a throw."""
    src = _icon_src()
    assert re.search(r"DEFAULT_SPEC|_defaultSpec|defaultSpec", src), (
        "renderer must define a default spec for missing/invalid input"
    )


def test_renderer_maps_unknown_glass_to_cup():
    """Unknown glass tokens must render with the `cup` geometry (§3.6)."""
    src = _icon_src()
    assert re.search(r'GLASSES\[[^\]]*\]\s*\|\|\s*GLASSES\[\s*"cup"\s*\]', src), (
        "unknown glass must fall back to the cup geometry"
    )


def test_renderer_neutral_layer_for_unknown_role():
    """Unknown layer roles render as a neutral grey layer (§5.3.2)."""
    src = _icon_src()
    assert re.search(r"neutral", src, re.IGNORECASE), (
        "renderer must have a neutral fill for unknown roles"
    )


def test_renderer_validates_color_hint():
    """color_hint is escaped data: strict #RRGGBB or ignored (§3.6)."""
    src = _icon_src()
    assert re.search(r"#\[0-9a-fA-F\]\{6\}", src), (
        "renderer must validate color_hint against a strict #RRGGBB regex"
    )


def test_renderer_uses_css_variables_with_fallbacks():
    """Layer colors come from CSS custom properties with fallbacks."""
    src = _icon_src()
    assert re.search(r"var\(--mb-icon-[a-z-]+,\s*#[0-9a-fA-F]{3,6}\)", src), (
        "role fills must be var(--mb-icon-*, <fallback>) so themes can retint"
    )


def test_renderer_covers_known_roles_and_foam():
    """All §3.6 known roles plus crema/foam/steam must be handled."""
    src = _icon_src()
    for token in ("coffee", "milk", "water", "additive", "milk_foam",
                  "crema", "steam", "fill_level", "fraction", "intensity"):
        assert token in src, f"renderer must handle {token!r}"


# ── the four consumer surfaces ───────────────────────────────────────────


def test_all_sommelier_surfaces_use_the_renderer():
    """Generated cards, favorites, history and presets render the icon."""
    for name, path in _CONSUMERS.items():
        src = path.read_text(encoding="utf-8")
        assert "ui/melitta-drink-icon.js" in src, (
            f"{name}: must import the shared icon renderer"
        )
        assert "<melitta-drink-icon" in src, (
            f"{name}: must place <melitta-drink-icon> next to the recipe name"
        )


def test_consumers_bind_the_icon_spec_property():
    """Surfaces must pass the WS payload's `icon` field into `.spec`."""
    for name, path in _CONSUMERS.items():
        src = path.read_text(encoding="utf-8")
        assert re.search(r"\.spec=\$\{[^}]*icon", src), (
            f"{name}: <melitta-drink-icon .spec=...> must bind the icon field"
        )


# ── brand badge in the panel shell ───────────────────────────────────────


def test_panel_fetches_ui_contract_for_the_badge():
    """The panel shell asks ui_contract/get for the active entry."""
    src = _panel_src()
    assert "melitta_barista/ui_contract/get" in src
    assert "entry_id" in src


def test_panel_guards_absent_brand_theme():
    """Older backend (no command / no brand_theme) → no badge, no error."""
    src = _panel_src()
    assert "brand_theme" in src
    # The fetch must be wrapped so an unknown-command rejection from an
    # older backend degrades silently instead of surfacing an error.
    fetch = re.search(
        r"async\s+_loadBrandTheme\s*\([^)]*\)\s*\{(.*?)\n  \}",
        src,
        re.DOTALL,
    )
    assert fetch, "panel must load the theme via a dedicated _loadBrandTheme"
    assert "try" in fetch.group(1) and "catch" in fetch.group(1), (
        "_loadBrandTheme must swallow fetch failures (old backend)"
    )
    # Absent block → falsy state → badge renderer bails out early.
    assert re.search(r"if\s*\(!\s*(bt|theme|this\._brandTheme)\b", src), (
        "badge rendering must no-op when brand_theme is absent"
    )


def test_panel_validates_brand_colors():
    """accent/accent_soft are escaped color data — strict #rrggbb only."""
    assert re.search(r"#\[0-9a-fA-F\]\{6\}", _panel_src()), (
        "panel must validate brand colors against a strict #RRGGBB regex"
    )


def test_panel_badge_falls_back_from_logo_to_wordmark():
    """logo_url renders an <img>; error path degrades to wordmark text."""
    src = _panel_src()
    assert "logo_url" in src
    assert "wordmark" in src
    assert "@error" in src, (
        "a broken user-supplied logo must fall back to the wordmark"
    )
    # Defense in depth: only HA-served /local/ URLs may become <img src>.
    assert re.search(r'startsWith\(\s*"/local/"', src), (
        "logo_url must be restricted to the /local/ path the contract emits"
    )
