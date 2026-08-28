"""Referential-integrity and migration-robustness tests for SommelierDB.

Covers the four DB-side audit fixes:
1. FK-safe deletes — deleting a bean/profile referenced by generation
   history must not raise IntegrityError; referencing columns are NULLed.
2. async_update_profile accepts both flat and nested `preferences` payloads.
3. Migration runner: idempotency errors (duplicate column / already exists)
   are swallowed and the version is stamped; any other error is logged and
   the schema_version stamp is skipped so the migration retries next start.
4. recipe_ratings orphan cleanup on favorite removal and history clear.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from custom_components.melitta_barista import sommelier_db as sdb_module
from custom_components.melitta_barista.sommelier_db import SommelierDB


# ── Fixtures / helpers ────────────────────────────────────────────────


@pytest.fixture
async def db() -> SommelierDB:
    """Create an in-memory SommelierDB, yield it, then close."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    yield sdb
    await sdb.async_close()


def _bean_data(**overrides: Any) -> dict[str, Any]:
    data = {
        "brand": "Melitta",
        "product": "BellaCrema Espresso",
        "roast": "dark",
        "bean_type": "arabica",
        "origin": "blend",
    }
    data.update(overrides)
    return data


def _component(process: str = "coffee", portion_ml: int = 30) -> dict[str, Any]:
    return {
        "process": process,
        "intensity": "strong",
        "portion_ml": portion_ml,
        "temperature": "normal",
        "shots": "one",
    }


def _recipe(**overrides: Any) -> dict[str, Any]:
    recipe = {
        "name": "Morning Espresso",
        "description": "A strong morning espresso",
        "blend": 1,
        "component1": _component("coffee", 30),
        "component2": _component("milk", 120),
    }
    recipe.update(overrides)
    return recipe


async def _create_session(
    db: SommelierDB,
    *,
    hopper1_bean_id: str | None = None,
    hopper2_bean_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    return await db.async_create_session(
        mode="surprise_me",
        preference=None,
        hopper1_bean_id=hopper1_bean_id,
        hopper2_bean_id=hopper2_bean_id,
        milk_types=["whole"],
        llm_agent="test.agent",
        recipes=[_recipe()],
        profile_id=profile_id,
    )


async def _session_row(db: SommelierDB, session_id: str) -> dict[str, Any]:
    cursor = await db.db.execute(
        "SELECT * FROM generation_sessions WHERE id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return dict(row)


# ── 1. FK-safe deletes ────────────────────────────────────────────────


class TestFkSafeDeletes:
    """Bean/profile deletion must not raise when history references them."""

    async def test_delete_bean_referenced_as_hopper1(self, db: SommelierDB):
        bean = await db.async_add_bean(_bean_data())
        session = await _create_session(db, hopper1_bean_id=bean["id"])

        assert await db.async_delete_bean(bean["id"]) is True

        assert await db.async_get_bean(bean["id"]) is None
        row = await _session_row(db, session["id"])
        assert row["hopper1_bean_id"] is None

    async def test_delete_bean_referenced_as_hopper2(self, db: SommelierDB):
        bean = await db.async_add_bean(_bean_data())
        session = await _create_session(db, hopper2_bean_id=bean["id"])

        assert await db.async_delete_bean(bean["id"]) is True

        row = await _session_row(db, session["id"])
        assert row["hopper2_bean_id"] is None

    async def test_delete_bean_referenced_in_both_hoppers(self, db: SommelierDB):
        bean = await db.async_add_bean(_bean_data())
        session = await _create_session(
            db, hopper1_bean_id=bean["id"], hopper2_bean_id=bean["id"]
        )

        assert await db.async_delete_bean(bean["id"]) is True

        row = await _session_row(db, session["id"])
        assert row["hopper1_bean_id"] is None
        assert row["hopper2_bean_id"] is None

    async def test_delete_bean_keeps_other_bean_refs(self, db: SommelierDB):
        bean1 = await db.async_add_bean(_bean_data())
        bean2 = await db.async_add_bean(_bean_data(product="Other"))
        session = await _create_session(
            db, hopper1_bean_id=bean1["id"], hopper2_bean_id=bean2["id"]
        )

        assert await db.async_delete_bean(bean1["id"]) is True

        row = await _session_row(db, session["id"])
        assert row["hopper1_bean_id"] is None
        assert row["hopper2_bean_id"] == bean2["id"]

    async def test_delete_profile_referenced_by_session(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})
        session = await _create_session(db, profile_id=profile["id"])

        assert await db.async_delete_profile(profile["id"]) is True

        assert await db.async_get_profile(profile["id"]) is None
        row = await _session_row(db, session["id"])
        assert row["profile_id"] is None

    async def test_delete_profile_keeps_other_profile_refs(self, db: SommelierDB):
        alice = await db.async_add_profile({"name": "Alice"})
        bob = await db.async_add_profile({"name": "Bob"})
        s_alice = await _create_session(db, profile_id=alice["id"])
        s_bob = await _create_session(db, profile_id=bob["id"])

        assert await db.async_delete_profile(alice["id"]) is True

        assert (await _session_row(db, s_alice["id"]))["profile_id"] is None
        assert (await _session_row(db, s_bob["id"]))["profile_id"] == bob["id"]


# ── 2. Profile update: nested vs flat payloads ────────────────────────


class TestProfileUpdatePayloads:
    """async_update_profile must accept flat keys and a nested preferences dict."""

    async def test_update_with_nested_preferences(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})

        updated = await db.async_update_profile(
            profile["id"],
            {"preferences": {"cup_size": "espresso", "caffeine_pref": "decaf"}},
        )

        assert updated is not None
        assert updated["cup_size"] == "espresso"
        assert updated["caffeine_pref"] == "decaf"
        # Untouched fields keep their values.
        assert updated["name"] == "Alice"
        assert updated["temperature_pref"] == "hot_only"

    async def test_update_with_nested_preferences_and_name(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})

        updated = await db.async_update_profile(
            profile["id"],
            {"name": "Alicia", "preferences": {"temperature_pref": "both"}},
        )

        assert updated is not None
        assert updated["name"] == "Alicia"
        assert updated["temperature_pref"] == "both"

    async def test_update_with_flat_payload_still_works(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})

        updated = await db.async_update_profile(
            profile["id"], {"cup_size": "cup", "caffeine_pref": "half_caf"}
        )

        assert updated is not None
        assert updated["cup_size"] == "cup"
        assert updated["caffeine_pref"] == "half_caf"

    async def test_update_nested_dietary_roundtrip(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})

        updated = await db.async_update_profile(
            profile["id"], {"preferences": {"dietary": ["lactose_free", "vegan"]}}
        )

        assert updated is not None
        assert updated["dietary"] == ["lactose_free", "vegan"]

    async def test_flat_key_wins_over_nested(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "Alice"})

        updated = await db.async_update_profile(
            profile["id"],
            {"name": "Flat", "preferences": {"name": "Nested"}},
        )

        assert updated is not None
        assert updated["name"] == "Flat"

    async def test_update_nested_not_found(self, db: SommelierDB):
        result = await db.async_update_profile(
            "missing", {"preferences": {"cup_size": "cup"}}
        )
        assert result is None


