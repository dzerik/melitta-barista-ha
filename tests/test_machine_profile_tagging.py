"""P7a Task 1 — machine_profile column on presets / favorites / generation_sessions.

Verifies the v8→v9 DB migration (additive INTEGER column), CRUD threading, and
the optional `machine_profile_filter` on list methods. NULL = shared row
(included by every filter); 1..n binds the row to a specific machine hardware
profile slot.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest

from custom_components.melitta_barista.sommelier_db import (
    SCHEMA_VERSION,
    SommelierDB,
)


# ── helpers ───────────────────────────────────────────────────────────


def _minimal_favorite_data(**overrides) -> dict:
    """Minimal `data` dict accepted by async_add_favorite."""
    base = {
        "name": "Favorite",
        "description": "",
        "blend": 1,
        "machine_phases": [
            {
                "component": {"process": "coffee", "portion_ml": 40},
                "user_action_before": [],
            }
        ],
        "steps": [{"order": 1, "action": "brew", "phase": "during"}],
    }
    base.update(overrides)
    return base


def _minimal_session_kwargs(**overrides) -> dict:
    """Minimal kwargs for async_create_session (recipes list excluded)."""
    base = {
        "mode": "surprise_me",
        "preference": None,
        "hopper1_bean_id": None,
        "hopper2_bean_id": None,
        "milk_types": [],
        "llm_agent": None,
        "recipes": [
            {
                "name": "R",
                "description": "",
                "blend": 1,
                "machine_phases": [
                    {
                        "component": {"process": "coffee", "portion_ml": 40},
                        "user_action_before": [],
                    }
                ],
                "steps": [{"order": 1, "action": "brew", "phase": "during"}],
            }
        ],
    }
    base.update(overrides)
    return base


# ── 1. fresh-DB column presence on three tables ───────────────────────


@pytest.mark.asyncio
async def test_migration_v8_to_v9_adds_machine_profile_columns_to_three_tables():
    """Fresh DB exposes machine_profile on presets / favorites / generation_sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        db = SommelierDB(db_path)
        await db.async_setup()
        await db.async_close()

        async with aiosqlite.connect(db_path) as conn:
            for table in ("sommelier_presets", "favorites", "generation_sessions"):
                cur = await conn.execute(f"PRAGMA table_info({table})")
                cols = {row[1] for row in await cur.fetchall()}
                assert "machine_profile" in cols, (
                    f"machine_profile missing from {table} after v9 setup"
                )


# ── 2. legacy v8 DB upgraded via ALTER TABLE ──────────────────────────


@pytest.mark.asyncio
async def test_legacy_v8_db_gets_machine_profile_via_alter():
    """A hand-rolled v8 DB upgrades cleanly: ALTER adds the column on all three tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        # Hand-roll a minimal v8 schema (no machine_profile column on the
        # three target tables) and stamp settings.schema_version = 8.
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            await conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', '8')"
            )
            await conn.execute(
                "CREATE TABLE sommelier_presets ("
                " id TEXT PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " description TEXT,"
                " payload TEXT NOT NULL,"
                " is_system INTEGER NOT NULL DEFAULT 0,"
                " dynamic_occasion INTEGER NOT NULL DEFAULT 0,"
                " created_at TEXT NOT NULL,"
                " updated_at TEXT"
                ")"
            )
            await conn.execute(
                "CREATE TABLE favorites ("
                " id TEXT PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " description TEXT NOT NULL,"
                " blend INTEGER NOT NULL,"
                " component1 TEXT NOT NULL,"
                " component2 TEXT NOT NULL,"
                " machine_phases TEXT,"
                " extras TEXT,"
                " steps TEXT,"
                " cup_type TEXT,"
                " source_recipe_id TEXT,"
                " source_bean_id TEXT,"
                " brew_count INTEGER NOT NULL DEFAULT 0,"
                " created_at TEXT NOT NULL,"
                " last_brewed_at TEXT"
                ")"
            )
            await conn.execute(
                "CREATE TABLE generation_sessions ("
                " id TEXT PRIMARY KEY,"
                " mode TEXT NOT NULL,"
                " preference TEXT,"
                " created_at TEXT NOT NULL"
                ")"
            )
            await conn.commit()

        db = SommelierDB(db_path)
        await db.async_setup()
        await db.async_close()

        async with aiosqlite.connect(db_path) as conn:
            cur = await conn.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cur.fetchone()
            assert row[0] == str(SCHEMA_VERSION)
            for table in (
                "sommelier_presets",
                "favorites",
                "generation_sessions",
            ):
                cur = await conn.execute(f"PRAGMA table_info({table})")
                cols = {r[1] for r in await cur.fetchall()}
                assert "machine_profile" in cols, (
                    f"machine_profile missing from legacy-upgraded {table}"
                )


# ── 3-4. async_add_preset persists / defaults machine_profile ─────────


@pytest.mark.asyncio
async def test_add_preset_persists_machine_profile():
    """machine_profile=2 round-trips into list_presets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset(
            "X", None, {"a": 1}, machine_profile=2
        )
        rows = [p for p in await db.async_list_presets() if p["id"] == preset_id]
        assert len(rows) == 1
        assert rows[0]["machine_profile"] == 2

        await db.async_close()


