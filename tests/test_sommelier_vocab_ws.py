"""Tests for `melitta_barista/vocab/get` and the cup-size normalization.

Zone I-K of the UI Contract v3 amendment (§9.2, §10.1): the full vocab
payload pinned verbatim (order-sensitive, from the ordered sommelier_api
lists), `strings_version` from the setup-time stash (== manifest, served
correctly when `vocab/get` is the session's FIRST WS call), non-admin
access, no `entry_id` parameter, set-equality pins between the
sommelier_api lists and the ai_recipes set duplicates, the free-form
families provably absent (§9.2.4), and the §9.2.6.4 cup-size token
normalization — the one-time v10 → v11 DB migration plus the
profiles/add|update write-path aliasing.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
import voluptuous as vol

from custom_components.melitta_barista import ai_recipes
from custom_components.melitta_barista import panel_api
from custom_components.melitta_barista import sommelier_api as sa
from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.sommelier_db import (
    SCHEMA_VERSION,
    SommelierDB,
)

VOCAB_TYPE = "melitta_barista/vocab/get"

_MANIFEST = json.loads(
    (
        pathlib.Path(__file__).parent.parent
        / "custom_components" / "melitta_barista" / "manifest.json"
    ).read_text(encoding="utf-8")
)

# §9.2.2 response `vocab` block, pinned verbatim. Token order is the wire
# order (the ordered sommelier_api lists); family order matches the
# normative example.
EXPECTED_VOCAB = {
    "roast": {"tokens": ["light", "medium", "medium_dark", "dark"]},
    "bean_type": {"tokens": ["arabica", "arabica_robusta", "robusta"]},
    "origin": {"tokens": ["single_origin", "blend"]},
    "mood": {
        "tokens": ["energizing", "relaxing", "dessert", "classic"],
        "multi": True,
    },
    "occasion": {
        "tokens": ["morning", "after_lunch", "guests", "romantic", "work"],
    },
    "cup_size": {
        "tokens": ["espresso_cup", "cup", "mug", "tall_glass", "travel"],
        "volumes_ml": {
            "espresso_cup": [60, 90],
            "cup": [150, 200],
            "mug": [250, 350],
            "tall_glass": [300, 400],
            "travel": [350, 500],
        },
    },
    "temperature": {"tokens": ["auto", "hot", "iced"]},
    "caffeine": {"tokens": ["regular", "low", "decaf_evening"]},
    "dietary": {
        "tokens": ["no_sugar", "lactose_free", "low_calorie", "vegan"],
        "multi": True,
    },
    "mode": {"tokens": ["surprise_me", "custom"]},
    "extras_kind": {"tokens": ["syrup", "topping", "liqueur"]},
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def make_hass(strings_version=_MANIFEST["version"]):
    """MagicMock hass; `strings_version` seeds the setup-time stash.

    Passing None models a hass where `async_setup_entry` never ran
    (impossible in production — the stash is written before WS
    registration — but the handler must still degrade, not crash).
    """
    hass = MagicMock()
    hass.data = {}
    if strings_version is not None:
        hass.data[DOMAIN] = {"ui_strings_version": strings_version}
    return hass


def make_connection(is_admin=True):
    """MagicMock WS connection with a controllable admin flag."""
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    connection.user.is_admin = is_admin
    return connection


def call(hass, connection=None, msg_id=5):
    """Invoke the sync handler and return the sent result payload."""
    connection = connection or make_connection()
    panel_api._ws_vocab_get(hass, connection, {"id": msg_id, "type": VOCAB_TYPE})
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    _msg_id, payload = connection.send_result.call_args.args
    return payload


# ---------------------------------------------------------------------------
# Registration & schema (§9.2.2)
# ---------------------------------------------------------------------------


def test_vocab_command_registered():
    """async_register_panel_websocket registers vocab/get."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    assert VOCAB_TYPE in hass.data["websocket_api"]


def test_vocab_schema_takes_no_entry_id():
    """The command is machine-independent: no entry_id, no locale.

    A type-only command is stored with schema ``False`` in HA's registry
    — the framework then rejects ANY extra key (id + type only), which
    is exactly the "no arguments; not entry-scoped; no locale" §9.2.2
    contract. The source schema itself carries only the type key.
    """
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    _handler, schema = hass.data["websocket_api"][VOCAB_TYPE]
    assert schema is False  # type-only: framework rejects extra keys
    assert set(panel_api._VOCAB_GET_SCHEMA.schema) == {"type"}


