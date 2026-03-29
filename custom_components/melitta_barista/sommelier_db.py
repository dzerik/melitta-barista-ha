"""SQLite database for AI Coffee Sommelier."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_LOGGER = logging.getLogger("melitta_barista")

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS coffee_beans (
    id              TEXT PRIMARY KEY,
    brand           TEXT NOT NULL,
    product         TEXT NOT NULL,
    roast           TEXT NOT NULL,
    bean_type       TEXT NOT NULL,
    origin          TEXT NOT NULL,
    origin_country  TEXT,
    flavor_notes    TEXT,
    composition     TEXT,
    preset_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hoppers (
    hopper_id   INTEGER PRIMARY KEY,
    bean_id     TEXT REFERENCES coffee_beans(id) ON DELETE SET NULL,
    assigned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milk_config (
    milk_type   TEXT PRIMARY KEY,
    available   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_extras (
    category    TEXT NOT NULL,
    item        TEXT NOT NULL,
    available   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (category, item)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sommelier_profiles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cup_size        TEXT DEFAULT 'mug',
    temperature_pref TEXT DEFAULT 'hot_only',
    dietary         TEXT,
    caffeine_pref   TEXT DEFAULT 'regular',
    is_active       INTEGER NOT NULL DEFAULT 0,
    machine_profile INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_sessions (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT REFERENCES sommelier_profiles(id),
    mode            TEXT NOT NULL,
    preference      TEXT,
    mood            TEXT,
    occasion        TEXT,
    temperature     TEXT,
    servings        INTEGER DEFAULT 1,
    hopper1_bean_id TEXT REFERENCES coffee_beans(id),
    hopper2_bean_id TEXT REFERENCES coffee_beans(id),
    milk_types      TEXT,
    extras_context  TEXT,
    weather_context TEXT,
    llm_agent       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generated_recipes (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES generation_sessions(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    blend       INTEGER NOT NULL,
    component1  TEXT NOT NULL,
    component2  TEXT NOT NULL,
    extras      TEXT,
    cup_type    TEXT,
    calories    INTEGER,
    brewed      INTEGER NOT NULL DEFAULT 0,
    brewed_at   TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    blend               INTEGER NOT NULL,
    component1          TEXT NOT NULL,
    component2          TEXT NOT NULL,
    extras              TEXT,
    cup_type            TEXT,
    source_recipe_id    TEXT,
    source_bean_id      TEXT,
    brew_count          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    last_brewed_at      TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
"""

MIGRATE_V1_TO_V2 = """
CREATE TABLE IF NOT EXISTS user_extras (
    category    TEXT NOT NULL,
    item        TEXT NOT NULL,
    available   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (category, item)
);
CREATE TABLE IF NOT EXISTS user_preferences (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sommelier_profiles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    cup_size        TEXT DEFAULT 'mug',
    temperature_pref TEXT DEFAULT 'hot_only',
    dietary         TEXT,
    caffeine_pref   TEXT DEFAULT 'regular',
    is_active       INTEGER NOT NULL DEFAULT 0,
    machine_profile INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
ALTER TABLE generation_sessions ADD COLUMN profile_id TEXT;
ALTER TABLE generation_sessions ADD COLUMN mood TEXT;
ALTER TABLE generation_sessions ADD COLUMN occasion TEXT;
ALTER TABLE generation_sessions ADD COLUMN temperature TEXT;
ALTER TABLE generation_sessions ADD COLUMN servings INTEGER DEFAULT 1;
ALTER TABLE generation_sessions ADD COLUMN extras_context TEXT;
ALTER TABLE generation_sessions ADD COLUMN weather_context TEXT;
ALTER TABLE generated_recipes ADD COLUMN extras TEXT;
ALTER TABLE generated_recipes ADD COLUMN cup_type TEXT;
ALTER TABLE generated_recipes ADD COLUMN calories INTEGER;
ALTER TABLE favorites ADD COLUMN extras TEXT;
ALTER TABLE favorites ADD COLUMN cup_type TEXT;
"""

