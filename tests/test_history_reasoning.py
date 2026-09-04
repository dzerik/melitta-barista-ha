"""Reasoning on history cards (0.89 leftover closed by the v10 migration).

The 0.89 pipeline carried the LLM's per-recipe `reasoning` end-to-end in
the LIVE generate reply, but never persisted it — history cards could not
show the "Why this recipe?" expander after a reload. Schema v10 adds a
`reasoning` column to `generated_recipes`:

1. fresh DBs expose the column; hand-rolled v9 DBs gain it via the strict
   migration runner (stamp advances to SCHEMA_VERSION);
2. `async_create_session` persists it, `async_get_recipe` /
   `async_list_history` return it (pre-v10 NULL rows normalize to "" —
   the same default as the live reply);
3. `ws_history_list` passes it through to the panel;
4. frontend contract (regex over the shipped source, same style as
   test_history_star_frontend.py): <melitta-sommelier-history> renders a
   per-card reasoning expander using the existing `sommelier.why` key.
"""

from __future__ import annotations

import inspect
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from custom_components.melitta_barista import sommelier_api
from custom_components.melitta_barista.sommelier_db import SCHEMA_VERSION, SommelierDB


@pytest.fixture
async def db() -> SommelierDB:
    """Create an in-memory SommelierDB, yield it, then close."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    yield sdb
    await sdb.async_close()


def _recipe(**overrides) -> dict:
    base = {
        "name": "Morning Flat White",
        "description": "d",
        "blend": 1,
        "component1": {"process": "coffee", "portion_ml": 40},
        "component2": {"process": "none", "portion_ml": 0},
    }
    base.update(overrides)
    return base


async def _create_session(db: SommelierDB, recipes: list[dict]) -> dict:
    return await db.async_create_session(
        mode="surprise_me", preference=None,
        hopper1_bean_id=None, hopper2_bean_id=None,
        milk_types=[], llm_agent=None, recipes=recipes,
    )


# ── 1. schema: fresh column + v9 → v10 migration ─────────────────────


async def test_fresh_db_has_reasoning_column(db: SommelierDB):
    cursor = await db.db.execute("PRAGMA table_info(generated_recipes)")
    cols = {row[1] for row in await cursor.fetchall()}
    assert "reasoning" in cols


async def test_legacy_v9_db_gains_reasoning_via_alter():
    """A hand-rolled v9 DB upgrades cleanly and its old rows read as ''."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            await conn.execute(
                "INSERT INTO settings(key, value) VALUES('schema_version', '9')"
            )
            # History readers JOIN recipe_ratings and subquery favorites,
            # so the hand-rolled schema needs those tables as well.
            await conn.execute(
                "CREATE TABLE recipe_ratings ("
                " target_id TEXT NOT NULL,"
                " target_type TEXT NOT NULL,"
                " rating INTEGER NOT NULL,"
                " note TEXT,"
                " created_at TEXT NOT NULL,"
                " updated_at TEXT,"
                " PRIMARY KEY (target_id, target_type)"
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
                " last_brewed_at TEXT,"
                " machine_profile INTEGER"
                ")"
            )
            # The system-preset seeder runs on every setup, so the
            # hand-rolled schema must carry the presets table too.
            await conn.execute(
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
            await conn.execute(
                "CREATE TABLE generation_sessions ("
                " id TEXT PRIMARY KEY,"
                " mode TEXT NOT NULL,"
                " preference TEXT,"
                " milk_types TEXT,"
                " created_at TEXT NOT NULL,"
                " machine_profile INTEGER"
                ")"
            )
            await conn.execute(
                "CREATE TABLE generated_recipes ("
                " id TEXT PRIMARY KEY,"
                " session_id TEXT NOT NULL,"
                " name TEXT NOT NULL,"
                " description TEXT NOT NULL,"
                " blend INTEGER NOT NULL,"
                " component1 TEXT NOT NULL,"
                " component2 TEXT NOT NULL,"
                " machine_phases TEXT,"
                " extras TEXT,"
                " steps TEXT,"
                " cup_type TEXT,"
                " calories INTEGER,"
                " brewed INTEGER NOT NULL DEFAULT 0,"
                " brewed_at TEXT,"
                " created_at TEXT NOT NULL"
                ")"
            )
            await conn.execute(
                "INSERT INTO generation_sessions(id, mode, created_at)"
                " VALUES('s1', 'surprise_me', '2026-01-01T00:00:00+00:00')"
            )
            await conn.execute(
                "INSERT INTO generated_recipes"
                " (id, session_id, name, description, blend,"
                "  component1, component2, created_at)"
                " VALUES('r1', 's1', 'Old Cup', 'd', 1,"
                "  '{\"process\": \"coffee\"}', '{}',"
                "  '2026-01-01T00:00:00+00:00')"
            )
            await conn.commit()

        db = SommelierDB(db_path)
        try:
            await db.async_setup()
            cursor = await db.db.execute("PRAGMA table_info(generated_recipes)")
            cols = {row[1] for row in await cursor.fetchall()}
            assert "reasoning" in cols

            cursor = await db.db.execute(
                "SELECT value FROM settings WHERE key='schema_version'"
            )
            row = await cursor.fetchone()
            assert row[0] == str(SCHEMA_VERSION)

            # Pre-v10 rows (NULL reasoning) normalize to "" on every reader.
            sessions = await db.async_list_history()
            assert sessions[0]["recipes"][0]["reasoning"] == ""
            recipe = await db.async_get_recipe("r1")
            assert recipe["reasoning"] == ""
        finally:
            await db.async_close()


# ── 2. persistence through create → read ─────────────────────────────


async def test_reasoning_survives_into_history(db: SommelierDB):
    """The rationale must persist — not just live in the generate reply."""
    await _create_session(
        db, [_recipe(reasoning="Rainy morning calls for a bold cup.")]
    )
    sessions = await db.async_list_history()
    assert sessions[0]["recipes"][0]["reasoning"] == (
        "Rainy morning calls for a bold cup."
    )


async def test_reasoning_survives_into_get_recipe(db: SommelierDB):
    session = await _create_session(
        db, [_recipe(reasoning="Bold pick for a bold day.")]
    )
    recipe_id = session["recipes"][0]["id"]
    recipe = await db.async_get_recipe(recipe_id)
    assert recipe["reasoning"] == "Bold pick for a bold day."


async def test_missing_reasoning_stored_and_read_as_empty(db: SommelierDB):
    await _create_session(db, [_recipe()])
    sessions = await db.async_list_history()
    assert sessions[0]["recipes"][0]["reasoning"] == ""


# ── 3. ws_history_list passes reasoning through ──────────────────────


@pytest.mark.asyncio
async def test_ws_history_list_payload_carries_reasoning():
    session = {
        "id": "s1",
        "mode": "surprise_me",
        "recipes": [
            {"id": "r1", "name": "Cup", "machine_phases": [],
             "reasoning": "Because it fits the afternoon."},
        ],
    }
    db = MagicMock()
    db.async_list_history = AsyncMock(return_value=[session])
    hass = MagicMock()
    connection = MagicMock()
    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_additive_color_hints", AsyncMock(return_value={})):
        await inspect.unwrap(sommelier_api.ws_history_list)(
            hass, connection, {"id": 1, "limit": 20, "offset": 0}
        )
    payload = connection.send_result.call_args.args[1]
    assert payload["sessions"][0]["recipes"][0]["reasoning"] == (
        "Because it fits the afternoon."
    )


# ── 4. frontend contract: history card expander ──────────────────────

_WWW = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
)
_HISTORY = _WWW / "components" / "melitta-sommelier-history.js"
_LOCALES = _WWW / "i18n" / "locales"


