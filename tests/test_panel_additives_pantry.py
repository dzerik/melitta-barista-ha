"""P4a Task 1 — `available` column on syrups/toppings catalogue tables.

Covers:
- Fresh DB: `available` column present via CREATE.
- Legacy DB: `available` column added via idempotent ALTER inside
  `_ensure_panel_schema`.
- List handler returns `available` field with correct values.
- Update handler accepts an `available` bool and persists it.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from custom_components.melitta_barista import panel_api


class _DbShim:
    """Minimal stand-in for SommelierDB exposing the `._db` attribute.

    `panel_api` reaches into `db._db` directly for its raw aiosqlite handle —
    we mirror that so handlers can use this object without the full SommelierDB
    init path.

    For tests that exercise the catalogue → user_extras mirror, we also expose
    an `async_set_extra_available` method that operates on the same connection
    (mirroring `SommelierDB.async_set_extra_available` exactly).
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._db = conn

    async def async_set_extra_available(
        self, category: str, item: str, available: bool
    ) -> None:
        """Test-shim mirror of SommelierDB.async_set_extra_available."""
        flag = 1 if available else 0
        cursor = await self._db.execute(
            "SELECT 1 FROM user_extras WHERE category = ? AND item = ?",
            (category, item),
        )
        row = await cursor.fetchone()
        if row is None:
            await self._db.execute(
                "INSERT INTO user_extras (category, item, available) VALUES (?, ?, ?)",
                (category, item, flag),
            )
        else:
            await self._db.execute(
                "UPDATE user_extras SET available = ? WHERE category = ? AND item = ?",
                (flag, category, item),
            )
        await self._db.commit()


async def _create_user_extras(conn: aiosqlite.Connection) -> None:
    """Mirror sommelier_db's user_extras schema for panel-handler tests.

    Production schema lives in `sommelier_db.SCHEMA_SQL`; the test fixture only
    bootstraps panel-side tables, so we add this one explicitly when a test
    needs to assert on the catalogue → user_extras mirror.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_extras (
            category    TEXT NOT NULL,
            item        TEXT NOT NULL,
            available   INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (category, item)
        )
    """)
    await conn.commit()


@pytest.fixture
async def fresh_db_shim():
    """Yield a `_DbShim` backed by an in-memory aiosqlite DB with panel schema.

    Also creates `user_extras` so tests that hit the catalogue → user_extras
    mirror (P4a Task 2) don't blow up on missing table.
    """
    conn = await aiosqlite.connect(":memory:")
    shim = _DbShim(conn)
    await panel_api._ensure_panel_schema(shim)
    await _create_user_extras(conn)
    try:
        yield shim
    finally:
        await conn.close()


