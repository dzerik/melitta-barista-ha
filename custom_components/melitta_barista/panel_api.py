"""WebSocket API serving the admin SPA panel.

Sommelier-specific endpoints live in `sommelier_api.py`; the ones registered
here are panel-wide (status, diagnostics, recipes, producers, additives).
Callers always pass `entry_id` so that multi-machine setups address a
specific config entry. The handlers below stay synchronous when they only
read in-memory client state, and use `async_response` when DB I/O is involved.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import async_register_command
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    AROMA_MAP,
    DirectKeyCategory,
    DOMAIN,
    INTENSITY_MAP,
    MACHINE_MODEL_NAMES,
    PROCESS_MAP,
    RECIPE_NAMES,
    SHOTS_MAP,
    TEMPERATURE_MAP,
    get_directkey_id,
)

_LOGGER = logging.getLogger("melitta_barista")

# Reverse-lookup tables (int → string) for serialising recipe components.
_SHOTS_NAMES = {v: k for k, v in SHOTS_MAP.items()}
_INTENSITY_NAMES = {v: k for k, v in INTENSITY_MAP.items()}
_AROMA_NAMES = {v: k for k, v in AROMA_MAP.items()}
_TEMPERATURE_NAMES = {v: k for k, v in TEMPERATURE_MAP.items()}


# ── helpers ──────────────────────────────────────────────────────────────


def _resolve_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    """Return the melitta_barista config entry by id, or None if unknown."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _resolve_client(hass: HomeAssistant, entry_id: str):
    """Return the live MelittaBleClient for an entry, or None."""
    entry = _resolve_entry(hass, entry_id)
    if entry is None:
        return None
    return getattr(entry, "runtime_data", None)


def _enum_name(value, name_map: dict[int, str]) -> str | None:
    """Look up a human-readable name for an integer enum value."""
    if value is None:
        return None
    try:
        return name_map.get(int(value))
    except (TypeError, ValueError):
        return None


def _process_name(value: int | None) -> str | None:
    """Reverse-lookup PROCESS_MAP (string→int) to give the string back."""
    if value is None:
        return None
    for name, code in PROCESS_MAP.items():
        if code == value:
            return name
    return None


# ── /status ──────────────────────────────────────────────────────────────


def _build_status_payload(client) -> dict[str, Any]:
    """Build the JSON-friendly status snapshot consumed by the Status tab."""
    if client is None:
        return {"available": False}

    status = client.status
    caps = client.capabilities
    machine_type = client.machine_type

    payload: dict[str, Any] = {
        "available": True,
        "address": client.address,
        "connected": client.connected,
        "firmware": client.firmware_version,
        "features": (
            {"name": client.features.name, "raw": int(client.features)}
            if client.features is not None
            else None
        ),
        "dis": dict(client.dis_info) if client.dis_info else None,
        "machine_type": machine_type.name if machine_type is not None else None,
        "model": (
            MACHINE_MODEL_NAMES.get(int(machine_type))
            if machine_type is not None and int(machine_type) in MACHINE_MODEL_NAMES
            else None
        ),
        "capabilities": (
            {
                "model_name": caps.model_name,
                "family_key": caps.family_key,
                "my_coffee_slots": caps.my_coffee_slots,
            }
            if caps is not None
            else None
        ),
        "last_handshake_at": getattr(client, "_last_handshake_at", None),
        "active_profile": client.active_profile,
        "selected_recipe": (
            int(client.selected_recipe) if client.selected_recipe is not None else None
        ),
        "total_cups": client.total_cups,
        "cup_counters": dict(client.cup_counters) if client.cup_counters else {},
    }

    if status is not None:
        payload["status"] = {
            "process": status.process.name if status.process is not None else None,
            "sub_process": (
                status.sub_process.name if status.sub_process is not None else None
            ),
            "manipulation": (
                status.manipulation.name if status.manipulation is not None else None
            ),
            "info_messages": int(status.info_messages),
            "progress": status.progress,
        }
    else:
        payload["status"] = None

    return payload


@callback
def _ws_status(hass: HomeAssistant, connection, msg) -> None:
    """Return a snapshot of the requested machine's runtime state."""
    client = _resolve_client(hass, msg["entry_id"])
    connection.send_result(msg["id"], _build_status_payload(client))


# ── /diagnostics ────────────────────────────────────────────────────────


