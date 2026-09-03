"""Tests for the `melitta_barista/i18n/get` WS endpoint.

Zone I-G of the UI Contract v2 (docs/UI_CONTRACT.md §6.3.1/§6.3.2/§8.1):
locale resolution (`de-DE` → `de`, unknown → `en`), the en-first overlay
merge for sparse locales, domain filtering (unknown domains ignored),
`strings_version` from the cached manifest version, non-admin access,
the pinned flat key format, and the per-resolved-locale loader cache
(a single executor read per requested locale).
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.melitta_barista import panel_api
from custom_components.melitta_barista.const import DOMAIN

I18N_TYPE = "melitta_barista/i18n/get"

_i18n_get = inspect.unwrap(panel_api._ws_i18n_get)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_hass(seed_version="0.0.0-test"):
    """MagicMock hass with a counting, synchronous executor shim.

    `seed_version` pre-populates the cached manifest version so most
    tests never touch `async_get_integration`; pass None to exercise
    the real resolution path (with the loader patched).
    """
    hass = MagicMock()
    hass.data = {}
    calls = []

    async def fake_executor(func, *args):
        calls.append(func)
        return func(*args)

    hass.async_add_executor_job = fake_executor
    hass.executor_calls = calls
    if seed_version is not None:
        hass.data[DOMAIN] = {"ui_strings_version": seed_version}
    return hass


def make_connection(is_admin=True):
    """MagicMock WS connection with a controllable admin flag."""
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    connection.user.is_admin = is_admin
    return connection


def make_msg(locale, domains=None, msg_id=7):
    msg = {"id": msg_id, "type": I18N_TYPE, "locale": locale}
    if domains is not None:
        msg["domains"] = domains
    return msg


async def call(hass, locale, domains=None, connection=None):
    """Invoke the unwrapped handler and return the sent result payload."""
    connection = connection or make_connection()
    await _i18n_get(hass, connection, make_msg(locale, domains))
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    _msg_id, payload = connection.send_result.call_args.args
    return payload


# ---------------------------------------------------------------------------
# Registration & schema
# ---------------------------------------------------------------------------


def test_i18n_command_registered():
    """async_register_panel_websocket registers i18n/get."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    assert I18N_TYPE in hass.data["websocket_api"]