# ── 3. Migration runner robustness ────────────────────────────────────


class TestMigrationRunner:
    """Idempotency errors are swallowed; real failures skip the version stamp."""

    async def _get_schema_version(self, db: SommelierDB) -> int:
        cursor = await db.db.execute(
            "SELECT value FROM settings WHERE key = 'schema_version'"
        )
        row = await cursor.fetchone()
        return int(row["value"])

    async def _make_db_at_version(self, path: Path, version: int) -> None:
        """Create a fully-migrated DB file, then downgrade its stamp."""
        db = SommelierDB(path)
        await db.async_setup()
        await db.db.execute(
            "UPDATE settings SET value = ? WHERE key = 'schema_version'",
            (str(version),),
        )
        await db.db.commit()
        await db.async_close()

    async def test_duplicate_column_errors_still_stamp(self, tmp_path: Path):
        """Re-running v8→v9 on an already-v9 schema stamps v9 (idempotent)."""
        db_path = tmp_path / "test.db"
        await self._make_db_at_version(db_path, 8)

        db = SommelierDB(db_path)
        await db.async_setup()
        assert await self._get_schema_version(db) == sdb_module.SCHEMA_VERSION
        await db.async_close()

    async def test_hard_failure_skips_stamp_and_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """A non-idempotency migration error keeps the old version stamp."""
        db_path = tmp_path / "test.db"
        await self._make_db_at_version(db_path, 8)

        monkeypatch.setattr(
            sdb_module,
            "MIGRATE_V8_TO_V9",
            "ALTER TABLE no_such_table ADD COLUMN broken TEXT;",
        )

        db = SommelierDB(db_path)
        with caplog.at_level(logging.WARNING, logger="melitta_barista"):
            await db.async_setup()

        # Version must NOT be stamped — migration retries next start.
        assert await self._get_schema_version(db) == 8
        assert any(
            "no_such_table" in rec.getMessage()
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        )
        await db.async_close()

    async def test_hard_failure_retries_next_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """After a failed start, an unbroken restart completes and stamps."""
        db_path = tmp_path / "test.db"
        await self._make_db_at_version(db_path, 8)

        monkeypatch.setattr(
            sdb_module,
            "MIGRATE_V8_TO_V9",
            "ALTER TABLE no_such_table ADD COLUMN broken TEXT;",
        )
        db = SommelierDB(db_path)
        await db.async_setup()
        assert await self._get_schema_version(db) == 8
        await db.async_close()

        monkeypatch.undo()
        db2 = SommelierDB(db_path)
        await db2.async_setup()
        assert await self._get_schema_version(db2) == sdb_module.SCHEMA_VERSION
        await db2.async_close()