def _src() -> str:
    return _HISTORY.read_text(encoding="utf-8")


def test_history_card_renders_reasoning_expander():
    """Non-empty reasoning → a <details class="reasoning"> expander."""
    src = _src()
    block = re.search(
        r"recipe\.reasoning\s*\?\s*html`\s*<details class=\"reasoning\">"
        r"[\s\S]*?<summary>[\s\S]*?</summary>"
        r"[\s\S]*?\$\{recipe\.reasoning\}",
        src,
    )
    assert block, (
        "the history card must gate a reasoning <details> expander on "
        "recipe.reasoning"
    )


def test_history_reasoning_uses_existing_why_key():
    """Reuses the generate tab's `sommelier.why` label (no new i18n key)."""
    src = _src()
    assert 'this._t("sommelier.why")' in src


def test_history_reasoning_styles_match_generate_tab():
    """Visual parity with the melitta-sommelier.js expander styling."""
    src = _src()
    assert ".reasoning summary" in src
    assert ".reasoning p" in src


def test_why_key_exists_in_en_and_ru():
    for locale in ("en.js", "ru.js"):
        locale_src = (_LOCALES / locale).read_text(encoding="utf-8")
        assert '"sommelier.why"' in locale_src, f"{locale} must define sommelier.why"


class TestFavoriteReasoningV12:
    """Favourites keep the sommelier's justification (schema v12).

    History is prunable; a favourite is the row a user keeps, so the one
    sentence saying why the drink was suggested has to live there too.
    """

    async def test_favorite_stores_reasoning_from_the_recipe(self, db):
        fav = await db.async_add_favorite({
            "name": "Velvet Latte",
            "description": "Milk-forward",
            "blend": 1,
            "component1": {"process": "coffee"},
            "component2": {"process": "milk"},
            "reasoning": "Your dark roast carries milk well on a cold morning.",
        })
        assert fav["reasoning"] == (
            "Your dark roast carries milk well on a cold morning."
        )
        listed = await db.async_list_favorites()
        assert listed[0]["reasoning"] == fav["reasoning"]

    async def test_missing_reasoning_normalizes_to_empty_string(self, db):
        fav = await db.async_add_favorite({
            "name": "Plain",
            "description": "No justification recorded",
            "blend": 1,
            "component1": {"process": "coffee"},
            "component2": {"process": "none"},
        })
        assert fav["reasoning"] == ""
