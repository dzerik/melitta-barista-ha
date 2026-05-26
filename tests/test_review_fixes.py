"""Regression tests for the v0.50.1 code-review fixes.

Each block exercises one fix without spinning up a full Home Assistant
instance — the focus is on the data-correctness invariants that are easy
to break in future refactors. WebSocket admin-guard and panel-API
end-to-end coverage is intentionally out of scope here; that needs full
HA test infrastructure and would expand the suite by an order of
magnitude.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from custom_components.melitta_barista.sommelier_db import (
    MIGRATE_V2_TO_V3,
    SCHEMA_VERSION,
    SommelierDB,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def db() -> SommelierDB:
    """In-memory SommelierDB with a clean schema."""
    sdb = SommelierDB(":memory:")
    await sdb.async_setup()
    yield sdb
    await sdb.async_close()


def _component(process: str = "coffee", portion_ml: int = 30) -> dict[str, Any]:
    return {
        "process": process,
        "intensity": "strong",
        "aroma": "standard",
        "temperature": "normal",
        "shots": "one",
        "portion_ml": portion_ml,
    }


def _recipe_with_steps(**overrides: Any) -> dict[str, Any]:
    recipe = {
        "name": "Test Latte",
        "description": "A test recipe",
        "blend": 1,
        "component1": _component("coffee", 30),
        "component2": _component("milk", 120),
        "steps": [
            {"order": 1, "action": "brew", "ingredient": "espresso", "amount": 30, "unit": "ml"},
            {"order": 2, "action": "add", "ingredient": "vanilla syrup", "amount": 15, "unit": "ml"},
            {"order": 3, "action": "pour", "ingredient": "steamed milk", "amount": 120, "unit": "ml"},
        ],
        "extras": {"syrup": "vanilla"},
        "cup_type": "latte_glass",
    }
    recipe.update(overrides)
    return recipe


# ── #3: steps persistence ─────────────────────────────────────────────


class TestStepsPersistence:
    """`steps` round-trips through generated_recipes and favorites."""

    async def test_steps_saved_and_loaded_from_generated_recipe(self, db: SommelierDB):
        session = await db.async_create_session(
            mode="surprise_me",
            preference=None,
            hopper1_bean_id=None,
            hopper2_bean_id=None,
            milk_types=[],
            llm_agent=None,
            recipes=[_recipe_with_steps()],
        )
        recipe_id = session["recipes"][0]["id"]

        loaded = await db.async_get_recipe(recipe_id)

        assert loaded is not None
        assert isinstance(loaded["steps"], list)
        assert len(loaded["steps"]) == 3
        assert loaded["steps"][0]["action"] == "brew"
        assert loaded["steps"][1]["ingredient"] == "vanilla syrup"
        assert loaded["steps"][2]["amount"] == 120

    async def test_history_returns_steps(self, db: SommelierDB):
        await db.async_create_session(
            mode="surprise_me",
            preference=None,
            hopper1_bean_id=None,
            hopper2_bean_id=None,
            milk_types=[],
            llm_agent=None,
            recipes=[_recipe_with_steps()],
        )

        history = await db.async_list_history(limit=5)

        assert len(history) == 1
        assert len(history[0]["recipes"]) == 1
        assert history[0]["recipes"][0]["steps"][0]["action"] == "brew"

    async def test_favorite_persists_steps(self, db: SommelierDB):
        recipe = _recipe_with_steps()
        fav = await db.async_add_favorite({
            **recipe,
            "source_recipe_id": "test-recipe",
        })

        loaded = await db.async_get_favorite(fav["id"])

        assert loaded is not None
        assert len(loaded["steps"]) == 3
        assert loaded["steps"][0]["action"] == "brew"

    async def test_recipe_without_steps_returns_empty_list(self, db: SommelierDB):
        """Recipes generated before SCHEMA_VERSION 3 (or without LLM steps)
        should still be readable; missing steps must be normalised to []
        so the frontend's `Array.isArray(steps)` short-circuit works."""
        recipe = _recipe_with_steps()
        del recipe["steps"]

        session = await db.async_create_session(
            mode="surprise_me",
            preference=None,
            hopper1_bean_id=None,
            hopper2_bean_id=None,
            milk_types=[],
            llm_agent=None,
            recipes=[recipe],
        )

        loaded = await db.async_get_recipe(session["recipes"][0]["id"])

        assert loaded is not None
        assert loaded["steps"] == []

    def test_schema_v3_constant_advances(self):
        """SCHEMA_VERSION reached 3 in this release; future bumps must
        come with a new migration block, so this guard tells the reviewer
        to re-check the migration runner if it ever changes."""
        assert SCHEMA_VERSION >= 3
        assert "steps" in MIGRATE_V2_TO_V3


# ── #1: async_set_active_profile contract ─────────────────────────────


class TestSetActiveProfileReturn:
    """async_set_active_profile now reports whether the row existed."""

    async def test_returns_true_for_existing_profile(self, db: SommelierDB):
        profile = await db.async_add_profile({"name": "morning"})

        ok = await db.async_set_active_profile(profile["id"])

        assert ok is True
        active = await db.async_get_active_profile()
        assert active is not None
        assert active["id"] == profile["id"]

    async def test_returns_false_for_missing_profile(self, db: SommelierDB):
        ok = await db.async_set_active_profile("does-not-exist")

        assert ok is False

    async def test_add_profile_persists_nested_preferences(self, db: SommelierDB):
        """ws_profiles_add used to forward `preferences={"dietary": [...]}` as
        a kwarg the DB method never accepted; the dict was silently dropped.
        The fix merges the nested preferences into the profile row, so a
        round-trip through the DB layer must preserve them.
        """
        profile = await db.async_add_profile({
            "name": "Evening",
            "dietary": ["lactose_free"],
            "caffeine_pref": "decaf",
            "cup_size": "small",
        })

        assert profile["name"] == "Evening"
        assert profile["dietary"] == ["lactose_free"]
        assert profile["caffeine_pref"] == "decaf"
        assert profile["cup_size"] == "small"


