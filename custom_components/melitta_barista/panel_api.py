"""WebSocket API serving the admin SPA panel.

Sommelier-specific endpoints live in `sommelier_api.py`; the ones registered
here are panel-wide (status, diagnostics, recipes, producers, additives).
Callers always pass `entry_id` so that multi-machine setups address a
specific config entry. The handlers below stay synchronous when they only
read in-memory client state, and use `async_response` when DB I/O is involved.
"""

from __future__ import annotations

import logging
import time
from collections import deque
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
    # LLM call buffer is domain-wide (cross-entry).
    _llm_call_buffer(hass).clear()
    connection.send_result(msg["id"], {"cleared": True})


@callback
def _ws_diagnostics_llm_calls(hass: HomeAssistant, connection, msg) -> None:
    """Return the recent LLM calls (full prompt + response). Domain-wide."""
    connection.send_result(msg["id"], {
        "llm_calls": list(_llm_call_buffer(hass)),
    })


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
    """Create panel-only tables (producers, syrups, toppings, tags, prompts)
    if missing. Beans and milk tables are managed by sommelier_db.async_setup.
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

        CREATE TABLE IF NOT EXISTS flavor_tags (
            name TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS panel_prompts (
            slot TEXT PRIMARY KEY,
            template TEXT NOT NULL,
            updated_at TEXT NOT NULL
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


async def _resolve_agent_id(hass, msg) -> str | None:
    """Pick the conversation agent: explicit msg arg > setting > HA default."""
    agent_id = msg.get("agent_id")
    if agent_id:
        return agent_id
    try:
        db = await _async_get_db(hass)
        settings = await db.async_get_settings()
        return settings.get("llm_agent_id") or None
    except Exception:  # noqa: BLE001
        return None


def _llm_call_buffer(hass) -> deque:
    """Lazy-init the per-process LLM call ring buffer. Capped at 20 entries."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    buf = domain_data.get("recent_llm_calls")
    if buf is None:
        buf = deque(maxlen=20)
        domain_data["recent_llm_calls"] = buf
    return buf


def _record_llm_call(
    hass, *, slot: str, agent_id: str | None, prompt: str, raw: str,
    via: str, validation_errors: list | None,
) -> None:
    """Append one diagnostic entry. Truncated to keep memory bounded."""
    _llm_call_buffer(hass).append({
        "ts": time.time(),
        "slot": slot,
        "agent_id": agent_id or "",
        "prompt": prompt[:8000],   # full prompts can be enormous; cap.
        "prompt_len": len(prompt),
        "raw": (raw or "")[:8000],
        "raw_len": len(raw or ""),
        "via": via,
        "validation_errors": validation_errors or [],
    })


async def _llm_call_text(hass, prompt: str, agent_id: str | None, ctx) -> str:
    """Single round-trip to the conversation agent; returns raw speech text."""
    from homeassistant.components import conversation  # noqa: PLC0415
    result = await conversation.async_converse(
        hass,
        text=prompt,
        conversation_id=None,
        context=ctx,
        language=hass.config.language,
        agent_id=agent_id,
    )
    try:
        return result.response.speech["plain"]["speech"]
    except (AttributeError, KeyError, TypeError):
        return str(result)


