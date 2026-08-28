"""P5a Tasks 1 & 2 — sommelier_presets DB methods + WS handlers."""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.melitta_barista.sommelier_db import (
    SCHEMA_VERSION,
    SommelierDB,
)


def _assert_result(connection, msg_id, expected: dict) -> None:
    """Assert send_result once with expected payload, ignoring schema_version.

    P6b (0.67.0) added a `schema_version: int` envelope to every WS response.
    """
    connection.send_result.assert_called_once()
    actual_id, actual_payload = connection.send_result.call_args.args
    assert actual_id == msg_id
    assert {
        k: v for k, v in actual_payload.items() if k != "schema_version"
    } == expected


@pytest.mark.asyncio
async def test_schema_version_is_current():
    """SCHEMA_VERSION tracks the current schema (>= 9 after P7a machine_profile)."""
    assert SCHEMA_VERSION >= 9


@pytest.mark.asyncio
async def test_add_preset_returns_id_and_persists():
    """Adding a preset returns its id and the row appears in list_presets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        payload = {"mode": "surprise_me", "servings": 2, "mood": "cozy"}
        preset_id = await db.async_add_preset("Morning routine", "Daily go-to", payload)
        assert isinstance(preset_id, str) and len(preset_id) > 0

        # Exclude P5b system presets — only the user row matters here.
        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(presets) == 1
        row = presets[0]
        assert row["id"] == preset_id
        assert row["name"] == "Morning routine"
        assert row["description"] == "Daily go-to"
        assert row["payload"] == payload
        assert isinstance(row["created_at"], str) and len(row["created_at"]) > 0
        assert row["updated_at"] is None

        await db.async_close()


@pytest.mark.asyncio
async def test_list_presets_orders_by_name_case_insensitive():
    """LOWER(name) ordering: Apple, morning, Zebra regardless of case."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_add_preset("Zebra", None, {"a": 1})
        await db.async_add_preset("Apple", None, {"a": 2})
        await db.async_add_preset("morning", None, {"a": 3})

        # Filter out P5b system presets — sort scope is the user rows.
        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        names = [p["name"] for p in presets]
        assert names == ["Apple", "morning", "Zebra"]

        await db.async_close()


@pytest.mark.asyncio
async def test_update_preset_patches_fields_and_sets_updated_at():
    """Partial patch updates only the supplied column and bumps updated_at."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset("Evening", "Original", {"x": 1})
        ok = await db.async_update_preset(preset_id, description="Updated text")
        assert ok is True

        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(presets) == 1
        row = presets[0]
        assert row["name"] == "Evening"
        assert row["description"] == "Updated text"
        assert row["payload"] == {"x": 1}
        assert row["updated_at"] is not None
        assert isinstance(row["updated_at"], str) and len(row["updated_at"]) > 0

        await db.async_close()


@pytest.mark.asyncio
async def test_update_preset_raises_when_no_fields():
    """Empty patch raises ValueError('no_fields')."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset("Anything", None, {"y": 2})
        with pytest.raises(ValueError) as exc_info:
            await db.async_update_preset(preset_id)
        assert exc_info.value.args[0] == "no_fields"

        await db.async_close()


@pytest.mark.asyncio
async def test_update_preset_unknown_id_returns_false():
    """Patching a non-existent id returns False, does not raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        ok = await db.async_update_preset("nonexistent-id", name="Whatever")
        assert ok is False

        await db.async_close()


@pytest.mark.asyncio
async def test_delete_preset_removes_row():
    """Delete returns True and the preset disappears from the listing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset("Doomed", None, {"z": 3})
        user_rows = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(user_rows) == 1

        removed = await db.async_delete_preset(preset_id)
        assert removed is True
        user_rows = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert user_rows == []

        await db.async_close()


@pytest.mark.asyncio
async def test_delete_preset_unknown_id_returns_false():
    """Deleting a non-existent id returns False, no exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        removed = await db.async_delete_preset("nonexistent-id")
        assert removed is False

        await db.async_close()


@pytest.mark.asyncio
async def test_migration_v6_to_v7_idempotent():
    """Re-opening the DB after v6→v7 leaves the table intact and usable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        db = SommelierDB(db_path)
        await db.async_setup()
        preset_id = await db.async_add_preset("Persistent", None, {"k": "v"})
        await db.async_close()

        db = SommelierDB(db_path)
        await db.async_setup()
        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(presets) == 1
        assert presets[0]["id"] == preset_id
        assert presets[0]["payload"] == {"k": "v"}

        await db.async_close()


