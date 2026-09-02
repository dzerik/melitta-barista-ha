"""Anti-repeat generation context + history star backend.

Covers 0.91.0b5:

1. `ai_recipes._existing_recipe_summaries` — the shared helper that
   condenses favorites + recent history into terse summary dicts for the
   "Existing Recipes" prompt section (profile filter passthrough, dedupe
   by name, recency strings).
2. `sommelier_db` additive enrichments — `favorite_id` on history recipe
   rows and `async_find_favorite_by_source`.
3. `ws_favorites_add` duplicate guard — second add returns the existing
   favorite instead of inserting a twin row.
4. `ws_generate` — the built prompt actually names an existing favorite.
"""

from __future__ import annotations

import inspect
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api as sa
from custom_components.melitta_barista.ai_recipes import (
    _existing_recipe_summaries,
    _recency_str,
    _summarize_recipe,
)
from custom_components.melitta_barista.sommelier_db import SommelierDB


def _stored_recipe(name: str, **overrides):
    base = {
        "name": name,
        "description": "Desc",
        "blend": 1,
        "machine_phases": [
            {
                "component": {
                    "process": "coffee", "intensity": "strong",
                    "aroma": "standard", "temperature": "normal",
                    "shots": "two", "portion_ml": 40,
                },
                "user_action_before": [],
            },
            {
                "component": {
                    "process": "milk", "intensity": "medium",
                    "aroma": "standard", "temperature": "normal",
                    "shots": "none", "portion_ml": 120,
                },
                "user_action_before": [],
            },
        ],
        "extras": {"ice": False, "syrup": "vanilla", "topping": None,
                   "liqueur": None, "instruction": None},
        "steps": [],
        "cup_type": "mug",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ── _recency_str / _summarize_recipe ─────────────────────────────────────


def test_recency_str_phrases():
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    assert _recency_str((now - timedelta(hours=2)).isoformat(), now) == "today"
    assert _recency_str((now - timedelta(days=1)).isoformat(), now) == "yesterday"
    assert _recency_str((now - timedelta(days=3)).isoformat(), now) == "3 days ago"
    assert _recency_str(None, now) is None
    assert _recency_str("not-a-date", now) is None


def test_summarize_recipe_extracts_traits():
    rec = _stored_recipe("Velvet Latte")
    now = datetime.now(timezone.utc)
    s = _summarize_recipe(rec, now=now)
    assert s["name"] == "Velvet Latte"
    assert s["milk"] is True
    assert s["strength"] == "strong"
    assert s["blend"] == 1
    assert s["extras"] == ["vanilla syrup"]
    assert s["recency"] == "today"


def test_summarize_recipe_black_coffee_no_extras():
    rec = _stored_recipe(
        "Solo",
        machine_phases=[{
            "component": {"process": "coffee", "intensity": "mild",
                          "portion_ml": 30},
            "user_action_before": [],
        }],
        extras=None,
        blend=0,
        created_at=None,
    )
    s = _summarize_recipe(rec)
    assert s["milk"] is False
    assert s["strength"] == "mild"
    assert s["blend"] == 0
    assert s["extras"] == []
    assert s["recency"] is None


# ── _existing_recipe_summaries ───────────────────────────────────────────


def _mock_db(favorites=(), sessions=()):
    db = MagicMock()
    db.async_list_favorites = AsyncMock(return_value=list(favorites))
    db.async_list_history = AsyncMock(return_value=list(sessions))
    return db


@pytest.mark.asyncio
async def test_summaries_pass_profile_filter_to_both_db_calls():
    db = _mock_db()
    await _existing_recipe_summaries(db, machine_profile=2)
    db.async_list_favorites.assert_awaited_once_with(machine_profile_filter=2)
    assert db.async_list_history.await_args.kwargs["machine_profile_filter"] == 2


@pytest.mark.asyncio
async def test_summaries_none_profile_means_unfiltered():
    db = _mock_db()
    await _existing_recipe_summaries(db, machine_profile=None)
    db.async_list_favorites.assert_awaited_once_with(machine_profile_filter=None)
    assert db.async_list_history.await_args.kwargs["machine_profile_filter"] is None


@pytest.mark.asyncio
async def test_summaries_dedupe_by_name_favorites_first():
    fav = _stored_recipe("Velvet Latte", blend=1)
    hist_dupe = _stored_recipe("velvet latte", blend=0)  # case-insensitive dupe
    hist_new = _stored_recipe("Midnight Espresso")
    sessions = [{"created_at": fav["created_at"],
                 "recipes": [hist_dupe, hist_new]}]
    db = _mock_db(favorites=[fav], sessions=sessions)
    result = await _existing_recipe_summaries(db)
    names = [s["name"] for s in result]
    assert names == ["Velvet Latte", "Midnight Espresso"]
    # The favorite's traits won over the history duplicate's.
    assert result[0]["blend"] == 1


@pytest.mark.asyncio
async def test_summaries_history_recipes_capped_at_10():
    recipes = [_stored_recipe(f"R{i}") for i in range(15)]
    sessions = [{"created_at": recipes[0]["created_at"], "recipes": recipes}]
    db = _mock_db(sessions=sessions)
    result = await _existing_recipe_summaries(db)
    assert len(result) == 10


@pytest.mark.asyncio
async def test_summaries_recipe_inherits_session_created_at():
    rec = _stored_recipe("Old Pick", created_at=None)
    session_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db = _mock_db(sessions=[{"created_at": session_ts, "recipes": [rec]}])
    result = await _existing_recipe_summaries(db)
    assert result[0]["recency"] == "3 days ago"


# ── DB enrichment: favorite_id on history + find-by-source ───────────────


@pytest.mark.asyncio
async def test_history_rows_carry_favorite_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        session = await db.async_create_session(
            mode="surprise_me", preference=None,
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=[_stored_recipe("Starred"), _stored_recipe("Plain")],
        )
        starred_id = session["recipes"][0]["id"]
        fav = await db.async_add_favorite(
            {**_stored_recipe("Starred"), "source_recipe_id": starred_id}
        )

        sessions = await db.async_list_history()
        recipes = {r["name"]: r for r in sessions[0]["recipes"]}
        assert recipes["Starred"]["favorite_id"] == fav["id"]
        assert recipes["Plain"]["favorite_id"] is None

        found = await db.async_find_favorite_by_source(starred_id)
        assert found is not None and found["id"] == fav["id"]
        assert await db.async_find_favorite_by_source("nope") is None

        await db.async_close()


# ── ws_favorites_add duplicate guard ─────────────────────────────────────


def _ws_env(db):
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    return hass, connection


@pytest.mark.asyncio
async def test_favorites_add_duplicate_returns_existing_row():
    existing = {"id": "fav-1", "name": "Starred"}
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=_stored_recipe("Starred"))
    db.async_find_favorite_by_source = AsyncMock(return_value=existing)
    db.async_add_favorite = AsyncMock()
    hass, connection = _ws_env(db)

    ws_favorites_add = inspect.unwrap(sa.ws_favorites_add)
    with patch.object(sa, "_attach_recipe_icons", new=AsyncMock()):
        await ws_favorites_add(hass, connection, {"id": 7, "recipe_id": "r1"})

    db.async_add_favorite.assert_not_awaited()
    payload = connection.send_result.call_args.args[1]
    assert payload["favorite"] == existing
    assert payload["duplicate"] is True


