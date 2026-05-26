"""Locale parity for the panel's i18n dictionary.

Every locale under `custom_components/melitta_barista/www/i18n/locales/`
must carry the same key set as `en.js` (the source of truth). New keys
land in en.js first and must be mirrored in every other locale in the
same PR.

We can't import ESM modules from Python, so parsing is regex-based:
each entry sits on its own line as `"key": "value",`. That contract is
load-bearing for this test — if formatting ever drifts (multi-line
literals, key on its own line), update the regex here too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_LOCALES_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
    / "i18n"
    / "locales"
)

_KEY_LINE = re.compile(r'^\s*"([^"\\]+)":', re.MULTILINE)


def _extract_keys(path: Path) -> set[str]:
    """Return the set of i18n keys declared in a locale module."""
    text = path.read_text(encoding="utf-8")
    return set(_KEY_LINE.findall(text))


def _load_locales() -> dict[str, set[str]]:
    """Return {locale_code: keyset} for every *.js file under locales/."""
    return {
        path.stem: _extract_keys(path)
        for path in sorted(_LOCALES_DIR.glob("*.js"))
    }


def test_locales_dir_contains_at_least_en_and_ru():
    """Sanity: en + ru both exist as locale modules."""
    found = {p.stem for p in _LOCALES_DIR.glob("*.js")}
    assert "en" in found, f"en.js missing from {_LOCALES_DIR}"
    assert "ru" in found, f"ru.js missing from {_LOCALES_DIR}"


def test_en_has_keys():
    """Sanity: the source-of-truth locale isn't empty."""
    keys = _extract_keys(_LOCALES_DIR / "en.js")
    assert len(keys) > 50, (
        f"en.js parsed only {len(keys)} keys — the regex contract may "
        "have drifted; check the locale file formatting."
    )


@pytest.mark.parametrize(
    "locale",
    [p.stem for p in sorted(_LOCALES_DIR.glob("*.js")) if p.stem != "en"],
)
def test_locale_has_full_parity_with_en(locale):
    """Every non-English locale must mirror the English key set exactly.

    Both directions:
      - Keys present in en.js but absent here -> translator missed them.
      - Keys here but absent in en.js -> stale entries from a key
        rename / removal that didn't propagate.
    """
    en_keys = _extract_keys(_LOCALES_DIR / "en.js")
    locale_keys = _extract_keys(_LOCALES_DIR / f"{locale}.js")

    missing_in_locale = en_keys - locale_keys
    extra_in_locale = locale_keys - en_keys

    assert not missing_in_locale, (
        f"{locale}.js is missing {len(missing_in_locale)} keys from en.js: "
        f"{sorted(missing_in_locale)[:10]}{'...' if len(missing_in_locale) > 10 else ''}"
    )
    assert not extra_in_locale, (
        f"{locale}.js has {len(extra_in_locale)} keys that are not in en.js "
        f"(stale entries from renames?): "
        f"{sorted(extra_in_locale)[:10]}{'...' if len(extra_in_locale) > 10 else ''}"
    )
