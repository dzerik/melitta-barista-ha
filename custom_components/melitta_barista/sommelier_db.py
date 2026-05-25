"""SQLite database for AI Coffee Sommelier."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_LOGGER = logging.getLogger("melitta_barista")

SCHEMA_VERSION = 6

_VALID_RATING_TARGET_TYPES = frozenset({"generated", "favorite"})

_ALLOWED_FAVORITE_UPDATE_FIELDS = frozenset({"name", "description", "note"})

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
    machine_phases TEXT,
    extras      TEXT,
    steps       TEXT,
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
    machine_phases      TEXT,
    extras              TEXT,
    steps               TEXT,
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

CREATE TABLE IF NOT EXISTS machine_capabilities (
  entry_id TEXT PRIMARY KEY,
  json_payload TEXT NOT NULL,
  probed_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recipe_ratings (
  target_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('generated', 'favorite')),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  PRIMARY KEY (target_id, target_type)
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

# v2 → v3: persist the LLM-generated preparation step list so users keep
# the per-recipe `1. Brew espresso 30 ml / 2. Add vanilla syrup 15 ml` view
# after reload and when brewing from favorites. Stored as JSON in the
# `steps` column on both generated_recipes and favorites.
MIGRATE_V2_TO_V3 = """
ALTER TABLE generated_recipes ADD COLUMN steps TEXT;
ALTER TABLE favorites ADD COLUMN steps TEXT;
"""

MIGRATE_V3_TO_V4 = """
CREATE TABLE IF NOT EXISTS machine_capabilities (
  entry_id TEXT PRIMARY KEY,
  json_payload TEXT NOT NULL,
  probed_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1
);
"""

# v4 → v5: add `machine_phases` column to generated_recipes/favorites and
# back-fill existing rows by synthesizing a two-phase JSON array from the
# legacy component1/component2 BLE payload columns. The legacy columns stay
# NOT NULL for cross-version readability; writes synthesize them from phase[0]
# and phase[1] (see async_create_session/async_add_favorite).
MIGRATE_V4_TO_V5 = """
ALTER TABLE generated_recipes ADD COLUMN machine_phases TEXT;
ALTER TABLE favorites ADD COLUMN machine_phases TEXT;
UPDATE generated_recipes
   SET machine_phases = json_array(
       json_object('component', json(component1), 'user_action_before', json_array()),
       json_object('component', json(component2), 'user_action_before', json_array())
   )
 WHERE machine_phases IS NULL;
UPDATE favorites
   SET machine_phases = json_array(
       json_object('component', json(component1), 'user_action_before', json_array()),
       json_object('component', json(component2), 'user_action_before', json_array())
   )
 WHERE machine_phases IS NULL;
"""

# v5 → v6: add the `recipe_ratings` table for the user-facing recipe rating
# feature (1..5 stars + optional note, keyed by (target_id, target_type) so
# the same UUID can carry separate ratings as a generated recipe vs. as a
# saved favorite — see CRUD methods async_set_rating / async_get_rating /
# async_clear_rating).
MIGRATE_V5_TO_V6 = """
CREATE TABLE IF NOT EXISTS recipe_ratings (
  target_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('generated', 'favorite')),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  PRIMARY KEY (target_id, target_type)
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


def _attach_machine_phases(d: dict[str, Any]) -> None:
    """Populate `d["machine_phases"]` from the row's stored value or synthesize.

    For v5+ rows the column is already populated. For legacy v4 rows that
    survived migration as NULL (or callers reading after a partial migration),
    synthesize a two-phase array from the legacy component1/component2 fields.
    Phase[1] is dropped when its process is `none` to avoid emitting a spurious
    second phase for single-component recipes. Mutates `d` in place.
    """
    mp_raw = d.get("machine_phases")
    if mp_raw:
        d["machine_phases"] = json.loads(mp_raw)
        return
    c1_raw = d.get("component1") or "{}"
    c2_raw = d.get("component2") or "{}"
    c1 = json.loads(c1_raw) if isinstance(c1_raw, str) else c1_raw
    c2 = json.loads(c2_raw) if isinstance(c2_raw, str) else c2_raw
    phases: list[dict[str, Any]] = [{"component": c1, "user_action_before": []}]
    if c2 and c2.get("process") and c2.get("process") != "none":
        phases.append({"component": c2, "user_action_before": []})
    d["machine_phases"] = phases


