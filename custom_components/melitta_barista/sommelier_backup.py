"""Sommelier configuration export / replace-import (bundle builder + applier).

The Sommelier keeps every piece of user-authored configuration — beans,
hoppers, milk, pantry extras, profiles, favourites, presets, producers,
syrups, toppings, flavour tags and the custom LLM prompt templates — in one
SQLite file. This module turns that into a self-describing JSON bundle and
back again, so a user can move their setup to another install, keep an
off-box copy, or roll back a bad import.

Design rules that are NOT negotiable (they are what makes this module safe):

1. **One connection.** Both the export read and the import write run on the
   *existing shared* ``SommelierDB`` connection (``db.db``), inside one
   explicit transaction each. There is no second ``aiosqlite`` connection and
   no ``file:…?mode=ro`` URI — that would need a module-level
   ``import aiosqlite`` (see rule 2), a hand-built URI whose escaping breaks on
   any config path containing ``?`` or ``#``, and it would race the shared
   writer for the write lock. Running on the shared connection also makes
   post-import coherence free: every later handler reads through the very
   connection the import committed on, so there is no cross-connection WAL
   visibility question to answer.

2. **Module-scope imports are stdlib + ``homeassistant.util.dt`` only.**
   ``__init__.py`` imports ``sommelier_api`` at module scope, which imports
   this module, so a module-level ``ImportError`` anywhere in that chain kills
   the *entire* integration import — it never reaches the guarded WS
   registration. ``aiosqlite`` is imported at module level by exactly one file
   (``sommelier_db.py``) precisely so the package still imports in an
   environment without it. This module never needs the ``aiosqlite`` module
   (it operates on a connection object handed to it) and imports
   ``SCHEMA_VERSION`` and the ``sommelier_api`` allowlists lazily, inside the
   functions that use them.

3. **The ``settings`` row ``schema_version`` is never read into a bundle and
   never written back.** It is the migration runner's stamp; a foreign DB's
   value would make future migrations skip or re-run incorrectly. ``settings``
   is filtered through ``VALID_SETTING_KEYS`` on the way out *and* on the way
   in, and ``schema_version`` is not in that allowlist.

4. **A future migration that rewrites data must also be added to the import
   normalisation step in :func:`async_apply_import_on_conn`.** The migration
   runner is deliberately not replayed over a bundle — column drift is absorbed
   by intersecting the bundle's columns with the live table's columns — so a
   *data* rewrite (like v11's cup-size normalisation, re-applied here through
   ``CUP_SIZE_ALIASES``) has to be repeated explicitly or an old bundle
   re-introduces the token the migration was written to eliminate. This is the
   only coupling between this module and the migration story.

5. **Every writer on the shared connection must hold ``db._lock``.** Running
   on one connection is what rule 1 buys, and the price is that a *foreign*
   ``commit()`` ends OUR transaction: SQLite has no nesting, so an unlocked
   ``db._db.execute(...) + commit()`` landing at any ``await`` inside the
   import would commit it half-applied, after which the rollback below can
   only undo the tail — a permanently half-replaced configuration reported to
   the user as a failed import. ``panel_api``'s raw ``db._db`` writes and its
   ``_ensure_panel_schema`` bootstrap (reached from *every* panel request,
   read-only ones included, and ending in a ``commit()``) therefore take that
   lock; see ``panel_api._write_lock``. :data:`_BACKUP_LOCK` is a different
   guarantee — it makes export and import mutually exclusive with *each
   other*, and a caller that finds it held gets :class:`BackupBusyError`
   immediately rather than queueing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger("melitta_barista")


# ── Envelope ──────────────────────────────────────────────────────────

EXPORT_FORMAT = "melitta_barista.sommelier_export"
EXPORT_FORMAT_VERSION = 1


# ── Caps ──────────────────────────────────────────────────────────────

#: The panel refuses to send a file bigger than this over WS. HA builds its
#: `web.WebSocketResponse` without a `max_msg_size` override, so aiohttp's
#: 4 MiB default applies to INBOUND frames — and an oversized inbound frame
#: kills the connection instead of returning an error. Stay under it.
MAX_INLINE_IMPORT_BYTES = 3 * 1024 * 1024

#: Total rows (config + history) a single import may carry.
MAX_IMPORT_ROWS = 200_000

#: Server-side snapshot files are read in an executor and parsed; a 40 MB JSON
#: expands into hundreds of MB of Python objects on a 2 GB Pi.
MAX_SNAPSHOT_FILE_BYTES = 64 * 1024 * 1024

#: How many automatic `pre-import-*` snapshots to keep. Files the user dropped
#: in or renamed by hand are never pruned — they go through `snapshots/delete`.
SNAPSHOT_RETENTION = 5

#: Folder under `<config>` holding snapshot bundles. Also the documented
#: escape hatch for imports too large for the inbound WS frame.
SNAPSHOT_DIR_NAME = "melitta_barista_sommelier_backups"

#: Prefix identifying an automatically created pre-import snapshot.
SNAPSHOT_AUTO_PREFIX = "pre-import-"

# ── Table inventory ───────────────────────────────────────────────────
#
# 18 tables live in this DB: 13 from `sommelier_db.SCHEMA_SQL` and 5 created
# lazily by `panel_api._ensure_panel_schema` (producers, syrups, toppings,
# flavor_tags, panel_prompts) entirely outside the schema_version machinery.
# An export that walked only SCHEMA_SQL would silently lose the whole
# Additives/Producers catalogue and every custom prompt template.
#
# `machine_capabilities` is deliberately absent from both lists: its PK is
# *this* install's config-entry UUID and it is re-probed on the next connect.

_CONFIG_TABLES: tuple[str, ...] = (
    "coffee_beans",
    "hoppers",
    "milk_config",
    "user_extras",
    "user_preferences",
    "sommelier_profiles",
    "favorites",
    "settings",
    "recipe_ratings",
    "sommelier_presets",
    # panel-owned, unversioned, but pure user data
    "producers",
    "syrups",
    "toppings",
    "flavor_tags",
    "panel_prompts",
)

_HISTORY_TABLES: tuple[str, ...] = (
    "generation_sessions",
    "generated_recipes",
    "recipe_ratings",
)

#: Primary-key columns, in DDL order — the export's `ORDER BY`, with no
#: carve-out: `flavor_tags.name` and `panel_prompts.slot` really are the PKs,
#: and the two composite keys order by their columns in DDL order. Two exports
#: of an unchanged DB then differ only in `exported_at`.
_TABLE_PKS: dict[str, tuple[str, ...]] = {
    "coffee_beans": ("id",),
    "hoppers": ("hopper_id",),
    "milk_config": ("milk_type",),
    "user_extras": ("category", "item"),
    "user_preferences": ("key",),
    "sommelier_profiles": ("id",),
    "favorites": ("id",),
    "settings": ("key",),
    "recipe_ratings": ("target_id", "target_type"),
    "sommelier_presets": ("id",),
    "producers": ("id",),
    "syrups": ("id",),
    "toppings": ("id",),
    "flavor_tags": ("name",),
    "panel_prompts": ("slot",),
    "generation_sessions": ("id",),
    "generated_recipes": ("id",),
}

#: Per-section row filters. `recipe_ratings` is split across the two sections:
#: favourite ratings are configuration, generated ratings are history.
#: System presets are re-derivable and are re-seeded after every import.
_EXPORT_FILTERS: dict[tuple[str, str], str] = {
    ("tables", "recipe_ratings"): "target_type = 'favorite'",
    ("tables", "sommelier_presets"): "is_system = 0",
    ("history", "recipe_ratings"): "target_type = 'generated'",
}

#: Insert order — reverse dependency order, so every FK target exists first.
_IMPORT_INSERT_ORDER: tuple[str, ...] = (
    "producers",
    "coffee_beans",
    "sommelier_profiles",
    "hoppers",
    "milk_config",
    "user_extras",
    "user_preferences",
    "favorites",
    "settings",
    "sommelier_presets",
    "syrups",
    "toppings",
    "flavor_tags",
    "panel_prompts",
    "generation_sessions",
    "generated_recipes",
    "recipe_ratings",
)

#: Tables an import is allowed to write at all. Anything else in a bundle
#: (a hand-edited `machine_capabilities`, a table from a future version) is
#: ignored and counted into `skipped`.
_KNOWN_TABLES: frozenset[str] = frozenset(_IMPORT_INSERT_ORDER)

#: The two installation-specific cells. Both are hoisted out of the exported
#: row arrays into `bundle["install_specific"]` so they never appear twice and
#: can be applied — or deliberately not applied — as a unit.
_INSTALL_SPECIFIC: dict[str, tuple[str, str]] = {
    # logical name -> (table, primary-key value)
    "llm_agent_id": ("settings", "llm_agent_id"),
    "weather_entity": ("user_preferences", "weather_entity"),
}

#: `llm_agent_id` values that are valid but are NOT entity ids, and must
#: therefore skip the entity-existence check: the sentinel prepended by the
#: agent picker meaning "HA's built-in agent", and the empty string, which the
#: agent resolver treats as "HA default". Without this exemption both would be
#: silently discarded on every import.
INSTALL_SPECIFIC_AGENT_SENTINELS: frozenset[str] = frozenset({"homeassistant", ""})

#: Mutual exclusion between export and import. Deliberately non-queueing: a
#: caller that finds it held is told the DB is busy and can retry.
_BACKUP_LOCK = asyncio.Lock()


# ── Errors ────────────────────────────────────────────────────────────


class BackupError(Exception):
    """Base class for every export/import failure with a WS error code."""


class BackupBusyError(BackupError):
    """Another export or import is already running on this DB."""


class UnsupportedFormatError(BackupError):
    """The payload is not a Sommelier export, or is from a newer format."""


class UnsupportedDbSchemaError(BackupError):
    """The bundle was produced by a newer Sommelier database schema."""


class InvalidExportError(BackupError):
    """The bundle is structurally malformed (bad section, non-scalar cell)."""


class ImportTooLargeError(BackupError):
    """The bundle carries more rows than a single import may apply."""


class InvalidSnapshotNameError(BackupError):
    """A snapshot name failed the charset or path-containment check."""


class SnapshotNotFoundError(BackupError):
    """No snapshot file with that name exists in the backups folder."""


class SnapshotFileTooLargeError(BackupError):
    """The snapshot file on disk is bigger than MAX_SNAPSHOT_FILE_BYTES."""


# ── Small helpers ─────────────────────────────────────────────────────


async def _live_columns(conn: Any, table: str) -> list[str]:
    """Return the column names a table actually has right now, or []."""
    # `table` comes from a module constant, never from caller input.
    cursor = await conn.execute(f"PRAGMA table_info({table})")  # nosec B608
    return [row[1] for row in await cursor.fetchall()]


def _is_scalar(value: Any) -> bool:
    """True for the JSON scalars a bundle cell is allowed to hold."""
    return value is None or isinstance(value, (str, int, float, bool))


def _coerce_cell(value: Any) -> Any:
    """Coerce a bundle cell for SQLite: JSON booleans become 1/0."""
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def count_bundle_rows(bundle: dict[str, Any]) -> int:
    """Total rows a bundle carries across both of its sections.

    The same number :func:`validate_bundle` measures against
    :data:`MAX_IMPORT_ROWS`, exposed so the snapshot writer can ask the
    question *before* writing a rollback artifact its own restore would refuse.
    """
    total = 0
    for section in ("tables", "history"):
        for rows in (bundle.get(section) or {}).values():
            if isinstance(rows, list):
                total += len(rows)
    return total


def drop_bundle_history(bundle: dict[str, Any]) -> dict[str, Any]:
    """Strip the history section out of a bundle, in place, and return it.

    Used only by the snapshot writer, on the one install shape where a full
    snapshot would be too big to restore: a configuration-only rollback point
    beats no rollback point at all, and an import clears the local history
    either way, so nothing survives the alternative.
    """
    bundle.pop("history", None)
    bundle["include_history"] = False
    counts = bundle.get("counts")
    if isinstance(counts, dict):
        for key in [k for k in counts if k.startswith("history.")]:
            counts.pop(key)
    return bundle


def _bump(counter: dict[str, int], key: str, amount: int = 1) -> None:
    """Add ``amount`` to ``counter[key]``, creating the entry if needed."""
    if amount:
        counter[key] = counter.get(key, 0) + amount


# ── Export ────────────────────────────────────────────────────────────


async def async_build_export_on_conn(
    conn: Any,
    *,
    include_history: bool = False,
    integration_version: str = "unknown",
) -> dict[str, Any]:
    """Build the export bundle, assuming the caller owns the transaction.

    This is the testability seam: :func:`async_build_export` wraps it in the
    module lock, the DB lock and a ``BEGIN DEFERRED``/``COMMIT`` pair, while a
    snapshot writer or a test can drive it directly on a connection it already
    controls. Tables that do not exist on this DB (a legacy install whose panel
    tables were never created) export as an empty array so the envelope shape
    stays stable.
    """
    from .sommelier_db import SCHEMA_VERSION  # noqa: PLC0415 — see rule 2

    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _CONFIG_TABLES:
        tables[table] = await _read_table(conn, "tables", table)

    history: dict[str, list[dict[str, Any]]] | None = None
    if include_history:
        history = {}
        for table in _HISTORY_TABLES:
            history[table] = await _read_table(conn, "history", table)

    install_specific = _hoist_install_specific(tables)

    counts = {name: len(rows) for name, rows in tables.items()}
    if history is not None:
        for name, rows in history.items():
            counts[f"history.{name}"] = len(rows)

    bundle: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "integration_version": integration_version,
        "db_schema_version": SCHEMA_VERSION,
        "exported_at": dt_util.utcnow().isoformat(),
        "include_history": bool(include_history),
        "counts": counts,
        "install_specific": install_specific,
        "tables": tables,
    }
    if history is not None:
        bundle["history"] = history
    return bundle


async def async_build_export(
    db: Any,
    *,
    include_history: bool = False,
    integration_version: str = "unknown",
) -> dict[str, Any]:
    """Build an export bundle from the shared ``SommelierDB`` connection.

    Runs every read inside one ``BEGIN DEFERRED`` transaction on the shared
    connection, holding both the module backup lock and the DB's own lock (see
    the module docstring for the residual limit of the latter). Raises
    :class:`BackupBusyError` immediately if another backup operation is in
    flight — this never queues.
    """
    if _BACKUP_LOCK.locked():
        raise BackupBusyError("Another backup operation is already running")
    async with _BACKUP_LOCK:
        # `db._lock` is the same lock SommelierDB's own destructive methods
        # take; reaching for it here is deliberate, not an accident.
        async with db._lock:
            conn = db.db
            # Close any dangling implicit transaction sqlite3 opened for us,
            # otherwise BEGIN raises "cannot start a transaction within a
            # transaction".
            await conn.commit()
            await conn.execute("BEGIN DEFERRED")
            try:
                bundle = await async_build_export_on_conn(
                    conn,
                    include_history=include_history,
                    integration_version=integration_version,
                )
            except BaseException:
                await _safe_rollback(conn)
                raise
            await conn.execute("COMMIT")
    return bundle


async def _read_table(
    conn: Any, section: str, table: str
) -> list[dict[str, Any]]:
    """Read one table into a list of flat row dicts, ordered by its PK."""
    live_cols = await _live_columns(conn, table)
    if not live_cols:
        return []
    cols = sorted(live_cols)
    col_sql = ", ".join(f'"{col}"' for col in cols)
    order_cols = [c for c in _TABLE_PKS.get(table, ()) if c in live_cols]
    # Every interpolated fragment below comes from a module constant or from
    # PRAGMA output — never from caller input.
    sql = f'SELECT {col_sql} FROM "{table}"'  # nosec B608
    where = _EXPORT_FILTERS.get((section, table))
    if where:
        sql += f" WHERE {where}"  # nosec B608
    if order_cols:
        sql += " ORDER BY " + ", ".join(f'"{c}"' for c in order_cols)  # nosec B608
    cursor = await conn.execute(sql)
    rows = [dict(zip(cols, row)) for row in await cursor.fetchall()]
    return _filter_exported_rows(table, rows)


def _filter_exported_rows(
    table: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply the key allowlists to the two key/value tables.

    ``settings`` is filtered to ``VALID_SETTING_KEYS``, which is what keeps the
    migration runner's ``schema_version`` stamp out of every bundle;
    ``user_preferences`` is filtered to ``VALID_PREFERENCE_KEYS``.
    """
    if table == "settings":
        from .sommelier_api import VALID_SETTING_KEYS  # noqa: PLC0415

        allowed = set(VALID_SETTING_KEYS)
        return [r for r in rows if r.get("key") in allowed]
    if table == "user_preferences":
        from .sommelier_api import VALID_PREFERENCE_KEYS  # noqa: PLC0415

        allowed = set(VALID_PREFERENCE_KEYS)
        return [r for r in rows if r.get("key") in allowed]
    return rows


