"""SQLite database for AI Coffee Sommelier."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_LOGGER = logging.getLogger("melitta_barista")

SCHEMA_VERSION = 12

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
    created_at      TEXT NOT NULL,
    machine_profile INTEGER
);

CREATE TABLE IF NOT EXISTS generated_recipes (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES generation_sessions(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    reasoning   TEXT,
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
    last_brewed_at      TEXT,
    machine_profile     INTEGER,
    reasoning           TEXT
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

CREATE TABLE IF NOT EXISTS sommelier_presets (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  description      TEXT,
  payload          TEXT NOT NULL,
  is_system        INTEGER NOT NULL DEFAULT 0,
  dynamic_occasion INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT,
  machine_profile  INTEGER
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

# v6 → v7: add the `sommelier_presets` table for user-managed named templates
# of the Sommelier "generate" form (R7 / P5a slice 1). User-managed only —
# no built-in system presets and no profile binding in this slice.
MIGRATE_V6_TO_V7 = """
CREATE TABLE IF NOT EXISTS sommelier_presets (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  payload      TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT
);
"""

# v7 → v8: extend `sommelier_presets` with `is_system` (write-protected
# built-in flag) and `dynamic_occasion` (re-resolve occasion at brew time
# from local time of day). Companion seeder `async_seed_system_presets`
# populates four built-in presets (Morning / After lunch / Work / Guests).
MIGRATE_V7_TO_V8 = """
ALTER TABLE sommelier_presets ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sommelier_presets ADD COLUMN dynamic_occasion INTEGER NOT NULL DEFAULT 0;
"""

# v8 → v9: optional machine_profile INTEGER on presets / favorites /
# generation_sessions, tagging a row to a specific machine hardware
# profile slot (1..n). NULL = shared across all profiles, the default
# for every existing row after migration. The WS surface and FE plumbing
# ride in P7a Tasks 2 and P7b respectively — this slice is DB-only.
MIGRATE_V8_TO_V9 = """
ALTER TABLE sommelier_presets ADD COLUMN machine_profile INTEGER;
ALTER TABLE favorites ADD COLUMN machine_profile INTEGER;
ALTER TABLE generation_sessions ADD COLUMN machine_profile INTEGER;
"""

# v9 → v10: persist the LLM's "why this pick" rationale per generated
# recipe. The 0.89 pipeline carried `reasoning` end-to-end in the live
# generate reply but never stored it, so history cards could not show the
# "Why this recipe?" expander after a reload. Existing rows keep NULL
# (read paths normalize to "" — same default as the live path).
MIGRATE_V9_TO_V10 = """
ALTER TABLE generated_recipes ADD COLUMN reasoning TEXT;
"""

# v11 → v12: favourites keep the sommelier's own justification.
# `reasoning` reached generated_recipes in v10, but favouriting a recipe
# dropped it — the row a user keeps forever lost the one sentence saying
# why the drink was suggested. Backfilled from the source recipe where
# that row still exists.
MIGRATE_V11_TO_V12 = """
ALTER TABLE favorites ADD COLUMN reasoning TEXT;
UPDATE favorites
   SET reasoning = (
       SELECT gr.reasoning FROM generated_recipes gr
        WHERE gr.id = favorites.source_recipe_id
   )
 WHERE reasoning IS NULL AND source_recipe_id IS NOT NULL;