class SommelierDB:
    """Async SQLite database manager for Coffee Sommelier."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

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
        else:
            # Apply each migration step in sequence. Each step is idempotent
            # at the per-statement level (ALTER TABLE may fail if the column
            # already exists; we swallow that so re-running is safe).
            migrations: list[tuple[int, str]] = []
            if current_version < 2:
                migrations.append((2, MIGRATE_V1_TO_V2))
            if current_version < 3:
                migrations.append((3, MIGRATE_V2_TO_V3))
            if current_version < 4:
                migrations.append((4, MIGRATE_V3_TO_V4))
            if current_version < 5:
                migrations.append((5, MIGRATE_V4_TO_V5))
            if current_version < 6:
                migrations.append((6, MIGRATE_V5_TO_V6))
            for target_version, sql in migrations:
                for stmt in sql.strip().split(";"):
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    try:
                        await self._db.execute(stmt)
                    except Exception:
                        pass  # Column/table may already exist
                _LOGGER.info(
                    "Sommelier DB migrated to v%d (from v%d)",
                    target_version, current_version,
                )

        now = _now()
        # Hopper rows may already exist, and on extremely minimal v3 fixtures
        # the table itself may be absent. Swallow either case rather than
        # blocking startup — the legitimate full-schema path stays unaffected.
        try:
            await self._db.execute(
                "INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (1, NULL, ?)",
                (now,),
            )
            await self._db.execute(
                "INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) VALUES (2, NULL, ?)",
                (now,),
            )
        except Exception:
            pass
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
            steps = recipe.get("steps")
            # Prefer the v5 phases-list representation when the caller supplies
            # it; synthesize the legacy NOT NULL component1/component2 columns
            # from the first/second phase so older readers and the DB
            # constraint remain happy.
            machine_phases = recipe.get("machine_phases") or []
            machine_phases_json = json.dumps(machine_phases)
            if machine_phases:
                legacy_c1_obj = (
                    machine_phases[0].get("component", {}) if len(machine_phases) >= 1 else {}
                )
                legacy_c2_obj = (
                    machine_phases[1].get("component", {}) if len(machine_phases) >= 2 else {}
                )
            else:
                # Backward-compat: pre-v5 callers still pass component1/component2 directly.
                legacy_c1_obj = recipe.get("component1", {})
                legacy_c2_obj = recipe.get("component2", {})
            await self.db.execute(
                """INSERT INTO generated_recipes
                   (id, session_id, name, description, blend,
                    component1, component2, machine_phases, extras, steps,
                    cup_type, calories, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    recipe_id,
                    session_id,
                    recipe["name"],
                    recipe["description"],
                    recipe["blend"],
                    json.dumps(legacy_c1_obj),
                    json.dumps(legacy_c2_obj),
                    machine_phases_json,
                    json.dumps(extras) if extras else None,
                    json.dumps(steps) if steps else None,
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
                "component1": legacy_c1_obj,
                "component2": legacy_c2_obj,
                "machine_phases": machine_phases,
                "extras": extras,
                "steps": steps or [],
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
        _attach_machine_phases(d)
        d["component1"] = json.loads(d["component1"])
        d["component2"] = json.loads(d["component2"])
        d["extras"] = json.loads(d["extras"]) if d.get("extras") else None
        d["steps"] = json.loads(d["steps"]) if d.get("steps") else []
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
                _attach_machine_phases(r)
                r["component1"] = json.loads(r["component1"])
                r["component2"] = json.loads(r["component2"])
                r["extras"] = json.loads(r["extras"]) if r.get("extras") else None
                r["steps"] = json.loads(r["steps"]) if r.get("steps") else []
                r["brewed"] = bool(r["brewed"])
                sess["recipes"].append(r)
            sessions.append(sess)
        return sessions

    async def async_clear_history(self, keep_favorited: bool = True) -> int:
        """Delete generation sessions (+ cascaded recipes). Returns # removed.

        When ``keep_favorited`` is True (default), sessions containing at least
        one recipe currently referenced by ``favorites.source_recipe_id`` are
        preserved. Cascade on ``generated_recipes.session_id`` (ON DELETE
        CASCADE, with PRAGMA foreign_keys=ON set at setup time) removes child
        recipe rows for every deleted session.
        """
        async with self._lock:
            if keep_favorited:
                cursor = await self.db.execute(
                    """DELETE FROM generation_sessions
                        WHERE id NOT IN (
                          SELECT DISTINCT r.session_id
                            FROM generated_recipes r
                            JOIN favorites f ON f.source_recipe_id = r.id
                        )"""
                )
            else:
                cursor = await self.db.execute("DELETE FROM generation_sessions")
            removed = cursor.rowcount or 0
            await self.db.commit()
        return removed

    # ── Favorites ─────────────────────────────────────────────────────

    async def async_list_favorites(self) -> list[dict[str, Any]]:
        """List all favorites, most brewed first."""
        cursor = await self.db.execute(
            "SELECT * FROM favorites ORDER BY brew_count DESC, created_at DESC"
        )
        result = []
        for row in await cursor.fetchall():
            d = _row_to_dict(row)
            _attach_machine_phases(d)
            d["component1"] = json.loads(d["component1"])
            d["component2"] = json.loads(d["component2"])
            d["extras"] = json.loads(d["extras"]) if d.get("extras") else None
            d["steps"] = json.loads(d["steps"]) if d.get("steps") else []
            result.append(d)
        return result

    async def async_add_favorite(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a recipe to favorites."""
        fav_id = _new_id()
        now = _now()
        extras = data.get("extras")
        steps = data.get("steps")
        # Prefer the v5 phases-list representation when present; synthesize the
        # legacy NOT NULL component1/component2 columns from the first/second
        # phase otherwise. Mirrors async_create_session.
        machine_phases = data.get("machine_phases") or []
        machine_phases_json = json.dumps(machine_phases)
        if machine_phases:
            legacy_c1_obj = (
                machine_phases[0].get("component", {}) if len(machine_phases) >= 1 else {}
            )
            legacy_c2_obj = (
                machine_phases[1].get("component", {}) if len(machine_phases) >= 2 else {}
            )
        else:
            legacy_c1_obj = data.get("component1", {})
            legacy_c2_obj = data.get("component2", {})
        await self.db.execute(
            """INSERT INTO favorites
               (id, name, description, blend, component1, component2,
                machine_phases, extras, steps, cup_type, source_recipe_id,
                source_bean_id, brew_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                fav_id,
                data["name"],
                data["description"],
                data["blend"],
                json.dumps(legacy_c1_obj),
                json.dumps(legacy_c2_obj),
                machine_phases_json,
                json.dumps(extras) if extras else None,
                json.dumps(steps) if steps else None,
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
        _attach_machine_phases(d)
        d["component1"] = json.loads(d["component1"])
        d["component2"] = json.loads(d["component2"])
        d["extras"] = json.loads(d["extras"]) if d.get("extras") else None
        d["steps"] = json.loads(d["steps"]) if d.get("steps") else []
        return d

    async def async_remove_favorite(self, fav_id: str) -> bool:
        """Remove a favorite. Returns True if removed."""
        cursor = await self.db.execute(
            "DELETE FROM favorites WHERE id = ?", (fav_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def async_update_favorite(self, favorite_id: str, **patch) -> bool:
        """Patch favorite fields. Returns True if any change was applied.

        Allowed: name, description, note. Note routes through recipe_ratings
        (favorite target_type) — requires an existing rating row.
        """
        if not patch:
            return False
        for k in patch:
            if k not in _ALLOWED_FAVORITE_UPDATE_FIELDS:
                raise ValueError(
                    f"field {k!r} not in allowed update set: "
                    f"{sorted(_ALLOWED_FAVORITE_UPDATE_FIELDS)}"
                )
        rows_changed = False
        db_columns = {k: v for k, v in patch.items() if k in {"name", "description"}}
        if db_columns:
            set_clause = ", ".join(f"{c} = ?" for c in db_columns)
            params = list(db_columns.values()) + [favorite_id]
            async with self._lock:
                cur = await self._db.execute(
                    f"UPDATE favorites SET {set_clause} WHERE id = ?",  # nosec B608
                    params,
                )
                await self._db.commit()
                rows_changed = cur.rowcount > 0
        if "note" in patch:
            note_value = patch["note"]
            existing = await self.async_get_rating(favorite_id, "favorite")
            if existing is None:
                raise ValueError(
                    "cannot set note without a rating; call recipe/rate first"
                )
            await self.async_set_rating(
                favorite_id, "favorite",
                int(existing["rating"]),
                note_value,
            )
            rows_changed = True
        return rows_changed

    async def async_increment_favorite_brew(self, fav_id: str) -> None:
        """Increment brew count for a favorite."""
        now = _now()
        await self.db.execute(
            "UPDATE favorites SET brew_count = brew_count + 1, last_brewed_at = ? WHERE id = ?",
            (now, fav_id),
        )
        await self.db.commit()

    # ── Recipe Ratings ────────────────────────────────────────────────

    async def async_set_rating(
        self, target_id: str, target_type: str, rating: int, note: str | None
    ) -> None:
        """Upsert a rating + optional note for a recipe (generated or favorite)."""
        if target_type not in _VALID_RATING_TARGET_TYPES:
            raise ValueError(
                f"target_type must be one of {sorted(_VALID_RATING_TARGET_TYPES)}, "
                f"got {target_type!r}"
            )
        if not (1 <= int(rating) <= 5):
            raise ValueError(f"rating must be in 1..5, got {rating!r}")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self._lock:
            cur = await self._db.execute(
                "SELECT created_at FROM recipe_ratings WHERE target_id = ? AND target_type = ?",
                (target_id, target_type),
            )
            existing = await cur.fetchone()
            if existing:
                await self._db.execute(
                    "UPDATE recipe_ratings SET rating = ?, note = ?, updated_at = ? "
                    "WHERE target_id = ? AND target_type = ?",
                    (int(rating), note, now, target_id, target_type),
                )
            else:
                await self._db.execute(
                    "INSERT INTO recipe_ratings (target_id, target_type, rating, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (target_id, target_type, int(rating), note, now),
                )
            await self._db.commit()

    async def async_clear_rating(self, target_id: str, target_type: str) -> None:
        """Remove the rating row (if any) for a target."""
        async with self._lock:
            await self._db.execute(
                "DELETE FROM recipe_ratings WHERE target_id = ? AND target_type = ?",
                (target_id, target_type),
            )
            await self._db.commit()

    async def async_get_rating(
        self, target_id: str, target_type: str
    ) -> dict | None:
        """Return the rating row or None."""
        async with self._lock:
            cur = await self._db.execute(
                "SELECT target_id, target_type, rating, note, created_at, updated_at "
                "FROM recipe_ratings WHERE target_id = ? AND target_type = ?",
                (target_id, target_type),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "target_id": row[0],
            "target_type": row[1],
            "rating": row[2],
            "note": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

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

    # ── Machine Capabilities ──────────────────────────────────────────

    async def async_get_capabilities(self, entry_id: str) -> dict[str, Any] | None:
        """Return the cached capabilities row for a config entry, or None."""
        async with self._lock:
            cur = await self._db.execute(
                "SELECT entry_id, json_payload, probed_at, schema_version "
                "FROM machine_capabilities WHERE entry_id = ?",
                (entry_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "entry_id": row[0],
            "json_payload": row[1],
            "probed_at": row[2],
            "schema_version": row[3],
        }

    async def async_save_capabilities(self, entry_id: str, json_payload: str) -> None:
        """Insert-or-replace the capabilities cache row for a config entry."""
        probed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self._lock:
            await self._db.execute(
                "INSERT OR REPLACE INTO machine_capabilities "
                "(entry_id, json_payload, probed_at, schema_version) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, json_payload, probed_at, 1),
            )
            await self._db.commit()

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

    async def async_set_active_profile(self, profile_id: str) -> bool:
        """Set a profile as active (deactivates all others).

        Returns True if the profile existed and is now active, False if no
        row matched profile_id (caller can surface a not_found error).
        """
        await self.db.execute("UPDATE sommelier_profiles SET is_active = 0")
        cursor = await self.db.execute(
            "UPDATE sommelier_profiles SET is_active = 1 WHERE id = ?",
            (profile_id,),
        )
        await self.db.commit()
        return cursor.rowcount > 0

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