# ── 4. recipe_ratings orphan cleanup ──────────────────────────────────


class TestRatingsOrphanCleanup:
    """Rating rows must not outlive their favorite / generated targets."""

    def _fav_data(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "name": "Fav",
            "description": "desc",
            "blend": 1,
            "component1": _component(),
            "component2": _component("milk", 100),
        }
        data.update(overrides)
        return data

    async def test_remove_favorite_deletes_its_rating(self, db: SommelierDB):
        fav = await db.async_add_favorite(self._fav_data())
        await db.async_set_rating(fav["id"], "favorite", 5, "great")

        assert await db.async_remove_favorite(fav["id"]) is True

        assert await db.async_get_rating(fav["id"], "favorite") is None

    async def test_remove_favorite_keeps_other_favorite_ratings(
        self, db: SommelierDB
    ):
        fav1 = await db.async_add_favorite(self._fav_data())
        fav2 = await db.async_add_favorite(self._fav_data(name="Fav2"))
        await db.async_set_rating(fav1["id"], "favorite", 5, None)
        await db.async_set_rating(fav2["id"], "favorite", 3, None)

        await db.async_remove_favorite(fav1["id"])

        assert await db.async_get_rating(fav1["id"], "favorite") is None
        rating2 = await db.async_get_rating(fav2["id"], "favorite")
        assert rating2 is not None and rating2["rating"] == 3

    async def test_remove_favorite_keeps_generated_rating_same_id(
        self, db: SommelierDB
    ):
        """A 'generated' rating sharing the id must survive favorite removal."""
        fav = await db.async_add_favorite(self._fav_data())
        await db.async_set_rating(fav["id"], "favorite", 5, None)
        await db.async_set_rating(fav["id"], "generated", 2, None)

        await db.async_remove_favorite(fav["id"])

        assert await db.async_get_rating(fav["id"], "favorite") is None
        gen = await db.async_get_rating(fav["id"], "generated")
        assert gen is not None and gen["rating"] == 2

    async def test_clear_history_deletes_generated_ratings(self, db: SommelierDB):
        session = await _create_session(db)
        recipe_id = session["recipes"][0]["id"]
        await db.async_set_rating(recipe_id, "generated", 4, "nice")

        removed = await db.async_clear_history(keep_favorited=False)
        assert removed == 1

        assert await db.async_get_rating(recipe_id, "generated") is None

    async def test_clear_history_keeps_ratings_of_kept_sessions(
        self, db: SommelierDB
    ):
        """keep_favorited=True preserves the session AND its recipe ratings."""
        session = await _create_session(db)
        recipe_id = session["recipes"][0]["id"]
        await db.async_add_favorite(
            self._fav_data(source_recipe_id=recipe_id)
        )
        await db.async_set_rating(recipe_id, "generated", 5, None)

        removed = await db.async_clear_history(keep_favorited=True)
        assert removed == 0

        rating = await db.async_get_rating(recipe_id, "generated")
        assert rating is not None and rating["rating"] == 5

    async def test_clear_history_keeps_favorite_ratings(self, db: SommelierDB):
        """Clearing history never touches favorite-type ratings."""
        fav = await db.async_add_favorite(self._fav_data())
        await db.async_set_rating(fav["id"], "favorite", 4, None)
        await _create_session(db)

        await db.async_clear_history(keep_favorited=False)

        rating = await db.async_get_rating(fav["id"], "favorite")
        assert rating is not None and rating["rating"] == 4

    async def test_clear_history_mixed_kept_and_removed(self, db: SommelierDB):
        """Only ratings of actually-removed recipes are swept."""
        kept = await _create_session(db)
        kept_recipe_id = kept["recipes"][0]["id"]
        await db.async_add_favorite(
            self._fav_data(source_recipe_id=kept_recipe_id)
        )
        gone = await _create_session(db)
        gone_recipe_id = gone["recipes"][0]["id"]
        await db.async_set_rating(kept_recipe_id, "generated", 5, None)
        await db.async_set_rating(gone_recipe_id, "generated", 1, None)

        removed = await db.async_clear_history(keep_favorited=True)
        assert removed == 1

        assert await db.async_get_rating(gone_recipe_id, "generated") is None
        kept_rating = await db.async_get_rating(kept_recipe_id, "generated")
        assert kept_rating is not None and kept_rating["rating"] == 5