async def _structured_call(
    hass, slot: str, fmt_vars: dict, agent_id: str | None, ctx,
    *, max_retries: int = 1, prebuilt_prompt: str | None = None,
) -> dict:
    """Hybrid structured-output call.

    Path A — when SmartChain ships an `async_generate_structured` helper and
    the user has selected a SmartChain agent, route the request through it
    so the provider's native JSON-Schema mode (OpenAI/Gemini/Anthropic
    tool-use/Ollama 0.5+) returns a strictly-typed object.

    Path B (fallback for any agent including the default HA Assist) — append
    the JSON Schema to the prompt as text, parse the LLM's reply, validate
    against the same pydantic model, and retry once with the validation
    errors as feedback if it fails.
    """
    smartchain = _try_smartchain_structured()
    model = RESPONSE_MODELS.get(slot)
    template = await _resolve_prompt(hass, slot)

    # The native (SmartChain) path takes the intent text without our
    # text-mode schema block; SmartChain enforces the schema natively.
    # When the caller pre-built the prompt (sommelier path that needs
    # the dynamic context inserted), use it as-is for the intent.
    if prebuilt_prompt is not None:
        intent = prebuilt_prompt
    else:
        try:
            intent = template.format(**fmt_vars)
        except (KeyError, IndexError):
            intent = template

    if smartchain is not None and model is not None and agent_id and "smartchain" in agent_id:
        try:
            obj = await smartchain(hass, schema=model, prompt=intent, agent_id=agent_id)
            payload = {
                "raw": "",
                "parsed": obj.model_dump() if hasattr(obj, "model_dump") else dict(obj),
                "validation_errors": [],
                "via": "smartchain_structured",
            }
            _record_llm_call(
                hass, slot=slot, agent_id=agent_id, prompt=intent,
                raw="", via="smartchain_structured", validation_errors=None,
            )
            return payload
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("SmartChain structured call failed, falling back: %s", exc)

    # Text path: append the JSON-Schema block to the (intent or prebuilt)
    # prompt and run the validate-and-retry loop.
    if prebuilt_prompt is not None:
        # When the caller assembled the prompt themselves, just bolt on the
        # schema block — no .format() is run on prebuilt_prompt.
        base_prompt = intent
        schema = _schema_for(slot)
        if schema is not None:
            import json as _json  # noqa: PLC0415
            base_prompt += (
                "\n\n--- RESPONSE FORMAT (DO NOT CHANGE) ---\n"
                "Reply with ONLY a JSON object that matches this JSON Schema. "
                "No prose, no markdown fences, no commentary.\n\n"
                f"{_json.dumps(schema, indent=2, ensure_ascii=False)}"
            )
    else:
        base_prompt = _assemble_prompt(slot, template, fmt_vars)

    last_errors: list | None = None
    raw = ""
    parsed: dict | None = None
    for attempt in range(max_retries + 1):
        prompt = base_prompt
        if last_errors and attempt > 0:
            import json as _json  # noqa: PLC0415
            prompt += (
                "\n\n--- PREVIOUS ATTEMPT FAILED VALIDATION ---\n"
                f"Errors: {_json.dumps(last_errors, ensure_ascii=False)}\n"
                "Re-emit a corrected JSON object matching the schema above."
            )
        raw = await _llm_call_text(hass, prompt, agent_id, ctx)
        data = _parse_llm_json(raw)
        if data is None:
            last_errors = [{"loc": "<root>", "msg": "Response was not valid JSON"}]
            continue
        validated, errors = _validate_parsed(slot, data)
        if errors is None:
            parsed = validated
            last_errors = None
            break
        last_errors = errors
        parsed = data  # surface the unvalidated dict so UI can preview
    payload = {
        "raw": raw,
        "parsed": parsed,
        "validation_errors": last_errors or [],
        "via": "text_with_validation",
    }
    _record_llm_call(
        hass, slot=slot, agent_id=agent_id, prompt=base_prompt,
        raw=raw, via="text_with_validation",
        validation_errors=last_errors,
    )
    return payload


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/beans/autofill",
    vol.Required("brand"): str,
    vol.Required("product"): str,
    vol.Optional("website"): str,
    vol.Optional("agent_id"): str,
})
@websocket_api.async_response
async def _ws_beans_autofill(hass, connection, msg):
    """Use an HA conversation agent to enrich a bean entry from brand+product.

    Hybrid path:
      - SmartChain native structured output when available (strict, no parse).
      - Otherwise: schema appended to the prompt + pydantic validation +
        one retry with validation errors as feedback.

    Returns: { raw, parsed, validation_errors, via }
      - `parsed` is the validated dict on success, the unvalidated dict when
        validation failed (so the UI can still preview), or null when the
        response wasn't even valid JSON.
      - `validation_errors` is empty on success.
      - `via` indicates which code path produced the result, useful for
        diagnostics.
    """
    # Build the optional website fragment so the template reads cleanly
    # whether or not the producer has a site configured. The whole hint
    # collapses to "" when no URL is supplied — the user-visible prompt
    # simply doesn't mention the website at all.
    website = (msg.get("website") or "").strip()
    website_hint = (
        f"\n\nThe producer's official website is {website} — feel free to "
        "draw on its product description if it adds accuracy. Still reply "
        "ONLY with the JSON object below."
        if website else ""
    )
    fmt_vars = {
        "brand": msg["brand"],
        "product": msg["product"],
        "website_hint": website_hint,
    }
    agent_id = await _resolve_agent_id(hass, msg)

    try:
        result = await _structured_call(
            hass, "beans_autofill", fmt_vars, agent_id, connection.context(msg),
        )
    except Exception as exc:  # noqa: BLE001
        connection.send_error(msg["id"], "conversation_error", str(exc))
        return

    connection.send_result(msg["id"], result)


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