# ── WS handlers (P5a Task 2) ──────────────────────────────────────────


async def _make_hass_with_db(db: SommelierDB) -> MagicMock:
    """Build a MagicMock hass exposing the DB at hass.data[DOMAIN]['sommelier_db']."""
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    return hass


def _make_connection() -> MagicMock:
    """Build a MagicMock connection with send_result / send_error stubs."""
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    return connection


@pytest.mark.asyncio
async def test_ws_presets_list_returns_db_rows():
    """ws_presets_list returns rows from async_list_presets with dict payload."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        await db.async_add_preset("Solo", "Single row", {"mode": "surprise_me"})

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {"id": 1, "type": "melitta_barista/sommelier/presets/list"}

        handler = inspect.unwrap(sa.ws_presets_list)
        await handler(hass, connection, msg)

        connection.send_result.assert_called_once()
        args, _ = connection.send_result.call_args
        assert args[0] == 1
        result = args[1]
        assert "presets" in result
        user_rows = [p for p in result["presets"] if not p.get("is_system")]
        assert len(user_rows) == 1
        row = user_rows[0]
        assert row["name"] == "Solo"
        assert row["description"] == "Single row"
        assert row["payload"] == {"mode": "surprise_me"}
        connection.send_error.assert_not_called()

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_add_persists_and_returns_id():
    """ws_presets_add returns an id and the row is persisted."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 7,
            "type": "melitta_barista/sommelier/presets/add",
            "name": "Morning",
            "description": "Light wake-up",
            "payload": {"mode": "surprise_me"},
        }

        handler = inspect.unwrap(sa.ws_presets_add)
        await handler(hass, connection, msg)

        connection.send_result.assert_called_once()
        args, _ = connection.send_result.call_args
        assert args[0] == 7
        result = args[1]
        assert isinstance(result["id"], str) and len(result["id"]) > 0
        connection.send_error.assert_not_called()

        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(presets) == 1
        assert presets[0]["name"] == "Morning"
        assert presets[0]["description"] == "Light wake-up"
        assert presets[0]["payload"] == {"mode": "surprise_me"}

        await db.async_close()


def test_ws_presets_add_validates_empty_name():
    """ws_presets_add schema rejects an empty name via voluptuous Length(min=1)."""
    from custom_components.melitta_barista import sommelier_api as sa

    schema = sa.ws_presets_add._ws_schema
    msg = {
        "id": 1,
        "type": "melitta_barista/sommelier/presets/add",
        "name": "",
        "payload": {"mode": "surprise_me"},
    }
    with pytest.raises(vol.Invalid):
        schema(msg)