def test_vocab_not_admin_gated():
    """A non-admin caller reaches the handler (deliberate, §9.2.2)."""
    hass = make_hass()
    panel_api.async_register_panel_websocket(hass)
    handler, _schema = hass.data["websocket_api"][VOCAB_TYPE]
    connection = make_connection(is_admin=False)
    handler(hass, connection, {"id": 5, "type": VOCAB_TYPE})
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()


# ---------------------------------------------------------------------------
# Payload (§9.2.2 pinned verbatim; §9.2.3 sources)
# ---------------------------------------------------------------------------


def test_vocab_payload_pinned_verbatim():
    """The full vocab block matches §9.2.2 byte-for-byte, order included."""
    payload = call(make_hass())
    assert payload["schema_version"] == 1
    assert payload["vocab"] == EXPECTED_VOCAB
    # Order-sensitive: token lists AND family order are wire order.
    assert list(payload["vocab"].keys()) == list(EXPECTED_VOCAB.keys())
    for family, expected in EXPECTED_VOCAB.items():
        assert payload["vocab"][family]["tokens"] == expected["tokens"], family


def test_vocab_strings_version_is_manifest_version():
    """strings_version mirrors the manifest via the setup-time stash."""
    payload = call(make_hass())
    assert payload["strings_version"] == _MANIFEST["version"]


def test_vocab_first_ws_call_of_session_gets_real_version():
    """vocab/get as the session's FIRST WS call never serves 'unknown'.

    The stash is written by async_setup_entry before WS registration
    (§9.2.2), so even with no prior i18n/get (the likely order for a
    sommelier screen with no machine connected) the version is resolved.
    """
    hass = make_hass()  # stash seeded exactly as async_setup_entry does
    assert "ui_strings_cache" not in hass.data[DOMAIN]  # no i18n ran
    payload = call(hass)
    assert payload["strings_version"] == _MANIFEST["version"]
    assert payload["strings_version"] != "unknown"


def test_vocab_without_stash_degrades_to_unknown():
    """No stash (setup never ran) serves 'unknown' instead of crashing."""
    payload = call(make_hass(strings_version=None))
    assert payload["strings_version"] == "unknown"


# ---------------------------------------------------------------------------
# Source pins: sommelier_api lists vs ai_recipes set duplicates (§9.2.3)
# ---------------------------------------------------------------------------


def test_ai_recipes_duplicates_are_set_equal():
    """The ai_recipes sets pin element membership, never order (§9.2.3)."""
    assert set(sa.VALID_MOODS) == ai_recipes.VALID_MOODS
    assert set(sa.VALID_OCCASIONS) == ai_recipes.VALID_OCCASIONS
    assert set(sa.VALID_CUP_SIZES) == ai_recipes.VALID_CUP_SIZES
    assert set(sa.VALID_GENERATE_TEMPERATURES) == (
        ai_recipes.VALID_TEMPERATURE_PREFS
    )
    assert set(sa.VALID_CAFFEINE_PREFS) == ai_recipes.VALID_CAFFEINE_PREFS
    assert set(sa.VALID_DIETARY) == ai_recipes.VALID_DIETARY


def test_volumes_ml_matches_cup_size_volumes():
    """The advisory volume map mirrors ai_recipes.CUP_SIZE_VOLUMES."""
    served = call(make_hass())["vocab"]["cup_size"]["volumes_ml"]
    assert served == {
        token: list(bounds)
        for token, bounds in ai_recipes.CUP_SIZE_VOLUMES.items()
    }


def test_extras_kind_matches_additive_slots():
    """extras_kind serves the singular slot tokens recipes carry, not the
    plural VALID_EXTRAS_CATEGORIES storage surface."""
    vocab = call(make_hass())["vocab"]
    assert vocab["extras_kind"]["tokens"] == list(sa._ADDITIVE_SLOTS)
    assert vocab["extras_kind"]["tokens"] != sa.VALID_EXTRAS_CATEGORIES


def test_generate_schema_uses_hoisted_temperature_constant():
    """The generate vol.In and the vocab read one named source (§9.2.3)."""
    hass = make_hass()
    sa.async_register_websocket_handlers(hass)
    _handler, schema = hass.data["websocket_api"][
        "melitta_barista/sommelier/generate"
    ]
    for token in sa.VALID_GENERATE_TEMPERATURES:
        schema({
            "id": 5, "type": "melitta_barista/sommelier/generate",
            "temperature": token,
        })
    with pytest.raises(vol.Invalid):
        schema({
            "id": 5, "type": "melitta_barista/sommelier/generate",
            "temperature": "hot_only",  # the dead superset must NOT pass
        })