def _make_additive_update_handler(table: str):
    """Generate the update handler for an additive table."""

    @websocket_api.websocket_command({
        vol.Required("type"): f"melitta_barista/{table}/update",
        vol.Required("id"): int,
        vol.Optional("name"): str,
        vol.Optional("brand"): str,
        vol.Optional("notes"): str,
    })
    @websocket_api.async_response
    async def _ws_update(hass, connection, msg):
        db = await _async_get_db(hass)
        fields = {k: msg[k] for k in ("name", "brand", "notes") if k in msg}
        if not fields:
            connection.send_error(msg["id"], "no_fields", "No fields to update")
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        await db._db.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",
            (*fields.values(), msg["id"]),
        )
        await db._db.commit()
        connection.send_result(msg["id"], {"updated": True})

    return _ws_update


_SYRUPS_UPDATE = _make_additive_update_handler("syrups")
_TOPPINGS_UPDATE = _make_additive_update_handler("toppings")


# tags --------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): "melitta_barista/tags/list"})
@websocket_api.async_response
async def _ws_tags_list(hass, connection, msg):
    """Return the union of explicit flavor_tags + tags ever used by any bean."""
    db = await _async_get_db(hass)
    cursor = await db._db.execute("SELECT name FROM flavor_tags ORDER BY name")
    explicit = {row[0] for row in await cursor.fetchall()}
    # also pick up anything currently referenced by beans so the autofill
    # results stay in sync without an extra registration step
    cursor = await db._db.execute(
        "SELECT flavor_notes FROM coffee_beans WHERE flavor_notes IS NOT NULL"
    )
    import json as _json  # noqa: PLC0415
    for (raw,) in await cursor.fetchall():
        try:
            arr = _json.loads(raw)
            if isinstance(arr, list):
                for tag in arr:
                    if isinstance(tag, str) and tag:
                        explicit.add(tag)
        except (TypeError, ValueError):
            continue
    connection.send_result(msg["id"], {"tags": sorted(explicit)})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/tags/add",
    vol.Required("name"): str,
})
@websocket_api.async_response
async def _ws_tags_add(hass, connection, msg):
    name = msg["name"].strip()
    if not name:
        connection.send_error(msg["id"], "empty", "Tag name is empty")
        return
    db = await _async_get_db(hass)
    await db._db.execute(
        "INSERT OR IGNORE INTO flavor_tags (name, created_at) VALUES (?, ?)",
        (name, _now_iso()),
    )
    await db._db.commit()
    connection.send_result(msg["id"], {"name": name})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/tags/delete",
    vol.Required("name"): str,
})
@websocket_api.async_response
async def _ws_tags_delete(hass, connection, msg):
    db = await _async_get_db(hass)
    await db._db.execute("DELETE FROM flavor_tags WHERE name = ?", (msg["name"],))
    await db._db.commit()
    connection.send_result(msg["id"], {"deleted": True})


# prompts + structured output --------------------------------------------

# User-editable "intent" portion of the prompt — purely describes WHAT we want.
# The strict schema block is auto-appended in _assemble_prompt() so users can
# customise the request without accidentally breaking the response shape.
DEFAULT_PROMPTS: dict[str, str] = {
    "beans_autofill": (
        "You are a coffee specialist. Describe the product {product!r} "
        "made by producer {brand!r}: its roast level, bean blend, "
        "origin, characteristic flavor notes, and a short brewing "
        "recommendation. Be concise and accurate.{website_hint}"
    ),
    "sommelier_intro": (
        "You are an expert barista and coffee sommelier. Generate exactly "
        "{count} unique coffee recipes for a bean-to-cup smart coffee machine. "
        "Use the available beans, milk, time of day, weather, and any "
        "preferences provided in the context below. Be creative but practical: "
        "every recipe must be brewable on the configured machine."
    ),
}


# Placeholders each slot supports — surfaced via /prompts/list so the panel
# can render an inline help block telling users which substitutions are
# available without making them grep the source.
PROMPT_PLACEHOLDERS: dict[str, list[dict[str, str]]] = {
    "beans_autofill": [
        {"name": "brand", "desc": "Producer name (e.g. Lavazza)"},
        {"name": "product", "desc": "Bean / blend name (e.g. Crema e Aroma)"},
        {"name": "website_hint",
         "desc": "Auto-built fragment: ' (official website: <url>)' when the producer "
                 "has a site set, empty string otherwise. Wherever you put it in the "
                 "template, it disappears cleanly when no URL is configured."},
    ],
    "sommelier_intro": [
        {"name": "count", "desc": "Number of recipes to generate (1–5)"},
    ],
}


