"""Tests for the `melitta_barista/i18n/get` WS endpoint.

Zone I-G of the UI Contract v2 (docs/UI_CONTRACT.md §6.3.1/§6.3.2/§8.1):
locale resolution (`de-DE` → `de`, unknown → `en`), the en-first overlay
merge for sparse locales, domain filtering (unknown domains ignored),
non-admin access, the pinned flat key format, and the
per-resolved-locale loader cache (a single executor read per requested
locale).

Extended by Zone I-K of the v3 amendment (§9.1.4/§9.2.5/§5.2 rule 10):
the domain set grows to six (`settings`, `sommelier` added), explicit
old-four-domain requests stay byte-identical, unfiltered requests
include the new domains (the real 2.x-compat mechanism), and
`strings_version` comes exclusively from the setup-time stash
`hass.data[DOMAIN]["ui_strings_version"]` — the lazy
`async_get_integration` path was removed (§9.2.2).
"""

from __future__ import annotations

import inspect
import json
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

    `seed_version` pre-populates the setup-time stash exactly as
    `async_setup_entry` does before WS registration; pass None to model
    a hass where setup never ran (the handler serves "unknown" — the
    lazy loader path was removed by the v3 amendment, §9.2.2).
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
    """No domains parameter serves every shipped domain.

    Robust across the Zone I-L asset seeding: the shipped en.json always
    carries the four v2 domains, may additionally carry the two v3
    domains (`settings`, `sommelier`) and the §6.3.7 `wizard` domain, and
    never anything else.
    """
    payload = await call(make_hass(), "en")
    prefixes = {key.split(".", 1)[0] for key in payload["strings"]}
    assert {"status", "values", "recipes", "actions"} <= prefixes
    assert prefixes <= panel_api._I18N_DOMAINS


# ---------------------------------------------------------------------------
# Seven-domain set (v3 §9.1.4/§9.2.5 + §6.3.7 `wizard`; §5.2 rule 10 mechanism)
# ---------------------------------------------------------------------------

# Fixture asset carrying keys in all seven served domains, so these tests
# hold regardless of when the string assets land.
_SEVEN_DOMAIN_EN = {
    "status.process.READY": "Ready",
    "values.intensity.mild": "Mild",
    "recipes.name.espresso": "Espresso",
    "actions.easy_clean.label": "Easy Clean",
    "settings.water_hardness.label": "Water hardness",
    "settings._levels.off": "Off",
    "sommelier.roast.medium_dark": "Medium-dark roast",
    "wizard.step.cup": "Place the cup",
}


@pytest.fixture
def seven_domain_dir(tmp_path, monkeypatch):
    """Point the loader at a fixture en.json spanning all seven domains."""
    (tmp_path / "en.json").write_text(
        json.dumps(_SEVEN_DOMAIN_EN), encoding="utf-8"
    )
    monkeypatch.setattr(panel_api, "_UI_STRINGS_DIR", tmp_path)
    return tmp_path


def test_i18n_domain_set_is_seven():
    """`settings`/`sommelier` (v3) and `wizard` (§6.3.7) joined the set."""
    assert panel_api._I18N_DOMAINS == frozenset({
        "status", "values", "recipes", "actions", "settings", "sommelier",
        "wizard",
    })


async def test_new_domains_are_filterable(seven_domain_dir):
    """The v3 and §6.3.7 domains are real filter values, not pass-through."""
    hass = make_hass()
    payload = await call(hass, "en", domains=["settings"])
    assert set(payload["strings"]) == {
        "settings.water_hardness.label", "settings._levels.off",
    }
    payload = await call(hass, "en", domains=["sommelier"])
    assert set(payload["strings"]) == {"sommelier.roast.medium_dark"}
    payload = await call(hass, "en", domains=["wizard"])
    assert set(payload["strings"]) == {"wizard.step.cup"}


async def test_old_four_domain_filter_byte_identical(seven_domain_dir):
    """An explicit old-four-domain request excludes every new key —
    byte-identical to a pre-0.93 response (§5.2 rule 10)."""
    payload = await call(
        make_hass(), "en", domains=["status", "values", "recipes", "actions"]
    )
    assert payload["strings"] == {
        key: value
        for key, value in _SEVEN_DOMAIN_EN.items()
        if key.split(".", 1)[0] in {"status", "values", "recipes", "actions"}
    }
    assert not any(
        key.startswith(("settings.", "sommelier.", "wizard."))
        for key in payload["strings"]
    )


async def test_unfiltered_request_includes_new_domains(seven_domain_dir):
    """Shipped clients fetch without a domain filter, so their responses
    grow with the `settings.*`/`sommelier.*`/`wizard.*` keys — the real
    2.x-compat mechanism is unknown-key tolerance, not filtering
    (§5.2 rule 10)."""
    payload = await call(make_hass(), "en")
    assert payload["strings"] == _SEVEN_DOMAIN_EN
    assert "settings._levels.off" in payload["strings"]
    assert "sommelier.roast.medium_dark" in payload["strings"]
    assert "wizard.step.cup" in payload["strings"]


# ---------------------------------------------------------------------------
# strings_version (§6.3.2)
# ---------------------------------------------------------------------------


async def test_strings_version_stash_only_loader_never_consulted():
    """strings_version comes from the setup-time stash; the lazy
    `async_get_integration` path is gone (§9.2.2 / §5.1 single-source)."""
    hass = make_hass(seed_version="7.7.7")
    with patch(
        "homeassistant.loader.async_get_integration",
        new=AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        payload = await call(hass, "en")
    assert payload["strings_version"] == "7.7.7"


async def test_missing_stash_serves_unknown_without_loader():
    """No stash (setup never ran — test-only state) degrades to 'unknown'
    instead of resolving lazily; production writes the stash before WS
    registration, so this state is unobservable on a live install."""
    hass = make_hass(seed_version=None)
    with patch(
        "homeassistant.loader.async_get_integration",
        new=AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        payload = await call(hass, "en")
    assert payload["strings_version"] == "unknown"


async def test_seeded_strings_version_served():
    """The hass.data-stashed version is served without loader access."""
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
