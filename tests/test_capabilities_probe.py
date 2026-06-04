"""Tests for capability probing on BLE connect."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.melitta_barista import _make_capabilities_probe_callback


@pytest.mark.asyncio
async def test_probe_callback_derives_and_saves_on_connect():
    """When the connection callback fires with True, derive runs and result is saved to DB."""

    db = MagicMock()
    db.async_save_capabilities = AsyncMock()

    client = MagicMock()
    caps = MagicMock()
    caps.family_key = "barista_ts"
    caps.model_name = "Melitta Barista TS"
    caps.strength_levels = 5
    caps.has_aroma_balance = True
    client.capabilities = caps

    hass = MagicMock()
    pending_tasks = []
    hass.async_create_task = lambda coro: pending_tasks.append(coro)

    callback = _make_capabilities_probe_callback(hass, db, client, "entry_42")
    callback(True)

    assert len(pending_tasks) == 1
    await pending_tasks[0]

    db.async_save_capabilities.assert_called_once()
    saved_entry_id, saved_json = db.async_save_capabilities.call_args.args
    assert saved_entry_id == "entry_42"
    parsed = json.loads(saved_json)
    assert parsed["family_key"] == "barista_ts"
    assert parsed["schema_version"] == 2
    assert "coffee" in parsed["supported_processes"]


@pytest.mark.asyncio
async def test_probe_callback_no_op_on_disconnect():
    """The callback does NOTHING when connected=False (no derive, no save)."""

    db = MagicMock()
    db.async_save_capabilities = AsyncMock()
    client = MagicMock()
    hass = MagicMock()
    hass.async_create_task = MagicMock()

    callback = _make_capabilities_probe_callback(hass, db, client, "entry_42")
    callback(False)

    hass.async_create_task.assert_not_called()
    db.async_save_capabilities.assert_not_called()


@pytest.mark.asyncio
async def test_probe_callback_swallows_derive_errors():
    """If derive_capabilities raises, the callback logs and continues — does not propagate."""

    db = MagicMock()
    db.async_save_capabilities = AsyncMock()
    client = MagicMock()
    client.capabilities = None  # derive will raise ValueError

    hass = MagicMock()
    pending = []
    hass.async_create_task = lambda coro: pending.append(coro)

    callback = _make_capabilities_probe_callback(hass, db, client, "entry_x")
    callback(True)

    await pending[0]
    db.async_save_capabilities.assert_not_called()