# Pydantic models declare the exact response shape per slot. They serve two
# purposes: they emit JSON Schema (appended to the prompt verbatim so the LLM
# sees the exact contract) and they validate the parsed response. When the
# model rejects the data we retry once with the validation errors as feedback.
try:
    from pydantic import BaseModel, Field, ValidationError  # noqa: PLC0415
    from typing import Literal  # noqa: PLC0415

    class BeanAutofillResult(BaseModel):
        """Strict schema for the beans autofill LLM response."""

        roast: Literal["light", "medium", "medium_dark", "dark"]
        bean_type: Literal["arabica", "arabica_robusta", "robusta"]
        origin: Literal["single_origin", "blend"]
        origin_country: str = ""
        flavor_notes: list[str] = Field(default_factory=list)
        composition: str = ""
        brewing_recommendation: str = ""

    class RecipeComponent(BaseModel):
        """One component of a generated freestyle recipe."""

        process: Literal["coffee", "milk", "water", "none"] = "none"
        intensity: Literal["very_mild", "mild", "medium", "strong", "very_strong"] = "medium"
        aroma: Literal["standard", "intense"] = "standard"
        temperature: Literal["cold", "normal", "high"] = "normal"
        shots: Literal["none", "one", "two", "three"] = "none"
        portion_ml: int = Field(default=0, ge=0, le=250)

    class RecipeExtras(BaseModel):
        """Optional add-ins suggested by the sommelier."""

        ice: bool = False
        syrup: str | None = None
        topping: str | None = None
        liqueur: str | None = None
        instruction: str | None = None

    class GeneratedRecipe(BaseModel):
        """One generated sommelier recipe — what the LLM must return."""

        name: str
        description: str = ""
        blend: Literal[0, 1] = 1
        component1: RecipeComponent
        component2: RecipeComponent
        extras: RecipeExtras = Field(default_factory=lambda: RecipeExtras())
        cup_type: str | None = None
        estimated_caffeine: Literal["none", "low", "medium", "high"] | None = None
        calories_approx: int | None = Field(default=None, ge=0, le=2000)

    class SommelierGenerateResult(BaseModel):
        """Wrapper enforcing an array of recipes (top-level list isn't a JSON
        Schema object — wrapping with a `recipes` key keeps responseSchema
        modes happy on every provider)."""

        recipes: list[GeneratedRecipe] = Field(min_length=1, max_length=5)

    RESPONSE_MODELS: dict[str, type[BaseModel]] = {
        "beans_autofill": BeanAutofillResult,
        "sommelier_intro": SommelierGenerateResult,
    }
    _PYDANTIC_OK = True
except ImportError:  # pragma: no cover — defensive fallback
    RESPONSE_MODELS = {}
    ValidationError = Exception  # type: ignore[assignment, misc]
    _PYDANTIC_OK = False


def _schema_for(slot: str) -> dict | None:
    """Return the JSON-Schema dict for a slot, or None if no model is defined."""
    if not _PYDANTIC_OK:
        return None
    model = RESPONSE_MODELS.get(slot)
    return model.model_json_schema() if model else None


def _assemble_prompt(slot: str, user_template: str, fmt_vars: dict) -> str:
    """Combine the user-editable intent with the auto-appended schema block.

    Format substitution applies only to the user template; the schema block is
    a literal so users editing the template can't accidentally break the
    response contract.
    """
    try:
        intent = user_template.format(**fmt_vars)
    except (KeyError, IndexError):
        # Template uses placeholders not in fmt_vars (or vice versa). Don't
        # silently drop user content — send the template literally so they
        # can spot the problem from the LLM's reply.
        intent = user_template

    schema = _schema_for(slot)
    if schema is None:
        return intent

    import json as _json  # noqa: PLC0415
    schema_text = _json.dumps(schema, indent=2, ensure_ascii=False)
    return (
        f"{intent}\n\n"
        "--- RESPONSE FORMAT (DO NOT CHANGE) ---\n"
        "Reply with ONLY a JSON object that matches this JSON Schema. "
        "No prose, no markdown fences, no commentary.\n\n"
        f"{schema_text}"
    )


