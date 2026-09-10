"""Tests for `sommelier_backup` — the export builder and the replace-importer.

The dangerous parts of F4, and therefore the parts pinned hardest here:

* the `settings` row `schema_version` is the migration runner's stamp; a
  naive full-replace would overwrite it with a foreign DB's value
  (`test_import_preserves_schema_version_stamp`);
* `include_install_specific: false` exists to PROTECT the receiving install,
  so it must preserve the local `llm_agent_id` / `weather_entity`, not erase
  them (the `TestInstallSpecific` block);
* export and import both run on the ONE shared `SommelierDB` connection in a
  single transaction each, which is what makes post-import coherence free
  (`test_shared_connection_sees_imported_rows`).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import panel_api, sommelier_backup
from custom_components.melitta_barista.sommelier_api import (
    VALID_PREFERENCE_KEYS,
    VALID_SETTING_KEYS,
)
from custom_components.melitta_barista.sommelier_db import (
    SCHEMA_VERSION,
    SommelierDB,
)


# ── Fixtures / seeding ────────────────────────────────────────────────


@pytest.fixture
async def raw_db():
    """A bare in-memory SommelierDB — no panel tables."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    yield sdb
    await sdb.async_close()


@pytest.fixture
async def db():
    """In-memory SommelierDB with the five panel-owned tables bootstrapped."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    await panel_api._ensure_panel_schema(sdb)
    yield sdb
    await sdb.async_close()


def _bean(**overrides):
    data = {
        "brand": "Melitta",
        "product": "BellaCrema",
        "roast": "dark",
        "bean_type": "arabica",
        "origin": "blend",
        "flavor_notes": ["cocoa"],
    }
    data.update(overrides)
    return data


def _recipe(name="Generated One"):
    return {
        "name": name,
        "description": "a drink",
        "reasoning": "because",
        "blend": 0,
        "component1": {"aroma": 2},
        "component2": {},
        "cup_type": "cup",
    }


async def _seed_panel_rows(db):
    """Insert rows into the five panel-owned tables through raw SQL."""
    conn = db.db
    await conn.execute(
        "INSERT INTO producers (id, name, country, created_at) "
        "VALUES (7, 'Monin', 'FR', '2026-01-01T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO syrups (id, name, brand, producer_id, available, created_at) "
        "VALUES (3, 'Vanilla', 'Monin', 7, 1, '2026-01-01T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO toppings (id, name, producer_id, available, created_at) "
        "VALUES (4, 'Cocoa', 7, 1, '2026-01-01T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO flavor_tags (name, created_at) "
        "VALUES ('nutty', '2026-01-01T00:00:00+00:00')"
    )
    await conn.execute(
        "INSERT INTO panel_prompts (slot, template, updated_at) "
        "VALUES ('sommelier', 'my custom prompt', '2026-01-01T00:00:00+00:00')"
    )
    await conn.commit()


async def _seed(db, *, with_panel=True):
    """Populate every exportable table and return the interesting ids."""
    bean = await db.async_add_bean(_bean())
    await db.async_assign_hopper(1, bean["id"])
    await db.async_set_milk(["whole", "oat"])
    await db.async_set_extras("liqueurs", ["amaretto"])
    await db.async_set_preferences_bulk(
        {
            "default_cup_size": "mug",
            "use_weather": "true",
            "weather_entity": "weather.home",
        }
    )
    profile = await db.async_add_profile({"name": "Anna", "cup_size": "mug"})
    await db.async_set_active_profile(profile["id"])
    session = await db.async_create_session(
        mode="surprise_me",
        preference=None,
        hopper1_bean_id=bean["id"],
        hopper2_bean_id=None,
        milk_types=["whole"],
        llm_agent="conversation.local",
        recipes=[_recipe()],
        profile_id=profile["id"],
    )
    recipe_id = session["recipes"][0]["id"]
    favorite = await db.async_add_favorite(
        {
            "name": "Anna's Latte",
            "description": "her usual",
            "blend": 0,
            "component1": {"aroma": 2},
            "component2": {},
            "source_recipe_id": recipe_id,
            "reasoning": "the sommelier's justification",
        }
    )
    await db.async_set_rating(favorite["id"], "favorite", 5, "great")
    await db.async_set_rating(recipe_id, "generated", 4, None)
    preset_id = await db.async_add_preset("My preset", "mine", {"mood": "calm"})
    await db.async_set_setting("llm_agent_id", "conversation.local")
    await db.async_set_setting("llm_timeout_s", "180")
    if with_panel:
        await _seed_panel_rows(db)
    return {
        "bean_id": bean["id"],
        "profile_id": profile["id"],
        "session_id": session["id"],
        "recipe_id": recipe_id,
        "favorite_id": favorite["id"],
        "preset_id": preset_id,
    }


async def _dump(db):
    """Whole-DB snapshot used by the bit-identical / rollback assertions."""
    conn = db.db
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    )
    names = [row[0] for row in await cursor.fetchall()]
    out = {}
    for name in names:
        if name.startswith("sqlite_"):
            continue
        cursor = await conn.execute(f'SELECT * FROM "{name}"')
        out[name] = sorted(str(tuple(row)) for row in await cursor.fetchall())
    return out


async def _settings_value(db, key):
    cursor = await db.db.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]


async def _preference_value(db, key):
    cursor = await db.db.execute(
        "SELECT value FROM user_preferences WHERE key = ?", (key,)
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]


# ── Export ────────────────────────────────────────────────────────────


class TestExport:
    """The bundle a healthy DB produces."""

    async def test_envelope_shape(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, integration_version="0.95.0b1"
        )
        assert bundle["format"] == "melitta_barista.sommelier_export"
        assert bundle["format_version"] == 1
        assert bundle["db_schema_version"] == SCHEMA_VERSION
        assert bundle["integration_version"] == "0.95.0b1"
        assert bundle["include_history"] is False
        # tz-aware ISO timestamp
        from datetime import datetime

        parsed = datetime.fromisoformat(bundle["exported_at"])
        assert parsed.tzinfo is not None

    async def test_history_omitted_by_default(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        assert "history" not in bundle

    async def test_history_section_carries_sessions_recipes_and_ratings(self, db):
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        history = bundle["history"]
        assert [r["id"] for r in history["generation_sessions"]] == [
            ids["session_id"]
        ]
        assert [r["id"] for r in history["generated_recipes"]] == [
            ids["recipe_id"]
        ]
        assert [r["target_id"] for r in history["recipe_ratings"]] == [
            ids["recipe_id"]
        ]

    async def test_favorite_ratings_live_in_the_config_section(self, db):
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        assert [r["target_id"] for r in bundle["tables"]["recipe_ratings"]] == [
            ids["favorite_id"]
        ]

    async def test_machine_capabilities_never_exported(self, db):
        await _seed(db)
        await db.async_save_capabilities("entry-abc", json.dumps({"a": 1}))
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        assert "machine_capabilities" not in bundle["tables"]
        assert "machine_capabilities" not in bundle.get("history", {})

    async def test_schema_version_setting_excluded(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        keys = {row["key"] for row in bundle["tables"]["settings"]}
        assert "schema_version" not in keys
        assert keys <= set(VALID_SETTING_KEYS)

    async def test_preferences_filtered_to_allowlist(self, db):
        await _seed(db)
        await db.async_set_preference("junk_key", "junk")
        bundle = await sommelier_backup.async_build_export(db)
        keys = {row["key"] for row in bundle["tables"]["user_preferences"]}
        assert "junk_key" not in keys
        assert keys <= set(VALID_PREFERENCE_KEYS)

    async def test_system_presets_excluded(self, db):
        ids = await _seed(db)
        rows = (await sommelier_backup.async_build_export(db))["tables"][
            "sommelier_presets"
        ]
        assert [r["id"] for r in rows] == [ids["preset_id"]]
        assert all(r["is_system"] == 0 for r in rows)

    async def test_panel_tables_included(self, db):
        await _seed(db)
        tables = (await sommelier_backup.async_build_export(db))["tables"]
        assert [r["name"] for r in tables["producers"]] == ["Monin"]
        assert [r["id"] for r in tables["syrups"]] == [3]
        assert [r["id"] for r in tables["toppings"]] == [4]
        assert [r["name"] for r in tables["flavor_tags"]] == ["nutty"]
        assert [r["slot"] for r in tables["panel_prompts"]] == ["sommelier"]

    async def test_export_works_without_panel_tables(self, raw_db):
        """A DB whose panel tabs were never opened still exports cleanly."""
        await _seed(raw_db, with_panel=False)
        bundle = await sommelier_backup.async_build_export(raw_db)
        assert bundle["tables"]["panel_prompts"] == []
        assert bundle["tables"]["coffee_beans"]

    async def test_install_specific_hoisted_exactly_once(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        assert bundle["install_specific"] == {
            "llm_agent_id": "conversation.local",
            "weather_entity": "weather.home",
        }
        assert "llm_agent_id" not in {
            r["key"] for r in bundle["tables"]["settings"]
        }
        assert "weather_entity" not in {
            r["key"] for r in bundle["tables"]["user_preferences"]
        }

    async def test_favorites_reasoning_carried(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        assert bundle["tables"]["favorites"][0]["reasoning"] == (
            "the sommelier's justification"
        )

    async def test_two_exports_differ_only_in_timestamp(self, db):
        await _seed(db)
        first = await sommelier_backup.async_build_export(db)
        second = await sommelier_backup.async_build_export(db)
        first.pop("exported_at")
        second.pop("exported_at")
        assert first == second

    async def test_rows_ordered_by_primary_key(self, db):
        for name in ("zulu", "alpha", "mike"):
            await db.async_add_bean(_bean(product=name))
        rows = (await sommelier_backup.async_build_export(db))["tables"][
            "coffee_beans"
        ]
        assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)

    async def test_busy_lock_refuses_export(self, db):
        """T4 — a second backup operation is refused, never queued."""
        async with sommelier_backup._BACKUP_LOCK:
            with pytest.raises(sommelier_backup.BackupBusyError):
                await sommelier_backup.async_build_export(db)


# ── Import round trips ────────────────────────────────────────────────


class TestImportRoundTrip:
    """Export → wipe/mutate → import → the state comes back."""

    async def test_config_only_round_trip(self, db):
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_delete_bean(ids["bean_id"])
        await db.async_remove_favorite(ids["favorite_id"])

        await sommelier_backup.async_apply_import(db, bundle)

        assert [b["id"] for b in await db.async_list_beans()] == [ids["bean_id"]]
        favorites = await db.async_list_favorites()
        assert [f["id"] for f in favorites] == [ids["favorite_id"]]
        assert favorites[0]["reasoning"] == "the sommelier's justification"

    async def test_round_trip_with_history(self, db):
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        await sommelier_backup.async_apply_import(
            db, bundle, include_history=True
        )
        recipe = await db.async_get_recipe(ids["recipe_id"])
        assert recipe is not None
        assert recipe["name"] == "Generated One"

    async def test_export_import_export_is_stable(self, db):
        await _seed(db)
        first = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        await sommelier_backup.async_apply_import(
            db, first, include_history=True
        )
        second = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        first.pop("exported_at")
        second.pop("exported_at")
        assert first == second

    async def test_row_absent_from_bundle_is_gone_after_import(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        extra = await db.async_add_bean(_bean(product="Interloper"))
        await sommelier_backup.async_apply_import(db, bundle)
        assert extra["id"] not in {b["id"] for b in await db.async_list_beans()}

    async def test_history_always_cleared_when_not_imported(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        summary = await sommelier_backup.async_apply_import(
            db, bundle, include_history=False
        )
        assert summary["history_cleared"] == 1
        cursor = await db.db.execute("SELECT COUNT(*) FROM generation_sessions")
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.db.execute("SELECT COUNT(*) FROM generated_recipes")
        assert (await cursor.fetchone())[0] == 0

    async def test_shared_connection_sees_imported_rows(self, db):
        """T5 — post-import coherence, read back through the DB's own methods."""
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_delete_bean(ids["bean_id"])
        await sommelier_backup.async_apply_import(db, bundle)
        assert await db.async_get_bean(ids["bean_id"]) is not None
        assert await db.async_list_favorites()
        assert (await db.async_get_hoppers())["hopper1"]["bean"] is not None

    async def test_system_presets_reseeded(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM sommelier_presets WHERE is_system = 1"
        )
        assert (await cursor.fetchone())[0] == 4

    async def test_producer_links_survive(self, db):
        """Autoincrement ids must be inserted explicitly, or FKs re-wire."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT s.name FROM syrups s JOIN producers p "
            "ON s.producer_id = p.id WHERE p.name = 'Monin'"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["Vanilla"]

    async def test_panel_prompt_template_survives(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT template FROM panel_prompts WHERE slot = 'sommelier'"
        )
        assert (await cursor.fetchone())[0] == "my custom prompt"

    async def test_busy_lock_refuses_import(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        async with sommelier_backup._BACKUP_LOCK:
            with pytest.raises(sommelier_backup.BackupBusyError):
                await sommelier_backup.async_apply_import(db, bundle)


# ── The stamp, the allowlists, the invariants ─────────────────────────


class TestImportGuards:
    """Everything a naive `DELETE FROM …; INSERT …` would break."""

    async def test_import_preserves_schema_version_stamp(self, db):
        """The single most important test in F4.

        `settings` doubles as the migration runner's version stamp store. A
        bundle claiming a foreign schema version must not be able to move it,
        or the next start either skips migrations or re-runs them wrongly.
        """
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        # A hand-edited bundle that tries to smuggle the stamp back in.
        bundle["tables"]["settings"].append(
            {"key": "schema_version", "value": "3"}
        )
        before = await _settings_value(db, "schema_version")
        assert before == str(SCHEMA_VERSION)

        await sommelier_backup.async_apply_import(db, bundle)

        assert await _settings_value(db, "schema_version") == before

    async def test_settings_outside_allowlist_never_written(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["settings"].append({"key": "evil", "value": "x"})
        summary = await sommelier_backup.async_apply_import(db, bundle)
        assert await _settings_value(db, "evil") is None
        assert summary["skipped"]["settings"] == 1

    async def test_preferences_outside_allowlist_never_written(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["user_preferences"].append(
            {"key": "evil_pref", "value": "x"}
        )
        summary = await sommelier_backup.async_apply_import(db, bundle)
        assert await _preference_value(db, "evil_pref") is None
        assert summary["skipped"]["user_preferences"] == 1

    async def test_system_preset_row_in_bundle_is_dropped(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["sommelier_presets"].append(
            {
                "id": "sys_smuggled",
                "name": "Smuggled",
                "description": None,
                "payload": "{}",
                "is_system": 1,
                "dynamic_occasion": 0,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": None,
                "machine_profile": None,
            }
        )
        summary = await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM sommelier_presets WHERE id = 'sys_smuggled'"
        )
        assert (await cursor.fetchone())[0] == 0
        assert summary["skipped"]["sommelier_presets"] == 1

    async def test_machine_capabilities_in_bundle_ignored(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["machine_capabilities"] = [
            {
                "entry_id": "foreign",
                "json_payload": "{}",
                "probed_at": "2026-01-01T00:00:00+00:00",
                "schema_version": 1,
            }
        ]
        summary = await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM machine_capabilities WHERE entry_id = 'foreign'"
        )
        assert (await cursor.fetchone())[0] == 0
        assert summary["skipped"]["machine_capabilities"] == 1

    async def test_unknown_table_ignored(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["from_the_future"] = [{"a": 1}, {"a": 2}]
        summary = await sommelier_backup.async_apply_import(db, bundle)
        assert summary["skipped"]["from_the_future"] == 2

    async def test_unknown_column_dropped_and_reported(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        for row in bundle["tables"]["coffee_beans"]:
            row["future_column"] = "x"
        summary = await sommelier_backup.async_apply_import(db, bundle)
        assert summary["dropped_columns"]["coffee_beans"] == ["future_column"]
        assert await db.async_list_beans()

    async def test_missing_column_accepted(self, db):
        """A v11 bundle has no `favorites.reasoning`; it must land NULL."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["db_schema_version"] = 11
        for row in bundle["tables"]["favorites"]:
            row.pop("reasoning")
        await sommelier_backup.async_apply_import(db, bundle)
        favorites = await db.async_list_favorites()
        assert favorites
        assert not favorites[0]["reasoning"]

    @pytest.mark.parametrize("legacy_key", ["cup_size", "default_cup_size"])
    async def test_legacy_cup_size_normalised(self, db, legacy_key):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        if legacy_key == "cup_size":
            bundle["tables"]["sommelier_profiles"][0]["cup_size"] = "espresso"
        else:
            for row in bundle["tables"]["user_preferences"]:
                if row["key"] == "default_cup_size":
                    row["value"] = "espresso"
        await sommelier_backup.async_apply_import(db, bundle)
        if legacy_key == "cup_size":
            profiles = await db.async_list_profiles()
            assert profiles[0]["cup_size"] == "espresso_cup"
        else:
            assert (
                await _preference_value(db, "default_cup_size")
            ) == "espresso_cup"

    @pytest.mark.parametrize("active_count", [0, 3])
    async def test_active_profile_singleton_reasserted(self, db, active_count):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        template = bundle["tables"]["sommelier_profiles"][0]
        rows = []
        for index in range(3):
            row = dict(template)
            row["id"] = f"profile-{index}"
            row["name"] = f"Person {index}"
            row["created_at"] = f"2026-01-0{index + 1}T00:00:00+00:00"
            row["is_active"] = 1 if index < active_count else 0
            rows.append(row)
        bundle["tables"]["sommelier_profiles"] = rows

        await sommelier_backup.async_apply_import(db, bundle)

        cursor = await db.db.execute(
            "SELECT id FROM sommelier_profiles WHERE is_active = 1"
        )
        active = [r[0] for r in await cursor.fetchall()]
        assert active == ["profile-0"]

    async def test_hopper_rows_always_exist(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["hoppers"] = []
        await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT hopper_id FROM hoppers ORDER BY hopper_id"
        )
        assert [r[0] for r in await cursor.fetchall()] == [1, 2]

    async def test_hopper_bean_id_nulled_when_bean_absent(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["coffee_beans"] = []
        await sommelier_backup.async_apply_import(db, bundle)
        hoppers = await db.async_get_hoppers()
        assert hoppers["hopper1"]["bean"] is None

    async def test_orphan_ratings_dropped(self, db):
        ids = await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["tables"]["favorites"] = []
        summary = await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM recipe_ratings WHERE target_id = ?",
            (ids["favorite_id"],),
        )
        assert (await cursor.fetchone())[0] == 0
        assert summary["skipped"]["recipe_ratings"] >= 1

    async def test_generated_ratings_dropped_without_history(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        await sommelier_backup.async_apply_import(
            db, bundle, include_history=False
        )
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM recipe_ratings WHERE target_type = 'generated'"
        )
        assert (await cursor.fetchone())[0] == 0

    async def test_rollback_leaves_db_unchanged(self, db):
        """An IntegrityError mid-transaction must roll the whole thing back."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        before = await _dump(db)
        # rating 9 violates the live CHECK (rating BETWEEN 1 AND 5)
        bundle["tables"]["recipe_ratings"][0]["rating"] = 9

        with pytest.raises(Exception):  # noqa: B017 — sqlite3.IntegrityError
            await sommelier_backup.async_apply_import(db, bundle)

        assert await _dump(db) == before


# ── Foreign writers on the shared connection ──────────────────────────


class TestForeignWriters:
    """Rule 5: nothing else may COMMIT while the importer owns the connection.

    `panel_api` writes its five own tables through raw `db._db.execute` on the
    very connection the importer runs its `BEGIN IMMEDIATE` on. SQLite has no
    nested transactions, so an unlocked `commit()` from one of those handlers
    lands as a commit of *the import*, and the importer's rollback can then
    only undo whatever happened after it — a half-replaced configuration, with
    `import_failed` on the wire.
    """

    async def _run_import_with_a_panel_write_inside(self, db, bundle):
        """Apply `bundle`, firing a real panel write task mid-insert-phase."""
        connection = MagicMock()
        hass = MagicMock()
        fired: list[asyncio.Task] = []
        real_insert = sommelier_backup._insert_rows

        async def insert_and_interleave(conn, table, rows):
            if table == "coffee_beans" and not fired:
                handler = inspect.unwrap(panel_api._ws_tags_add)
                fired.append(
                    asyncio.create_task(
                        handler(
                            hass,
                            connection,
                            {"id": 1, "name": "mid-import"},
                        )
                    )
                )
                # Give the handler every chance to reach its commit.
                for _ in range(20):
                    await asyncio.sleep(0)
            return await real_insert(conn, table, rows)

        with (
            patch.object(
                sommelier_backup, "_insert_rows", insert_and_interleave
            ),
            patch.object(
                panel_api, "_async_get_db", new=AsyncMock(return_value=db)
            ),
        ):
            with pytest.raises(Exception):  # noqa: B017 — IntegrityError
                await sommelier_backup.async_apply_import(db, bundle)
            await asyncio.wait_for(fired[0], timeout=5)

    async def test_panel_write_cannot_commit_a_failing_import(self, db):
        """The failed import rolls back WHOLLY, panel write or no panel write."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        before = await _dump(db)
        # A duplicate primary key in a table late in the insert order: the
        # import blows up well after the delete phase and after the beans.
        bundle["tables"]["flavor_tags"] = [
            {"name": "clash", "created_at": "2026-01-01T00:00:00+00:00"},
            {"name": "clash", "created_at": "2026-01-01T00:00:00+00:00"},
        ]

        await self._run_import_with_a_panel_write_inside(db, bundle)

        after = await _dump(db)
        # The panel write itself is serialised AFTER the rolled-back import,
        # so it is the only difference between the two dumps.
        assert set(after) == set(before)
        for table in before:
            if table != "flavor_tags":
                assert after[table] == before[table], table
        tags = " ".join(after["flavor_tags"])
        assert "nutty" in tags and "mid-import" in tags