@pytest.fixture
async def legacy_db_shim():
    """Yield a `_DbShim` with a pre-migration `syrups` table (no `available`).

    `_ensure_panel_schema` is NOT called here — the caller drives the migration
    explicitly to make the assertion target obvious.
    """
    conn = await aiosqlite.connect(":memory:")
    # Pre-migration schema (no `available` column).
    await conn.executescript("""
        CREATE TABLE syrups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE toppings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
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
    """Unwrap HA WS decorators down to the bare coroutine function."""
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
        if inspect.iscoroutinefunction(handler):
            break
    return handler


@pytest.mark.asyncio
async def test_fresh_schema_has_available_column(fresh_db_shim):
    """A brand-new DB exposes `available` (default 1) on both additive tables."""
    for table in ("syrups", "toppings"):
        cursor = await fresh_db_shim._db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)
        cols = {r[1]: r for r in rows}
        assert "available" in cols, f"`available` missing on fresh `{table}`"
        col = cols["available"]
        assert col[2].upper() == "INTEGER"
        assert col[3] == 1  # NOT NULL
        # SQLite stores the default as a string literal — accept "1" or 1.
        assert str(col[4]) == "1"


@pytest.mark.asyncio
async def test_legacy_db_gets_available_via_alter(legacy_db_shim):
    """Legacy syrups table (no `available`) gets the column added with DEFAULT 1."""
    # Pre-populate a row that pre-dates the migration.
    await legacy_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("Vanilla", "Monin", None, "2026-01-01T00:00:00+00:00"),
    )
    await legacy_db_shim._db.commit()

    # Run the migration.
    await panel_api._ensure_panel_schema(legacy_db_shim)

    # Column now exists.
    cursor = await legacy_db_shim._db.execute("PRAGMA table_info(syrups)")
    cols = {r[1] for r in await cursor.fetchall()}
    assert "available" in cols

    # The legacy row inherits DEFAULT 1 (sqlite backfills on ALTER ADD COLUMN
    # with a literal default).
    cursor = await legacy_db_shim._db.execute(
        "SELECT name, available FROM syrups WHERE name = ?", ("Vanilla",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "Vanilla"
    assert row[1] == 1

    # Second call is a no-op (idempotent): doesn't raise "duplicate column".
    await panel_api._ensure_panel_schema(legacy_db_shim)


@pytest.mark.asyncio
async def test_list_returns_available_field(fresh_db_shim):
    """list handler returns `available` for each row, honoring the stored bit."""
    # One available syrup, one out-of-stock.
    await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Hazelnut", "Monin", None, 1, "2026-01-01T00:00:00+00:00"),
    )
    await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Caramel", "Monin", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    await fresh_db_shim._db.commit()

    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_list(hass, connection, {"id": 1, "type": "melitta_barista/syrups/list"})

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    _msg_id, payload = connection.send_result.call_args.args
    assert _msg_id == 1
    by_name = {row["name"]: row for row in payload["syrups"]}
    assert by_name["Hazelnut"]["available"] is True
    assert by_name["Caramel"]["available"] is False


@pytest.mark.asyncio
async def test_update_can_toggle_available(fresh_db_shim):
    """update handler accepts `available=False` and persists it as 0."""
    cursor = await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Almond", "Monin", None, 1, "2026-01-01T00:00:00+00:00"),
    )
    syrup_id = cursor.lastrowid
    await fresh_db_shim._db.commit()

    ws_update = _unwrap(panel_api._SYRUPS_UPDATE)

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_update(
            hass,
            connection,
            {
                "id": 2,
                "type": "melitta_barista/syrups/update",
                "additive_id": syrup_id,
                "available": False,
            },
        )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once_with(2, {"updated": True})

    cursor = await fresh_db_shim._db.execute(
        "SELECT available FROM syrups WHERE id = ?", (syrup_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 0

    # Now re-list and confirm the public shape exposes the toggled bool.
    ws_list = _unwrap(panel_api._SYRUPS_HANDLERS[0])
    list_conn = MagicMock()
    list_conn.send_result = MagicMock()
    list_conn.send_error = MagicMock()
    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_list(hass, list_conn, {"id": 3, "type": "melitta_barista/syrups/list"})

    list_conn.send_error.assert_not_called()
    _msg_id, payload = list_conn.send_result.call_args.args
    assert _msg_id == 3
    assert len(payload["syrups"]) == 1
    assert payload["syrups"][0]["available"] is False


@pytest.mark.asyncio
async def test_update_no_fields_still_errors(fresh_db_shim):
    """With no patchable fields the handler still reports `no_fields`."""
    cursor = await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Mint", "Monin", None, 1, "2026-01-01T00:00:00+00:00"),
    )
    syrup_id = cursor.lastrowid
    await fresh_db_shim._db.commit()

    ws_update = _unwrap(panel_api._SYRUPS_UPDATE)

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_update(
            hass,
            connection,
            {
                "id": 4,
                "type": "melitta_barista/syrups/update",
                "additive_id": syrup_id,
            },
        )

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    args = connection.send_error.call_args.args
    assert args[0] == 4
    assert args[1] == "no_fields"


# ── P4a Task 2: set_available endpoint + user_extras mirror ───────────────


@pytest.mark.asyncio
async def test_set_available_endpoint_updates_catalogue_and_mirrors_to_user_extras(
    fresh_db_shim,
):
    """set_available toggles the catalogue row AND mirrors into user_extras."""
    cursor = await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Vanilla", "Monin", None, 1, "2026-01-01T00:00:00+00:00"),
    )
    syrup_id = cursor.lastrowid
    await fresh_db_shim._db.commit()

    ws_set_available = _unwrap(panel_api._SYRUPS_SET_AVAILABLE)

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_set_available(
            hass,
            connection,
            {
                "id": 10,
                "type": "melitta_barista/syrups/set_available",
                "additive_id": syrup_id,
                "available": False,
            },
        )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once_with(10, {"updated": True})

    # Catalogue row got toggled.
    cursor = await fresh_db_shim._db.execute(
        "SELECT available FROM syrups WHERE id = ?", (syrup_id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 0

    # user_extras mirror reflects the catalogue state.
    cursor = await fresh_db_shim._db.execute(
        "SELECT available FROM user_extras WHERE category = ? AND item = ?",
        ("syrups", "Vanilla"),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_set_available_unknown_id_returns_not_found(fresh_db_shim):
    """A missing additive_id surfaces `not_found` via send_error."""
    ws_set_available = _unwrap(panel_api._SYRUPS_SET_AVAILABLE)

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_set_available(
            hass,
            connection,
            {
                "id": 11,
                "type": "melitta_barista/syrups/set_available",
                "additive_id": 999999,
                "available": True,
            },
        )

    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once()
    args = connection.send_error.call_args.args
    assert args[0] == 11
    assert args[1] == "not_found"


@pytest.mark.asyncio
async def test_set_available_enabling_inserts_into_user_extras(fresh_db_shim):
    """Toggling an out-of-stock syrup back ON inserts a fresh user_extras row."""
    cursor = await fresh_db_shim._db.execute(
        "INSERT INTO syrups (name, brand, notes, available, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Hazelnut", "Monin", None, 0, "2026-01-01T00:00:00+00:00"),
    )
    syrup_id = cursor.lastrowid
    await fresh_db_shim._db.commit()

    # Sanity: no row in user_extras yet.
    cursor = await fresh_db_shim._db.execute(
        "SELECT COUNT(*) FROM user_extras WHERE category = ? AND item = ?",
        ("syrups", "Hazelnut"),
    )
    assert (await cursor.fetchone())[0] == 0

    ws_set_available = _unwrap(panel_api._SYRUPS_SET_AVAILABLE)

    hass = MagicMock()
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    with patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=fresh_db_shim),
    ):
        await ws_set_available(
            hass,
            connection,
            {
                "id": 12,
                "type": "melitta_barista/syrups/set_available",
                "additive_id": syrup_id,
                "available": True,
            },
        )

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once_with(12, {"updated": True})

    # Catalogue row now ON.
    cursor = await fresh_db_shim._db.execute(
        "SELECT available FROM syrups WHERE id = ?", (syrup_id,)
    )
    assert (await cursor.fetchone())[0] == 1

    # user_extras row freshly inserted with available=1.
    cursor = await fresh_db_shim._db.execute(
        "SELECT available FROM user_extras WHERE category = ? AND item = ?",
        ("syrups", "Hazelnut"),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1