@callback
def _ws_diagnostics(hass: HomeAssistant, connection, msg) -> None:
    """Return diagnostic snapshot: recent errors, recent frames, transport info."""
    entry = _resolve_entry(hass, msg["entry_id"])
    client = getattr(entry, "runtime_data", None) if entry else None
    if client is None or entry is None:
        connection.send_result(msg["id"], {"available": False})
        return

    # Best-effort transport detection: an ESPHome BLE proxy device's
    # `details` dict carries `source` set to the proxy's MAC; a local
    # adapter advertisement either has no source or it's hci0.
    proxy = "unknown"
    ble_device = getattr(client, "_ble_device", None)
    details = getattr(ble_device, "details", None) if ble_device else None
    if isinstance(details, dict):
        source = details.get("source") or details.get("path")
        proxy = "remote" if source and ":" in str(source) else "local"

    payload = {
        "available": True,
        "address": client.address,
        "brand": entry.data.get("brand"),
        "proxy": proxy,
        "poll_interval": getattr(client, "_poll_interval", None),
        "ble_connect_timeout": getattr(client, "_ble_connect_timeout", None),
        "frame_timeout": getattr(client, "_frame_timeout", None),
        "recent_errors": list(getattr(client, "_recent_errors", [])),
        "recent_frames": list(getattr(client, "_recent_frames", [])),
    }
    connection.send_result(msg["id"], payload)


@callback
def _ws_diagnostics_clear(hass: HomeAssistant, connection, msg) -> None:
    """Clear the in-memory diagnostic ring buffers for the given entry."""
    client = _resolve_client(hass, msg["entry_id"])
    if client is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    if hasattr(client, "_recent_errors"):
        client._recent_errors.clear()
    if hasattr(client, "_recent_frames"):
        client._recent_frames.clear()
    connection.send_result(msg["id"], {"cleared": True})


# ── /recipes ────────────────────────────────────────────────────────────


def _component_to_dict(comp) -> dict[str, Any] | None:
    if comp is None:
        return None
    return {
        "process": _process_name(comp.process) or "none",
        "process_code": int(comp.process),
        "shots": _enum_name(comp.shots, _SHOTS_NAMES),
        "intensity": _enum_name(comp.intensity, _INTENSITY_NAMES),
        "aroma": _enum_name(comp.aroma, _AROMA_NAMES),
        "temperature": _enum_name(comp.temperature, _TEMPERATURE_NAMES),
        "blend": int(comp.blend),
        "portion_ml": int(comp.portion) * 5,
    }


def _recipe_to_dict(recipe_id: int, recipe, *, label: str | None = None) -> dict[str, Any]:
    """Serialise a MachineRecipe for the panel.

    `label` is optional context (e.g. DirectKey category name) supplied by the
    caller — `MachineRecipe` itself doesn't carry a name field.
    """
    name = label or RECIPE_NAMES.get(recipe_id)
    if recipe is None:
        return {"id": recipe_id, "name": name, "components": [None, None]}
    return {
        "id": recipe_id,
        "name": name,
        "type": int(getattr(recipe, "recipe_type", 0)),
        "components": [
            _component_to_dict(getattr(recipe, "component1", None)),
            _component_to_dict(getattr(recipe, "component2", None)),
        ],
    }


@callback
def _ws_recipes_list(hass: HomeAssistant, connection, msg) -> None:
    """Return DirectKey recipes per profile from the live cache.

    Base recipes (HR/HS, IDs 200-223) are not pre-cached on the client — they
    are read on demand. The panel only ships the cached DirectKey view today;
    a base-recipe loader is wired up in a follow-up commit.
    """
    client = _resolve_client(hass, msg["entry_id"])
    if client is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return

    profile_names = getattr(client, "_profile_names", {}) or {}
    directkey_dict = getattr(client, "_directkey_recipes", {}) or {}
    directkey = []
    for profile_id in sorted(directkey_dict.keys()):
        recipes = directkey_dict[profile_id]
        rows = []
        for cat_value in sorted(recipes.keys()):
            try:
                category = DirectKeyCategory(cat_value)
                label = category.name.replace("_", " ").title()
            except ValueError:
                category = None
                label = f"Category {cat_value}"
            ble_recipe_id = (
                get_directkey_id(profile_id, category)
                if category is not None
                else cat_value
            )
            rows.append(_recipe_to_dict(ble_recipe_id, recipes[cat_value], label=label))
        directkey.append({
            "profile_id": profile_id,
            "profile_name": profile_names.get(profile_id, f"Profile {profile_id}"),
            "recipes": rows,
        })

    connection.send_result(msg["id"], {
        "base_recipes": [],
        "directkey": directkey,
    })


# ── /producers + /beans (extends sommelier_db) ──────────────────────────


async def _async_get_db(hass: HomeAssistant):
    """Lazy-init the SommelierDB and ensure panel-side schema is present."""
    from .sommelier_api import _async_get_db as _sommelier_get_db  # noqa: PLC0415
    db = await _sommelier_get_db(hass)
    # Ensure panel-only tables exist — calling on each request is cheap and
    # avoids ordering coupling with sommelier_api's first call.
    await _ensure_panel_schema(db)
    return db