# ── install_specific (M4) ─────────────────────────────────────────────


class TestInstallSpecific:
    """The flag that must protect the receiving install, not erase it."""

    async def test_true_writes_the_bundles_values(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_set_setting("llm_agent_id", "conversation.mine")
        await db.async_set_preference("weather_entity", "weather.mine")

        await sommelier_backup.async_apply_import(
            db, bundle, include_install_specific=True
        )

        assert await _settings_value(db, "llm_agent_id") == "conversation.local"
        assert await _preference_value(db, "weather_entity") == "weather.home"

    async def test_false_preserves_the_local_values(self, db):
        """The regression this test exists for: the flag must not wipe them."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_set_setting("llm_agent_id", "conversation.mine")
        await db.async_set_preference("weather_entity", "weather.mine")

        await sommelier_backup.async_apply_import(
            db, bundle, include_install_specific=False
        )

        assert await _settings_value(db, "llm_agent_id") == "conversation.mine"
        assert await _preference_value(db, "weather_entity") == "weather.mine"

    async def test_false_does_not_resurrect_the_bundles_values(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_set_setting("llm_agent_id", "conversation.mine")
        await sommelier_backup.async_apply_import(
            db, bundle, include_install_specific=False
        )
        assert await _settings_value(db, "llm_agent_id") != "conversation.local"

    async def test_handler_override_is_what_gets_written(self, db):
        """The handler pre-filters entity ids; the applier writes what it got."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await sommelier_backup.async_apply_import(
            db,
            bundle,
            include_install_specific=True,
            install_specific={"llm_agent_id": "homeassistant"},
        )
        assert await _settings_value(db, "llm_agent_id") == "homeassistant"
        # weather_entity was filtered out by the handler → the LOCAL value
        # stays, it is not replaced by the bundle's and not erased either.
        assert await _preference_value(db, "weather_entity") == "weather.home"

    async def test_value_the_handler_rejected_keeps_the_local_one(self, db):
        """A rejected install-specific value must not take the local one down.

        The handler drops a value whose entity is not in this state machine;
        before this was fixed the delete phase had already removed the local
        row, so the install ended up with neither — the Sommelier lost its LLM
        agent on any import from another box.
        """
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_set_setting("llm_agent_id", "conversation.mine")

        await sommelier_backup.async_apply_import(
            db,
            bundle,
            include_install_specific=True,
            install_specific={},  # everything rejected by the entity check
        )

        assert await _settings_value(db, "llm_agent_id") == "conversation.mine"
        assert await _preference_value(db, "weather_entity") == "weather.home"

    async def test_bundle_without_install_specific_keeps_the_local_ones(self, db):
        """A bundle that simply never carried them is the same case."""
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["install_specific"] = {}
        await db.async_set_setting("llm_agent_id", "conversation.mine")

        await sommelier_backup.async_apply_import(
            db, bundle, include_install_specific=True
        )

        assert await _settings_value(db, "llm_agent_id") == "conversation.mine"

    async def test_other_settings_still_replaced_when_flag_is_false(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        await db.async_set_setting("llm_timeout_s", "999")
        await sommelier_backup.async_apply_import(
            db, bundle, include_install_specific=False
        )
        assert await _settings_value(db, "llm_timeout_s") == "180"


# ── Compatibility policy ──────────────────────────────────────────────


class TestCompatibility:
    """§5.7 — what a bundle from another version is allowed to do."""

    async def test_older_db_schema_accepted(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["db_schema_version"] = SCHEMA_VERSION - 1
        await sommelier_backup.async_apply_import(db, bundle)
        assert await db.async_list_beans()

    async def test_newer_db_schema_refused(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        bundle["db_schema_version"] = SCHEMA_VERSION + 1
        with pytest.raises(sommelier_backup.UnsupportedDbSchemaError):
            await sommelier_backup.async_apply_import(db, bundle)

    async def test_bad_marker_refused(self, db):
        with pytest.raises(sommelier_backup.UnsupportedFormatError):
            sommelier_backup.validate_bundle({"format": "something.else"})

    async def test_missing_marker_refused(self, db):
        with pytest.raises(sommelier_backup.UnsupportedFormatError):
            sommelier_backup.validate_bundle({"format_version": 1})

    async def test_future_format_version_refused(self, db):
        with pytest.raises(sommelier_backup.UnsupportedFormatError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": sommelier_backup.EXPORT_FORMAT_VERSION + 1,
                }
            )

    async def test_non_object_payload_refused(self, db):
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(["not", "a", "bundle"])

    async def test_non_scalar_cell_refused(self, db):
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"coffee_beans": [{"id": {"nested": 1}}]},
                }
            )

    async def test_row_array_that_is_not_a_list_refused(self, db):
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"coffee_beans": {"id": 1}},
                }
            )

    async def test_over_the_row_cap_refused(self, db, monkeypatch):
        monkeypatch.setattr(sommelier_backup, "MAX_IMPORT_ROWS", 3)
        with pytest.raises(sommelier_backup.ImportTooLargeError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"flavor_tags": [{"name": str(i)} for i in range(4)]},
                }
            )

    async def test_bundle_without_a_tables_section_refused(self, db):
        """An envelope-only payload must never reach the delete phase.

        `tables` is optional nowhere: the applier deletes first and consults
        the sections afterwards, so a file that kept only the two envelope
        keys — hand-edited, tool-stripped, half-copied — used to wipe every
        replaceable table, insert nothing and report success.
        """
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                }
            )

    async def test_bundle_whose_tables_name_nothing_known_refused(self, db):
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"machine_capabilities": [{"id": "x"}]},
                }
            )

    async def test_envelope_only_bundle_leaves_the_db_alone(self, db):
        await _seed(db)
        before = await _dump(db)
        with pytest.raises(sommelier_backup.InvalidExportError):
            await sommelier_backup.async_apply_import(
                db,
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                },
            )
        assert await _dump(db) == before

    @pytest.mark.parametrize("value", [[1, 2, 3], "nope", 7])
    async def test_install_specific_of_the_wrong_type_refused(self, db, value):
        """The one hostile shape that used to raise out of the WS handler."""
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"flavor_tags": []},
                    "install_specific": value,
                }
            )

    async def test_install_specific_with_a_nested_value_refused(self, db):
        with pytest.raises(sommelier_backup.InvalidExportError):
            sommelier_backup.validate_bundle(
                {
                    "format": sommelier_backup.EXPORT_FORMAT,
                    "format_version": 1,
                    "tables": {"flavor_tags": []},
                    "install_specific": {"llm_agent_id": {"a": 1}},
                }
            )

    async def test_json_booleans_coerced(self, db):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(db)
        for row in bundle["tables"]["milk_config"]:
            row["available"] = True
        await sommelier_backup.async_apply_import(db, bundle)
        cursor = await db.db.execute(
            "SELECT DISTINCT available FROM milk_config"
        )
        assert [r[0] for r in await cursor.fetchall()] == [1]