def test_i18n_schema_locale_required_domains_optional():
    """Schema requires locale; domains is an optional list of strings."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    _handler, schema = hass.data["websocket_api"][I18N_TYPE]
    schema({"id": 7, "type": I18N_TYPE, "locale": "de"})
    schema({"id": 7, "type": I18N_TYPE, "locale": "de", "domains": ["status"]})
    with pytest.raises(vol.Invalid):
        schema({"id": 7, "type": I18N_TYPE})  # locale missing
    with pytest.raises(vol.Invalid):
        schema({"id": 7, "type": I18N_TYPE, "locale": "de", "domains": "status"})


async def test_i18n_not_admin_gated():
    """A non-admin caller gets a full result (informational endpoint)."""
    hass = make_hass()
    payload = await call(hass, "en", connection=make_connection(is_admin=False))
    assert payload["strings"]


# ---------------------------------------------------------------------------
# Locale resolution (§6.3.1)
# ---------------------------------------------------------------------------


async def test_regional_locale_resolves_to_base_language():
    """de-DE has no file of its own and resolves to de."""
    payload = await call(make_hass(), "de-DE")
    assert payload["locale"] == "de-DE"
    assert payload["resolved_locale"] == "de"
    assert payload["strings"]["status.process.READY"] == "Bereit"


async def test_unknown_locale_resolves_to_en():
    """A locale with no exact or base match falls back to en."""
    payload = await call(make_hass(), "xx")
    assert payload["resolved_locale"] == "en"
    assert payload["strings"]["status.process.READY"] == "Ready"


async def test_malformed_locale_resolves_to_en():
    """A locale that is not a plausible tag never touches other paths."""
    payload = await call(make_hass(), "../../etc/passwd")
    assert payload["resolved_locale"] == "en"
    assert payload["strings"]["status.process.READY"] == "Ready"


async def test_exact_locale_match_wins():
    """An exact locale file is served as-is."""
    payload = await call(make_hass(), "ru")
    assert payload["resolved_locale"] == "ru"


# ---------------------------------------------------------------------------
# Sparse-locale overlay (§6.3.3)
# ---------------------------------------------------------------------------


async def test_sparse_locale_overlays_en(tmp_path, monkeypatch):
    """Keys missing from a sparse locale are served from en, per key."""
    (tmp_path / "en.json").write_text(json.dumps({
        "status.process.READY": "Ready",
        "values.intensity.mild": "Mild",
    }), encoding="utf-8")
    (tmp_path / "zz.json").write_text(json.dumps({
        "status.process.READY": "Zeady",
    }), encoding="utf-8")
    monkeypatch.setattr(panel_api, "_UI_STRINGS_DIR", tmp_path)

    payload = await call(make_hass(), "zz")
    assert payload["resolved_locale"] == "zz"
    assert payload["strings"]["status.process.READY"] == "Zeady"
    assert payload["strings"]["values.intensity.mild"] == "Mild"


# ---------------------------------------------------------------------------
# Domain filtering (§6.3.1)
# ---------------------------------------------------------------------------


async def test_domain_filter_limits_keys():
    """domains=["status"] serves only status.* keys."""
    payload = await call(make_hass(), "en", domains=["status"])
    assert payload["strings"]
    assert all(key.startswith("status.") for key in payload["strings"])
    assert "status.process.READY" in payload["strings"]


async def test_unknown_domains_ignored():
    """Unknown requested domains are ignored, not errors."""
    hass = make_hass()
    filtered = await call(hass, "en", domains=["status", "bogus"])
    plain = await call(hass, "en", domains=["status"])
    assert filtered["strings"] == plain["strings"]


async def test_omitted_domains_serves_all():
    """No domains parameter serves every domain."""
    payload = await call(make_hass(), "en")
    prefixes = {key.split(".", 1)[0] for key in payload["strings"]}
    assert prefixes == {"status", "values", "recipes", "actions"}


# ---------------------------------------------------------------------------
# strings_version (§6.3.2)
# ---------------------------------------------------------------------------


async def test_strings_version_from_manifest():
    """strings_version is the manifest version, resolved once and cached."""
    hass = make_hass(seed_version=None)
    integration = SimpleNamespace(manifest={"version": "7.7.7"})
    with patch(
        "homeassistant.loader.async_get_integration",
        new=AsyncMock(return_value=integration),
    ):
        payload = await call(hass, "en")
    assert payload["strings_version"] == "7.7.7"

    # Cached: a second call never consults the loader again.
    with patch(
        "homeassistant.loader.async_get_integration",
        new=AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        payload = await call(hass, "en")
    assert payload["strings_version"] == "7.7.7"


async def test_seeded_strings_version_served():
    """The hass.data-cached version is served without loader access."""
    payload = await call(make_hass(seed_version="1.2.3"), "en")
    assert payload["strings_version"] == "1.2.3"


# ---------------------------------------------------------------------------
# Envelope & key format
# ---------------------------------------------------------------------------


async def test_envelope_and_flat_key_format():
    """Versioned envelope; flat dot-joined keys, token casing preserved."""
    payload = await call(make_hass(), "en")
    assert payload["schema_version"] == 1
    assert payload["locale"] == "en"
    assert payload["resolved_locale"] == "en"
    strings = payload["strings"]
    assert strings["status.process.READY"] == "Ready"
    assert all(
        isinstance(k, str) and isinstance(v, str) for k, v in strings.items()
    )


# ---------------------------------------------------------------------------
# Loader cache (§6.3.3 — one executor read per locale)
# ---------------------------------------------------------------------------


async def test_single_executor_read_per_locale():
    """Repeat requests for a locale are served from the hass.data cache."""
    hass = make_hass()
    await call(hass, "de")
    await call(hass, "de")
    assert len(hass.executor_calls) == 1
    await call(hass, "ru")
    assert len(hass.executor_calls) == 2
    await call(hass, "ru", domains=["actions"])
    assert len(hass.executor_calls) == 2


async def test_cache_shared_across_requested_spellings():
    """de-DE reuses nothing destructive: one read per requested locale."""
    hass = make_hass()
    await call(hass, "de-DE")
    await call(hass, "de-DE")
    assert len(hass.executor_calls) == 1
