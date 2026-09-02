"""Contract tests for the history-card star-to-favorite button (0.91.0b5).

Like test_panel_icons_frontend.py, there is no JS test runner in this
repo, so these are regex-based checks over the shipped panel source:

1. <melitta-sommelier-history> renders a favorite star per recipe card
   that calls `melitta_barista/sommelier/favorites/add` with the recipe
   id, tracks a busy state, and surfaces a success toast.
2. Already-favorited entries (backend enrichment: `favorite_id` on each
   history recipe row) render the filled star and stay disabled.
3. The i18n keys the component uses exist in both en.js and ru.js (the
   loader falls back to en for the other locales).
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
_HISTORY = _WWW / "components" / "melitta-sommelier-history.js"
_LOCALES = _WWW / "i18n" / "locales"


def _src() -> str:
    return _HISTORY.read_text(encoding="utf-8")


def test_history_calls_favorites_add_with_recipe_id():
    """The star button drives the existing favorites/add WS command."""
    src = _src()
    assert "melitta_barista/sommelier/favorites/add" in src
    call = re.search(
        r"callWS\(\{\s*type:\s*\"melitta_barista/sommelier/favorites/add\","
        r"\s*recipe_id:",
        src,
    )
    assert call, "favorites/add must be called with the recipe id"


def test_history_star_reflects_favorite_id():
    """favorite_id (already favorited) → filled ★; otherwise outline ☆."""
    src = _src()
    assert re.search(r"favorite_id\s*\?\s*\"★\"\s*:\s*\"☆\"", src), (
        "star glyph must be driven by the recipe's favorite_id"
    )
    # A favorited entry must not be clickable again.
    assert re.search(r"\?disabled=\$\{[^}]*favorite_id", src), (
        "the star must be disabled once the entry is favorited"
    )


def test_history_star_has_busy_state():
    """While the add is in flight the per-recipe busy state disables the star."""
    src = _src()
    assert "_favoriting" in src
    assert re.search(r"_favoriting\s*=\s*recipe\.id", src), (
        "busy state must track the recipe being favorited"
    )
    assert re.search(r"finally\s*\{[^}]*_favoriting\s*=\s*\"\"", src, re.DOTALL), (
        "busy state must be cleared in a finally block"
    )


def test_history_star_success_toast_and_error_path():
    """Success surfaces a toast; failure lands in the error banner."""
    src = _src()
    assert "_showToast" in src
    assert "sommelier.favorited_toast" in src
    assert "sommelier.favorite_failed" in src


def test_history_star_marks_entry_after_add():
    """After a successful add the entry is starred without a reload."""
    src = _src()
    assert re.search(r"recipe\.favorite_id\s*=", src), (
        "the card must flip to the favorited state after the add"
    )


def test_history_star_i18n_keys_exist_in_en_and_ru():
    """Every star i18n key referenced by the component ships in en + ru."""
    keys = (
        "sommelier.fav_in",
        "sommelier.fav_add",
        "sommelier.favorited_toast",
        "sommelier.favorite_failed",
    )
    src = _src()
    for key in keys:
        assert key in src, f"history component must use {key!r}"
    for locale in ("en.js", "ru.js"):
        locale_src = (_LOCALES / locale).read_text(encoding="utf-8")
        for key in keys:
            assert f'"{key}"' in locale_src, f"{locale} must define {key!r}"
