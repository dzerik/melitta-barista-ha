"""entry_id scoping for sommelier brew commands (multi-machine correctness).

`ws_brew`, `ws_favorites_brew` and `ws_brew_phase` historically resolved
the machine via `_find_client(hass)` — always the FIRST config entry, so
on a two-machine install every brew hit machine #1. The commands now take
an optional `entry_id`:

- entry_id given  -> that exact entry's client (`_resolve_brew_client`),
  `entry_not_found` for an unknown id, `no_device` for a known entry
  without a live client;
- entry_id absent -> the legacy first-entry behavior (backwards
  compatible with older panels / single-machine installs).

Backend tests run the unwrapped handlers against a hass mock carrying two
config entries with distinct clients. Frontend contract tests (regex over
the shipped panel source, same style as test_brew_wizard_frontend.py)
pin that the brew wizard threads its `entryId` prop into all three WS
calls.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api
from custom_components.melitta_barista.const import DOMAIN


# ── helpers ──────────────────────────────────────────────────────────


def _client(brew_result: bool = True) -> MagicMock:
    """Machine client mock with no capability gate and a stubbed brew."""
    client = MagicMock()
    client.capabilities = None
    client.brew_freestyle = AsyncMock(return_value=brew_result)
    return client


def _entry(entry_id: str, client) -> MagicMock:
    """Config entry mock shaped like _resolve_entry/_find_client expect."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = DOMAIN
    entry.runtime_data = client
    return entry


def _hass_with_entries(entries: list) -> MagicMock:
    """hass mock whose config_entries expose the given melitta entries."""
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=list(entries))
    by_id = {e.entry_id: e for e in entries}
    hass.config_entries.async_get_entry = MagicMock(
        side_effect=lambda entry_id: by_id.get(entry_id)
    )
    return hass


def _row(row_id: str = "r1") -> dict:
    """Minimal brewable one-phase recipe/favorite row."""
    return {
        "id": row_id,
        "name": "Scoped Cup",
        "description": "d",
        "blend": 1,
        "component1": {},
        "component2": {},
        "machine_phases": [
            {
                "component": {
                    "process": "coffee", "intensity": "medium",
                    "temperature": "normal", "shots": "one", "portion_ml": 40,
                },
                "user_action_before": [],
            }
        ],
    }


def _db(*, recipe=None, favorite=None) -> MagicMock:
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=recipe)
    db.async_get_favorite = AsyncMock(return_value=favorite)
    db.async_mark_recipe_brewed = AsyncMock()
    db.async_increment_favorite_brew = AsyncMock()
    return db


async def _call(handler, hass, db, msg) -> MagicMock:
    """Run an unwrapped WS handler with a patched DB; returns the connection."""
    connection = MagicMock()
    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)):
        await inspect.unwrap(handler)(hass, connection, msg)
    return connection


# Handler + msg-builder matrix so every scenario covers all three commands.
def _brew_msg(msg_id: int, **extra) -> tuple:
    return sommelier_api.ws_brew, {"id": msg_id, "recipe_id": "r1", **extra}


def _fav_msg(msg_id: int, **extra) -> tuple:
    return sommelier_api.ws_favorites_brew, {"id": msg_id, "favorite_id": "f1", **extra}


def _phase_msg(msg_id: int, **extra) -> tuple:
    return sommelier_api.ws_brew_phase, {
        "id": msg_id, "recipe_id": "r1", "phase_index": 0, **extra,
    }


def _db_for(handler) -> MagicMock:
    if handler is sommelier_api.ws_favorites_brew:
        return _db(favorite=_row("f1"))
    return _db(recipe=_row("r1"))


_ALL_COMMANDS = [_brew_msg, _fav_msg, _phase_msg]


# ── entry_id routes to the requested entry's client ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_entry_id_routes_to_second_entry_client(make):
    client1, client2 = _client(), _client()
    hass = _hass_with_entries([_entry("e1", client1), _entry("e2", client2)])
    handler, msg = make(1, entry_id="e2")
    connection = await _call(handler, hass, _db_for(handler), msg)

    connection.send_error.assert_not_called()
    client2.brew_freestyle.assert_awaited_once()
    client1.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_entry_id_of_first_entry_still_works(make):
    client1, client2 = _client(), _client()
    hass = _hass_with_entries([_entry("e1", client1), _entry("e2", client2)])
    handler, msg = make(2, entry_id="e1")
    connection = await _call(handler, hass, _db_for(handler), msg)

    connection.send_error.assert_not_called()
    client1.brew_freestyle.assert_awaited_once()
    client2.brew_freestyle.assert_not_awaited()