def _hoist_install_specific(
    tables: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Move the install-specific cells out of the row arrays into their own map.

    A value must never appear twice in a bundle: once hoisted, the whole
    key/value row is removed from ``tables[<table>]`` so an import that leaves
    ``include_install_specific`` off cannot re-introduce it through the ordinary
    row path.
    """
    hoisted: dict[str, Any] = {}
    for logical, (table, key) in _INSTALL_SPECIFIC.items():
        rows = tables.get(table)
        if not rows:
            continue
        kept: list[dict[str, Any]] = []
        for row in rows:
            if row.get("key") == key:
                hoisted[logical] = row.get("value")
            else:
                kept.append(row)
        tables[table] = kept
    return hoisted


# ── Validation ────────────────────────────────────────────────────────


def validate_bundle(bundle: Any) -> None:
    """Validate an incoming bundle's envelope, shape and size.

    Raises the specific :class:`BackupError` subclass the WS layer maps to an
    error code. Runs entirely before the import transaction opens, so a
    malformed payload never touches the DB — which is why the "would this
    replace anything at all?" check lives here and not in the applier: by the
    time the applier runs, the delete phase has already happened.
    """
    from .sommelier_db import SCHEMA_VERSION  # noqa: PLC0415 — see rule 2

    if not isinstance(bundle, dict):
        raise InvalidExportError("Export payload is not an object")

    if bundle.get("format") != EXPORT_FORMAT:
        raise UnsupportedFormatError(
            "This file is not a Melitta Barista Sommelier export"
        )

    version = bundle.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise UnsupportedFormatError("Export format version is missing")
    if version > EXPORT_FORMAT_VERSION:
        raise UnsupportedFormatError(
            f"This backup uses export format v{version}; "
            f"update the integration first (this one reads "
            f"v{EXPORT_FORMAT_VERSION})"
        )

    db_version = bundle.get("db_schema_version")
    if isinstance(db_version, int) and not isinstance(db_version, bool):
        if db_version > SCHEMA_VERSION:
            raise UnsupportedDbSchemaError(
                f"This backup was made with a newer Sommelier database "
                f"(v{db_version}); update the integration first "
                f"(this one is v{SCHEMA_VERSION})"
            )

    # `tables` is mandatory, and has to name at least one table this import
    # knows how to write. An import is a REPLACE: the delete phase runs before
    # any section is consulted, so an envelope-only payload — the shape a
    # hand-edited or half-copied file degrades to — would wipe every
    # replaceable table, insert nothing and report success. `history` stays
    # optional: a config-only bundle is a first-class export.
    tables = bundle.get("tables")
    if not isinstance(tables, dict):
        raise InvalidExportError(
            "This backup has no 'tables' section; importing it would delete "
            "the current configuration and write nothing back"
        )
    if not any(name in _KNOWN_TABLES for name in tables):
        raise InvalidExportError(
            "This backup's 'tables' section names no table this version "
            "imports; importing it would delete the current configuration "
            "and write nothing back"
        )

    install_specific = bundle.get("install_specific")
    if install_specific is not None:
        if not isinstance(install_specific, dict):
            raise InvalidExportError(
                "Section 'install_specific' is not an object"
            )
        for logical, value in install_specific.items():
            if not isinstance(logical, str) or not _is_scalar(value):
                raise InvalidExportError(
                    f"Install-specific value {logical!r} is not a JSON scalar"
                )

    for section in ("tables", "history"):
        rows_by_table = bundle.get(section)
        if rows_by_table is None:
            continue
        if not isinstance(rows_by_table, dict):
            raise InvalidExportError(f"Section {section!r} is not an object")
        for table, rows in rows_by_table.items():
            if not isinstance(rows, list):
                raise InvalidExportError(f"Table {table!r} is not a row array")

    total_rows = count_bundle_rows(bundle)
    if total_rows > MAX_IMPORT_ROWS:
        raise ImportTooLargeError(
            f"This backup carries {total_rows} rows; the limit is "
            f"{MAX_IMPORT_ROWS}"
        )

    for section in ("tables", "history"):
        rows_by_table = bundle.get(section) or {}
        for table, rows in rows_by_table.items():
            for row in rows:
                if not isinstance(row, dict):
                    raise InvalidExportError(
                        f"Table {table!r} contains a non-object row"
                    )
                for column, value in row.items():
                    if not isinstance(column, str) or not _is_scalar(value):
                        raise InvalidExportError(
                            f"Table {table!r} column {column!r} is not a "
                            f"JSON scalar"
                        )


# ── Import ────────────────────────────────────────────────────────────


async def async_apply_import(
    db: Any,
    bundle: dict[str, Any],
    *,
    include_history: bool = False,
    include_install_specific: bool = True,
    install_specific: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace the Sommelier configuration from a bundle, all or nothing.

    Validates first, then runs delete + insert + normalisation inside one
    ``BEGIN IMMEDIATE`` transaction on the shared connection with
    ``PRAGMA defer_foreign_keys`` set, rolling back on any error. After COMMIT
    the four built-in system presets are re-seeded — the seeder is gated on
    their absence and otherwise runs only at the end of ``async_setup``, so
    without this call they would be missing until the next HA restart.

    ``install_specific`` is the handler's post-entity-check override for the
    bundle's own values; it is consulted only when ``include_install_specific``
    is true. The receiving install's *own* ``llm_agent_id`` and
    ``weather_entity`` are read before the delete phase either way and written
    back for any of the two the bundle (or the override) has no usable value
    for — the flag exists to protect the local install, and so does the
    fallback: neither may erase.

    Raises :class:`BackupBusyError` if another backup operation is in flight.
    """
    validate_bundle(bundle)

    if _BACKUP_LOCK.locked():
        raise BackupBusyError("Another backup operation is already running")
    async with _BACKUP_LOCK:
        async with db._lock:
            conn = db.db
            await conn.commit()
            try:
                await conn.execute("BEGIN IMMEDIATE")
            except Exception as exc:  # noqa: BLE001 — classify and re-raise
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    raise BackupBusyError(
                        "The Sommelier database is busy; try again"
                    ) from exc
                raise
            try:
                # `PRAGMA foreign_keys` is a no-op inside a transaction;
                # `defer_foreign_keys` is not, and reverts at COMMIT.
                await conn.execute("PRAGMA defer_foreign_keys = ON")
                summary = await async_apply_import_on_conn(
                    conn,
                    bundle,
                    include_history=include_history,
                    include_install_specific=include_install_specific,
                    install_specific=install_specific,
                )
                await conn.execute("COMMIT")
            except BaseException:
                await _safe_rollback(conn)
                raise

    await db.async_seed_system_presets()
    return summary


async def async_apply_import_on_conn(
    conn: Any,
    bundle: dict[str, Any],
    *,
    include_history: bool = False,
    include_install_specific: bool = True,
    install_specific: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an import, assuming the caller owns the transaction.

    Returns a summary of ``imported`` / ``skipped`` row counts, the
    ``dropped_columns`` the live schema did not have, and ``history_cleared``
    (the number of ``generation_sessions`` rows the replace removed; their
    recipes cascade).
    """
    from .sommelier_api import (  # noqa: PLC0415 — see rule 2
        CUP_SIZE_ALIASES,
        VALID_PREFERENCE_KEYS,
        VALID_SETTING_KEYS,
    )

    imported: dict[str, int] = {}
    skipped: dict[str, int] = {}
    dropped_columns: dict[str, list[str]] = {}

    sections = _collect_sections(bundle, include_history, skipped)

    # Read the local install-specific values BEFORE anything is deleted —
    # unconditionally, not just when the flag is off. They are the fallback
    # for every logical name the bundle does not supply a usable value for
    # (absent from the bundle, or rejected by the handler's entity check), and
    # without them a rejected value would leave the install with *nothing*
    # where it had a working LLM agent a moment earlier.
    preserved: dict[str, Any] = {}
    for logical, (table, key) in _INSTALL_SPECIFIC.items():
        if await _table_exists(conn, table):
            preserved[logical] = await _read_kv(conn, table, key)

    history_cleared = await _delete_phase(conn, VALID_SETTING_KEYS)

    for table in _IMPORT_INSERT_ORDER:
        rows = sections.get(table)
        if not rows:
            continue
        rows, dropped_rows = _normalise_rows(
            table,
            rows,
            cup_size_aliases=CUP_SIZE_ALIASES,
            valid_setting_keys=VALID_SETTING_KEYS,
            valid_preference_keys=VALID_PREFERENCE_KEYS,
        )
        _bump(skipped, table, dropped_rows)
        inserted, dropped_cols = await _insert_rows(conn, table, rows)
        _bump(imported, table, inserted)
        if dropped_cols:
            dropped_columns[table] = dropped_cols

    await _reassert_invariants(conn, skipped)
    await _apply_install_specific(
        conn,
        include_install_specific=include_install_specific,
        from_bundle=(
            install_specific
            if install_specific is not None
            else (bundle.get("install_specific") or {})
        ),
        preserved=preserved,
    )

    return {
        "imported": imported,
        "skipped": skipped,
        "dropped_columns": dropped_columns,
        "history_cleared": history_cleared,
    }


def _collect_sections(
    bundle: dict[str, Any],
    include_history: bool,
    skipped: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    """Flatten the bundle's sections into one table -> rows map.

    ``recipe_ratings`` is the one table that appears in both sections, so the
    two row arrays are concatenated when history is included. Unknown table
    keys — a hand-added ``machine_capabilities``, a table from a future
    version — are ignored and counted into ``skipped`` rather than failing the
    import, so a bundle stays forward-compatible.
    """
    collected: dict[str, list[dict[str, Any]]] = {}
    section_names = ["tables"] + (["history"] if include_history else [])
    for section in section_names:
        for table, rows in (bundle.get(section) or {}).items():
            if table not in _KNOWN_TABLES:
                _bump(skipped, table, len(rows))
                continue
            collected.setdefault(table, []).extend(rows)
    # A history section that is present but deliberately not applied is not an
    # error; count it so the UI can say what was left behind.
    if not include_history:
        for table, rows in (bundle.get("history") or {}).items():
            _bump(skipped, f"history.{table}", len(rows))
    return collected


async def _read_kv(conn: Any, table: str, key: str) -> Any:
    """Read one value out of a key/value table, or None when absent."""
    # `table` comes from `_INSTALL_SPECIFIC`, a module constant.
    cursor = await conn.execute(
        f'SELECT value FROM "{table}" WHERE key = ?',  # nosec B608
        (key,),
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]


async def _table_exists(conn: Any, table: str) -> bool:
    """True when the table is present on this DB right now."""
    return bool(await _live_columns(conn, table))


async def _delete_phase(conn: Any, valid_setting_keys: list[str]) -> int:
    """Clear every replaceable table, in dependency-safe order.

    Returns the number of ``generation_sessions`` rows removed. History is
    cleared unconditionally — a full-replace import must not leave a foreign
    config sitting on top of the previous install's generation history — and
    ``include_history`` only decides whether the bundle's history is written
    back afterwards.

    ``hoppers`` rows 1 and 2 are structural: they are seeded at every DB setup
    and referenced by the whole UI, so their ``bean_id`` is NULLed rather than
    the rows being deleted.
    """
    history_cleared = 0
    if await _table_exists(conn, "generation_sessions"):
        cursor = await conn.execute("SELECT COUNT(*) FROM generation_sessions")
        row = await cursor.fetchone()
        history_cleared = int(row[0]) if row else 0

    statements: list[tuple[str, str, tuple[Any, ...]]] = [
        ("recipe_ratings", "DELETE FROM recipe_ratings", ()),
        # Cascades generated_recipes.
        ("generation_sessions", "DELETE FROM generation_sessions", ()),
        ("favorites", "DELETE FROM favorites", ()),
        ("hoppers", "UPDATE hoppers SET bean_id = NULL", ()),
        ("coffee_beans", "DELETE FROM coffee_beans", ()),
        ("sommelier_profiles", "DELETE FROM sommelier_profiles", ()),
        ("syrups", "DELETE FROM syrups", ()),
        ("toppings", "DELETE FROM toppings", ()),
        ("producers", "DELETE FROM producers", ()),
        ("milk_config", "DELETE FROM milk_config", ()),
        ("user_extras", "DELETE FROM user_extras", ()),
        ("user_preferences", "DELETE FROM user_preferences", ()),
        ("flavor_tags", "DELETE FROM flavor_tags", ()),
        ("panel_prompts", "DELETE FROM panel_prompts", ()),
        (
            "sommelier_presets",
            "DELETE FROM sommelier_presets WHERE is_system = 0",
            (),
        ),
    ]
    for table, sql, params in statements:
        if await _table_exists(conn, table):
            await conn.execute(sql, params)

    # `settings` is cleared key by key through the allowlist so the migration
    # runner's `schema_version` stamp is never touched.
    if await _table_exists(conn, "settings"):
        placeholders = ", ".join("?" for _ in valid_setting_keys)
        await conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",  # nosec B608
            tuple(valid_setting_keys),
        )
    return history_cleared


def _normalise_rows(
    table: str,
    rows: list[dict[str, Any]],
    *,
    cup_size_aliases: dict[str, str],
    valid_setting_keys: list[str],
    valid_preference_keys: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Filter and rewrite a table's rows before they are inserted.

    Covers the four rules that raw SQL would otherwise bypass: the two
    key/value allowlists (which also keep the install-specific cells out of the
    ordinary row path), the Python-only read-only guard on system presets, and
    the v11 cup-size data rewrite — see module docstring rule 4 for why a data
    migration has to be repeated here.

    Returns the kept rows and the number dropped.
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        row = dict(row)
        if table == "settings":
            key = row.get("key")
            if key not in valid_setting_keys or key == "llm_agent_id":
                dropped += 1
                continue
        elif table == "user_preferences":
            key = row.get("key")
            if key not in valid_preference_keys or key == "weather_entity":
                dropped += 1
                continue
            if key == "default_cup_size":
                value = row.get("value")
                if isinstance(value, str) and value in cup_size_aliases:
                    row["value"] = cup_size_aliases[value]
        elif table == "sommelier_presets":
            if _truthy(row.get("is_system")):
                dropped += 1
                continue
        elif table == "sommelier_profiles":
            value = row.get("cup_size")
            if isinstance(value, str) and value in cup_size_aliases:
                row["cup_size"] = cup_size_aliases[value]
        kept.append(row)
    return kept, dropped


def _truthy(value: Any) -> bool:
    """Interpret a bundle cell as a SQLite boolean (1/0, "1"/"0", true/false)."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


async def _insert_rows(
    conn: Any, table: str, rows: list[dict[str, Any]]
) -> tuple[int, list[str]]:
    """Insert rows, intersecting their columns with the live table's columns.

    Column drift survives in both directions: a bundle column the live schema
    lacks is dropped and reported, a live column the bundle lacks takes its DDL
    default. ``producers`` / ``syrups`` / ``toppings`` use
    ``INTEGER PRIMARY KEY AUTOINCREMENT`` rather than the uuid4 TEXT keys the
    rest of the DB uses, so their ``id`` is inserted explicitly — otherwise
    SQLite re-numbers them and every ``producer_id`` link is silently re-wired.
    """
    live_cols = await _live_columns(conn, table)
    if not live_cols:
        return 0, []
    live = set(live_cols)
    # `hoppers` rows 1/2 are never deleted (see `_delete_phase`), so the import
    # replaces them in place instead of colliding on their primary key.
    verb = "INSERT OR REPLACE INTO" if table == "hoppers" else "INSERT INTO"

    inserted = 0
    dropped: set[str] = set()
    for row in rows:
        use = [c for c in sorted(row) if c in live]
        dropped.update(c for c in row if c not in live)
        if not use:
            continue
        col_sql = ", ".join(f'"{c}"' for c in use)
        placeholders = ", ".join("?" for _ in use)
        # Table and column names come from module constants and PRAGMA output;
        # every value is bound as a parameter.
        await conn.execute(
            f'{verb} "{table}" ({col_sql}) '  # nosec B608
            f"VALUES ({placeholders})",
            tuple(_coerce_cell(row[c]) for c in use),
        )
        inserted += 1
    return inserted, sorted(dropped)


async def _reassert_invariants(conn: Any, skipped: dict[str, int]) -> None:
    """Re-assert the invariants the DB itself does not enforce.

    Three of them: the two structural hopper rows exist and never point at a
    bean that was not imported (there is an ``ON DELETE SET NULL`` FK but
    nothing that repairs a dangling id an import wrote); ratings never outlive
    their target (``recipe_ratings`` has no FK at all, so an orphan would be
    permanent); and at most one profile is active (the singleton is enforced
    procedurally in ``async_set_active_profile``, not by a constraint).
    """
    now = dt_util.utcnow().isoformat()

    if await _table_exists(conn, "hoppers"):
        for hopper_id in (1, 2):
            await conn.execute(
                "INSERT OR IGNORE INTO hoppers (hopper_id, bean_id, assigned_at) "
                "VALUES (?, NULL, ?)",
                (hopper_id, now),
            )
        if await _table_exists(conn, "coffee_beans"):
            await conn.execute(
                "UPDATE hoppers SET bean_id = NULL WHERE bean_id IS NOT NULL "
                "AND bean_id NOT IN (SELECT id FROM coffee_beans)"
            )

    if (
        await _table_exists(conn, "recipe_ratings")
        and await _table_exists(conn, "favorites")
        and await _table_exists(conn, "generated_recipes")
    ):
        cursor = await conn.execute(
            "DELETE FROM recipe_ratings WHERE "
            "(target_type = 'favorite' "
            " AND target_id NOT IN (SELECT id FROM favorites)) "
            "OR (target_type = 'generated' "
            " AND target_id NOT IN (SELECT id FROM generated_recipes))"
        )
        _bump(skipped, "recipe_ratings", max(cursor.rowcount or 0, 0))

    if await _table_exists(conn, "sommelier_profiles"):
        cursor = await conn.execute("SELECT COUNT(*) FROM sommelier_profiles")
        row = await cursor.fetchone()
        total = int(row[0]) if row else 0
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM sommelier_profiles WHERE is_active = 1"
        )
        row = await cursor.fetchone()
        active = int(row[0]) if row else 0
        if total > 0 and active != 1:
            await conn.execute("UPDATE sommelier_profiles SET is_active = 0")
            await conn.execute(
                "UPDATE sommelier_profiles SET is_active = 1 WHERE id = "
                "(SELECT id FROM sommelier_profiles "
                " ORDER BY created_at ASC, id ASC LIMIT 1)"
            )


async def _apply_install_specific(
    conn: Any,
    *,
    include_install_specific: bool,
    from_bundle: dict[str, Any],
    preserved: dict[str, Any],
) -> None:
    """Write the two install-specific cells, falling back to the local ones.

    With the flag on, the bundle's (already entity-checked) values are written.
    With it off, the values read before the delete phase are re-inserted: the
    delete of ``settings WHERE key IN (VALID_SETTING_KEYS)`` plus the wholesale
    replacement of ``user_preferences`` would otherwise wipe both, and the
    Sommelier would stop working until the user re-picked their LLM agent —
    the precise opposite of what the flag is for.

    ``preserved`` is the fallback in *both* modes. A value the handler dropped
    because its entity is not in this state machine, or that the bundle never
    carried, must not take the local one down with it: these two cells are the
    definition of "not portable", so the only sane thing to do when a bundle
    has nothing usable to say about them is to leave this install's own value
    exactly where it was.
    """
    values = from_bundle if include_install_specific else preserved
    for logical, (table, key) in _INSTALL_SPECIFIC.items():
        value = values.get(logical)
        if value is None:
            # Nothing usable came from the bundle: keep what this install had.
            value = preserved.get(logical)
        if value is None:
            continue
        if not await _table_exists(conn, table):
            continue
        # `table` comes from `_INSTALL_SPECIFIC`, a module constant.
        await conn.execute(
            f'INSERT OR REPLACE INTO "{table}" (key, value) '  # nosec B608
            f"VALUES (?, ?)",
            (key, str(value)),
        )


async def _safe_rollback(conn: Any) -> None:
    """Roll back, swallowing the error if no transaction is open."""
    try:
        await conn.execute("ROLLBACK")
    except Exception as exc:  # noqa: BLE001 — the original error must win
        _LOGGER.debug("Sommelier backup rollback was a no-op: %s", exc)


# ── Snapshot store (sync helpers — run these in an executor) ──────────


def validate_snapshot_name(directory: str | Path, name: Any) -> Path:
    """Return the resolved path of ``name`` inside ``directory``, or raise.

    The guarantee is containment: the name has to resolve to a file whose
    parent *is* the backups folder, which is what stops ``../secrets.json``,
    an absolute path, a subdirectory and a symlink pointing out of the folder
    alike. The only other requirement is a ``.json`` suffix.

    Deliberately not a charset regex. This function gates Download, Restore
    and Delete for every file :func:`list_snapshots` offers, and the folder is
    a documented drop point for bundles too large for the inbound WS frame —
    so a browser's ``melitta-sommelier-2026-09-10 (1).json`` or a
    ``Küche-backup.json`` has to be usable. A stricter name rule than the
    filesystem's does not add security here (containment already carries it),
    it only creates rows the UI can list but not act on.
    """
    if not isinstance(name, str) or not name.endswith(".json"):
        raise InvalidSnapshotNameError(f"Invalid snapshot name: {name!r}")
    base = Path(directory)
    try:
        candidate = (base / name).resolve()
        contained = candidate.parent == base.resolve()
    except (OSError, ValueError) as exc:  # embedded NUL, name too long, …
        raise InvalidSnapshotNameError(f"Invalid snapshot name: {name!r}") from exc
    if not contained:
        raise InvalidSnapshotNameError(f"Invalid snapshot name: {name!r}")
    return candidate


def write_snapshot(directory: str | Path, bundle: dict[str, Any]) -> str:
    """Write a bundle as a timestamped ``pre-import-*.json`` and return its name.

    The snapshot is a full bundle produced by the ordinary export builder, not
    a copy of the ``.db`` file: it is a local rollback artifact, nothing may be
    lost from it, and it has to round-trip through the ordinary importer.
    Collisions within the same second get a ``-2``, ``-3``… suffix.

    The file is measured after writing and removed again if it is over
    :data:`MAX_SNAPSHOT_FILE_BYTES`: :func:`read_snapshot` refuses such a file,
    and a rollback artifact that its own Restore button rejects is worse than
    an import that refuses to start. Measuring the file on disk rather than an
    in-memory copy keeps the comparison identical to the one the reader makes
    and avoids holding a second copy of a large bundle.
    """
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    stamp = dt_util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    name = f"{SNAPSHOT_AUTO_PREFIX}{stamp}.json"
    suffix = 1
    while (base / name).exists():
        suffix += 1
        name = f"{SNAPSHOT_AUTO_PREFIX}{stamp}-{suffix}.json"
    path = base / name
    path.write_text(
        json.dumps(bundle, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    size = path.stat().st_size
    if size > MAX_SNAPSHOT_FILE_BYTES:
        path.unlink(missing_ok=True)
        raise SnapshotFileTooLargeError(
            f"The pre-import backup would be {size} bytes, over the "
            f"{MAX_SNAPSHOT_FILE_BYTES}-byte limit a restore can read"
        )
    return name


def list_snapshots(directory: str | Path) -> list[dict[str, Any]]:
    """List snapshot files newest first, from ``os.stat`` metadata only.

    Files are deliberately never parsed here: the list is rendered on every
    panel visit and a corrupt or huge file must not break it. ``source`` is
    ``"auto"`` for the automatic pre-import snapshots and ``"manual"`` for
    anything the user dropped in or renamed — only the former are ever pruned.
    """
    base = Path(directory)
    if not base.is_dir():
        return []
    entries: list[tuple[int, str, dict[str, Any]]] = []
    for entry in os.scandir(base):
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        stat = entry.stat()
        entries.append(
            (
                stat.st_mtime_ns,
                entry.name,
                {
                    "name": entry.name,
                    "created_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "size_bytes": stat.st_size,
                    "source": (
                        "auto"
                        if entry.name.startswith(SNAPSHOT_AUTO_PREFIX)
                        else "manual"
                    ),
                },
            )
        )
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in entries]


def read_snapshot(directory: str | Path, name: str) -> dict[str, Any]:
    """Read and parse one snapshot file, guarded by name and size checks."""
    path = validate_snapshot_name(directory, name)
    if not path.is_file():
        raise SnapshotNotFoundError(f"No such snapshot: {name}")
    size = path.stat().st_size
    if size > MAX_SNAPSHOT_FILE_BYTES:
        raise SnapshotFileTooLargeError(
            f"Snapshot {name} is {size} bytes; the limit is "
            f"{MAX_SNAPSHOT_FILE_BYTES}"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InvalidExportError(f"Snapshot {name} is not valid JSON") from exc


def delete_snapshot(directory: str | Path, name: str) -> None:
    """Delete exactly one validated snapshot file."""
    path = validate_snapshot_name(directory, name)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise SnapshotNotFoundError(f"No such snapshot: {name}") from exc


def prune_snapshots(
    directory: str | Path,
    keep: int = SNAPSHOT_RETENTION,
    protect: str | None = None,
) -> list[str]:
    """Keep the newest ``keep`` automatic snapshots, delete the rest.

    Only files whose name starts with ``pre-import-`` are candidates: a file
    the user dropped in or kept by hand is never pruned, it is removed through
    ``snapshots/delete`` alone.

    ``protect`` exempts one file from the prune entirely. The restore path
    passes the snapshot it is restoring *from*: with the folder already at the
    retention limit, the pre-restore snapshot taken a moment earlier pushes the
    oldest file over the edge — and the oldest file is exactly the one the user
    just reached for. Using a rollback point must never destroy it.
    """
    auto = [
        entry["name"]
        for entry in list_snapshots(directory)
        if entry["source"] == "auto" and entry["name"] != protect
    ]
    removed: list[str] = []
    for name in auto[keep:]:
        try:
            (Path(directory) / name).unlink()
            removed.append(name)
        except OSError as exc:  # noqa: PERF203 — one bad file must not stop pruning
            _LOGGER.warning("Could not prune Sommelier snapshot %s: %s", name, exc)
    return removed
