"""P4a Task 2 — async_set_extra_available upsert for user_extras."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from custom_components.melitta_barista.sommelier_db import SommelierDB


@pytest.mark.asyncio
async def test_set_extra_available_inserts_when_missing():
    """A fresh (category, item) pair is INSERTed with the right `available` bit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_set_extra_available("syrups", "Vanilla", True)

        cursor = await db.db.execute(
            "SELECT category, item, available FROM user_extras "
            "WHERE category = ? AND item = ?",
            ("syrups", "Vanilla"),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["category"] == "syrups"
        assert row["item"] == "Vanilla"
        assert row["available"] == 1

        await db.async_close()


@pytest.mark.asyncio
async def test_set_extra_available_updates_when_present():
    """An existing row's `available` is UPDATEd in place (no duplicate row)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        # Seed an available=1 row.
        await db.db.execute(
            "INSERT INTO user_extras (category, item, available) VALUES (?, ?, 1)",
            ("toppings", "Cocoa"),
        )
        await db.db.commit()

        await db.async_set_extra_available("toppings", "Cocoa", False)

        cursor = await db.db.execute(
            "SELECT available FROM user_extras WHERE category = ? AND item = ?",
            ("toppings", "Cocoa"),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["available"] == 0

        # No duplicate row sneaked in.
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM user_extras WHERE category = ? AND item = ?",
            ("toppings", "Cocoa"),
        )
        count = (await cursor.fetchone())[0]
        assert count == 1

        await db.async_close()
