"""Tests for the melitta_barista/capabilities/get WS endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista.sommelier_api import ws_capabilities_get as _ws_capabilities_get_decorated

# Unwrap the @websocket_api.async_response decorator — the decorated form is
# a sync callback that schedules a background task, which we can't await
# directly in unit tests. The original async function is preserved on
# `__wrapped__` by functools.wraps.
ws_capabilities_get = _ws_capabilities_get_decorated.__wrapped__


@pytest.mark.asyncio
async def test_returns_cached_payload_when_db_has_row():
    """When DB has a capabilities row, the handler returns the parsed JSON + meta."""
    hass = MagicMock()
    db = MagicMock()
    db.async_get_capabilities = AsyncMock(
        return_value={
            "entry_id": "entry_1",
            "json_payload": json.dumps({
                "schema_version": 1,
                "family_key": "barista_ts",
                "model_name": "Melitta Barista TS",
                "supported_processes": ["coffee", "milk"],
                "supported_intensities": ["mild"],
                "supported_aromas": ["standard"],
                "supported_temperatures": ["normal"],
                "supported_shots": ["one"],
                "portion_limits": {"coffee": {"min": 5, "max": 250, "step": 5}},
                "forbidden_combinations": [],
            }),
            "probed_at": "2026-05-25T10:00:00+00:00",
            "schema_version": 1,
        }
    )
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_result = MagicMock()

    await ws_capabilities_get(hass, connection, {"id": 7, "type": "melitta_barista/capabilities/get", "entry_id": "entry_1"})

    db.async_get_capabilities.assert_awaited_once_with("entry_1")
    connection.send_result.assert_called_once()
    args = connection.send_result.call_args.args
    assert args[0] == 7
    result = args[1]
    assert result["schema_version"] == 1
    assert result["entry_id"] == "entry_1"
    assert result["source"] == "cache"
    assert result["capabilities"]["family_key"] == "barista_ts"
    assert result["probed_at"] == "2026-05-25T10:00:00+00:00"


@pytest.mark.asyncio
async def test_falls_back_to_live_derive_when_db_empty():
    """When DB has no row, the handler derives on-the-fly from runtime_data."""
    from custom_components.melitta_barista.brands.base import MachineCapabilities

    caps = MachineCapabilities(
        family_key="barista_t",
        model_name="Melitta Barista T",
        supports_recipe_writes=True,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=True,
        image_transfer=None,
        fluid_scale_factor=1,
        brew_command_mode=0x0B,
        recipe_text_encoding="utf16_le",
        tolerated_brew_manipulations=(),
        recipes=(),
        settings=(),
        stats=(),
    )

    client = MagicMock()
    client.capabilities = caps

    entry = MagicMock()
    entry.entry_id = "entry_new"
    entry.runtime_data = client

    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    db = MagicMock()
    db.async_get_capabilities = AsyncMock(return_value=None)
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_result = MagicMock()

    await ws_capabilities_get(
        hass, connection,
        {"id": 1, "type": "melitta_barista/capabilities/get", "entry_id": "entry_new"},
    )

    args = connection.send_result.call_args.args
    result = args[1]
    assert result["source"] == "derive"
    assert result["capabilities"]["family_key"] == "barista_t"
    assert result["probed_at"] is None


@pytest.mark.asyncio
async def test_returns_error_when_entry_unknown():
    """Unknown entry_id, no DB row → connection.send_error called."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=None)

    db = MagicMock()
    db.async_get_capabilities = AsyncMock(return_value=None)
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_error = MagicMock()
    connection.send_result = MagicMock()

    await ws_capabilities_get(
        hass, connection,
        {"id": 4, "type": "melitta_barista/capabilities/get", "entry_id": "bogus"},
    )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args.args[0] == 4
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_cache_with_corrupt_payload_falls_back_to_derive():
    """A DB row with invalid JSON or unsupported schema_version falls through to derive."""
    from custom_components.melitta_barista.brands.base import MachineCapabilities

    caps = MachineCapabilities(
        family_key="barista_t",
        model_name="Melitta Barista T",
        supports_recipe_writes=True,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=True,
        image_transfer=None,
        fluid_scale_factor=1,
        brew_command_mode=0x0B,
        recipe_text_encoding="utf16_le",
        tolerated_brew_manipulations=(),
        recipes=(),
        settings=(),
        stats=(),
    )

    client = MagicMock()
    client.capabilities = caps

    entry = MagicMock()
    entry.entry_id = "entry_corrupt"
    entry.runtime_data = client

    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    db = MagicMock()
    # DB returns a row whose JSON is corrupt — this MUST not crash the handler.
    db.async_get_capabilities = AsyncMock(return_value={
        "entry_id": "entry_corrupt",
        "json_payload": "{not valid json",
        "probed_at": "2026-05-25T10:00:00+00:00",
        "schema_version": 1,
    })
    hass.data = {"melitta_barista": {"sommelier_db": db}}

    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    await ws_capabilities_get(
        hass, connection,
        {"id": 11, "type": "melitta_barista/capabilities/get", "entry_id": "entry_corrupt"},
    )

    # Must have fallen through to derive, not crashed and not send_error
    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    result = connection.send_result.call_args.args[1]
    assert result["source"] == "derive"
    assert result["capabilities"]["family_key"] == "barista_t"
