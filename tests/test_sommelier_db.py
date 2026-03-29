"""Tests for sommelier_db.py — SQLite database manager for AI Coffee Sommelier."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.melitta_barista.sommelier_db import SommelierDB


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def db() -> SommelierDB:
    """Create an in-memory SommelierDB, yield it, then close."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    yield sdb
    await sdb.async_close()


def _sample_bean_data(**overrides: Any) -> dict[str, Any]:
    """Return sample bean data with optional overrides."""
    data = {
        "brand": "Melitta",
        "product": "BellaCrema Espresso",
        "roast": "dark",
        "bean_type": "arabica",
        "origin": "blend",
        "origin_country": "Brazil",
        "flavor_notes": ["chocolate", "spicy"],
        "composition": "100% Arabica",
        "preset_id": "melitta_bellacrema_espresso",
    }
    data.update(overrides)
    return data


def _sample_component(process: str = "coffee", portion_ml: int = 30) -> dict[str, Any]:
    return {
        "process": process,
        "intensity": "strong",
        "aroma": "intense",
        "temperature": "normal",
        "shots": "two",
        "portion_ml": portion_ml,
    }


def _sample_recipe(**overrides: Any) -> dict[str, Any]:
    recipe = {
        "name": "Morning Espresso",
        "description": "A strong morning espresso",
        "blend": 1,
        "component1": _sample_component("coffee", 30),
        "component2": _sample_component("milk", 120),
    }
    recipe.update(overrides)
    return recipe


# ── Schema & Initialization ──────────────────────────────────────────


class TestSchemaInit:
    """Test database schema creation and initial state."""

    async def test_setup_creates_tables(self, db: SommelierDB):
        """Tables exist after setup."""
        cursor = await db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        expected = {
            "coffee_beans", "hoppers", "milk_config",
            "generation_sessions", "generated_recipes",
            "favorites", "settings",
        }
        assert expected.issubset(tables)

    async def test_hoppers_initialized(self, db: SommelierDB):
        """Both hoppers 1 and 2 exist with NULL beans."""
        cursor = await db.db.execute("SELECT * FROM hoppers ORDER BY hopper_id")
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["hopper_id"] == 1
        assert rows[0]["bean_id"] is None
        assert rows[1]["hopper_id"] == 2
        assert rows[1]["bean_id"] is None

    async def test_schema_version_set(self, db: SommelierDB):
        """Schema version setting is stored."""
        settings = await db.async_get_settings()
        assert settings["schema_version"] == "2"

    async def test_setup_idempotent(self):
        """Calling setup a second time does not fail or duplicate rows."""
        sdb = SommelierDB(":memory:")
        await sdb.async_setup()
        await sdb.async_close()
        # Re-setup on a new in-memory DB works
        sdb2 = SommelierDB(":memory:")
        await sdb2.async_setup()
        cursor = await sdb2.db.execute("SELECT COUNT(*) FROM hoppers")
        row = await cursor.fetchone()
        assert row[0] == 2
        await sdb2.async_close()

    async def test_db_property_raises_if_not_initialized(self):
        """Accessing .db before setup raises AssertionError."""
        sdb = SommelierDB(":memory:")
        with pytest.raises(AssertionError, match="Database not initialized"):
            _ = sdb.db

    async def test_close_sets_none(self):
        """After close, _db is None."""
        sdb = SommelierDB(":memory:")
        await sdb.async_setup()
        await sdb.async_close()
        assert sdb._db is None

    async def test_close_when_not_opened(self):
        """Closing without opening does not raise."""
        sdb = SommelierDB(":memory:")
        await sdb.async_close()  # should not raise


# ── Coffee Beans CRUD ─────────────────────────────────────────────────


