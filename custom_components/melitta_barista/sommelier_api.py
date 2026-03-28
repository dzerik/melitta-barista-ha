"""WebSocket API for AI Coffee Sommelier."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PROCESS_MAP, SHOTS_MAP, INTENSITY_MAP, AROMA_MAP, TEMPERATURE_MAP

_LOGGER = logging.getLogger("melitta_barista")


def _find_client(hass: HomeAssistant):
    """Find the first available MelittaBleClient via config entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and entry.runtime_data:
            return entry.runtime_data
    return None


async def _brew_recipe_components(
    client, name: str, blend: int, comp1: dict, comp2: dict
) -> None:
    """Build RecipeComponents and call brew_freestyle on the client."""
    from .protocol import RecipeComponent

    component1 = RecipeComponent(
        process=PROCESS_MAP.get(comp1["process"], 1),
        shots=SHOTS_MAP.get(comp1["shots"], 0),
        blend=blend,
        intensity=INTENSITY_MAP.get(comp1["intensity"], 2),
        aroma=AROMA_MAP.get(comp1["aroma"], 0),
        temperature=TEMPERATURE_MAP.get(comp1["temperature"], 1),
        portion=comp1["portion_ml"] // 5,
    )
    component2 = RecipeComponent(
        process=PROCESS_MAP.get(comp2["process"], 0),
        shots=SHOTS_MAP.get(comp2["shots"], 0),
        blend=0 if blend == 1 else 1,
        intensity=INTENSITY_MAP.get(comp2["intensity"], 2),
        aroma=AROMA_MAP.get(comp2["aroma"], 0),
        temperature=TEMPERATURE_MAP.get(comp2["temperature"], 1),
        portion=comp2["portion_ml"] // 5,
    )

    await client.brew_freestyle(
        name=name,
        recipe_type=24,
        component1=component1,
        component2=component2,
    )


# ── Schemas ───────────────────────────────────────────────────────────

VALID_ROASTS = ["light", "medium", "medium_dark", "dark"]
VALID_BEAN_TYPES = ["arabica", "arabica_robusta", "robusta"]
VALID_ORIGINS = ["single_origin", "blend"]
VALID_FLAVOR_NOTES = [
    "chocolate", "nutty", "fruity", "floral", "caramel",
    "spicy", "earthy", "honey", "berry", "citrus",
]
VALID_MILK_TYPES = [
    "regular", "whole", "skim", "oat", "almond",
    "soy", "coconut", "cream",
]
VALID_MODES = ["surprise_me", "custom"]

BEAN_SCHEMA = {
    vol.Required("brand"): cv.string,
    vol.Required("product"): cv.string,
    vol.Required("roast"): vol.In(VALID_ROASTS),
    vol.Required("bean_type"): vol.In(VALID_BEAN_TYPES),
    vol.Required("origin"): vol.In(VALID_ORIGINS),
    vol.Optional("origin_country"): cv.string,
    vol.Optional("flavor_notes", default=[]): vol.All(
        cv.ensure_list, [vol.In(VALID_FLAVOR_NOTES)]
    ),
    vol.Optional("composition"): cv.string,
    vol.Optional("preset_id"): cv.string,
}


