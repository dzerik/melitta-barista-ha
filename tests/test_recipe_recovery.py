"""Regression coverage for recipe preservation and recovery services."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista import _async_register_services
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.const import (
    DOMAIN, DirectKeyCategory, MachineProcess, RecipeId,
)
from custom_components.melitta_barista.protocol import (
    MachineRecipe, MachineStatus, MelittaProtocol, RecipeComponent,
)


def _connected_client(mock_bleak_client) -> MelittaBleClient:
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._connected = True
    client._client = mock_bleak_client
    return client


def _register_service_client(hass, client) -> str:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    entry.runtime_data = client
    entity = er.async_get(hass).async_get_or_create(
        "button", DOMAIN, "recipe_recovery_brew", config_entry=entry,
    )
    _async_register_services(hass)
    return entity.entity_id


@pytest.mark.parametrize("blends", [(2, 1), (0, 2), (1, 0)])
async def test_save_amount_preserves_hoppers_and_reserved_bytes(
    hass, mock_bleak_client, blends,
):
    """Editing an amount leaves both saved hopper and reserved bytes intact."""
    client = _connected_client(mock_bleak_client)
    client._protocol.read_recipe = AsyncMock(return_value=MachineRecipe(
        313, 5,
        RecipeComponent(process=1, blend=blends[0], reserve=17),
        RecipeComponent(process=1, blend=blends[1], reserve=23),
    ))
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    entity_id = _register_service_client(hass, client)

    await hass.services.async_call(DOMAIN, "save_directkey", {
        "entity_id": entity_id, "category": "cafe_creme", "profile_id": 1,
        "process1": "coffee", "portion1_ml": 150,
    }, blocking=True)

    command, payload, _ = client._protocol.send_and_wait_ack.call_args.args
    assert command == "HJ"
    assert struct.unpack(">h", payload[:2])[0] == 313
    assert payload[5] == blends[0]
    assert payload[13] == blends[1]
    assert payload[10] == 17
    assert payload[18] == 23
    assert payload[9] == 30  # 150 ml in units of 5 ml.


async def test_save_refuses_to_guess_hidden_fields_when_read_fails(
    hass, mock_bleak_client,
):
    """An unreadable saved recipe must not be overwritten with guessed hoppers."""
    client = _connected_client(mock_bleak_client)
    client._protocol.read_recipe = AsyncMock(return_value=None)
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    entity_id = _register_service_client(hass, client)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "save_directkey", {
            "entity_id": entity_id, "category": "cafe_creme", "profile_id": 1,
            "process1": "coffee", "portion1_ml": 150,
        }, blocking=True)

    client._protocol.send_and_wait_ack.assert_not_awaited()


def _recipe_frame(protocol, recipe_id, blend) -> bytes:
    payload = (
        struct.pack(">hB", recipe_id, 5)
        + RecipeComponent(process=1, blend=blend).to_bytes()
        + RecipeComponent().to_bytes()
    ).ljust(66, b"\x00")
    return protocol.build_frame("HC", payload, include_key_prefix=False)


async def test_late_recipe_response_is_ignored_until_requested_recipe_arrives():
    """A late Espresso reply cannot satisfy a subsequent Cafe Creme read."""
    protocol = MelittaProtocol(frame_timeout=0.01)
    writes = 0

    async def respond(_):
        nonlocal writes
        writes += 1
        if writes == 2:
            protocol.on_ble_data(_recipe_frame(protocol, 200, 1))
            asyncio.get_running_loop().call_soon(
                protocol.on_ble_data, _recipe_frame(protocol, 205, 2),
            )

    assert await protocol.read_recipe(respond, 200) is None
    result = await protocol.read_recipe(respond, 205)

    assert result is not None
    assert result.recipe_id == 205
    assert result.component1.blend == 2


async def test_unrelated_recipe_response_does_not_fill_client_cache(mock_bleak_client):
    """A read times out rather than caching an unsolicited recipe under its ID."""
    client = _connected_client(mock_bleak_client)
    client._protocol = MelittaProtocol(frame_timeout=0.01)

    async def respond(_):
        client._protocol.on_ble_data(_recipe_frame(client._protocol, 200, 1))

    client._write_ble = respond
    assert await client.read_recipe(205) is None
    assert client.base_recipes == {}


async def test_profile_reload_keeps_failed_slots_and_updates_successful_ones(
    mock_bleak_client,
):
    """A transient missing reply cannot delete a previously loaded recipe."""
    client = _connected_client(mock_bleak_client)
    client._protocol.read_alphanumeric = AsyncMock(return_value=None)

    async def initial_recipe(_, recipe_id):
        return MachineRecipe(recipe_id, 5, RecipeComponent(blend=2))

    client._protocol.read_recipe = AsyncMock(side_effect=initial_recipe)
    await client.read_profile_data()
    previous = client.directkey_recipes[0][DirectKeyCategory.CAFE_CREME]
    generation = client.recipe_cache_generation

    async def reload_recipe(_, recipe_id):
        if recipe_id == 303:
            return None
        return replace(previous, recipe_id=recipe_id, recipe_type=6)

    client._protocol.read_recipe = AsyncMock(side_effect=reload_recipe)
    await client.read_profile_data()

    assert client.directkey_recipes[0][DirectKeyCategory.CAFE_CREME] is previous
    assert client.directkey_recipes[0][DirectKeyCategory.ESPRESSO].recipe_type == 6
    assert client.recipe_cache_generation > generation


async def test_refresh_service_reloads_both_caches_and_reports_partial_failure(
    hass, mock_bleak_client,
):
    """Refresh reads the device, retains failed slots and updates subscribers."""
    client = _connected_client(mock_bleak_client)
    client._recipe_retries = 2
    old_base = MachineRecipe(200, 0, RecipeComponent(blend=2))
    client.store_base_recipe(200, old_base)
    client._protocol.read_alphanumeric = AsyncMock(return_value=None)
    attempts: dict[int, int] = {}

    async def read(_, recipe_id):
        attempts[recipe_id] = attempts.get(recipe_id, 0) + 1
        if recipe_id == 200 or (recipe_id == 205 and attempts[recipe_id] == 1):
            return None
        return MachineRecipe(recipe_id, 5, RecipeComponent(blend=2))

    client._protocol.read_recipe = AsyncMock(side_effect=read)
    updates = []
    client.add_recipe_refresh_callback(lambda recipe_id, _: updates.append(recipe_id))
    entity_id = _register_service_client(hass, client)

    result = await hass.services.async_call(DOMAIN, "refresh_recipes", {
        "entity_id": entity_id,
    }, blocking=True, return_response=True)

    assert result == {
        "base_updated": 23, "base_expected": 24,
        "directkey_updated": 63, "directkey_expected": 63, "complete": False,
    }
    assert client.base_recipes[200] is old_base
    assert client.base_recipes[205].component1.blend == 2
    assert client.directkey_recipes[0][DirectKeyCategory.CAFE_CREME].recipe_id == 303
    assert RecipeId.CAFE_CREME in updates
    assert attempts[205] == 2
    assert client.recipe_refresh_status == result


async def test_concurrent_refresh_calls_share_one_read_and_cancel_independently(
    mock_bleak_client,
):
    """Cancelling one waiter leaves the shared device refresh running."""
    client = _connected_client(mock_bleak_client)
    started = asyncio.Event()
    release = asyncio.Event()

    async def read(_, recipe_id):
        started.set()
        await release.wait()
        return MachineRecipe(recipe_id, 5)

    client._protocol.read_recipe = AsyncMock(side_effect=read)
    client._protocol.read_alphanumeric = AsyncMock(return_value=None)
    first = asyncio.create_task(client.refresh_recipes())
    await started.wait()
    second = asyncio.create_task(client.refresh_recipes())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    result = await second

    assert result["complete"] is True
    assert client._protocol.read_recipe.await_count == 87


async def test_disconnect_cancels_refresh_without_restarting_polling(mock_bleak_client):
    """Disconnect must not let refresh cleanup restart the polling loop."""
    client = _connected_client(mock_bleak_client)
    started = asyncio.Event()
    client._poll_task = asyncio.create_task(asyncio.Event().wait())
    client.start_polling = Mock()

    async def read(*_):
        started.set()
        await asyncio.Event().wait()

    client._protocol.read_recipe = AsyncMock(side_effect=read)
    refresh = asyncio.create_task(client.refresh_recipes())
    await started.wait()
    await client.disconnect()

    with pytest.raises(asyncio.CancelledError):
        await refresh
    client.start_polling.assert_not_called()
    assert client.connected is False


async def test_recipe_diagnostics_distinguish_cached_data_from_fresh_reads(
    hass, mock_bleak_client,
):
    """Diagnostics expose cache sizes and the last explicit refresh outcome."""
    from custom_components.melitta_barista.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    client = _connected_client(mock_bleak_client)
    client.store_base_recipe(200, MachineRecipe(200))
    client.recipe_refresh_status = {
        "base_updated": 0, "base_expected": 24,
        "directkey_updated": 0, "directkey_expected": 63, "complete": False,
    }
    _register_service_client(hass, client)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["recipes"] == {
        "base_cached": 1, "directkey_cached": 0,
        "refresh_in_progress": False, "last_refresh": client.recipe_refresh_status,
    }


async def test_refresh_time_budget_returns_partial_progress(mock_bleak_client):
    """A stalled device cannot hold the refresh and brew lock indefinitely."""
    client = _connected_client(mock_bleak_client)

    async def read(_, recipe_id):
        if recipe_id == 200:
            return MachineRecipe(200)
        await asyncio.Event().wait()

    client._protocol.read_recipe = AsyncMock(side_effect=read)
    try:
        with patch(
            "custom_components.melitta_barista._ble_recipes._RECIPE_REFRESH_TIMEOUT",
            0.01, create=True,
        ):
            result = await asyncio.wait_for(client.refresh_recipes(), timeout=0.2)
        assert result["base_updated"] == 1
        assert result["directkey_updated"] == 0
        assert result["complete"] is False
        assert client.base_recipes[200].recipe_id == 200
    finally:
        await client.disconnect()


@pytest.mark.parametrize("condition", ["disconnected", "brewing", "unsupported"])
async def test_refresh_rejects_unavailable_machine_without_sending_reads(
    hass, mock_bleak_client, condition,
):
    """An unavailable machine cannot start a recipe refresh."""
    client = _connected_client(mock_bleak_client)
    if condition == "disconnected":
        client._connected = False
    elif condition == "brewing":
        client._status = MachineStatus(process=MachineProcess.PRODUCT)
    else:
        from custom_components.melitta_barista.brands import get_profile

        client._brand = get_profile("nivona")
    client._protocol.read_recipe = AsyncMock()
    entity_id = _register_service_client(hass, client)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "refresh_recipes", {
            "entity_id": entity_id,
        }, blocking=True)

    client._protocol.read_recipe.assert_not_awaited()


async def test_refresh_reports_total_read_failure_and_preserves_cache(
    hass, mock_bleak_client,
):
    """No successful reads is an error even when cached data remains available."""
    client = _connected_client(mock_bleak_client)
    client._recipe_retries = 1
    saved = MachineRecipe(200)
    client.store_base_recipe(200, saved)
    client._protocol.read_recipe = AsyncMock(return_value=None)
    entity_id = _register_service_client(hass, client)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "refresh_recipes", {
            "entity_id": entity_id,
        }, blocking=True)

    assert client.base_recipes[200] is saved
    assert client.recipe_refresh_status == {
        "base_updated": 0, "base_expected": 24,
        "directkey_updated": 0, "directkey_expected": 63, "complete": False,
    }