def _parse_llm_json(raw: str) -> dict | None:
    """Best-effort parse of an LLM speech reply as JSON.

    Strips markdown fences and falls back to extracting the first {...} block
    if the model wrapped its response in prose despite our instructions.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    import json as _json  # noqa: PLC0415
    try:
        data = _json.loads(text)
    except _json.JSONDecodeError:
        import re  # noqa: PLC0415
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = _json.loads(match.group(0))
            except _json.JSONDecodeError:
                return None
        else:
            return None
    return data if isinstance(data, dict) else None


def _validate_parsed(slot: str, data: dict) -> tuple[dict | None, list | None]:
    """Validate parsed data against the slot's pydantic model.

    Returns (validated_dict, None) on success, (None, errors) on failure.
    Slots without a registered model accept whatever was parsed.
    """
    if not _PYDANTIC_OK or slot not in RESPONSE_MODELS:
        return data, None
    try:
        validated = RESPONSE_MODELS[slot].model_validate(data)
        return validated.model_dump(), None
    except ValidationError as exc:
        # Trim error payload to schema-level info so it fits in a follow-up
        # prompt without dumping every Pydantic detail.
        return None, [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]}
            for e in exc.errors()
        ]


def _try_smartchain_structured():
    """Return SmartChain's structured-output helper if shipped, else None.

    SmartChain (`dzerik/ha-smartchain`) is the user's own multi-provider
    Conversation integration. When it ships an `async_generate_structured`
    helper we route LLM calls through it so that providers with native JSON
    Schema mode (OpenAI, Gemini, Anthropic tool-use, Ollama 0.5+) get a
    strict guarantee instead of best-effort prose parsing.
    """
    try:
        from custom_components.smartchain import async_generate_structured  # type: ignore  # noqa: PLC0415
        return async_generate_structured
    except ImportError:
        return None


@websocket_api.websocket_command({vol.Required("type"): "melitta_barista/prompts/list"})
@websocket_api.async_response
async def _ws_prompts_list(hass, connection, msg):
    db = await _async_get_db(hass)
    cursor = await db._db.execute("SELECT slot, template FROM panel_prompts")
    overrides = {row[0]: row[1] for row in await cursor.fetchall()}
    items = []
    for slot, default in DEFAULT_PROMPTS.items():
        items.append({
            "slot": slot,
            "default": default,
            "template": overrides.get(slot, default),
            "is_default": slot not in overrides,
            # Schema is auto-appended on send; expose it read-only so users
            # can see the contract their template is paired with.
            "schema": _schema_for(slot),
            "placeholders": PROMPT_PLACEHOLDERS.get(slot, []),
        })
    connection.send_result(msg["id"], {"prompts": items})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/prompts/save",
    vol.Required("slot"): str,
    vol.Required("template"): str,
})
@websocket_api.async_response
async def _ws_prompts_save(hass, connection, msg):
    if msg["slot"] not in DEFAULT_PROMPTS:
        connection.send_error(msg["id"], "unknown_slot", f"Unknown prompt {msg['slot']}")
        return
    db = await _async_get_db(hass)
    await db._db.execute(
        """INSERT INTO panel_prompts (slot, template, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(slot) DO UPDATE SET template = excluded.template,
                                           updated_at = excluded.updated_at""",
        (msg["slot"], msg["template"], _now_iso()),
    )
    await db._db.commit()
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/prompts/preview",
    vol.Required("slot"): str,
})
@websocket_api.async_response
async def _ws_prompts_preview(hass, connection, msg):
    """Return the exact prompt text that would be sent for a given slot.

    The text-mode result is what callers see when no SmartChain agent is
    selected: the user template (with sample fmt_vars) + the auto-appended
    JSON Schema block. For sommelier_intro we additionally inline the
    dynamic context block built from current DB / HA state so the user can
    inspect the full message that the next /generate call will produce.
    """
    slot = msg["slot"]
    if slot not in DEFAULT_PROMPTS:
        connection.send_error(msg["id"], "unknown_slot", slot)
        return

    if slot == "beans_autofill":
        # Sample includes the website hint exactly as the runtime path
        # builds it, so the preview shows what users see when a producer
        # has a website configured.
        sample = {
            "brand": "Lavazza",
            "product": "Crema e Aroma",
            "website_hint": (
                "\n\nThe producer's official website is https://www.lavazza.com — "
                "feel free to draw on its product description if it adds accuracy. "
                "Still reply ONLY with the JSON object below."
            ),
        }
        template = await _resolve_prompt(hass, slot)
        prompt = _assemble_prompt(slot, template, sample)
        connection.send_result(msg["id"], {"prompt": prompt, "sample": sample})
        return

    if slot == "sommelier_intro":
        from .ai_recipes import _build_prompt  # noqa: PLC0415
        try:
            db = await _async_get_db(hass)
            hoppers = await db.async_get_hoppers()
            milk_types = await db.async_get_milk()
            extras = await db.async_get_extras()
        except Exception:  # noqa: BLE001
            hoppers = {"hopper1": None, "hopper2": None}
            milk_types = []
            extras = {}
        intro = await _resolve_prompt(hass, slot)
        prebuilt = _build_prompt(
            hopper1_bean=(hoppers.get("hopper1") or {}).get("bean"),
            hopper2_bean=(hoppers.get("hopper2") or {}).get("bean"),
            milk_types=milk_types,
            mode="surprise_me",
            preference=None,
            count=3,
            extras=extras or None,
            intro=intro,
            omit_output_format=True,
        )
        schema = _schema_for(slot)
        if schema is not None:
            import json as _json  # noqa: PLC0415
            prebuilt += (
                "\n\n--- RESPONSE FORMAT (DO NOT CHANGE) ---\n"
                "Reply with ONLY a JSON object that matches this JSON Schema. "
                "No prose, no markdown fences, no commentary.\n\n"
                f"{_json.dumps(schema, indent=2, ensure_ascii=False)}"
            )
        connection.send_result(msg["id"], {
            "prompt": prebuilt,
            "sample": {"count": 3, "mode": "surprise_me"},
        })
        return

    # Slots without a custom preview path: just substitute placeholder
    # values from PROMPT_PLACEHOLDERS and run the generic assembler.
    template = await _resolve_prompt(hass, slot)
    sample = {ph["name"]: f"<{ph['name']}>" for ph in PROMPT_PLACEHOLDERS.get(slot, [])}
    prompt = _assemble_prompt(slot, template, sample)
    connection.send_result(msg["id"], {"prompt": prompt, "sample": sample})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/prompts/reset",
    vol.Required("slot"): str,
})
@websocket_api.async_response
async def _ws_prompts_reset(hass, connection, msg):
    db = await _async_get_db(hass)
    await db._db.execute("DELETE FROM panel_prompts WHERE slot = ?", (msg["slot"],))
    await db._db.commit()
    connection.send_result(msg["id"], {"reset": True})


async def _resolve_prompt(hass, slot: str) -> str:
    """Return the user override for a prompt slot, or the bundled default."""
    default = DEFAULT_PROMPTS.get(slot, "")
    try:
        db = await _async_get_db(hass)
        cursor = await db._db.execute(
            "SELECT template FROM panel_prompts WHERE slot = ?", (slot,)
        )
        row = await cursor.fetchone()
        if row:
            return row[0]
    except Exception:  # noqa: BLE001
        pass
    return default


# llm agents --------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): "melitta_barista/llm/agents"})
@websocket_api.async_response
async def _ws_llm_agents(hass, connection, msg):
    """List available HA conversation agents.

    Modern HA (2024.6+) exposes each agent as an entity in the
    `conversation` domain. We enumerate them from the state machine — the
    most stable cross-version interface — and prepend the legacy/default
    "Home Assistant" agent so users can fall back to it without picking a
    specific entity.
    """
    agents: list[dict[str, str]] = [
        {"id": "homeassistant", "name": "Home Assistant (default)"},
    ]
    for state in hass.states.async_all("conversation"):
        agents.append({
            "id": state.entity_id,
            "name": state.attributes.get("friendly_name") or state.entity_id,
        })
    connection.send_result(msg["id"], {"agents": agents})


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

_DIAG_LLM_CALLS_SCHEMA = vol.Schema({
    vol.Required("type"): "melitta_barista/diagnostics/llm_calls",
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
        hass, _wrap_sync_with_schema(_ws_diagnostics_llm_calls, _DIAG_LLM_CALLS_SCHEMA)
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
    async_register_command(hass, _SYRUPS_UPDATE)
    async_register_command(hass, _TOPPINGS_UPDATE)

    # tags + prompts + LLM agent picker
    async_register_command(hass, _ws_tags_list)
    async_register_command(hass, _ws_tags_add)
    async_register_command(hass, _ws_tags_delete)
    async_register_command(hass, _ws_prompts_list)
    async_register_command(hass, _ws_prompts_save)
    async_register_command(hass, _ws_prompts_reset)
    async_register_command(hass, _ws_prompts_preview)
    async_register_command(hass, _ws_llm_agents)

    domain_data["panel_api_registered"] = True
    _LOGGER.debug("Panel WS API registered")