@pytest.mark.asyncio
async def test_add_preset_default_is_shared():
    """machine_profile omitted → row stored as NULL (shared)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        preset_id = await db.async_add_preset("X", None, {"a": 1})
        rows = [p for p in await db.async_list_presets() if p["id"] == preset_id]
        assert len(rows) == 1
        assert rows[0]["machine_profile"] is None

        await db.async_close()


# ── 5-6. async_list_presets filter / no-filter ────────────────────────


@pytest.mark.asyncio
async def test_list_presets_filter_includes_shared():
    """Filter returns shared (NULL) rows plus the matching slot, never the other slot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        # Seeded by async_setup: 4 system presets (all shared, NULL).
        p1 = await db.async_add_preset("On slot 1", None, {}, machine_profile=1)
        p2 = await db.async_add_preset("On slot 2", None, {}, machine_profile=2)

        rows = await db.async_list_presets(machine_profile_filter=1)
        ids = {r["id"] for r in rows}
        # 4 system shared rows + the slot-1 row, slot-2 excluded.
        assert len(rows) == 5
        assert p1 in ids
        assert p2 not in ids
        # System rows present and all NULL machine_profile.
        system_rows = [r for r in rows if r["is_system"]]
        assert len(system_rows) == 4
        assert all(r["machine_profile"] is None for r in system_rows)

        await db.async_close()


@pytest.mark.asyncio
async def test_list_presets_no_filter_returns_all():
    """No filter → all rows surface regardless of machine_profile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_add_preset("On slot 1", None, {}, machine_profile=1)
        await db.async_add_preset("On slot 2", None, {}, machine_profile=2)

        rows = await db.async_list_presets()
        # 4 seeded system + 2 user.
        assert len(rows) == 6

        await db.async_close()


# ── 7. async_add_favorite persists machine_profile ────────────────────


@pytest.mark.asyncio
async def test_add_favorite_persists_machine_profile():
    """data['machine_profile']=3 round-trips into list_favorites."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        added = await db.async_add_favorite(
            _minimal_favorite_data(name="X", machine_profile=3),
        )
        assert added["machine_profile"] == 3

        rows = await db.async_list_favorites()
        assert len(rows) == 1
        assert rows[0]["machine_profile"] == 3

        await db.async_close()


# ── 8. async_list_favorites filter includes shared ────────────────────


@pytest.mark.asyncio
async def test_list_favorites_filter_includes_shared():
    """Filter returns NULL favorites + the matching slot, excludes other slot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        shared = await db.async_add_favorite(_minimal_favorite_data(name="Shared"))
        on1 = await db.async_add_favorite(
            _minimal_favorite_data(name="Slot1", machine_profile=1)
        )
        on2 = await db.async_add_favorite(
            _minimal_favorite_data(name="Slot2", machine_profile=2)
        )

        rows = await db.async_list_favorites(machine_profile_filter=1)
        ids = {r["id"] for r in rows}
        assert shared["id"] in ids
        assert on1["id"] in ids
        assert on2["id"] not in ids
        assert len(rows) == 2

        # Sanity: no filter returns all three.
        all_rows = await db.async_list_favorites()
        assert len(all_rows) == 3

        await db.async_close()


# ── 9. async_create_session persists machine_profile ──────────────────


@pytest.mark.asyncio
async def test_create_session_persists_machine_profile():
    """machine_profile=1 round-trips into list_history."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        session = await db.async_create_session(
            **_minimal_session_kwargs(),
            machine_profile=1,
        )
        assert session["machine_profile"] == 1

        history = await db.async_list_history()
        assert len(history) == 1
        assert history[0]["machine_profile"] == 1

        await db.async_close()


# ── 10. async_list_history filter includes shared ─────────────────────


