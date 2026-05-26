"""Tests for the melitta_barista/api/info discovery endpoint.

We avoid monkeypatching the live `homeassistant.loader` module because that
leaks into the autouse Bluetooth fixture's teardown and triggers a cascade
of `SocketConnectBlockedError`s on every subsequent test. Patching via
`unittest.mock.patch` as a context manager keeps the change scoped to the
single handler call.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import panel_api
from custom_components.melitta_barista.const import API_VERSION, DOMAIN
from custom_components.melitta_barista.sommelier_db import SCHEMA_VERSION


def _make_hass(registry: dict | None = None) -> MagicMock:
    hass = MagicMock()
    hass.data = {"websocket_api": registry or {}}
    return hass


def _make_connection() -> MagicMock:
    connection = MagicMock()
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()
    return connection


def _fake_integration(version: str) -> MagicMock:
    integration = MagicMock()
    integration.manifest = {"version": version}
    return integration


@pytest.mark.asyncio
async def test_api_info_returns_versions_and_endpoints():
    """api/info reports api_version, integration_version, schema, endpoints."""
    registry = {
        f"{DOMAIN}/status": object(),
        f"{DOMAIN}/sommelier/generate": object(),
        f"{DOMAIN}/api/info": object(),
        # Foreign keys must be filtered out.
        "frontend/get_panels": object(),
        "auth/current_user": object(),
    }
    hass = _make_hass(registry)
    connection = _make_connection()

    fake_loader = AsyncMock(return_value=_fake_integration("0.66.0"))
    with patch("homeassistant.loader.async_get_integration", new=fake_loader):
        handler = inspect.unwrap(panel_api._ws_api_info)
        await handler(hass, connection, {"id": 1, "type": "melitta_barista/api/info"})

    connection.send_error.assert_not_called()
    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args.args
    assert msg_id == 1
    assert payload["api_version"] == API_VERSION
    assert payload["integration_version"] == "0.66.0"
    assert payload["schema_db_version"] == SCHEMA_VERSION
    # Only the domain-prefixed types make it through, alphabetically.
    assert payload["endpoints"] == [
        f"{DOMAIN}/api/info",
        f"{DOMAIN}/sommelier/generate",
        f"{DOMAIN}/status",
    ]


@pytest.mark.asyncio
async def test_api_info_handles_missing_websocket_registry():
    """Empty hass.data['websocket_api'] yields an empty endpoints list."""
    hass = _make_hass({})
    connection = _make_connection()

    fake_loader = AsyncMock(return_value=_fake_integration("0.66.0"))
    with patch("homeassistant.loader.async_get_integration", new=fake_loader):
        handler = inspect.unwrap(panel_api._ws_api_info)
        await handler(hass, connection, {"id": 2, "type": "melitta_barista/api/info"})

    connection.send_error.assert_not_called()
    payload = connection.send_result.call_args.args[1]
    assert payload["endpoints"] == []
    assert payload["api_version"] == API_VERSION


@pytest.mark.asyncio
async def test_api_info_falls_back_to_unknown_version_when_lookup_raises():
    """If async_get_integration raises, the handshake still succeeds."""
    async def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    hass = _make_hass()
    connection = _make_connection()
    with patch("homeassistant.loader.async_get_integration", new=_raise):
        handler = inspect.unwrap(panel_api._ws_api_info)
        await handler(hass, connection, {"id": 3, "type": "melitta_barista/api/info"})

    connection.send_error.assert_not_called()
    payload = connection.send_result.call_args.args[1]
    assert payload["integration_version"] == "unknown"
    assert payload["api_version"] == API_VERSION