@pytest.mark.asyncio
async def test_ws_presets_update_patches_description_only():
    """ws_presets_update with only description patches that field and leaves name."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        preset_id = await db.async_add_preset("Original", "Old desc", {"k": 1})

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 2,
            "type": "melitta_barista/sommelier/presets/update",
            "preset_id": preset_id,
            "description": "new",
        }

        handler = inspect.unwrap(sa.ws_presets_update)
        await handler(hass, connection, msg)

        _assert_result(connection, 2, {"updated": True})
        connection.send_error.assert_not_called()

        presets = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert len(presets) == 1
        assert presets[0]["name"] == "Original"
        assert presets[0]["description"] == "new"
        assert presets[0]["payload"] == {"k": 1}

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_update_empty_payload_returns_no_fields():
    """ws_presets_update with only preset_id surfaces 'no_fields' from DB layer."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        preset_id = await db.async_add_preset("Stay", None, {"k": 1})

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 3,
            "type": "melitta_barista/sommelier/presets/update",
            "preset_id": preset_id,
        }

        handler = inspect.unwrap(sa.ws_presets_update)
        await handler(hass, connection, msg)

        connection.send_error.assert_called_once()
        args, _ = connection.send_error.call_args
        assert args[0] == 3
        assert args[1] == "no_fields"
        connection.send_result.assert_not_called()

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_update_unknown_id_returns_not_found():
    """ws_presets_update on a missing preset_id surfaces 'not_found'."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 4,
            "type": "melitta_barista/sommelier/presets/update",
            "preset_id": "no-such-id",
            "name": "Whatever",
        }

        handler = inspect.unwrap(sa.ws_presets_update)
        await handler(hass, connection, msg)

        connection.send_error.assert_called_once()
        args, _ = connection.send_error.call_args
        assert args[0] == 4
        assert args[1] == "not_found"
        connection.send_result.assert_not_called()

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_delete_removes():
    """ws_presets_delete drops the row and returns {'deleted': True}."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        preset_id = await db.async_add_preset("Doomed", None, {"k": 1})

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 5,
            "type": "melitta_barista/sommelier/presets/delete",
            "preset_id": preset_id,
        }

        handler = inspect.unwrap(sa.ws_presets_delete)
        await handler(hass, connection, msg)

        _assert_result(connection, 5, {"deleted": True})
        connection.send_error.assert_not_called()
        user_rows = [p for p in await db.async_list_presets() if not p.get("is_system")]
        assert user_rows == []

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_delete_unknown_id_returns_not_found():
    """ws_presets_delete on a missing preset_id surfaces 'not_found'."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 6,
            "type": "melitta_barista/sommelier/presets/delete",
            "preset_id": "no-such-id",
        }

        handler = inspect.unwrap(sa.ws_presets_delete)
        await handler(hass, connection, msg)

        connection.send_error.assert_called_once()
        args, _ = connection.send_error.call_args
        assert args[0] == 6
        assert args[1] == "not_found"
        connection.send_result.assert_not_called()

        await db.async_close()


# ── P5b Task 1: system presets v7 → v8 + seeder + write-protect ───────


@pytest.mark.asyncio
async def test_migration_v7_to_v8_idempotent():
    """Closing and reopening a v8 DB keeps the two new columns in place."""
    import aiosqlite

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        db = SommelierDB(db_path)
        await db.async_setup()
        await db.async_close()

        db = SommelierDB(db_path)
        await db.async_setup()
        await db.async_close()

        async with aiosqlite.connect(db_path) as conn:
            cur = await conn.execute("PRAGMA table_info(sommelier_presets)")
            cols = {row[1] for row in await cur.fetchall()}
        assert "is_system" in cols
        assert "dynamic_occasion" in cols


@pytest.mark.asyncio
async def test_seeder_populates_four_system_presets():
    """Fresh DB seeds four built-in presets sorted by name (case-insensitive)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        presets = await db.async_list_presets()
        system_rows = [p for p in presets if p["is_system"]]
        assert len(system_rows) == 4
        names = [p["name"] for p in system_rows]
        # LOWER(name) ordering: 'After lunch', 'Guests', 'Morning', 'Work'
        assert names == ["After lunch", "Guests", "Morning", "Work"]
        for row in system_rows:
            assert row["is_system"] is True
            assert row["dynamic_occasion"] is True
            assert isinstance(row["payload"], dict)
            assert "name_key" in row["payload"]
            assert row["payload"]["name_key"].startswith("presets.system.")

        await db.async_close()