class TestCoffeeBeans:
    """Test CRUD operations for coffee beans."""

    async def test_add_bean(self, db: SommelierDB):
        """Adding a bean returns it with an id and timestamps."""
        bean = await db.async_add_bean(_sample_bean_data())
        assert bean["id"]
        assert bean["brand"] == "Melitta"
        assert bean["product"] == "BellaCrema Espresso"
        assert bean["roast"] == "dark"
        assert bean["flavor_notes"] == ["chocolate", "spicy"]
        assert bean["created_at"]
        assert bean["updated_at"]

    async def test_get_bean(self, db: SommelierDB):
        """Retrieving a bean by id returns the correct record."""
        added = await db.async_add_bean(_sample_bean_data())
        fetched = await db.async_get_bean(added["id"])
        assert fetched is not None
        assert fetched["id"] == added["id"]
        assert fetched["brand"] == "Melitta"

    async def test_get_bean_not_found(self, db: SommelierDB):
        """Requesting a non-existent id returns None."""
        result = await db.async_get_bean("non-existent-id")
        assert result is None

    async def test_list_beans_empty(self, db: SommelierDB):
        """Initially the bean list is empty."""
        beans = await db.async_list_beans()
        assert beans == []

    async def test_list_beans_multiple(self, db: SommelierDB):
        """List returns all beans, newest first."""
        await db.async_add_bean(_sample_bean_data(product="Bean A"))
        await db.async_add_bean(_sample_bean_data(product="Bean B"))
        beans = await db.async_list_beans()
        assert len(beans) == 2
        # newest first
        assert beans[0]["product"] == "Bean B"

    async def test_update_bean(self, db: SommelierDB):
        """Partial update changes only specified fields."""
        added = await db.async_add_bean(_sample_bean_data())
        updated = await db.async_update_bean(added["id"], {"product": "New Name"})
        assert updated is not None
        assert updated["product"] == "New Name"
        assert updated["brand"] == "Melitta"  # unchanged

    async def test_update_bean_flavor_notes(self, db: SommelierDB):
        """Updating flavor_notes replaces the list."""
        added = await db.async_add_bean(_sample_bean_data())
        updated = await db.async_update_bean(added["id"], {"flavor_notes": ["nutty"]})
        assert updated["flavor_notes"] == ["nutty"]

    async def test_update_bean_not_found(self, db: SommelierDB):
        """Updating a non-existent bean returns None."""
        result = await db.async_update_bean("no-such-id", {"product": "X"})
        assert result is None

    async def test_delete_bean(self, db: SommelierDB):
        """Deleting a bean returns True and removes it."""
        added = await db.async_add_bean(_sample_bean_data())
        assert await db.async_delete_bean(added["id"]) is True
        assert await db.async_get_bean(added["id"]) is None

    async def test_delete_bean_not_found(self, db: SommelierDB):
        """Deleting a non-existent bean returns False."""
        assert await db.async_delete_bean("no-such-id") is False

    async def test_add_bean_no_optional_fields(self, db: SommelierDB):
        """Adding a bean without optional fields works."""
        data = {
            "brand": "Test",
            "product": "Simple",
            "roast": "light",
            "bean_type": "arabica",
            "origin": "Ethiopia",
        }
        bean = await db.async_add_bean(data)
        assert bean["origin_country"] is None
        assert bean["flavor_notes"] == []
        assert bean["composition"] is None
        assert bean["preset_id"] is None

    async def test_bean_flavor_notes_empty_list(self, db: SommelierDB):
        """Flavor notes stored as empty list when given empty."""
        bean = await db.async_add_bean(_sample_bean_data(flavor_notes=[]))
        assert bean["flavor_notes"] == []


# ── Hoppers ───────────────────────────────────────────────────────────


