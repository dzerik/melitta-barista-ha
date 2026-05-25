"""P3a — history/clear endpoint + async_clear_history."""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista.sommelier_db import SommelierDB


def _recipe(name: str = "X") -> dict:
    return {
        "name": name,
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


def _create_session_kwargs(recipes: list[dict]) -> dict:
    """Minimal kwargs for async_create_session (positional-as-kwargs)."""
    return {
        "mode": "surprise_me",
        "preference": None,
        "hopper1_bean_id": None,
        "hopper2_bean_id": None,
        "milk_types": [],
        "llm_agent": None,
        "recipes": recipes,
    }


@pytest.mark.asyncio
async def test_clear_history_default_keeps_favorited_sessions():
    """keep_favorited=True (default) preserves sessions whose recipes are in favorites."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        # Create two sessions.
        session_a = await db.async_create_session(
            **_create_session_kwargs([_recipe("Plain")]),
        )
        session_b = await db.async_create_session(
            **_create_session_kwargs([_recipe("Favorited")]),
        )

        # Favorite a recipe from session_b by passing source_recipe_id
        # in the recipe dict (async_add_favorite reads it via data.get).
        fav_recipe_id = session_b["recipes"][0]["id"]
        await db.async_add_favorite({
            **_recipe("Favorited copy"),
            "source_recipe_id": fav_recipe_id,
        })

        cleared = await db.async_clear_history()  # default keep_favorited=True

        history = await db.async_list_history()
        session_ids = [s["id"] for s in history]
        assert session_b["id"] in session_ids, (
            "session_b should be preserved (linked to favorite)"
        )
        assert session_a["id"] not in session_ids, "session_a should be deleted"
        assert cleared == 1

        await db.async_close()


@pytest.mark.asyncio
async def test_clear_history_force_removes_all():
    """keep_favorited=False removes all sessions regardless of favorite links."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()

        await db.async_create_session(**_create_session_kwargs([_recipe("A")]))
        await db.async_create_session(**_create_session_kwargs([_recipe("B")]))

        cleared = await db.async_clear_history(keep_favorited=False)
        history = await db.async_list_history()
        assert history == []
        assert cleared == 2

        await db.async_close()


@pytest.mark.asyncio
async def test_clear_history_when_empty():
    """Clearing an empty history returns 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        cleared = await db.async_clear_history()
        assert cleared == 0
        await db.async_close()


@pytest.mark.asyncio
async def test_ws_history_clear_default_keep():
    """WS handler defaults keep_favorited=True and returns cleared count."""
    from custom_components.melitta_barista import sommelier_api as sa

    db = MagicMock()
    db.async_clear_history = AsyncMock(return_value=3)
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_result = MagicMock()

    ws_history_clear = inspect.unwrap(sa.ws_history_clear)

    msg = {"id": 1, "type": "melitta_barista/sommelier/history/clear", "keep_favorited": True}
    await ws_history_clear(hass, connection, msg)

    db.async_clear_history.assert_awaited_once_with(keep_favorited=True)
    connection.send_result.assert_called_once()
    assert connection.send_result.call_args.args[1] == {"cleared": 3}


@pytest.mark.asyncio
async def test_ws_history_clear_force():
    """WS handler honours keep_favorited=False from msg."""
    from custom_components.melitta_barista import sommelier_api as sa

    db = MagicMock()
    db.async_clear_history = AsyncMock(return_value=5)
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_result = MagicMock()

    ws_history_clear = inspect.unwrap(sa.ws_history_clear)

    msg = {
        "id": 2,
        "type": "melitta_barista/sommelier/history/clear",
        "keep_favorited": False,
    }
    await ws_history_clear(hass, connection, msg)

    db.async_clear_history.assert_awaited_once_with(keep_favorited=False)