INIT_HOPPERS_SQL = """
INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (1, NULL, ?);
INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (2, NULL, ?);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return dict(row)


class SommelierDB:
    """Async SQLite database manager for Coffee Sommelier."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def async_setup(self) -> None:
        """Open DB and create schema, run migrations if needed."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        # Check current schema version
        current_version = 0
        try:
            cursor = await self._db.execute(
                "SELECT value FROM settings WHERE key = 'schema_version'"
            )
            row = await cursor.fetchone()
            if row:
                current_version = int(row["value"])
        except Exception:
            pass  # Table doesn't exist yet

        if current_version < 1:
            # Fresh install — create full schema
            await self._db.executescript(SCHEMA_SQL)
        elif current_version < SCHEMA_VERSION:
            # Migration from v1 to v2
            for stmt in MIGRATE_V1_TO_V2.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        await self._db.execute(stmt)
                    except Exception:
                        pass  # Column/table may already exist
            _LOGGER.info("Sommelier DB migrated from v%d to v%d", current_version, SCHEMA_VERSION)

        now = _now()
        await self._db.execute(
            "INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (1, NULL, ?)",
            (now,),
        )
        await self._db.execute(
            "INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (2, NULL, ?)",
            (now,),
        )
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await self._db.commit()
        _LOGGER.info("Sommelier DB initialized (v%d) at %s", SCHEMA_VERSION, self._db_path)

    async def async_close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not initialized"
        return self._db

    # ── Coffee Beans CRUD ─────────────────────────────────────────────

    async def async_list_beans(self) -> list[dict[str, Any]]:
        """List all coffee beans."""
        cursor = await self.db.execute(
            "SELECT * FROM coffee_beans ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["flavor_notes"] = json.loads(d["flavor_notes"]) if d["flavor_notes"] else []
            result.append(d)
        return result

    async def async_get_bean(self, bean_id: str) -> dict[str, Any] | None:
        """Get a single coffee bean by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM coffee_beans WHERE id = ?", (bean_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["flavor_notes"] = json.loads(d["flavor_notes"]) if d["flavor_notes"] else []
        return d

    async def async_add_bean(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new coffee bean to the catalog."""
        bean_id = _new_id()
        now = _now()
        flavor_notes = json.dumps(data.get("flavor_notes", []))
        await self.db.execute(
            """INSERT INTO coffee_beans
               (id, brand, product, roast, bean_type, origin, origin_country,
                flavor_notes, composition, preset_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bean_id,
                data["brand"],
                data["product"],
                data["roast"],
                data["bean_type"],
                data["origin"],
                data.get("origin_country"),
                flavor_notes,
                data.get("composition"),
                data.get("preset_id"),
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.async_get_bean(bean_id)  # type: ignore[return-value]

    async def async_update_bean(
        self, bean_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update an existing coffee bean."""
        existing = await self.async_get_bean(bean_id)
        if existing is None:
            return None
        now = _now()
        flavor_notes = json.dumps(data.get("flavor_notes", existing["flavor_notes"]))
        await self.db.execute(
            """UPDATE coffee_beans SET
               brand = ?, product = ?, roast = ?, bean_type = ?, origin = ?,
               origin_country = ?, flavor_notes = ?, composition = ?,
               preset_id = ?, updated_at = ?
               WHERE id = ?""",
            (
                data.get("brand", existing["brand"]),
                data.get("product", existing["product"]),
                data.get("roast", existing["roast"]),
                data.get("bean_type", existing["bean_type"]),
                data.get("origin", existing["origin"]),
                data.get("origin_country", existing.get("origin_country")),
                flavor_notes,
                data.get("composition", existing.get("composition")),
                data.get("preset_id", existing.get("preset_id")),
                now,
                bean_id,
            ),
        )
        await self.db.commit()
        return await self.async_get_bean(bean_id)

    async def async_delete_bean(self, bean_id: str) -> bool:
        """Delete a coffee bean. Returns True if deleted."""
        cursor = await self.db.execute(
            "DELETE FROM coffee_beans WHERE id = ?", (bean_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    # ── Hoppers ───────────────────────────────────────────────────────

    async def async_get_hoppers(self) -> dict[str, Any]:
        """Get current hopper assignments with full bean data."""
        result: dict[str, Any] = {}
        for hopper_id in (1, 2):
            cursor = await self.db.execute(
                """SELECT h.hopper_id, h.assigned_at, b.*
                   FROM hoppers h
                   LEFT JOIN coffee_beans b ON h.bean_id = b.id
                   WHERE h.hopper_id = ?""",
                (hopper_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                result[f"hopper{hopper_id}"] = None
                continue
            d = _row_to_dict(row)
            if d.get("id"):
                d["flavor_notes"] = (
                    json.loads(d["flavor_notes"]) if d["flavor_notes"] else []
                )
                result[f"hopper{hopper_id}"] = {
                    "assigned_at": d["assigned_at"],
                    "bean": {
                        k: d[k]
                        for k in (
                            "id", "brand", "product", "roast", "bean_type",
                            "origin", "origin_country", "flavor_notes", "composition",
                        )
                    },
                }
            else:
                result[f"hopper{hopper_id}"] = {"assigned_at": d["assigned_at"], "bean": None}
        return result

    async def async_assign_hopper(self, hopper_id: int, bean_id: str | None) -> None:
        """Assign a bean to a hopper (or clear with None)."""
        now = _now()
        await self.db.execute(
            "UPDATE hoppers SET bean_id = ?, assigned_at = ? WHERE hopper_id = ?",
            (bean_id, now, hopper_id),
        )
        await self.db.commit()

    # ── Milk Config ───────────────────────────────────────────────────

    async def async_get_milk(self) -> list[str]:
        """Get list of available milk types."""
        cursor = await self.db.execute(
            "SELECT milk_type FROM milk_config WHERE available = 1 ORDER BY milk_type"
        )
        rows = await cursor.fetchall()
        return [row["milk_type"] for row in rows]

    async def async_set_milk(self, milk_types: list[str]) -> None:
        """Set available milk types (replaces existing)."""
        await self.db.execute("DELETE FROM milk_config")
        for mt in milk_types:
            await self.db.execute(
                "INSERT INTO milk_config (milk_type, available) VALUES (?, 1)",
                (mt,),
            )
        await self.db.commit()

    # ── Generation Sessions & Recipes ─────────────────────────────────

    async def async_create_session(
        self,
        mode: str,
        preference: str | None,
        hopper1_bean_id: str | None,
        hopper2_bean_id: str | None,
        milk_types: list[str],
        llm_agent: str | None,
        recipes: list[dict[str, Any]],
        *,
        profile_id: str | None = None,
        mood: str | None = None,
        occasion: str | None = None,
        temperature: str | None = None,
        servings: int = 1,
        extras_context: dict[str, Any] | None = None,
        weather_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a generation session with recipes."""
        session_id = _new_id()
        now = _now()
        await self.db.execute(
            """INSERT INTO generation_sessions
               (id, profile_id, mode, preference, mood, occasion, temperature,
                servings, hopper1_bean_id, hopper2_bean_id,
                milk_types, extras_context, weather_context, llm_agent, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                profile_id,
                mode,
                preference,
                mood,
                occasion,
                temperature,
                servings,
                hopper1_bean_id,
                hopper2_bean_id,
                json.dumps(milk_types),
                json.dumps(extras_context) if extras_context else None,
                json.dumps(weather_context) if weather_context else None,
                llm_agent,
                now,
            ),
        )
        saved_recipes = []
        for recipe in recipes:
            recipe_id = _new_id()
            extras = recipe.get("extras")
            await self.db.execute(
                """INSERT INTO generated_recipes
                   (id, session_id, name, description, blend,
                    component1, component2, extras, cup_type, calories, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    recipe_id,
                    session_id,
                    recipe["name"],
                    recipe["description"],
                    recipe["blend"],
                    json.dumps(recipe["component1"]),
                    json.dumps(recipe["component2"]),
                    json.dumps(extras) if extras else None,
                    recipe.get("cup_type"),
                    recipe.get("calories_approx"),
                    now,
                ),
            )
            saved_recipes.append({
                "id": recipe_id,
                "name": recipe["name"],
                "description": recipe["description"],
                "blend": recipe["blend"],
                "component1": recipe["component1"],
                "component2": recipe["component2"],
                "extras": extras,
                "cup_type": recipe.get("cup_type"),
                "calories_approx": recipe.get("calories_approx"),
                "brewed": False,
            })
        await self.db.commit()
        return {
            "id": session_id,
            "mode": mode,
            "preference": preference,
            "mood": mood,
            "occasion": occasion,
            "created_at": now,
            "recipes": saved_recipes,
        }

    async def async_mark_recipe_brewed(self, recipe_id: str) -> None:
        """Mark a generated recipe as brewed."""
        now = _now()
        await self.db.execute(
            "UPDATE generated_recipes SET brewed = 1, brewed_at = ? WHERE id = ?",
            (now, recipe_id),
        )
        await self.db.commit()

    async def async_get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        """Get a single generated recipe by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM generated_recipes WHERE id = ?", (recipe_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["component1"] = json.loads(d["component1"])
        d["component2"] = json.loads(d["component2"])
        d["extras"] = json.loads(d["extras"]) if d.get("extras") else None
        d["brewed"] = bool(d["brewed"])
        return d

    async def async_list_history(
        self, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List generation sessions with their recipes, newest first."""
        cursor = await self.db.execute(
            """SELECT * FROM generation_sessions
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        sessions = []
        for sess_row in await cursor.fetchall():
            sess = _row_to_dict(sess_row)
            sess["milk_types"] = json.loads(sess["milk_types"]) if sess["milk_types"] else []
            recipe_cursor = await self.db.execute(
                """SELECT * FROM generated_recipes
                   WHERE session_id = ? ORDER BY created_at""",
                (sess["id"],),
            )
            sess["recipes"] = []
            for r_row in await recipe_cursor.fetchall():
                r = _row_to_dict(r_row)
                r["component1"] = json.loads(r["component1"])
                r["component2"] = json.loads(r["component2"])
                r["brewed"] = bool(r["brewed"])
                sess["recipes"].append(r)
            sessions.append(sess)
        return sessions

    # ── Favorites ─────────────────────────────────────────────────────

    async def async_list_favorites(self) -> list[dict[str, Any]]:
        """List all favorites, most brewed first."""
        cursor = await self.db.execute(
            "SELECT * FROM favorites ORDER BY brew_count DESC, created_at DESC"
        )
        result = []
        for row in await cursor.fetchall():
            d = _row_to_dict(row)
            d["component1"] = json.loads(d["component1"])
            d["component2"] = json.loads(d["component2"])
            result.append(d)
        return result

    async def async_add_favorite(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a recipe to favorites."""
        fav_id = _new_id()
        now = _now()
        extras = data.get("extras")
        await self.db.execute(
            """INSERT INTO favorites
               (id, name, description, blend, component1, component2,
                extras, cup_type, source_recipe_id, source_bean_id,
                brew_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                fav_id,
                data["name"],
                data["description"],
                data["blend"],
                json.dumps(data["component1"]),
                json.dumps(data["component2"]),
                json.dumps(extras) if extras else None,
                data.get("cup_type"),
                data.get("source_recipe_id"),
                data.get("source_bean_id"),
                now,
            ),
        )
        await self.db.commit()
        return await self.async_get_favorite(fav_id)  # type: ignore[return-value]

    async def async_get_favorite(self, fav_id: str) -> dict[str, Any] | None:
        """Get a single favorite by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM favorites WHERE id = ?", (fav_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["component1"] = json.loads(d["component1"])
        d["component2"] = json.loads(d["component2"])
        d["extras"] = json.loads(d["extras"]) if d.get("extras") else None
        return d

    async def async_remove_favorite(self, fav_id: str) -> bool:
        """Remove a favorite. Returns True if removed."""
        cursor = await self.db.execute(
            "DELETE FROM favorites WHERE id = ?", (fav_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def async_increment_favorite_brew(self, fav_id: str) -> None:
        """Increment brew count for a favorite."""
        now = _now()
        await self.db.execute(
            "UPDATE favorites SET brew_count = brew_count + 1, last_brewed_at = ? WHERE id = ?",
            (now, fav_id),
        )
        await self.db.commit()

    # ── Settings ──────────────────────────────────────────────────────

    async def async_get_settings(self) -> dict[str, str]:
        """Get all settings."""
        cursor = await self.db.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in await cursor.fetchall()}

    async def async_set_setting(self, key: str, value: str) -> None:
        """Set a single setting."""
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.db.commit()

    # ── User Extras (syrups, toppings, liqueurs, ice) ─────────────────

    async def async_get_extras(self) -> dict[str, list[str]]:
        """Get all available extras grouped by category."""
        cursor = await self.db.execute(
            "SELECT category, item FROM user_extras WHERE available = 1 ORDER BY category, item"
        )
        result: dict[str, list[str]] = {}
        for row in await cursor.fetchall():
            result.setdefault(row["category"], []).append(row["item"])
        return result

    async def async_set_extras(self, category: str, items: list[str]) -> None:
        """Set available extras for a category (replaces existing)."""
        await self.db.execute(
            "DELETE FROM user_extras WHERE category = ?", (category,)
        )
        for item in items:
            await self.db.execute(
                "INSERT INTO user_extras (category, item, available) VALUES (?, ?, 1)",
                (category, item),
            )
        await self.db.commit()

    # ── User Preferences ──────────────────────────────────────────────

    async def async_get_preferences(self) -> dict[str, str]:
        """Get all user preferences."""
        cursor = await self.db.execute("SELECT key, value FROM user_preferences")
        return {row["key"]: row["value"] for row in await cursor.fetchall()}

    async def async_set_preference(self, key: str, value: str) -> None:
        """Set a single user preference."""
        await self.db.execute(
            "INSERT OR REPLACE INTO user_preferences (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.db.commit()

    async def async_set_preferences_bulk(self, prefs: dict[str, str]) -> None:
        """Set multiple preferences at once."""
        for key, value in prefs.items():
            await self.db.execute(
                "INSERT OR REPLACE INTO user_preferences (key, value) VALUES (?, ?)",
                (key, value),
            )
        await self.db.commit()

    # ── Sommelier Profiles ────────────────────────────────────────────

    async def async_list_profiles(self) -> list[dict[str, Any]]:
        """List all sommelier profiles."""
        cursor = await self.db.execute(
            "SELECT * FROM sommelier_profiles ORDER BY is_active DESC, name"
        )
        result = []
        for row in await cursor.fetchall():
            d = _row_to_dict(row)
            d["dietary"] = json.loads(d["dietary"]) if d["dietary"] else []
            d["is_active"] = bool(d["is_active"])
            result.append(d)
        return result

    async def async_add_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new sommelier profile."""
        profile_id = _new_id()
        now = _now()
        dietary = json.dumps(data.get("dietary", []))
        await self.db.execute(
            """INSERT INTO sommelier_profiles
               (id, name, cup_size, temperature_pref, dietary, caffeine_pref,
                is_active, machine_profile, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                data["name"],
                data.get("cup_size", "mug"),
                data.get("temperature_pref", "hot_only"),
                dietary,
                data.get("caffeine_pref", "regular"),
                0,
                data.get("machine_profile"),
                now,
                now,
            ),
        )
        await self.db.commit()
        return await self.async_get_profile(profile_id)  # type: ignore[return-value]

    async def async_get_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Get a single profile by ID."""
        cursor = await self.db.execute(
            "SELECT * FROM sommelier_profiles WHERE id = ?", (profile_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["dietary"] = json.loads(d["dietary"]) if d["dietary"] else []
        d["is_active"] = bool(d["is_active"])
        return d

    async def async_update_profile(
        self, profile_id: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update an existing profile."""
        existing = await self.async_get_profile(profile_id)
        if existing is None:
            return None
        now = _now()
        dietary = json.dumps(data.get("dietary", existing["dietary"]))
        await self.db.execute(
            """UPDATE sommelier_profiles SET
               name = ?, cup_size = ?, temperature_pref = ?, dietary = ?,
               caffeine_pref = ?, machine_profile = ?, updated_at = ?
               WHERE id = ?""",
            (
                data.get("name", existing["name"]),
                data.get("cup_size", existing["cup_size"]),
                data.get("temperature_pref", existing["temperature_pref"]),
                dietary,
                data.get("caffeine_pref", existing["caffeine_pref"]),
                data.get("machine_profile", existing.get("machine_profile")),
                now,
                profile_id,
            ),
        )
        await self.db.commit()
        return await self.async_get_profile(profile_id)

    async def async_delete_profile(self, profile_id: str) -> bool:
        """Delete a profile."""
        cursor = await self.db.execute(
            "DELETE FROM sommelier_profiles WHERE id = ?", (profile_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def async_set_active_profile(self, profile_id: str) -> None:
        """Set a profile as active (deactivates all others)."""
        await self.db.execute("UPDATE sommelier_profiles SET is_active = 0")
        await self.db.execute(
            "UPDATE sommelier_profiles SET is_active = 1 WHERE id = ?",
            (profile_id,),
        )
        await self.db.commit()

    async def async_get_active_profile(self) -> dict[str, Any] | None:
        """Get the currently active profile."""
        cursor = await self.db.execute(
            "SELECT * FROM sommelier_profiles WHERE is_active = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["dietary"] = json.loads(d["dietary"]) if d["dietary"] else []
        d["is_active"] = True
        return d