"""

# v10 → v11: one-time normalization of the legacy cup-size token
# `espresso` (written server-side by PWA ≤1.8.3 through the
# profiles/add|update WS path) to the served token `espresso_cup` in
# both storage columns (UI Contract §9.2.6.4). Without it, a stored
# legacy token hits the hard vol.In(VALID_CUP_SIZES) rejection on
# generate-time reads and silently misses the CUP_SIZE_VOLUMES prompt
# lookup. The write path in sommelier_api (CUP_SIZE_ALIASES)
# re-normalizes any further legacy writes on ingest, so this data
# rewrite stays one-time. Idempotent — re-running matches zero rows.
MIGRATE_V10_TO_V11 = """
UPDATE sommelier_profiles SET cup_size = 'espresso_cup' WHERE cup_size = 'espresso';
UPDATE user_preferences SET value = 'espresso_cup' WHERE key = 'default_cup_size' AND value = 'espresso';
"""

# Four built-in system presets seeded by `async_seed_system_presets` on
# first setup. Deterministic ids (`sys_*`) keep INSERT OR IGNORE re-runs
# stable. Each `payload` is a Sommelier "generate" form template; the
# `name_key` lets the panel resolve a translated label, with the row's
# `name` column acting as the English fallback.
SYSTEM_PRESETS = [
    {
        "id": "sys_morning",
        "name": "Morning",
        "description": "Energizing brew for the start of the day.",
        "dynamic_occasion": True,
        "payload": {
            "name_key": "presets.system.morning",
            "mode": "surprise_me",
            "occasion": "morning",
            "temperature": "hot",
            "moods": ["energizing"],
            "caffeine_pref": "regular",
            "cup_size": "mug",
            "dietary": [],
            "dynamic_occasion": True,
        },
    },
    {
        "id": "sys_after_lunch",
        "name": "After lunch",
        "description": "Balanced cup for the afternoon.",
        "dynamic_occasion": True,
        "payload": {
            "name_key": "presets.system.after_lunch",
            "mode": "surprise_me",
            "occasion": "after_lunch",
            "temperature": "auto",
            "moods": ["balanced"],
            "caffeine_pref": "regular",
            "cup_size": "cup",
            "dietary": [],
            "dynamic_occasion": True,
        },
    },
    {
        "id": "sys_work",
        "name": "Work",
        "description": "Focused, head-down brew.",
        "dynamic_occasion": True,
        "payload": {
            "name_key": "presets.system.work",
            "mode": "surprise_me",
            "occasion": "work",
            "temperature": "hot",
            "moods": ["focused"],
            "caffeine_pref": "regular",
            "cup_size": "cup",
            "dietary": [],
            "dynamic_occasion": True,
        },
    },
    {
        "id": "sys_guests",
        "name": "Guests",
        "description": "Something special for visitors.",
        "dynamic_occasion": True,
        "payload": {
            "name_key": "presets.system.guests",
            "mode": "surprise_me",
            "occasion": "guests",
            "temperature": "auto",
            "moods": ["indulgent"],
            "caffeine_pref": "regular",
            "cup_size": "mug",
            "dietary": [],
            "dynamic_occasion": True,
        },
    },
]

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
        """Open DB and create schema, run migrations if needed.

        Migration statements that fail with an idempotency error (duplicate
        column / already exists) are skipped silently — re-running a step is
        safe. UPDATE data-rewrite statements against a missing table are
        also skipped (vacuous: no rows to rewrite; a later CREATE gets the
        current schema). Any other migration error is logged with the
        failing statement and the schema_version stamp is withheld, so the
        migration is retried on the next start instead of being silently
        marked as applied.
        """
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

        migration_failed = False
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
            if current_version < 7:
                migrations.append((7, MIGRATE_V6_TO_V7))
            if current_version < 8:
                migrations.append((8, MIGRATE_V7_TO_V8))
            if current_version < 9:
                migrations.append((9, MIGRATE_V8_TO_V9))
            if current_version < 10:
                migrations.append((10, MIGRATE_V9_TO_V10))
            if current_version < 11:
                migrations.append((11, MIGRATE_V10_TO_V11))
            if current_version < 12:
                migrations.append((12, MIGRATE_V11_TO_V12))
            for target_version, sql in migrations:
                for stmt in sql.strip().split(";"):
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    try:
                        await self._db.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        msg = str(exc).lower()
                        if "duplicate column" in msg or "already exists" in msg:
                            continue  # idempotent re-run — column/table present
                        if (
                            "no such table" in msg
                            and stmt.upper().startswith("UPDATE")
                        ):
                            # Data-rewrite step (e.g. the v11 cup-size
                            # normalization, the v12 reasoning backfill)
                            # against a table this DB never created:
                            # vacuously complete — there are no rows to
                            # rewrite, and a later CREATE gets the current,
                            # legacy-free schema. Withholding the stamp here
                            # would retry forever for nothing. A missing
                            # COLUMN is deliberately NOT tolerated: the table
                            # exists but is shaped unexpectedly, which is a
                            # real schema problem worth retrying loudly
                            # (pinned by
                            # test_v10_to_v11_migration_stamp_withheld_on_failure).
                            _LOGGER.debug(
                                "Migration to v%d: skipping vacuous data "
                                "rewrite %r (%s)", target_version, stmt, exc,
                            )
                            continue
                        migration_failed = True
                        _LOGGER.warning(
                            "Sommelier DB migration to v%d failed on statement "
                            "%r: %s — schema_version stamp withheld, will retry "
                            "on next start",
                            target_version, stmt, exc,
                        )
                    except Exception as exc:  # noqa: BLE001 — keep startup alive
                        migration_failed = True
                        _LOGGER.warning(
                            "Sommelier DB migration to v%d failed on statement "
                            "%r: %s — schema_version stamp withheld, will retry "
                            "on next start",
                            target_version, stmt, exc,
                        )
                if migration_failed:
                    _LOGGER.warning(
                        "Sommelier DB migration to v%d is INCOMPLETE "
                        "(from v%d) — will retry on next start",
                        target_version, current_version,
                    )
                else:
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
        if migration_failed:
            _LOGGER.warning(
                "Sommelier DB schema_version stays at v%d after a failed "
                "migration statement — the migration will be retried on "
                "next start",
                current_version,
            )
        else:
            await self._db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        await self._db.commit()
        _LOGGER.info("Sommelier DB initialized (v%d) at %s", SCHEMA_VERSION, self._db_path)

        # Seed the four built-in system presets if they're not present yet.
        # Idempotent — re-running after a partial damage repairs gaps but
        # logs nothing when no rows were inserted.
        seeded = await self.async_seed_system_presets()
        if seeded > 0:
            _LOGGER.info("Seeded %d system presets", seeded)

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
        """Delete a coffee bean. Returns True if deleted.

        generation_sessions.hopper1_bean_id/hopper2_bean_id reference
        coffee_beans without an ON DELETE action, so with PRAGMA
        foreign_keys=ON a bare DELETE would raise IntegrityError the moment
        history references the bean. The referencing columns are NULLed
        first, in the same transaction as the DELETE, so the method never
        raises for this case and history rows survive (with the bean link
        detached, matching hoppers.bean_id ON DELETE SET NULL semantics).
        """
        async with self._lock:
            await self.db.execute(
                "UPDATE generation_sessions SET hopper1_bean_id = NULL "
                "WHERE hopper1_bean_id = ?",
                (bean_id,),
            )
            await self.db.execute(
                "UPDATE generation_sessions SET hopper2_bean_id = NULL "
                "WHERE hopper2_bean_id = ?",
                (bean_id,),
            )
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
        """Get list of currently in-stock milk types (out-of-stock filtered out)."""
        cursor = await self.db.execute(
            "SELECT milk_type FROM milk_config WHERE available = 1 ORDER BY milk_type"
        )
        rows = await cursor.fetchall()
        return [row["milk_type"] for row in rows]

    async def async_list_milk_full(self) -> list[dict[str, Any]]:
        """Get all configured milk types with their availability flag.

        Used by the Additives panel which surfaces the per-row toggle.
        Sommelier's chip picker keeps using `async_get_milk()` so
        out-of-stock milks stay hidden from the LLM context.
        """
        cursor = await self.db.execute(
            "SELECT milk_type, available FROM milk_config ORDER BY milk_type"
        )
        rows = await cursor.fetchall()
        return [
            {"milk_type": row["milk_type"], "available": bool(row["available"])}
            for row in rows
        ]

    async def async_set_milk(self, milk_types: list[str]) -> None:
        """Set the configured milk types — preserves `available` for surviving rows.

        Previously this method DELETE'd everything and re-INSERT'd with
        `available=1`, which resurrected milks the user had toggled off
        any time the bulk-save flow ran. The new behavior is closer to a
        UPSERT-with-prune: INSERT OR IGNORE for new rows (default
        available=1), DELETE rows not in the new list, leave the rest
        untouched.
        """
        # New rows default to available=1; pre-existing rows keep their flag.
        for mt in milk_types:
            await self.db.execute(
                "INSERT OR IGNORE INTO milk_config (milk_type, available) "
                "VALUES (?, 1)",
                (mt,),
            )
        # Prune rows not in the new list. Use a parameterised IN clause via a
        # placeholder list of the right length (empty list → DELETE everything).
        if milk_types:
            placeholders = ",".join("?" * len(milk_types))
            await self.db.execute(
                f"DELETE FROM milk_config WHERE milk_type NOT IN ({placeholders})",  # nosec B608
                tuple(milk_types),
            )
        else:
            await self.db.execute("DELETE FROM milk_config")
        await self.db.commit()

    async def async_set_milk_available(self, milk_type: str, available: bool) -> None:
        """Toggle a single milk's availability flag (upsert)."""
        flag = 1 if available else 0
        await self.db.execute(
            "INSERT INTO milk_config (milk_type, available) VALUES (?, ?) "
            "ON CONFLICT(milk_type) DO UPDATE SET available = excluded.available",
            (milk_type, flag),
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
        machine_profile: int | None = None,
    ) -> dict[str, Any]:
        """Create a generation session with recipes.

        ``machine_profile`` (1..n) tags the session to a specific machine
        hardware profile slot; ``None`` keeps the row shared.
        """
        session_id = _new_id()
        now = _now()
        await self.db.execute(
            """INSERT INTO generation_sessions
               (id, profile_id, mode, preference, mood, occasion, temperature,
                servings, hopper1_bean_id, hopper2_bean_id,
                milk_types, extras_context, weather_context, llm_agent,
                created_at, machine_profile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                machine_profile,
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
                   (id, session_id, name, description, reasoning, blend,
                    component1, component2, machine_phases, extras, steps,
                    cup_type, calories, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    recipe_id,
                    session_id,
                    recipe["name"],
                    recipe["description"],
                    recipe.get("reasoning", ""),
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
                # Why-this-pick rationale (0.89). Persisted since v10 so
                # history cards can render the "Why this recipe?" expander
                # after a reload, not only in the live generate reply.
                "reasoning": recipe.get("reasoning", ""),
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
            "machine_profile": machine_profile,
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
        """Get a single generated recipe by ID, enriched with rating + note."""
        cursor = await self.db.execute(
            """SELECT gr.*, r.rating AS rating, r.note AS note
                 FROM generated_recipes gr
                 LEFT JOIN recipe_ratings r
                   ON r.target_id = gr.id AND r.target_type = 'generated'
                WHERE gr.id = ?""",
            (recipe_id,),
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
        # Pre-v12 favourites have NULL reasoning — normalize to "".
        d["reasoning"] = d.get("reasoning") or ""
        d["brewed"] = bool(d["brewed"])
        # Pre-v10 rows have NULL reasoning — normalize to "" like the
        # live generate reply does.
        d["reasoning"] = d.get("reasoning") or ""
        return d

    async def async_list_history(
        self,
        limit: int = 20,
        offset: int = 0,
        *,
        machine_profile_filter: int | None = None,
    ) -> list[dict[str, Any]]:
        """List generation sessions with their recipes, newest first.

        Each session row carries ``machine_profile`` (int | None) — NULL
        means the session is shared across all machine profiles.

        When ``machine_profile_filter`` is supplied (1..n), the returned set
        is restricted to sessions whose generation_sessions.machine_profile
        equals the filter OR is NULL (shared rows always come through). The
        recipe-level LEFT JOIN against recipe_ratings is untouched — the
        filter applies only to the session row.

        Additive enrichment: each recipe row also carries ``favorite_id``
        (str | None) — the id of the earliest favorite created from that
        recipe (``favorites.source_recipe_id``), so the panel can render
        already-favorited history entries with a filled star. A scalar
        subquery (not a JOIN) keeps one row per recipe even if several
        favorites reference the same source.

        Each recipe also carries ``reasoning`` (v10 column; "" for rows
        written before the column existed) so history cards can show the
        "Why this recipe?" expander.
        """
        if machine_profile_filter is None:
            cursor = await self.db.execute(
                """SELECT * FROM generation_sessions
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        else:
            cursor = await self.db.execute(
                """SELECT * FROM generation_sessions
                   WHERE machine_profile = ? OR machine_profile IS NULL
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (machine_profile_filter, limit, offset),
            )
        sessions = []
        for sess_row in await cursor.fetchall():
            sess = _row_to_dict(sess_row)
            sess["milk_types"] = json.loads(sess["milk_types"]) if sess["milk_types"] else []
            recipe_cursor = await self.db.execute(
                """SELECT gr.*, rt.rating AS rating, rt.note AS note,
                          (SELECT f.id FROM favorites f
                            WHERE f.source_recipe_id = gr.id
                            ORDER BY f.created_at LIMIT 1) AS favorite_id
                     FROM generated_recipes gr
                     LEFT JOIN recipe_ratings rt
                       ON rt.target_id = gr.id AND rt.target_type = 'generated'
                    WHERE gr.session_id = ?
                    ORDER BY gr.created_at""",
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
                # Pre-v10 rows have NULL reasoning — normalize to "" like
                # the live generate reply does.
                r["reasoning"] = r.get("reasoning") or ""
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
            # Sweep ratings whose generated recipe no longer exists (the
            # cascade above removed it). Recipes in kept sessions are still
            # present, so their ratings survive — keep_favorited semantics
            # carry over for free. Favorite-type ratings are never touched.
            await self.db.execute(
                "DELETE FROM recipe_ratings WHERE target_type = 'generated' "
                "AND target_id NOT IN (SELECT id FROM generated_recipes)"
            )
            await self.db.commit()
        return removed

    # ── Favorites ─────────────────────────────────────────────────────

    async def async_list_favorites(
        self, *, machine_profile_filter: int | None = None
    ) -> list[dict[str, Any]]:
        """List all favorites, most brewed first.

        Each row is enriched with ``rating`` (1..5 or None) and ``note`` (str
        or None) via LEFT JOIN on ``recipe_ratings`` (target_type='favorite').
        Each row also carries ``machine_profile`` (int | None) from the
        favorites column.

        When ``machine_profile_filter`` is supplied (1..n), the returned set
        is restricted to rows whose favorites.machine_profile equals the
        filter OR is NULL (shared). The recipe_ratings LEFT JOIN remains
        in place — the filter touches only the favorites row.
        """
        base_sql = (
            "SELECT f.*, r.rating AS rating, r.note AS note "
            "FROM favorites f "
            "LEFT JOIN recipe_ratings r "
            "  ON r.target_id = f.id AND r.target_type = 'favorite'"
        )
        params: tuple[Any, ...] = ()
        if machine_profile_filter is not None:
            base_sql += (
                " WHERE f.machine_profile = ? OR f.machine_profile IS NULL"
            )
            params = (machine_profile_filter,)
        base_sql += " ORDER BY f.brew_count DESC, f.created_at DESC"
        cursor = await self.db.execute(base_sql, params)
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
                source_bean_id, brew_count, created_at, machine_profile,
                reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
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
                data.get("machine_profile"),
                data.get("reasoning") or "",
            ),
        )
        await self.db.commit()
        return await self.async_get_favorite(fav_id)  # type: ignore[return-value]

    async def async_find_favorite_by_source(
        self, recipe_id: str
    ) -> dict[str, Any] | None:
        """Return the earliest favorite created from a generated recipe.

        Duplicate-add guard for ``favorites/add``: looks up
        ``favorites.source_recipe_id == recipe_id`` (oldest ``created_at``
        wins) and returns the fully enriched row via
        :meth:`async_get_favorite`, or None when the recipe was never
        favorited.
        """
        cursor = await self.db.execute(
            "SELECT id FROM favorites WHERE source_recipe_id = ? "
            "ORDER BY created_at LIMIT 1",
            (recipe_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await self.async_get_favorite(row["id"])

    async def async_get_favorite(self, fav_id: str) -> dict[str, Any] | None:
        """Get a single favorite by ID, enriched with rating + note."""
        cursor = await self.db.execute(
            """SELECT f.*, r.rating AS rating, r.note AS note
                 FROM favorites f
                 LEFT JOIN recipe_ratings r
                   ON r.target_id = f.id AND r.target_type = 'favorite'
                WHERE f.id = ?""",
            (fav_id,),
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
        # Pre-v12 favourites have NULL reasoning — normalize to "".
        d["reasoning"] = d.get("reasoning") or ""
        return d

    async def async_remove_favorite(self, fav_id: str) -> bool:
        """Remove a favorite. Returns True if removed.

        Also deletes the favorite's ``recipe_ratings`` row (target_type
        'favorite') in the same transaction — recipe_ratings has no FK to
        its targets, so without this the rating would linger as an orphan.
        A 'generated' rating sharing the same UUID is left untouched.
        """
        async with self._lock:
            cursor = await self.db.execute(
                "DELETE FROM favorites WHERE id = ?", (fav_id,)
            )
            await self.db.execute(
                "DELETE FROM recipe_ratings "
                "WHERE target_id = ? AND target_type = 'favorite'",
                (fav_id,),
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

    # ── Sommelier Presets ─────────────────────────────────────────────

    async def async_list_presets(
        self, *, machine_profile_filter: int | None = None
    ) -> list[dict[str, Any]]:
        """List all sommelier presets, ordered case-insensitively by name.

        Each row dict is `{id, name, description, payload, is_system,
        dynamic_occasion, created_at, updated_at, machine_profile}` with
        ``payload`` parsed back from JSON to a dict, and the two int flags
        coerced to ``bool``. ``description``, ``updated_at`` and
        ``machine_profile`` may be ``None``.

        When ``machine_profile_filter`` is supplied (1..n), the returned set
        is restricted to rows whose ``machine_profile`` equals the filter
        OR is NULL (shared) — shared rows always come through.
        """
        base_sql = (
            "SELECT id, name, description, payload, is_system, "
            "dynamic_occasion, created_at, updated_at, machine_profile "
            "FROM sommelier_presets"
        )
        params: tuple[Any, ...] = ()
        if machine_profile_filter is not None:
            base_sql += " WHERE machine_profile = ? OR machine_profile IS NULL"
            params = (machine_profile_filter,)
        base_sql += " ORDER BY LOWER(name)"
        cursor = await self.db.execute(base_sql, params)
        result: list[dict[str, Any]] = []
        for row in await cursor.fetchall():
            d = _row_to_dict(row)
            d["payload"] = json.loads(d["payload"])
            d["is_system"] = bool(d["is_system"])
            d["dynamic_occasion"] = bool(d["dynamic_occasion"])
            result.append(d)
        return result

    async def async_seed_system_presets(self) -> int:
        """Insert the four built-in system presets if none exist yet.

        Returns the number of rows actually inserted — 0 when at least one
        ``is_system = 1`` row already exists (idempotent gate), or 4 on the
        first call against a clean DB. ``INSERT OR IGNORE`` keyed on the
        deterministic ``sys_*`` ids keeps a partial-damage rerun safe.
        """
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM sommelier_presets WHERE is_system = 1"
        )
        row = await cursor.fetchone()
        if row and int(row[0]) > 0:
            return 0

        inserted = 0
        now = _now()
        for preset in SYSTEM_PRESETS:
            cur = await self.db.execute(
                "INSERT OR IGNORE INTO sommelier_presets "
                "(id, name, description, payload, is_system, "
                "dynamic_occasion, created_at) "
                "VALUES (?, ?, ?, ?, 1, 1, ?)",
                (
                    preset["id"],
                    preset["name"],
                    preset["description"],
                    json.dumps(preset["payload"]),
                    now,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        await self.db.commit()
        return inserted

    async def async_add_preset(
        self,
        name: str,
        description: str | None,
        payload: dict,
        *,
        machine_profile: int | None = None,
    ) -> str:
        """Insert a new sommelier preset and return its generated id.

        ``machine_profile`` (1..n) binds the preset to a specific machine
        hardware profile slot; the default ``None`` keeps the row shared
        across every machine profile (existing pre-v9 behaviour).
        """
        preset_id = _new_id()
        now = _now()
        await self.db.execute(
            "INSERT INTO sommelier_presets "
            "(id, name, description, payload, created_at, machine_profile) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (preset_id, name, description, json.dumps(payload), now, machine_profile),
        )
        await self.db.commit()
        return preset_id

    async def async_update_preset(self, preset_id: str, **fields) -> bool:
        """Patch ``name``, ``description``, and/or ``payload`` on a preset.

        At least one of the three fields must be supplied — an empty patch
        raises ``ValueError("no_fields")``. System (built-in) rows refuse
        any update with ``ValueError("system_preset_readonly")``; that check
        fires before the empty-patch check so the readonly contract is
        unambiguous regardless of payload shape. ``payload`` is JSON-encoded
        when present. ``updated_at`` is bumped on every successful update.
        Returns True iff a row matched ``preset_id``.
        """
        cursor = await self.db.execute(
            "SELECT is_system FROM sommelier_presets WHERE id = ?",
            (preset_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None and int(existing["is_system"]) == 1:
            raise ValueError("system_preset_readonly")

        name = fields.get("name")
        description = fields.get("description")
        payload = fields.get("payload")
        if name is None and description is None and payload is None:
            raise ValueError("no_fields")

        set_parts: list[str] = []
        params: list[Any] = []
        if name is not None:
            set_parts.append("name = ?")
            params.append(name)
        if description is not None:
            set_parts.append("description = ?")
            params.append(description)
        if payload is not None:
            set_parts.append("payload = ?")
            params.append(json.dumps(payload))
        set_parts.append("updated_at = ?")
        params.append(_now())
        params.append(preset_id)

        cursor = await self.db.execute(
            f"UPDATE sommelier_presets SET {', '.join(set_parts)} WHERE id = ?",  # nosec B608
            params,
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def async_delete_preset(self, preset_id: str) -> bool:
        """Delete a sommelier preset. Returns True iff a row was removed.

        System (built-in) rows refuse deletion with
        ``ValueError("system_preset_readonly")``; the readonly check fires
        before the DELETE so the row is never even attempted to be removed.
        """
        cursor = await self.db.execute(
            "SELECT is_system FROM sommelier_presets WHERE id = ?",
            (preset_id,),
        )
        existing = await cursor.fetchone()
        if existing is not None and int(existing["is_system"]) == 1:
            raise ValueError("system_preset_readonly")

        cursor = await self.db.execute(
            "DELETE FROM sommelier_presets WHERE id = ?", (preset_id,)
        )
        await self.db.commit()
        return cursor.rowcount > 0

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

    async def async_get_pantry_extras(self) -> dict[str, list[str]]:
        """Catalogue-first pantry reader: syrups/toppings from panel tables, liqueurs/misc from user_extras."""
        result: dict[str, list[str]] = {
            "syrups": [],
            "toppings": [],
            "liqueurs": [],
            "misc": [],
        }
        # Syrups/toppings come from the panel-side catalogue (P4a). If the
        # tables don't exist yet (panel schema not bootstrapped), treat the
        # category as empty rather than blowing up.
        for table in ("syrups", "toppings"):
            try:
                cursor = await self.db.execute(
                    f"SELECT name FROM {table} WHERE available = 1 ORDER BY name"  # nosec B608
                )
                result[table] = [row["name"] for row in await cursor.fetchall()]
            except aiosqlite.OperationalError:
                result[table] = []
        # Liqueurs/misc still live in user_extras.
        cursor = await self.db.execute(
            "SELECT item FROM user_extras "
            "WHERE category = 'liqueurs' AND available = 1 ORDER BY item"
        )
        result["liqueurs"] = [row["item"] for row in await cursor.fetchall()]
        cursor = await self.db.execute(
            "SELECT item FROM user_extras "
            "WHERE category = 'misc' AND available = 1 ORDER BY item"
        )
        result["misc"] = [row["item"] for row in await cursor.fetchall()]
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

    async def async_set_extra_available(
        self, category: str, item: str, available: bool
    ) -> None:
        """Upsert the in-stock flag for a single user_extras (category, item)."""
        flag = 1 if available else 0
        cursor = await self.db.execute(
            "SELECT 1 FROM user_extras WHERE category = ? AND item = ?",
            (category, item),
        )
        row = await cursor.fetchone()
        if row is None:
            await self.db.execute(
                "INSERT INTO user_extras (category, item, available) VALUES (?, ?, ?)",
                (category, item, flag),
            )
        else:
            await self.db.execute(
                "UPDATE user_extras SET available = ? WHERE category = ? AND item = ?",
                (flag, category, item),
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
        """Update an existing profile. Returns the updated row, or None.

        ``data`` accepts either flat profile-row keys (name, cup_size,
        temperature_pref, dietary, caffeine_pref, machine_profile) or a
        nested ``preferences`` dict carrying those same keys — the shape
        the profiles/update WS handler sends. Nested preferences are
        flattened; an explicit flat key wins over its nested duplicate.
        """
        existing = await self.async_get_profile(profile_id)
        if existing is None:
            return None
        if "preferences" in data:
            nested = data.get("preferences") or {}
            data = {
                **nested,
                **{k: v for k, v in data.items() if k != "preferences"},
            }
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
        """Delete a profile. Returns True if deleted.

        generation_sessions.profile_id references sommelier_profiles without
        an ON DELETE action, so with PRAGMA foreign_keys=ON a bare DELETE
        would raise IntegrityError once the profile has generation history.
        The referencing column is NULLed first, in the same transaction as
        the DELETE, so the method never raises for this case and history
        rows survive with the profile link detached.
        """
        async with self._lock:
            await self.db.execute(
                "UPDATE generation_sessions SET profile_id = NULL "
                "WHERE profile_id = ?",
                (profile_id,),
            )
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
