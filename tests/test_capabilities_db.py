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
async def test_schema_version_is_current():
    """SCHEMA_VERSION tracks the current schema (>= 9 after P7a machine_profile)."""
    assert SCHEMA_VERSION >= 9


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


# ── P10: LiveCapabilities schema v1 -> v2 (supports_recipe_writes) ──────


def test_v1_blob_parses_and_defaults_recipe_writes_to_true():
    """Reading a v1 cached blob defaults supports_recipe_writes=True.

    The field was added in P10 (schema v2); existing Melitta installs that
    were cached under v1 must keep behaving exactly as before — i.e. the
    Sommelier brew path stays open. The Nivona-only refusal only takes
    effect when the field is explicitly False (v2 blob from a Nivona
    family).
    """
    from custom_components.melitta_barista.capabilities import LiveCapabilities

    blob = json.dumps({
        "schema_version": 1,
        "family_key": "barista_ts",
        "model_name": "Melitta Barista TS Smart",
        "supported_processes": ["coffee", "milk"],
        "supported_intensities": ["mild", "medium", "strong"],
        "supported_aromas": ["standard", "intense"],
        "supported_temperatures": ["normal"],
        "supported_shots": ["one", "two"],
        "portion_limits": {"coffee": {"min": 0, "max": 250, "step": 5}},
        "forbidden_combinations": [],
    })
    caps = LiveCapabilities.from_json(blob)
    assert caps.schema_version == 1
    assert caps.supports_recipe_writes is True


def test_v2_blob_round_trips_supports_recipe_writes_false():
    """v2 dataclass with supports_recipe_writes=False survives serialization."""
    from custom_components.melitta_barista.capabilities import LiveCapabilities

    original = LiveCapabilities(
        schema_version=2,
        family_key="900",
        model_name="Nivona 9xx",
        supported_processes=("coffee", "milk"),
        supported_intensities=("mild", "medium", "strong"),
        supported_aromas=("standard",),
        supported_temperatures=("normal",),
        supported_shots=("one", "two"),
        portion_limits={"coffee": {"min": 10, "max": 200, "step": 10}},
        forbidden_combinations=(),
        supports_recipe_writes=False,
    )
    restored = LiveCapabilities.from_json(original.to_json())
    assert restored == original
    assert restored.supports_recipe_writes is False
    assert restored.schema_version == 2


def test_derive_capabilities_sources_recipe_writes_from_machine_caps():
    """derive_capabilities reads MachineCapabilities.supports_recipe_writes
    verbatim and stamps schema_version=2 on the result."""
    from unittest.mock import MagicMock

    from custom_components.melitta_barista.brands.base import MachineCapabilities
    from custom_components.melitta_barista.capabilities import derive_capabilities

    caps = MachineCapabilities(
        family_key="900",
        model_name="Nivona 9xx",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=8,
        strength_levels=3,
        has_aroma_balance=False,
        image_transfer=None,
        fluid_scale_factor=1,
        brew_command_mode=0x0B,
        recipe_text_encoding="utf16_le",
        tolerated_brew_manipulations=(),
        recipes=(),
        settings=(),
        stats=(),
    )
    client = MagicMock()
    client.capabilities = caps

    live = derive_capabilities(client)
    assert live.supports_recipe_writes is False
    assert live.schema_version == 2