# ---------------------------------------------------------------------------
# Free-form families are NOT served; dead constants are gone (§9.2.4)
# ---------------------------------------------------------------------------


def test_free_form_families_absent():
    """Milk, flavor notes, extras items, temperature_pref: never served."""
    vocab = call(make_hass())["vocab"]
    for family in (
        "milk", "milk_type", "milk_types",
        "flavor_note", "flavor_notes",
        "extras", "extras_categories",
        "temperature_pref", "temp_pref",
    ):
        assert family not in vocab, family


def test_dead_enum_constants_removed():
    """VALID_MILK_TYPES / VALID_FLAVOR_NOTES / VALID_TEMP_PREFS are gone
    so no future reader mistakes them for enforced enums."""
    for name in ("VALID_MILK_TYPES", "VALID_FLAVOR_NOTES", "VALID_TEMP_PREFS"):
        assert not hasattr(sa, name), name


# ---------------------------------------------------------------------------
# Cup-size normalization — v10 → v11 DB migration (§9.2.6.4)
# ---------------------------------------------------------------------------


_PROFILES_DDL = (
    "CREATE TABLE sommelier_profiles ("
    " id TEXT PRIMARY KEY,"
    " name TEXT NOT NULL,"
    " cup_size TEXT DEFAULT 'mug',"
    " temperature_pref TEXT DEFAULT 'hot_only',"
    " dietary TEXT DEFAULT '[]',"
    " caffeine_pref TEXT DEFAULT 'regular',"
    " is_active INTEGER NOT NULL DEFAULT 0,"
    " machine_profile INTEGER,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT"
    ")"
)

_PRESETS_DDL = (
    "CREATE TABLE sommelier_presets ("
    " id TEXT PRIMARY KEY,"
    " name TEXT NOT NULL,"
    " description TEXT,"
    " payload TEXT NOT NULL,"
    " is_system INTEGER NOT NULL DEFAULT 0,"
    " dynamic_occasion INTEGER NOT NULL DEFAULT 0,"
    " created_at TEXT NOT NULL,"
    " updated_at TEXT,"
    " machine_profile INTEGER"
    ")"
)


async def _make_v10_db(db_path: str, *, profiles_ddl: str | None = _PROFILES_DDL):
    """Hand-roll a v10 DB with legacy `espresso` cup-size tokens."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        await conn.execute(
            "INSERT INTO settings(key, value) VALUES('schema_version', '10')"
        )
        await conn.execute(_PRESETS_DDL)
        await conn.execute(
            "CREATE TABLE user_preferences (key TEXT PRIMARY KEY, value TEXT)"
        )
        await conn.execute(
            "INSERT INTO user_preferences(key, value)"
            " VALUES('default_cup_size', 'espresso')"
        )
        await conn.execute(
            "INSERT INTO user_preferences(key, value)"
            " VALUES('default_caffeine', 'espresso')"
        )
        if profiles_ddl is not None:
            await conn.execute(profiles_ddl)
            if "cup_size" in profiles_ddl:
                await conn.execute(
                    "INSERT INTO sommelier_profiles"
                    " (id, name, cup_size, created_at)"
                    " VALUES('p1', 'Legacy', 'espresso',"
                    " '2026-01-01T00:00:00+00:00')"
                )
                await conn.execute(
                    "INSERT INTO sommelier_profiles"
                    " (id, name, cup_size, created_at)"
                    " VALUES('p2', 'Modern', 'mug',"
                    " '2026-01-01T00:00:00+00:00')"
                )
        await conn.commit()


async def test_v10_to_v11_migration_rewrites_legacy_tokens():
    """`espresso` → `espresso_cup` in both columns; other rows untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        await _make_v10_db(db_path)

        db = SommelierDB(db_path)
        try:
            await db.async_setup()

            cursor = await db.db.execute(
                "SELECT id, cup_size FROM sommelier_profiles ORDER BY id"
            )
            rows = {row[0]: row[1] for row in await cursor.fetchall()}
            assert rows == {"p1": "espresso_cup", "p2": "mug"}

            cursor = await db.db.execute(
                "SELECT key, value FROM user_preferences ORDER BY key"
            )
            prefs = {row[0]: row[1] for row in await cursor.fetchall()}
            assert prefs["default_cup_size"] == "espresso_cup"
            # Only the default_cup_size key is rewritten — an unrelated
            # preference that happens to hold 'espresso' is untouched.
            assert prefs["default_caffeine"] == "espresso"

            cursor = await db.db.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cursor.fetchone()
            assert row[0] == str(SCHEMA_VERSION)
        finally:
            await db.async_close()