async def _async_get_db(hass: HomeAssistant):
    """Get or lazily initialize the SommelierDB instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    db = domain_data.get("sommelier_db")
    if db is not None:
        return db

    from .sommelier_db import SommelierDB

    db_path = hass.config.path("melitta_barista_sommelier.db")
    db = SommelierDB(db_path)
    await db.async_setup()
    domain_data["sommelier_db"] = db
    _LOGGER.info("Sommelier DB initialized lazily at %s", db_path)
    return db


def async_register_websocket_handlers(hass: HomeAssistant) -> None:
    """Register all Sommelier WebSocket command handlers."""
    websocket_api.async_register_command(hass, ws_beans_list)
    websocket_api.async_register_command(hass, ws_beans_add)
    websocket_api.async_register_command(hass, ws_beans_update)
    websocket_api.async_register_command(hass, ws_beans_delete)
    websocket_api.async_register_command(hass, ws_hoppers_get)
    websocket_api.async_register_command(hass, ws_hoppers_assign)
    websocket_api.async_register_command(hass, ws_milk_get)
    websocket_api.async_register_command(hass, ws_milk_set)
    websocket_api.async_register_command(hass, ws_generate)
    websocket_api.async_register_command(hass, ws_brew)
    websocket_api.async_register_command(hass, ws_favorites_list)
    websocket_api.async_register_command(hass, ws_favorites_add)
    websocket_api.async_register_command(hass, ws_favorites_remove)
    websocket_api.async_register_command(hass, ws_favorites_brew)
    websocket_api.async_register_command(hass, ws_history_list)
    websocket_api.async_register_command(hass, ws_presets_list)
    websocket_api.async_register_command(hass, ws_settings_get)
    websocket_api.async_register_command(hass, ws_settings_set)


# ── Beans ─────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/beans/list"}
)
@websocket_api.async_response
async def ws_beans_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List all coffee beans."""
    db = await _async_get_db(hass)
    beans = await db.async_list_beans()
    connection.send_result(msg["id"], {"beans": beans})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/add",
        **BEAN_SCHEMA,
    }
)
@websocket_api.async_response
async def ws_beans_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a new coffee bean."""
    db = await _async_get_db(hass)
    data = {
        k: msg[k]
        for k in (
            "brand", "product", "roast", "bean_type", "origin",
            "origin_country", "flavor_notes", "composition", "preset_id",
        )
        if k in msg
    }
    bean = await db.async_add_bean(data)
    connection.send_result(msg["id"], {"bean": bean})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/update",
        vol.Required("bean_id"): cv.string,
        vol.Optional("brand"): cv.string,
        vol.Optional("product"): cv.string,
        vol.Optional("roast"): vol.In(VALID_ROASTS),
        vol.Optional("bean_type"): vol.In(VALID_BEAN_TYPES),
        vol.Optional("origin"): vol.In(VALID_ORIGINS),
        vol.Optional("origin_country"): cv.string,
        vol.Optional("flavor_notes"): vol.All(
            cv.ensure_list, [vol.In(VALID_FLAVOR_NOTES)]
        ),
        vol.Optional("composition"): cv.string,
        vol.Optional("preset_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_beans_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update an existing coffee bean."""
    db = await _async_get_db(hass)
    bean_id = msg["bean_id"]
    data = {
        k: msg[k]
        for k in (
            "brand", "product", "roast", "bean_type", "origin",
            "origin_country", "flavor_notes", "composition", "preset_id",
        )
        if k in msg
    }
    bean = await db.async_update_bean(bean_id, data)
    if bean is None:
        connection.send_error(msg["id"], "not_found", f"Bean {bean_id} not found")
        return
    connection.send_result(msg["id"], {"bean": bean})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/delete",
        vol.Required("bean_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_beans_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a coffee bean."""
    db = await _async_get_db(hass)
    deleted = await db.async_delete_bean(msg["bean_id"])
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Bean not found")
        return
    connection.send_result(msg["id"])


# ── Hoppers ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/hoppers/get"}
)
@websocket_api.async_response
async def ws_hoppers_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get current hopper assignments."""
    db = await _async_get_db(hass)
    hoppers = await db.async_get_hoppers()
    connection.send_result(msg["id"], hoppers)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/hoppers/assign",
        vol.Required("hopper_id"): vol.In([1, 2]),
        vol.Optional("bean_id"): vol.Any(cv.string, None),
    }
)
@websocket_api.async_response
async def ws_hoppers_assign(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Assign a bean to a hopper."""
    db = await _async_get_db(hass)
    await db.async_assign_hopper(msg["hopper_id"], msg.get("bean_id"))
    connection.send_result(msg["id"])


# ── Milk ──────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/milk/get"}
)
@websocket_api.async_response
async def ws_milk_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get available milk types."""
    db = await _async_get_db(hass)
    milk = await db.async_get_milk()
    connection.send_result(msg["id"], {"milk_types": milk})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/milk/set",
        vol.Required("milk_types"): vol.All(
            cv.ensure_list, [vol.In(VALID_MILK_TYPES)]
        ),
    }
)
@websocket_api.async_response
async def ws_milk_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set available milk types."""
    db = await _async_get_db(hass)
    await db.async_set_milk(msg["milk_types"])
    connection.send_result(msg["id"])


# ── Generate ──────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/generate",
        vol.Optional("mode", default="surprise_me"): vol.In(VALID_MODES),
        vol.Optional("preference"): cv.string,
        vol.Optional("count", default=3): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=5)
        ),
    }
)
@websocket_api.async_response
async def ws_generate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Generate AI freestyle recipes."""
    from .ai_recipes import async_generate_recipes

    db = await _async_get_db(hass)
    hoppers = await db.async_get_hoppers()
    milk_types = await db.async_get_milk()
    settings = await db.async_get_settings()

    hopper1_bean = hoppers.get("hopper1", {}).get("bean")
    hopper2_bean = hoppers.get("hopper2", {}).get("bean")

    try:
        recipes = await async_generate_recipes(
            hass=hass,
            hopper1_bean=hopper1_bean,
            hopper2_bean=hopper2_bean,
            milk_types=milk_types,
            mode=msg["mode"],
            preference=msg.get("preference"),
            count=msg["count"],
            llm_agent=settings.get("llm_agent_id"),
        )
    except Exception as err:
        _LOGGER.error("Failed to generate recipes: %s", err)
        connection.send_error(msg["id"], "generation_failed", str(err))
        return

    hopper1_bean_id = hopper1_bean["id"] if hopper1_bean else None
    hopper2_bean_id = hopper2_bean["id"] if hopper2_bean else None

    session = await db.async_create_session(
        mode=msg["mode"],
        preference=msg.get("preference"),
        hopper1_bean_id=hopper1_bean_id,
        hopper2_bean_id=hopper2_bean_id,
        milk_types=milk_types,
        llm_agent=settings.get("llm_agent_id"),
        recipes=recipes,
    )
    connection.send_result(msg["id"], {"session": session})


# ── Brew (from generated recipe) ─────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/brew",
        vol.Required("recipe_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_brew(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Brew a generated recipe via freestyle."""
    db = await _async_get_db(hass)
    recipe = await db.async_get_recipe(msg["recipe_id"])
    if recipe is None:
        connection.send_error(msg["id"], "not_found", "Recipe not found")
        return

    client = _find_client(hass)
    if client is None:
        connection.send_error(msg["id"], "no_device", "No Melitta device available")
        return

    try:
        await _brew_recipe_components(
            client, recipe["name"], recipe["blend"],
            recipe["component1"], recipe["component2"],
        )
    except Exception as err:
        _LOGGER.error("Failed to brew recipe: %s", err)
        connection.send_error(msg["id"], "brew_failed", str(err))
        return

    await db.async_mark_recipe_brewed(msg["recipe_id"])
    connection.send_result(msg["id"])


