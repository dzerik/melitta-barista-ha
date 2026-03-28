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

SCHEMA_VERSION = 1

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

CREATE TABLE IF NOT EXISTS generation_sessions (
    id              TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,
    preference      TEXT,
    hopper1_bean_id TEXT REFERENCES coffee_beans(id),
    hopper2_bean_id TEXT REFERENCES coffee_beans(id),
    milk_types      TEXT,
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
        """Open DB and create schema."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
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
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await self._db.commit()
        _LOGGER.info("Sommelier DB initialized at %s", self._db_path)

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
    ) -> dict[str, Any]:
        """Create a generation session with recipes."""
        session_id = _new_id()
        now = _now()
        await self.db.execute(
            """INSERT INTO generation_sessions
               (id, mode, preference, hopper1_bean_id, hopper2_bean_id,
                milk_types, llm_agent, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                mode,
                preference,
                hopper1_bean_id,
                hopper2_bean_id,
                json.dumps(milk_types),
                llm_agent,
                now,
            ),
        )
        saved_recipes = []
        for recipe in recipes:
            recipe_id = _new_id()
            await self.db.execute(
                """INSERT INTO generated_recipes
                   (id, session_id, name, description, blend,
                    component1, component2, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    recipe_id,
                    session_id,
                    recipe["name"],
                    recipe["description"],
                    recipe["blend"],
                    json.dumps(recipe["component1"]),
                    json.dumps(recipe["component2"]),
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
                "brewed": False,
            })
        await self.db.commit()
        return {
            "id": session_id,
            "mode": mode,
            "preference": preference,
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
        await self.db.execute(
            """INSERT INTO favorites
               (id, name, description, blend, component1, component2,
                source_recipe_id, source_bean_id, brew_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                fav_id,
                data["name"],
                data["description"],
                data["blend"],
                json.dumps(data["component1"]),
                json.dumps(data["component2"]),
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
