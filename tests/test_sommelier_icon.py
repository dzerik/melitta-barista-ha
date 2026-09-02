"""UI Contract v1 §3.9 — sommelier payloads carry a per-recipe IconSpec.

Covers the Zone I-D surface: `sommelier/generate` results, saved-recipe
listings (`history/list` sessions) and `favorites` payloads all gain an
`icon` key computed by `ui_contract.build_icon_spec` from the recipe's
`machine_phases` components plus additive slots (extras syrup/topping/
liqueur), with `color_hint` looked up in the panel additive tables.
"""

from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import ai_recipes, panel_api
from custom_components.melitta_barista import sommelier_api as sa
from custom_components.melitta_barista.sommelier_db import SommelierDB
from custom_components.melitta_barista.ui_contract import build_icon_spec

pytestmark = pytest.mark.timeout(10)


LATTE_PHASES = [
    {
        "component": {
            "process": "coffee",
            "intensity": "medium",
            "aroma": "standard",
            "temperature": "normal",
            "shots": "two",
            "portion_ml": 40,
        },
        "user_action_before": [],
    },
    {
        "component": {
            "process": "milk",
            "intensity": "medium",
            "aroma": "standard",
            "temperature": "normal",
            "shots": "none",
            "portion_ml": 160,
        },
        "user_action_before": [],
    },
]

# Byte-exact §4 derivation for LATTE_PHASES: coffee darkness
# 0.30 + 0.125*2 + 0.10*1 = 0.65; milk foam round5(160*0.2)=30, body 130;
# component total 200 (not >200, coffee-first) -> cup; fill 200/220 -> 0.91.
EXPECTED_LATTE_ICON = {
    "spec_version": 1,
    "glass": "cup",
    "total_ml": 200,
    "fill_level": 0.91,
    "layers": [
        {"role": "coffee", "ml": 40, "fraction": 0.2, "intensity": 0.65},
        {"role": "milk", "ml": 130, "fraction": 0.65, "intensity": 0.0},
    ],
    "foam": {"role": "milk_foam", "ml": 30, "fraction": 0.15},
    "steam": True,
}


def _latte_recipe(**overrides) -> dict:
    base = {
        "name": "Latte",
        "description": "Classic milk-forward coffee",
        "blend": 1,
        "machine_phases": [json.loads(json.dumps(p)) for p in LATTE_PHASES],
        "extras": None,
        "steps": [{"order": 1, "action": "brew", "phase": "during"}],
        "cup_type": "mug",
    }
    base.update(overrides)
    return base


def _extras(**overrides) -> dict:
    base = {
        "ice": False,
        "syrup": None,
        "topping": None,
        "liqueur": None,
        "instruction": None,
    }
    base.update(overrides)
    return base


# ── pure helpers ──────────────────────────────────────────────────────


def test_attach_recipe_icon_matches_builder_byte_exact():
    """The attached icon is build_icon_spec's output for the phase components."""
    recipe = _latte_recipe()
    sa._attach_recipe_icon(recipe, {})
    assert recipe["icon"] == EXPECTED_LATTE_ICON
    assert recipe["icon"] == build_icon_spec(
        [phase["component"] for phase in LATTE_PHASES]
    )


def test_attach_recipe_icon_none_for_empty_composition():
    """No brewable phases and no additives -> explicit icon: None."""
    recipe = _latte_recipe(machine_phases=[], extras=None)
    sa._attach_recipe_icon(recipe, {})
    assert "icon" in recipe
    assert recipe["icon"] is None


def test_attach_recipe_icon_tolerates_placeholder_phase():
    """A 'none'-process placeholder phase alone still yields icon: None."""
    recipe = _latte_recipe(
        machine_phases=[
            {"component": {"process": "none", "portion_ml": 0}, "user_action_before": []}
        ]
    )
    sa._attach_recipe_icon(recipe, {})
    assert recipe["icon"] is None


def test_additive_slots_from_extras_order_and_hints():
    """Slots syrup -> topping -> liqueur; ml None; hint from lowercased name."""
    recipe = _latte_recipe(
        extras=_extras(ice=True, syrup="vanilla", topping="cocoa", instruction="stir")
    )
    slots = sa._recipe_additive_slots(recipe, {"vanilla": "#AB12CD"})
    assert slots == [
        {"name": "vanilla", "ml": None, "color_hint": "#AB12CD"},
        {"name": "cocoa", "ml": None, "color_hint": None},
    ]


def test_additive_slots_empty_without_extras():
    assert sa._recipe_additive_slots(_latte_recipe(extras=None), {}) == []
    assert sa._recipe_additive_slots(_latte_recipe(extras=_extras(ice=True)), {}) == []


def test_attach_recipe_icon_with_additive_layer():
    """Additive layer stacks above components, below foam; hint normalized."""
    recipe = _latte_recipe(extras=_extras(syrup="Vanilla"))
    sa._attach_recipe_icon(recipe, {"vanilla": "#AB12CD"})
    icon = recipe["icon"]
    assert icon["total_ml"] == 210  # 40 coffee + 130 milk + 10 syrup + 30 foam
    assert icon["glass"] == "cup"  # glass chosen from component ml only (§4.6)
    assert icon["fill_level"] == 0.95
    assert icon["layers"][-1] == {
        "role": "additive",
        "ml": 10,
        "fraction": 0.05,
        "intensity": 0.5,
        "color_hint": "#ab12cd",  # lowercased by §3.6 normalization
        "label": "Vanilla",
    }
    assert icon["foam"]["ml"] == 30
    # Determinism: same builder, same inputs.
    assert icon == build_icon_spec(
        [phase["component"] for phase in LATTE_PHASES],
        [{"name": "Vanilla", "ml": None, "color_hint": "#AB12CD"}],
    )