class TestHoppers:
    """Test hopper assignment operations."""

    async def test_get_hoppers_initial(self, db: SommelierDB):
        """Initial hoppers have no beans assigned."""
        hoppers = await db.async_get_hoppers()
        assert "hopper1" in hoppers
        assert "hopper2" in hoppers
        assert hoppers["hopper1"]["bean"] is None
        assert hoppers["hopper2"]["bean"] is None

    async def test_assign_hopper(self, db: SommelierDB):
        """Assigning a bean to a hopper links them."""
        bean = await db.async_add_bean(_sample_bean_data())
        await db.async_assign_hopper(1, bean["id"])
        hoppers = await db.async_get_hoppers()
        assert hoppers["hopper1"]["bean"] is not None
        assert hoppers["hopper1"]["bean"]["id"] == bean["id"]
        assert hoppers["hopper1"]["bean"]["brand"] == "Melitta"

    async def test_clear_hopper(self, db: SommelierDB):
        """Assigning None clears the hopper."""
        bean = await db.async_add_bean(_sample_bean_data())
        await db.async_assign_hopper(1, bean["id"])
        await db.async_assign_hopper(1, None)
        hoppers = await db.async_get_hoppers()
        assert hoppers["hopper1"]["bean"] is None

    async def test_assign_both_hoppers(self, db: SommelierDB):
        """Both hoppers can hold different beans."""
        bean1 = await db.async_add_bean(_sample_bean_data(product="Bean 1"))
        bean2 = await db.async_add_bean(_sample_bean_data(product="Bean 2"))
        await db.async_assign_hopper(1, bean1["id"])
        await db.async_assign_hopper(2, bean2["id"])
        hoppers = await db.async_get_hoppers()
        assert hoppers["hopper1"]["bean"]["product"] == "Bean 1"
        assert hoppers["hopper2"]["bean"]["product"] == "Bean 2"

    async def test_delete_bean_clears_hopper_fk(self, db: SommelierDB):
        """Deleting a bean clears the hopper FK (ON DELETE SET NULL)."""
        bean = await db.async_add_bean(_sample_bean_data())
        await db.async_assign_hopper(1, bean["id"])
        await db.async_delete_bean(bean["id"])
        hoppers = await db.async_get_hoppers()
        assert hoppers["hopper1"]["bean"] is None


# ── Milk Config ───────────────────────────────────────────────────────


class TestMilkConfig:
    """Test milk type configuration."""

    async def test_initial_milk_empty(self, db: SommelierDB):
        """No milk types configured initially."""
        assert await db.async_get_milk() == []

    async def test_set_and_get_milk(self, db: SommelierDB):
        """Setting milk types and retrieving them."""
        await db.async_set_milk(["whole", "oat", "almond"])
        result = await db.async_get_milk()
        assert sorted(result) == ["almond", "oat", "whole"]

    async def test_set_milk_replaces(self, db: SommelierDB):
        """Setting milk types replaces the previous list."""
        await db.async_set_milk(["whole", "oat"])
        await db.async_set_milk(["soy"])
        assert await db.async_get_milk() == ["soy"]

    async def test_set_milk_empty(self, db: SommelierDB):
        """Setting an empty list clears all milk types."""
        await db.async_set_milk(["whole"])
        await db.async_set_milk([])
        assert await db.async_get_milk() == []


# ── Generation Sessions & Recipes ────────────────────────────────────