# ── missing entry_id keeps the legacy first-entry behavior ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_missing_entry_id_falls_back_to_first_entry(make):
    client1, client2 = _client(), _client()
    hass = _hass_with_entries([_entry("e1", client1), _entry("e2", client2)])
    handler, msg = make(3)
    connection = await _call(handler, hass, _db_for(handler), msg)

    connection.send_error.assert_not_called()
    client1.brew_freestyle.assert_awaited_once()
    client2.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_missing_entry_id_without_any_client_is_no_device(make):
    hass = _hass_with_entries([])
    handler, msg = make(4)
    connection = await _call(handler, hass, _db_for(handler), msg)

    assert connection.send_error.call_args.args[1] == "no_device"
    connection.send_result.assert_not_called()


# ── bad entry_id errors ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_unknown_entry_id_is_entry_not_found(make):
    client1 = _client()
    hass = _hass_with_entries([_entry("e1", client1)])
    handler, msg = make(5, entry_id="nope")
    connection = await _call(handler, hass, _db_for(handler), msg)

    args = connection.send_error.call_args.args
    assert args[1] == "entry_not_found"
    assert "nope" in args[2]
    client1.brew_freestyle.assert_not_awaited()
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_foreign_domain_entry_id_is_entry_not_found(make):
    """An entry_id belonging to another integration must not resolve."""
    client1 = _client()
    foreign = _entry("alien", MagicMock())
    foreign.domain = "other_domain"
    hass = _hass_with_entries([_entry("e1", client1)])
    hass.config_entries.async_get_entry = MagicMock(
        side_effect=lambda entry_id: foreign if entry_id == "alien" else None
    )
    handler, msg = make(6, entry_id="alien")
    connection = await _call(handler, hass, _db_for(handler), msg)

    assert connection.send_error.call_args.args[1] == "entry_not_found"
    client1.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", _ALL_COMMANDS)
async def test_known_entry_without_client_is_no_device(make):
    hass = _hass_with_entries([_entry("e1", None)])
    handler, msg = make(7, entry_id="e1")
    connection = await _call(handler, hass, _db_for(handler), msg)

    args = connection.send_error.call_args.args
    assert args[1] == "no_device"
    assert "e1" in args[2]


# ── schemas accept the optional entry_id ─────────────────────────────


@pytest.mark.parametrize(
    "handler",
    [sommelier_api.ws_brew, sommelier_api.ws_favorites_brew, sommelier_api.ws_brew_phase],
)
def test_ws_schema_allows_optional_entry_id(handler):
    """voluptuous must accept entry_id and keep it optional."""
    schema_keys = {str(k) for k in handler._ws_schema.schema}
    assert "entry_id" in schema_keys


# ── frontend contract: the wizard threads entryId into the calls ─────

_WIZARD = (
    Path(__file__).parent.parent
    / "custom_components"
    / "melitta_barista"
    / "www"
    / "components"
    / "melitta-brew-wizard.js"
)


def _wizard_src() -> str:
    return _WIZARD.read_text(encoding="utf-8")


def test_wizard_builds_entry_scope_from_entry_id_prop():
    """The scope object is derived from the entryId prop, never hardcoded."""
    src = _wizard_src()
    assert re.search(
        r"scope\s*=\s*this\.entryId\s*\?\s*\{\s*entry_id:\s*this\.entryId\s*\}",
        src,
    ), "wizard must build an entry_id scope from its entryId prop"


def test_wizard_brew_phase_call_carries_entry_scope():
    src = _wizard_src()
    call = re.search(
        r"callWS\(\{\s*type:\s*\"melitta_barista/sommelier/brew_phase\","
        r"[^}]*\.\.\.scope,",
        src,
    )
    assert call, "brew_phase call must spread the entry scope"


def test_wizard_legacy_brew_calls_carry_entry_scope():
    src = _wizard_src()
    for command in (
        "melitta_barista/sommelier/favorites/brew",
        "melitta_barista/sommelier/brew",
    ):
        line = re.search(
            r"type:\s*\"" + re.escape(command) + r"\",[^}\n]*\.\.\.scope",
            src,
        )
        assert line, f"legacy full-recipe call {command} must spread the entry scope"