async def _ensure_panel_schema(db) -> None:
    """Create panel-only tables (producers, syrups, toppings) if missing.

    Beans and milk tables are managed by sommelier_db.async_setup; we only
    need to add producers and the pure additives.
    """
    db_handle = db._db
    if db_handle is None:
        return
    await db_handle.executescript("""
        CREATE TABLE IF NOT EXISTS producers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            country TEXT,
            website TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS syrups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS toppings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
    """)
    await db_handle.commit()


def _now_iso() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).isoformat()


# producers ----------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): "melitta_barista/producers/list"})
@websocket_api.async_response
async def _ws_producers_list(hass, connection, msg):
    db = await _async_get_db(hass)
    cursor = await db._db.execute(
        "SELECT id, name, country, website, notes FROM producers ORDER BY name"
    )
    rows = await cursor.fetchall()
    connection.send_result(msg["id"], {
        "producers": [
            {"id": r[0], "name": r[1], "country": r[2], "website": r[3], "notes": r[4]}
            for r in rows
        ],
    })


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/producers/add",
    vol.Required("name"): str,
    vol.Optional("country"): str,
    vol.Optional("website"): str,
    vol.Optional("notes"): str,
})
@websocket_api.async_response
async def _ws_producers_add(hass, connection, msg):
    db = await _async_get_db(hass)
    try:
        cursor = await db._db.execute(
            "INSERT INTO producers (name, country, website, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (msg["name"], msg.get("country"), msg.get("website"), msg.get("notes"), _now_iso()),
        )
        await db._db.commit()
        connection.send_result(msg["id"], {"id": cursor.lastrowid})
    except Exception as exc:
        connection.send_error(msg["id"], "db_error", str(exc))


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/producers/update",
    vol.Required("id"): int,
    vol.Optional("name"): str,
    vol.Optional("country"): str,
    vol.Optional("website"): str,
    vol.Optional("notes"): str,
})
@websocket_api.async_response
async def _ws_producers_update(hass, connection, msg):
    db = await _async_get_db(hass)
    fields = {k: msg[k] for k in ("name", "country", "website", "notes") if k in msg}
    if not fields:
        connection.send_error(msg["id"], "no_fields", "No fields to update")
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    await db._db.execute(
        f"UPDATE producers SET {set_clause} WHERE id = ?",
        (*fields.values(), msg["id"]),
    )
    await db._db.commit()
    connection.send_result(msg["id"], {"updated": True})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/producers/delete",
    vol.Required("id"): int,
})
@websocket_api.async_response
async def _ws_producers_delete(hass, connection, msg):
    db = await _async_get_db(hass)
    await db._db.execute("DELETE FROM producers WHERE id = ?", (msg["id"],))
    await db._db.commit()
    connection.send_result(msg["id"], {"deleted": True})


# beans/autofill -----------------------------------------------------------


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/beans/autofill",
    vol.Required("brand"): str,
    vol.Required("product"): str,
    vol.Optional("agent_id"): str,
})
@websocket_api.async_response
async def _ws_beans_autofill(hass, connection, msg):
    """Use the HA conversation agent to enrich a bean entry from brand+product.

    Returns a partial bean dict (roast / bean_type / origin / origin_country /
    flavor_notes / composition) the panel can preview before saving. Best
    effort — when the LLM response can't be parsed we return what we got
    raw so the UI can show it as plain text.
    """
    from homeassistant.components import conversation  # noqa: PLC0415

    prompt = (
        f"You are a coffee specialist. For the product {msg['product']!r} "
        f"by producer {msg['brand']!r}, return a JSON object with keys:\n"
        f"  roast (light|medium|medium_dark|dark),\n"
        f"  bean_type (arabica|arabica_robusta|robusta),\n"
        f"  origin (single_origin|blend),\n"
        f"  origin_country (string, two-letter or full country name),\n"
        f"  flavor_notes (array of strings, choose from: chocolate, nutty, "
        f"fruity, floral, caramel, spicy, earthy, honey, berry, citrus),\n"
        f"  composition (string with the arabica/robusta ratio if known),\n"
        f"  brewing_recommendation (one short sentence).\n"
        f"Reply ONLY with the JSON object — no commentary, no markdown."
    )

    try:
        result = await conversation.async_converse(
            hass,
            text=prompt,
            conversation_id=None,
            context=connection.context(msg),
            language=hass.config.language,
            agent_id=msg.get("agent_id"),
        )
    except Exception as exc:  # noqa: BLE001
        connection.send_error(msg["id"], "conversation_error", str(exc))
        return

    raw_text = ""
    try:
        raw_text = result.response.speech["plain"]["speech"]
    except (AttributeError, KeyError, TypeError):
        raw_text = str(result)

    parsed: dict[str, Any] | None = None
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip markdown fence if the model used one despite our instructions.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    import json  # noqa: PLC0415
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: try to find the first {...} block.
        import re  # noqa: PLC0415
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None

    connection.send_result(msg["id"], {
        "raw": raw_text,
        "parsed": parsed,
    })