# ── Favorites ─────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/favorites/list"}
)
@websocket_api.async_response
async def ws_favorites_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List favorites."""
    db = await _async_get_db(hass)
    favorites = await db.async_list_favorites()
    connection.send_result(msg["id"], {"favorites": favorites})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/add",
        vol.Required("recipe_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_favorites_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a generated recipe to favorites."""
    db = await _async_get_db(hass)
    recipe = await db.async_get_recipe(msg["recipe_id"])
    if recipe is None:
        connection.send_error(msg["id"], "not_found", "Recipe not found")
        return

    # Get current hopper bean for source tracking
    hoppers = await db.async_get_hoppers()
    hopper_key = f"hopper{recipe['blend'] + 1}"
    source_bean = hoppers.get(hopper_key, {}).get("bean")

    fav = await db.async_add_favorite({
        "name": recipe["name"],
        "description": recipe["description"],
        "blend": recipe["blend"],
        "component1": recipe["component1"],
        "component2": recipe["component2"],
        "source_recipe_id": recipe["id"],
        "source_bean_id": source_bean["id"] if source_bean else None,
    })
    connection.send_result(msg["id"], {"favorite": fav})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/remove",
        vol.Required("favorite_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_favorites_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a favorite."""
    db = await _async_get_db(hass)
    removed = await db.async_remove_favorite(msg["favorite_id"])
    if not removed:
        connection.send_error(msg["id"], "not_found", "Favorite not found")
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/brew",
        vol.Required("favorite_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_favorites_brew(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Brew a favorite recipe."""
    db = await _async_get_db(hass)
    fav = await db.async_get_favorite(msg["favorite_id"])
    if fav is None:
        connection.send_error(msg["id"], "not_found", "Favorite not found")
        return

    client = _find_client(hass)
    if client is None:
        connection.send_error(msg["id"], "no_device", "No Melitta device available")
        return

    try:
        await _brew_recipe_components(
            client, fav["name"], fav["blend"],
            fav["component1"], fav["component2"],
        )
    except Exception as err:
        _LOGGER.error("Failed to brew favorite: %s", err)
        connection.send_error(msg["id"], "brew_failed", str(err))
        return

    await db.async_increment_favorite_brew(msg["favorite_id"])
    connection.send_result(msg["id"])


# ── History ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/history/list",
        vol.Optional("limit", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("offset", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)
@websocket_api.async_response
async def ws_history_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List generation history."""
    db = await _async_get_db(hass)
    sessions = await db.async_list_history(
        limit=msg["limit"], offset=msg["offset"]
    )
    connection.send_result(msg["id"], {"sessions": sessions})


# ── Presets ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/presets/list"}
)
@callback
def ws_presets_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List built-in coffee presets."""
    presets_path = Path(__file__).parent / "coffee_presets.json"
    try:
        presets = json.loads(presets_path.read_text(encoding="utf-8"))
    except Exception as err:
        _LOGGER.error("Failed to load presets: %s", err)
        connection.send_error(msg["id"], "load_failed", str(err))
        return
    connection.send_result(msg["id"], {"presets": presets})


# ── Settings ──────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/settings/get"}
)
@websocket_api.async_response
async def ws_settings_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get Sommelier settings."""
    db = await _async_get_db(hass)
    settings = await db.async_get_settings()
    connection.send_result(msg["id"], {"settings": settings})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/settings/set",
        vol.Required("key"): cv.string,
        vol.Required("value"): cv.string,
    }
)
@websocket_api.async_response
async def ws_settings_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set a Sommelier setting."""
    db = await _async_get_db(hass)
    await db.async_set_setting(msg["key"], msg["value"])
    connection.send_result(msg["id"])