# ── DB color-hint lookup ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_additive_color_hints_missing_tables_is_empty():
    """Fresh sommelier DB has no panel additive tables -> no hints, no raise."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        hints = await sa._additive_color_hints(db)
        assert hints == {}
        await db.async_close()


@pytest.mark.asyncio
async def test_additive_color_hints_reads_attributes_json():
    """Hints come from syrups/toppings `attributes` JSON, keyed lowercase."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        for table in ("syrups", "toppings"):
            await db.db.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name TEXT, "
                "attributes TEXT)"
            )
        await db.db.execute(
            "INSERT INTO syrups (name, attributes) VALUES (?, ?)",
            ("Vanilla", json.dumps({"color_hint": "#FFAA00"})),
        )
        await db.db.execute(
            "INSERT INTO syrups (name, attributes) VALUES (?, ?)",
            ("Plain", None),
        )
        await db.db.execute(
            "INSERT INTO toppings (name, attributes) VALUES (?, ?)",
            ("Cocoa", json.dumps({"color": "#331100"})),
        )
        await db.db.execute(
            "INSERT INTO toppings (name, attributes) VALUES (?, ?)",
            ("Broken", "{not json"),
        )
        await db.db.commit()

        hints = await sa._additive_color_hints(db)
        assert hints == {"vanilla": "#FFAA00", "cocoa": "#331100"}
        await db.async_close()


# ── WS surfaces ───────────────────────────────────────────────────────


def _hass_with_db(db) -> MagicMock:
    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    return hass


def _sent_payload(connection) -> dict:
    assert connection.send_error.call_count == 0, connection.send_error.call_args
    return connection.send_result.call_args[0][1]


def _create_session_kwargs(recipes: list[dict]) -> dict:
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
async def test_ws_history_list_attaches_icons():
    """Saved-recipe listings (history sessions) carry per-recipe icons."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        await db.async_create_session(**_create_session_kwargs([_latte_recipe()]))

        connection = MagicMock()
        handler = inspect.unwrap(sa.ws_history_list)
        await handler(
            _hass_with_db(db),
            connection,
            {"id": 1, "type": "melitta_barista/sommelier/history/list",
             "limit": 20, "offset": 0},
        )

        payload = _sent_payload(connection)
        recipes = payload["sessions"][0]["recipes"]
        assert recipes[0]["icon"] == EXPECTED_LATTE_ICON
        await db.async_close()


@pytest.mark.asyncio
async def test_ws_favorites_list_attaches_icons():
    """favorites/list rows carry the same builder-derived icon."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        await db.async_add_favorite(_latte_recipe())

        connection = MagicMock()
        handler = inspect.unwrap(sa.ws_favorites_list)
        await handler(
            _hass_with_db(db),
            connection,
            {"id": 2, "type": "melitta_barista/sommelier/favorites/list"},
        )

        payload = _sent_payload(connection)
        assert payload["favorites"][0]["icon"] == EXPECTED_LATTE_ICON
        await db.async_close()


@pytest.mark.asyncio
async def test_ws_favorites_add_response_carries_icon():
    """favorites/add echoes the stored favorite with its icon attached."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SommelierDB(str(Path(tmpdir) / "test.db"))
        await db.async_setup()
        session = await db.async_create_session(
            **_create_session_kwargs([_latte_recipe()])
        )
        recipe_id = session["recipes"][0]["id"]

        connection = MagicMock()
        handler = inspect.unwrap(sa.ws_favorites_add)
        await handler(
            _hass_with_db(db),
            connection,
            {"id": 3, "type": "melitta_barista/sommelier/favorites/add",
             "recipe_id": recipe_id},
        )

        payload = _sent_payload(connection)
        assert payload["favorite"]["icon"] == EXPECTED_LATTE_ICON
        await db.async_close()


@pytest.mark.asyncio
async def test_ws_generate_attaches_icons_to_session_recipes():
    """sommelier/generate results carry per-recipe icons (§3.9)."""
    saved_recipe = _latte_recipe()
    db = MagicMock()
    db.async_get_hoppers = AsyncMock(return_value={})
    db.async_get_milk = AsyncMock(return_value=[])
    db.async_get_settings = AsyncMock(return_value={})
    db.async_get_pantry_extras = AsyncMock(return_value={})
    db.async_get_active_profile = AsyncMock(return_value=None)
    db.async_get_preferences = AsyncMock(return_value={})
    db.async_get_capabilities = AsyncMock(return_value=None)
    db.async_create_session = AsyncMock(
        return_value={"id": "s1", "recipes": [saved_recipe]}
    )

    hass = _hass_with_db(db)
    hass.config_entries.async_entries.return_value = []
    hass.config.language = "en"

    connection = MagicMock()
    connection.context = MagicMock(return_value=MagicMock())

    raw_llm_recipe = _latte_recipe(extras=None)
    handler = inspect.unwrap(sa.ws_generate)
    with (
        patch.object(sa, "_resolve_agent_id", AsyncMock(return_value="conversation.t")),
        patch.object(sa, "_check_llm_agent", MagicMock(return_value=None)),
        patch.object(panel_api, "_resolve_prompt", AsyncMock(return_value=None)),
        patch.object(ai_recipes, "_build_prompt", MagicMock(return_value="prompt")),
        patch.object(
            panel_api,
            "_structured_call",
            AsyncMock(return_value={"parsed": {"recipes": [raw_llm_recipe]}}),
        ),
    ):
        await handler(
            hass,
            connection,
            {"id": 4, "type": "melitta_barista/sommelier/generate",
             "mode": "surprise_me", "count": 1},
        )

    payload = _sent_payload(connection)
    assert payload["session"]["recipes"][0]["icon"] == EXPECTED_LATTE_ICON