# ── Snapshot store ────────────────────────────────────────────────────


class TestSnapshots:
    """The `<config>/melitta_barista_sommelier_backups` folder."""

    async def test_write_read_round_trip(self, db, tmp_path):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        name = sommelier_backup.write_snapshot(tmp_path, bundle)
        assert name.startswith("pre-import-")
        read = sommelier_backup.read_snapshot(tmp_path, name)
        assert read == bundle

    async def test_snapshot_is_a_bundle_not_a_db_copy(self, db, tmp_path):
        await _seed(db)
        bundle = await sommelier_backup.async_build_export(
            db, include_history=True
        )
        name = sommelier_backup.write_snapshot(tmp_path, bundle)
        payload = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert payload["format"] == sommelier_backup.EXPORT_FORMAT
        # …and it restores through the ordinary importer
        await db.async_delete_bean((await db.async_list_beans())[0]["id"])
        await sommelier_backup.async_apply_import(
            db, payload, include_history=True
        )
        assert await db.async_list_beans()

    async def test_collision_gets_a_suffix(self, tmp_path):
        first = sommelier_backup.write_snapshot(tmp_path, {"a": 1})
        second = sommelier_backup.write_snapshot(tmp_path, {"a": 2})
        assert first != second
        assert second.endswith("-2.json")

    def test_list_is_newest_first_and_never_parses(self, tmp_path):
        for index in range(3):
            path = tmp_path / f"pre-import-2026010{index}T000000Z.json"
            path.write_text("this is not JSON at all", encoding="utf-8")
        (tmp_path / "keeper.json").write_text("{}", encoding="utf-8")
        entries = sommelier_backup.list_snapshots(tmp_path)
        assert len(entries) == 4
        assert entries[0]["name"] == "keeper.json"
        assert entries[0]["source"] == "manual"
        assert all(e["size_bytes"] > 0 for e in entries)

    def test_list_of_missing_directory_is_empty(self, tmp_path):
        assert sommelier_backup.list_snapshots(tmp_path / "nope") == []

    def test_retention_prunes_to_five_and_spares_hand_kept_files(self, tmp_path):
        for index in range(8):
            (tmp_path / f"pre-import-2026010{index}T000000Z.json").write_text(
                "{}", encoding="utf-8"
            )
        (tmp_path / "my-own-backup.json").write_text("{}", encoding="utf-8")

        removed = sommelier_backup.prune_snapshots(tmp_path)

        remaining = {p.name for p in Path(tmp_path).iterdir()}
        assert len(removed) == 3
        assert "my-own-backup.json" in remaining
        assert len([n for n in remaining if n.startswith("pre-import-")]) == 5

    def test_retention_spares_the_protected_snapshot(self, tmp_path):
        """Restoring the oldest snapshot must not prune the file it read.

        The restore path writes its own pre-restore snapshot first, which puts
        the folder one over the retention limit — and the file that falls off
        the end is precisely the rollback point the user just reached for.
        """
        for index in range(6):
            (tmp_path / f"pre-import-2026010{index}T000000Z.json").write_text(
                "{}", encoding="utf-8"
            )
        oldest = "pre-import-20260100T000000Z.json"

        removed = sommelier_backup.prune_snapshots(tmp_path, protect=oldest)

        remaining = {p.name for p in Path(tmp_path).iterdir()}
        assert oldest not in removed
        assert oldest in remaining

    def test_snapshot_too_big_to_restore_is_not_left_on_disk(
        self, tmp_path, monkeypatch
    ):
        """Never write a rollback artifact `read_snapshot` would refuse."""
        monkeypatch.setattr(sommelier_backup, "MAX_SNAPSHOT_FILE_BYTES", 10)
        with pytest.raises(sommelier_backup.SnapshotFileTooLargeError):
            sommelier_backup.write_snapshot(
                tmp_path, {"format": "x", "tables": {"flavor_tags": []}}
            )
        assert list(Path(tmp_path).iterdir()) == []

    @pytest.mark.parametrize(
        "name",
        [
            "melitta-sommelier-2026-09-10 (1).json",
            "Küche-backup.json",
            "a" * 200 + ".json",
        ],
    )
    def test_hand_dropped_names_are_usable(self, tmp_path, name):
        """The list offers these files, so Download/Restore/Delete must work.

        The backups folder is a documented drop point, and a browser's
        duplicate-download naming produces exactly the first of these. A name
        rule stricter than the filesystem's only creates rows the panel can
        list but never act on.
        """
        (tmp_path / name).write_text('{"format": "x"}', encoding="utf-8")
        assert name in {e["name"] for e in sommelier_backup.list_snapshots(tmp_path)}
        assert sommelier_backup.read_snapshot(tmp_path, name) == {"format": "x"}
        sommelier_backup.delete_snapshot(tmp_path, name)
        assert not (tmp_path / name).exists()

    def test_delete_removes_exactly_one_file(self, tmp_path):
        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        (tmp_path / "b.json").write_text("{}", encoding="utf-8")
        sommelier_backup.delete_snapshot(tmp_path, "a.json")
        assert {p.name for p in Path(tmp_path).iterdir()} == {"b.json"}

    def test_delete_of_missing_file_raises(self, tmp_path):
        with pytest.raises(sommelier_backup.SnapshotNotFoundError):
            sommelier_backup.delete_snapshot(tmp_path, "ghost.json")

    def test_read_of_missing_file_raises(self, tmp_path):
        with pytest.raises(sommelier_backup.SnapshotNotFoundError):
            sommelier_backup.read_snapshot(tmp_path, "ghost.json")

    def test_oversized_snapshot_refused(self, tmp_path, monkeypatch):
        (tmp_path / "big.json").write_text("{}" + " " * 100, encoding="utf-8")
        monkeypatch.setattr(sommelier_backup, "MAX_SNAPSHOT_FILE_BYTES", 10)
        with pytest.raises(sommelier_backup.SnapshotFileTooLargeError):
            sommelier_backup.read_snapshot(tmp_path, "big.json")

    @pytest.mark.parametrize(
        "name",
        ["../secrets.json", "/etc/passwd", "a/b.json", "x.json.txt", "", None],
    )
    @pytest.mark.parametrize(
        "func",
        [
            sommelier_backup.read_snapshot,
            sommelier_backup.delete_snapshot,
        ],
    )
    def test_traversal_names_rejected(self, tmp_path, name, func):
        with pytest.raises(sommelier_backup.InvalidSnapshotNameError):
            func(tmp_path, name)

    def test_symlinked_escape_rejected(self, tmp_path):
        """The regex alone is not enough — containment is checked too."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.json").write_text("{}", encoding="utf-8")
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "escape.json").symlink_to(outside / "secret.json")
        with pytest.raises(sommelier_backup.InvalidSnapshotNameError):
            sommelier_backup.read_snapshot(backups, "escape.json")


# ── Module invariants ─────────────────────────────────────────────────


def test_module_imports_neither_aiosqlite_nor_sommelier_db():
    """Rule 2 of the module docstring, enforced against the source text.

    A module-level import of either would be evaluated while
    `custom_components.melitta_barista` is still importing, so an environment
    without aiosqlite would lose the ENTIRE integration rather than just the
    Sommelier surface.
    """
    source = Path(sommelier_backup.__file__).read_text(encoding="utf-8")
    module_level = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    ]
    assert not [line for line in module_level if "aiosqlite" in line]
    assert not [line for line in module_level if "sommelier_db" in line]
    assert not [line for line in module_level if "sommelier_api" in line]


def test_backup_lock_is_not_held_between_tests():
    """A leaked lock would make every later backup return db_busy."""
    assert not sommelier_backup._BACKUP_LOCK.locked()
    assert isinstance(sommelier_backup._BACKUP_LOCK, asyncio.Lock)