# additives: syrups + toppings --------------------------------------------


def _make_additive_handlers(table: str):
    """Generate list/add/delete WS handlers for a given simple-additive table."""

    @websocket_api.websocket_command({
        vol.Required("type"): f"melitta_barista/{table}/list",
    })
    @websocket_api.async_response
    async def _ws_list(hass, connection, msg):
        db = await _async_get_db(hass)
        cursor = await db._db.execute(
            f"SELECT id, name, brand, notes FROM {table} ORDER BY name"
        )
        rows = await cursor.fetchall()
        connection.send_result(msg["id"], {
            table: [
                {"id": r[0], "name": r[1], "brand": r[2], "notes": r[3]}
                for r in rows
            ],
        })

    @websocket_api.websocket_command({
        vol.Required("type"): f"melitta_barista/{table}/add",
        vol.Required("name"): str,
        vol.Optional("brand"): str,
        vol.Optional("notes"): str,
    })
    @websocket_api.async_response
    async def _ws_add(hass, connection, msg):
        db = await _async_get_db(hass)
        cursor = await db._db.execute(
            f"INSERT INTO {table} (name, brand, notes, created_at) VALUES (?, ?, ?, ?)",
            (msg["name"], msg.get("brand"), msg.get("notes"), _now_iso()),
        )
        await db._db.commit()
        connection.send_result(msg["id"], {"id": cursor.lastrowid})

    @websocket_api.websocket_command({
        vol.Required("type"): f"melitta_barista/{table}/delete",
        vol.Required("id"): int,
    })
    @websocket_api.async_response
    async def _ws_delete(hass, connection, msg):
        db = await _async_get_db(hass)
        await db._db.execute(f"DELETE FROM {table} WHERE id = ?", (msg["id"],))
        await db._db.commit()
        connection.send_result(msg["id"], {"deleted": True})

    return _ws_list, _ws_add, _ws_delete


_SYRUPS_HANDLERS = _make_additive_handlers("syrups")
_TOPPINGS_HANDLERS = _make_additive_handlers("toppings")


# ── registration ────────────────────────────────────────────────────────


_STATUS_SCHEMA = vol.Schema({
    vol.Required("type"): "melitta_barista/status",
    vol.Required("entry_id"): str,
})

_DIAG_SCHEMA = vol.Schema({
    vol.Required("type"): "melitta_barista/diagnostics",
    vol.Required("entry_id"): str,
})

_DIAG_CLEAR_SCHEMA = vol.Schema({
    vol.Required("type"): "melitta_barista/diagnostics/clear",
    vol.Required("entry_id"): str,
})

_RECIPES_LIST_SCHEMA = vol.Schema({
    vol.Required("type"): "melitta_barista/recipes/list",
    vol.Required("entry_id"): str,
})


def _wrap_sync_with_schema(handler, schema):
    """Wrap a sync `(hass, connection, msg)` handler with a vol schema decorator."""
    return websocket_api.websocket_command(schema.schema)(handler)


@callback
def async_register_panel_websocket(hass: HomeAssistant) -> None:
    """Register all panel WS commands. Idempotent via hass.data flag."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_api_registered"):
        return

    async_register_command(hass, _wrap_sync_with_schema(_ws_status, _STATUS_SCHEMA))
    async_register_command(hass, _wrap_sync_with_schema(_ws_diagnostics, _DIAG_SCHEMA))
    async_register_command(
        hass, _wrap_sync_with_schema(_ws_diagnostics_clear, _DIAG_CLEAR_SCHEMA)
    )
    async_register_command(
        hass, _wrap_sync_with_schema(_ws_recipes_list, _RECIPES_LIST_SCHEMA)
    )

    # producers
    async_register_command(hass, _ws_producers_list)
    async_register_command(hass, _ws_producers_add)
    async_register_command(hass, _ws_producers_update)
    async_register_command(hass, _ws_producers_delete)

    # beans LLM autofill
    async_register_command(hass, _ws_beans_autofill)

    # additives — syrups + toppings (milk lives in sommelier_api.py)
    for handler in (*_SYRUPS_HANDLERS, *_TOPPINGS_HANDLERS):
        async_register_command(hass, handler)

    domain_data["panel_api_registered"] = True
    _LOGGER.debug("Panel WS API registered")
