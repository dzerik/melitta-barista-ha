"""P10 Task 1 — brand-honest gate for Sommelier custom-recipe brews.

Covers:
- `_brew_recipe_components` raising RecipeWritesUnsupportedError when the
  active machine's MachineCapabilities declares supports_recipe_writes=False
  (Nivona families) and proceeding normally when True (Melitta families).
- WS handlers `ws_brew` and `ws_favorites_brew` translating that exception
  into `send_error("recipe_writes_unsupported", ...)` so the FE can show
  an explicit print-only state instead of failing silently inside the BLE
  layer.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api as sa
from custom_components.melitta_barista.sommelier_api import (
    RecipeWritesUnsupportedError,
    _brew_recipe_components,
)


def _unwrap(ws_handler):
    """Peel HA's @websocket_api decorators to reach the underlying coroutine."""
    inner = ws_handler
    while hasattr(inner, "__wrapped__"):
        inner = inner.__wrapped__
        if inspect.iscoroutinefunction(inner):
            break
    return inner


def _make_phases() -> list[dict]:
    """Minimal single-phase machine_phases payload for the helper."""
    return [
        {
            "component": {
                "process": "coffee",
                "shots": "one",
                "intensity": "medium",
                "aroma": "standard",
                "temperature": "normal",
                "portion_ml": 40,
            },
            "user_action_before": [],
        }
    ]


# ── _brew_recipe_components ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brew_recipe_components_raises_when_recipe_writes_unsupported():
    """Nivona-family client must raise RecipeWritesUnsupportedError before BLE."""
    client = MagicMock()
    client.capabilities = MagicMock(
        supports_recipe_writes=False,
        family_key="900",
    )
    client.brew_freestyle = AsyncMock(return_value=True)

    with pytest.raises(RecipeWritesUnsupportedError) as excinfo:
        await _brew_recipe_components(
            client,
            name="Test",
            blend=1,
            phases=_make_phases(),
        )

    assert excinfo.value.family_key == "900"
    # BLE call must NOT happen on the refusal path.
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_brew_recipe_components_allows_when_supported():
    """Melitta-family client (supports_recipe_writes=True) reaches brew_freestyle."""
    client = MagicMock()
    client.capabilities = MagicMock(
        supports_recipe_writes=True,
        family_key="barista_ts",
    )
    client.brew_freestyle = AsyncMock(return_value=True)

    await _brew_recipe_components(
        client,
        name="Test",
        blend=1,
        phases=_make_phases(),
    )

    client.brew_freestyle.assert_awaited_once()


@pytest.mark.asyncio
async def test_brew_recipe_components_allows_when_capabilities_missing():
    """Pre-handshake clients (capabilities=None) fall through to the BLE call.

    This preserves existing test-double behaviour for callers that haven't
    attached a MachineCapabilities yet; the gate only fires when the flag is
    explicitly False.
    """
    client = MagicMock()
    client.capabilities = None
    client.brew_freestyle = AsyncMock(return_value=True)

    await _brew_recipe_components(
        client,
        name="Test",
        blend=1,
        phases=_make_phases(),
    )

    client.brew_freestyle.assert_awaited_once()


# ── ws_brew + ws_favorites_brew translation ─────────────────────────────


@pytest.mark.asyncio
async def test_ws_sommelier_brew_translates_unsupported_error():
    """ws_brew converts RecipeWritesUnsupportedError into a send_error code."""
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value={
        "id": "r1",
        "name": "Test",
        "blend": 1,
        "machine_phases": _make_phases(),
    })
    db.async_mark_recipe_brewed = AsyncMock()

    client = MagicMock()
    client.capabilities = MagicMock(
        supports_recipe_writes=False,
        family_key="900",
    )
    client.brew_freestyle = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_error = MagicMock()
    connection.send_result = MagicMock()

    msg = {"id": 7, "type": "melitta_barista/sommelier/brew", "recipe_id": "r1"}

    ws_brew = _unwrap(sa.ws_brew)

    with patch(
        "custom_components.melitta_barista.sommelier_api._async_get_db",
        new=AsyncMock(return_value=db),
    ), patch(
        "custom_components.melitta_barista.sommelier_api._find_client",
        return_value=client,
    ):
        await ws_brew(hass, connection, msg)

    connection.send_error.assert_called_once()
    code = connection.send_error.call_args.args[1]
    assert code == "recipe_writes_unsupported"
    # The mark-as-brewed bookkeeping must not run on the refusal path.
    db.async_mark_recipe_brewed.assert_not_called()
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_ws_favorites_brew_translates_unsupported_error():
    """ws_favorites_brew converts RecipeWritesUnsupportedError into a send_error code."""
    db = MagicMock()
    db.async_get_favorite = AsyncMock(return_value={
        "id": "f1",
        "name": "Fav",
        "blend": 1,
        "machine_phases": _make_phases(),
    })
    db.async_increment_favorite_brew = AsyncMock()

    client = MagicMock()
    client.capabilities = MagicMock(
        supports_recipe_writes=False,
        family_key="700",
    )
    client.brew_freestyle = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_error = MagicMock()
    connection.send_result = MagicMock()

    msg = {
        "id": 9,
        "type": "melitta_barista/sommelier/favorites/brew",
        "favorite_id": "f1",
    }

    ws_favorites_brew = _unwrap(sa.ws_favorites_brew)

    with patch(
        "custom_components.melitta_barista.sommelier_api._async_get_db",
        new=AsyncMock(return_value=db),
    ), patch(
        "custom_components.melitta_barista.sommelier_api._find_client",
        return_value=client,
    ):
        await ws_favorites_brew(hass, connection, msg)

    connection.send_error.assert_called_once()
    code = connection.send_error.call_args.args[1]
    assert code == "recipe_writes_unsupported"
    db.async_increment_favorite_brew.assert_not_called()
    client.brew_freestyle.assert_not_awaited()