# ── #1 (sommelier): settings/preferences allowlist ────────────────────


class TestSettingsAllowlist:
    """ws_settings_set / ws_preferences_set schemas reject arbitrary keys."""

    def _settings_schema(self) -> vol.Schema:
        # Reconstruct just the key-value schema fragment the WS handler uses.
        # Importing the full WS handler decorator wrapper would require an
        # HA event loop; the constants live in plain module scope.
        from custom_components.melitta_barista.sommelier_api import (
            VALID_SETTING_KEYS,
        )
        import homeassistant.helpers.config_validation as cv
        return vol.Schema({
            vol.Required("key"): vol.In(VALID_SETTING_KEYS),
            vol.Required("value"): cv.string,
        })

    def _preferences_schema(self) -> vol.Schema:
        from custom_components.melitta_barista.sommelier_api import (
            VALID_PREFERENCE_KEYS,
        )
        import homeassistant.helpers.config_validation as cv
        return vol.Schema({
            vol.Required("key"): vol.In(VALID_PREFERENCE_KEYS),
            vol.Required("value"): cv.string,
        })

    def test_settings_schema_rejects_schema_version(self):
        with pytest.raises(vol.Invalid):
            self._settings_schema()({"key": "schema_version", "value": "999"})

    def test_settings_schema_rejects_arbitrary_key(self):
        with pytest.raises(vol.Invalid):
            self._settings_schema()({"key": "evil_attr", "value": "x"})

    def test_settings_schema_accepts_llm_agent_id(self):
        out = self._settings_schema()({"key": "llm_agent_id", "value": "conversation.x"})
        assert out["key"] == "llm_agent_id"

    def test_preferences_schema_rejects_schema_version(self):
        with pytest.raises(vol.Invalid):
            self._preferences_schema()({"key": "schema_version", "value": "999"})

    def test_preferences_schema_accepts_valid_key(self):
        out = self._preferences_schema()({"key": "default_cup_size", "value": "mug"})
        assert out["key"] == "default_cup_size"

    def test_preferences_schema_accepts_use_weather(self):
        # 0.72.0 (P9 / TZ §10 B6): use_weather was readable but not
        # writable via WS. Now in the allowlist.
        out = self._preferences_schema()({"key": "use_weather", "value": "true"})
        assert out["key"] == "use_weather"

    def test_preferences_schema_accepts_use_presence(self):
        out = self._preferences_schema()({"key": "use_presence", "value": "false"})
        assert out["key"] == "use_presence"

    def test_preferences_schema_accepts_weather_entity(self):
        out = self._preferences_schema()(
            {"key": "weather_entity", "value": "weather.home"}
        )
        assert out["key"] == "weather_entity"


# ── #C3 frontend: safeHttpUrl URL sanitiser shape ────────────────────


class TestSafeHttpUrlContract:
    """Asserts that the JS sanitiser source still rejects javascript: URIs.

    We can't execute JS here, but the existence of the helper + presence
    of a `protocol === "http:"` allowlist is the contract the audit
    relied on. Regression-guarded as a text match against the source so
    a future refactor that strips it will fail this test.
    """

    def test_safe_http_url_helper_exists(self):
        from pathlib import Path

        beans_js = (
            Path(__file__).parent.parent
            / "custom_components"
            / "melitta_barista"
            / "www"
            / "components"
            / "melitta-beans.js"
        ).read_text()

        assert "function safeHttpUrl" in beans_js
        # Allowlist must check http(s); deny by default if the URL parser
        # raises or returns a different scheme.
        assert 'parsed.protocol === "http:"' in beans_js
        assert 'parsed.protocol === "https:"' in beans_js
        # Producer link must go through the helper, with rel=noopener.
        assert "safeHttpUrl(p.website)" in beans_js
        assert 'rel="noopener noreferrer"' in beans_js


# ── #C4: services removed on unload ───────────────────────────────────


class TestServiceLifecycleContract:
    """Smoke test that async_unload_entry's removal block names every
    service the integration registers. The textual check keeps the
    list in sync without needing a full HA harness."""

    def test_unload_removes_every_registered_service(self):
        from pathlib import Path

        init_py = (
            Path(__file__).parent.parent
            / "custom_components"
            / "melitta_barista"
            / "__init__.py"
        ).read_text()

        # Every SERVICE_* constant declared near line 470-480 must appear
        # in the unload block's removal tuple.
        services = [
            "SERVICE_BREW_FREESTYLE",
            "SERVICE_BREW_DIRECTKEY",
            "SERVICE_SAVE_DIRECTKEY",
            "SERVICE_RESET_RECIPE",
            "SERVICE_CONFIRM_PROMPT",
            "SERVICE_WRITE_RECIPE_PARAM",
            "SERVICE_WRITE_MYCOFFEE_PARAM",
        ]
        # Find the unload block and check each service is named within it.
        unload_idx = init_py.index("async def async_unload_entry")
        unload_body = init_py[unload_idx:unload_idx + 4000]
        for svc in services:
            assert svc in unload_body, f"{svc} is registered but not removed on unload"
        assert "hass.services.async_remove" in unload_body
