"""Tests for capabilities persistence in sommelier DB."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from custom_components.melitta_barista.sommelier_db import (
    SCHEMA_VERSION,
    SommelierDB,
)


@pytest.mark.asyncio
async def test_schema_version_is_5():
    """SCHEMA_VERSION constant bumped to 5 (machine_phases column added)."""
    assert SCHEMA_VERSION == 5


@pytest.mark.asyncio
async def test_save_and_get_capabilities_roundtrip():
    """Save, then get returns the same JSON blob with probed_at iso8601 string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = SommelierDB(db_path)
        await db.async_setup()

        payload = json.dumps({"family_key": "barista_ts", "supported_processes": ["coffee", "milk"]})
        await db.async_save_capabilities("entry_123", payload)

        row = await db.async_get_capabilities("entry_123")
        assert row is not None
        assert row["json_payload"] == payload
        assert row["entry_id"] == "entry_123"
        # probed_at must be set to a non-empty iso8601 string
        assert isinstance(row["probed_at"], str) and len(row["probed_at"]) > 0
        assert row["schema_version"] == 1

        await db.async_close()


@pytest.mark.asyncio
async def test_get_capabilities_returns_none_when_missing():
    """Lookup for an unknown entry_id returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = SommelierDB(db_path)
        await db.async_setup()
        assert await db.async_get_capabilities("unknown") is None
        await db.async_close()


@pytest.mark.asyncio
async def test_save_overwrites_existing():
    """Calling save twice for same entry_id replaces, doesn't duplicate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = SommelierDB(db_path)
        await db.async_setup()

        await db.async_save_capabilities("entry_x", '{"v": 1}')
        await db.async_save_capabilities("entry_x", '{"v": 2}')

        row = await db.async_get_capabilities("entry_x")
        assert row["json_payload"] == '{"v": 2}'

        await db.async_close()


@pytest.mark.asyncio
async def test_migration_from_v3_preserves_data():
    """Existing v3 DB upgraded to current schema keeps its previous rows AND has the new table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        import aiosqlite

        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            await conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', '3')"
            )
            await conn.execute(
                "CREATE TABLE coffee_beans (id TEXT PRIMARY KEY, brand TEXT, product TEXT)"
            )
            await conn.execute(
                "INSERT INTO coffee_beans(id, brand, product) VALUES('b1', 'Lavazza', 'Crema')"
            )
            await conn.commit()

        db = SommelierDB(db_path)
        await db.async_setup()

        # Old data still there
        async with db._lock:
            cur = await db._db.execute("SELECT brand, product FROM coffee_beans WHERE id = 'b1'")
            row = await cur.fetchone()
        # sommelier_db sets aiosqlite.Row factory; sqlite3.Row does not __eq__
        # cleanly against a plain tuple, so compare via tuple().
        assert tuple(row) == ("Lavazza", "Crema")

        # New table works
        await db.async_save_capabilities("e1", "{}")
        assert await db.async_get_capabilities("e1") is not None

        # Schema bumped
        async with db._lock:
            cur = await db._db.execute("SELECT value FROM settings WHERE key='schema_version'")
            row = await cur.fetchone()
        assert row[0] == str(SCHEMA_VERSION)

        await db.async_close()
