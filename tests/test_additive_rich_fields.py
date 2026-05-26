"""P8a R1 slice 1 — rich-field catalogue for syrups & toppings.

Verifies the new optional metadata columns (`producer_id`, `variant`,
`flavor_notes`, `composition`, `attributes`) work end-to-end across:
  * CREATE on fresh DBs
  * ALTER on legacy DBs (pre-P8a schema, only had `available`)
  * list / add / update WS handlers (JSON encoding/decoding, partial patches,
    full-replacement semantics for `attributes`, defensive handling of
    invalid JSON in `flavor_notes`).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from custom_components.melitta_barista import panel_api


# ── shared shim & fixtures ────────────────────────────────────────────────


class _DbShim:
    """Minimal stand-in for SommelierDB exposing `._db` for raw aiosqlite access."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn


@pytest.fixture
async def fresh_db_shim():
    """In-memory DB with the current (post-P8a) panel schema."""
    conn = await aiosqlite.connect(":memory:")
    shim = _DbShim(conn)
    await panel_api._ensure_panel_schema(shim)
    try:
        yield shim
    finally:
        await conn.close()


@pytest.fixture
async def legacy_db_shim():
    """In-memory DB with the pre-P8a syrups/toppings schema (only `available`).

    Mirrors the on-disk state of a real HA install that was set up under
    P4a (available column added) but never upgraded to P8a. The migration
    must add the five rich-field columns idempotently.
    """
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript("""
        CREATE TABLE producers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            country TEXT,
            website TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE syrups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
            available INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE toppings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
            available INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    shim = _DbShim(conn)
    try:
        yield shim
    finally:
        await conn.close()


def _unwrap(handler):
    """Peel HA WS decorators until we reach the bare async function."""
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
        if inspect.iscoroutinefunction(handler):
            break
    return handler


async def _call_handler(handler, shim, msg):
    """Helper: invoke a handler with mocked connection and patched _async_get_db."""
    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=shim),
    ):
        await handler(hass, connection, msg)
    return connection


def _list_payload(connection):
    """Extract the (id, payload) from a captured send_result call."""
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    return msg_id, payload


# ── 1. Fresh schema has all rich-field columns ────────────────────────────


@pytest.mark.asyncio
async def test_fresh_schema_has_rich_fields(fresh_db_shim):
    """A brand-new DB exposes producer_id/variant/flavor_notes/composition/attributes."""
    expected_columns = {
        "producer_id", "variant", "flavor_notes", "composition", "attributes",
    }
    for table in ("syrups", "toppings"):
        cursor = await fresh_db_shim._db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cursor.fetchall()}
        missing = expected_columns - cols
        assert not missing, f"`{table}` missing rich-field columns: {missing}"


# ── 2. Legacy DB gets the columns via the ALTER guard ─────────────────────


@pytest.mark.asyncio
async def test_legacy_db_gets_rich_fields_via_alter(legacy_db_shim):
    """Pre-P8a syrups/toppings tables get the five rich-field columns added."""
    # Sanity: confirm the pre-state is what we expect.
    cursor = await legacy_db_shim._db.execute("PRAGMA table_info(syrups)")
    pre_cols = {row[1] for row in await cursor.fetchall()}
    assert "producer_id" not in pre_cols
    assert "variant" not in pre_cols

    # Run the migration.
    await panel_api._ensure_panel_schema(legacy_db_shim)

    expected_columns = {
        "producer_id", "variant", "flavor_notes", "composition", "attributes",
    }
    for table in ("syrups", "toppings"):
        cursor = await legacy_db_shim._db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cursor.fetchall()}
        missing = expected_columns - cols
        assert not missing, f"after migration, `{table}` still missing {missing}"

    # Idempotent: a second call doesn't raise "duplicate column" errors.
    await panel_api._ensure_panel_schema(legacy_db_shim)


# ── 3. Add with full rich payload, round-trip via list ───────────────────


@pytest.mark.asyncio
async def test_add_syrup_with_full_rich_payload(fresh_db_shim):
    """A syrup added with all rich fields round-trips through list with Python types."""
    ws_add = _unwrap(panel_api._SYRUPS_HANDLERS[1])
    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])

    add_conn = await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/syrups/add",
        "name": "Vanilla deluxe",
        "brand": "Monin",
        "producer_id": 42,
        "variant": "Sugar-free",
        "flavor_notes": ["vanilla", "caramel"],
        "composition": "vegan, sugar-free",
        "attributes": {"vegan": True, "sugar_free": True},
    })
    _, add_payload = _list_payload(add_conn)
    assert "id" in add_payload

    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 2, "type": "melitta_barista/syrups/list",
    })
    _, payload = _list_payload(list_conn)
    assert len(payload["syrups"]) == 1
    row = payload["syrups"][0]
    assert row["name"] == "Vanilla deluxe"
    assert row["producer_id"] == 42
    assert row["variant"] == "Sugar-free"
    assert row["flavor_notes"] == ["vanilla", "caramel"]
    assert row["composition"] == "vegan, sugar-free"
    assert row["attributes"] == {"vegan": True, "sugar_free": True}


# ── 4. Add without rich fields keeps them NULL ────────────────────────────


@pytest.mark.asyncio
async def test_add_syrup_with_no_rich_fields_returns_nulls(fresh_db_shim):
    """Omitted rich fields surface as None / None / None / None / None on read."""
    ws_add = _unwrap(panel_api._SYRUPS_HANDLERS[1])
    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])

    await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/syrups/add",
        "name": "Plain hazelnut",
    })

    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 2, "type": "melitta_barista/syrups/list",
    })
    _, payload = _list_payload(list_conn)
    row = payload["syrups"][0]
    assert row["producer_id"] is None
    assert row["variant"] is None
    assert row["flavor_notes"] is None
    assert row["composition"] is None
    assert row["attributes"] is None


# ── 5. Defensive JSON decoding ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_handles_invalid_flavor_notes_gracefully(fresh_db_shim):
    """A row with a garbage flavor_notes payload lists with flavor_notes=None."""
    # Backdoor the bad payload (handler would never produce this, but a
    # hand-edited DB might).
    await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, "
        "flavor_notes, attributes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Bad row", None, None, 1, "not valid json", "{nope", "2026-01-01T00:00:00+00:00"),
    )
    await fresh_db_shim._db.commit()

    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])
    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 1, "type": "melitta_barista/syrups/list",
    })
    _, payload = _list_payload(list_conn)
    row = payload["syrups"][0]
    assert row["name"] == "Bad row"
    assert row["flavor_notes"] is None
    assert row["attributes"] is None


# ── 6. Partial update doesn't clobber unrelated rich fields ──────────────


@pytest.mark.asyncio
async def test_update_syrup_partial_patches_rich_fields(fresh_db_shim):
    """Patching `variant` alone leaves the other rich fields untouched."""
    ws_add = _unwrap(panel_api._SYRUPS_HANDLERS[1])
    ws_update = _unwrap(panel_api._SYRUPS_UPDATE)
    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])

    add_conn = await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/syrups/add",
        "name": "Caramel",
        "variant": "Original",
        "flavor_notes": ["caramel", "butter"],
        "composition": "sugar, water, natural flavor",
        "attributes": {"vegan": False},
    })
    _, add_payload = _list_payload(add_conn)
    syrup_id = add_payload["id"]

    update_conn = await _call_handler(ws_update, fresh_db_shim, {
        "id": 2,
        "type": "melitta_barista/syrups/update",
        "additive_id": syrup_id,
        "variant": "Updated",
    })
    update_conn.send_error.assert_not_called()
    _, update_payload = _list_payload(update_conn)
    # Ignore schema_version envelope (P6b) — assert on the business payload.
    assert {
        k: v for k, v in update_payload.items() if k != "schema_version"
    } == {"updated": True}

    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 3, "type": "melitta_barista/syrups/list",
    })
    _, payload = _list_payload(list_conn)
    row = payload["syrups"][0]
    assert row["variant"] == "Updated"
    # Unchanged fields stay intact.
    assert row["flavor_notes"] == ["caramel", "butter"]
    assert row["composition"] == "sugar, water, natural flavor"
    assert row["attributes"] == {"vegan": False}


# ── 7. attributes patch is full replacement, not merge ───────────────────


@pytest.mark.asyncio
async def test_update_syrup_with_attributes_replaces_full_object(fresh_db_shim):
    """`attributes` patch overwrites the column wholesale — semantic: replace."""
    ws_add = _unwrap(panel_api._SYRUPS_HANDLERS[1])
    ws_update = _unwrap(panel_api._SYRUPS_UPDATE)
    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])

    add_conn = await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/syrups/add",
        "name": "Mint",
        "attributes": {"vegan": True},
    })
    _, add_payload = _list_payload(add_conn)
    syrup_id = add_payload["id"]

    await _call_handler(ws_update, fresh_db_shim, {
        "id": 2,
        "type": "melitta_barista/syrups/update",
        "additive_id": syrup_id,
        "attributes": {"sugar_free": True},
    })

    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 3, "type": "melitta_barista/syrups/list",
    })
    _, payload = _list_payload(list_conn)
    row = payload["syrups"][0]
    # Full replacement, not merge — `vegan` is gone.
    assert row["attributes"] == {"sugar_free": True}


# ── 8. Topping factory shares behaviour ───────────────────────────────────


@pytest.mark.asyncio
async def test_topping_handlers_share_rich_field_behaviour(fresh_db_shim):
    """Toppings table goes through the same factory and accepts the same payload."""
    ws_add = _unwrap(panel_api._TOPPINGS_HANDLERS[1])
    ws_list = _unwrap(panel_api._TOPPINGS_HANDLERS[0])

    await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/toppings/add",
        "name": "Cocoa powder",
        "brand": "Valrhona",
        "producer_id": 7,
        "variant": "Dark",
        "flavor_notes": ["dark chocolate", "earthy"],
        "composition": "100% cocoa",
        "attributes": {"vegan": True, "gluten_free": True},
    })

    list_conn = await _call_handler(ws_list, fresh_db_shim, {
        "id": 2, "type": "melitta_barista/toppings/list",
    })
    _, payload = _list_payload(list_conn)
    row = payload["toppings"][0]
    assert row["name"] == "Cocoa powder"
    assert row["producer_id"] == 7
    assert row["variant"] == "Dark"
    assert row["flavor_notes"] == ["dark chocolate", "earthy"]
    assert row["composition"] == "100% cocoa"
    assert row["attributes"] == {"vegan": True, "gluten_free": True}


# ── Bonus: verify on-disk encoding to catch silent regressions ────────────


@pytest.mark.asyncio
async def test_add_persists_json_typed_columns_as_text(fresh_db_shim):
    """flavor_notes and attributes hit disk as valid JSON strings, not raw lists/dicts."""
    ws_add = _unwrap(panel_api._SYRUPS_HANDLERS[1])
    await _call_handler(ws_add, fresh_db_shim, {
        "id": 1,
        "type": "melitta_barista/syrups/add",
        "name": "Persistence check",
        "flavor_notes": ["a", "b"],
        "attributes": {"k": 1},
    })
    cursor = await fresh_db_shim._db.execute(
        "SELECT flavor_notes, attributes FROM syrups"
    )
    row = await cursor.fetchone()
    assert json.loads(row[0]) == ["a", "b"]
    assert json.loads(row[1]) == {"k": 1}