@pytest.mark.asyncio
async def test_favorites_add_first_time_inserts():
    recipe = _stored_recipe("Fresh")
    recipe["id"] = "r2"
    recipe["component1"] = {}
    recipe["component2"] = {}
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=recipe)
    db.async_find_favorite_by_source = AsyncMock(return_value=None)
    db.async_get_hoppers = AsyncMock(return_value={"hopper1": {}, "hopper2": {}})
    db.async_add_favorite = AsyncMock(return_value={"id": "fav-2", "name": "Fresh"})
    hass, connection = _ws_env(db)

    ws_favorites_add = inspect.unwrap(sa.ws_favorites_add)
    with patch.object(sa, "_attach_recipe_icons", new=AsyncMock()):
        await ws_favorites_add(hass, connection, {"id": 8, "recipe_id": "r2"})

    db.async_add_favorite.assert_awaited_once()
    payload = connection.send_result.call_args.args[1]
    assert payload["favorite"]["id"] == "fav-2"
    assert "duplicate" not in payload


# ── ws_generate threads the anti-repeat context into the prompt ──────────


@pytest.mark.asyncio
async def test_ws_generate_prompt_mentions_existing_favorite():
    """With a favorite in the DB, the prebuilt prompt names it in the
    Existing Recipes section."""
    captured = {}

    async def _fake_structured_call(hass, **kwargs):
        captured["prompt"] = kwargs.get("prebuilt_prompt")
        return {"parsed": {"recipes": []}, "validation_errors": []}

    db = MagicMock()
    db.async_get_hoppers = AsyncMock(return_value={"hopper1": {}, "hopper2": {}})
    db.async_get_milk = AsyncMock(return_value=[])
    db.async_get_pantry_extras = AsyncMock(return_value={})
    db.async_get_active_profile = AsyncMock(return_value=None)
    db.async_get_settings = AsyncMock(return_value={"llm_agent_id": None})
    db.async_get_preferences = AsyncMock(return_value={})
    db.async_get_capabilities = AsyncMock(return_value=None)
    db.async_list_favorites = AsyncMock(
        return_value=[_stored_recipe("Velvet Latte")]
    )
    db.async_list_history = AsyncMock(return_value=[])

    fake_entry = MagicMock()
    fake_entry.entry_id = "entry1"
    fake_entry.runtime_data = None

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    hass.config = MagicMock()
    hass.config.language = "en"
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[fake_entry])
    hass.config_entries.async_get_entry = MagicMock(return_value=fake_entry)
    hass.states = MagicMock()
    hass.states.async_all = MagicMock(return_value=[])

    connection = MagicMock()
    connection.context = MagicMock(return_value=None)

    msg = {
        "id": 1,
        "type": "melitta_barista/sommelier/generate",
        "mode": "surprise_me",
        "agent_id": "smartchain.test",
        "count": 3,
        "machine_profile": 1,
    }

    ws_generate = inspect.unwrap(sa.ws_generate)
    with patch(
        "custom_components.melitta_barista.panel_api._structured_call",
        new=_fake_structured_call,
    ):
        await ws_generate(hass, connection, msg)

    prompt = captured.get("prompt")
    assert prompt, "prebuilt_prompt never reached _structured_call"
    assert "## Existing Recipes" in prompt
    assert "Velvet Latte" in prompt
    # Profile scope was forwarded from the WS message.
    db.async_list_favorites.assert_awaited_once_with(machine_profile_filter=1)