@pytest.mark.asyncio
async def test_seeder_idempotent_on_resetup():
    """Calling the seeder a second time inserts zero rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        inserted = await db.async_seed_system_presets()
        assert inserted == 0

        presets = await db.async_list_presets()
        system_rows = [p for p in presets if p["is_system"]]
        assert len(system_rows) == 4

        await db.async_close()


@pytest.mark.asyncio
async def test_seeder_runs_on_legacy_v7_db_upgrade():
    """A v7-style DB (no system rows, no new columns) upgrades cleanly and seeds."""
    import aiosqlite

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        # Hand-roll a minimal v7 schema: settings table + the v6→v7
        # sommelier_presets shape (no is_system / no dynamic_occasion).
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            await conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', '7')"
            )
            await conn.execute(
                "CREATE TABLE sommelier_presets ("
                "  id TEXT PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  description TEXT,"
                "  payload TEXT NOT NULL,"
                "  created_at TEXT NOT NULL,"
                "  updated_at TEXT"
                ")"
            )
            # Tables every real v7 DB has and the v8->v9 migration ALTERs —
            # the strict migration runner refuses to stamp the version when a
            # statement fails for a reason other than idempotency.
            await conn.execute("CREATE TABLE favorites (id TEXT PRIMARY KEY)")
            await conn.execute(
                "CREATE TABLE generation_sessions (id TEXT PRIMARY KEY)"
            )
            await conn.commit()

        db = SommelierDB(db_path)
        await db.async_setup()

        # Schema version bumped.
        async with aiosqlite.connect(db_path) as conn:
            cur = await conn.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cur.fetchone()
            assert row[0] == str(SCHEMA_VERSION)
            cur = await conn.execute("PRAGMA table_info(sommelier_presets)")
            cols = {r[1] for r in await cur.fetchall()}
        assert "is_system" in cols
        assert "dynamic_occasion" in cols

        # Seeder ran during async_setup → four system rows.
        presets = await db.async_list_presets()
        system_rows = [p for p in presets if p["is_system"]]
        assert len(system_rows) == 4

        await db.async_close()


@pytest.mark.asyncio
async def test_update_system_preset_raises_readonly():
    """Updating a seeded system preset raises ValueError('system_preset_readonly')."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        with pytest.raises(ValueError) as exc_info:
            await db.async_update_preset("sys_morning", description="hacked")
        assert exc_info.value.args[0] == "system_preset_readonly"

        # Confirm the row was untouched.
        presets = await db.async_list_presets()
        morning = next(p for p in presets if p["id"] == "sys_morning")
        assert morning["description"] == "Energizing brew for the start of the day."

        await db.async_close()


@pytest.mark.asyncio
async def test_delete_system_preset_raises_readonly():
    """Deleting a seeded system preset raises ValueError('system_preset_readonly')."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        with pytest.raises(ValueError) as exc_info:
            await db.async_delete_preset("sys_work")
        assert exc_info.value.args[0] == "system_preset_readonly"

        # Confirm the row still exists.
        presets = await db.async_list_presets()
        assert any(p["id"] == "sys_work" for p in presets)

        await db.async_close()


@pytest.mark.asyncio
async def test_user_preset_update_still_works_after_seeding():
    """Seeded DB still allows user presets to be patched and deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset(
            "My evening", "User-owned", {"mode": "surprise_me"}
        )
        ok = await db.async_update_preset(preset_id, description="updated text")
        assert ok is True

        presets = await db.async_list_presets()
        mine = next(p for p in presets if p["id"] == preset_id)
        assert mine["description"] == "updated text"
        assert mine["is_system"] is False
        assert mine["dynamic_occasion"] is False

        removed = await db.async_delete_preset(preset_id)
        assert removed is True

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_update_system_preset_returns_readonly_error():
    """ws_presets_update on a system preset surfaces 'system_preset_readonly'."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 20,
            "type": "melitta_barista/sommelier/presets/update",
            "preset_id": "sys_morning",
            "description": "tampered",
        }

        handler = inspect.unwrap(sa.ws_presets_update)
        await handler(hass, connection, msg)

        connection.send_error.assert_called_once()
        args, _ = connection.send_error.call_args
        assert args[0] == 20
        assert args[1] == "system_preset_readonly"
        connection.send_result.assert_not_called()

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_delete_system_preset_returns_readonly_error():
    """ws_presets_delete on a system preset surfaces 'system_preset_readonly'."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 21,
            "type": "melitta_barista/sommelier/presets/delete",
            "preset_id": "sys_work",
        }

        handler = inspect.unwrap(sa.ws_presets_delete)
        await handler(hass, connection, msg)

        connection.send_error.assert_called_once()
        args, _ = connection.send_error.call_args
        assert args[0] == 21
        assert args[1] == "system_preset_readonly"
        connection.send_result.assert_not_called()

        # Row still there.
        rows = [p for p in await db.async_list_presets() if p["id"] == "sys_work"]
        assert len(rows) == 1

        await db.async_close()