class TestGenerationSessions:
    """Test session and recipe creation, retrieval, and history."""

    async def test_create_session(self, db: SommelierDB):
        """Creating a session returns it with recipes."""
        session = await db.async_create_session(
            mode="surprise_me",
            preference=None,
            hopper1_bean_id=None,
            hopper2_bean_id=None,
            milk_types=["whole"],
            llm_agent="agent.openai",
            recipes=[_sample_recipe()],
        )
        assert session["id"]
        assert session["mode"] == "surprise_me"
        assert session["preference"] is None
        assert len(session["recipes"]) == 1
        r = session["recipes"][0]
        assert r["name"] == "Morning Espresso"
        assert r["blend"] == 1
        assert r["brewed"] is False

    async def test_create_session_multiple_recipes(self, db: SommelierDB):
        """Creating a session with multiple recipes stores all."""
        recipes = [
            _sample_recipe(name="Recipe 1"),
            _sample_recipe(name="Recipe 2"),
            _sample_recipe(name="Recipe 3"),
        ]
        session = await db.async_create_session(
            mode="custom", preference="strong black",
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=recipes,
        )
        assert len(session["recipes"]) == 3
        names = {r["name"] for r in session["recipes"]}
        assert names == {"Recipe 1", "Recipe 2", "Recipe 3"}

    async def test_get_recipe(self, db: SommelierDB):
        """Retrieving a recipe by id returns full data."""
        session = await db.async_create_session(
            mode="custom", preference=None,
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=[_sample_recipe()],
        )
        recipe_id = session["recipes"][0]["id"]
        recipe = await db.async_get_recipe(recipe_id)
        assert recipe is not None
        assert recipe["name"] == "Morning Espresso"
        assert isinstance(recipe["component1"], dict)
        assert recipe["component1"]["process"] == "coffee"
        assert recipe["brewed"] is False

    async def test_get_recipe_not_found(self, db: SommelierDB):
        """Non-existent recipe returns None."""
        assert await db.async_get_recipe("no-such-id") is None

    async def test_mark_recipe_brewed(self, db: SommelierDB):
        """Marking a recipe as brewed sets brewed=True and brewed_at."""
        session = await db.async_create_session(
            mode="custom", preference=None,
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=[_sample_recipe()],
        )
        recipe_id = session["recipes"][0]["id"]
        await db.async_mark_recipe_brewed(recipe_id)
        recipe = await db.async_get_recipe(recipe_id)
        assert recipe["brewed"] is True
        assert recipe["brewed_at"] is not None

    async def test_list_history_empty(self, db: SommelierDB):
        """History is empty initially."""
        assert await db.async_list_history() == []

    async def test_list_history_order(self, db: SommelierDB):
        """History returns sessions newest first with nested recipes."""
        await db.async_create_session(
            mode="custom", preference="first",
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=[_sample_recipe(name="R1")],
        )
        await db.async_create_session(
            mode="surprise_me", preference=None,
            hopper1_bean_id=None, hopper2_bean_id=None,
            milk_types=["oat"], llm_agent=None,
            recipes=[_sample_recipe(name="R2")],
        )
        history = await db.async_list_history()
        assert len(history) == 2
        # newest first
        assert history[0]["mode"] == "surprise_me"
        assert history[0]["milk_types"] == ["oat"]
        assert len(history[0]["recipes"]) == 1

    async def test_list_history_limit_offset(self, db: SommelierDB):
        """Limit and offset work correctly."""
        for i in range(5):
            await db.async_create_session(
                mode="custom", preference=f"pref{i}",
                hopper1_bean_id=None, hopper2_bean_id=None,
                milk_types=[], llm_agent=None,
                recipes=[_sample_recipe(name=f"R{i}")],
            )
        page = await db.async_list_history(limit=2, offset=0)
        assert len(page) == 2
        page2 = await db.async_list_history(limit=2, offset=2)
        assert len(page2) == 2
        # pages should not overlap
        ids_page1 = {s["id"] for s in page}
        ids_page2 = {s["id"] for s in page2}
        assert ids_page1.isdisjoint(ids_page2)

    async def test_session_with_bean_refs(self, db: SommelierDB):
        """Session stores bean id references."""
        bean = await db.async_add_bean(_sample_bean_data())
        session = await db.async_create_session(
            mode="custom", preference=None,
            hopper1_bean_id=bean["id"], hopper2_bean_id=None,
            milk_types=[], llm_agent=None,
            recipes=[_sample_recipe()],
        )
        history = await db.async_list_history()
        assert history[0]["hopper1_bean_id"] == bean["id"]
        assert history[0]["hopper2_bean_id"] is None


# ── Favorites ─────────────────────────────────────────────────────────