@pytest.mark.asyncio
async def test_list_history_filter_includes_shared():
    """Filter returns NULL sessions + the matching slot, excludes other slot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        shared = await db.async_create_session(**_minimal_session_kwargs())
        on1 = await db.async_create_session(
            **_minimal_session_kwargs(), machine_profile=1
        )
        on2 = await db.async_create_session(
            **_minimal_session_kwargs(), machine_profile=2
        )

        rows = await db.async_list_history(machine_profile_filter=1)
        ids = {r["id"] for r in rows}
        assert shared["id"] in ids
        assert on1["id"] in ids
        assert on2["id"] not in ids
        assert len(rows) == 2

        # Sanity: no filter returns all three.
        all_rows = await db.async_list_history()
        assert len(all_rows) == 3

        await db.async_close()


# ── WS-layer integration (P7a Task 2) ─────────────────────────────────


async def _make_hass_with_db(db: SommelierDB) -> MagicMock:
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    return hass


def _make_connection() -> MagicMock:
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    return connection


@pytest.mark.asyncio
async def test_ws_presets_list_honors_machine_profile_filter():
    """ws_presets_list passes machine_profile_filter through to the DB."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        # System presets are shared. Add one bound to profile 1.
        await db.async_add_preset("Profile-1 preset", None, {"a": 1}, machine_profile=1)
        await db.async_add_preset("Profile-2 preset", None, {"a": 2}, machine_profile=2)

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 1,
            "type": "melitta_barista/sommelier/presets/list",
            "machine_profile_filter": 1,
        }

        handler = inspect.unwrap(sa.ws_presets_list)
        await handler(hass, connection, msg)

        connection.send_error.assert_not_called()
        payload = connection.send_result.call_args.args[1]
        names = [p["name"] for p in payload["presets"]]
        # 4 system (shared) + Profile-1 preset; NOT Profile-2 preset.
        assert "Profile-1 preset" in names
        assert "Profile-2 preset" not in names

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_presets_add_persists_machine_profile():
    """ws_presets_add forwards machine_profile to async_add_preset."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 2,
            "type": "melitta_barista/sommelier/presets/add",
            "name": "Bound",
            "payload": {"k": 1},
            "machine_profile": 3,
        }

        handler = inspect.unwrap(sa.ws_presets_add)
        await handler(hass, connection, msg)

        connection.send_error.assert_not_called()
        rows = [p for p in await db.async_list_presets() if p["name"] == "Bound"]
        assert len(rows) == 1
        assert rows[0]["machine_profile"] == 3

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_favorites_list_honors_machine_profile_filter():
    """ws_favorites_list passes machine_profile_filter through to the DB."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_add_favorite(_minimal_favorite_data(name="Shared"))
        await db.async_add_favorite(_minimal_favorite_data(name="P1", machine_profile=1))
        await db.async_add_favorite(_minimal_favorite_data(name="P2", machine_profile=2))

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 3,
            "type": "melitta_barista/sommelier/favorites/list",
            "machine_profile_filter": 1,
        }

        handler = inspect.unwrap(sa.ws_favorites_list)
        await handler(hass, connection, msg)

        connection.send_error.assert_not_called()
        payload = connection.send_result.call_args.args[1]
        names = {f["name"] for f in payload["favorites"]}
        assert names == {"Shared", "P1"}

        await db.async_close()


@pytest.mark.asyncio
async def test_ws_history_list_honors_machine_profile_filter():
    """ws_history_list passes machine_profile_filter through to the DB."""
    from custom_components.melitta_barista import sommelier_api as sa

    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_create_session(**_minimal_session_kwargs())  # shared
        await db.async_create_session(
            **_minimal_session_kwargs(), machine_profile=1
        )
        await db.async_create_session(
            **_minimal_session_kwargs(), machine_profile=2
        )

        hass = await _make_hass_with_db(db)
        connection = _make_connection()
        msg = {
            "id": 4,
            "type": "melitta_barista/sommelier/history/list",
            "limit": 100,
            "offset": 0,
            "machine_profile_filter": 1,
        }

        handler = inspect.unwrap(sa.ws_history_list)
        await handler(hass, connection, msg)

        connection.send_error.assert_not_called()
        payload = connection.send_result.call_args.args[1]
        profiles = {s["machine_profile"] for s in payload["sessions"]}
        # Shared (None) + profile 1, no profile 2.
        assert 2 not in profiles
        assert 1 in profiles
        assert None in profiles

        await db.async_close()