async def test_v10_to_v11_migration_stamp_withheld_on_failure():
    """Strict runner: a genuinely failing statement withholds the stamp."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        # A profiles table WITHOUT the cup_size column → "no such column"
        # — a real error (not idempotency, not a vacuous data rewrite) →
        # stamp stays at 10 so the migration retries next start.
        await _make_v10_db(
            db_path,
            profiles_ddl=(
                "CREATE TABLE sommelier_profiles"
                " (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
            ),
        )

        db = SommelierDB(db_path)
        try:
            await db.async_setup()
            cursor = await db.db.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cursor.fetchone()
            assert row[0] == "10"
        finally:
            await db.async_close()


async def test_v10_to_v11_migration_vacuous_on_missing_table():
    """An UPDATE data rewrite against a never-created table is vacuously
    complete (no rows to rewrite; minimal legacy DBs and fixtures) — the
    stamp advances instead of arming a pointless eternal retry."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        await _make_v10_db(db_path, profiles_ddl=None)

        db = SommelierDB(db_path)
        try:
            await db.async_setup()
            cursor = await db.db.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cursor.fetchone()
            assert row[0] == str(SCHEMA_VERSION)
            # The user_preferences half still ran.
            cursor = await db.db.execute(
                "SELECT value FROM user_preferences"
                " WHERE key='default_cup_size'"
            )
            row = await cursor.fetchone()
            assert row[0] == "espresso_cup"
        finally:
            await db.async_close()


async def test_fresh_db_stamps_v11():
    """A fresh install lands directly on the current schema version."""
    db = SommelierDB(":memory:")
    try:
        await db.async_setup()
        cursor = await db.db.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        )
        row = await cursor.fetchone()
        assert row[0] == str(SCHEMA_VERSION)
        assert SCHEMA_VERSION >= 11
    finally:
        await db.async_close()


# ---------------------------------------------------------------------------
# Cup-size normalization — profiles/add|update write path (§9.2.6.4)
# ---------------------------------------------------------------------------


def _make_hass_with_db(db: SommelierDB) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: {"sommelier_db": db}}
    return hass


async def test_profiles_add_normalizes_legacy_cup_size():
    """A 1.8.3-style add with cup_size='espresso' is corrected on ingest."""
    db = SommelierDB(":memory:")
    try:
        await db.async_setup()
        hass = _make_hass_with_db(db)
        connection = make_connection()
        handler = inspect.unwrap(sa.ws_profiles_add)

        await handler(hass, connection, {
            "id": 1, "type": "melitta_barista/sommelier/profiles/add",
            "name": "PWA", "preferences": {"cup_size": "espresso"},
        })

        connection.send_error.assert_not_called()
        _msg_id, payload = connection.send_result.call_args.args
        assert payload["profile"]["cup_size"] == "espresso_cup"
    finally:
        await db.async_close()


async def test_profiles_update_normalizes_legacy_cup_size():
    """A 1.8.3-style re-save of `espresso` is corrected on ingest."""
    db = SommelierDB(":memory:")
    try:
        await db.async_setup()
        profile = await db.async_add_profile({"name": "P", "cup_size": "mug"})
        hass = _make_hass_with_db(db)
        connection = make_connection()
        handler = inspect.unwrap(sa.ws_profiles_update)

        await handler(hass, connection, {
            "id": 1, "type": "melitta_barista/sommelier/profiles/update",
            "profile_id": profile["id"],
            "preferences": {"cup_size": "espresso"},
        })

        connection.send_error.assert_not_called()
        _msg_id, payload = connection.send_result.call_args.args
        assert payload["profile"]["cup_size"] == "espresso_cup"
    finally:
        await db.async_close()


async def test_profiles_add_valid_tokens_pass_through():
    """Served tokens are stored untouched by the alias map."""
    db = SommelierDB(":memory:")
    try:
        await db.async_setup()
        hass = _make_hass_with_db(db)
        connection = make_connection()
        handler = inspect.unwrap(sa.ws_profiles_add)

        await handler(hass, connection, {
            "id": 1, "type": "melitta_barista/sommelier/profiles/add",
            "name": "OK", "preferences": {"cup_size": "espresso_cup"},
        })

        _msg_id, payload = connection.send_result.call_args.args
        assert payload["profile"]["cup_size"] == "espresso_cup"
    finally:
        await db.async_close()