class TestFavorites:
    """Test favorites CRUD and brew count."""

    def _fav_data(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "name": "My Favorite Latte",
            "description": "Creamy and delicious",
            "blend": 1,
            "component1": _sample_component("coffee", 30),
            "component2": _sample_component("milk", 120),
        }
        data.update(overrides)
        return data

    async def test_add_favorite(self, db: SommelierDB):
        """Adding a favorite returns it with id and brew_count=0."""
        fav = await db.async_add_favorite(self._fav_data())
        assert fav["id"]
        assert fav["name"] == "My Favorite Latte"
        assert fav["brew_count"] == 0
        assert isinstance(fav["component1"], dict)

    async def test_get_favorite(self, db: SommelierDB):
        """Retrieving a favorite by id works."""
        added = await db.async_add_favorite(self._fav_data())
        fetched = await db.async_get_favorite(added["id"])
        assert fetched is not None
        assert fetched["name"] == "My Favorite Latte"

    async def test_get_favorite_not_found(self, db: SommelierDB):
        """Non-existent favorite returns None."""
        assert await db.async_get_favorite("no-such-id") is None

    async def test_list_favorites_empty(self, db: SommelierDB):
        """Initially no favorites."""
        assert await db.async_list_favorites() == []

    async def test_list_favorites_ordered_by_brew_count(self, db: SommelierDB):
        """Favorites are ordered by brew_count descending."""
        fav1 = await db.async_add_favorite(self._fav_data(name="Low"))
        fav2 = await db.async_add_favorite(self._fav_data(name="High"))
        await db.async_increment_favorite_brew(fav2["id"])
        await db.async_increment_favorite_brew(fav2["id"])
        favs = await db.async_list_favorites()
        assert favs[0]["name"] == "High"
        assert favs[0]["brew_count"] == 2
        assert favs[1]["name"] == "Low"

    async def test_remove_favorite(self, db: SommelierDB):
        """Removing a favorite returns True and deletes it."""
        fav = await db.async_add_favorite(self._fav_data())
        assert await db.async_remove_favorite(fav["id"]) is True
        assert await db.async_get_favorite(fav["id"]) is None

    async def test_remove_favorite_not_found(self, db: SommelierDB):
        """Removing a non-existent favorite returns False."""
        assert await db.async_remove_favorite("no-such-id") is False

    async def test_increment_favorite_brew(self, db: SommelierDB):
        """Incrementing brew count increases it and sets last_brewed_at."""
        fav = await db.async_add_favorite(self._fav_data())
        await db.async_increment_favorite_brew(fav["id"])
        updated = await db.async_get_favorite(fav["id"])
        assert updated["brew_count"] == 1
        assert updated["last_brewed_at"] is not None

    async def test_add_favorite_with_source_refs(self, db: SommelierDB):
        """Favorite stores optional source_recipe_id and source_bean_id."""
        fav = await db.async_add_favorite(self._fav_data(
            source_recipe_id="recipe-123",
            source_bean_id="bean-456",
        ))
        assert fav["source_recipe_id"] == "recipe-123"
        assert fav["source_bean_id"] == "bean-456"


# ── Settings ──────────────────────────────────────────────────────────


class TestSettings:
    """Test key-value settings storage."""

    async def test_get_settings_initial(self, db: SommelierDB):
        """Initial settings contain schema_version."""
        settings = await db.async_get_settings()
        assert "schema_version" in settings

    async def test_set_and_get_setting(self, db: SommelierDB):
        """Setting a key stores and retrieves it."""
        await db.async_set_setting("llm_agent", "agent.openai")
        settings = await db.async_get_settings()
        assert settings["llm_agent"] == "agent.openai"

    async def test_set_setting_upsert(self, db: SommelierDB):
        """Setting an existing key overwrites the value."""
        await db.async_set_setting("theme", "dark")
        await db.async_set_setting("theme", "light")
        settings = await db.async_get_settings()
        assert settings["theme"] == "light"

    async def test_multiple_settings(self, db: SommelierDB):
        """Multiple settings coexist."""
        await db.async_set_setting("key1", "val1")
        await db.async_set_setting("key2", "val2")
        settings = await db.async_get_settings()
        assert settings["key1"] == "val1"
        assert settings["key2"] == "val2"
